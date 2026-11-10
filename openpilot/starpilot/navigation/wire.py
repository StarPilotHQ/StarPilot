VERSION = 2
PHONE_SOURCE_PREFIX = 'phone:'
PHONE_CONTROL_AGE_NS = 25_000_000_000
PHONE_PASSED_SLACK_M = 50.


def is_phone_navigation(state):
  return getattr(state, 'source', '').startswith(PHONE_SOURCE_PREFIX)


def navigation_state(envelope):
  try:
    state = envelope.navigation
    return state if state.version == VERSION else None
  except Exception:
    return None


def navigation_envelope(value):
  return {'navigation': {**value, 'version': VERSION}}


LEGACY_SOURCE_FORMAT = 'starpilot-navigation-flat-slot0-v1'


def decode_legacy_navigation(raw, *, source_format, event_stamp_ns):
  import math
  from pathlib import Path
  if source_format != LEGACY_SOURCE_FORMAT or not isinstance(raw, bytes) or not 0 < len(raw) <= 1_048_576:
    return None
  try:
    import capnp
    schema = capnp.load(str(Path(__file__).with_name('legacy_navigation.capnp')))
    with schema.LegacyNavigation.from_bytes(raw) as state:
      if (not state.sessionId or not state.revision or state.status not in
          ('disabled', 'idle', 'unavailable', 'routing', 'guiding', 'arrived', 'error') or
          not 0 < state.frameMonoTime <= event_stamp_ns <= state.frameMonoTime + 3_000_000_000 or
          len(state.route) > 4096):
        return None
      if state.controlValid and (not state.enabled or state.status != 'guiding' or
          not 0 < state.startedMonoTime <= state.locationMonoTime <= state.frameMonoTime):
        return None
      for coordinate in state.route:
        if not (math.isfinite(coordinate.latitude) and math.isfinite(coordinate.longitude) and
                -90 <= coordinate.latitude <= 90 and -180 <= coordinate.longitude <= 180):
          return None
      return state.to_dict()
  except Exception:
    return None
