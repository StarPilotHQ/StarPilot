"""Ioniq 6 torque shaping; native control retains output ownership and limits."""

from __future__ import annotations
from openpilot.starpilot.flm import torque_stages as auto_stages

import math
import numpy as np

from opendbc.car import structs
from opendbc.car.hyundai.values import CAR
from openpilot.common.constants import CV
from openpilot.common.scalar_clip import clip_scalar
from openpilot.starpilot.lateral.torque_shaping import (
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
)

HKG_CANFD_BASE_FRICTION_THRESHOLD = 0.39
IONIQ_6_CARS = (CAR.HYUNDAI_IONIQ_6,)


def get_hkg_canfd_base_friction_threshold(v_ego: float) -> float:
  base = float(np.interp(v_ego, [1 * CV.MPH_TO_MS, 20 * CV.MPH_TO_MS, 75 * CV.MPH_TO_MS], [0.16, 0.19, 0.27]))
  return max(base, HKG_CANFD_BASE_FRICTION_THRESHOLD)


def is_ioniq_6_2025_model(CP) -> bool:
  if getattr(CP, "carFingerprint", None) not in IONIQ_6_CARS:
    return False
  try:
    versions = [bytes(fw.fwVersion).decode("ascii", errors="ignore")
                if isinstance(fw.fwVersion, (bytes, bytearray, memoryview)) else str(fw.fwVersion)
                for fw in CP.carFw]
  except (AttributeError, TypeError, ValueError):
    return False
  return any("230915" in version for version in versions) and any("240206" in version for version in versions)

IONIQ_6_FF_GAIN_LEFT = 0.045

IONIQ_6_FF_GAIN_RIGHT = 0.015

IONIQ_6_BASE_LAT_ACCEL_FACTOR_MULT = 1.22

IONIQ_6_BASE_FRICTION_THRESHOLD = HKG_CANFD_BASE_FRICTION_THRESHOLD

IONIQ_6_FF_ONSET = 0.10

IONIQ_6_FF_ONSET_WIDTH = 0.04

IONIQ_6_FF_CUTOFF = 0.48

IONIQ_6_FF_CUTOFF_WIDTH = 0.12

IONIQ_6_TRANSITION_SPEED = 10.0

IONIQ_6_PHASE_SCALE = 0.10

IONIQ_6_TURN_IN_BOOST_LEFT = 1.64

IONIQ_6_TURN_IN_BOOST_RIGHT = 2.10

IONIQ_6_UNWIND_TAPER_LEFT = 3.18

IONIQ_6_UNWIND_TAPER_RIGHT = 8.20

IONIQ_6_FRICTION_MULT = 0.928

IONIQ_6_FRICTION_LAT_RISE = 0.20

IONIQ_6_FRICTION_JERK_RISE = 0.24

IONIQ_6_TURN_IN_THRESHOLD_REDUCTION_LEFT = 0.78

IONIQ_6_TURN_IN_THRESHOLD_REDUCTION_RIGHT = 1.42

IONIQ_6_UNWIND_THRESHOLD_INCREASE_LEFT = 3.90

IONIQ_6_UNWIND_THRESHOLD_INCREASE_RIGHT = 10.20

IONIQ_6_TURN_IN_FRICTION_BOOST_LEFT = 0.44

IONIQ_6_TURN_IN_FRICTION_BOOST_RIGHT = 0.94

IONIQ_6_UNWIND_FRICTION_REDUCTION_LEFT = 3.55

IONIQ_6_UNWIND_FRICTION_REDUCTION_RIGHT = 9.10

IONIQ_6_CENTER_TAPER_MAX = 0.082

IONIQ_6_CENTER_TAPER_LAT = 0.24

IONIQ_6_CENTER_TAPER_LAT_WIDTH = 0.025

IONIQ_6_CENTER_TAPER_SPEED = 18.0

IONIQ_6_CENTER_TAPER_SPEED_WIDTH = 2.5

IONIQ_6_HIGHWAY_CENTER_TAPER_MAX = 0.046

IONIQ_6_HIGHWAY_CENTER_TAPER_LAT = 0.10

IONIQ_6_HIGHWAY_CENTER_TAPER_LAT_WIDTH = 0.035

IONIQ_6_HIGHWAY_CENTER_TAPER_SPEED = 24.5

IONIQ_6_HIGHWAY_CENTER_TAPER_SPEED_WIDTH = 1.8

IONIQ_6_HIGHWAY_OUTPUT_TAPER_MAX = 0.10

IONIQ_6_HIGHWAY_OUTPUT_TAPER_LAT = 0.14

IONIQ_6_HIGHWAY_OUTPUT_TAPER_LAT_WIDTH = 0.04

IONIQ_6_HIGHWAY_OUTPUT_TAPER_SPEED = 23.5

IONIQ_6_HIGHWAY_OUTPUT_TAPER_SPEED_WIDTH = 2.0

IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_MAX = 0.18

IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_LAT = 1.05

IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_LAT_WIDTH = 0.22

IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_JERK = 0.24

IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_JERK_WIDTH = 0.14

IONIQ_6_LOW_MID_CENTER_TAPER_MAX = 0.088

IONIQ_6_LOW_MID_CENTER_TAPER_LAT = 0.28

IONIQ_6_LOW_MID_CENTER_TAPER_LAT_WIDTH = 0.06

IONIQ_6_LOW_MID_CENTER_TAPER_SPEED_MIN = 8.5

IONIQ_6_LOW_MID_CENTER_TAPER_SPEED_MAX = 16.5

IONIQ_6_LOW_MID_CENTER_TAPER_SPEED_WIDTH = 1.5

IONIQ_6_DIRECTIONAL_TAPER_LAT_START = 0.19

IONIQ_6_DIRECTIONAL_TAPER_LAT_END = 0.90

IONIQ_6_DIRECTIONAL_TAPER_LAT_WIDTH = 0.06

IONIQ_6_DIRECTIONAL_TAPER_BASE_LEFT = 0.11

IONIQ_6_DIRECTIONAL_TAPER_BASE_RIGHT = 0.45

IONIQ_6_DIRECTIONAL_TAPER_UNWIND_LEFT = 1.10

IONIQ_6_DIRECTIONAL_TAPER_UNWIND_RIGHT = 2.10

IONIQ_6_DIRECTIONAL_TAPER_FLOOR_LEFT = 0.48

IONIQ_6_DIRECTIONAL_TAPER_FLOOR_RIGHT = 0.52

IONIQ_6_DIRECTIONAL_TAPER_UNWIND_FLOOR_LEFT = 0.20

IONIQ_6_DIRECTIONAL_TAPER_UNWIND_FLOOR_RIGHT = 0.10

IONIQ_6_DIRECTIONAL_TAPER_JERK_ONSET = 1.00

IONIQ_6_DIRECTIONAL_TAPER_JERK_WIDTH = 0.30

IONIQ_6_DIRECTIONAL_TAPER_PHASE_SCALE = 0.45

IONIQ_6_DIRECTIONAL_TAPER_FILTER_RC = 0.4

IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF = 0.98

IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF_SPEED = 11.2

IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF_SPEED_WIDTH = 1.5

IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF_LAT = 0.10

IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF_LAT_WIDTH = 0.06

IONIQ_6_UNWIND_HIGH_SPEED_SPEED = 23.2

IONIQ_6_UNWIND_HIGH_SPEED_SPEED_WIDTH = 1.7

IONIQ_6_CRAWL_TURN_IN_FF_BOOST_LEFT = 0.18

IONIQ_6_CRAWL_TURN_IN_FF_BOOST_RIGHT = 0.24

