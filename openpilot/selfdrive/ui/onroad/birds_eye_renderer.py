"""Birds-eye view of the model's road, plan and leads: a 3D chase camera above and behind the car.

The road is drawn by raylib through a camera matrix (birds_eye_math.view_params): for the ground pass the camera
is raylib's modelview, so road fill, lane lines, edges, path, shadows and the ego's glow are plain 2D raylib
shapes in meters (x forward, y right) that the GPU projects onto the ground plane. Their geometry is resampled
onto one forward-distance grid, built once per model frame and replayed from cache; the camera moving (the
speed zoom gliding) changes one matrix and rebuilds nothing. Cars are low-poly bodies whose few dozen vertices
go through the same matrix on the CPU, drawn as flat-shaded fans in painter's order, so no depth buffer is used.
Labels and the speed-limit sign are ordinary 2D drawing in pixels.

The look is flat: a solid black background, no gradients, and panels (the lead label, the overlay card) drawn as
glass: a smoked tint under a bright hairline rim (bem.GLASS_FILL / GLASS_RIM).

Drawing cost: a frame issues O(ribbons) C calls, not O(vertices). Each road edge, lane line and the road fill is
one single-coloured DrawTriangleStrip per valid run plus a few constant-alpha strips for its soft end (each line
ends at its own distance, bem.line_rows). The path is a handful of constant-alpha strips that step through its
soft end; only the per-row colour modes (experimental accel tint, rainbow, colour gradient) replay per-vertex
quads, for the path alone. A car is one DrawTriangleFan per face that looks toward the camera (about ten).
"""
from collections.abc import Callable
import ctypes
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import chain, repeat, starmap
import math
import operator
import time
from types import SimpleNamespace
from typing import NamedTuple

import numpy as np

from openpilot.selfdrive.ui.onroad import birds_eye_math as bem
from openpilot.starpilot.ui.raylib import native_library_path, rl
from openpilot.system.ui.lib.shader_polygon import Gradient
from openpilot.system.ui.widgets import Widget


class _CColor(ctypes.Structure):
  _fields_ = [(channel, ctypes.c_ubyte) for channel in "rgba"]


class _CMatrix(ctypes.Structure):
  """raylib's Matrix: its 16 floats in memory order are the rows of the matrix as written on paper."""
  _fields_ = [(f"m{i}", ctypes.c_float) for i in (0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15)]


class _Raw:
  """The C functions of the raylib library the UI runs on, looked up by name in that same library (as the native
  extension does), so an op replays a point buffer and a prebuilt Color with no argument conversion. The nanobind
  bindings have no rlColor4ub, back-face culling or modelview calls, and their strip and fan calls take a Python
  sequence."""

  def __init__(self, path: str):
    library = ctypes.CDLL(path)

    def bind(name: str, *argtypes, restype=None):
      function = getattr(library, name)
      function.argtypes, function.restype = argtypes, restype
      return function

    integer, color = ctypes.c_int, _CColor
    self.DrawTriangleStrip = bind("DrawTriangleStrip", ctypes.c_void_p, integer, color)
    self.DrawTriangleFan = bind("DrawTriangleFan", ctypes.c_void_p, integer, color)
    self.rlColor4ub = bind("rlColor4ub", *[ctypes.c_ubyte] * 4)
    self.rlVertex2f = bind("rlVertex2f", ctypes.c_float, ctypes.c_float)
    self.rlBegin = bind("rlBegin", integer)
    self.rlEnd = bind("rlEnd")
    self.rlSetTexture = bind("rlSetTexture", ctypes.c_uint)
    self.rlGetTextureIdDefault = bind("rlGetTextureIdDefault", restype=ctypes.c_uint)
    self.rlDisableBackfaceCulling = bind("rlDisableBackfaceCulling")
    self.rlEnableBackfaceCulling = bind("rlEnableBackfaceCulling")
    self.rlDrawRenderBatchActive = bind("rlDrawRenderBatchActive")
    self.rlGetMatrixModelview = bind("rlGetMatrixModelview", restype=_CMatrix)
    self.rlGetMatrixTransform = bind("rlGetMatrixTransform", restype=_CMatrix)
    self.rlSetMatrixModelview = bind("rlSetMatrixModelview", _CMatrix)
    self.rlPushMatrix = bind("rlPushMatrix")
    self.rlPopMatrix = bind("rlPopMatrix")
    self.rlLoadIdentity = bind("rlLoadIdentity")


_raw = _Raw(native_library_path)

EGO_FRONT = 1.5      # meters from the camera to the ego front bumper
EGO_LENGTH = 4.6     # car bodies are 4.6 x 1.9 m, true to the road's scale
EGO_WIDTH = 1.9
LEAD_LENGTH = 4.6
LEAD_WIDTH = 1.9
PATH_WIDTH = 1.7
LANE_WIDTH = 0.14
EDGE_WIDTH = 0.28
MODEL_LEAD_PROB = 0.5
SMOOTHING = 0.55     # blend toward each new model frame
LEAD_SMOOTHING = 0.5
# Lines narrower than this many pixels are widened to it: what the far end of the view shows. Near the car the
# metre widths are wider anyway.
LANE_MIN_PX = 3.0
EGO_LANE_MIN_PX = 3.5
EDGE_MIN_PX = 3.5
PATH_MIN_PX = 3.0
WIDTH_REFERENCE_STEP = 8.0  # m of zoom per width reference: the minimum widths are sized for the zoom rounded up to it
TAIL_STEP = 0.5             # m between rows in a ribbon's soft end
SOFT_ENDS = False           # lines and the path end with a hard cut: the design has no fades
# model_renderer.MIN/MAX_DRAW_DISTANCE (not imported: that module needs Params and the UI state at import).
MIN_DRAW_DISTANCE = 10.0
MAX_DRAW_DISTANCE = 100.0
LABEL_PAD = 6.0
LABEL_ROUNDNESS = 0.35  # the label pill's corner radius over half its height: a rounded rectangle, not a capsule
LABEL_SMOOTHING = 0.5   # per frame, toward the glyph-attached label position
LABEL_SNAP_PX = 80.0    # a jump larger than this (side flip, a new lead) is taken directly
SUBLABEL_SCALE = 0.62   # curve tag, relative to the distance label
SHOW_CURVE_MARK = False  # the curve-speed bar and tag on the path (scene.curve is still ingested)
SHOW_LANE_CHANGE_GHOST = False  # the ego outline one lane over during a lane change (the state is still ingested)

LANE_HEADING_PROB = 0.3  # an ego lane line needs this probability to steer the lead glyph's heading
LAT_ACTIVE_LANE = (52, 216, 104)  # ego lane lines while lateral control is active
LANE_CENTERING_LANE = (0, 176, 220)  # = lane_feedback.BLUE, while the lane-centering correction applies

THROTTLE = ((13, 248, 122), (114, 255, 92))
NO_THROTTLE = ((242, 242, 242), (242, 242, 242))

BACKGROUND = (0, 0, 0)                # the full-frame view's background: solid black
ROAD = (30, 34, 44)                   # the road surface between the edges
PANEL_FILL = (0, 0, 0, 170)           # the overlay card over the camera: smoked glass, under the glass rim
PANEL_RADIUS = 28                     # px, the overlay panel's corner radius
LINE_ALPHA_STEP = 0.1                 # each line's soft end: strips within +/-5 % of a linear fade
PATH_ALPHA_STEP = 0.04                # the path's stepped fade: each strip within +/-2 % of the smooth alpha
LEAD_LATERAL_GATE = 1.6               # a lead within this of the path (m) ends it 1 m short of its rear bumper
LEAD_PATH_GAP = 1.0

GHOST_PRE = (190, 198, 212)           # lane-change ghost: preLaneChange
GHOST_GO = LAT_ACTIVE_LANE            # laneChangeStarting / laneChangeFinishing
GHOST_BLOCKED = (240, 84, 72)         # the blind spot on that side is set
CURVE_CANDIDATE = (138, 156, 184)     # curve tag: a candidate only
CURVE_CEILING = (222, 228, 240)       # a ceiling the planner is not using
CURVE_CONTROLLING = (246, 184, 64)    # = bem.CAUTION_LEAD: the curve ceiling is controlling
STOP_RED = (240, 64, 56)
HARD_BRAKE_HZ = 2.0
GHOST_OUTLINE = 0.12                  # m, the lane-change ghost's outline width
SHADOW_ALPHA = 70
EGO_BODY, EGO_GLASS = (238, 241, 247), (48, 58, 76)
EGO_LIGHT, EGO_BRAKE = (150, 34, 34), (255, 56, 44)   # the ego's tail lights: idle, braking
LEAD_GLASS, LEAD_LIGHT = (64, 72, 88), (176, 44, 44)
BRAKE_LIGHT_ACCEL = -1.2              # m/s^2: the ego's tail lights also light below this measured deceleration


@dataclass
class BirdsEyeLead:
  d_rel: float
  y: float            # right-positive, meters
  v_rel: float
  radar: bool = True


@dataclass(frozen=True)
class CurveMark:
  """A fresh curve-speed observation worth showing: the speed (ceiling, else candidate) and where it binds."""
  speed_mps: float
  distance: float
  controlling: bool = False
  ceiling: bool = False


