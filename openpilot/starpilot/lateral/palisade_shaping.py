import math

from openpilot.starpilot.lateral.bolt_shaping import get_gm_base_friction_threshold


PALISADE_BASE_LAT_ACCEL_FACTOR_MULT = 0.98

PALISADE_FF_GAIN_LEFT = 0.14

PALISADE_FF_GAIN_RIGHT = 0.12

PALISADE_FF_ONSET = 0.08

PALISADE_FF_ONSET_WIDTH = 0.04

PALISADE_FF_CUTOFF = 1.25

PALISADE_FF_CUTOFF_WIDTH = 0.36

PALISADE_TRANSITION_SPEED = 9.0

PALISADE_PHASE_SCALE = 0.11

PALISADE_TURN_IN_BOOST_LEFT = 0.44

PALISADE_TURN_IN_BOOST_RIGHT = 0.34

PALISADE_UNWIND_TAPER_LEFT = 0.18

PALISADE_UNWIND_TAPER_RIGHT = 0.30

PALISADE_FRICTION_MULT = 1.02

PALISADE_FRICTION_LAT_RISE = 0.20

PALISADE_FRICTION_JERK_RISE = 0.24

PALISADE_TURN_IN_THRESHOLD_REDUCTION_LEFT = 0.18

PALISADE_TURN_IN_THRESHOLD_REDUCTION_RIGHT = 0.14

PALISADE_UNWIND_THRESHOLD_INCREASE_LEFT = 0.14

PALISADE_UNWIND_THRESHOLD_INCREASE_RIGHT = 0.22

PALISADE_TURN_IN_FRICTION_BOOST_LEFT = 0.08

PALISADE_TURN_IN_FRICTION_BOOST_RIGHT = 0.06

PALISADE_UNWIND_FRICTION_REDUCTION_LEFT = 0.12

PALISADE_UNWIND_FRICTION_REDUCTION_RIGHT = 0.20

PALISADE_CENTER_TAPER_MAX = 0.12

PALISADE_CENTER_TAPER_LAT = 0.28

PALISADE_CENTER_TAPER_LAT_WIDTH = 0.055

PALISADE_CENTER_TAPER_SPEED = 12.0

PALISADE_CENTER_TAPER_SPEED_WIDTH = 2.5

PALISADE_CENTER_OUTPUT_TAPER_MAX = 0.18

PALISADE_CENTER_OUTPUT_TAPER_LAT = 0.28

PALISADE_CENTER_OUTPUT_TAPER_LAT_WIDTH = 0.055

PALISADE_CENTER_OUTPUT_TAPER_SPEED = 15.0

PALISADE_CENTER_OUTPUT_TAPER_SPEED_WIDTH = 3.0

def _sigmoid(x: float) -> float:
  if x >= 0.0:
    z = math.exp(-x)
    return 1.0 / (1.0 + z)

  z = math.exp(x)
  return z / (1.0 + z)

def _palisade_sigmoid(x: float) -> float:
  return _sigmoid(x)

def _palisade_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / PALISADE_TRANSITION_SPEED) ** 2)

def _palisade_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / PALISADE_PHASE_SCALE)

def _palisade_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value

def _palisade_transition_envelope(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  lat_factor = 1.0 - math.exp(-abs(desired_lateral_accel) / PALISADE_FRICTION_LAT_RISE)
  jerk_factor = 1.0 - math.exp(-abs(desired_lateral_jerk) / PALISADE_FRICTION_JERK_RISE)
  return _palisade_low_speed_factor(v_ego) * lat_factor * jerk_factor

def get_palisade_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  gain = _palisade_side_value(desired_lateral_accel, PALISADE_FF_GAIN_LEFT, PALISADE_FF_GAIN_RIGHT)
  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _palisade_sigmoid((abs_lateral_accel - PALISADE_FF_ONSET) / PALISADE_FF_ONSET_WIDTH)
  cutoff = _palisade_sigmoid((PALISADE_FF_CUTOFF - abs_lateral_accel) / PALISADE_FF_CUTOFF_WIDTH)
  extra_scale = gain * onset * cutoff
  phase = _palisade_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  low_speed_factor = _palisade_low_speed_factor(v_ego)
  turn_in_boost = 1.0 + (_palisade_side_value(desired_lateral_accel, PALISADE_TURN_IN_BOOST_LEFT, PALISADE_TURN_IN_BOOST_RIGHT) *
                          turn_in_weight * (0.35 + 0.65 * low_speed_factor))
  unwind_taper = 1.0 - (_palisade_side_value(desired_lateral_accel, PALISADE_UNWIND_TAPER_LEFT, PALISADE_UNWIND_TAPER_RIGHT) *
                         unwind_weight * (0.35 + 0.65 * low_speed_factor))
  return 1.0 + (extra_scale * turn_in_boost * max(unwind_taper, 0.0))

def get_palisade_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  base_threshold = get_gm_base_friction_threshold(v_ego)
  transition_envelope = _palisade_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _palisade_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  threshold_scale = 1.0 - (_palisade_side_value(desired_lateral_accel, PALISADE_TURN_IN_THRESHOLD_REDUCTION_LEFT, PALISADE_TURN_IN_THRESHOLD_REDUCTION_RIGHT) *
                           transition_envelope * turn_in_weight)
  threshold_scale += (_palisade_side_value(desired_lateral_accel, PALISADE_UNWIND_THRESHOLD_INCREASE_LEFT, PALISADE_UNWIND_THRESHOLD_INCREASE_RIGHT) *
                      transition_envelope * unwind_weight)
  return base_threshold * min(max(threshold_scale, 0.84), 1.14)

def get_palisade_friction_scale(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  transition_envelope = _palisade_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _palisade_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  friction_scale = PALISADE_FRICTION_MULT
  friction_scale += (_palisade_side_value(desired_lateral_accel, PALISADE_TURN_IN_FRICTION_BOOST_LEFT, PALISADE_TURN_IN_FRICTION_BOOST_RIGHT) *
                     transition_envelope * turn_in_weight)
  friction_scale -= (_palisade_side_value(desired_lateral_accel, PALISADE_UNWIND_FRICTION_REDUCTION_LEFT, PALISADE_UNWIND_FRICTION_REDUCTION_RIGHT) *
                     transition_envelope * unwind_weight)
  return min(max(friction_scale, 0.92), 1.12)

def get_palisade_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _palisade_sigmoid((v_ego - PALISADE_CENTER_TAPER_SPEED) / PALISADE_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _palisade_sigmoid((PALISADE_CENTER_TAPER_LAT - abs(desired_lateral_accel)) /
                                    PALISADE_CENTER_TAPER_LAT_WIDTH)
  return 1.0 - (PALISADE_CENTER_TAPER_MAX * speed_weight * center_weight)

def get_palisade_center_output_scale(desired_lateral_accel: float, v_ego: float, *, taper_max: float = PALISADE_CENTER_OUTPUT_TAPER_MAX) -> float:
  """Reduce high-speed center corrections without reducing normal turn authority."""
  speed_weight = _palisade_sigmoid((v_ego - PALISADE_CENTER_OUTPUT_TAPER_SPEED) /
                                    PALISADE_CENTER_OUTPUT_TAPER_SPEED_WIDTH)
  center_weight = _palisade_sigmoid((PALISADE_CENTER_OUTPUT_TAPER_LAT - abs(desired_lateral_accel)) /
                                    PALISADE_CENTER_OUTPUT_TAPER_LAT_WIDTH)
  return 1.0 - (taper_max * speed_weight * center_weight)
