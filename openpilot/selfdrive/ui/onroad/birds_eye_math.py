"""Pure geometry for the birds-eye (3D chase view) renderer. No GL, no messaging.

Car frame follows modelV2 (device frame): x forward, y right, meters; heights are z up from the road. radarState
yRel is left-positive and must be negated before it is mapped here.

The view is a pinhole camera above and behind the car, looking forward and down by `pitch`, with no roll or yaw.
Its place follows from what the view must show: the ground at x_min lands on the bottom edge of the rect and the
ground at x_max on the top edge, so with the two edge rays `pitch + fov / 2` and `pitch - fov / 2` below the horizon

  height = (x_max - x_min) / (cot(pitch - fov / 2) - cot(pitch + fov / 2))
  x      = x_min - height * cot(pitch + fov / 2)

ViewParams.matrix maps (x, y, z, 1) to (X, Y, Z, W) with the pixel at (X / W, Y / W). The renderer hands the same
matrix to raylib as its modelview, so road geometry stays in meters and the GPU projects it; car_to_widget is the
CPU form for the few points that need pixels (car bodies, labels).
"""
from dataclasses import dataclass, replace
import math

import numpy as np


@dataclass(frozen=True)
class BirdsEyeConfig:
  """What the view shows and how steeply it looks down.

  - x_min -6.9: the ground 6.9 m behind the camera is the bottom edge, so the whole ego car (rear bumper at
    -3.1 m) and its shadow sit above it.
  - x_max 102: the highway preset, not a ceiling. The renderer zooms this config (zoomed_config) between
    ZOOM_NEAR_X_MAX and far_x_max (ZOOM_FAR_X_MAX, 120 m) to keep a radar lead on screen, and its sample_grid
    spans that widest zoom.
  - pitch 32, fov 42: the edge rays are 53 and 11 degrees below the horizon, so the horizon is never in view and
    the far end of the road is drawn about a quarter as wide as the road beside the car. At the highway preset the
    camera is 25 m up and 26 m behind; at the city preset 12 m up and 16 m behind.
  - These replace pitch 36 with x_min -7 and presets of 36 / 80 m: the camera tilted up 4 degrees and lowered so
    that the ego car keeps its size and place on screen (within 0.1 % of the view height) at every speed while the
    top edge reaches 27 % further.
  """
  x_min: float = -6.9          # meters of ground behind the device shown at the bottom edge
  x_max: float = 102.0         # meters ahead at the top edge
  pitch: float = 32.0          # degrees the camera looks below the horizon
  fov: float = 42.0            # vertical field of view, degrees
  sample_step: float = 1.5     # meters between resampled line points near the car


DEFAULT_CONFIG = BirdsEyeConfig()

# Speed-based zoom. The view shows the next few seconds of travel: 45 m ahead at city speed (<= 10 m/s) widening
# linearly to the default 102 m at highway speed (>= 30 m/s). A radar leadOne always stays on screen: when its front
# bumper plus ZOOM_LEAD_BUFFER is beyond the speed preset the view widens to it (up to ZOOM_FAR_X_MAX); a nearer
# lead never tightens the view below the preset.
ZOOM_NEAR_X_MAX = 45.0
ZOOM_FAR_X_MAX = 120.0  # m, the widest zoom: radar and model leads are reported out to ~100+ m
ZOOM_SLOW_SPEED = 10.0
ZOOM_FAST_SPEED = 30.0
ZOOM_TAU = 1.0    # s, first-order filter time constant
ZOOM_MAX_RATE = 12.0  # m/s, the most x_max may move per second: a lead appearing 20 m ahead on the highway takes ~3 s
ZOOM_STEP = 0.5   # m, x_max quantum of ZoomFilter.x_max (the camera itself follows the unquantized ZoomFilter.value)
ZOOM_LEAD_BUFFER = 6.0  # m shown beyond a lead's front bumper
MIN_FAR_RAY = math.radians(4.0)  # the top edge ray stays at least this far below the horizon
NEAR_DEPTH = 0.5  # m: points nearer the camera plane than this (or behind it) are projected as if at it


