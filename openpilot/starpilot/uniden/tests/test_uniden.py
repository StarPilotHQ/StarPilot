import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openpilot.starpilot.bluetooth.owner import ADAPTER, DEVICE, BluetoothOwner, BluetoothRejected
from openpilot.starpilot.system.starport.bluetooth_bridge import SharedBluetoothOwner
from openpilot.starpilot.uniden.protocol import ALERT_UUID, WRITE_UUID, GATT, DEFAULT_CONFIG, alert_key, normalize_config, parse_alerts
from openpilot.starpilot.uniden.transport import exchange
from openpilot.starpilot.uniden.service import DetectorService
from openpilot.starpilot.uniden.feed import DetectorFeed

ADDRESS = 'AA:BB:CC:DD:EE:FF'
ADAPTER_PATH = '/org/bluez/hci0'
PATH = ADAPTER_PATH + '/dev_AA_BB_CC_DD_EE_FF'
PACKET = b'1,00,KA,3,33,33.7850,R,1&1,00,K,4,45,24.1500,F,2&0&0'
CONFIG = {'enabled': True, 'address': ADDRESS, 'model': 'R8',
          'auto_mute': False, 'hide_filtered': False, 'frequency_rules': []}


@pytest.fixture
def setup(tmp_path):
  objects = {ADAPTER_PATH: {ADAPTER: {'Powered': True}}, PATH: {DEVICE: {
    'Name': 'R8W@12', 'Address': ADDRESS, 'Paired': True, 'Connected': True, 'ServicesResolved': True}},
    PATH + '/service/alert': {GATT: {'UUID': ALERT_UUID}}, PATH + '/service/write': {GATT: {'UUID': WRITE_UUID}}}
  client = Mock()
  client._objects.side_effect = lambda: objects
  client.adapter_identity.side_effect = lambda: (ADAPTER_PATH, objects[ADAPTER_PATH][ADAPTER], ':1.5')
  client._call.return_value = (PACKET,)
  owner = SharedBluetoothOwner(lambda: False, session_valid=lambda s: s == ('live',),
                               bluez_factory=lambda: client, lock_path=tmp_path / 'owner.lock')
  return owner, client, objects


def test_packets_and_r4():
  alerts = parse_alerts(PACKET)
  assert alerts[0]['band'] == 'K' and alerts[0]['muted']
  assert alerts[1]['frequency'] == 33.785 and alerts[1]['direction'] == 'rear'
  assert all(a['direction'] == '' for a in parse_alerts(PACKET, 'R4'))
  assert parse_alerts(b'0&0&0&0') == []
  assert parse_alerts(b'1,00,LASER,8,0,5,F,1')[0]['frequency'] is None


def test_photo_radar_has_no_frequency_and_unknown_mute_keeps_alert():
  assert parse_alerts(b'1,00,MRCD,4,0,3,F,1')[0]['frequency'] is None
  assert parse_alerts(b'1,00,MRCT,4,0,x,F,1')[0]['frequency'] is None
  alert = parse_alerts(b'1,00,KA,3,0,33.785,F,8')[0]
  assert alert['band'] == 'KA' and alert['frequency'] == 33.785 and not alert['muted']


@pytest.mark.parametrize('packet', [b'', b'1,00,KA,9,0,33,F,1', b'1,00,KA,3,0,nan,F,1', b'1,00,K,3',
                                    b'0&broken', b'\xff'])
def test_bad_snapshots_rejected(packet):
  with pytest.raises(ValueError):
    parse_alerts(packet)


def test_read_and_mute_onroad(setup):
  owner, client, _ = setup
  state = exchange(owner, ADDRESS, 'R8', ('live',))
  assert owner.admission_file is None and state['connected']
  result = exchange(owner, ADDRESS, 'R8', ('live',), mute={'observed_at': state['observed_at'], 'alerts': alert_key(state['alerts'])})
  assert result['command'] == 'sent_unconfirmed'
  assert not result['alerts'][1]['muted']  # Never fabricate acknowledgment.
  assert client._call.call_args.args[2:] == ('WriteValue', 'aya{sv}', (b'BTreqMUTE:1', {'type': ('s', 'command')}))


