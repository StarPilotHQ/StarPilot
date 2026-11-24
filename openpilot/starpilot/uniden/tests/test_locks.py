import json
import threading
import time
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.bluetooth.owner import ADAPTER, DEVICE, BluetoothRejected
from openpilot.starpilot.system.starport.bluetooth_bridge import SharedBluetoothOwner
from openpilot.starpilot.uniden.feed import DetectorFeed, OFFLINE_S
from openpilot.starpilot.uniden.locks import LEARN_COUNT, REPEAT_S, LockStore, matches_lock, new_lock, normalize_lock
from openpilot.starpilot.uniden.policy import apply
from openpilot.starpilot.uniden.protocol import ALERT_UUID, DEFAULT_CONFIG, GATT, WRITE_UUID, alert_key, normalize_config, parse_alerts
from openpilot.starpilot.uniden.service import DetectorService
from openpilot.starpilot.uniden.tracker import PassTracker
from openpilot.starpilot.uniden.transport import exchange

ADDRESS = 'AA:BB:CC:DD:EE:FF'
ADAPTER_PATH = '/org/bluez/hci0'
PATH = ADAPTER_PATH + '/dev_AA_BB_CC_DD_EE_FF'
CONFIG = {**DEFAULT_CONFIG, 'enabled': True, 'address': ADDRESS}


def context(**fields):
  return {'lat': 36.0, 'lon': -115.0, 'heading': 90.0, 'speed': 20.0, 'limit': 25.0, 'at': time.monotonic(), **fields}


def alerts(packet, model='R8'):
  return parse_alerts(packet, model)


def event(**fields):
  return {'band': 'K', 'frequency': 24.15, 'lat': 36.0, 'lon': -115.0, 'strength': 4, 'at': 1_000_000, **fields}


@pytest.fixture
def setup(tmp_path):
  objects = {ADAPTER_PATH: {ADAPTER: {'Powered': True}}, PATH: {DEVICE: {
    'Name': 'R8W@12', 'Address': ADDRESS, 'Paired': True, 'Connected': True, 'ServicesResolved': True}},
    PATH + '/service/alert': {GATT: {'UUID': ALERT_UUID}}, PATH + '/service/write': {GATT: {'UUID': WRITE_UUID}}}
  client = Mock()
  client._objects.side_effect = lambda: objects
  client.adapter_identity.side_effect = lambda: (ADAPTER_PATH, objects[ADAPTER_PATH][ADAPTER], ':1.5')
  owner = SharedBluetoothOwner(lambda: False, session_valid=lambda s: s == ('live',),
                               bluez_factory=lambda: client, lock_path=tmp_path / 'owner.lock')
  return owner, client


def writes(client):
  return sum(call.args[2] == 'WriteValue' for call in client._call.call_args_list)


def test_lock_matches_only_near_the_place_and_signal():
  lock = new_lock('tap', event(), 1)
  alert = alerts(b'1,00,K,3,0,24.150,F,1')[0]
  assert matches_lock(alert, lock, context())
  assert not matches_lock(alert, lock, None)
  assert not matches_lock(alert, lock, context(lat=36.01))
  assert not matches_lock({**alert, 'frequency': 24.2}, lock, context())
  assert not matches_lock({**alert, 'strength': 8}, {**lock, 'max_strength': 4}, context())
  assert matches_lock({**alert, 'strength': 6}, {**lock, 'max_strength': 4}, context())
  assert not matches_lock(alert, {**lock, 'enabled': False}, context())


def test_lock_heading_window():
  lock = new_lock('tap', event(), 1, heading=90, heading_tolerance=45)
  alert = alerts(b'1,00,K,3,0,24.150,F,1')[0]
  assert matches_lock(alert, lock, context(heading=120))
  assert not matches_lock(alert, lock, context(heading=270))
  assert not matches_lock(alert, lock, context(heading=None))


def test_laser_lock_has_no_frequency_and_matches_by_place():
  lock = new_lock('tap', event(band='LASER', frequency=None), 1)
  laser = alerts(b'1,00,LASER,8,0,5,F,1')[0]
  assert matches_lock(laser, lock, context())
  assert not matches_lock(laser, lock, context(lon=-115.02))
  with pytest.raises(ValueError):
    normalize_lock({**lock, 'frequency': 24.15})


@pytest.mark.parametrize('change', [{'lat': 91}, {'radius_m': 10}, {'band': 'POP'}, {'source': 'x'}, {'frequency': 99.}, {'count': 0}])
def test_invalid_locks_rejected(change):
  with pytest.raises(ValueError):
    normalize_lock({**new_lock('tap', event(), 1), **change})


