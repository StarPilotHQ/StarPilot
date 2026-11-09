"""Compare nominal and corrected motion against current observed road edges."""

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class BodyDimensions:
  width: float
  length: float
  height: float | None = None

  @property
  def valid(self):
    return (
      type(self.width) is float
      and type(self.length) is float
      and math.isfinite(self.width)
      and math.isfinite(self.length)
      and 1.3 <= self.width <= 2.6
      and 2.5 <= self.length <= 7.0
      and (self.height is None or type(self.height) is float and math.isfinite(self.height) and 1.0 <= self.height <= 3.5)
    )


def interface_dimensions(interface, cp) -> BodyDimensions | None:
  provider = getattr(interface, 'get_lateral_body_dimensions', None)
  if not callable(provider):
    return None
  dimensions = provider(cp)
  if dimensions is None:
    return None
  try:
    body = BodyDimensions(*dimensions)
  except (TypeError, ValueError):
    return None
  return body if body.valid else None


def body_dimensions_required(interface, cp) -> bool:
  provider = getattr(interface, 'requires_lateral_body_dimensions', None)
  return bool(provider(cp)) if callable(provider) else False


def current_delay(sm, now_ns: int) -> float | None:
  from openpilot.cereal.services import SERVICE_LIST
  stamp = int(sm.logMonoTime['lateralDelay'])
  if (not sm.all_checks(['lateralDelay']) or not 0 < stamp <= now_ns or
      now_ns - stamp > int(2.5e9 / SERVICE_LIST['lateralDelay'].frequency)):
    return None
  value = float(sm['lateralDelay'].lateralDelay)
  return value if math.isfinite(value) and 0. <= value <= 1. else None


def _center_motion(curvature: float, measured: float, speed: float, delay: float):
  horizon = min(35.0, max(8.0, speed * 1.5))
  ds = horizon / 16.0
  distances = (np.arange(16, dtype=float) + 0.5) * ds
  # Test the measured-to-command handoff rather than substituting the auxiliary
  # model position path for the commanded motion.
  blend = np.clip((distances / speed - delay) / 0.2, 0.0, 1.0)
  ks = measured + (curvature - measured) * blend
  headings = np.cumsum(ks * ds)
  midpoint = headings - ks * ds * 0.5
  x, y = np.cumsum(np.cos(midpoint) * ds), np.cumsum(np.sin(midpoint) * ds)
  return x, y, headings


def _motion(curvature: float, measured: float, speed: float, delay: float, body: BodyDimensions):
  x, y, headings = _center_motion(curvature, measured, speed, delay)
  # Model coordinates originate below the camera. With no measured mounting
  # offset, full length/width on EACH side conservatively cover any camera
  # position inside the body rectangle. These are not assumed axle coordinates.
  longitudinal = np.array([-body.length, -body.length, body.length, body.length])
  lateral = np.array([-body.width, body.width, -body.width, body.width])
  c, s = np.cos(headings)[:, None], np.sin(headings)[:, None]
  return x[:, None] + c * longitudinal - s * lateral, y[:, None] + s * longitudinal + c * lateral