@pytest.mark.parametrize('change', ['unpaired', 'wrong_model', 'unresolved', 'missing_characteristic', 'discovering', 'pairing', 'expired'])
def test_invalid_device_never_writes(setup, change):
  owner, client, objects = setup
  session = ('live',)
  if change == 'unpaired':
    objects[PATH][DEVICE]['Paired'] = False
  if change == 'wrong_model':
    objects[PATH][DEVICE]['Name'] = 'Phone'
  if change == 'unresolved':
    objects[PATH][DEVICE]['ServicesResolved'] = False
  if change == 'missing_characteristic':
    del objects[PATH + '/service/alert']
  if change == 'discovering':
    objects[ADAPTER_PATH][ADAPTER]['Discovering'] = True
  if change == 'pairing':
    owner.pairing = NS(done=threading.Event())
  if change == 'expired':
    session = ('old',)
  with pytest.raises(BluetoothRejected):
    exchange(owner, ADDRESS, 'R8', session, mute={'observed_at': time.monotonic(), 'alerts': alert_key(parse_alerts(PACKET))})
  assert not any(c.args[2] == 'WriteValue' for c in client._call.call_args_list)


def test_cross_process_pairing_lock_is_respected(setup):
  owner, client, _ = setup
  other = BluetoothOwner(lambda: True, admission_lock=owner.admission_lock)
  other._admission_enter()
  try:
    with pytest.raises(BluetoothRejected):
      exchange(owner, ADDRESS, 'R8', ('live',))
    client._call.assert_not_called()
  finally:
    other._admission_leave()


def lease():
  return NS(session=None, released=False, enabled=lambda: True, selected_receiver=lambda: '11:22:33:44:55:66',
            receiver='11:22:33:44:55:66', adapter_path=ADAPTER_PATH, bluez_sender=':1.5')


def test_aa_lease_permits_only_bonded_detector_io_without_radio_mutation(setup):
  owner, client, _ = setup
  owner.phone_lease = lease()
  owner._admission_enter = Mock(side_effect=AssertionError('must reuse lease'))
  assert exchange(owner, ADDRESS, 'R8', ('live',))['connected']
  assert all(c.args[2] == 'ReadValue' for c in client._call.call_args_list)
  client.operation.assert_not_called()


@pytest.mark.parametrize('change', ['pairing', 'released', 'receiver', 'adapter', 'revoked_during_read'])
def test_aa_lease_changes_reject(setup, change):
  owner, client, _ = setup
  owner.phone_lease = lease()
  if change == 'pairing':
    owner.phone_lease.session = ('live',)
  if change == 'released':
    owner.phone_lease.released = True
  if change == 'receiver':
    owner.phone_lease.receiver = ADDRESS
  if change == 'adapter':
    owner.phone_lease.bluez_sender = ':1.6'
  if change == 'revoked_during_read':
    def read(*args, **kwargs):
      owner.phone_lease.released = True
      return (PACKET,)
    client._call.side_effect = read
  with pytest.raises(BluetoothRejected):
    exchange(owner, ADDRESS, 'R8', ('live',), mute={'observed_at': time.monotonic(), 'alerts': alert_key(parse_alerts(PACKET))})
  assert not any(c.args[2] == 'WriteValue' for c in client._call.call_args_list)


@pytest.mark.parametrize('age,key', [(4, None), (-1, None), (0, [] )])
def test_mute_expiry_and_changed_alert(setup, age, key):
  owner, client, _ = setup
  with pytest.raises(BluetoothRejected):
    exchange(owner, ADDRESS, 'R8', ('live',), mute={'observed_at': time.monotonic() - age,
             'alerts': alert_key(parse_alerts(PACKET)) if key is None else key})
  assert not any(c.args[2] == 'WriteValue' for c in client._call.call_args_list)


def test_reconnect_bounded_and_never_replays_mute(setup):
  owner, client, objects = setup
  objects[PATH][DEVICE]['Connected'] = False
  for _ in range(2):
    with pytest.raises(BluetoothRejected):
      exchange(owner, ADDRESS, 'R8', ('live',))
  assert sum(c.args[2] == 'Connect' for c in client._call.call_args_list) == 1
  with pytest.raises(BluetoothRejected):
    exchange(owner, ADDRESS, 'R8', ('live',), mute={})
  assert sum(c.args[2] == 'Connect' for c in client._call.call_args_list) == 1


def test_service_defaults_configure_authority_and_selected_device():
  params = Mock()
  params.get.return_value = None
  service = DetectorService(params, lambda: False)
  transport = Mock()
  assert service.request({'operation': 'status'}, transport)['config'] == normalize_config(DEFAULT_CONFIG)
  transport.assert_not_called()
  with pytest.raises(BluetoothRejected):
    service.request({'operation': 'configure', 'config': CONFIG}, transport)
  params.put.assert_not_called()
  params.get.return_value = CONFIG
  with pytest.raises(BluetoothRejected):
    service.request({'operation': 'mute', 'address': 'old', 'alerts': [], 'observed_at': 0}, transport)
  transport.side_effect = BluetoothRejected('Pairing busy')
  result = service.request({'operation': 'status'}, transport)
  assert result['config'] == normalize_config({**DEFAULT_CONFIG, **CONFIG}) and not result['connected'] and result['alerts'] == []


