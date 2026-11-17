import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from unittest.mock import Mock, patch

from jeepney import DBusAddress, new_method_call
from jeepney.low_level import HeaderFields, MessageType

from openpilot.starpilot.bluetooth.radio_preference import RadioPreference
from openpilot.starpilot.bluetooth.owner import (
  AGENT, AGENT_PATH, BlueZ, BluetoothOwner, BluetoothRejected, BluetoothUnavailable, PairingSession, normalized_address,
)


class FakeBlueZ:
  devices = [{'address': 'AA:BB:CC:DD:EE:FF', 'name': 'Saved speaker', 'paired': True,
              'connected': False, 'trusted': True}]
  operations = []
  powered = False
  closed = 0

  def snapshot(self):
    return {'adapter': True, 'powered': self.powered, 'discovering': False,
            'devices': [dict(device) for device in self.devices]}

  def operation(self, operation, address=None):
    self.operations.append((operation, address))
    if operation == 'connect':
      self.devices[0]['connected'] = True
    if operation == 'disconnect':
      self.devices[0]['connected'] = False
    if operation == 'forget':
      self.devices.clear()

  def _objects(self):
    return None

  def _devices(self, _objects):
    return [dict(device, path='/org/bluez/hci0/dev_' + device['address'].replace(':', '_')) for device in self.devices]

  def pair(self, address, path, session):
    self.operations.append(('pair', address))
    accepted, _ = session.ask('confirmation', path, '123456')
    if accepted:
      next(device for device in self.devices if device['address'] == address)['paired'] = True
    return accepted

  def cancel_pair(self, path):
    self.operations.append(('cancel_pair', path))

  def set_powered(self, enabled):
    type(self).powered = enabled

  def close(self):
    type(self).closed += 1


class FakeTimer:
  def __init__(self, delay, callback, args):
    self.delay = delay
    self.callback = callback
    self.args = args
    self.daemon = False
    self.cancelled = False

  def start(self):
    pass

  def cancel(self):
    self.cancelled = True

  def fire(self):
    self.callback(*self.args)


class BluetoothOwnerTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.helper = Path(self.temp.name) / 'bluetooth-radio'
    self.helper.write_bytes(b'helper')
    self.parked = [True]
    self.commands = []
    self.preference = RadioPreference(Path(self.temp.name) / 'BluetoothEnabled')
    FakeBlueZ.devices = [{'address': 'AA:BB:CC:DD:EE:FF', 'name': 'Saved speaker', 'paired': True,
                          'connected': False, 'trusted': True}]
    FakeBlueZ.operations = []
    FakeBlueZ.powered = False
    FakeBlueZ.closed = 0
    self.owner = BluetoothOwner(lambda: self.parked[0], bluez_factory=FakeBlueZ, radio_helper=self.helper,
                                systemctl=self.systemctl, radio_preference=self.preference,
                                timer_factory=FakeTimer, admission_lock=Path(self.temp.name) / 'adapter.lock')
    forget_car = patch.object(BluetoothOwner, '_forget_starport_car')
    self.forgotten_cars = forget_car.start()
    self.addCleanup(forget_car.stop)

  def systemctl(self, command, **kwargs):
    self.commands.append((command, kwargs))
    if command[1] == 'show':
      return subprocess.CompletedProcess(command, 0, 'inactive\n', '')
    if command[-2] == 'start':
      self.assertTrue(self.preference.enabled(), 'AGNOS ExecCondition must see On before starting')
    if command[-2] == 'stop':
      FakeBlueZ.powered = False

  def connectivity_fixture(self):
    from openpilot.starpilot.ui.tests.test_runtime_snapshot import TestParkedClockDomains
    fixture = TestParkedClockDomains()
    fixture.setUp()
    fixture.ui.sm['pandaStates'][0].ignitionCan = True
    self.owner.parked = fixture.adapter.connectivity_allowed
    self.addCleanup(self.owner.close)
    return fixture

  def test_forced_offroad_power_scan_connect_and_revocation_use_actual_owner(self):
    fixture = self.connectivity_fixture()
    self.assertTrue(fixture.adapter.confirmed_offroad())
    self.assertTrue(self.owner.snapshot()['parked'])
    self.assertTrue(self.owner.request('power', enabled=True)['powered'])
    self.owner.request('scan')
    self.assertIn(('scan', None), FakeBlueZ.operations)
    self.owner.request('stop_scan')
    self.assertTrue(self.owner.request('connect', address='AA:BB:CC:DD:EE:FF')['devices'][0]['connected'])
    self.assertFalse(self.owner.request('disconnect', address='AA:BB:CC:DD:EE:FF')['devices'][0]['connected'])
    fixture.ui.started = True
    self.assertFalse(fixture.adapter.confirmed_offroad())
    self.assertFalse(self.owner.snapshot()['parked'])
    before = list(FakeBlueZ.operations)
    self.assertTrue(self.owner.request('connect', address='AA:BB:CC:DD:EE:FF')['devices'][0]['connected'])
    self.assertEqual(FakeBlueZ.operations[len(before):], [('connect', 'AA:BB:CC:DD:EE:FF')])
    with self.assertRaises(BluetoothRejected):
      self.owner.request('power', enabled=False)

  def test_pairing_survives_road_transition_but_not_session_revocation(self):
    fixture = self.connectivity_fixture()
    FakeBlueZ.devices.append({'address': '11:22:33:44:55:66', 'name': 'Nearby', 'paired': False,
                              'connected': False, 'trusted': False})
    identity = ('session-offroad', b'generation')
    self.owner.request('pair', address='11:22:33:44:55:66', session=identity)
    prompt = None
    for _ in range(100):
      prompt = self.owner.snapshot(session=identity)['pairing']['prompt']
      if prompt is not None:
        break
      time.sleep(.01)
    assert prompt is not None
    with self.assertRaises(BluetoothRejected):
      self.owner.request('pairing_response', session=('wrong', b'generation'), prompt_id=prompt['id'], accepted=True)
    self.owner.request('pairing_response', session=identity, prompt_id=prompt['id'], accepted=True)
    self.assertTrue(self.owner.pairing.done.wait(1))
    self.assertTrue(FakeBlueZ.devices[-1]['paired'])
    FakeBlueZ.devices[-1]['paired'] = False
    with self.owner.lock:
      self.owner.request('pair', address='11:22:33:44:55:66', session=identity)
    pairing = self.owner.pairing
    fixture.ui.sm['deviceState'].started = True
    self.assertEqual(self.owner.snapshot(session=identity)['pairing']['state'], 'pairing')
    self.owner.session_valid = lambda _identity: False
    self.owner.snapshot(session=identity)
    self.assertTrue(pairing.done.wait(1))
    self.assertEqual(pairing.state, 'canceled')
    self.assertFalse(FakeBlueZ.devices[-1]['paired'])

  def test_forced_offroad_power_revocation_rolls_back_before_bluez(self):
    fixture = self.connectivity_fixture()
    def revoke_start(command, **kwargs):
      result = self.systemctl(command, **kwargs)
      if command[-2] == 'start':
        fixture.ui.started = True
      return result
    self.owner.systemctl = revoke_start
    with self.assertRaises(BluetoothRejected):
      self.owner.request('power', enabled=True)
    self.assertFalse(FakeBlueZ.powered)
    self.assertFalse(self.preference.path.exists())
    self.assertEqual(self.commands[-1][0], ['sudo', '-n', 'systemctl', 'stop', 'starpilot-bluetooth-radio.service'])

  def test_power_status_and_device_changes_are_observed(self):
    self.assertFalse(self.owner.snapshot()['powered'])
    self.assertTrue(self.owner.request('power', enabled=True)['powered'])
    self.assertEqual(self.commands[1][0], ['sudo', '-n', 'systemctl', 'start', 'starpilot-bluetooth-radio.service'])
    self.assertTrue(self.owner.request('connect', address='aa:bb:cc:dd:ee:ff')['devices'][0]['connected'])
    self.assertEqual(FakeBlueZ.operations[-1], ('connect', 'AA:BB:CC:DD:EE:FF'))
    self.assertFalse(self.owner.request('disconnect', address='AA:BB:CC:DD:EE:FF')['devices'][0]['connected'])
    self.assertEqual(self.owner.request('forget', address='AA:BB:CC:DD:EE:FF')['devices'], [])
    self.forgotten_cars.assert_called_once_with('AA:BB:CC:DD:EE:FF')
    self.assertFalse(self.owner.request('power', enabled=False)['powered'])
    self.assertEqual(self.commands[-1][0], ['sudo', '-n', 'systemctl', 'stop', 'starpilot-bluetooth-radio.service'])
    self.assertGreater(FakeBlueZ.closed, 0)

  def test_parked_and_input_guard_precedes_side_effects(self):
    for address in ('', 'AA:BB:CC:DD:EE:GG', 'AA:BB:CC:DD:EE:FF; touch /tmp/x', '00:11:22:33:44:55\n'):
      with self.subTest(address=address), self.assertRaises(ValueError):
        self.owner.request('connect', address=address)
    with self.assertRaises(ValueError):
      Mock(wraps=self.owner.request)('power', enabled=1)
    with self.assertRaises(ValueError):
      self.owner.request('pair', address='AA:BB:CC:DD:EE:FF')
    self.parked[0] = False
    with self.assertRaises(BluetoothRejected):
      self.owner.request('power', enabled=True)
    self.assertFalse(self.commands)
    self.assertFalse(FakeBlueZ.operations)

  def test_missing_radio_or_adapter_are_unavailable(self):
    self.helper.unlink()
    self.assertEqual(self.owner.snapshot()['errorCode'], 'radio_unavailable')
    with self.assertRaises(BluetoothUnavailable):
      self.owner.request('power', enabled=True)

  def test_starport_can_power_on_onroad_with_a_valid_session(self):
    self.parked[0] = False
    session = ('android-auto-session',)
    self.owner.session_valid = lambda identity: identity == session
    result = self.owner.request('power', enabled=True, session=session, power_on_for_starport=True)
    self.assertTrue(result['powered'])
    self.assertFalse(result['parked'])
    self.assertEqual(self.preference.path.read_bytes(), b'1')
    with self.assertRaises(BluetoothRejected):
      self.owner.request('power', enabled=False, session=session)
    for operation, enabled, identity in [('power', False, session), ('power', True, None), ('scan', None, session)]:
      with self.subTest(operation=operation, enabled=enabled), self.assertRaises(ValueError):
        self.owner.request(operation, enabled=enabled, session=identity, power_on_for_starport=True)

  def test_starport_power_on_rechecks_session_and_rolls_back(self):
    self.parked[0] = False
    valid = [True]
    self.owner.session_valid = lambda _identity: valid[0]
    def revoke_start(command, **kwargs):
      result = self.systemctl(command, **kwargs)
      if command[-2] == 'start':
        valid[0] = False
      return result
    self.owner.systemctl = revoke_start
    with self.assertRaises(BluetoothRejected) as rejected:
      self.owner.request('power', enabled=True, session=('android-auto-session',), power_on_for_starport=True)
    self.assertEqual(rejected.exception.code, 'session_expired')
    self.assertFalse(FakeBlueZ.powered)
    self.assertFalse(self.preference.path.exists())
    self.assertIsNone(self.owner.admission_file)
    self.assertEqual(self.commands[-1][0], ['sudo', '-n', 'systemctl', 'stop', 'starpilot-bluetooth-radio.service'])

  def test_missing_adapter_and_service_failure_are_distinct_and_read_only(self):
    with patch.object(FakeBlueZ, 'snapshot', return_value={'adapter': False, 'powered': False, 'discovering': False, 'devices': []}):
      self.assertEqual(self.owner.snapshot()['errorCode'], 'adapter_unavailable')
    self.preference.path.write_bytes(b'1')
    with patch.object(FakeBlueZ, 'snapshot', side_effect=OSError('system bus unavailable')):
      status = self.owner.snapshot()
      self.assertEqual(status['errorCode'], 'service_unavailable')
      self.assertFalse(status['powered'])
    self.assertFalse(self.commands)
    self.assertFalse(FakeBlueZ.operations)
    self.assertIsNone(self.owner.snapshot()['errorCode'])

  def test_rejections_identify_parked_and_other_radio_owner(self):
    self.parked[0] = False
    with self.assertRaises(BluetoothRejected) as rejected:
      self.owner.request('power', enabled=True)
    self.assertEqual(rejected.exception.code, 'park_required')
    self.parked[0] = True
    self.owner.request('scan')
    other = BluetoothOwner(lambda: True, bluez_factory=FakeBlueZ, radio_helper=self.helper,
                           admission_lock=Path(self.temp.name) / 'adapter.lock', radio_preference=self.preference)
    try:
      with self.assertRaises(BluetoothRejected) as rejected:
        other.request('power', enabled=True)
      self.assertEqual(rejected.exception.code, 'busy')
    finally:
      other.close()
      self.owner.close()

  def test_cold_radio_status_is_read_only_and_explicit_power_starts_service(self):
    running = [False]

    def client():
      if not running[0]:
        raise OSError('org.bluez is not running')
      return FakeBlueZ()

    def systemctl(command, **kwargs):
      result = self.systemctl(command, **kwargs)
      if command[1] == 'show':
        return result
      running[0] = command[-2] == 'start'

    self.owner.bluez_factory = client
    self.owner.systemctl = systemctl
    for _ in range(3):
      status = self.owner.snapshot()
      self.assertTrue(status['available'])
      self.assertFalse(status['powered'])
      self.assertIsNone(status['errorCode'])
    self.assertFalse(self.preference.path.exists())
    self.assertFalse(self.commands)
    self.assertTrue(self.owner.request('power', enabled=True)['powered'])
    self.assertEqual(self.preference.path.read_bytes(), b'1')
    self.assertFalse(self.owner.request('power', enabled=False)['powered'])
    self.assertEqual(self.preference.path.read_bytes(), b'0')
    self.assertFalse(running[0])

  def test_power_failure_restores_exact_prior_preference(self):
    for previous in (None, b'0\n', b'1\n'):
      with self.subTest(previous=previous):
        self.owner.close()
        if previous is None:
          self.preference.path.unlink(missing_ok=True)
        else:
          self.preference.path.write_bytes(previous)
        def refuse_start(command, **kwargs):
          if command[-2] == 'start':
            raise subprocess.CalledProcessError(1, command)
          return self.systemctl(command, **kwargs)
        self.owner.systemctl = refuse_start
        with self.assertRaises(BluetoothUnavailable):
          self.owner.request('power', enabled=True)
        self.assertEqual(self.preference.path.read_bytes() if self.preference.path.exists() else None, previous)
        self.assertIsNone(self.owner.admission_file)

  def test_bluez_failure_rolls_back_preference_while_retaining_radio_lease(self):
    other = BluetoothOwner(lambda: True, bluez_factory=FakeBlueZ, radio_helper=self.helper,
                           admission_lock=Path(self.temp.name) / 'adapter.lock', radio_preference=self.preference)
    self.addCleanup(other.close)
    original_begin = self.preference.begin

    def begin(enabled):
      change = original_begin(enabled)
      original_rollback = change.rollback

      def rollback():
        with self.assertRaises(BluetoothRejected) as rejected:
          other.request('power', enabled=False)
        self.assertEqual(rejected.exception.code, 'busy')
        return original_rollback()

      replacement = patch.object(change, 'rollback', rollback)
      replacement.start()
      self.addCleanup(replacement.stop)
      return change

    replacement = patch.object(self.preference, 'begin', begin)
    replacement.start()
    self.addCleanup(replacement.stop)
    with patch.object(FakeBlueZ, 'set_powered', side_effect=OSError('BlueZ disappeared')):
      with self.assertRaises(BluetoothUnavailable):
        self.owner.request('power', enabled=True)
    self.assertFalse(self.preference.path.exists())
    self.assertIsNone(self.owner.admission_file)

  def test_failed_power_cleans_up_only_newly_started_service(self):
    for service_state in ('inactive', 'failed', 'active', 'activating'):
      with self.subTest(service_state=service_state):
        self.commands.clear()
        self.preference.path.write_bytes(b'0\n')

        def systemctl(command, service_state=service_state, **kwargs):
          if command[1] == 'show':
            return subprocess.CompletedProcess(command, 0, service_state + '\n', '')
          return self.systemctl(command, **kwargs)

        self.owner.systemctl = systemctl
        with patch.object(FakeBlueZ, 'set_powered', side_effect=BluetoothUnavailable('adapter disappeared')):
          with self.assertRaises(BluetoothUnavailable):
            self.owner.request('power', enabled=True)
        self.assertEqual(self.preference.path.read_bytes(), b'0\n')
        stops = [command for command, _ in self.commands if command[-2] == 'stop']
        self.assertEqual(len(stops), int(service_state in ('inactive', 'failed')))

  def test_unknown_service_state_aborts_before_preference_write(self):
    self.owner.systemctl = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
    with self.assertRaises(BluetoothUnavailable):
      self.owner.request('power', enabled=True)
    self.assertFalse(self.preference.path.exists())
    self.assertEqual(self.owner.systemctl.call_count, 1)
    self.assertIsNone(self.owner.admission_file)

  def test_cleanup_failure_is_reported_and_preference_is_restored(self):
    self.preference.path.write_bytes(b'0\n')

    def systemctl(command, **kwargs):
      if command[-2] == 'stop':
        raise subprocess.CalledProcessError(1, command)
      return self.systemctl(command, **kwargs)

    self.owner.systemctl = systemctl
    with patch.object(FakeBlueZ, 'set_powered', side_effect=BluetoothUnavailable('adapter disappeared')):
      with self.assertRaisesRegex(BluetoothUnavailable, 'startup could not be stopped'):
        self.owner.request('power', enabled=True)
    self.assertEqual(self.preference.path.read_bytes(), b'0\n')
    self.assertIsNone(self.owner.admission_file)

  def test_refusal_before_power_does_not_write_preference(self):
    self.preference.path.write_bytes(b'0\n')
    self.owner.session_valid = lambda _: False
    with self.assertRaises(BluetoothRejected):
      self.owner.request('power', enabled=True, session=('expired',))
    self.assertEqual(self.preference.path.read_bytes(), b'0\n')
    self.assertFalse(self.commands)

  def test_failed_power_does_not_overwrite_concurrent_preference_change(self):
    self.preference.path.write_bytes(b'0\n')

    def externally_changed(command, **kwargs):
      if command[1] == 'show':
        return self.systemctl(command, **kwargs)
      self.preference.path.write_bytes(b'1\n')
      raise subprocess.CalledProcessError(1, command)

    self.owner.systemctl = externally_changed
    with self.assertRaises(BluetoothUnavailable):
      self.owner.request('power', enabled=True)
    self.assertEqual(self.preference.path.read_bytes(), b'1\n')

  def test_power_off_failure_restores_on_without_disabling_bluez(self):
    self.preference.path.write_bytes(b'1\n')
    FakeBlueZ.powered = True
    self.owner.systemctl = Mock(side_effect=subprocess.CalledProcessError(1, 'systemctl'))
    with self.assertRaises(BluetoothUnavailable):
      self.owner.request('power', enabled=False)
    self.assertEqual(self.preference.path.read_bytes(), b'1\n')
    self.assertTrue(FakeBlueZ.powered)

  def test_scan_retains_sender_until_stop_expiry_or_owner_close(self):
    now = [10.0]
    self.owner.clock = lambda: now[0]
    self.owner.request('scan')
    self.assertEqual(FakeBlueZ.closed, 0)
    self.assertEqual(FakeBlueZ.operations, [('scan', None)])
    timer = self.owner.scan_timer
    self.assertTrue(timer.daemon)
    now[0] = 29.0
    self.owner.snapshot()
    self.assertEqual(FakeBlueZ.closed, 0)
    now[0] = 30.0
    timer.fire()
    self.assertEqual(FakeBlueZ.operations[-1], ('stop_scan', None))
    self.owner.request('scan')
    old_timer = self.owner.scan_timer
    self.owner.request('scan')
    self.assertTrue(old_timer.cancelled)
    old_timer.fire()
    self.assertEqual(FakeBlueZ.operations[-1], ('scan', None))
    self.owner.close()
    self.assertEqual(FakeBlueZ.operations[-1], ('stop_scan', None))
    self.assertEqual(FakeBlueZ.closed, 1)
    self.owner.request('scan')
    self.assertEqual(FakeBlueZ.closed, 1)
    self.parked[0] = False
    self.owner.snapshot()
    self.assertEqual(FakeBlueZ.operations[-1], ('scan', None))
    now[0] += self.owner.SCAN_SECONDS
    self.owner.scan_timer.fire()
    self.assertEqual(FakeBlueZ.operations[-1], ('stop_scan', None))

  def test_power_bootstrap_rechecks_parked_before_bluez_action(self):
    def revoke_during_start(command, **kwargs):
      if command[1] == 'show':
        return self.systemctl(command, **kwargs)
      self.parked[0] = False
      self.commands.append((command, kwargs))
    self.owner.systemctl = revoke_during_start
    with self.assertRaises(BluetoothRejected):
      self.owner.request('power', enabled=True)
    self.assertFalse(FakeBlueZ.powered)
    self.assertFalse(self.preference.path.exists())

  def test_close_does_not_block_ui_during_native_operation(self):
    entered = threading.Event()
    release = threading.Event()
    original = FakeBlueZ.operation

    def slow_operation(self, operation, address=None):
      entered.set()
      release.wait(2)
      original(self, operation, address)

    self.enterContext(patch.object(FakeBlueZ, 'operation', slow_operation))
    errors = []

    def work():
      try:
        self.owner.request('connect', address='AA:BB:CC:DD:EE:FF')
      except Exception as error:
        errors.append(error)

    worker = threading.Thread(target=work)
    worker.start()
    self.assertTrue(entered.wait(1))
    start = time.monotonic()
    self.owner.close()
    self.assertLess(time.monotonic() - start, 0.1)
    release.set()
    worker.join(timeout=2)
    self.assertFalse(worker.is_alive())
    self.assertFalse(errors)
    self.assertEqual(FakeBlueZ.closed, 1)

  def test_address_normalization(self):
    self.assertEqual(normalized_address('aa:bb:cc:dd:ee:ff'), 'AA:BB:CC:DD:EE:FF')

  def test_adapter_lease_serializes_separate_ui_and_galaxy_owners(self):
    other = BluetoothOwner(lambda: self.parked[0], bluez_factory=FakeBlueZ, radio_helper=self.helper,
                           systemctl=self.owner.systemctl, timer_factory=FakeTimer, radio_preference=self.preference,
                           admission_lock=Path(self.temp.name) / 'adapter.lock')
    self.addCleanup(other.close)
    self.owner.request('scan')
    with self.assertRaises(BluetoothRejected):
      other.request('connect', address='AA:BB:CC:DD:EE:FF')
    self.assertNotIn(('connect', 'AA:BB:CC:DD:EE:FF'), FakeBlueZ.operations)
    self.owner.request('stop_scan')
    self.assertTrue(other.request('connect', address='AA:BB:CC:DD:EE:FF')['devices'][0]['connected'])

  def test_expired_session_rejected_before_pair_side_effect(self):
    FakeBlueZ.devices.append({'address': '11:22:33:44:55:66', 'name': 'Nearby', 'paired': False,
                              'connected': False, 'trusted': False})
    self.owner.session_valid = lambda _identity: False
    with self.assertRaises(BluetoothRejected):
      self.owner.request('pair', address='11:22:33:44:55:66', session=('closed-page',))
    self.assertIsNone(self.owner.pairing)
    self.assertFalse(FakeBlueZ.operations)

  def test_selected_pairing_requires_same_session_prompt_and_observed_bond(self):
    FakeBlueZ.devices.append({'address': '11:22:33:44:55:66', 'name': 'Nearby', 'paired': False,
                              'connected': False, 'trusted': False})
    identity = ('session-a', b'generation')
    started = self.owner.request('pair', address='11:22:33:44:55:66', session=identity)
    self.assertEqual(started['pairing']['state'], 'pairing')
    prompt = None
    for _ in range(100):
      prompt = self.owner.snapshot(session=identity)['pairing']['prompt']
      if prompt is not None:
        break
      time.sleep(0.01)
    assert prompt is not None
    self.assertEqual(prompt['value'], '123456')
    self.assertIsNone(self.owner.snapshot(session=('session-b', b'generation'))['pairing'])
    with self.assertRaises(BluetoothRejected):
      self.owner.request('pairing_response', session=('session-b', b'generation'), prompt_id=prompt['id'], accepted=True)
    with self.assertRaises(BluetoothRejected):
      self.owner.request('pairing_response', session=identity, prompt_id='0' * 32, accepted=True)
    self.owner.request('pairing_response', session=identity, prompt_id=prompt['id'], accepted=True)
    self.assertTrue(self.owner.pairing.done.wait(1))
    result = self.owner.snapshot(session=identity)
    self.assertEqual(result['pairing']['state'], 'paired')
    self.assertTrue(next(device for device in result['devices'] if device['address'] == '11:22:33:44:55:66')['paired'])

  def test_logout_cancels_only_owned_pair_and_new_session_can_pair(self):
    FakeBlueZ.devices.append({'address': '11:22:33:44:55:66', 'name': 'Nearby', 'paired': False,
                              'connected': False, 'trusted': False})
    identity = ('session-a', b'generation')
    self.owner.request('pair', address='11:22:33:44:55:66', session=identity)
    self.owner.cancel_session(('other', b'generation'))
    self.assertEqual(self.owner.snapshot(session=identity)['pairing']['state'], 'pairing')
    self.owner.cancel_session(identity)
    self.assertTrue(self.owner.pairing.done.wait(1))
    self.assertEqual(self.owner.snapshot(session=identity)['pairing']['state'], 'canceled')
    self.assertFalse(FakeBlueZ.devices[-1]['paired'])
    with self.owner.lock:
      self.owner.request('pair', address='11:22:33:44:55:66', session=identity)
    self.owner.session_valid = lambda _identity: False
    self.owner.snapshot(session=identity)
    self.assertTrue(self.owner.pairing.done.wait(1))
    self.assertEqual(self.owner.pairing.state, 'canceled')

  def test_radio_loss_cancels_pending_pair_even_without_followup_action(self):
    FakeBlueZ.devices.append({'address': '11:22:33:44:55:66', 'name': 'Nearby', 'paired': False,
                              'connected': False, 'trusted': False})
    identity = ('session-a', b'generation')
    self.owner.request('pair', address='11:22:33:44:55:66', session=identity)
    pairing = self.owner.pairing
    self.helper.unlink()
    self.assertEqual(self.owner.snapshot(session=identity)['errorCode'], 'radio_unavailable')
    self.assertTrue(pairing.done.wait(1))
    self.assertEqual(pairing.state, 'canceled')
    self.assertFalse(FakeBlueZ.devices[-1]['paired'])

  def test_session_revocation_cancels_pair_without_browser_poll(self):
    FakeBlueZ.devices.append({'address': '11:22:33:44:55:66', 'name': 'Nearby', 'paired': False,
                              'connected': False, 'trusted': False})
    valid = [True]
    self.owner.session_valid = lambda _identity: valid[0]
    identity = ('session-a', b'generation')
    self.owner.request('pair', address='11:22:33:44:55:66', session=identity)
    pairing = self.owner.pairing
    valid[0] = False
    self.assertTrue(pairing.done.wait(1))
    self.assertEqual(pairing.state, 'canceled')
    self.assertFalse(FakeBlueZ.devices[-1]['paired'])

  def test_pairing_deadline_expires_without_browser_poll(self):
    FakeBlueZ.devices.append({'address': '11:22:33:44:55:66', 'name': 'Nearby', 'paired': False,
                              'connected': False, 'trusted': False})
    now = [10.0]
    self.owner.clock = lambda: now[0]
    identity = ('session-a', b'generation')
    self.owner.request('pair', address='11:22:33:44:55:66', session=identity)
    pairing = self.owner.pairing
    now[0] = 71.0
    self.assertTrue(pairing.done.wait(1))
    self.assertEqual(pairing.state, 'canceled')
    self.assertFalse(FakeBlueZ.devices[-1]['paired'])

  def test_pairing_prompts_are_one_use_nonoverlapping_and_deadline_bound(self):
    now = [10.0]
    path = '/org/bluez/hci0/dev_11_22_33_44_55_66'
    session = PairingSession(('session', b'generation'), '11:22:33:44:55:66', path, lambda: True, lambda: now[0])
    answers = []
    worker = threading.Thread(target=lambda: answers.append(session.ask('confirmation', path, '123456')))
    worker.start()
    for _ in range(100):
      if session.prompt is not None:
        break
      time.sleep(0.01)
    assert session.prompt is not None
    prompt = session.prompt
    if prompt is None:
      self.fail('pairing prompt was not created')
    prompt_id = prompt['id']
    self.assertEqual(session.ask('authorization', path), (False, ''))
    self.assertFalse(session.display('display_passkey', path, '654321'))
    prompt = session.prompt
    if prompt is None:
      self.fail('overlapping prompt replaced the original')
    self.assertEqual(prompt['id'], prompt_id)
    self.assertTrue(session.respond(session.identity, prompt_id, True, ''))
    self.assertFalse(session.respond(session.identity, prompt_id, True, ''))
    worker.join(timeout=1)
    self.assertEqual(answers, [(True, '')])
    session.prompt = {'id': 'a' * 32, 'kind': 'confirmation', 'value': '', 'displayOnly': False}
    now[0] = session.deadline
    self.assertFalse(session.respond(session.identity, 'a' * 32, True, ''))
    self.assertFalse(session.display('display_passkey', path, '654321'))
    session.finish(True)
    self.assertEqual(session.state, 'canceled')

  def test_agent_rejects_spoofed_sender_and_malformed_body(self):
    path = '/org/bluez/hci0/dev_11_22_33_44_55_66'
    responses = []
    incoming = Queue()

    class Router:
      @contextmanager
      def filter(self, *_args, **_kwargs):
        yield incoming

      def send(self, message):
        responses.append(message)

    bluez = BlueZ.__new__(BlueZ)
    self.enterContext(patch.object(bluez, 'router', Router(), create=True))
    self.enterContext(patch.object(bluez, '_device_path', lambda _address: path))
    self.enterContext(patch.object(bluez, '_bluez_sender', lambda: ':1.7'))
    self.enterContext(patch.object(bluez, '_objects', lambda: None))
    self.enterContext(patch.object(bluez, '_devices', lambda _objects: []))

    def callback(sender, signature, body):
      message = new_method_call(DBusAddress(AGENT_PATH, bus_name=':1.9', interface=AGENT),
                                'RequestConfirmation', signature, body)
      message.header.fields[HeaderFields.sender] = sender
      return message

    def call(_path, _interface, member, *_args, **_kwargs):
      if member == 'Pair':
        incoming.put(callback(':1.8', 'ou', (path, 123456)))
        incoming.put(callback(':1.7', 'o', (path,)))
        for _ in range(100):
          if len(responses) == 2:
            break
          time.sleep(0.01)
      return ()

    self.enterContext(patch.object(bluez, '_call', call))
    session = PairingSession(('session', b'generation'), '11:22:33:44:55:66', path, lambda: True, time.monotonic)
    self.assertFalse(bluez.pair(session.address, path, session))
    self.assertEqual([item.header.message_type for item in responses], [MessageType.error, MessageType.error])
    self.assertIsNone(session.prompt)

  def test_agent_teardown_wakes_pending_prompt_without_answer(self):
    path = '/org/bluez/hci0/dev_11_22_33_44_55_66'
    session = PairingSession(('session', b'generation'), '11:22:33:44:55:66', path, lambda: True, time.monotonic)
    answers = []
    worker = threading.Thread(target=lambda: answers.append(session.ask('pin', path)))
    worker.start()
    for _ in range(100):
      if session.prompt is not None:
        break
      time.sleep(0.01)
    assert session.prompt is not None
    prompt = session.prompt
    if prompt is None:
      self.fail('pairing prompt was not created')
    prompt_id = prompt['id']
    session.close_callbacks()
    worker.join(timeout=1)
    self.assertFalse(worker.is_alive())
    self.assertEqual(answers, [(False, '')])
    self.assertFalse(session.respond(session.identity, prompt_id, True, '1234'))

  def test_pair_deadline_after_registration_prevents_device_pair_call(self):
    path = '/org/bluez/hci0/dev_11_22_33_44_55_66'
    incoming = Queue()
    calls = []
    now = [10.0]

    class Router:
      @contextmanager
      def filter(self, *_args, **_kwargs):
        yield incoming

    bluez = BlueZ.__new__(BlueZ)
    self.enterContext(patch.object(bluez, 'router', Router(), create=True))
    self.enterContext(patch.object(bluez, '_device_path', lambda _address: path))
    self.enterContext(patch.object(bluez, '_bluez_sender', lambda: ':1.7'))
    def call(_path, _interface, member, *_args, **_kwargs):
      calls.append(member)
      if member == 'RegisterAgent':
        now[0] = 71.0
      return ()
    self.enterContext(patch.object(bluez, '_call', call))
    session = PairingSession(('session', b'generation'), '11:22:33:44:55:66', path, lambda: True, lambda: now[0])
    self.assertFalse(bluez.pair(session.address, path, session))
    self.assertEqual(calls, ['RegisterAgent', 'UnregisterAgent'])

  def test_agent_release_during_successful_unregister_does_not_cancel_pair(self):
    path = '/org/bluez/hci0/dev_11_22_33_44_55_66'
    incoming = Queue()
    replies = []
    calls = []

    class Router:
      @contextmanager
      def filter(self, *_args, **_kwargs):
        yield incoming

      def send(self, message):
        replies.append(message)

    bluez = BlueZ.__new__(BlueZ)
    self.enterContext(patch.object(bluez, 'router', Router(), create=True))
    self.enterContext(patch.object(bluez, '_device_path', lambda _address: path))
    self.enterContext(patch.object(bluez, '_bluez_sender', lambda: ':1.7'))
    self.enterContext(patch.object(bluez, '_objects', lambda: None))
    self.enterContext(patch.object(bluez, '_devices', lambda _objects: [{'address': '11:22:33:44:55:66', 'paired': True}]))
    def call(_path, _interface, member, *_args, **_kwargs):
      calls.append((_path, _interface, member, _args))
      if member == 'UnregisterAgent':
        message = new_method_call(DBusAddress(AGENT_PATH, bus_name=':1.9', interface=AGENT), 'Release')
        message.header.fields[HeaderFields.sender] = ':1.7'
        incoming.put(message)
        for _ in range(100):
          if replies:
            break
          time.sleep(0.01)
      return ()
    self.enterContext(patch.object(bluez, '_call', call))
    session = PairingSession(('session', b'generation'), '11:22:33:44:55:66', path, lambda: True, time.monotonic)
    self.assertTrue(bluez.pair(session.address, path, session))
    self.assertIn((path, 'org.freedesktop.DBus.Properties', 'Set', ('ssv', ('org.bluez.Device1', 'Trusted', ('b', True)))), calls)
    self.assertEqual([item.header.message_type for item in replies], [MessageType.method_return])
    session.finish(True)
    self.assertEqual(session.state, 'paired')

  def test_bluez_uses_object_paths_from_observed_adapter_and_device(self):
    bluez = BlueZ.__new__(BlueZ)
    calls = []
    objects = {
      '/org/bluez/hci0': {'org.bluez.Adapter1': {'Powered': ('b', True), 'Discovering': ('b', False)}},
      '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF': {'org.bluez.Device1': {
        'Address': ('s', 'AA:BB:CC:DD:EE:FF'), 'Alias': ('s', 'Speaker'),
        'Paired': ('b', True), 'Connected': ('b', False), 'Trusted': ('b', True),
      }},
    }

    def call(path, interface, member, signature=None, body=()):
      calls.append((path, interface, member, signature, body))
      return (objects,) if member == 'GetManagedObjects' else ()

    self.enterContext(patch.object(bluez, '_call', call))
    self.assertEqual(bluez.snapshot()['devices'][0]['name'], 'Speaker')
    self.assertTrue(bluez.snapshot()['powered'])
    bluez.operation('connect', 'AA:BB:CC:DD:EE:FF')
    self.assertIn(('/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF', 'org.bluez.Device1', 'Connect', None, ()), calls)
    bluez.operation('forget', 'AA:BB:CC:DD:EE:FF')
    self.assertIn(('/org/bluez/hci0', 'org.bluez.Adapter1', 'RemoveDevice', 'o',
                   ('/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF',)), calls)

  def test_each_bluez_close_releases_router_and_underlying_connection(self):
    closed = []

    class Router:
      def __init__(self, index):
        self.index = index
        self.conn = self

      def close(self):
        closed.append(('router', self.index))

    class Connection(Router):
      def close(self):
        closed.append(('connection', self.index))

    for index in range(3):
      client = BlueZ.__new__(BlueZ)
      router = Router(index)
      router.conn = Connection(index)
      self.enterContext(patch.object(client, 'router', router, create=True))
      client.close()
    self.assertEqual(closed, [item for index in range(3) for item in (('router', index), ('connection', index))])
    failed = BlueZ.__new__(BlueZ)
    router = Router(3)
    router.conn = Connection(3)
    self.enterContext(patch.object(failed, 'router', router, create=True))
    self.enterContext(patch.object(router, 'close', lambda: (_ for _ in ()).throw(OSError('receiver failed'))))
    with self.assertRaises(OSError):
      failed.close()
    self.assertEqual(closed[-1], ('connection', 3))


