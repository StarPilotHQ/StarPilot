"""Actual owner and daemon imports with hardware operations replaced by fakes."""
import threading
from types import SimpleNamespace
import unittest

from unittest.mock import Mock, patch
from openpilot.starpilot.bluetooth.owner import BluetoothRejected as Rejected
from openpilot.starpilot.system.starport.bluetooth_bridge import SharedBluetoothOwner as OwnerMethods
from openpilot.starpilot.system.starport import daemon

CAR = '02:00:00:00:00:01'
ADAPTER = '02:00:00:00:00:02'
HFP = '0000111e-0000-1000-8000-00805f9b34fb'
SESSION = ('galaxy', 'live-proof')

class CompanionTests(unittest.TestCase):
  def setUp(self):
    self.state: dict = {'parked': True, 'valid': True, 'enabled': True, 'selected': ADAPTER}
    self.devices: dict[str, dict] = {CAR: {'address': CAR, 'path': '/org/bluez/hci0/dev_00_92_A5_2F_68_32',
      'paired': True, 'connected': False, 'uuids': [HFP]},
      ADAPTER: {'address': ADAPTER, 'path': '/org/bluez/hci0/dev_50_5A_65_8B_8B_82',
      'paired': True, 'connected': True, 'uuids': []}}
    self.calls = []
    self.hook = lambda: None
    self.owner = OwnerMethods.__new__(OwnerMethods)
    self.owner.lock = threading.RLock()
    self.owner.parked = lambda: self.state['parked']
    self.owner.session_valid = lambda session: session == SESSION and self.state['valid']
    self.owner.companion_devices = {}
    self.forgotten = []
    self.client = SimpleNamespace(adapter_identity=lambda: ('/org/bluez/hci0', {'Powered': True}, ':1.5'),
      _objects=dict, _devices=lambda objects: list(self.devices.values()),
      operation=lambda operation, address: self.forgotten.append((operation, address)))
    self.owner._client = Mock(side_effect=lambda: self.client)
    def call(*args, **kwargs):
      self.calls.append((args, kwargs))
      self.hook()
      self.devices[CAR]['connected'] = args[2] == 'ConnectProfile'
    self.phone = SimpleNamespace(device=lambda address: self.devices.get(address), _call=call)
    self.lease = Mock(phone=self.phone, receiver=ADAPTER, session=None, released=False,
      adapter_path='/org/bluez/hci0', bluez_sender=':1.5', device_path=self.devices[ADAPTER]['path'], enabled=lambda: self.state['enabled'],
      selected_receiver=lambda: self.state['selected'])
    self.owner.phone_lease = self.lease
    self.owner.snapshot = Mock(side_effect=lambda session: {'version': 1, 'available': True, 'parked': self.state['parked'],
      'powered': True, 'discovering': False, 'errorCode': None, 'pairing': None,
      'devices': [{'address': address, 'connected': device['connected']} for address, device in self.devices.items()]})

  def test_explicit_car_hfp_admission_and_disconnect_preserve_adapter_and_lease(self):
    self.assertFalse(self.owner.companion_accepts(CAR))
    def callback(): self.assertTrue(self.owner.companion_accepts(CAR))
    self.hook = callback
    result = self.owner.companion_action('connect', CAR, SESSION)
    assert result is not None
    self.assertEqual(self.calls[0], ((self.devices[CAR]['path'], 'org.bluez.Device1', 'ConnectProfile', 's', (HFP,)), {'timeout': 12.0}))
    self.assertTrue(result['devices'][0]['connected'])
    self.assertIs(self.owner.phone_lease, self.lease)
    self.owner.companion_action('disconnect', CAR, SESSION)
    self.assertEqual(self.calls[1][0][2], 'Disconnect')
    self.assertFalse(self.owner.companion_accepts(CAR))
    self.assertTrue(self.devices[ADAPTER]['connected'])
    self.assertFalse(self.lease.released)

  def test_selected_adapter_and_unpaired_or_foreign_peers_never_change_radio(self):
    for address, alteration in [(ADAPTER, {}), (CAR, {'paired': False}),
                                (CAR, {'path': '/org/bluez/hci1/dev_car'})]:
      before = dict(self.devices[address])
      self.devices[address].update(alteration)
      with self.assertRaises(Rejected):
        self.owner.companion_action('connect', address, SESSION)
      self.devices[address] = before
    self.assertEqual(self.calls, [])
    self.assertEqual(self.owner.companion_devices, {})

  def test_accessory_connect_preserves_the_selected_car_and_projection(self):
    from openpilot.starpilot.system.starport.supervisor import Supervisor
    self.devices[CAR]['uuids'] = []  # HID controller, not an HFP car.
    def operation(operation, address):
      self.forgotten.append((operation, address))
      self.devices[address]['connected'] = operation == 'connect'
    self.client.operation = operation
    host = Supervisor.__new__(Supervisor)
    host._lock = threading.RLock()
    host.config = {'receiver_address': ADAPTER, 'companion_address': 'saved-car', 'companion_name': 'My car'}
    host._shared_bluetooth_owner = self.owner
    with patch.object(host, '_phone', return_value=self.phone), patch('openpilot.starpilot.system.starport.identity.save_config') as save:
      host.companion_bluetooth_action('connect', CAR, SESSION)
      host.companion_bluetooth_action('disconnect', CAR, SESSION)
      save.assert_not_called()
    self.assertEqual(self.forgotten, [('connect', CAR), ('disconnect', CAR)])
    self.assertEqual(host.config['companion_address'], 'saved-car')
    self.assertEqual(self.owner.companion_devices, {})
    self.assertFalse(self.lease.released)

  def test_session_pairing_and_changed_owner_are_fail_closed(self):
    alterations = [('valid', False), ('enabled', False), ('selected', CAR)]
    for key, value in alterations:
      before = self.state[key]
      self.state[key] = value
      with self.assertRaises(Rejected):
        self.owner.companion_action('connect', CAR, SESSION)
      self.state[key] = before
    self.lease.session = SESSION
    with self.assertRaises(Rejected) as error:
      self.owner.companion_action('connect', CAR, SESSION)
    self.assertEqual(error.exception.code, 'busy')
    self.assertEqual(self.calls, [])
    self.assertFalse(self.lease.released)

  def test_no_owner_is_only_fallback_case_and_unsupported_operations_stay_denied(self):
    self.owner.phone_lease = None
    self.assertIsNone(self.owner.companion_action('connect', CAR, SESSION))
    for operation in ('scan', 'pair', 'power', 'stop_scan'):
      with self.assertRaises(ValueError):
        self.owner.companion_action(operation, CAR, SESSION)
    self.assertEqual(self.calls, [])

  def test_forget_removes_another_device_while_projection_keeps_its_lease(self):
    other = '02:00:00:00:00:09'
    self.devices[other] = {'address': other, 'path': '/org/bluez/hci0/dev_old', 'paired': True, 'connected': False, 'uuids': []}
    self.owner.companion_devices[other] = self.devices[other]['path']
    result = self.owner.companion_action('forget', other, SESSION)
    self.assertIsNotNone(result)
    self.assertEqual(self.forgotten, [('forget', other)])
    self.assertNotIn(other, self.owner.companion_devices)
    self.assertFalse(self.lease.released)
    self.assertIs(self.owner.phone_lease, self.lease)

  def test_forget_never_removes_the_projection_receiver_or_foreign_devices(self):
    with self.assertRaises(Rejected) as error:
      self.owner.companion_action('forget', ADAPTER, SESSION)
    self.assertEqual(error.exception.code, 'projection_active')
    self.devices[CAR]['path'] = '/org/bluez/hci1/dev_car'
    with self.assertRaises(Rejected):
      self.owner.companion_action('forget', CAR, SESSION)
    del self.devices[CAR]
    with self.assertRaises(Rejected):
      self.owner.companion_action('forget', CAR, SESSION)
    self.assertEqual(self.forgotten, [])

  def test_forget_is_fail_closed_during_pairing_and_for_expired_sessions(self):
    self.state['valid'] = False
    with self.assertRaises(Rejected):
      self.owner.companion_action('forget', CAR, SESSION)
    self.state['valid'] = True
    self.lease.session = SESSION
    with self.assertRaises(Rejected):
      self.owner.companion_action('forget', CAR, SESSION)
    self.assertEqual(self.forgotten, [])

  def test_failed_connect_revokes_new_admission_without_releasing_projection(self):
    def failure(): raise TimeoutError('D-Bus operation timed out')
    self.hook = failure
    with self.assertRaises(TimeoutError):
      self.owner.companion_action('connect', CAR, SESSION)
    self.assertFalse(self.owner.companion_accepts(CAR))
    self.assertFalse(self.lease.released)
    self.assertIs(self.owner.phone_lease, self.lease)
    self.assertTrue(self.devices[ADAPTER]['connected'])

  def test_source_change_during_operation_does_not_claim_success(self):
    for key in ('valid',):
      self.state[key] = True
      self.hook = lambda key=key: self.state.update({key: False})
      with self.assertRaises(Rejected):
        self.owner.companion_action('connect', CAR, SESSION)
      self.assertEqual(self.owner.companion_devices, {})
      self.state[key] = True

  def test_companion_callback_revalidates_bond_path_and_projection_selection(self):
    self.owner.companion_action('connect', CAR, SESSION)
    for key, value in [('paired', False), ('path', '/org/bluez/hci0/new_device'), ('uuids', [])]:
      before = self.devices[CAR][key]
      self.devices[CAR][key] = value
      self.owner.maintain_phone_role()
      self.assertFalse(self.owner.companion_accepts(CAR))
      self.devices[CAR][key] = before
      self.owner.companion_action('connect', CAR, SESSION)
    self.state['selected'] = CAR
    self.assertFalse(self.owner.companion_accepts(CAR))

  def test_hfp_callback_never_queries_bluez_while_connect_waits_for_callback(self):
    def callback():
      original = self.phone.device
      self.phone.device = lambda address: (_ for _ in ()).throw(AssertionError('Callback queried BlueZ'))
      try:
        self.assertTrue(self.owner.companion_accepts(CAR))
      finally:
        self.phone.device = original
    self.hook = callback
    self.owner.companion_action('connect', CAR, SESSION)

  def test_saved_companion_reconnect_confirms_car_before_adapter_work(self):
    def callback(): self.assertTrue(self.owner.companion_accepts(CAR))
    self.hook = callback
    result = self.owner.prepare_companion(CAR, lambda: CAR, connect=True)
    self.assertTrue(result['connected'])
    self.assertEqual(self.calls[0][0][2:5], ('ConnectProfile', 's', (HFP,)))
    self.assertTrue(self.devices[ADAPTER]['connected'])
    self.assertFalse(self.lease.released)

  def test_saved_companion_standby_admission_and_reconnect_fail_closed(self):
    self.owner.prepare_companion(CAR, lambda: CAR)
    self.assertTrue(self.owner.companion_accepts(CAR))
    self.assertEqual(self.calls, [])
    self.owner.companion_devices.clear()
    with self.assertRaises(Rejected):
      self.owner.prepare_companion(CAR, lambda: ADAPTER, connect=True)
    self.devices[CAR]['paired'] = False
    with self.assertRaises(Rejected):
      self.owner.prepare_companion(CAR, lambda: CAR, connect=True)
    self.assertEqual(self.calls, [])
    self.assertEqual(self.owner.companion_devices, {})

  def test_saved_companion_changed_during_connect_revokes_callback(self):
    selected = [CAR]
    self.hook = lambda: selected.__setitem__(0, ADAPTER)
    with self.assertRaises(Rejected):
      self.owner.prepare_companion(CAR, lambda: selected[0], connect=True)
    self.assertFalse(self.owner.companion_accepts(CAR))
    self.assertFalse(self.lease.released)

  def test_manual_car_save_cannot_cross_receiver_change(self):
    from openpilot.starpilot.system.starport.supervisor import Supervisor
    host = Supervisor.__new__(Supervisor)
    host._lock = threading.RLock()
    host._thread, host._bluez = None, None
    host.config = {'receiver_address': ADAPTER, 'companion_address': '', 'companion_name': ''}
    host._shared_bluetooth_owner = self.owner
    host._phone = Mock(side_effect=lambda: self.phone)
    host._unselect_car_audio = Mock(side_effect=lambda address: None)
    self.phone.acquire = lambda: None
    self.phone.set_trusted = lambda address: None
    self.devices[CAR]['name'] = 'My car'
    replacement = '02:00:00:00:00:03'
    self.devices[replacement] = dict(self.devices[ADAPTER], address=replacement, name='New adapter', starport=True)
    started, switched = threading.Event(), threading.Event()
    saved, threads = [], []
    def change_receiver():
      started.set()
      host.select_receiver(replacement)
      switched.set()
    def during_connect():
      thread = threading.Thread(target=change_receiver)
      threads.append(thread)
      thread.start()
      self.assertTrue(started.wait(1))
      self.assertFalse(switched.wait(.05))
    self.hook = during_connect
    with patch('openpilot.starpilot.system.starport.identity.save_config', side_effect=lambda config: saved.append(dict(config))):
      host.companion_bluetooth_action('connect', CAR, SESSION)
      for thread in threads:
        thread.join(1)
        self.assertFalse(thread.is_alive())
    self.assertTrue(switched.is_set())
    self.assertEqual(saved[0]['receiver_address'], ADAPTER)
    self.assertEqual(saved[0]['companion_address'], CAR)
    self.assertEqual(saved[-1]['receiver_address'], replacement)
    self.assertEqual(saved[-1]['companion_address'], '')

  def test_manual_car_save_revalidates_source_after_action(self):
    from openpilot.starpilot.system.starport.supervisor import Supervisor
    host = Supervisor.__new__(Supervisor)
    host._lock = threading.RLock()
    host.config = {'receiver_address': ADAPTER, 'companion_address': '', 'companion_name': ''}
    host._shared_bluetooth_owner = self.owner
    host._phone = Mock(side_effect=lambda: self.phone)
    self.devices[CAR]['name'] = 'My car'
    # Simulate revocation immediately after the owner's connection returned.
    action = self.owner.companion_action
    def revoke_after_action(*args):
      result = action(*args)
      self.state['valid'] = False
      return result
    with patch.object(self.owner, 'companion_action', side_effect=revoke_after_action), \
         patch('openpilot.starpilot.system.starport.identity.save_config') as save:
      with self.assertRaisesRegex(RuntimeError, 'authorization changed'):
        host.companion_bluetooth_action('connect', CAR, SESSION)
      save.assert_not_called()
    self.assertEqual(host.config['companion_address'], '')

  def test_forgetting_the_selected_car_stops_its_retry_then_deletes_it(self):
    from openpilot.starpilot.system.starport.supervisor import Supervisor
    host = Supervisor.__new__(Supervisor)
    host._lock = threading.RLock()
    host.config = {'receiver_address': ADAPTER}
    host._shared_bluetooth_owner = self.owner
    host.auto = Mock()
    alive = {'value': True}
    host._session_alive = Mock(side_effect=lambda: alive['value'])
    host.stop = Mock(side_effect=lambda: alive.update(value=False))
    host.forget_receiver = Mock(return_value=True)
    self.owner.request = Mock(return_value={'devices': []})  # test double
    self.assertEqual(host.companion_bluetooth_action('forget', ADAPTER.lower(), SESSION), {'devices': []})
    host.auto.manual_stop.assert_called_once_with(True)
    host.stop.assert_called_once_with()
    self.owner.request.assert_called_once_with('forget', address=ADAPTER.lower())
    host.forget_receiver.assert_called_once_with(ADAPTER.lower())
    host.forget_receiver.side_effect = OSError('Cannot save config')
    with self.assertRaisesRegex(OSError, 'Cannot save config'):
      host.companion_bluetooth_action('forget', ADAPTER, SESSION)

  def test_forgetting_the_selected_car_waits_out_pairing_and_expired_sessions(self):
    from openpilot.starpilot.system.starport.supervisor import Supervisor
    host = Supervisor.__new__(Supervisor)
    host.config = {'receiver_address': ADAPTER}
    host._shared_bluetooth_owner = self.owner
    host.stop = Mock()
    self.lease.session = SESSION
    with self.assertRaises(Rejected) as error:
      host.companion_bluetooth_action('forget', ADAPTER, SESSION)
    self.assertEqual(error.exception.code, 'busy')
    self.lease.session = None
    self.state['valid'] = False
    with self.assertRaises(Rejected) as error:
      host.companion_bluetooth_action('forget', ADAPTER, SESSION)
    self.assertEqual(error.exception.code, 'session_expired')
    host.stop.assert_not_called()

  def daemon_call(self, operation='connect', address=CAR, valid=True, parked=True):
    supervisor = SimpleNamespace(companion_bluetooth_action=self.owner.companion_action,
      bind_source_session=lambda session: self.fail('Companion command must not rebind projection source'))
    verifier = SimpleNamespace(valid=lambda source: valid)
    request = {'command': 'bluetooth_action', 'source': SESSION[1], 'operation': operation, 'address': address}
    with patch('openpilot.common.params.Params') as params:
      params.return_value.get_bool.return_value = True
      return daemon.handle(Mock(wraps=supervisor), request, Mock(wraps=verifier), parked=lambda: parked)

  def test_daemon_returns_snapshot_or_unhandled_only_without_owner(self):
    result = self.daemon_call()
    self.assertTrue(result['handled'])
    self.assertIn('bluetooth', result)
    self.owner.phone_lease = None
    self.assertEqual(self.daemon_call(), {'handled': False})

  def test_daemon_denials_and_timeouts_never_produce_normal_owner_fallback(self):
    self.assertEqual(self.daemon_call(address=ADAPTER), {'handled': True, 'error_code': 'projection_active',
                                                         'message': 'Stop projection before changing its adapter'})
    self.lease.session = SESSION
    self.assertEqual(self.daemon_call(), {'handled': True, 'error_code': 'busy', 'message': 'Finish Android Auto pairing first'})
    self.lease.session = None
    def timeout(): raise TimeoutError('No profile reply')
    self.hook = timeout
    self.assertEqual(self.daemon_call(), {'handled': True, 'error_code': 'service_unavailable'})
    self.assertFalse(self.lease.released)

  def test_power_off_goes_through_the_owner_and_power_on_is_a_no_op(self):
    self.owner.request = Mock(return_value={'powered': False})  # test double
    self.assertEqual(self.owner.companion_power(False, SESSION), {'powered': False})
    self.owner.request.assert_called_once_with('power', enabled=False, session=SESSION)
    self.owner.request.reset_mock()
    result = self.owner.companion_power(True, SESSION)
    assert result is not None
    self.assertTrue(result['powered'])
    self.owner.request.assert_not_called()

  def test_power_refusals_and_no_lease(self):
    self.state['valid'] = False
    with self.assertRaises(Rejected) as error:
      self.owner.companion_power(False, SESSION)
    self.assertEqual(error.exception.code, 'session_expired')
    self.state['valid'] = True
    self.lease.session = SESSION
    with self.assertRaises(Rejected) as error:
      self.owner.companion_power(False, SESSION)
    self.assertEqual(error.exception.code, 'busy')
    self.lease.session = None
    self.owner.phone_lease = None
    self.assertIsNone(self.owner.companion_power(False, SESSION))

  def power_call(self, enabled=False, valid=True, parked=True):
    supervisor = SimpleNamespace(companion_power=self.owner.companion_power,
      bind_source_session=lambda session: self.fail('Companion command must not rebind projection source'))
    request = {'command': 'bluetooth_power', 'source': SESSION[1], 'enabled': enabled}
    with patch('openpilot.common.params.Params') as params:
      params.return_value.get_bool.return_value = True
      return daemon.handle(Mock(wraps=supervisor), request, Mock(wraps=SimpleNamespace(valid=lambda source: valid)), parked=lambda: parked)

  def test_daemon_power_command_requires_proof_park_and_a_boolean(self):
    self.owner.request = Mock(return_value={'powered': False})  # test double
    self.assertEqual(self.power_call(), {'handled': True, 'bluetooth': {'powered': False}})
    self.assertEqual(self.power_call(parked=False), {'handled': True, 'error_code': 'park_required'})
    with self.assertRaises(RuntimeError):
      self.power_call(valid=False)
    with self.assertRaises(ValueError):
      self.power_call(enabled='off')  # wrong type on purpose
    self.lease.session = SESSION
    self.assertEqual(self.power_call(), {'handled': True, 'error_code': 'busy', 'message': 'Finish Android Auto pairing first'})
    self.owner.phone_lease = None
    self.assertEqual(self.power_call(), {'handled': False})

  def test_daemon_requires_live_source_and_park_before_touching_owner(self):
    with self.assertRaises(RuntimeError):
      self.daemon_call(valid=False)
    self.assertEqual(self.daemon_call(parked=False), {'handled': True, 'error_code': 'park_required'})
    self.assertEqual(self.calls, [])

  def test_existing_companion_connection_remains_available_onroad(self):
    self.state["parked"] = False
    self.hook = lambda: self.state.update(parked=False)
    result = self.owner.companion_action("connect", CAR, SESSION)
    assert result is not None
    self.assertFalse(result["parked"])
    self.assertTrue(self.owner.companion_accepts(CAR))
    self.assertFalse(self.lease.released)

if __name__ == '__main__':
  unittest.main()
