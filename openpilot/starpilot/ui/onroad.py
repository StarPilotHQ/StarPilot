"""Two-profile native onroad composition over a caller-owned camera layer.

The host supplies state and, when a frame is available, the current camera/model
renderer as a callback. Offline previews create no camera or IPC resources.
The visual widgets retain the frozen StarPilot geometry under the adjacent
LICENSE, while the application owns all state and action transport.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol
import hashlib
import json

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import clip
from openpilot.system.ui.lib.application import gui_app
from openpilot.starpilot.ui.onroad_customization import (CLOCK_WIDGET, MODE_WIDGET, RAIL_WIDGETS, STOPPED_TIMER_WIDGET, placement, rgba,
                                                         widget_order, widget_size)
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice

from openpilot.starpilot.ui.onroad_alerts import AlertRenderer
from openpilot.starpilot.ui.onroad_border import render_compact_half_borders, render_large_half_borders
from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer, MiciSidebarWidgets
from openpilot.starpilot.ui.onroad_personality import PersonalityWidget
from openpilot.starpilot.ui.onroad_curve import controlling as curve_controlling, glow_color, intensity, render_glow, status_label
from openpilot.starpilot.ui.onroad_conditional import stop_active
from openpilot.starpilot.ui.onroad_corner import CornerHintCache, render_corner_hint
from openpilot.starpilot.ui.onroad_large_widgets import CurrentSpeedHud, UnifiedSpeedWidget, SteeringWheelWidget
from openpilot.starpilot.ui.onroad_cem_gauge import AetherGauge
from openpilot.starpilot.ui.live_developer_sidebar import render_sidebar as render_live_sidebar
from openpilot.starpilot.ui.layout_preview_sidebar import SIDEBAR_WIDTH
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadState, manual_slc_confirmation, slc_action_occludes
from openpilot.starpilot.ui.onroad_stopped_timer import duration_color, duration_text, timer_size
from openpilot.starpilot.ui.onroad_torque import TorqueBarWidget
from openpilot.starpilot.ui.onroad_navigation import NavigationCard
from openpilot.starpilot.ui.onroad_navigation_favorites import NavigationFavorites
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile
from openpilot.starpilot.ui.large_speed_geometry import ACTION_TEXT_SIZE
from openpilot.starpilot.ui.speed_limit_widget import INK, MUTED, draw_ink
from openpilot.starpilot.conditional_mode.policy import ModeChoice


CameraLayer = Callable[[rl.Rectangle, OnroadState], None]
OverlayLayer = Callable[[rl.Rectangle, OnroadState], None]
PipLayer = Callable[[rl.Rectangle, OnroadState], None]
ProjectionControlLayer = Callable[[OnroadState], None]

def _load_fade(path: Path) -> rl.Texture:
  entry = next(item for item in json.loads(Path(__file__).with_name('onroad-assets.json').read_text())['files']
               if Path(item['file']).name == path.name)
  with path.open('rb') as stream:
    data = stream.read(entry['bytes'] + 1)
  if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
    raise ValueError(f'Unreviewed onroad fade art: {path.name}')
  texture = rl.load_texture(str(path))
  if not texture.id:
    raise RuntimeError(f'Unable to load onroad fade art: {path.name}')
  return texture


def driving_mode_description(state: OnroadState) -> str:
  if state.longitudinal_overridden:
    return "ACC - Override"
  if state.lateral_active and state.switchback_mode:
    return "Switchback Mode"
  if state.longitudinal_active:
    if state.traffic_mode:
      return "Traffic Mode"
    mode = state.conditional_effective
    experimental = (mode.effective_experimental if mode is not None else
                    state.experimental_enabled and state.conditional_configured in (None, ModeChoice.STOCK))
    return "ACC - Experimental" if experimental else "ACC - Chill"
  if state.lateral_active:
    return "Always On Lateral"
  return "Stock ACC" if state.stock_cruise_active else "Disengaged"


def clock_text(now: datetime | None = None, use_24_hour: bool = False) -> str:
  value = now or datetime.now().astimezone()
  return value.strftime('%H:%M') if use_24_hour else value.strftime('%I:%M %p').lstrip('0')


def axis_status_color(state: OnroadState) -> rl.Color:
  """Retain StarPilot's existing off, AOL, long-only, and combined edge colors."""
  lateral = state.lateral_active if state.aol_latched is None else state.aol_latched
  if state.driver_override_active or state.longitudinal_overridden:
    return rl.Color(145, 155, 149, 255)
  if lateral and state.switchback_mode:
    return rl.Color(139, 108, 197, 255)
  if stop_active(state):
    return rl.Color(218, 111, 37, 255)
  if state.longitudinal_active and state.traffic_mode:
    return rl.Color(201, 34, 49, 255)
  mode = state.conditional_effective if state.longitudinal_active else None
  if mode is not None and mode.reason == 'manual_chill':
    return rl.Color(255, 214, 0, 255)
  if mode is not None and mode.effective_experimental:
    return rl.Color(218, 111, 37, 255)
  if lateral and state.longitudinal_active:
    if state.experimental_enabled and state.conditional_configured in (None, ModeChoice.STOCK):
      return rl.Color(218, 111, 37, 255)
    return rl.Color(22, 127, 64, 255)
  if lateral:
    return rl.Color(22, 127, 64, 255) if state.stock_cruise_active else rl.Color(10, 186, 181, 255)
  if state.longitudinal_active:
    return rl.Color(255, 105, 180, 255)
  return rl.Color(18, 40, 57, 255)