IONIQ_6_CRAWL_TURN_IN_FF_SPEED = 5.3

IONIQ_6_CRAWL_TURN_IN_FF_SPEED_WIDTH = 1.0

IONIQ_6_CRAWL_TURN_IN_FF_LAT = 0.06

IONIQ_6_CRAWL_TURN_IN_FF_LAT_WIDTH = 0.035

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_MAX_TORQUE = 0.46

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_SPEED = 3.25

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_SPEED_WIDTH = 0.45

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ERROR = 1.9

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ERROR_WIDTH = 1.20

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_DESIRED_ANGLE = 5.5

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_DESIRED_ANGLE_WIDTH = 2.4

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_TRACK_RATIO_START = 0.66

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_TRACK_RATIO_WIDTH = 0.12

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_TRACK_RATIO_FLOOR = 0.26

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ADD_BP = [0.0, 0.35, 0.65, 1.0]

IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ADD_V = [1.0, 1.0, 0.88, 0.08]

IONIQ_6_LOW_SPEED_UNWIND_ASSIST_MAX_TORQUE = 0.30

IONIQ_6_LOW_SPEED_UNWIND_ASSIST_SPEED = 3.35

IONIQ_6_LOW_SPEED_UNWIND_ASSIST_SPEED_WIDTH = 0.50

IONIQ_6_LOW_SPEED_UNWIND_ASSIST_ERROR = 1.6

IONIQ_6_LOW_SPEED_UNWIND_ASSIST_ERROR_WIDTH = 0.95

IONIQ_6_LOW_SPEED_UNWIND_ASSIST_ACTUAL_ANGLE = 10.5

IONIQ_6_LOW_SPEED_UNWIND_ASSIST_ACTUAL_ANGLE_WIDTH = 4.0

IONIQ_6_LOW_SPEED_UNWIND_ASSIST_BLEND = 0.52

IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_BOOST = 0.10

IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_SPEED = 18.0

IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_SPEED_WIDTH = 2.5

IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_LAT_START = 0.06

IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_LAT_END = 0.22

IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_LAT_WIDTH = 0.035

IONIQ_6_CURVY_SPEED_MIN = 7.2

IONIQ_6_CURVY_SPEED_MAX = 21.5

IONIQ_6_CURVY_SPEED_MIN_WIDTH = 1.1

IONIQ_6_CURVY_SPEED_MAX_WIDTH = 1.8

IONIQ_6_CURVY_UNWIND_EXTRA_REDUCTION_LEFT = 0.26

IONIQ_6_CURVY_UNWIND_EXTRA_REDUCTION_RIGHT = 0.30

IONIQ_6_CURVY_UNWIND_FLOOR_RELIEF_LEFT = 0.22

IONIQ_6_CURVY_UNWIND_FLOOR_RELIEF_RIGHT = 0.28

IONIQ_6_CURVY_UNWIND_LAT_START = 0.45

IONIQ_6_CURVY_UNWIND_LAT_END = 3.6

IONIQ_6_CURVY_UNWIND_LAT_ONSET_WIDTH = 0.14

IONIQ_6_CURVY_UNWIND_LAT_CUTOFF_WIDTH = 0.55

IONIQ_6_CURVY_RIGHT_UNWIND_JERK_ONSET = 0.40

IONIQ_6_CURVY_RIGHT_UNWIND_JERK_WIDTH = 0.22

IONIQ_6_CURVY_TURN_IN_TRIM_SPEED_MIN = 11.5

IONIQ_6_CURVY_TURN_IN_TRIM_SPEED_MAX = 20.5

IONIQ_6_CURVY_TURN_IN_TRIM_SPEED_WIDTH = 1.2

IONIQ_6_CURVY_TURN_IN_TRIM_LEFT = 0.08

IONIQ_6_CURVY_TURN_IN_TRIM_RIGHT = 0.09

IONIQ_6_CURVY_TURN_IN_TRIM_LAT_START = 1.0

IONIQ_6_CURVY_TURN_IN_TRIM_LAT_END = 2.5

IONIQ_6_CURVY_TURN_IN_TRIM_LAT_ONSET_WIDTH = 0.18

IONIQ_6_CURVY_TURN_IN_TRIM_LAT_CUTOFF_WIDTH = 0.30

IONIQ_6_2023_UNWIND_FF_REDUCTION_MAX = 0.24

IONIQ_6_2023_UNWIND_FF_OVERSHOOT = 0.15

IONIQ_6_2023_UNWIND_FF_OVERSHOOT_WIDTH = 0.18

IONIQ_6_2023_UNWIND_FF_JERK = 0.10

IONIQ_6_2023_UNWIND_FF_JERK_WIDTH = 0.10

IONIQ_6_2023_UNWIND_FF_SPEED_ONSET = 8.0

IONIQ_6_2023_UNWIND_FF_SPEED_ONSET_WIDTH = 2.5

IONIQ_6_2023_UNWIND_FF_SPEED_CUTOFF = 23.5

IONIQ_6_2023_UNWIND_FF_SPEED_CUTOFF_WIDTH = 2.0

IONIQ_6_LOW_SPEED_PID_RESET_SPEED = 0.1 * CV.MPH_TO_MS

IONIQ_6_FRICTION_JERK_DEADZONE = 0.30

IONIQ_6_FRICTION_CENTER_FADE_MAX = 0.50

IONIQ_6_FRICTION_CENTER_FADE_LAT = 0.15

IONIQ_6_FRICTION_CENTER_FADE_LAT_WIDTH = 0.06

IONIQ_6_FRICTION_CENTER_FADE_SPEED = 18.0

IONIQ_6_FRICTION_CENTER_FADE_SPEED_WIDTH = 2.5

IONIQ_6_2025_FRICTION_SCALE_MULT = 0.80

IONIQ_6_2025_FRICTION_JERK_DEADZONE = 0.45

IONIQ_6_2025_CENTER_OUTPUT_TAPER_MAX = 0.32

IONIQ_6_2025_CENTER_OUTPUT_TAPER_LAT = 0.35

IONIQ_6_2025_CENTER_OUTPUT_TAPER_LAT_WIDTH = 0.10

IONIQ_6_2025_CENTER_OUTPUT_TAPER_SPEED = 22.0

IONIQ_6_2025_CENTER_OUTPUT_TAPER_SPEED_WIDTH = 2.5

IONIQ_6_2025_LOW_SPEED_CENTER_ERROR_SCALE = 0.68

IONIQ_6_2025_LOW_SPEED_CENTER_FRICTION_SCALE = 0.72

IONIQ_6_2025_LOW_SPEED_CENTER_SPEED = 5.0

IONIQ_6_2025_LOW_SPEED_CENTER_SPEED_WIDTH = 1.3

IONIQ_6_2025_LOW_SPEED_CENTER_LAT = 0.22

IONIQ_6_2025_LOW_SPEED_CENTER_LAT_WIDTH = 0.10

IONIQ_6_2025_LOW_SPEED_CENTER_JERK = 0.30

IONIQ_6_2025_LOW_SPEED_CENTER_JERK_WIDTH = 0.13

IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_BASE = 0.22

IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_TURN_RELIEF = 0.50

IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_SPEED_RELIEF = 0.20

IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_SPEED = 6.5

IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_SPEED_WIDTH = 1.5

IONIQ_6_HEAVY_DIRECTIONAL_TAPER_LAT_START = 0.90

IONIQ_6_HEAVY_DIRECTIONAL_TAPER_LAT_WIDTH = 0.18

IONIQ_6_HEAVY_DIRECTIONAL_TAPER_BASE_LEFT = 0.03

