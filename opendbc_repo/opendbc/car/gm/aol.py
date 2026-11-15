"""Independent lateral axes for exact source-qualified Bolt and Volt owners."""

import math
from functools import lru_cache

from capnp.lib.capnp import _DynamicStructBuilder, _DynamicStructReader
from opendbc.car.gm.cancel_remap import aol_enabled, valid_alternative_experience

from opendbc.car.structs import CarParams
from opendbc.car.gm.values import (malibu_hybrid_profile, volt_cc_pedal_profile, control_flags,
  camera_acc_pedal_profile, CAR, GMFlags, GMSafetyFlags, is_bolt_cc_profile, is_bolt_pedal_profile,
  is_ordinary_ascm_profile, is_ordinary_camera_profile, is_ordinary_sdgm_profile, is_bolt_euv_longitudinal,
  is_volt_gateway_profile, is_volt_cc_profile, is_silverado_cc_stock_profile,
  is_volt_ascm_longitudinal, is_volt_camera_stock, is_volt_camera_longitudinal,
  is_volt_camera_removed, is_volt_sdgm_profile, is_ordinary_cc_profile, is_conventional_cc_pedal_profile,
)
from opendbc.car.gm.lateral import lane_centering_supported

GM_AOL_ALTERNATIVE_EXPERIENCE = 32
GM_BASE_GATEWAY_IDS = frozenset((CAR.GMC_ACADIA, CAR.BUICK_LACROSSE, CAR.CADILLAC_ESCALADE,
                                 CAR.CADILLAC_ESCALADE_ESV, CAR.CADILLAC_ESCALADE_ESV_2019, CAR.CHEVROLET_SUBURBAN))
GM_AOL_WORDS = (frozenset(range(0xE600, 0xE608)) | frozenset(range(0xE610, 0xE618))) | frozenset(
  start + index for start in (0xE200, 0xE210, 0xE220, 0xE240, 0xE260,
                                                                   0xE300, 0xE310, 0xE320, 0xE340, 0xE360,
                                                                   0xE400, 0xE410, 0xE420, 0xE440, 0xE460,
                                                                   0xE500, 0xE510, 0xE520, 0xE540, 0xE560) for index in range(4)) | frozenset((
  *range(0xE700, 0xE704), *range(0xE710, 0xE714),
  0, 16, 0x80, 0xE100, 0xE101, 0xE102, 0xE103, 0xE110, 0xE111, 0xE112, 0xE113,
  0xD100, 0xD101, 0xD102, 0xD103, 0xD104, 0xD105, 0xD106, 0xD107, 0xD108, 0xD109, 0xD10A,
  0xD110, 0xD111, 0xD112, 0xD113, 0xD114, 0xD115, 0xD116, 0xD117, 0xD118, 0xD119, 0xD11A,
  0xE900, 0x1001, 0x1401, 0x3001, 0x3401, 0x1003, 0x1403,
  0x201, 0x601, 0xA01, 0xE01, 0x203, 0x603, 0xA03, 0xE03,
  5, 7, 20, 0xBD, 0x9D, 0x19D, 0x1CD,
  0x205, 0x605, 0xA05, 0xE05, 0x4207, 0x4607, 0x4A07, 0x4E07,
  0x4004, 0xC004, 0x4084, 0xC084, 0x4007, 0x1005, 0x1405, 0x5007, 0x5407,
  0x4287, 0x4687, 0x4A87, 0x4E87, 0x4087,
  0x5087, 0x5487,
  0xC1D1, 0xC1D3,
  0xC110, 0xC111, 0xC120, 0xC121, 0xC130, 0xC131, 0xC140, 0xC141,
  0xE800, 0xE801, 0xE802, 0xE803, 0xE804, 0xE805,
  0xC150, 0xC151, 0xC160, 0xC161, 0xC162, 0xC163, 0xC170, 0xC171, 0xC172, 0xC173, 0xC180, 0xC181, 0xC182, 0xC183, 0xC184, 0xC185, 0xC186, 0xC187,
))