def test_learning_needs_separate_passes_and_stays_off_without_opt_in(tmp_path):
  store = LockStore(tmp_path)
  at = 1_000_000
  for n in range(LEARN_COUNT['K']):
    earned = store.observe(event(at=at + n * REPEAT_S), learn=False, bands=['K'])
  assert earned is None and not store.locks
  store = LockStore(tmp_path / 'on')
  assert store.observe(event(at=at), learn=True, bands=['K']) is None
  assert store.observe(event(at=at + 5), learn=True, bands=['K']) is None
  assert store.candidates[0]['count'] == 1
  assert store.observe(event(at=at + REPEAT_S), learn=True, bands=['K']) is None
  lock = store.observe(event(at=at + 2 * REPEAT_S, strength=6), learn=True, bands=['K'])
  assert lock['source'] == 'auto' and lock['band'] == 'K' and lock['max_strength'] == 6 and not store.candidates
  assert LockStore(tmp_path / 'on').locks == [lock]


def test_learning_respects_band_choice_and_laser_needs_more_passes(tmp_path):
  store = LockStore(tmp_path)
  for n in range(LEARN_COUNT['KA']):
    assert store.observe(event(band='KA', frequency=34.7, at=1_000_000 + n * REPEAT_S), learn=True, bands=['K']) is None
  assert not store.locks and LEARN_COUNT['LASER'] > LEARN_COUNT['K']


def test_distant_or_different_signals_are_not_merged(tmp_path):
  store = LockStore(tmp_path)
  store.observe(event(), learn=True, bands=['K'])
  store.observe(event(at=1_000_000 + REPEAT_S, lat=36.05), learn=True, bands=['K'])
  store.observe(event(at=1_000_000 + REPEAT_S, frequency=24.2), learn=True, bands=['K'])
  assert len(store.candidates) == 3


def test_store_survives_a_corrupt_file_and_unlock(tmp_path):
  (tmp_path / 'locks.json').write_text('{broken')
  store = LockStore(tmp_path)
  assert store.locks == []
  lock = new_lock('tap', event(), 1)
  store.add(lock)
  assert LockStore(tmp_path).locks == [lock]
  store.remove(lock['id'])
  assert LockStore(tmp_path).locks == []
  with pytest.raises(ValueError):
    store.remove('missing')


def run_pass(tracker, directions, model='R8', start=0.):
  found = []
  for n, direction in enumerate(directions):
    now = start + n * .5
    packet = f'1,00,K,{3 + n % 3},0,24.150,{direction},1'.encode()
    found += tracker.update(alerts(packet, model), context(at=now, lon=-115 + n * .001), now, 1000 + now)
  return found


def test_arrow_flip_pinpoints_a_fixed_source_once():
  found = run_pass(PassTracker(), ['F', 'F', 'F', 'S', 'S', 'R', 'R'])
  assert len(found) == 1 and found[0]['band'] == 'K' and found[0]['lon'] == pytest.approx(-115 + .003)


def test_moving_with_you_or_no_gps_never_pinpoints():
  assert run_pass(PassTracker(), ['R', 'R', 'R', 'R']) == []
  tracker = PassTracker()
  found = [e for n in range(4) for e in tracker.update(alerts(f'1,00,K,3,0,24.150,{"FSRR"[n]},1'.encode()), None, n * .5, 1000)]
  assert found == []
  assert PassTracker().update(alerts(b'1,00,K,3,0,24.150,S,1'), context(speed=1., at=0.), 0., 1000) == []


def test_r4_strength_peak_marks_the_pass_when_the_alert_ends():
  tracker, found = PassTracker(), []
  for n, strength in enumerate([1, 3, 6, 4, 2]):
    found += tracker.update(alerts(f'1,00,K,{strength},0,24.150,F,1'.encode(), 'R4'), context(at=n * .5, lon=-115 + n * .001), n * .5, 1000)
  assert found == []
  found = tracker.update([], context(at=5.), 5., 1000)
  assert len(found) == 1 and found[0]['strength'] == 6 and found[0]['lon'] == pytest.approx(-115 + .002)


def situation(**fields):
  return {'context': context(), 'locks': [], 'quiet': {'bands': ['X', 'K'], 'margin': 0}, **fields}


def test_quiet_only_at_or_under_the_limit_for_chosen_bands():
  now = time.monotonic()
  def run(sit, packet=b'1,00,K,3,0,24.150,F,1', previous=None):
    items = alerts(packet)
    apply(items, [], sit, previous, now)
    return items[0]
  assert run(situation(context=context(at=now)))['quiet']
  assert not run(situation(context=context(at=now, speed=26.)))['quiet']
  assert run(situation(context=context(at=now, speed=26.), quiet={'bands': ['K'], 'margin': 2}))['quiet']
  assert not run(situation(context=context(at=now, limit=None)))['quiet']
  assert not run(situation(context=context(at=now - 2)))['quiet']
  assert not run(situation(context=None))['quiet']
  assert not run(situation(context=context(at=now), quiet=None))['quiet']
  assert not run(situation(context=context(at=now)), b'1,00,KA,3,0,34.7,F,1')['quiet']
  assert run(situation(context=context(at=now), quiet={'bands': ['LASER'], 'margin': 0}), b'1,00,LASER,8,0,5,F,1')['quiet']


