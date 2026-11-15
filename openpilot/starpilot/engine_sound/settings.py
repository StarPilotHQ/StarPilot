from openpilot.starpilot.engine_sound.profiles import DEFAULT_PROFILE, PROFILES

ENABLED_KEY = 'EngineSound'
PROFILE_KEY = 'EngineSoundProfile'
VOLUME_KEY = 'EngineSoundVolume'
DEFAULT_VOLUME = 60
VOLUME_STEP = 10


def enabled(params) -> bool:
  return params.get_bool(ENABLED_KEY)


def profile_name(params) -> str:
  name = params.get(PROFILE_KEY)
  name = name.decode() if isinstance(name, bytes) else name
  return name if name in PROFILES else DEFAULT_PROFILE


def volume(params) -> float:
  try:
    return min(max(int(params.get(VOLUME_KEY)), 0), 100) / 100.0
  except (TypeError, ValueError):
    return DEFAULT_VOLUME / 100.0
