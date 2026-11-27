import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from opendbc.car.can_definitions import CanData
from opendbc.car.car_helpers import fingerprint, get_car
from opendbc.car import fw_versions, structs
from opendbc.car.honda.values import CAR, HondaFlags
from opendbc.car.vin import VIN_UNKNOWN


def recorded_frames():
  fixture = json.loads((Path(__file__).parent / 'testdata/bosch_normal_discovery.json').read_text())
  return [CanData(f['address'], bytes.fromhex(f['data']), f['bus']) for f in fixture['frames']]


class TestHondaNormalDiscovery(unittest.TestCase):
  def test_recorded_bosch_startup_never_switches_to_obd(self):
    switches = []
    with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)) as vin, \
         patch('opendbc.car.car_helpers.get_fw_versions', return_value=[]) as query, \
         patch('opendbc.car.car_helpers.get_present_ecus') as present, \
         patch('opendbc.car.car_helpers.get_fw_versions_ordered') as generic_query, \
         patch('opendbc.car.car_helpers.match_fw_to_car', return_value=(True, set())), \
         patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
      actual = fingerprint(lambda wait_for_one=False: [recorded_frames()], lambda frames: None, switches.append, None)
    self.assertFalse(any(switches))
    self.assertTrue(switches)
    query.assert_called_once()
    self.assertEqual(query.call_args.kwargs, {'query_brand': 'honda', 'normal_bus_only': True})
    self.assertEqual(vin.call_args.args[2], (0, 1))
    present.assert_not_called()
    generic_query.assert_not_called()
    self.assertIsNone(actual[0])

  def _accord_discovery(self, normal_read, obd_read):
    switches = []
    with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)) as vin, \
         patch('opendbc.car.car_helpers.get_fw_versions', side_effect=[normal_read, obd_read]) as query, \
         patch('opendbc.car.car_helpers.match_fw_to_car', return_value=(True, {CAR.HONDA_ACCORD})), \
         patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
      actual = fingerprint(lambda wait_for_one=False: [recorded_frames()], lambda frames: None, switches.append, None)
    return switches, vin, query, actual

  def test_accord_without_an_eps_read_queries_the_obd_port(self):
    radar = SimpleNamespace(ecu="fwdRadar", logging=False, responseAddress=0, subAddress=0, bus=1)
    eps = SimpleNamespace(ecu="eps", logging=False, responseAddress=0, subAddress=0, bus=1)
    switches, vin, query, actual = self._accord_discovery([radar], [radar, eps])
    self.assertEqual(switches, [False, True, False])
    self.assertEqual(vin.call_count, 2)
    self.assertEqual([call.kwargs for call in query.call_args_list],
                     [{'query_brand': 'honda', 'normal_bus_only': True}, {'query_brand': 'honda'}])
    self.assertEqual(actual[3], [radar, eps])

  def test_accord_with_an_eps_read_stays_on_normal_wiring(self):
    eps = SimpleNamespace(ecu="eps", logging=False, responseAddress=0, subAddress=0, bus=1)
    switches, vin, query, _ = self._accord_discovery([eps], AssertionError("no second query"))
    self.assertFalse(any(switches))
    query.assert_called_once()

  def test_manually_selected_accord_queries_eps_when_radar_and_camera_cannot_identify_it(self):
    radar = structs.CarParams.CarFw.new_message(ecu="fwdRadar", address=0x18DAB0F1, fwVersion=b"36802-TVA-A190\x00\x00", brand="honda")
    camera = structs.CarParams.CarFw.new_message(ecu="fwdCamera", address=0x18DAB5F1, fwVersion=b"36161-TVA-A060\x00\x00", brand="honda")
    normal_fw = [radar, camera]
    self.assertNotIn(CAR.HONDA_ACCORD, fw_versions.match_fw_to_car(normal_fw, VIN_UNKNOWN)[1])

    for version, angle in ((b"39990-TVA-A160\x00\x00", False), (b"39990-TVA,A16C\x00\x00", True), (None, False)):
      with self.subTest(version=version):
        switches = []
        obd_fw = normal_fw.copy()
        if version is not None:
          obd_fw.append(structs.CarParams.CarFw.new_message(ecu="eps", address=0x18DA30F1, fwVersion=version, brand="honda"))
        with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)), \
             patch('opendbc.car.car_helpers.get_fw_versions', side_effect=[normal_fw, obd_fw]) as query, \
             patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {0: {}, 1: {}, 2: {}})):
          interface = get_car(lambda wait_for_one=False: [recorded_frames()], lambda frames: None, switches.append,
                              False, False, forced_candidate=CAR.HONDA_ACCORD)
        self.assertEqual(switches, [False, True, False])
        self.assertEqual([call.kwargs for call in query.call_args_list],
                         [{'query_brand': 'honda', 'normal_bus_only': True}, {'query_brand': 'honda'}])
        self.assertEqual(interface.CP.fingerprintSource, structs.CarParams.FingerprintSource.fixed)
        self.assertEqual(interface.CP.steerControlType == structs.CarParams.SteerControlType.angle, angle)
        self.assertEqual(bool(interface.CP.flags & HondaFlags.EPS_FW_UNREAD), version is None)

  def test_incomplete_or_invalid_traffic_cannot_select_honda(self):
    from opendbc.car.honda.discovery import normal_bus_topology
    frames = recorded_frames()
    self.assertTrue(normal_bus_topology([frames]))
    for index, frame in enumerate(frames):
      with self.subTest(index=index):
        others = frames[:index] + frames[index + 1:]
        self.assertFalse(normal_bus_topology([others]))
        for bad in (CanData(frame.address, frame.dat[:-1], frame.src),
                    CanData(frame.address, frame.dat, frame.src + 128),
                    CanData(frame.address, frame.dat, 0),
                    CanData(frame.address, frame.dat[:-1] + bytes([frame.dat[-1] ^ 1]), frame.src)):
          self.assertFalse(normal_bus_topology([[*others, bad]]))
    self.assertFalse(normal_bus_topology([]))
    self.assertFalse(normal_bus_topology([frames + [CanData(frames[0].address, b'', frames[0].src)]]))

  def test_normal_query_filter_executes_live_honda_pt_requests(self):
    switches, buses = [], []

    class Query:
      def __init__(self, send, recv, bus, *args):
        buses.append(bus)

      def get_data(self, timeout):
        return {}

    with patch.object(fw_versions, 'IsoTpParallelQuery', Query):
      fw_versions.get_fw_versions(lambda wait_for_one=False: [], lambda frames: None, switches.append,
                                  query_brand='honda', normal_bus_only=True)
    self.assertIn(1, buses)
    self.assertTrue(switches)
    self.assertFalse(any(switches))

  def test_unrecognized_layout_retains_generic_discovery(self):
    switches = []
    with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)), \
         patch('opendbc.car.car_helpers.get_fw_versions_ordered', return_value=[]) as query, \
         patch('opendbc.car.car_helpers.get_present_ecus', return_value=set()) as present, \
         patch('opendbc.car.car_helpers.match_fw_to_car', return_value=(True, set())), \
         patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
      fingerprint(lambda wait_for_one=False: [], lambda frames: None, switches.append, None)
    self.assertEqual(switches, [True, False])
    present.assert_called_once()
    query.assert_called_once()
