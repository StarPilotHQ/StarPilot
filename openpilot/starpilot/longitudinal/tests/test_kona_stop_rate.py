import unittest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.selfdrive.controls.lib.longcontrol import LongControl


def kona_params(candidate, alpha, alternate=False, marked=False):
  fp = gen_empty_fingerprint()
  fp[0].update({0x1AA if alternate else 0x1CF: 16 if alternate else 8,
                0xFA if candidate == CAR.HYUNDAI_KONA_HEV_2ND_GEN else 0x100: 32,
                0x175: 24, 0xA0: 24, 0xEA: 24, 0x125: 16})
  fp[2].update({0x12A: 16, 0x1A0: 32, 0x161: 16, 0x162: 32})
  cp = CarInterface.get_params(candidate, fp, [], alpha, False, False)
  if marked:
    cp.safetyConfigs[0].safetyParam |= 0x800
    cp.alternativeExperience = 32
  return cp


class TestKonaStopRate(unittest.TestCase):
  def test_qualified_gas_kona_restores_stable_stop_rate(self):
    for alternate in (False, True):
      for marked in (False, True):
        with self.subTest(alternate=alternate, marked=marked):
          cp = kona_params(CAR.HYUNDAI_KONA_2ND_GEN, True, alternate, marked)
          original = cp.to_dict()
          controller = LongControl(cp)
          state = structs.CarState(vEgo=1.3).as_reader()
          for frame in range(30):
            output = controller.update(True, state, -0.5, True, (-3.5, 2.0))
            self.assertAlmostEqual(output, -0.008 * (frame + 1))
          self.assertEqual(controller.update(False, state, -0.5, True, (-3.5, 2.0)), 0.)
          self.assertEqual(original, cp.to_dict())

  def test_kona_camera_handoff_keeps_stable_stop_rate_and_factory_hold(self):
    cp = kona_params(CAR.HYUNDAI_KONA_2ND_GEN, True, marked=True)
    cp.safetyConfigs[0].safetyParam = 0x1C0C
    controller = LongControl(cp)
    self.assertEqual(controller.stopping_decel_rate, 0.8)
    state = structs.CarState(vEgo=0.3).as_reader()
    for _ in range(380):
      output = controller.update(True, state, -0.5, True, (-3.5, 2.0))
    self.assertAlmostEqual(output, cp.stopAccel, delta=0.008)
    self.assertEqual(controller.update(False, state, -0.5, True, (-3.5, 2.0)), 0.)

  def test_stock_hybrid_and_unqualified_profiles_keep_default_rate(self):
    stock = kona_params(CAR.HYUNDAI_KONA_2ND_GEN, False)
    hybrid = kona_params(CAR.HYUNDAI_KONA_HEV_2ND_GEN, True)
    unqualified = kona_params(CAR.HYUNDAI_KONA_2ND_GEN, True)
    unqualified.safetyConfigs[0].safetyParam ^= 0x100
    for cp in (stock, hybrid, unqualified):
      self.assertEqual(LongControl(cp).stopping_decel_rate, 1.)
