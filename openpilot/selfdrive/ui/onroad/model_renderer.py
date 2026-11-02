import colorsys
import numpy as np
from openpilot.starpilot.ui.raylib import rl
from openpilot.cereal import messaging
from opendbc.car.structs import car
from dataclasses import dataclass, field
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.starpilot.ui.appearance_preferences import format_lead_info
from openpilot.starpilot.ui.rainbow_path import RainbowPath
from openpilot.starpilot.ui.road_colors import edge_gradient, lane_color, path_mode, solid_gradient
from openpilot.starpilot.ui.onroad_customization import ROAD_COLORS
from openpilot.starpilot.lateral.lane_feedback import BLUE, direction as lane_centering_direction
from openpilot.selfdrive.locationd.calibrationd import HEIGHT_INIT
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.shader_polygon import draw_polygon, Gradient
from openpilot.system.ui.widgets import Widget

CLIP_MARGIN = 500
MIN_DRAW_DISTANCE = 10.0
MAX_DRAW_DISTANCE = 100.0

THROTTLE_COLORS = [
  rl.Color(13, 248, 122, 102),   # HSLF(148/360, 0.94, 0.51, 0.4)
  rl.Color(114, 255, 92, 89),    # HSLF(112/360, 1.0, 0.68, 0.35)
  rl.Color(114, 255, 92, 0),     # HSLF(112/360, 1.0, 0.68, 0.0)
]

NO_THROTTLE_COLORS = [
  rl.Color(242, 242, 242, 102), # HSLF(148/360, 0.0, 0.95, 0.4)
  rl.Color(242, 242, 242, 89),  # HSLF(112/360, 0.0, 0.95, 0.35)
  rl.Color(242, 242, 242, 0),   # HSLF(112/360, 0.0, 0.95, 0.0)
]


@dataclass
class ModelPoints:
  raw_points: np.ndarray = field(default_factory=lambda: np.empty((0, 3), dtype=np.float32))
  projected_points: np.ndarray = field(default_factory=lambda: np.empty((0, 2), dtype=np.float32))


@dataclass
class LeadVehicle:
  glow: list[tuple[float, float]] = field(default_factory=list)
  chevron: list[tuple[float, float]] = field(default_factory=list)
  fill_alpha: int = 0


