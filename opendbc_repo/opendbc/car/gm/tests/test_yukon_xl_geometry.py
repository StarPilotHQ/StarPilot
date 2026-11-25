from unittest.mock import patch

import pytest

from opendbc.car import car_helpers, gen_empty_fingerprint, scale_rot_inertia, structs
from opendbc.car.gm.values import CAR
from opendbc.car.vin import VIN_UNKNOWN


@pytest.mark.parametrize('candidate,vin,xl', [
  (CAR.GMC_YUKON_CC, '1GKS2GKC0JR000001', True),
  (CAR.GMC_YUKON_CC, '1GKS2BKC0JR000001', False),
  (CAR.GMC_YUKON_CC, '1GKS2GKC0KR000001', False),
  (CAR.GMC_YUKON_CC, VIN_UNKNOWN, False),
  (CAR.GMC_YUKON, '1GKS2GKC0JR000001', False),
  (CAR.BUICK_LACROSSE_ASCM_19US, '1GKS2GKC0JR000001', False),
])
def test_manual_profile_retains_its_control_config_with_vin_geometry(candidate, vin, xl):
  fp = gen_empty_fingerprint()
  with patch.object(car_helpers, 'fingerprint', return_value=(None, fp, vin, [], structs.CarParams.FingerprintSource.can, True)):
    cp = car_helpers.get_car(lambda **_: [], lambda _: None, lambda _: None, False, True, forced_candidate=candidate).CP

  assert cp.carFingerprint == candidate and cp.fingerprintSource == structs.CarParams.FingerprintSource.fixed
  baseline = car_helpers.interfaces[candidate].get_params(candidate, fp, [], False, True, False, auto_pedal=False)
  assert cp.wheelbase == pytest.approx(3.302 if xl else baseline.wheelbase)
  assert cp.centerToFront == pytest.approx(cp.wheelbase * candidate.config.specs.centerToFrontRatio)
  assert cp.rotationalInertia == pytest.approx(scale_rot_inertia(cp.mass, cp.wheelbase))
  assert cp.lateralTuning.to_dict() == baseline.lateralTuning.to_dict()
  assert cp.safetyConfigs[0].to_dict() == baseline.safetyConfigs[0].to_dict()
  assert cp.openpilotLongitudinalControl == baseline.openpilotLongitudinalControl
  assert cp.steerRatio == baseline.steerRatio
