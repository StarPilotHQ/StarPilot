"""Independent opt-in; legacy LaneChangePreferences bytes are never changed."""
from openpilot.starpilot.saved_source import read_saved

KEY = "AutomaticTurnInitiation"
REFRESH_NS = 1_000_000_000


def saved(params):
  raw, readable = read_saved(params, KEY, 8)
  return raw, readable and raw in (None, b"0", b"1")


class TurnInitiationPreference:
  def __init__(self, params):
    self.params = params
    self.last_read_ns = None
    self.requested = self.applied = False

  def sample(self, now_ns, *, signaling=False, maneuver_active=False):
    if type(now_ns) is not int or now_ns <= 0 or (self.last_read_ns is not None and now_ns < self.last_read_ns):
      self.last_read_ns = None
      self.requested = self.applied = False
      return False
    if self.last_read_ns is None or now_ns - self.last_read_ns >= REFRESH_NS:
      raw, valid = saved(self.params)
      self.requested = bool(valid and raw == b"1")
      self.last_read_ns = now_ns
    if not self.requested:
      self.applied = False
    elif not signaling and not maneuver_active:
      self.applied = True
    return self.applied
