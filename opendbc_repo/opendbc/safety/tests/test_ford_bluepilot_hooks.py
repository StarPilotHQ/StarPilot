"""StarPilot's hooks for the BluePilot lateral library in modes/ford.h: its startup bits ride bits 9-14 of the
Ford param, and StarPilot's own profiles keep their RX/TX contracts around them."""
from pathlib import Path
import unittest

from opendbc.car.structs import CarParams
from opendbc.car.ford.aol import BP_LATERAL, BP_PARAM_MASK
from opendbc.bluepilot_lateral.core.values_ext import FORD_PINION_GEOMETRY_INDEX, FORD_PINION_GEOMETRY_SHIFT, FordSafetyFlagsSP
from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import BP_SAFETY_PARAM_MASK, BP_SAFETY_PARAM_SHIFT
from opendbc.safety.tests.libsafety import libsafety_py

LATERAL = int(FordSafetyFlagsSP.BP_LATERAL)
PINION = LATERAL | int(FordSafetyFlagsSP.STEER_ANGLE_CURVATURE) | (FORD_PINION_GEOMETRY_INDEX["FORD_BRONCO_SPORT_MK1"] << FORD_PINION_GEOMETRY_SHIFT)


def word(base, bits=0):
  return base | (bits << BP_SAFETY_PARAM_SHIFT)


class TestFordBluePilotHooks(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety

  def tearDown(self):
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(CarParams.SafetyModel.noOutput, 0)

  def init(self, param, ae=0):
    self.safety.init_tests()
    self.safety.set_alternative_experience(ae)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.ford, param), 0)

  def test_starpilot_layout_matches_the_library(self):
    self.assertEqual(BP_LATERAL, LATERAL << BP_SAFETY_PARAM_SHIFT)
    self.assertEqual(BP_PARAM_MASK, BP_SAFETY_PARAM_MASK << BP_SAFETY_PARAM_SHIFT)
    header = (Path(__file__).parents[1] / "modes" / "ford_param.h").read_text()
    self.assertIn(f"#define FORD_BP_PARAM_SHIFT {BP_SAFETY_PARAM_SHIFT}U", header)
    self.assertIn(f"#define FORD_BP_PARAM_MASK 0x{BP_PARAM_MASK:04X}U", header)

  def test_bits_reach_the_library(self):
    for base in (0, 1, 2, 3, 8, 9, 10, 11):
      with self.subTest(base=base):
        self.init(word(base, LATERAL))
        self.assertTrue(self.safety.get_ford_bp_lateral())
        self.init(word(base))
        self.assertFalse(self.safety.get_ford_bp_lateral())

  def test_transit_never_takes_the_bits(self):
    for base in (12, 13):
      with self.subTest(base=base):
        self.init(word(base, PINION))
        self.assertFalse(self.safety.get_ford_bp_lateral())
        self.assertFalse(self.safety.get_ford_bp_pinion_curvature())

  def test_pinion_rx_check_joins_the_selected_list(self):
    for ae in (0, 32):  # the stock list, and AOL's own
      with self.subTest(ae=ae):
        self.init(word(0, LATERAL), ae)
        count = self.safety.get_ford_rx_checks_len()
        self.init(word(0, PINION), ae)
        self.assertTrue(self.safety.get_ford_bp_pinion_curvature())
        self.assertEqual(self.safety.get_ford_rx_checks_len(), count + 1)

  def test_full_rx_list_denies_tx_instead_of_dropping_a_check(self):
    self.init(word(0, PINION), 32)
    self.assertTrue(self.safety.test_ford_bp_pinion_rx_overflow())

  def test_manual_bit_outside_the_registered_profiles_sends_nothing(self):
    neutral_lka = libsafety_py.make_CANPacket(0x3CA, 0, bytes(8))
    self.init(word(256, LATERAL))
    self.assertTrue(self.safety.safety_tx_hook(neutral_lka))
    self.init(word(4 | 256, LATERAL))  # LKA steering without the new port: not a registered profile
    self.assertFalse(self.safety.safety_tx_hook(neutral_lka))


if __name__ == "__main__":
  unittest.main()
