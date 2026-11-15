import json
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from opendbc.car import fw_versions
from opendbc.car.can_definitions import CanData
from opendbc.car.car_helpers import fingerprint
from opendbc.car.chrysler.values import CAR
from opendbc.car.structs import CarParams
from opendbc.car.vin import VIN_UNKNOWN


def recorded_frames():
  fixture = json.loads((Path(__file__).parent / 'testdata/ram_normal_discovery.json').read_text())
  return [CanData(f['address'], bytes.fromhex(f['data']), f['bus']) for f in fixture['frames']]


def recorded_firmware():
  fixture = json.loads((Path(__file__).parent / 'testdata/ram_normal_discovery.json').read_text())
  result = []
  for row in fixture['firmware']:
    fw = CarParams.CarFw()
    fw.ecu, fw.address, fw.bus, fw.brand = row['ecu'], row['address'], 0, 'chrysler'
    fw.fwVersion = bytes.fromhex(row['version'])
    result.append(fw)
  return result


class TestRamNormalDiscovery(unittest.TestCase):
  def test_recorded_ram_never_switches_to_obd_and_preserves_fresh_identity(self):
    switches = []
    firmware = recorded_firmware()
    with patch.dict('os.environ', {}, clear=True), \
         patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)) as vin, \
         patch('opendbc.car.car_helpers.get_fw_versions', return_value=firmware) as query, \
         patch('opendbc.car.car_helpers.get_present_ecus') as present, \
         patch('opendbc.car.car_helpers.get_fw_versions_ordered') as generic_query, \
         patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
      actual = fingerprint(lambda wait_for_one=False: [recorded_frames()], lambda frames: None, switches.append, None)
    self.assertTrue(switches)
    self.assertFalse(any(switches))
    query.assert_called_once()
    self.assertEqual(query.call_args.kwargs, {'query_brand': 'chrysler', 'normal_bus_only': True})
    self.assertEqual(vin.call_args.args[2], (0,))
    self.assertEqual(actual[0], CAR.RAM_1500_5TH_GEN)
    self.assertEqual(len(actual[3]), 8)
    self.assertTrue(actual[5])
    present.assert_not_called()
    generic_query.assert_not_called()

  def test_routing_alone_cannot_identify_a_car_and_manual_override_survives(self):
    for manual in ('', CAR.RAM_HD_5TH_GEN):
      with self.subTest(manual=manual), patch.dict('os.environ', {'FINGERPRINT': str(manual)}, clear=True), \
           patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)), \
           patch('opendbc.car.car_helpers.get_fw_versions', return_value=[]), \
           patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
        actual = fingerprint(lambda wait_for_one=False: [recorded_frames()], lambda frames: None, lambda enabled: self.assertFalse(enabled), None)
        self.assertEqual(actual[0], manual or None)

  def test_incomplete_bad_crc_length_bus_or_forwarded_frames_cannot_select_chrysler(self):
    from opendbc.car.chrysler.discovery import normal_bus_topology
    frames = recorded_frames()
    self.assertTrue(normal_bus_topology([frames]))
    self.assertFalse(normal_bus_topology([]))
    for index, frame in enumerate(frames):
      others = frames[:index] + frames[index + 1:]
      with self.subTest(index=index):
        self.assertFalse(normal_bus_topology([others]))
        for invalid in (CanData(frame.address, frame.dat[:-1], frame.src),
                        CanData(frame.address, frame.dat, frame.src + 128),
                        CanData(frame.address, frame.dat, 1),
                        CanData(frame.address, frame.dat[:-1] + bytes([frame.dat[-1] ^ 1]), frame.src)):
          self.assertFalse(normal_bus_topology([[*others, invalid]]))
    self.assertFalse(normal_bus_topology([frames + [CanData(frames[0].address, b'', frames[0].src)]]))

  def test_live_chrysler_queries_execute_only_the_declared_normal_buses(self):
    switches, buses = [], []
    class Query:
      def __init__(self, send, recv, bus, *args):
        buses.append(bus)
      def get_data(self, timeout):
        return {}
    with patch.object(fw_versions, 'IsoTpParallelQuery', Query):
      fw_versions.get_fw_versions(lambda wait_for_one=False: [], lambda frames: None, switches.append,
                                  query_brand='chrysler', normal_bus_only=True)
    self.assertIn(0, buses)
    self.assertTrue(set(buses) <= {0, 4})
    self.assertFalse(any(switches))

  def test_changed_declared_or_effective_query_topology_rejects_normal_routing(self):
    from opendbc.car.chrysler.discovery import normal_bus_topology
    config = fw_versions.FW_QUERY_CONFIGS['chrysler']
    bad = replace(config.requests[0], bus=1)
    with patch.object(config, 'requests', [*config.requests, bad]):
      self.assertFalse(normal_bus_topology([recorded_frames()]))
    with patch.object(fw_versions, 'REQUESTS', [*fw_versions.REQUESTS, ('chrysler', config, bad)]):
      self.assertFalse(normal_bus_topology([recorded_frames()]))
    with patch.object(config, 'requests', []):
      self.assertFalse(normal_bus_topology([recorded_frames()]))

  def test_unrecognized_or_malformed_layout_retains_generic_discovery(self):
    for frames in ([], recorded_frames()[:-1]):
      switches = []
      with self.subTest(frames=len(frames)), patch.dict('os.environ', {}, clear=True), \
           patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)), \
           patch('opendbc.car.car_helpers.get_fw_versions_ordered', return_value=[]) as query, \
           patch('opendbc.car.car_helpers.get_present_ecus', return_value=set()) as present, \
           patch('opendbc.car.car_helpers.match_fw_to_car', return_value=(True, set())), \
           patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
        fingerprint(lambda wait_for_one=False, rows=frames: [rows], lambda frames: None, switches.append, None)
      self.assertEqual(switches, [True, False])
      present.assert_called_once()
      query.assert_called_once()
