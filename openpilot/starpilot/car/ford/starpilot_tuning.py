"""Ford lateral-control extensions.

The extended curvature strategy, manual-turn detector, and related safety protocol are substantially
adapted from BluePilot's Ford work, principally by Alan Polk and additional contributors. The audited
bp-7.0 reference is e1d051d7ba270261b4455068bd68f1a58db15a4a; source attribution is retained in
CREDITS.md and THIRD_PARTY_NOTICES.md. StarPilot reorganized that work for its own architecture and has since
changed its tuning and lookahead behavior.

See CREDITS.md for feature-level authorship and upstream commits, and THIRD_PARTY_NOTICES.md for the
published upstream license notices. Upstream contributors do not maintain this adaptation.
"""

from collections import deque
from dataclasses import dataclass

import numpy as np

from opendbc.car import ACCELERATION_DUE_TO_GRAVITY, DT_CTRL
from opendbc.car.ford.values import CAR, CarControllerParams, FordFlags
from opendbc.car.lateral import AngleSteeringLimits, ISO_LATERAL_ACCEL, apply_std_steer_angle_limits


# These rate-limit values descend from BluePilot's ``values_ext.py``, which carries the Haibin Wen
# and sunnypilot contributors copyright notice reproduced in THIRD_PARTY_NOTICES.md.
FORD_CURVATURE_LIMITS = AngleSteeringLimits(
  0.02,
  ([5, 16, 25], [0.0025, 0.0012, 0.00008]),
  ([5, 16, 25], [0.0025, 0.0014, 0.00018]),
)

MAX_LATERAL_ACCEL = ISO_LATERAL_ACCEL - ACCELERATION_DUE_TO_GRAVITY * 0.06
STEER_DT = CarControllerParams.STEER_STEP * DT_CTRL
CURVATURE_LOOKAHEAD_MIN = 0.20
CURVATURE_LOOKAHEAD_MAX = 0.40
MACH_E_HIGH_SPEED_LOOKAHEAD_EXTRA = 0.40
MACH_E_HIGH_SPEED_LOOKAHEAD_START_SPEED = 16.0
MACH_E_HIGH_SPEED_LOOKAHEAD_FULL_SPEED = 23.0
MACH_E_TURN_IN_LOOKAHEAD_EXTRA = 0.80
MACH_E_LOW_SPEED_TURN_IN_LOOKAHEAD_EXTRA = 1.60
MACH_E_LOW_SPEED_TURN_IN_START_SPEED = 2.0
MACH_E_LOW_SPEED_TURN_IN_FULL_SPEED = 3.0
MACH_E_LOW_SPEED_TURN_IN_MAX_SPEED = 12.0
MACH_E_LOW_SPEED_TURN_IN_FADE_SPEED = 15.0
MACH_E_TURN_IN_MIN_CURVATURE = 0.002
MACH_E_TURN_IN_FULL_CURVATURE = 0.008
MACH_E_TURN_IN_LAG_CURVATURE = 0.006
MACH_E_TURN_IN_PREVIEW_HOLD_SECONDS = 0.10
MACH_E_TURN_IN_PREVIEW_HOLD_MIN_LAG = 0.0005
MACH_E_TURN_IN_RATE_MIN_SPEED = 3.0
MACH_E_TURN_IN_RATE_MIN_DEFICIT = 0.002
MACH_E_UNWIND_LOOKAHEAD_EXTRA = 0.80
MACH_E_UNWIND_FULL_LAG_CURVATURE = 0.0005
MACH_E_UNWIND_PREVIEW_LAG_CURVATURE = 0.002
MACH_E_UNWIND_PREVIEW_HOLD_SECONDS = 0.10
MACH_E_UNWIND_PREVIEW_RISE_TOLERANCE = 0.0002
MACH_E_UNWIND_RELEASE_GAIN = 0.40
MACH_E_DIRECTION_CHANGE_MIN_SPEED = 9.0
MACH_E_DIRECTION_CHANGE_LOOKAHEAD_RAMP_SPEED = 10.0
MACH_E_DIRECTION_CHANGE_LOOKAHEAD_FULL_SPEED = 12.0
MACH_E_DIRECTION_CHANGE_LOOKAHEAD_FADE_SPEED = 15.0
MACH_E_DIRECTION_CHANGE_LOOKAHEAD_EXTRA = 2.40
MACH_E_DIRECTION_CHANGE_MIN_PREVIEW_CURVATURE = 0.0005
MACH_E_DIRECTION_CHANGE_FULL_PREVIEW_CURVATURE = 0.002
MACH_E_DIRECTION_CHANGE_MIN_LAG_CURVATURE = 0.0008
MACH_E_DIRECTION_CHANGE_FULL_LAG_CURVATURE = 0.0015
MACH_E_DIRECTION_CHANGE_EARLY_MIN_LAG_CURVATURE = -0.001
MACH_E_DIRECTION_CHANGE_EARLY_FULL_LAG_CURVATURE = 0.0008
MACH_E_DIRECTION_CHANGE_EARLY_MIN_CURVATURE = 0.002
MACH_E_DIRECTION_CHANGE_EARLY_FULL_CURVATURE = 0.004
MACH_E_LOW_SPEED_DIRECTION_CHANGE_START_SPEED = 1.8
MACH_E_LOW_SPEED_DIRECTION_CHANGE_FULL_SPEED = 2.0
MACH_E_LOW_SPEED_DIRECTION_CHANGE_HOLD_SPEED = 2.8
MACH_E_LOW_SPEED_DIRECTION_CHANGE_FADE_SPEED = 3.5
MACH_E_LOW_SPEED_DIRECTION_CHANGE_MIN_CURVATURE = 0.0004
MACH_E_LOW_SPEED_DIRECTION_CHANGE_FULL_CURVATURE = 0.0006
MACH_E_LOW_SPEED_DIRECTION_CHANGE_MAX_CURVATURE = 0.0015
MACH_E_SHARP_DIRECTION_CHANGE_START_SPEED = 1.5
MACH_E_SHARP_DIRECTION_CHANGE_FULL_SPEED = 1.8
MACH_E_SHARP_DIRECTION_CHANGE_HOLD_SPEED = 3.0
MACH_E_SHARP_DIRECTION_CHANGE_FADE_SPEED = 4.0
MACH_E_SHARP_DIRECTION_CHANGE_MIN_CURVATURE = 0.0002
MACH_E_SHARP_DIRECTION_CHANGE_FULL_CURVATURE = 0.0005
MACH_E_SHARP_DIRECTION_CHANGE_MIN_PREVIEW_CURVATURE = 0.008
MACH_E_SHARP_DIRECTION_CHANGE_FULL_PREVIEW_CURVATURE = 0.012
MACH_E_SHARP_DIRECTION_CHANGE_MIN_ACCEL = 1.8
MACH_E_SHARP_DIRECTION_CHANGE_FULL_ACCEL = 2.2
MACH_E_SHARP_DIRECTION_CHANGE_MIN_LAG_CURVATURE = -0.0005
MACH_E_SHARP_DIRECTION_CHANGE_FULL_LAG_CURVATURE = 0.0008
MACH_E_CURVATURE_ERROR_MAX = 0.006
MACH_E_UNDERSTEER_ERROR_MIN_DEFICIT = 0.002
MACH_E_UNDERSTEER_ERROR_FULL_DEFICIT = 0.004
MACH_E_PATH_ANGLE_MAX = 0.16
MACH_E_PATH_ANGLE_STEP = 0.055
MACH_E_PATH_ANGLE_FADE_START_SPEED = 8.0
MACH_E_PATH_ANGLE_MAX_SPEED = 8.8
MACH_E_PATH_ANGLE_TRACKING_FACTOR = 0.75
MACH_E_PATH_ANGLE_DRIVER_COOLDOWN = 0.75
MACH_E_DRIVER_ASSIST_MIN_SPEED = 2.0
MACH_E_DRIVER_ASSIST_MAX_SPEED = 15.0
MACH_E_DRIVER_ASSIST_MAX_TORQUE = 3.5
FORD_CURVATURE_LOOKAHEAD = {
  CAR.FORD_EXPLORER_MK6: 0.20,
}
FORD_CONSERVATIVE_PREVIEW_CARS = frozenset({
  CAR.FORD_MUSTANG_MACH_E_MK1,
})
FORD_SHARP_DIRECTION_CHANGE_CARS = frozenset({
  CAR.FORD_MUSTANG_MACH_E_MK1,
})
FORD_MANUAL_TURN_LATCH_CARS = frozenset({
  CAR.FORD_MUSTANG_MACH_E_MK1,
})
MANUAL_TURN_ENTRY_ANGLE_DEG = 12.0
MANUAL_TURN_RELEASE_ANGLE_DEG = 12.0
MANUAL_TURN_RECOVERY_SECONDS = 0.25


