"""Frozen StarPilot lead tracking/following detector over validated current inputs.

This module has no messaging or control authority. Its caller supplies freshness for
current radar/model/car events and the selected longitudinal headway owner.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from openpilot.common.realtime import DT_MDL
from openpilot.starpilot.model_geometry import STANDSTILL_RELEASE_SPEED_MPS, normalized_origin, standstill_clear_horizon


STOP_DISTANCE_M = 6.0
FILTER_TIME_S = 0.5
FILTER_THRESHOLD = 1.0 - 1.0 / math.e
CONTINUITY_THRESHOLD = FILTER_THRESHOLD * 0.6
RADARLESS_HOLD_S = 0.45
MAX_FRAME_GAP_S = 2 * DT_MDL
SOURCE_MAX_AGE_S = 0.25


@dataclass(frozen=True)
class LeadObservation:
  raw_present: bool | None
  tracked: bool | None
  following: bool | None
  raw_radar: bool | None


def _number(value: object, low: float, high: float) -> float | None:
  if isinstance(value, bool) or not isinstance(value, (float, int)):
    return None
  try:
    number = float(value)
  except (OverflowError, ValueError):
    return None
  return number if math.isfinite(number) and low <= number <= high else None


def _get(obj: object, field: str) -> object | None:
  try:
    return getattr(obj, field)
  except (AttributeError, TypeError, ValueError, RuntimeError):
    return None


def _smoothstep(value: float, start: float, end: float) -> float:
  factor = min(1.0, max(0.0, (value - start) / max(end - start, 1e-3)))
  return factor * factor * (3.0 - 2.0 * factor)


def _path_at_distance(model: object, distance_m: float) -> tuple[float, float] | None:
  position = _get(model, 'position')
  try:
    xs = normalized_origin(tuple(position.x))
    ys = tuple(position.y)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None
  if len(xs) != 33 or len(ys) != 33:
    return None
  distances = tuple(_number(x, 0.0, 500.0) for x in xs)
  offsets = tuple(_number(y, -500.0, 500.0) for y in ys)
  if any(value is None for value in distances) or any(value is None for value in offsets):
    return None
  clean_xs = tuple(value for value in distances if value is not None)
  clean_ys = tuple(value for value in offsets if value is not None)
  if any(b < a for a, b in zip(clean_xs, clean_xs[1:], strict=False)):
    return None
  return clean_xs[-1], float(np.interp(distance_m, clean_xs, clean_ys))


def _should_track(present: bool, distance: float, model_length: float, speed: float, lead_speed: float, radar: bool) -> bool:
  if not present:
    return False
  tracking_buffer = max(STOP_DISTANCE_M, 4.0)
  model_limit = model_length + tracking_buffer
  if radar:
    return distance < model_limit
  closing = max(0.0, speed - lead_speed)
  gap = 1.75 + min(closing * 0.20, 2.50)
  vision_limit = max(25.0, speed * gap + tracking_buffer)
  return distance < min(model_limit, vision_limit)


def _should_hold_vision(present: bool, distance: float, model_length: float, speed: float, model_prob: float, y_rel: float, path_y: float, radar: bool) -> bool:
  if not present or radar or model_prob < 0.70:
    return False
  lateral = abs(y_rel + path_y)
  if lateral > 1.6:
    return False
  tracking_buffer = max(STOP_DISTANCE_M, 4.0)
  model_limit = model_length + tracking_buffer
  exit_limit = max(25.0, speed * 2.30 + tracking_buffer)
  if distance < min(model_limit, exit_limit):
    return True
  if model_prob < 0.95 or lateral > 1.1:
    return False
  speed_factor = 1.0 - _smoothstep(speed, 20.0, 25.0)
  continuity_gap = 2.30 + 0.55 * speed_factor
  continuity_limit = max(25.0, speed * continuity_gap + tracking_buffer)
  return distance < continuity_limit


def _radarless_follow_window(speed: float, distance: float, lead_speed: float, headway: float, radar: bool, lead_brake: float, model_prob: float) -> bool:
  if radar or headway <= 0.0 or speed < 22.0 or model_prob < 0.70 or lead_brake > 0.35:
    return False
  if abs(speed - lead_speed) > 2.0:
    return False
  actual_gap = distance / max(speed, 1e-3)
  min_gap = max(0.95, headway - 0.35)
  max_gap = headway + 0.90
  return min_gap <= actual_gap <= max_gap


class LeadDetector:
  """One detector per drive. Unknown/dead sources reset temporal consensus."""

  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.filter_value = 0.0
    self.tracked = False
    self.radarless_hold_until_s = 0.0
    self.last_observed_mono_s: float | None = None

  def step(
    self,
    radar_lead: object | None,
    model: object | None,
    *,
    speed_mps: float | None,
    t_follow_s: float | None,
    standstill: bool | None,
    observed_mono_s: float,
    now_mono_s: float,
    radar_fresh: bool,
    model_fresh: bool,
    car_fresh: bool,
    headway_fresh: bool,
  ) -> LeadObservation:
    present = _get(radar_lead, 'present') if radar_fresh else None
    radar = _get(radar_lead, 'radar') if radar_fresh else None
    raw_present = present if type(present) is bool else None
    raw_radar = radar if type(radar) is bool else None
    unknown = LeadObservation(raw_present, None, None, raw_radar)
    speed = _number(speed_mps, 0.0, 80.0)
    headway = _number(t_follow_s, 0.0, 5.0)
    observed = _number(observed_mono_s, 0.0, 1e12)
    now = _number(now_mono_s, 0.0, 1e12)
    distance = _number(_get(radar_lead, 'dRel'), 0.0, 500.0)
    lead_speed = _number(_get(radar_lead, 'vLead'), -30.0, 100.0)
    lead_brake = _number(_get(radar_lead, 'aLeadK'), -20.0, 20.0)
    prob = _number(_get(radar_lead, 'modelProb'), 0.0, 1.0)
    y_rel = _number(_get(radar_lead, 'yRel'), -20.0, 20.0)
    path = _path_at_distance(model, distance) if model_fresh and distance is not None else None
    if path is None and model_fresh and standstill is True and speed is not None and speed <= STANDSTILL_RELEASE_SPEED_MPS:
      # Only retain a stopped track; these coordinates never enter interpolation.
      horizon = standstill_clear_horizon(model)
      try:
        offsets = tuple(_number(value, -50.0, 50.0) for value in model.position.y)
      except (AttributeError, TypeError, ValueError, OverflowError):
        offsets = ()
      if horizon is not None and len(offsets) == 33 and all(value is not None for value in offsets):
        path = horizon, 0.0
    if (
      not radar_fresh
      or not model_fresh
      or not car_fresh
      or not headway_fresh
      or raw_present is None
      or raw_radar is None
      or speed is None
      or headway is None
      or type(standstill) is not bool
      or observed is None
      or now is None
      or not 0.0 <= now - observed <= SOURCE_MAX_AGE_S
      or path is None
      or None in (distance, lead_speed, lead_brake, prob, y_rel)
    ):
      self.reset()
      return unknown
    assert distance is not None and lead_speed is not None and lead_brake is not None and prob is not None and y_rel is not None
    model_length, path_y = path
    if self.last_observed_mono_s is not None:
      interval = observed - self.last_observed_mono_s
      if interval <= 0.0:
        return unknown  # One source event cannot advance the model-tick filter twice.
      if interval > MAX_FRAME_GAP_S:
        self.reset()
        return unknown
    self.last_observed_mono_s = observed
    if not standstill:
      candidate = _should_track(raw_present, distance, model_length, speed, lead_speed, raw_radar)
      continuity = self.tracked or self.filter_value >= CONTINUITY_THRESHOLD
      if not candidate and continuity:
        candidate = _should_hold_vision(raw_present, distance, model_length, speed, prob, y_rel, path_y, raw_radar)
      window = raw_present and _radarless_follow_window(speed, distance, lead_speed, max(headway, 1.45), raw_radar, max(0.0, -lead_brake), prob)
      if window and (candidate or self.tracked or self.filter_value >= CONTINUITY_THRESHOLD):
        self.radarless_hold_until_s = observed + RADARLESS_HOLD_S
      elif raw_radar or not raw_present:
        self.radarless_hold_until_s = 0.0
      if not candidate and window and observed < self.radarless_hold_until_s:
        candidate = True
      alpha = DT_MDL / (FILTER_TIME_S + DT_MDL)
      self.filter_value = (1.0 - alpha) * self.filter_value + alpha * float(candidate)
      self.tracked = self.filter_value >= FILTER_THRESHOLD
    # Retain the filter's brief following state after leadOne.present becomes false.
    following = self.tracked and distance < (headway * 2.0) * speed
    return LeadObservation(raw_present, self.tracked, following, raw_radar)
