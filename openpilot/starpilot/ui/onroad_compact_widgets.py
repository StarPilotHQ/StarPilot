"""Compact onroad HUD and right rail adapted from native StarPilot widgets."""

from functools import lru_cache
import math
import hashlib
import json
import time
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.ui.onroad_customization import offset as widget_offset, placement, rgba, widget_size
from openpilot.starpilot.ui.onroad_state import AlertSize, ObservationKind, OnroadState, is_lane_change_notice, manual_slc_confirmation
from openpilot.starpilot.ui.onroad_curve import controlling as curve_controlling
from openpilot.starpilot.ui.onroad_conditional import reason as conditional_reason, status as conditional_status, stop_active
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile
from openpilot.starpilot.ui.onroad_widget_style import draw_widget_frame
from openpilot.starpilot.ui.wheel_feedback import WheelFeedbackFade
from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget
from openpilot.starpilot.ui.moon import draw_moon
from openpilot.starpilot.ui.onroad_personality import PersonalityWidget


@lru_cache(maxsize=128)
def _line_points(x1: float, y1: float, x2: float, y2: float) -> tuple:
  return rl.Vector2(x1, y1), rl.Vector2(x2, y2)


def _line(x1: float, y1: float, x2: float, y2: float, color: rl.Color, width: float = 3) -> None:
  # The native primitive uses the same active GL transform as the Python API.
  # Keep geometry in logical coordinates and pass the current color every draw.
  rl.draw_line_ex(*_line_points(x1, y1, x2, y2), width, color)


def _curve_center(t: float) -> tuple[float, float]:
  points = ((-2, 24), (-3, 9), (-1, -9), (4, -24))
  u = 1 - t
  x = u**3 * points[0][0] + 3 * u**2 * t * points[1][0] + 3 * u * t**2 * points[2][0] + t**3 * points[3][0]
  y = u**3 * points[0][1] + 3 * u**2 * t * points[1][1] + 3 * u * t**2 * points[2][1] + t**3 * points[3][1]
  return x, y


_CURVE_DASHES = tuple((_curve_center(start), _curve_center(end))
                      for start, end in ((0.18, 0.28), (0.39, 0.50), (0.61, 0.72)))


def draw_curve_road_icon(rect: rl.Rectangle) -> None:
  cx, cy = rect.x + rect.width / 2, rect.y + rect.height / 2
  blue = rl.Color(112, 192, 216, 255)
  def point(x: float, y: float) -> rl.Vector2:
    return rl.Vector2(cx + x, cy + y)
  for x in (-18, 14):
    rl.draw_spline_segment_bezier_cubic(point(x, 24), point(x - 1, 9),
                                        point(x + 1, -9), point(x + 6, -24), 3, rl.WHITE)
  for start, end in _CURVE_DASHES:
    _line(cx + start[0], cy + start[1], cx + end[0], cy + end[1], blue, 4)


def draw_stop_light_icon(rect: rl.Rectangle) -> None:
  cx, cy = rect.x + rect.width / 2, rect.y + rect.height / 2
  housing = rl.Rectangle(cx - 13, cy - 29, 26, 58)
  rl.draw_rectangle_rounded(housing, 0.32, 8, rl.Color(20, 24, 27, 255))
  rl.draw_rectangle_rounded_lines_ex(housing, 0.32, 8, 2, rl.WHITE)
  for y, fill in ((cy - 18, rl.Color(230, 52, 66, 255)),
                  (cy, rl.Color(69, 54, 30, 255)),
                  (cy + 18, rl.Color(28, 63, 44, 255))):
    rl.draw_circle(int(cx), int(y), 7, rl.Color(7, 12, 16, 255))
    rl.draw_circle(int(cx), int(y), 5, fill)
  rl.draw_circle(int(cx - 2), int(cy - 20), 2, rl.Color(255, 182, 185, 255))


