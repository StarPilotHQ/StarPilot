"""Exact modern Tesla screen harness capability and frozen safety selection."""
from opendbc.car.tesla.values import CAR, TeslaFlags

SCREEN_WORDS = frozenset((512, 513, 1536, 1537, 768, 769, 1792, 1793))
BASE_WORDS = frozenset((0, 1, 256, 257))


def screen_tap_supported(cp):
  try:
    if (cp.brand != 'tesla' or cp.carFingerprint not in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y) or
        cp.passive or cp.dashcamOnly or cp.notCar or cp.alternativeExperience not in (0, 32) or len(cp.safetyConfigs) != 1 or
        int(cp.safetyConfigs[0].safetyModel.raw) != 10 or
        not cp.flags & TeslaFlags.HAS_VEHICLE_BUS or
        cp.flags & ~int(TeslaFlags.MISSING_DAS_SETTINGS | TeslaFlags.HAS_VEHICLE_BUS | TeslaFlags.AOL_SCREEN_BUTTON)):
      return False
    word = int(cp.safetyConfigs[0].safetyParam)
    return (word in BASE_WORDS | SCREEN_WORDS and (not word & 256 or cp.carFingerprint == CAR.TESLA_MODEL_3) and
            bool(word & 1) == bool(cp.openpilotLongitudinalControl) and
            bool(word & 512) == bool(cp.flags & TeslaFlags.AOL_SCREEN_BUTTON))
  except (AttributeError, ValueError, TypeError, IndexError):
    return False


modern_screen_supported = screen_tap_supported


def screen_qualified(cp, *, marked_only=False):
  return bool(screen_tap_supported(cp) and int(cp.safetyConfigs[0].safetyParam) in SCREEN_WORDS and
              cp.alternativeExperience in ((32,) if marked_only else (0, 32)))


def apply_screen_button(cp, requested, brake_disengage):
  if not screen_tap_supported(cp):
    return
  base = int(cp.safetyConfigs[0].safetyParam) & 257
  cp.flags &= ~int(TeslaFlags.AOL_SCREEN_BUTTON)
  cp.safetyConfigs[0].safetyParam = base
  if requested:
    cp.flags |= TeslaFlags.AOL_SCREEN_BUTTON.value
    cp.safetyConfigs[0].safetyParam = base | 512 | (1024 if brake_disengage else 0)
