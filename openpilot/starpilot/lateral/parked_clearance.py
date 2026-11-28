"""Left shift request beside stationary radar tracks on the right.

The caller owns freshness, lateral authority and final curvature limiting.
This module neither reads settings nor sends commands.
"""

from bisect import bisect_left
from dataclasses import dataclass
import math

MIN_SPEED = 3.0
MAX_SPEED = 12.5
STATIONARY_SPEED = 1.5
MIN_DISTANCE = 4.0
MAX_DISTANCE = 35.0
MIN_LATERAL = 1.5
MAX_LATERAL = 3.8
TRACK_LATERAL = 6.0
CONFIRM_SECONDS = 0.4
TRACK_GAP_SECONDS = 0.2
HOLD_SECONDS = 0.6
LATERAL_TAU = 0.15
EGO_HALF_WIDTH = 0.9
OBJECT_HALF_WIDTH = 0.9
MAX_GAP = 2.0
MAX_SHIFT = 0.3
MIN_LIMIT = 0.1
MAX_LIMIT = 0.5
RISE_RATE = 0.4
FALL_RATE = 0.15
MIN_LINE_SPACING = 0.9
ONCOMING_LINE_SPACING = 1.1
MIN_EDGE_SPACING = 1.5
LINE_PROB = 0.5
LINE_STD = 0.3
NO_LINE_PROB = 0.2
UNCERTAIN_LINE_MARGIN = 0.5
MAX_EDGE_STD = 1.5
ONCOMING_SPEED = -2.0
ONCOMING_LATERAL = 4.5
ONCOMING_DISTANCE = 120.0
ROADSIDE_LATERAL = 2.0
MAX_ROADSIDE_EDGE_STD = 1.0


@dataclass(frozen=True)
class RadarPoint:
  identifier: int
  distance: float
  lateral: float
  speed: float


@dataclass
class _Track:
  age: float
  unseen: float
  lateral: float


def path_lateral(path_x, path_y, distance: float, y_rel: float) -> float | None:
  """Metres right of the planned path for a radar point with left-positive yRel."""
  if len(path_x) < 2 or len(path_x) != len(path_y) or not path_x[0] <= distance <= path_x[-1]:
    return None
  index = max(1, bisect_left(path_x, distance))
  span = path_x[index] - path_x[index - 1]
  if span <= 0:
    return None
  ratio = (distance - path_x[index - 1]) / span
  value = -y_rel - (path_y[index - 1] + ratio * (path_y[index] - path_y[index - 1]))
  return value if math.isfinite(value) else None


def on_road(lateral: float, edge: float | None, edge_std: float) -> bool:
  """Far targets at or beyond a confident right road edge are roadside objects."""
  if lateral <= ROADSIDE_LATERAL:
    return True
  if edge is None or not all(math.isfinite(v) for v in (lateral, edge, edge_std)):
    return False
  return edge_std > MAX_ROADSIDE_EDGE_STD or lateral < edge


def oncoming_ahead(points) -> bool:
  return any(math.isfinite(p.distance) and math.isfinite(p.lateral) and math.isfinite(p.speed) and p.speed < ONCOMING_SPEED and
             -ONCOMING_LATERAL < p.lateral < 0 and 0 < p.distance < ONCOMING_DISTANCE for p in points)


def left_room(line_spacing: float, line_prob: float, line_std: float, edge_spacing: float, edge_std: float, *, oncoming: bool = False) -> float:
  """Metres the path may move left before the left line, or the left edge when no line is seen.

  With oncoming traffic only a confident line grants room, at a wider spacing.
  """
  if not all(math.isfinite(v) for v in (line_spacing, line_prob, line_std, edge_spacing, edge_std)) or line_std < 0 or edge_std < 0:
    return 0.0
  edge = edge_spacing - MIN_EDGE_SPACING - 2.0 * edge_std if edge_std <= MAX_EDGE_STD else 0.0
  confident = line_prob >= LINE_PROB and line_std <= LINE_STD
  if oncoming:
    return max(0.0, line_spacing - ONCOMING_LINE_SPACING) if confident else 0.0
  if line_prob < NO_LINE_PROB:
    return max(0.0, edge)
  line = line_spacing - MIN_LINE_SPACING
  if not confident:
    line -= min(line_std, UNCERTAIN_LINE_MARGIN)
    return max(0.0, min(line, edge))
  return max(0.0, line)


class ParkedClearance:
  def __init__(self) -> None:
    self.tracks: dict[int, _Track] = {}
    self.shift = 0.0
    self.demand = 0.0
    self.hold = 0.0
    self.reason = 'inactive'

  def reset(self) -> None:
    self.tracks.clear()
    self.shift = 0.0
    self.demand = 0.0
    self.hold = 0.0
    self.reason = 'inactive'

  def update(self, points, speed: float, dt: float, gap: float, room: float, active: bool, limit: float = MAX_SHIFT, watch: bool = False) -> float:
    values = (speed, dt, gap, room, limit)
    if not active or not all(type(v) in (int, float) and math.isfinite(v) for v in values) or dt <= 0 or gap <= 0 or limit <= 0:
      self.reset()
      return 0.0
    gap = min(gap, MAX_GAP)
    limit = min(limit, MAX_LIMIT)
    seen = set()
    for point in points:
      if not all(math.isfinite(v) for v in (point.distance, point.lateral, point.speed)):
        continue
      if (abs(point.speed) > STATIONARY_SPEED or not MIN_DISTANCE <= point.distance <= MAX_DISTANCE or
          not 0.0 <= point.lateral <= TRACK_LATERAL):
        continue
      seen.add(point.identifier)
      track = self.tracks.get(point.identifier)
      if track is None:
        self.tracks[point.identifier] = _Track(0.0, 0.0, point.lateral)
        continue
      track.unseen = 0.0
      track.lateral += (point.lateral - track.lateral) * (1.0 - math.exp(-dt / LATERAL_TAU))
      track.age = track.age + dt if MIN_LATERAL <= track.lateral <= MAX_LATERAL else 0.0
    for identifier in [i for i in self.tracks if i not in seen]:
      self.tracks[identifier].unseen += dt
      if self.tracks[identifier].unseen > TRACK_GAP_SECONDS:
        del self.tracks[identifier]
    if watch:
      self.shift, self.demand, self.hold, self.reason = 0.0, 0.0, 0.0, 'inactive'
      return 0.0
    confirmed = [t.lateral for i, t in self.tracks.items() if i in seen and t.age >= CONFIRM_SECONDS]
    if not MIN_SPEED <= speed <= MAX_SPEED:
      self.demand, self.hold, self.reason = 0.0, 0.0, 'speed'
    elif confirmed:
      self.demand = max(0.0, gap - (min(confirmed) - EGO_HALF_WIDTH - OBJECT_HALF_WIDTH))
      self.hold = HOLD_SECONDS
      self.reason = 'parked'
    elif self.hold > 0:
      self.hold = max(0.0, self.hold - dt)
      self.reason = 'hold'
    else:
      self.demand, self.reason = 0.0, 'clear'
    target = min(self.demand, limit, max(0.0, room))
    if target < self.demand and self.reason == 'parked' and room < min(self.demand, limit):
      self.reason = 'left_limited'
    step = target - self.shift
    self.shift += min(step, RISE_RATE * dt) if step > 0 else max(step, -FALL_RATE * dt)
    return self.shift
