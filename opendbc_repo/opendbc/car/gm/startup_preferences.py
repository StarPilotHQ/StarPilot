from opendbc.car.gm.values import gm_control_word, is_volt_one_pedal, camera_acc_pedal_profile, ALT_ACCS, volt_cc_pedal_profile
"""Finalized GM speed-control choices; existing native envelopes are retained."""

from opendbc.car.gm.values import (is_malibu_cc_f1_profile, MALIBU_CC_F1_STOCK_WORD, is_volt_gateway_profile, is_volt_cc_profile,
                                  is_ordinary_cc_profile, is_ordinary_cc_stock_profile, is_ordinary_camera_profile,
                                  is_conventional_cc_pedal_profile, is_silverado_cc_pedal_profile, SILVERADO_CC_PEDAL_WORDS,
                                  CONVENTIONAL_CC_PEDAL_STOCK_WORDS, GMFlags)
from opendbc.car.gm.values import (is_volt_ascm_longitudinal, is_volt_camera_longitudinal, is_volt_sdgm_profile,
                                  is_volt_camera_removed, is_lacrosse_gateway_profile, GMSafetyFlags)


def disable_long_supported(cp) -> bool:
  from opendbc.car.gm.values import malibu_hybrid_profile
  if malibu_hybrid_profile(cp) is not None:
    return True
  return (volt_cc_pedal_profile(cp) is not None or camera_acc_pedal_profile(cp) is not None or is_conventional_cc_pedal_profile(cp) or
          is_ordinary_cc_profile(cp) or is_ordinary_cc_stock_profile(cp) or
          is_ordinary_camera_profile(cp) or is_ordinary_camera_profile(cp, longitudinal=True) or
          is_volt_gateway_profile(cp) or is_volt_cc_profile(cp) or
          is_volt_ascm_longitudinal(cp) or is_volt_camera_longitudinal(cp) or is_volt_sdgm_profile(cp, longitudinal=True) or
          is_volt_camera_removed(cp, longitudinal=True) or is_lacrosse_gateway_profile(cp))


def prepare_disable_longitudinal(cp, requested: bool) -> None:
  from opendbc.car.gm.values import malibu_hybrid_profile
  hybrid = malibu_hybrid_profile(cp)
  if requested and hybrid is not None:
    cp.openpilotLongitudinalControl = False
    cp.pcmCruise = True
    cp.autoResumeSng = False
    cp.safetyConfigs[0].safetyParam = 0xE804 + int(hybrid.removed)
    return
  if requested and is_ordinary_camera_profile(cp, longitudinal=True):
    cp.safetyConfigs[0].safetyParam = 0xC172 if cp.flags & GMFlags.NO_CAMERA else 0xC171
    cp.openpilotLongitudinalControl = False
    cp.pcmCruise = True
    cp.minEnableSpeed = -1. if cp.carFingerprint in ALT_ACCS else 5. / 3.6
    return
  if requested and is_malibu_cc_f1_profile(cp):
    cp.openpilotLongitudinalControl = False
    cp.pcmCruise = True
    cp.autoResumeSng = False
    cp.safetyConfigs[0].safetyParam = MALIBU_CC_F1_STOCK_WORD
    return
  if requested and (is_ordinary_cc_profile(cp) or is_ordinary_cc_stock_profile(cp)):
    cp.openpilotLongitudinalControl = False
    cp.pcmCruise = True
    cp.autoResumeSng = False
    cp.flags &= ~int(GMFlags.CC_LONG)
    cp.safetyConfigs[0].safetyParam = int(GMSafetyFlags.NO_ACC)
    cp.minEnableSpeed = -1.
    return
  cc_profile = volt_cc_pedal_profile(cp)
  if requested and cc_profile is not None:
    cp.safetyConfigs[0].safetyParam = 0xE610 + 4 * int(cc_profile.radar) + 2 * int(cc_profile.removed) + int(cc_profile.brake_source.value == "F1")
    cp.openpilotLongitudinalControl = False
    cp.pcmCruise = False
    cp.autoResumeSng = False
    return
  profile = camera_acc_pedal_profile(cp)
  if requested and profile is not None:
    stock_words = {0xE100: 0xE110, 0xE101: 0xE111, 0xE102: 0xE112, 0xE103: 0xE113}
    if profile.volt:
      base = {"camera": 0xE210, "gateway": 0xE310, "ascm": 0xE410, "sdgm": 0xE510}[profile.topology]
      starts = (base - 0x10, base + 0x10, base + 0x30, base + 0x50)
      stock_words.update({start + index: base + index for start in starts for index in range(4)})
    cp.safetyConfigs[0].safetyParam = stock_words.get(int(cp.safetyConfigs[0].safetyParam), int(cp.safetyConfigs[0].safetyParam))
    cp.openpilotLongitudinalControl = False
    cp.pcmCruise = profile.topology == "camera"
    cp.autoResumeSng = False
    cp.minEnableSpeed = -1. if profile.volt or cp.carFingerprint in ALT_ACCS else 5. / 3.6
    return
  if requested and is_volt_one_pedal(cp):
    cp.safetyConfigs[0].safetyParam = gm_control_word(cp)
  if requested and is_volt_camera_removed(cp, longitudinal=True):
    cp.safetyConfigs[0].safetyParam = 0xC150
    cp.openpilotLongitudinalControl = False
    cp.pcmCruise = True
    return
  if requested and (is_volt_ascm_longitudinal(cp) or is_volt_camera_longitudinal(cp) or is_volt_sdgm_profile(cp, longitudinal=True)):
    # Withdraw into the corresponding existing camera stock owner before CI construction.
    cp.safetyConfigs[0].safetyParam &= ~int(GMSafetyFlags.HW_CAM_LONG | GMSafetyFlags.VOLT_LONG | GMSafetyFlags.VOLT_AUTO_HOLD)
    cp.openpilotLongitudinalControl = False
    cp.pcmCruise = True
    return
  if requested and disable_long_supported(cp):
    if is_lacrosse_gateway_profile(cp):
      cp.safetyConfigs[0].safetyParam = 0
    silverado = is_silverado_cc_pedal_profile(cp)
    ordinary_pedal = is_conventional_cc_pedal_profile(cp) and not silverado
    cp.openpilotLongitudinalControl = False
    if silverado:
      cp.safetyConfigs[0].safetyParam = SILVERADO_CC_PEDAL_WORDS[1][bool(cp.flags & GMFlags.NO_CAMERA)]
    elif ordinary_pedal:
      cp.safetyConfigs[0].safetyParam = CONVENTIONAL_CC_PEDAL_STOCK_WORDS[bool(cp.flags & GMFlags.NO_CAMERA)]


def gateway_sources_current(source_ns, now_ns: int) -> bool:
  # The selected brake is supplied by the exact finalized gateway profile.
  limits = (300_000_000, 300_000_000, 100_000_000, 100_000_000, 100_000_000, 100_000_000, 100_000_000)
  return (isinstance(source_ns, tuple) and len(source_ns) == len(limits) and
          all(source > 0 and 0 <= now_ns - source <= limit for source, limit in zip(source_ns, limits, strict=True)))
