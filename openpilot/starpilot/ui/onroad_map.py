"""Heading-up road-line map, drawn as a translucent overlay on the Android Auto screen.

Roads come from the compact tiles ``navtilesd`` writes; this module never
touches the network. The work is split so no frame waits on anything slow:

  worker thread   reads and decodes tiles, and turns roads and the route into
                  triangle strips for a north-up canvas around the car
  prepare()       outside any parent render target: draws a finished canvas
                  into a texture (only when one is ready), then composes the
                  widget: canvas rotated heading-up, the car, a glass tint
  draw()          inside the frame: one textured quad through a shader that
                  rounds and feathers the edges; opacity fades the map, not navigation guidance

Colors are premultiplied end to end, so translucent glows and the feathered
edge composite without dark fringes.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.gps.acquisition import GpsAcquisition, acquisition_progress
from openpilot.starpilot.gps.source import GPS_MAX_AGE_NS, PHONE_MAX_AGE_NS
from openpilot.starpilot.navigation.road_tiles import (
  DATA_ZOOM, EXTENT, LINK, MAJOR, MINOR, MOTORWAY, STREET, RoadTile, TileFormatError, TileKey, decode_road_tile,
  encode_road_tile, lat_lon, meters_per_tile, world_xy,
)
from openpilot.system.ui.lib.application import GL_VERSION

SUPERSAMPLE = 1.5            # canvas pixels per screen pixel: edges are smoothed when it is scaled down
RASTER_SUPERSAMPLE = 1.0     # Google's pictures gain nothing from it, and the canvas needs under half the memory
MAX_CANVAS = 2560
TILE_CACHE = 48
MISSING_RETRY_S = 3.0
DEAD_RECKON_S = 1.0
PHONE_DEAD_RECKON_S = GPS_MAX_AGE_NS / 1e9  # cover phone measurement/transport gaps, independently of the 10 s hold
ROUTE_STALE_OVERLAY_SECONDS = 10.0   # a route lost mid-guidance only brings back "Starting navigation" after this long
STARTING_OVERLAY_MIN_SECONDS = 5.0   # "Starting navigation" stays up at least this long, even when the route loads sooner
DESTINATION_MAX_CHARS = 32
CAR_ANCHOR = 0.68            # the car sits below center so more road ahead is visible
ZOOM_SLOW_M_PER_PX = 0.9     # parking-lot and city detail
ZOOM_FAST_M_PER_PX = 3.4     # highway overview
ZOOM_ROUTE_M_PER_PX = 1.8    # on a route: one steady level at any speed, closer only for turns
# Route steps that do not change direction (a road renaming, "continue straight") do not zoom in.
NO_ZOOM_MANEUVERS = frozenset({'continue', 'new name', 'notification', 'depart', 'merge'})  # unknown types still zoom
NO_ZOOM_MODIFIERS = frozenset({'', 'none', 'straight'})
MIN_HEADING_SPEED = 1.5      # m/s; GPS heading is noise below this
ROTATE_MIN_SPEED = 3.0       # m/s; below this the map holds its rotation
HEADING_DEADBAND_DEG = 3.0   # heading changes smaller than this leave the map still
HEADING_SETTLE_DEG = 0.5     # ...and once it turns, it keeps turning until this close, not stopping at the deadband edge
HEADING_FILTER_RATE = 2.0    # 1/s: GPS and route headings are smoothed this fast before the map follows them
HEADING_RATE = 1.5           # 1/s: how quickly the map eases toward the heading
HEADING_LOOKAHEAD_S = 2.0    # on the route, the map faces the line this far ahead, not the segment under the car
HEADING_LOOKAHEAD_MIN_M, HEADING_LOOKAHEAD_MAX_M = 20.0, 80.0
GUIDANCE_SUPERSAMPLE = 2     # the route line and puck are drawn at this scale and filtered down, so edges are smooth
MAX_TURN_DEG_S = 45.0        # the map never spins faster than this
JUNCTION_LOOKAHEAD_S = 14.0  # zoom in on the next maneuver this far ahead
JUNCTION_MIN_M, JUNCTION_MAX_M = 80.0, 300.0
STOPPED_SPEED = 0.5          # m/s
OVERVIEW_AFTER_S = 30.0      # stopped this long: zoom out to an overview
OVERVIEW_MIN_MANEUVER_M = 50.0  # ...unless a maneuver is about to happen
NAME_HOLD_S = 1.5            # a new road name must hold this long to replace the old one
ROAD_NAME_MAX_AGE_S = 3.0
GUIDANCE_SLACK_PX = 4.0      # screen pixels of driven route left under the puck before guidance is trimmed again
RASTER_REDRAW_S = 0.25       # newly arrived Google tiles redraw the canvas at most this often
SNAP_M = 22.0                # the car is drawn on the route line when it is this close to it
SNAP_HEADING_DEG = 45.0      # ...and, while moving, heading along it
SNAP_AHEAD = 400             # route points searched past the last snapped one
SNAP_RATE = 4.0              # 1/s: how quickly the car eases onto and off the line


@dataclass(frozen=True)
class RoadStyle:
  fill: tuple[int, int, int]
  width: float               # screen pixels at the reference zoom
  hide_above_m_per_px: float = math.inf


# Bottom to top. Casings go under every fill so crossings read cleanly.
ROAD_STYLES: dict[int, RoadStyle] = {
  MINOR: RoadStyle((112, 124, 140), 2.4, hide_above_m_per_px=2.2),
  STREET: RoadStyle((176, 188, 204), 3.4, hide_above_m_per_px=4.5),
  LINK: RoadStyle((214, 220, 232), 3.6),
  MAJOR: RoadStyle((236, 240, 246), 5.2),
  MOTORWAY: RoadStyle((255, 196, 92), 6.8),
}
CASING = (0, 0, 0, 200)
CASING_EXTRA = 2.6
ROUTE_GLOW = (150, 110, 255, 90)
ROUTE_CORE = (178, 150, 255, 255)
ROUTE_DONE = (118, 104, 150, 200)
GLASS = (8, 12, 20, 132)
SOURCE_LABELS = {"comma": "Comma GPS", "car": "Car GPS", "phone": "Phone GPS"}
REFERENCE_M_PER_PX = 1.4


def premultiplied(color: Sequence[int], alpha_scale: float = 1.0) -> rl.Color:
  r, g, b, a = (*color, 255) if len(color) == 3 else color
  alpha = a / 255.0 * alpha_scale
  return rl.Color(round(r * alpha), round(g * alpha), round(b * alpha), round(255 * alpha))


def zoom_for_speed(speed_mps: float) -> float:
  """Meters per screen pixel: street detail when slow, a wider view at highway speed."""
  t = min(1.0, max(0.0, (speed_mps - 8.0) / 24.0))
  t = t * t * (3 - 2 * t)
  return ZOOM_SLOW_M_PER_PX + (ZOOM_FAST_M_PER_PX - ZOOM_SLOW_M_PER_PX) * t


def zoom_target(speed_mps: float, maneuver_m: float | None = None, stopped_s: float = 0.0, guided: bool = False) -> float:
  """Meters per screen pixel: closer ahead of a maneuver, wider after sitting still.

  Without a route the zoom follows speed. On a route it holds one steady level and only zooms in for
  the next turn, so the map does not breathe in and out with every change of speed.
  """
  base = ZOOM_ROUTE_M_PER_PX if guided else zoom_for_speed(speed_mps)
  near = maneuver_m is not None and maneuver_m <= OVERVIEW_MIN_MANEUVER_M
  if stopped_s >= OVERVIEW_AFTER_S and not near:
    return ZOOM_FAST_M_PER_PX
  if maneuver_m is not None:
    reach = min(JUNCTION_MAX_M, max(JUNCTION_MIN_M, speed_mps * JUNCTION_LOOKAHEAD_S))
    t = min(1.0, max(0.0, (reach - maneuver_m) / (reach * 0.5)))
    base -= (base - ZOOM_SLOW_M_PER_PX) * t * t * (3 - 2 * t)
  return base


# ---------------------------------------------------------------- geometry

def line_strip(points: np.ndarray, offsets: np.ndarray, half_width: float, bounds: tuple[float, float, float, float],
               max_miter: float = 2.5) -> np.ndarray:
  """One triangle strip for many polylines: mitered quads, joined by degenerate triangles.

  ``points`` (N, 2) and ``offsets`` (L + 1) describe L polylines of at least two
  points. Lines entirely outside ``bounds`` (x0, y0, x1, y1) are skipped.
  """
  if len(offsets) < 2 or len(points) < 2:
    return np.empty((0, 2), np.float32)
  starts, ends = offsets[:-1], offsets[1:]
  x0, y0, x1, y1 = bounds
  lo, hi = np.minimum.reduceat(points, starts, axis=0), np.maximum.reduceat(points, starts, axis=0)
  keep = (hi[:, 0] >= x0) & (lo[:, 0] <= x1) & (hi[:, 1] >= y0) & (lo[:, 1] <= y1) & (ends - starts >= 2)
  if not keep.any():
    return np.empty((0, 2), np.float32)
  lengths = (ends - starts)[keep]
  line_offsets = np.concatenate(([0], np.cumsum(lengths)[:-1]))
  index = np.arange(int(lengths.sum())) - np.repeat(line_offsets, lengths) + np.repeat(starts[keep], lengths)
  p = points[index].astype(np.float32)
  first = np.zeros(len(p), bool)
  first[np.concatenate(([0], np.cumsum(lengths)[:-1]))] = True
  last = np.roll(first, -1)
  last[-1] = True

  seg = np.diff(p, axis=0)
  norm = np.hypot(seg[:, 0], seg[:, 1])
  seg /= np.maximum(norm, 1e-6)[:, None]
  normal = np.stack((-seg[:, 1], seg[:, 0]), axis=1)           # left of travel
  n_prev = np.vstack((normal[:1], normal))                     # segment ending at each vertex
  n_next = np.vstack((normal, normal[-1:]))                    # segment starting at each vertex
  n_prev[first] = n_next[first]
  n_next[last] = n_prev[last]
  miter = n_prev + n_next
  miter_len = np.hypot(miter[:, 0], miter[:, 1])
  flat = miter_len < 1e-3                                      # a full reversal: fall back to the segment normal
  miter[flat] = n_next[flat]
  miter_len[flat] = 1.0
  miter /= miter_len[:, None]
  scale = 1.0 / np.clip(np.einsum("ij,ij->i", miter, n_next), 1.0 / max_miter, 1.0)
  offset = miter * (scale * half_width)[:, None]

  # Right of travel, then left: front-facing for raylib in y-down screen space,
  # so backface culling never drops a road.
  strip = np.empty((len(p) * 2, 2), np.float32)
  strip[0::2] = p - offset
  strip[1::2] = p + offset
  # Repeat the last vertex of each line and the first of the next: two zero-area
  # triangles bridge the gap and keep every real triangle's winding.
  repeat = np.ones(len(strip), np.int64)
  line_starts = np.flatnonzero(first) * 2
  repeat[line_starts[1:]] += 1
  repeat[line_starts[1:] - 1] += 1
  return np.repeat(strip, repeat, axis=0)


@dataclass
class CanvasRequest:
  center: tuple[float, float]          # world (data-zoom tile units) at the canvas center
  m_per_px: float                      # screen meters per pixel this canvas was built for
  side: int                            # canvas texture side in pixels (supersampled)
  route: np.ndarray | None             # (N, 2) world points ahead of the car
  route_done: np.ndarray | None        # (N, 2) world points already driven
  tiles: tuple[TileKey, ...]
  raster: tuple[TileKey, ...] = ()     # Google tiles drawn over the road lines
  supersample: float = SUPERSAMPLE     # canvas pixels per screen pixel


@dataclass
class CanvasGeometry:
  request: CanvasRequest
  layers: list[tuple[rl.Color, np.ndarray]] = field(default_factory=list)
  tiles_missing: int = 0
  guidance: list[tuple[rl.Color, np.ndarray]] = field(default_factory=list)


def build_canvas(request: CanvasRequest, tiles: dict[TileKey, RoadTile], latitude: float) -> CanvasGeometry:
  """Screen-space strips for a north-up canvas. Pure numpy; runs off the render thread."""
  side = request.side
  px_per_m = request.supersample / request.m_per_px
  px_per_unit = meters_per_tile(latitude) * px_per_m           # canvas pixels per world tile unit
  cx, cy = request.center
  bounds = (-8.0, -8.0, side + 8.0, side + 8.0)
  ratio = math.sqrt(REFERENCE_M_PER_PX / request.m_per_px)
  width_scale = request.supersample * min(1.35, max(0.7, ratio))
  geometry = CanvasGeometry(request)
  per_class: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {}
  for key in request.tiles:
    tile = tiles.get(key)
    if tile is None:
      geometry.tiles_missing += 1
      continue
    ox = (key.x - cx) * px_per_unit + side / 2
    oy = (key.y - cy) * px_per_unit + side / 2
    scale = px_per_unit / EXTENT
    for road_class, (points, offsets) in tile.lines.items():
      if request.m_per_px > ROAD_STYLES[road_class].hide_above_m_per_px:
        continue
      per_class.setdefault(road_class, []).append((points * scale + (ox, oy), offsets))
  merged = {}
  for road_class, parts in per_class.items():
    points = np.concatenate([part[0] for part in parts])
    offsets, base = [np.zeros(1, np.int32)], 0
    for part_points, part_offsets in parts:
      offsets.append(part_offsets[1:] + base)
      base += len(part_points)
    merged[road_class] = (points, np.concatenate(offsets))
  order = [c for c in ROAD_STYLES if c in merged]
  for road_class in order:
    style = ROAD_STYLES[road_class]
    strip = line_strip(*merged[road_class], (style.width + CASING_EXTRA) * width_scale / 2, bounds)
    if len(strip):
      geometry.layers.append((premultiplied(CASING), strip))
  for road_class in order:
    style = ROAD_STYLES[road_class]
    strip = line_strip(*merged[road_class], style.width * width_scale / 2, bounds)
    if len(strip):
      geometry.layers.append((premultiplied(style.fill), strip))

  geometry.guidance = route_guidance(request.route, request, latitude)
  return geometry


def route_guidance(route, request: CanvasRequest, latitude: float):
  """Build only the remaining route; the road canvas never bakes in a driven tail."""
  if route is None or len(route) < 2:
    return []
  scale = meters_per_tile(latitude) * request.supersample / request.m_per_px
  points = ((route - request.center) * scale + request.side / 2).astype(np.float32)
  width_scale = request.supersample * min(1.35, max(0.7, math.sqrt(REFERENCE_M_PER_PX / request.m_per_px)))
  bounds = (-8.0, -8.0, request.side + 8.0, request.side + 8.0)
  # Every frame draws these strips: keep only the stretches that can reach the canvas, widest line included.
  reach = 8.0 + 17.0 * width_scale / 2
  points, offsets = visible_runs(points, (-reach, -reach, request.side + reach, request.side + reach))
  layers = []
  for color, width in ((ROUTE_GLOW, 17.0), (ROUTE_CORE, 7.0)):
    strip = line_strip(points, offsets, width * width_scale / 2, bounds)
    if len(strip):
      layers.append((premultiplied(color), strip))
  return layers


def visible_runs(points: np.ndarray, bounds: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
  """The polyline's stretches whose segments can touch ``bounds``, as (points, offsets) for line_strip.

  A stretch is broken only where a whole segment lies outside, so seams never show inside the bounds.
  """
  x0, y0, x1, y1 = bounds
  a, b = points[:-1], points[1:]
  lo, hi = np.minimum(a, b), np.maximum(a, b)
  keep = (hi[:, 0] >= x0) & (lo[:, 0] <= x1) & (hi[:, 1] >= y0) & (lo[:, 1] <= y1)
  if keep.all():
    return points, np.array([0, len(points)], np.int32)
  edges = np.diff(np.concatenate(([0], keep.astype(np.int8), [0])))
  runs = [points[start:end + 1] for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True)]
  if not runs:
    return points[:0], np.zeros(1, np.int32)
  return np.concatenate(runs), np.concatenate(([0], np.cumsum([len(run) for run in runs]))).astype(np.int32)


def snap_to_route(route: np.ndarray, position: tuple[float, float], bearing: float | None, max_offset: float,
                  start: int | None = None) -> tuple[tuple[float, float], int, float] | None:
  """The nearest point on the route ahead of ``start`` within ``max_offset`` world units (and heading along it):
  (point, segment, segment bearing), or None."""
  if route is None or len(route) < 2:
    return None
  first = 0 if start is None else max(0, start - 1)
  last = len(route) - 1 if start is None else min(len(route) - 1, start + SNAP_AHEAD)
  a, b = route[first:last], route[first + 1:last + 1]
  if not len(a):
    return None
  ab = b - a
  lengths = np.einsum('ij,ij->i', ab, ab)
  p = np.asarray(position, np.float64)
  t = np.clip(np.einsum('ij,ij->i', p - a, ab) / np.maximum(lengths, 1e-18), 0.0, 1.0)
  nearest = a + ab * t[:, None]
  offsets = np.hypot(*(nearest - p).T)
  headings = np.degrees(np.arctan2(ab[:, 0], -ab[:, 1])) % 360.0
  usable = (offsets <= max_offset) & (lengths > 0)
  if bearing is not None:
    usable &= np.abs((headings - bearing + 180.0) % 360.0 - 180.0) <= SNAP_HEADING_DEG
  if not usable.any():
    return None
  index = int(np.argmin(np.where(usable, offsets, np.inf)))
  return (float(nearest[index, 0]), float(nearest[index, 1])), first + index, float(headings[index])


def route_lengths(route: np.ndarray) -> np.ndarray:
  """Distance along the route to each point, in world units."""
  return np.concatenate(([0.0], np.cumsum(np.hypot(*np.diff(route, axis=0).T))))


def lookahead_heading(route: np.ndarray, lengths: np.ndarray, segment: int, point: tuple[float, float],
                      ahead: float) -> float | None:
  """Bearing from ``point`` (on ``segment``) to the spot ``ahead`` world units farther along the route.

  Facing a point down the line, rather than the segment under the car, keeps the map steady across the
  small kinks of a simplified route line and starts a turn's rotation smoothly as the car approaches it.
  """
  segment = min(max(segment, 0), len(route) - 2)
  a = route[segment]
  along = float(lengths[segment]) + math.hypot(point[0] - a[0], point[1] - a[1]) + ahead
  if along >= lengths[-1]:
    target = route[-1]
  else:
    j = min(len(route) - 2, max(segment, int(np.searchsorted(lengths, along, side='right')) - 1))
    span = float(lengths[j + 1] - lengths[j])
    target = route[j] + (route[j + 1] - route[j]) * (0.0 if span <= 0 else min(1.0, (along - lengths[j]) / span))
  dx, dy = float(target[0] - point[0]), float(target[1] - point[1])
  if math.hypot(dx, dy) < ahead * 0.25:
    return None                                                # at the end of the route: too short to trust
  return math.degrees(math.atan2(dx, -dy)) % 360.0


def angle_delta(target: float, current: float) -> float:
  """Signed shortest rotation from ``current`` to ``target``, in degrees."""
  return (target - current + 540.0) % 360.0 - 180.0


def split_route(route: np.ndarray | None, position: tuple[float, float]) -> tuple[np.ndarray | None, np.ndarray | None]:
  """(driven, ahead) world polylines, split at the point on the route nearest the car."""
  if route is None or len(route) < 2:
    return None, None
  a, b = route[:-1], route[1:]
  ab = b - a
  t = np.clip(np.einsum("ij,ij->i", np.asarray(position) - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-12), 0, 1)
  nearest = a + ab * t[:, None]
  index = int(np.argmin(np.hypot(*(nearest - position).T)))
  split = nearest[index]
  return np.vstack((route[:index + 1], split)), np.vstack((split, route[index + 1:]))


# ---------------------------------------------------------------- data

@dataclass(frozen=True)
class MapFix:
  latitude: float
  longitude: float
  bearing: float | None
  speed: float
  monotonic: float


@dataclass(frozen=True)
class MapInput:
  fix: MapFix | None
  route: tuple[tuple[float, float], ...] = ()      # (latitude, longitude)
  navigation_active: bool = False
  satellites: int | None = None
  gps_wait_seconds: float = 0.0
  destination: str = ''                             # where navigation is going: "Home", "Work" or the place name
  source: str = ''                                  # where the fix comes from: comma, car or phone
  maneuver_distance_m: float | None = None          # meters to the next turn while guiding (None for straight-on steps)
  road_name: str = ''                               # the road the car is on, from the map data


class TileReader:
  """Road tiles from disk, decoded on a worker thread; the render thread only reads the cache."""

  def __init__(self, root: Path | None = None, clock: Callable[[], float] = time.monotonic):
    from openpilot.starpilot.navigation.offline_roads import TileStore
    self.store = TileStore(root)
    self.clock = clock
    self._lock = threading.Condition()
    self._tiles: OrderedDict[TileKey, RoadTile] = OrderedDict()
    self._missing: dict[TileKey, float] = {}
    self._wanted: tuple[TileKey, ...] = ()
    self._jobs: list[Callable[[], None]] = []
    self.generation = 0
    self._stopped = False
    self._thread = threading.Thread(target=self._run, name="map-tiles", daemon=True)
    self._thread.start()

  def want(self, keys: Sequence[TileKey]) -> None:
    with self._lock:
      self._wanted = tuple(keys)
      self._lock.notify()

  def submit(self, job: Callable[[], None]) -> None:
    with self._lock:
      self._jobs = [job]          # only the newest canvas matters
      self._lock.notify()

  def snapshot(self, keys: Sequence[TileKey]) -> dict[TileKey, RoadTile]:
    with self._lock:
      return {key: self._tiles[key] for key in keys if key in self._tiles}

  def _next_load(self) -> TileKey | None:
    now = self.clock()
    for key in self._wanted:
      if key not in self._tiles and self._missing.get(key, -math.inf) <= now:
        return key
    return None

  def _run(self) -> None:
    while True:
      with self._lock:
        while not self._stopped and not self._jobs and self._next_load() is None:
          self._lock.wait(timeout=MISSING_RETRY_S / 2)
        if self._stopped:
          return
        key = self._next_load()
        job = self._jobs.pop() if key is None and self._jobs else None
      if key is not None:
        raw = self.store.read(key)
        try:
          tile = decode_road_tile(key, raw) if raw is not None else None
        except TileFormatError:
          tile = None
        with self._lock:
          if tile is None:
            self._missing[key] = self.clock() + MISSING_RETRY_S
          else:
            self._missing.pop(key, None)
            self._tiles[key] = tile
            self._tiles.move_to_end(key)
            while len(self._tiles) > max(TILE_CACHE, len(self._wanted)):
              self._tiles.popitem(last=False)
            self.generation += 1
      elif job is not None:
        try:
          job()
        except Exception:
          pass

  def close(self) -> None:
    with self._lock:
      self._stopped = True
      self._lock.notify()
    self._thread.join(timeout=1.0)


# ---------------------------------------------------------------- shader

MASK_VERTEX = GL_VERSION + """
in vec3 vertexPosition;
in vec2 vertexTexCoord;
in vec4 vertexColor;
out vec2 fragTexCoord;
uniform mat4 mvp;
void main() {
  fragTexCoord = vertexTexCoord;
  gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""

MASK_FRAGMENT = GL_VERSION + """
in vec2 fragTexCoord;
out vec4 finalColor;
uniform sampler2D texture0;
uniform sampler2D guidance;
uniform vec2 size;       // widget pixels
uniform float radius;    // corner radius in pixels
uniform float feather;   // soft edge width in pixels
uniform float opacity;
void main() {
  vec2 p = fragTexCoord * size;
  vec2 q = abs(p - size * 0.5) - (size * 0.5 - vec2(radius));
  float outside = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;   // rounded-rect distance, < 0 inside
  float edge = clamp(-outside / feather, 0.0, 1.0);
  edge = edge * edge * (3.0 - 2.0 * edge);
  // The glass fades a little toward the top so the road ahead blends into the camera.
  float top = smoothstep(0.0, 0.22, fragTexCoord.y) * 0.35 + 0.65;
  // A faint glass rim where the feather meets the solid part.
  float rim = (1.0 - smoothstep(0.0, 1.6, abs(outside + feather))) * 0.14;
  vec2 uv = vec2(fragTexCoord.x, 1.0 - fragTexCoord.y);
  vec4 base = texture(texture0, uv) * (opacity * top);
  vec4 route = texture(guidance, uv);
  finalColor = (route + base * (1.0 - route.a)) * edge + vec4(rim * opacity * top) * (1.0 - route.a);
}
"""


# ---------------------------------------------------------------- overlay

class MapOverlay:
  """Owns two canvases (front/back), a widget texture and the mask shader."""

  def __init__(self, root: Path | None = None, *, reader: TileReader | SampleTileReader | None = None, fonts=None,
               clock: Callable[[], float] = time.monotonic, raster=None):
    self.reader = reader or TileReader(root, clock)
    self.raster = raster
    self.fonts = fonts
    self.clock = clock
    self._lock = threading.Lock()
    self._ready: CanvasGeometry | None = None
    self._pending: CanvasRequest | None = None
    self._canvas: rl.RenderTexture | None = None
    self._canvas_request: CanvasRequest | None = None
    self._widget: rl.RenderTexture | None = None
    self._guidance: rl.RenderTexture | None = None
    self._route_layers: list[tuple[rl.Color, np.ndarray]] = []
    self._guidance_key = None
    self._guidance_at: tuple[float, float] | None = None
    self._widget_size: tuple[int, int] | None = None
    self._shader: rl.Shader | None = None
    self._uniforms: dict[str, int] = {}
    self._uniform_values = (rl.Vector2(), np.zeros(1, dtype=np.float32), np.zeros(1, dtype=np.float32), np.zeros(1, dtype=np.float32))
    self._pending_at = -math.inf
    self.has_roads = False
    self._geometry: CanvasGeometry | None = None
    self._raster_drawn = 0
    self._raster_generation = 0
    self._raster_redrawn = -math.inf
    self._route_key: tuple | None = None
    self._route_world: np.ndarray | None = None
    self._route_lengths: np.ndarray | None = None
    self._route_last_shown: float | None = None   # clock time the route was last available this navigation session
    self._starting_since: float | None = None     # clock time "Starting navigation" went up
    self._generation_built = -1
    # Smoothed display state.
    self._world: tuple[float, float] | None = None
    self._latitude = 0.0
    self._bearing = 0.0
    self._heading_target = 0.0                    # smoothed GPS or route heading the map rotates toward
    self._turning = False
    self._has_heading = False
    self._stopped_since: float | None = None
    self._name = ''
    self._name_candidate = ''
    self._name_since = 0.0
    self._name_layout: tuple | None = None
    self._attribution_layout: tuple | None = None
    self._m_per_px = ZOOM_SLOW_M_PER_PX
    self._snap_index: int | None = None
    self._snap_weight = 0.0
    self._last_prepare: float | None = None
    self.status = "waiting"
    self._input = MapInput(None)

  # ---- state

  def _advance(self, data: MapInput, now: float) -> None:
    self._input = data
    self.status = "live" if data.fix is not None and 0 <= now - data.fix.monotonic <= fix_hold_seconds(data.source) else "waiting"
    fix = data.fix
    self._track_name(data.road_name if self.status == "live" else '', now)
    dt = 0.0 if self._last_prepare is None else min(0.2, max(0.0, now - self._last_prepare))
    self._last_prepare = now
    if fix is None:
      return
    target_world = world_xy(fix.latitude, fix.longitude)
    age = min(PHONE_DEAD_RECKON_S if data.source == "phone" else DEAD_RECKON_S, max(0.0, now - fix.monotonic))
    moving = fix.bearing is not None and fix.speed > MIN_HEADING_SPEED
    if moving:
      units = fix.speed * age / meters_per_tile(fix.latitude)
      heading = math.radians(fix.bearing)
      target_world = (target_world[0] + math.sin(heading) * units, target_world[1] - math.cos(heading) * units)
    snap = None
    if data.navigation_active and self._route_world is not None:
      snap = snap_to_route(self._route_world, target_world, fix.bearing if moving else None,
                           SNAP_M / meters_per_tile(fix.latitude), self._snap_index)
    self._snap_index = snap[1] if snap is not None else None
    self._snap_weight += ((1.0 if snap is not None else 0.0) - self._snap_weight) * min(1.0, dt * SNAP_RATE)
    if snap is not None:
      w = self._snap_weight
      target_world = (target_world[0] + (snap[0][0] - target_world[0]) * w, target_world[1] + (snap[0][1] - target_world[1]) * w)
    if moving:
      assert fix.bearing is not None
      road_bearing = fix.bearing
      if snap is not None:
        assert self._route_world is not None and self._route_lengths is not None
        ahead_m = min(HEADING_LOOKAHEAD_MAX_M, max(HEADING_LOOKAHEAD_MIN_M, fix.speed * HEADING_LOOKAHEAD_S))
        ahead = lookahead_heading(self._route_world, self._route_lengths, snap[1], snap[0], ahead_m / meters_per_tile(fix.latitude))
        road_bearing = snap[2] if ahead is None else ahead
      if not self._has_heading:
        self._bearing = self._heading_target = road_bearing % 360.0
        self._has_heading, self._turning = True, False
      else:
        # Smooth the heading first so GPS noise and route kinks never reach the map, then rotate with
        # hysteresis: start past the deadband, finish close to the heading instead of parking at the edge.
        self._heading_target = (self._heading_target + angle_delta(road_bearing, self._heading_target) *
                                min(1.0, dt * HEADING_FILTER_RATE)) % 360.0
        delta = angle_delta(self._heading_target, self._bearing)
        if fix.speed <= ROTATE_MIN_SPEED or abs(delta) < HEADING_SETTLE_DEG:
          self._turning = False
        elif abs(delta) > HEADING_DEADBAND_DEG:
          self._turning = True
        if self._turning:
          limit = MAX_TURN_DEG_S * dt
          self._bearing = (self._bearing + max(-limit, min(limit, delta * min(1.0, dt * HEADING_RATE)))) % 360.0
    if self._world is None or math.hypot(target_world[0] - self._world[0], target_world[1] - self._world[1]) > 0.5:
      self._world = target_world                               # first fix or a jump: no slow glide across town
    else:
      k = min(1.0, dt * 12.0)
      self._world = (self._world[0] + (target_world[0] - self._world[0]) * k,
                     self._world[1] + (target_world[1] - self._world[1]) * k)
    self._latitude = fix.latitude
    stopped = math.isfinite(fix.speed) and fix.speed < STOPPED_SPEED
    if not stopped:
      self._stopped_since = None
    elif self._stopped_since is None:
      self._stopped_since = now
    stopped_s = 0.0 if self._stopped_since is None else now - self._stopped_since
    maneuver = data.maneuver_distance_m if data.navigation_active else None
    guided = data.navigation_active and self._route_world is not None
    target_zoom = zoom_target(fix.speed, maneuver, stopped_s, guided) if math.isfinite(fix.speed) else self._m_per_px
    self._m_per_px += (target_zoom - self._m_per_px) * min(1.0, dt * 0.8)

  def _track_name(self, name: str, now: float) -> None:
    name = ' '.join(name.split())[:64]
    if name != self._name_candidate:
      self._name_candidate, self._name_since = name, now
    if not name or now - self._name_since >= NAME_HOLD_S:
      self._name = name

  def _route(self, route: tuple[tuple[float, float], ...]) -> None:
    if route is self._route_key:
      return                                                   # MapFeed hands back the same tuple until the route changes
    if route != self._route_key:
      self._guidance_key = None
      self._snap_index = None
      self._route_world = np.array([world_xy(lat, lon) for lat, lon in route], np.float64) if len(route) >= 2 else None
      self._route_lengths = route_lengths(self._route_world) if self._route_world is not None else None
    self._route_key = route

  def _track_route(self, data: MapInput, now: float) -> None:
    if self._route_world is not None:
      self._route_last_shown = now
    elif not data.navigation_active:
      self._route_last_shown = None
    if not data.navigation_active:
      self._starting_since = None
    elif self._waiting_for_route(data, now):
      if self._starting_since is None:
        self._starting_since = now
    elif self._starting_since is not None and now - self._starting_since >= STARTING_OVERLAY_MIN_SECONDS:
      self._starting_since = None

  def _waiting_for_route(self, data: MapInput, now: float) -> bool:
    """Navigation is active without a route: at once on a fresh start, after a grace period once a route was lost."""
    return (data.navigation_active and self._route_world is None and
            (self._route_last_shown is None or now - self._route_last_shown >= ROUTE_STALE_OVERLAY_SECONDS))

  def _starting_navigation(self) -> bool:
    """While waiting for the route, and for a short minimum so the card can be read when the route is quick."""
    now = self.clock()
    return self._waiting_for_route(self._input, now) or (
      self._input.navigation_active and self._starting_since is not None and now - self._starting_since < STARTING_OVERLAY_MIN_SECONDS)

  # ---- canvas planning

  @staticmethod
  def _reach(width: int, height: int) -> float:
    """Screen pixels from the car to the widget's farthest corner, at any rotation."""
    return math.hypot(width / 2, height * max(CAR_ANCHOR, 1 - CAR_ANCHOR))

  def _supersample(self) -> float:
    return RASTER_SUPERSAMPLE if self.raster is not None and self.raster.active else SUPERSAMPLE

  def _canvas_side(self, width: int, height: int) -> int:
    return int(min(MAX_CANVAS, math.ceil(self._reach(width, height) * 2 * 1.6 * self._supersample() / 64) * 64))

  def _needs_canvas(self, side: int) -> bool:
    request = self._canvas_request
    if request is None or request.side != side:
      return True
    assert self._world is not None
    assert self._widget_size is not None
    px_per_unit = meters_per_tile(self._latitude) * request.supersample / request.m_per_px
    drift = math.hypot(self._world[0] - request.center[0], self._world[1] - request.center[1]) * px_per_unit
    reach = self._reach(*self._widget_size) * request.supersample * self._m_per_px / request.m_per_px
    ratio = request.m_per_px / self._m_per_px
    replan = (self.raster is not None and not request.raster and self.raster.active) or request.supersample != self._supersample()
    return drift > (side / 2 - reach) * 0.5 or not 0.82 < ratio < 1.22 or self.reader.generation != self._generation_built or replan

  def _request(self, side: int) -> CanvasRequest:
    assert self._world is not None
    supersample = self._supersample()
    reach_m = side / supersample * self._m_per_px * 0.75
    reach = reach_m / meters_per_tile(self._latitude)
    x, y = self._world
    tiles = tuple(TileKey(DATA_ZOOM, tx % (1 << DATA_ZOOM), ty)
                  for ty in range(int(math.floor(y - reach)), int(math.floor(y + reach)) + 1)
                  for tx in range(int(math.floor(x - reach)), int(math.floor(x + reach)) + 1)
                  if 0 <= ty < 1 << DATA_ZOOM)
    done, ahead = split_route(self._route_world, self._world) if self._route_world is not None else (None, None)
    return CanvasRequest(self._world, self._m_per_px, side, ahead, done, tiles, self._raster_plan(side, supersample), supersample)

  def _raster_plan(self, side: int, supersample: float = SUPERSAMPLE) -> tuple[TileKey, ...]:
    """Google tiles out to the widget's farthest corner plus the drift and zoom a canvas allows before it is rebuilt."""
    if self.raster is None or self._world is None or self._widget_size is None:
      return ()
    reach = self._reach(*self._widget_size)
    drift = max(0.0, side / supersample / 2 - reach) * 0.5
    latitude, longitude = lat_lon(*self._world)
    return self.raster.plan(latitude, longitude, self._m_per_px, (reach + drift) * self._m_per_px * 1.22)

  def _schedule(self, request: CanvasRequest) -> None:
    self._pending = request
    self._pending_at = self.clock()
    self.reader.want(sorted(request.tiles, key=lambda k: (k.x + 0.5 - request.center[0]) ** 2 + (k.y + 0.5 - request.center[1]) ** 2))
    generation = self.reader.generation
    latitude = self._latitude

    def job():
      geometry = build_canvas(request, self.reader.snapshot(request.tiles), latitude)
      with self._lock:
        if self._pending is request:
          self._ready = geometry
          self._generation_built = generation
    self.reader.submit(job)

  # ---- GPU

  def _ensure_targets(self, width: int, height: int, side: int) -> bool:
    if self._widget_size != (width, height):
      if self._widget is not None:
        rl.unload_render_texture(self._widget)
      if self._guidance is not None:
        rl.unload_render_texture(self._guidance)
      self._widget = rl.load_render_texture(width, height)
      self._guidance = rl.load_render_texture(width * GUIDANCE_SUPERSAMPLE, height * GUIDANCE_SUPERSAMPLE)
      self._widget_size = None
      if not self._widget.id or not self._guidance.id:
        for target in (self._widget, self._guidance):
          if target.id:
            rl.unload_render_texture(target)
        self._widget = self._guidance = None
        return False
      self._widget_size = (width, height)
      for target in (self._widget, self._guidance):
        rl.set_texture_filter(target.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
    if self._shader is None:
      self._shader = rl.load_shader_from_memory(MASK_VERTEX, MASK_FRAGMENT)
      if not self._shader.id:
        raise RuntimeError("Map overlay shader unavailable")
      self._uniforms = {name: rl.get_shader_location(self._shader, name) for name in ("size", "radius", "feather", "opacity", "guidance")}
    return True

  def _draw_canvas(self, geometry: CanvasGeometry) -> None:
    side = geometry.request.side
    if self._canvas is None or self._canvas.texture.width != side:
      if self._canvas is not None:
        rl.unload_render_texture(self._canvas)
      self._canvas = rl.load_render_texture(side, side)
      if not self._canvas.id:
        self._canvas = None
        return
      rl.set_texture_filter(self._canvas.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
    rl.begin_texture_mode(self._canvas)
    try:
      rl.clear_background(rl.BLANK)
      rl.begin_blend_mode(rl.BlendMode.BLEND_ALPHA_PREMULTIPLY)
      for color, strip in geometry.layers:
        strip = np.ascontiguousarray(strip, np.float32)
        rl.draw_triangle_strip(strip, len(strip), color)
      rl.end_blend_mode()
      self._raster_drawn = self._draw_raster(geometry.request)
    finally:
      rl.end_texture_mode()
    self._geometry = geometry
    self._guidance_key = None
    self._canvas_request = geometry.request
    self.has_roads = geometry.tiles_missing < len(geometry.request.tiles) or self._raster_drawn > 0

  def _draw_raster(self, request: CanvasRequest) -> int:
    """Google tiles over the road lines, which show through wherever a tile has not arrived yet."""
    if self.raster is None or not request.raster:
      return 0
    self._raster_generation = self.raster.generation
    textures = self.raster.textures(request.raster)
    px_per_unit = meters_per_tile(lat_lon(*request.center)[0]) * request.supersample / request.m_per_px
    rl.begin_blend_mode(rl.BlendMode.BLEND_ALPHA)
    try:
      for key, texture in textures.items():
        scale = 2.0 ** (DATA_ZOOM - key.z)
        x = (key.x * scale - request.center[0]) * px_per_unit + request.side / 2
        y = (key.y * scale - request.center[1]) * px_per_unit + request.side / 2
        size = scale * px_per_unit + 1.0      # a pixel of overlap hides seams between tiles
        rl.draw_texture_pro(texture, rl.Rectangle(0, 0, texture.width, texture.height), rl.Rectangle(x, y, size, size),
                            rl.Vector2(0, 0), 0, rl.WHITE)
    finally:
      rl.end_blend_mode()
    return len(textures)

  def _puck(self, x: float, y: float) -> None:
    rl.draw_circle_v(rl.Vector2(x, y), 26, premultiplied((150, 110, 255, 70)))
    points = [(0, -19), (-14, 15), (0, 7), (14, 15)]
    outline = [rl.Vector2(x + px * 1.25, y + py * 1.25) for px, py in points]
    body = [rl.Vector2(x + px, y + py) for px, py in points]
    for shape, color in ((outline, premultiplied((96, 70, 210))), (body, premultiplied((255, 255, 255)))):
      rl.draw_triangle(shape[0], shape[1], shape[2], color)
      rl.draw_triangle(shape[0], shape[2], shape[3], color)

  def _compass(self, width: int, height: int) -> None:
    """A small needle in the top right that always points north."""
    inset = max(22.0, min(width, height) * 0.09)
    cx, cy = width - inset, inset
    rl.draw_circle_v(rl.Vector2(cx, cy), 13, premultiplied((8, 12, 20, 170)))
    angle = math.radians(-self._bearing)
    def point(r, a):
      return rl.Vector2(cx + r * math.sin(angle + a), cy - r * math.cos(angle + a))
    rl.draw_triangle(point(10, 0), point(5, -2.3), point(5, 2.3), premultiplied((255, 110, 96)))
    rl.draw_triangle(point(10, math.pi), point(5, math.pi + 2.3), point(5, math.pi - 2.3), premultiplied((210, 214, 224, 200)))

  def _compose_layer(self, width: int, height: int, guidance: bool) -> None:
    rl.begin_texture_mode(self._guidance if guidance else self._widget)
    try:
      rl.clear_background(rl.BLANK)
      rl.begin_blend_mode(rl.BlendMode.BLEND_ALPHA_PREMULTIPLY)
      # Render textures have no MSAA: the guidance layer is drawn larger and its bilinear sample in the
      # mask shader averages each 2x2 block, so the route line's edges stay smooth at any rotation.
      rl.rl_push_matrix()
      if guidance:
        rl.rl_scalef(GUIDANCE_SUPERSAMPLE, GUIDANCE_SUPERSAMPLE, 1)
      if not guidance:
        rl.draw_rectangle(0, 0, width, height, premultiplied(GLASS))
      request = self._canvas_request
      if self._canvas is not None and request is not None and self._world is not None:
        px_per_unit = meters_per_tile(self._latitude) / request.m_per_px
        zoom = request.m_per_px / self._m_per_px / request.supersample
        side = request.side
        # The car's spot on the canvas, in canvas pixels.
        car_x = (self._world[0] - request.center[0]) * px_per_unit * request.supersample + side / 2
        car_y = (self._world[1] - request.center[1]) * px_per_unit * request.supersample + side / 2
        anchor = rl.Vector2(width / 2, height * CAR_ANCHOR)
        if guidance:
          # Road geometry is cached across many frames. Guidance is trimmed at the
          # interpolated car, but only again once the car has moved a few screen
          # pixels: the driven bit in between stays under the puck. Re-trimming
          # every frame halved the car view's frame rate on long routes.
          moved = math.inf if self._guidance_at is None else math.hypot(self._world[0] - self._guidance_at[0],
                                                                         self._world[1] - self._guidance_at[1])
          slack = GUIDANCE_SLACK_PX * self._m_per_px / meters_per_tile(self._latitude)
          if self._guidance_key != id(request) or moved > slack:
            _, ahead = split_route(self._route_world, self._world)
            self._route_layers = route_guidance(ahead, request, self._latitude)
            self._guidance_key = id(request)
            self._guidance_at = self._world
          rl.rl_push_matrix()
          try:
            rl.rl_translatef(anchor.x, anchor.y, 0)
            rl.rl_rotatef(-self._bearing, 0, 0, 1)
            rl.rl_scalef(zoom, zoom, 1)
            rl.rl_translatef(-car_x, -car_y, 0)
            for color, strip in self._route_layers:
              strip = np.ascontiguousarray(strip, np.float32)
              rl.draw_triangle_strip(strip, len(strip), color)
          finally:
            rl.rl_pop_matrix()
          if self.status == "live":
            self._puck(anchor.x, anchor.y)
          self._compass(width, height)
        else:
          rl.draw_texture_pro(self._canvas.texture, rl.Rectangle(0, 0, side, -side),
                              rl.Rectangle(anchor.x, anchor.y, side * zoom, side * zoom),
                              rl.Vector2(car_x * zoom, car_y * zoom), -self._bearing, rl.WHITE)
      rl.rl_pop_matrix()
      rl.end_blend_mode()
    finally:
      rl.end_texture_mode()

  def _compose(self, width: int, height: int) -> None:
    self._compose_layer(width, height, False)
    self._compose_layer(width, height, True)

  # ---- public

  def prepare(self, data: MapInput, width: int, height: int) -> None:
    """Outside any render target: advance, rebuild the canvas if needed and compose the widget."""
    width, height = max(16, int(width)), max(16, int(height))
    if not self._ensure_targets(width, height, 0):
      return
    now = self.clock()
    self._route(data.route)
    self._advance(data, now)
    self._track_route(data, now)
    if self._world is not None:
      side = self._canvas_side(width, height)
      with self._lock:
        ready, self._ready = self._ready, None
      if ready is not None:
        self._draw_canvas(ready)
      if self.raster is not None:
        self.raster.upload()
        if (self._geometry is not None and self.raster.generation != self._raster_generation and
            now - self._raster_redrawn >= RASTER_REDRAW_S):
          self._raster_redrawn = now
          self._draw_canvas(self._geometry)
      in_flight = self._pending is not None and self._canvas_request is not self._pending and now - self._pending_at < 2.0
      if not in_flight and self._needs_canvas(side):
        self._schedule(self._request(side))
    self._compose(width, height)

  def draw(self, rect: rl.Rectangle, opacity: float) -> None:
    """Inside the frame: the composed widget, rounded, feathered and faded."""
    if self._widget is None or self._shader is None:
      return
    assert self._widget_size is not None
    width, height = self._widget_size
    size, radius, feather, alpha = self._uniform_values
    size.x, size.y = width, height
    radius[0] = min(width, height) * 0.09
    feather[0] = max(10.0, min(width, height) * 0.07)
    alpha[0] = max(0.0, min(1.0, opacity))
    rl.begin_blend_mode(rl.BlendMode.BLEND_ALPHA_PREMULTIPLY)
    rl.begin_shader_mode(self._shader)
    try:
      # Typed pointers: pyray drops float arrays and by-value structs passed as uniform data.
      vec2, scalar = rl.ShaderUniformDataType.SHADER_UNIFORM_VEC2, rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT
      for name, value, kind in (("size", size, vec2), ("radius", radius, scalar), ("feather", feather, scalar), ("opacity", alpha, scalar)):
        rl.set_shader_value(self._shader, self._uniforms[name], value, kind)
      rl.set_shader_value_texture(self._shader, self._uniforms["guidance"], self._guidance.texture)
      rl.draw_texture_pro(self._widget.texture, rl.Rectangle(0, 0, width, height), rect, rl.Vector2(0, 0), 0, rl.WHITE)
    finally:
      rl.end_shader_mode()
      rl.end_blend_mode()
    if self._raster_drawn:
      self._draw_attribution(rect)
    if self.status == "waiting":
      self._draw_gps_status(rect)
      return
    self._draw_source_chip(rect)
    if self._starting_navigation():
      self._draw_starting_navigation(rect)
      return
    text = "" if self.has_roads or self._canvas_request is None else "No map data here yet"
    if not text and self._name and self.fonts is not None:
      self._draw_road_name(rect)
    if text and self.fonts is not None:
      from openpilot.starpilot.ui.presentation import FontRole
      measured = self.fonts.measure(text, FontRole.MEDIUM, 26)
      self.fonts.draw(text, FontRole.MEDIUM, 26, rect.x + (rect.width - measured.width) / 2,
                      rect.y + rect.height * CAR_ANCHOR + 40, rl.Color(225, 228, 238, round(200 * opacity)))

  def _draw_road_name(self, rect: rl.Rectangle) -> None:
    """Draw the current road below the car, independent of map opacity."""
    from openpilot.starpilot.ui.presentation import FontRole
    size = max(18, round(min(rect.width, rect.height) * 0.075))
    limit = rect.width * 0.8 - size
    key = (self._name, size, round(limit))
    if self._name_layout is None or self._name_layout[0] != key:
      text = self._name
      while len(text) > 3 and self.fonts.measure(text, FontRole.MEDIUM, size).width > limit:
        text = text[:-4] + "..." if text.endswith("...") else text[:-1] + "..."
      self._name_layout = key, text, self.fonts.measure(text, FontRole.MEDIUM, size).width
    _, text, text_width = self._name_layout
    pad = size * 0.55
    pill = rl.Rectangle(rect.x + (rect.width - text_width) / 2 - pad, rect.y + rect.height * CAR_ANCHOR + 38,
                        text_width + pad * 2, size * 1.55)
    rl.draw_rectangle_rounded(pill, 0.5, 8, rl.Color(15, 13, 23, 255))
    rl.draw_rectangle_rounded_lines_ex(pill, 0.5, 8, 1.5, rl.Color(150, 140, 200, 255))
    self.fonts.draw(text, FontRole.MEDIUM, size, pill.x + pad, pill.y + size * 0.27, rl.Color(240, 242, 250, 255))

  def _draw_attribution(self, rect: rl.Rectangle) -> None:
    """Google's name and data credit along the bottom edge, solid whatever the map's opacity."""
    if self.fonts is None:
      return
    from openpilot.starpilot.ui.onroad_google_map import ATTRIBUTION
    from openpilot.starpilot.ui.presentation import FontRole
    scale = max(.7, min(1.0, rect.width / 560, rect.height / 420))
    inset, bottom = 16 * scale, rect.y + rect.height - 10 * scale
    logo = self.fonts.measure(ATTRIBUTION, FontRole.SEMI_BOLD, 17 * scale)
    self.fonts.draw(ATTRIBUTION, FontRole.SEMI_BOLD, 17 * scale, rect.x + inset, bottom - logo.height, rl.Color(240, 242, 250, 255))
    size, room = 13 * scale, rect.width - logo.width - inset * 3
    key = (self.raster.copyright if self.raster is not None else '', size, round(room))
    if self._attribution_layout is None or self._attribution_layout[0] != key:
      text = key[0] if room > 0 else ''
      while len(text) > 3 and self.fonts.measure(text, FontRole.NORMAL, size).width > room:
        text = text[:-4] + "..." if text.endswith("...") else text[:-1] + "..."
      self._attribution_layout = key, text, self.fonts.measure(text, FontRole.NORMAL, size) if text else None
    _, text, measured = self._attribution_layout
    if text:
      self.fonts.draw(text, FontRole.NORMAL, size, rect.x + rect.width - inset - measured.width, bottom - measured.height,
                      rl.Color(214, 218, 230, 255))

  def _draw_source_chip(self, rect: rl.Rectangle) -> None:
    """Where the fix comes from, left of the compass; solid whatever the map's opacity so it always reads."""
    label = SOURCE_LABELS.get(self._input.source)
    if label is None or self.fonts is None:
      return
    from openpilot.starpilot.ui.presentation import FontRole
    scale = max(.7, min(1.0, rect.width / 560, rect.height / 420))
    size, pad_x, height = 17 * scale, 10 * scale, 26 * scale
    inset = max(22.0, min(rect.width, rect.height) * 0.09)
    measured = self.fonts.measure(label, FontRole.SEMI_BOLD, size)
    width = measured.width + 2 * pad_x
    chip = rl.Rectangle(rect.x + rect.width - inset - 13 - 8 * scale - width, rect.y + inset - height / 2, width, height)
    if chip.x < rect.x + 4:
      return
    rl.draw_rectangle_rounded(chip, 1.0, 10, rl.Color(15, 13, 23, 255))
    rl.draw_rectangle_rounded_lines_ex(chip, 1.0, 10, 1.5, rl.Color(150, 140, 200, 255))
    self.fonts.draw(label, FontRole.SEMI_BOLD, size, chip.x + pad_x, chip.y + (height - measured.height) / 2, rl.Color(240, 242, 250, 255))

  def _draw_starting_navigation(self, rect: rl.Rectangle) -> None:
    """Shown while navigation is starting; solid whatever the map's opacity so it always reads."""
    if self.fonts is None:
      return
    from openpilot.starpilot.ui.presentation import FontRole
    scale = max(.7, min(1.0, rect.width / 560, rect.height / 420))
    inset, padding = 20 * scale, 14 * scale
    width = rect.width - 2 * inset
    destination = self._input.destination.strip()
    if len(destination) > DESTINATION_MAX_CHARS:
      destination = destination[:DESTINATION_MAX_CHARS - 1].rstrip() + '…'
    rows = [('Starting navigation', FontRole.SEMI_BOLD, 26)]
    if destination:
      rows.append((f'to {destination}', FontRole.MEDIUM, 22))
    if self._route_world is None:
      rows.append(('Finding your route…', FontRole.NORMAL, 18))
    scale = min(scale, *(max(1, width - 2 * padding) / self.fonts.measure(text, role, size).width
                         for text, role, size in rows))
    height = (len(rows) * 32 + 22) * scale
    card = rl.Rectangle(rect.x + inset, rect.y + rect.height - inset - height, width, height)
    rl.draw_rectangle_rounded(card, .2, 8, rl.Color(15, 13, 23, 255))
    y = card.y + 10 * scale
    for text, role, size in rows:
      measured = self.fonts.measure(text, role, size * scale)
      self.fonts.draw(text, role, size * scale, card.x + (card.width - measured.width) / 2, y,
                      rl.Color(225, 228, 238, 255))
      y += 32 * scale

  def _draw_gps_status(self, rect: rl.Rectangle) -> None:
    """AAComma's acquisition overlay, fitted to the bottom of the projected map."""
    if self.fonts is None:
      return
    from openpilot.starpilot.ui.presentation import FontRole
    data = self._input
    elapsed = max(0, int(data.gps_wait_seconds))
    satellites = ('Searching for satellites' if data.satellites is None else
                  f"{data.satellites} satellite{'s' if data.satellites != 1 else ''} locked")
    rows = [('Navigation active', FontRole.SEMI_BOLD, 22)] if data.navigation_active else []
    rows += [('Waiting for GPS', FontRole.MEDIUM, 26), (f'{satellites}  •  {elapsed // 60}:{elapsed % 60:02d}', FontRole.NORMAL, 18)]
    scale = max(.7, min(1.0, rect.width / 560, rect.height / 420))
    inset, padding = 20 * scale, 14 * scale
    width = rect.width - 2 * inset
    # Keep every line inside even the smallest allowed map widget.
    scale = min(scale, *(max(1, width - 2 * padding) / self.fonts.measure(text, role, size).width
                         for text, role, size in rows))
    height = (len(rows) * 30 + 30) * scale
    card = rl.Rectangle(rect.x + inset, rect.y + rect.height - inset - height, width, height)
    rl.draw_rectangle_rounded(card, .2, 8, rl.Color(15, 13, 23, 235))
    y = card.y + 10 * scale
    for text, role, size in rows:
      measured = self.fonts.measure(text, role, size * scale)
      self.fonts.draw(text, role, size * scale, card.x + (card.width - measured.width) / 2, y,
                      rl.Color(225, 228, 238, 255))
      y += 30 * scale
    track = rl.Rectangle(card.x + padding, y + 2 * scale, card.width - 2 * padding, 6 * scale)
    rl.draw_rectangle_rounded(track, 1, 8, rl.Color(66, 58, 82, 255))
    fill = rl.Rectangle(track.x, track.y, max(track.height, track.width * acquisition_progress(data.satellites)), track.height)
    rl.draw_rectangle_rounded(fill, 1, 8, rl.Color(199, 174, 247, 255))

  def close(self) -> None:
    self.reader.close()
    if self.raster is not None:
      self.raster.close()
    if rl.is_window_ready():
      for target in (self._canvas, self._widget, self._guidance):
        if target is not None:
          rl.unload_render_texture(target)
      if self._shader is not None and self._shader.id:
        rl.unload_shader(self._shader)
    self._canvas = self._widget = self._guidance = None
    self._route_layers = []
    self._shader = None


def live_overlay(fonts=None) -> MapOverlay:
  """The driving map: road lines from disk, with Google tiles over them when Google is the chosen map style."""
  from openpilot.starpilot.ui.onroad_google_map import GoogleRaster
  return MapOverlay(fonts=fonts, raster=GoogleRaster())


# ---------------------------------------------------------------- live input

def fix_hold_seconds(source: str) -> float:
  return (PHONE_MAX_AGE_NS if source == "phone" else GPS_MAX_AGE_NS) / 1e9


class MapFeed:
  """The same GPS choice as navigation (either receiver, then the car's own), and the active route."""

  def __init__(self, sm=None, *, acquisition=None):
    from openpilot.starpilot.gps.source import GPS_SERVICES
    if sm is None:
      from openpilot.cereal import messaging
      sm = messaging.SubMaster([*GPS_SERVICES, "mapdOut"])
    self.sm = sm
    self._fix: MapFix | None = None
    self._source = ''
    self._route_stamp = None
    self._route_valid_until = 0
    self._route: tuple[tuple[float, float], ...] = ()
    self._navigation_active = False
    self._destination = ''
    self._maneuver_m: float | None = None
    self._road = ''
    self._road_at = -math.inf
    self._acquisition = acquisition if acquisition is not None else GpsAcquisition()
    self._acquisition_polled = -math.inf

  def read(self, navigation_sm=None, *, navigation_requested=None, destination=None) -> MapInput:
    from openpilot.starpilot.gps.source import bearing, select_source
    self.sm.update(0)
    now = time.monotonic()
    now_ns = time.monotonic_ns()
    chosen = select_source(self.sm, now_ns)
    selected = chosen[1] if chosen is not None else None
    if selected is not None:
      stamp, gps = selected
      speed = float(getattr(gps, "speed", float("nan")))
      if -85 < gps.latitude < 85:
        # The fix's own timestamp, so dead reckoning starts from when it was measured.
        self._fix = MapFix(gps.latitude, gps.longitude, bearing(gps), max(0.0, speed) if math.isfinite(speed) else math.nan, stamp / 1e9)
        self._source = chosen[0]
    if self._fix is not None and now - self._fix.monotonic > max(5.0, fix_hold_seconds(self._source)):
      self._fix = None
    fresh = self._fix is not None and 0 <= now - self._fix.monotonic <= fix_hold_seconds(self._source)
    if fresh:
      self._acquisition.reset()
      self._acquisition_polled = -math.inf
    elif now - self._acquisition_polled >= .5:
      self._acquisition_polled = now
      self._acquisition.update(now)
    route: tuple[tuple[float, float], ...] = ()
    active = False
    if navigation_sm is not None:
      # Decode only new navigation messages and retain the same route tuple
      # while its points are unchanged. GPS acquisition still advances each read.
      try:
        stamp = (navigation_sm.logMonoTime["starpilotNavigation"], navigation_sm.valid["starpilotNavigation"])
      except Exception:
        stamp = None
      if stamp is None or stamp != self._route_stamp:
        self._route_stamp = stamp
        try:
          from openpilot.starpilot.navigation.wire import navigation_state
          nav = navigation_state(navigation_sm["starpilotNavigation"]) if navigation_sm.valid["starpilotNavigation"] else None
          active = bool(nav is not None and nav.enabled and nav.destinationName)
          self._destination = nav.destinationName if active else ''
          self._route_valid_until = 0
          self._maneuver_m = None
          if (nav is not None and nav.enabled and nav.status in ("guiding", "arrived") and
              0 < nav.frameMonoTime <= now_ns <= nav.frameMonoTime + 3_000_000_000):
            self._route_valid_until = nav.frameMonoTime + 3_000_000_000
            route = tuple((point.latitude, point.longitude) for point in nav.route)
            self._maneuver_m = self._maneuver(nav)
        except Exception:
          route = ()
        if route != self._route:
          self._route = route
        self._navigation_active = active
      if now_ns > self._route_valid_until:
        self._route = ()
        self._maneuver_m = None
      route, active = self._route, self._navigation_active
    acquisition_elapsed = 0.0
    if not fresh:
      acquisition_since = self._acquisition.since
      assert acquisition_since is not None  # update() starts the acquisition clock.
      acquisition_elapsed = now - acquisition_since
    return MapInput(self._fix, route, active if navigation_requested is None else navigation_requested,
                    None if fresh else self._acquisition.satellites(now),
                    acquisition_elapsed, self._destination if destination is None else destination,
                    self._source if self._fix is not None else '', self._maneuver_m if route else None, self._road_name(now))

  @staticmethod
  def _maneuver(nav) -> float | None:
    """Distance to the next turn worth zooming in for, or None for a step that keeps going straight."""
    instruction = nav.instruction
    meters = float(instruction.distanceMeters)
    if (getattr(instruction, 'maneuverType', '') in NO_ZOOM_MANEUVERS and
        getattr(instruction, 'maneuverModifier', '') in NO_ZOOM_MODIFIERS):
      return None
    return meters if nav.status == "guiding" and math.isfinite(meters) and meters >= 0 else None

  def _road_name(self, now: float) -> str:
    if self.sm.updated.get("mapdOut", False):
      out = self.sm["mapdOut"]
      self._road = (out.roadName or out.wayName) if self.sm.valid["mapdOut"] else ''
      self._road_at = now
    return self._road if 0 <= now - self._road_at <= ROAD_NAME_MAX_AGE_S else ''



# ---------------------------------------------------------------- parked preview

SAMPLE_TILE = TileKey(DATA_ZOOM, 2950, 6427)
SAMPLE_CAR = (1450.0, 2930.0)          # tile units inside SAMPLE_TILE, south of a left turn
SAMPLE_BEARING = 0.0


def _sample_roads() -> dict[int, list[np.ndarray]]:
  """A believable neighborhood for the parked layout preview; drawn from no real map."""
  t = np.linspace(-4500, 8600, 90)
  roads: dict[int, list[np.ndarray]] = {
    MOTORWAY: [np.stack((2700 + 520 * np.sin(t / 1900), t), axis=1)],
    MAJOR: [np.array([[-4500, 2300], [1200, 2620], [8600, 2050]]), np.array([[1450, 8600], [1450, 2620], [1700, -4500]])],
    STREET: [], MINOR: [], LINK: [np.array([[2400, 1500], [2650, 1900], [3050, 2200]])],
  }
  angle = math.radians(8)
  rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
  for i in range(-12, 22):
    offset = i * 420.0
    roads[STREET].append(np.array([[offset, -4500], [offset + 60, 8600]]) @ rotation.T)
    roads[STREET].append(np.array([[-4500, offset], [8600, offset - 90]]) @ rotation.T)
    roads[MINOR].append(np.array([[offset + 210, offset - 300], [offset + 330, offset - 120], [offset + 210, offset + 60]]))
  return roads


class SampleTileReader:
  """TileReader's interface over one synthetic tile; jobs run inline."""

  def __init__(self):
    raw = encode_road_tile(_sample_roads())
    self._tile = decode_road_tile(SAMPLE_TILE, raw)
    self.generation = 1

  def want(self, keys) -> None:
    pass

  def submit(self, job) -> None:
    job()

  def snapshot(self, keys) -> dict[TileKey, RoadTile]:
    return {key: self._tile for key in keys if key == SAMPLE_TILE}

  def close(self) -> None:
    pass


def sample_input() -> MapInput:
  def world(x, y):
    return lat_lon(SAMPLE_TILE.x + x / EXTENT, SAMPLE_TILE.y + y / EXTENT)
  latitude, longitude = world(*SAMPLE_CAR)
  route = tuple(world(x, y) for x, y in ((1450, 4200), (1450, 2620), (1200, 2620), (-1800, 2460)))
  return MapInput(MapFix(latitude, longitude, SAMPLE_BEARING, 8.0, time.monotonic()), route, road_name="Desert Inn Road")
