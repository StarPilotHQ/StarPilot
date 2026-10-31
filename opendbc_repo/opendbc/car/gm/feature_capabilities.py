"""Optional planner owners follow the finalized GM driving ownership."""

from opendbc.car.config_cache import cached_config
from opendbc.car.gm.lateral import lane_centering_supported
from opendbc.car.gm.suburban import stopping_decel_rate
from opendbc.car.gm.values import (malibu_hybrid_profile, camera_acc_pedal_profile, control_flags, CAR, GMFlags, GMSafetyFlags,
                                     is_bolt_cc_profile, is_bolt_euv_longitudinal,
                                  is_bolt_pedal_profile, is_volt_cc_longitudinal, is_volt_longitudinal,
                                  uses_camera_stock_controls, is_ordinary_ascm_profile, is_ordinary_camera_profile,
                                  is_ordinary_sdgm_profile, is_ordinary_cc_profile, is_conventional_cc_pedal_profile, volt_cc_pedal_profile)
from opendbc.car.structs import CarParams


def _healthy(cp) -> bool:
  return (cp.brand == 'gm' and not (cp.passive or cp.dashcamOnly or cp.notCar) and
          len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm)


def longitudinal_supported(cp) -> bool:
  """Reuse exact source-qualified control owners, including conventional cruise."""
  try:
    if not _healthy(cp) or not cp.openpilotLongitudinalControl or cp.pcmCruise:
      return False
    return _longitudinal_owner(cp)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


@cached_config
def _longitudinal_owner(cp) -> bool:
  return (volt_cc_pedal_profile(cp) is not None or camera_acc_pedal_profile(cp) is not None or is_ordinary_camera_profile(cp, longitudinal=True) or
            is_ordinary_cc_profile(cp) or is_conventional_cc_pedal_profile(cp) or malibu_hybrid_profile(cp) is not None or
            is_ordinary_ascm_profile(cp, longitudinal=True) or is_ordinary_sdgm_profile(cp, longitudinal=True)
        or is_volt_longitudinal(cp) or is_volt_cc_longitudinal(cp) or is_bolt_euv_longitudinal(cp) or
            is_bolt_pedal_profile(cp) or
            (control_flags(cp) == int(GMFlags.CC_LONG) and is_bolt_cc_profile(cp)) or
            (control_flags(cp) == 0 and stopping_decel_rate(cp) is not None))


def display_supported(cp) -> bool:
  """Stock and disabled-long sessions may observe limits without speed authority."""
  try:
    if not _healthy(cp):
      return False
    if longitudinal_supported(cp) or lane_centering_supported(cp):
      return True
    if cp.openpilotLongitudinalControl:
      return False
    if is_bolt_pedal_profile(cp, stock_only=True):
      return True
    if control_flags(cp) == int(GMFlags.CC_LONG) and is_bolt_cc_profile(cp):
      return True
    factory_stock = (cp.carFingerprint in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023) and
                     cp.radarUnavailable and int(cp.safetyConfigs[0].safetyParam) == int(GMSafetyFlags.HW_CAM | GMSafetyFlags.EV))
    stock_words = (int(GMSafetyFlags.HW_CAM), int(GMSafetyFlags.HW_CAM | GMSafetyFlags.EV))
    return (cp.pcmCruise and cp.networkLocation == CarParams.NetworkLocation.fwdCamera and control_flags(cp) == 0 and
            int(cp.safetyConfigs[0].safetyParam) in stock_words and (uses_camera_stock_controls(cp) or factory_stock))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False
