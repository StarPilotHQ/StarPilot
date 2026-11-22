from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.canfd_stock_aol import qualified_long
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.car.hyundai.aol import native_accepts_cp, policy_for
from openpilot.starpilot.ui.wheel_feature import WheelFeature


@pytest.mark.parametrize('identity, hybrid, base', [
  (CAR.HYUNDAI_TUCSON_4TH_GEN, False, 0x08),
  (CAR.KIA_NIRO_HEV_2ND_GEN, True, 0x0A),
  (CAR.HYUNDAI_SONATA_2024, False, 0x408),
  (CAR.HYUNDAI_SONATA_HEV_2024, True, 0x40A),
  (CAR.KIA_K4_2025, False, 0x408),
])
@pytest.mark.parametrize('alternate', [False, True])
@pytest.mark.parametrize('offset', [0, 4])
def test_camera_scc_wheel_choice_survives_alpha_long(identity, hybrid, base, alternate, offset):
  fp = gen_empty_fingerprint()
  fp[offset].update({0x35: 32, 0xA0: 24, 0xEA: 24, 0x175: 24, 0x130: 16})
  fp[offset + 2].update({0x12A: 16, 0x1A0: 32})
  fp[offset][0x1AA if alternate else 0x1CF] = 16 if alternate else 8
  if hybrid:
    fp[offset][0xFA] = 8

  for alpha in (False, True):
    cp = CarInterface.get_params(identity, fp, [], alpha, False, False)
    assert cp.alphaLongitudinalAvailable and cp.openpilotLongitudinalControl == alpha
    assert not CarInterface.startup_required(cp)
    wheel = WheelFeature(SimpleNamespace(params=SimpleNamespace(get_bool=lambda key: key == 'AlwaysOnLateral'),
                                         vehicle_params=lambda cp=cp: cp, configuration_vehicle=lambda: False,
                                         configuration_longitudinal=lambda: False, longitudinal_available=lambda alpha=alpha: alpha,
                                         authority=lambda _: True))
    assert 'Toggle AOL' in wheel.choices('LKASButtonControl')
    assert 'Toggle AOL' in wheel.choices('MainCruiseButtonControl')
    if alpha:
      config = cp.safetyConfigs[offset // 4]
      assert config.safetyParam == base | (0x20 if alternate else 0) | 4
      assert qualified_long(cp)
      policy = policy_for(cp)
      config.safetyParam |= policy.safety_param_addition
      cp.alternativeExperience |= policy.alternative_experience_addition
      assert config.safetyParam == base | (0x20 if alternate else 0) | 0x804
      assert native_accepts_cp(cp, config.safetyModel.raw, config.safetyParam)
      cp.alternativeExperience = 0
      assert not qualified_long(cp, marked_only=True)
