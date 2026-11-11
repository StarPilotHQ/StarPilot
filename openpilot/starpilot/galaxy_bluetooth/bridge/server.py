#!/usr/bin/env python3
"""Add a GATT peripheral beside StarPilot's existing Bluetooth service."""
import argparse
import asyncio
import base64
import json
import hashlib
import logging
import os
from pathlib import Path
import secrets
import signal
import struct
import time

from dbus_next import BusType, DBusError, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method

import claim
from ancs_dbus import BluezBond, BluezGatt, Poster, Watcher, supervise
from bond import BOND_PATH, READY_PATH, BondAcceptance, readiness_error
from protocol import (Assembler, BOND_UUID, INFO_UUID, RX_UUID, SERVICE_UUID, TX_UUID, NOTIFY_UUID,
                      NOTIFICATION_WINDOW, open_message, packets, seal, stream_tag, notification_packets)
from proxy import GalaxyProxy
from pairing import add_phone, introduce, link_address, load_keys, mark_used, remove_phone
from setup_health import write_ready, clear_ready

ROOT = "/link/galaxy/ble"
SERVICE_PATH = ROOT + "/service0"
GATT_SERVICE = "org.bluez.GattService1"
GATT_CHAR = "org.bluez.GattCharacteristic1"
LOG = logging.getLogger("galaxy-ble")
NOTIFICATION_ACK_TIMEOUT = 15


class Session:
  def __init__(self):
    self.challenge = secrets.token_bytes(16)
    self.counter = 0
    self.assembler = Assembler()
    self.response = []
    self.index = 0
    self.busy = False
    self.last_seen = time.monotonic()
    self.task = None
    self.packet_size = 20
    self.read_stream = False
    self.response_started = 0
    self.notification_stream = False
    self.notification_tag = b""
    self.notification_ack = asyncio.Event()
    self.notification_expected_ack = None
    self.notification_task = None
    self.closed = False
    self.phone = None  # (id, key) of the phone whose key opened this session
    self.introduced = False
    self.claiming = False
    self.device = None

  def close(self):
    self.closed = True
    if self.task:
      self.task.cancel()
    if self.notification_task:
      self.notification_task.cancel()


