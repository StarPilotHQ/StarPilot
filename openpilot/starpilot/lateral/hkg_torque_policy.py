from openpilot.starpilot.flm import torque_stages as auto_stages
from collections import deque
import math

import numpy as np

from openpilot.starpilot.lateral.torque_extension import apply_turn_assist
from openpilot.cereal import log
from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY, CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.pid import PIDController
from opendbc.car import structs
from openpilot.starpilot.lateral.torque_shaping import (
  FF_ROLL_OFFSET_FADE_BP,
  FF_ROLL_OFFSET_FADE_V,
  JERK_GAIN,
  KI,
  INTERP_SPEEDS,
  KP_INTERP,
  LAT_ACCEL_REQUEST_BUFFER_SECONDS,
  LOW_SPEED_X,
  LOW_SPEED_Y,
  LP_FILTER_CUTOFF_HZ,
  MAX_LAT_JERK_UP,
  MIN_SPEED,
  UNWIND_D_DES_THRESHOLD,
  UNWIND_LAT_ACCEL_NEAR_ZERO,
  center_chatter_friction_jerk_deadzone,
)

def supported_torque_cp(cp, vehicles):
  if (cp.brand != 'hyundai' or cp.carFingerprint not in vehicles or cp.notCar or cp.passive or cp.dashcamOnly or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or cp.lateralTuning.which() != 'torque'):
    return False
  tune = cp.lateralTuning.torque
  return (math.isfinite(tune.latAccelFactor) and tune.latAccelFactor > 0 and
          math.isfinite(tune.latAccelOffset) and math.isfinite(tune.friction) and tune.friction >= 0)


