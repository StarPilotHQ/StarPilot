"""Jerk-only lane-change shaping with a gradual return to native limits."""

import math

from openpilot.cereal import log
from openpilot.common.realtime import DT_CTRL
from openpilot.starpilot.lateral.signal_turn import SignalTurnEvidence
from openpilot.selfdrive.controls.lib.desire_helper import LANE_CHANGE_SPEED_MIN
from openpilot.selfdrive.controls.lib.drive_helpers import MAX_LATERAL_JERK, MIN_SPEED


class LaneChangeSmoother:
  def __init__(self):
    self.turn_evidence = SignalTurnEvidence()
    self.reset()

  def reset(self):
    self.release = 0.0
    self.entry_sign = 0.0
    self.previous_factor = 1.0
    self.intersection_maneuver = False

  def factor(self, *, active, lane_change_state, speed, minimum_speed, duration, previous, desired, turn_assist=False, intersection_turn=False):
    if (not active or not all(math.isfinite(value) for value in (speed, minimum_speed, duration, previous, desired)) or
        not 3.0 < duration <= 8.0):
      self.reset()
      return 1.0
    if not turn_assist:
      self.intersection_maneuver = False
    maneuver = lane_change_state in (log.LaneChangeState.laneChangeStarting, log.LaneChangeState.laneChangeFinishing)
    # A model lane-change state can outlive an intersection turn while the car
    # accelerates across the highway lane-change floor. Finish that exemption
    # before admitting pacing for a new maneuver.
    intersection = bool(turn_assist and maneuver and
                        (speed < LANE_CHANGE_SPEED_MIN or intersection_turn or self.intersection_maneuver))
    if turn_assist and (speed < LANE_CHANGE_SPEED_MIN or intersection_turn or self.intersection_maneuver):
      self.reset()
      self.intersection_maneuver = intersection
      return 1.0
    base = min(1., math.pi ** 3 * 3.5 / duration ** 3 * 1.3 / MAX_LATERAL_JERK)
    if maneuver and speed >= minimum_speed:
      self.release = 2.0
      if self.entry_sign == 0.0 and abs(desired - previous) > 2e-4:
        self.entry_sign = math.copysign(1., desired - previous)
    else:
      self.release = max(0., self.release - DT_CTRL)
      if self.release <= 0.:
        self.entry_sign = 0.
    factor = 1.0
    if self.release > 0.:
      release = 1. - self.release / 2.
      factor = base + (1. - base) * release
      step = desired - previous
      if self.entry_sign and abs(step) > 5e-5 and math.copysign(1., step) == -self.entry_sign:
        pursuit = (max(abs(step) - 5e-5, 0.) / .2) * max(speed, 1.) ** 2 / MAX_LATERAL_JERK
        factor = max(factor, min(.6 + .4 * release, factor + pursuit))
      if factor > self.previous_factor:
        factor = self.previous_factor + (1. - math.exp(-DT_CTRL / .2)) * (factor - self.previous_factor)
    self.previous_factor = factor
    return max(0., min(1., factor))


def limit_rate(speed, previous, desired, factor):
  if factor >= 1. or previous * (desired - previous) < 0.:
    return desired
  step = MAX_LATERAL_JERK / max(speed, MIN_SPEED) ** 2 * DT_CTRL * factor
  return max(previous - step, min(previous + step, desired))
