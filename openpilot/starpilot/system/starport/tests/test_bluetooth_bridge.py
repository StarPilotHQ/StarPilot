from unittest.mock import Mock
import pytest
from openpilot.starpilot.bluetooth.owner import BluetoothOwner, BluetoothRejected
from openpilot.starpilot.bluetooth.radio_preference import RadioPreference
from openpilot.starpilot.system.starport.bluetooth_bridge import SharedBluetoothOwner


class FakeBlueZ:
  def __init__(self):
    self.path = '/org/bluez/hci0'
    self.sender = ':1.20'
    self.props = {'Powered': True, 'Discovering': False, 'Pairable': False, 'Discoverable': False}
    self.paired = [{'path': '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF', 'address': 'AA:BB:CC:DD:EE:FF', 'paired': True, 'connected': False, 'name': 'Car'}]
    self.writes = []
    self.closed = False
    self.agent = None
    self.agent_closed = 0

  def adapter_identity(self):
    return self.path, dict(self.props), self.sender

  def set_pairing_visibility(self, path, *, pairable, discoverable):
    assert path == self.path
    self.writes.append((pairable, discoverable))
    self.props.update(Pairable=pairable, Discoverable=discoverable)

  def _objects(self):
    return None

  def _devices(self, _objects):
    return list(self.paired)

  def register_incoming_agent(self, agent, sender):
    assert sender == self.sender
    self.agent = agent

    def close():
      agent.close()
      self.agent_closed += 1

    return close

  def snapshot(self):
    return {'adapter': True, 'powered': True, 'discovering': self.props['Discovering'], 'devices': list(self.paired)}

  def operation(self, operation, address=None):
    if operation == 'scan':
      self.props['Discovering'] = True
    if operation == 'stop_scan':
      self.props['Discovering'] = False

  def set_powered(self, enabled):
    self.props['Powered'] = enabled

  def close(self):
    self.closed = True


class Phone:
  def __init__(self):
    self.acquired = 0
    self.released = 0

  def acquire(self, phone_class=True):
    self.acquired += 1

  def release(self):
    self.released += 1


def owner(tmp_path, state, bluez):
  radio = tmp_path / 'bluetooth-radio'
  radio.touch()
  return SharedBluetoothOwner(
    lambda: state['parked'],
    session_valid=lambda session: session == state['session'],
    bluez_factory=lambda: bluez,
    radio_helper=radio,
    lock_path=tmp_path / "aa.lock",
  )


