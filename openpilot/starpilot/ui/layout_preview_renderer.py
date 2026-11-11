"""Parked onroad layout preview using the same native view as the driving UI."""

from typing import cast

from pathlib import Path
import json
import math
import struct
import time
from openpilot.common.filter_simple import FirstOrderFilter
import zlib

import numpy as np
from dataclasses import replace

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.layout_preview_transport import SCENES, validate_preview_document
from openpilot.starpilot.ui.developer_preview import OnroadVisualPreview
from openpilot.starpilot.ui.onroad_customization import (CAMERA_WIDGETS, PROFILES, ROAD_BASE_WIDTH, RUNTIME_PREVIEW_WIDGETS, fit_to_road, placement,
                                                         validate_document, widget_size)
from openpilot.starpilot.ui.onroad_widget_style import draw_widget_frame
from openpilot.starpilot.curve_speed.status import CurveObservation
from openpilot.starpilot.ui.onroad_state import ConditionalLead, speed_limit_bounds
from openpilot.starpilot.ui.onroad_state import AlertSize, ObservationKind, OnroadAlert, OnroadState, SourceReading, SpeedLimitObservation
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice, OnroadAppearance
from openpilot.starpilot.ui.onroad_dm import draw_monitor_art, monitor_bounds, monitor_visible
from openpilot.starpilot.ui.onroad_stopped_timer import PREVIEW_DURATION_S
from openpilot.starpilot.ui.layout_preview_sidebar import SIDEBAR_WIDTH, render_sidebar
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile, default_font_directory
from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
from openpilot.starpilot.ui.rainbow_path import RainbowPath
from openpilot.starpilot.ui.road_colors import edge_gradient, lane_color, path_mode, solid_gradient
from openpilot.starpilot.ui.onroad_customization import ROAD_COLORS
from openpilot.system.ui.lib.shader_polygon import Gradient, draw_polygon


CEM_SCENE_REASONS = {"cem_stop_light": "STOP LIGHT", "cem_lead": "LEAD", "cem_curve": "CURVE"}
IDLE_SECONDS = 30.0
MAX_SHEET_WIDTH = 2048
ASSET_DIRECTORY = Path(__file__).parents[2] / "selfdrive/assets"


def render_sample_road(rect: rl.Rectangle, state: OnroadState, profile: Profile) -> None:
  style = state.customization["roadColors"][str(profile)]

  def points(values):
    return np.array([(rect.x + x * rect.width, rect.y + y * rect.height) for x, y in values], dtype=np.float32)

  draw_polygon(rect, points([(0, 1), (.4, .45), (.55, .45), (.83, 1)]), rl.Color(48, 57, 67, 255))
  if profile == Profile.LARGE and "pathEdge" in style:
    for strip in ([(.18, 1), (.44, .45), (.447, .45), (.231, 1)],
                  [(.639, 1), (.503, .45), (.51, .45), (.69, 1)]):
      draw_polygon(rect, points(strip), gradient=edge_gradient(style["pathEdge"]))
    path = [(.231, 1), (.447, .45), (.503, .45), (.639, 1)]
  else:
    path = [(.18, 1), (.44, .45), (.51, .45), (.69, 1)]
  mode = path_mode(style)
  gradient = (RainbowPath().gradient() if mode == "rainbow" else
              solid_gradient(style.get("path", ROAD_COLORS["path"])) if mode == "color" else
              Gradient((0, 1), (0, 0), [rl.Color(13, 248, 122, 102), rl.Color(114, 255, 92, 89),
                                       rl.Color(114, 255, 92, 0)], [0, .5, 1]))
  draw_polygon(rect, points(path), gradient=gradient)
  for index, (bottom, top) in enumerate(((.16, .43), (.71, .52), (.02, .38), (.85, .57))):
    adjacent = profile == Profile.COMPACT and index < 2
    default = rl.Color(0, 255, 64, 178) if adjacent else rl.Color(255, 255, 255, 178)
    line = [(bottom - .002, 1), (top - .0005, .45), (top + .0005, .45), (bottom + .002, 1)]
    draw_polygon(rect, points(line), lane_color(style, adjacent, default, .7))