def test_sudden_strong_signal_is_never_quiet_but_a_steady_one_is():
  now = time.monotonic()
  first = alerts(b'1,00,K,2,0,24.150,F,1')
  state = apply(first, [], situation(context=context(at=now)), None, now)
  sudden = alerts(b'1,00,K,2,0,24.150,F,1&1,00,KA,6,0,34.7,F,1')
  state = apply(sudden, [], situation(context=context(at=now + .1), quiet={'bands': ['K', 'KA'], 'margin': 0}), state, now + .1)
  ka = next(a for a in sudden if a['band'] == 'KA')
  assert ka['instant_on'] and not ka['quiet']
  assert not next(a for a in sudden if a['band'] == 'K')['instant_on']
  later = alerts(b'1,00,KA,7,0,34.7,F,1')
  apply(later, [], situation(context=context(at=now + .2)), state, now + .2)
  assert later[0]['instant_on']


def test_stale_previous_read_does_not_call_a_signal_sudden():
  now = time.monotonic()
  items = alerts(b'1,00,KA,7,0,34.7,F,1')
  apply(items, [], situation(), (now - 5, {}), now)
  assert not items[0]['instant_on']


def test_exchange_sends_mute_when_quiet_and_not_when_one_alert_is_loud(setup):
  owner, client = setup
  client._call.return_value = (b'1,00,K,3,0,24.150,F,1',)
  result = exchange(owner, ADDRESS, 'R8', ('live',), situation=situation())
  assert result['alerts'][0]['quiet'] and writes(client) == 1
  owner.uniden.auto_mute = (None, 0.)
  client._call.return_value = (b'1,00,K,3,0,24.150,F,1&1,00,KA,3,0,34.7,F,1',)
  exchange(owner, ADDRESS, 'R8', ('live',), situation=situation())
  assert writes(client) == 1
  client._call.return_value = (b'1,00,K,3,0,24.150,F,1',)
  exchange(owner, ADDRESS, 'R8', ('live',), situation=situation(context=context(speed=40.)))
  assert writes(client) == 1


def test_exchange_lock_filters_and_mutes_only_with_auto_mute(setup):
  owner, client = setup
  client._call.return_value = (b'1,00,K,3,0,24.150,F,1',)
  sit = situation(quiet=None, locks=[new_lock('tap', event(), 1)])
  result = exchange(owner, ADDRESS, 'R8', ('live',), situation=sit)
  assert result['alerts'][0]['filtered'] and writes(client) == 0
  exchange(owner, ADDRESS, 'R8', ('live',), auto_mute=True, situation=sit)
  assert writes(client) == 1


def make_service(tmp_path, config=CONFIG, now=None):
  params = Mock()
  params.get.return_value = config
  params.get_bool.return_value = False
  clock = [100.]
  return DetectorService(params, lambda: False, LockStore(tmp_path), clock=lambda: clock[0], wall=lambda: 5_000_000.), params, clock


def reading(packet, at):
  items = alerts(packet)
  return {'connected': True, 'address': ADDRESS, 'model': 'R8', 'alerts': items, 'observed_at': at, 'can_mute': True, 'command': ''}


def test_service_builds_situation_from_config_and_converts_margin(tmp_path):
  service, params, clock = make_service(tmp_path, {**CONFIG, 'quiet_ride': True, 'quiet_margin': 5})
  seen = {}
  def transport(config, mute, sit):
    seen.update(sit)
    return reading(b'1,00,K,3,0,24.150,F,1', 100.)
  service.request({'operation': 'status', 'context': context(at=100.)}, transport)
  assert seen['quiet']['bands'] == ['X', 'K'] and seen['quiet']['margin'] == pytest.approx(5 * .44704)
  params.get_bool.return_value = True
  service.request({'operation': 'status'}, transport)
  assert seen['quiet']['margin'] == pytest.approx(5 / 3.6)


@pytest.mark.parametrize('bad', [{'lat': 1}, {**context(), 'lat': 100}, {**context(), 'speed': float('nan')}, {**context(), 'extra': 1}, 'x'])
def test_service_rejects_bad_context(tmp_path, bad):
  service, _, _ = make_service(tmp_path)
  with pytest.raises(ValueError):
    service.request({'operation': 'status', 'context': bad}, Mock())


def test_service_rejects_context_on_other_operations_and_missing_fields(tmp_path):
  service, _, _ = make_service(tmp_path)
  for payload in ({'operation': 'locks', 'context': context()}, {'operation': 'unlock'}, {'operation': 'lock'},
                  {'operation': 'unlock', 'id': 'x', 'extra': 1}):
    with pytest.raises(ValueError):
      service.request(payload, Mock())