if __name__ == '__main__':
  unittest.main()


class BluetoothDiscoveryNamesTest(unittest.TestCase):
  def test_anonymous_discovery_is_hidden_but_saved_device_can_be_removed(self):
    objects = {f'/org/bluez/hci0/dev_{i}': {'org.bluez.Device1': props} for i, props in enumerate((
      {'Address': '11:22:33:44:55:01', 'Alias': '11-22-33-44-55-01'},
      {'Address': '11:22:33:44:55:02', 'Name': '  '},
      {'Address': '11:22:33:44:55:03', 'Name': 'Controller', 'Alias': '11-22-33-44-55-03'},
      {'Address': '11:22:33:44:55:04', 'Alias': 'My headset'},
      {'Address': '11:22:33:44:55:05', 'Paired': True},
    ))}
    found = BlueZ._devices(objects)
    self.assertEqual([d['name'] for d in found], ['Controller', 'My headset', '11:22:33:44:55:05'])
    self.assertTrue(found[-1]['paired'])


class BluetoothHeadUnitDiscoveryTest(unittest.TestCase):
  def test_unnamed_pairable_profiles_and_device_class_are_visible(self):
    props = [
      {'UUIDs': ['0000111e-0000-1000-8000-00805f9b34fb']},
      {'Class': 0x200404},
      {'UUIDs': ['4de17a00-52cb-11e6-bdf4-0800200c9a66']},
      {'UUIDs': ['00001812-0000-1000-8000-00805f9b34fb']},
      {'LegacyPairing': True},
      {'RSSI': -40},
      {'Class': 0x200404, 'Blocked': True},
    ]
    objects = {f'/org/bluez/hci0/dev_{i}': {'org.bluez.Device1': dict(p, Address=f'11:22:33:44:55:{i:02X}')}
               for i, p in enumerate(props)}
    found = BlueZ._devices(objects)
    self.assertEqual({d['address'] for d in found}, {f'11:22:33:44:55:{i:02X}' for i in range(5)})
    self.assertTrue(all(d['name'] for d in found))
    self.assertTrue(all(not d['paired'] for d in found))


class BluetoothAudioOutputTest(unittest.TestCase):
  def test_only_audio_sink_profile_can_receive_music(self):
    profiles = ['0000110B-0000-1000-8000-00805F9B34FB', '0000110a-0000-1000-8000-00805f9b34fb',
                '4de17a00-52cb-11e6-bdf4-0800200c9a66', None]
    objects = {f'/org/bluez/hci0/dev_{i}': {'org.bluez.Device1': {
      'Address': f'11:22:33:44:55:0{i}', 'Paired': True, 'UUIDs': [profile] if profile else [],
    }} for i, profile in enumerate(profiles)}
    self.assertEqual([device['audioSink'] for device in BlueZ._devices(objects)], [True, False, False, False])
