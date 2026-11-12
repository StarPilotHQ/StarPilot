"""Persistent, default-off lead takeoff preference; invalid bytes never activate it."""
from opendbc.car import ACCEL_MAX_TAKEOFF
from openpilot.starpilot.saved_source import read_saved

KEY = 'FasterLeadTakeoff'
MAX_ACCEL = ACCEL_MAX_TAKEOFF


class TakeoffPreferences:
  def __init__(self, params):
    self.params = params
    self.enabled = False
    self.next_ns = 0
    self.last_ns = 0

  def sample(self, now_ns):
    if now_ns < self.last_ns:
      self.next_ns = 0
    self.last_ns = now_ns
    if now_ns >= self.next_ns:
      self.next_ns = now_ns + 1_000_000_000
      try:
        raw, readable = read_saved(self.params, KEY, 16)
        safe, safe_readable = read_saved(self.params, 'SafeMode', 16)
        self.enabled = readable and safe_readable and raw == b'1' and safe in (None, b'0')
      except (OSError, RuntimeError, TypeError, ValueError):
        self.enabled = False
    return self.enabled