def _interval_body_bounds(curvature: float, measured: float, speed: float, delay: float, body: BodyDimensions):
  """Enclose the piecewise-linear predicted motion in the calibrated frame.

  Camera, motion reference and body points must be inside the supplied body
  box. The forward axis is aligned with the car; initial roll may be unknown.
  Future pitch/bank changes and positions outside observed edges are not modeled.
  """
  assert body.height is not None
  x, y, heading = _center_motion(curvature, measured, speed, delay)
  x0, y0, h0 = np.r_[0., x[:-1]], np.r_[0., y[:-1]], np.r_[0., heading[:-1]]
  low, high = np.minimum(h0, heading), np.maximum(h0, heading)
  sin_abs = np.maximum(np.abs(np.sin(low)), np.abs(np.sin(high)))
  cos_low = np.minimum(np.cos(low), np.cos(high))
  cos_abs = np.maximum(np.abs(np.cos(low)), np.abs(np.cos(high)))
  sin_abs = np.where(np.ceil((low - math.pi / 2) / math.pi) <= np.floor((high - math.pi / 2) / math.pi), 1., sin_abs)
  cos_abs = np.where(np.ceil(low / math.pi) <= np.floor(high / math.pi), 1., cos_abs)
  cos_low = np.where(np.ceil((low - math.pi) / (2 * math.pi)) <= np.floor((high - math.pi) / (2 * math.pi)), -1., cos_low)
  # For t + R - C + Yaw(P - R), these support bounds retain the unknown
  # camera/reference lever arm. They do not treat the camera as an axle.
  scale = (cos_abs + 2. - cos_low) / 2.
  hx = body.length * scale + body.width * sin_abs
  hy = body.width * scale + body.length * sin_abs
  extent = np.hypot(np.maximum(np.abs(y0), np.abs(y)) + hy, body.height)
  return np.minimum(x0, x) - hx, np.maximum(x0, x) + hx, extent


def _interval_edge_margin(bounds, x, y, side):
  lo, hi, extent = bounds
  a = np.maximum(lo[:, None], x[:-1])
  b = np.minimum(hi[:, None], x[1:])
  slope = np.diff(y) / np.diff(x)
  ay = y[:-1] + slope * (a - x[:-1])
  by = y[:-1] + slope * (b - x[:-1])
  separation = (-extent[:, None] - np.maximum(ay, by) if side == 0 else
                np.minimum(ay, by) - extent[:, None]) / np.hypot(1., slope)
  return np.min(np.where(a <= b, separation, np.inf), axis=1)


def _limit_body_envelope(edges, base, correction, measured, speed, delay, body):
  original = _interval_body_bounds(base, measured, speed, delay, body)
  for factor in (1., .75, .5, .25):
    candidate = _interval_body_bounds(base + correction * factor, measured, speed, delay, body)
    accepted = True
    for side, x, y, _, std in edges:
      rows = (original[0] >= x[0]) & (original[1] <= x[-1])
      if np.count_nonzero(rows) < 4 or not np.all((candidate[0][rows] >= x[0]) & (candidate[1][rows] <= x[-1])):
        accepted = False
        break
      margin = _interval_edge_margin(candidate, x, y, side)[rows]
      if not np.isfinite(margin).all() or np.min(margin) < .1 + std:
        accepted = False
        break
    if accepted:
      return correction * factor
  return 0.


def limit_tracking(path, base: float, correction: float, measured: float, speed: float, delay: float, *, recovery_budget: float = 0.) -> float:
  """A line-tracking contribution must improve its predicted spacing objective."""
  if not all(math.isfinite(v) for v in (base, correction, measured, speed, delay)) or speed < 5.0 or not 0.0 <= delay <= 1.0:
    return 0.0
  x, y, _ = _center_motion(base, measured, speed, delay)
  selected = (x >= max(2.0, path[0][0])) & (x <= path[0][-1])
  if np.count_nonzero(selected) < 4:
    return 0.0
  original = float(np.mean((y[selected] - np.interp(x[selected], *path)) ** 2))
  cx, cy, _ = _center_motion(base + correction, measured, speed, delay)
  if not np.all((cx[selected] >= path[0][0]) & (cx[selected] <= path[0][-1])):
    return 0.0
  error = y[selected] - np.interp(x[selected], *path)
  delta = cy[selected] - np.interp(cx[selected], *path) - error
  sensitivity = float(delta @ delta)
  if sensitivity <= 1e-24:
    return 0.0
  # Project onto the spacing objective instead of stepping between four fixed
  # correction fractions. Check the resulting nonlinear motion before use.
  factor = min(1.0, max(0.0, -float(error @ delta) / sensitivity))
  if recovery_budget > 0.:
    near, far = float(error[0]), float(error[-1])
    if near != 0.:
      reserve = max(0., 1. - float(np.max(np.abs(error))) / recovery_budget)
      recovery = min(1., max(0., far / near))
      factor *= 1. - reserve * min(1., abs(near) / .08) * (1. - recovery)
  if factor <= 0.0:
    return 0.0
  if factor == 1.0:
    candidate = float(np.mean((error + delta) ** 2))
    return correction if candidate <= original + 1e-12 else 0.0
  for _ in range(3):
    cx, cy, _ = _center_motion(base + correction * factor, measured, speed, delay)
    if np.all((cx[selected] >= path[0][0]) & (cx[selected] <= path[0][-1])):
      candidate = float(np.mean((cy[selected] - np.interp(cx[selected], *path)) ** 2))
      if candidate <= original + 1e-12:
        return correction * factor
    factor *= 0.5
  return 0.0