@dataclass
class BirdsEyeScene:
  grid: np.ndarray = field(default_factory=lambda: bem.sample_grid())
  path: np.ndarray | None = None          # lateral offset per grid point, NaN where absent
  path_accel: np.ndarray | None = None
  path_curvature: np.ndarray | None = None  # signed 1/m per grid point (right-positive), NaN where absent
  lanes: list[np.ndarray] = field(default_factory=list)
  lane_probs: np.ndarray = field(default_factory=lambda: np.zeros(4))
  lane_stds: np.ndarray = field(default_factory=lambda: np.zeros(4))
  edges: list[np.ndarray] = field(default_factory=list)
  edge_stds: np.ndarray = field(default_factory=lambda: np.ones(2))
  # Per-point lateral std (XYZTData.yStd) of each lane line / road edge on the grid, None where not published.
  lane_ystds: list = field(default_factory=list)
  edge_ystds: list = field(default_factory=list)
  leads: list[BirdsEyeLead] = field(default_factory=list)
  model_leads: list = field(default_factory=list)  # bem.model_lead_tracks: (x, y) at t = 0, 2 .. 10 s
  steering_deg: float = 0.0
  left_blinker: bool = False
  right_blinker: bool = False
  left_blindspot: bool = False
  right_blindspot: bool = False
  lane_change_state: int = 0      # bem.lane_change_code: 0 off, 1 pre, 2 starting, 3 finishing
  lane_change_direction: int = 0  # -1 left, +1 right
  hard_brake: bool = False        # modelV2.meta.hardBrakePredicted
  should_stop: bool = False       # modelV2.action.shouldStop
  curve: CurveMark | None = None
  speed_limit_mps: float | None = None
  engaged: bool = False
  experimental: bool = False
  allow_throttle: bool = True
  lat_active: bool = False        # carControl.latActive
  # lane_feedback.direction(): -1 the correction pulls toward the left line (laneLines[1]), +1 toward the right
  # line (laneLines[2]), 0 none. Only that line turns blue, as in the camera view.
  lane_centering_direction: int = 0
  v_ego: float | None = None      # carState.vEgo: drives the speed-based zoom
  braking: bool = False           # carState.brakePressed, or decelerating harder than BRAKE_LIGHT_ACCEL
  draw_distance: float = MAX_DRAW_DISTANCE  # camera-view visibility: modelV2.position.x[-1] clipped
  lead_one_d: float | None = None  # radarState.leadOne.dRel while present: where the camera view cuts the path
  valid: bool = False


def _floats(value) -> np.ndarray:
  return np.asarray(list(value), dtype=np.float64)


def model_lines(model, grid):
  """Resampled path, lane lines and road edges from a modelV2 message (or a duck-typed stand-in)."""
  pos = model.position
  path = bem.resample_line(_floats(pos.x), _floats(pos.y), grid, extend_back=False)
  path[grid < 0] = np.nan
  accel_src = _floats(model.acceleration.x) if hasattr(model, 'acceleration') else np.empty(0)
  path_accel = None
  px = _floats(pos.x)
  if accel_src.size and px.size:
    n = min(accel_src.size, px.size)
    path_accel = bem.resample_line(px[:n], accel_src[:n], grid, extend_back=False)
  lanes = [bem.resample_line(_floats(line.x), _floats(line.y), grid) for line in list(model.laneLines)[:4]]
  edges = [bem.resample_line(_floats(edge.x), _floats(edge.y), grid) for edge in list(model.roadEdges)[:2]]
  probs = _floats(model.laneLineProbs)[:4]
  stds = _floats(model.roadEdgeStds)[:2]
  return path, path_accel, lanes, np.pad(probs, (0, 4 - probs.size)), edges, np.pad(stds, (0, 2 - stds.size), constant_values=1.0)


def _line_ystd(line, grid) -> np.ndarray | None:
  """XYZTData.yStd resampled on the grid like the line itself; None when not published (this fork's modeld fills
  laneLines/roadEdges without stds: fill_model_msg.py fill_xyzt(..) for them passes no y_std)."""
  try:
    std = _floats(line.yStd)
    xs = _floats(line.x)
  except (AttributeError, TypeError, ValueError):
    return None
  n = min(std.size, xs.size)
  if n < 2 or not (np.nan_to_num(std[:n]) > 0).any():
    return None
  return bem.resample_line(xs[:n], std[:n], grid)


@dataclass(frozen=True)
class ModelExtras:
  lane_stds: np.ndarray
  lane_ystds: list
  edge_ystds: list
  curvature: np.ndarray | None
  tracks: list
  lane_change_state: int
  lane_change_direction: int
  hard_brake: bool
  should_stop: bool


def model_extras(model, grid) -> ModelExtras:
  """Everything else the birds-eye view shows from a modelV2: lane line stds, path curvature, model lead tracks,
  lane-change intent, hardBrakePredicted and action.shouldStop. Missing fields read as absent."""
  try:
    stds = _floats(model.laneLineStds)[:4]
  except (AttributeError, TypeError, ValueError):
    stds = np.zeros(0)
  stds = np.pad(stds, (0, 4 - stds.size))
  profile = bem.path_curvature(model)
  curvature = bem.resample_line(*profile, grid, extend_back=False) if profile is not None else None
  meta = getattr(model, 'meta', None)
  action = getattr(model, 'action', None)
  lane_ystds = [_line_ystd(line, grid) for line in list(model.laneLines)[:4]]
  edge_ystds = [_line_ystd(edge, grid) for edge in list(model.roadEdges)[:2]]
  return ModelExtras(stds, lane_ystds, edge_ystds, curvature, bem.model_lead_tracks(getattr(model, 'leadsV3', []), MODEL_LEAD_PROB),
                     bem.lane_change_code(getattr(meta, 'laneChangeState', 0)),
                     bem.lane_change_side(getattr(meta, 'laneChangeDirection', 0)),
                     bool(getattr(meta, 'hardBrakePredicted', False)), bool(getattr(action, 'shouldStop', False)))


def camera_draw_distance(model) -> float:
  """How far the camera view draws lane lines, edges and path: modelV2.position.x[-1], clipped (model_renderer)."""
  xs = list(model.position.x)
  last = float(xs[-1]) if xs else math.nan
  return float(np.clip(last, MIN_DRAW_DISTANCE, MAX_DRAW_DISTANCE)) if math.isfinite(last) else MAX_DRAW_DISTANCE


def camera_path_end(lead_d_rel: float | None, draw_distance: float) -> float:
  """Path end with a radar leadOne present, the camera view's rule (model_renderer._update_model)."""
  if lead_d_rel is None:
    return draw_distance
  lead_d = lead_d_rel * 2.0
  return float(np.clip(lead_d - min(lead_d * 0.35, 10.0), 0.0, draw_distance))


def scene_leads(radar_state, model, tracks=None) -> list[BirdsEyeLead]:
  """Radar leads first, then each confident model lead (leadsV3 prob >= MODEL_LEAD_PROB) where no drawn lead is
  within 6 m / 2 m of it (the three entries are the lead at three probability horizons, usually one car).

  A radar (radard-fused) lead's lateral position is -yRel and nothing else: it is drawn where radard puts it,
  unclamped, so a lead changing lanes moves across the lane lines."""
  if tracks is None:
    tracks = bem.model_lead_tracks(getattr(model, 'leadsV3', []), MODEL_LEAD_PROB) if model is not None else []
  leads: list[BirdsEyeLead] = []
  if radar_state is not None:
    for lead in (radar_state.leadOne, radar_state.leadTwo):
      if lead.present and math.isfinite(lead.dRel) and lead.dRel > 0:
        # radarState yRel is left-positive.
        leads.append(BirdsEyeLead(float(lead.dRel), -float(lead.yRel), float(lead.vRel), radar=True))
  for xs, ys in tracks:
    d, y = float(xs[0]) - EGO_FRONT, float(ys[0])
    if d > 0 and not any(abs(o.d_rel - d) < 6.0 and abs(o.y - y) < 2.0 for o in leads):
      # leadsV3 speed is absolute; without ego speed here the closing rate is unknown.
      leads.append(BirdsEyeLead(d, y, math.nan, radar=False))
  return leads


class _DefaultText:
  """gui_app fonts; screens with their own bitmap fonts pass a drawer instead."""
  def measure(self, text: str, size: float) -> tuple[float, float]:
    from openpilot.system.ui.lib.application import FontWeight, gui_app
    from openpilot.system.ui.lib.text_measure import measure_text_cached
    measured = measure_text_cached(gui_app.font(FontWeight.SEMI_BOLD), text, int(size))
    return measured.x, measured.y

  def draw(self, text: str, size: float, x: float, y: float, color: rl.Color) -> None:
    from openpilot.system.ui.lib.application import FontWeight, gui_app
    rl.draw_text_ex(gui_app.font(FontWeight.SEMI_BOLD), text, rl.Vector2(x, y), size, 0, color)


def _rgba(rgb, alpha) -> tuple[int, int, int, int]:
  return int(rgb[0]), int(rgb[1]), int(rgb[2]), int(max(0, min(255, alpha)))


def _color(rgb, alpha) -> rl.Color:
  return rl.Color(*_rgba(rgb, alpha))


# ---- C-call lists -------------------------------------------------------------------------------
# An op list is a ready-made list of raw raylib calls, (function, *args), replayed by _run. Strip and fan ops hold
# their float32 points as a ctypes view of the array (which keeps it alive) and a prebuilt Color struct, so a
# replay converts nothing.

_call = getattr(operator, 'call', lambda function, *args: function(*args))


def _ccolor(rgba) -> object:
  r, g, b, a = rgba
  return _ccolor_cached(int(r), int(g), int(b), int(max(0, min(255, round(a)))))


@lru_cache(maxsize=1024)
def _ccolor_cached(r: int, g: int, b: int, a: int) -> object:
  """A Color struct, shared: ops only read it."""
  return _CColor(r, g, b, a)


def _points(pts: np.ndarray):
  arr = np.require(pts, dtype=np.float32, requirements=('C', 'W'))
  return (ctypes.c_float * arr.size).from_buffer(arr), arr.shape[0]


def _strip(pts: np.ndarray, rgba) -> tuple:
  """One DrawTriangleStrip call: interleaved [L0, R0, L1, R1, ..] points in one colour."""
  ptr, n = _points(pts)
  return _raw.DrawTriangleStrip, ptr, n, _ccolor(rgba)


def _fan(pts: np.ndarray, rgba) -> tuple:
  """One DrawTriangleFan call: a convex polygon in one colour."""
  ptr, n = _points(pts)
  return _raw.DrawTriangleFan, ptr, n, _ccolor(rgba)


