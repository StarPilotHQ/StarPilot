import json

import pytest

from openpilot.cereal import custom
from openpilot.starpilot.navigation.phone import LEASE_NS, OBSERVATION_NS, PhoneNavigationPublisher, read_phone_navigation, select_navigation
from openpilot.starpilot.ui.navigation_state import navigation_display

NOW = 100_000_000_000
DRIVE = 1_000_000_000


@pytest.fixture
def stream(tmp_path):
  clock = [NOW]
  path = tmp_path / 'navigation.json'
  return PhoneNavigationPublisher(path, lambda: clock[0]), path, clock


def payload(**values):
  return dict(session='test', sequence=1, active=True, ageMs=0, instruction='Take exit 12B onto Main St',
              distanceMeters=120, maneuverType='off ramp', maneuverModifier='right', **values)


def base(status='noDestination'):
  return {'status': status, 'sessionId': 'mapbox', 'enabled': False, 'revision': '1', 'frameMonoTime': NOW,
          'startedMonoTime': DRIVE, 'instruction': {}, 'nextManeuver': {}, 'route': [], 'locationMonoTime': 0, 'controlValid': False}


def state(value):
  return custom.StarPilotCarControl.new_message(navigation={**value, 'version': 2}).navigation


def test_handoff_existing_schema_and_display(stream):
  publisher, path, clock = stream
  assert publisher.publish(payload()) == {'ok': True, 'accepted': True}
  selected = select_navigation(base(), read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE)
  nav = state(selected)
  assert nav.controlValid is False
  assert len(nav.route) == 0
  display = navigation_display(nav, now_ns=NOW, drive_id=DRIVE)
  assert display.text == 'Take exit 12B onto Main St'
  assert display.distance_m == 120
  assert display.distance_known
  assert not display.totals_known


@pytest.mark.parametrize('status', ['routing', 'guiding', 'arrived', 'waitingForLocation', 'routeUnavailable'])
def test_mapbox_owns_calculation_rerouting_and_guidance(stream, status):
  publisher, path, _ = stream
  publisher.publish(payload())
  mapbox = base(status)
  assert select_navigation(mapbox, read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE) is mapbox


def test_mapbox_start_end_and_official_priority(stream):
  publisher, path, _ = stream
  publisher.publish(payload())
  phone = read_phone_navigation(NOW, path)
  for status, source in [('noDestination', 'phone:googleMaps'), ('guiding', None), ('routing', None), ('noDestination', 'phone:googleMaps')]:
    assert select_navigation(base(status), phone, now_ns=NOW, drive_id=DRIVE).get('source') == source
  mapbox = base()
  assert select_navigation(mapbox, phone, now_ns=NOW, drive_id=DRIVE, official_active=True) is mapbox


def test_disconnect_and_observation_expiry_are_independent(stream):
  publisher, path, clock = stream
  publisher.publish(payload())
  assert read_phone_navigation(NOW + LEASE_NS, path)
  assert read_phone_navigation(NOW + LEASE_NS + 1, path) is None
  for elapsed in range(10, 91, 10):
    clock[0] = NOW + elapsed * 1_000_000_000
    assert publisher.publish({**payload(), 'ageMs': elapsed * 1000})['accepted']
  assert read_phone_navigation(NOW + OBSERVATION_NS, path)
  assert read_phone_navigation(NOW + OBSERVATION_NS + 1, path) is None


def test_duplicate_does_not_rejuvenate_old_distance(stream):
  publisher, path, clock = stream
  publisher.publish(payload())
  clock[0] += 10_000_000_000
  publisher.publish(payload())
  assert json.loads(path.read_text())['observed'] == NOW


@pytest.mark.parametrize('patch', [{'instruction': 'Turn left'}, {'distanceMeters': 1}, {'source': 'waze'},
                                  {'maneuverModifier': 'left'}, {'roadName': 'Other road'}, {'remainingMeters': 10},
                                  {'remainingSeconds': 10}])
def test_duplicate_sequence_cannot_change_maneuver(stream, patch):
  publisher, path, _ = stream
  publisher.publish(payload())
  assert not publisher.publish({**payload(), **patch})['accepted']
  assert read_phone_navigation(NOW, path)['instruction'] == payload()['instruction']