def zoom_x_max(v_ego: float, cfg: BirdsEyeConfig = DEFAULT_CONFIG) -> float:
  """Unfiltered top-edge distance for an ego speed: ZOOM_NEAR_X_MAX up to ZOOM_SLOW_SPEED, cfg.x_max from ZOOM_FAST_SPEED."""
  if not math.isfinite(v_ego):
    return cfg.x_max
  near = min(ZOOM_NEAR_X_MAX, cfg.x_max)
  t = (v_ego - ZOOM_SLOW_SPEED) / (ZOOM_FAST_SPEED - ZOOM_SLOW_SPEED)
  return near + (cfg.x_max - near) * min(1.0, max(0.0, t))


def far_x_max(cfg: BirdsEyeConfig = DEFAULT_CONFIG) -> float:
  """The widest zoom: ZOOM_FAR_X_MAX (or cfg.x_max if that is further)."""
  return max(cfg.x_max, ZOOM_FAR_X_MAX)


def lead_zoom_x_max(v_ego: float, lead_front: float | None, cfg: BirdsEyeConfig = DEFAULT_CONFIG) -> float:
  """zoom_x_max, widened to lead_front + ZOOM_LEAD_BUFFER (at most far_x_max) when a lead is given beyond it.

  A lead never tightens the view below the speed preset, so it widens it only.
  lead_front: the lead's front bumper ahead of the camera (m), dRel + ego front + lead length; None without a lead."""
  preset = zoom_x_max(v_ego, cfg)
  if lead_front is None or not math.isfinite(lead_front):
    return preset
  return min(far_x_max(cfg), max(preset, lead_front + ZOOM_LEAD_BUFFER))


def zoomed_config(cfg: BirdsEyeConfig, x_max: float) -> BirdsEyeConfig:
  """cfg showing x_max ahead: same x_min, pitch and fov, so the camera rises and backs away as the view widens."""
  if x_max == cfg.x_max:
    return cfg
  return replace(cfg, x_max=x_max)


class ZoomFilter:
  """First-order filtered, rate-limited x_max for the speed-based (and lead-widened) zoom.

  The filter never moves faster than max_rate (m/s) however far the target jumps (a lead appearing or vanishing),
  so the view always glides. `value` is the filtered distance the camera follows; `x_max` is that distance
  quantized to `step`, moving only once the filter is more than 3/4 of a step away from it, for callers that key
  work on the zoom (speed noise near a step boundary does not toggle it back and forth)."""
  def __init__(self, cfg: BirdsEyeConfig = DEFAULT_CONFIG, tau: float = ZOOM_TAU, step: float = ZOOM_STEP,
               max_rate: float = ZOOM_MAX_RATE):
    self.cfg, self.tau, self.step, self.max_rate = cfg, tau, step, max_rate
    self.value: float | None = None
    self.x_max = cfg.x_max

  def update(self, v_ego: float | None, dt: float, lead_front: float | None = None) -> float:
    if v_ego is None or not math.isfinite(v_ego):
      return self.x_max
    target = lead_zoom_x_max(v_ego, lead_front, self.cfg)
    if self.value is None:
      self.value = target
    else:
      dt = max(0.0, min(dt, 0.5))
      alpha = 1.0 - math.exp(-dt / self.tau)
      delta = (target - self.value) * alpha
      self.value += max(-self.max_rate * dt, min(self.max_rate * dt, delta))
    if abs(self.value - self.x_max) >= 0.75 * self.step:
      self.x_max = min(far_x_max(self.cfg), round(self.value / self.step) * self.step)
    return self.x_max


@dataclass(frozen=True, eq=False)
class ViewParams:
  """The camera for one rect and config. `matrix` is 4x4: (x, y, z, 1) -> (X, Y, Z, W), pixel = (X / W, Y / W)."""
  matrix: np.ndarray
  position: tuple[float, float, float]  # the camera in the car frame: (x, 0, height)
  focal: float                          # pixels
  cos_pitch: float
  sin_pitch: float
  x_min: float
  x_max: float


