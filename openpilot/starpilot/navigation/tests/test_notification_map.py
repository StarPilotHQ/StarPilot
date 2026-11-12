import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.cereal import log
from openpilot.starpilot.navigation.notification import LocalMapCorroborator
from openpilot.starpilot.navigation.phone import select_navigation

BOOT = 100_000_000_000
NOW = 10_000_000_000


def branch(angle=90, distance=120, **patch):
  return {'wayId': 77, 'junctionLatitude': 35.151, 'junctionLongitude': -97.9,
          'distanceMeters': distance, 'angleDegrees': angle, 'highwayClass': 'residential', **patch}


def phone(**patch):
  return {'observed': NOW, 'received': NOW, 'active': True, 'instruction': 'Turn right onto Main St',
          'maneuverType': 'turn', 'maneuverModifier': 'right', 'distanceMeters': 120,
          'source': 'googleMaps', 'session': 'test', 'sequence': 1, **patch}


def packet(branches=None, **patch):
  event = log.Event.new_message()
  event.init('mapdOut')
  event.valid, event.logMonoTime = True, BOOT
  event.mapdOut = {'sampleVersion': 2, 'roadStatus': 'matchedNoLimit', 'gpsSource': 'external',
                   'gpsMonoTime': BOOT - 100_000_000, 'computedMonoTime': BOOT,
                   'sourceGeneration': 1, 'producerSession': 1, 'tileLoaded': True,
                   'wayId': 42, 'waySelectionType': 'current', 'speedLimit': 0,
                   'navigationBranches': branches if branches is not None else [branch()], **patch}
  return event


def update(local_map, event, *, now=BOOT, alive=True):
  # Exercise the actual host wire schema, not a dictionary reader.
  with log.Event.from_bytes(event.to_bytes()) as wire:
    local_map.update(wire.mapdOut, valid=wire.valid, event_boot_ns=wire.logMonoTime,
                     received=True, alive=alive, now_boot_ns=now)


@pytest.mark.parametrize('kind,side,rows', [
    ('turn', 'right', [branch()]), ('turn', 'left', [branch(-90)]),
    ('turn', 'sharpLeft', [branch(-120)]), ('turn', 'slightRight', [branch(20)]),
    ('turn', 'uturn', [branch(160)]), ('continue', 'straight', [branch(5)]),
    ('continue', '', [branch(0)]),
    ('off ramp', 'right', [branch(25, highwayClass='motorwayLink')]),
    ('on ramp', '', [branch(40, highwayClass='trunkLink')]),
    ('fork', 'slightLeft', [branch(-20), branch(10, wayId=78)]),
])
def test_local_geometry_corroborates_notification_without_a_route(kind, side, rows):
  local_map = LocalMapCorroborator()
  update(local_map, packet(rows))
  assert local_map.status(phone(maneuverType=kind, maneuverModifier=side)) == 'mapAligned'


@pytest.mark.parametrize('rows,patch,status', [
    ([branch(120, 165)], {}, 'mapAligned'),  # Permit notification rounding and GPS/position uncertainty.
    ([branch(-90)], {}, 'mapDifferentDirection'),
    ([branch(90, 300)], {}, 'mapNoNearbyJunction'),
    ([branch(), branch(90, 175, junctionLatitude=35.152, wayId=78)], {}, 'mapAmbiguous'),
    ([branch(), branch(90, 140, junctionLatitude=35.1512, wayId=78)], {}, 'mapAligned'),  # Both carriageways of a divided road.
    ([branch()], {'maneuverType': 'fork'}, 'mapUnverifiable'),
    ([branch()], {'maneuverType': 'roundabout', 'exitNumber': '3'}, 'mapUnverifiable'),
    ([branch()], {'maneuverType': 'merge'}, 'mapUnverifiable'),
    ([branch()], {'distanceMeters': None}, 'mapUnverifiable'),
    ([], {}, 'mapUnavailable'),
])
def test_incomplete_or_disagreeing_maps_do_not_suppress_phone_guidance(rows, patch, status):
  local_map = LocalMapCorroborator()
  update(local_map, packet(rows))
  diagnostics = {}
  selected = select_navigation({'status': 'needsKey'}, phone(**patch), now_ns=NOW, drive_id=1,
                               local_map=local_map, diagnostics=diagnostics)
  assert selected['source'] == 'phone:googleMaps'
  assert selected['instruction']['text'] == phone()['instruction']
  assert diagnostics['mapStatus'] == status
  assert selected['controlValid'] == (status == 'mapAligned') and selected['route'] == []


