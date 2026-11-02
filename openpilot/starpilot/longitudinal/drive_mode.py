"""Vehicle drive mode (Eco/Normal/Sport) selects the global acceleration and deceleration profiles."""

KEY = 'DriveModeProfiles'
SOURCE_AGE_NS = 500_000_000
PROFILES = {
  'eco': ('eco', 'eco'),
  'normal': ('standard', 'standard'),
  'sport': ('sport_plus', 'sport'),
}


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


def apply(document: dict, drive_mode: str | None) -> dict:
  if drive_mode not in PROFILES:
    return document
  acceleration, deceleration = PROFILES[drive_mode]
  return {**document, 'selectedAccelerationProfile': acceleration, 'selectedDecelerationProfile': deceleration}
