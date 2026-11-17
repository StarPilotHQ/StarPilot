from opendbc.car.gm.values import gm_control_word, camera_acc_pedal_profile, volt_cc_pedal_profile
"""Exact finalized GM configurations for shared lateral preferences."""
import math
from opendbc.car.structs import CarParams
from opendbc.car.gm.values import (malibu_hybrid_profile, control_flags, CAR, GMSafetyFlags, is_volt_gateway_profile,
                                  is_volt_ascm_longitudinal, is_volt_cc_profile, is_volt_sdgm_profile, is_volt_camera_removed,
                                      is_ordinary_ascm_profile, is_ordinary_camera_profile, is_ordinary_sdgm_profile, is_ordinary_cc_profile,
                                   is_ordinary_cc_stock_profile,
                                      is_conventional_cc_pedal_profile)


def lane_centering_supported(cp) -> bool:
  try:
    if (cp.brand != 'gm' or cp.passive or cp.dashcamOnly or cp.notCar or
        cp.steerControlType != CarParams.SteerControlType.torque or cp.lateralTuning.which() not in ('pid', 'torque')):
      return False
    if cp.lateralTuning.which() == 'torque':
      tune = cp.lateralTuning.torque
      if (not all(math.isfinite(value) for value in (tune.latAccelFactor, tune.latAccelOffset, tune.friction)) or
          tune.latAccelFactor <= 0 or tune.friction < 0):
        return False
    else:
      tune = cp.lateralTuning.pid
      if not all(math.isfinite(value) for value in (*tune.kpBP, *tune.kpV, *tune.kiBP, *tune.kiV, tune.kf)):
        return False
    if camera_acc_pedal_profile(cp) is not None or is_ordinary_camera_profile(cp, longitudinal=cp.openpilotLongitudinalControl):
      return True
    if is_ordinary_cc_profile(cp) or is_ordinary_cc_stock_profile(cp) or is_conventional_cc_pedal_profile(cp) or malibu_hybrid_profile(cp) is not None:
      return True
    if (is_ordinary_ascm_profile(cp, longitudinal=cp.openpilotLongitudinalControl)
        or is_ordinary_sdgm_profile(cp, longitudinal=cp.openpilotLongitudinalControl)):
      return True
    if is_volt_camera_removed(cp) or is_volt_sdgm_profile(cp) or is_volt_sdgm_profile(cp, longitudinal=True):
      return True
    if volt_cc_pedal_profile(cp) is not None or is_volt_gateway_profile(cp) or is_volt_ascm_longitudinal(cp) or is_volt_cc_profile(cp):
      return True
    if (len(cp.safetyConfigs) != 1 or cp.safetyConfigs[0].safetyModel != CarParams.SafetyModel.gm or
        cp.networkLocation != CarParams.NetworkLocation.fwdCamera or control_flags(cp) != 0):
      return False
    word = gm_control_word(cp)
    if cp.carFingerprint == CAR.CHEVROLET_VOLT_CAMERA:
      return ((word == 5 and cp.pcmCruise and not cp.openpilotLongitudinalControl) or
              (word in (0x4007, 0x4087) and not cp.pcmCruise and cp.openpilotLongitudinalControl and cp.alphaLongitudinalAvailable))
    if cp.carFingerprint == CAR.CHEVROLET_VOLT_ASCM and cp.pcmCruise and not cp.openpilotLongitudinalControl:
      required = int(GMSafetyFlags.EV | GMSafetyFlags.HW_CAM | GMSafetyFlags.ASCM_INTERCEPT)
      optional = int(GMSafetyFlags.ASCM_BRAKE_C9 | GMSafetyFlags.ASCM_RADAR)
      return (word & required == required and not word & ~(required | optional) and
              bool(word & int(GMSafetyFlags.ASCM_RADAR)) is not cp.radarUnavailable)
    return False
  except (AttributeError, IndexError, TypeError, ValueError):
    return False