class Gateway:
  def __init__(self, keys, proxy, key_path=None, claim_path=None):
    # keys: {phone id: key}. A bare key is accepted for a single phone.
    self.keys = {"default": keys} if isinstance(keys, (bytes, bytearray)) else dict(keys or {})
    self.key_path = key_path
    self.claim_path = claim_path
    self.claim_lock = asyncio.Lock()
    self.used = set()
    self.proxy = proxy
    self.sessions = {}
    self.notifier = None
    self.notification_lock = asyncio.Lock()
    self.bonder = None

  def refresh_key(self):
    if self.key_path is None:
      return
    try:
      keys = load_keys(self.key_path)
    except Exception:
      # Fail closed if the local credential is removed or invalid.
      keys = {}
    if keys != self.keys:
      # Only sessions of forgotten (or re-keyed) phones are revoked; other phones stay connected.
      for path, state in list(self.sessions.items()):
        if state.phone is None and state.claiming:
          continue
        if state.phone is None or keys.get(state.phone[0]) != state.phone[1]:
          state.close()
          del self.sessions[path]
      self.keys = keys
      LOG.info("Pairing credentials changed; %d phone(s) paired", len(keys))
    if not keys and claim.window(self.claim_path) is None:
      raise DBusError("org.bluez.Error.NotAuthorized", "No phones are paired")

  async def answer_claim(self, state, data):
    """While comma's pairing window is open, give one phone its own key. False when this is not a claim."""
    if self.claim_path is None or self.key_path is None or data[:4] != claim.REQUEST_MAGIC or claim.window(self.claim_path) is None:
      return False
    public, name = claim.parse_request(data)
    claim.validate(public)
    async with self.claim_lock:
      window = claim.window(self.claim_path)
      if window is None:
        raise ValueError("Pairing window closed")
      state.claiming = True
      try:
        phone_id, key = await asyncio.to_thread(add_phone, name, self.key_path)
        state.phone = (phone_id, key)
      finally:
        state.claiming = False
      try:
        body = claim.reply(public, state.challenge, {"key": key.hex(), "id": phone_id, "adapterAddress": window["adapterAddress"],
                                                      "adapterName": window["adapterName"]})
        if not claim.finish(name or "Phone", phone_id, self.claim_path):
          raise ValueError("Pairing window closed")
      except Exception:
        await asyncio.to_thread(remove_phone, phone_id, self.key_path)
        raise
    linked = await asyncio.to_thread(claim.take_link, self.claim_path)
    if linked:
      await asyncio.to_thread(link_address, linked[0], linked[1], self.key_path)
    if state.closed or load_keys(self.key_path).get(phone_id) != key:
      state.closed = True
      state.phone = None
      raise ValueError("Pairing key was removed before delivery")
    frame = struct.pack(">I", len(body)) + body
    state.notification_stream = False
    state.notification_tag = b""
    state.read_stream = True
    state.response = list(packets(frame, state.packet_size))
    state.index = 0
    state.response_started = time.monotonic()
    LOG.info("Phone claimed pairing window")
    return True

  def open_request(self, state, data):
    candidates = [state.phone] if state.phone else list(self.keys.items())
    for phone_id, key in candidates:
      try:
        request = open_message(data, key, "request")
      except ValueError:
        continue
      state.phone = (phone_id, key)
      return request
    raise ValueError("Message is not sealed with a paired phone's key")

  async def watch_key(self):
    while True:
      await asyncio.sleep(0.5)
      try:
        self.refresh_key()
      except DBusError:
        pass

  def session(self, options, fresh=False):
    self.refresh_key()
    device = options.get("device")
    if not device or not isinstance(device.value, str):
      raise DBusError("org.bluez.Error.NotAuthorized", "A device identity is required")
    path = device.value
    for old_path, state in list(self.sessions.items()):
      if time.monotonic() - state.last_seen > 180:
        state.close()
        del self.sessions[old_path]
    if fresh and path in self.sessions:
      self.sessions.pop(path).close()
    if path not in self.sessions:
      if len(self.sessions) >= 4:
        raise DBusError("org.bluez.Error.InProgress", "Too many sessions")
      LOG.info("Galaxy BLE handshake challenge requested; active sessions=%d", len(self.sessions) + 1)
      self.sessions[path] = Session()
      self.sessions[path].device = path
      self.sessions[path].proxy = self.proxy.new_session() if hasattr(self.proxy, "new_session") else self.proxy
    state = self.sessions[path]
    state.last_seen = time.monotonic()
    if "mtu" in options:
      state.packet_size = min(512, max(20, int(options["mtu"].value) - 3))
    return state

  def write(self, value, options):
    state = self.session(options)
    if options.get("offset", Variant("q", 0)).value:
      raise DBusError("org.bluez.Error.InvalidOffset", "Long writes are not used")
    try:
      if len(value) == 13 and value[0] == 4:
        sequence = struct.unpack(">I", value[9:])[0]
        if (not state.notification_stream or not state.response or bytes(value[1:9]) != state.notification_tag
                or sequence != state.notification_expected_ack or state.index != sequence + 1
                or state.notification_ack.is_set()):
          raise ValueError("Unexpected notification acknowledgement")
        state.notification_ack.set()
        state.notification_expected_ack = None
        if state.index == len(state.response):
          self.acknowledged(state)
        return
      if len(value) == 5 and value[0] == 2:
        sequence = struct.unpack(">I", value[1:])[0]
        if state.notification_stream:
          raise ValueError("Notification responses require a tagged acknowledgement")
        elif state.read_stream and state.response:
          if state.index != len(state.response) or sequence != len(state.response) - 1:
            raise ValueError("Unexpected stream acknowledgement")
          self.acknowledged(state)
        elif state.response and sequence == state.index:
          state.index += 1
          if state.index == len(state.response):
            self.acknowledged(state)
        else:
          raise ValueError("Unexpected acknowledgement")
        return
      if state.busy:
        raise DBusError("org.bluez.Error.InProgress", "Previous response has not been acknowledged")
      data = state.assembler.add(bytes(value))
      if data is not None:
        state.busy = True
        state.task = asyncio.create_task(self.process(state, data))
    except ValueError as e:
      state.assembler.reset()
      raise DBusError("org.bluez.Error.InvalidValueLength", str(e)) from e

  def acknowledged(self, state):
    LOG.info("Response delivered: %d packets in %.2fs (%s)", len(state.response),
             time.monotonic() - state.response_started,
             "notification stream" if state.notification_stream else "read stream" if state.read_stream else "per-packet ACK")
    state.response = []
    state.index = 0
    state.busy = False

  def read(self, options):
    state = self.session(options)
    if options.get("offset", Variant("q", 0)).value:
      raise DBusError("org.bluez.Error.InvalidOffset", "Packets fit inside the negotiated MTU")
    if state.notification_stream:
      return b"\x00"
    if not state.response or state.index >= len(state.response):
      return b"\x00"
    packet = state.response[state.index]
    if state.read_stream:
      # ACK once after the read stream is verified.
      state.index += 1
    return packet

  async def process(self, state, data):
    started = time.monotonic()
    try:
      self.refresh_key()
      if state.closed:
        return
      if await self.answer_claim(state, data):
        return
      request = self.open_request(state, data)
      counter = request.get("counter")
      if request.get("session") != state.challenge.hex() or type(counter) is not int or counter <= state.counter:
        raise ValueError("Invalid session or replayed request")
      if not isinstance(request.get("id"), str) or len(request["id"]) > 64:
        raise ValueError("Invalid request identifier")
      if counter > 0xffffffffffffffff:
        raise ValueError("Invalid counter")
      fast = request.get("responseCodec") == 2
      notify = fast and request.get("responseFlow") == "notify-window8"
      if notify and (self.notifier is None or not self.notifier.notifying):
        raise ValueError("Subscribe to notifications before requesting that response flow")
      state.counter = counter  # Consume before invoking any mutating endpoint.
      hello = request.get("phone")
      if self.key_path is not None and not state.introduced and isinstance(hello, dict):
        # Optional {"name", "replaces"}: the app names itself and drops the entry its new key replaced.
        state.introduced = True
        try:
          await asyncio.to_thread(introduce, state.phone[0], hello.get("name"), hello.get("replaces"), self.key_path)
        except (OSError, ValueError):
          LOG.warning("Could not record phone name")
      if self.key_path is not None and state.phone[0] not in self.used:
        self.used.add(state.phone[0])
        try:
          await asyncio.to_thread(mark_used, state.phone[0], self.key_path)
        except (OSError, ValueError):
          LOG.warning("Could not record phone use")
      if request.get("path") == BOND_PATH and request.get("method") == "POST":
        response = await self.bond(state, request["id"])
      else:
        response = await asyncio.to_thread(state.proxy.handle, request)
      self.refresh_key()
      if state.closed:
        return
      response["session"] = state.challenge.hex()
      response["counter"] = counter
      state.notification_stream = notify
      state.notification_tag = stream_tag(state.challenge.hex(), counter) if notify else b""
      state.read_stream = fast and request.get("responseFlow") == "read-stream"
      frame = seal(response, state.phone[1], "response", compact_body=fast)
      size = state.packet_size if fast else min(180, state.packet_size)
      state.response = list(notification_packets(frame, state.notification_tag, size) if notify else packets(frame, size))
      state.index = 0
      state.response_started = time.monotonic()
      LOG.info("Response prepared: %d bytes, %d packets, ATT payload %d, %.2fs (%s)",
               len(frame), len(state.response), size, time.monotonic() - started,
               "notification stream" if notify else "read stream" if state.read_stream else "per-packet ACK")
      if notify:
        state.notification_task = asyncio.create_task(self.push_response(state))
    except asyncio.CancelledError:
      raise
    except Exception:
      # No unauthenticated error oracle; never log payloads or keys.
      LOG.warning("Rejected invalid or unauthenticated BLE message")
      state.busy = False
    finally:
      state.task = None

  async def bond(self, state, request_id):
    async def ready():
      request = {"id": request_id, "method": "GET", "path": READY_PATH, "headers": {"accept": "application/json"}, "body": ""}
      try:
        reply = await asyncio.to_thread(state.proxy.handle, request)
      except Exception:
        reply = None
      return readiness_error(reply if isinstance(reply, dict) else {})

    if self.bonder is None:
      status, body = 503, {"error": "Notification pairing is unavailable", "code": "unavailable"}
    else:
      status, body = await self.bonder.open(state.device, ready)
    return {"id": request_id, "status": status, "headers": {"content-type": "application/json"},
            "body": base64.b64encode(json.dumps(body).encode()).decode()}

  async def push_response(self, state):
    response = state.response
    try:
      # Serialize broadcasts and tag each client’s frames.
      async with self.notification_lock:
        while state.response is response and response and not state.closed:
          if self.notifier is None or not self.notifier.notifying:
            raise RuntimeError("Notification subscription ended")
          window = response[state.index:state.index + NOTIFICATION_WINDOW]
          state.notification_ack.clear()
          state.notification_expected_ack = state.index + len(window) - 1
          for packet in window:
            if state.closed or not self.notifier.notifying:
              raise RuntimeError("Notification session ended")
            state.index += 1
            self.notifier.send(packet)
            # Bound the burst and yield to D-Bus/ATT.
            await asyncio.sleep(0.004)
          await asyncio.wait_for(state.notification_ack.wait(), timeout=NOTIFICATION_ACK_TIMEOUT)
    except asyncio.CancelledError:
      raise
    except Exception as error:
      LOG.warning("Notification transfer interrupted (%s); reconnect to check request outcome", type(error).__name__)
      # Keep the session busy until a fresh INFO handshake replaces it.
    finally:
      if state.notification_task is asyncio.current_task():
        state.notification_task = None


