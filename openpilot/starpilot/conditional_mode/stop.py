"""Frozen CEM stop-light model-length detector over explicit owner evidence.

The current model action.shouldStop can veto release, but cannot acquire a light.
No Params, IPC, sign recognition, or longitudinal control is owned here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from opendbc.car.ford.values import CAR as FORD_CAR

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from openpilot.starpilot.conditional_mode.turn_scene import committed_turn_scene


FORD_STOP_NAMES = frozenset(str(car) for car in FORD_CAR)

THRESHOLD = 1.0 - 1.0 / math.e
MAX_SOURCE_AGE_S = 0.25
MAX_FRAME_GAP_S = MAX_SOURCE_AGE_S
MPH_TO_MPS = 0.44704
MPS_TO_MPH = 1.0 / MPH_TO_MPS
FROZEN_RAW_STOP_DISTANCE_M = 50.0  # CRUISING_SPEED * PLANNER_TIME.
STOP_MODEL_RELEASE_MARGIN_M = 4.0  # Existing model-stop off margin.


@dataclass(frozen=True)
class StopLead:
  present: bool
  distance_m: float | None = None
  speed_mps: float | None = None
  radar: bool | None = None
  model_probability: float | None = None
  tracked: bool | None = None


@dataclass(frozen=True)
class StopFrame:
  observed_mono_s: float
  now_mono_s: float
  speed_mps: float | None
  model_horizon_m: float | None
  model_stop_time_s: float | None
  traffic_mode: bool | None
  stop_sign_confirmed: bool | None
  forcing_stop: bool | None
  lead: StopLead | None
  standstill: bool | None
  left_blinker: bool | None
  right_blinker: bool | None
  steering_angle_deg: float | None
  driving_in_curve: bool | None
  car_fingerprint: str | None
  dashboard_stop_sign: bool | None = None
  pedal_override: bool | None = None
  model_tick_mono_s: float | None = None
  model_should_stop: bool | None = None
  stationary_launch_clear: bool = False
  standstill_path_extent_m: float | None = None
  longitudinal_stop_active: bool = False
  longitudinal_launch_active: bool = False


@dataclass(frozen=True)
class StopObservation:
  light_detected: bool | None
  standstill_hold: bool | None
  standstill_reason: str | None
  model_stopping: bool | None


def _finite(value: object, low: float, high: float) -> float | None:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return None
  try:
    number = float(value)
  except (OverflowError, ValueError):
    return None
  return number if math.isfinite(number) and low <= number <= high else None


def _boolean(value: object) -> bool | None:
  return value if type(value) is bool else None


def _interp(speed_mph: float, low: float, high: float) -> float:
  return float(np.interp(speed_mph, [0.0, 35.0, 45.0], [low, low, high]))


class StopLightDetector:
  """One frozen model-tick filter; missing decisive owner input resets it."""

  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.light_filter = FirstOrderFilter(0.0, 0.8, DT_MDL)
    self.lead_clear_filter = FirstOrderFilter(0.0, 0.6, DT_MDL)
    self.light_detected = False
    self.model_detected = False
    self.light_hold_until_s = 0.0
    self.approach_hold_until_s = 0.0
    self.standstill_model_stopped = False
    self.standstill_reason: str | None = None
    self.committed = False
    self.standstill_committed = False
    self.commit_distance_m = 0.0
    self.commit_clear_since_s: float | None = None
    self.last_observed_mono_s: float | None = None
    self.last_model_tick_mono_s: float | None = None

  def invalidate(self) -> None:
    committed, distance, stopped = self.committed, self.commit_distance_m, self.standstill_committed
    self.reset()
    # Missing evidence cannot acquire a stop or prove that a committed one cleared.
    self.committed, self.commit_distance_m, self.standstill_committed = committed, distance, stopped

  def _reset_light(self) -> None:
    # Retain filter alpha across resets; clear its state and latches.
    self.light_filter.x = 0.0
    self.lead_clear_filter.x = 0.0
    self.light_detected = False
    self.model_detected = False
    self.light_hold_until_s = 0.0
    self.approach_hold_until_s = 0.0

  def _commit(self, frame: StopFrame, horizon: float | None, model_stopping: bool | None, acquire: bool, dt: float) -> None:
    if frame.pedal_override is True:
      self.committed = False
      self.standstill_committed = False
      self.commit_clear_since_s = None
      return
    clear_launch = (frame.standstill is True and frame.longitudinal_launch_active is True and
                    frame.longitudinal_stop_active is False and
                    frame.model_should_stop is False and model_stopping is False and frame.forcing_stop is False and
                    horizon is not None and horizon >= FROZEN_RAW_STOP_DISTANCE_M + STOP_MODEL_RELEASE_MARGIN_M)
    if acquire and not self.committed and horizon is not None and not clear_launch:
      self.committed = True
      self.commit_distance_m = horizon
    if self.committed:
      assert frame.speed_mps is not None
      self.commit_distance_m = max(0.0, self.commit_distance_m - frame.speed_mps * dt)
      launch_clear = (self.standstill_committed and frame.standstill is True and frame.speed_mps <= 0.01 and
                      frame.forcing_stop is False and frame.stationary_launch_clear is True)
      clear_distance = max(0.0 if launch_clear else FROZEN_RAW_STOP_DISTANCE_M,
                           self.commit_distance_m) + STOP_MODEL_RELEASE_MARGIN_M
      clear = (horizon is not None and model_stopping is False and frame.model_should_stop is False and
               horizon >= clear_distance)
      if clear:
        if self.commit_clear_since_s is None:
          self.commit_clear_since_s = frame.now_mono_s
        elif frame.now_mono_s - self.commit_clear_since_s >= 0.5:
          self.committed = False
          self.standstill_committed = False
          self.commit_clear_since_s = None
          self._reset_light()
      else:
        self.commit_clear_since_s = None

  def _commit_ford_standstill(self, frame: StopFrame, extent: float) -> None:
    # Commit only an already established, geometry-qualified stopped hold.
    # The model action corroborates that hold; it cannot acquire a light alone.
    if (frame.car_fingerprint in FORD_STOP_NAMES and frame.standstill is True and
        frame.pedal_override is False and frame.model_should_stop is True and
        frame.longitudinal_stop_active is True and
        self.standstill_reason == 'light' and self.standstill_model_stopped and
        extent < FROZEN_RAW_STOP_DISTANCE_M):
      self._commit(frame, extent, True, True, 0.0)

  def _standstill(self, frame: StopFrame, model_stopped: bool) -> tuple[bool | None, str | None]:
    if not frame.standstill:
      self.standstill_reason = None
      self.standstill_model_stopped = False
      return False, None
    if _boolean(frame.pedal_override) is None:
      self.standstill_reason = None
      self.standstill_model_stopped = False
      return None, None
    if frame.pedal_override:
      self.standstill_reason = None
      self.standstill_model_stopped = False
      return False, None
    if not frame.stop_sign_confirmed and _boolean(frame.dashboard_stop_sign) is None:
      self.standstill_reason = None
      self.standstill_model_stopped = False
      return None, None
    if self.committed:
      self.standstill_committed = True
    if frame.stop_sign_confirmed or frame.dashboard_stop_sign:
      self.standstill_reason = 'sign'
    elif self.light_detected or frame.forcing_stop or model_stopped:
      if self.standstill_reason is None:
        self.standstill_reason = 'light'
    elif self.standstill_reason == 'light':
      self.standstill_reason = None
    if self.standstill_reason == 'sign':
      return True, 'sign'
    return bool(self.light_detected or frame.forcing_stop or model_stopped), self.standstill_reason

  def step(self, frame: StopFrame) -> StopObservation:
    unknown = StopObservation(None, None, None, None)
    observed = _finite(frame.observed_mono_s, 0.0, 1e12)
    model_tick = _finite(frame.model_tick_mono_s, 0.0, 1e12) if frame.model_tick_mono_s is not None else observed
    now = _finite(frame.now_mono_s, 0.0, 1e12)
    speed = _finite(frame.speed_mps, 0.0, 80.0)
    horizon = _finite(frame.model_horizon_m, 0.0, 500.0)
    model_time = _finite(frame.model_stop_time_s, 0.0, 10.0)
    forcing_stop = _boolean(frame.forcing_stop)
    lead = frame.lead
    if (
      observed is None
      or model_tick is None
      or now is None
      or not 0.0 <= now - observed <= MAX_SOURCE_AGE_S
      or not observed <= model_tick <= now
      or speed is None
      or model_time is None
      or _boolean(frame.traffic_mode) is None
      or _boolean(frame.stop_sign_confirmed) is None
      or forcing_stop is None
      or _boolean(frame.standstill) is None
      or _boolean(frame.left_blinker) is None
      or _boolean(frame.right_blinker) is None
      or type(frame.car_fingerprint) is not str
      or not frame.car_fingerprint
      or lead is None
      or _boolean(lead.present) is None
    ):
      self.invalidate()
      return unknown
    if self.last_model_tick_mono_s is not None:
      elapsed_ns = round(model_tick * 1e9) - round(self.last_model_tick_mono_s * 1e9)
      if elapsed_ns <= 0:
        return unknown  # Re-reading one model event cannot renew a stop scene.
      if elapsed_ns > round(MAX_FRAME_GAP_S * 1e9):
        self.invalidate()
        return unknown
    if lead.present:
      distance = _finite(lead.distance_m, 0.0, 500.0)
      lead_speed = _finite(lead.speed_mps, -30.0, 100.0)
      lead_prob = _finite(lead.model_probability, 0.0, 1.0)
      if None in (distance, lead_speed, lead_prob) or _boolean(lead.radar) is None or (_boolean(lead.tracked) is None and not self.committed):
        self.invalidate()
        return unknown
    else:
      distance, lead_speed, lead_prob = None, None, None
    assert lead is not None
    if frame.pedal_override is True:
      self.reset()
      return StopObservation(False, False, None, False)
    dt = model_tick - self.last_model_tick_mono_s if self.last_model_tick_mono_s is not None else DT_MDL
    self.last_observed_mono_s = observed
    self.last_model_tick_mono_s = model_tick
    if horizon is None or (self.committed and lead.present and lead.tracked is None):
      self._commit(frame, None, None, False, dt)
      if self.committed:
        self.light_detected = True
        hold, reason = self._standstill(frame, True)
        return StopObservation(True, hold, reason, None)
      extent = _finite(frame.standstill_path_extent_m, 0.0, 500.0)
      if horizon is None and frame.standstill is True and frame.pedal_override is False and extent is not None:
        self._commit_ford_standstill(frame, extent)
        release_distance = FROZEN_RAW_STOP_DISTANCE_M + (STOP_MODEL_RELEASE_MARGIN_M if self.standstill_model_stopped else 0.0)
        self.standstill_model_stopped = extent < release_distance
        hold, reason = self._standstill(frame, self.standstill_model_stopped or forcing_stop)
        return StopObservation(None, hold, reason, None)
      self.invalidate()
      return unknown
    # At standstill the speed-scaled detector has a zero threshold. Its raw
    # 50 m fallback therefore needs the same spatial release band as an approach;
    # otherwise a 49.9/50.1 m horizon alternates the actual CEM hold each tick.
    if frame.stationary_launch_clear is True and frame.standstill and frame.pedal_override is False:
      # The qualified launch proof clears the coarse stationary <50m fallback;
      # the acquired stop still holds until _commit's continuous clear dwell.
      self.standstill_model_stopped = False
    elif frame.standstill and frame.pedal_override is False:
      release_distance = FROZEN_RAW_STOP_DISTANCE_M + (STOP_MODEL_RELEASE_MARGIN_M if self.standstill_model_stopped else 0.0)
      self.standstill_model_stopped = horizon < release_distance
    else:
      self.standstill_model_stopped = False
    self._commit_ford_standstill(frame, horizon)
    model_stopped = (self.standstill_model_stopped if frame.standstill else horizon < FROZEN_RAW_STOP_DISTANCE_M) or forcing_stop
    if frame.stop_sign_confirmed:
      self.light_filter.x = 1.0
      self.light_detected = True
      hold, reason = self._standstill(frame, model_stopped)
      return StopObservation(True, hold, reason, None)
    turn_scene = committed_turn_scene(
      speed_mps=speed, standstill=frame.standstill, left_blinker=frame.left_blinker,
      right_blinker=frame.right_blinker, steering_angle_deg=frame.steering_angle_deg,
      driving_in_curve=frame.driving_in_curve,
    )
    if turn_scene is None:
      self.invalidate()
      return unknown
    if turn_scene or frame.traffic_mode or speed * MPS_TO_MPH > 75.0:
      self._reset_light()
      self._commit(frame, horizon, horizon < FROZEN_RAW_STOP_DISTANCE_M, False, dt)
      self.light_detected = self.committed
      hold, reason = self._standstill(frame, model_stopped)
      return StopObservation(self.light_detected, hold, reason, False)

    speed_mph = speed * MPS_TO_MPH
    self.light_filter.update_alpha(
      min(_interp(speed_mph, 0.35, 0.8), 0.25) if frame.car_fingerprint == 'HYUNDAI_ELANTRA_2021' else _interp(speed_mph, 0.35, 0.8)
    )
    self.lead_clear_filter.update_alpha(_interp(speed_mph, 0.6, 0.35))
    boost = _interp(speed_mph, 1.0, 1.2)
    cap_factor = _interp(speed_mph, 0.0, 1.0)
    adjusted_time = model_time * boost
    if cap_factor > 0.0:
      adjusted_time = min(adjusted_time, 9.0 * cap_factor + model_time * (1.0 - cap_factor))
    threshold_m = max(speed * adjusted_time, 0.0)
    if self.model_detected:
      model_stopping = horizon < threshold_m + 4.0
    else:
      model_stopping = horizon < max(threshold_m - 2.5, 0.0)
    self.model_detected = model_stopping

    relevant = bool(lead.present and distance is not None and distance < threshold_m + 15.0)
    if lead.present:
      assert distance is not None and lead_speed is not None and lead_prob is not None
      trackable_approach = bool(relevant and not lead.radar and lead_prob >= 0.9 and lead_speed < 4.5 and not lead.tracked)
      handoff_speed = lead_speed < 2.0
    else:
      trackable_approach = False
      handoff_speed = False
    if (self.light_detected or self.model_detected or now < self.approach_hold_until_s) and trackable_approach:
      self.approach_hold_until_s = now + 1.0
    approach_latched = now < self.approach_hold_until_s and trackable_approach
    handoff = relevant and not lead.tracked and ((self.light_detected and handoff_speed) or approach_latched)
    if handoff:
      lead_cleared = True
    else:
      self.lead_clear_filter.update(not relevant)
      lead_cleared = self.lead_clear_filter.x >= THRESHOLD
    self.light_filter.update(model_stopping and lead_cleared)
    filtered = bool(self.light_filter.x >= THRESHOLD**2 and lead_cleared)
    active = bool(filtered or handoff or approach_latched)
    strong = horizon < max(threshold_m - 10.0, 0.0)
    if filtered and (model_stopped or strong):
      self.light_hold_until_s = now + 4.0
    hold_context = not relevant or trackable_approach
    self._commit(frame, horizon, model_stopping, filtered or handoff or approach_latched, dt)
    self.light_detected = bool(self.committed or active or (hold_context and now < self.light_hold_until_s))
    hold, reason = self._standstill(frame, model_stopped)
    return StopObservation(self.light_detected, hold, reason, model_stopping)