def test_stop_and_out_of_order_updates_do_not_resurrect(stream):
  publisher, path, clock = stream
  publisher.publish(payload())
  publisher.publish({'session': 'test', 'sequence': 2, 'active': False, 'ageMs': 0})
  assert read_phone_navigation(NOW, path) is None
  assert not publisher.publish(payload())['accepted']
  assert read_phone_navigation(NOW, path) is None
  publisher.publish({**payload(), 'sequence': 3})
  assert read_phone_navigation(NOW, path)


@pytest.mark.parametrize('patch', [{'active': 1}, {'sequence': True}, {'ageMs': -1}, {'ageMs': 90001},
                                  {'distanceMeters': float('nan')}, {'distanceMeters': -1}, {'ageMs': float('inf')},
                                  {'instruction': ''}, {'maneuverType': []}, {'maneuverModifier': 'guess'},
                                  {'exitNumber': 1}, {'unknown': True}, {'session': ''},
                                  {'remainingMeters': -1}, {'remainingMeters': 20_000_001}, {'remainingMeters': float('inf')},
                                  {'remainingSeconds': 604_801}, {'remainingSeconds': '60'}, {'remainingSeconds': True}])
def test_rejects_malformed_updates_without_overwriting(stream, patch):
  publisher, path, _ = stream
  publisher.publish(payload())
  with pytest.raises(ValueError):
    publisher.publish({**payload(), **patch})
  assert read_phone_navigation(NOW, path)['instruction'] == payload()['instruction']


def test_text_only_guidance_is_selected_without_route_or_gps(stream):
  publisher, path, _ = stream
  publisher.publish({**payload(), 'distanceMeters': None, 'maneuverType': '', 'maneuverModifier': ''})
  mapbox = base()
  selected = select_navigation(mapbox, read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE)
  assert selected['source'] == 'phone:googleMaps'
  display = navigation_display(state(selected), now_ns=NOW, drive_id=DRIVE)
  assert display.text == payload()['instruction']
  assert not display.distance_known and not display.totals_known
  assert selected['locationMonoTime'] == 0 and not selected['controlValid']


def test_corrupt_and_previous_boot_handoff(stream):
  publisher, path, _ = stream
  path.write_text('bad json')
  assert read_phone_navigation(NOW, path) is None
  publisher.publish(payload())
  assert read_phone_navigation(NOW - 1, path) is None


def test_suspend_invalidates_existing_lease(stream, monkeypatch):
  from openpilot.starpilot.navigation import phone
  publisher, path, _ = stream
  monkeypatch.setattr(phone, 'boot_offset_ns', lambda: 100)
  publisher.publish(payload())
  assert read_phone_navigation(NOW, path)
  monkeypatch.setattr(phone, 'boot_offset_ns', lambda: 2_000_000_100)
  assert read_phone_navigation(NOW, path) is None


def test_late_previous_session_cannot_replace_restart(stream):
  publisher, path, _ = stream
  publisher.publish(payload())
  assert publisher.publish({**payload(), 'session': 'restarted', 'sequence': 0})['accepted']
  assert not publisher.publish({**payload(), 'sequence': 2})['accepted']
  assert read_phone_navigation(NOW, path)['session'] == 'restarted'


@pytest.mark.parametrize('corruption', [{'instruction': None}, {'retiredSessions': 1}, {'sequence': -1}])
def test_corrupted_handoff_is_ignored_and_next_update_recovers(stream, corruption):
  publisher, path, _ = stream
  publisher.publish(payload())
  value = json.loads(path.read_text())
  path.write_text(json.dumps({**value, **corruption}))
  assert read_phone_navigation(NOW, path) is None
  assert publisher.publish({**payload(), 'sequence': 2})['accepted']
  assert read_phone_navigation(NOW, path)


@pytest.mark.parametrize('source', ['googleMaps', 'waze', 'futureApp'])
def test_provider_agnostic_receiver_and_display(stream, source):
  publisher, path, _ = stream
  publisher.publish({**payload(), 'source': source})
  selected = select_navigation(base(), read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE)
  assert selected['source'] == 'phone:' + source
  assert not selected['controlValid']
  assert navigation_display(state(selected), now_ns=NOW, drive_id=DRIVE).text == payload()['instruction']
  assert select_navigation(base('guiding'), read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE)['sessionId'] == 'mapbox'


@pytest.mark.parametrize('source', ['', 'Mapbox', 'phone:waze', '../waze', None, 1])
def test_source_identifier_validation(stream, source):
  publisher, path, _ = stream
  with pytest.raises(ValueError):
    publisher.publish({**payload(), 'source': source})
  assert read_phone_navigation(NOW, path) is None