class GattService(ServiceInterface):
  def __init__(self):
    super().__init__(GATT_SERVICE)

  @dbus_property(access=PropertyAccess.READ)
  def UUID(self) -> 's':
    return SERVICE_UUID

  @dbus_property(access=PropertyAccess.READ)
  def Primary(self) -> 'b':
    return True

  def props(self):
    return {"UUID": Variant("s", self.UUID), "Primary": Variant("b", True)}


class Characteristic(ServiceInterface):
  def __init__(self, uuid, flags, gateway, kind):
    super().__init__(GATT_CHAR)
    self.uuid = uuid
    self.flags = flags
    self.gateway = gateway
    self.kind = kind

  @dbus_property(access=PropertyAccess.READ)
  def UUID(self) -> 's':
    return self.uuid

  @dbus_property(access=PropertyAccess.READ)
  def Service(self) -> 'o':
    return SERVICE_PATH

  @dbus_property(access=PropertyAccess.READ)
  def Flags(self) -> 'as':
    return self.flags

  @method()
  def ReadValue(self, options: 'a{sv}') -> 'ay':
    if self.kind == "info":
      if options.get("offset", Variant("q", 0)).value:
        raise DBusError("org.bluez.Error.InvalidOffset", "Invalid offset")
      return b"\x01" + self.gateway.session(options, fresh=True).challenge
    if self.kind == "tx":
      return self.gateway.read(options)
    if self.kind == "bond":
      # encrypt-read: BlueZ only calls this on an encrypted link, so a read on an unbonded link makes iOS pair.
      return b"\x01"
    raise DBusError("org.bluez.Error.NotSupported", "Write-only characteristic")

  @method()
  def WriteValue(self, value: 'ay', options: 'a{sv}'):
    if self.kind != "rx":
      raise DBusError("org.bluez.Error.NotSupported", "Read-only characteristic")
    self.gateway.write(value, options)

  def props(self):
    return {"UUID": Variant("s", self.UUID), "Service": Variant("o", self.Service), "Flags": Variant("as", self.Flags)}


