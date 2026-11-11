"""BlueZ glue: read an iPhone's notifications over ANCS, and the agent for the notification bond window."""
import asyncio
import base64
import json
import logging
import secrets
import threading
import time

from dbus_next import DBusError, Message, MessageType, Variant

import ancs

BLUEZ = "org.bluez"
DEVICE = "org.bluez.Device1"
ADAPTER = "org.bluez.Adapter1"
GATT_SERVICE = "org.bluez.GattService1"
GATT_CHAR = "org.bluez.GattCharacteristic1"
PROPERTIES = "org.freedesktop.DBus.Properties"
CALL_TIMEOUT = 10
LOG = logging.getLogger("galaxy-ble")


async def bluez_call(bus, path, interface, member, signature="", body=None, destination=BLUEZ):
  reply = await asyncio.wait_for(bus.call(Message(destination=destination, path=path, interface=interface, member=member,
                                                  signature=signature, body=body or [])), timeout=CALL_TIMEOUT)
  if reply.message_type == MessageType.ERROR:
    raise DBusError(reply.error_name, str(reply.body[0]) if reply.body else "")
  return reply.body


def unwrap(props):
  return {key: value.value if isinstance(value, Variant) else value for key, value in props.items()}


def find_ancs(objects, adapter_path):
  """-> {device path: {uuid: characteristic path}} for connected, bonded devices under the adapter exposing all three ANCS characteristics."""
  prefix = adapter_path.rstrip("/") + "/"
  wanted = {ancs.NOTIFICATION_SOURCE_UUID, ancs.CONTROL_POINT_UUID, ancs.DATA_SOURCE_UUID}
  services = {path for path, interfaces in objects.items()
              if str(interfaces.get(GATT_SERVICE, {}).get("UUID", "")).lower() == ancs.SERVICE_UUID}
  found = {}
  for path, interfaces in objects.items():
    char = interfaces.get(GATT_CHAR)
    if char is None or char.get("Service") not in services:
      continue
    uuid = str(char.get("UUID", "")).lower()
    device = char.get("Service").rsplit("/", 1)[0]
    device_props = objects.get(device, {}).get(DEVICE)
    # iOS only serves ANCS on a bonded link; an unbonded device advertising these UUIDs is not an iPhone.
    if (uuid in wanted and device.startswith(prefix) and device_props and device_props.get("Connected") is True
            and device_props.get("Paired") is True):
      found.setdefault(device, {})[uuid] = path
  return {device: chars for device, chars in found.items() if set(chars) == wanted}


class Poster:
  """Hands payloads to Galaxy off the event loop; bounded, silent and never retried."""
  def __init__(self, proxy, limit=4):
    self.proxy = proxy
    self.limit = limit
    self.tasks = set()
    self.lock = threading.Lock()
    self.posted = 0
    self.dropped = 0

  def post(self, payload):
    if len(self.tasks) >= self.limit:
      self.dropped += 1
      return
    task = asyncio.create_task(self._send(payload))
    self.tasks.add(task)
    task.add_done_callback(self.tasks.discard)

  async def _send(self, payload):
    request = {"id": "ancs-" + secrets.token_hex(4), "method": "POST", "path": "/api/companion/notification",
               "headers": {"content-type": "application/json"},
               "body": base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()}
    try:
      response = await asyncio.to_thread(self._handle, request)
      if response.get("status") == 200:
        self.posted += 1
        return
    except Exception:
      pass
    self.dropped += 1

  def _handle(self, request):
    # GalaxyProxy keeps one session cookie; serialize so a burst does not mint several sessions.
    with self.lock:
      return self.proxy.handle(request)


class Phone:
  def __init__(self, path, chars, client):
    self.path = path
    self.chars = chars
    self.client = client
    self.subscriptions = []


class Watcher:
  """gatt: async objects(), start_notify(path), stop_notify(path), write_value(path, data); subscribe(path, callback) -> unsubscribe()."""
  def __init__(self, gatt, adapter_path, emit, clock=time.monotonic, sleep=asyncio.sleep, scan_interval=3.0, tick=0.5,
               retry_after=30.0):
    self.gatt = gatt
    self.adapter_path = adapter_path
    self.emit = emit
    self.clock = clock
    self.sleep = sleep
    self.scan_interval = scan_interval
    self.tick_interval = tick
    self.retry_after = retry_after
    self.phones = {}
    self.failed = {}
    self.writes = set()

  async def scan(self):
    found = find_ancs({path: {name: unwrap(props) for name, props in interfaces.items()}
                       for path, interfaces in (await self.gatt.objects()).items()}, self.adapter_path)
    for path in list(self.phones):
      if found.get(path) != self.phones[path].chars:
        await self.detach(path)
    now = self.clock()
    for path, chars in found.items():
      if path in self.phones or now < self.failed.get(path, 0):
        continue
      try:
        await self.attach(path, chars)
      except asyncio.CancelledError:
        raise
      except Exception as error:
        LOG.warning("ANCS subscription failed (%s); retrying in %ds", type(error).__name__, int(self.retry_after))
        await self.detach(path)
        self.failed[path] = now + self.retry_after
    for path in list(self.failed):
      if path not in found:
        del self.failed[path]

  async def attach(self, path, chars):
    control = chars[ancs.CONTROL_POINT_UUID]
    client = ancs.Client(lambda data: self._write(path, control, data), self.emit, clock=self.clock)
    phone = Phone(path, chars, client)
    self.phones[path] = phone
    phone.subscriptions.append(self.gatt.subscribe(chars[ancs.DATA_SOURCE_UUID], client.on_data_source))
    phone.subscriptions.append(self.gatt.subscribe(chars[ancs.NOTIFICATION_SOURCE_UUID], client.on_notification_source))
    await self.gatt.start_notify(chars[ancs.DATA_SOURCE_UUID])
    await self.gatt.start_notify(chars[ancs.NOTIFICATION_SOURCE_UUID])
    LOG.info("ANCS client attached; phones=%d", len(self.phones))

  async def detach(self, path):
    phone = self.phones.pop(path, None)
    if phone is None:
      return
    for unsubscribe in phone.subscriptions:
      try:
        unsubscribe()
      except Exception:
        pass
    for uuid in (ancs.NOTIFICATION_SOURCE_UUID, ancs.DATA_SOURCE_UUID):
      try:
        await self.gatt.stop_notify(phone.chars[uuid])
      except Exception:
        pass
    LOG.info("ANCS client detached; emitted=%d dropped=%d phones=%d", phone.client.emitted, phone.client.dropped, len(self.phones))

  def _write(self, path, control, data):
    phone = self.phones.get(path)
    request = phone.client.current if phone is not None else None
    task = asyncio.create_task(self._send(path, control, data, request))
    self.writes.add(task)
    task.add_done_callback(self.writes.discard)

  async def _send(self, path, control, data, request):
    try:
      await self.gatt.write_value(control, data)
    except asyncio.CancelledError:
      raise
    except Exception:
      phone = self.phones.get(path)
      if phone is not None and request is not None and phone.client.current is request:
        phone.client.abort()

  def tick(self):
    for phone in list(self.phones.values()):
      phone.client.tick()

  async def run(self):
    try:
      next_scan = 0.0
      while True:
        if self.clock() >= next_scan:
          await self.scan()
          next_scan = self.clock() + self.scan_interval
        self.tick()
        await self.sleep(self.tick_interval)
    finally:
      for task in list(self.writes):
        task.cancel()
      for path in list(self.phones):
        await self.detach(path)