class HKGShapedTorquePolicy:
  FACTOR_MULT = 1.0

  def __init__(self, parent, cp):
    if not self.supported_cp(cp):
      raise ValueError("Unsupported HKG torque profile")
    self.parent = parent
    self.dt = parent.dt
    parent.pid = PIDController([INTERP_SPEEDS, KP_INTERP], KI, rate=1 / self.dt)
    parent.update_limits()
    parent.torque_params.latAccelFactor *= self.FACTOR_MULT
    self.buffer_len = int(LAT_ACCEL_REQUEST_BUFFER_SECONDS / self.dt)
    self.curvature_buffer = deque([0.0] * self.buffer_len, maxlen=self.buffer_len)
    self.jerk_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * LP_FILTER_CUTOFF_HZ), self.dt)
    self.measurement_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * (MAX_LAT_JERK_UP - 0.5)), self.dt)
    self.low_speed_reset_threshold = max(cp.minSteerSpeed, 0.3)
    self.previous_measurement = 0.0
    self.previous_setpoint = 0.0
    self.previous_pressed = False
    if parent.accord_center_friction:
      self.accord_feedforward = FirstOrderFilter(0.0, 0.08, self.dt, initialized=False)

  def _accord_feedforward(self, future, setpoint, cs, curvature_limited):
    smoother = getattr(self, "accord_feedforward", None)
    if smoother is None:
      return future

    speed_weight = np.interp(cs.vEgo, [50. * CV.MPH_TO_MS, 55. * CV.MPH_TO_MS], [0., 1.])
    curve_weight = np.interp(max(abs(future), abs(setpoint)), [0.25, 0.55], [1., 0.])
    if curvature_limited or cs.steeringPressed or cs.leftBlinker or cs.rightBlinker or speed_weight == 0 or curve_weight == 0:
      smoother.initialized = False
      return future

    correction = float(np.clip((smoother.update(future) - future) * speed_weight * curve_weight, -0.08, 0.08))
    # Never carry the old curve's feedforward through a change in steering direction.
    shaped = future + correction
    return shaped if future * shaped > 0. else 0.

  def feedforward_context(self, value, setpoint, jerk, measurement, speed):
    return self.feedforward(value, setpoint, jerk, speed)

  def friction_context(self, setpoint, jerk, measurement, speed):
    return self.friction(setpoint, jerk, speed)

  def jerk_deadzone(self, setpoint, jerk, measurement, speed):
    return 0.0

  def pid_error_context(self, error, setpoint, speed):
    return error

  def output_context(self, value, setpoint, jerk, measurement, speed):
    return self.output(value, setpoint, speed)

  def update(self, active, cs, vm, params, safety_limited, curvature, curvature_limited, delay):
    parent = self.parent
    pid_log = log.ControlsState.LateralTorqueState.new_message()
    pid_log.version = 2
    measurement = -vm.calc_curvature(math.radians(cs.steeringAngleDeg - params.angleOffsetDeg), cs.vEgo, params.roll) * cs.vEgo**2
    future = curvature * cs.vEgo**2
    if not active:
      parent.pid.reset()
      self.curvature_buffer.append(curvature)
      self.previous_measurement = measurement
      self.measurement_filter.x = self.jerk_filter.x = 0.0
      self.previous_setpoint = future
      self.previous_pressed = cs.steeringPressed
      if parent.accord_center_friction:
        self.accord_feedforward.initialized = False
      pid_log.active = False
      return 0.0, 0.0, pid_log
    if self.previous_pressed and not cs.steeringPressed:
      parent.pid.i *= 0.8
    fade = np.interp(cs.vEgo, FF_ROLL_OFFSET_FADE_BP, FF_ROLL_OFFSET_FADE_V)
    roll = params.roll * ACCELERATION_DUE_TO_GRAVITY * fade
    deadzone = abs(vm.calc_curvature(math.radians(parent.steering_angle_deadzone_deg), cs.vEgo, 0.0)) * cs.vEgo**2
    deadzone = auto_stages.apply_selected_deadzone(parent, cs, vm, float(deadzone), curvature,
      active=active, limited=safety_limited or curvature_limited, curvature_limited=curvature_limited)
    delay_frames = int(np.clip(delay / self.dt, 1, self.buffer_len))
    expected = self.curvature_buffer[-delay_frames] * cs.vEgo**2
    self.curvature_buffer.append(curvature)
    raw_jerk = np.clip((future - expected) / max(delay, self.dt), -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP)
    jerk = np.clip(self.jerk_filter.update(raw_jerk), -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP)
    setpoint = expected + jerk * delay
    unwind = (setpoint - self.previous_setpoint) / self.dt < UNWIND_D_DES_THRESHOLD and abs(setpoint) < UNWIND_LAT_ACCEL_NEAR_ZERO
    self.previous_setpoint = setpoint
    rate = np.clip(self.measurement_filter.update((measurement - self.previous_measurement) / self.dt), -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP)
    self.previous_measurement = measurement
    lsf = (np.interp(cs.vEgo, LOW_SPEED_X, LOW_SPEED_Y) / max(cs.vEgo, MIN_SPEED)) ** 2
    kp = np.interp(cs.vEgo, parent.pid._k_p[0], parent.pid._k_p[1])
    error = (setpoint - measurement) * (1 + lsf / max(kp, 1e-3))
    gravity_adjusted = self._accord_feedforward(future, setpoint, cs, curvature_limited) - roll
    ff = gravity_adjusted - parent.torque_params.latAccelOffset * fade
    ff = self.feedforward_context(ff, setpoint, jerk, measurement, cs.vEgo)
    threshold, friction_scale = self.friction_context(setpoint, jerk, measurement, cs.vEgo)
    jerk_deadzone = center_chatter_friction_jerk_deadzone(cs.vEgo, setpoint, self.jerk_deadzone(setpoint, jerk, measurement, cs.vEgo))
    friction_jerk = math.copysign(max(abs(jerk) - jerk_deadzone, 0.0), jerk)
    ff, threshold = auto_stages.apply_stages(parent, cs, setpoint, jerk, ff, threshold,
      active=active, limited=safety_limited or curvature_limited, curvature_limited=curvature_limited)
    selected_friction = friction_scale * parent.friction(error + JERK_GAIN * friction_jerk, deadzone, threshold, cs, setpoint)
    ff += auto_stages.apply_selected_friction(parent, cs, setpoint, float(selected_friction),
      active=active, limited=safety_limited or curvature_limited, curvature_limited=curvature_limited)
    if cs.vEgo < self.low_speed_reset_threshold:
      parent.pid.reset()
    freeze = safety_limited or cs.steeringPressed or cs.vEgo < self.low_speed_reset_threshold or unwind
    pid_log.error = float(self.pid_error_context(error, setpoint, cs.vEgo))
    output = parent.torque_from_lateral_accel(
      parent.pid.update(pid_log.error, error_rate=-rate, speed=cs.vEgo, feedforward=ff, freeze_integrator=freeze), parent.torque_params
    )
    output = apply_turn_assist(parent, cs, vm, params, curvature, output)
    output = self.output_context(output, setpoint, jerk, measurement, cs.vEgo)
    self.previous_pressed = cs.steeringPressed
    pid_log.active = True
    pid_log.p, pid_log.i, pid_log.d, pid_log.f = map(float, (parent.pid.p, parent.pid.i, parent.pid.d, parent.pid.f))
    pid_log.output = float(-output)
    pid_log.actualLateralAccel, pid_log.desiredLateralAccel, pid_log.desiredLateralJerk = map(float, (measurement, setpoint, jerk))
    pid_log.saturated = bool(parent._check_saturation(parent.steer_max - abs(output) < 1e-3, cs, safety_limited, curvature_limited))
    return -float(output), 0.0, pid_log
