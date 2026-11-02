"""Dom's perspective CEM road gauge, driven only by current UI observations.

Geometry and animation preserve the original AetherGauge. This renderer never
reads IPC, settings, or control globals and never caches a missing CEM receipt.
"""
from dataclasses import dataclass
from enum import Enum
import math

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.onroad_conditional import reason, status
from openpilot.starpilot.ui.onroad_curve import controlling, glow_color, intensity
from openpilot.starpilot.ui.onroad_customization import CEM_GAUGE_FRAME, CEM_GAUGE_INSET, placement
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadState
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole

SCALE = 1.5


X_MIN = 3.0
X_MAX = 60.0
PATH_Y_FAR_SCALE = 1800.0
PERSPECTIVE_GAIN = 0.35
PERSPECTIVE_MAX_OFFSET = 26.0 * SCALE
PERSPECTIVE_EXPONENT = 1.8

ROAD_HEIGHT = 80.0 * SCALE
ROAD_SEGMENTS = 24
ROAD_W_BOTTOM = 32.0 * SCALE
ROAD_W_TOP = 18.0 * SCALE
ROAD_THICKNESS = 4.0 * SCALE
ROAD_HALF_SIZE = 40.0 * SCALE
ROAD_EDGE_INSET = 2.0 * SCALE
FILL_ALPHA = 90

STOP_DISTANCE_MAX = 60.0

CHEVRON_COUNT = 6
CHEVRON_SPACING = 0.3
CHEVRON_STEP = 0.05



COLOR_FORCE_STOP = rl.Color(255, 30, 60, 255)
COLOR_LEAD_STOPPED = rl.Color(255, 60, 60, 255)
COLOR_LEAD_SLOWER = rl.Color(255, 191, 0, 255)
COLOR_SHADOW = rl.Color(0, 0, 0, 100)
COLOR_STOP_LINE_GLOW = rl.Color(255, 30, 60, 255)
COLOR_STOP_LINE_CORE = rl.Color(255, 200, 200, 255)
COLOR_CEM_SPEED = rl.Color(112, 192, 216, 255)
COLOR_REDUCTION = rl.Color(255, 191, 0, 220)


def _channels(color):
  """Extract (r, g, b, a) from either an rl.Color struct or a 4-tuple."""
  if hasattr(color, "r"):
    return color.r, color.g, color.b, color.a
  return color[0], color[1], color[2], color[3]

def _with_alpha(color, a: int) -> rl.Color:
  r, g, b, _ = _channels(color)
  return rl.Color(r, g, b, max(0, min(255, a)))

def _fade(color, alpha: float) -> rl.Color:
  r, g, b, a = _channels(color)
  return rl.Color(r, g, b, max(0, min(255, int(a * alpha))))

def _lerp_color(a, b, t: float) -> rl.Color:
  t = max(0.0, min(1.0, t))
  ar, ag, ab, _ = _channels(a)
  br, bg, bb, _ = _channels(b)
  return rl.Color(
    ar + int((br - ar) * t),
    ag + int((bg - ag) * t),
    ab + int((bb - ab) * t),
    255,
  )

def _pulse(freq: float) -> float:
  return 0.5 + 0.5 * math.sin(rl.get_time() * freq)

def _get_road_height(data: 'AetherGaugeData | None') -> float:
  is_curve = data.indicator_type == IndicatorType.ROAD_CURVE if data else False
  return ROAD_HEIGHT if is_curve else (45.0 * SCALE)

def _road_xy(distance: float, icx: float, bottom: float, data: 'AetherGaugeData', road_h: float | None = None) -> tuple[float, float, float]:
  t = max(0.0, min(1.0, _get_t_from_x(distance)))
  offset = _get_perspective_offset(t, data)
  h = road_h if road_h is not None else _get_road_height(data)
  return t, icx + offset, bottom - t * h

def _get_t_from_x(x: float) -> float:
  x_clamped = max(X_MIN, min(X_MAX, x))
  return (1.0 - X_MIN / x_clamped) / (1.0 - X_MIN / X_MAX)

def _get_perspective_offset(t: float, data: 'AetherGaugeData | None') -> float:
  curvature = 0.0
  if data and data.indicator_type == IndicatorType.ROAD_CURVE:
    curvature = data.indicator_value

  path_y_far = PATH_Y_FAR_SCALE * curvature
  max_offset_top = math.tanh(path_y_far * PERSPECTIVE_GAIN) * PERSPECTIVE_MAX_OFFSET
  return max_offset_top * (t ** PERSPECTIVE_EXPONENT)

