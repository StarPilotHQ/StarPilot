"""Comma can set speed on Tesla dashboard: the saved switch, followed while the car is on.

The car's parameters only say whether the panda admits the scroll frame (opendbc.car.tesla.speed_sync.selected, fixed
when the car starts). Whether the comma owns the set speed follows the saved switch: card, the planner's Speed Limit
Controller and the screen each read it here about once a second, so a change takes effect without a restart. For up to
a second the processes can differ. If card follows first, the limit still caps the comma's set speed. If the planner
follows first, the limit neither caps nor is written until card follows, so the car holds the Tesla's own set speed
for that moment. The switch can only be changed while parked, so neither happens while driving unless Safe Mode is
toggled.
"""
import time

from opendbc.car.tesla.speed_sync import selected
from openpilot.starpilot.saved_source import read_saved

PARAM = 'TeslaSpeedSync'


def requested(params) -> bool:
  """The switch is saved on, and Safe Mode is off."""
  return bool(read_saved(params, PARAM, 8) == (b'1', True) and
              read_saved(params, 'SafeMode', 8) in ((None, True), (b'0', True)))


def active(params, cp) -> bool:
  """The comma owns the set speed on this car now: a Tesla Model 3/Y that started with the scroll frame admitted, with
  the switch on. False for every other car without reading anything."""
  try:
    return bool(selected(cp) and requested(params))
  except (AttributeError, TypeError, ValueError, OSError):
    return False


class Polled:
  """active(), read from storage at most once a second: for loops that ask many times a second."""

  def __init__(self, params, clock=time.monotonic):
    self.params, self.clock = params, clock
    self.read_at = None
    self.value = False

  def __call__(self, cp) -> bool:
    if not selected(cp):
      return False
    now = self.clock()
    if self.read_at is None or not 0 <= now - self.read_at < 1.:
      self.read_at, self.value = now, requested(self.params)
    return self.value
