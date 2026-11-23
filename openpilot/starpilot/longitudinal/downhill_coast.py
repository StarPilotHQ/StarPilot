import math

from openpilot.common.constants import CV
from openpilot.starpilot.saved_source import read_saved

KEY = 'CoastDownhills'
SURPLUS_KEY = 'DownhillSpeedSurplus'
MAX_SURPLUS_MPH = 25


def read_surplus(params) -> float:
  try:
    if read_saved(params, KEY, 8) != (b'1', True) or read_saved(params, 'SafeMode', 8) not in ((None, True), (b'0', True)):
      return 0.
    raw, readable = read_saved(params, SURPLUS_KEY, 32)
    surplus = float(raw) if raw is not None else 0.
    if readable and 0. <= surplus <= MAX_SURPLUS_MPH:
      return surplus * CV.MPH_TO_MS
  except (OSError, RuntimeError, TypeError, ValueError):
    pass
  return 0.


def speed_error(set_speed: float, speed: float, accel: float, pitch: float, *, limit: float, delay: float) -> float | None:
  if (not all(map(math.isfinite, (set_speed, speed, accel, pitch, limit, delay))) or
      not 0. < set_speed < limit or speed < set_speed or not -math.radians(20.) <= pitch <= -math.atan(.01)):
    return None
  # Only delay cruise braking. Never create positive speed error or chase the allowance.
  return min(0., limit - (speed + max(0., accel) * max(0., delay)))