class NotificationCharacteristic(Characteristic):
  def __init__(self, gateway):
    super().__init__(NOTIFY_UUID, ["notify"], gateway, "notify")
    self.value = b""
    self.notifying = False
    gateway.notifier = self

  @dbus_property(access=PropertyAccess.READ)
  def Value(self) -> 'ay':
    return self.value

  @dbus_property(access=PropertyAccess.READ)
  def Notifying(self) -> 'b':
    return self.notifying

  @method()
  def StartNotify(self):
    if not self.notifying:
      self.notifying = True
      self.emit_properties_changed({"Notifying": True})
      LOG.info("Bluetooth push enabled")

  @method()
  def StopNotify(self):
    self.notifying = False
    self.emit_properties_changed({"Notifying": False})
    LOG.info("Bluetooth push disabled")
    for state in self.gateway.sessions.values():
      if state.notification_task:
        state.notification_task.cancel()

  def send(self, packet):
    if not self.notifying:
      raise RuntimeError("Not subscribed")
    self.value = bytes(packet)
    self.emit_properties_changed({"Value": self.value})

  def props(self):
    return {**super().props(), "Value": Variant("ay", self.value), "Notifying": Variant("b", self.notifying)}


class BondAgent(ServiceInterface):
  """org.bluez.Agent1 that approves only the device the bond window is scoped to."""
  def __init__(self, allows):
    super().__init__("org.bluez.Agent1")
    self.allows = allows

  def _check(self, device):
    if not self.allows(device):
      raise DBusError("org.bluez.Error.Rejected", "Not the phone this window is for")

  @method()
  def Release(self):
    pass

  @method()
  def RequestPinCode(self, device: 'o') -> 's':
    raise DBusError("org.bluez.Error.Rejected", "PIN pairing is not used")

  @method()
  def DisplayPinCode(self, device: 'o', pincode: 's'):
    raise DBusError("org.bluez.Error.Rejected", "PIN pairing is not used")

  @method()
  def RequestPasskey(self, device: 'o') -> 'u':
    raise DBusError("org.bluez.Error.Rejected", "Passkey pairing is not used")

  @method()
  def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):
    raise DBusError("org.bluez.Error.Rejected", "Passkey pairing is not used")

  @method()
  def RequestConfirmation(self, device: 'o', passkey: 'u'):
    self._check(device)

  @method()
  def RequestAuthorization(self, device: 'o'):
    self._check(device)

  @method()
  def AuthorizeService(self, device: 'o', uuid: 's'):
    self._check(device)

  @method()
  def Cancel(self):
    pass


