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


def _commit(params, key: str, raw: bytes, expected: bytes | None) -> bool:
  from openpilot.starpilot.saved_document import commit_exact
  result = commit_exact(params, key=key, max_bytes=64, raw=raw, expected=expected, authorized=lambda: True,
                        temp_prefix=".engine-sound-")
  return result.committed and result.verified


def toggle(params) -> bool | None:
  """Flip Engine Sounds; returns the new state, or None if the saved value could not be replaced."""
  from openpilot.starpilot.saved_source import read_saved
  raw, readable = read_saved(params, ENABLED_KEY, 8)
  if not readable or raw not in (None, b"0", b"1"):
    return None
  on = raw != b"1"
  return on if _commit(params, ENABLED_KEY, b"1" if on else b"0", raw) else None


def next_profile(params) -> str | None:
  """Select the profile after the current one, wrapping; returns its name."""
  from openpilot.starpilot.saved_source import read_saved
  raw, readable = read_saved(params, PROFILE_KEY, 64)
  if not readable:
    return None
  names = list(PROFILES)
  current = raw.decode(errors="replace") if raw is not None else DEFAULT_PROFILE
  name = names[(names.index(current) + 1) % len(names)] if current in PROFILES else DEFAULT_PROFILE
  return name if _commit(params, PROFILE_KEY, name.encode(), raw) else None


def volume(params) -> float:
  try:
    return min(max(int(params.get(VOLUME_KEY)), 0), 100) / 100.0
  except (TypeError, ValueError):
    return DEFAULT_VOLUME / 100.0