def test_hold_to_lock_uses_the_pinpointed_source_and_unlock_removes_it(tmp_path):
  service, _, clock = make_service(tmp_path)
  steps = [(b'1,00,K,3,0,24.150,F,1', 0), (b'1,00,K,4,0,24.150,F,1', .5), (b'1,00,K,5,0,24.150,S,1', 1.)]
  for n, (packet, offset) in enumerate(steps):
    clock[0] = 100. + offset
    service.request({'operation': 'status', 'context': context(at=clock[0], lon=-115 + n * .001)},
                    lambda config, mute, sit, p=packet, o=clock[0]: reading(p, o))
  current = service.last['alerts']
  payload = {'operation': 'lock', 'address': ADDRESS, 'observed_at': 101., 'alerts': alert_key(current)}
  result = service.request(payload, Mock())
  assert result['locked'] == 1 and result['locks'][0]['lon'] == pytest.approx(-115 + .002) and result['locks'][0]['source'] == 'tap'
  with pytest.raises(BluetoothRejected, match='Already locked'):
    service.request(payload, Mock())
  assert service.request({'operation': 'locks'}, Mock())['locks'][0]['band'] == 'K'
  assert service.request({'operation': 'unlock', 'id': result['locks'][0]['id']}, Mock())['locks'] == []


def test_listing_shows_learning_progress_closest_first(tmp_path):
  service, _, _ = make_service(tmp_path)
  store = service.store
  store.candidates = [
    {'id': 'a', 'band': 'KA', 'frequency': 34.7, 'lat': 36., 'lon': -115., 'count': 2, 'misses': 0, 'last': 1., 'max_strength': 3},
    {'id': 'b', 'band': 'K', 'frequency': 24.15, 'lat': 36., 'lon': -115., 'count': 2, 'misses': 1, 'last': 1., 'max_strength': 3},
    {'id': 'c', 'band': 'X', 'frequency': 10.525, 'lat': 36., 'lon': -115., 'count': 2, 'misses': 0, 'last': 1., 'max_strength': 3},
  ]
  listing = service.request({'operation': 'locks'}, Mock())
  assert listing['candidates'] == 3
  assert [(c['id'], c['count'], c['need']) for c in listing['learning']] == [('c', 2, 3), ('b', 2, 3), ('a', 2, 5)]
  assert all('lat' not in c and 'lon' not in c for c in listing['learning']), 'only progress, no position'


def test_lock_is_rejected_for_a_changed_alert_stale_read_or_no_fix(tmp_path):
  service, _, clock = make_service(tmp_path)
  service.request({'operation': 'status', 'context': context(at=100.)}, lambda config, mute, sit: reading(b'1,00,K,3,0,24.150,F,1', 100.))
  base = {'operation': 'lock', 'address': ADDRESS, 'observed_at': 100., 'alerts': alert_key(service.last['alerts'])}
  for change in ({'alerts': []}, {'address': 'OTHER'}, {'observed_at': 90.}):
    with pytest.raises(BluetoothRejected):
      service.request({**base, **change}, Mock())
  clock[0] = 102.
  service.request({'operation': 'status'}, lambda config, mute, sit: reading(b'1,00,K,3,0,24.150,F,1', 102.))
  with pytest.raises(BluetoothRejected, match='GPS'):
    service.request(base, Mock())


def test_service_learns_from_passes_only_when_enabled(tmp_path):
  def drive(config):
    service, _, clock = make_service(tmp_path / str(config['auto_learn']), config)
    for trip in range(LEARN_COUNT['K']):
      for n, d in enumerate('FFSR'):
        clock[0] = 100. + n * .5
        service.wall = lambda trip=trip: 5_000_000. + trip * REPEAT_S
        service.request({'operation': 'status', 'context': context(at=clock[0], lon=-115 + n * .001)},
                        lambda config, mute, sit, d=d, o=clock[0]: reading(f'1,00,K,4,0,24.150,{d},1'.encode(), o))
      clock[0] += 5
      service.request({'operation': 'status'}, lambda config, mute, sit, o=clock[0]: reading(b'0', o))
    return service
  assert drive({**CONFIG, 'auto_learn': False}).store.locks == []
  locks = drive({**CONFIG, 'auto_learn': True}).store.locks
  assert len(locks) == 1 and locks[0]['source'] == 'auto'


def test_config_defaults_and_validation():
  assert normalize_config({'enabled': False, 'address': '', 'model': 'R8'}) == normalize_config(DEFAULT_CONFIG)
  assert normalize_config({**CONFIG, 'quiet_bands': ['LASER', 'X']})['quiet_bands'] == ['X', 'LASER']
  for change in ({'quiet_margin': 16}, {'quiet_margin': True}, {'quiet_bands': ['POP']}, {'border_bands': ['X']},
                 {'border_colors': {'KA': 'cyan', 'K': 'violet', 'LASER': 'white'}}, {'border_colors': {'KA': 'purple'}},
                 {'quiet_ride': 1}, {'auto_learn': 'yes'}):
    with pytest.raises(ValueError):
      normalize_config({**CONFIG, **change})


def test_a_saved_config_from_an_older_version_still_loads():
  old = {**CONFIG, 'transparency': 40, 'hide_when_no_alert': False, 'unmute_on_breakthrough': True,
         'border_styles': {'KA': 'chase', 'K': 'solid', 'LASER': 'pulse'}, 'border_colors': {'KA': 'purple', 'K': 'yellow', 'LASER': 'violet'}}
  config = normalize_config(old)
  assert config == normalize_config({**CONFIG, 'border_colors': {'KA': 'ultraviolet', 'K': 'lime', 'LASER': 'ultraviolet'}})
  assert config['address'] == ADDRESS and config['enabled']


