"""Recorded Trax CAN collision; VIN identity never selects actuator capability.

2025 GM VIN standard, page 37:
https://vpic.nhtsa.dot.gov/mid/home/displayfile/93e823b4-c342-4ae0-8546-c947a390078d
"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car import car_helpers, gen_empty_fingerprint, structs
from opendbc.car.can_definitions import CanData
from opendbc.car.gm.discovery import automatic_trax_candidate
from opendbc.car.gm.values import CAR

RECORDED = json.loads((Path(__file__).parent / 'testdata/trax_2025_fingerprint.json').read_text())
VIN = 'KL77LKEP0SC000001'


def layout():
  fingerprint = gen_empty_fingerprint()
  fingerprint[0] = {int(address): size for address, size in RECORDED['bus0'].items()}
  fingerprint[2] = fingerprint[0].copy()
  return fingerprint


def receive():
  return [[CanData(address, bytes(size), bus) for bus, messages in layout().items()
           for address, size in messages.items()]]


class TestTraxFingerprint(unittest.TestCase):
  def test_recorded_collision_requires_vin(self):
    self.assertIsNone(car_helpers.can_fingerprint(lambda **kwargs: receive())[0])
    self.assertEqual(car_helpers.can_fingerprint(lambda **kwargs: receive(), vin=VIN)[0], CAR.CHEVROLET_TRAX)

  def test_documented_2025_descriptors_require_the_recorded_can_layout(self):
    candidates = {0: [CAR.CHEVROLET_TRAX, CAR.CHEVROLET_EQUINOX, CAR.CHEVROLET_SILVERADO]}
    for trim in 'FGHJK':
      for engine in '2P':
        vin = 'KL77L' + trim + 'E' + engine + '0SC000001'
        with self.subTest(trim=trim, engine=engine):
          self.assertEqual(automatic_trax_candidate(None, layout(), candidates, vin), CAR.CHEVROLET_TRAX)
    for address in (0xBE, 0xD3, 0x130, 0x1C4, 0x1E1):
      bad = layout()
      bad[0][address] += 1
      self.assertIsNone(automatic_trax_candidate(None, bad, candidates, VIN))

  def test_wrong_or_missing_vin_and_unrelated_candidates_stay_unresolved(self):
    candidates = {0: [CAR.CHEVROLET_TRAX, CAR.CHEVROLET_EQUINOX, CAR.CHEVROLET_SILVERADO]}
    for vin in (None, '', '00000000000000000', VIN[:-1], '1G1RC6S50HU000001',
                VIN[:5] + 'Z' + VIN[6:], VIN[:7] + 'Z' + VIN[8:], VIN[:9] + 'R' + VIN[10:],
                VIN[:10] + 'A' + VIN[11:]):
      with self.subTest(vin=vin):
        self.assertIsNone(automatic_trax_candidate(None, layout(), candidates, vin))
    for remaining in ([CAR.CHEVROLET_EQUINOX], [CAR.CHEVROLET_TRAX, CAR.CHEVROLET_VOLT]):
      self.assertIsNone(automatic_trax_candidate(None, layout(), {0: remaining}, VIN))
    self.assertEqual(automatic_trax_candidate(CAR.CHEVROLET_EQUINOX, layout(), candidates, VIN), CAR.CHEVROLET_EQUINOX)

  def test_actual_factory_auto_and_manual_keep_the_same_control_configuration(self):
    firmware = structs.CarParams.CarFw.new_message(ecu='fwdCamera', address=0x24b, brand='gm',
                                                 bus=0, fwVersion=b'recorded-camera', logging=True)
    cached = SimpleNamespace(brand='gm', carFingerprint=CAR.CHEVROLET_TRAX, carVin=VIN, carFw=[firmware])
    with patch.dict(car_helpers.os.environ, {'FINGERPRINT': '', 'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': ''}):
      auto = car_helpers.get_car(lambda **kwargs: receive(), lambda *args: None, lambda *args: None,
                                True, False, cached_params=cached).CP
      manual = car_helpers.get_car(lambda **kwargs: receive(), lambda *args: None, lambda *args: None,
                                  True, False, cached_params=cached, forced_candidate=CAR.CHEVROLET_TRAX).CP
    self.assertEqual(auto.carFingerprint, CAR.CHEVROLET_TRAX)
    self.assertEqual(auto.fingerprintSource, structs.CarParams.FingerprintSource.can)
    self.assertEqual(manual.fingerprintSource, structs.CarParams.FingerprintSource.fixed)
    self.assertFalse(auto.dashcamOnly)
    self.assertTrue(auto.openpilotLongitudinalControl)
    auto.fingerprintSource = manual.fingerprintSource
    self.assertEqual(auto.to_dict(), manual.to_dict())

  def test_explicit_override_wins_over_the_trax_route(self):
    with patch.dict(car_helpers.os.environ, {'FINGERPRINT': str(CAR.CHEVROLET_BOLT_CC_2017), 'SKIP_FW_QUERY': '1'}):
      result = car_helpers.fingerprint(lambda **kwargs: receive(), lambda *args: None, lambda *args: None, None)
    self.assertEqual(result[0], CAR.CHEVROLET_BOLT_CC_2017)
    self.assertEqual(result[4], structs.CarParams.FingerprintSource.fixed)