def _strips(points: np.ndarray, runs, colors) -> list:
  """DrawTriangleStrip ops over row runs [(lo, hi)] of one ribbon's interleaved points (bem.strip_points), each in
  its own colour. All share one buffer: each op's pointer is an offset into it, and the buffer itself rides along
  as a trailing no-op entry so the pointers stay valid as long as the op list lives."""
  if not runs:
    return []
  arr = np.require(points, dtype=np.float32, requirements=('C', 'W'))
  base, vector2 = arr.ctypes.data, 2 * arr.itemsize
  ops: list = [(_raw.DrawTriangleStrip, base + 2 * lo * vector2, 2 * (hi - lo), _ccolor(rgba)) for (lo, hi), rgba in zip(runs, colors, strict=True)]
  ops.append((_keep, arr))
  return ops


def _keep(*_args) -> None:
  """The no-op an op list ends with to hold its shared point buffer."""


def _run(ops) -> None:
  if ops:
    any(starmap(_call, ops))  # every call returns None, so any() just drains the iterator in C


# Per-vertex quads, kept for the path's per-row colour modes only. Each quad is color0, vertex0, vertex1, color1,
# vertex2, vertex3, so a ribbon row fades along its length.
Layer = list
EMPTY: Layer = []


def _layer(cols, verts) -> Layer:
  """Quads from per-quad colors (Q, 8) [r0, g0, b0, a0, r1, g1, b1, a1] and vertices (Q, 8) [x0, y0 .. x3, y3].

  Built with C-level zips: no Python bytecode runs per vertex, here or in _emit."""
  cols = np.asarray(cols, dtype=np.int32).reshape(-1, 8)
  if not cols.shape[0]:
    return []
  c = cols.T.tolist()
  v = np.asarray(verts, dtype=np.float64).reshape(-1, 8).T.tolist()
  color, vertex = _raw.rlColor4ub, _raw.rlVertex2f

  def calls(function, *columns):  # (function, *args) per quad; repeat() is endless, so not strict
    return zip(repeat(function), *columns, strict=False)

  quads = zip(calls(color, *c[:4]), calls(vertex, *v[0:2]), calls(vertex, *v[2:4]),
              calls(color, *c[4:]), calls(vertex, *v[4:6]), calls(vertex, *v[6:8]), strict=True)
  return list(chain.from_iterable(quads))


def _emit(*layers: Layer) -> None:
  """All quads of the given layers in one rlBegin/rlEnd, through the raw C functions (no binding wrappers)."""
  if not any(layers):
    return
  # Bind the 1x1 white texture the way raylib's own shapes do: a quad batch opened right after text would
  # otherwise continue the font's draw call and sample its atlas.
  _raw.rlSetTexture(_raw.rlGetTextureIdDefault())
  _raw.rlBegin(rl.RL_QUADS)
  for layer in layers:
    any(starmap(_call, layer))
  _raw.rlEnd()
  _raw.rlSetTexture(0)


def _ribbon_quads(left: np.ndarray, right: np.ndarray, rows: np.ndarray, valid: np.ndarray) -> Layer:
  """Quads between consecutive valid rows of (N, 2) ribbon sides with one RGBA per row.

  Rows fully transparent at both ends are dropped."""
  rgba = np.clip(rows, 0, 255).astype(np.int32)
  verts = np.concatenate((left[:-1], right[:-1], right[1:], left[1:]), axis=-1)
  keep = valid[:-1] & valid[1:] & ((rgba[:-1, 3] > 0) | (rgba[1:, 3] > 0))
  cols = np.concatenate((rgba[:-1], rgba[1:]), axis=-1)
  return _layer(cols[keep], verts[keep])


# ---- the ground pass ----------------------------------------------------------------------------

def _matrix_array(matrix: _CMatrix) -> np.ndarray:
  return np.frombuffer(matrix, dtype=np.float32).reshape(4, 4).astype(np.float64)


def _begin_ground(view: bem.ViewParams) -> _CMatrix:
  """Make raylib project the ground plane: until _end_ground, every 2D shape drawn (in meters, x forward, y right)
  lands on the road as the camera sees it. Returns the modelview to restore.

  raylib applies a pushed matrix to each vertex on the CPU as an affine transform, which cannot carry the
  perspective divide, so the camera goes into the modelview (the GPU's matrix, as in raylib's own 3D mode) with
  the caller's pushed transform folded in ahead of it, and the CPU transform is identity for the pass. With no
  matrix pushed, rlgl's transform slot holds a copy of the modelview and is not applied, so it is left out."""
  _raw.rlDrawRenderBatchActive()
  saved = _raw.rlGetMatrixModelview()
  modelview, outer = _matrix_array(saved), _matrix_array(_raw.rlGetMatrixTransform())
  if np.allclose(outer, modelview):
    outer = np.eye(4)
  _raw.rlPushMatrix()
  _raw.rlLoadIdentity()
  camera = modelview @ outer @ bem.ground_matrix(view)
  _raw.rlSetMatrixModelview(_CMatrix(*camera.astype(np.float32).ravel().tolist()))
  return saved


def _end_ground(saved: _CMatrix) -> None:
  _raw.rlDrawRenderBatchActive()
  _raw.rlSetMatrixModelview(saved)
  _raw.rlPopMatrix()


# ---- car bodies ---------------------------------------------------------------------------------

SKIRT, BELT, ROOF_HEIGHT = 0.22, 0.86, 1.46   # meters above the road: underside, window line, roof
BODY, GLASS, LIGHT = 0, 1, 2                  # a face's part: which palette entry colours it
AMBIENT, DIFFUSE = 0.56, 0.5                  # flat shading: a face is AMBIENT + DIFFUSE * max(0, normal . light)
LIGHT_DIRECTION = np.array([-0.35, -0.3, 0.9]) / np.linalg.norm([-0.35, -0.3, 0.9])  # toward the light: above, behind, left


class CarMesh(NamedTuple):
  """A low-poly car centred at the origin (x forward, y right, z up), faces in painter order for a camera above:
  body walls, tail lights, deck, cabin walls, roof. Every face is a convex polygon."""
  vertices: np.ndarray   # (V, 3)
  parts: tuple           # per face: BODY, GLASS or LIGHT
  normals: np.ndarray    # (F, 3), outward
  centres: np.ndarray    # (F, 3)
  index: np.ndarray      # the faces' vertex indices, concatenated
  starts: tuple          # (F + 1,) offsets of each face in index


@lru_cache(maxsize=8)
def _car_mesh(length: float, width: float) -> CarMesh:
  sl, sw = length / 4.6, width / 1.9
  chamfer = 0.38 * sw
  hull = bem.car_outline(0.0, 0.0, 0.0, length, width, chamfer=chamfer)

  def ring(points, z):
    return np.column_stack((points, np.full(len(points), z)))
  cabin_base = np.array([(0.95 * sl, -0.84 * sw), (0.95 * sl, 0.84 * sw), (-1.6 * sl, 0.84 * sw), (-1.6 * sl, -0.84 * sw)])
  cabin_roof = np.array([(0.3 * sl, -0.7 * sw), (0.3 * sl, 0.7 * sw), (-1.05 * sl, 0.7 * sw), (-1.05 * sl, -0.7 * sw)])
  rear, outer = -length / 2 - 0.01, width / 2 - chamfer
  lights = [(rear, side * y, z) for side in (-1, 1) for y, z in ((outer, 0.54), (outer - 0.4 * sw, 0.54), (outer - 0.4 * sw, 0.76), (outer, 0.76))]
  vertices = np.vstack((ring(hull, SKIRT), ring(hull, BELT), ring(cabin_base, BELT), ring(cabin_roof, ROOF_HEIGHT), np.array(lights)))
  walls = [(i, (i + 1) % 8, 8 + (i + 1) % 8, 8 + i) for i in range(8)]
  cabin = [(16, 17, 21, 20), (17, 18, 22, 21), (18, 19, 23, 22), (19, 16, 20, 23)]
  faces = [*walls, (24, 25, 26, 27), (28, 29, 30, 31), tuple(range(8, 16)), *cabin, (20, 21, 22, 23)]
  parts = (BODY,) * 8 + (LIGHT,) * 2 + (BODY,) + (GLASS,) * 4 + (BODY,)
  normals, centres = [], []
  for face in faces:
    points = vertices[list(face)]
    normal = np.cross(points[1] - points[0], points[2] - points[0])
    normal /= np.linalg.norm(normal)
    centre = points.mean(axis=0)
    normals.append(normal if normal @ (centre - (0.0, 0.0, 0.6)) > 0 else -normal)
    centres.append(centre)
  starts = tuple(np.concatenate(([0], np.cumsum([len(face) for face in faces]))).tolist())
  return CarMesh(vertices, parts, np.array(normals), np.array(centres), np.concatenate(faces), starts)


class RoadDraw(NamedTuple):
  """Cached ops for one road geometry, in meters on the ground: the road fill, then edges + lane lines."""
  road: list
  lines: list


class PathDraw(NamedTuple):
  """Cached path ops in meters on the ground: constant-alpha strips (or per-vertex quads in a per-row colour
  mode), the curvature edge, and where the path ends (car frame x, y, heading) for the stop bar."""
  strips: list
  quads: Layer
  curve_edge: list
  end: tuple[float, float, float] | None


class Reference(NamedTuple):
  """The camera that line widths are sized against: the view's height and config at the zoom rounded up to
  WIDTH_REFERENCE_STEP, so road geometry is rebuilt only when the zoom crosses a step, never as it glides."""
  key: tuple
  view: bem.ViewParams


NO_ROAD = RoadDraw([], [])
NO_PATH = PathDraw([], EMPTY, [], None)
_UNSET = object()


