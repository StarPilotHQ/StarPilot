"""Gentle clear-road Experimental acceleration, below all existing plan ceilings."""

import math

from openpilot.starpilot.saved_source import read_saved

KEY = "ExperimentalSpeedNudge"

SOURCES = ('carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState', 'modelV2')
MAX_SOURCE_AGE_NS = 250_000_000
MAX_BOOST = .6
BOOST_RATE = .25
SETTLE_SECONDS = 1.5
MIN_HESITATION_GAP = 1.
HESITATION_ACCEL = .3
RECOVERY_ACCEL = .6
HESITATION_FILTER_SECONDS = .5


class Encouragement:
  def __init__(self):
    self.clear_seconds = 0.
    self.boost = 0.
    self.drive_id = None
    self.last_model_ns = 0
    self.hesitation_accel: float | None = None

  def update(self, sm, cp, *, now_ns, drive_id, dt, target_speed, model_accel, mpc_accel, cruise_accel,
             lateral_accel, blocked):
    eligible = False
    model_ns = 0
    try:
      car = sm['carState']
      model_ns = sm.logMonoTime['modelV2']
      fresh = all(sm.valid[name] and sm.alive[name] and
                  getattr(sm, 'freq_ok', {}).get(name, True) and
                  0 < sm.logMonoTime[name] <= now_ns and now_ns - sm.logMonoTime[name] <= MAX_SOURCE_AGE_NS
                  for name in SOURCES)
      eligible = (not blocked and fresh and cp.openpilotLongitudinalControl and
                  not cp.passive and not cp.dashcamOnly and not cp.notCar and
                  sm['selfdriveState'].experimentalMode and sm['selfdriveState'].enabled and
                  sm['carControl'].longActive and car.canValid and not car.canTimeout and
                  not car.gasPressed and not car.brakePressed and not car.standstill and
                  not sm['controlsState'].forceDecel and not sm['modelV2'].action.shouldStop and
                  not sm['radarState'].leadOne.present and not sm['radarState'].leadTwo.present and
                  not any(sm['radarState'].radarErrors.to_dict().values()) and
                  all(math.isfinite(x) for x in (dt, target_speed, model_accel, mpc_accel, cruise_accel, lateral_accel, car.vEgo, car.aEgo)) and
                  0 < dt <= .1 and car.vEgo >= 4.5 and target_speed - car.vEgo > .2 and
                  model_accel >= 0 and abs(lateral_accel) < .5 and min(mpc_accel, cruise_accel) > model_accel)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      eligible = False
    if type(model_ns) is not int or model_ns <= 0:
      model_ns = 0
      eligible = False
    if drive_id != self.drive_id or model_ns <= self.last_model_ns:
      eligible = False
    self.drive_id = drive_id
    self.last_model_ns = model_ns
    if not eligible:
      self.clear_seconds = self.boost = 0.
      self.hesitation_accel = None
      return model_accel
    if self.hesitation_accel is None:
      self.hesitation_accel = car.aEgo
    else:
      self.hesitation_accel += dt / (HESITATION_FILTER_SECONDS + dt) * (car.aEgo - self.hesitation_accel)
    if self.boost == 0. and (target_speed - car.vEgo < MIN_HESITATION_GAP or
                            car.aEgo >= RECOVERY_ACCEL or self.hesitation_accel > HESITATION_ACCEL):
      self.clear_seconds = 0.
      return model_accel
    self.clear_seconds += dt
    if self.clear_seconds > SETTLE_SECONDS and car.aEgo < RECOVERY_ACCEL:
      self.boost = min(MAX_BOOST, self.boost + BOOST_RATE * dt)
    gap_scale = min(1., max(0., (target_speed - car.vEgo - .2) / 1.8))
    return min(max(model_accel, self.boost * gap_scale), mpc_accel, cruise_accel)


class NudgePreferences:
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
        self.enabled = readable and raw in (None, b'1')
      except (OSError, RuntimeError, TypeError, ValueError):
        self.enabled = False
    return self.enabled