IONIQ_6_HEAVY_DIRECTIONAL_TAPER_BASE_RIGHT = 0.11

IONIQ_6_HEAVY_DIRECTIONAL_TAPER_UNWIND_LEFT = 0.40

IONIQ_6_HEAVY_DIRECTIONAL_TAPER_UNWIND_RIGHT = 0.55

def _ioniq_6_sigmoid(x: float) -> float:
  return _sigmoid(x)

def _ioniq_6_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / IONIQ_6_TRANSITION_SPEED) ** 2)

def _ioniq_6_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / IONIQ_6_PHASE_SCALE)

def _ioniq_6_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value

def _ioniq_6_transition_envelope(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  lat_factor = 1.0 - math.exp(-abs(desired_lateral_accel) / IONIQ_6_FRICTION_LAT_RISE)
  jerk_factor = 1.0 - math.exp(-abs(desired_lateral_jerk) / IONIQ_6_FRICTION_JERK_RISE)
  return _ioniq_6_low_speed_factor(v_ego) * lat_factor * jerk_factor

def _ioniq_6_curvy_speed_weight(v_ego: float) -> float:
  curvy_speed_min = IONIQ_6_CURVY_SPEED_MIN
  curvy_speed_max = IONIQ_6_CURVY_SPEED_MAX
  onset = _ioniq_6_sigmoid((max(v_ego, 0.0) - curvy_speed_min) / IONIQ_6_CURVY_SPEED_MIN_WIDTH)
  cutoff = _ioniq_6_sigmoid((curvy_speed_max - max(v_ego, 0.0)) / IONIQ_6_CURVY_SPEED_MAX_WIDTH)
  return onset * cutoff

def _ioniq_6_curvy_turn_in_trim_speed_weight(v_ego: float) -> float:
  curvy_turn_in_speed_min = IONIQ_6_CURVY_TURN_IN_TRIM_SPEED_MIN
  curvy_turn_in_speed_max = IONIQ_6_CURVY_TURN_IN_TRIM_SPEED_MAX
  onset = _ioniq_6_sigmoid((max(v_ego, 0.0) - curvy_turn_in_speed_min) / IONIQ_6_CURVY_TURN_IN_TRIM_SPEED_WIDTH)
  cutoff = _ioniq_6_sigmoid((curvy_turn_in_speed_max - max(v_ego, 0.0)) / IONIQ_6_CURVY_TURN_IN_TRIM_SPEED_WIDTH)
  return onset * cutoff

def get_ioniq_6_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float,
                         directional_taper_scale: float | None = None) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  gain = _ioniq_6_side_value(
    desired_lateral_accel,
    IONIQ_6_FF_GAIN_LEFT,
    IONIQ_6_FF_GAIN_RIGHT,
  )
  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _ioniq_6_sigmoid((abs_lateral_accel - IONIQ_6_FF_ONSET) / IONIQ_6_FF_ONSET_WIDTH)
  cutoff = _ioniq_6_sigmoid((IONIQ_6_FF_CUTOFF - abs_lateral_accel) / IONIQ_6_FF_CUTOFF_WIDTH)
  extra_scale = gain * onset * cutoff
  phase = _ioniq_6_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  low_speed_factor = _ioniq_6_low_speed_factor(v_ego)
  turn_in_boost = 1.0 + (_ioniq_6_side_value(
                          desired_lateral_accel,
                          IONIQ_6_TURN_IN_BOOST_LEFT,
                          IONIQ_6_TURN_IN_BOOST_RIGHT,
                        ) *
                          turn_in_weight * low_speed_factor)
  unwind_taper = 1.0 - (_ioniq_6_side_value(
                         desired_lateral_accel,
                         IONIQ_6_UNWIND_TAPER_LEFT,
                         IONIQ_6_UNWIND_TAPER_RIGHT,
                       ) *
                         unwind_weight * (0.30 + 0.70 * low_speed_factor))
  crawl_turn_in_scale = 0.0
  if desired_lateral_accel * desired_lateral_jerk > 0.0:
    crawl_speed_weight = _ioniq_6_sigmoid((IONIQ_6_CRAWL_TURN_IN_FF_SPEED - max(v_ego, 0.0)) /
                                          IONIQ_6_CRAWL_TURN_IN_FF_SPEED_WIDTH)
    crawl_lat_weight = _ioniq_6_sigmoid((abs_lateral_accel - IONIQ_6_CRAWL_TURN_IN_FF_LAT) /
                                        IONIQ_6_CRAWL_TURN_IN_FF_LAT_WIDTH)
    crawl_turn_in_scale = _ioniq_6_side_value(
      desired_lateral_accel,
      IONIQ_6_CRAWL_TURN_IN_FF_BOOST_LEFT,
      IONIQ_6_CRAWL_TURN_IN_FF_BOOST_RIGHT,
    ) * crawl_speed_weight * crawl_lat_weight
  high_speed_right_turn_in_scale = 0.0
  if desired_lateral_accel < 0.0 and desired_lateral_accel * desired_lateral_jerk > 0.0:
    high_speed_weight = _ioniq_6_sigmoid((max(v_ego, 0.0) - IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_SPEED) /
                                         IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_SPEED_WIDTH)
    high_speed_lat_onset = _ioniq_6_sigmoid((abs_lateral_accel - IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_LAT_START) /
                                            IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_LAT_WIDTH)
    high_speed_lat_cutoff = _ioniq_6_sigmoid((IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_LAT_END - abs_lateral_accel) /
                                             IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_LAT_WIDTH)
    high_speed_right_turn_in_scale = IONIQ_6_HIGH_SPEED_RIGHT_TURN_IN_FF_BOOST * high_speed_weight * high_speed_lat_onset * high_speed_lat_cutoff
  if directional_taper_scale is None:
    directional_taper_scale = get_ioniq_6_directional_taper_scale(desired_lateral_accel, desired_lateral_jerk, v_ego)
  return (1.0 + crawl_turn_in_scale + high_speed_right_turn_in_scale +
          (extra_scale * turn_in_boost * max(unwind_taper, 0.0))) * directional_taper_scale

def get_ioniq_6_2023_unwind_ff_scale(setpoint: float, measured_lateral_accel: float,
                                     desired_lateral_jerk: float, v_ego: float) -> float:
  """Trim residual curve feedforward when the 2023 car is already over-rotated."""
  if setpoint * desired_lateral_jerk >= 0.0 or setpoint * measured_lateral_accel <= 0.0:
    return 1.0

  overshoot = max(abs(measured_lateral_accel) - abs(setpoint), 0.0)
  if overshoot <= 0.0:
    return 1.0

  overshoot_weight = _ioniq_6_sigmoid((overshoot - IONIQ_6_2023_UNWIND_FF_OVERSHOOT) /
                                      IONIQ_6_2023_UNWIND_FF_OVERSHOOT_WIDTH)
  jerk_weight = _ioniq_6_sigmoid((abs(desired_lateral_jerk) - IONIQ_6_2023_UNWIND_FF_JERK) /
                                 IONIQ_6_2023_UNWIND_FF_JERK_WIDTH)
  speed_onset = _ioniq_6_sigmoid((v_ego - IONIQ_6_2023_UNWIND_FF_SPEED_ONSET) /
                                 IONIQ_6_2023_UNWIND_FF_SPEED_ONSET_WIDTH)
  speed_cutoff = _ioniq_6_sigmoid((IONIQ_6_2023_UNWIND_FF_SPEED_CUTOFF - v_ego) /
                                  IONIQ_6_2023_UNWIND_FF_SPEED_CUTOFF_WIDTH)
  reduction = (IONIQ_6_2023_UNWIND_FF_REDUCTION_MAX * overshoot_weight * jerk_weight *
               speed_onset * speed_cutoff)
  return 1.0 - reduction