def limit_correction(model, base: float, correction: float, measured: float, speed: float, delay: float, body: BodyDimensions) -> float:
  """Check the added contribution against currently observed road edges.

  Height-qualified bounds require absolute margin on covered predicted intervals.
  Planar bounds retain the sampled planar intrusion check. Neither certifies
  actual future motion or the body outside observed longitudinal coverage.

  This guards only the added lane contribution. The original model command and
  native curvature, jerk, authority and driver-override limits remain upstream.
  """
  if not body.valid or not all(math.isfinite(v) for v in (base, correction, measured, speed, delay)):
    return 0.0
  if speed < 5.0 or not 0.0 <= delay <= 1.0 or abs(base) > 0.2 or abs(measured) > 0.2:
    return 0.0
  if correction == 0.0:
    return 0.0
  edges = []
  try:
    for index in (0, 1):
      if len(model.roadEdges) <= index or len(model.roadEdgeStds) <= index:
        continue
      std = float(model.roadEdgeStds[index])
      if not math.isfinite(std) or std < 0.0:
        return 0.0
      if std > 0.3:
        continue
      edge = model.roadEdges[index]
      x, y = np.asarray(edge.x, dtype=float), np.asarray(edge.y, dtype=float)
      if x.ndim != 1 or y.ndim != 1 or len(x) < 2 or len(x) != len(y) or not np.isfinite(x).all() or not np.isfinite(y).all() or np.any(np.diff(x) <= 0):
        return 0.0
      with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        slope = np.gradient(y, x)
      if not np.isfinite(slope).all():
        return 0.0
      edges.append((index, x, y, slope, std))
  except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
    return 0.0
  if not edges:
    return correction
  if body.height is not None:
    return _limit_body_envelope(edges, base, correction, measured, speed, delay, body)
  original_x, original_y = _motion(base, measured, speed, delay, body)
  for factor in (1.0, 0.75, 0.5, 0.25):
    candidate_x, candidate_y = _motion(base + correction * factor, measured, speed, delay, body)
    accepted = True
    for side, x, y, slope, std in edges:
      # Use the same future body samples in both arms; never extrapolate an edge
      # behind the camera or beyond the observed geometry.
      rows = np.all((original_x >= x[0]) & (original_x <= x[-1]), axis=1)
      if np.count_nonzero(rows) < 4 or not np.all((candidate_x[rows] >= x[0]) & (candidate_x[rows] <= x[-1])):
        accepted = False
        break
      ox, oy = original_x[rows].ravel(), original_y[rows].ravel()
      cx, cy = candidate_x[rows].ravel(), candidate_y[rows].ravel()
      sign = 1.0 if side == 0 else -1.0
      old = float(np.min(sign * (oy - np.interp(ox, x, y)) / np.hypot(1.0, np.interp(ox, x, slope))))
      new = float(np.min(sign * (cy - np.interp(cx, x, y)) / np.hypot(1.0, np.interp(cx, x, slope))))
      margin = 0.1 + std
      if min(new, margin) < min(old, margin) - 1e-6:
        accepted = False
        break
    if accepted:
      return correction * factor
  return 0.0
