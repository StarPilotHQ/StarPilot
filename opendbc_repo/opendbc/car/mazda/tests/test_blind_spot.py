import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR, DBC, GEN1


class TestMazdaBlindSpot(unittest.TestCase):
  def test_gen2_and_gen3_report_no_blind_spot(self):
    # GEN2/GEN3 CarState never reads the BSM message, so they must not claim it.
    for model in set(CAR) - GEN1:
      with self.subTest(model=model):
        cp = CarInterface.get_params(model, gen_empty_fingerprint(), [], False, False, False)
        self.assertFalse(cp.deprecated.enableBsm)

  def test_reported_capability_matches_both_live_sides(self):
    for model in sorted(GEN1):
      with self.subTest(model=model):
        cp = CarInterface.get_params(model, gen_empty_fingerprint(), [], False, False, False)
        self.assertTrue(cp.deprecated.enableBsm)
        interface = CarInterface(cp)
        packer = CANPacker(DBC[model][Bus.pt])
        for frame, (left, right) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1), (0, 0)), 1):
          packet = packer.make_can_msg('BSM', 0, {'LEFT_BS_STATUS': left, 'RIGHT_BS_STATUS': right})
          state = interface.update([(frame * 100_000_000, [packet])])
          self.assertEqual((state.leftBlindspot, state.rightBlindspot), (bool(left), bool(right)))