def test_feed_flags_offline_after_the_grace_period_and_clears_on_reconnect():
  now = [0.]
  state = {'config': CONFIG, 'connected': False, 'alerts': [], 'error': 'Reconnecting'}
  call = Mock(return_value=(200, state))
  feed = DetectorFeed(call, clock=lambda: now[0], context=lambda: None)
  feed.set_enabled(True)
  feed.poll()
  assert not feed.snapshot().get('offline')
  now[0] = OFFLINE_S + 1
  feed.requested_at = now[0]
  assert feed.snapshot()['offline']
  call.return_value = (200, {**state, 'connected': True, 'observed_at': now[0]})
  feed.poll()
  assert not feed.snapshot().get('offline')
  feed.set_enabled(False)


def test_feed_sends_fresh_context_and_a_lock_request_with_a_notice():
  now = [10.]
  state = {'config': CONFIG, 'connected': True, 'address': ADDRESS, 'observed_at': 10., 'alerts': alerts(b'1,00,K,3,0,24.150,F,1'), 'can_mute': True}
  call = Mock(return_value=(200, state))
  feed = DetectorFeed(call, clock=lambda: now[0], context=lambda: context())
  feed.enabled, feed.requested_at = True, 10.
  feed.poll()
  assert 'context' in call.call_args.args[2]
  feed.lock_here(feed.snapshot())
  call.return_value = (200, {'locked': 1, 'locks': []})
  feed.poll()
  assert call.call_args.args[2]['operation'] == 'lock' and 'context' not in call.call_args.args[2]
  assert feed.snapshot()['notice'].startswith('Locked out here') and feed.snapshot()['alerts']
  now[0] = 14
  feed.state['observed_at'] = 14.
  assert 'notice' not in feed.snapshot()
  feed.lock_here(feed.snapshot())
  call.return_value = (409, {'error': 'Already locked out here'})
  now[0] = 14.1
  feed.requested_at = 14.1
  feed.poll()
  assert feed.snapshot()['notice'] == 'Already locked out here'


def test_only_nearby_locks_are_sent_and_none_without_a_fresh_fix(tmp_path):
  service, _, clock = make_service(tmp_path)
  service.store.locks = [new_lock('tap', event(lat=36.0 + n * .01), 1) for n in range(200)]
  near = service.situation(CONFIG, context(at=100.))['locks']
  assert 0 < len(near) < 40 and all(abs(lock['lat'] - 36.0) < .03 for lock in near)
  assert service.situation(CONFIG, None)['locks'] == []
  assert service.situation(CONFIG, context(at=90.))['locks'] == []


def test_a_poll_without_location_reuses_the_last_fresh_fix_but_not_a_stale_one(tmp_path):
  service, _, clock = make_service(tmp_path)
  seen = []
  def transport(config, mute, sit):
    seen.append(sit['context'])
    return reading(b'1,00,K,3,0,24.150,F,1', clock[0])
  service.request({'operation': 'status', 'context': context(at=100.)}, transport)
  clock[0] = 100.4
  service.request({'operation': 'status'}, transport)
  assert seen[1] == seen[0] and service.last['context'] is not None
  base = {'operation': 'lock', 'address': ADDRESS, 'observed_at': 100.4, 'alerts': alert_key(service.last['alerts'])}
  assert service.request(base, Mock())['locked'] == 1
  clock[0] = 102.
  service.request({'operation': 'status'}, transport)
  assert seen[2] is None and service.last['context'] is None


def test_a_tap_lock_held_on_approach_moves_to_the_pass_point(tmp_path):
  service, _, clock = make_service(tmp_path)
  def poll(n, direction):
    clock[0] = 100. + n * .5
    service.request({'operation': 'status', 'context': context(at=clock[0], lon=-115 + n * .001)},
                    lambda config, mute, sit: reading(f'1,00,K,4,0,24.150,{direction},1'.encode(), clock[0]))
  poll(0, 'F')
  service.request({'operation': 'lock', 'address': ADDRESS, 'observed_at': 100., 'alerts': alert_key(service.last['alerts'])}, Mock())
  assert service.store.locks[0]['lon'] == pytest.approx(-115)
  poll(1, 'F')
  poll(2, 'S')
  assert service.store.locks[0]['lon'] == pytest.approx(-115 + .002)


def test_a_sudden_strong_signal_is_never_pinpointed():
  tracker, found = PassTracker(), []
  for n, direction in enumerate('FFSR'):
    items = alerts(f'1,00,K,7,0,24.150,{direction},1'.encode())
    items[0]['instant_on'] = n == 0
    found += tracker.update(items, context(at=n * .5, lon=-115 + n * .001), n * .5, 1000)
  assert found == []


def drive_by(store, alerts_seen, lon=-115.0):
  store.visit(alerts_seen, context(lon=lon))
  store.visit(alerts_seen, context(lon=lon + .05))


def leave(store):
  store.visit([], context(lon=-115.05))