def _draw_text_with_shadow(fonts: BitmapFonts, role: FontRole, text: str, pos: rl.Vector2, size: int,
                           color: rl.Color, alpha: float = 1.0):
  for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
    fonts.draw(text, role, size, pos.x + dx, pos.y + dy, _fade(rl.BLACK, alpha))
  fonts.draw(text, role, size, pos.x, pos.y, _fade(color, alpha))


class IndicatorType(Enum):
  ROAD_CURVE = "road_curve"
  STOP_LIGHT = "stop_light"
  LEAD = "lead"


@dataclass(frozen=True)
class AetherGaugeData:
  text: str
  unit: str = ""
  color: rl.Color = rl.WHITE
  indicator_type: IndicatorType = IndicatorType.ROAD_CURVE
  indicator_value: float = 0.0
  indicator_extra: str = ""
  reduction_text: str = ""
  is_numeric: bool = False


def gauge_data(state: OnroadState, *, lead_stopped: bool = False) -> AetherGaugeData | None:
  """Use the selected reason; never infer a CEM condition from raw geometry."""
  if (state.alert.size == AlertSize.FULL or state.longitudinal_overridden or
      state.appearance.camera_view in (CameraViewChoice.DRIVER, CameraViewChoice.NONE) or state.reverse_driver_camera):
    return None
  current = reason(state) if status(state) is not None else None
  if current == 'cem_stop':
    # The current receipt has no committed distance. Preserve the light/road
    # graphic without claiming a numeric distance from an unrelated model path.
    return AetherGaugeData('STOP', color=COLOR_FORCE_STOP, indicator_type=IndicatorType.STOP_LIGHT,
                           indicator_extra='red')
  curve = state.curve
  if controlling(state) or current == 'cem_curve':
    speed = state.speed_mps
    target = curve.ceiling_mps if controlling(state) and curve is not None else None
    shown = min(speed, target) if speed is not None and target is not None else speed
    curvature = curve.road_curvature if curve is not None else None
    preview = state.visual_preview
    if preview is not None and preview.curve_curvature is not None:
      curvature = preview.curve_curvature
    conversion = 3.6 if state.metric else 2.2369362921
    reduction = (round((state.cruise_kph / 3.6 - target) * conversion)
                 if target is not None and state.cruise_kph is not None and state.cruise_kph / 3.6 > target else 0)
    return AetherGaugeData(str(round(shown * conversion)) if shown is not None else 'CURVE',
                           unit=('km/h' if state.metric else 'mph') if shown is not None else '',
                           color=glow_color(intensity(curvature)) if curvature is not None else COLOR_CEM_SPEED,
                           indicator_type=IndicatorType.ROAD_CURVE, indicator_value=curvature or 0.0,
                           reduction_text=f'-{reduction}' if reduction > 0 else '', is_numeric=shown is not None)
  if current in ('cem_lead', 'ccm_lead'):
    lead = state.conditional_lead
    # Only current radar metrics may classify the lead as stopped/slower.
    stopped = lead is not None and lead.speed_mps < (1.2 if lead_stopped else 0.8)
    return AetherGaugeData('STOPPED' if stopped else 'SLOW' if lead is not None else 'LEAD',
                           color=COLOR_LEAD_STOPPED if stopped else COLOR_LEAD_SLOWER,
                           indicator_type=IndicatorType.LEAD, indicator_value=lead.distance_m if lead is not None else 0.0,
                           indicator_extra='stopped' if stopped else 'slower' if lead is not None else '')
  return None


