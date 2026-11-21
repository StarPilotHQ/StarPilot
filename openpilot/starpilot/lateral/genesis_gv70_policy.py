"""First-generation Electrified GV70 torque shaping."""

from openpilot.starpilot.flm import torque_stages as auto_stages
import math
from collections import deque

import numpy as np

from openpilot.starpilot.lateral.torque_extension import apply_turn_assist
from openpilot.cereal import log
from opendbc.car import structs
from opendbc.car.hyundai.values import CAR
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
  sigmoid as _gv70_sigmoid,
)


from openpilot.starpilot.lateral.gv70_shaping import (
  get_genesis_gv70_friction_threshold,
  get_genesis_gv70_friction_jerk_deadzone,
  get_genesis_gv70_center_output_scale,
  get_genesis_gv70_unwind_ff_scale,
  get_genesis_gv70_high_speed_error_scale,
  get_genesis_gv70_reversal_output_scale,
  get_genesis_gv70_low_speed_center_overshoot_scale,
  get_genesis_gv70_stabilized_output,
  GENESIS_GV70_MEASUREMENT_DAMPING_SPEED_BP, GENESIS_GV70_MEASUREMENT_DAMPING_V,
)


# --- Constants derived from the Ioniq 6 policy, adapted for the GV70 ---
# All constants derived from the Ioniq 6 policy are kept in this one
# table; do not scatter them elsewhere in this file.
# Sign convention: on the GV70, left turns (steering angle > 0) have
# setpoint/desLat < 0 and right turns have setpoint/desLat > 0. The
# Ioniq 6 code labels desLat >= 0 "left" and desLat < 0 "right" -- the
# opposite of the GV70 -- so the values below are transferred by sign,
# not by Ioniq label. The value Ioniq applies at desLat < 0 (its
# "right" constant) is therefore the GV70 left value, and vice versa.
# Hence GV70 left gets the larger constants and GV70 right the smaller.
# The constants are derived from the Ioniq 6 policy, sign-transferred
# for the GV70; they are not GV70-fitted values. They were retained
# after GV70 road testing.
# Crawl turn-in FF boost (Ioniq get_ioniq_6_ff_scale, crawl branch).
GV70_CRAWL_TURN_IN_FF_BOOST_LEFT = 0.24  # Ioniq CRAWL_TURN_IN_FF_BOOST_RIGHT
GV70_CRAWL_TURN_IN_FF_BOOST_RIGHT = 0.18  # Ioniq CRAWL_TURN_IN_FF_BOOST_LEFT
GV70_CRAWL_TURN_IN_FF_SPEED = 5.3
GV70_CRAWL_TURN_IN_FF_SPEED_WIDTH = 1.0
GV70_CRAWL_TURN_IN_FF_LAT = 0.06
GV70_CRAWL_TURN_IN_FF_LAT_WIDTH = 0.035
# Turn-in friction threshold reduction + friction boost
# (Ioniq get_ioniq_6_friction_threshold / get_ioniq_6_friction_scale,
# turn-in part only; the unwind parts are not carried over). The GV70
# has no phase-dependent friction change, so this is a separate block
# here, not an edit to shared torque_shaping.py or gv70_shaping.py.
# Clamps are Ioniq's overall clamps (threshold 0.82-1.18, friction
# 0.82-1.08); the GV70 friction base is 1.0, not Ioniq's 0.928 base
# multiplier -- that base difference is the one deliberate deviation.
GV70_TURN_IN_THRESHOLD_REDUCTION_LEFT = 1.42  # Ioniq ..._RIGHT
GV70_TURN_IN_THRESHOLD_REDUCTION_RIGHT = 0.78  # Ioniq ..._LEFT
GV70_TURN_IN_FRICTION_BOOST_LEFT = 0.94  # Ioniq ..._RIGHT
GV70_TURN_IN_FRICTION_BOOST_RIGHT = 0.44  # Ioniq ..._LEFT
GV70_TURN_IN_FRICTION_LAT_RISE = 0.20
GV70_TURN_IN_FRICTION_JERK_RISE = 0.24
GV70_TURN_IN_TRANSITION_SPEED = 10.0
GV70_TURN_IN_PHASE_SCALE = 0.10
GV70_TURN_IN_THRESHOLD_SCALE_MIN = 0.82
GV70_TURN_IN_THRESHOLD_SCALE_MAX = 1.18
GV70_TURN_IN_FRICTION_SCALE_MIN = 0.82
GV70_TURN_IN_FRICTION_SCALE_MAX = 1.08
# Low-speed calming. The setpoint smoothstep below returns 0 at
# |setpoint| <= 0.30 (full low-speed-factor reduction) and 1 at
# |setpoint| >= 0.50 (no reduction). Two trigger paths feed it: a
# standstill-history move-off path, and a state-based path covering
# decelerating approaches that never stood still. The speed-entry path
# uses 4.0/4.5 m/s hysteresis and a 1.5 s ramp-in; loss of both
# triggers releases the amount over 0.3 s.
GV70_CALM_SETPOINT_START = 0.30
GV70_CALM_SETPOINT_FULL = 0.50
GV70_CALM_MOVEOFF_FRAMES = 600
GV70_CALM_MOVEOFF_MAX_SPEED = 9.0
GV70_CALM_ENTER_SPEED = 4.0
GV70_CALM_EXIT_SPEED = 4.5
GV70_CALM_RAMP_IN_SECONDS = 1.5
GV70_CALM_RAMP_OUT_SECONDS = 0.3
# The helpers below mirror the named Ioniq 6 helpers, with the same
# signatures and formulas, for the branches the GV70 uses.
# ioniq6_policy.py itself is untouched and no Ioniq 6 function is
# renamed.
def _gv70_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  # Mirror of _ioniq_6_side_value, with the one intentional sign flip:
  # Ioniq returns left_value for desLat >= 0; GV70 left turns have
  # desLat < 0, so GV70 returns left_value for desLat < 0.
  return left_value if desired_lateral_accel < 0.0 else right_value