@pytest.mark.parametrize('patch,valid,alive', [({'sampleVersion': 1}, True, True),
    ({'roadStatus': 'unknown'}, True, True), ({'tileLoaded': False}, True, True),
    ({'wayId': 0}, True, True), ({}, False, True), ({}, True, False)])
def test_unqualified_or_missing_maps_never_confirm(patch, valid, alive):
  local_map = LocalMapCorroborator()
  update(local_map, packet())
  event = packet(**patch)
  event.valid = valid
  event.logMonoTime += 1
  update(local_map, event, alive=alive, now=BOOT + 1)
  assert local_map.status(phone()) == 'mapUnavailable'


def test_stopped_map_producer_cannot_retain_old_confirmation():
  local_map = LocalMapCorroborator()
  update(local_map, packet())
  assert local_map.status(phone()) == 'mapAligned'
  local_map.update(None, valid=False, event_boot_ns=0, received=False, alive=True,
                   now_boot_ns=BOOT + 251_000_000)
  assert local_map.status(phone()) == 'mapUnavailable'


@pytest.mark.parametrize('row', [branch(angle=float('nan')), branch(distance=-1),
    branch(junctionLatitude=100), branch(wayId=0)])
def test_malformed_optional_geometry_clears_confirmation_and_recovers(row):
  local_map = LocalMapCorroborator()
  update(local_map, packet())
  invalid = packet([row])
  invalid.logMonoTime += 1
  update(local_map, invalid, now=BOOT + 1)
  assert local_map.status(phone()) == 'mapUnavailable'
  update(local_map, packet(), now=BOOT + 1)
  # A new event with the same event timestamp is a duplicate, not new evidence.
  event = packet()
  event.logMonoTime += 2
  update(local_map, event, now=BOOT + 2)
  assert local_map.status(phone()) == 'mapAligned'


def test_older_event_cannot_change_current_confirmation():
  local_map = LocalMapCorroborator()
  update(local_map, packet())
  event = packet([branch(-90)])
  event.logMonoTime -= 1
  update(local_map, event)
  assert local_map.status(phone()) == 'mapAligned'


def test_mapbox_priority_never_consults_phone_map_evidence():
  class UnusedMap:
    def resolve(self, _phone, **_):
      raise AssertionError('Mapbox source selection must remain independent')
  base = {'status': 'guiding'}
  assert select_navigation(base, phone(), now_ns=NOW, drive_id=1, local_map=UnusedMap()) is base


def test_old_provider_with_no_branch_field_still_allows_phone_guidance():
  local_map = LocalMapCorroborator()
  local_map.update(SimpleNamespace(), valid=True, event_boot_ns=BOOT, received=True,
                   alive=True, now_boot_ns=BOOT)
  assert local_map.status(phone()) == 'mapUnavailable'
  assert select_navigation({'status': 'noDestination'}, phone(), now_ns=NOW, drive_id=1,
                           local_map=local_map)['source'] == 'phone:googleMaps'


def test_actual_go_wire_fixture_confirms_phone_turn():
  path = Path(__file__).resolve().parents[4] / 'mapd_repo/cereal/testdata/mapd_navigation_wire.json'
  raw = base64.b64decode(json.loads(path.read_text())['event_base64'])
  local_map = LocalMapCorroborator()
  with log.Event.from_bytes(raw) as event:
    local_map.update(event.mapdOut, valid=event.valid, event_boot_ns=event.logMonoTime,
                     received=True, alive=True, now_boot_ns=BOOT)
  assert local_map.status(phone()) == 'mapAligned'


def ambiguous(gps_age=100_000_000, **patch):
  # mapd's shape for an ambiguous fix: invalid, no road or limit, navigation-only junctions.
  event = packet(**{'roadStatus': 'unknown', 'wayId': 0, 'tileLoaded': False, 'waySelectionType': 'fail',
                    'gpsMonoTime': BOOT - gps_age, **patch})
  event.valid = False
  return event


