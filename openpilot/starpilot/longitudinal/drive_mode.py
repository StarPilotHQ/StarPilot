"""Vehicle drive mode (Eco/Normal/Sport) selects the global acceleration and deceleration profiles."""

KEY = 'DriveModeProfiles'
ACCELERATION_KEY = 'DriveModeAcceleration'
DECELERATION_KEY = 'DriveModeDeceleration'
SOURCE_AGE_NS = 500_000_000
PROFILES = {
  'eco': ('eco', 'eco'),
  'normal': ('standard', 'standard'),
  'sport': ('sport_plus', 'sport'),
}


def capable(cp) -> bool:
  return cp is not None and str(cp.carFingerprint) == 'HYUNDAI_IONIQ_6' and not cp.notCar


def read_enabled(params) -> tuple[bool, bool]:
  from openpilot.starpilot.saved_source import read_saved
  if read_saved(params, KEY, 8) != (b'1', True):
    return False, False
  return tuple(read_saved(params, key, 8) in ((None, True), (b'1', True)) for key in (ACCELERATION_KEY, DECELERATION_KEY))


def observe(sm, *, now_ns: int) -> str | None:
  try:
    if not sm.seen['starpilotCarState'] or not sm.valid['starpilotCarState']:
      return None
    if not 0 <= now_ns - int(sm.logMonoTime['starpilotCarState']) <= SOURCE_AGE_NS:
      return None
    state = sm['starpilotCarState']
    return 'eco' if state.ecoGear else 'sport' if state.sportGear else 'normal'
  except (AttributeError, KeyError, TypeError, ValueError):
    return None


def apply(document: dict, drive_mode: str | None, *, acceleration: bool = True, deceleration: bool = True) -> dict:
  if drive_mode not in PROFILES:
    return document
  selected_acceleration, selected_deceleration = PROFILES[drive_mode]
  if acceleration:
    document = {**document, 'selectedAccelerationProfile': selected_acceleration}
  if deceleration:
    document = {**document, 'selectedDecelerationProfile': selected_deceleration}
  return document