def test_feed_stale_and_hidden_and_failed_mute_not_replayed():
  now = [10.]
  state = {'config': CONFIG, 'connected': True, 'address': ADDRESS, 'observed_at': 10., 'alerts': parse_alerts(PACKET), 'can_mute': True}
  call = Mock(return_value=(200, state))
  feed = DetectorFeed(call, clock=lambda: now[0])
  feed.enabled, feed.requested_at = True, 10.
  feed.poll()
  feed.mute(feed.snapshot())
  call.return_value = (503, {'error': 'Lost connection'})
  feed.poll()
  assert call.call_args.args[2]['operation'] == 'mute' and feed.snapshot()['alerts'], 'one failed read does not blink the alerts'
  now[0] = 10.8
  feed.poll()
  assert call.call_args.args[2]['operation'] == 'status' and feed.snapshot()['alerts'] == [], 'a lasting failure clears them'
  now[0] = 10
  feed.state = state
  now[0] = 14
  assert not feed.snapshot()['connected']
  call.reset_mock()
  feed.poll()
  call.assert_not_called()


def test_daemon_requires_proof_but_mute_does_not_require_park():
  from unittest.mock import patch
  from openpilot.starpilot.system.starport import daemon
  host = Mock()
  host.uniden_exchange.return_value = {'connected': True}
  verifier = Mock()
  verifier.valid.return_value = True
  request = {'command': 'uniden', 'source': 'proof', 'address': ADDRESS, 'model': 'R8', 'mute': None}
  with patch('openpilot.common.params.Params') as params:
    params.return_value.get_bool.return_value = True
    assert daemon.handle(host, request, verifier, parked=lambda: False)['handled']
    host.bind_source_session.assert_not_called()
    verifier.valid.return_value = False
    with pytest.raises(RuntimeError):
      daemon.handle(host, request, verifier)
    assert host.uniden_exchange.call_count == 1


def test_expired_session_cannot_save_detector_configuration():
  params = Mock()
  service = DetectorService(params, lambda: True)
  with pytest.raises(BluetoothRejected):
    service.request({'operation': 'configure', 'config': CONFIG}, Mock(), valid=lambda: False)
  params.put.assert_not_called()


def test_older_detector_configuration_gains_defaults_and_drops_display_settings():
  from openpilot.starpilot.uniden.protocol import normalize_config
  old = {'enabled': True, 'address': ADDRESS, 'model': 'R8'}
  assert normalize_config(old) == normalize_config({**DEFAULT_CONFIG, **CONFIG})
  for value in (100, -1, True, 'false'):
    config = normalize_config({**CONFIG, 'transparency': value, 'hide_when_no_alert': value})
    assert 'transparency' not in config and 'hide_when_no_alert' not in config, 'display settings live in the layout'


RULE = {'enabled': True, 'label': 'Head unit', 'band': 'KA', 'frequency': 34.313, 'tolerance_mhz': 0}


def test_auto_mute_in_first_exchange_and_bounded_retry(setup):
  owner, client, _ = setup
  client._call.return_value = (b'1,00,KA,3,0,34.313,F,1',)
  result = exchange(owner, ADDRESS, 'R8', ('live',), auto_mute=True, frequency_rules=[RULE])
  assert result['alerts'][0]['filtered'] and not result['alerts'][0]['muted']
  assert result['command'] == 'sent_unconfirmed'
  assert [c.args[2] for c in client._call.call_args_list] == ['ReadValue', 'WriteValue']
  exchange(owner, ADDRESS, 'R8', ('live',), auto_mute=True, frequency_rules=[RULE])
  assert sum(c.args[2] == 'WriteValue' for c in client._call.call_args_list) == 1
  owner.uniden.auto_mute = (owner.uniden.auto_mute[0], time.monotonic() - 1)
  exchange(owner, ADDRESS, 'R8', ('live',), auto_mute=True, frequency_rules=[RULE])
  assert sum(c.args[2] == 'WriteValue' for c in client._call.call_args_list) == 2