def test_comma_opt_in_required_before_selecting_phone(stream):
  publisher, path, _ = stream
  publisher.publish(payload())
  mapbox = base()
  assert select_navigation(mapbox, read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE, phone_enabled=False) is mapbox
  assert select_navigation(mapbox, read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE, phone_enabled=True)['source'] == 'phone:googleMaps'


def test_phone_status_explains_receipt_end_and_expiry(stream):
  from openpilot.starpilot.navigation.phone import phone_navigation_status
  publisher, path, _ = stream
  assert phone_navigation_status(NOW, path) is None
  publisher.publish(payload())
  status = phone_navigation_status(NOW, path)
  assert status['state'] == 'receiving' and status['instruction'] == payload()['instruction']
  assert status['distanceMeters'] == 120
  expired = phone_navigation_status(NOW + LEASE_NS + 1, path)
  assert expired['state'] == 'expired' and expired['instruction'] == '' and expired['distanceMeters'] is None
  assert status['remainingMeters'] is None and status['remainingSeconds'] is None
  publisher.publish({**payload(), 'sequence': 2, 'remainingMeters': 8000, 'remainingSeconds': 600})
  status = phone_navigation_status(NOW, path)
  assert (status['remainingMeters'], status['remainingSeconds']) == (8000, 600)
  assert phone_navigation_status(NOW + LEASE_NS + 1, path)['remainingMeters'] is None
  publisher.publish({'session': 'test', 'sequence': 3, 'active': False, 'ageMs': 0})
  assert phone_navigation_status(NOW, path)['state'] == 'ended'


def test_trip_totals_reach_the_display_only_when_both_known(stream):
  publisher, path, _ = stream
  publisher.publish(payload(remainingMeters=8046.7, remainingSeconds=540))
  selected = select_navigation(base(), read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE)
  display = navigation_display(state(selected), now_ns=NOW, drive_id=DRIVE)
  assert display is not None and display.totals_known
  assert display.remaining_distance_m == pytest.approx(8046.7, rel=1e-6) and display.remaining_seconds == 540
  publisher.publish({**payload(), 'sequence': 2, 'remainingSeconds': 540})
  selected = select_navigation(base(), read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE)
  display = navigation_display(state(selected), now_ns=NOW, drive_id=DRIVE)
  assert display is not None and not display.totals_known


def test_arrive_flows_to_the_card(stream):
  from openpilot.starpilot.ui.navigation_icons import icon_name
  publisher, path, _ = stream
  publisher.publish({**payload(), 'maneuverType': 'arrive', 'maneuverModifier': '', 'instruction': 'Arrive at Library', 'distanceMeters': 40})
  selected = select_navigation(base(), read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE)
  display = navigation_display(state(selected), now_ns=NOW, drive_id=DRIVE)
  assert display is not None and display.maneuver_type == 'arrive' and display.text == 'Arrive at Library'
  assert icon_name('arrive', '') == icon_name(display.maneuver_type, display.modifier)
  assert 'arrive' in icon_name('arrive', '')


class Aligned:
  def __init__(self, status='mapAligned'):
    self.value = status

  def status(self, phone):
    return self.value


def selected_for_control(stream, local_map, **values):
  publisher, path, clock = stream
  publisher.publish({**payload(), 'session': f'control{len(values)}{id(local_map)}{values.get("distanceMeters", "")}',
                     'maneuverType': 'turn', 'maneuverModifier': 'right', **values})
  return select_navigation(base(), read_phone_navigation(NOW, path), now_ns=NOW, drive_id=DRIVE, local_map=local_map)


def test_control_needs_map_agreement_known_distance_and_fresh_observation(stream):
  granted = selected_for_control(stream, Aligned())
  assert granted['controlValid'] and granted['locationMonoTime'] == NOW
  for status in ('mapUnavailable', 'mapAmbiguous', 'mapDifferentDirection', 'mapNoNearbyJunction', 'mapUnverifiable'):
    assert not selected_for_control(stream, Aligned(status))['controlValid']
  assert not selected_for_control(stream, None)['controlValid']
  assert not selected_for_control(stream, Aligned(), distanceMeters=None)['controlValid']
  assert not selected_for_control(stream, Aligned(), distanceMeters=0)['controlValid']
