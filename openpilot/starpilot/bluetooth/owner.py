from __future__ import annotations

from collections.abc import Callable
from queue import Full
import fcntl
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from jeepney import DBusAddress, MatchRule, new_error, new_method_call, new_method_return
from jeepney.io.threading import DBusRouter, open_dbus_connection
from jeepney.low_level import HeaderFields, MessageType
from jeepney.wrappers import Properties

from openpilot.starpilot.bluetooth.radio_preference import RadioPreference


RADIO_HELPER = Path('/usr/comma/bluetooth-radio')
RADIO_UNIT = 'starpilot-bluetooth-radio.service'
ADDRESS = re.compile(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\Z')
# iPhones only list nearby devices that look like audio gear, so the window presents as a loudspeaker.
SPEAKER_CLASS = (4, 5)


def set_device_class(major: int, minor: int) -> bool:
  # btmgmt takes the minor class as its byte in the class field (shifted past the two format bits).
  for command in (['sudo', '-n', 'btmgmt', '--index', '0', 'class', str(major), str(minor << 2)],
                  ['sudo', '-n', 'hciconfig', 'hci0', 'class', f'{(major << 8) | (minor << 2):#08x}']):
    try:
      if subprocess.run(command, capture_output=True, timeout=5).returncode == 0:
        return True
    except (OSError, subprocess.TimeoutExpired):
      pass
  return False


BLUEZ = 'org.bluez'
ADAPTER = 'org.bluez.Adapter1'
DEVICE = 'org.bluez.Device1'
AGENT = 'org.bluez.Agent1'
AGENT_PATH = '/org/starpilot/bluetooth/pairing'
ADMISSION_LOCK = Path('/data/starpilot/bluetooth-owner.lock')


class PairingSession:
  SECONDS = 60.0

  def __init__(self, identity: tuple, address: str, path: str, parked: Callable[[], bool], clock: Callable[[], float]):
    self.identity, self.address, self.path = identity, address, path
    self.parked, self.clock = parked, clock
    self.deadline = clock() + self.SECONDS
    self.condition = threading.Condition()
    self.prompt: dict | None = None
    self.response: tuple[bool, str] | None = None
    self.state = 'pairing'
    self.callbacks_closed = False
    self.cancel_callback: Callable[[], None] | None = None
    self.done = threading.Event()

  def cancel(self) -> None:
    with self.condition:
      if self.state != 'pairing':
        return
      self.state = 'canceled'
      self.prompt = None
      self.condition.notify_all()
      callback = self.cancel_callback
    if callback is not None:
      threading.Thread(target=callback, daemon=True).start()

  def finish(self, paired: bool) -> None:
    with self.condition:
      if self.state == 'pairing':
        if not self.parked() or self.clock() >= self.deadline:
          self.state = 'canceled'
        else:
          self.state = 'paired' if paired else 'failed'
      self.prompt = None
      self.condition.notify_all()
    self.done.set()

  def close_callbacks(self) -> None:
    with self.condition:
      self.callbacks_closed = True
      self.prompt = None
      self.condition.notify_all()

  def ask(self, kind: str, path: str, value: str = '') -> tuple[bool, str]:
    with self.condition:
      if (self.state != 'pairing' or self.callbacks_closed or path != self.path or
          (self.prompt is not None and not self.prompt['displayOnly']) or
          not self.parked() or self.clock() >= self.deadline):
        return False, ''
      prompt_id = uuid.uuid4().hex
      self.prompt = {'id': prompt_id, 'kind': kind, 'value': value[:16], 'displayOnly': False}
      self.response = None
      while self.response is None and self.state == 'pairing' and not self.callbacks_closed and self.parked():
        remaining = self.deadline - self.clock()
        if remaining <= 0:
          break
        self.condition.wait(min(remaining, 0.25))
      result = (self.response if self.response is not None and self.state == 'pairing' and not self.callbacks_closed and
                self.parked() and self.clock() < self.deadline else (False, ''))
      self.response = None
      self.prompt = None
      return result

  def display(self, kind: str, path: str, value: str) -> bool:
    with self.condition:
      if (self.state != 'pairing' or self.callbacks_closed or path != self.path or
          (self.prompt is not None and not self.prompt['displayOnly']) or
          not self.parked() or self.clock() >= self.deadline):
        return False
      self.prompt = {'id': uuid.uuid4().hex, 'kind': kind, 'value': value[:16], 'displayOnly': True}
      return True

  def respond(self, identity: tuple, prompt_id: str, accepted: bool, value: str) -> bool:
    with self.condition:
      prompt = self.prompt
      if (self.state != 'pairing' or self.callbacks_closed or identity != self.identity or not self.parked() or
          self.clock() >= self.deadline or prompt is None or prompt['id'] != prompt_id or
          prompt['displayOnly'] or self.response is not None):
        return False
      kind = prompt['kind']
      if accepted and ((kind == 'passkey' and (not re.fullmatch(r'[0-9]{1,6}', value) or int(value) > 999999)) or
                       (kind == 'pin' and (not 1 <= len(value) <= 16 or not value.isascii() or not value.isprintable())) or
                       (kind not in ('pin', 'passkey') and value)):
        return False
      self.response = accepted, value if accepted else ''
      self.condition.notify_all()
      return True


class OpenPairingSession(PairingSession):
  """A short window in which the first phone to ask is the one paired."""
  SECONDS = 120.0

  def __init__(self, identity: tuple, parked: Callable[[], bool], clock: Callable[[], float]):
    super().__init__(identity, '', '', parked, clock)
    self.name = ''
    self.uuids: tuple[str, ...] = ()

  def claim(self, path: str, address: str, name: str) -> bool:
    with self.condition:
      if self.state != 'pairing' or self.callbacks_closed:
        return False
      if not self.path:
        self.path, self.address, self.name = path, address, name[:80]
      return self.path == path

  def ask(self, kind: str, path: str, value: str = '') -> tuple[bool, str]:
    # The signed-in owner opened this window for their own phone; a confirmation is shown, not waited on.
    if kind not in ('confirmation', 'authorization'):
      return super().ask(kind, path, value)
    return (True, '') if self.display(kind, path, value) else (False, '')


class BluetoothUnavailable(Exception):
  def __init__(self, message: str, *, code: str = 'service_unavailable'):
    super().__init__(message)
    self.code = code


class BluetoothRejected(Exception):
  def __init__(self, message: str, *, code: str = 'changed'):
    super().__init__(message)
    self.code = code


class AdapterBusyElsewhere(BluetoothRejected):
  def __init__(self, message: str):
    super().__init__(message, code='busy')


def normalized_address(value: str) -> str:
  if not isinstance(value, str) or not ADDRESS.fullmatch(value.upper()):
    raise ValueError('Invalid Bluetooth address')
  return value.upper()


def _unwrap(value: Any) -> Any:
  if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
    return _unwrap(value[1])
  if isinstance(value, dict):
    return {key: _unwrap(item) for key, item in value.items()}
  if isinstance(value, list):
    return [_unwrap(item) for item in value]
  return value


class BlueZ:
  def __init__(self):
    self.router = DBusRouter(open_dbus_connection(bus='SYSTEM'))

  def close(self) -> None:
    try:
      self.router.close()
    finally:
      self.router.conn.close()

  def _call(self, path: str, interface: str, member: str, signature: str | None = None, body: tuple = (), timeout: float = 10.0):
    address = DBusAddress(path, bus_name=BLUEZ, interface=interface)
    message = new_method_call(address, member, signature, body) if signature else new_method_call(address, member)
    reply = self.router.send_and_get_reply(message, timeout=timeout)
    if reply.header.message_type == MessageType.error:
      error_name = str(reply.header.fields.get(HeaderFields.error_name, 'Bluetooth operation failed'))
      if error_name.startswith('org.freedesktop.DBus.Error.'):
        raise BluetoothUnavailable('Bluetooth service could not be reached')
      raise BluetoothRejected(error_name)
    return reply.body

  def _objects(self) -> dict:
    return _unwrap(self._call('/', 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects')[0])

  @staticmethod
  def _adapter(objects: dict) -> tuple[str, dict]:
    return next(((path, interfaces[ADAPTER]) for path, interfaces in objects.items() if ADAPTER in interfaces), ('', {}))

  @staticmethod
  def _devices(objects: dict) -> list[dict]:
    devices = []
    for path, interfaces in objects.items():
      if DEVICE not in interfaces:
        continue
      props = interfaces[DEVICE]
      try:
        address = normalized_address(props.get('Address', ''))
      except ValueError:
        continue
      def named(value):
        text = str(value or '').strip()
        return text if text and not ADDRESS.fullmatch(text.replace('-', ':').upper()) else ''

      name = named(props.get('Name')) or named(props.get('Alias'))
      known = props.get('Paired') is True or props.get('Connected') is True
      profiles = {str(value).lower() for value in props.get('UUIDs', ())}
      audio_profiles = {f"0000{service}-0000-1000-8000-00805f9b34fb" for service in
                        ('1108', '110a', '110b', '110c', '110e', '110f', '1112', '111e', '111f', '1124', '1812')}
      relevant = bool(profiles & audio_profiles or '4de17a00-52cb-11e6-bdf4-0800200c9a66' in profiles)
      device_class = props.get('Class', 0)
      major_class = (device_class >> 8) & 0x1f if type(device_class) is int else 0
      relevant = relevant or major_class in (2, 4, 5) or props.get('LegacyPairing') is True
      if not known and (props.get('Blocked') is True or not name and not relevant):
        continue
      name = name or (f"Audio Device · {address[-5:]}" if major_class == 4 else
                      f"Bluetooth Device · {address[-5:]}" if relevant else address)
      devices.append({'path': path, 'address': address, 'name': str(name)[:80],
                      'paired': props.get('Paired') is True, 'connected': props.get('Connected') is True,
                      'trusted': props.get('Trusted') is True,
                      'audioSink': '0000110b-0000-1000-8000-00805f9b34fb' in profiles})
    return devices

  def snapshot(self) -> dict:
    objects = self._objects()
    _, adapter = self._adapter(objects)
    devices = sorted(self._devices(objects), key=lambda item: (not item['connected'], not item['paired'], item['name']))
    return {'adapter': bool(adapter), 'powered': adapter.get('Powered') is True,
            'discovering': adapter.get('Discovering') is True,
            'adapterAddress': str(adapter.get('Address', '')).upper(), 'adapterName': str(adapter.get('Alias') or adapter.get('Name') or ''),
            'devices': [{key: value for key, value in item.items() if key != 'path'} for item in devices[:64]]}

  def _device_path(self, address: str) -> str:
    for item in self._devices(self._objects()):
      if item['address'] == address:
        return item['path']
    raise BluetoothRejected('Device is no longer available')

  def operation(self, operation: str, address: str | None = None) -> None:
    objects = self._objects()
    adapter_path, _ = self._adapter(objects)
    if not adapter_path:
      raise BluetoothUnavailable('Bluetooth adapter is unavailable', code='adapter_unavailable')
    if operation in ('scan', 'stop_scan'):
      self._call(adapter_path, ADAPTER, 'StartDiscovery' if operation == 'scan' else 'StopDiscovery')
      return
    if address is None:
      raise ValueError('Bluetooth address required')
    path = self._device_path(address)
    if operation == 'forget':
      self._call(adapter_path, ADAPTER, 'RemoveDevice', 'o', (path,))
    elif operation in ('connect', 'disconnect'):
      self._call(path, DEVICE, operation.capitalize())
    else:
      raise ValueError('Unsupported Bluetooth operation')

  def set_powered(self, enabled: bool) -> None:
    path, _ = self._adapter(self._objects())
    if not path:
      raise BluetoothUnavailable('Bluetooth adapter is unavailable', code='adapter_unavailable')
    address = DBusAddress(path, bus_name=BLUEZ, interface=ADAPTER)
    reply = self.router.send_and_get_reply(Properties(address).set('Powered', 'b', enabled), timeout=10.0)
    if reply.header.message_type == MessageType.error:
      raise BluetoothRejected('Bluetooth power change failed')

  def cancel_pair(self, path: str) -> None:
    try:
      self._call(path, DEVICE, 'CancelPairing', timeout=5.0)
    except (BluetoothRejected, OSError, RuntimeError):
      pass

  def _bluez_sender(self) -> str:
    address = DBusAddress('/org/freedesktop/DBus', bus_name='org.freedesktop.DBus', interface='org.freedesktop.DBus')
    message = new_method_call(address, 'GetNameOwner', 's', (BLUEZ,))
    reply = self.router.send_and_get_reply(message, timeout=5.0)
    if reply.header.message_type == MessageType.error or len(reply.body) != 1 or not isinstance(reply.body[0], str) or \
       not reply.body[0].startswith(':'):
      raise BluetoothUnavailable('BlueZ identity is unavailable')
    return reply.body[0]

  def adapter_identity(self) -> tuple[str, dict[str, Any], str]:
    path, props = self._adapter(self._objects())
    if not path:
      raise BluetoothUnavailable('Bluetooth adapter is unavailable')
    return path, props, self._bluez_sender()

  set_class = staticmethod(set_device_class)

  def set_pairing_visibility(self, path: str, *, pairable: bool, discoverable: bool) -> None:
    address = DBusAddress(path, bus_name=BLUEZ, interface=ADAPTER)
    for key, value in (('Pairable', pairable), ('Discoverable', discoverable)):
      reply = self.router.send_and_get_reply(Properties(address).set(key, 'b', value), timeout=10.0)
      if reply.header.message_type == MessageType.error:
        raise BluetoothRejected(f'Bluetooth {key} change failed')

  def pair(self, address: str, expected_path: str, session: PairingSession) -> bool:
    path = self._device_path(address)
    if path != expected_path:
      raise BluetoothRejected('Device changed before pairing')

    def action(pair_succeeded: threading.Event) -> bool:
      if session.state != 'pairing' or session.callbacks_closed or not session.parked() or session.clock() >= session.deadline:
        return False
      self._call(path, DEVICE, 'Pair', timeout=70.0)
      paired = next((item['paired'] for item in self._devices(self._objects()) if item['address'] == address), False)
      if paired:
        if session.state != 'pairing' or not session.parked() or session.clock() >= session.deadline:
          return False
        self._call(path, 'org.freedesktop.DBus.Properties', 'Set', 'ssv', (DEVICE, 'Trusted', ('b', True)))
        pair_succeeded.set()
      return paired
    return self._serve_agent(session, lambda device_path: device_path == path, action)

  def accept_pair(self, session: OpenPairingSession, journal: Callable[[str, str, bool, bool], None],
                  restored: Callable[[], None]) -> bool:
    """Let one nearby phone pair with the comma while the pairing agent is registered."""
    adapter, props, sender = self.adapter_identity()
    if props.get('Powered') is not True or props.get('Discovering') is True:
      raise BluetoothUnavailable('Bluetooth adapter is not ready for phone pairing')
    pairable, discoverable = props.get('Pairable') is True, props.get('Discoverable') is True
    device_class = props.get('Class')
    original_class = device_class if type(device_class) is int else 0

    def claim(device_path: str) -> bool:
      device = self._objects().get(device_path, {}).get(DEVICE)
      if device is None or device.get('Paired') is True or device.get('Blocked') is True:
        return False
      device_class = device.get('Class', 0)
      # Phones only: a nearby speaker, car or accessory cannot take the window.
      if type(device_class) is not int or (device_class >> 8) & 0x1f != 2:
        return False
      address = str(device.get('Address', '')).upper()
      return ADDRESS.fullmatch(address) is not None and session.claim(device_path, address, str(device.get('Alias') or device.get('Name') or ''))

    def action(pair_succeeded: threading.Event) -> bool:
      if session.state != 'pairing' or not session.parked() or session.clock() >= session.deadline:
        return False
      if (original_class >> 8) & 0x1f != SPEAKER_CLASS[0]:
        self.class_changed = self.set_class(*SPEAKER_CLASS)
      self.set_pairing_visibility(adapter, pairable=True, discoverable=True)
      while session.state == 'pairing' and session.parked() and session.clock() < session.deadline:
        if session.path:
          device = self._objects().get(session.path, {}).get(DEVICE, {})
          if device.get('Paired') is True:
            self._call(session.path, 'org.freedesktop.DBus.Properties', 'Set', 'ssv', (DEVICE, 'Trusted', ('b', True)))
            pair_succeeded.set()
            # Service records arrive just after the bond; the music source needs them.
            for _ in range(20):
              if device.get('UUIDs') or session.clock() >= session.deadline:
                break
              time.sleep(0.5)
              device = self._objects().get(session.path, {}).get(DEVICE, {})
            session.uuids = tuple(str(value).lower() for value in device.get('UUIDs', ()))
            return True
        time.sleep(0.25)
      return False

    journal(adapter, sender, pairable, discoverable)
    self.class_changed = False
    try:
      return self._serve_agent(session, claim, action, default_agent=True)
    finally:
      now_adapter, _, now_sender = self.adapter_identity()
      if (now_adapter, now_sender) == (adapter, sender):
        self.set_pairing_visibility(adapter, pairable=pairable, discoverable=discoverable)
      if self.class_changed:
        self.set_class((original_class >> 8) & 0x1f, (original_class >> 2) & 0x3f)
      # Kept on failure above so the next owner can still undo the window.
      restored()

  def _serve_agent(self, session: PairingSession, target: Callable[[str], bool],
                   action: Callable[[threading.Event], bool], *, default_agent: bool = False) -> bool:
    bluez_sender = self._bluez_sender()
    agent_filter = self.router.filter(MatchRule(type='method_call', interface=AGENT, path=AGENT_PATH), bufsize=16)
    queue = agent_filter.__enter__()
    finished = threading.Event()
    pair_succeeded = threading.Event()
    slots = threading.BoundedSemaphore(4)
    handlers: set[threading.Thread] = set()
    handlers_lock = threading.Lock()

    def reply(message, *, accepted: bool, signature: str | None = None, body: tuple = ()):
      result = new_method_return(message, signature, body) if accepted else new_error(message, 'org.bluez.Error.Rejected', 's', ('Pairing rejected',))
      self.router.send(result)

    def handle(message):
      member = message.header.fields.get(HeaderFields.member, '')
      body = message.body
      try:
        if message.header.fields.get(HeaderFields.sender) != bluez_sender:
          reply(message, accepted=False)
          return
        if member in ('Release', 'Cancel'):
          if body:
            reply(message, accepted=False)
            return
          if member == 'Cancel' or not pair_succeeded.is_set():
            session.cancel()
          reply(message, accepted=True)
          return
        if not body or type(body[0]) is not str or not target(body[0]):
          reply(message, accepted=False)
          return
        device_path = body[0]
        if member == 'RequestPinCode':
          if len(body) != 1:
            reply(message, accepted=False)
            return
          allowed, value = session.ask('pin', device_path)
          reply(message, accepted=allowed, signature='s', body=(value,))
        elif member == 'RequestPasskey':
          if len(body) != 1:
            reply(message, accepted=False)
            return
          allowed, value = session.ask('passkey', device_path)
          reply(message, accepted=allowed, signature='u', body=(int(value),) if allowed else ())
        elif member == 'RequestConfirmation':
          if len(body) != 2 or type(body[1]) is not int or not 0 <= body[1] <= 999999:
            reply(message, accepted=False)
            return
          allowed, _ = session.ask('confirmation', device_path, f'{body[1]:06d}')
          reply(message, accepted=allowed)
        elif member == 'RequestAuthorization':
          if len(body) != 1:
            reply(message, accepted=False)
            return
          allowed, _ = session.ask('authorization', device_path)
          reply(message, accepted=allowed)
        elif member == 'AuthorizeService':
          if len(body) != 2 or type(body[1]) is not str or len(body[1]) > 64:
            reply(message, accepted=False)
            return
          allowed, _ = session.ask('authorization', device_path)
          reply(message, accepted=allowed)
        elif member == 'DisplayPinCode':
          if (len(body) != 2 or type(body[1]) is not str or not 1 <= len(body[1]) <= 16 or
              not body[1].isascii() or not body[1].isprintable()):
            reply(message, accepted=False)
            return
          reply(message, accepted=session.display('display_pin', device_path, body[1]))
        elif member == 'DisplayPasskey':
          if (len(body) != 3 or type(body[1]) is not int or not 0 <= body[1] <= 999999 or
              type(body[2]) is not int or not 0 <= body[2] <= 6):
            reply(message, accepted=False)
            return
          reply(message, accepted=session.display('display_passkey', device_path, f'{body[1]:06d}'))
        else:
          reply(message, accepted=False)
      except (OSError, RuntimeError, ValueError, TypeError, IndexError):
        session.cancel()
        try:
          reply(message, accepted=False)
        except (OSError, RuntimeError):
          pass

    def agent_loop():
      while not finished.is_set():
        message = queue.get()
        if message is None or finished.is_set():
          return
        if not slots.acquire(blocking=False):
          try:
            reply(message, accepted=False)
          except (OSError, RuntimeError):
            pass
          continue
        def bounded_handle(message=message):
          try:
            handle(message)
          finally:
            with handlers_lock:
              handlers.discard(threading.current_thread())
            slots.release()
        handler = threading.Thread(target=bounded_handle, daemon=True)
        with handlers_lock:
          handlers.add(handler)
        handler.start()

    worker = threading.Thread(target=agent_loop, daemon=True)
    worker.start()
    registered = False
    try:
      self._call('/org/bluez', 'org.bluez.AgentManager1', 'RegisterAgent', 'os', (AGENT_PATH, 'KeyboardDisplay'))
      registered = True
      if default_agent:
        self._call('/org/bluez', 'org.bluez.AgentManager1', 'RequestDefaultAgent', 'o', (AGENT_PATH,))
      return action(pair_succeeded)
    finally:
      session.close_callbacks()
      try:
        if registered:
          try:
            self._call('/org/bluez', 'org.bluez.AgentManager1', 'UnregisterAgent', 'o', (AGENT_PATH,))
          except (BluetoothRejected, OSError, RuntimeError):
            pass
      finally:
        finished.set()
        try:
          queue.put_nowait(None)
        except Full:
          pass
        agent_filter.__exit__(None, None, None)
        worker.join(timeout=1.0)
        with handlers_lock:
          pending = tuple(handlers)
        for handler in pending:
          handler.join(timeout=1.0)


class BluetoothOwner:
  OPERATIONS = frozenset(('power', 'scan', 'stop_scan', 'connect', 'disconnect', 'forget', 'pair', 'accept_pair', 'pairing_response', 'cancel_pair'))
  SCAN_SECONDS = 20.0

  def __init__(self, parked: Callable[[], bool], *, bluez_factory=BlueZ, radio_helper: Path = RADIO_HELPER,
               systemctl: Callable[..., Any] = subprocess.run, clock: Callable[[], float] = time.monotonic,
               timer_factory: Callable[..., Any] = threading.Timer,
               session_valid: Callable[[tuple], bool] = lambda _identity: True,
               admission_lock: Path = ADMISSION_LOCK, radio_preference: RadioPreference | None = None,
               on_paired: Callable[[str, str, tuple[str, ...]], None] | None = None):
    self.parked = parked
    self.on_paired = on_paired
    self.bluez_factory = bluez_factory
    self.radio_helper = radio_helper
    self.radio_preference = radio_preference if radio_preference is not None else RadioPreference()
    self.systemctl = systemctl
    self.clock = clock
    self.timer_factory = timer_factory
    self.session_valid = session_valid
    self.lock = threading.RLock()
    self.client: BlueZ | None = None
    self.scan_deadline = 0.0
    self.scan_timer: Any | None = None
    self.scan_generation = 0
    self.close_pending = False
    self.pairing: PairingSession | None = None
    self.admission_lock = admission_lock
    self.admission_file = None
    self.power_change_active = False

  def _admission_enter(self) -> None:
    if self.admission_file is not None:
      return
    self.admission_lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(self.admission_lock, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    stream = os.fdopen(fd, 'r+')
    try:
      fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
      stream.close()
      raise AdapterBusyElsewhere('Bluetooth adapter is busy in another process') from None
    self.admission_file = stream

  def _admission_busy(self) -> bool:
    return bool(self.scan_deadline or (self.pairing is not None and not self.pairing.done.is_set()))

  def _admission_leave(self) -> None:
    if not self.power_change_active and not self._admission_busy() and self.admission_file is not None:
      self.admission_file.close()
      self.admission_file = None

  def _client(self) -> BlueZ:
    if self.client is None:
      self.client = self.bluez_factory()
    return self.client

  def _stop_scan(self) -> None:
    self.scan_generation += 1
    if self.scan_timer is not None:
      self.scan_timer.cancel()
      self.scan_timer = None
    if self.scan_deadline:
      try:
        self._client().operation('stop_scan')
      finally:
        self.scan_deadline = 0.0

  def _expire_scan(self, generation: int) -> None:
    with self.lock:
      if generation != self.scan_generation or not self.scan_deadline:
        return
      if self.clock() < self.scan_deadline:
        remaining = self.scan_deadline - self.clock()
        timer = self.timer_factory(remaining, self._expire_scan, args=(generation,))
        timer.daemon = True
        self.scan_timer = timer
        timer.start()
        return
      try:
        self._stop_scan()
        self._admission_leave()
      except Exception:
        self.close()

  def _start_scan_lease(self) -> None:
    self.scan_generation += 1
    if self.scan_timer is not None:
      self.scan_timer.cancel()
    self.scan_deadline = self.clock() + self.SCAN_SECONDS
    timer = self.timer_factory(self.SCAN_SECONDS, self._expire_scan, args=(self.scan_generation,))
    timer.daemon = True
    self.scan_timer = timer
    timer.start()

  def _close_locked(self) -> None:
    self.close_pending = False
    if self.pairing is not None:
      self.pairing.cancel()
    try:
      self._stop_scan()
    except Exception:
      pass
    if self.client is not None:
      try:
        self.client.close()
      except Exception:
        pass
      finally:
        self.client = None
    self._admission_leave()

  def close(self) -> None:
    if not self.lock.acquire(blocking=False):
      self.close_pending = True
      return
    try:
      self._close_locked()
    finally:
      self.lock.release()

  def snapshot(self, *, session: tuple | None = None) -> dict:
    parked = self.parked()
    result: dict[str, Any] = {'version': 1, 'available': self.radio_helper.is_file(), 'parked': parked,
              'powered': False, 'discovering': False, 'devices': [], 'errorCode': None, 'pairing': None}
    if not result['available']:
      with self.lock:
        self._close_locked()
      result['errorCode'] = 'radio_unavailable'
      return result
    with self.lock:
      try:
        if self.pairing is not None and not self.session_valid(self.pairing.identity):
          self.pairing.cancel()
        if self.scan_deadline and (self.clock() >= self.scan_deadline):
          self._stop_scan()
          self._admission_leave()
        result.update(self._client().snapshot())
        if not result.get('adapter'):
          result['errorCode'] = 'adapter_unavailable'
      except Exception:
        try:
          result['errorCode'] = 'service_unavailable' if self.radio_preference.enabled() else None
        except OSError:
          result['errorCode'] = 'radio_preference_unavailable'
        self._close_locked()
      finally:
        if self.close_pending:
          self._close_locked()
      pairing = self.pairing
      if pairing is not None and session == pairing.identity:
        with pairing.condition:
          info: dict[str, Any] = {'address': pairing.address, 'state': pairing.state,
                                  'prompt': dict(pairing.prompt) if pairing.prompt is not None else None}
          if isinstance(pairing, OpenPairingSession):
            info.update(incoming=True, name=pairing.name)
          result['pairing'] = info
      else:
        result['pairing'] = None
    return result

  def _pair_worker(self, pairing: PairingSession) -> None:
    client = None
    paired = False
    try:
      client = self.bluez_factory()
      with pairing.condition:
        pairing.cancel_callback = lambda: client.cancel_pair(pairing.path)
        canceled = pairing.state != 'pairing'
      if not canceled:
        paired = client.pair(pairing.address, pairing.path, pairing)
    except (BluetoothRejected, BluetoothUnavailable, OSError, RuntimeError, ValueError):
      pass
    finally:
      if client is not None:
        try:
          client.close()
        except (OSError, RuntimeError):
          pass
      pairing.finish(paired and pairing.parked())

  @property
  def window_journal_path(self) -> Path:
    return self.admission_lock.with_suffix('.recovery.json')

  def _write_journal(self, path: str, sender: str, pairable: bool, discoverable: bool) -> None:
    payload = {'path': path, 'sender': sender, 'pairable': pairable, 'discoverable': discoverable}
    fd, temp_name = tempfile.mkstemp(prefix='.' + self.window_journal_path.name + '.', dir=self.window_journal_path.parent)
    temp = Path(temp_name)
    try:
      with os.fdopen(fd, 'w') as out:
        json.dump(payload, out)
        out.flush()
        os.fsync(out.fileno())
      os.replace(temp, self.window_journal_path)
    finally:
      temp.unlink(missing_ok=True)

  def _recover_visibility(self) -> None:
    """Undo a window left open by a crash, only on the same BlueZ instance."""
    try:
      data = json.loads(self.window_journal_path.read_text())
    except FileNotFoundError:
      return
    except ValueError:
      self.window_journal_path.unlink(missing_ok=True)
      return
    client = self._client()
    path, _, sender = client.adapter_identity()
    if (path, sender) == (data.get('path'), data.get('sender')) and type(data.get('pairable')) is bool and \
       type(data.get('discoverable')) is bool:
      client.set_pairing_visibility(path, pairable=data['pairable'], discoverable=data['discoverable'])
    self.window_journal_path.unlink(missing_ok=True)

  def _accept_worker(self, pairing: OpenPairingSession, journal: Callable | None = None, restored: Callable | None = None) -> None:
    client = None
    paired = False
    try:
      client = self.bluez_factory()
      with pairing.condition:
        pairing.cancel_callback = lambda: client.cancel_pair(pairing.path) if pairing.path else None
        canceled = pairing.state != 'pairing'
      if not canceled:
        paired = client.accept_pair(pairing, journal or self._write_journal,
                                    restored or (lambda: self.window_journal_path.unlink(missing_ok=True)))
    except (BluetoothRejected, BluetoothUnavailable, OSError, RuntimeError, ValueError):
      pass
    finally:
      if client is not None:
        try:
          client.close()
        except (OSError, RuntimeError):
          pass
      pairing.finish(paired and pairing.parked())
    if pairing.state == 'paired' and self.on_paired is not None:
      try:
        self.on_paired(pairing.address, pairing.name, pairing.uuids)
      except Exception:
        pass

  def _watch_pair(self, pairing: PairingSession) -> None:
    while not pairing.done.wait(0.25):
      if not pairing.parked() or self.clock() >= pairing.deadline:
        pairing.cancel()
    with self.lock:
      self._admission_leave()

  def _end_idle_window(self) -> None:
    """A phone pairing window nobody has claimed yet must not block the buttons; close it and carry on."""
    pairing = self.pairing
    if isinstance(pairing, OpenPairingSession) and pairing.state == 'pairing' and not pairing.path:
      pairing.cancel()
      pairing.done.wait(5.0)

  def cancel_session(self, identity: tuple) -> None:
    with self.lock:
      if self.pairing is not None and self.pairing.identity == identity:
        self.pairing.cancel()

  @staticmethod
  def _forget_starport_car(address: str) -> None:
    """A deleted pairing also stops being Android Auto's car or companion, so it leaves the car pickers.

    Runs in the background: clearing may first stop a running projection (whose daemon may be the
    caller), and a failure here must not undo or delay the forget itself.
    """
    def worker():
      try:
        from openpilot.starpilot.system.starport.protocol import forget_car
        forget_car(address)
      except Exception as error:
        try:
          from openpilot.common.swaglog import cloudlog
          cloudlog.warning(f"Android Auto car clear failed for {address}: {error}")
        except Exception:
          pass

    threading.Thread(target=worker, name='starport_forget_car', daemon=True).start()

  def request(self, operation: str, *, address: str | None = None, enabled: bool | None = None,
              session: tuple | None = None, prompt_id: str | None = None, accepted: bool | None = None,
              value: str = '', power_on_for_starport: bool = False) -> dict:
    # Only the confirmed Android Auto enable flow may power the radio on in either road state.
    if type(power_on_for_starport) is not bool or power_on_for_starport and \
       (operation != 'power' or enabled is not True or session is None):
      raise ValueError('Android Auto Bluetooth startup requires an authenticated power-on request')
    if operation not in self.OPERATIONS:
      raise ValueError('Unsupported Bluetooth operation')
    if operation == 'power':
      if type(enabled) is not bool or address is not None or prompt_id is not None or accepted is not None or value:
        raise ValueError('Invalid power request')
    elif operation == 'pairing_response':
      if (session is None or address is not None or enabled is not None or type(accepted) is not bool or
          not isinstance(prompt_id, str) or not re.fullmatch('[0-9a-f]{32}', prompt_id) or
          not isinstance(value, str) or len(value) > 16):
        raise ValueError('Invalid pairing response')
    elif enabled is not None or prompt_id is not None or accepted is not None or value or \
         (address is None) != (operation in ('scan', 'stop_scan', 'cancel_pair', 'accept_pair')):
      raise ValueError('Invalid Bluetooth request')
    if operation in ('pair', 'accept_pair', 'cancel_pair') and session is None:
      raise ValueError('Pairing requires a session')
    if address is not None:
      address = normalized_address(address)
    def check_power_authority():
      if operation != 'power':
        return
      if session is not None and not self.session_valid(session):
        raise BluetoothRejected('Bluetooth session expired', code='session_expired')
      if not power_on_for_starport and not self.parked():
        raise BluetoothRejected('Bluetooth radio restart requires Park', code='park_required')
    if not self.lock.acquire(blocking=False):
      raise BluetoothRejected('Bluetooth is busy', code='busy')
    radio_change = None
    power_complete = False
    radio_start_owned = False
    radio_start_attempted = False
    try:
      check_power_authority()
      if not self.radio_helper.is_file():
        raise BluetoothUnavailable('Bluetooth radio is unavailable', code='radio_unavailable')
      if session is not None and not self.session_valid(session):
        raise BluetoothRejected('Pairing session expired', code='session_expired')
      self._admission_enter()
      check_power_authority()
      if operation == 'pairing_response':
        assert session is not None and prompt_id is not None and accepted is not None
        if self.pairing is None or not self.pairing.respond(session, prompt_id, accepted, value):
          raise BluetoothRejected('Pairing prompt changed')
        return self.snapshot(session=session)
      if operation == 'cancel_pair':
        if self.pairing is None or self.pairing.identity != session or self.pairing.state != 'pairing':
          raise BluetoothRejected('Pairing changed')
        self.pairing.cancel()
        return self.snapshot(session=session)
      if operation != 'stop_scan':
        # An unclaimed window gives way to everything, including a new window, so reopening Pair phone never
        # waits out the old one. A window a phone has already claimed still blocks (below).
        self._end_idle_window()
      if self.pairing is not None and self.pairing.state == 'pairing' and operation != 'stop_scan':
        raise BluetoothRejected('Pairing is in progress', code='busy')
      if operation == 'power':
        assert enabled is not None
        self.power_change_active = True
        if enabled:
          state = self.systemctl(['systemctl', 'show', '--property=ActiveState', '--value', RADIO_UNIT],
                                 check=True, capture_output=True, text=True, timeout=5).stdout.strip()
          if state not in ('inactive', 'failed', 'active', 'activating', 'deactivating', 'reloading', 'maintenance'):
            raise BluetoothUnavailable('Bluetooth service state is unavailable')
          radio_start_owned = state in ('inactive', 'failed')
        try:
          radio_change = self.radio_preference.begin(enabled)
          radio_change.apply()
        except OSError as exc:
          raise BluetoothUnavailable('Bluetooth power setting is unavailable', code='radio_preference_unavailable') from exc
        check_power_authority()
      if operation == 'power' and enabled:
        radio_start_attempted = True
        self.systemctl(['sudo', '-n', 'systemctl', 'start', RADIO_UNIT], check=True, timeout=30)
        check_power_authority()
      if operation == 'power':
        assert enabled is not None
        if not enabled:
          self._stop_scan()
        else:
          self._client().set_powered(True)
      elif operation == 'scan':
        if self.scan_deadline == 0.0:
          self._client().operation(operation, address)
        self._start_scan_lease()
      elif operation == 'stop_scan':
        self._stop_scan()
      elif operation == 'pair':
        assert session is not None and address is not None
        if self.pairing is not None and not self.pairing.done.is_set():
          raise BluetoothRejected('Pairing is in progress', code='busy')
        device = next((item for item in self._client()._devices(self._client()._objects()) if item['address'] == address), None)
        if device is None or device['paired']:
          raise BluetoothRejected('Device is unavailable for pairing')
        self._stop_scan()
        pairing = PairingSession(session, address, device['path'],
                                 lambda: self.session_valid(session), self.clock)
        self.pairing = pairing
        threading.Thread(target=self._pair_worker, args=(pairing,), daemon=True).start()
        threading.Thread(target=self._watch_pair, args=(pairing,), daemon=True).start()
      elif operation == 'accept_pair':
        assert session is not None
        if self.pairing is not None and not self.pairing.done.is_set():
          raise BluetoothRejected('Pairing is in progress', code='busy')
        self._stop_scan()
        self._recover_visibility()
        pairing = OpenPairingSession(session, lambda: self.session_valid(session), self.clock)
        self.pairing = pairing
        threading.Thread(target=self._accept_worker, args=(pairing,), daemon=True).start()
        threading.Thread(target=self._watch_pair, args=(pairing,), daemon=True).start()
      else:
        self._client().operation(operation, address)
        if operation == 'forget':
          assert address is not None
          self._forget_starport_car(address)
      if operation == 'power' and not enabled:
        self.systemctl(['sudo', '-n', 'systemctl', 'stop', RADIO_UNIT], check=True, timeout=15)
        self.close()
      check_power_authority()
      result = self.snapshot(session=session)
      if radio_change is not None:
        try:
          radio_change.verify()
        except OSError as exc:
          raise BluetoothUnavailable('Bluetooth power setting changed', code='radio_preference_unavailable') from exc
        if result['powered'] is not enabled:
          raise BluetoothUnavailable('Bluetooth power change could not be verified')
        power_complete = True
      return result
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
      self.close()
      raise BluetoothUnavailable('Bluetooth service is unavailable') from exc
    finally:
      try:
        if radio_change is not None and not power_complete:
          try:
            if radio_start_owned and radio_start_attempted and radio_change.current(radio_change.value, radio_change.owned_identity):
              try:
                self.systemctl(['sudo', '-n', 'systemctl', 'stop', RADIO_UNIT], check=True, timeout=15)
              except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                raise BluetoothUnavailable('Bluetooth startup could not be stopped') from exc
          finally:
            try:
              radio_change.rollback()
            except OSError as exc:
              raise BluetoothUnavailable('Bluetooth power setting could not be restored', code='radio_preference_unavailable') from exc
      finally:
        self.power_change_active = False
        if self.close_pending:
          self._close_locked()
        self._admission_leave()
        self.lock.release()