def view_params(rect, cfg: BirdsEyeConfig = DEFAULT_CONFIG) -> ViewParams:
  """The camera that puts the ground at cfg.x_min on the rect's bottom edge and at cfg.x_max on its top edge."""
  width, height = float(rect.width), float(rect.height)
  half = math.radians(min(max(cfg.fov, 5.0), 120.0)) / 2.0
  pitch = max(math.radians(cfg.pitch), half + MIN_FAR_RAY)
  near, far = pitch + half, pitch - half
  span = max(1e-3, cfg.x_max - cfg.x_min)
  cam_h = span / (1.0 / math.tan(far) - 1.0 / math.tan(near))
  cam_x = cfg.x_min - cam_h / math.tan(near)
  focal = max(1.0, height) / 2.0 / math.tan(half)
  c, s = math.cos(pitch), math.sin(pitch)
  depth = np.array([c, 0.0, -s, -cam_x * c + cam_h * s])  # distance along the optical axis
  up = np.array([s, 0.0, c, -cam_x * s - cam_h * c])       # offset along the camera's up axis
  px, py = float(rect.x) + width / 2.0, float(rect.y) + height / 2.0
  matrix = np.stack((px * depth + focal * np.array([0.0, 1.0, 0.0, 0.0]), py * depth - focal * up, -0.5 * depth, depth))
  return ViewParams(matrix, (cam_x, 0.0, cam_h), focal, c, s, cfg.x_min, cfg.x_max)


def ground_matrix(view: ViewParams, ground_z: float = -1.0) -> np.ndarray:
  """view.matrix for vertices whose z is `ground_z` on the road: raylib's 2D shapes emit their vertices at its
  batch depth (-1 at the start of a batch), so drawn through this matrix they lie on the ground plane."""
  shift = np.eye(4)
  shift[2, 3] = -ground_z
  return view.matrix @ shift


def depth_at(x, view: ViewParams):
  """Distance along the optical axis (m) of the ground point at forward distance x."""
  m = view.matrix[3]
  return np.maximum(m[0] * np.asarray(x, dtype=np.float64) + m[3], NEAR_DEPTH)


def car_to_widget(points, rect, cfg: BirdsEyeConfig = DEFAULT_CONFIG, view: ViewParams | None = None) -> np.ndarray:
  """Map (N, 2) ground points (x fwd, y right) or (N, 3) points (z up, meters) to (N, 2) widget pixels."""
  view = view_params(rect, cfg) if view is None else view
  columns = np.shape(points)[-1] if np.ndim(points) > 1 else 2
  pts = np.asarray(points, dtype=np.float64).reshape(-1, columns)
  m = view.matrix
  homogeneous = pts[:, :2] @ m[(0, 1, 3), :2].T + m[(0, 1, 3), 3]
  if columns > 2:
    homogeneous += pts[:, 2:3] * m[(0, 1, 3), 2]
  w = np.maximum(homogeneous[:, 2], NEAR_DEPTH)
  out = np.empty((pts.shape[0], 2), dtype=np.float32)
  out[:, 0] = homogeneous[:, 0] / w
  out[:, 1] = homogeneous[:, 1] / w
  return out


def scale_at(x, view: ViewParams) -> np.ndarray:
  """Sideways pixels per meter on the ground at forward distance x."""
  return view.focal / depth_at(x, view)


def meters_per_pixel(x, view: ViewParams) -> np.ndarray:
  """Sideways meters one pixel covers on the ground at forward distance x."""
  return depth_at(x, view) / view.focal


def depth_scale_at(x, view: ViewParams) -> np.ndarray:
  """Forward pixels per meter on the ground at forward distance x (derivative of the pixel row)."""
  return view.focal * view.position[2] / depth_at(x, view) ** 2