class BirdsEyeRenderer(Widget):
  """3D chase view. Feed it with poll(sm, started_frame) or ingest(...), then render(rect).

  panel=True draws a rounded translucent card (overlay); panel=False fills the rect edge to edge.

  Geometry that only changes with the settings, the lateral state or a new model frame (road, lane lines, path)
  is built once in meters and replayed from cache whatever the camera does. Scene arrays are treated as
  immutable: ingest() replaces them, and the cache keys on their identity.
  """

  def __init__(self, config: bem.BirdsEyeConfig | None = None, text=None,
               source: Callable[[], tuple] | None = None):
    super().__init__()
    self.config = config or bem.BirdsEyeConfig()
    self.text = text or _DefaultText()
    # Returns (sm, started_frame); None means the caller ingests data itself.
    self.source = source
    self.panel = True
    self.opacity = 1.0
    self.road_style: dict = {}
    self.metric = True
    self.long_control = True
    self.parent_clip: rl.Rectangle | None = None
    self.clock = time.monotonic
    self.clock_ns = time.monotonic_ns  # the logMonoTime clock lane-centering freshness is judged against
    self.scene = BirdsEyeScene(grid=bem.sample_grid(bem.zoomed_config(self.config, bem.far_x_max(self.config))))
    self._throttle_blend = 1.0
    self._last_model_frame = -1
    self._rainbow = None
    self._lead_model = None
    self._cache: dict[str, tuple] = {}
    self._zoom = bem.ZoomFilter(self.config)
    self._zoom_clock: float | None = None
    self._labels: list[tuple[float, float]] = []  # last drawn label positions, for the jitter filter
    self._limit_frame: tuple[tuple, float | None] | None = None

  # ---- data -------------------------------------------------------------------------------------

  def ingest(self, model=None, radar_state=None, car_state=None, *, engaged=None, experimental=None,
             allow_throttle=None, lat_active=None, lane_centering_direction=None, curve=_UNSET, speed_limit=_UNSET) -> None:
    """Accept one observation. model=None keeps the last good road geometry.

    lane_centering_direction is lane_feedback.direction(): -1, 0 or +1 (the side whose line turns blue).
    curve (CurveMark or None) and speed_limit (m/s or None) replace the last value when given."""
    scene = self.scene
    tracks = None
    if model is not None:
      path, accel, lanes, probs, edges, stds = model_lines(model, scene.grid)
      scene.path = bem.smooth(scene.path, path, SMOOTHING) if scene.valid else path
      scene.path_accel = accel
      scene.lanes = [bem.smooth(old, new, SMOOTHING) for old, new in zip(scene.lanes, lanes, strict=False)] \
        if scene.valid and len(scene.lanes) == len(lanes) else lanes
      scene.edges = [bem.smooth(old, new, SMOOTHING) for old, new in zip(scene.edges, edges, strict=False)] \
        if scene.valid and len(scene.edges) == len(edges) else edges
      scene.lane_probs, scene.edge_stds = probs, stds
      scene.draw_distance = camera_draw_distance(model)
      scene.valid = bool(np.isfinite(path).any() or any(np.isfinite(lane).any() for lane in lanes))
      extras = model_extras(model, scene.grid)
      if not np.array_equal(extras.lane_stds, scene.lane_stds):
        scene.lane_stds = extras.lane_stds
      scene.path_curvature = extras.curvature
      scene.lane_ystds, scene.edge_ystds = extras.lane_ystds, extras.edge_ystds
      scene.model_leads = tracks = extras.tracks
      scene.lane_change_state, scene.lane_change_direction = extras.lane_change_state, extras.lane_change_direction
      scene.hard_brake, scene.should_stop = extras.hard_brake, extras.should_stop
      self._lead_model = model
    if radar_state is not None:
      one = radar_state.leadOne
      scene.lead_one_d = float(one.dRel) if one.present and math.isfinite(one.dRel) else None
    if radar_state is not None or model is not None:
      scene.leads = self._smooth_leads(scene.leads, scene_leads(radar_state, self._lead_model,
                                                                scene.model_leads if tracks is None else tracks))
    if car_state is not None:
      scene.steering_deg = float(car_state.steeringAngleDeg)
      scene.left_blinker, scene.right_blinker = bool(car_state.leftBlinker), bool(car_state.rightBlinker)
      scene.left_blindspot = bool(getattr(car_state, 'leftBlindspot', False))
      scene.right_blindspot = bool(getattr(car_state, 'rightBlindspot', False))
      v_ego = getattr(car_state, 'vEgo', None)
      scene.v_ego = float(v_ego) if v_ego is not None and math.isfinite(v_ego) else scene.v_ego
      a_ego = getattr(car_state, 'aEgo', None)
      scene.braking = bool(getattr(car_state, 'brakePressed', False)) or (
        isinstance(a_ego, (int, float)) and math.isfinite(a_ego) and a_ego < BRAKE_LIGHT_ACCEL)
    if engaged is not None:
      scene.engaged = bool(engaged)
    if experimental is not None:
      scene.experimental = bool(experimental)
    if allow_throttle is not None:
      scene.allow_throttle = bool(allow_throttle)
    if lat_active is not None:
      scene.lat_active = bool(lat_active)
    if lane_centering_direction is not None:
      scene.lane_centering_direction = int(np.sign(lane_centering_direction))
    if curve is not _UNSET:
      scene.curve = curve
    if speed_limit is not _UNSET:
      scene.speed_limit_mps = float(speed_limit) if speed_limit is not None and math.isfinite(speed_limit) and speed_limit > 0 else None

  @staticmethod
  def _smooth_leads(previous, current):
    out = []
    for lead in current:
      match = next((p for p in previous if p.radar == lead.radar and abs(p.d_rel - lead.d_rel) < 5 and abs(p.y - lead.y) < 1.5), None)
      if match is not None:
        lead = BirdsEyeLead(match.d_rel + (lead.d_rel - match.d_rel) * LEAD_SMOOTHING,
                            match.y + (lead.y - match.y) * LEAD_SMOOTHING, lead.v_rel, lead.radar)
      out.append(lead)
    return out

  def poll(self, sm, started_frame: int) -> None:
    """Read SubMaster services; road geometry changes only on a fresh modelV2."""
    def fresh(name):
      return bool(sm.valid.get(name, False) if hasattr(sm.valid, 'get') else sm.valid[name]) and sm.recv_frame[name] >= started_frame

    model = None
    if fresh('modelV2') and sm.updated['modelV2'] and sm.recv_frame['modelV2'] != self._last_model_frame:
      model = sm['modelV2']
      self._last_model_frame = sm.recv_frame['modelV2']
    radar = sm['radarState'] if fresh('radarState') else None
    car = sm['carState'] if fresh('carState') else None
    engaged = experimental = allow = None
    if fresh('selfdriveState'):
      engaged = sm['selfdriveState'].enabled
      experimental = sm['selfdriveState'].experimentalMode
    if fresh('longitudinalPlan'):
      allow = sm['longitudinalPlan'].allowThrottle or not self.long_control
    lat_active = bool(fresh('carControl') and sm['carControl'].latActive)
    # Blue only while the fork's own freshness and consistency rules say the correction applies, on its side.
    lane_dir = self._lane_centering_direction(sm, started_frame) if lat_active else 0
    now_ns = self.clock_ns()
    # Without a new modelV2 the road keeps its last good frame; leads, wheel and blinkers still follow.
    self.ingest(model, radar, car, engaged=engaged, experimental=experimental, allow_throttle=allow,
                lat_active=lat_active, lane_centering_direction=lane_dir,
                curve=read_curve(sm, now_ns, started_frame), speed_limit=self._read_speed_limit(sm, now_ns, started_frame))

  def _lane_centering_direction(self, sm, started_frame: int) -> int:
    try:
      from openpilot.starpilot.lateral.lane_feedback import direction
    except ImportError:
      return 0
    return direction(sm, self.clock_ns(), started_frame)

  def _read_speed_limit(self, sm, now_ns: int, started_frame: int) -> float | None:
    """The valid SLC speed limit (m/s) through the UI's own qualified readers; decoded once per slcState frame."""
    try:
      from openpilot.starpilot.ui.runtime_snapshot import display_message
      from openpilot.starpilot.ui.onroad_state import ObservationKind, speed_limit_from_message
    except ImportError:
      return None
    message = display_message(sm, 'slcState', now_ns, after_frame=started_frame)
    if message is None:
      return None
    frame = (int(sm.recv_frame['slcState']), id(message))
    if self._limit_frame is None or self._limit_frame[0] != frame:
      observation = speed_limit_from_message(message)
      value = observation.speed_limit_mps if observation.kind == ObservationKind.VALID else None
      self._limit_frame = (frame, float(value) if value is not None and math.isfinite(value) and value > 0 else None)
    return self._limit_frame[1]

  # ---- caching ----------------------------------------------------------------------------------

  def _cached(self, name: str, key, build, keep=None):
    """build() once per key. keep holds the objects whose ids are in the key, so those ids stay unique."""
    hit = self._cache.get(name)
    if hit is not None and hit[0] == key:
      return hit[1]
    value = build()
    self._cache[name] = (key, value, keep)
    return value

  # ---- drawing ----------------------------------------------------------------------------------

  def _render(self, rect: rl.Rectangle):
    if self.source is not None:
      sm, started_frame = self.source()
      self.poll(sm, started_frame)
    self.draw(rect)

  def view_config(self) -> bem.BirdsEyeConfig:
    """self.config zoomed for the current speed, widened to keep a radar leadOne on screen (filtered and quantized: see
    bem.ZoomFilter). Calling it advances the zoom filter by the time since the last call."""
    if self._zoom.cfg is not self.config:
      self._zoom = bem.ZoomFilter(self.config)
    now = self.clock()
    dt = 0.0 if self._zoom_clock is None else now - self._zoom_clock
    self._zoom_clock = now
    lead = self.scene.lead_one_d
    lead_front = lead + EGO_FRONT + LEAD_LENGTH if lead is not None else None
    return bem.zoomed_config(self.config, self._zoom.update(self.scene.v_ego, dt, lead_front))

  def _views(self, rect, cfg: bem.BirdsEyeConfig) -> tuple[bem.ViewParams, Reference]:
    """The camera for this frame and the reference that line widths are sized against.

    The camera follows the zoom filter's unquantized value, so it glides; only the matrix changes with it."""
    value = self._zoom.value
    x_max = cfg.x_max if value is None else round(min(value, bem.far_x_max(self.config)), 2)
    key = (float(rect.x), float(rect.y), float(rect.width), float(rect.height), x_max, self.config)
    view = self._cached('view', key, lambda: bem.view_params(rect, bem.zoomed_config(self.config, x_max)))
    return view, self._reference(rect.height, x_max)

  def _reference(self, height: float, x_max: float) -> Reference:
    step = math.ceil(x_max / WIDTH_REFERENCE_STEP - 1e-9) * WIDTH_REFERENCE_STEP
    key = (float(height), step, self.config)

    def build():
      square = SimpleNamespace(x=0.0, y=0.0, width=float(height), height=float(height))
      return Reference(key, bem.view_params(square, bem.zoomed_config(self.config, step)))
    return self._cached('reference', key, build)

  def draw(self, rect: rl.Rectangle) -> None:
    from openpilot.starpilot.ui import clip
    view, reference = self._views(rect, self.view_config())
    self._background(rect)
    clip.begin_scissor_mode(rect.x, rect.y, rect.width, rect.height)
    _raw.rlDisableBackfaceCulling()
    try:
      scene = self.scene
      leads = self._lead_poses(scene, view)
      saved = _begin_ground(view)
      try:
        if scene.valid:
          if scene.path is not None:
            target = 1.0 if scene.allow_throttle else 0.0
            self._throttle_blend += (target - self._throttle_blend) * 0.2
            if abs(target - self._throttle_blend) < 0.01:  # settle, so the cached path stops being rebuilt
              self._throttle_blend = target
          road = self._road_draw(scene, reference)
          _run(road.road)
          _run(road.lines)
          path = self._path_draw(scene, reference)
          _run(path.strips)
          _emit(path.quads)
          _run(path.curve_edge)
          _run(self._marks(scene, reference, path))
        _run(self._lane_change_ghost(scene))
        _run(self._shadows([pose for _, pose in leads]))
        self._ego_ground()
      finally:
        _end_ground(saved)
      if scene.valid:
        self._curve_label(scene, rect, view)
      self._leads(scene, rect, view, leads)
      self._ego(scene, view)
      self._speed_limit_sign(scene, rect)
    finally:
      rl.rl_draw_render_batch_active()
      _raw.rlEnableBackfaceCulling()
      if self.parent_clip is not None:
        clip.begin_scissor_mode(self.parent_clip.x, self.parent_clip.y, self.parent_clip.width, self.parent_clip.height)
      else:
        clip.end_scissor_mode()
    if self.panel:
      rl.draw_rectangle_rounded_lines_ex(rect, self._roundness(rect), 12, bem.GLASS_RIM_PX,
                                         rl.Color(*bem.GLASS_RIM[:3], int(bem.GLASS_RIM[3] * self.opacity)))

  def _roundness(self, rect) -> float:
    return min(1.0, 2 * PANEL_RADIUS / max(1.0, min(rect.width, rect.height)))

  def _background(self, rect) -> None:
    if self.panel:
      rl.draw_rectangle_rounded(rect, self._roundness(rect), 12, rl.Color(*PANEL_FILL[:3], int(PANEL_FILL[3] * self.opacity)))
    else:
      rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height), rl.Color(*BACKGROUND, 255))

  def _glass(self, rect, roundness: float, segments: int = 8) -> None:
    """A glass panel: a smoked tint under a bright hairline rim."""
    a = self.opacity
    rl.draw_rectangle_rounded(rect, roundness, segments, rl.Color(*bem.GLASS_FILL[:3], int(bem.GLASS_FILL[3] * a)))
    rl.draw_rectangle_rounded_lines_ex(rect, roundness, segments, bem.GLASS_RIM_PX, rl.Color(*bem.GLASS_RIM[:3], int(bem.GLASS_RIM[3] * a)))

  @staticmethod
  def _rows(rgb, alpha, n: int) -> np.ndarray:
    """(n, 4) per-row RGBA of one colour (rgb (3,) or (n, 3)) at one alpha."""
    rows = np.empty((n, 4))
    rows[:, :3] = rgb
    rows[:, 3] = alpha
    return rows

  @staticmethod
  def _sample(gradient: Gradient, t: np.ndarray, alpha_scale: float = 1.0) -> np.ndarray:
    """CPU sample of a shader Gradient (colors[0] nearest the car) at fractions t of the path's length."""
    stops = np.asarray(gradient.stops, dtype=np.float64)
    cols = np.array([(c.r, c.g, c.b, c.a) for c in gradient.colors], dtype=np.float64)
    out = np.stack([np.interp(t, stops, cols[:, i]) for i in range(4)], axis=1)
    out[:, 3] *= alpha_scale
    return out

  @staticmethod
  def _half_width(width_m, min_px, xs: np.ndarray, reference: Reference) -> np.ndarray:
    """Half the drawn width (m) of a ribbon at each row: its real width, or min_px pixels of the reference camera
    where that is wider (far away, where a painted line would otherwise shrink below a pixel and shimmer)."""
    return np.maximum(width_m, min_px * bem.meters_per_pixel(xs, reference.view)) / 2

  # ---- road and lines ---------------------------------------------------------------------------

  def _road_draw(self, scene, reference: Reference) -> RoadDraw:
    """Road, edges and lane lines; rebuilt only when the geometry, the draw distance, the width reference or a
    setting changes. The camera is not part of the key: the ops are in meters."""
    reach = round(scene.draw_distance * 2) / 2  # 0.5 m steps steady the cache key
    style = self.road_style
    arrays = (scene.grid, *scene.lanes, *scene.edges, scene.lane_probs, scene.lane_stds, scene.edge_stds,
              *scene.lane_ystds, *scene.edge_ystds)
    key = (reference.key, self.opacity, style.get('laneLines'), tuple(map(id, arrays)), reach,
           scene.lat_active, scene.lane_centering_direction)
    return self._cached('road', key, lambda: self._build_road(scene, reference, reach), keep=arrays)

  def _road_pair(self, scene) -> list:
    """(offsets, yStd) of the two lines bounding the road fill: the road edges, else the outer lane lines."""
    if len(scene.edges) == 2:
      return list(zip(scene.edges, self._ystds(scene.edge_ystds, 2), strict=True))
    lanes = self._ystds(scene.lane_ystds, 4)
    return [(scene.lanes[0], lanes[0]), (scene.lanes[3], lanes[3])] if len(scene.lanes) == 4 else []

  @staticmethod
  def _ystds(ystds, n) -> list:
    return [ystds[i] if i < len(ystds) else None for i in range(n)]

  @staticmethod
  def _lateral_rgb(scene, i: int):
    """Ego lane line i's color for the lateral state: blue on the side lane centering pulls toward (laneLines[1]
    for -1, laneLines[2] for +1, as in the camera view), green while lateral control is active, None (the normal
    lane color) otherwise or for the outer lines."""
    if not scene.lat_active or i not in (1, 2):
      return None
    direction = scene.lane_centering_direction
    if (i == 1 and direction < 0) or (i == 2 and direction > 0):
      return LANE_CENTERING_LANE
    return LAT_ACTIVE_LANE

  def _line_styles(self, scene) -> list[tuple]:
    """(offsets, width m, min width px, rgb, alpha, yStd) of each road edge then lane line worth drawing.

    A lane line's alpha follows its probability and, through bem.std_alpha, its laneLineStds entry."""
    from openpilot.starpilot.ui.road_colors import lane_color
    lines = []
    edge_ystds, lane_ystds = self._ystds(scene.edge_ystds, len(scene.edges)), self._ystds(scene.lane_ystds, len(scene.lanes))
    for i, edge in enumerate(scene.edges):
      std = scene.edge_stds[i] if i < len(scene.edge_stds) else 1.0
      alpha = float(np.clip(1.0 - std, 0.0, 1.0))
      if alpha >= 0.05:
        lines.append((edge, EDGE_WIDTH, EDGE_MIN_PX, (150, 158, 176), 230 * alpha * self.opacity, edge_ystds[i]))
    for i, lane in enumerate(scene.lanes):
      prob = float(scene.lane_probs[i]) if i < len(scene.lane_probs) else 0.0
      certainty = bem.std_alpha(float(scene.lane_stds[i])) if i < len(scene.lane_stds) else 1.0
      alpha = float(np.clip(prob, 0.0, 0.7)) / 0.7
      if alpha < 0.05:
        continue
      lateral = self._lateral_rgb(scene, i)
      if lateral is not None:  # the ego lane's own left and right lines
        lines.append((lane, LANE_WIDTH * 1.6, EGO_LANE_MIN_PX, lateral, 245 * alpha * certainty * self.opacity, lane_ystds[i]))
        continue
      default = _color((236, 240, 248), 235 * alpha)
      color = lane_color(self.road_style, False, default, prob)
      lines.append((lane, LANE_WIDTH, LANE_MIN_PX, (color.r, color.g, color.b),
                    (color.a if color is default else min(255, color.a / 0.7)) * certainty * self.opacity, lane_ystds[i]))
    return lines

  def _build_road(self, scene, reference: Reference, reach=MAX_DRAW_DISTANCE) -> RoadDraw:
    """The road fill, edges and lane lines as ground-plane strips in meters, each ending at its own distance
    (with SOFT_ENDS off, a hard cut there: one strip per unbroken line).

    Each line stops at min(reach, where its own yStd passes bem.LINE_STD_LIMIT, its data end) and fades out over its
    last bem.end_fade_length metres as a few constant-alpha strips (LINE_ALPHA_STEP), unless that soft end starts
    beyond the reference zoom's top edge, where it could not be seen."""
    grid = scene.grid
    start, top = self.config.x_min, reference.view.x_max if SOFT_ENDS else -math.inf
    road_ops: list = []
    pair = self._road_pair(scene)
    if pair:
      (a, a_std), (b, b_std) = pair
      end = min(reach, bem.std_end(grid, a_std), bem.std_end(grid, b_std))
      rows = bem.line_rows(grid, np.where(np.isfinite(b), a, np.nan), end, start, TAIL_STEP, top)
      if rows is not None:
        xs, ya, ok, end, fade = rows
        ok_b = np.isfinite(b)
        yb = np.interp(xs, grid[ok_b], b[ok_b]) if np.count_nonzero(ok_b) >= 2 else np.full(xs.shape, np.nan)
        road_ops = self._faded_strips(np.stack((xs, ya), axis=1), np.stack((xs, yb), axis=1), ok & np.isfinite(yb),
                                      bem.alpha_groups(bem.taper(xs, end, fade), LINE_ALPHA_STEP), ROAD, 215 * self.opacity)
    # Lines with the same rows (the usual case: one model frame, one end) share one ribbon pass.
    styles = self._line_styles(scene)
    rows = [bem.line_rows(grid, line[0], min(reach, bem.std_end(grid, line[5])), start, TAIL_STEP, top) for line in styles]
    batches: dict = {}
    for i, r in enumerate(rows):
      if r is not None:
        batches.setdefault((r[0].size, float(r[0][0]), r[3], r[4]), []).append(i)
    sides: dict = {}
    for members in batches.values():
      xs, _, _, end, fade = rows[members[0]]
      lateral = np.stack([rows[i][1] for i in members])
      ok = np.stack([rows[i][2] for i in members])
      centre = np.stack((np.broadcast_to(xs, lateral.shape), lateral), axis=-1)
      width_m, min_px = np.array([(styles[i][1], styles[i][2]) for i in members]).T
      half = np.maximum(width_m[:, None], min_px[:, None] * bem.meters_per_pixel(xs, reference.view)[None, :]) / 2
      left, right = bem.ribbon_sides(centre, half, ok)
      groups = bem.alpha_groups(bem.taper(xs, end, fade), LINE_ALPHA_STEP)
      for k, i in enumerate(members):
        sides[i] = (left[k], right[k], ok[k], groups)
    line_ops: list = []
    for i, line in enumerate(styles):
      if i in sides:
        left, right, ok, groups = sides[i]
        line_ops += self._faded_strips(left, right, ok, groups, line[3], line[4])
    return RoadDraw(road_ops, line_ops)

  @staticmethod
  def _faded_strips(left, right, valid, groups, rgb, alpha) -> list:
    """Strip ops for one ribbon: rows in the constant-alpha groups of its soft end (bem.alpha_groups), each split
    at gaps."""
    runs, colors = [], []
    for s, e, f in groups:
      if f * alpha < 1.0:
        continue
      for lo, hi in ([(s, e)] if valid[s:e].all() else [(s + lo, s + hi) for lo, hi in bem.valid_runs(valid[s:e])]):
        if hi - lo >= 2:
          runs.append((lo, hi))
          colors.append((*rgb, f * alpha))
    return _strips(bem.strip_points(left, right), runs, colors)

  # ---- path -------------------------------------------------------------------------------------

  def _path_end(self, scene, reach: float) -> float:
    """min(camera rule, reach, a lead's rear bumper - 1 m for each lead within LEAD_LATERAL_GATE of the path)."""
    end = min(camera_path_end(scene.lead_one_d, scene.draw_distance), reach)
    profile = self._path_profile(scene) if scene.leads else None
    for lead in scene.leads:
      x = lead.d_rel + EGO_FRONT
      lateral = lead.y if profile is None or x < profile[0][0] or x > profile[0][-1] else float(np.interp(x, profile[0], profile[1]))
      if abs(lead.y - lateral) < LEAD_LATERAL_GATE:
        end = min(end, x - LEAD_PATH_GAP)
    return end

  def _path_draw(self, scene, reference: Reference) -> PathDraw:
    from openpilot.starpilot.ui.road_colors import path_mode
    if scene.path is None:
      return NO_PATH
    end = round(self._path_end(scene, scene.draw_distance) * 4) / 4  # 0.25 m steps: a lead's jitter replays the cache
    style = self.road_style
    mode = path_mode(style)
    build = lambda: self._build_path(scene, reference, end)  # noqa: E731
    if scene.engaged and mode == 'rainbow':  # animated: rebuilt every frame
      return build()
    arrays = (scene.grid, scene.path, scene.path_accel, scene.path_curvature)
    key = (reference.key, self.opacity, tuple(map(id, arrays)), end, self._throttle_blend, scene.engaged, scene.experimental,
           mode, style.get('path'))
    return self._cached('path', key, build, keep=arrays)

  def _per_vertex_path(self, scene) -> bool:
    """True in the path colour modes that need a colour per row: rainbow, colour gradient, experimental accel tint."""
    from openpilot.starpilot.ui.road_colors import path_mode
    if not scene.engaged:
      return False
    mode = path_mode(self.road_style)
    if mode in ('rainbow', 'color'):
      return True
    accel = scene.path_accel
    return bool(scene.experimental and accel is not None and np.isfinite(accel).any())

  def _path_rgb_alpha(self, scene) -> tuple[tuple, float]:
    """(rgb, alpha) of a single-colour path."""
    a = self.opacity
    if not scene.engaged:
      return (205, 210, 222), 150 * a
    blend = self._throttle_blend
    rgb = tuple(n + (th - n) * blend for n, th in zip(NO_THROTTLE[0], THROTTLE[0], strict=True))
    return rgb, 210 * a

  def _build_path(self, scene, reference: Reference, end: float) -> PathDraw:
    """The path from just ahead of the ego to `end` as a ground-plane ribbon in meters, ending softly over its last
    bem.end_fade_length metres.

    Single-colour modes step that fade through constant-alpha strips (rows every TAIL_STEP in the fade, so each
    strip is within PATH_ALPHA_STEP / 2 of the smooth alpha); per-row colour modes keep per-vertex quads."""
    grid, ys_all = scene.grid, scene.path
    x0 = EGO_FRONT - 0.5
    runs = bem.valid_runs(np.isfinite(ys_all) & (grid >= x0) & (grid <= end))
    if not runs or runs[0][1] - runs[0][0] < 1:
      return NO_PATH
    lo, hi = runs[0]
    gx, gy = grid[lo:hi], ys_all[lo:hi]
    if hi < grid.size and math.isfinite(ys_all[hi]) and end > gx[-1] + 0.05:  # end between grid rows: cut exactly
      gx = np.append(gx, end)
      gy = np.append(gy, np.interp(end, grid[hi - 1:hi + 1], ys_all[hi - 1:hi + 1]))
    if gx.size < 2:
      return NO_PATH
    fade = bem.end_fade_length(gx[0], gx[-1]) if SOFT_ENDS else 0.0
    body = gx[gx < gx[-1] - fade - 1e-6]
    xs = np.concatenate((body, np.arange(gx[-1] - fade, gx[-1] - 1e-6, TAIL_STEP), [gx[-1]]))
    ys = np.interp(xs, gx, gy)
    ok = np.ones(xs.size, dtype=bool)
    left, right = bem.ribbon_sides(np.stack((xs, ys), axis=1), self._half_width(PATH_WIDTH, PATH_MIN_PX, xs, reference), ok)
    taper = bem.taper(xs, xs[-1], fade)
    strips: list = []
    quads: Layer = EMPTY
    if self._per_vertex_path(scene):
      accel = scene.path_accel
      accel_rows = np.interp(xs, grid, np.nan_to_num(accel)) if accel is not None else np.empty(0)
      rows = self._path_rows(scene, (xs - xs[0]) / max(1e-3, xs[-1] - xs[0]), accel_rows)
      rows[:, 3] *= taper
      quads = _ribbon_quads(left, right, rows, ok)
      factor = rows[:, 3] / 255
    else:
      rgb, alpha = self._path_rgb_alpha(scene)
      factor = taper * alpha / 255
      groups = [(s, e, a) for s, e, a in bem.alpha_groups(factor, PATH_ALPHA_STEP) if a * 255 >= 1.0]
      strips = _strips(bem.strip_points(left, right), [(s, e) for s, e, _ in groups], [(*rgb, 255 * a) for _, _, a in groups])
    edge = self._curve_edge(scene, xs, left, right, factor)
    heading = math.atan2(ys[-1] - ys[-2], xs[-1] - xs[-2])
    return PathDraw(strips, quads, edge, (float(xs[-1]), float(ys[-1]), heading))

  @staticmethod
  def _curve_edge(scene, xs, left, right, factor) -> list:
    """A brighter band along the path's outer edge where |curvature| >= bem.CURVE_MARK (right turn: the left edge)."""
    curvature = scene.path_curvature
    if curvature is None or not (np.abs(np.nan_to_num(curvature)) >= bem.CURVE_MARK).any():
      return []
    ok = np.isfinite(curvature)
    kappa = np.interp(xs, scene.grid[ok], scene.path_curvature[ok], left=0.0, right=0.0)
    ops = []
    for sign in (1, -1):
      mark = sign * kappa >= bem.CURVE_MARK
      outer, other = (left, right) if sign > 0 else (right, left)
      inner = outer + (other - outer) * 0.22
      for lo, hi in bem.valid_runs(mark):
        lo, hi = max(0, lo - 1), min(xs.size, hi + 1)  # a single marked row still gets a segment
        a = float(np.mean(factor[lo:hi]))
        if hi - lo >= 2 and a * 255 >= 4:
          ops.append(_strip(bem.strip_points(outer[lo:hi], inner[lo:hi]), (255, 255, 255, 120 * a)))
    return ops

  def _path_rows(self, scene, t: np.ndarray, accel: np.ndarray) -> np.ndarray:
    """Per-row path color along the fraction t of its length, chosen the way ModelRenderer picks the camera path's."""
    from openpilot.starpilot.ui.road_colors import path_mode, solid_gradient
    a = self.opacity
    if not scene.engaged:
      return self._rows((205, 210, 222), 150 * a, t.size)
    style = self.road_style
    mode = path_mode(style)
    if mode == 'rainbow':
      if self._rainbow is None:
        from openpilot.starpilot.ui.rainbow_path import RainbowPath
        self._rainbow = RainbowPath()
      self._rainbow.update(time.monotonic_ns(), None, source_alive=False)
      return self._sample(self._rainbow.gradient(), t, 1.8 * a)
    if mode == 'color':
      from openpilot.starpilot.ui.onroad_customization import ROAD_COLORS
      return self._sample(solid_gradient(style.get('path', ROAD_COLORS['path'])), t, a)
    if scene.experimental and accel.size == t.size and np.isfinite(accel).any():
      acc = np.nan_to_num(accel)
      hue = np.clip(60 + acc * 35, 0, 120) / 360
      sat = np.minimum(np.abs(acc * 1.5), 1)
      light = np.interp(sat, [0, 1], [0.95, 0.62])
      return self._rows(bem.hls_to_rgb(hue, light, sat) * 255, 215 * a, t.size)
    blend = self._throttle_blend
    rgb = np.array([n + (th - n) * blend for n, th in zip(NO_THROTTLE[0], THROTTLE[0], strict=True)])
    return self._rows(rgb, 210 * a, t.size)

  def _path_profile(self, scene):
    """(distances, offsets, slopes) of the path's finite points, or None: what lateral_at/heading_at derive per call."""
    def build():
      ok = np.isfinite(scene.path)
      if np.count_nonzero(ok) < 2:
        return None
      gx, gy = scene.grid[ok], scene.path[ok]
      return gx, gy, np.gradient(gy, gx)
    return self._cached('path_profile', (id(scene.path), id(scene.grid)), build, keep=(scene.path, scene.grid))

  def _heading_at(self, scene, x: float) -> float:
    """bem.heading_at on the cached path profile."""
    profile = self._path_profile(scene) if scene.path is not None else None
    return 0.0 if profile is None else math.atan(float(np.interp(x, profile[0], profile[2])))

  def _lateral_at(self, scene, x: float) -> float:
    profile = self._path_profile(scene) if scene.path is not None else None
    if profile is None or x < profile[0][0] or x > profile[0][-1]:
      return 0.0
    return float(np.interp(x, profile[0], profile[1]))

  def _road_profiles(self, scene) -> list[list]:
    """Slope profiles of the ego lane's two lines (if confident), then of the road edges; cached per model frame."""
    def build():
      lanes = [scene.lanes[i] for i in (1, 2) if i < len(scene.lanes) and i < len(scene.lane_probs) and
               scene.lane_probs[i] >= LANE_HEADING_PROB]
      return [[p for p in (bem.slope_profile(scene.grid, ys) for ys in group) if p is not None] for group in (lanes, scene.edges)]
    arrays = (scene.grid, scene.lane_probs, *scene.lanes, *scene.edges)
    return self._cached('road_profiles', tuple(map(id, arrays)), build, keep=arrays)

  def _road_heading(self, scene, x: float) -> float:
    """Road direction at x: mean slope of the ego lane lines, else of the road edges, else the planned path.

    The path bends with steering intent; the lane lines are the road, so a lead does not turn with the wheel."""
    for profiles in self._road_profiles(scene):
      heading = bem.mean_heading(profiles, x)
      if heading is not None:
        return heading
    return self._heading_at(scene, x)

  # ---- marks on the path ------------------------------------------------------------------------

  def _font_size(self, rect) -> float:
    """Label size: bem.label_font_size (the stream HUD's readout is a multiple of it)."""
    return bem.label_font_size(rect.height)

  @staticmethod
  def _bar(x: float, y: float, heading: float, along: float, across: float, rgba) -> tuple:
    """A bar across the path on the ground, as one fan op in meters."""
    return _fan(bem.segment_quad(x, y, heading, along, across), rgba)

  def _curve_place(self, scene, x_max: float) -> tuple[float, float, float]:
    x = float(np.clip(scene.curve.distance, EGO_FRONT + 3.0, x_max - 3.0))
    return x, self._lateral_at(scene, x), self._heading_at(scene, x)

  def _marks(self, scene, reference: Reference, path: PathDraw) -> list:
    """Ground ops. action.shouldStop: a red bar at the path's end. Curve speed (only with SHOW_CURVE_MARK): a bar at
    the binding distance (its speed tag is _curve_label). Bars are at least a few pixels deep."""
    a = self.opacity
    ops = []
    if scene.should_stop and path.end is not None:
      x, y, heading = path.end
      ops.append(self._bar(x, y, heading, max(0.6, 4.0 * float(bem.meters_per_pixel(x, reference.view))), PATH_WIDTH * 1.5,
                           _rgba(STOP_RED, 235 * a)))
    curve = scene.curve
    if curve is not None and SHOW_CURVE_MARK:
      x, y, heading = self._curve_place(scene, reference.view.x_max)
      rgb = CURVE_CONTROLLING if curve.controlling else CURVE_CEILING if curve.ceiling else CURVE_CANDIDATE
      ops.append(self._bar(x, y, heading, max(0.45, 3.0 * float(bem.meters_per_pixel(x, reference.view))), PATH_WIDTH * 1.9,
                           _rgba(rgb, 225 * a)))
    return ops

  def _curve_label(self, scene, rect, view) -> None:
    """The curve mark's speed tag, in pixels beside its bar (only with SHOW_CURVE_MARK)."""
    curve = scene.curve
    if curve is None or not SHOW_CURVE_MARK:
      return
    a = self.opacity
    x, y, heading = self._curve_place(scene, view.x_max)
    rgb = CURVE_CONTROLLING if curve.controlling else CURVE_CEILING if curve.ceiling else CURVE_CANDIDATE
    pts = bem.car_to_widget(bem.segment_quad(x, y, heading, 0.5, PATH_WIDTH * 1.9), rect, view=view)
    text = f"{bem.speed_text(curve.speed_mps, self.metric)} {'km/h' if self.metric else 'mph'}"
    size = self._font_size(rect) * SUBLABEL_SCALE
    w, h = self.text.measure(text, size)
    # Left of the bar: lead labels sit right of their cars, so the two rarely meet.
    tx = float(pts[:, 0].min()) - 8 - LABEL_PAD - w
    if tx - LABEL_PAD < rect.x + 4:
      tx = float(pts[:, 0].max()) + 8 + LABEL_PAD
    ty = float(pts[:, 1].mean()) - h / 2
    self._glass(rl.Rectangle(round(tx) - LABEL_PAD, round(ty) - LABEL_PAD / 2, w + 2 * LABEL_PAD, h + LABEL_PAD), 1.0)
    self.text.draw(text, size, round(tx), round(ty), _color(rgb, 255 * a))

  # ---- cars -------------------------------------------------------------------------------------

  @staticmethod
  def _cars(view, poses, mesh: CarMesh, palettes) -> tuple[list[list], np.ndarray]:
    """Car bodies: poses (C, 3) of (x, y, heading) on the road, one mesh for all, palettes[c] = the (rgb, alpha) of
    its BODY, GLASS and LIGHT faces. Returns one op list per car and every car's vertices in pixels (C, V, 2).

    The vertices go through the camera on the CPU (a few dozen points per car), and each face that looks toward
    the camera is drawn as one fan, flat-shaded by its normal. The mesh's face order is a painter's order for a
    camera above the cars and every face is convex, so no depth buffer is needed."""
    poses = np.asarray(poses, dtype=np.float64).reshape(-1, 3)
    count = poses.shape[0]
    cos_h, sin_h = np.cos(poses[:, 2])[:, None], np.sin(poses[:, 2])[:, None]

    def turn(local):  # (P, 3) car-local -> (C, P, 3) turned by each heading
      out = np.empty((count, local.shape[0], 3))
      out[..., 0] = local[None, :, 0] * cos_h - local[None, :, 1] * sin_h
      out[..., 1] = local[None, :, 0] * sin_h + local[None, :, 1] * cos_h
      out[..., 2] = local[None, :, 2]
      return out
    offset = np.concatenate((poses[:, :2], np.zeros((count, 1))), axis=1)[:, None, :]
    world = turn(mesh.vertices) + offset
    screen = bem.car_to_widget(world.reshape(-1, 3), None, view=view).reshape(count, -1, 2)
    normals = turn(mesh.normals)
    visible = (normals * (np.asarray(view.position) - (turn(mesh.centres) + offset))).sum(axis=-1) > 0
    shade = AMBIENT + DIFFUSE * np.clip(normals @ LIGHT_DIRECTION, 0.0, None)
    points = np.ascontiguousarray(screen[:, mesh.index], dtype=np.float32)
    base, vector2, per_car = points.ctypes.data, 2 * points.itemsize, mesh.index.size
    fan, starts, parts = _raw.DrawTriangleFan, mesh.starts, mesh.parts
    cars = []
    for c in range(count):
      ops: list = []
      palette = palettes[c]
      for f in np.flatnonzero(visible[c]).tolist():
        part = parts[f]
        rgb, alpha = palette[part]
        if alpha <= 0:
          continue
        k = 1.0 if part == LIGHT else float(shade[c, f])
        ops.append((fan, base + (c * per_car + starts[f]) * vector2, starts[f + 1] - starts[f],
                    _ccolor((min(255.0, rgb[0] * k), min(255.0, rgb[1] * k), min(255.0, rgb[2] * k), alpha))))
      ops.append((_keep, points))
      cars.append(ops)
    return cars, screen

  def _shadows(self, poses) -> list:
    """Ground ops: a soft dark footprint under each car at poses (x, y, heading), a little larger than its body."""
    alpha = SHADOW_ALPHA * self.opacity
    return [_fan(bem.car_outline(x, y, heading, LEAD_LENGTH + 0.5, LEAD_WIDTH + 0.35, chamfer=0.55), (0, 0, 0, alpha))
            for x, y, heading in poses]

  def _ego_ground(self) -> None:
    """The ego's shadow, drawn on the road during the ground pass (meters)."""
    _run(self._shadows([(EGO_FRONT - EGO_LENGTH / 2, 0.0, 0.0)]))

  def _lane_change_ghost(self, scene) -> list:
    """Ground ops. While the model's lane change is pending or under way, an outline of the ego car one lane over
    on that side: dim before the change starts, green once it does, red when that side's blind spot is set."""
    state, side = scene.lane_change_state, scene.lane_change_direction
    if not SHOW_LANE_CHANGE_GHOST or state == 0 or side == 0:
      return []
    width = round(bem.lane_width(scene.lanes, scene.grid, EGO_FRONT, side), 1)
    blocked = scene.left_blindspot if side < 0 else scene.right_blindspot
    rgb, alpha = (GHOST_BLOCKED, 0.6) if blocked else (GHOST_GO, 0.5) if state >= 2 else (GHOST_PRE, 0.35)

    def build():
      outline = bem.car_outline(EGO_FRONT - EGO_LENGTH / 2, side * width, 0.0, EGO_LENGTH, EGO_WIDTH, chamfer=0.38)
      return [_fan(outline, _rgba(rgb, 40 * alpha * self.opacity)),
              _strip(bem.ring_strip(outline, GHOST_OUTLINE), _rgba(rgb, 255 * alpha * self.opacity))]
    return self._cached('ghost', (self.opacity, side, width, rgb, alpha), build)

  def _lead_poses(self, scene, view) -> list[tuple]:
    """(lead, (x, y, heading) of its centre), far to near, for the leads whose rear bumper is inside the view."""
    leads = [lead for lead in sorted(scene.leads, key=lambda lead: -lead.d_rel) if lead.d_rel + EGO_FRONT <= view.x_max]
    poses = []
    for lead in leads:
      # The body's rear bumper is at dRel ahead of the ego front bumper: exactly the gap the label prints.
      cx = lead.d_rel + EGO_FRONT + LEAD_LENGTH / 2
      poses.append((lead, (cx, lead.y, self._road_heading(scene, cx))))
    return poses

  def _leads(self, scene, rect, view, leads=None) -> None:
    """Lead bodies, far to near so a nearer car covers a farther one, and the closest lead's distance label."""
    size = self._font_size(rect)
    leads = self._lead_poses(scene, view) if leads is None else leads
    if not leads:
      self._labels = []
      return
    palettes, rgbs = [], []
    for lead, _ in leads:
      rgb = bem.lead_color(lead.d_rel, lead.v_rel if math.isfinite(lead.v_rel) else 0.0)
      alpha = 255 * (1.0 if lead.radar else 0.7) * self.opacity
      palettes.append(((rgb, alpha), (LEAD_GLASS, alpha), (LEAD_LIGHT, alpha)))
      rgbs.append(rgb)
    cars, screens = self._cars(view, [pose for _, pose in leads], _car_mesh(LEAD_LENGTH, LEAD_WIDTH), palettes)
    previous, self._labels = self._labels, []
    for (lead, _), rgb, ops, screen in zip(leads, rgbs, cars, screens, strict=True):
      _run(ops)
      if lead is leads[-1][0]:  # only the closest lead carries a distance
        self._lead_label(lead, rgb, screen, rect, size, previous, scene.hard_brake)

  def _lead_label(self, lead, rgb, screen, rect, size, previous, hard_brake: bool) -> None:
    text = bem.distance_label(lead.d_rel, self.metric)
    box_w, box_h = self.text.measure(text, size)
    pad = LABEL_PAD
    x = float(screen[:, 0].max()) + 10 + pad
    if x + box_w + pad > rect.x + rect.width - 8:
      x = float(screen[:, 0].min()) - 10 - pad - box_w
    y = min(max(float(screen[:, 1].mean()) - box_h / 2, rect.y + 4 + pad), rect.y + rect.height - box_h - 4 - pad)
    x, y = self._label_position(previous, x, y)
    pill = rl.Rectangle(x - pad, y - pad, box_w + 2 * pad, box_h + 2 * pad)
    roundness = LABEL_ROUNDNESS
    self._glass(pill, roundness)
    if hard_brake:  # modelV2.meta.hardBrakePredicted: the nearest lead's pill border pulses red
      pulse = 0.5 + 0.5 * math.sin(2 * math.pi * HARD_BRAKE_HZ * self.clock())
      rl.draw_rectangle_rounded_lines_ex(pill, roundness, 8, 3.0, _color(STOP_RED, (110 + 145 * pulse) * self.opacity))
    self.text.draw(text, size, x, y, _color(rgb, 255 * self.opacity))

  def _label_position(self, previous: list[tuple[float, float]], x: float, y: float) -> tuple[float, float]:
    """Follow the car with a light filter, then snap to whole pixels, so a label neither jitters nor slides
    by sub-pixel amounts (each would redraw every glyph edge on the stream)."""
    near = min(previous, key=lambda p: math.hypot(p[0] - x, p[1] - y), default=None)
    if near is not None and math.hypot(near[0] - x, near[1] - y) < LABEL_SNAP_PX:
      x, y = near[0] + (x - near[0]) * LABEL_SMOOTHING, near[1] + (y - near[1]) * LABEL_SMOOTHING
    self._labels.append((x, y))
    return float(round(x)), float(round(y))

  def _ego(self, scene, view) -> None:
    """The ego body, its tail lights bright while it brakes. Steering is the HUD's wheel, not drawn on the car."""
    alpha = 255 * self.opacity
    palette = ((EGO_BODY, alpha), (EGO_GLASS, alpha), (EGO_BRAKE if scene.braking else EGO_LIGHT, alpha))
    cars, _ = self._cars(view, [(EGO_FRONT - EGO_LENGTH / 2, 0.0, 0.0)], _car_mesh(EGO_LENGTH, EGO_WIDTH), [palette])
    _run(cars[0])

  # ---- speed limit ------------------------------------------------------------------------------

  def _sign_top(self, rect) -> float:
    """Below the HUD's top-right turn-signal arrow when full-frame; a small margin in the overlay panel."""
    if self.panel:
      return rect.y + 12
    try:
      from openpilot.starpilot.system.starport import birds_eye_hud as hud
      reserve = hud.SIGNAL_MARGIN + hud.SIGNAL_HEIGHT
      frame_rows = 1080
    except ImportError:
      reserve, frame_rows = 288, 1080
    return rect.y + reserve * rect.height / frame_rows

  def _speed_limit_sign(self, scene, rect) -> None:
    """A compact speed-limit sign at the top right: a US white rectangle in mph, a red-ringed disc in km/h."""
    if scene.speed_limit_mps is None:
      return
    a = self.opacity
    value = bem.speed_text(scene.speed_limit_mps, self.metric)
    height = max(56.0, rect.height * 0.12)
    top = self._sign_top(rect)
    right = rect.x + rect.width - 16
    black = _color((16, 18, 24), 255 * a)
    if self.metric:
      r = height / 2
      centre = rl.Vector2(right - r, top + r)
      rl.draw_circle_v(centre, r, _color((255, 255, 255), 250 * a))
      rl.draw_ring(centre, r * 0.8, r, 0, 360, 36, _color((214, 40, 40), 255 * a))
      size = r * (0.95 if len(value) <= 2 else 0.75)
      w, h = self.text.measure(value, size)
      self.text.draw(value, size, round(centre.x - w / 2), round(centre.y - h / 2), black)
      return
    width = height * 0.8
    sign = rl.Rectangle(right - width, top, width, height)
    rl.draw_rectangle_rounded(sign, 0.18, 8, _color((255, 255, 255), 250 * a))
    rl.draw_rectangle_rounded_lines_ex(rl.Rectangle(sign.x + 3, sign.y + 3, width - 6, height - 6), 0.16, 8, 2.0, black)
    small, big = height * 0.17, height * 0.46
    y = top + height * 0.1
    for word in ('SPEED', 'LIMIT'):
      w, h = self.text.measure(word, small)
      self.text.draw(word, small, round(sign.x + (width - w) / 2), round(y), black)
      y += h * 0.95
    w, h = self.text.measure(value, big)
    self.text.draw(value, big, round(sign.x + (width - w) / 2), round(top + height * 0.93 - h), black)