def test_phone_role_temporarily_changes_adapter_and_restores(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  phone = Phone()
  shared = owner(tmp_path, state, bluez)
  lease = shared.acquire_phone_role(state['session'], phone, seconds=10)
  assert bluez.writes == [(True, True)] and phone.acquired == 1
  assert bluez.agent is not None and bluez.agent.status()['active']
  with pytest.raises(BluetoothRejected, match='Android Auto pairing'):
    shared.request('scan')
  with pytest.raises(BluetoothRejected, match='Android Auto pairing operation'):
    shared.request('pair', address='AA:BB:CC:DD:EE:FF', session=('old', 'session'))
  with pytest.raises(BluetoothRejected, match='Android Auto pairing operation'):
    shared.request('accept_pair', session=state['session'])
  lease.release()
  lease.release()
  assert bluez.writes == [(True, True), (False, False)] and phone.released == 1 and bluez.agent_closed == 1
  shared.close()


def test_live_scan_coexists_by_refusing_aa_takeover(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  phone = Phone()
  shared = owner(tmp_path, state, bluez)
  shared.scan_deadline = shared.clock() + 20
  with pytest.raises(BluetoothRejected, match='Finish the current scan'):
    shared.acquire_phone_role(state['session'], phone)
  assert bluez.writes == [] and phone.acquired == 0
  shared.scan_deadline = 0
  shared.close()


def test_stale_source_rejected_and_active_lease_revoked(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  phone = Phone()
  shared = owner(tmp_path, state, bluez)
  with pytest.raises(BluetoothRejected, match='current parked session'):
    shared.acquire_phone_role(('stale', '42'), phone)
  assert bluez.writes == []
  lease = shared.acquire_phone_role(state['session'], phone)
  state['session'] = ('galaxy', '43')
  shared.maintain_phone_role()
  assert lease.released and phone.released == 1 and bluez.writes[-1] == (False, False)
  shared.close()


def test_restart_does_not_restore_old_adapter_state(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  phone = Phone()
  shared = owner(tmp_path, state, bluez)
  lease = shared.acquire_phone_role(state['session'], phone)
  bluez.sender = ':1.21'
  bluez.writes.clear()
  shared.maintain_phone_role()
  assert lease.released and bluez.writes == [] and phone.released == 1 and bluez.agent_closed == 1
  shared.close()


def test_approved_head_unit_disconnect_ends_temporary_pairing_role(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  phone = Phone()
  shared = owner(tmp_path, state, bluez)
  lease = shared.acquire_phone_role(state['session'], phone)
  lease.incoming.approved_address = 'AA:BB:CC:DD:EE:FF'
  bluez.paired[0]['connected'] = True
  shared.maintain_phone_role()
  assert not lease.released
  bluez.paired[0]['connected'] = False
  shared.maintain_phone_role()
  assert lease.released and phone.released == 1 and bluez.agent_closed == 1
  shared.close()


def test_admission_change_during_acquire_restores_even_if_profile_not_ready(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()

  class ChangingPhone(Phone):
    def acquire(self, phone_class=True):
      super().acquire(phone_class)
      state['parked'] = False

  phone = ChangingPhone()
  shared = owner(tmp_path, state, bluez)
  with pytest.raises(BluetoothRejected, match='admission changed'):
    shared.acquire_phone_role(state['session'], phone)
  assert phone.released == 1 and bluez.writes == [(True, True), (False, False)]
  shared.close()


def test_other_process_owner_cannot_scan_during_phone_lease(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  first = owner(tmp_path, state, bluez)
  second = owner(tmp_path, state, bluez)
  lease = first.acquire_phone_role(state['session'], Phone())
  with pytest.raises(BluetoothRejected, match='another process'):
    second.request('scan')
  lease.release()
  first.close()
  second.close()


def test_galaxy_compact_and_aa_share_process_admission(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  radio = tmp_path / 'bluetooth-radio'
  radio.touch()
  lock = tmp_path / 'aa.lock'
  preference = RadioPreference(tmp_path / 'BluetoothEnabled')
  preference.path.write_bytes(b'1\n')
  galaxy = BluetoothOwner(lambda: True, bluez_factory=lambda: bluez, radio_helper=radio, admission_lock=lock, radio_preference=preference)
  compact = BluetoothOwner(lambda: True, bluez_factory=lambda: bluez, radio_helper=radio, admission_lock=lock, radio_preference=preference)
  aa = owner(tmp_path, state, bluez)
  galaxy.request('scan')
  with pytest.raises(BluetoothRejected, match='another process'):
    compact.request('power', enabled=False)
  with pytest.raises(BluetoothRejected, match='another process'):
    aa.acquire_phone_role(state['session'], Phone())
  galaxy.request('stop_scan')
  lease = aa.acquire_phone_role(state['session'], Phone())
  with pytest.raises(BluetoothRejected, match='another process'):
    galaxy.request('scan')
  for enabled in (False, True):
    with pytest.raises(BluetoothRejected, match='another process'):
      compact.request('power', enabled=enabled)
    assert preference.path.read_bytes() == b'1\n'
    assert bluez.props['Powered'] is True
  lease.release()
  assert compact.request('scan')['discovering'] is True
  compact.request('stop_scan')
  aa.close()
  compact.close()
  galaxy.close()


def test_recover_crashed_pairing_visibility_from_journal(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  first = owner(tmp_path, state, bluez)
  lease = first.acquire_phone_role(state['session'], Phone())
  # Simulate process death: OS closes the flock; BlueZ drops its HFP D-Bus profile.
  lease.process_lock.close()
  second = owner(tmp_path, state, bluez)
  assert second.recover_phone_role() is True
  assert bluez.writes[-1] == (False, False) and not second.journal_path.exists()
  assert second.recover_phone_role() is False
  second.close()


def test_donor_phone_uses_same_lease_and_does_not_take_over_independently(tmp_path):
  from openpilot.starpilot.system.starport.bluetooth_bridge import LeaseAwarePhone, BluetoothAdmissionError

  state = {'parked': True, 'session': ('aa', 'run')}
  bluez = FakeBlueZ()
  shared = owner(tmp_path, state, bluez)

  class FakePhone(Phone):
    def __init__(self):
      super().__init__()
      self.hfp = 0
      self.closed = 0
      self.accepts = None
      self.on_connection = None

    def register_hfp(self):
      self.hfp += 1

    def restore_class(self):
      pass

    def close(self):
      self.closed += 1

    def devices(self):
      return []

    def adapter(self):
      return '/org/bluez/hci0', {'Address': '39:90:21:64:07:00'}

  phone = FakePhone()
  wrapper = LeaseAwarePhone(shared, state['session'], lambda *a, **k: None, lambda log: phone)
  assert wrapper.adapter()[1]['Address'] == '39:90:21:64:07:00'  # read before any lease, for the pairing request
  with pytest.raises(BluetoothAdmissionError, match='not active'):
    wrapper.register_hfp()
  wrapper.acquire_pairing()
  wrapper.acquire_pairing()
  wrapper.register_hfp()
  assert phone.acquired == 1 and phone.hfp == 1 and bluez.writes == [(True, True)]
  wrapper.release()
  wrapper.close()
  assert phone.released == 1 and phone.closed == 1 and bluez.writes[-1] == (False, False)
  shared.close()


def test_projection_survives_drive_and_browser_expiry_but_not_disable_or_receiver_switch(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42'), 'enabled': True, 'selected': 'AA:BB:CC:DD:EE:FF'}
  bluez = FakeBlueZ()
  phone = Phone()
  shared = owner(tmp_path, state, bluez)
  lease = shared.acquire_projection_role(state['selected'], phone, enabled=lambda: state['enabled'], selected_receiver=lambda: state['selected'])
  assert bluez.writes == [(False, False)] and phone.acquired == 1
  state['parked'] = False
  state['session'] = ('expired', '0')
  shared.maintain_phone_role()
  assert not lease.released
  state['selected'] = '11:22:33:44:55:66'
  shared.maintain_phone_role()
  assert lease.released and phone.released == 1
  state['selected'] = 'AA:BB:CC:DD:EE:FF'
  state['enabled'] = True
  lease = shared.acquire_projection_role(state['selected'], phone, enabled=lambda: state['enabled'], selected_receiver=lambda: state['selected'])
  state['enabled'] = False
  shared.maintain_phone_role()
  assert lease.released and phone.released == 2
  assert bluez.writes == [(False, False)] * 4
  shared.close()


def test_accessory_scan_and_pair_keep_projection_lock_and_cancel_on_release(tmp_path):
  import threading
  state = {'parked': True, 'session': ('galaxy', '42'), 'enabled': True, 'selected': 'AA:BB:CC:DD:EE:FF'}
  bluez = FakeBlueZ()
  target = '11:22:33:44:55:66'
  bluez.paired.append({'address': target, 'path': '/org/bluez/hci0/dev_11_22_33_44_55_66',
                       'paired': False, 'connected': False, 'name': 'Car'})
  shared = owner(tmp_path, state, bluez)
  lease = shared.acquire_projection_role(state['selected'], Phone(), enabled=lambda: state['enabled'],
                                         selected_receiver=lambda: state['selected'])
  other = owner(tmp_path, state, bluez)
  started = threading.Event()
  def pair(address, path, session):
    started.set()
    session.done.wait(2)
    return False
  bluez.pair = pair
  bluez.cancel_pair = lambda path: None
  try:
    assert shared.phone_window('scan', state['session'])['discovering']
    with pytest.raises(BluetoothRejected, match='another process'):
      other.request('scan')
    result = shared.phone_window('pair', state['session'], address=target)
    assert result['pairing']['address'] == target
    assert started.wait(1)
    assert not bluez.props['Discovering']
    assert shared.phone_lease is lease and not lease.released
    assert shared.phone_window('status', state['session'])['pairing']['state'] == 'pairing'
    lease.release()
    assert shared.pairing.done.wait(1)
    assert shared.pairing.state == 'canceled'
    assert other.request('scan')['discovering']
  finally:
    shared.close()
    other.close()


def test_projection_accessory_operations_require_park_and_reject_receiver_pairing(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  shared = owner(tmp_path, state, bluez)
  receiver = bluez.paired[0]['address']
  shared.acquire_projection_role(receiver, Phone(), enabled=lambda: True, selected_receiver=lambda: receiver)
  try:
    with pytest.raises(BluetoothRejected, match='Use Android Auto'):
      shared.phone_window('pair', state['session'], address=receiver)
    state['parked'] = False
    for operation in ('scan', 'pair'):
      with pytest.raises(BluetoothRejected) as error:
        shared.phone_window(operation, state['session'], address='11:22:33:44:55:66')
      assert error.value.code == 'park_required'
    assert not bluez.props['Discovering']
  finally:
    shared.close()


def test_projection_restores_original_visibility_on_release_and_failure(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  bluez.props.update(Pairable=True, Discoverable=False)
  shared = owner(tmp_path, state, bluez)
  lease = shared.acquire_projection_role('AA:BB:CC:DD:EE:FF', Phone(), enabled=lambda: True, selected_receiver=lambda: 'AA:BB:CC:DD:EE:FF')
  assert bluez.writes == [(False, False)] and shared.journal_path.exists()
  lease.release()
  assert bluez.writes[-1] == (True, False) and not shared.journal_path.exists()

  class FailingPhone(Phone):
    def acquire(self, phone_class=True):
      super().acquire(phone_class)
      raise RuntimeError('profile failed')

  phone = FailingPhone()
  with pytest.raises(RuntimeError, match='profile failed'):
    shared.acquire_projection_role('AA:BB:CC:DD:EE:FF', phone, enabled=lambda: True, selected_receiver=lambda: 'AA:BB:CC:DD:EE:FF')
  assert phone.released == 1 and bluez.writes[-1] == (True, False)
  assert not shared.journal_path.exists()
  shared.close()


def test_projection_crash_recovery_requires_same_bluez_generation(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  bluez.props.update(Pairable=True, Discoverable=True)
  first = owner(tmp_path, state, bluez)
  lease = first.acquire_projection_role('AA:BB:CC:DD:EE:FF', Phone(), enabled=lambda: True, selected_receiver=lambda: 'AA:BB:CC:DD:EE:FF')
  lease.process_lock.close()
  second = owner(tmp_path, state, bluez)
  assert second.recover_phone_role() is True
  assert bluez.writes[-1] == (True, True) and not second.journal_path.exists()
  second.close()

  bluez.writes.clear()
  third = owner(tmp_path, state, bluez)
  second_lease = third.acquire_projection_role('AA:BB:CC:DD:EE:FF', Phone(), enabled=lambda: True, selected_receiver=lambda: 'AA:BB:CC:DD:EE:FF')
  second_lease.process_lock.close()
  bluez.sender = ':1.21'
  fourth = owner(tmp_path, state, bluez)
  assert fourth.recover_phone_role() is True
  assert bluez.writes == [(False, False)] and not fourth.journal_path.exists()
  fourth.close()


def test_projection_partial_visibility_failure_uses_recovery_journal(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}

  class PartialBlueZ(FakeBlueZ):
    def __init__(self):
      super().__init__()
      self.props.update(Pairable=True, Discoverable=True)
      self.fail_once = True

    def set_pairing_visibility(self, path, *, pairable, discoverable):
      if self.fail_once:
        self.fail_once = False
        self.props['Pairable'] = pairable
        raise BluetoothRejected('visibility write failed')
      super().set_pairing_visibility(path, pairable=pairable, discoverable=discoverable)

  bluez = PartialBlueZ()
  phone = Phone()
  shared = owner(tmp_path, state, bluez)
  with pytest.raises(BluetoothRejected, match='visibility write failed'):
    shared.acquire_projection_role('AA:BB:CC:DD:EE:FF', phone, enabled=lambda: True, selected_receiver=lambda: 'AA:BB:CC:DD:EE:FF')
  assert bluez.props['Pairable'] is True and bluez.props['Discoverable'] is True
  assert bluez.writes == [(True, True)] and not shared.journal_path.exists()
  assert phone.acquired == 0 and phone.released == 1
  shared.close()


def test_projection_requires_bond_and_revokes_on_bluez_identity_or_forget(tmp_path):
  state = {'parked': True, 'session': ('galaxy', '42')}
  bluez = FakeBlueZ()
  phone = Phone()
  shared = owner(tmp_path, state, bluez)
  bluez.paired[0]['paired'] = False
  with pytest.raises(BluetoothRejected, match='not paired'):
    shared.acquire_projection_role('AA:BB:CC:DD:EE:FF', phone, enabled=lambda: True, selected_receiver=lambda: 'AA:BB:CC:DD:EE:FF')
  assert phone.acquired == 0
  bluez.paired[0]['paired'] = True
  lease = shared.acquire_projection_role('AA:BB:CC:DD:EE:FF', phone, enabled=lambda: True, selected_receiver=lambda: 'AA:BB:CC:DD:EE:FF')
  bluez.sender = ':1.21'
  shared.maintain_phone_role()
  assert lease.released
  lease = shared.acquire_projection_role('AA:BB:CC:DD:EE:FF', phone, enabled=lambda: True, selected_receiver=lambda: 'AA:BB:CC:DD:EE:FF')
  state['parked'] = True
  shared.request('forget', address='AA:BB:CC:DD:EE:FF')
  assert lease.released
  shared.close()


def test_supervisor_default_refuses_independent_bluez_owner(tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import identity as identity_store, supervisor

  monkeypatch.setattr(identity_store, 'CONFIG_PATH', tmp_path / 'config.json')
  obj = supervisor.Supervisor()
  with pytest.raises(RuntimeError, match='Shared StarPilot Bluetooth owner'):
    obj._phone()


def test_incoming_agent_is_registered_only_for_window_and_unregistered_on_close():
  from queue import Queue
  from openpilot.starpilot.system.starport.bluetooth_bridge import AA_AGENT_PATH, PhoneRoleBlueZ

  calls = []
  queue = Queue()

  class Filter:
    def __enter__(self):
      return queue

    def __exit__(self, *_):
      calls.append(('filter_closed',))

  class Router:
    def filter(self, *_args, **_kwargs):
      return Filter()

  client = PhoneRoleBlueZ.__new__(PhoneRoleBlueZ)
  client.router = Mock(wraps=Router())
  client._call = Mock(side_effect=lambda _path, _iface, name, _signature, body: calls.append((name, body)))

  class Agent:
    def close(self):
      calls.append(('agent_closed',))

  close = client.register_incoming_agent(Mock(wraps=Agent()), ':1.20')
  assert calls[:2] == [('RegisterAgent', (AA_AGENT_PATH, 'KeyboardDisplay')), ('RequestDefaultAgent', (AA_AGENT_PATH,))]
  close()
  assert ('UnregisterAgent', (AA_AGENT_PATH,)) in calls
  assert ('agent_closed',) in calls and ('filter_closed',) in calls


def test_default_agent_failure_cleans_registration_before_pairing_visibility():
  from queue import Queue
  from openpilot.starpilot.system.starport.bluetooth_bridge import PhoneRoleBlueZ

  calls = []

  class Filter:
    def __enter__(self):
      return Queue()

    def __exit__(self, *_):
      calls.append('filter_closed')

  class Router:
    def filter(self, *_args, **_kwargs):
      return Filter()

  client = PhoneRoleBlueZ.__new__(PhoneRoleBlueZ)
  client.router = Mock(wraps=Router())

  def call(_path, _iface, name, _signature, _body):
    calls.append(name)
    if name == 'RequestDefaultAgent':
      raise BluetoothRejected('permission denied')

  client._call = Mock(side_effect=call)
  with pytest.raises(BluetoothRejected, match='permission denied'):
    client.register_incoming_agent(Mock(wraps=object()), ':1.20')
  assert calls == ['RegisterAgent', 'RequestDefaultAgent', 'UnregisterAgent', 'filter_closed']