def get_ioniq_6_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  base_threshold = max(get_hkg_canfd_base_friction_threshold(v_ego), IONIQ_6_BASE_FRICTION_THRESHOLD)
  transition_envelope = _ioniq_6_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _ioniq_6_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  unwind_speed_weight = _ioniq_6_sigmoid((v_ego - IONIQ_6_UNWIND_HIGH_SPEED_SPEED) / IONIQ_6_UNWIND_HIGH_SPEED_SPEED_WIDTH)
  threshold_scale = 1.0 - (_ioniq_6_side_value(
                           desired_lateral_accel,
                           IONIQ_6_TURN_IN_THRESHOLD_REDUCTION_LEFT,
                           IONIQ_6_TURN_IN_THRESHOLD_REDUCTION_RIGHT,
                         ) *
                           transition_envelope * turn_in_weight)
  threshold_scale += (_ioniq_6_side_value(
                      desired_lateral_accel,
                      IONIQ_6_UNWIND_THRESHOLD_INCREASE_LEFT,
                      IONIQ_6_UNWIND_THRESHOLD_INCREASE_RIGHT,
                    ) *
                      transition_envelope * unwind_weight * unwind_speed_weight)
  return base_threshold * min(max(threshold_scale, 0.82), 1.18)

def get_ioniq_6_friction_scale(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  transition_envelope = _ioniq_6_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _ioniq_6_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  unwind_speed_weight = _ioniq_6_sigmoid((v_ego - IONIQ_6_UNWIND_HIGH_SPEED_SPEED) / IONIQ_6_UNWIND_HIGH_SPEED_SPEED_WIDTH)
  friction_scale = IONIQ_6_FRICTION_MULT
  friction_scale += (_ioniq_6_side_value(desired_lateral_accel, IONIQ_6_TURN_IN_FRICTION_BOOST_LEFT, IONIQ_6_TURN_IN_FRICTION_BOOST_RIGHT) *
                     transition_envelope * turn_in_weight)
  friction_scale -= (_ioniq_6_side_value(desired_lateral_accel, IONIQ_6_UNWIND_FRICTION_REDUCTION_LEFT, IONIQ_6_UNWIND_FRICTION_REDUCTION_RIGHT) *
                     transition_envelope * unwind_weight * unwind_speed_weight)
  return min(max(friction_scale, 0.82), 1.08)

def get_ioniq_6_friction_center_fade_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _ioniq_6_sigmoid((v_ego - IONIQ_6_FRICTION_CENTER_FADE_SPEED) / IONIQ_6_FRICTION_CENTER_FADE_SPEED_WIDTH)
  center_weight = _ioniq_6_sigmoid((IONIQ_6_FRICTION_CENTER_FADE_LAT - abs(desired_lateral_accel)) / IONIQ_6_FRICTION_CENTER_FADE_LAT_WIDTH)
  return 1.0 - IONIQ_6_FRICTION_CENTER_FADE_MAX * speed_weight * center_weight

def get_ioniq_6_2025_center_output_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _ioniq_6_sigmoid((v_ego - IONIQ_6_2025_CENTER_OUTPUT_TAPER_SPEED) /
                                  IONIQ_6_2025_CENTER_OUTPUT_TAPER_SPEED_WIDTH)
  center_weight = _ioniq_6_sigmoid((IONIQ_6_2025_CENTER_OUTPUT_TAPER_LAT - abs(desired_lateral_accel)) /
                                   IONIQ_6_2025_CENTER_OUTPUT_TAPER_LAT_WIDTH)
  return 1.0 - IONIQ_6_2025_CENTER_OUTPUT_TAPER_MAX * speed_weight * center_weight

def get_ioniq_6_2025_low_speed_output_limit(desired_lateral_accel: float,
                                              desired_lateral_jerk: float, v_ego: float) -> float:
  """Limit small-signal torque at crawl speed while leaving real turn commands open."""
  speed_weight = _ioniq_6_sigmoid((IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_SPEED - max(v_ego, 0.0)) /
                                  IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_SPEED_WIDTH)
  center_weight = _ioniq_6_sigmoid((IONIQ_6_2025_LOW_SPEED_CENTER_LAT - abs(desired_lateral_accel)) /
                                   IONIQ_6_2025_LOW_SPEED_CENTER_LAT_WIDTH)
  calm_weight = _ioniq_6_sigmoid((IONIQ_6_2025_LOW_SPEED_CENTER_JERK - abs(desired_lateral_jerk)) /
                                 IONIQ_6_2025_LOW_SPEED_CENTER_JERK_WIDTH)
  center_weight *= calm_weight
  limit = (IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_BASE +
           IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_TURN_RELIEF * (1.0 - center_weight) +
           IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_SPEED_RELIEF * (1.0 - speed_weight))
  return clip_scalar(limit, IONIQ_6_2025_LOW_SPEED_OUTPUT_LIMIT_BASE, 1.0)

def _ioniq_6_2025_low_speed_center_envelope(desired_lateral_accel: float,
                                             desired_lateral_jerk: float, v_ego: float) -> float:
  speed_weight = _ioniq_6_sigmoid((IONIQ_6_2025_LOW_SPEED_CENTER_SPEED - max(v_ego, 0.0)) /
                                  IONIQ_6_2025_LOW_SPEED_CENTER_SPEED_WIDTH)
  center_weight = _ioniq_6_sigmoid((IONIQ_6_2025_LOW_SPEED_CENTER_LAT - abs(desired_lateral_accel)) /
                                   IONIQ_6_2025_LOW_SPEED_CENTER_LAT_WIDTH)
  calm_weight = _ioniq_6_sigmoid((IONIQ_6_2025_LOW_SPEED_CENTER_JERK - abs(desired_lateral_jerk)) /
                                 IONIQ_6_2025_LOW_SPEED_CENTER_JERK_WIDTH)
  return speed_weight * center_weight * calm_weight

def get_ioniq_6_2025_low_speed_center_error_scale(desired_lateral_accel: float,
                                                   desired_lateral_jerk: float, v_ego: float) -> float:
  envelope = _ioniq_6_2025_low_speed_center_envelope(desired_lateral_accel, desired_lateral_jerk, v_ego)
  return 1.0 - (1.0 - IONIQ_6_2025_LOW_SPEED_CENTER_ERROR_SCALE) * envelope

def get_ioniq_6_2025_low_speed_center_friction_scale(desired_lateral_accel: float,
                                                      desired_lateral_jerk: float, v_ego: float) -> float:
  envelope = _ioniq_6_2025_low_speed_center_envelope(desired_lateral_accel, desired_lateral_jerk, v_ego)
  return 1.0 - (1.0 - IONIQ_6_2025_LOW_SPEED_CENTER_FRICTION_SCALE) * envelope

def get_ioniq_6_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _ioniq_6_sigmoid((v_ego - IONIQ_6_CENTER_TAPER_SPEED) / IONIQ_6_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _ioniq_6_sigmoid((IONIQ_6_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / IONIQ_6_CENTER_TAPER_LAT_WIDTH)
  high_speed_reduction = IONIQ_6_CENTER_TAPER_MAX * speed_weight * center_weight

  highway_speed_weight = _ioniq_6_sigmoid((v_ego - IONIQ_6_HIGHWAY_CENTER_TAPER_SPEED) / IONIQ_6_HIGHWAY_CENTER_TAPER_SPEED_WIDTH)
  highway_center_weight = _ioniq_6_sigmoid((IONIQ_6_HIGHWAY_CENTER_TAPER_LAT - abs(desired_lateral_accel)) /
                                           IONIQ_6_HIGHWAY_CENTER_TAPER_LAT_WIDTH)
  highway_center_reduction = (IONIQ_6_HIGHWAY_CENTER_TAPER_MAX *
                              highway_speed_weight * highway_center_weight)

  low_mid_onset = _ioniq_6_sigmoid((v_ego - IONIQ_6_LOW_MID_CENTER_TAPER_SPEED_MIN) / IONIQ_6_LOW_MID_CENTER_TAPER_SPEED_WIDTH)
  low_mid_cutoff = _ioniq_6_sigmoid((IONIQ_6_LOW_MID_CENTER_TAPER_SPEED_MAX - v_ego) / IONIQ_6_LOW_MID_CENTER_TAPER_SPEED_WIDTH)
  low_mid_speed_weight = low_mid_onset * low_mid_cutoff
  low_mid_center_weight = _ioniq_6_sigmoid((IONIQ_6_LOW_MID_CENTER_TAPER_LAT - abs(desired_lateral_accel)) /
                                           IONIQ_6_LOW_MID_CENTER_TAPER_LAT_WIDTH)
  low_mid_reduction = IONIQ_6_LOW_MID_CENTER_TAPER_MAX * low_mid_speed_weight * low_mid_center_weight

  return 1.0 - min(high_speed_reduction + highway_center_reduction + low_mid_reduction, 0.12)

def get_ioniq_6_directional_taper_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float | None = None) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _ioniq_6_sigmoid((abs_lateral_accel - IONIQ_6_DIRECTIONAL_TAPER_LAT_START) / IONIQ_6_DIRECTIONAL_TAPER_LAT_WIDTH)
  cutoff = _ioniq_6_sigmoid((IONIQ_6_DIRECTIONAL_TAPER_LAT_END - abs_lateral_accel) / IONIQ_6_DIRECTIONAL_TAPER_LAT_WIDTH)
  band_weight = onset * cutoff
  heavy_band_weight = _ioniq_6_sigmoid((abs_lateral_accel - IONIQ_6_HEAVY_DIRECTIONAL_TAPER_LAT_START) / IONIQ_6_HEAVY_DIRECTIONAL_TAPER_LAT_WIDTH)
  phase = math.tanh((desired_lateral_accel * desired_lateral_jerk) / IONIQ_6_DIRECTIONAL_TAPER_PHASE_SCALE)
  unwind_weight = max(-phase, 0.0) * _ioniq_6_sigmoid((abs(desired_lateral_jerk) - IONIQ_6_DIRECTIONAL_TAPER_JERK_ONSET) /
                                                       IONIQ_6_DIRECTIONAL_TAPER_JERK_WIDTH)
  low_speed_relief_weight = 0.0
  curvy_turn_in_trim_weight = 0.0
  if v_ego is not None:
    low_speed_weight = _ioniq_6_sigmoid((IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF_SPEED - max(v_ego, 0.0)) /
                                        IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF_SPEED_WIDTH)
    tight_turn_weight = _ioniq_6_sigmoid((abs_lateral_accel - IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF_LAT) /
                                         IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF_LAT_WIDTH)
    low_speed_relief_weight = IONIQ_6_DIRECTIONAL_TAPER_LOW_SPEED_RELIEF * low_speed_weight * tight_turn_weight * (1.0 - unwind_weight)
    turn_in_weight = max(phase, 0.0)
    curvy_turn_in_speed_weight = _ioniq_6_curvy_turn_in_trim_speed_weight(v_ego)
    curvy_turn_in_lat_onset = _ioniq_6_sigmoid((abs_lateral_accel - IONIQ_6_CURVY_TURN_IN_TRIM_LAT_START) /
                                               IONIQ_6_CURVY_TURN_IN_TRIM_LAT_ONSET_WIDTH)
    curvy_turn_in_lat_cutoff = _ioniq_6_sigmoid((IONIQ_6_CURVY_TURN_IN_TRIM_LAT_END - abs_lateral_accel) /
                                                IONIQ_6_CURVY_TURN_IN_TRIM_LAT_CUTOFF_WIDTH)
    curvy_turn_in_trim_weight = curvy_turn_in_speed_weight * curvy_turn_in_lat_onset * curvy_turn_in_lat_cutoff * turn_in_weight
  base_reduction = _ioniq_6_side_value(desired_lateral_accel, IONIQ_6_DIRECTIONAL_TAPER_BASE_LEFT, IONIQ_6_DIRECTIONAL_TAPER_BASE_RIGHT)
  unwind_reduction = _ioniq_6_side_value(desired_lateral_accel, IONIQ_6_DIRECTIONAL_TAPER_UNWIND_LEFT, IONIQ_6_DIRECTIONAL_TAPER_UNWIND_RIGHT)
  heavy_base_reduction = _ioniq_6_side_value(desired_lateral_accel, IONIQ_6_HEAVY_DIRECTIONAL_TAPER_BASE_LEFT, IONIQ_6_HEAVY_DIRECTIONAL_TAPER_BASE_RIGHT)
  heavy_unwind_reduction = _ioniq_6_side_value(desired_lateral_accel, IONIQ_6_HEAVY_DIRECTIONAL_TAPER_UNWIND_LEFT, IONIQ_6_HEAVY_DIRECTIONAL_TAPER_UNWIND_RIGHT)
  base_reduction *= 1.0 - low_speed_relief_weight
  heavy_base_reduction *= 1.0 - low_speed_relief_weight
  reduction = band_weight * (base_reduction + unwind_reduction * unwind_weight)
  reduction += heavy_band_weight * (heavy_base_reduction + heavy_unwind_reduction * unwind_weight)
  reduction += (_ioniq_6_side_value(desired_lateral_accel,
                                    IONIQ_6_CURVY_TURN_IN_TRIM_LEFT,
                                    IONIQ_6_CURVY_TURN_IN_TRIM_RIGHT) *
                curvy_turn_in_trim_weight)
  curvy_unwind_weight = 0.0
  curvy_unwind_floor_relief = 0.0
  if v_ego is not None:
    curvy_unwind_phase_weight = unwind_weight
    if desired_lateral_accel < 0.0:
      curvy_unwind_phase_weight = max(-phase, 0.0) * _ioniq_6_sigmoid(
        (abs(desired_lateral_jerk) - IONIQ_6_CURVY_RIGHT_UNWIND_JERK_ONSET) / IONIQ_6_CURVY_RIGHT_UNWIND_JERK_WIDTH)
    curvy_unwind_speed_weight = _ioniq_6_curvy_speed_weight(v_ego)
    curvy_unwind_lat_onset = _ioniq_6_sigmoid((abs_lateral_accel - IONIQ_6_CURVY_UNWIND_LAT_START) /
                                              IONIQ_6_CURVY_UNWIND_LAT_ONSET_WIDTH)
    curvy_unwind_lat_cutoff = _ioniq_6_sigmoid((IONIQ_6_CURVY_UNWIND_LAT_END - abs_lateral_accel) /
                                               IONIQ_6_CURVY_UNWIND_LAT_CUTOFF_WIDTH)
    curvy_unwind_weight = curvy_unwind_speed_weight * curvy_unwind_lat_onset * curvy_unwind_lat_cutoff * curvy_unwind_phase_weight
    curvy_unwind_floor_relief = (_ioniq_6_side_value(desired_lateral_accel,
                                                     IONIQ_6_CURVY_UNWIND_FLOOR_RELIEF_LEFT,
                                                     IONIQ_6_CURVY_UNWIND_FLOOR_RELIEF_RIGHT) *
                                 curvy_unwind_weight)
  reduction += (_ioniq_6_side_value(desired_lateral_accel,
                                    IONIQ_6_CURVY_UNWIND_EXTRA_REDUCTION_LEFT,
                                    IONIQ_6_CURVY_UNWIND_EXTRA_REDUCTION_RIGHT) *
                curvy_unwind_weight)
  floor = _ioniq_6_side_value(desired_lateral_accel, IONIQ_6_DIRECTIONAL_TAPER_FLOOR_LEFT, IONIQ_6_DIRECTIONAL_TAPER_FLOOR_RIGHT)
  floor -= _ioniq_6_side_value(desired_lateral_accel, IONIQ_6_DIRECTIONAL_TAPER_UNWIND_FLOOR_LEFT, IONIQ_6_DIRECTIONAL_TAPER_UNWIND_FLOOR_RIGHT) * unwind_weight
  floor -= curvy_unwind_floor_relief
  return max(1.0 - reduction, floor)

def get_ioniq_6_highway_output_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _ioniq_6_sigmoid((v_ego - IONIQ_6_HIGHWAY_OUTPUT_TAPER_SPEED) / IONIQ_6_HIGHWAY_OUTPUT_TAPER_SPEED_WIDTH)
  center_weight = _ioniq_6_sigmoid((IONIQ_6_HIGHWAY_OUTPUT_TAPER_LAT - abs(desired_lateral_accel)) /
                                   IONIQ_6_HIGHWAY_OUTPUT_TAPER_LAT_WIDTH)
  reduction = IONIQ_6_HIGHWAY_OUTPUT_TAPER_MAX * speed_weight * center_weight
  return 1.0 - reduction

def get_ioniq_6_highway_transition_output_taper_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  speed_weight = _ioniq_6_sigmoid((v_ego - IONIQ_6_HIGHWAY_OUTPUT_TAPER_SPEED) / IONIQ_6_HIGHWAY_OUTPUT_TAPER_SPEED_WIDTH)
  center_weight = _ioniq_6_sigmoid((IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_LAT - abs(desired_lateral_accel)) /
                                   IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_LAT_WIDTH)
  jerk_weight = _ioniq_6_sigmoid((abs(desired_lateral_jerk) - IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_JERK) /
                                 IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_JERK_WIDTH)
  reduction = IONIQ_6_HIGHWAY_TRANSITION_OUTPUT_TAPER_MAX * speed_weight * center_weight * jerk_weight
  return 1.0 - reduction

def get_ioniq_6_low_speed_angle_assist_torque(desired_angle_deg: float, actual_angle_deg: float,
                                              current_output_torque: float, v_ego: float) -> float:
  angle_error = desired_angle_deg - actual_angle_deg
  turn_in_output = None
  if desired_angle_deg * angle_error > 0.0:
    speed_weight = _ioniq_6_sigmoid((IONIQ_6_LOW_SPEED_ANGLE_ASSIST_SPEED - max(v_ego, 0.0)) /
                                    IONIQ_6_LOW_SPEED_ANGLE_ASSIST_SPEED_WIDTH)
    error_weight = _ioniq_6_sigmoid((abs(angle_error) - IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ERROR) /
                                    IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ERROR_WIDTH)
    # Sigmoid tails remain nonzero at zero error. Taper only inside the
    # existing transition width so sign reversals cannot introduce a torque step.
    error_weight *= min(abs(angle_error) / IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ERROR_WIDTH, 1.0)
    desired_angle_weight = _ioniq_6_sigmoid((abs(desired_angle_deg) - IONIQ_6_LOW_SPEED_ANGLE_ASSIST_DESIRED_ANGLE) /
                                            IONIQ_6_LOW_SPEED_ANGLE_ASSIST_DESIRED_ANGLE_WIDTH)
    tracking_ratio = abs(actual_angle_deg) / max(abs(desired_angle_deg), 1e-3)
    tracking_taper = _ioniq_6_sigmoid((tracking_ratio - IONIQ_6_LOW_SPEED_ANGLE_ASSIST_TRACK_RATIO_START) /
                                      IONIQ_6_LOW_SPEED_ANGLE_ASSIST_TRACK_RATIO_WIDTH)
    tracking_scale = max(1.0 - tracking_taper, IONIQ_6_LOW_SPEED_ANGLE_ASSIST_TRACK_RATIO_FLOOR)
    assist_torque = math.copysign(
      IONIQ_6_LOW_SPEED_ANGLE_ASSIST_MAX_TORQUE *
      speed_weight * error_weight * desired_angle_weight * tracking_scale,
      -angle_error,
    )
    if abs(assist_torque) < 1e-4:
      turn_in_output = current_output_torque
    elif current_output_torque * assist_torque >= 0.0:
      add_scale = float(np.interp(abs(current_output_torque),
                                  IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ADD_BP,
                                  IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ADD_V))
      turn_in_output = clip_scalar(current_output_torque + (assist_torque * add_scale), -1.0, 1.0)
    else:
      turn_in_output = clip_scalar(current_output_torque + assist_torque, -1.0, 1.0)
    if abs(desired_angle_deg) >= IONIQ_6_LOW_SPEED_ANGLE_ASSIST_DESIRED_ANGLE_WIDTH:
      return turn_in_output

  speed_weight = _ioniq_6_sigmoid((IONIQ_6_LOW_SPEED_UNWIND_ASSIST_SPEED - max(v_ego, 0.0)) /
                                  IONIQ_6_LOW_SPEED_UNWIND_ASSIST_SPEED_WIDTH)
  error_weight = _ioniq_6_sigmoid((abs(angle_error) - IONIQ_6_LOW_SPEED_UNWIND_ASSIST_ERROR) /
                                  IONIQ_6_LOW_SPEED_UNWIND_ASSIST_ERROR_WIDTH)
  error_weight *= min(abs(angle_error) / IONIQ_6_LOW_SPEED_UNWIND_ASSIST_ERROR_WIDTH, 1.0)
  actual_angle_weight = _ioniq_6_sigmoid((abs(actual_angle_deg) - IONIQ_6_LOW_SPEED_UNWIND_ASSIST_ACTUAL_ANGLE) /
                                         IONIQ_6_LOW_SPEED_UNWIND_ASSIST_ACTUAL_ANGLE_WIDTH)
  assist_torque = math.copysign(IONIQ_6_LOW_SPEED_UNWIND_ASSIST_MAX_TORQUE * speed_weight * error_weight * actual_angle_weight, -angle_error)
  opposition = max(-current_output_torque * math.copysign(1.0, assist_torque), 0.0)
  assist_torque *= float(np.interp(opposition, [0.0, IONIQ_6_LOW_SPEED_ANGLE_ASSIST_ADD_BP[1]],
                                  [IONIQ_6_LOW_SPEED_UNWIND_ASSIST_BLEND, 1.0]))
  unwind_output = (current_output_torque if abs(assist_torque) < 1e-4 else
                   clip_scalar(current_output_torque + assist_torque, -1.0, 1.0))
  if turn_in_output is None:
    return unwind_output
  turn_in_weight = abs(desired_angle_deg) / IONIQ_6_LOW_SPEED_ANGLE_ASSIST_DESIRED_ANGLE_WIDTH
  return unwind_output + (turn_in_output - unwind_output) * turn_in_weight


# Buffer curvature and scale at the current speed to avoid pull-away unwind
# from lateral acceleration recorded at old speeds.
from collections import deque

from openpilot.cereal import log
from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.pid import PIDController

class Ioniq6TorquePolicy:
  """Per-car controller state; the native LatControlTorque still owns output."""

  FACTOR_MULT = IONIQ_6_BASE_LAT_ACCEL_FACTOR_MULT

  def __init__(self, parent, CP, *, surface=None, turn_assist: bool = False):
    # Validate the replay-only surface before changing the parent's PID, factor or limits.
    surface_math = None
    if surface is not None:
      from openpilot.starpilot.flm import torque_surface as surface_math
      expected_variant = "firmware_2025" if is_ioniq_6_2025_model(CP) else "standard"
      if (str(CP.carFingerprint) != "HYUNDAI_IONIQ_6" or
          not isinstance(surface, surface_math.Ioniq6Surface) or surface.variant != expected_variant):
        raise ValueError("Ioniq 6 surface does not match CarParams")
    self.turn_assist = bool(turn_assist)
    minimum = float(CP.minSteerSpeed)
    self.turn_assist_min_speed = max(0.044704, minimum) if math.isfinite(minimum) else math.inf
    self.surface = surface
    self.surface_math = surface_math
    self.parent = parent
    self.dt = parent.dt
    self.is_2025 = is_ioniq_6_2025_model(CP)
    self.vehicle_factor = float(CP.lateralTuning.torque.latAccelFactor)
    parent.pid = PIDController([[0.0], [KP_INTERP[-1]]], KI, rate=1 / self.dt)
    parent.torque_params.latAccelFactor = self.vehicle_factor * IONIQ_6_BASE_LAT_ACCEL_FACTOR_MULT
    parent.update_limits()
    self.request_buffer_len = int(LAT_ACCEL_REQUEST_BUFFER_SECONDS / self.dt)
    self.curvature_request_buffer = deque([0.0] * self.request_buffer_len, maxlen=self.request_buffer_len)
    self.jerk_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * LP_FILTER_CUTOFF_HZ), self.dt)
    self.measurement_rate_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * (MAX_LAT_JERK_UP - 0.5)), self.dt)
    self.directional_taper_filter = FirstOrderFilter(1.0, IONIQ_6_DIRECTIONAL_TAPER_FILTER_RC, self.dt)
    self.previous_measurement = 0.0
    self.prev_desired_lateral_accel = 0.0
    self.prev_steering_pressed = False
    self.prev_center_friction = None
    self.low_speed_reset_threshold = min(max(CP.minSteerSpeed, 0.3), IONIQ_6_LOW_SPEED_PID_RESET_SPEED)

  def center_friction(self, friction, CS, *, saturated):
    """Bound growing unsaturated friction in lateral-acceleration units."""
    weight = clip_scalar((CS.vEgo - 12.0) / 13.0, 0.0, 1.0)
    if self.surface is not None or weight <= 0.0 or CS.steeringPressed or saturated:
      self.prev_center_friction = None
      return friction
    previous = self.prev_center_friction
    if previous is not None:
      step = MAX_LAT_JERK_UP * self.dt / weight
      # Shrinking or reversing requests must never retain an old force.
      requested = friction
      friction = clip_scalar(friction, previous - step, previous + step)
      friction = clip_scalar(friction, min(0.0, requested), max(0.0, requested))
    self.prev_center_friction = friction
    return friction

  def turn_assist_active(self, CS) -> bool:
    return bool(self.turn_assist and math.isfinite(CS.vEgo) and
                CS.vEgo >= self.turn_assist_min_speed and not CS.standstill and
                CS.gearShifter == structs.CarState.GearShifter.drive and
                not CS.steeringPressed and not CS.steerFaultTemporary and not CS.steerFaultPermanent)

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited, lat_delay):
    parent = self.parent
    pid_log = log.ControlsState.LateralTorqueState.new_message()
    pid_log.version = 2
    measured_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
    measurement = measured_curvature * CS.vEgo ** 2
    future_desired_lateral_accel = desired_curvature * CS.vEgo ** 2
    if not active:
      self.prev_center_friction = None
      parent.pid.reset()
      self.curvature_request_buffer.append(desired_curvature)
      self.previous_measurement = measurement
      self.measurement_rate_filter.x = 0.0
      self.jerk_filter.x = 0.0
      self.directional_taper_filter.x = 1.0
      self.prev_desired_lateral_accel = future_desired_lateral_accel
      self.prev_steering_pressed = CS.steeringPressed
      pid_log.active = False
      return 0.0, 0.0, pid_log

    if self.prev_steering_pressed and not CS.steeringPressed:
      parent.pid.i *= 0.8
    roll_offset_fade = float(np.interp(CS.vEgo, FF_ROLL_OFFSET_FADE_BP, FF_ROLL_OFFSET_FADE_V))
    roll_compensation = params.roll * ACCELERATION_DUE_TO_GRAVITY * roll_offset_fade
    effective_deadzone_deg = parent.steering_angle_deadzone_deg
    if self.surface is not None:
      effective_deadzone_deg += self.surface_math.center_deadband(self.surface, CS.vEgo)
    curvature_deadzone = abs(VM.calc_curvature(math.radians(effective_deadzone_deg), CS.vEgo, 0.0))
    lateral_accel_deadzone = curvature_deadzone * CS.vEgo ** 2
    lateral_accel_deadzone = auto_stages.apply_selected_deadzone(parent, CS, VM, float(lateral_accel_deadzone), desired_curvature,
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)
    # Honor the measured delay instead of rounding across a whole request tick.
    delay_frames = clip_scalar(lat_delay / self.dt, 1.0, float(self.request_buffer_len))
    recent = int(delay_frames)
    older = min(recent + 1, self.request_buffer_len)
    fraction = delay_frames - recent
    delayed_curvature = self.curvature_request_buffer[-recent]
    if fraction:
      delayed_curvature = ((1.0 - fraction) * delayed_curvature +
                           fraction * self.curvature_request_buffer[-older])
    expected_lateral_accel = delayed_curvature * CS.vEgo ** 2
    self.curvature_request_buffer.append(desired_curvature)
    raw_lateral_jerk = (future_desired_lateral_accel - expected_lateral_accel) / max(lat_delay, self.dt)
    raw_lateral_jerk = clip_scalar(raw_lateral_jerk, -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP)
    desired_lateral_jerk = clip_scalar(self.jerk_filter.update(raw_lateral_jerk), -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP)
    gravity_adjusted_future_lateral_accel = future_desired_lateral_accel - roll_compensation
    setpoint = expected_lateral_accel + desired_lateral_jerk * lat_delay
    desired_lateral_accel_magnitude_rate = (abs(setpoint) - abs(self.prev_desired_lateral_accel)) / self.dt
    unwind_detected = desired_lateral_accel_magnitude_rate < UNWIND_D_DES_THRESHOLD and abs(setpoint) < UNWIND_LAT_ACCEL_NEAR_ZERO
    self.prev_desired_lateral_accel = setpoint
    measurement_rate = self.measurement_rate_filter.update((measurement - self.previous_measurement) / self.dt)
    measurement_rate = clip_scalar(measurement_rate, -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP)
    self.previous_measurement = measurement
    low_speed_factor = (np.interp(CS.vEgo, LOW_SPEED_X, LOW_SPEED_Y) / max(CS.vEgo, MIN_SPEED)) ** 2
    current_kp = np.interp(CS.vEgo, parent.pid._k_p[0], parent.pid._k_p[1])
    error = setpoint - measurement
    error_with_lsf = error * (1 + low_speed_factor / max(current_kp, 1e-3))
    if self.is_2025:
      error_with_lsf *= get_ioniq_6_2025_low_speed_center_error_scale(setpoint, desired_lateral_jerk, CS.vEgo)
    pid_log.error = float(error_with_lsf)
    ff = gravity_adjusted_future_lateral_accel - parent.torque_params.latAccelOffset * roll_offset_fade
    if self.surface is None:
      center_taper = get_ioniq_6_center_taper_scale(setpoint, CS.vEgo)
      directional_target = get_ioniq_6_directional_taper_scale(setpoint, desired_lateral_jerk, CS.vEgo)
    else:
      center_taper = self.surface_math.center_taper(self.surface, CS.vEgo, setpoint)
      directional_target = self.surface_math.directional_taper_target(self.surface, CS.vEgo, setpoint, desired_lateral_jerk)
    directional_taper = self.directional_taper_filter.update(directional_target)
    if self.surface is None:
      ff *= get_ioniq_6_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo, directional_taper_scale=directional_taper) * center_taper
    else:
      ff *= self.surface_math.feedforward_scale(self.surface, CS.vEgo, setpoint, desired_lateral_jerk, directional_taper) * center_taper
    if not self.is_2025:
      ff *= get_ioniq_6_2023_unwind_ff_scale(setpoint, measurement, desired_lateral_jerk, CS.vEgo)
    if self.surface is None:
      friction_threshold = get_ioniq_6_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk) / max(center_taper, 1e-3)
    else:
      friction_threshold = self.surface_math.friction_threshold(self.surface, CS.vEgo, setpoint, desired_lateral_jerk) / max(center_taper, 1e-3)
    if self.surface is None and not self.is_2025 and not CS.steeringPressed and not self.prev_steering_pressed:
      friction_threshold += get_ioniq_6_2023_center_friction_increment(
        CS.vEgo, setpoint, measurement, desired_lateral_jerk, future_desired_lateral_accel) / max(center_taper, 1e-3)
    friction_scale = get_ioniq_6_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
    friction_scale = 1.0 + ((friction_scale - 1.0) * center_taper)
    friction_scale *= get_ioniq_6_friction_center_fade_scale(setpoint, CS.vEgo)
    if self.is_2025:
      friction_scale *= IONIQ_6_2025_FRICTION_SCALE_MULT
      friction_scale *= get_ioniq_6_2025_low_speed_center_friction_scale(setpoint, desired_lateral_jerk, CS.vEgo)
    vehicle_jerk_deadzone = IONIQ_6_2025_FRICTION_JERK_DEADZONE if self.is_2025 else IONIQ_6_FRICTION_JERK_DEADZONE
    friction_jerk_deadzone = center_chatter_friction_jerk_deadzone(CS.vEgo, setpoint, vehicle_jerk_deadzone)
    friction_jerk = math.copysign(max(abs(desired_lateral_jerk) - friction_jerk_deadzone, 0.0), desired_lateral_jerk)
    ff, friction_threshold = auto_stages.apply_stages(parent, CS, setpoint, desired_lateral_jerk, ff, friction_threshold,
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)
    friction_error = error_with_lsf + JERK_GAIN * friction_jerk
    friction = friction_scale * parent.friction(friction_error, lateral_accel_deadzone,
                                                friction_threshold, CS, setpoint)
    selected_friction = self.center_friction(friction, CS,
      saturated=abs(friction_error) >= friction_threshold + lateral_accel_deadzone)
    ff += auto_stages.apply_selected_friction(parent, CS, setpoint, float(selected_friction),
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)
    if CS.vEgo < self.low_speed_reset_threshold:
      parent.pid.reset()
    freeze_integrator = (steer_limited_by_safety or CS.steeringPressed or
                         CS.vEgo < self.low_speed_reset_threshold or unwind_detected)
    output_lataccel = parent.pid.update(pid_log.error, error_rate=-measurement_rate, speed=CS.vEgo,
                                        feedforward=ff, freeze_integrator=freeze_integrator)
    output_torque = parent.torque_from_lateral_accel(output_lataccel, parent.torque_params)
    if self.turn_assist_active(CS):
      desired_angle = math.degrees(VM.get_steer_from_curvature(-desired_curvature, CS.vEgo, params.roll))
      actual_angle = CS.steeringAngleDeg - params.angleOffsetDeg
      if math.isfinite(desired_angle) and math.isfinite(actual_angle) and math.isfinite(output_torque):
        if self.surface is None:
          output_torque = get_ioniq_6_low_speed_angle_assist_torque(desired_angle, actual_angle, output_torque, CS.vEgo)
        else:
          output_torque = self.surface_math.low_speed_output(self.surface, self.surface_math.SurfaceInput(
            CS.vEgo, setpoint, desired_lateral_jerk, desired_angle, actual_angle, output_torque, False, directional_taper))
    output_torque *= get_ioniq_6_highway_output_taper_scale(setpoint, CS.vEgo)
    output_torque *= get_ioniq_6_highway_transition_output_taper_scale(setpoint, desired_lateral_jerk, CS.vEgo)
    if self.is_2025:
      output_torque *= get_ioniq_6_2025_center_output_scale(setpoint, CS.vEgo)
      output_limit = get_ioniq_6_2025_low_speed_output_limit(setpoint, desired_lateral_jerk, CS.vEgo)
      output_torque = clip_scalar(output_torque, -output_limit, output_limit)
    pid_log.active = True
    pid_log.p = float(parent.pid.p)
    pid_log.i = float(parent.pid.i)
    pid_log.d = float(parent.pid.d)
    pid_log.f = float(parent.pid.f)
    pid_log.output = float(-output_torque)
    pid_log.actualLateralAccel = float(measurement)
    pid_log.desiredLateralAccel = float(setpoint)
    pid_log.desiredLateralJerk = float(desired_lateral_jerk)
    pid_log.saturated = bool(parent._check_saturation(parent.steer_max - abs(output_torque) < 1e-3,
                                                       CS, steer_limited_by_safety, curvature_limited))
    self.prev_steering_pressed = CS.steeringPressed
    return -output_torque, 0.0, pid_log