def read_curve(sm, now_ns: int, started_frame: int) -> CurveMark | None:
  """A fresh slcState.curve with a candidate or a ceiling, through the UI's qualified reader (stale data rejected)."""
  try:
    from openpilot.starpilot.ui.runtime_snapshot import current_curve_message, curve_observation
  except ImportError:
    return None
  message = current_curve_message(sm, now_ns, after_frame=started_frame)
  observation = curve_observation(message, now_ns) if message is not None else None
  if observation is None:
    return None
  speed = observation.ceiling_mps if observation.ceiling_mps is not None else observation.candidate_mps
  if speed is None:
    return None
  return CurveMark(float(speed), float(observation.binding_distance), bool(observation.controlling),
                   observation.ceiling_mps is not None)


class BitmapText:
  """Adapter for StarPilot BitmapFonts (headless-safe), used by the projection and the preview."""
  def __init__(self, fonts, role=None):
    from openpilot.starpilot.ui.presentation import FontRole
    self.fonts = fonts
    self.role = FontRole.SEMI_BOLD if role is None else role

  def measure(self, text: str, size: float) -> tuple[float, float]:
    measured = self.fonts.measure(text, self.role, size)
    return measured.width, measured.height

  def draw(self, text: str, size: float, x: float, y: float, color: rl.Color) -> None:
    self.fonts.draw(text, self.role, size, x, y, color)
