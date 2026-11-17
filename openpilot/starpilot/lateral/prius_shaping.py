"""Pre-rebase Prius torque shaping, retaining the original rack-specific defaults."""

import math

import numpy as np

from openpilot.common.constants import CV
from openpilot.starpilot.lateral.torque_shaping import sigmoid


PRIUS_TRANSITION_SPEED = 11.0
PRIUS_PHASE_SCALE = 0.09
PRIUS_FF_GAIN_LEFT = 0.06
PRIUS_FF_GAIN_RIGHT = 0.07
PRIUS_FF_ONSET = 0.15
PRIUS_FF_ONSET_WIDTH = 0.08
PRIUS_FF_CUTOFF = 1.3
PRIUS_FF_CUTOFF_WIDTH = 0.32
PRIUS_FRICTION_LAT_RISE = 0.18
PRIUS_FRICTION_JERK_RISE = 0.22
PRIUS_TURN_IN_BOOST_LEFT = 0.24
PRIUS_TURN_IN_BOOST_RIGHT = 0.22
PRIUS_UNWIND_TAPER_LEFT = 0.42
PRIUS_UNWIND_TAPER_RIGHT = 0.62
PRIUS_TURN_IN_THRESHOLD_REDUCTION_LEFT = 0.14
PRIUS_TURN_IN_THRESHOLD_REDUCTION_RIGHT = 0.14
PRIUS_UNWIND_THRESHOLD_INCREASE_LEFT = 0.4
PRIUS_UNWIND_THRESHOLD_INCREASE_RIGHT = 0.56
PRIUS_TURN_IN_FRICTION_BOOST_LEFT = 0.06
PRIUS_TURN_IN_FRICTION_BOOST_RIGHT = 0.06
PRIUS_UNWIND_FRICTION_REDUCTION_LEFT = 0.22
PRIUS_UNWIND_FRICTION_REDUCTION_RIGHT = 0.3
PRIUS_CENTER_TAPER_MAX = 0.155
PRIUS_CENTER_TAPER_LAT = 0.24
PRIUS_CENTER_TAPER_LAT_WIDTH = 0.035
PRIUS_CENTER_TAPER_SPEED = 18.0
PRIUS_CENTER_TAPER_SPEED_WIDTH = 2.2
PRIUS_CENTER_FRICTION_THRESHOLD_GAIN = 0.08
PRIUS_CENTER_FRICTION_THRESHOLD_LAT = 0.3
PRIUS_CENTER_FRICTION_THRESHOLD_LAT_WIDTH = 0.07
PRIUS_CENTER_FRICTION_THRESHOLD_SPEED = 18.0
PRIUS_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH = 2.2
PRIUS_FRICTION_JERK_DEADZONE_MAX = 0.24
PRIUS_STANDARD_FRICTION_JERK_DEADZONE_MAX = 0.3
PRIUS_FRICTION_JERK_DEADZONE_LAT = 0.3
PRIUS_FRICTION_JERK_DEADZONE_LAT_WIDTH = 0.07
PRIUS_FRICTION_JERK_DEADZONE_SPEED = 18.0
PRIUS_FRICTION_JERK_DEADZONE_SPEED_WIDTH = 2.2
PRIUS_HIGH_SPEED_OUTPUT_TAPER_MAX = 0.06
PRIUS_STANDARD_HIGH_SPEED_OUTPUT_TAPER_MAX = 0.1
PRIUS_HIGH_SPEED_OUTPUT_TAPER_LAT = 0.3
PRIUS_HIGH_SPEED_OUTPUT_TAPER_LAT_WIDTH = 0.35
PRIUS_HIGH_SPEED_OUTPUT_TAPER_SPEED = 22.0
PRIUS_HIGH_SPEED_OUTPUT_TAPER_SPEED_WIDTH = 2.5


def base_friction_threshold(v_ego: float) -> float:
  return float(np.interp(v_ego, [1 * CV.MPH_TO_MS, 20 * CV.MPH_TO_MS, 75 * CV.MPH_TO_MS], [0.16, 0.19, 0.27]))


def _prius_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / PRIUS_TRANSITION_SPEED) ** 2)


def _prius_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh(desired_lateral_accel * desired_lateral_jerk / PRIUS_PHASE_SCALE)


def _prius_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value