def _qualified_gm_uncached(cp) -> bool:
  try:
    if (cp.brand != 'gm' or cp.notCar or cp.passive or cp.dashcamOnly or
        cp.steerControlType != CarParams.SteerControlType.torque or
        len(cp.safetyConfigs) != 1 or cp.safetyConfigs[0].safetyModel != CarParams.SafetyModel.gm or
        int(cp.safetyConfigs[0].safetyParam) not in GM_AOL_WORDS or
        not valid_alternative_experience(cp) or
        cp.lateralTuning.which() not in ('pid', 'torque')):
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
    if volt_cc_pedal_profile(cp) is not None or camera_acc_pedal_profile(cp) is not None:
      return True
    if cp.carFingerprint in GM_BASE_GATEWAY_IDS:
      return (cp.networkLocation == CarParams.NetworkLocation.gateway and
              cp.transmissionType == CarParams.TransmissionType.automatic and
              not cp.pcmCruise and not cp.alphaLongitudinalAvailable and
              not cp.radarUnavailable and control_flags(cp) == 0 and
              (int(cp.safetyConfigs[0].safetyParam) == 0 and
               (cp.openpilotLongitudinalControl or cp.carFingerprint == CAR.BUICK_LACROSSE) or
               int(cp.safetyConfigs[0].safetyParam) == 0x80 and
               cp.carFingerprint == CAR.BUICK_LACROSSE and cp.openpilotLongitudinalControl))
    if is_ordinary_camera_profile(cp, longitudinal=cp.openpilotLongitudinalControl):
      return True
    if is_silverado_cc_stock_profile(cp) or is_ordinary_cc_profile(cp) or is_conventional_cc_pedal_profile(cp) or malibu_hybrid_profile(cp) is not None:
      return True
    if is_ordinary_sdgm_profile(cp, longitudinal=False) or is_ordinary_sdgm_profile(cp, longitudinal=True):
      return True
    if is_ordinary_ascm_profile(cp, longitudinal=False) or is_ordinary_ascm_profile(cp, longitudinal=True):
      return True
    if ((is_bolt_cc_profile(cp) and control_flags(cp) == int(GMFlags.CC_LONG)) or
        is_bolt_pedal_profile(cp) or is_bolt_pedal_profile(cp, stock_only=True) or
        is_bolt_euv_longitudinal(cp)):
      return True
    if (is_volt_gateway_profile(cp) or is_volt_cc_profile(cp) or is_volt_ascm_longitudinal(cp) or
        is_volt_camera_stock(cp) or is_volt_camera_longitudinal(cp) or is_volt_camera_removed(cp) or
        is_volt_sdgm_profile(cp) or is_volt_sdgm_profile(cp, longitudinal=True)):
      return True
    if cp.carFingerprint == CAR.CHEVROLET_VOLT_ASCM:
      return lane_centering_supported(cp)
    return (cp.carFingerprint in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023) and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and control_flags(cp) == 0 and
            cp.pcmCruise and not cp.openpilotLongitudinalControl and cp.radarUnavailable and
            int(cp.safetyConfigs[0].safetyParam) == int(GMSafetyFlags.HW_CAM | GMSafetyFlags.EV))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


# Qualification depends only on the exact finalized CarParams value. Cache no
# live vehicle state, native grant, source timestamp, or output eligibility.
@lru_cache(maxsize=8)
def _qualified_gm_bytes(key: bytes) -> bool:
  with CarParams.from_bytes(key) as cp:
    return _qualified_gm_uncached(cp)


def qualified_gm(cp) -> bool:
  cp_type = type(cp)
  if cp_type is _DynamicStructBuilder or cp_type is _DynamicStructReader:
    try:
      if cp.schema == CarParams.schema:
        if cp.brand != 'gm':
          return False
        snapshot = cp.copy() if cp_type is _DynamicStructBuilder else cp.as_builder()
        key = snapshot.to_bytes()
        if len(key) <= 64 * 1024:
          return _qualified_gm_bytes(key)
    except Exception:
      pass
  return _qualified_gm_uncached(cp)


def lateral_request(cp, cc) -> bool:
  """Only a finalized independent-axis owner may relax its cruise-active lateral gate."""
  return (qualified_gm(cp) and aol_enabled(cp) and cc.latActive)


def native_bootstrap_supported(cp) -> bool:
  """Finalized GM configuration may precede selfdrived readiness."""
  try:
    return bool(qualified_gm(cp))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False