@pytest.mark.parametrize('source,gps_age,status', [
    ('external', 100_000_000, 'mapAligned'), ('external', 600_000_000, 'mapUnavailable'),
    ('phone', 2_000_000_000, 'mapAligned'), ('phone', 3_000_000_000, 'mapUnavailable'),
])
def test_navigation_only_junctions_from_ambiguous_fix_confirm_while_fresh(source, gps_age, status):
  local_map = LocalMapCorroborator()
  update(local_map, ambiguous(gps_age, gpsSource=source))
  assert local_map.status(phone()) == status
  # Held evidence expires with the producer's event age, as matched evidence does.
  local_map.update(None, valid=False, event_boot_ns=0, received=False, alive=True, now_boot_ns=BOOT + 251_000_000)
  assert local_map.status(phone()) == 'mapUnavailable'


@pytest.mark.parametrize('patch', [{'speedLimit': 20.}, {'wayId': 42}, {'sampleVersion': 1}])
def test_ambiguous_fix_with_road_fields_is_not_navigation_evidence(patch):
  local_map = LocalMapCorroborator()
  update(local_map, ambiguous(**patch))
  assert local_map.status(phone()) == 'mapUnavailable'


def test_phone_gps_matched_road_confirms():
  # The Galaxy "phone" GPS mode once made every frame unreadable here (enum value 4).
  local_map = LocalMapCorroborator()
  update(local_map, packet(gpsSource='phone', gpsMonoTime=BOOT - 2_000_000_000))
  assert local_map.status(phone()) == 'mapAligned'


def test_unreadable_provider_logs_once():
  local_map, logged = LocalMapCorroborator(), []
  from openpilot.common import swaglog
  original = swaglog.cloudlog.exception
  swaglog.cloudlog.exception = lambda *args, **kwargs: logged.append(args)
  try:
    for now in (BOOT, BOOT + 1):
      local_map.update(SimpleNamespace(), valid=True, event_boot_ns=now, received=True, alive=True, now_boot_ns=now)
  finally:
    swaglog.cloudlog.exception = original
  assert len(logged) == 1 and local_map.status(phone()) == 'mapUnavailable'


def test_trip_totals_do_not_affect_map_corroboration():
  local_map = LocalMapCorroborator()
  update(local_map, packet())
  plain = select_navigation({'status': 'noDestination'}, phone(), now_ns=NOW, drive_id=NOW - 1, local_map=local_map)
  totals = select_navigation({'status': 'noDestination'}, phone(remainingMeters=3000, remainingSeconds=240), now_ns=NOW,
                             drive_id=NOW - 1, local_map=local_map)
  assert plain['controlValid'] == totals['controlValid']
  assert not plain['totalsKnown'] and totals['totalsKnown']
  assert totals['instruction']['remainingDistanceMeters'] == 3000 and totals['instruction']['remainingDurationSeconds'] == 240


def bare(**patch):
  # Google Maps' usual notification: a distance and the road, the direction only in the arrow icon.
  return phone(instruction='S Oak Hill Blvd', maneuverType='', maneuverModifier='', **patch)


@pytest.mark.parametrize('rows,status', [
    ([branch(88, roadName='South Oak Hill Boulevard')], 'mapInferred'),
    ([branch(-64, roadName='Oak Hill Boulevard'), branch(115, roadName='Oak Hill Boulevard', wayId=78)], 'mapInferred'),
    ([branch(3, roadName='South Oak Hill Boulevard')], 'mapUnverifiable'),   # the road only changes its name
    ([branch(88, roadName='Elm Street')], 'mapUnverifiable'),
    ([branch(88)], 'mapUnverifiable'),
    ([branch(88, roadName='Oak Hill Boulevard'), branch(88, 175, roadName='Oak Hill Boulevard', wayId=78)], 'mapAmbiguous'),
])
def test_arrow_only_guidance_is_confirmed_by_a_turn_onto_the_named_road(rows, status):
  local_map = LocalMapCorroborator()
  update(local_map, packet(rows))
  resolved = local_map.resolve(bare())
  assert resolved.status == status
  if resolved.control:
    assert (resolved.kind, resolved.side) == ('turn', '')


