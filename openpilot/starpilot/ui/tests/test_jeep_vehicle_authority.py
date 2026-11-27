"""Galaxy vehicle authority admits Jeep Brake Hold on stock-ACC WK2."""

import unittest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.chrysler.brake_hold import brake_hold_supported
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR
from opendbc.car.toyota.interface import toyota_auto_hold_supported


class TestJeepVehicleAuthority(unittest.TestCase):
  def test_brake_hold_supported_on_stock_acc_wk2(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, gen_empty_fingerprint(), [], False, False, False)
    self.assertTrue(brake_hold_supported(cp))
    self.assertFalse(toyota_auto_hold_supported(cp))
    # Combined vehicle-authority predicate used by runtime_app._feature_authority("vehicle").
    self.assertTrue(toyota_auto_hold_supported(cp) or brake_hold_supported(cp))

  def test_op_long_removes_jeep_vehicle_authority(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, gen_empty_fingerprint(), [], True, False, False)
    self.assertFalse(brake_hold_supported(cp))
    self.assertFalse(toyota_auto_hold_supported(cp) or brake_hold_supported(cp))

  def test_runtime_app_wires_chrysler_hold_into_vehicle_authority(self):
    # Avoid importing runtime_app (pulls native raylib). Assert the wiring in source.
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / 'runtime_app.py'
    text = src.read_text()
    self.assertIn('brake_hold_supported', text)
    self.assertIn('toyota_auto_hold_supported(cp) or brake_hold_supported(cp)', text)


if __name__ == '__main__':
  unittest.main()
