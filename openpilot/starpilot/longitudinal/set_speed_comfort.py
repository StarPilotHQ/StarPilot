"""Cruise-transition helpers shared by the SLC limit-change forecast (speed_limits/mapped_timing.py), and gentle Tesla
acceleration during a selected-speed change."""

import math

UP_JERK = 0.2  # m/s^3
DOWN_JERK = 0.4  # Cruise slowing only; MPC/model/hazard braking keeps priority.


class SetSpeedComfort:
  def __init__(self):
    self.previous = None
    self.profile_owned = False
    self.active = False

  def update(self, cp, *, selected, speed, enabled, profile_owned=False):
    valid = (enabled and cp.carFingerprint in ('TESLA_MODEL_3', 'TESLA_MODEL_Y') and
             cp.openpilotLongitudinalControl and all(math.isfinite(v) for v in (selected, speed)) and
             0 < selected < 80 and speed > 1.)
    if not valid:
      self.previous, self.active, self.profile_owned = None, False, False
      return False
    previous, self.previous = self.previous, selected
    if previous is None:
      self.active = False
    elif abs(selected - previous) > .05:
      self.active = True
    if previous is None or abs(selected - previous) > .05 or not self.active:
      self.profile_owned = False
    if self.active and profile_owned:
      self.profile_owned = True
    return self.active


def braking_lower_bound(previous, dt, native_jerk, braking_jerk):
  """Release positive acceleration normally, then ramp into cruise braking."""
  release_time = min(dt, max(previous, 0.0) / native_jerk)
  return previous - native_jerk * release_time - min(native_jerk, braking_jerk) * (dt - release_time)


def owns_selected_target(request, now_ns, selected):
  from openpilot.starpilot.longitudinal.slc_coast import EasingRequest
  return (isinstance(request, EasingRequest) and type(request.now_ns) is int and
          0 <= now_ns - request.now_ns <= 100_000_000 and
          math.isfinite(request.target) and math.isfinite(selected) and
          abs(request.target - selected) <= .01)


class CruiseTransition:
  """Shape the cruise-speed response before lead/model arbitration.

  State follows the previous final request, so a hidden cruise trajectory cannot
  accumulate behind stronger braking. Repeated target changes never reset it.
  Rates limit transitions, not the selected acceleration/braking profile.
  """

  UP_RATE = 0.35
  DOWN_RATE = 0.4
  RATE_CHANGE = 0.7
  # A lower final request (model blip, radar track swap, MPC step) must persist this long before it replaces our
  # trajectory. Shorter dips keep the ramp state so acceleration resumes where it was instead of restarting at zero.
  DIP_HOLD_NS = 300_000_000

  def __init__(self):
    self.reset()

  def reset(self):
    self.time_ns = None
    self.output = None
    self.rate = 0.0
    self.recovering = False
    self.dip_since_ns = None

  def shape(self, *, selected, speed, desired, previous, now_ns, enabled, protected=False, response_time=0.5, release_rate=1.0):
    valid = (
      enabled
      and type(now_ns) is int
      and now_ns > 0
      and all(math.isfinite(v) for v in (selected, speed, desired, previous, response_time, release_rate))
      and 0.0 < selected < 80.0
      and speed > 1.0
      and 0.0 <= response_time <= 2.0
      and 0.0 < release_rate <= 3.0
    )
    if not valid or protected:
      self.reset()
      self.recovering = True
      return desired
    dt = (now_ns - self.time_ns) / 1e9 if self.time_ns is not None else 0.05
    base = previous
    if self.time_ns is None or not 0.01 <= dt <= 0.15:
      dt, self.rate = 0.05, 0.0
      self.dip_since_ns = None
    elif self.output is not None and previous < self.output - 0.02:
      # A lower final model/lead/stop output replaced ours. Only once it has persisted for DIP_HOLD_NS is it
      # authoritative: then restart from that actual request without storing a deficit. A shorter dip keeps our own
      # trajectory (the planner still publishes the lower minimum meanwhile).
      if self.dip_since_ns is None:
        self.dip_since_ns = now_ns
      if now_ns - self.dip_since_ns < self.DIP_HOLD_NS:
        base = self.output
      else:
        self.rate = 0.0
        self.recovering = True
        self.dip_since_ns = None
    elif self.output is not None and previous > self.output + 0.02:
      self.rate = 0.0
      self.dip_since_ns = None
    else:
      self.dip_since_ns = None
    self.time_ns = now_ns
    if self.recovering and base < 0.0:
      # This braking came from another authority (or re-engagement), not an
      # ordinary cruise target. Retain the native release rate to avoid holding
      # the brakes after a lead/stop/model constraint has cleared. Stronger
      # requested braking is still immediate; positive build-up restarts at zero.
      release_time = min(dt, -base / release_rate)
      remaining = dt - release_time
      self.rate = min(self.UP_RATE, self.RATE_CHANGE * remaining)
      ceiling = base + release_rate * release_time + 0.5 * self.rate * remaining
      self.output = min(desired, ceiling)
      if self.output >= 0.0:
        self.recovering = False
      return self.output
    self.recovering = False
    error = selected - speed
    target = desired
    if target * error > 0.0:
      # Ease out before reaching the speed, allowing for the actuator delay and
      # the time needed to turn the acceleration trend around. Symmetric on
      # slowing: don't keep braking hard until the target has been crossed.
      j = self.DOWN_RATE if target > 0.0 else self.UP_RATE
      horizon = response_time + 1.25
      arrival = max(0.0, -j * horizon + math.sqrt((j * horizon) ** 2 + 2 * j * abs(error)))
      target = math.copysign(min(abs(target), arrival), target)
    goal = min(max((target - base) / 0.5, -self.DOWN_RATE), self.UP_RATE)
    # Do not keep increasing acceleration after the target asks for less (or
    # keep deepening braking after it asks for release). Preserve acceleration,
    # restart only the opposing trend at zero, then build the new rate gently.
    if goal * self.rate <= 0.0:
      self.rate = 0.0
    rate = min(max(goal, self.rate - self.RATE_CHANGE * dt), self.rate + self.RATE_CHANGE * dt)
    result = base + (self.rate + rate) * 0.5 * dt
    # Avoid numerical overshoot of an acceleration target. This small final
    # convergence is not a promise of a globally bounded acceleration derivative.
    if (target - base) * (target - result) < 0.0:
      result, rate = target, 0.0
    self.output, self.rate = result, rate
    return result