def test_named_road_settles_two_junctions_on_the_instructed_side():
  local_map = LocalMapCorroborator()
  update(local_map, packet([branch(90, 100, roadName='Elm Street'), branch(90, 165, roadName='Main Street', wayId=78)]))
  resolved = local_map.resolve(phone(roadName='Main St'))
  assert resolved.status == 'mapAligned' and resolved.distance == 165
  assert local_map.resolve(phone()).status == 'mapAmbiguous'


def test_control_runs_on_the_junction_distance_and_fix_time():
  local_map = LocalMapCorroborator()
  update(local_map, packet([branch(90, 95)]))
  selected = select_navigation({'status': 'noDestination'}, phone(), now_ns=NOW, drive_id=NOW - 1_000_000_000,
                               local_map=local_map)
  assert selected['controlValid'] and selected['instruction']['distanceMeters'] == 95
  assert selected['locationMonoTime'] == selected['observationMonoTime'] == NOW - 100_000_000


def test_phone_distance_is_compared_after_the_distance_driven_since_it_was_seen():
  local_map = LocalMapCorroborator()
  local_map.resolve(phone(), speed=15., now_ns=NOW - 6_000_000_000)
  for second in range(5, -1, -1):
    update(local_map, packet([branch(90, 100)]))
    resolved = local_map.resolve(phone(distanceMeters=190, observed=NOW - 6_000_000_000), speed=15., now_ns=NOW - second * 1_000_000_000)
  assert resolved.status == 'mapAligned'
  assert LocalMapCorroborator().status(phone(distanceMeters=190)) == 'mapUnavailable'
  stopped = LocalMapCorroborator()
  update(stopped, packet([branch(90, 100)]))
  assert stopped.resolve(phone(distanceMeters=190, observed=NOW - 6_000_000_000), speed=0., now_ns=NOW).status == 'mapNoNearbyJunction'


def test_confirmed_junction_is_carried_while_a_live_map_loses_the_road():
  local_map = LocalMapCorroborator()
  update(local_map, packet([branch(90, 100)]))
  assert local_map.resolve(phone(distanceMeters=100), speed=10., now_ns=NOW).control
  def lost(seconds):
    now = BOOT + seconds * 1_000_000_000
    event = ambiguous(navigationBranches=[], gpsMonoTime=now - 100_000_000, computedMonoTime=now)
    event.logMonoTime = now
    update(local_map, event, now=now)
  lost(1)
  held = local_map.resolve(phone(distanceMeters=91), speed=10., now_ns=NOW + 1_000_000_000)
  # 100 m at the fix 0.1 s before NOW, less 1.1 s at 10 m/s.
  assert held.control and held.distance == pytest.approx(89) and held.anchor_ns == NOW + 1_000_000_000
  # The phone moving on to another distance, or the map producer stopping, ends it.
  lost(2)
  assert not local_map.resolve(phone(distanceMeters=400), speed=10., now_ns=NOW + 2_000_000_000).control
  assert not local_map.resolve(phone(distanceMeters=80), speed=10., now_ns=NOW + 3_000_000_000).control


def live(local_map, second, rows=None, **patch):
  # A current mapd frame `second` after BOOT; no rows is mapd momentarily losing the road.
  now = BOOT + int(second * 1e9)
  event = (packet(rows, gpsMonoTime=now - 100_000_000, computedMonoTime=now, **patch) if rows else
           ambiguous(navigationBranches=[], gpsMonoTime=now - 100_000_000, computedMonoTime=now))
  event.logMonoTime = now
  update(local_map, event, now=now)


def at(local_map, second, rows, make=phone, speed=10., **patch):
  # Approach at 10 m/s: the junction 100 m ahead at second 0, and the phone reading that, rounded.
  live(local_map, second, rows)
  observed = NOW + int(second * 1e9)
  return local_map.resolve(make(observed=observed, **{'distanceMeters': 100 - 10 * second, **patch}),
                           speed=speed, now_ns=observed)


def named(angle, distance, **patch):
  return branch(angle, distance, roadName='Oak Hill Boulevard', **patch)