def sample_state(scene: str, document: dict) -> OnroadState:
  if scene not in SCENES:
    raise ValueError("Unknown layout preview scene")
  # SLC presentation changes do not disengage either driving axis. Only the explicit
  # cruise-off/lateral-only/longitudinal-only scenes change engagement.
  lateral = scene not in ('long_only', 'slc_disengaged')
  longitudinal = scene not in ('aol', 'slc_disengaged')
  pending = scene in ('slc_pending', 'slc_notice')
  from openpilot.starpilot.ui.weather import WeatherReading
  state = OnroadState(weather=WeatherReading(21, 2, True), engaged=lateral or longitudinal, camera_available=False,
                     speed_mps=0.0 if scene in ("cem_lead", "stopped") else 22.0, cruise_kph=88.0,
                     slc_offset_schedule=OffsetSchedule((OffsetBand(0., None, 2.),)),
                     stopped_duration_s=PREVIEW_DURATION_S if scene == "stopped" else None,
                     speed_limit=SpeedLimitObservation(kind=ObservationKind.VALID, source="dashboard",
                                                       speed_limit_mps=22.0 if pending else 20.0,
                                                       accepted_speed_limit_mps=20.0, accepted_source='dashboard',
                                                       accepted_adjusted_limit_mps=22.0, effective_cluster_target_mps=22.0, effective_cap_mps=22.0,
                                                       offset_mps=2.0, limiting_max_set=True,
                                                       pending_adjusted_limit_mps=24.0 if pending else None,
                                                       presentation_adjusted_limit_mps=24.0 if pending else 22.0,
                                                       pending_speed_limit_mps=22.0 if pending else None,
                                                       session_id="preview",
                                                       source_readings=(SourceReading('dashboard', True, 'valid', 22 if pending else 20),
                                                                        SourceReading('map', True, 'valid', 20),
                                                                        SourceReading('vision', True, 'unknown')),
                                                       decision_id=1, presentation_id=1, action_enabled=longitudinal),
                     alert=OnroadAlert(), appearance=OnroadAppearance(wheel_pedal_feedback=True,
                                                                      show_speed_limit_sign=True),
                     wheel_acceleration=-2.0 if scene == "braking" else 0.0,
                     metric=False, experimental_available=True,
                     experimental_enabled=scene == "experimental" or scene in CEM_SCENE_REASONS, lateral_active=lateral,
                     longitudinal_active=longitudinal, stock_cruise_active=False,
                     lead_detected=True, gas_pressed_override=scene == 'slc_pedal',
                     slc_system_long_available=longitudinal, slc_wheel_confirmation_available=longitudinal, torque_utilization=0.78 if lateral else 0.0,
                     customization=document,
                     slc_prompt_open=scene == 'slc_pending',
                     visual_preview=OnroadVisualPreview(cem_reason=CEM_SCENE_REASONS[scene], layout_sample=True) if scene in CEM_SCENE_REASONS else None)

  if scene == 'cem_lead':
    state = replace(state, conditional_lead=ConditionalLead(12.0, 0.0))
  elif scene == 'cem_curve':
    state = replace(state, curve=CurveObservation(False, None, None, False, False, False, 0.0, 2.0, 0.0,
                                                 'preview', 'idle', 'none', False, False, 0.005))
  observation = state.speed_limit
  if scene == 'slc_max':
    state = replace(state, cruise_kph=65)
    observation = replace(observation, limiting_max_set=False)
  elif scene == 'slc_equal':
    state = replace(state, cruise_kph=79.2)
    observation = replace(observation, limiting_max_set=False)
  elif scene == 'slc_pedal':
    state = replace(state, longitudinal_overridden=True)
  elif scene == 'slc_retained':
    observation = replace(observation, driver_override_active=True,
                          override_basis='persistent', retained_override=True, effective_cluster_target_mps=24.0)
  elif scene == 'slc_unavailable':
    observation = SpeedLimitObservation(kind=ObservationKind.STALE, status='display_hold')
  elif scene == 'slc_disengaged':
    observation = replace(observation, action_enabled=True, effective_cluster_target_mps=None)
  elif scene == 'slc_display':
    observation = replace(observation, action_enabled=False, effective_cluster_target_mps=None)
  return replace(state, speed_limit=observation)