def _gv70_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  # GV70 version of _ioniq_6_transition_phase (identical formula).
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / GV70_TURN_IN_PHASE_SCALE)


def _gv70_transition_envelope(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  # GV70 version of _ioniq_6_transition_envelope (identical formula).
  low_speed_factor = 1.0 / (1.0 + (max(v_ego, 0.0) / GV70_TURN_IN_TRANSITION_SPEED) ** 2)
  lat_factor = 1.0 - math.exp(-abs(desired_lateral_accel) / GV70_TURN_IN_FRICTION_LAT_RISE)
  jerk_factor = 1.0 - math.exp(-abs(desired_lateral_jerk) / GV70_TURN_IN_FRICTION_JERK_RISE)
  return low_speed_factor * lat_factor * jerk_factor


def get_gv70_crawl_turn_in_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  """GV70 version of the crawl branch of get_ioniq_6_ff_scale.

  Signature mirrors get_ioniq_6_ff_scale (desLat, jerk, v) for the
  crawl branch only; the non-crawl terms of that helper (turn-in
  boost, unwind taper, directional taper, high-speed right boost) are
  not carried over. Constants are derived from the Ioniq 6 policy,
  sign-transferred per the table above, and were retained after GV70
  road testing.
  """
  if desired_lateral_accel * desired_lateral_jerk <= 0.0:
    return 1.0
  crawl_speed_weight = _gv70_sigmoid((GV70_CRAWL_TURN_IN_FF_SPEED - max(v_ego, 0.0)) /
                                     GV70_CRAWL_TURN_IN_FF_SPEED_WIDTH)
  crawl_lat_weight = _gv70_sigmoid((abs(desired_lateral_accel) - GV70_CRAWL_TURN_IN_FF_LAT) /
                                   GV70_CRAWL_TURN_IN_FF_LAT_WIDTH)
  crawl_turn_in_scale = _gv70_side_value(
    desired_lateral_accel,
    GV70_CRAWL_TURN_IN_FF_BOOST_LEFT,
    GV70_CRAWL_TURN_IN_FF_BOOST_RIGHT,
  ) * crawl_speed_weight * crawl_lat_weight
  return 1.0 + crawl_turn_in_scale


def get_gv70_turn_in_threshold_scale(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  """GV70 version of the turn-in part of get_ioniq_6_friction_threshold.

  Signature mirrors get_ioniq_6_friction_threshold (v, desLat, jerk).
  Returns a scale only (the GV70's own threshold from gv70_shaping.py
  is multiplied by it at the call site) because the GV70 base
  threshold differs from Ioniq's; the unwind-increase part of the
  Ioniq helper is not carried over. Constants are derived from the
  Ioniq 6 policy, sign-transferred per the table above, and were
  retained after GV70 road testing.
  """
  turn_in_weight = max(_gv70_transition_phase(desired_lateral_accel, desired_lateral_jerk), 0.0)
  threshold_scale = 1.0 - (_gv70_side_value(
    desired_lateral_accel,
    GV70_TURN_IN_THRESHOLD_REDUCTION_LEFT,
    GV70_TURN_IN_THRESHOLD_REDUCTION_RIGHT,
  ) * _gv70_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk) * turn_in_weight)
  return min(max(threshold_scale, GV70_TURN_IN_THRESHOLD_SCALE_MIN), GV70_TURN_IN_THRESHOLD_SCALE_MAX)


def get_gv70_turn_in_friction_scale(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  """GV70 version of the turn-in part of get_ioniq_6_friction_scale.

  Signature mirrors get_ioniq_6_friction_scale (v, desLat, jerk).
  The GV70 base is 1.0, not Ioniq's 0.928, and the unwind-reduction
  part of the Ioniq helper is not carried over. Constants are derived
  from the Ioniq 6 policy, sign-transferred per the table above, and
  were retained after GV70 road testing.
  """
  turn_in_weight = max(_gv70_transition_phase(desired_lateral_accel, desired_lateral_jerk), 0.0)
  friction_scale = 1.0
  friction_scale += (_gv70_side_value(desired_lateral_accel, GV70_TURN_IN_FRICTION_BOOST_LEFT, GV70_TURN_IN_FRICTION_BOOST_RIGHT) *
                     _gv70_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk) * turn_in_weight)
  return min(max(friction_scale, GV70_TURN_IN_FRICTION_SCALE_MIN), GV70_TURN_IN_FRICTION_SCALE_MAX)

def supported_cp(CP):
  return (
    CP.brand == "hyundai"
    and CP.carFingerprint == CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN
    and CP.steerControlType == structs.CarParams.SteerControlType.torque
    and CP.lateralTuning.which() == "torque"
    and not CP.dashcamOnly
    and not CP.passive
    and not CP.notCar
  )



class GenesisGV70TorquePolicy:
  def __init__(self, parent, CP):
    if not supported_cp(CP):
      raise ValueError("Electrified GV70 policy does not match CarParams")
    self.parent = parent
    self.dt = parent.dt
    parent.pid = PIDController([INTERP_SPEEDS, KP_INTERP], KI,
                               [GENESIS_GV70_MEASUREMENT_DAMPING_SPEED_BP, GENESIS_GV70_MEASUREMENT_DAMPING_V],
                               rate=1 / self.dt)
    parent.update_limits()
    self.request_buffer_len = int(LAT_ACCEL_REQUEST_BUFFER_SECONDS / self.dt)
    self.curvature_request_buffer = deque([0.0] * self.request_buffer_len, maxlen=self.request_buffer_len)
    self.jerk_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * LP_FILTER_CUTOFF_HZ), self.dt)
    self.measurement_rate_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * (MAX_LAT_JERK_UP - 0.5)), self.dt)
    self.previous_measurement = 0.0
    self.prev_desired_lateral_accel = 0.0
    self.prev_steering_pressed = False
    self.prev_output_torque = 0.0
    self.previous_feedforward = None
    self.low_speed_reset_threshold = max(CP.minSteerSpeed, 0.3)
    self._moveoff_frames = 0
    self._calm_lsf_amount = 0.0
    self._calm_speed_band = False

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited, lat_delay):
    parent = self.parent
    pid_log = log.ControlsState.LateralTorqueState.new_message()
    pid_log.version = 2
    measured_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
    measurement = measured_curvature * CS.vEgo**2
    future_desired_lateral_accel = desired_curvature * CS.vEgo**2
    if not active:
      parent.pid.reset()
      self.prev_output_torque = 0.0
      self.previous_feedforward = None
      self._moveoff_frames = 0
      self._calm_lsf_amount = 0.0
      self._calm_speed_band = False
      self.curvature_request_buffer.append(desired_curvature)
      self.previous_measurement = measurement
      self.measurement_rate_filter.x = 0.0
      self.jerk_filter.x = 0.0
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
    desired_lateral_accel_magnitude_rate = (abs(setpoint) - abs(self.prev_desired_lateral_accel)) / self.dt
    unwind_detected = desired_lateral_accel_magnitude_rate < UNWIND_D_DES_THRESHOLD and abs(setpoint) < UNWIND_LAT_ACCEL_NEAR_ZERO
    self.prev_desired_lateral_accel = setpoint
    measurement_rate = self.measurement_rate_filter.update((measurement - self.previous_measurement) / self.dt)
    measurement_rate = float(np.clip(measurement_rate, -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP))
    self.previous_measurement = measurement
    # Low-speed calming. The setpoint gate is 0 at |setpoint| <= 0.30,
    # 1 at >= 0.50, smoothstep between. The amount state controls how
    # much of that reduction is currently applied, so changes in the
    # speed-entry path are ramped rather than stepped. At
    # |setpoint| >= 0.50 the effective scale is 1 regardless of amount,
    # so a real turn restores full low-speed factor immediately.
    if CS.standstill:
      self._moveoff_frames = 0
    else:
      self._moveoff_frames += 1
    if CS.vEgo <= GV70_CALM_ENTER_SPEED:
      self._calm_speed_band = True
    elif CS.vEgo >= GV70_CALM_EXIT_SPEED:
      self._calm_speed_band = False
    _calm_setpoint_scale_t = float(np.clip((abs(setpoint) - GV70_CALM_SETPOINT_START) /
                                           (GV70_CALM_SETPOINT_FULL - GV70_CALM_SETPOINT_START), 0.0, 1.0))
    _calm_setpoint_scale = _calm_setpoint_scale_t * _calm_setpoint_scale_t * (3.0 - 2.0 * _calm_setpoint_scale_t)
    _moveoff_calm = (self._moveoff_frames <= GV70_CALM_MOVEOFF_FRAMES and CS.vEgo < GV70_CALM_MOVEOFF_MAX_SPEED and
                     abs(setpoint) < GV70_CALM_SETPOINT_FULL)
    _speed_entry_calm = self._calm_speed_band and abs(setpoint) < GV70_CALM_SETPOINT_FULL
    if _moveoff_calm:
      # A true move-off is eligible from its first frames; standstill
      # history is itself the dwell/confirmation.
      self._calm_lsf_amount = 1.0
    elif _speed_entry_calm:
      self._calm_lsf_amount = min(1.0, self._calm_lsf_amount + self.dt / GV70_CALM_RAMP_IN_SECONDS)
    else:
      self._calm_lsf_amount = max(0.0, self._calm_lsf_amount - self.dt / GV70_CALM_RAMP_OUT_SECONDS)
    _lsf_scale = 1.0 - self._calm_lsf_amount * (1.0 - _calm_setpoint_scale)
    low_speed_factor = (np.interp(CS.vEgo, LOW_SPEED_X, LOW_SPEED_Y) / max(CS.vEgo, MIN_SPEED)) ** 2
    low_speed_factor *= _lsf_scale
    current_kp = np.interp(CS.vEgo, parent.pid._k_p[0], parent.pid._k_p[1])
    error = setpoint - measurement
    error_with_lsf = error * (1 + low_speed_factor / max(current_kp, 1e-3))
    pid_log.error = float(error_with_lsf)
    ff = gravity_adjusted_future_lateral_accel - parent.torque_params.latAccelOffset * roll_offset_fade
    ff *= get_genesis_gv70_unwind_ff_scale(setpoint, measurement, desired_lateral_jerk, CS.vEgo)
    # Crawl turn-in FF boost (GV70 version of the crawl branch of
    # get_ioniq_6_ff_scale).
    ff *= get_gv70_crawl_turn_in_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
    friction_threshold = get_genesis_gv70_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
    # Turn-in friction threshold scale (separate block; the GV70's own
    # threshold from gv70_shaping.py is left untouched).
    friction_threshold *= get_gv70_turn_in_threshold_scale(CS.vEgo, setpoint, desired_lateral_jerk)
    vehicle_deadzone = get_genesis_gv70_friction_jerk_deadzone(CS.vEgo, setpoint)
    friction_jerk_deadzone = center_chatter_friction_jerk_deadzone(CS.vEgo, setpoint, vehicle_deadzone)
    friction_jerk = math.copysign(max(abs(desired_lateral_jerk) - friction_jerk_deadzone, 0.0), desired_lateral_jerk)
    ff, friction_threshold = auto_stages.apply_stages(parent, CS, setpoint, desired_lateral_jerk, ff, friction_threshold,
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)
    selected_friction = parent.friction(error_with_lsf + JERK_GAIN * friction_jerk, lateral_accel_deadzone, friction_threshold, CS, setpoint)
    # Turn-in friction scale (separate block; base is 1.0 for the
    # GV70, not Ioniq's 0.928).
    selected_friction = float(selected_friction) * get_gv70_turn_in_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
    ff += auto_stages.apply_selected_friction(parent, CS, setpoint, float(selected_friction),
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)
    if CS.vEgo < self.low_speed_reset_threshold:
      parent.pid.reset()
    # Filter feedforward before PID so feedback remains immediate.
    ff *= get_genesis_gv70_center_output_scale(setpoint, CS.vEgo)
    ff *= get_genesis_gv70_low_speed_center_overshoot_scale(setpoint, measurement, CS.vEgo)
    ff *= get_genesis_gv70_high_speed_error_scale(setpoint, measurement, desired_lateral_jerk, CS.vEgo)
    ff *= get_genesis_gv70_reversal_output_scale(setpoint, measurement, desired_lateral_jerk, CS.vEgo)
    if not CS.steeringPressed and not self.prev_steering_pressed and self.previous_feedforward is not None:
      ff = get_genesis_gv70_stabilized_output(ff, self.previous_feedforward, setpoint,
                                            desired_lateral_jerk, CS.vEgo, self.dt)
    self.previous_feedforward = ff
    error_rate = 0.0 if CS.steeringPressed else -measurement_rate
    freeze_integrator = steer_limited_by_safety or CS.steeringPressed or CS.vEgo < self.low_speed_reset_threshold or unwind_detected
    output_lataccel = parent.pid.update(pid_log.error, error_rate=error_rate, speed=CS.vEgo, feedforward=ff, freeze_integrator=freeze_integrator)
    output_torque = parent.torque_from_lateral_accel(output_lataccel, parent.torque_params)
    output_torque = apply_turn_assist(parent, CS, VM, params, desired_curvature, output_torque)
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
    self.prev_output_torque = float(output_torque)
    return -output_torque, 0.0, pid_log