def test_hold_survives_ticks_that_do_not_contradict_it():
  local_map = LocalMapCorroborator()
  assert at(local_map, 0, [named(88, 100)], bare).status == 'mapInferred'
  assert at(local_map, 1, [named(88, 90)], bare).distance == 90
  # mapd sees the junction but not its name for two ticks: the speed ceiling must not release.
  for second, left in ((2, 79), (3, 69)):
    held = at(local_map, second, [branch(88, 100 - 10 * second)], bare)
    assert held.status == 'mapInferred' and held.distance == pytest.approx(left)
    assert held.anchor_ns == NOW + second * 1_000_000_000
  assert at(local_map, 4, None, bare).distance == pytest.approx(59)
  assert at(local_map, 5, [named(88, 50)], bare).distance == 50


@pytest.mark.parametrize('rows,patch', [
    ([branch(-90, 80)], {}),                                                        # a different direction
    ([branch(90, 80), branch(90, 140, junctionLatitude=35.152, wayId=78)], {}),     # two junctions
    (None, {'instruction': 'Turn right onto Elm St'}),                               # the next instruction
    (None, {'distanceMeters': 300}),                                                 # the phone disagrees
    (None, {'session': 'other'}),                                                    # another trip
])
def test_hold_ends_on_contradiction_or_new_guidance(rows, patch):
  local_map = LocalMapCorroborator()
  assert at(local_map, 0, [branch(90, 100)]).control and at(local_map, 1, [branch(90, 90)]).control
  assert not at(local_map, 2, rows, **patch).control
  assert not at(local_map, 3, None).control


def test_hold_ends_with_the_map_producer_or_after_its_timeout():
  stopped = LocalMapCorroborator()
  assert at(stopped, 0, [branch(90, 100)]).control
  stopped.update(None, valid=False, event_boot_ns=0, received=False, alive=True, now_boot_ns=BOOT + 1_000_000_000)
  assert not stopped.resolve(phone(distanceMeters=90, observed=NOW + 1_000_000_000), speed=10., now_ns=NOW + 1_000_000_000).control
  # A packet the tracker refuses (stamped in the future) is not a live producer either.
  refused = LocalMapCorroborator()
  assert at(refused, 0, [branch(90, 100)]).control
  event = ambiguous(navigationBranches=[], gpsMonoTime=BOOT, computedMonoTime=BOOT + 1_000_000_000)
  event.logMonoTime = BOOT + 2_000_000_000
  update(refused, event, now=BOOT + 1_000_000_000)
  assert not refused.fresh
  assert not refused.resolve(phone(distanceMeters=90, observed=NOW + 1_000_000_000), speed=10., now_ns=NOW + 1_000_000_000).control
  # Creeping in traffic, the phone still agreeing: the hold lasts 20 s from the last confirmation.
  slow = LocalMapCorroborator()
  assert at(slow, 0, [branch(90, 100)], speed=1.).control
  assert all(at(slow, second, None, speed=1., distanceMeters=100 - second).control for second in range(1, 21))
  assert not at(slow, 21, None, speed=1., distanceMeters=79).control


def test_unknown_distance_driven_never_confirms():
  # The phone's reading predates the odometer by more than a gap: the match may be the next junction.
  local_map = LocalMapCorroborator()
  live(local_map, 0, [branch(90, 100)])
  stale = local_map.resolve(phone(distanceMeters=160, observed=NOW - 6_000_000_000), speed=10., now_ns=NOW)
  assert stale.status == 'mapUnverifiable'
  # A reading just before the odometer's first call is advanced at the current speed.
  assert local_map.resolve(phone(distanceMeters=120, observed=NOW - 2_000_000_000), speed=10., now_ns=NOW).control


def test_odometer_coasts_over_a_missed_speed_and_restarts_after_a_gap():
  coast = LocalMapCorroborator()
  assert at(coast, 0, [branch(90, 100)]).control
  assert at(coast, 1, None, speed=None).control
  assert at(coast, 2, None).distance == pytest.approx(79)
  # Without speed for longer than a gap there is no knowing how far the car went.
  blind = LocalMapCorroborator()
  assert at(blind, 0, [branch(90, 100)]).control
  assert [at(blind, second, None, speed=None).control for second in range(1, 6)] == [True, True, True, False, False]
  gap = LocalMapCorroborator()
  assert at(gap, 0, [branch(90, 100)]).control
  # No call for 5 s: the held junction cannot be advanced, so it is dropped.
  assert not at(gap, 5, None).control


