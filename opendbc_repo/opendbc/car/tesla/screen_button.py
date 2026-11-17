"""Exact modern Tesla screen harness capability and frozen safety selection."""
from opendbc.car.tesla.values import CAR, TeslaFlags

SCREEN_WORDS = frozenset((512, 513, 1536, 1537, 768, 769, 1792, 1793))
BASE_WORDS = frozenset((0, 1, 256, 257))
ORDINARY_WORDS = frozenset((2048, 2049, 2304, 2305, 3072, 3073, 3328, 3329))
AOL_WORDS = SCREEN_WORDS | ORDINARY_WORDS


def modern_aol_supported(cp):
  try:
    if (cp.brand != 'tesla' or cp.carFingerprint not in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y) or
        cp.passive or cp.dashcamOnly or cp.notCar or cp.alternativeExperience not in (0, 32) or len(cp.safetyConfigs) != 1 or
        int(cp.safetyConfigs[0].safetyModel.raw) != 10 or
        cp.flags & ~int(TeslaFlags.MISSING_DAS_SETTINGS | TeslaFlags.HAS_VEHICLE_BUS | TeslaFlags.AOL_SCREEN_BUTTON)):
      return False
    word = int(cp.safetyConfigs[0].safetyParam)
    return (word in BASE_WORDS | AOL_WORDS and (not word & 256 or cp.carFingerprint == CAR.TESLA_MODEL_3) and
            bool(word & 1) == bool(cp.openpilotLongitudinalControl) and
            bool(word & 512) == bool(cp.flags & TeslaFlags.AOL_SCREEN_BUTTON) and
            (not word & 512 or bool(cp.flags & TeslaFlags.HAS_VEHICLE_BUS)))
  except (AttributeError, ValueError, TypeError, IndexError):
    return False


def screen_tap_supported(cp):
  return bool(modern_aol_supported(cp) and cp.flags & TeslaFlags.HAS_VEHICLE_BUS)


modern_screen_supported = screen_tap_supported


def screen_qualified(cp, *, marked_only=False):
  return bool(screen_tap_supported(cp) and int(cp.safetyConfigs[0].safetyParam) in SCREEN_WORDS and
              cp.alternativeExperience in ((32,) if marked_only else (0, 32)))


def modern_aol_qualified(cp, *, marked_only=False):
  return bool(modern_aol_supported(cp) and int(cp.safetyConfigs[0].safetyParam) in AOL_WORDS and
              cp.alternativeExperience in ((32,) if marked_only else (0, 32)))


def apply_modern_aol(cp, requested, screen, brake_disengage):
  if not modern_aol_supported(cp):
    return
  use_screen = requested and screen and screen_tap_supported(cp)
  base = int(cp.safetyConfigs[0].safetyParam) & 257
  cp.flags &= ~int(TeslaFlags.AOL_SCREEN_BUTTON)
  cp.safetyConfigs[0].safetyParam = base
  if requested:
    cp.safetyConfigs[0].safetyParam = base | (512 if use_screen else 2048) | (1024 if brake_disengage else 0)
    if use_screen:
      cp.flags |= TeslaFlags.AOL_SCREEN_BUTTON.value


def apply_screen_button(cp, requested, brake_disengage):
  if not screen_tap_supported(cp):
    return
  base = int(cp.safetyConfigs[0].safetyParam) & 257
  cp.flags &= ~int(TeslaFlags.AOL_SCREEN_BUTTON)
  cp.safetyConfigs[0].safetyParam = base
  if requested:
    cp.flags |= TeslaFlags.AOL_SCREEN_BUTTON.value
    cp.safetyConfigs[0].safetyParam = base | 512 | (1024 if brake_disengage else 0)
