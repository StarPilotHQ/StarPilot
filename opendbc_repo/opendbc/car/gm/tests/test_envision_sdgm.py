"""PR232 retrofit identity is isolated from ordinary GM configurations."""
import unittest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import CAR, is_ordinary_sdgm_profile
from opendbc.car.gm.fingerprints import FINGERPRINTS
from opendbc.car.gm.aol import qualified_gm


def envision_params(alpha=False):
  fingerprint = gen_empty_fingerprint()
  fingerprint[0] = FINGERPRINTS[CAR.BUICK_ENVISION_SDGM][0]
  return CarInterface.get_params(CAR.BUICK_ENVISION_SDGM, fingerprint, [], alpha, False, False)


class TestEnvisionSdgm(unittest.TestCase):
  def test_exact_stock_profile(self):
    for alpha in (False, True):
      cp = envision_params(alpha)
      self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xE900)
      self.assertTrue(cp.pcmCruise)
      self.assertFalse(cp.openpilotLongitudinalControl)
      self.assertFalse(cp.alphaLongitudinalAvailable)
      self.assertTrue(is_ordinary_sdgm_profile(cp, longitudinal=False))
      self.assertTrue(qualified_gm(cp))
      CarInterface(cp)

  def test_owner_cannot_be_borrowed(self):
    for identity in CAR:
      if identity == CAR.BUICK_ENVISION_SDGM:
        continue
      cp = envision_params()
      cp.carFingerprint = identity
      self.assertFalse(qualified_gm(cp), str(identity))
    for word in (0x1001, 0x1003, 0xE901, 0x3001):
      cp = envision_params()
      cp.safetyConfigs[0].safetyParam = word
      self.assertFalse(qualified_gm(cp), hex(word))

  def test_analog_source_and_custom_frame_length(self):
    from opendbc.car.gm.carstate import CarState
    from opendbc.car import Bus
    from opendbc.can import CANPacker
    from opendbc.car.gm.values import DBC
    cp = envision_params()
    state = CarState(cp)
    parser = state.get_can_parsers(cp)[Bus.pt]
    self.assertIn('ECMAcceleratorPos', parser.vl)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    self.assertEqual(len(packer.make_can_msg('ECMAcceleratorPos', 0, {'BrakePedalPos': 10})[1]), 3)