def draw_lead_icon(rect: rl.Rectangle) -> None:
  cx, cy = rect.x + rect.width / 2, rect.y + rect.height / 2
  accent = rl.Color(112, 192, 216, 255)
  for side in (-1, 1):
    for edge in (-1, 1):
      x, y = cx + side * 24, cy + edge * 22
      _line(x, y, x - side * 7, y, accent, 3)
      _line(x, y, x, y - edge * 7, accent, 3)
  roof = ((cx - 15, cy - 1), (cx - 10, cy - 13), (cx + 10, cy - 13), (cx + 15, cy - 1))
  for start, end in zip(roof, roof[1:], strict=False):
    _line(*start, *end, rl.WHITE, 3)
  rl.draw_rectangle_rounded(rl.Rectangle(cx - 16, cy - 3, 32, 18), 0.4, 8, rl.WHITE)
  for side in (-1, 1):
    rl.draw_rectangle(int(cx + side * 12 - 2), int(cy + 13), 4, 6, rl.WHITE)
    _line(cx + side * 7, cy + 3, cx + side * 12, cy + 3, rl.Color(200, 32, 48, 255), 3)
  _line(cx - 4, cy + 9, cx + 4, cy + 9, rl.Color(20, 24, 27, 255), 2)


class CompactHudRenderer:
  """Transient compact MAX, detached speed-limit sign and native steering wheel."""

  def __init__(self, fonts: BitmapFonts, asset_directory: Path):
    self.fonts = fonts
    self.asset_directory = asset_directory
    self._wheel: rl.Texture | None = None
    self._feedback = WheelFeedbackFade()
    self._speed_limit_widget = SpeedLimitWidget(fonts, Profile.COMPACT)
    self._set_speed_alpha = FirstOrderFilter(0.0, 0.1, 1 / 60)
    self._wheel_alpha = FirstOrderFilter(0.0, 0.05, 1 / 60)
    self._wheel_y = FirstOrderFilter(0.0, 0.1, 1 / 60)
    self._was_cruise_active = False
    self._last_cruise_display: int | None = None
    self._last_cruise_ns: int | None = None
    self._set_speed_changed_ns: int | None = None
    self._cruise_drive_frame: int | None = None

  def prepare(self) -> None:
    if self._wheel is not None:
      return
    path = self.asset_directory / "icons_mici/wheel.png"
    entry = next(item for item in json.loads(Path(__file__).with_name("onroad-assets.json").read_text())["files"]
                 if item["file"] == "icons_mici/wheel.png")
    data = path.read_bytes()
    if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
      raise ValueError("Unreviewed compact wheel art")
    image = rl.load_image(str(path))
    try:
      if image.data is None:
        raise RuntimeError("Unable to load compact wheel art")
      rl.image_resize(image, 50, 50)
      self._wheel = rl.load_texture_from_image(image)
      if not self._wheel.id:
        raise RuntimeError("Unable to create compact wheel texture")
    finally:
      if image.data is not None:
        rl.unload_image(image)

  def render(self, state: OnroadState) -> None:
    self._speed_limit_sign(state)
    self.render_max_speed(state)
    self.render_steering_wheel(state)

  def render_max_speed(self, state: OnroadState) -> None:
    set_speed_alpha = self._set_speed_opacity(state, (state.observed_ns or time.monotonic_ns()))
    cruise = "–" if state.cruise_kph is None else str(round(state.cruise_kph if state.metric else state.cruise_kph * 0.621371))
    mx, my = widget_offset(state.customization, "compact", "max_speed")
    if set_speed_alpha > 0.01 and placement(state.customization, "compact", "max_speed")["enabled"]:
      shadow = rl.Color(0, 0, 0, int(255 / 2 * set_speed_alpha))
      try:
        rl.draw_circle_gradient(rl.Vector2(81 + mx, 81 + my), 81, shadow, rl.BLANK)
      except (RuntimeError, TypeError):
        rl.draw_circle_gradient(int(81 + mx), int(81 + my), 81, shadow, rl.BLANK)
      red, green, blue, alpha = rgba(state.customization, "text", "compact", "max_speed")
      color = rl.Color(red, green, blue, int(alpha * 0.9 * set_speed_alpha))
      speed_size = 112
      while speed_size > 72 and self.fonts.measure(cruise, FontRole.SET_SPEED, speed_size).width > 128:
        speed_size -= 1
      self.fonts.draw(cruise, FontRole.SET_SPEED, speed_size, 17 + mx, -4 + my, color)
      self.fonts.draw("MAX", FontRole.SEMI_BOLD, 36, 25 + mx, 109 + my, color)

  def render_steering_wheel(self, state: OnroadState) -> None:
    color = self._feedback.update(state.wheel_acceleration, state.appearance.wheel_pedal_feedback, state.observed_ns, state.drive_frame)
    if state.lateral_active:
      wheel_alpha = self._wheel_alpha.update(255 * 0.9)
      wheel_y = self._wheel_y.update(0)
    else:
      self._wheel_alpha.x = 0.0
      self._wheel_y.x = 25.0
      wheel_alpha, wheel_y = 0.0, 25.0
    wx, wy = widget_offset(state.customization, "compact", "steering_wheel")
    if (state.alert.size == AlertSize.NONE and wheel_alpha > 1 and
        placement(state.customization, "compact", "steering_wheel")["enabled"]):
      self.prepare()
      size, _ = widget_size(state.customization, "compact", "steering_wheel")
      rl.draw_texture_pro(self._wheel, rl.Rectangle(0, 0, 50, 50),
                          rl.Rectangle(21 + wx + size / 2, 176 + wy + size / 2 + wheel_y, size, size),
                          rl.Vector2(size / 2, size / 2), 0,
                          rl.Color(*color, int(wheel_alpha)))

  def close(self) -> None:
    self._speed_limit_widget.close()
    if self._wheel is not None and rl.is_window_ready():
      rl.unload_texture(self._wheel)
    self._wheel = None

  def _set_speed_opacity(self, state: OnroadState, now_ns: int) -> float:
    if state.drive_frame != self._cruise_drive_frame:
      self._set_speed_alpha.x = 0.0
      self._was_cruise_active = False
      self._last_cruise_display = self._last_cruise_ns = self._set_speed_changed_ns = None
      self._cruise_drive_frame = state.drive_frame
    available = state.cruise_active and state.cruise_kph is not None
    if available:
      display = round(state.cruise_kph if state.metric else state.cruise_kph * 0.621371)
      changed = self._last_cruise_display is not None and display != self._last_cruise_display
      just_engaged = not self._was_cruise_active
      if changed or just_engaged:
        self._set_speed_changed_ns = now_ns
      self._was_cruise_active = True
      self._last_cruise_display = display
      self._last_cruise_ns = now_ns
      elapsed = now_ns - self._set_speed_changed_ns if self._set_speed_changed_ns is not None else 0
      persistent = (manual_slc_confirmation(Profile.COMPACT, state) or
                    state.toyota_cruise_status in ('raise_dash', 'release_plus', 'off_hold'))
      return self._set_speed_alpha.update(float(persistent or 0 < elapsed < 2_500_000_000))
    if (self._last_cruise_ns is None or now_ns < self._last_cruise_ns or
        now_ns - self._last_cruise_ns > 150_000_000):
      self._set_speed_alpha.x = 0.0
      self._was_cruise_active = False
      self._last_cruise_display = None
      self._last_cruise_ns = None
      self._set_speed_changed_ns = None
    return 0.0

  def _speed_limit_sign(self, state: OnroadState) -> None:
    self._speed_limit_widget.render(state, defer_confirmation=True)