async def supervise(make_watcher, sleep=asyncio.sleep, max_backoff=60):
  """Keep the ANCS client alive; a failure here only logs and never touches the Galaxy GATT service."""
  backoff = 2
  while True:
    started = time.monotonic()
    try:
      await make_watcher().run()
    except asyncio.CancelledError:
      raise
    except Exception as error:
      if time.monotonic() - started > max_backoff:
        backoff = 2
      LOG.warning("ANCS client stopped (%s); retrying in %ds", type(error).__name__, backoff)
    await sleep(backoff)
    backoff = min(max_backoff, backoff * 2)


class BluezGatt:
  """The real BlueZ calls behind Watcher."""
  def __init__(self, bus):
    self.bus = bus
    self.callbacks = {}
    self.matched = False
    bus.add_message_handler(self._on_message)

  async def _call(self, path, interface, member, signature="", body=None):
    return await bluez_call(self.bus, path, interface, member, signature, body)

  async def _match(self):
    if self.matched:
      return
    rule = f"type='signal',sender='{BLUEZ}',interface='{PROPERTIES}',member='PropertiesChanged',arg0='{GATT_CHAR}'"
    await bluez_call(self.bus, "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s", [rule],
                     destination="org.freedesktop.DBus")
    self.matched = True

  async def objects(self):
    await self._match()
    return (await self._call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"))[0]

  async def start_notify(self, path):
    await self._call(path, GATT_CHAR, "StartNotify")

  async def stop_notify(self, path):
    await self._call(path, GATT_CHAR, "StopNotify")

  async def write_value(self, path, data):
    await self._call(path, GATT_CHAR, "WriteValue", "aya{sv}", [bytes(data), {"type": Variant("s", "request")}])

  def subscribe(self, path, callback):
    self.callbacks[path] = callback
    return lambda: self.callbacks.pop(path, None) if self.callbacks.get(path) is callback else None

  def _on_message(self, message):
    # Returning anything truthy would hide the message from the exported Galaxy GATT service.
    try:
      if (message.message_type != MessageType.SIGNAL or message.member != "PropertiesChanged"
              or message.interface != PROPERTIES or not message.body or message.body[0] != GATT_CHAR):
        return None
      callback = self.callbacks.get(message.path)
      value = message.body[1].get("Value") if len(message.body) > 1 else None
      if callback is not None and value is not None:
        callback(bytes(value.value))
    except Exception:
      LOG.warning("ANCS value handler failed")
    return None


class BluezBond:
  """The real BlueZ calls behind bond.BondAcceptance."""
  def __init__(self, bus, adapter_path, agent_path):
    self.bus = bus
    self.adapter_path = adapter_path
    self.agent_path = agent_path

  async def _call(self, path, interface, member, signature="", body=None):
    return await bluez_call(self.bus, path, interface, member, signature, body)

  async def register_agent(self):
    await self._call("/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os", [self.agent_path, "NoInputNoOutput"])
    try:
      await self._call("/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent", "o", [self.agent_path])
    except Exception:
      await self.unregister_agent()
      raise

  async def unregister_agent(self):
    await self._call("/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent", "o", [self.agent_path])

  async def get_pairable(self):
    return (await self._call(self.adapter_path, PROPERTIES, "Get", "ss", [ADAPTER, "Pairable"]))[0].value

  async def set_pairable(self, value):
    await self._call(self.adapter_path, PROPERTIES, "Set", "ssv", [ADAPTER, "Pairable", Variant("b", bool(value))])

  async def device(self, path):
    try:
      return unwrap((await self._call(path, PROPERTIES, "GetAll", "s", [DEVICE]))[0])
    except DBusError:
      return None

  async def set_trusted(self, path):
    await self._call(path, PROPERTIES, "Set", "ssv", [DEVICE, "Trusted", Variant("b", True)])