@dataclass(frozen=True)
class FordLateralResult:
  curvature: float = 0.0
  curvature_rate: float = 0.0
  path_angle: float = 0.0
  ramp_type: int = 0
  precision_type: int = 1
  active: bool = False


# Adapted from BluePilot HumanTurnDetector (Alan Polk, 97867c1eb57b7472f6fc3de62f0fef576e5a5497).
class HumanTurnDetector:
  ANGLE_DEG = 45.0
  HOLD_SECONDS = 1.5
  PRETURNED_HOLD_SECONDS = 3.0

  def __init__(self):
    self.timer = 0.0
    self.active = False
    self._pressed_last = False
    self._press_started_preturned = False

  def update(self, enabled: bool, steering_pressed: bool, steering_angle_deg: float) -> bool:
    if steering_pressed and not self._pressed_last:
      self._press_started_preturned = abs(steering_angle_deg) > self.ANGLE_DEG
    self._pressed_last = steering_pressed

    if enabled and steering_pressed and abs(steering_angle_deg) > self.ANGLE_DEG:
      self.timer += STEER_DT
    else:
      self.timer = 0.0

    hold_time = self.PRETURNED_HOLD_SECONDS if self._press_started_preturned else self.HOLD_SECONDS
    self.active = self.timer + 1e-9 >= hold_time
    return self.active

  def reset(self):
    self.timer = 0.0
    self.active = False
    self._pressed_last = False
    self._press_started_preturned = False