class MiciSidebarWidgets:
  """The frozen compact confidence/CEM/personality rail, supplied externally."""

  def __init__(self, fonts: BitmapFonts | None = None):
    self.fonts = fonts
    self.personality = PersonalityWidget(fonts)
    self._confidence_filter = FirstOrderFilter(-0.5, 0.5, 1 / 60)
    self._confidence_drive_frame: int | None = None
    self._confidence_stamp_ns: int | None = None

  def render(self, rect: rl.Rectangle, state: OnroadState, *, widget=None) -> None:
    sidebar = rl.Rectangle(rect.x + rect.width - 60, rect.y, 60, rect.height)
    if widget is None:
      rl.draw_rectangle(int(sidebar.x), int(sidebar.y), int(sidebar.width), int(sidebar.height), rl.BLACK)
    for key, render in (("model_confidence", self._confidence_ball), ("conditional_mode", self._conditional),
                        ("following_distance", self._personality)):
      if widget is not None and key != widget:
        continue
      position = placement(state.customization, "compact", key)
      if not position["enabled"] or (position["x"] < sidebar.x and state.alert.size != AlertSize.NONE and not is_lane_change_notice(state.alert)):
        continue
      bounds = rl.Rectangle(position["x"], position["y"], 60, 80)
      if key != "following_distance":
        draw_widget_frame(bounds, state.customization, "compact", key)
      render(bounds, state)

  def render_personality(self, rect: rl.Rectangle, state: OnroadState) -> None:
    position = placement(state.customization, "compact", "following_distance")
    if not position["enabled"] or (position["x"] < rect.x + rect.width - 60 and state.alert.size != AlertSize.NONE and not is_lane_change_notice(state.alert)):
      return
    bounds = rl.Rectangle(position["x"], position["y"], 60, 80)
    self._personality(bounds, state)

  def _conditional(self, center: rl.Rectangle, state: OnroadState) -> None:
    conditional = conditional_status(state)
    preview = state.visual_preview
    if stop_active(state):
      self._stop_icon(center)
    elif (preview is not None and preview.curve_curvature is not None and state.alert.size != AlertSize.FULL) or curve_controlling(state):
      self._curve_icon(center)
    elif conditional is not None and self.fonts is not None:
      family, _, color = conditional
      reason = conditional_reason(state)
      icon = {'cem_stop': 'stop', 'cem_lead': 'lead', 'ccm_lead': 'lead',
              'cem_curve': 'curve', 'cem_signal': 'turn',
              'cem_speed': 'speed', 'cem_open_road': 'speed', 'cem_speed_limit': 'speed', 'ccm_speed': 'speed'}.get(reason)
      if icon == 'stop':
        self._stop_icon(center)
      elif icon == 'lead':
        self._lead_icon(center)
      elif icon == 'curve':
        self._curve_icon(center)
      elif icon == 'turn':
        self._turn_icon(center, state)
      elif icon == 'speed':
        self._speed_icon(center, state)
      elif family == 'CEM':
        self._chill_icon(center)
      else:
        self._active_icon(center, color)
    else:
      if (state.conditional_configured == ModeChoice.CEM or
          state.conditional_effective is not None and state.conditional_effective.choice == ModeChoice.CEM):
        self._chill_icon(center)
      elif state.experimental_enabled:
        self._active_icon(center, rl.Color(112, 192, 216, 255))
      else:
        self._chill_icon(center)

  def _confidence_ball(self, rect: rl.Rectangle, state: OnroadState) -> None:
    radius = 18
    center_x, center_y = rect.x + rect.width / 2, rect.y + rect.height / 2
    active = (state.lateral_active or state.longitudinal_active) and (state.alert.size == AlertSize.NONE or is_lane_change_notice(state.alert))
    stamp = state.stock_confidence_source_stamp_ns if state.stock_confidence_source_fresh else None
    if (stamp is None or state.model_confidence is None or state.stock_confidence_drive_frame != self._confidence_drive_frame or
        self._confidence_stamp_ns is not None and (stamp < self._confidence_stamp_ns or stamp - self._confidence_stamp_ns > 200_000_000)):
      self._confidence_filter.x = -0.5
    self._confidence_drive_frame = state.stock_confidence_drive_frame
    self._confidence_stamp_ns = stamp
    if active and stamp is not None and state.model_confidence is not None:
      confidence = self._confidence_filter.update(state.model_confidence)
      if confidence > 0.5:
        top, bottom = rl.Color(0, 255, 204, 255), rl.Color(0, 255, 38, 255)
      elif confidence > 0.2:
        top, bottom = rl.Color(255, 200, 0, 255), rl.Color(255, 115, 0, 255)
      else:
        top, bottom = rl.Color(255, 0, 21, 255), rl.Color(255, 0, 89, 255)
    else:
      top, bottom = rl.Color(50, 50, 50, 255), rl.Color(13, 13, 13, 255)
    rl.draw_rectangle_gradient_v(int(center_x - radius), int(center_y - radius), 2 * radius, 2 * radius, top, bottom)
    outer = math.ceil(radius * math.sqrt(2)) + 1
    rl.draw_ring(rl.Vector2(int(center_x), int(center_y)), radius, outer, 0, 360, 20, rl.BLACK)

  def _chill_icon(self, rect: rl.Rectangle) -> None:
    draw_moon(rl.Rectangle(rect.x + rect.width / 2 - 21, rect.y + rect.height / 2 - 21, 42, 42))

  def _curve_icon(self, rect: rl.Rectangle) -> None:
    draw_curve_road_icon(rect)

  def _lead_icon(self, rect: rl.Rectangle) -> None:
    draw_lead_icon(rect)

  def _stop_icon(self, rect: rl.Rectangle) -> None:
    draw_stop_light_icon(rect)

  def _turn_icon(self, rect: rl.Rectangle, state: OnroadState) -> None:
    cx, cy = rect.x + rect.width / 2, rect.y + rect.height / 2
    signals = state.border_signals
    direction = -1 if signals is not None and signals.left_blinker and not signals.right_blinker else 1
    blue = rl.Color(112, 192, 216, 255)
    points = ((cx - direction * 10, cy + 25), (cx - direction * 13, cy + 6),
              (cx - direction * 4, cy - 8), (cx + direction * 18, cy - 20))
    for start, end in zip(points, points[1:], strict=False):
      _line(*start, *end, blue, 4)
    tip = (cx + direction * 24, cy - 26)
    base1 = rl.Vector2(cx + direction * 4, cy - 24)
    base2 = rl.Vector2(cx + direction * 19, cy - 7)
    rl.draw_triangle(rl.Vector2(*tip), base2 if direction > 0 else base1,
                     base1 if direction > 0 else base2, blue)

  def _speed_icon(self, rect: rl.Rectangle, state: OnroadState) -> None:
    cx, cy = rect.x + rect.width / 2, rect.y + rect.height / 2
    blue = rl.Color(112, 192, 216, 255)
    rl.draw_circle(int(cx), int(cy), 20, rl.WHITE)
    rl.draw_ring(rl.Vector2(cx, cy), 17, 20, 0, 360, 48, blue)
    observation = state.speed_limit
    value = str(round(observation.speed_limit_mps * (3.6 if state.metric else 2.2369362921))) if \
      observation.kind == ObservationKind.VALID and observation.speed_limit_mps is not None else 'S'
    size = 19 if len(value) <= 2 else 16
    measured = self.fonts.measure(value, FontRole.BOLD, size)
    self.fonts.draw(value, FontRole.BOLD, size, cx - measured.width / 2, cy - measured.height / 2, rl.BLACK)

  def _active_icon(self, rect: rl.Rectangle, color: rl.Color) -> None:
    cx, cy = rect.x + rect.width / 2, rect.y + rect.height / 2
    rl.draw_ring(rl.Vector2(cx, cy), 17, 20, 0, 360, 48, color)
    measured = self.fonts.measure('E', FontRole.BOLD, 22)
    self.fonts.draw('E', FontRole.BOLD, 22, cx - measured.width / 2, cy - measured.height / 2, color)

  def _personality(self, _rect: rl.Rectangle, state: OnroadState) -> None:
    self.personality.render(state)