@pytest.mark.parametrize('packet', [b'0', b'1,00,KA,3,0,34.314,F,1', b'1,00,KA,3,0,34.313,F,2',
  b'1,00,KA,3,0,34.313,F,1&1,00,KA,3,0,34.7,R,1',
  b'1,00,KA,3,0,34.313,F,1&1,00,LASER,8,0,5,F,1'])
def test_auto_mute_preserves_unmatched_or_already_muted_signals(setup, packet):
  owner, client, _ = setup
  client._call.return_value = (packet,)
  exchange(owner, ADDRESS, 'R8', ('live',), auto_mute=True, frequency_rules=[RULE])
  assert not any(c.args[2] == 'WriteValue' for c in client._call.call_args_list)


def test_matching_does_not_require_auto_mute_and_absent_write_supported(setup):
  owner, client, objects = setup
  client._call.return_value = (b'1,00,KA,3,0,34.313,F,1',)
  result = exchange(owner, ADDRESS, 'R8', ('live',), frequency_rules=[RULE])
  assert result['alerts'][0]['filtered'] and not result['command']
  del objects[PATH + '/service/write']
  result = exchange(owner, ADDRESS, 'R8', ('live',), auto_mute=True, frequency_rules=[RULE])
  assert not result['can_mute'] and not result['command']
  assert not any(c.args[2] == 'WriteValue' for c in client._call.call_args_list)


def test_rule_tolerance_band_and_enabled():
  from openpilot.starpilot.uniden.protocol import matches_rule
  rule = {**RULE, 'tolerance_mhz': 2}
  assert matches_rule({'band': 'KA', 'frequency': 34.315}, [rule])
  assert not matches_rule({'band': 'KA', 'frequency': 34.316}, [rule])
  assert not matches_rule({'band': 'K', 'frequency': 34.313}, [rule])
  assert not matches_rule({'band': 'KA', 'frequency': 34.313}, [{**rule, 'enabled': False}])


@pytest.mark.parametrize('change', [{'frequency': float('nan')}, {'frequency': True}, {'frequency': 24.15},
  {'tolerance_mhz': -1}, {'tolerance_mhz': 11}, {'tolerance_mhz': float('inf')}, {'band': 'LASER'},
  {'band': []}, {'enabled': 1}, {'label': 'x' * 61}])
def test_invalid_frequency_rule_rejected(change):
  from openpilot.starpilot.uniden.protocol import normalize_config
  with pytest.raises(ValueError):
    normalize_config({**CONFIG, 'frequency_rules': [{**RULE, **change}]})


def test_legacy_config_gains_independent_rule_lists():
  from openpilot.starpilot.uniden.protocol import normalize_config
  legacy = {key: value for key, value in CONFIG.items() if key not in ('auto_mute', 'hide_filtered', 'frequency_rules')}
  first, second = normalize_config(legacy), normalize_config(legacy)
  first['frequency_rules'].append(RULE)
  assert second['frequency_rules'] == [] and not second['auto_mute']


def test_failed_connect_and_retry_delay_have_same_status(setup):
  owner, client, objects = setup
  objects[PATH][DEVICE]['Connected'] = False
  client._call.side_effect = BluetoothRejected('org.bluez.Error.Failed')
  params = Mock()
  params.get.return_value = CONFIG
  service = DetectorService(params, lambda: True)
  results = [service.request({'operation': 'status'}, lambda config, mute, situation=None: exchange(owner, ADDRESS, 'R8', ('live',))) for _ in range(2)]
  assert all(result['connection_state'] == 'reconnecting' for result in results)
  assert results[0]['error'] == results[1]['error'] == 'Reconnecting to detector'
  assert not any(result['connected'] or result['alerts'] for result in results)
  assert sum(call.args[2] == 'Connect' for call in client._call.call_args_list) == 1


@pytest.mark.parametrize('error,expected', [
  (BluetoothRejected('org.bluez.Error.Failed'), 'reconnecting'),
  (BluetoothRejected('org.bluez.Error.NotAuthorized'), 'detector_pairing_required'),
  (BluetoothRejected('off', code='radio_off'), 'radio_off'),
  (BluetoothRejected('pairing', code='busy'), 'busy'),
  (ValueError('Unsupported alert packet'), 'unavailable'),
])
def test_connection_errors_are_friendly_and_keep_alerts_unavailable(error, expected):
  params = Mock()
  params.get.return_value = CONFIG
  result = DetectorService(params, lambda: True).request({'operation': 'status'}, Mock(side_effect=error))
  assert result['connection_state'] == expected
  assert 'org.bluez' not in result['error']
  assert not result['connected'] and result['alerts'] == []
