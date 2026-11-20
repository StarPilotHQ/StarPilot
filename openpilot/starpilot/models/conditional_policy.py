"""Speed is a road-mode hint, never authority to change models or engage."""
import math

from openpilot.starpilot.models.conditional_protocol import FRESH_NS, fresh

MPH_TO_MS = 0.44704
HYSTERESIS_MPH = 5
DEFAULT_THRESHOLD_MPH = 50
CITY_DELAY_SECONDS = 30


class SpeedPolicy:
  """Fast Highway entry, delayed City entry, and no progress on stale samples."""
  def __init__(self, threshold_mph=DEFAULT_THRESHOLD_MPH):
    if type(threshold_mph) is not int or not 10 <= threshold_mph <= 100:
      raise ValueError('Invalid Conditional Model speed policy')
    self.high = threshold_mph * MPH_TO_MS
    self.low = (threshold_mph - HYSTERESIS_MPH) * MPH_TO_MS
    self.delay = CITY_DELAY_SECONDS * 1_000_000_000
    self.low_since = None
    self.last_sample = self.last_switch = 0
    self.hint = ''

  def switched(self, now):
    self.last_switch, self.low_since = now, None

  def sample(self, speed, stamp, now, *, valid, current):
    self.hint = ''
    if (not valid or type(speed) not in (int, float) or not math.isfinite(speed) or not 0 <= speed <= 100 or
        not fresh(stamp, now) or stamp < self.last_sample or current not in ('highway', 'city')):
      self.low_since = None
      return None
    if self.last_sample and stamp - self.last_sample > FRESH_NS:
      self.low_since = None
    self.last_sample = stamp
    if speed >= self.high:
      self.hint = 'highway'
      self.low_since = None
      return 'highway' if current != 'highway' else None
    if speed <= self.low and current != 'city':
      self.hint = 'city'
      if self.low_since is None:
        self.low_since = stamp
      if now - self.low_since >= self.delay and now - self.last_switch >= self.delay:
        return 'city'
    else:
      self.low_since = None
    return None