class ModelRenderer(Widget):
  def __init__(self):
    super().__init__()
    self._longitudinal_control = False
    self._experimental_mode = False
    self._blend_filter = FirstOrderFilter(1.0, 0.25, 1 / gui_app.target_fps)
    self._prev_allow_throttle = True
    self._default_path_gradient = None
    self._default_path_blend_factor = None
    self._lane_line_probs = np.zeros(4, dtype=np.float32)
    self._lane_centering_direction = 0
    self._road_edge_stds = np.zeros(2, dtype=np.float32)
    self._lead_vehicles = [LeadVehicle(), LeadVehicle()]
    # (show marker, LeadInfoMode, metric) from a screen with its own lead choices; None keeps the stock chevrons.
    self.lead_display: tuple[bool, int, bool | None] | None = None
    self._path_offset_z = HEIGHT_INIT[0]

    # Initialize ModelPoints objects
    self._path = ModelPoints()
    self._path_edges = []
    self._lane_lines = [ModelPoints() for _ in range(4)]
    self._road_edges = [ModelPoints() for _ in range(2)]
    self._acceleration_x = np.empty((0,), dtype=np.float32)

    # Transform matrix (3x3 for car space to screen space)
    self._car_space_transform = np.zeros((3, 3), dtype=np.float32)
    self._transform_dirty = True
    self._clip_region = None
    self._lane_projection_clip = None

    self._exp_gradient = Gradient(
      start=(0.0, 1.0),  # Bottom of path
      end=(0.0, 0.0),  # Top of path
      colors=[],
      stops=[],
    )
    self._rainbow_path = RainbowPath()
    self.road_style = {}

    # Get longitudinal control setting from car parameters
    if car_params := Params().get("CarParams"):
      cp = messaging.log_from_bytes(car_params, car.CarParams)
      self._longitudinal_control = cp.openpilotLongitudinalControl

  def set_transform(self, transform: np.ndarray):
    transform = transform.astype(np.float32)
    if not np.array_equal(transform, self._car_space_transform):
      self._car_space_transform = transform
      self._transform_dirty = True

  def _render(self, rect: rl.Rectangle):
    sm = ui_state.sm
    replay = getattr(ui_state, 'replay_clock', None) is not None
    if replay and any(not sm.seen[service] or not sm.valid[service] or not sm.alive[service]
                      for service in ('modelV2', 'extrinsicsCalibration')):
      return

    # Check if data is up-to-date
    if (sm.recv_frame["extrinsicsCalibration"] < ui_state.started_frame or
        sm.recv_frame["modelV2"] < ui_state.started_frame):
      return

    # Set up clipping region
    self._clip_region = rl.Rectangle(
      rect.x - CLIP_MARGIN, rect.y - CLIP_MARGIN, rect.width + 2 * CLIP_MARGIN, rect.height + 2 * CLIP_MARGIN
    )

    # Update state
    self._experimental_mode = sm['selfdriveState'].experimentalMode

    extrinsics_calibration = sm['extrinsicsCalibration']
    self._path_offset_z = extrinsics_calibration.height[0] if extrinsics_calibration.height else HEIGHT_INIT[0]

    if sm.updated['carParams']:
      self._longitudinal_control = sm['carParams'].openpilotLongitudinalControl

    model = sm['modelV2']
    radar_state = sm['radarState'] if sm.valid['radarState'] else None
    lead_one = radar_state.leadOne if radar_state else None
    lead_display = self.lead_display
    show_lead = self._longitudinal_control if lead_display is None else lead_display[0]
    render_lead_indicator = show_lead and radar_state is not None

    # Update model data when needed
    model_updated = sm.updated['modelV2']
    if model_updated or sm.updated['radarState'] or self._transform_dirty:
      if model_updated:
        self._update_raw_points(model)

      path_x_array = self._path.raw_points[:, 0]
      if path_x_array.size == 0:
        return

      self._update_model(lead_one, path_x_array, update_lane_geometry=model_updated or self._transform_dirty)
      if render_lead_indicator:
        self._update_leads(radar_state, path_x_array)
      self._transform_dirty = False

    # Draw elements
    self._draw_lane_lines()
    self._draw_path(sm)

    if render_lead_indicator and radar_state:
      self._draw_lead_indicator()
      if lead_display is not None and self._lead_vehicles[0].chevron:
        text = format_lead_info(lead_one, lead_display[1], lead_display[2])
        if text:
          self._draw_lead_info(self._lead_vehicles[0], text)

  def _update_raw_points(self, model):
    """Update raw 3D points from model data"""
    self._path.raw_points = np.array([model.position.x, model.position.y, model.position.z], dtype=np.float32).T

    # Older logs may append lane centers after the four boundaries.
    for points, lane_line in zip(self._lane_lines, model.laneLines, strict=False):
      points.raw_points = np.array([lane_line.x, lane_line.y, lane_line.z], dtype=np.float32).T

    for i, road_edge in enumerate(model.roadEdges):
      self._road_edges[i].raw_points = np.array([road_edge.x, road_edge.y, road_edge.z], dtype=np.float32).T

    self._lane_line_probs = np.array(model.laneLineProbs, dtype=np.float32)
    self._road_edge_stds = np.array(model.roadEdgeStds, dtype=np.float32)
    self._acceleration_x = np.array(model.acceleration.x, dtype=np.float32)

  def _update_leads(self, radar_state, path_x_array):
    """Update positions of lead vehicles"""
    self._lead_vehicles = [LeadVehicle(), LeadVehicle()]
    leads = [radar_state.leadOne, radar_state.leadTwo]

    for i, lead_data in enumerate(leads):
      if lead_data and lead_data.present:
        d_rel, y_rel, v_rel = lead_data.dRel, lead_data.yRel, lead_data.vRel
        idx = self._get_path_length_idx(path_x_array, d_rel)

        # Get z-coordinate from path at the lead vehicle position
        z = self._path.raw_points[idx, 2] if idx < len(self._path.raw_points) else 0.0
        point = self._map_to_screen(d_rel, -y_rel, z + self._path_offset_z)
        if point:
          self._lead_vehicles[i] = self._update_lead_vehicle(d_rel, v_rel, point, self._rect)

  def _update_model(self, lead, path_x_array, *, update_lane_geometry: bool = True):
    """Update model visualization data based on model message"""
    max_distance = np.clip(path_x_array[-1], MIN_DRAW_DISTANCE, MAX_DRAW_DISTANCE)
    clip = self._clip_region
    clip_key = (clip.x, clip.y, clip.width, clip.height)
    if update_lane_geometry or clip_key != getattr(self, "_lane_projection_clip", None):
      max_idx = self._get_path_length_idx(self._lane_lines[0].raw_points[:, 0], max_distance)

      # Radar changes the path's lead-distance clipping, not lane/edge geometry.
      lines = [*self._lane_lines, *self._road_edges]
      widths = [0.025 * float(prob) for prob in self._lane_line_probs] + [0.025] * len(self._road_edges)
      polygons = self._map_lines_to_polygons([line.raw_points for line in lines], widths, 0.0, max_idx, max_distance)
      for line, polygon in zip(lines, polygons, strict=True):
        line.projected_points = polygon
      self._lane_projection_clip = clip_key

    # Update path using raw points
    if lead and lead.present:
      lead_d = lead.dRel * 2.0
      max_distance = np.clip(lead_d - min(lead_d * 0.35, 10.0), 0.0, max_distance)

    max_idx = self._get_path_length_idx(path_x_array, max_distance)
    if "pathEdge" in getattr(self, "road_style", {}):
      left, right = self._path.raw_points.copy(), self._path.raw_points.copy()
      left[:, 1] -= 0.81
      right[:, 1] += 0.81
      polygons = self._map_lines_to_polygons([self._path.raw_points, left, right], [0.72, 0.09, 0.09],
                                           self._path_offset_z, max_idx, max_distance, allow_invert=False)
      self._path.projected_points, *self._path_edges = polygons
    else:
      self._path.projected_points = self._map_line_to_polygon(
        self._path.raw_points, 0.9, self._path_offset_z, max_idx, max_distance, allow_invert=False
      )
      self._path_edges = []

    self._update_experimental_gradient()

  def _update_experimental_gradient(self):
    """Pre-calculate experimental mode gradient colors"""
    if not self._experimental_mode:
      return

    max_len = min(len(self._path.projected_points) // 2, len(self._acceleration_x))

    segment_colors = []
    gradient_stops = []

    i = 0
    while i < max_len:
      # Some points (screen space) are out of frame (rect space)
      track_y = self._path.projected_points[i][1]
      if track_y < self._rect.y or track_y > (self._rect.y + self._rect.height):
        i += 1
        continue

      # Calculate color based on acceleration (0 is bottom, 1 is top)
      lin_grad_point = 1 - (track_y - self._rect.y) / self._rect.height

      # speed up: 120, slow down: 0
      path_hue = np.clip(60 + self._acceleration_x[i] * 35, 0, 120)

      saturation = min(abs(self._acceleration_x[i] * 1.5), 1)
      lightness = np.interp(saturation, [0.0, 1.0], [0.95, 0.62])
      alpha = np.interp(lin_grad_point, [0.75 / 2.0, 0.75], [0.4, 0.0])

      # Use HSL to RGB conversion
      color = self._hsla_to_color(path_hue / 360.0, saturation, lightness, alpha)

      gradient_stops.append(lin_grad_point)
      segment_colors.append(color)

      # Skip a point, unless next is last
      i += 1 + (1 if (i + 2) < max_len else 0)

    # Store the gradient in the path object
    self._exp_gradient = Gradient(
      start=(0.0, 1.0),  # Bottom of path
      end=(0.0, 0.0),  # Top of path
      colors=segment_colors,
      stops=gradient_stops,
    )

  def _update_lead_vehicle(self, d_rel, v_rel, point, rect):
    speed_buff, lead_buff = 10.0, 40.0

    # Calculate fill alpha
    fill_alpha = 0
    if d_rel < lead_buff:
      fill_alpha = 255 * (1.0 - (d_rel / lead_buff))
      if v_rel < 0:
        fill_alpha += 255 * (-1 * (v_rel / speed_buff))
      fill_alpha = min(fill_alpha, 255)

    # Calculate size and position
    sz = np.clip((25 * 30) / (d_rel / 3 + 30), 15.0, 30.0) * 2.35
    x = np.clip(point[0], 0.0, rect.width - sz / 2)
    y = min(point[1], rect.height - sz * 0.6)

    g_xo = sz / 5
    g_yo = sz / 10

    glow = [(x + (sz * 1.35) + g_xo, y + sz + g_yo), (x, y - g_yo), (x - (sz * 1.35) - g_xo, y + sz + g_yo)]
    chevron = [(x + (sz * 1.25), y + sz), (x, y), (x - (sz * 1.25), y + sz)]

    return LeadVehicle(glow=glow, chevron=chevron, fill_alpha=int(fill_alpha))

  def _draw_lane_lines(self):
    """Draw lane lines and road edges"""
    self._lane_centering_direction = lane_centering_direction(ui_state.sm, ui_state.display_time_ns(), ui_state.started_frame)
    for i, lane_line in enumerate(self._lane_lines):
      if lane_line.projected_points.size == 0:
        continue

      alpha = np.clip(self._lane_line_probs[i], 0.0, 0.7)
      color = lane_color(getattr(self, "road_style", {}), False, rl.Color(255, 255, 255, int(alpha * 255)), alpha)
      if (i == 1 and self._lane_centering_direction < 0) or (i == 2 and self._lane_centering_direction > 0):
        color = rl.Color(*BLUE, int(alpha * 255))
      draw_polygon(self._rect, lane_line.projected_points, color)

    for i, road_edge in enumerate(self._road_edges):
      if road_edge.projected_points.size == 0:
        continue

      alpha = np.clip(1.0 - self._road_edge_stds[i], 0.0, 1.0)
      color = rl.Color(255, 0, 0, int(alpha * 255))
      draw_polygon(self._rect, road_edge.projected_points, color)

  def _draw_path(self, sm):
    """Draw path with dynamic coloring based on mode and throttle state."""
    if not self._path.projected_points.size:
      return

    replay = getattr(ui_state, 'replay_clock', None) is not None
    plan_known = not replay or (sm.seen['longitudinalPlan'] and sm.valid['longitudinalPlan'] and sm.alive['longitudinalPlan'])
    if plan_known:
      allow_throttle = sm['longitudinalPlan'].allowThrottle or not self._longitudinal_control
      self._blend_filter.update(int(allow_throttle))

    now_ns = ui_state.display_time_ns()
    style = getattr(self, "road_style", {})
    if "pathEdge" in style:
      for edge in getattr(self, "_path_edges", []):
        if edge.size:
          draw_polygon(self._rect, edge, gradient=edge_gradient(style["pathEdge"]))
    mode = path_mode(style)
    if mode == "rainbow":
      car_fresh = bool(sm.valid.get('carState', False) and sm.alive.get('carState', False) and
                       sm.recv_frame['carState'] >= ui_state.started_frame)
      speed = float(sm['carState'].vEgo) if car_fresh and sm.updated['carState'] else None
      self._rainbow_path.update(now_ns, speed, source_alive=car_fresh)
      draw_polygon(self._rect, self._path.projected_points, gradient=self._rainbow_path.gradient())
      return
    if mode == "color":
      draw_polygon(self._rect, self._path.projected_points, gradient=solid_gradient(style.get("path", ROAD_COLORS["path"])))
      return

    if not self._experimental_mode and not plan_known:
      draw_polygon(self._rect, self._path.projected_points, rl.Color(255, 255, 255, 30))
      return

    if self._experimental_mode:
      # Draw with acceleration coloring
      if len(self._exp_gradient.colors) > 1:
        draw_polygon(self._rect, self._path.projected_points, gradient=self._exp_gradient)
      else:
        draw_polygon(self._rect, self._path.projected_points, rl.Color(255, 255, 255, 30))
    else:
      # Blend throttle/no throttle colors based on transition
      blend_factor = round(self._blend_filter.x * 100) / 100
      if blend_factor != getattr(self, "_default_path_blend_factor", None):
        self._default_path_gradient = Gradient(
          start=(0.0, 1.0),  # Bottom of path
          end=(0.0, 0.0),  # Top of path
          colors=self._blend_colors(NO_THROTTLE_COLORS, THROTTLE_COLORS, blend_factor),
          stops=[0.0, 0.5, 1.0],
        )
        self._default_path_blend_factor = blend_factor
      gradient = self._default_path_gradient
      draw_polygon(self._rect, self._path.projected_points, gradient=gradient)

  def _draw_lead_indicator(self):
    # Draw lead vehicles if available
    for lead in self._lead_vehicles:
      if not lead.glow or not lead.chevron:
        continue

      rl.draw_triangle_fan(lead.glow, len(lead.glow), rl.Color(218, 202, 37, 255))
      rl.draw_triangle_fan(lead.chevron, len(lead.chevron), rl.Color(201, 34, 49, lead.fill_alpha))

  def _draw_lead_info(self, lead: LeadVehicle, text: str) -> None:
    # Centered just above the chevron's glow, kept on screen.
    font = gui_app.font(FontWeight.SEMI_BOLD)
    size = measure_text_cached(font, text, 48)
    tip_x, tip_y = lead.glow[1]
    x = min(max(tip_x - size.x / 2, self._rect.x), self._rect.x + self._rect.width - size.x)
    y = max(tip_y - size.y - 12, self._rect.y)
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
      rl.draw_text_ex(font, text, rl.Vector2(x + dx, y + dy), 48, 0, rl.BLACK)
    rl.draw_text_ex(font, text, rl.Vector2(x, y), 48, 0, rl.WHITE)

  @staticmethod
  def _get_path_length_idx(pos_x_array: np.ndarray, path_distance: float) -> int:
    """Get the index corresponding to the given path distance"""
    if len(pos_x_array) == 0:
      return 0
    indices = np.where(pos_x_array <= path_distance)[0]
    return indices[-1] if indices.size > 0 else 0

  def _map_to_screen(self, in_x, in_y, in_z):
    """Project a point in car space to screen space"""
    input_pt = np.array([in_x, in_y, in_z])
    pt = self._car_space_transform @ input_pt

    if abs(pt[2]) < 1e-6:
      return None

    x, y = pt[0] / pt[2], pt[1] / pt[2]

    clip = self._clip_region
    if not (clip.x <= x <= clip.x + clip.width and clip.y <= y <= clip.y + clip.height):
      return None

    return (x, y)

  def _map_lines_to_polygons(self, lines: list[np.ndarray], widths: list[float], z_off: float, max_idx: int,
                             max_distance: float, allow_invert: bool = True) -> list[np.ndarray]:
    if len(lines) != len(widths):
      raise ValueError("line and width counts differ")
    if not lines:
      return []

    prepared = []
    for line in lines:
      if line.shape[0] == 0:
        prepared.append(np.empty((0, 3), dtype=np.float32))
        continue
      points = line[:max_idx + 1]
      if 0 < max_idx < line.shape[0] - 1:
        p0, p1 = line[max_idx], line[max_idx + 1]
        x0, x1 = float(p0[0]), float(p1[0])
        if x1 > x0:
          # Two increasing endpoints: plain arithmetic, the same result as np.interp without its temporaries.
          t = min(1.0, max(0.0, (float(max_distance) - x0) / (x1 - x0)))
          interp_y = float(p0[1]) + t * (float(p1[1]) - float(p0[1]))
          interp_z = float(p0[2]) + t * (float(p1[2]) - float(p0[2]))
        else:
          interp_y = np.interp(max_distance, [p0[0], p1[0]], [p0[1], p1[1]])
          interp_z = np.interp(max_distance, [p0[0], p1[0]], [p0[2], p1[2]])
        interp_point = np.array([max_distance, interp_y, interp_z], dtype=points.dtype)
        points = np.concatenate((points, interp_point[None, :]), axis=0)
      prepared.append(points[points[:, 0] >= 0])

    counts = [len(points) for points in prepared]
    total = sum(counts)
    if not total:
      return [np.empty((0, 2), dtype=np.float32) for _ in lines]

    offsets = np.zeros((2, total, 3), dtype=np.float32)
    offsets[0, :, 1] = -np.repeat(widths, counts)
    offsets[1, :, 1] = -offsets[0, :, 1]
    offsets[:, :, 2] = z_off
    points_3d = np.concatenate(prepared)[None, :, :] + offsets
    proj = (self._car_space_transform @ points_3d.reshape(2 * total, 3).T).reshape(3, 2, total)
    valid = (np.abs(proj[2, 0]) >= 1e-6) & (np.abs(proj[2, 1]) >= 1e-6)
    left = np.zeros((2, total), dtype=np.float32)
    right = np.zeros((2, total), dtype=np.float32)
    np.divide(proj[:2, 0], proj[2, 0], out=left, where=valid)
    np.divide(proj[:2, 1], proj[2, 1], out=right, where=valid)

    clip = self._clip_region
    visible = (valid &
               (left[0] >= clip.x) & (left[0] <= clip.x + clip.width) &
               (left[1] >= clip.y) & (left[1] <= clip.y + clip.height) &
               (right[0] >= clip.x) & (right[0] <= clip.x + clip.width) &
               (right[1] >= clip.y) & (right[1] <= clip.y + clip.height))
    polygons = []
    start = 0
    for count in counts:
      end = start + count
      mask = visible[start:end]
      if not np.any(mask):
        polygons.append(np.empty((0, 2), dtype=np.float32))
      else:
        left_line = left[:, start:end][:, mask]
        right_line = right[:, start:end][:, mask]
        if not allow_invert and left_line.shape[1] > 1:
          keep = left_line[1] == np.minimum.accumulate(left_line[1])
          left_line = left_line[:, keep]
          right_line = right_line[:, keep]
        polygons.append(np.vstack((left_line.T, right_line[:, ::-1].T)).astype(np.float32))
      start = end
    return polygons

  def _map_line_to_polygon(self, line: np.ndarray, y_off: float, z_off: float, max_idx: int, max_distance: float, allow_invert: bool = True) -> np.ndarray:
    """Convert one 3D line to a 2D polygon for rendering."""
    return self._map_lines_to_polygons([line], [y_off], z_off, max_idx, max_distance, allow_invert)[0]

  @staticmethod
  def _hsla_to_color(h, s, l, a):
    rgb = colorsys.hls_to_rgb(h, l, s)
    return rl.Color(
      int(rgb[0] * 255),
      int(rgb[1] * 255),
      int(rgb[2] * 255),
      int(a * 255)
    )

  @staticmethod
  def _blend_colors(begin_colors, end_colors, t):
    if t >= 1.0:
      return end_colors
    if t <= 0.0:
      return begin_colors

    inv_t = 1.0 - t
    return [rl.Color(
      int(inv_t * start.r + t * end.r),
      int(inv_t * start.g + t * end.g),
      int(inv_t * start.b + t * end.b),
      int(inv_t * start.a + t * end.a)
    ) for start, end in zip(begin_colors, end_colors, strict=True)]