IONIQ_6_2023_CENTER_FRICTION_INCREMENT = 0.24

IONIQ_6_2023_CENTER_FRICTION_SPEED_BP = [3.5, 8.0]

IONIQ_6_2023_CENTER_FRICTION_LAT_BP = [0.12, 0.35]

IONIQ_6_2023_CENTER_FRICTION_ERROR_BP = [0.08, 0.18]

IONIQ_6_2023_CENTER_FRICTION_JERK_BP = [0.20, 0.60]

def get_ioniq_6_2023_center_friction_increment(v_ego: float, desired_lateral_accel: float, measured_lateral_accel: float,
                                             desired_lateral_jerk: float, future_lateral_accel: float) -> float:
  speed_weight = np.interp(v_ego, IONIQ_6_2023_CENTER_FRICTION_SPEED_BP, [0.0, 1.0])
  center_weight = np.interp(max(abs(desired_lateral_accel), abs(measured_lateral_accel), abs(future_lateral_accel)),
                            IONIQ_6_2023_CENTER_FRICTION_LAT_BP, [1.0, 0.0])
  tracking_weight = np.interp(abs(desired_lateral_accel - measured_lateral_accel),
                              IONIQ_6_2023_CENTER_FRICTION_ERROR_BP, [1.0, 0.0])
  jerk_weight = np.interp(abs(desired_lateral_jerk), IONIQ_6_2023_CENTER_FRICTION_JERK_BP, [1.0, 0.0])
  return float(IONIQ_6_2023_CENTER_FRICTION_INCREMENT * speed_weight * center_weight * tracking_weight * jerk_weight)
