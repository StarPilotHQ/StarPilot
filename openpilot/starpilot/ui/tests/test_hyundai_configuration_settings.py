from types import SimpleNamespace
import unittest

from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.car.hyundai.settings import (
  configuration_media_supported, configuration_wheel_policy, supports_long_configuration,
)


class HyundaiConfigurationSettingsTests(unittest.TestCase):
  def test_saved_ioniq6_declares_editors_without_live_authority(self):
    cp = SimpleNamespace(brand='hyundai', carFingerprint=CAR.HYUNDAI_IONIQ_6, notCar=False, passive=False,
                         dashcamOnly=False, alphaLongitudinalAvailable=False, openpilotLongitudinalControl=False,
                         pcmCruise=True)
    before = vars(cp).copy()
    self.assertTrue(supports_long_configuration(cp))
    self.assertTrue(configuration_media_supported(cp))
    policy = configuration_wheel_policy(cp)
    assert policy is not None
    self.assertFalse(policy.fixed_cruise_buttons)
    self.assertTrue(policy.distance_pause_only)
    self.assertFalse(policy.runtime_supported)
    self.assertEqual(vars(cp), before)
    cp.dashcamOnly = True
    self.assertFalse(supports_long_configuration(cp))
    self.assertFalse(configuration_media_supported(cp))
    self.assertIsNone(configuration_wheel_policy(cp))

  def test_other_identity_does_not_borrow_ioniq6_media_or_policy(self):
    cp = SimpleNamespace(brand='hyundai', carFingerprint=CAR.HYUNDAI_IONIQ_5, notCar=False, passive=False,
                         dashcamOnly=False, alphaLongitudinalAvailable=True)
    self.assertTrue(supports_long_configuration(cp))
    self.assertFalse(configuration_media_supported(cp))
    self.assertIsNone(configuration_wheel_policy(cp))
