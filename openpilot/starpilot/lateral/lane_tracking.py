"""Current-line spacing preferences with bounded acquisition and ownership."""

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class TrackingTarget:
  y: float | None = None
  blocked: bool = False


class SingleLineTracker:
  DEFAULT_SPACING = 1.7
  MAX_SOURCE_GAP = 0.15
  WIDTH_MAX_AGE = 5.0
  WIDTH_MAX_DISTANCE = 75.0
  MAX_PATH_DISAGREEMENT = 0.8
  MIN_CLEARANCE = 1.1

  def __init__(self):
    self.reset()

  def reset(self):
    self.clock = 0.0
    self.travel = 0.0
    self.width = None
    self.width_at = self.width_travel = 0.0
    self.side = None
    self.spacing = None
    self.pending = None
    self.acquired_at = 0.0
    self.last_good_at = None
    self.last_id = None
    self.last_revision = None
    self.frame_at = 0.0
    self.last_near = None
    self.last_slope = None
    self.path = None
    self.blocked = False

  def _clear_line(self):
    self.side = self.spacing = self.pending = self.path = None
    self.last_near = self.last_slope = self.last_good_at = None

  @staticmethod
  def _points(path, *, uncertainty=False):
    x, y = np.asarray(path.x, dtype=float), np.asarray(path.yStd if uncertainty else path.y, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or len(x) < 2 or len(x) != len(y) or not np.isfinite(x).all() or not np.isfinite(y).all() or np.any(np.diff(x) <= 0):
      return None
    return x, y

  @staticmethod
  def _at(points, x):
    return float(np.interp(x, *points)) if points[0][0] <= x <= points[0][-1] else None

  def target(self, model, sample_id: int, speed: float, dt: float, offset: float, *, revision=None) -> TrackingTarget:
    self.clock += dt
    self.travel += speed * dt
    lookahead = min(35.0, max(8.0, speed))
    revision = id(model) if revision is None else revision
    if sample_id == self.last_id and revision == self.last_revision:
      if self.clock - self.frame_at > self.MAX_SOURCE_GAP:
        self._clear_line()
        self.blocked = False
        return TrackingTarget()
      return self._target(lookahead, offset)
    previous_id, self.last_id = self.last_id, sample_id
    self.last_revision, self.frame_at = revision, self.clock
    if previous_id is not None and sample_id < previous_id:
      self.reset()
      self.last_id = sample_id
      return TrackingTarget(blocked=True)
    try:
      probs, stds = model.laneLineProbs, model.laneLineStds
      if len(probs) < 3 or len(stds) < 3 or len(model.laneLines) < 3:
        raise ValueError
      if not all(math.isfinite(float(v)) for v in (probs[1], probs[2], stds[1], stds[2])):
        raise ValueError
      good = [0.6 <= probs[i] <= 1.0 and 0.0 <= stds[i] <= 0.3 for i in (1, 2)]
      lines = [self._points(model.laneLines[i]) if good[i - 1] else None for i in (1, 2)]
      if all(good):
        if any(line is None for line in lines):
          raise ValueError
        valid_lines = [line for line in lines if line is not None]
        samples = (lookahead * 0.25, lookahead * 0.5, lookahead)
        widths = []
        for distance in samples:
          left, right = (self._at(line, distance) for line in lines)
          if left is None or right is None or not 2.6 <= right - left <= 4.8:
            raise ValueError
          slope = sum(float(np.interp(distance, x, np.gradient(y, x))) for x, y in valid_lines) * 0.5
          widths.append((right - left) / math.hypot(1.0, slope))
        if not all(math.isfinite(value) for value in widths):
          raise ValueError
        width = sorted(widths)[1]
        if not 2.6 <= width <= 4.8:
          raise ValueError
        self.width, self.width_at, self.width_travel = width, self.clock, self.travel
        self._clear_line()
        left, right = valid_lines
        shared = (left[0] >= right[0][0]) & (left[0] <= right[0][-1])
        x = left[0][shared]
        if len(x) < 2:
          raise ValueError
        # Both-line corrections must improve the observed center path too.
        self.path = x, (left[1][shared] + np.interp(x, *right)) * 0.5
        self.blocked = False
        return TrackingTarget()
      if not any(good):
        self.path = None
        if self.last_good_at is not None and self.clock - self.last_good_at > self.MAX_SOURCE_GAP:
          self._clear_line()
        return TrackingTarget(blocked=True)
      side = 0 if good[0] else 1
      line = lines[side]
      position = self._points(model.position)
      uncertainty = self._points(model.position, uncertainty=True)
      if line is None or position is None or uncertainty is None:
        raise ValueError
      x, y = line
      slopes = np.gradient(y, x)
      samples = (lookahead * 0.25, lookahead * 0.5, lookahead)
      distances = []
      for distance in samples:
        line_y, path_y, std = self._at(line, distance), self._at(position, distance), self._at(uncertainty, distance)
        if line_y is None or path_y is None or std is None or not 0.0 <= std <= 0.35:
          raise ValueError
        signed = (path_y - line_y) * (1 if side == 0 else -1)
        normal = signed / math.hypot(1.0, float(np.interp(distance, x, slopes)))
        if not 0.5 <= normal <= 2.4:
          raise ValueError
        distances.append(normal)
      near = self._at(line, samples[0])
      slope = float(np.interp(samples[0], x, slopes))
      gap = self.last_good_at is None or self.clock - self.last_good_at > self.MAX_SOURCE_GAP
      jump = (
        self.side == side
        and self.last_near is not None
        and self.last_slope is not None
        and (abs(near - self.last_near) > 0.35 or abs(slope - self.last_slope) > 0.12)
      )
      if self.side != side or gap or jump:
        self._clear_line()
        self.side = side
        self.acquired_at = self.clock
        self.pending = max(self.MIN_CLEARANCE, sorted(distances)[1])
        if (
          not jump
          and self.width is not None
          and self.clock - self.width_at <= self.WIDTH_MAX_AGE
          and self.travel - self.width_travel <= self.WIDTH_MAX_DISTANCE
        ):
          self.spacing = self.width * 0.5
      elif self.spacing is None and sample_id != previous_id:
        # Confirm the line on a second distinct model frame. Its spacing target
        # is already known, so model-path drift must not restart acquisition.
        self.spacing = self.DEFAULT_SPACING
      self.last_good_at, self.last_near, self.last_slope = self.clock, near, slope
      self.blocked = jump
      if self.spacing is None:
        self.path = None
        return TrackingTarget(blocked=jump)
      signed_spacing = self.spacing if side == 0 else -self.spacing
      norm = np.hypot(1.0, slopes)
      target_x, target_y = x - signed_spacing * slopes / norm, y + signed_spacing / norm
      if np.any(np.diff(target_x) <= 0):
        raise ValueError
      self.path = target_x, target_y
      target = self._target(lookahead, offset)
      if target.y is None:
        raise ValueError
      # An observed road edge vetoes a conflicting target. A distant edge is
      # not interpreted as the missing lane line or a lane-width measurement.
      for index in (0, 1):
        if len(model.roadEdges) <= index or len(model.roadEdgeStds) <= index:
          continue
        std = float(model.roadEdgeStds[index])
        if not math.isfinite(std) or std < 0.0:
          raise ValueError
        if std > 0.3:
          continue
        edge = self._points(model.roadEdges[index])
        if edge is None:
          raise ValueError
        for distance in samples:
          edge_y, target_y = self._at(edge, distance), self._at(self.path, distance)
          if edge_y is None or target_y is None:
            raise ValueError
          clearance = (target_y - edge_y) if index == 0 else (edge_y - target_y)
          if clearance < self.MIN_CLEARANCE + max(-0.3, min(0.3, offset)) * (1 if index == 1 else -1):
            raise ValueError
      return target
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
      self._clear_line()
      self.blocked = True
      return TrackingTarget(blocked=True)

  def _target(self, lookahead: float, offset: float) -> TrackingTarget:
    if self.path is None or self.blocked:
      return TrackingTarget(blocked=self.blocked)
    value = self._at(self.path, lookahead)
    return TrackingTarget(None if value is None else value + max(-0.3, min(0.3, offset)))
