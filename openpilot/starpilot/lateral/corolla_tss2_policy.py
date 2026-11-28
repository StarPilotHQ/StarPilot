"""Default Corolla TSS2 torque shaping from StarPilot ba901b5f (no FLM overrides)."""

from openpilot.starpilot.flm import torque_stages as auto_stages
import math
from collections import deque

import numpy as np

from openpilot.starpilot.lateral.torque_extension import apply_turn_assist
from openpilot.cereal import log
from opendbc.car import structs
from opendbc.car.lateral import get_friction
from opendbc.car.toyota.values import CAR
from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.pid import PIDController
from openpilot.starpilot.lateral.torque_shaping import (
  INTERP_SPEEDS,
  KP_INTERP,
  KI,
  LAT_ACCEL_REQUEST_BUFFER_SECONDS,
  LP_FILTER_CUTOFF_HZ,
  MAX_LAT_JERK_UP,
  LOW_SPEED_X,
  LOW_SPEED_Y,
  MIN_SPEED,
  JERK_GAIN,
  FF_ROLL_OFFSET_FADE_BP,
  FF_ROLL_OFFSET_FADE_V,
  UNWIND_D_DES_THRESHOLD,
  UNWIND_LAT_ACCEL_NEAR_ZERO,
  center_chatter_friction_jerk_deadzone,
  sigmoid as _sigmoid,
  get_standard_friction_threshold,
)


def supported_cp(CP):
  return (
    CP.brand == "toyota"
    and CP.carFingerprint == CAR.TOYOTA_COROLLA_TSS2
    and CP.steerControlType == structs.CarParams.SteerControlType.torque
    and CP.lateralTuning.which() == "torque"
    and not CP.dashcamOnly
    and not CP.passive
  )


TOYOTA_COROLLA_TSS2_PHASE_SCALE = 0.12
TOYOTA_COROLLA_TSS2_TURN_IN_FF_BOOST = 0.035
TOYOTA_COROLLA_TSS2_UNWIND_FF_REDUCTION = 0.12
TOYOTA_COROLLA_TSS2_CURVE_LAT_ONSET = 0.24
TOYOTA_COROLLA_TSS2_CURVE_LAT_WIDTH = 0.1
TOYOTA_COROLLA_TSS2_SPEED_ONSET = 4.0
TOYOTA_COROLLA_TSS2_SPEED_ONSET_WIDTH = 1.5
TOYOTA_COROLLA_TSS2_SPEED_CUTOFF = 24.0
TOYOTA_COROLLA_TSS2_SPEED_CUTOFF_WIDTH = 3.0
TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_MAX = 0.3
TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_LAT = 0.18
TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_LAT_WIDTH = 0.08
TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_SPEED = 4.5
TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_SPEED_WIDTH = 1.5
TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_GAIN = 0.12
TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_SPEED_ONSET = 12.0
TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH = 2.0
TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_SPEED_CUTOFF = 25.0
TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_SPEED_CUTOFF_WIDTH = 3.0
TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_LAT = 0.24
TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_LAT_WIDTH = 0.1
TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_JERK = 0.25
TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_JERK_WIDTH = 0.1
TOYOTA_COROLLA_TSS2_CURVE_FRICTION_THRESHOLD_GAIN = 0.5
TOYOTA_COROLLA_TSS2_CURVE_FRICTION_THRESHOLD_LAT = 0.5
TOYOTA_COROLLA_TSS2_CURVE_FRICTION_THRESHOLD_LAT_WIDTH = 0.15
TOYOTA_COROLLA_TSS2_UNWIND_SMOOTH_WN = 12.0
TOYOTA_COROLLA_TSS2_UNWIND_SMOOTH_BLEND_TAU = 0.15
TOYOTA_COROLLA_TSS2_UNWIND_SMOOTH_SPEED_ONSET = 1.5
TOYOTA_COROLLA_TSS2_UNWIND_SMOOTH_SPEED_ONSET_WIDTH = 0.5
TOYOTA_COROLLA_TSS2_RELEASE_HOLD_SECONDS = 1.5
TOYOTA_COROLLA_TSS2_RELEASE_HOLD_WN = 2.0
TOYOTA_COROLLA_TSS2_RELEASE_HOLD_MIN_LAT = 0.3


