"""Grant bounded Android Auto phone-role leases through the Bluetooth owner.

The cross-process admission lock prevents concurrent adapter roles. Importing
this module does not start a radio, BlueZ profile, or service.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from queue import Full
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from jeepney import MatchRule, new_error, new_method_return
from jeepney.low_level import HeaderFields

from openpilot.starpilot.bluetooth.owner import (
  AGENT, BlueZ, BluetoothOwner, BluetoothRejected, BluetoothUnavailable, OpenPairingSession, PairingSession, normalized_address,
)
from openpilot.starpilot.system.starport.incoming_pairing import IncomingPairing

AA_AGENT_PATH = '/org/starpilot/starport/pairing'


class BluetoothAdmissionError(RuntimeError):
  pass


class PhoneProfile(Protocol):
  def acquire(self, phone_class: bool = True) -> None: ...
  def release(self) -> None: ...


class PhoneRoleBlueZ(BlueZ):
  """The existing owner's D-Bus connection is the sole adapter-property writer."""

  def register_incoming_agent(self, agent: IncomingPairing, sender: str) -> Callable[[], None]:
    agent_filter = self.router.filter(MatchRule(type='method_call', interface=AGENT, path=AA_AGENT_PATH), bufsize=16)
    queue = agent_filter.__enter__()
    finished = threading.Event()
    slots = threading.BoundedSemaphore(4)
    handlers: set[threading.Thread] = set()
    handlers_lock = threading.Lock()

    def reply(message, accepted: bool, signature: str | None = None, body: tuple = ()) -> None:
      result = new_method_return(message, signature, body) if accepted else \
               new_error(message, 'org.bluez.Error.Rejected', 's', ('Pairing rejected',))
      self.router.send(result)

    def handle(message) -> None:
      try:
        if message.header.fields.get(HeaderFields.sender) != sender:
          reply(message, False)
          return
        member = message.header.fields.get(HeaderFields.member, '')
        accepted, signature, body = agent.handle(member, message.body)
        reply(message, accepted, signature, body)
      except (OSError, RuntimeError, ValueError, TypeError, IndexError):
        agent.close()
        try:
          reply(message, False)
        except (OSError, RuntimeError):
          pass

    def agent_loop() -> None:
      while not finished.is_set():
        message = queue.get()
        if message is None or finished.is_set():
          return
        if not slots.acquire(blocking=False):
          try:
            reply(message, False)
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
        worker = threading.Thread(target=bounded_handle, daemon=True)
        with handlers_lock:
          handlers.add(worker)
        worker.start()

    worker = threading.Thread(target=agent_loop, daemon=True)
    worker.start()
    registered = False
    try:
      self._call('/org/bluez', 'org.bluez.AgentManager1', 'RegisterAgent', 'os', (AA_AGENT_PATH, 'KeyboardDisplay'))
      registered = True
      self._call('/org/bluez', 'org.bluez.AgentManager1', 'RequestDefaultAgent', 'o', (AA_AGENT_PATH,))
    except BaseException:
      if registered:
        try:
          self._call('/org/bluez', 'org.bluez.AgentManager1', 'UnregisterAgent', 'o', (AA_AGENT_PATH,))
        except (BluetoothRejected, OSError, RuntimeError):
          pass
      finished.set()
      try:
        queue.put_nowait(None)
      except Full:
        pass
      agent_filter.__exit__(None, None, None)
      worker.join(timeout=1.0)
      raise

    def close() -> None:
      agent.close()
      try:
        self._call('/org/bluez', 'org.bluez.AgentManager1', 'UnregisterAgent', 'o', (AA_AGENT_PATH,))
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
    return close


@dataclass
class PhoneRoleLease:
  owner: SharedBluetoothOwner
  session: tuple | None
  phone: PhoneProfile
  adapter_path: str
  bluez_sender: str
  was_pairable: bool
  was_discoverable: bool
  deadline: float
  process_lock: Any
  receiver: str = ''
  device_path: str = ''
  enabled: Callable[[], bool] | None = None
  selected_receiver: Callable[[], str] | None = None
  incoming: IncomingPairing | None = None
  close_agent: Callable[[], None] | None = None
  outgoing: PairingSession | None = None
  discovering: bool = False
  operation_state: str = 'idle'
  error: str = ''
  peer_connected: bool = False
  released: bool = False

  def release(self) -> None:
    self.owner.release_phone_role(self)