class Application(ServiceInterface):
  def __init__(self, objects):
    super().__init__("org.freedesktop.DBus.ObjectManager")
    self.objects = objects

  @method()
  def GetManagedObjects(self) -> 'a{oa{sa{sv}}}':
    return {path: {interface.name: interface.props()} for path, interface in self.objects.items()}


class Advertisement(ServiceInterface):
  def __init__(self):
    super().__init__("org.bluez.LEAdvertisement1")
    self.released = False

  @dbus_property(access=PropertyAccess.READ)
  def Type(self) -> 's':
    return "peripheral"

  @dbus_property(access=PropertyAccess.READ)
  def ServiceUUIDs(self) -> 'as':
    return [SERVICE_UUID]

  @dbus_property(access=PropertyAccess.READ)
  def LocalName(self) -> 's':
    return "Galaxy"

  @method()
  def Release(self):
    self.released = True
    LOG.warning("BLE advertisement released by BlueZ; bridge will re-register after restart")


def create_key(path):
  path.parent.mkdir(parents=True, exist_ok=True)
  fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
  key = secrets.token_bytes(32)
  with os.fdopen(fd, "w") as f:
    json.dump({"key": key.hex()}, f)
  return key


async def check_adapter(properties, advertisement):
  if advertisement.released:
    raise RuntimeError("BlueZ released the Galaxy advertisement")
  if not (await asyncio.wait_for(properties.call_get("org.bluez.Adapter1", "Powered"), timeout=5)).value:
    raise RuntimeError("Bluetooth switched off")