def test_drive_bys_without_the_signal_block_learning_and_remove_learned_lockouts(tmp_path):
  store = LockStore(tmp_path)
  at = 1_000_000
  store.observe(event(at=at), learn=True, bands=['K'])
  store.observe(event(at=at + REPEAT_S), learn=True, bands=['K'])
  leave(store)
  for _ in range(2):
    drive_by(store, [])
  assert store.candidates[0]['misses'] == 2
  store.observe(event(at=at + 2 * REPEAT_S), learn=True, bands=['K'])
  assert store.candidates and not store.locks
  store.observe(event(at=at + 3 * REPEAT_S), learn=True, bands=['K'])
  lock = store.locks[0]
  assert lock['source'] == 'auto' and lock['misses'] == 0
  leave(store)
  for _ in range(lock['count']):
    drive_by(store, [])
  assert store.locks
  drive_by(store, [])
  assert not store.locks


def test_the_pass_that_created_a_candidate_is_not_counted_as_a_miss(tmp_path):
  store = LockStore(tmp_path)
  store.observe(event(at=1_000_000), learn=True, bands=['K'])
  drive_by(store, [])
  assert store.candidates[0]['misses'] == 0


def test_tap_lockouts_and_slow_or_unlocated_cars_never_collect_misses(tmp_path):
  store = LockStore(tmp_path)
  store.add(new_lock('tap', event(), 1))
  drive_by(store, [])
  store.observe(event(lat=36.5, at=1_000_000), learn=True, bands=['K'])
  store.visit([], context(lat=36.5, speed=1.))
  store.visit([], context(lat=36.6, speed=1.))
  store.visit([], None)
  assert store.locks[0]['misses'] == 0 and store.candidates[0]['misses'] == 0


TIMED = {'bands': ['X', 'K', 'KA'], 'seconds': 5, 'strength': 6}


def timed_run(packet, previous, now, timed=TIMED):
  items = alerts(packet)
  return items, apply(items, [], {'context': None, 'locks': [], 'quiet': None, 'timed': timed}, previous, now)


def test_timed_mute_waits_for_the_delay_and_skips_unchosen_bands_and_laser():
  items, state = timed_run(b'1,00,K,3,0,24.150,F,1', None, 100.)
  assert not items[0]['timed']
  items, state = timed_run(b'1,00,K,3,0,24.150,F,1', state, 100.5)
  assert not items[0]['timed']
  items, state = timed_run(b'1,00,K,3,0,24.150,F,1', (104.9, {k: {**v, 'since': 100.} for k, v in state[1].items()}), 105.)
  assert items[0]['timed'] and not items[0]['breakthrough']
  for packet, timed in ((b'1,00,LASER,3,0,5,F,1', TIMED), (b'1,00,KA,3,0,34.7,F,1', {**TIMED, 'bands': ['K']})):
    _, state = timed_run(packet, None, 100., timed)
    items, _ = timed_run(packet, (104.9, {k: {**v, 'since': 90.} for k, v in state[1].items()}), 105., timed)
    assert not items[0]['timed']
  items, _ = timed_run(b'1,00,K,3,0,24.150,F,1', None, 100., timed=None)
  assert not items[0]['timed'] and not items[0]['breakthrough']


def test_a_strong_signal_stays_loud_until_it_clears_and_a_sudden_one_is_never_timed():
  _, state = timed_run(b'1,00,K,3,0,24.150,F,1', None, 100.)
  state = (104.9, {k: {**v, 'since': 90.} for k, v in state[1].items()})
  items, state = timed_run(b'1,00,K,7,0,24.150,F,1', state, 105.)
  assert items[0]['breakthrough'] and not items[0]['timed']
  items, state = timed_run(b'1,00,K,3,0,24.150,F,1', state, 105.5)
  assert items[0]['breakthrough'] and not items[0]['timed']
  _, state = timed_run(b'0', state, 106.)
  items, state = timed_run(b'1,00,K,3,0,24.150,F,1', state, 106.5)
  assert not items[0]['breakthrough']
  _, state = timed_run(b'0', None, 100.)
  items, state = timed_run(b'1,00,KA,7,0,34.7,F,1', state, 100.5)
  items, _ = timed_run(b'1,00,KA,7,0,34.7,F,1', (104.9, {k: {**v, 'since': 90.} for k, v in state[1].items()}), 105.)
  assert items[0]['instant_on'] and not items[0]['timed']


def age(owner, seconds):
  _, seen = owner.uniden.seen
  owner.uniden.seen = (time.monotonic(), {k: {**v, 'since': v['since'] - seconds} for k, v in seen.items()})


def sent(client):
  return [call.args[4][0] for call in client._call.call_args_list if call.args[2] == 'WriteValue']