class JetlinkRenderLayer(Protocol):
  def render(self, state: OnroadState) -> None: ...


class OnroadView:
  preview_pip_layer: PipLayer | None
  jetlink_status: JetlinkRenderLayer

  def __init__(self, fonts: BitmapFonts, asset_directory: Path, *, camera_layer: CameraLayer | None = None,
               extra_overlays: OverlayLayer | None = None, pip_layer: PipLayer | None = None,
               background_layer: OverlayLayer | None = None, projection_viewport: tuple[float, float] | None = None,
               wall_clock: Callable[[], datetime | None] | None = None):
    self.projection_viewport = projection_viewport
    self.wall_clock = wall_clock or (lambda: datetime.now().astimezone())
    self._corner_cache = CornerHintCache()
    self.quick_select_layer = None
    gui_app.add_render_prepare(self._corner_cache.prepare, self._corner_cache.close)
    self.fonts = fonts
    self.camera_layer = camera_layer
    self.background_layer = background_layer
    self.extra_overlays = extra_overlays
    self.driver_monitor_layer: OverlayLayer | None = None
    self.map_layer: OverlayLayer | None = None  # Android Auto map overlay, under the HUD widgets
    self.projection_exit_layer: ProjectionControlLayer | None = None
    self.pip_layer = pip_layer
    self.stock_confidence_layer: OverlayLayer | None = None
    self.stock_confidence_reset: Callable[[], None] | None = None
    self._stock_confidence_last_source_ns: int | None = None
    self._stock_confidence_last_drive_frame: int | None = None
    self.alert = AlertRenderer(fonts)
    self.navigation = NavigationCard(fonts, clock=self.wall_clock)
    self.navigation_favorites = NavigationFavorites(fonts)
    if projection_viewport is not None:
      from openpilot.starpilot.ui.onroad_now_playing import NowPlayingWidget
      self.now_playing = NowPlayingWidget(fonts)
    self.torque_bar = TorqueBarWidget()
    gui_app.add_render_prepare(self.torque_bar.prepare, self.torque_bar.close)
    if fonts.profile == Profile.LARGE:
      self.personality = PersonalityWidget(fonts)
      self.unified_speed = UnifiedSpeedWidget(fonts)
      self.current_speed = CurrentSpeedHud(fonts)
      self.cem_gauge = AetherGauge(fonts)
      self.steering_wheel = SteeringWheelWidget(asset_directory)
      from openpilot.starpilot.ui.onroad_jetlink import JetlinkStatusWidget
      self.jetlink_status = JetlinkStatusWidget(fonts)
    else:
      self.compact_hud = CompactHudRenderer(fonts, asset_directory)
      self.compact_sidebar = MiciSidebarWidgets(fonts)
      self.personality = self.compact_sidebar.personality
      self._fade_path = asset_directory / "icons_mici/onroad/onroad_fade.png"
      self._fade: rl.Texture | None = None

  def _prepare_compact_fade(self) -> None:
    if self._fade is None:
      self._fade = _load_fade(self._fade_path)

  def render(self, state: OnroadState) -> None:
    if state.camera_available and self.camera_layer is None:
      raise ValueError("Camera observation requires a camera renderer")
    if self.fonts.profile == Profile.LARGE:
      self._large(state)
    else:
      self._compact(state)

  def _slc_actions(self, state: OnroadState) -> None:
    from openpilot.starpilot.ui.onroad_state import slc_action_bounds
    from openpilot.starpilot.ui.unified_speed_presentation import toyota_cruise_notice
    if manual_slc_confirmation(self.fonts.profile, state):
      sign = self.unified_speed.sign if self.fonts.profile == Profile.LARGE else self.compact_hud._speed_limit_widget
      sign.render_confirmation(state, getattr(self, 'pressed_action', lambda: None)())
      return
    notice = toyota_cruise_notice(state)
    bounds = slc_action_bounds(self.fonts.profile, state)
    if notice is not None and state.alert.size == AlertSize.NONE:
      large = self.fonts.profile == Profile.LARGE
      left, top, right = bounds[0][0], bounds[0][1], bounds[-1][2]
      center = (left + right) / 2
      primary = ACTION_TEXT_SIZE[self.fonts.profile]
      secondary = 32 if large else 20
      for text, size, y in ((notice[0], primary, top + (8 if large else 4)),
                            (notice[1], secondary, top + (64 if large else 43))):
        a, b = self.fonts.horizontal_ink(text, FontRole.MEDIUM, size)
        draw_ink(self.fonts, text, FontRole.MEDIUM, size, center - (a + b) / 2, y, INK)
      value, unit = notice[2:]
      value_left, value_right = self.fonts.horizontal_ink(value, FontRole.MEDIUM, primary)
      unit_left, unit_right = self.fonts.horizontal_ink(unit, FontRole.MEDIUM, secondary)
      value_width, unit_width = value_right - value_left, unit_right - unit_left
      x = center - (value_width + 8 + unit_width) / 2
      y = top + (100 if large else 64)
      _, value_height = draw_ink(self.fonts, value, FontRole.MEDIUM, primary, x - value_left, y, INK)
      a, b = self.fonts.vertical_ink(unit, FontRole.MEDIUM, secondary)
      draw_ink(self.fonts, unit, FontRole.MEDIUM, secondary, x + value_width + 8 - unit_left, y + value_height - (b - a), INK)
      return
    feedback = state.speed_limit.action_feedback
    if (self.fonts.profile == Profile.LARGE and feedback and state.alert.size == AlertSize.NONE and
        placement(state.customization, self.fonts.profile, 'speed_limit_actions')['enabled']):
      draw_ink(self.fonts, feedback, FontRole.MEDIUM, 30, bounds[0][0], bounds[0][1] + 24, MUTED)

  def _preview_badge(self, state: OnroadState) -> None:
    """Keep synthetic replay visibly identified even over a full alert."""
    preview = state.visual_preview
    if preview is None or preview.layout_sample:
      return
    flags = []
    if preview.cem_reason is not None:
      flags.append('CEM')
    if preview.curve_curvature is not None:
      flags.append('CSC')
    if not flags:
      return
    large = self.fonts.profile == Profile.LARGE
    panel = rl.Rectangle(65, 966, 570, 74) if large else rl.Rectangle(8, 194, 310, 40)
    rl.draw_rectangle_rounded(panel, 0.18, 6, rl.Color(12, 24, 32, 245))
    rl.draw_rectangle_rounded_lines_ex(panel, 0.18, 6, 2, rl.Color(112, 192, 216, 255))
    self.fonts.draw('SYNTHETIC REPLAY PREVIEW', FontRole.SEMI_BOLD,
                    22 if large else 14, panel.x + 12, panel.y + (8 if large else 4))
    self.fonts.draw(' + '.join(flags), FontRole.SEMI_BOLD,
                    18 if large else 11, panel.x + 12, panel.y + (39 if large else 23))

  def _traffic_badge(self, state: OnroadState, y: int) -> None:
    if state.traffic_display is None or state.alert.size == AlertSize.FULL:
      return
    traffic_color = (rl.Color(200, 32, 48, 255) if state.traffic_display.state == 'active' else
                     rl.Color(255, 155, 63, 255) if state.traffic_display.state in ('paused', 'unavailable_profile', 'unavailable_source') else
                     rl.Color(165, 175, 180, 255))
    x = 1390 + (self.projection_viewport[0] if self.projection_viewport else state.viewport_width) - 1860
    self.fonts.draw(state.traffic_display.label, FontRole.SEMI_BOLD, 25, x, y, traffic_color)

  def _driving_mode(self, state: OnroadState, center_shift: float = 0) -> None:
    profile = self.fonts.profile
    position = placement(state.customization, profile, MODE_WIDGET)
    if (not position["enabled"] or state.alert.size != AlertSize.NONE or
        state.appearance.camera_view == CameraViewChoice.DRIVER or state.reverse_driver_camera):
      return
    width, height = widget_size(state.customization, profile, MODE_WIDGET)
    size = 40 if profile == Profile.LARGE else 18
    role = FontRole.SEMI_BOLD
    lines = [""]
    for word in driving_mode_description(state).split():
      candidate = f"{lines[-1]} {word}".strip()
      if lines[-1] and self.fonts.measure(candidate, role, size).width > width - 4:
        lines.append(word)
      else:
        lines[-1] = candidate
    line_height = size * profile.font_scale * 1.2
    top = position["y"] + (height - len(lines) * line_height) / 2
    color = rl.Color(*rgba(state.customization, "text", profile, MODE_WIDGET))
    shadow = rl.Color(0, 0, 0, min(color.a, 170))
    shadow_offset = 2 if profile == Profile.LARGE else 1
    for index, text in enumerate(lines):
      x = position["x"] + center_shift + (width - self.fonts.measure(text, role, size).width) / 2
      ink_top, ink_bottom = self.fonts.vertical_ink(text, role, size)
      y = top + index * line_height + (line_height - ink_bottom + ink_top) / 2 - ink_top
      self.fonts.draw(text, role, size, x + shadow_offset, y + shadow_offset, shadow)
      self.fonts.draw(text, role, size, x, y, color)

  def _clock(self, state: OnroadState, center_shift: float = 0) -> None:
    profile = self.fonts.profile
    position = placement(state.customization, profile, CLOCK_WIDGET)
    if (not position["enabled"] or state.alert.size != AlertSize.NONE or
        state.appearance.camera_view == CameraViewChoice.DRIVER or state.reverse_driver_camera):
      return
    width, height = widget_size(state.customization, profile, CLOCK_WIDGET)
    size = 42 if profile == Profile.LARGE else 20
    role = FontRole.SEMI_BOLD
    now = self.wall_clock()
    if now is None:
      return
    text = clock_text(now, use_24_hour=state.customization.get("clock24Hour", False))
    measured = self.fonts.measure(text, role, size)
    x = position["x"] + center_shift + (width - measured.width) / 2
    y = position["y"] + (height - measured.height) / 2
    color = rl.Color(*rgba(state.customization, "text", profile, CLOCK_WIDGET))
    offset_px = 2 if profile == Profile.LARGE else 1
    self.fonts.draw(text, role, size, x + offset_px, y + offset_px, rl.Color(0, 0, 0, min(color.a, 170)))
    self.fonts.draw(text, role, size, x, y, color)

  def _ordered_widgets(self, content, state, *, driver_camera=False, stock_layer=None, only_widget=None):
    profile = str(self.fonts.profile)
    large = profile == "large"
    width = self.projection_viewport[0] if self.projection_viewport else state.viewport_width
    callbacks = {}
    def submit(key, draw):
      callbacks[key] = draw
    pip = getattr(self, "pip_layer", None) if state.camera_available else getattr(self, "preview_pip_layer", None)
    if pip is not None and (large or not driver_camera and state.appearance.camera_view != CameraViewChoice.NONE):
      pip(content, state, submit=submit)
    if large:
      submit('following_distance', lambda: self.personality.render(state))
      if "nav_map" in state.customization["layouts"]["large"] and state.alert.size != AlertSize.FULL:
        if map_layer := getattr(self, "map_layer", None):
          submit("nav_map", lambda: map_layer(content, state))
      if "nav_card" in state.customization["layouts"]["large"]:
        submit("nav_card", lambda: self.navigation.render(state))
      for key in ("nav_home", "nav_work", "bookmark"):
        if key in state.customization["layouts"]["large"]:
          submit(key, lambda key=key: self.navigation_favorites.render(key, state))
      if "now_playing" in state.customization["layouts"]["large"]:
        if now_playing := getattr(self, "now_playing", None):
          submit("now_playing", lambda: now_playing.render(state))
      if "jetlink_status" in state.customization["layouts"]["large"]:
        if jetlink := getattr(self, "jetlink_status", None):
          submit("jetlink_status", lambda: jetlink.render(state))
      if "car_exit" in state.customization["layouts"]["large"]:
        if exit_layer := getattr(self, "projection_exit_layer", None):
          submit("car_exit", lambda: exit_layer(state))
      if state.alert.size != AlertSize.FULL:
        if placement(state.customization, profile, "cruise_limits")["enabled"]:
          submit("cruise_limits", lambda: self.unified_speed.render(content, state))
        submit("speed_limit_actions", lambda: self._slc_actions(state))
        if gauge := getattr(self, "cem_gauge", None):
          submit("cem_gauge", lambda: gauge.render(state))
      if placement(state.customization, profile, "current_speed")["enabled"]:
        speed_content = rl.Rectangle(content.x + (width - 1860) / 2, content.y, content.width, content.height)
        submit("current_speed", lambda: self.current_speed.render(speed_content, state))
      if state.alert.size == AlertSize.NONE:
        if placement(state.customization, profile, "steering_wheel")["enabled"]:
          submit("steering_wheel", lambda: self.steering_wheel.render(content, state))
        if placement(state.customization, profile, "torque_bar")["enabled"]:
          submit("torque_bar", lambda: self.torque_bar.render(content, state, width if self.projection_viewport else 2160))
        if state.stopped_duration_s is not None:
          submit(STOPPED_TIMER_WIDGET, lambda: self._stopped_timer(state, state.stopped_duration_s))
    else:
      if state.alert.size == AlertSize.NONE and not driver_camera:
        submit("speed_limit", lambda: self.compact_hud._speed_limit_sign(state))
        submit("max_speed", lambda: self.compact_hud.render_max_speed(state))
        submit("steering_wheel", lambda: self.compact_hud.render_steering_wheel(state))
        submit("speed_limit_actions", lambda: self._slc_actions(state))
        if placement(state.customization, profile, "torque_bar")["enabled"]:
          submit("torque_bar", lambda: self.torque_bar.render(content, state, 536))
        if state.stopped_duration_s is not None:
          submit(STOPPED_TIMER_WIDGET, lambda: self._stopped_timer(state, state.stopped_duration_s))
      if not driver_camera:
        if stock_layer is not None:
          submit("model_confidence", lambda: stock_layer(rl.Rectangle(0, 0, 536, 240), state))
          submit("following_distance", lambda: self.compact_sidebar.render_personality(rl.Rectangle(0, 0, 536, 240), state))
        else:
          for key in RAIL_WIDGETS:
            submit(key, lambda key=key: self.compact_sidebar.render(rl.Rectangle(0, 0, 536, 240), state, widget=key))
    if monitor := getattr(self, "driver_monitor_layer", None):
      submit("driver_monitor", lambda: monitor(content, state))
    submit(MODE_WIDGET, lambda: self._driving_mode(state, (width - 1860) / 2 if large else 0))
    submit(CLOCK_WIDGET, lambda: self._clock(state, (width - 1860) / 2 if large else 0))
    pending = manual_slc_confirmation(self.fonts.profile, state)
    for key in widget_order(state.customization, profile):
      if only_widget is not None and key != only_widget:
        continue
      if pending and key == 'speed_limit_actions':
        continue
      if draw := callbacks.get(key):
        draw()
    if pending and (only_widget is None or only_widget == ('cruise_limits' if large else 'speed_limit')) and not driver_camera:
      self._slc_actions(state)

  def _large(self, state: OnroadState) -> None:
    width, height = self.projection_viewport or (state.viewport_width, 1080)
    right_shift = width - 1860
    frame = rl.Rectangle(0, 0, width, height)
    content = rl.Rectangle(30, 30, width - 60, height - 60)
    # Current CameraView's no-frame placeholder stays at the disengaged blue.
    color = rl.Color(18, 40, 57, 255)
    edge = axis_status_color(state)
    rl.draw_rectangle_rec(content, color)
    clip.begin_scissor_mode(30, 30, int(content.width), int(content.height))
    try:
      if self.camera_layer is not None and state.camera_available:
        self.camera_layer(content, state)
      elif self.background_layer is not None:
        self.background_layer(content, state)
      ordered = bool(state.customization.get("widgetOrder", {}).get("large"))
      if ordered:
        rl.draw_rectangle_gradient_v(30, 30, int(content.width), 300, rl.Color(0, 0, 0, 114), rl.BLANK)
        self._ordered_widgets(content, state)
        if self.extra_overlays is not None:
          self.extra_overlays(content, state)
      else:
        pip_layer = getattr(self, "pip_layer", None)
        if pip_layer is not None and state.camera_available:
          pip_layer(content, state)
        map_layer = getattr(self, "map_layer", None)
        if map_layer is not None and state.alert.size != AlertSize.FULL:
          map_layer(content, state)
        rl.draw_rectangle_gradient_v(30, 30, int(content.width), 300, rl.Color(0, 0, 0, 114), rl.BLANK)
        if state.alert.size != AlertSize.FULL:
          if placement(state.customization, "large", "cruise_limits")["enabled"]:
            self.unified_speed.render(content, state)
          if not manual_slc_confirmation(self.fonts.profile, state):
            self._slc_actions(state)
          if gauge := getattr(self, "cem_gauge", None):
            gauge.render(state)
        if placement(state.customization, "large", "current_speed")["enabled"]:
          speed_content = rl.Rectangle(content.x + right_shift / 2, content.y, content.width, content.height)
          self.current_speed.render(speed_content, state)
        if state.alert.size == AlertSize.NONE:
          if placement(state.customization, "large", "steering_wheel")["enabled"]:
            self.steering_wheel.render(content, state)
          if placement(state.customization, "large", "torque_bar")["enabled"]:
            self.torque_bar.render(content, state, width if self.projection_viewport else 2160)
          if state.stopped_duration_s is not None:
            self._stopped_timer(state, state.stopped_duration_s)
        if self.extra_overlays is not None:
          self.extra_overlays(content, state)
        self.personality.render(state)
        if monitor_layer := getattr(self, "driver_monitor_layer", None):
          monitor_layer(content, state)
      if self.projection_viewport and "nav_card" not in state.customization["layouts"]["large"]:
        rl.rl_push_matrix()
        try:
          rl.rl_translatef(right_shift, 0, 0)
          self.navigation.render(state)
        finally:
          rl.rl_pop_matrix()
      elif not ordered or "nav_card" not in state.customization["layouts"]["large"]:
        self.navigation.render(state)
      if not ordered:
        for key in ("nav_home", "nav_work", "bookmark"):
          self.navigation_favorites.render(key, state)
        if now_playing := getattr(self, "now_playing", None):
          now_playing.render(state)
        if jetlink := getattr(self, "jetlink_status", None):
          jetlink.render(state)
        self._driving_mode(state, right_shift / 2)
        self._clock(state, right_shift / 2)
        if exit_layer := getattr(self, "projection_exit_layer", None):
          exit_layer(state)
      if not ordered and manual_slc_confirmation(self.fonts.profile, state):
        self._slc_actions(state)
      if not self.projection_viewport and (quick_select := getattr(self, 'quick_select_layer', None)) is not None:
        quick_select(content, state)
      self.alert.render(content, state.alert)
    finally:
      clip.end_scissor_mode()
    if not self.projection_viewport and state.appearance.show_developer_sidebar:
      render_live_sidebar(self.fonts, rl.Rectangle(width, 0, SIDEBAR_WIDTH, height), state.developer_metrics)
    # Projection has its own permanent escape control in this corner; the native
    # favorite-menu hint would overlap it and does not control projected input.
    if not self.projection_viewport and not state.quick_select_open and state.alert.size == AlertSize.NONE:
      render_corner_hint(content, cache=self._corner_cache)
    label = status_label(state)
    if label is not None:
      preview = state.visual_preview
      curve_color = glow_color(intensity(preview.curve_curvature)) if preview is not None and preview.curve_curvature is not None else \
        glow_color(intensity(state.curve.road_curvature)) if curve_controlling(state) and state.curve is not None else \
        rl.Color(112, 192, 216, 255)
      self.fonts.draw(label, FontRole.SEMI_BOLD, 29, 1390 + right_shift, 56,
                      curve_color)
    self._traffic_badge(state, 56 + 45 * int(label is not None))
    render_glow(content, state, border_width=30, time_s=state.observed_ns / 1e9 if state.observed_ns else rl.get_time())
    rl.draw_rectangle_lines_ex(frame, 30, rl.BLACK)
    rl.draw_rectangle_rounded_lines_ex(content, 0.12, 10, 30, edge)
    render_large_half_borders(state, state.observed_ns / 1e9 if state.observed_ns else rl.get_time(), content, int(height))
    self._preview_badge(state)

  def _compact(self, state: OnroadState) -> None:
    frame = rl.Rectangle(0, 0, 476, 240)
    driver_camera = state.appearance.camera_view == CameraViewChoice.DRIVER or state.reverse_driver_camera
    stock_layer = getattr(self, "stock_confidence_layer", None)
    source_ns = state.stock_confidence_source_stamp_ns if state.stock_confidence_source_fresh else None
    use_stock = (not driver_camera and state.appearance.camera_view != CameraViewChoice.NONE and
                 state.appearance.show_stock_confidence_ball and source_ns is not None and stock_layer is not None)
    if use_stock:
      last_source_ns = getattr(self, "_stock_confidence_last_source_ns", None)
      last_drive_frame = getattr(self, "_stock_confidence_last_drive_frame", None)
      if (last_source_ns is None or last_drive_frame != state.stock_confidence_drive_frame or
          source_ns < last_source_ns or source_ns - last_source_ns > 200_000_000):
        reset = getattr(self, "stock_confidence_reset", None)
        if reset is not None:
          reset()
      self._stock_confidence_last_source_ns = source_ns
      self._stock_confidence_last_drive_frame = state.stock_confidence_drive_frame
    else:
      self._stock_confidence_last_source_ns = None
    color = axis_status_color(state)
    if state.alert.size != AlertSize.NONE and (
        not (state.driver_override_active or state.aol_latched) or state.alert.critical or state.alert.size == AlertSize.FULL):
      color = rl.Color(18, 40, 57, 255)
    rl.draw_rectangle(0, 0, 536, 240, rl.BLACK)
    clip.begin_scissor_mode(0, 0, 476, 240)
    try:
      if self.camera_layer is not None and state.camera_available:
        self.camera_layer(frame, state)
      elif self.background_layer is not None:
        self.background_layer(frame, state)
      ordered = bool(state.customization.get("widgetOrder", {}).get("compact"))
      if ordered:
        self._prepare_compact_fade()
        rl.draw_texture_ex(self._fade, rl.Vector2(0, 0), 0, 1, rl.WHITE)
        clip.end_scissor_mode()
        clip.begin_scissor_mode(0, 0, 536, 240)
        self._ordered_widgets(frame, state, driver_camera=driver_camera, stock_layer=stock_layer if use_stock else None)
        clip.end_scissor_mode()
        clip.begin_scissor_mode(0, 0, 476, 240)
        if self.extra_overlays is not None:
          self.extra_overlays(frame, state)
      else:
        pip_layer = getattr(self, "pip_layer", None)
        if pip_layer is not None and state.camera_available and not driver_camera and \
           state.appearance.camera_view != CameraViewChoice.NONE:
          pip_layer(frame, state)
        self._prepare_compact_fade()
        rl.draw_texture_ex(self._fade, rl.Vector2(0, 0), 0, 1, rl.WHITE)
        if state.alert.size == AlertSize.NONE and not driver_camera:
          self.compact_hud.render(state)
          if placement(state.customization, "compact", "torque_bar")["enabled"]:
            self.torque_bar.render(frame, state, 536)
          if not manual_slc_confirmation(self.fonts.profile, state):
            self._slc_actions(state)
          if state.stopped_duration_s is not None:
            self._stopped_timer(state, state.stopped_duration_s)
        if self.extra_overlays is not None:
          self.extra_overlays(frame, state)
        if monitor_layer := getattr(self, "driver_monitor_layer", None):
          monitor_layer(frame, state)
      self.navigation.render(state)
      if not ordered:
        self._driving_mode(state)
        self._clock(state)
      if not ordered and not driver_camera and manual_slc_confirmation(self.fonts.profile, state):
        self._slc_actions(state)
      signals = state.border_signals
      direction = (-1 if signals.left_blinker else 1 if signals.right_blinker else 0) if signals else 0
      self.alert.render(frame, state.alert, signal_direction=direction)
    finally:
      clip.end_scissor_mode()
    self._preview_badge(state)
    if not driver_camera and not state.customization.get("widgetOrder", {}).get("compact"):
      rail = rl.Rectangle(0, 0, 536, 240)
      if use_stock:
        stock_layer(rail, state)
        self.compact_sidebar.render_personality(rail, state)
      else:
        self.compact_sidebar.render(rail, state)
    clip.begin_scissor_mode(0, 0, 476, 240)
    try:
      rl.draw_rectangle_rounded_lines_ex(rl.Rectangle(4, 4, 468, 232), 0.12, 16, 8, color)
    finally:
      clip.end_scissor_mode()
    render_compact_half_borders(state, state.observed_ns / 1e9 if state.observed_ns else rl.get_time())

  def _stopped_timer(self, state: OnroadState, seconds: int) -> None:
    profile = str(self.fonts.profile)
    position = state.customization["layouts"][profile].get(STOPPED_TIMER_WIDGET)
    if position is None or not position["enabled"]:
      return
    width, height = timer_size(profile)
    if slc_action_occludes(self.fonts.profile, state, (position['x'], position['y'], position['x'] + width, position['y'] + height)):
      return
    scale = height / 112
    minutes, remainder = duration_text(seconds)
    color = rl.Color(*duration_color(seconds), 255)
    # Line centers sit 53 and 101 px into the widget box.
    for text, role, initial_size, minimum_size, center_y, tint in (
      (minutes, FontRole.BOLD, 78, 28, 53, color),
      (remainder, FontRole.MEDIUM, 35, 16, 101, rl.Color(255, 255, 255, 242)),
    ):
      size = round(initial_size * scale)
      while size > round(minimum_size * scale) and self.fonts.measure(text, role, size).width > width:
        size -= 2
      measured = self.fonts.measure(text, role, size)
      x = position["x"] + (width - measured.width) / 2
      y = position["y"] + center_y * scale - measured.height / 2
      self.fonts.draw(text, role, size, x + 2, y + 2, rl.Color(0, 0, 0, 170))
      self.fonts.draw(text, role, size, x, y, tint)

  def close(self) -> None:
    self.quick_select_layer = None
    gui_app.remove_render_prepare(self._corner_cache.prepare)
    self._corner_cache.close()
    gui_app.remove_render_prepare(self.torque_bar.prepare)
    self.torque_bar.close()
    self.navigation.close()
    if self.fonts.profile == Profile.LARGE:
      self.unified_speed.close()
      self.steering_wheel.close()
    else:
      self.compact_hud.close()
      if self._fade is not None and rl.is_window_ready():
        rl.unload_texture(self._fade)
      self._fade = None