class SharedBluetoothOwner(BluetoothOwner):
  """Current parked owner extended with session-bound AA phone presentation.

  The UI's scan/power operations and AA's temporary Pairable/Discoverable/HFP
  identity cannot race: each uses this one owner lock and client. A live scan is
  rejected before any AA change; scans/power changes are denied while leased.
  """

  def __init__(self, parked: Callable[[], bool], *, session_valid: Callable[[tuple], bool],
               bluez_factory=PhoneRoleBlueZ, lock_path: Path = Path('/data/starpilot/bluetooth-owner.lock'), **kwargs):
    super().__init__(parked, session_valid=session_valid, bluez_factory=bluez_factory, admission_lock=lock_path, **kwargs)
    self.phone_lease: PhoneRoleLease | None = None
    self.lock_path = lock_path
    self.companion_devices: dict[str, str] = {}
    self.journal_path = lock_path.with_suffix('.recovery.json')
    self.window_paired: dict | None = None
    if self.on_paired is None:
      self.on_paired = lambda address, name, uuids: setattr(self, 'window_paired', {'address': address, 'name': name, 'uuids': list(uuids)})

  def _process_lock(self):
    self.lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(self.lock_path, flags, 0o600)
    stream = os.fdopen(fd, 'r+')
    try:
      fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
      stream.close()
      raise BluetoothRejected('Bluetooth adapter is busy in another process') from None
    return stream

  def _journal(self, path: str, sender: str, pairable: bool, discoverable: bool) -> None:
    payload = {'path': path, 'sender': sender, 'pairable': pairable, 'discoverable': discoverable}
    fd, temp_name = tempfile.mkstemp(prefix='.' + self.journal_path.name + '.', dir=self.journal_path.parent)
    temp = Path(temp_name)
    try:
      with os.fdopen(fd, 'w') as out:
        json.dump(payload, out)
        out.flush()
        os.fsync(out.fileno())
      os.replace(temp, self.journal_path)
    finally:
      temp.unlink(missing_ok=True)

  def _read_journal(self) -> dict:
    data = json.loads(self.journal_path.read_text())
    if set(data) != {'path', 'sender', 'pairable', 'discoverable'} or \
       not isinstance(data['path'], str) or not isinstance(data['sender'], str) or \
       type(data['pairable']) is not bool or type(data['discoverable']) is not bool:
      raise BluetoothRejected('Invalid phone-role recovery journal')
    return data

  def recover_phone_role(self) -> bool:
    """Restore a crashed holder's visibility only on the same BlueZ instance."""
    with self.lock:
      with self._process_lock():
        if not self.journal_path.exists():
          return False
        data = self._read_journal()
        self._restore_visibility(self._client(), data['path'], data['sender'],
                                 data['pairable'], data['discoverable'])
        self.journal_path.unlink()
        return True

  def _admission_busy(self) -> bool:
    return self.phone_lease is not None or super()._admission_busy()

  @staticmethod
  def _start_scan(client) -> bool:
    """BlueZ can hold a stale discovery request; reset it once, and still open the window (the car can pair to us) if it stays stuck."""
    try:
      client.operation('scan')
      return True
    except BluetoothRejected as error:
      if 'InProgress' not in str(error):
        raise
    try:
      client.operation('stop_scan')
    except BluetoothRejected:
      pass
    try:
      client.operation('scan')
      return True
    except BluetoothRejected as error:
      if 'InProgress' not in str(error):
        raise
      return False

  def acquire_phone_role(self, session: tuple, phone: PhoneProfile, *, seconds: float = 180.0) -> PhoneRoleLease:
    if not session or not 0 < seconds <= 180:
      raise ValueError('Invalid phone-role session or duration')
    process_lock = self._process_lock()
    if not self.lock.acquire(blocking=False):
      process_lock.close()
      raise BluetoothRejected('Bluetooth is busy')
    try:
      if not self.parked() or not self.session_valid(session):
        raise BluetoothRejected('Phone-role pairing requires the current parked session')
      if self.phone_lease is not None or self.pairing is not None and self.pairing.state == 'pairing':
        raise BluetoothRejected('Bluetooth pairing is busy')
      if self.journal_path.exists():
        data = self._read_journal()
        self._restore_visibility(self._client(), data['path'], data['sender'],
                                 data['pairable'], data['discoverable'])
        self.journal_path.unlink()
      if self.scan_deadline:
        raise BluetoothRejected('Finish the current scan before Android Auto pairing')
      client = self._client()
      path, props, sender = client.adapter_identity()
      if props.get('Powered') is not True or props.get('Discovering') is True:
        raise BluetoothUnavailable('Bluetooth adapter is not ready for phone-role pairing')
      pairable = props.get('Pairable') is True
      discoverable = props.get('Discoverable') is True
      changed = False
      close_agent = None
      deadline = self.clock() + seconds
      incoming = IncomingPairing(session, devices=lambda: client._devices(client._objects()),
                                 live=lambda: self.parked() and self.session_valid(session),
                                 clock=self.clock, deadline=deadline)
      try:
        self._journal(path, sender, pairable, discoverable)
        changed = True
        close_agent = client.register_incoming_agent(incoming, sender)
        client.set_pairing_visibility(path, pairable=True, discoverable=True)
        if not self.parked() or not self.session_valid(session):
          raise BluetoothRejected('Phone-role pairing admission changed')
        # Class-of-device requires a separate crash-recovery journal before enabling.
        phone.acquire(phone_class=False)
        if not self.parked() or not self.session_valid(session):
          raise BluetoothRejected('Phone-role pairing admission changed')
        current_path, _, current_sender = client.adapter_identity()
        if (current_path, current_sender) != (path, sender):
          raise BluetoothRejected('Bluetooth adapter changed during pairing setup')
      except BaseException:
        try:
          try:
            if close_agent is not None:
              close_agent()
          finally:
            phone.release()
        finally:
          if changed:
            self._restore_visibility(client, path, sender, pairable, discoverable)
            self.journal_path.unlink(missing_ok=True)
        raise
      lease = PhoneRoleLease(self, session, phone, path, sender, pairable, discoverable, deadline,
                             process_lock, incoming=incoming, close_agent=close_agent)
      self.phone_lease = lease
      self.admission_file = process_lock
      try:
        lease.discovering = self._start_scan(client)
      except BaseException:
        self.release_phone_role(lease)
        raise
      return lease
    finally:
      self.lock.release()
      if self.phone_lease is None:
        self.admission_file = None
        process_lock.close()

  @staticmethod
  def _restore_visibility(client: BlueZ, path: str, sender: str, pairable: bool, discoverable: bool) -> None:
    # Never apply old adapter state after bluetoothd restart or adapter replacement.
    now_path, _, now_sender = client.adapter_identity()
    if (now_path, now_sender) == (path, sender):
      client.set_pairing_visibility(path, pairable=pairable, discoverable=discoverable)

  def release_phone_role(self, lease: PhoneRoleLease) -> None:
    with self.lock:
      if lease.released:
        return
      if self.phone_lease is not lease:
        raise BluetoothRejected('Phone-role lease changed')
      lease.released = True
      self.companion_devices.clear()
      self.phone_lease = None
      window = self.pairing
      if window is not None and not window.done.is_set():
        window.cancel()
        window.done.wait(5.0)
      if lease.session is not None:
        self.cancel_session(lease.session)
      try:
        try:
          try:
            self._stop_scan()
            if lease.discovering:
              try:
                self._client().operation('stop_scan')
              finally:
                lease.discovering = False
          finally:
            if lease.close_agent is not None:
              lease.close_agent()
        finally:
          lease.phone.release()
      finally:
        try:
          self._restore_visibility(self._client(), lease.adapter_path, lease.bluez_sender,
                                   lease.was_pairable, lease.was_discoverable)
          self.journal_path.unlink(missing_ok=True)
        finally:
          self.admission_file = None
          lease.process_lock.close()

  def maintain_phone_role(self) -> None:
    lease = self.phone_lease
    if lease is None:
      return
    if lease.session is not None:
      try:
        client = self._client()
        path, _, sender = client.adapter_identity()
        approved = lease.receiver or (lease.incoming.approved_address if lease.incoming is not None else '')
        device = next((item for item in client._devices(client._objects()) if item['address'] == approved), None) \
                 if approved else None
        if device is not None and device['connected']:
          lease.peer_connected = True
        disconnected = bool(approved and (device is None or lease.receiver and device['path'] != lease.device_path or
                                             lease.peer_connected and not device['connected']))
      except Exception:
        path, sender, disconnected = '', '', True
      if (self.clock() >= lease.deadline or not self.parked() or not self.session_valid(lease.session) or
          (path, sender) != (lease.adapter_path, lease.bluez_sender) or disconnected or
          not lease.receiver and lease.incoming is not None and not lease.incoming.status()['active']):
        lease.release()
      return
    try:
      path, _, sender = self._client().adapter_identity()
      device = next((item for item in self._client()._devices(self._client()._objects())
                     if item['address'] == lease.receiver), None)
      for address, companion_path in list(self.companion_devices.items()):
        companion = lease.phone.device(address)
        if companion is None or not companion['paired'] or companion['path'] != companion_path or \
           '0000111e-0000-1000-8000-00805f9b34fb' not in companion.get('uuids', []):
          self.companion_devices.pop(address, None)
      valid = (lease.enabled is not None and lease.enabled() and lease.selected_receiver is not None and
               lease.selected_receiver().upper() == lease.receiver and path == lease.adapter_path and
               sender == lease.bluez_sender and device is not None and device['paired'] and
               device['path'] == lease.device_path)
    except Exception:
      valid = False
    if not valid:
      lease.release()

  def pair_device(self, session: tuple, address: str) -> None:
    address = normalized_address(address)
    self.maintain_phone_role()
    with self.lock:
      lease = self.phone_lease
      if lease is None or lease.released or lease.session != session or not self.parked() or not self.session_valid(session):
        raise BluetoothRejected('Current Android Auto pairing session required')
      if lease.receiver or lease.incoming is not None and lease.incoming.approved_address:
        raise BluetoothRejected('Finish or cancel the current car operation first')
      client = self._client()
      path, _, sender = client.adapter_identity()
      if (path, sender) != (lease.adapter_path, lease.bluez_sender):
        raise BluetoothRejected('Bluetooth adapter changed')
      device = next((item for item in client._devices(client._objects()) if item['address'] == address), None)
      if device is None:
        raise BluetoothRejected('Selected device is no longer available')
      if lease.discovering:
        client.operation('stop_scan')
        lease.discovering = False
      # Close the incoming default agent before starting the address-bound outgoing agent.
      if lease.close_agent is not None:
        lease.close_agent()
        lease.close_agent = None
      lease.incoming = None
      lease.receiver, lease.device_path = address, device['path']
      if device['paired']:
        lease.operation_state = 'connecting'
      else:
        pairing = PairingSession(session, address, device['path'],
                                 lambda: self._outgoing_live(lease), self.clock)
        pairing.deadline = min(pairing.deadline, lease.deadline)
        lease.outgoing = self.pairing = pairing
        lease.operation_state = 'pairing'
      threading.Thread(target=self._outgoing_worker, args=(lease,), daemon=True).start()

  def _outgoing_live(self, lease: PhoneRoleLease) -> bool:
    if (lease.session is None or self.phone_lease is not lease or lease.released or not self.parked() or
        not self.session_valid(lease.session) or self.clock() >= lease.deadline):
      return False
    try:
      client = self._client()
      path, _, sender = client.adapter_identity()
      device = next((item for item in client._devices(client._objects()) if item['address'] == lease.receiver), None)
      return (path, sender) == (lease.adapter_path, lease.bluez_sender) and device is not None and device['path'] == lease.device_path
    except Exception:
      return False

  def _outgoing_worker(self, lease: PhoneRoleLease) -> None:
    try:
      if lease.outgoing is not None:
        self._pair_worker(lease.outgoing)
        if lease.outgoing.state != 'paired':
          raise BluetoothRejected('Bluetooth pairing did not complete')
      self.maintain_phone_role()
      if self.phone_lease is not lease or lease.released:
        return
      device = lease.phone.device(lease.receiver)
      if device is None or device['path'] != lease.device_path or not device['paired']:
        raise BluetoothRejected('Selected device bond changed')
      if not self._outgoing_live(lease):
        self.maintain_phone_role()
        return
      lease.operation_state = 'connecting'
      lease.phone.connect_device(lease.receiver)
      self.maintain_phone_role()
      if self.phone_lease is lease and not lease.released:
        device = lease.phone.device(lease.receiver)
        lease.operation_state = 'connected' if device and device['connected'] else 'paired'
    except Exception:
      if self.phone_lease is lease and not lease.released:
        lease.operation_state = 'failed'
        lease.error = 'The selected car did not complete Bluetooth pairing or connection.'

  def incoming_status(self, session: tuple) -> dict:
    self.maintain_phone_role()
    lease = self.phone_lease
    empty = {'active': False, 'receiver': None, 'prompt': None, 'approved': False,
             'devices': [], 'discovering': False, 'state': 'idle', 'error': ''}
    if lease is None or lease.session != session or lease.released:
      return empty
    result = lease.incoming.status() if lease.incoming is not None else dict(empty, active=True)
    devices = lease.phone.devices()
    result.update(devices=[{key: item[key] for key in ('address', 'name', 'paired', 'connected', 'starport')}
                           for item in devices[:64]], discovering=lease.discovering,
                  state=lease.operation_state, error=lease.error)
    if lease.receiver:
      device = next((item for item in devices if item['address'] == lease.receiver and item['path'] == lease.device_path), None)
      result['receiver'] = {'address': lease.receiver, 'name': device['name'] if device else lease.receiver}
      result['approved'] = bool(device and device['paired'] and lease.operation_state in ('paired', 'connected'))
      if lease.outgoing is not None:
        with lease.outgoing.condition:
          result['prompt'] = dict(lease.outgoing.prompt) if lease.outgoing.prompt is not None else None
    return result

  def incoming_response(self, session: tuple, prompt_id: str, accepted: bool, value: str = '') -> bool:
    self.maintain_phone_role()
    lease = self.phone_lease
    if lease is None or lease.session != session or lease.released:
      return False
    if lease.outgoing is not None:
      return lease.outgoing.respond(session, prompt_id, accepted, value)
    return bool(lease.incoming is not None and lease.incoming.respond(session, prompt_id, accepted, value))

  def acquire_projection_role(self, receiver: str, phone: PhoneProfile, *, enabled: Callable[[], bool],
                              selected_receiver: Callable[[], str]) -> PhoneRoleLease:
    """Hold HFP only for the enabled, selected and bonded receiver; never open pairing visibility."""
    from openpilot.starpilot.bluetooth.owner import normalized_address
    receiver = normalized_address(receiver)
    process_lock = self._process_lock()
    if not self.lock.acquire(blocking=False):
      process_lock.close()
      raise BluetoothRejected('Bluetooth is busy')
    try:
      if not enabled() or selected_receiver().upper() != receiver:
        raise BluetoothRejected('Selected Android Auto receiver changed')
      if self.phone_lease is not None or self.scan_deadline or self.pairing is not None and self.pairing.state == 'pairing':
        raise BluetoothRejected('Bluetooth pairing is busy')
      client = self._client()
      if self.journal_path.exists():
        data = self._read_journal()
        self._restore_visibility(client, data['path'], data['sender'], data['pairable'], data['discoverable'])
        self.journal_path.unlink()
      path, props, sender = client.adapter_identity()
      if props.get('Powered') is not True or props.get('Discovering') is True:
        raise BluetoothUnavailable('Bluetooth adapter is not ready for projection')
      device = next((item for item in client._devices(client._objects()) if item['address'] == receiver), None)
      if device is None or not device['paired']:
        raise BluetoothRejected('Selected receiver is not paired')
      pairable = props.get('Pairable') is True
      discoverable = props.get('Discoverable') is True
      journaled = False
      try:
        self._journal(path, sender, pairable, discoverable)
        journaled = True
        client.set_pairing_visibility(path, pairable=False, discoverable=False)
        phone.acquire(phone_class=False)
        if not enabled() or selected_receiver().upper() != receiver:
          raise BluetoothRejected('Selected Android Auto receiver changed')
        current_path, _, current_sender = client.adapter_identity()
        if (current_path, current_sender) != (path, sender):
          raise BluetoothRejected('Bluetooth adapter changed')
      except BaseException:
        try:
          phone.release()
        finally:
          if journaled:
            self._restore_visibility(client, path, sender, pairable, discoverable)
            self.journal_path.unlink(missing_ok=True)
        raise
      lease = PhoneRoleLease(self, None, phone, path, sender, pairable, discoverable, float('inf'), process_lock,
                             receiver=receiver, device_path=device['path'], enabled=enabled,
                             selected_receiver=selected_receiver)
      self.phone_lease = lease
      self.admission_file = process_lock
      return lease
    finally:
      self.lock.release()
      if self.phone_lease is None:
        self.admission_file = None
        process_lock.close()

  def companion_accepts(self, address: str) -> bool:
    """Only an explicitly admitted bonded car may share this projection's HFP."""
    lease = self.phone_lease
    address = address.upper()
    if lease is None or lease.released or lease.session is not None or address == lease.receiver:
      return False
    path = self.companion_devices.get(address)
    if path is None or lease.enabled is None or not lease.enabled() or lease.selected_receiver is None or \
       lease.selected_receiver().upper() != lease.receiver:
      return False
    # Do not query BlueZ in a NewConnection callback: ConnectProfile may be
    # waiting for this callback while holding the phone's D-Bus request lock.
    # Admission was checked before ConnectProfile and is revoked by maintenance.
    return True

  def prepare_companion(self, address: str, selected_companion: Callable[[], str], *, connect: bool = False) -> dict:
    """Admit only the saved car for this lease; optionally establish its HFP first."""
    address = normalized_address(address)
    with self.lock:
      lease = self.phone_lease
      if lease is None or lease.released or lease.session is not None:
        raise BluetoothRejected('Android Auto projection Bluetooth owner required')
      client = self._client()
      path, props, sender = client.adapter_identity()
      receiver = lease.phone.device(lease.receiver)
      device = lease.phone.device(address)
      hfp = '0000111e-0000-1000-8000-00805f9b34fb'
      def valid():
        return (self.phone_lease is lease and not lease.released and lease.enabled is not None and lease.enabled() and
                lease.selected_receiver is not None and lease.selected_receiver().upper() == lease.receiver and
                selected_companion().upper() == address and address != lease.receiver)
      if not valid() or (path, sender) != (lease.adapter_path, lease.bluez_sender) or props.get('Powered') is not True or \
         receiver is None or not receiver['paired'] or receiver['path'] != lease.device_path or \
         device is None or not device['paired'] or hfp not in device.get('uuids', []) or \
         not device['path'].startswith(lease.adapter_path + '/'):
        raise BluetoothRejected('Selected paired hands-free car is unavailable')
      was_admitted = self.companion_devices.get(address)
      self.companion_devices[address] = device['path']
      try:
        if connect and not device['connected']:
          lease.phone._call(device['path'], 'org.bluez.Device1', 'ConnectProfile', 's', (hfp,), timeout=12.0)
        current_path, current_props, current_sender = client.adapter_identity()
        current = lease.phone.device(address)
        current_receiver = lease.phone.device(lease.receiver)
        if not valid() or (current_path, current_sender) != (lease.adapter_path, lease.bluez_sender) or \
           current_props.get('Powered') is not True or current is None or not current['paired'] or \
           current['path'] != device['path'] or hfp not in current.get('uuids', []) or \
           current_receiver is None or not current_receiver['paired'] or current_receiver['path'] != lease.device_path:
          raise BluetoothRejected('Bluetooth owner or selected car changed')
        if connect and not current['connected']:
          raise BluetoothRejected('Connect the selected car before starting its Android Auto adapter')
        return current
      except BaseException:
        if was_admitted is None:
          self.companion_devices.pop(address, None)
        else:
          self.companion_devices[address] = was_admitted
        raise

  def companion_action(self, operation: str, address: str, session: tuple) -> dict | None:
    """Delegate saved-car actions within the existing projection owner's lock.

    None means this process holds no AA lease. A pairing lease or any invalid
    target is rejected without releasing the controller or falling back.
    """
    if operation not in ('connect', 'disconnect', 'forget'):
      raise ValueError('Unsupported companion Bluetooth operation')
    address = normalized_address(address)
    if not self.lock.acquire(blocking=False):
      raise BluetoothRejected('Bluetooth is busy', code='busy')
    try:
      lease = self.phone_lease
      if lease is None:
        return None
      if not self.session_valid(session):
        raise BluetoothRejected('Galaxy session expired', code='session_expired')
      if lease.released or lease.session is not None:
        raise BluetoothRejected('Finish Android Auto pairing first', code='busy')
      client = self._client()
      path, props, sender = client.adapter_identity()
      if path != lease.adapter_path or sender != lease.bluez_sender or props.get('Powered') is not True or \
         lease.enabled is None or not lease.enabled() or lease.selected_receiver is None or \
         lease.selected_receiver().upper() != lease.receiver:
        raise BluetoothRejected('Android Auto Bluetooth owner changed', code='changed')
      receiver = lease.phone.device(lease.receiver)
      if receiver is None or not receiver['paired'] or receiver['path'] != lease.device_path:
        raise BluetoothRejected('Projection receiver changed', code='changed')
      if address == lease.receiver:
        raise BluetoothRejected('Stop projection before changing its adapter', code='projection_active')
      device = lease.phone.device(address)
      if operation == 'forget':
        if device is None or not device['path'].startswith(lease.adapter_path + '/'):
          raise BluetoothRejected('Device is unavailable', code='changed')
        client.operation('forget', address)
        if self.phone_lease is not lease or lease.released or not self.session_valid(session):
          raise BluetoothRejected('Bluetooth owner changed', code='changed')
        self.companion_devices.pop(address, None)
        return self.snapshot(session=session)
      hfp = '0000111e-0000-1000-8000-00805f9b34fb'
      if device is None or not device['paired']:
        raise BluetoothRejected('Choose an already paired device', code='changed')
      if not device['path'].startswith(lease.adapter_path + '/'):
        raise BluetoothRejected('Bluetooth adapter changed', code='changed')
      is_car = hfp in device.get('uuids', [])
      was_admitted = self.companion_devices.get(address)
      if operation == 'connect' and is_car:
        self.companion_devices[address] = device['path']
      try:
        if not is_car:
          client.operation(operation, address)
        elif operation == 'connect':
          lease.phone._call(device['path'], 'org.bluez.Device1', 'ConnectProfile', 's', (hfp,), timeout=12.0)
        else:
          # Disconnect only the companion car, never the projection adapter.
          lease.phone._call(device['path'], 'org.bluez.Device1', 'Disconnect', timeout=12.0)
        current_path, _, current_sender = client.adapter_identity()
        current = lease.phone.device(address)
        if self.phone_lease is not lease or lease.released or not self.session_valid(session) or \
           (current_path, current_sender) != (lease.adapter_path, lease.bluez_sender) or \
           lease.enabled is None or not lease.enabled() or lease.selected_receiver is None or \
           lease.selected_receiver().upper() != lease.receiver or current is None or not current['paired'] or \
           current['path'] != device['path']:
          raise BluetoothRejected('Bluetooth owner or car changed', code='changed')
      except BaseException:
        if was_admitted is None:
          self.companion_devices.pop(address, None)
        else:
          self.companion_devices[address] = was_admitted
        raise
      if operation == 'disconnect':
        self.companion_devices.pop(address, None)
      return self.snapshot(session=session)
    finally:
      self.lock.release()

  def companion_power(self, enabled: bool, session: tuple) -> dict | None:
    """Turn the radio off (or confirm it is on) from Galaxy while Android Auto holds the adapter.

    None means no lease holds it, so the caller's own owner handles the request. Turning it off releases the
    projection lease first (see request), so Android Auto lets go of the adapter before the radio stops.
    """
    lease = self.phone_lease
    if lease is None or lease.released:
      return None
    if not self.session_valid(session):
      raise BluetoothRejected('Galaxy session expired', code='session_expired')
    if lease.session is not None:
      raise BluetoothRejected('Finish Android Auto pairing first', code='busy')
    if enabled:
      return self.snapshot(session=session)
    return self.request('power', enabled=False, session=session)

  def phone_window(self, operation: str, session: tuple, *, prompt_id: str | None = None,
                   accepted: bool | None = None, value: str = '', address: str | None = None) -> dict | None:
    """Open, answer or read a phone pairing window inside the projection lease; None means no projection holds the adapter."""
    if operation not in ('accept_pair', 'pair', 'scan', 'stop_scan', 'pairing_response', 'cancel_pair', 'status'):
      raise ValueError('Unsupported phone window operation')
    if not self.lock.acquire(blocking=False):
      raise BluetoothRejected('Bluetooth is busy', code='busy')
    try:
      lease = self.phone_lease
      window = self.pairing
      if lease is None or lease.released:
        return None
      if lease.session is not None:
        raise BluetoothRejected('Finish Android Auto pairing first', code='busy')
      if not self.session_valid(session):
        raise BluetoothRejected('Galaxy session expired', code='session_expired')
      if operation in ('status', 'cancel_pair', 'pairing_response') and (window is None or window.identity != session):
        return None
      if operation != 'status' and not self.parked():
        raise BluetoothRejected('Park before changing Bluetooth devices', code='park_required')
      if operation in ('scan', 'stop_scan'):
        return super().request(operation, session=session)
      if operation == 'pair':
        if address is None:
          raise ValueError('Invalid Bluetooth address')
        address = normalized_address(address)
        if address == lease.receiver:
          raise BluetoothRejected('Use Android Auto to pair its receiver', code='projection_active')
        self._end_idle_window()
        if self.pairing is not None and not self.pairing.done.is_set():
          raise BluetoothRejected('Pairing is in progress', code='busy')
        client = self._client()
        path, _, sender = client.adapter_identity()
        device = next((d for d in client._devices(client._objects()) if d['address'] == address), None)
        if (path, sender) != (lease.adapter_path, lease.bluez_sender) or device is None or device['paired'] or not device['path'].startswith(path + '/'):
          raise BluetoothRejected('Device changed before pairing', code='changed')
        self._stop_scan()
        window = PairingSession(session, address, device['path'],
                                lambda: self.phone_lease is lease and not lease.released and self.parked() and self.session_valid(session), self.clock)
        self.pairing = window
        self.window_paired = None
        threading.Thread(target=self._pair_worker, args=(window,), daemon=True).start()
        threading.Thread(target=self._watch_pair, args=(window,), daemon=True).start()
      elif operation == 'accept_pair':
        if not self.parked():
          raise BluetoothRejected('Park before pairing a phone', code='park_required')
        # A window nobody has claimed yet gives way to a new one; a claimed one still blocks.
        self._end_idle_window()
        if self.pairing is not None and not self.pairing.done.is_set():
          raise BluetoothRejected('Pairing is in progress', code='busy')
        window = OpenPairingSession(session, lambda: self.session_valid(session) and self.parked(), self.clock)
        self.pairing = window
        self.window_paired = None
        threading.Thread(target=self._accept_worker, args=(window, lambda *_: None, lambda: None), daemon=True).start()
        threading.Thread(target=self._watch_pair, args=(window,), daemon=True).start()
      elif operation == 'cancel_pair':
        window.cancel()
      elif operation == 'pairing_response' and (prompt_id is None or type(accepted) is not bool or not window.respond(session, prompt_id, accepted, value)):
        raise BluetoothRejected('Pairing prompt changed')
      result = self.snapshot(session=session)
      result['windowPaired'] = self.window_paired
      return result
    finally:
      self.lock.release()

  def request(self, operation: str, **kwargs) -> dict:
    lease = self.phone_lease
    if lease is not None and lease.session is None and operation in ('forget', 'power'):
      address = kwargs.get('address')
      if operation == 'power' and kwargs.get('enabled') is False or \
         operation == 'forget' and isinstance(address, str) and address.upper() == lease.receiver:
        lease.release()
        lease = None
    if lease is not None and operation in ('power', 'scan', 'stop_scan', 'forget', 'connect', 'disconnect'):
      raise BluetoothRejected('Android Auto pairing has the Bluetooth adapter')
    if lease is not None and operation in ('pair', 'accept_pair', 'cancel_pair', 'pairing_response'):
      raise BluetoothRejected('Use the Android Auto pairing operation for this lease')
    return super().request(operation, **kwargs)

  def close(self) -> None:
    lease = self.phone_lease
    if lease is not None:
      lease.release()
    super().close()