def test_exchange_mutes_after_the_delay_then_unmutes_once_the_signal_is_strong(setup):
  owner, client = setup
  sit = {'context': None, 'locks': [], 'quiet': None, 'timed': TIMED}
  def read(packet):
    client._call.return_value = (packet,)
    return exchange(owner, ADDRESS, 'R8', ('live',), situation=sit)
  read(b'1,00,K,3,0,24.150,F,1')
  assert sent(client) == []
  age(owner, 6)
  result = read(b'1,00,K,3,0,24.150,F,1')
  assert result['alerts'][0]['timed'] and sent(client) == [b'BTreqMUTE:1'] and owner.uniden.timed_mute
  read(b'1,00,K,4,0,24.150,F,2')
  assert sent(client) == [b'BTreqMUTE:1']
  result = read(b'1,00,K,6,0,24.150,F,2')
  assert result['alerts'][0]['breakthrough'] and result['command'] == 'unmute_sent_unconfirmed'
  assert sent(client)[-1] == b'BTreqMUTE:0'
  read(b'1,00,K,6,0,24.150,F,2')
  assert sent(client).count(b'BTreqMUTE:0') == 1
  for _ in range(5):
    owner.uniden.unmute = (owner.uniden.unmute[0], owner.uniden.unmute[1], 0)
    read(b'1,00,K,6,0,24.150,F,2')
  assert sent(client).count(b'BTreqMUTE:0') == 3
  assert sent(client).count(b'BTreqMUTE:1') == 1


def test_unmute_needs_timed_mute_and_never_undoes_the_drivers_own_mute(setup):
  owner, client = setup
  def sit(timed=TIMED):
    return {'context': None, 'locks': [], 'quiet': None, 'timed': timed}
  client._call.return_value = (b'1,00,K,7,0,24.150,F,2',)
  exchange(owner, ADDRESS, 'R8', ('live',), situation=sit())
  assert sent(client) == []
  owner.uniden.timed_mute = True
  exchange(owner, ADDRESS, 'R8', ('live',), situation=sit(None))
  assert sent(client) == []
  client._call.return_value = (b'1,00,K,7,0,24.150,F,3',)
  exchange(owner, ADDRESS, 'R8', ('live',), situation=sit())
  assert sent(client) == []
  client._call.return_value = (b'0',)
  exchange(owner, ADDRESS, 'R8', ('live',), situation=sit())
  assert not owner.uniden.timed_mute


def test_service_sends_the_timed_settings_and_validates_them(tmp_path):
  service, _, _ = make_service(tmp_path, {**CONFIG, 'timed_mute': True, 'timed_mute_seconds': 8, 'timed_mute_strength': 5,
                                          'timed_mute_bands': ['K'], 'unmute_on_breakthrough': False})
  assert service.situation(service.config(), None)['timed'] == {'bands': ['K'], 'seconds': 8, 'strength': 5,
    'settings': {band: {'seconds': 8, 'strength': 5} for band in ('X', 'K', 'KA')}}, 'a saved unmute_on_breakthrough still loads'
  assert normalize_config({**CONFIG, 'timed_mute_strength': 0})['timed_mute_strength'] == 0, '0 is never warn again'
  assert make_service(tmp_path)[0].situation(CONFIG, None)['timed'] is None
  for change in ({'timed_mute_seconds': 0}, {'timed_mute_seconds': 61}, {'timed_mute_strength': 1}, {'timed_mute_strength': 9},
                 {'timed_mute_seconds': 5.5}, {'timed_mute_bands': ['LASER']}, {'timed_mute': 1}):
    with pytest.raises(ValueError):
      normalize_config({**CONFIG, **change})


def test_timed_mute_uses_each_bands_delay_and_strength():
  timed = {**TIMED, 'settings': {'K': {'seconds': 2, 'strength': 0}, 'KA': {'seconds': 10, 'strength': 5}}}
  packet = b'1,00,K,3,0,24.150,F,1&1,00,KA,3,0,34.7,F,1'
  _, state = timed_run(packet, None, 100., timed)
  state = (102.9, state[1])
  items, state = timed_run(packet, state, 103., timed)
  by_band = {a['band']: a for a in items}
  assert by_band['K']['timed'] and not by_band['KA']['timed']
  items, _ = timed_run(b'1,00,K,8,0,24.150,F,1&1,00,KA,5,0,34.7,F,1', state, 103.5, timed)
  by_band = {a['band']: a for a in items}
  assert by_band['K']['timed'] and not by_band['K']['breakthrough']
  assert by_band['KA']['breakthrough'] and not by_band['KA']['timed']


def test_per_band_timed_settings_migrate_validate_and_round_trip():
  legacy = {**CONFIG, 'timed_mute_seconds': 9, 'timed_mute_strength': 0}
  migrated = normalize_config(legacy)
  assert migrated['timed_mute_settings'] == {band: {'seconds': 9, 'strength': 0} for band in ('X', 'K', 'KA')}
  migrated['timed_mute_settings']['KA'] = {'seconds': 20, 'strength': 7}
  assert normalize_config(migrated) == migrated
  assert migrated['timed_mute_settings']['K']['seconds'] == 9
  for settings in (None, [], {'LASER': {'seconds': 5, 'strength': 6}}, {'K': {}},
                   {'K': {'seconds': True, 'strength': 6}}, {'K': {'seconds': 61, 'strength': 6}},
                   {'K': {'seconds': 5, 'strength': 1}}, {'K': {'seconds': 5, 'strength': False}}):
    with pytest.raises(ValueError):
      normalize_config({**legacy, 'timed_mute_settings': settings})