def test_phone_distance_already_driven_past_never_confirms():
  local_map = LocalMapCorroborator()
  for second in range(-10, 0):
    local_map.resolve(phone(observed=NOW - 10_000_000_000), speed=10., now_ns=NOW + second * 1_000_000_000)
  live(local_map, 0, [branch(90, 30)])
  # 100 m driven since the phone said 20 m: the junction 30 m ahead is not that turn.
  assert local_map.resolve(phone(distanceMeters=20, observed=NOW - 10_000_000_000), speed=10., now_ns=NOW).status == 'mapUnverifiable'
  # Nor does it keep a junction confirmed earlier alive.
  assert local_map.resolve(phone(distanceMeters=10, observed=NOW), speed=10., now_ns=NOW).control
  stale = phone(distanceMeters=20, observed=NOW - 10_000_000_000)
  assert not local_map.resolve(stale, speed=10., now_ns=NOW + 500_000_000).control and local_map.held is None


@pytest.mark.parametrize('phone_road,map_road,same', [
    ('S Oak Hill Blvd', 'South Oak Hill Boulevard', True), ('S Oak Hill Blvd', 'Oak Hill Boulevard', True),
    ('E St', 'West Street', False), ('North Ave', 'West Avenue', False), ('N St', 'North Street', True),
    ('Main St', 'Main Avenue', False), ('Oak Hill Blvd', '', False),
])
def test_road_names_match_without_conflicting_cardinals(phone_road, map_road, same):
  from openpilot.starpilot.navigation.notification import instruction_roads, same_road
  assert same_road(map_road, instruction_roads(phone_road)) == same
  local_map = LocalMapCorroborator()
  update(local_map, packet([branch(88, roadName=map_road)]))
  assert local_map.resolve(phone(instruction=phone_road, maneuverType='', maneuverModifier='')).control == same


def test_diagnostics_detail_keeps_the_phone_reading_and_the_resolution():
  local_map = LocalMapCorroborator()
  update(local_map, packet([branch(88, 95, roadName='South Oak Hill Boulevard')]))
  diagnostics = {}
  select_navigation({'status': 'noDestination'}, bare(), now_ns=NOW, drive_id=1, local_map=local_map, diagnostics=diagnostics)
  assert diagnostics['mapStatus'] == 'mapInferred' and diagnostics['detail'] == ('', '', 120, 'turn', '', 95)
  diagnostics = {}
  select_navigation({'status': 'noDestination'}, phone(), now_ns=NOW, drive_id=1, local_map=LocalMapCorroborator(),
                    diagnostics=diagnostics)
  assert diagnostics['detail'] == ('turn', 'right', 120, '', '', 0)


class Master:
  valid, logMonoTime, updated, alive = {'mapdOut': True}, {'mapdOut': BOOT}, {'mapdOut': True}, {'mapdOut': True}

  def __getitem__(self, _service):
    return 'mapdOut'


def test_runtime_reads_map_evidence_only_while_a_phone_is_guiding():
  from openpilot.starpilot.navigation.runtime import corroborate
  local_map, reads = LocalMapCorroborator(), []
  local_map.update = lambda road, **values: reads.append(values['now_boot_ns'])
  def hold():
    local_map.held, local_map.last_event_ns = ('key', None, NOW), 1
    local_map.odometer.append((NOW, 0.))
  hold()
  corroborate(local_map, Master(), None, 7, NOW, 5)
  assert reads == [] and local_map.held is None and not local_map.odometer and not local_map.last_event_ns
  corroborate(local_map, Master(), phone(), 7, NOW, 5)
  assert reads == [NOW + 5]
  # A deviceState hiccup (no drive) keeps the hold; a new drive does not.
  hold()
  corroborate(local_map, Master(), phone(), 0, NOW, 5)
  corroborate(local_map, Master(), phone(), 7, NOW, 5)
  assert local_map.held is not None
  corroborate(local_map, Master(), phone(), 9, NOW, 5)
  assert local_map.held is None and len(reads) == 4