async def serve(args):
  LOG.info("Starting Galaxy bridge pid=%d source=%s", os.getpid(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12])  # noqa: ASYNC240 -- once at startup
  key_path = Path(args.key_file)
  clear_ready(key_path.parent)
  keys = load_keys(key_path)
  bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
  gateway = Gateway(keys, GalaxyProxy(args.galaxy_port), key_path, claim.CLAIM_PATH)
  objects = {
      SERVICE_PATH: GattService(),
      SERVICE_PATH + "/rx": Characteristic(RX_UUID, ["write"], gateway, "rx"),
      SERVICE_PATH + "/tx": Characteristic(TX_UUID, ["read"], gateway, "tx"),
      SERVICE_PATH + "/info": Characteristic(INFO_UUID, ["read"], gateway, "info"),
      SERVICE_PATH + "/notify": NotificationCharacteristic(gateway),
      SERVICE_PATH + "/bond": Characteristic(BOND_UUID, ["encrypt-read"], gateway, "bond"),
  }
  bus.export(ROOT, Application(objects))
  for path, interface in objects.items():
    bus.export(path, interface)
  ad_path = ROOT + "/advertisement"
  advertisement = Advertisement()
  bus.export(ad_path, advertisement)
  introspection = await bus.introspect("org.bluez", "/")
  manager = bus.get_proxy_object("org.bluez", "/", introspection).get_interface("org.freedesktop.DBus.ObjectManager")
  managed = await manager.call_get_managed_objects()
  adapters = [path for path, interfaces in managed.items()
              if "org.bluez.GattManager1" in interfaces and "org.bluez.LEAdvertisingManager1" in interfaces]
  if args.adapter:
    adapters = [p for p in adapters if p.endswith("/" + args.adapter)]
  if not adapters:
    raise RuntimeError("No BlueZ adapter exposes GATT server and LE advertising. Enable Bluetooth in StarPilot and inspect bluetoothctl show.")
  adapter_path = adapters[0]
  obj = bus.get_proxy_object("org.bluez", adapter_path, await bus.introspect("org.bluez", adapter_path))
  properties = obj.get_interface("org.freedesktop.DBus.Properties")
  if not (await properties.call_get("org.bluez.Adapter1", "Powered")).value:
    raise RuntimeError("Bluetooth is off. Enable it in StarPilot before starting the bridge.")
  gatt = obj.get_interface("org.bluez.GattManager1")
  advertising = obj.get_interface("org.bluez.LEAdvertisingManager1")
  LOG.info("Registering Galaxy GATT application on %s", adapter_path)
  await gatt.call_register_application(ROOT, {})
  watcher = asyncio.create_task(gateway.watch_key())
  agent_path = ROOT + "/agent"
  bonder = BondAcceptance(BluezBond(bus, adapter_path, agent_path), claim.CLAIM_PATH)
  bus.export(agent_path, BondAgent(bonder.allows))
  gateway.bonder = bonder
  poster = Poster(GalaxyProxy(args.galaxy_port))
  gatt_client = BluezGatt(bus)
  notifications = asyncio.create_task(supervise(lambda: Watcher(gatt_client, adapter_path, poster.post)))
  try:
    LOG.info("Registering Galaxy BLE advertisement")
    await advertising.call_register_advertisement(ad_path, {})
    write_ready(key_path.parent)
    LOG.info("Galaxy BLE bridge ready on %s; forwarding to localhost:%s", adapter_path, args.galaxy_port)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
      loop.add_signal_handler(sig, stopped.set)
    async def monitor_adapter():
      while not stopped.is_set():
        await asyncio.sleep(2)
        await check_adapter(properties, advertisement)
    monitor = asyncio.create_task(monitor_adapter())
    stop_waiter = asyncio.create_task(stopped.wait())
    disconnected = asyncio.create_task(bus.wait_for_disconnect())
    try:
      done, _ = await asyncio.wait((monitor, stop_waiter, disconnected), return_when=asyncio.FIRST_COMPLETED)
      if disconnected in done:
        raise RuntimeError("System D-Bus connection closed")
      for task in done:
        task.result()
    finally:
      monitor.cancel()
      stop_waiter.cancel()
      disconnected.cancel()
      await asyncio.gather(monitor, stop_waiter, disconnected, return_exceptions=True)
  finally:
    LOG.info("Stopping Galaxy bridge; clearing ready status and Bluetooth sessions")
    clear_ready(key_path.parent)
    watcher.cancel()
    notifications.cancel()
    for task in (watcher, notifications):
      try:
        await task
      except (asyncio.CancelledError, Exception):
        pass
    try:
      await asyncio.wait_for(bonder.close(), timeout=15)
    except Exception:
      pass
    for state in gateway.sessions.values():
      state.close()
    try:
      await asyncio.wait_for(advertising.call_unregister_advertisement(ad_path), timeout=5)
    except Exception:
      pass
    try:
      await asyncio.wait_for(gatt.call_unregister_application(ROOT), timeout=5)
    finally:
      bus.disconnect()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--key-file", default="/data/galaxy-ble/pairing.json")
  parser.add_argument("--galaxy-port", type=int, default=8082)
  parser.add_argument("--adapter", help="Optional adapter, e.g. hci0")
  parser.add_argument("--init-key", action="store_true", help="Create a private pairing key and print it once")
  args = parser.parse_args()
  if args.init_key:
    print(create_key(Path(args.key_file)).hex())
    return
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
  asyncio.run(serve(args))


if __name__ == "__main__":
  main()