def slope_profile(grid: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
  """(distances, dy/dx) of a resampled line's finite points, or None with fewer than two."""
  ok = np.isfinite(ys)
  if np.count_nonzero(ok) < 2:
    return None
  gx, gy = grid[ok], ys[ok]
  return gx, np.gradient(gy, gx)


def mean_heading(profiles, x: float) -> float | None:
  """atan of the mean slope at x over the slope_profiles that cover x; None when none does."""
  slopes = [float(np.interp(x, gx, dy)) for gx, dy in profiles if gx[0] <= x <= gx[-1]]
  return math.atan(sum(slopes) / len(slopes)) if slopes else None


def widget_y_for_distance(x: float, rect, cfg: BirdsEyeConfig = DEFAULT_CONFIG) -> float:
  return float(car_to_widget(np.array([[x, 0.0]]), rect, cfg)[0, 1])


def sample_grid(cfg: BirdsEyeConfig = DEFAULT_CONFIG) -> np.ndarray:
  """Forward distances to sample: sample_step near the car, coarser far away where perspective shrinks it."""
  # Start a little behind the view so lanes run off the bottom edge instead of stopping short of it.
  near = np.arange(cfg.x_min - 4.0, min(15.0, cfg.x_max), cfg.sample_step)
  mid = np.arange(near[-1] + cfg.sample_step if near.size else cfg.x_min, min(40.0, cfg.x_max), cfg.sample_step * 2)
  start = mid[-1] + cfg.sample_step * 2 if mid.size else (near[-1] + cfg.sample_step if near.size else cfg.x_min)
  far = np.arange(start, cfg.x_max, cfg.sample_step * 3)
  return np.concatenate((near, mid, far, [cfg.x_max]))


def resample_line(xs, ys, grid: np.ndarray, *, extend_back: bool = True) -> np.ndarray:
  """Lateral offset of a model line at each grid distance; NaN beyond the line's data.

  Behind its first point the line keeps its first lateral offset (lanes continue under the car)."""
  xs = np.asarray(xs, dtype=np.float64)
  ys = np.asarray(ys, dtype=np.float64)
  n = min(xs.size, ys.size)
  out = np.full(grid.shape, np.nan)
  if n < 2:
    return out
  xs, ys = xs[:n], ys[:n]
  finite = np.isfinite(xs) & np.isfinite(ys)
  xs, ys = xs[finite], ys[finite]
  if xs.size < 2:
    return out
  xs = np.maximum.accumulate(xs)  # tolerate a non-monotonic tail (stopped car); interp needs increasing xp
  keep = np.concatenate(([True], np.diff(xs) > 1e-4))
  xs, ys = xs[keep], ys[keep]
  if xs.size < 2:
    return out
  inside = (grid >= xs[0]) & (grid <= xs[-1])
  out[inside] = np.interp(grid[inside], xs, ys)
  if extend_back:
    out[grid < xs[0]] = ys[0]
  return out


def ribbon(centre: np.ndarray, half_widths) -> np.ndarray:
  """Screen-space ribbon polygon [L0..Ln-1, Rn-1..R0] around an (N, 2) pixel centreline."""
  centre = np.asarray(centre, dtype=np.float64)
  if centre.shape[0] < 2:
    return np.empty((0, 2), dtype=np.float32)
  tangent = np.gradient(centre, axis=0)
  norm = np.linalg.norm(tangent, axis=1, keepdims=True)
  norm[norm < 1e-6] = 1.0
  tangent /= norm
  # For a centreline running up the screen this normal points screen-left, so the first chain is the
  # left one, which is the winding draw_polygon's triangle strip needs to survive back-face culling.
  normal = np.stack((tangent[:, 1], -tangent[:, 0]), axis=1)
  half = np.broadcast_to(np.asarray(half_widths, dtype=np.float64), (centre.shape[0],))[:, None]
  left, right = centre + normal * half, centre - normal * half
  return np.vstack((left, right[::-1])).astype(np.float32)


def valid_runs(mask: np.ndarray) -> list[tuple[int, int]]:
  """[start, end) index runs where mask is true."""
  edges = np.flatnonzero(np.diff(np.concatenate(([0], np.asarray(mask, dtype=np.int8).ravel(), [0]))))
  return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def ribbon_sides(centre: np.ndarray, half_widths, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
  """Left and right edges of (..., N, 2) pixel centrelines, one ribbon per leading index.

  Batched form of ribbon(): each row's tangent matches np.gradient over the valid run the row belongs
  to (central inside a run, one-sided at its ends), so lines split by gaps need no per-run loop."""
  c = np.asarray(centre, dtype=np.float64)
  ok = np.asarray(valid, dtype=bool)
  fwd = np.zeros_like(c)
  fwd[..., :-1, :] = c[..., 1:, :] - c[..., :-1, :]
  has_fwd = np.zeros_like(ok)
  has_fwd[..., :-1] = ok[..., 1:] & ok[..., :-1]
  bwd = np.zeros_like(c)
  bwd[..., 1:, :] = fwd[..., :-1, :]
  has_bwd = np.zeros_like(ok)
  has_bwd[..., 1:] = has_fwd[..., :-1]
  tangent = np.where((has_fwd & has_bwd)[..., None], (fwd + bwd) / 2, np.where(has_fwd[..., None], fwd, bwd))
  norm = np.sqrt(tangent[..., 0] ** 2 + tangent[..., 1] ** 2)
  norm[norm < 1e-6] = 1.0
  half = np.broadcast_to(np.asarray(half_widths, dtype=np.float64), ok.shape) / norm
  normal = np.stack((tangent[..., 1] * half, -tangent[..., 0] * half), axis=-1)
  return c + normal, c - normal


def hls_to_rgb(hue, light, sat) -> np.ndarray:
  """Vectorized colorsys.hls_to_rgb: (..., 3) RGB in 0..1."""
  hue, light, sat = np.broadcast_arrays(*(np.asarray(v, dtype=np.float64) for v in (hue, light, sat)))
  m2 = np.where(light <= 0.5, light * (1.0 + sat), light + sat - light * sat)
  m1 = 2.0 * light - m2

  def channel(h):
    h = np.mod(h, 1.0)
    rise = m1 + (m2 - m1) * h * 6.0
    fall = m1 + (m2 - m1) * (2.0 / 3.0 - h) * 6.0
    return np.select([h < 1 / 6, h < 0.5, h < 2 / 3], [rise, m2, fall], m1)

  rgb = np.stack((channel(hue + 1 / 3), channel(hue), channel(hue - 1 / 3)), axis=-1)
  return np.where((sat == 0.0)[..., None], light[..., None], rgb)


def heading_at(grid: np.ndarray, ys: np.ndarray, x: float) -> float:
  """Path heading (radians, right-positive) at forward distance x."""
  ok = np.isfinite(ys)
  if np.count_nonzero(ok) < 2:
    return 0.0
  gx, gy = grid[ok], ys[ok]
  dy = np.gradient(gy, gx)
  return float(math.atan(np.interp(x, gx, dy)))


def lateral_at(grid: np.ndarray, ys: np.ndarray, x: float, default: float = 0.0) -> float:
  ok = np.isfinite(ys)
  if np.count_nonzero(ok) < 2 or x < grid[ok][0] or x > grid[ok][-1]:
    return default
  return float(np.interp(x, grid[ok], ys[ok]))


def car_outline(x: float, y: float, heading: float = 0.0, length: float = 4.6, width: float = 1.9,
                chamfer: float = 0.45) -> np.ndarray:
  """Convex octagonal car footprint in car-frame meters, centered at (x, y), counter-clockwise on screen."""
  hl, hw, c = length / 2.0, width / 2.0, min(chamfer, width / 2.0 - 1e-3)
  # (forward, right) corners going clockwise on the ground when viewed from above = CCW on screen (y down).
  local = np.array([(hl, -hw + c), (hl - c, -hw), (-hl + c, -hw), (-hl, -hw + c),
                    (-hl, hw - c), (-hl + c, hw), (hl - c, hw), (hl, hw - c)], dtype=np.float64)
  cos_h, sin_h = math.cos(heading), math.sin(heading)
  rot = np.array([[cos_h, -sin_h], [sin_h, cos_h]])
  return local @ rot.T + np.array([x, y])


def rotate_offsets(local: np.ndarray, x: float, y: float, heading: float) -> np.ndarray:
  cos_h, sin_h = math.cos(heading), math.sin(heading)
  rot = np.array([[cos_h, -sin_h], [sin_h, cos_h]])
  return np.asarray(local, dtype=np.float64) @ rot.T + np.array([x, y])


def time_to_collision(d_rel: float, v_rel: float) -> float:
  """Seconds until contact at the current closing speed; inf when not closing."""
  if not math.isfinite(d_rel) or not math.isfinite(v_rel) or v_rel >= -0.1:
    return math.inf
  return max(0.0, d_rel) / -v_rel


NEUTRAL_LEAD = (214, 219, 228)
ALERT_LEAD = (240, 64, 56)
CAUTION_LEAD = (246, 184, 64)


def lead_color(d_rel: float, v_rel: float) -> tuple[int, int, int]:
  """Neutral grey above 6 s TTC, amber around 3 s, red at 1.5 s or less (or closer than 4 m)."""
  ttc = time_to_collision(d_rel, v_rel)
  if d_rel < 4.0:
    ttc = min(ttc, 1.5)
  if ttc >= 6.0:
    return NEUTRAL_LEAD
  if ttc >= 3.0:
    t = (6.0 - ttc) / 3.0
    a, b = NEUTRAL_LEAD, CAUTION_LEAD
  else:
    t = min(1.0, (3.0 - max(ttc, 1.5)) / 1.5)
    a, b = CAUTION_LEAD, ALERT_LEAD
  return tuple(int(round(p + (q - p) * t)) for p, q in zip(a, b, strict=True))  # type: ignore[return-value]


YARDS_PER_METER = 1.09361


def distance_label(d_rel: float, metric: bool = True) -> str:
  """The radar gap (dRel, ego front bumper to lead rear bumper) as printed, with no offset: metres or yards."""
  if metric:
    return f"{max(0.0, d_rel):.0f} m"
  return f"{max(0.0, d_rel) * YARDS_PER_METER:.0f} yd"


def ego_wheel_angle(steering_angle_deg: float, steer_ratio: float = 15.0, exaggerate: float = 2.0) -> float:
  """Front-wheel angle in radians for the ego glyph (right-positive, matching car-frame y)."""
  if not math.isfinite(steering_angle_deg):
    return 0.0
  # steeringAngleDeg is left-positive; the glyph's frame is right-positive.
  return -math.radians(max(-35.0, min(35.0, steering_angle_deg / steer_ratio * exaggerate)))


def smooth(previous: np.ndarray | None, current: np.ndarray, alpha: float) -> np.ndarray:
  """Exponential blend toward current; NaNs and shape changes take the new value directly."""
  if previous is None or previous.shape != current.shape:
    return current.copy()
  out = previous + (current - previous) * alpha
  fresh = ~np.isfinite(previous) | ~np.isfinite(current)
  out[fresh] = current[fresh]
  return out


# ---- strip drawing ------------------------------------------------------------------------------

def strip_points(left: np.ndarray, right: np.ndarray) -> np.ndarray:
  """(N, 2) left and right ribbon sides -> contiguous (2N, 2) float32 [L0, R0, L1, R1, ..]: a triangle strip."""
  left, right = np.asarray(left), np.asarray(right)
  out = np.empty((2 * left.shape[0], 2), dtype=np.float32)
  out[0::2] = left
  out[1::2] = right
  return out


def ring_strip(poly: np.ndarray, thickness: float) -> np.ndarray:
  """A closed outline of a convex (or simple) polygon as one triangle strip: (2(n+1), 2) float32.

  The stroke is centred on the edges and mitred at the corners, so every edge is `thickness` wide."""
  p = np.asarray(poly, dtype=np.float64)
  e_in = p - np.roll(p, 1, axis=0)
  e_out = np.roll(p, -1, axis=0) - p
  for e in (e_in, e_out):
    n = np.hypot(e[:, 0], e[:, 1])
    n[n < 1e-9] = 1.0
    e /= n[:, None]
  n_in = np.stack((e_in[:, 1], -e_in[:, 0]), axis=1)
  n_out = np.stack((e_out[:, 1], -e_out[:, 0]), axis=1)
  denom = np.maximum(1.0 + np.sum(n_in * n_out, axis=1), 0.25)  # caps the miter at sharp corners
  offset = (n_in + n_out) / denom[:, None] * (thickness / 2.0)
  out = strip_points(p + offset, p - offset)
  return np.concatenate((out, out[:2]))


# Glass panels (the lead label, the HUD's bar tracks, the music card, the overlay card's rim): a smoked tint under
# a bright hairline rim, both flat colours. The tint is nearly opaque and a step brighter than the road surface, so
# a panel stands out from both the road and the black background and mutes whatever passes under its text.
GLASS_FILL = (46, 50, 60, 218)
GLASS_RIM = (255, 255, 255, 112)
GLASS_RIM_PX = 1.5


def end_fade_length(start: float, end: float, fraction: float = 0.25, low: float = 4.0, high: float = 12.0) -> float:
  """Length of the soft end of a line drawn from start to end: `fraction` of it, clipped to [low, high] metres and
  never longer than the line."""
  return max(0.0, min(max(fraction * (end - start), low), high, end - start))


LINE_STD_LIMIT = 1.0  # m: a lane line or road edge stops where its own per-point lateral std (yStd) passes this


def std_end(grid: np.ndarray, y_std: np.ndarray | None, limit: float = LINE_STD_LIMIT) -> float:
  """First grid distance where a line's resampled yStd exceeds limit; inf when it never does or is not published
  (missing, or all zero / non-finite, as when modeld leaves XYZTData.yStd empty)."""
  if y_std is None:
    return math.inf
  std = np.asarray(y_std, dtype=np.float64)
  ok = np.isfinite(std)
  if not (std[ok] > 0).any():
    return math.inf
  over = np.flatnonzero(ok & (std > limit))
  return float(grid[over[0]]) if over.size else math.inf


def line_rows(grid: np.ndarray, ys: np.ndarray, end: float, start: float, step: float = 0.75, top: float = math.inf):
  """Rows to draw a resampled line up to `end` with a soft end: its grid rows before the fade, then every `step`
  metres over the fade (bem.end_fade_length from `start`, the view's bottom), the last row exactly at the end.

  Returns (xs, lateral, valid, end, fade length) or None with nothing to draw. The end is also cut at the line's
  own data (no fade when that is the grid's end, or when the whole fade is beyond `top`, the view's top edge);
  rows inside a gap of the line are invalid."""
  ok = np.isfinite(ys)
  if np.count_nonzero(ok) < 2:
    return None
  gx = grid[ok]
  end = min(end, float(gx[-1]))
  first = float(gx[0])
  if end - first < 1e-3:
    return None
  # A line running to the grid's end runs off the widest view: no soft end there.
  fade = 0.0 if end >= grid[-1] - 1e-6 else end_fade_length(max(start, first), end)
  if end - fade >= top:  # the soft end is off the top of the view
    fade = 0.0
  body = grid[(grid >= first) & (grid < end - fade)]
  xs = np.concatenate((body, np.arange(end - fade, end, step), [end]))
  lateral = np.interp(xs, gx, ys[ok])
  valid = np.interp(xs, grid, ok.astype(np.float64)) > 0.999
  return xs, lateral, valid, end, fade


def taper(xs, end: float, length: float) -> np.ndarray:
  """1 up to end - length, falling linearly to 0 at end."""
  xs = np.asarray(xs, dtype=np.float64)
  if length <= 1e-6:
    return (xs <= end).astype(np.float64)
  return np.clip((end - xs) / length, 0.0, 1.0)


def alpha_groups(alpha: np.ndarray, step: float = 0.05) -> list[tuple[int, int, float]]:
  """Split rows with per-row alpha into strips of one alpha: (first row, last row + 1, alpha).

  Each segment between consecutive rows takes the mean of its two rows; consecutive segments share a strip while
  they fall in the same `step`-wide alpha bin, and the strip's alpha is the middle of its segments' range, so it
  is within step / 2 of every segment it covers."""
  a = np.asarray(alpha, dtype=np.float64)
  if a.size < 2:
    return []
  seg = (a[:-1] + a[1:]) / 2
  starts = np.concatenate(([0], np.flatnonzero(np.diff(np.floor(seg / step))) + 1))
  values = (np.minimum.reduceat(seg, starts) + np.maximum.reduceat(seg, starts)) / 2
  ends = np.append(starts[1:], seg.size) + 1
  return list(zip(starts.tolist(), ends.tolist(), values.tolist(), strict=True))


# ---- extra model data -----------------------------------------------------------------------------

LANE_CHANGE_STATES = {'off': 0, 'preLaneChange': 1, 'laneChangeStarting': 2, 'laneChangeFinishing': 3}
LANE_CHANGE_SIDES = {'none': 0, 'left': -1, 'right': 1}
DEFAULT_LANE_WIDTH = 3.7


def lane_change_code(state) -> int:
  """modelV2.meta.laneChangeState (enum, its name or raw int) -> 0 off, 1 pre, 2 starting, 3 finishing."""
  if isinstance(state, (int, np.integer)) and not isinstance(state, bool):
    return int(state) if 0 <= state <= 3 else 0
  return LANE_CHANGE_STATES.get(str(state), 0)


def lane_change_side(direction) -> int:
  """modelV2.meta.laneChangeDirection -> -1 left, +1 right (car-frame y sign), 0 none."""
  if isinstance(direction, (int, np.integer)) and not isinstance(direction, bool):
    return {1: -1, 2: 1}.get(int(direction), 0)
  return LANE_CHANGE_SIDES.get(str(direction), 0)


def lane_width(lanes, grid: np.ndarray, x: float, side: int, default: float = DEFAULT_LANE_WIDTH,
               low: float = 2.5, high: float = 5.0) -> float:
  """Width of the lane on `side` (-1 left, +1 right) at x from laneLines [0]/[1] or [2]/[3]; default if implausible."""
  if side == 0 or len(lanes) < 4:
    return default
  a, b = (lanes[0], lanes[1]) if side < 0 else (lanes[2], lanes[3])
  width = lateral_at(grid, b, x, math.nan) - lateral_at(grid, a, x, math.nan)
  return width if math.isfinite(width) and low <= width <= high else default


def std_alpha(std: float) -> float:
  """Lane line alpha factor from its laneLineStds entry: clip(1 - std, 0.35, 1); 1 when unknown."""
  return float(np.clip(1.0 - std, 0.35, 1.0)) if math.isfinite(std) else 1.0


def model_lead_tracks(leads, min_prob: float) -> list[tuple[np.ndarray, np.ndarray]]:
  """(x, y) trajectories (6 points at t = 0, 2 .. 10 s, camera frame, y right-positive) of leadsV3 entries
  with prob >= min_prob and a finite first point."""
  tracks = []
  for lead in list(leads)[:3]:
    try:
      prob = float(lead.prob)
      xs = np.asarray(list(lead.x), dtype=np.float64)
      ys = np.asarray(list(lead.y), dtype=np.float64)
    except (AttributeError, TypeError, ValueError):
      continue
    n = min(xs.size, ys.size)
    if prob >= min_prob and n and math.isfinite(xs[0]) and math.isfinite(ys[0]):
      tracks.append((xs[:n], ys[:n]))
  return tracks


MPH_PER_MPS = 2.2369362921
KPH_PER_MPS = 3.6


def label_font_size(view_height: float) -> float:
  """The birds-eye lead label's text size for a view rect this tall: ~2x the first version (unreadable on a car
  screen), ~37 px at 720 p. The stream HUD's speed readout is a multiple of it."""
  return max(30.0, min(54.0, view_height * 0.052))


def speed_text(mps: float, metric: bool) -> str:
  return f"{round(mps * (KPH_PER_MPS if metric else MPH_PER_MPS)):d}"


def closing_speed_label(v_rel: float, metric: bool = True, minimum: float = 0.5) -> str | None:
  """'-5 mph' / '+8 km/h' from radar vRel (negative = closing); None below `minimum` m/s or when unknown."""
  if not math.isfinite(v_rel) or abs(v_rel) < minimum:
    return None
  value = round(v_rel * (KPH_PER_MPS if metric else MPH_PER_MPS))
  return f"{value:+d} {'km/h' if metric else 'mph'}"


CURVE_MARK = 0.01  # 1/m: path points tighter than this get a brighter outer edge


def path_curvature(model) -> tuple[np.ndarray, np.ndarray] | None:
  """(position.x, signed curvature 1/m) per plan point: orientationRate.z / velocity.x, right-positive.

  The curve_speed.target formula (zero below 3 m/s, magnitude capped at 0.1); None when the fields are missing."""
  try:
    rates = np.asarray(list(model.orientationRate.z), dtype=np.float64)
    speeds = np.asarray(list(model.velocity.x), dtype=np.float64)
    xs = np.asarray(list(model.position.x), dtype=np.float64)
  except (AttributeError, TypeError, ValueError):
    return None
  n = min(rates.size, speeds.size, xs.size)
  if n < 2:
    return None
  rates, speeds = rates[:n], speeds[:n]
  with np.errstate(divide='ignore', invalid='ignore'):
    kappa = np.where(speeds >= 3.0, np.clip(rates / speeds, -0.1, 0.1), 0.0)
  return xs[:n], np.nan_to_num(kappa)


def segment_quad(x: float, y: float, heading: float, along: float, across: float) -> np.ndarray:
  """Car-frame corners (4, 2) of a bar centred at (x, y): `along` metres in the heading, `across` metres across it."""
  half = np.array([(along / 2, -across / 2), (along / 2, across / 2), (-along / 2, across / 2), (-along / 2, -across / 2)])
  return rotate_offsets(half, x, y, heading)