def _chunk(kind: bytes, data: bytes) -> bytes:
  payload = kind + data
  return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xffffffff)


def _straight_rgba(pixels: bytes) -> bytes:
  """PNG uses straight alpha; the transparent GL scratch target stores premultiplied RGB."""
  values = np.frombuffer(pixels, dtype=np.uint8).reshape(-1, 4).astype(np.uint16)
  alpha = values[:, 3:4]
  values[:, :3] = np.where(alpha > 0, np.minimum(255, (values[:, :3] * 255 + alpha // 2) // np.maximum(alpha, 1)), 0)
  return values.astype(np.uint8).tobytes()


def _png_rgba(width: int, height: int, pixels: bytes, *, widget_art: dict | None = None) -> bytes:
  if len(pixels) != width * height * 4:
    raise ValueError("Invalid layout preview pixel buffer")
  stride = width * 4
  rows = b"".join(b"\0" + pixels[y * stride:(y + 1) * stride] for y in range(height))
  return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
          (_chunk(b"tEXt", b"starpilot-widget-art\0" + json.dumps(widget_art, separators=(",", ":"), allow_nan=False).encode())
           if widget_art is not None else b"") +
          _chunk(b"IDAT", zlib.compress(rows, 3)) + _chunk(b"IEND", b""))


class _DriverMonitorArt:
  def __init__(self, profile: Profile):
    from openpilot.selfdrive.ui.mici.onroad.driver_state import DriverStateRenderer

    self.profile = profile
    self.textures: dict[str, rl.Texture] = {}
    self.renderer = DriverStateRenderer.__new__(DriverStateRenderer)
    self.renderer._lines = False
    self.renderer._is_active = True
    self.renderer._force_active = False
    self.renderer._awareness_unfull = False
    self.renderer._fade_filter = FirstOrderFilter(1.0, 0.05, 1 / 60)
    self.renderer._rotation_filter = FirstOrderFilter(90.0, 0.05, 1 / 60)
    self.renderer._color_fade_filter = FirstOrderFilter(1.0, 0.05, 1 / 60)
    directory = ASSET_DIRECTORY / "icons_mici/onroad/driver_monitoring"
    try:
      for name in ("dm_background", "dm_person", "dm_cone"):
        image = rl.load_image(str(directory / f"{name}.png"))
        try:
          size = 128 if profile == Profile.LARGE else 60
          if name != "dm_background":
            size = round(52 / 60 * size)
          rl.image_resize(image, size, size)
          texture = rl.load_texture_from_image(image)
        finally:
          rl.unload_image(image)
        if not texture.id:
          raise RuntimeError(f"Unable to load driver monitoring artwork: {name}")
        self.textures[name] = texture
      self.renderer._dm_background = self.textures["dm_background"]
      self.renderer._dm_person = self.textures["dm_person"]
      self.renderer._dm_cone = self.textures["dm_cone"]
    except Exception:
      self.close()
      raise

  def render(self, _content: rl.Rectangle, state: OnroadState, *, top_icons: bool = False, y_offset=0) -> None:
    bounds = monitor_bounds(state.customization, self.profile, state.right_hand_drive)
    bounds.y += y_offset
    if not monitor_visible(self.profile, state, fresh=True, onroad=True, top_icons=top_icons, bounds=bounds):
      return
    draw_widget_frame(bounds, state.customization, self.profile, "driver_monitor")
    draw_monitor_art(self.renderer, bounds, self.profile)

  def close(self) -> None:
    if rl.is_window_ready():
      for texture in self.textures.values():
        rl.unload_texture(texture)
    self.textures.clear()


class _Canvas:
  def __init__(self, profile: Profile, viewport=None):
    from openpilot.starpilot.ui.onroad import OnroadView

    self.profile = profile
    self.viewport = viewport
    self.fonts: BitmapFonts | None = None
    self.view: OnroadView | None = None
    self.monitor: _DriverMonitorArt | None = None
    self.target: rl.RenderTexture | None = None
    self.map = None
    try:
      self.fonts = BitmapFonts(profile, default_font_directory())
      layers = {'background_layer': self._render_background}
      if viewport is None:
        self.view = OnroadView(self.fonts, ASSET_DIRECTORY, background_layer=layers['background_layer'])
      else:
        from openpilot.starpilot.system.starport.projection_onroad import ProjectionOnroad
        self.view = ProjectionOnroad.create_view(OnroadView, self.fonts, viewport=viewport, **layers)
        from openpilot.starpilot.system.starport.projection_exit import ProjectionExit
        self.view.projection_exit_layer = ProjectionExit(rl).render
        self.view.now_playing.track = {'source': 'spotify', 'status': 'playing', 'title': 'Midnight City', 'artist': 'M83', 'album': '',
                                       'durationMs': 243000, 'position': {'ms': 92000, 'at': 0, 'speed': 0.0}}
      if profile == Profile.LARGE:
        from openpilot.starpilot.ui.onroad_map import MapOverlay, SampleTileReader
        self.map = MapOverlay(reader=SampleTileReader(), fonts=self.fonts)
        self.view.map_layer = self._render_map
        from openpilot.starpilot.models.jetlink_status import JetlinkStatus
        from openpilot.starpilot.ui.onroad_jetlink import JetlinkStatusWidget
        cast(JetlinkStatusWidget, self.view.jetlink_status).status = JetlinkStatus('iPhone', 'active', 24.6, 'connected')
      self.monitor = _DriverMonitorArt(profile)
      self.view.driver_monitor_layer = self._render_driver_monitor
      self.view.preview_pip_layer = self._render_preview_pip
      self.target = rl.load_render_texture(*(viewport or profile.size))
      if not self.target.id:
        raise RuntimeError("Unable to create layout preview target")
    except Exception:
      self.close()
      raise

  def _render_background(self, rect: rl.Rectangle, state: OnroadState) -> None:
    render_sample_road(rect, state, self.profile)
    if not state.customization.get("widgetOrder", {}).get(str(self.profile)) and self.viewport is None:
      self._render_side_cameras(state)

  @staticmethod
  def _map_placement(state: OnroadState):
    placed = state.customization["layouts"]["large"].get("nav_map")
    return placed if placed is not None and placed["enabled"] else None

  def _render_map(self, _rect: rl.Rectangle, state: OnroadState) -> None:
    placed = self._map_placement(state)
    if placed is not None and self.map is not None:
      shift = (self.viewport[0] if self.viewport else state.viewport_width) - 1860
      self.map.draw(rl.Rectangle(placed["x"] + shift, placed["y"], placed["width"], placed["height"]), placed["opacity"] / 100.0)

  def _render_preview_pip(self, rect, state, *, submit=None):
    if submit is not None:
      for key in CAMERA_WIDGETS:
        submit(key, lambda key=key: self._render_side_cameras(state, only=key))

  def _render_side_cameras(self, state: OnroadState, only=None) -> None:
    for key in CAMERA_WIDGETS:
      if only is not None and key != only:
        continue
      position = placement(state.customization, self.profile, key)
      if not position["enabled"]:
        continue
      widget = PROFILES[str(self.profile)]["widgets"][key]
      radius = widget["width"] / 2
      center = rl.Vector2(position["x"] + radius, position["y"] + radius)
      rl.draw_circle_v(center, radius, rl.Color(21, 44, 57, 230))
      rl.draw_ring(center, radius - 3, radius, 0, 360, 64, rl.Color(94, 229, 238, 255))

  def _render_driver_monitor(self, rect: rl.Rectangle, state: OnroadState, *, general: bool = False) -> None:
    top_icons = (not general and self.profile == Profile.COMPACT and state.alert.size == AlertSize.NONE and
                 state.appearance.camera_view != CameraViewChoice.DRIVER and not state.reverse_driver_camera and
                 self.view.compact_hud._set_speed_alpha.x > 0.01 and
                 placement(state.customization, self.profile, "max_speed")["enabled"])
    self.monitor.render(rect, state, top_icons=top_icons, y_offset=self.viewport[1] - 1080 if self.viewport else 0)

  def render(self, state: OnroadState, scene: str) -> bytes:
    if self.view is None or self.fonts is None or self.target is None:
      raise RuntimeError("Layout preview resources are closed")
    self._settle(state)
    width, height = self.viewport or self.profile.size
    placed = self._map_placement(state) if self.map is not None else None
    if placed is not None:
      from openpilot.starpilot.ui.onroad_map import sample_input
      self.map.prepare(sample_input(), placed["width"], placed["height"])  # offscreen, before the preview target
    rl.begin_texture_mode(self.target)
    try:
      rl.clear_background(rl.BLACK)
      self.view.render(state)
      if self.viewport is not None:
        # ProjectionOnroad adds this permanent brand after the shared onroad view.
        self.fonts.draw('StarPilot', FontRole.BRAND, 30, width - 210, height - 90)
      if self.profile == Profile.LARGE and self.viewport is None and state.appearance.show_developer_sidebar:
        render_sidebar(self.fonts, rl.Rectangle(width - SIDEBAR_WIDTH, 0, SIDEBAR_WIDTH, height))
    finally:
      rl.end_texture_mode()
    image = rl.load_image_from_texture(self.target.texture)
    try:
      rl.image_format(image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
      rl.image_flip_vertical(image)
      scale = min(1.0, 2560 / width, 1440 / height)
      if scale < 1.0:
        width, height = round(width * scale), round(height * scale)
        rl.image_resize(image, width, height)
      return _png_rgba(width, height, bytes(image.data[:width * height * 4]))
    finally:
      rl.unload_image(image)

  def render_widget_art(self, state: OnroadState, positions: dict, *, general: bool = False) -> bytes:
    """Transparent arrangement art from the same widgets/fonts as the live view."""
    if self.view is None or self.fonts is None or self.target is None:
      raise RuntimeError("Layout preview resources are closed")
    # Sprite crops use the saved 1860px road layout; the editor applies runtime anchor offsets.
    if self.profile == Profile.LARGE and self.viewport is None:
      state = replace(state, viewport_width=1860)
    self._settle(state)
    canvas_width, canvas_height = self.viewport or self.profile.size
    road_width = self.viewport[0] if self.viewport else state.viewport_width
    content = (rl.Rectangle(30, 30, road_width - 60, canvas_height - 60) if self.profile == Profile.LARGE
               else rl.Rectangle(0, 0, 476, 240))
    sprites = {}
    # A bounded subset covers the edited widgets without adding an onroad render loop.
    x = y = row_height = 0
    captures = []
    keys, extra = RUNTIME_PREVIEW_WIDGETS[str(self.profile)], {}
    if self.viewport is not None:
      from openpilot.starpilot.system.starport.projection_layout import SPRITE_WIDGETS, layout_metadata_for_viewport, placement_size
      metadata = layout_metadata_for_viewport(self.viewport)['widgets']
      extra = {key: metadata[key] for key in SPRITE_WIDGETS if positions.get(key, {}).get('enabled')}
      keys = (*keys, *extra)
    for key in keys:
      position = positions[key]
      if key in extra:
        widget = extra[key]
        logical_width, logical_height = placement_size(key, widget, position)
      else:
        widget = PROFILES[str(self.profile)]['widgets'][key]
        logical_width, logical_height = widget_size(state.customization, self.profile, key)
      crop_x = max(0, math.floor(position['x'] - widget.get('visualInsetLeft', 0)))
      crop_y = max(0, math.floor(position['y'] - widget.get('visualInsetTop', 0)))
      offset_x, offset_y = crop_x - position['x'], crop_y - position['y']
      width, height = math.ceil(logical_width - offset_x), math.ceil(logical_height - offset_y)
      if (key == ('speed_limit' if self.profile == Profile.COMPACT else 'cruise_limits') and
          state.speed_limit.pending_speed_limit_mps is None and (area := speed_limit_bounds(self.profile, state)) is not None):
        # The existing padded touch area includes the raised and SET-limited outlines.
        crop_x, crop_y = max(0, math.floor(min(crop_x, area[0]))), max(0, math.floor(min(crop_y, area[1])))
        width = math.ceil(max(position['x'] + logical_width, area[2]) - crop_x)
        height = math.ceil(max(position['y'] + logical_height, area[3]) - crop_y)
        offset_x, offset_y = crop_x - position['x'], crop_y - position['y']
      if state.speed_limit.pending_speed_limit_mps is not None and (area := widget.get('confirmation')) is not None:
        crop_x = math.floor(min(crop_x, position['x'] + area['x']))
        crop_y = math.floor(min(crop_y, position['y'] + area['y']))
        width = math.ceil(max(position['x'] + logical_width, position['x'] + area['x'] + area['width']) - crop_x)
        height = math.ceil(max(position['y'] + logical_height, position['y'] + area['y'] + area['height']) - crop_y)
        offset_x, offset_y = crop_x - position['x'], crop_y - position['y']
      if self.profile == Profile.COMPACT and key == 'speed_limit' and 0 <= crop_y < canvas_height:
        height = min(height, canvas_height - crop_y)
      if not (0 <= crop_x <= canvas_width - width and 0 <= crop_y <= canvas_height - height):
        if key in extra:
          continue
        raise ValueError("Widget art crop lies outside the canvas")
      rl.begin_texture_mode(self.target)
      try:
        rl.clear_background(rl.BLANK)
        # Match the runtime's transparent cache coverage before exporting straight-alpha PNG.
        rl.rl_set_blend_factors_separate(0x0302, 0x0303, 1, 0x0303, 0x8006, 0x8006)
        rl.begin_blend_mode(rl.BlendMode.BLEND_CUSTOM_SEPARATE)
        try:
          if general and key == 'driver_monitor':
            self._render_driver_monitor(content, state, general=True)
          else:
            # General edit shows a representative CEM sample even though its backdrop
            # remains engaged. Driving scenes always use their unmodified state.
            sample = {'cem_gauge': 'cem_curve', 'speed_limit_actions': 'slc_pending'}.get(key) if general else None
            widget_state = sample_state(sample, state.customization) if sample is not None else state
            if key == "rear_fog":
              from openpilot.starpilot.car.ford.rear_fog import boot_time_ns
              widget_state = replace(widget_state, ford_rear_fog_on=True, ford_rear_fog_source_ns=boot_time_ns())
            self._settle(widget_state)
            self.view._ordered_widgets(content, widget_state, only_widget=key)
        finally:
          rl.end_blend_mode()
      finally:
        rl.end_texture_mode()
      image = rl.load_image_from_texture(self.target.texture)
      try:
        rl.image_format(image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
        rl.image_flip_vertical(image)
        rl.image_crop(image, rl.Rectangle(crop_x, crop_y, width, height))
        data = _straight_rgba(bytes(image.data[:width * height * 4]))
      finally:
        rl.unload_image(image)
      # StarPort widgets with nothing to show keep the editor's sketch.
      if key in extra and not any(data[3::4]):
        continue
      if x + width > MAX_SHEET_WIDTH:
        x, y, row_height = 0, y + row_height, 0
      sprites[key] = {'x': x, 'y': y, 'width': width, 'height': height, 'offsetX': offset_x, 'offsetY': offset_y,
                      'visible': any(data[3::4])}
      x += width
      row_height = max(row_height, height)
      captures.append((key, data))
    sheet_width = max(slot['x'] + slot['width'] for slot in sprites.values())
    sheet_height = y + row_height
    if not (0 < sheet_width <= MAX_SHEET_WIDTH and 0 < sheet_height <= 1440):
      raise ValueError("Widget art sheet exceeds preview bounds")
    pixels = bytearray(sheet_width * sheet_height * 4)
    for key, data in captures:
      slot = sprites[key]
      for row in range(slot['height']):
        start = ((slot['y'] + row) * sheet_width + slot['x']) * 4
        pixels[start:start + slot['width'] * 4] = data[row * slot['width'] * 4:(row + 1) * slot['width'] * 4]
    return _png_rgba(sheet_width, sheet_height, bytes(pixels),
                     widget_art={'version': 1, 'width': sheet_width, 'height': sheet_height, 'sprites': sprites})

  def _settle(self, state: OnroadState) -> None:
    if self.view is None:
      return
    personality = self.view.personality
    personality._alpha_filter.x = 1.0
    personality._personality = state.personality
    personality._personality_changed_time = -2.5
    active_lead = state.lead_detected and state.cruise_active
    personality._lead_car_filters[0].x = 0.0 if active_lead else 1.0 if state.lead_detected else .35
    personality._lead_car_filters[1].x = float(active_lead)
    for i, (active, green) in enumerate(personality._distance_filters):
      active.x, green.x = float(i < (1 if state.traffic_mode else state.personality + 1)), 0.0
    if self.profile == Profile.COMPACT:
      hud = self.view.compact_hud
      # Freeze the synthetic sample inside the normal post-change set-speed blink.
      # Otherwise the first frame/reset or an old cached scene can hide its glyphs.
      now_ns = time.monotonic_ns()
      hud._cruise_drive_frame = state.drive_frame
      hud._last_cruise_display = (round(state.cruise_kph * (1 if state.metric else .621371))
                                  if state.cruise_active and state.cruise_kph is not None else None)
      hud._last_cruise_ns = now_ns if state.cruise_active else None
      hud._set_speed_changed_ns = now_ns - 1_000_000_000 if state.cruise_active else None
      hud._was_cruise_active = state.cruise_active
      hud._set_speed_alpha.x = 1.0 if state.cruise_active else 0.0
      hud._wheel_alpha.x = 255 * 0.9 if state.lateral_active else 0.0
      hud._wheel_y.x = 0.0 if state.lateral_active else 25.0
    self.view.torque_bar._torque_filter.x = state.torque_utilization if state.lateral_active else 0.0
    self.view.torque_bar._alpha_filter.x = 1.0 if state.lateral_active else 0.0
    if gauge := getattr(self.view, 'cem_gauge', None):
      from openpilot.starpilot.ui.onroad_cem_gauge import gauge_data, _get_road_height
      data = gauge_data(state)
      gauge._drive_frame = state.drive_frame
      gauge._lead_stopped = data is not None and data.indicator_extra == 'stopped'
      gauge._road_h_filter.x = gauge._current_road_h = _get_road_height(data)

  def close(self) -> None:
    if self.map is not None:
      self.map.close()
    self.map = None
    if self.target is not None and rl.is_window_ready():
      rl.unload_render_texture(self.target)
    self.target = None
    if self.monitor is not None:
      self.monitor.close()
    self.monitor = None
    if self.view is not None:
      self.view.close()
    self.view = None
    if self.fonts is not None:
      self.fonts.close()
    self.fonts = None


class LayoutPreviewRenderer:
  def __init__(self):
    self._canvas: _Canvas | None = None
    self._last_use = 0.0

  @property
  def has_resources(self) -> bool:
    return self._canvas is not None

  def __call__(self, payload: dict) -> bytes:
    if type(payload) is not dict or set(payload) not in ({"document", "profile", "scene"}, {"document", "profile", "scene", "art"}):
      raise ValueError("Invalid layout preview request")
    if "art" in payload and not (payload["art"] is True or payload["art"] == "backdrop"):
      raise ValueError("Invalid widget art request")
    viewport = None
    if payload['profile'] == 'projection':
      from openpilot.starpilot.system.starport.projection_layout import projection_customization
      layout = payload['document']['layout']
      viewport = (layout['canvas']['width'], layout['canvas']['height'])
      document = projection_customization(layout, validate_document(payload['document']['base']))
    else:
      document = validate_preview_document(payload['document'])
    if type(payload["profile"]) is not str or payload["profile"] not in ("large", "compact", "projection"):
      raise ValueError("Invalid layout preview profile")
    profile = Profile.LARGE if viewport else Profile(payload["profile"])
    scene = payload["scene"]
    sidebar = document.pop('developerSidebar', False)
    if profile == Profile.LARGE and viewport is None:
      width = profile.size[0] - (SIDEBAR_WIDTH if sidebar else 0)
      # Widget art is cropped from the saved 1860px road, as the editor places each sprite itself.
      document = fit_to_road(document, ROAD_BASE_WIDTH if payload.get('art') is True else width)
    state = sample_state(scene, document)
    if profile == Profile.LARGE and viewport is None:
      state = replace(state, viewport_width=width, appearance=replace(state.appearance, show_developer_sidebar=sidebar))
    elif viewport is not None:
      # Match ProjectionOnroad, which draws at the car canvas width.
      state = replace(state, viewport_width=viewport[0])
    if profile == Profile.LARGE:
      from openpilot.starpilot.ui.navigation_state import NavigationDisplay
      state = replace(state, navigation=NavigationDisplay(("preview", 1), "Turn left onto Desert Inn Road", "turn", "left",
                                                          152.0, 6800.0, 540.0, False, "turn", "right", 300.0,
                                                          ((True, ("left",), "left"), (True, ("left",), "left"), (False, ("straight",), ""),
                                                           (False, ("straight",), ""), (False, ("right",), ""))))
    if not rl.is_window_ready():
      raise RuntimeError("Layout preview requires the UI graphics context")
    if self._canvas is None or self._canvas.profile != profile or self._canvas.viewport != viewport:
      self.close()
      self._canvas = _Canvas(profile, viewport)
    self._last_use = time.monotonic()
    if payload.get('art') is True:
      positions = payload['document']['layout']['widgets'] if viewport else document['layouts'][str(profile)]
      return self._canvas.render_widget_art(state, positions, general=scene == 'general')
    if payload.get('art') == 'backdrop':
      # Everything the car draws except the movable widgets, which the editor places over it.
      # A non-empty order that names no widget keeps the ordered path and draws none of them.
      order = {**state.customization.get('widgetOrder', {}), str(profile): ['backdrop']}
      return self._canvas.render(replace(state, customization={**state.customization, 'widgetOrder': order}), scene)
    return self._canvas.render(state, scene)

  def expire(self) -> None:
    if self._canvas is not None and time.monotonic() - self._last_use >= IDLE_SECONDS:
      self.close()

  def close(self) -> None:
    if self._canvas is not None:
      self._canvas.close()
    self._canvas = None