class AetherGauge:
  def __init__(self, fonts: BitmapFonts):
    self.fonts = fonts
    self._chevron_accum = 0.0
    self._lead_chevron_accum = 0.0
    self._road_h_filter = FirstOrderFilter(ROAD_HEIGHT, 0.06, 1 / 60)
    self._current_road_h = ROAD_HEIGHT
    self._speed_mps = 0.0
    self._lead_stopped = False
    self._drive_frame = None

  def render(self, state: OnroadState) -> None:
    saved = placement(state.customization, 'large', 'cem_gauge')
    if state.drive_frame != self._drive_frame:
      self._road_h_filter.x = ROAD_HEIGHT
      self._chevron_accum = self._lead_chevron_accum = 0.0
      self._lead_stopped = False
    self._drive_frame = state.drive_frame
    data = gauge_data(state, lead_stopped=self._lead_stopped) if saved['enabled'] else None
    if data is None:
      self._lead_stopped = False
      self._road_h_filter.x = ROAD_HEIGHT
      return
    self._lead_stopped = data.indicator_extra == 'stopped'
    self._speed_mps = state.speed_mps or 0.0
    # Draw in the gauge's full frame; the saved box is the inset part it actually fills.
    rect = rl.Rectangle(saved['x'] - CEM_GAUGE_INSET[0], saved['y'] - CEM_GAUGE_INSET[1], *CEM_GAUGE_FRAME)
    self._render_unified_road(rect, rect.x + rect.width / 2, rect.y + 145 - ROAD_HALF_SIZE, data)

  def _render_unified_road(self, rect, icx, icy, data, alpha=1.0):
    bottom = icy + ROAD_HALF_SIZE

    if data.indicator_type == IndicatorType.STOP_LIGHT:
      # Frozen decorative road anchor, not a reported stop distance.
      distance = 15.0
    else:
      distance = data.indicator_value

    points_left = []
    points_right = []
    self._current_road_h = self._road_h_filter.update(_get_road_height(data))
    road_h = self._current_road_h

    for i in range(ROAD_SEGMENTS + 1):
      t = i / ROAD_SEGMENTS
      offset = _get_perspective_offset(t, data)
      cx_t = icx + offset
      y_t = bottom - t * road_h
      w_t = ROAD_W_BOTTOM - t * (ROAD_W_BOTTOM - ROAD_W_TOP)

      points_left.append(rl.Vector2(cx_t - w_t, y_t))
      points_right.append(rl.Vector2(cx_t + w_t, y_t))

    fill_color = _fade(_with_alpha(data.color, FILL_ALPHA), alpha)
    for i in range(ROAD_SEGMENTS):
      t = i / ROAD_SEGMENTS
      stroke = ROAD_THICKNESS * (1.0 - 0.6 * t)
      rl.draw_triangle(points_left[i], points_right[i], points_left[i+1], fill_color)
      rl.draw_triangle(points_right[i], points_right[i+1], points_left[i+1], fill_color)
      rl.draw_line_ex(points_left[i], points_left[i+1], stroke, _fade(COLOR_SHADOW, alpha))
      rl.draw_line_ex(points_right[i], points_right[i+1], stroke, _fade(COLOR_SHADOW, alpha))
      rl.draw_line_ex(points_left[i], points_left[i+1], stroke, _fade(data.color, alpha))
      rl.draw_line_ex(points_right[i], points_right[i+1], stroke, _fade(data.color, alpha))

    it = data.indicator_type

    if it == IndicatorType.STOP_LIGHT:
      self._draw_stop_line(icx, bottom, distance, data, alpha)

    if it == IndicatorType.LEAD:
      self._draw_lead_car(icx, bottom, data, alpha)

    if it in (IndicatorType.ROAD_CURVE, IndicatorType.STOP_LIGHT):
      self._draw_standard_chevrons(icx, bottom, distance, data, alpha)

    if it == IndicatorType.STOP_LIGHT:
      self._draw_traffic_light(icx, icy, distance, data, alpha)


    self._draw_mini_cradle(icx, bottom, data, alpha)

  def _draw_stop_line(self, icx, bottom, distance, data, alpha=1.0):
    t, cx_line, cy_line = _road_xy(distance, icx, bottom, data, self._current_road_h)
    w_line = ROAD_W_BOTTOM - t * (ROAD_W_BOTTOM - ROAD_W_TOP)

    p_left = rl.Vector2(cx_line - w_line + ROAD_EDGE_INSET, cy_line)
    p_right = rl.Vector2(cx_line + w_line - ROAD_EDGE_INSET, cy_line)

    fade = 1.0 - t * 0.5
    glow_a = int(150 * fade)

    rl.draw_line_ex(p_left, p_right, max(3.0 * SCALE, 7.0 * SCALE * fade), _fade(_with_alpha(COLOR_STOP_LINE_GLOW, glow_a), alpha))
    rl.draw_line_ex(p_left, p_right, max(1.5 * SCALE, 3.5 * SCALE * fade), _fade(COLOR_STOP_LINE_CORE, alpha))

  def _draw_lead_car(self, icx, bottom, data, alpha=1.0):
    t_lead, cx_lead, cy_lead = _road_xy(6.5, icx, bottom, data, self._current_road_h)
    t_lead = max(0.15, min(0.85, t_lead))

    car_scale = 0.45 + (1.0 - t_lead) * 0.55
    W = 38.0 * SCALE * car_scale
    H = 22.0 * SCALE * car_scale

    is_stopped = data.indicator_extra == "stopped"
    is_slower = data.indicator_extra == "slower"

    if is_stopped:
      border_color = _fade(COLOR_LEAD_STOPPED, alpha)
    elif is_slower:
      border_color = _fade(_with_alpha(data.color, int(160 + 95 * _pulse(1.2))), alpha)
    else:
      border_color = _fade(data.color, alpha)

    # Car body: cabin and main body with high-contrast outlines
    for rect_args, corner_r, fill in [
      ((cx_lead - W * 0.3, cy_lead - H, W * 0.6, H * 0.45), 0.5, _fade(rl.Color(15, 15, 15, 240), alpha)),
      ((cx_lead - W / 2, cy_lead - H * 0.65, W, H * 0.55), 0.3, _fade(rl.Color(20, 20, 20, 240), alpha)),
    ]:
      r = rl.Rectangle(*rect_args)
      rl.draw_rectangle_rounded(r, corner_r, 4, fill)
      rl.draw_rectangle_rounded_lines_ex(r, corner_r, 4, 2.0, border_color)

    # Tail lights with radial bloom
    tl_w = max(4.0, W * 0.16)
    tl_h = max(2.5, H * 0.14)
    tl_y = int(cy_lead - H * 0.55)
    glow_y = int(cy_lead - H * 0.5)
    for tl_x, glow_x in [(int(cx_lead - W * 0.45), int(cx_lead - W * 0.38)),
                          (int(cx_lead + W * 0.3), int(cx_lead + W * 0.38))]:
      if is_stopped:
        pulse = _pulse(2.5)
        r_glow = int(W * 0.10 * (1.0 + 0.4 * pulse))
        rl.draw_circle(glow_x, glow_y, r_glow + 4, _fade(rl.Color(255, 30, 60, int(60 + 40 * pulse)), alpha))
        rl.draw_circle(glow_x, glow_y, r_glow, _fade(rl.Color(255, 30, 60, int(150 + 105 * pulse)), alpha))
        c_tl = _fade(rl.Color(255, 220, 220, 255), alpha)
      elif is_slower:
        pulse = _pulse(1.2)
        r_glow = int(W * 0.08 * (1.0 + 0.2 * pulse))
        rl.draw_circle(glow_x, glow_y, r_glow, _fade(rl.Color(255, 140, 30, int(100 + 80 * pulse)), alpha))
        c_tl = _fade(rl.Color(255, 160, 60, int(180 + 75 * pulse)), alpha)
      else:
        c_tl = _fade(COLOR_FORCE_STOP, alpha)
      rl.draw_rectangle(tl_x, tl_y, int(tl_w), int(tl_h), c_tl)

    # Wheels
    wheel_y = int(cy_lead - H * 0.1)
    wheel_w = int(W * 0.12)
    wheel_h = int(H * 0.1)
    wheel_c = _fade(rl.Color(10, 10, 10, 255), alpha)
    rl.draw_rectangle(int(cx_lead - W * 0.4), wheel_y, wheel_w, wheel_h, wheel_c)
    rl.draw_rectangle(int(cx_lead + W * 0.28), wheel_y, wheel_w, wheel_h, wheel_c)

    # Following chevrons (from lead car back toward viewer)
    dt = rl.get_frame_time()
    v_ego = self._speed_mps
    flow_speed_factor = min(1.8, v_ego * 0.08)
    self._lead_chevron_accum = (self._lead_chevron_accum + dt * flow_speed_factor * 1.8) % 1.0
    progress = self._lead_chevron_accum
    for i in range(2):
      t = t_lead * (1.0 - ((i + progress) / 2) % 1.0)

      cx_t = icx + _get_perspective_offset(t, data)
      road_h = self._current_road_h
      cy_t = bottom - t * road_h

      chevron_w = 14.0 * SCALE - t * 5.0 * SCALE
      chevron_thick = max(2.0 * SCALE, 4.0 * SCALE - t * 1.5 * SCALE)
      lx = cx_t - chevron_w
      rx = cx_t + chevron_w

      chev_a = max(0, min(255, int(data.color.a * (1.0 - t / t_lead) * math.sin(t / t_lead * math.pi))))
      c_color = _fade(_with_alpha(data.color, chev_a), alpha)

      rl.draw_line_ex(rl.Vector2(lx, cy_t - chevron_w * 0.5), rl.Vector2(cx_t, cy_t), chevron_thick, c_color)
      rl.draw_line_ex(rl.Vector2(rx, cy_t - chevron_w * 0.5), rl.Vector2(cx_t, cy_t), chevron_thick, c_color)

  def _draw_standard_chevrons(self, icx, bottom, distance, data, alpha=1.0):
    is_stop = data.indicator_type == IndicatorType.STOP_LIGHT
    dt = rl.get_frame_time()
    v_ego = self._speed_mps
    flow_speed_factor = min(1.5, v_ego * 0.08)
    self._chevron_accum = (self._chevron_accum + dt * flow_speed_factor * 1.5) % 1.0
    progress = self._chevron_accum
    t_stop = max(0.05, min(1.0, distance / STOP_DISTANCE_MAX)) if is_stop else 1.0
    max_t = t_stop if is_stop else 1.0

    for i in range(CHEVRON_COUNT):
      t = max_t - (i + progress) * CHEVRON_SPACING
      if t < 0.0 or t > max_t:
        continue

      t_next = min(max_t, t + CHEVRON_STEP)
      cx_t = icx + _get_perspective_offset(t, data)
      cx_next = icx + _get_perspective_offset(t_next, data)

      road_h = self._current_road_h
      cy_t = bottom - t * road_h
      cy_next = bottom - t_next * road_h

      dx = cx_next - cx_t
      dy = cy_next - cy_t
      len_v = math.hypot(dx, dy)
      dir_up_x, dir_up_y = (dx / len_v, dy / len_v) if len_v > 0.001 else (0.0, -1.0)

      dir_right_x = -dir_up_y
      dir_right_y = dir_up_x

      chevron_w = max(3.0 * SCALE, 18.0 * SCALE - t * 7.0 * SCALE)
      chevron_h = chevron_w * 0.6
      chevron_thick = max(2.5 * SCALE, 4.5 * SCALE - t * 2.0 * SCALE)

      lx = cx_t - dir_right_x * chevron_w + dir_up_x * chevron_h
      ly = cy_t - dir_right_y * chevron_w + dir_up_y * chevron_h
      rx = cx_t + dir_right_x * chevron_w + dir_up_x * chevron_h
      ry = cy_t + dir_right_y * chevron_w + dir_up_y * chevron_h

      alpha_factor = math.sin((t / max_t) * math.pi) if max_t > 0.01 else 0.0
      chev_a = max(0, min(255, int(data.color.a * alpha_factor)))
      chev_color = _fade(_with_alpha(data.color, chev_a), alpha)
      chev_shadow = _fade(rl.Color(0, 0, 0, int(chev_a * 0.5)), alpha)

      rl.draw_line_ex(rl.Vector2(lx, ly + 1.5), rl.Vector2(cx_t, cy_t + 1.5), chevron_thick, chev_shadow)
      rl.draw_line_ex(rl.Vector2(rx, ry + 1.5), rl.Vector2(cx_t, cy_t + 1.5), chevron_thick, chev_shadow)
      rl.draw_line_ex(rl.Vector2(lx, ly), rl.Vector2(cx_t, cy_t), chevron_thick, chev_color)
      rl.draw_line_ex(rl.Vector2(rx, ry), rl.Vector2(cx_t, cy_t), chevron_thick, chev_color)

  def _draw_traffic_light(self, icx, icy, distance, data, alpha=1.0):
    t_light, cx_light, cy_road = _road_xy(distance, icx, icy + ROAD_HALF_SIZE, data, self._current_road_h)
    s_light = 1.0 - t_light

    scale_light = 0.65 + (s_light ** 2.0) * 1.35
    cy_light = cy_road - 38.0 * SCALE * s_light
    width = 15.0 * SCALE * scale_light
    height = 36.0 * SCALE * scale_light

    rl.draw_rectangle_rounded(rl.Rectangle(cx_light - width/2, cy_light - height/2 + 1.5, width, height), 0.25, 4, _fade(rl.Color(0, 0, 0, 120), alpha))
    rect_housing = rl.Rectangle(cx_light - width/2, cy_light - height/2, width, height)
    rl.draw_rectangle_rounded(rect_housing, 0.25, 4, _fade(rl.Color(22, 22, 22, 255), alpha))
    rl.draw_rectangle_rounded_lines_ex(rect_housing, 0.25, 4, 1.5, _fade(rl.Color(100, 100, 100, 255), alpha))

    r_bulb = 3.2 * SCALE * scale_light
    active_light = data.indicator_extra if data.indicator_extra in ("red", "yellow", "green") else "red"

    bulbs = [
      (cy_light - 10.0 * SCALE * scale_light, "red", rl.Color(255, 30, 60, 255), rl.Color(50, 10, 15, 255)),
      (cy_light, "yellow", rl.Color(255, 200, 0, 255), rl.Color(50, 40, 0, 255)),
      (cy_light + 10.0 * SCALE * scale_light, "green", rl.Color(0, 255, 100, 255), rl.Color(0, 40, 15, 255)),
    ]
    for y, name, active_c, inactive_c in bulbs:
      if name == "red" and active_light == "red":
        glow_pulse = _pulse(1.5)
        rl.draw_circle_v(rl.Vector2(cx_light, y), r_bulb + 8.0 * SCALE * glow_pulse, _fade(rl.Color(255, 30, 60, 25), alpha))
        rl.draw_circle_v(rl.Vector2(cx_light, y), r_bulb + 4.0 * SCALE * glow_pulse, _fade(rl.Color(255, 30, 60, 60), alpha))
      c = _fade(active_c if active_light == name else inactive_c, alpha)
      rl.draw_circle_v(rl.Vector2(cx_light, y), r_bulb, c)

  def _draw_mini_cradle(self, cx, bottom, data, alpha=1.0):
    if not data.text:
      return

    if data.is_numeric:
      val_size = self.fonts.measure(data.text, FontRole.BOLD, int(50 * SCALE))
      val_pos = rl.Vector2(int(cx - val_size.width / 2), int(bottom + 6 * SCALE))
      _draw_text_with_shadow(self.fonts, FontRole.BOLD, data.text, val_pos, int(50 * SCALE), data.color, alpha)

      accent_y = int(val_pos.y + val_size.height + 2 * SCALE)
      accent_w = int(val_size.width + 16 * SCALE)
      accent_x = int(cx - accent_w / 2)
      rl.draw_line_ex(
        rl.Vector2(accent_x, accent_y),
        rl.Vector2(accent_x + accent_w, accent_y),
        2.0 * SCALE,
        _fade(_with_alpha(data.color, 160), alpha),
      )

      if data.reduction_text:
        red_size = self.fonts.measure(data.reduction_text, FontRole.MEDIUM, int(22 * SCALE))
        red_pos = rl.Vector2(int(cx + val_size.width / 2 + 6 * SCALE), int(val_pos.y + val_size.height / 2 - red_size.height / 2))
        _draw_text_with_shadow(self.fonts, FontRole.MEDIUM, data.reduction_text, red_pos, int(22 * SCALE), COLOR_REDUCTION, alpha)

      if data.unit:
        unit_size = self.fonts.measure(data.unit, FontRole.MEDIUM, int(20 * SCALE))
        unit_pos = rl.Vector2(int(cx - unit_size.width / 2), int(accent_y + 3 * SCALE))
        _draw_text_with_shadow(self.fonts, FontRole.MEDIUM, data.unit, unit_pos, int(20 * SCALE), rl.Color(255, 255, 255, 180), alpha)
    else:
      val_size = self.fonts.measure(data.text, FontRole.BOLD, int(32 * SCALE))
      val_pos = rl.Vector2(int(cx - val_size.width / 2), int(bottom + 10 * SCALE))
      _draw_text_with_shadow(self.fonts, FontRole.BOLD, data.text, val_pos, int(32 * SCALE), data.color, alpha)