class LeaseAwarePhone:
  """Restrict donor HFP/phone profile to an owner-issued AA lease."""

  def __init__(self, owner: SharedBluetoothOwner, session: tuple | None, log: Callable[..., None], phone_factory=None,
               *, enabled: Callable[[], bool] = lambda: False, selected_receiver: Callable[[], str] = lambda: ''):
    if phone_factory is None:
      from openpilot.starpilot.system.starport.bluez_phone import BluezPhone
      phone_factory = BluezPhone
    self.owner, self.session = owner, session
    self.enabled, self.selected_receiver = enabled, selected_receiver
    self.phone = phone_factory(log)
    self.lease: PhoneRoleLease | None = None

  @property
  def accepts(self): return self.phone.accepts
  @accepts.setter
  def accepts(self, value): self.phone.accepts = value
  @property
  def on_connection(self): return self.phone.on_connection
  @on_connection.setter
  def on_connection(self, value): self.phone.on_connection = value

  def devices(self): return self.phone.devices()
  def device(self, address): return self.phone.device(address)
  def snapshot(self, address): return self.phone.snapshot(address)
  def adapter(self): return self.phone.adapter()  # this comma's address, for the head unit's pairing request

  def acquire(self, phone_class: bool = True) -> None:
    if self.lease is None:
      receiver = self.selected_receiver()
      self.lease = self.owner.acquire_projection_role(receiver, self.phone, enabled=self.enabled,
                                                       selected_receiver=self.selected_receiver)

  def acquire_pairing(self) -> None:
    if self.lease is None:
      if self.session is None:
        raise BluetoothAdmissionError('Current pairing authorization required')
      self.lease = self.owner.acquire_phone_role(self.session, self.phone)

  def _require_lease(self):
    self.owner.maintain_phone_role()
    if self.lease is None or self.lease.released:
      raise BluetoothAdmissionError('Phone-role lease is not active')

  def register_hfp(self):
    self._require_lease()
    self.phone.register_hfp()

  def restore_class(self):
    self._require_lease()
    self.phone.restore_class()

  def set_trusted(self, address):
    self._require_lease()
    self.phone.set_trusted(address)

  def connect_device(self, address):
    self._require_lease()
    self.phone.connect_device(address)

  def disconnect_stray_link(self):
    self._require_lease()
    return self.phone.disconnect_stray_link()

  def release(self):
    lease, self.lease = self.lease, None
    if lease is not None:
      lease.release()

  def close(self):
    self.release()
    self.phone.close()
