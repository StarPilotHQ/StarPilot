"""Bounded Model 3 pedal handover; vehicle comfort remains unverified.

Caller supplies final acceleration and fresh raw CAN observation time. Negative
requests and faults restore the unchanged formatter immediately. No speed or
acceleration alteration. Hold/recovery are capped and a new pedal edge is needed.
"""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class HandoverSample:
  time: float
  gas: bool
  enabled: bool
  long_active: bool
  override: bool
  di_age: float
  valid: bool
  brake: bool
  aeb: bool
  cancel: bool
  fault: bool
  speed: float
  native_speed: float
  selected_speed: float
  accel: float


class PedalHandover:
  def __init__(self, *, hold_seconds=.6, include_initial_zero=True, recovery_seconds=1.0):
    if not isfinite(hold_seconds) or not 0 < hold_seconds <= 1.0:
      raise ValueError('explicit experimental hold must be in (0, 1] seconds')
    if not isfinite(recovery_seconds) or not 0 < recovery_seconds <= 1.0:
      raise ValueError('recovery must be in (0, 1] seconds')
    if type(include_initial_zero) is not bool:
      raise ValueError('initial-zero experiment must be explicitly boolean')
    self.include_initial_zero = include_initial_zero
    self.hold_seconds = hold_seconds
    self.recovery_seconds = recovery_seconds
    self.reset()

  def reset(self):
    self.last_time = None
    self.gas_since = None
    self.was_gas = False
    self.release_time = None
    self.recovery_time = None
    self.native_at_release = None
    self.reason = 'baseline'

  def _drop_episode(self):
    self.release_time = self.recovery_time = self.native_at_release = None

  def update(self, s, baseline):
    """Return a copy of formatter values. Only the two jerk fields may differ."""
    result = dict(baseline)
    numeric = (s.time, s.di_age, s.speed, s.native_speed, s.selected_speed, s.accel,
               baseline['DAS_accelMin'], baseline['DAS_accelMax'], baseline['DAS_jerkMin'], baseline['DAS_jerkMax'])
    healthy = (all(isfinite(x) for x in numeric) and s.time >= 0 and
               s.valid and s.enabled and 0 <= s.di_age <= .15 and
               s.native_speed > 0 and 0 < s.selected_speed < 255 / 3.6 and s.speed > 2 and
               not (s.brake or s.aeb or s.cancel or s.fault) and
               baseline['DAS_accState'] == 4 and baseline['DAS_aebEvent'] == 0 and
               baseline['DAS_accelMin'] == s.accel)
    continuous = self.last_time is None or 0 < s.time - self.last_time <= .1
    if not healthy or not continuous:
      self.reset()
      self.reason = 'invalid_or_gap'
      return result
    self.last_time = s.time
    if s.gas:
      self._drop_episode()
      if not self.was_gas:
        self.gas_since = s.time
      self.was_gas = True
      self.reason = 'pedal'
      return result
    if self.was_gas:
      held = self.gas_since is not None and s.time - self.gas_since >= .1
      self.was_gas = False
      self.gas_since = None
      if held and s.override and s.speed - s.native_speed > .25 and s.selected_speed >= s.speed and s.accel >= 0:
        self.release_time = s.time
        self.native_at_release = s.native_speed
    if self.release_time is None:
      self.reason = 'baseline'
      return result
    # One-shot per pedal episode: aborts cannot rearm without another held pedal.
    if s.accel < 0 or abs(s.native_speed - self.native_at_release) > .05 or s.selected_speed < s.speed:
      self._drop_episode()
      self.reason = 'request_or_selection_abort'
      return result
    elapsed = s.time - self.release_time
    if not s.long_active:
      if elapsed > .15:
        self._drop_episode()
      if elapsed > .15 or not self.include_initial_zero or s.accel != 0:
        self.reason = 'inactive'
        return result
    if self.recovery_time is None and (not s.override or elapsed >= self.hold_seconds):
      self.recovery_time = min(s.time, self.release_time + self.hold_seconds)
    if self.recovery_time is None:
      bound = 0.0
      self.reason = 'hold'
    else:
      recovery_elapsed = s.time - self.recovery_time
      if recovery_elapsed >= self.recovery_seconds:
        self._drop_episode()
        self.reason = 'baseline'
        return result
      bound = 4.9 * max(0.0, recovery_elapsed) / self.recovery_seconds
      self.reason = 'recovery'
    # Never create a stronger jerk allowance than the existing formatter.
    # The optional zero-command phase is explicit and limited to .15s off-axis.
    # ECU response and the benefit of including this phase remain unknown.
    if s.accel > 0 or self.include_initial_zero:
      result['DAS_jerkMax'] = min(baseline['DAS_jerkMax'], bound)
      result['DAS_jerkMin'] = max(baseline['DAS_jerkMin'], -bound)
    return result