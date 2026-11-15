"""Frozen Model 3/Y cooperative steering selection."""
from opendbc.car.tesla.screen_button import SPEED_SYNC, speed_sync_allowed
from opendbc.car.tesla.values import CAR, TeslaSafetyFlags, TeslaFlags


def supported(cp):
  try:
    if (cp.brand != 'tesla' or cp.carFingerprint not in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y) or cp.passive or cp.dashcamOnly or cp.notCar or
        cp.alternativeExperience not in (0, 32) or len(cp.safetyConfigs) != 1 or int(cp.safetyConfigs[0].safetyModel.raw) != 10 or
        cp.flags & ~int(TeslaFlags.MISSING_DAS_SETTINGS | TeslaFlags.HAS_VEHICLE_BUS | TeslaFlags.AOL_SCREEN_BUTTON)):
      return False
    word = int(cp.safetyConfigs[0].safetyParam)
    # Scroll speed sync rides on top of the cooperative word (e.g. 4353 = 4096 | 256 | 1); it is stripped before the check.
    if word & SPEED_SYNC:
      if not speed_sync_allowed(cp, word):
        return False
      word &= ~SPEED_SYNC
    return (word in (0, 1, 256, 257, 512, 513, 768, 769, 1536, 1537, 1792, 1793, 2048, 2049, 2304, 2305, 3072, 3073, 3328, 3329) and
            bool(word & 1) == bool(cp.openpilotLongitudinalControl))
  except (AttributeError, TypeError, ValueError, IndexError):
    return False


def prepare(cp, requested):
  if supported(cp):
    cp.safetyConfigs[0].safetyParam &= ~int(TeslaSafetyFlags.COOP_STEERING)
    if requested:
      cp.safetyConfigs[0].safetyParam |= int(TeslaSafetyFlags.COOP_STEERING)
  return cp
