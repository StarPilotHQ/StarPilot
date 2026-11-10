"""Actual historical and Go-provider wire fixtures retain their field meanings."""

import base64
import json
from pathlib import Path
import unittest

from openpilot.cereal import custom, log

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = json.loads(Path(__file__).with_name('fixtures').joinpath('mapd_wire.json').read_text())
IO_FIXTURES = json.loads((ROOT / 'mapd_repo/cereal/testdata/mapd_io_wire.json').read_text())


class MapdSchemaTests(unittest.TestCase):
  def test_historical_status_and_input_payloads_preserve_meanings(self):
    with custom.MapdExtendedOut.from_bytes(base64.b64decode(IO_FIXTURES['historical']['extended_payload_base64'])) as status:
      progress = status.downloadProgress
      self.assertTrue(progress.active and progress.cancelled)
      self.assertEqual((progress.totalFiles, progress.downloadedFiles), (7, 3))
      self.assertEqual(list(progress.locations), ['historical-region'])
      self.assertEqual(progress.locationDetails[0].to_dict(), {'location': 'historical-region', 'totalFiles': 7, 'downloadedFiles': 3})
      self.assertEqual(status.settings, '{"historical":true}')
      self.assertEqual((status.path[0].latitude, status.path[0].longitude, status.path[0].targetVelocity), (36.25, -96.5, 8.5))
      self.assertAlmostEqual(status.path[0].curvature, -0.002)
      self.assertFalse(status._has('position'))
      self.assertEqual((status.loopRateAverage, status.loopRateMin), (0, 0))
    with custom.MapdIn.from_bytes(base64.b64decode(IO_FIXTURES['historical']['input_payload_base64'])) as request:
      self.assertEqual((str(request.type), request.float, request.str, request.bool, request.jsonPath),
                       ('setPressGasToOverrideSpeedLimit', 1.25, 'historical input', True, ''))

  def test_actual_go_provider_extended_status_and_input(self):
    with log.Event.from_bytes(base64.b64decode(IO_FIXTURES['provider']['extended_event_base64'])) as event:
      self.assertEqual((event.which(), event.logMonoTime, event.valid), ('mapdExtendedOut', 780_000_000_000, True))
      status = event.mapdExtendedOut
      self.assertEqual((status.downloadProgress.totalFiles, status.downloadProgress.downloadedFiles), (10, 4))
      self.assertEqual(list(status.downloadProgress.locations), ['synthetic-region'])
      self.assertEqual(status.settings, '{"fixture":true}')
      self.assertEqual((status.path[0].latitude, status.path[0].longitude, status.path[0].targetVelocity), (35.15, -97.9, 12.5))
      self.assertAlmostEqual(status.path[0].curvature, 0.001)
      self.assertEqual((status.position.latitude, status.position.longitude), (35.151, -97.901))
      self.assertEqual((status.loopRateAverage, status.loopRateMin), (20, 18.5))
    with log.Event.from_bytes(base64.b64decode(IO_FIXTURES['provider']['input_event_base64'])) as event:
      self.assertEqual((event.which(), event.logMonoTime, event.valid), ('mapdIn', 780_000_000_000, True))
      request = event.mapdIn
      self.assertEqual((str(request.type), request.float, request.str, request.bool, request.jsonPath),
                       ('setJsonPathFloat', 2.75, 'synthetic input', True, 'curve.targetLatA'))

  def test_actual_host_input_fixture_read_by_provider(self):
    event = log.Event.new_message(logMonoTime=790_000_000_000, valid=True)
    request = event.init('mapdIn')
    request.type, request.float, request.str, request.bool, request.jsonPath = ('setJsonPathText', 1.5, 'host fixture', True, 'display.units')
    self.assertEqual(event.to_bytes(), base64.b64decode(IO_FIXTURES['host']['input_event_base64']))

  def test_historical_event_reads_with_appended_defaults(self):
    with log.Event.from_bytes(base64.b64decode(FIXTURES['historical']['event_base64'])) as event:
      self.assertEqual(event.which(), 'mapdOut')
      self.assertTrue(event.valid)
      self.assertEqual(event.logMonoTime, 123_000_000_000)
      road = event.mapdOut
      self.assertEqual((road.roadName, road.wayName, road.wayRef), ('Synthetic old road', 'Old Way', 'T1'))
      self.assertAlmostEqual(road.speedLimit, 19.44, places=4)
      self.assertAlmostEqual(road.nextSpeedLimit, 13.4112, places=4)
      self.assertEqual(road.nextSpeedLimitDistance, 150)
      self.assertTrue(road.tileLoaded)
      self.assertTrue(road.speedLimitAccepted)
      self.assertEqual(road.lanes, 2)
      self.assertEqual(str(road.roadContext), 'city')
      self.assertEqual(str(road.waySelectionType), 'current')
      self.assertEqual((road.wayId, str(road.highwayClass), road.conditionalSpeedLimit), (0, 'unknown', ''))

  def test_provider_go_event_reads_new_fields_without_reinterpreting_old(self):
    with log.Event.from_bytes(base64.b64decode(FIXTURES['provider']['event_base64'])) as event:
      self.assertEqual(event.which(), 'mapdOut')
      self.assertTrue(event.valid)
      self.assertEqual(event.logMonoTime, 456_000_000_000)
      road = event.mapdOut
      self.assertEqual(road.roadName, 'Synthetic provider road')
      self.assertAlmostEqual(road.speedLimit, 13.4112, places=4)
      self.assertTrue(road.tileLoaded)
      self.assertEqual(str(road.roadContext), 'city')
      self.assertEqual(str(road.waySelectionType), 'possible')
      self.assertEqual(str(road.highwayClass), 'residential')
      self.assertEqual(road.wayId, 1_234_567_890_123)
      self.assertEqual(road.conditionalSpeedLimit, '20 @ (Mo-Fr 08:00-09:00)')
      # A fresh old-provider Event is still missing the v1 computation evidence.
      self.assertEqual(road.sampleVersion, 0)
      self.assertEqual(str(road.roadStatus), 'unknown')
      self.assertEqual((road.gpsMonoTime, road.computedMonoTime, road.producerSession), (0, 0, 0))

  def test_historical_provider_v1_wire_remains_readable(self):
    fixture = json.loads((ROOT / 'mapd_repo/cereal/testdata/mapd_v1_wire.json').read_text())
    # Its schema hash records the original v1 producer. The current v2 reader
    # preserves that layout, but v1 timestamps no longer qualify as evidence.
    with log.Event.from_bytes(base64.b64decode(fixture['event_base64'])) as event:
      self.assertEqual(event.which(), 'mapdOut')
      self.assertTrue(event.valid)
      road = event.mapdOut
      self.assertEqual((road.sampleVersion, str(road.roadStatus), str(road.gpsSource)), (1, 'matchedLimit', 'external'))
      self.assertEqual((road.gpsMonoTime, road.computedMonoTime, event.logMonoTime),
                       (455_950_000_000, 455_990_000_000, 456_000_000_000))
      self.assertEqual((road.sourceGeneration, road.producerSession), (3, 0x1234abcd5011))
      self.assertAlmostEqual(road.speedLimit, 13.4112, places=4)
      self.assertEqual(road.wayId, 1_234_567_890_123)

  def test_actual_go_navigation_geometry_fixture(self):
    fixture = json.loads((ROOT / 'mapd_repo/cereal/testdata/mapd_navigation_wire.json').read_text())
    with log.Event.from_bytes(base64.b64decode(fixture['event_base64'])) as event:
      self.assertTrue(event.valid)
      self.assertEqual(event.mapdOut.wayId, 42)
      self.assertEqual(len(event.mapdOut.navigationBranches), 1)
      branch = event.mapdOut.navigationBranches[0]
      self.assertEqual((branch.wayId, branch.roadName, str(branch.highwayClass)), (77, 'Main St', 'residential'))
      self.assertEqual((branch.angleDegrees, branch.distanceMeters), (90, 120))
      self.assertAlmostEqual(branch.junctionLatitude, 35.151)
      self.assertAlmostEqual(branch.junctionLongitude, -97.9)
    with log.Event.from_bytes(base64.b64decode(FIXTURES['historical']['event_base64'])) as event:
      self.assertEqual(len(event.mapdOut.navigationBranches), 0)

  def test_provider_pin_and_enum_identities(self):
    pin = next(row for row in json.loads((ROOT / 'upstream-sync.json').read_text())['dependencies'] if row['path'] == 'mapd_repo')
    self.assertEqual(pin['commit'], FIXTURES['provider']['source_revision'])
    self.assertEqual(custom.MapdOut.schema.node.id, 0xa4f1eb3323f5f582)
    for name, type_id in (('roadContext', 0xabefa88b9563dbae), ('waySelectionType', 0xfa3e5ce74e25e82a),
                          ('highwayClass', 0xd18274dcdc63c41d)):
      self.assertEqual(custom.MapdOut.schema.fields[name].schema.node.id, type_id)


if __name__ == '__main__':
  unittest.main()