def test_store_drops_bad_entries_one_by_one_and_refuses_links_and_oversized_files(tmp_path):
  from openpilot.starpilot.uniden import locks
  good = new_lock('tap', event(), 1)
  candidate = {'band': 'K', 'frequency': 24.15, 'lat': 36.0, 'lon': -115.0, 'count': 2, 'last': 1_000_000, 'max_strength': 4}
  (tmp_path / 'locks.json').write_text(json.dumps({'locks': [good, {**good, 'lat': 91}, 'junk'],
                                                    'candidates': [candidate, {**candidate, 'band': 'POP'}, {**candidate, 'extra': 1}, 7]}))
  store = LockStore(tmp_path)
  assert store.locks == [good]
  assert len(store.candidates) == 1 and store.candidates[0]['misses'] == 0 and type(store.candidates[0]['id']) is str
  linked = tmp_path / 'linked'
  linked.mkdir()
  (linked / 'locks.json').symlink_to(tmp_path / 'locks.json')
  assert LockStore(linked).locks == []
  with patch.object(locks, 'MAX_BYTES', 64):
    assert LockStore(tmp_path).locks == []


def test_lockout_write_failures_reach_the_request_and_learning_failures_are_logged(tmp_path):
  service, _, clock = make_service(tmp_path)
  service.store.add(new_lock('tap', event(), 1))
  lock_id = service.store.locks[0]['id']
  with patch.object(service.store, 'save', side_effect=OSError('read-only')), pytest.raises(OSError):
    service.request({'operation': 'unlock', 'id': lock_id}, Mock())
  assert [lock['id'] for lock in service.store.locks] == [lock_id], 'memory follows what was saved'
  with patch.object(service.store, 'visit', side_effect=OSError('full')), patch('openpilot.starpilot.uniden.service.cloudlog') as log:
    result = service.request({'operation': 'status'}, lambda config, mute, sit: reading(b'1,00,K,3,0,24.150,F,1', clock[0]))
  assert result['connected'] and log.exception.call_count == 1


def test_choosing_another_detector_forgets_the_last_alerts_and_passes(tmp_path):
  service, _, _ = make_service(tmp_path)
  service.parked = lambda: True
  service.last, service.passes, tracker = {'address': ADDRESS}, [{'at': 1}], service.tracker
  service.request({'operation': 'configure', 'config': CONFIG}, Mock())
  assert service.last is not None and service.tracker is tracker
  service.request({'operation': 'configure', 'config': {**CONFIG, 'address': '11:22:33:44:55:66'}}, Mock())
  assert service.last is None and service.passes == [] and service.tracker is not tracker


def test_detector_state_belongs_to_one_address_and_expires_when_unused(setup):
  from openpilot.starpilot.uniden.transport import STALE_S, detector_state
  owner, client = setup
  client._call.return_value = (b'1,00,K,3,0,24.150,F,1',)
  exchange(owner, ADDRESS, 'R8', ('live',), situation=situation())
  state = owner.uniden
  state.timed_mute = True
  assert state.address == ADDRESS and state.seen is not None and detector_state(owner, ADDRESS) is state
  other = detector_state(owner, '11:22:33:44:55:66')
  assert other is not state and not other.timed_mute and other.seen is None
  other.used_at -= STALE_S + 1
  assert detector_state(owner, '11:22:33:44:55:66') is not other


def test_close_wakes_and_stops_the_worker_even_while_a_call_blocks():
  entered, release, calls = threading.Event(), threading.Event(), []
  def call(method, path, payload):
    calls.append(payload)
    entered.set()
    release.wait(5)
    return 200, {'config': CONFIG, 'connected': False, 'alerts': []}
  feed = DetectorFeed(call, context=lambda: None)
  feed.set_enabled(True)
  assert entered.wait(2)
  feed.close()
  assert feed.stop.is_set() and not feed.enabled
  release.set()
  assert feed.thread is not None
  feed.thread.join(2)
  assert not feed.thread.is_alive() and len(calls) == 1
  idle = DetectorFeed(Mock(return_value=(200, {'config': {'enabled': False}})), context=lambda: None)
  idle.set_enabled(True)
  time.sleep(.05)
  started = time.monotonic()
  idle.close()
  assert idle.thread is not None and not idle.thread.is_alive() and time.monotonic() - started < .5, 'a sleeping worker is woken'


def test_learning_progress_survives_a_restart(tmp_path):
  store = LockStore(tmp_path)
  store.observe(event(), learn=True, bands=['K'])
  store.observe(event(band='LASER', frequency=None), learn=True, bands=['K'])
  store.observe(event(at=1_000_000 + REPEAT_S), learn=True, bands=['K'])
  assert len(store.candidates) == 2 and store.candidates[0]['count'] == 2
  assert LockStore(tmp_path).candidates == store.candidates
