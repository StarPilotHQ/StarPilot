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
    ([branch(), branch(90, junctionLatitude=35.152, wayId=78)], {}, 'mapAmbiguous'),
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
  assert not selected['controlValid'] and selected['route'] == []


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
    def status(self, _phone):
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
