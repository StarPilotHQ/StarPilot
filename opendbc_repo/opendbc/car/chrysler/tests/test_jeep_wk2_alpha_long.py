"""JEEP_GRAND_CHEROKEE_2019 alpha-long + radar admission."""

import unittest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.chrysler.aol_contract import qualified as aol_qualified
from opendbc.car.chrysler.brake_hold import brake_hold_supported
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR, ChryslerFlags, ChryslerSafetyFlags


def _fp_with_radar():
  fp = gen_empty_fingerprint()
  # OEM Chrysler track IDs observed on WK2 bus 1.
  fp[1] = {addr: 8 for addr in list(range(0x2a2, 0x2b4 + 2, 2)) + list(range(0x2c2, 0x2d4 + 2, 2))}
  return fp


class TestJeepWk2AlphaLong(unittest.TestCase):
  def test_alpha_long_off_keeps_stock_acc_and_hold(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, _fp_with_radar(), [], False, False, False)
    self.assertTrue(cp.flags & ChryslerFlags.ALPHA_LONG_AVAILABLE)
    self.assertTrue(cp.alphaLongitudinalAvailable)
    self.assertFalse(cp.openpilotLongitudinalControl)
    self.assertTrue(cp.pcmCruise)
    # Still unavailable until #26842-class parse/FCW validation on WK2 (addrs alone are not enough).
    self.assertTrue(cp.radarUnavailable)
    self.assertEqual(cp.safetyConfigs[0].safetyParam & ChryslerSafetyFlags.LONG_CONTROL, 0)
    self.assertTrue(brake_hold_supported(cp))
    self.assertTrue(aol_qualified(cp))

  def test_alpha_long_on_enables_op_long_and_disables_hold(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, _fp_with_radar(), [], True, False, False)
    self.assertTrue(cp.openpilotLongitudinalControl)
    self.assertFalse(cp.pcmCruise)
    self.assertTrue(cp.autoResumeSng)
    self.assertEqual(cp.minEnableSpeed, -1.)
    self.assertTrue(cp.safetyConfigs[0].safetyParam & ChryslerSafetyFlags.LONG_CONTROL)
    self.assertFalse(brake_hold_supported(cp))
    self.assertFalse(aol_qualified(cp))

  def test_alpha_long_hidden_on_release(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, _fp_with_radar(), [], True, True, False)
    self.assertFalse(cp.alphaLongitudinalAvailable)
    self.assertFalse(cp.openpilotLongitudinalControl)
    self.assertTrue(cp.pcmCruise)

  def test_radar_stays_unavailable_even_when_tracks_present(self):
    # Presence proves hardware; #26842 was parse/FCW quality — do not auto-enable.
    for fp in (gen_empty_fingerprint(), _fp_with_radar()):
      cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, fp, [], False, False, False)
      self.assertTrue(cp.radarUnavailable)

  def test_older_wk2_has_no_alpha_long(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE, gen_empty_fingerprint(), [], True, False, False)
    self.assertFalse(cp.flags & ChryslerFlags.ALPHA_LONG_AVAILABLE)
    self.assertFalse(getattr(cp, 'alphaLongitudinalAvailable', False))
    self.assertFalse(cp.openpilotLongitudinalControl)

  def test_gap_stalk_maps_both_distance_directions(self):
    # Distance+ and Distance− both feed gapAdjustCruise (OR'd into distance_button).
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'carstate.py').read_text()
    self.assertIn('ACC_Distance_Dec', src)
    self.assertIn('ACC_Distance_Inc', src)
    self.assertIn('or buttons["ACC_Distance_Inc"]', src)


if __name__ == '__main__':
  unittest.main()