def _prius_transition_envelope(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  lat_factor = 1.0 - math.exp(-abs(desired_lateral_accel) / PRIUS_FRICTION_LAT_RISE)
  jerk_factor = 1.0 - math.exp(-abs(desired_lateral_jerk) / PRIUS_FRICTION_JERK_RISE)
  return _prius_low_speed_factor(v_ego) * lat_factor * jerk_factor


def get_prius_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0
  gain = _prius_side_value(desired_lateral_accel, PRIUS_FF_GAIN_LEFT, PRIUS_FF_GAIN_RIGHT)
  abs_lateral_accel = abs(desired_lateral_accel)
  onset = sigmoid((abs_lateral_accel - PRIUS_FF_ONSET) / PRIUS_FF_ONSET_WIDTH)
  cutoff = sigmoid((PRIUS_FF_CUTOFF - abs_lateral_accel) / PRIUS_FF_CUTOFF_WIDTH)
  extra_scale = gain * onset * cutoff
  phase = _prius_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  low_speed_factor = _prius_low_speed_factor(v_ego)
  turn_in_boost = 1.0 + _prius_side_value(desired_lateral_accel, PRIUS_TURN_IN_BOOST_LEFT, PRIUS_TURN_IN_BOOST_RIGHT) * turn_in_weight * (
    0.35 + 0.65 * low_speed_factor
  )
  unwind_taper = 1.0 - _prius_side_value(desired_lateral_accel, PRIUS_UNWIND_TAPER_LEFT, PRIUS_UNWIND_TAPER_RIGHT) * unwind_weight * (
    0.35 + 0.65 * low_speed_factor
  )
  return 1.0 + extra_scale * turn_in_boost * max(unwind_taper, 0.0)


def get_prius_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  base_threshold = base_friction_threshold(v_ego)
  transition_envelope = _prius_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _prius_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  threshold_scale = (
    1.0
    - _prius_side_value(desired_lateral_accel, PRIUS_TURN_IN_THRESHOLD_REDUCTION_LEFT, PRIUS_TURN_IN_THRESHOLD_REDUCTION_RIGHT)
    * transition_envelope
    * turn_in_weight
  )
  threshold_scale += (
    _prius_side_value(desired_lateral_accel, PRIUS_UNWIND_THRESHOLD_INCREASE_LEFT, PRIUS_UNWIND_THRESHOLD_INCREASE_RIGHT) * transition_envelope * unwind_weight
  )
  center_speed_weight = sigmoid((v_ego - PRIUS_CENTER_FRICTION_THRESHOLD_SPEED) / PRIUS_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH)
  center_lat_weight = sigmoid((PRIUS_CENTER_FRICTION_THRESHOLD_LAT - abs(desired_lateral_accel)) / PRIUS_CENTER_FRICTION_THRESHOLD_LAT_WIDTH)
  threshold_scale += PRIUS_CENTER_FRICTION_THRESHOLD_GAIN * center_speed_weight * center_lat_weight
  return base_threshold * min(max(threshold_scale, 0.86), 1.16)


def get_prius_friction_scale(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  transition_envelope = _prius_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _prius_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  friction_scale = 1.0
  friction_scale += (
    _prius_side_value(desired_lateral_accel, PRIUS_TURN_IN_FRICTION_BOOST_LEFT, PRIUS_TURN_IN_FRICTION_BOOST_RIGHT) * transition_envelope * turn_in_weight
  )
  friction_scale -= (
    _prius_side_value(desired_lateral_accel, PRIUS_UNWIND_FRICTION_REDUCTION_LEFT, PRIUS_UNWIND_FRICTION_REDUCTION_RIGHT) * transition_envelope * unwind_weight
  )
  return min(max(friction_scale, 0.9), 1.14)


def get_prius_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = sigmoid((v_ego - PRIUS_CENTER_TAPER_SPEED) / PRIUS_CENTER_TAPER_SPEED_WIDTH)
  center_weight = sigmoid((PRIUS_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / PRIUS_CENTER_TAPER_LAT_WIDTH)
  reduction = PRIUS_CENTER_TAPER_MAX * speed_weight * center_weight
  return 1.0 - reduction


def get_prius_friction_jerk_deadzone(v_ego: float, desired_lateral_accel: float, deadzone_max: float = PRIUS_FRICTION_JERK_DEADZONE_MAX) -> float:
  speed_weight = sigmoid((v_ego - PRIUS_FRICTION_JERK_DEADZONE_SPEED) / PRIUS_FRICTION_JERK_DEADZONE_SPEED_WIDTH)
  center_weight = sigmoid((PRIUS_FRICTION_JERK_DEADZONE_LAT - abs(desired_lateral_accel)) / PRIUS_FRICTION_JERK_DEADZONE_LAT_WIDTH)
  return deadzone_max * speed_weight * center_weight


def get_prius_high_speed_output_taper_scale(desired_lateral_accel: float, v_ego: float, taper_max: float = PRIUS_HIGH_SPEED_OUTPUT_TAPER_MAX) -> float:
  speed_weight = sigmoid((v_ego - PRIUS_HIGH_SPEED_OUTPUT_TAPER_SPEED) / PRIUS_HIGH_SPEED_OUTPUT_TAPER_SPEED_WIDTH)
  curve_weight = sigmoid((abs(desired_lateral_accel) - PRIUS_HIGH_SPEED_OUTPUT_TAPER_LAT) / PRIUS_HIGH_SPEED_OUTPUT_TAPER_LAT_WIDTH)
  return 1.0 - taper_max * speed_weight * curve_weight