class StarPilotLateralTuning:

  def __init__(self, CP, measured_curvature):
    self.CP = CP
    self.measured_curvature = measured_curvature
    self.model = None
    self.time_indices = ()
    self.lookahead = CURVATURE_LOOKAHEAD_MIN

    self.human_turn_enabled = True
    self.curvature_blend_low = 0.4
    self.curvature_blend_high = 0.4
    self.curvature_lane_change_factor = 0.85

    self.human_turn = HumanTurnDetector()
    self.manual_turn_latched = False
    self.manual_turn_recovery_timer = 0.0
    self.manual_turn_direction = 0.0
    self.curvature_samples = deque(maxlen=max(2, round(0.3 / STEER_DT)))
    self.curvature_last = 0.0
    self.path_angle_last = 0.0
    self.path_angle_driver_cooldown = 0.0
    self.desired_curvature_last = 0.0
    self.turn_in_preview_hold_timer = 0.0
    self.unwind_preview_hold_timer = 0.0

  def _path_angle_assist_permitted(self, CC, CS) -> bool:
    # Current native LAT authority is independent of cruise and pedals.
    return bool(CC.latActive)

  def set_blend_settings(self, low: float, high: float, lane_change: float):
    self.curvature_blend_low = float(np.clip(low if np.isfinite(low) else 0.4, 0.0, 1.0))
    self.curvature_blend_high = float(np.clip(high if np.isfinite(high) else 0.4, 0.0, 1.0))
    self.curvature_lane_change_factor = float(np.clip(lane_change if np.isfinite(lane_change) else 0.85, 0.5, 1.25))

  def set_inputs(self, model, time_indices, lookahead: float, human_turn_enabled: bool):
    self.model = model
    self.time_indices = time_indices
    self.lookahead = float(np.clip(lookahead, CURVATURE_LOOKAHEAD_MIN, CURVATURE_LOOKAHEAD_MAX))
    self.human_turn_enabled = human_turn_enabled

  def _predicted_curvature(self, v_ego: float, lookup_time: float) -> float:
    if self.model is None or len(self.model.orientationRate.z) < 17:
      return 0.0
    curvatures = np.asarray(self.model.orientationRate.z) / max(v_ego, 0.01)
    return float(np.interp(lookup_time, self.time_indices, curvatures))

  def _curvature_lookahead(self) -> float:
    return self.lookahead

  def _lane_change(self) -> tuple[bool, int]:
    if self.model is None:
      return False, 0
    state = int(getattr(self.model.meta.laneChangeState, "raw", self.model.meta.laneChangeState))
    direction = int(getattr(self.model.meta.laneChangeDirection, "raw", self.model.meta.laneChangeDirection))
    return state in (1, 2, 3), direction

  def _high_speed_lookahead_extra(self, v_ego: float, steering_pressed: bool, lane_change: bool) -> float:
    if (self.CP.carFingerprint != CAR.FORD_MUSTANG_MACH_E_MK1 or not self.CP.flags & FordFlags.CANFD or
        steering_pressed or lane_change):
      return 0.0
    return MACH_E_HIGH_SPEED_LOOKAHEAD_EXTRA * float(np.interp(
      v_ego, [MACH_E_HIGH_SPEED_LOOKAHEAD_START_SPEED, MACH_E_HIGH_SPEED_LOOKAHEAD_FULL_SPEED], [0.0, 1.0]))

  def _current_curvature(self, CS) -> float:
    return float(self.measured_curvature(CS))

  def _driver_assisting_curve(self, CS, desired: float) -> bool:
    v_ego = float(CS.out.vEgoRaw)
    if (self.CP.carFingerprint != CAR.FORD_MUSTANG_MACH_E_MK1 or not self.CP.flags & FordFlags.CANFD or
        not CS.out.steeringPressed or not MACH_E_DRIVER_ASSIST_MIN_SPEED <= v_ego < MACH_E_DRIVER_ASSIST_MAX_SPEED or
        self._lane_change()[0] or abs(desired) < MACH_E_TURN_IN_MIN_CURVATURE or
        desired * CS.out.steeringTorque >= 0.0 or abs(CS.out.steeringTorque) > MACH_E_DRIVER_ASSIST_MAX_TORQUE):
      return False
    current = self._current_curvature(CS)
    preview = self._predicted_curvature(v_ego, self._curvature_lookahead() + MACH_E_TURN_IN_LOOKAHEAD_EXTRA)
    return bool(desired * current > 0.0 and desired * preview > 0.0 and
                abs(preview) >= MACH_E_TURN_IN_FULL_CURVATURE and
                np.sign(desired) * (current - desired) <= CarControllerParams.CURVATURE_ERROR)

  def _curvature_error_limit(self, requested: float, desired: float, current: float, v_ego: float,
                             steering_pressed: bool, lane_change: bool, predicted: float = 0.0) -> float:
    base = CarControllerParams.CURVATURE_ERROR
    if (self.CP.carFingerprint != CAR.FORD_MUSTANG_MACH_E_MK1 or not self.CP.flags & FordFlags.CANFD or
        steering_pressed or lane_change):
      return base
    speed_weight = float(np.interp(v_ego, [8.0, 9.0, 14.0, 16.0], [0.0, 1.0, 1.0, 0.0]))
    deficit_weight = 0.0
    planned_curve = abs(desired) >= 0.003 or (abs(requested) >= 0.003 and requested * predicted > 0.0)
    if requested * desired > 0.0 and planned_curve:
      deficit = np.sign(desired) * (requested - current)
      deficit_weight = float(np.interp(
        deficit, [MACH_E_UNDERSTEER_ERROR_MIN_DEFICIT, MACH_E_UNDERSTEER_ERROR_FULL_DEFICIT], [0.0, 1.0]))
    if current * desired < 0.0:
      predicted = self._predicted_curvature(v_ego, self._curvature_lookahead() + MACH_E_TURN_IN_LOOKAHEAD_EXTRA)
    reversal_weight = 0.0
    if current * predicted < 0.0 and current * (requested - current) < 0.0:
      reversal_weight = float(np.interp(
        abs(predicted),
        [MACH_E_DIRECTION_CHANGE_MIN_PREVIEW_CURVATURE, MACH_E_DIRECTION_CHANGE_FULL_PREVIEW_CURVATURE],
        [0.0, 1.0],
      ))
    unwind_weight = 0.0
    if (self.model is not None and len(self.model.orientationRate.z) >= 17 and
        requested * current >= 0.0 and desired * current >= 0.0 and abs(desired) < abs(current)):
      unwind_deficit = np.sign(current) * (current - requested)
      preview_deficit = np.sign(current) * (current - predicted)
      unwind_weight = float(np.interp(
        min(unwind_deficit, preview_deficit),
        [MACH_E_UNDERSTEER_ERROR_MIN_DEFICIT, MACH_E_UNDERSTEER_ERROR_FULL_DEFICIT],
        [0.0, 1.0],
      ))
    return base + (MACH_E_CURVATURE_ERROR_MAX - base) * speed_weight * max(deficit_weight, reversal_weight, unwind_weight)

  def _path_angle_assist(self, requested: float, desired: float, applied: float, current: float, v_ego: float,
                         steering_pressed: bool, lane_change: bool) -> float:
    if steering_pressed:
      self.path_angle_driver_cooldown = MACH_E_PATH_ANGLE_DRIVER_COOLDOWN
    else:
      self.path_angle_driver_cooldown = max(0.0, self.path_angle_driver_cooldown - STEER_DT)
    target = 0.0
    if (self.CP.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1 and self.CP.flags & FordFlags.CANFD and
        not steering_pressed and self.path_angle_driver_cooldown == 0.0 and not lane_change and
        3.0 <= v_ego < MACH_E_PATH_ANGLE_MAX_SPEED and
        requested * desired > 0.0 and requested * applied > 0.0 and
        abs(requested) > 0.0198 and abs(desired) > MACH_E_TURN_IN_FULL_CURVATURE and abs(applied) >= 0.0195 and
        np.sign(desired) * (desired - current) > 0.002):
      max_curvature = MAX_LATERAL_ACCEL / v_ego ** 2
      residual = max(0.0, min(max(abs(requested), abs(desired)), max_curvature) - abs(applied))
      tracking_deficit = max(0.0, np.sign(applied) * (applied - current))
      acceleration_headroom = max(0.0, (max_curvature - abs(applied)) * v_ego)
      speed_weight = float(np.interp(
        v_ego, [MACH_E_PATH_ANGLE_FADE_START_SPEED, MACH_E_PATH_ANGLE_MAX_SPEED], [1.0, 0.0]))
      target = float(np.sign(applied) * min(
        (residual + MACH_E_PATH_ANGLE_TRACKING_FACTOR * tracking_deficit) * v_ego,
        acceleration_headroom, MACH_E_PATH_ANGLE_MAX) * speed_weight)
    if target == 0.0 or target * self.path_angle_last < 0.0:
      self.path_angle_last = 0.0
    else:
      self.path_angle_last = float(np.clip(
        target, self.path_angle_last - MACH_E_PATH_ANGLE_STEP, self.path_angle_last + MACH_E_PATH_ANGLE_STEP))
    return self.path_angle_last

  def _blend_and_scale(self, desired: float, predicted: float, v_ego: float, current: float = 0.0,
                       allow_opposite_preview: bool = False) -> tuple[float, int]:
    blend = float(np.interp(abs(desired), [0.0, 0.001], [self.curvature_blend_low, self.curvature_blend_high]))
    if self.CP.carFingerprint in FORD_CONSERVATIVE_PREVIEW_CARS:
      if desired * predicted <= 0.0 and not allow_opposite_preview:
        blend = 0.0
      elif current * predicted > 0.0 and abs(current) > abs(desired) and abs(predicted) > abs(desired):
        blend *= abs(desired) / abs(predicted)
    requested = predicted * blend + desired * (1.0 - blend)
    lane_change, direction = self._lane_change()
    precision = 1
    if lane_change:
      factor = float(np.interp(v_ego, [4.4, 40.23], [0.95, self.curvature_lane_change_factor]))
      if (direction == 1 and requested < 0.0) or (direction == 2 and requested > 0.0):
        requested *= factor
        precision = 0
    return requested, precision

  def _unwind_preview(self, desired: float, predicted: float, current: float, v_ego: float,
                      allow_plateau: bool = False) -> float:
    if self.CP.carFingerprint != CAR.FORD_MUSTANG_MACH_E_MK1:
      return predicted
    if desired * current <= 0.0 or desired * predicted <= 0.0 or desired * self.desired_curvature_last <= 0.0:
      return predicted
    if abs(desired) >= abs(self.desired_curvature_last) and not allow_plateau:
      return predicted
    preview = self._predicted_curvature(v_ego, self._curvature_lookahead() + MACH_E_UNWIND_LOOKAHEAD_EXTRA)
    if desired * preview <= 0.0 or abs(preview) >= min(abs(desired), abs(predicted), abs(current)):
      return predicted
    speed_weight = float(np.interp(v_ego, [5.0, 7.0, 12.0, 15.0], [0.0, 1.0, 1.0, 0.0]))
    curvature_weight = float(np.interp(abs(desired), [0.002, 0.008], [0.0, 1.0]))
    lag_weight = float(np.interp(abs(current) - abs(desired), [0.0, MACH_E_UNWIND_FULL_LAG_CURVATURE], [0.0, 1.0]))
    preview_lag_weight = float(np.interp(
      abs(current) - abs(preview), [0.0, MACH_E_UNWIND_PREVIEW_LAG_CURVATURE], [0.0, 1.0]))
    lag_weight = max(lag_weight, preview_lag_weight)
    return predicted + speed_weight * curvature_weight * lag_weight * (preview - predicted)

  def _turn_in_preview_plateau(self, desired: float, current: float, v_ego: float,
                              steering_pressed: bool, lane_change: bool) -> float:
    if (self.CP.carFingerprint != CAR.FORD_MUSTANG_MACH_E_MK1 or not self.CP.flags & FordFlags.CANFD or
        not MACH_E_LOW_SPEED_TURN_IN_START_SPEED <= v_ego < MACH_E_LOW_SPEED_TURN_IN_FADE_SPEED or
        steering_pressed or lane_change or desired * self.desired_curvature_last < 0.0 or
        abs(desired) < abs(self.desired_curvature_last)):
      self.turn_in_preview_hold_timer = 0.0
      return 0.0
    if abs(desired) > abs(self.desired_curvature_last):
      self.turn_in_preview_hold_timer = MACH_E_TURN_IN_PREVIEW_HOLD_SECONDS
      return 0.0
    self.turn_in_preview_hold_timer = max(0.0, self.turn_in_preview_hold_timer - STEER_DT)
    if (self.turn_in_preview_hold_timer <= 1e-9 or
        np.sign(desired) * (desired - current) <= MACH_E_TURN_IN_PREVIEW_HOLD_MIN_LAG):
      return 0.0
    return float(np.interp(v_ego, [2.0, 3.0, 14.0, 15.0], [0.0, 1.0, 1.0, 0.0]))

  def _unwind_preview_plateau(self, desired: float, v_ego: float, steering_pressed: bool, lane_change: bool) -> bool:
    if (self.CP.carFingerprint != CAR.FORD_MUSTANG_MACH_E_MK1 or not self.CP.flags & FordFlags.CANFD or
        not 5.0 < v_ego < 15.0 or steering_pressed or lane_change or
        desired * self.desired_curvature_last <= 0.0 or
        abs(desired) - abs(self.desired_curvature_last) > MACH_E_UNWIND_PREVIEW_RISE_TOLERANCE):
      self.unwind_preview_hold_timer = 0.0
      return False
    if abs(desired) < abs(self.desired_curvature_last):
      self.unwind_preview_hold_timer = MACH_E_UNWIND_PREVIEW_HOLD_SECONDS
      return False
    self.unwind_preview_hold_timer = max(0.0, self.unwind_preview_hold_timer - STEER_DT)
    return self.unwind_preview_hold_timer > 1e-9

  def _unwind_request(self, desired: float, predicted: float, command_predicted: float, requested: float) -> float:
    delta = command_predicted - predicted
    if (self.CP.carFingerprint != CAR.FORD_MUSTANG_MACH_E_MK1 or not self.CP.flags & FordFlags.CANFD or
        not np.isfinite([desired, predicted, command_predicted, requested]).all() or
        desired * command_predicted <= 0.0 or desired * delta >= 0.0):
      return requested
    release_weight = float(np.interp(abs(delta), [0.002, 0.006], [0.0, 1.0]))
    blend = float(np.interp(abs(desired), [0.0, 0.001], [self.curvature_blend_low, self.curvature_blend_high]))
    preview_authority = float(np.clip(blend / 0.4, 0.0, 1.0))
    return float(np.clip(requested + MACH_E_UNWIND_RELEASE_GAIN * preview_authority * release_weight * delta,
                         min(desired, command_predicted), max(desired, command_predicted)))

  def _reversal_followthrough_preview(self, desired: float, predicted: float, current: float, v_ego: float,
                                      steering_pressed: bool, lane_change: bool) -> float:
    if (self.CP.carFingerprint != CAR.FORD_MUSTANG_MACH_E_MK1 or not self.CP.flags & FordFlags.CANFD or
        steering_pressed or lane_change or desired * current >= 0.0):
      return predicted
    speed_weight = float(np.interp(v_ego, [8.0, 9.0, 14.0, 16.0], [0.0, 1.0, 1.0, 0.0]))
    if speed_weight == 0.0:
      return predicted
    preview = self._predicted_curvature(v_ego, self._curvature_lookahead() + MACH_E_TURN_IN_LOOKAHEAD_EXTRA)
    if not np.isfinite(preview) or desired * preview <= 0.0 or np.sign(desired) * (preview - predicted) <= 0.0:
      return predicted
    preview_weight = float(np.interp(
      abs(preview), [MACH_E_DIRECTION_CHANGE_MIN_PREVIEW_CURVATURE, MACH_E_DIRECTION_CHANGE_FULL_PREVIEW_CURVATURE],
      [0.0, 1.0]))
    lag_weight = float(np.interp(
      abs(current), [MACH_E_DIRECTION_CHANGE_MIN_LAG_CURVATURE, MACH_E_DIRECTION_CHANGE_FULL_LAG_CURVATURE],
      [0.0, 1.0]))
    return predicted + speed_weight * preview_weight * lag_weight * (preview - predicted)

  def _turn_in_preview_weight(self, desired: float, preview: float, current: float,
                              allow_plateau: float = 0.0) -> float:
    if self.CP.carFingerprint not in FORD_CONSERVATIVE_PREVIEW_CARS:
      return 0.0
    if desired * preview <= 0.0 or desired * self.desired_curvature_last < 0.0:
      return 0.0
    if (abs(desired) < abs(self.desired_curvature_last) or
        (abs(desired) == abs(self.desired_curvature_last) and not allow_plateau)):
      return 0.0

    target = max(abs(desired), abs(preview))
    curvature_weight = float(np.interp(
      target,
      [MACH_E_TURN_IN_MIN_CURVATURE, MACH_E_TURN_IN_FULL_CURVATURE],
      [0.0, 1.0],
    ))
    direction = float(np.sign(desired))
    lag_weight = float(np.clip(
      (target - direction * current) / MACH_E_TURN_IN_LAG_CURVATURE,
      0.0, 1.0,
    ))
    plateau_weight = allow_plateau if abs(desired) == abs(self.desired_curvature_last) else 1.0
    return curvature_weight * lag_weight * plateau_weight

  def _turn_in_curvature_rate(self, desired: float, current: float, curvature_rate: float,
                              v_ego: float, lookahead: float, steering_pressed: bool, lane_change: bool) -> float:
    if (self.CP.carFingerprint != CAR.FORD_MUSTANG_MACH_E_MK1 or not self.CP.flags & FordFlags.CANFD or
        not MACH_E_TURN_IN_RATE_MIN_SPEED <= v_ego < MACH_E_LOW_SPEED_TURN_IN_FADE_SPEED or
        steering_pressed or lane_change or desired * self.desired_curvature_last <= 0.0 or
        abs(desired) < abs(self.desired_curvature_last) or desired * curvature_rate >= 0.0 or
        np.sign(desired) * (desired - current) <= MACH_E_TURN_IN_RATE_MIN_DEFICIT):
      return curvature_rate
    preview = self._predicted_curvature(v_ego, lookahead + MACH_E_TURN_IN_LOOKAHEAD_EXTRA)
    if (desired * preview > 0.0 and abs(preview) >= abs(desired) and
        np.sign(desired) * (preview - current) > MACH_E_TURN_IN_RATE_MIN_DEFICIT):
      return 0.0
    return curvature_rate

  @staticmethod
  def _turn_in_lookahead_extra(v_ego: float) -> float:
    return float(np.interp(
      v_ego,
      [MACH_E_LOW_SPEED_TURN_IN_START_SPEED, MACH_E_LOW_SPEED_TURN_IN_FULL_SPEED,
       MACH_E_LOW_SPEED_TURN_IN_MAX_SPEED, MACH_E_LOW_SPEED_TURN_IN_FADE_SPEED],
      [MACH_E_TURN_IN_LOOKAHEAD_EXTRA, MACH_E_LOW_SPEED_TURN_IN_LOOKAHEAD_EXTRA,
       MACH_E_LOW_SPEED_TURN_IN_LOOKAHEAD_EXTRA, MACH_E_TURN_IN_LOOKAHEAD_EXTRA],
    ))

  @staticmethod
  def _direction_change_lookahead_extra(v_ego: float) -> float:
    return float(np.interp(
      v_ego,
      [MACH_E_DIRECTION_CHANGE_MIN_SPEED, MACH_E_DIRECTION_CHANGE_LOOKAHEAD_RAMP_SPEED,
       MACH_E_DIRECTION_CHANGE_LOOKAHEAD_FULL_SPEED, MACH_E_DIRECTION_CHANGE_LOOKAHEAD_FADE_SPEED],
      [MACH_E_TURN_IN_LOOKAHEAD_EXTRA, MACH_E_DIRECTION_CHANGE_LOOKAHEAD_EXTRA,
       MACH_E_DIRECTION_CHANGE_LOOKAHEAD_EXTRA, MACH_E_TURN_IN_LOOKAHEAD_EXTRA],
    ))

  def _direction_change_preview_weight(self, desired: float, preview: float, current: float,
                                       allow_rising_desired: bool = False, early_handoff_weight: float = 0.0,
                                       sharp_handoff: bool = False) -> float:
    if self.CP.carFingerprint not in FORD_CONSERVATIVE_PREVIEW_CARS:
      return 0.0
    if desired * preview >= 0.0 or desired * self.desired_curvature_last <= 0.0 or desired * current <= 0.0:
      return 0.0
    early_handoff_weight = float(np.clip(early_handoff_weight, 0.0, 1.0))
    lag = abs(current) - abs(desired)
    if sharp_handoff:
      lag_min = MACH_E_SHARP_DIRECTION_CHANGE_MIN_LAG_CURVATURE
      lag_full = MACH_E_SHARP_DIRECTION_CHANGE_FULL_LAG_CURVATURE
    else:
      lag_min = float(np.interp(
        early_handoff_weight, [0.0, 1.0],
        [MACH_E_DIRECTION_CHANGE_MIN_LAG_CURVATURE, MACH_E_DIRECTION_CHANGE_EARLY_MIN_LAG_CURVATURE],
      ))
      lag_full = float(np.interp(
        early_handoff_weight, [0.0, 1.0],
        [MACH_E_DIRECTION_CHANGE_FULL_LAG_CURVATURE, MACH_E_DIRECTION_CHANGE_EARLY_FULL_LAG_CURVATURE],
      ))
    desired_rising = abs(desired) >= abs(self.desired_curvature_last)
    rising_handoff = (allow_rising_desired and abs(desired) > abs(self.desired_curvature_last) and
                      abs(desired) <= MACH_E_LOW_SPEED_DIRECTION_CHANGE_MAX_CURVATURE)
    early_rising_handoff = early_handoff_weight > 0.0 and lag > lag_min
    if desired_rising and not rising_handoff and not early_rising_handoff:
      return 0.0

    preview_weight = float(np.interp(
      abs(preview),
      [MACH_E_DIRECTION_CHANGE_MIN_PREVIEW_CURVATURE, MACH_E_DIRECTION_CHANGE_FULL_PREVIEW_CURVATURE],
      [0.0, 1.0],
    ))
    if sharp_handoff:
      preview_weight *= float(np.interp(
        abs(preview),
        [MACH_E_SHARP_DIRECTION_CHANGE_MIN_PREVIEW_CURVATURE, MACH_E_SHARP_DIRECTION_CHANGE_FULL_PREVIEW_CURVATURE],
        [0.0, 1.0],
      ))
    lag_weight = float(np.interp(
      lag,
      [lag_min, lag_full],
      [0.0, 1.0],
    ))
    return preview_weight * lag_weight

  @staticmethod
  def _low_speed_direction_change_weight(v_ego: float, desired: float) -> float:
    speed_weight = float(np.interp(
      v_ego,
      [MACH_E_LOW_SPEED_DIRECTION_CHANGE_START_SPEED, MACH_E_LOW_SPEED_DIRECTION_CHANGE_FULL_SPEED,
       MACH_E_LOW_SPEED_DIRECTION_CHANGE_HOLD_SPEED, MACH_E_LOW_SPEED_DIRECTION_CHANGE_FADE_SPEED],
      [0.0, 1.0, 1.0, 0.0],
    ))
    curvature_weight = float(np.interp(
      abs(desired),
      [MACH_E_LOW_SPEED_DIRECTION_CHANGE_MIN_CURVATURE, MACH_E_LOW_SPEED_DIRECTION_CHANGE_FULL_CURVATURE],
      [0.0, 1.0],
    ))
    return speed_weight * curvature_weight

  @staticmethod
  def _sharp_direction_change_weight(v_ego: float, a_ego: float, desired: float, preview: float) -> float:
    speed_weight = float(np.interp(
      v_ego,
      [MACH_E_SHARP_DIRECTION_CHANGE_START_SPEED, MACH_E_SHARP_DIRECTION_CHANGE_FULL_SPEED,
       MACH_E_SHARP_DIRECTION_CHANGE_HOLD_SPEED, MACH_E_SHARP_DIRECTION_CHANGE_FADE_SPEED],
      [0.0, 1.0, 1.0, 0.0],
    ))
    curvature_weight = float(np.interp(
      abs(desired),
      [MACH_E_SHARP_DIRECTION_CHANGE_MIN_CURVATURE, MACH_E_SHARP_DIRECTION_CHANGE_FULL_CURVATURE],
      [0.0, 1.0],
    ))
    preview_weight = float(np.interp(
      abs(preview),
      [MACH_E_SHARP_DIRECTION_CHANGE_MIN_PREVIEW_CURVATURE, MACH_E_SHARP_DIRECTION_CHANGE_FULL_PREVIEW_CURVATURE],
      [0.0, 1.0],
    ))
    acceleration_weight = float(np.interp(
      a_ego,
      [MACH_E_SHARP_DIRECTION_CHANGE_MIN_ACCEL, MACH_E_SHARP_DIRECTION_CHANGE_FULL_ACCEL],
      [0.0, 1.0],
    ))
    return speed_weight * curvature_weight * preview_weight * acceleration_weight

  def _manual_turn(self, CC, CS, desired: float, driver_assisting: bool = False) -> bool:
    if not CC.latActive:
      self.human_turn.reset()
      self.manual_turn_latched = False
      self.manual_turn_recovery_timer = 0.0
      self.manual_turn_direction = 0.0
      return False
    detected = self.human_turn.update(
      self.human_turn_enabled and not driver_assisting, CS.out.steeringPressed, CS.out.steeringAngleDeg)
    if self.CP.carFingerprint not in FORD_MANUAL_TURN_LATCH_CARS:
      return detected

    if not self.human_turn_enabled:
      self.manual_turn_latched = False
      self.manual_turn_recovery_timer = 0.0
      self.manual_turn_direction = 0.0
      return False

    blinker_direction = float(CS.out.rightBlinker) - float(CS.out.leftBlinker)
    driver_turning_with_signal = (
      CS.out.steeringPressed and not driver_assisting and abs(CS.out.steeringAngleDeg) >= MANUAL_TURN_ENTRY_ANGLE_DEG and
      blinker_direction != 0.0 and not self._lane_change()[0] and
      CS.out.steeringTorque * blinker_direction < 0.0
    )
    if self.model is not None and (detected or driver_turning_with_signal):
      self.manual_turn_latched = True
      if blinker_direction != 0.0:
        self.manual_turn_direction = blinker_direction
      elif self.manual_turn_direction == 0.0:
        self.manual_turn_direction = -float(np.sign(CS.out.steeringAngleDeg))

    if not self.manual_turn_latched:
      self.manual_turn_recovery_timer = 0.0
      self.manual_turn_direction = 0.0
      return False

    following_next_curve = (
      driver_assisting and not CS.out.leftBlinker and not CS.out.rightBlinker and
      CS.out.vEgoRaw >= MACH_E_DIRECTION_CHANGE_MIN_SPEED and self.manual_turn_direction * desired < 0.0
    )
    if not following_next_curve and (CS.out.steeringPressed or blinker_direction != 0.0 or
                                    abs(CS.out.steeringAngleDeg) > MANUAL_TURN_RELEASE_ANGLE_DEG):
      self.manual_turn_recovery_timer = 0.0
    else:
      self.manual_turn_recovery_timer += STEER_DT
      if self.manual_turn_recovery_timer + 1e-9 >= MANUAL_TURN_RECOVERY_SECONDS:
        current = self._current_curvature(CS)
        if (self.manual_turn_direction * desired > 0.0 and
            self.manual_turn_direction * (desired - current) > CarControllerParams.CURVATURE_ERROR):
          self.manual_turn_recovery_timer = MANUAL_TURN_RECOVERY_SECONDS
        else:
          self.manual_turn_latched = False
          self.manual_turn_recovery_timer = 0.0
          self.manual_turn_direction = 0.0

    return self.manual_turn_latched

  def update(self, CC, CS, actuators) -> FordLateralResult:
    current = self._current_curvature(CS)
    if not CC.latActive:
      self.human_turn.reset()
      self.manual_turn_latched = False
      self.manual_turn_recovery_timer = 0.0
      self.manual_turn_direction = 0.0
      self.curvature_samples.clear()
      self.curvature_last = 0.0
      self.path_angle_last = 0.0
      self.path_angle_driver_cooldown = 0.0
      self.desired_curvature_last = 0.0
      self.turn_in_preview_hold_timer = 0.0
      self.unwind_preview_hold_timer = 0.0
      return FordLateralResult()

    desired = float(actuators.curvature)
    driver_assisting = self._driver_assisting_curve(CS, desired)
    driver_override = bool(CS.out.steeringPressed) and not driver_assisting
    manual_turn = self._manual_turn(CC, CS, desired, driver_assisting)
    if manual_turn or CS.out.vEgoRaw < 0.1:
      if CS.out.steeringPressed:
        self.path_angle_driver_cooldown = MACH_E_PATH_ANGLE_DRIVER_COOLDOWN
      self.curvature_samples.clear()
      self.curvature_last = 0.0
      self.path_angle_last = 0.0
      self.desired_curvature_last = 0.0
      self.turn_in_preview_hold_timer = 0.0
      self.unwind_preview_hold_timer = 0.0
      return FordLateralResult(active=not (
        manual_turn and self.CP.carFingerprint in FORD_MANUAL_TURN_LATCH_CARS))

    v_ego = float(CS.out.vEgoRaw)
    lookahead = self._curvature_lookahead()
    lookahead += self._high_speed_lookahead_extra(v_ego, bool(CS.out.steeringPressed), self._lane_change()[0])
    predicted = self._predicted_curvature(v_ego, lookahead)
    allow_opposite_preview = False
    if self.CP.carFingerprint in FORD_CONSERVATIVE_PREVIEW_CARS:
      turn_in_plateau = self._turn_in_preview_plateau(
        desired, current, v_ego, bool(CS.out.steeringPressed), self._lane_change()[0])
      turn_in_predicted = self._predicted_curvature(v_ego, lookahead + MACH_E_TURN_IN_LOOKAHEAD_EXTRA)
      direction_change_predicted = turn_in_predicted
      direction_change_weight = 0.0
      sharp_direction_change_weight = 0.0
      direction_change_speed_weight = float(v_ego > MACH_E_DIRECTION_CHANGE_MIN_SPEED)
      low_speed_direction_change = direction_change_speed_weight == 0.0
      if direction_change_speed_weight == 0.0:
        direction_change_speed_weight = self._low_speed_direction_change_weight(v_ego, desired)
        if self.CP.carFingerprint in FORD_SHARP_DIRECTION_CHANGE_CARS:
          sharp_direction_change_weight = self._sharp_direction_change_weight(
            v_ego, float(CS.out.aEgo), desired, turn_in_predicted)
        direction_change_speed_weight = max(direction_change_speed_weight, sharp_direction_change_weight)
      if direction_change_speed_weight > 0.0 and not CS.out.steeringPressed and not self._lane_change()[0]:
        direction_change_lookahead_extra = self._direction_change_lookahead_extra(v_ego)
        early_handoff_weight = float(np.interp(
          direction_change_lookahead_extra,
          [MACH_E_TURN_IN_LOOKAHEAD_EXTRA, MACH_E_DIRECTION_CHANGE_LOOKAHEAD_EXTRA],
          [0.0, 1.0],
        ))
        early_handoff_weight *= float(np.interp(
          abs(desired),
          [MACH_E_DIRECTION_CHANGE_EARLY_MIN_CURVATURE, MACH_E_DIRECTION_CHANGE_EARLY_FULL_CURVATURE],
          [0.0, 1.0],
        ))
        if direction_change_lookahead_extra > MACH_E_TURN_IN_LOOKAHEAD_EXTRA:
          direction_change_predicted = self._predicted_curvature(v_ego, lookahead + direction_change_lookahead_extra)
        direction_change_weight = self._direction_change_preview_weight(
          desired, direction_change_predicted, current, allow_rising_desired=low_speed_direction_change,
          early_handoff_weight=early_handoff_weight, sharp_handoff=sharp_direction_change_weight > 0.0)
        direction_change_weight *= direction_change_speed_weight
      if direction_change_weight > 0.0:
        predicted = float(np.interp(direction_change_weight, [0.0, 1.0], [predicted, direction_change_predicted]))
        allow_opposite_preview = True
      else:
        turn_in_lookahead_extra = self._turn_in_lookahead_extra(v_ego)
        if (turn_in_lookahead_extra > MACH_E_TURN_IN_LOOKAHEAD_EXTRA and
            desired * self.desired_curvature_last >= 0.0 and
            (abs(desired) > abs(self.desired_curvature_last) or turn_in_plateau)):
          low_speed_turn_in_predicted = self._predicted_curvature(v_ego, lookahead + turn_in_lookahead_extra)
          if (desired * low_speed_turn_in_predicted > 0.0 and
              abs(low_speed_turn_in_predicted) > abs(turn_in_predicted)):
            turn_in_predicted = low_speed_turn_in_predicted
        turn_in_weight = self._turn_in_preview_weight(desired, turn_in_predicted, current, turn_in_plateau)
        if turn_in_weight > 0.0:
          turn_in_target = float(np.copysign(max(abs(desired), abs(turn_in_predicted)), desired))
          predicted = float(np.interp(turn_in_weight, [0.0, 1.0], [predicted, turn_in_target]))
    unwind_plateau = self._unwind_preview_plateau(
      desired, v_ego, bool(CS.out.steeringPressed), self._lane_change()[0])
    command_predicted = predicted
    canfd_mach_e = self.CP.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1 and self.CP.flags & FordFlags.CANFD
    if (not allow_opposite_preview or canfd_mach_e) and not CS.out.steeringPressed and not self._lane_change()[0]:
      command_predicted = self._unwind_preview(desired, predicted, current, v_ego, unwind_plateau)
    unwind_selected = command_predicted != predicted
    command_predicted = self._reversal_followthrough_preview(
      desired, command_predicted, current, v_ego, bool(CS.out.steeringPressed), self._lane_change()[0])
    requested, precision = self._blend_and_scale(desired, command_predicted, v_ego, current, allow_opposite_preview)
    if unwind_selected:
      requested = self._unwind_request(desired, predicted, command_predicted, requested)

    if v_ego > 9.0:
      error_limit = self._curvature_error_limit(
        requested, desired, current, v_ego, driver_override, self._lane_change()[0], command_predicted)
      requested = float(np.clip(requested, current - error_limit, current + error_limit))
    applied = float(apply_std_steer_angle_limits(
      requested, self.curvature_last, v_ego, CS.out.steeringAngleDeg, True, FORD_CURVATURE_LIMITS))
    if self.CP.flags & FordFlags.CANFD:
      max_curvature = MAX_LATERAL_ACCEL / max(v_ego, 1.0) ** 2
      applied = float(np.clip(applied, -max_curvature, max_curvature))
    path_angle = self._path_angle_assist(
      requested, desired, applied, current, v_ego, driver_override, self._lane_change()[0])
    if path_angle != 0.0 and not self._path_angle_assist_permitted(CC, CS):
      self.path_angle_last = 0.0
      path_angle = 0.0

    self.curvature_samples.append(predicted)
    curvature_rate = 0.0
    if len(self.curvature_samples) > 1:
      sample_time = (len(self.curvature_samples) - 1) * STEER_DT
      curvature_rate = (self.curvature_samples[-1] - self.curvature_samples[0]) / max(sample_time * v_ego, 0.01)
      curvature_rate *= float(np.interp(abs(predicted), [0.0, 0.008, 0.01], [0.0, 0.0, 1.0]))
      curvature_rate *= float(np.interp(v_ego, [0.0, 14.5, 15.5], [1.0, 1.0, 0.0]))
      if self._lane_change()[0]:
        curvature_rate = 0.0

    if (self.CP.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1 and self.CP.flags & FordFlags.CANFD and
        unwind_selected and command_predicted * curvature_rate > 0.0):
      curvature_rate = 0.0

    curvature_rate = self._turn_in_curvature_rate(
      desired, current, curvature_rate, v_ego, lookahead, bool(CS.out.steeringPressed), self._lane_change()[0])
    self.desired_curvature_last = desired

    self.curvature_last = float(np.clip(applied, -0.02, 0.02))
    min_curvature_rate = -0.001024
    if self.CP.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1 and self.CP.flags & FordFlags.CANFD:
      min_curvature_rate = -0.001023
    curvature_rate = float(np.clip(curvature_rate, min_curvature_rate, 0.001023))
    return FordLateralResult(
      curvature=self.curvature_last,
      curvature_rate=curvature_rate,
      path_angle=path_angle,
      ramp_type=2,
      precision_type=precision,
      active=True,
    )


class ExplorerLateralTuning(StarPilotLateralTuning):
  def _curvature_lookahead(self) -> float:
    return 0.20


def create_tuning(cp, measured_curvature):
  tuning = ExplorerLateralTuning if cp.carFingerprint == CAR.FORD_EXPLORER_MK6 else StarPilotLateralTuning
  return tuning(cp, measured_curvature)