def get_toyota_corolla_tss2_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  """Add a small, transition-only turn-in correction for Corolla TSS2 torque EPS."""
  if desired_lateral_accel == 0.0:
    return 1.0
  phase = math.tanh(desired_lateral_accel * desired_lateral_jerk / TOYOTA_COROLLA_TSS2_PHASE_SCALE)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  curve_weight = _sigmoid((abs(desired_lateral_accel) - TOYOTA_COROLLA_TSS2_CURVE_LAT_ONSET) / TOYOTA_COROLLA_TSS2_CURVE_LAT_WIDTH)
  speed_weight = _sigmoid((v_ego - TOYOTA_COROLLA_TSS2_SPEED_ONSET) / TOYOTA_COROLLA_TSS2_SPEED_ONSET_WIDTH) * _sigmoid(
    (TOYOTA_COROLLA_TSS2_SPEED_CUTOFF - v_ego) / TOYOTA_COROLLA_TSS2_SPEED_CUTOFF_WIDTH
  )
  boost = TOYOTA_COROLLA_TSS2_TURN_IN_FF_BOOST
  unwind_reduction = TOYOTA_COROLLA_TSS2_UNWIND_FF_REDUCTION
  return 1.0 + curve_weight * speed_weight * (boost * turn_in_weight - unwind_reduction * unwind_weight)


def get_toyota_corolla_tss2_unwind_weight(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 0.0
  phase = math.tanh(desired_lateral_accel * desired_lateral_jerk / TOYOTA_COROLLA_TSS2_PHASE_SCALE)
  curve_weight = _sigmoid((abs(desired_lateral_accel) - TOYOTA_COROLLA_TSS2_CURVE_LAT_ONSET) / TOYOTA_COROLLA_TSS2_CURVE_LAT_WIDTH)
  speed_weight = _sigmoid((v_ego - TOYOTA_COROLLA_TSS2_UNWIND_SMOOTH_SPEED_ONSET) / TOYOTA_COROLLA_TSS2_UNWIND_SMOOTH_SPEED_ONSET_WIDTH) * _sigmoid(
    (TOYOTA_COROLLA_TSS2_SPEED_CUTOFF - v_ego) / TOYOTA_COROLLA_TSS2_SPEED_CUTOFF_WIDTH
  )
  return curve_weight * speed_weight * max(-phase, 0.0)


def get_toyota_corolla_tss2_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  """Reduce center-only friction chasing on highway-sized model corrections."""
  speed_weight = _sigmoid(
    (v_ego - TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_SPEED_ONSET) / TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH
  ) * _sigmoid((TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_SPEED_CUTOFF - v_ego) / TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_SPEED_CUTOFF_WIDTH)
  center_weight = _sigmoid(
    (TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_LAT - abs(desired_lateral_accel)) / TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_LAT_WIDTH
  )
  calm_weight = _sigmoid(
    (TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_JERK - abs(desired_lateral_jerk)) / TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_JERK_WIDTH
  )
  curve_weight = _sigmoid(
    (abs(desired_lateral_accel) - TOYOTA_COROLLA_TSS2_CURVE_FRICTION_THRESHOLD_LAT) / TOYOTA_COROLLA_TSS2_CURVE_FRICTION_THRESHOLD_LAT_WIDTH
  )
  gain = TOYOTA_COROLLA_TSS2_CENTER_FRICTION_THRESHOLD_GAIN
  curve_gain = TOYOTA_COROLLA_TSS2_CURVE_FRICTION_THRESHOLD_GAIN
  return get_standard_friction_threshold(v_ego) * (1.0 + gain * speed_weight * center_weight * calm_weight + curve_gain * curve_weight)


def get_toyota_corolla_tss2_center_output_scale(desired_lateral_accel: float, v_ego: float) -> float:
  """Taper only near-center crawl-speed torque during manual handoff."""
  center_weight = _sigmoid((TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_LAT - abs(desired_lateral_accel)) / TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_LAT_WIDTH)
  low_speed_weight = _sigmoid((TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_SPEED - v_ego) / TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_SPEED_WIDTH)
  reduction = TOYOTA_COROLLA_TSS2_CENTER_OUTPUT_TAPER_MAX * center_weight * low_speed_weight
  return max(1.0 - reduction, 0.65)


class CorollaTSS2TorquePolicy:
  def __init__(self, parent, CP):
    if not supported_cp(CP):
      raise ValueError("Corolla TSS2 policy does not match CarParams")
    self.parent = parent
    self.dt = parent.dt
    parent.pid = PIDController([INTERP_SPEEDS, KP_INTERP], KI, rate=1 / self.dt)
    parent.update_limits()
    self.request_buffer_len = int(LAT_ACCEL_REQUEST_BUFFER_SECONDS / self.dt)
    self.curvature_request_buffer = deque([0.0] * self.request_buffer_len, maxlen=self.request_buffer_len)
    self.jerk_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * LP_FILTER_CUTOFF_HZ), self.dt)
    self.measurement_rate_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * (MAX_LAT_JERK_UP - 0.5)), self.dt)
    self.previous_measurement = 0.0
    self.prev_desired_lateral_accel = 0.0
    self.prev_steering_pressed = False
    self.unwind_target = 0.0
    self.unwind_target_rate = 0.0
    self.unwind_blend = FirstOrderFilter(0.0, TOYOTA_COROLLA_TSS2_UNWIND_SMOOTH_BLEND_TAU, self.dt)
    self.release_hold = 0.0
    self.low_speed_reset_threshold = max(CP.minSteerSpeed, 0.3)

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited, lat_delay):
    parent = self.parent
    pid_log = log.ControlsState.LateralTorqueState.new_message()
    pid_log.version = 2
    measured_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
    measurement = measured_curvature * CS.vEgo**2
    future_desired_lateral_accel = desired_curvature * CS.vEgo**2
    if not active:
      parent.pid.reset()
      self.curvature_request_buffer.append(desired_curvature)
      self.previous_measurement = measurement
      self.measurement_rate_filter.x = 0.0
      self.jerk_filter.x = 0.0
      self.unwind_target = future_desired_lateral_accel
      self.unwind_target_rate = 0.0
      self.unwind_blend.x = 0.0
      self.release_hold = 0.0
      self.prev_desired_lateral_accel = future_desired_lateral_accel
      self.prev_steering_pressed = CS.steeringPressed
      pid_log.active = False
      return 0.0, 0.0, pid_log

    if self.prev_steering_pressed and not CS.steeringPressed:
      parent.pid.i *= 0.8
    roll_offset_fade = float(np.interp(CS.vEgo, FF_ROLL_OFFSET_FADE_BP, FF_ROLL_OFFSET_FADE_V))
    roll_compensation = params.roll * ACCELERATION_DUE_TO_GRAVITY * roll_offset_fade
    curvature_deadzone = abs(VM.calc_curvature(math.radians(parent.steering_angle_deadzone_deg), CS.vEgo, 0.0))
    lateral_accel_deadzone = curvature_deadzone * CS.vEgo**2
    lateral_accel_deadzone = auto_stages.apply_selected_deadzone(parent, CS, VM, float(lateral_accel_deadzone), desired_curvature,
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)
    delay_frames = int(np.clip(lat_delay / self.dt, 1, self.request_buffer_len))
    expected_lateral_accel = self.curvature_request_buffer[-delay_frames] * CS.vEgo**2
    self.curvature_request_buffer.append(desired_curvature)
    raw_lateral_jerk = (future_desired_lateral_accel - expected_lateral_accel) / max(lat_delay, self.dt)
    raw_lateral_jerk = float(np.clip(raw_lateral_jerk, -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP))
    desired_lateral_jerk = float(np.clip(self.jerk_filter.update(raw_lateral_jerk), -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP))
    gravity_adjusted_future_lateral_accel = future_desired_lateral_accel - roll_compensation
    setpoint = expected_lateral_accel + desired_lateral_jerk * lat_delay
    if CS.steeringPressed:
      self.unwind_target = measurement
      self.unwind_target_rate = 0.0
      self.release_hold = 0.0
    elif self.prev_steering_pressed and abs(measurement) > TOYOTA_COROLLA_TSS2_RELEASE_HOLD_MIN_LAT and CS.vEgo < TOYOTA_COROLLA_TSS2_SPEED_CUTOFF:
      self.release_hold = 1.0
    else:
      self.release_hold = max(self.release_hold - self.dt / TOYOTA_COROLLA_TSS2_RELEASE_HOLD_SECONDS, 0.0)
    wn = TOYOTA_COROLLA_TSS2_UNWIND_SMOOTH_WN + (TOYOTA_COROLLA_TSS2_RELEASE_HOLD_WN - TOYOTA_COROLLA_TSS2_UNWIND_SMOOTH_WN) * self.release_hold
    self.unwind_target_rate += (wn * wn * (setpoint - self.unwind_target) - 2.0 * wn * self.unwind_target_rate) * self.dt
    self.unwind_target += self.unwind_target_rate * self.dt
    unwind_blend = self.unwind_blend.update(get_toyota_corolla_tss2_unwind_weight(setpoint, desired_lateral_jerk, CS.vEgo))
    unwind_offset = 0.0 if CS.steeringPressed else max(unwind_blend, self.release_hold) * (self.unwind_target - setpoint)
    setpoint += unwind_offset
    gravity_adjusted_future_lateral_accel += unwind_offset
    desired_lateral_accel_magnitude_rate = (abs(setpoint) - abs(self.prev_desired_lateral_accel)) / self.dt
    unwind_detected = desired_lateral_accel_magnitude_rate < UNWIND_D_DES_THRESHOLD and abs(setpoint) < UNWIND_LAT_ACCEL_NEAR_ZERO
    self.prev_desired_lateral_accel = setpoint
    measurement_rate = self.measurement_rate_filter.update((measurement - self.previous_measurement) / self.dt)
    measurement_rate = float(np.clip(measurement_rate, -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP))
    self.previous_measurement = measurement
    low_speed_factor = (np.interp(CS.vEgo, LOW_SPEED_X, LOW_SPEED_Y) / max(CS.vEgo, MIN_SPEED)) ** 2
    current_kp = np.interp(CS.vEgo, parent.pid._k_p[0], parent.pid._k_p[1])
    error = setpoint - measurement
    error_with_lsf = error * (1 + low_speed_factor / max(current_kp, 1e-3))
    pid_log.error = float(error_with_lsf)
    ff = gravity_adjusted_future_lateral_accel - parent.torque_params.latAccelOffset * roll_offset_fade
    ff *= get_toyota_corolla_tss2_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
    friction_threshold = get_toyota_corolla_tss2_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
    friction_jerk_deadzone = center_chatter_friction_jerk_deadzone(CS.vEgo, setpoint, 0.0)
    friction_jerk = math.copysign(max(abs(desired_lateral_jerk) - friction_jerk_deadzone, 0.0), desired_lateral_jerk)
    ff, friction_threshold = auto_stages.apply_stages(parent, CS, setpoint, desired_lateral_jerk, ff, friction_threshold,
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)
    selected_friction = get_friction(error_with_lsf + JERK_GAIN * friction_jerk, lateral_accel_deadzone, friction_threshold, parent.torque_params)
    ff += auto_stages.apply_selected_friction(parent, CS, setpoint, float(selected_friction),
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)
    if CS.vEgo < self.low_speed_reset_threshold:
      parent.pid.reset()
    freeze_integrator = steer_limited_by_safety or CS.steeringPressed or CS.vEgo < self.low_speed_reset_threshold or unwind_detected
    output_lataccel = parent.pid.update(pid_log.error, error_rate=-measurement_rate, speed=CS.vEgo, feedforward=ff, freeze_integrator=freeze_integrator)
    output_torque = parent.torque_from_lateral_accel(output_lataccel, parent.torque_params)
    output_torque = apply_turn_assist(parent, CS, VM, params, desired_curvature, output_torque)
    output_torque *= get_toyota_corolla_tss2_center_output_scale(setpoint, CS.vEgo)
    pid_log.active = True
    pid_log.p = float(parent.pid.p)
    pid_log.i = float(parent.pid.i)
    pid_log.d = float(parent.pid.d)
    pid_log.f = float(parent.pid.f)
    pid_log.output = float(-output_torque)
    pid_log.actualLateralAccel = float(measurement)
    pid_log.desiredLateralAccel = float(setpoint)
    pid_log.desiredLateralJerk = float(desired_lateral_jerk)
    pid_log.saturated = bool(parent._check_saturation(parent.steer_max - abs(output_torque) < 1e-3, CS, steer_limited_by_safety, curvature_limited))
    self.prev_steering_pressed = CS.steeringPressed
    return -output_torque, 0.0, pid_log
