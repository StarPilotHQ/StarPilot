import os
import time
import numpy as np
import pyray as rl
from cereal import messaging, car, log
from opendbc.car import structs
from msgq.visionipc import VisionStreamType
from openpilot.common.constants import CV
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.mici.onroad.alert_renderer import AlertRenderer, ALERT_COLORS, AlertStatus
from openpilot.selfdrive.ui.mici.onroad.driver_state import DriverStateRenderer
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer, VISION_SPEED_LIMIT_PULSE_COLOR
from openpilot.selfdrive.ui.mici.onroad.model_renderer import ModelRenderer
from openpilot.selfdrive.ui.mici.onroad.confidence_ball import ConfidenceBall
from openpilot.selfdrive.ui.mici.onroad.sidebar_widgets import MiciSidebarWidgets
from openpilot.selfdrive.ui.mici.onroad.starpilot_status import (
  ENGAGED_COLOR,
  EXPERIMENTAL_COLOR,
  TRAFFIC_COLOR,
  get_border_color,
)
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.selfdrive.ui.onroad.starpilot.pip_sidecam import PipSideCamera
from openpilot.selfdrive.ui.onroad.starpilot.pulse_glide import get_pulse_glide_border_color
from openpilot.selfdrive.ui.onroad.starpilot.starpilot_border import get_traffic_border_colors
from openpilot.selfdrive.ui.lib.starpilot_visuals import get_border_width
from openpilot.starpilot.common.favorite_slots import (
  get_favorite_enum_state,
  is_enum_param,
  is_favorite_action_key,
  load_favorite_slots,
  toggle_favorite_slot,
)
from openpilot.system.ui.lib.application import FontWeight, gui_app, MousePos, MouseEvent, RECORD_HUD_ONLY, RECORD_CAMERA_ONLY, RECORD_COMBINED
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.wrap_text import wrap_text
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets import Widget
from openpilot.common.filter_simple import BounceFilter
from openpilot.common.transformations.camera import DEVICE_CAMERAS, DeviceCameraConfig, view_frame_from_device_frame
from openpilot.common.transformations.orientation import rot_from_euler
from enum import IntEnum

OpState = log.SelfdriveState.OpenpilotState
CALIBRATED = log.LiveCalibrationData.Status.calibrated
ROAD_CAM = VisionStreamType.VISION_STREAM_ROAD
WIDE_CAM = VisionStreamType.VISION_STREAM_WIDE_ROAD
DRIVER_CAM = VisionStreamType.VISION_STREAM_DRIVER
GEAR_SHIFTER_REVERSE = structs.CarState.GearShifter.reverse
DEFAULT_DEVICE_CAMERA = DEVICE_CAMERAS["tici", "ar0231"]

CAMERA_VIEW_AUTO = 0
CAMERA_VIEW_DRIVER = 1
CAMERA_VIEW_STANDARD = 2
CAMERA_VIEW_WIDE = 3
CAMERA_VIEW_NONE = 4

# Optional camera selection for desktop-only recording modes.
RECORD_CAMERA_VIEW = (
  os.getenv("RECORD_CAMERA_VIEW", "").strip().lower()
  if RECORD_HUD_ONLY or RECORD_CAMERA_ONLY or RECORD_COMBINED
  else ""
)


class BookmarkState(IntEnum):
  HIDDEN = 0
  DRAGGING = 1
  TRIGGERED = 2

WIDE_CAM_MAX_SPEED = 10.0  # m/s
ROAD_CAM_MIN_SPEED = 15.0  # m/s

CAM_Y_OFFSET = 20
REVERSE_DRIVER_CAMERA_DELAY_FRAMES = max(1, int(round(gui_app.target_fps * 0.5)))


class BookmarkIcon(Widget):
  PEEK_THRESHOLD = 50  # If icon peeks out this much, snap it fully visible
  FULL_VISIBLE_OFFSET = 200  # How far onscreen when fully visible
  HIDDEN_OFFSET = -50  # How far offscreen when hidden

  def __init__(self, bookmark_callback):
    super().__init__()
    self._bookmark_callback = bookmark_callback
    self._icon = gui_app.texture("icons_mici/onroad/bookmark.png", 180, 180)
    self._offset_filter = BounceFilter(0.0, 0.1, 1 / gui_app.target_fps)

    # State
    self._interacting = False
    self._state = BookmarkState.HIDDEN
    self._swipe_start_x = 0.0
    self._swipe_current_x = 0.0
    self._is_swiping = False
    self._is_swiping_left: bool = False
    self._triggered_time: float = 0.0

  def is_swiping_left(self) -> bool:
    """Check if currently swiping left (for scroller to disable)."""
    return self._is_swiping_left

  def interacting(self):
    interacting, self._interacting = self._interacting, False
    return interacting

  def _update_state(self):
    if self._state == BookmarkState.DRAGGING:
      # Allow pulling past activated position with rubber band effect
      swipe_offset = self._swipe_start_x - self._swipe_current_x
      swipe_offset = min(swipe_offset, self.FULL_VISIBLE_OFFSET + 50)
      self._offset_filter.update(swipe_offset)

    elif self._state == BookmarkState.TRIGGERED:
      # Continue animating to fully visible
      self._offset_filter.update(self.FULL_VISIBLE_OFFSET)
      # Stay in TRIGGERED state for 1 second
      if rl.get_time() - self._triggered_time >= 1.5:
        self._state = BookmarkState.HIDDEN

    elif self._state == BookmarkState.HIDDEN:
      self._offset_filter.update(self.HIDDEN_OFFSET)

      if self._offset_filter.x < 1e-3:
        self._interacting = False

  def _handle_mouse_event(self, mouse_event: MouseEvent):
    if not ui_state.started:
      return

    if mouse_event.left_pressed:
      # Store relative position within widget
      self._swipe_start_x = mouse_event.pos.x
      self._swipe_current_x = mouse_event.pos.x
      self._is_swiping = True
      self._is_swiping_left = False
      self._state = BookmarkState.DRAGGING

    elif mouse_event.left_down and self._is_swiping:
      self._swipe_current_x = mouse_event.pos.x
      swipe_offset = self._swipe_start_x - self._swipe_current_x
      self._is_swiping_left = swipe_offset > 0
      if self._is_swiping_left:
        self._interacting = True

    elif mouse_event.left_released:
      if self._is_swiping:
        swipe_distance = self._swipe_start_x - self._swipe_current_x

        # If peeking past threshold, transition to animating to fully visible and bookmark
        if swipe_distance > self.PEEK_THRESHOLD:
          self._state = BookmarkState.TRIGGERED
          self._triggered_time = rl.get_time()
          self._bookmark_callback()
        else:
          # Otherwise, transition back to hidden
          self._state = BookmarkState.HIDDEN

        # Reset swipe state
        self._is_swiping = False
        self._is_swiping_left = False

  def _render(self, _):
    """Render the bookmark icon."""
    if self._offset_filter.x > 0:
      icon_x = self.rect.x + self.rect.width - round(self._offset_filter.x)
      icon_y = self.rect.y + (self.rect.height - self._icon.height) / 2  # Vertically centered
      rl.draw_texture(self._icon, int(icon_x), int(icon_y), rl.WHITE)


class FavoriteSlotsOverlay(Widget):
  SLOT_COUNT = 3
  SLOTS_REFRESH_INTERVAL = 1.0
  MAX_TAP_TRAVEL = 24
  COLOR_TRANSITION_SECONDS = 0.65
  FADE_START_SECONDS = 2.0
  FEEDBACK_DURATION_SECONDS = 3.0

  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    self._button_rects: list[tuple[int, rl.Rectangle]] = []
    self._pressed_slot: int | None = None
    self._press_pos: MousePos | None = None
    self._max_tap_travel = 0.0
    self._feedback_slot: int | None = None
    self._feedback_started_at = -self.FEEDBACK_DURATION_SECONDS
    self._feedback_value: bool | None = None
    self._interacting = False
    self._visible_slots_cache: list[tuple[int, dict]] = []
    self._visible_slots_cached_at = float("-inf")

  def interacting(self):
    interacting, self._interacting = self._interacting, False
    return interacting

  def _visible_slots(self, force: bool = False) -> list[tuple[int, dict]]:
    now = time.monotonic()
    if not force and now - self._visible_slots_cached_at < self.SLOTS_REFRESH_INTERVAL:
      return self._visible_slots_cache

    visible = []
    for index, slot in enumerate(load_favorite_slots(ui_state.ui_params)):
      if slot.get("enabled") and slot.get("show_onroad") and slot.get("key"):
        visible.append((index, slot))
    self._visible_slots_cache = visible
    self._visible_slots_cached_at = now
    return self._visible_slots_cache

  def _slot_rects(self, rect: rl.Rectangle, slots: list[tuple[int, dict]]) -> list[tuple[int, rl.Rectangle]]:
    slot_width = rect.width / self.SLOT_COUNT
    return [
      (slot_index, rl.Rectangle(rect.x + slot_index * slot_width, rect.y, slot_width, rect.height))
      for slot_index, _slot in slots
    ]

  def _fit_label(self, label: str, max_width: float, max_height: float) -> tuple[list[str], int]:
    label = label or "Favorite"
    lines = [label]
    for font_size in range(30, 17, -1):
      if any(measure_text_cached(self._font, word, font_size).x > max_width for word in label.split()):
        continue
      lines = wrap_text(self._font, label, font_size, int(max_width)) or [label]
      line_height = font_size * 1.12
      if len(lines) <= 3 and len(lines) * line_height <= max_height:
        return lines, font_size
    return lines[:3], 18

  @staticmethod
  def _ease_out_cubic(progress: float) -> float:
    progress = float(np.clip(progress, 0.0, 1.0))
    return 1.0 - (1.0 - progress) ** 3

  @staticmethod
  def _blend_color(start: rl.Color, end: rl.Color, progress: float, alpha: int) -> rl.Color:
    progress = float(np.clip(progress, 0.0, 1.0))
    return rl.Color(
      round(start.r + (end.r - start.r) * progress),
      round(start.g + (end.g - start.g) * progress),
      round(start.b + (end.b - start.b) * progress),
      alpha,
    )

  def _feedback_alpha(self, elapsed: float) -> float:
    if elapsed < self.FADE_START_SECONDS:
      return 1.0
    fade_duration = self.FEEDBACK_DURATION_SECONDS - self.FADE_START_SECONDS
    return float(np.clip(1.0 - (elapsed - self.FADE_START_SECONDS) / fade_duration, 0.0, 1.0))

  def _draw_feedback(self, slot_rect: rl.Rectangle, slot: dict, elapsed: float, held: bool) -> None:
    alpha_scale = 1.0 if held else self._feedback_alpha(elapsed)
    if alpha_scale <= 0.0:
      return

    color_progress = self._ease_out_cubic(elapsed / self.COLOR_TRANSITION_SECONDS)
    alpha = round(255 * alpha_scale)
    accent = self._blend_color(VISION_SPEED_LIMIT_PULSE_COLOR, rl.Color(255, 255, 255, 255), color_progress, alpha)

    panel_margin = 12
    panel_width = max(96.0, slot_rect.width - 2 * panel_margin)
    panel_height = min(132.0, slot_rect.height * 0.5)
    panel_rect = rl.Rectangle(
      slot_rect.x + (slot_rect.width - panel_width) / 2,
      slot_rect.y + (slot_rect.height - panel_height) / 2,
      panel_width,
      panel_height,
    )

    # A restrained initial glow gives the same purple acknowledgement as the
    # vision speed-limit pulse without leaving persistent controls onscreen.
    glow_strength = (1.0 - color_progress) * alpha_scale
    for expansion, opacity in ((8, 22), (4, 44)):
      glow_rect = rl.Rectangle(
        panel_rect.x - expansion,
        panel_rect.y - expansion,
        panel_rect.width + 2 * expansion,
        panel_rect.height + 2 * expansion,
      )
      glow = rl.Color(
        VISION_SPEED_LIMIT_PULSE_COLOR.r,
        VISION_SPEED_LIMIT_PULSE_COLOR.g,
        VISION_SPEED_LIMIT_PULSE_COLOR.b,
        round(opacity * glow_strength),
      )
      rl.draw_rectangle_rounded_lines_ex(glow_rect, 0.16, 12, 2, glow)

    rl.draw_rectangle_rounded(panel_rect, 0.16, 12, rl.Color(0, 0, 0, round(178 * alpha_scale)))
    rl.draw_rectangle_rounded_lines_ex(panel_rect, 0.16, 12, 3, accent)

    label = slot.get("label") or slot.get("key") or "Favorite"
    key = slot.get("key")
    if is_favorite_action_key(key):
      state_text = "PRESS"
    elif is_enum_param(key):
      _curr, _idx, active_label, _opts = get_favorite_enum_state(key, ui_state.ui_params)
      state_text = active_label.upper() if active_label else "CYCLE"
    else:
      state_text = "ON" if self._feedback_value else "OFF"

    lines, font_size = self._fit_label(label, panel_rect.width - 20, panel_rect.height - 46)
    line_height = font_size * 1.08
    label_height = len(lines) * line_height
    text_y = panel_rect.y + 12 + (panel_rect.height - 42 - label_height) / 2
    for line in lines:
      text_size = measure_text_cached(self._font, line, font_size)
      text_x = panel_rect.x + (panel_rect.width - text_size.x) / 2
      rl.draw_text_ex(self._font, line, rl.Vector2(text_x, text_y), font_size, 0, accent)
      text_y += line_height

    state_size = measure_text_cached(self._font, state_text, 20)
    state_pos = rl.Vector2(panel_rect.x + (panel_rect.width - state_size.x) / 2, panel_rect.y + panel_rect.height - 30)
    rl.draw_text_ex(self._font, state_text, state_pos, 20, 0, rl.Color(255, 255, 255, round(210 * alpha_scale)))

  def _render(self, rect: rl.Rectangle):
    visible_slots = self._visible_slots()
    self._button_rects = self._slot_rects(rect, visible_slots)
    slot_by_index = dict(visible_slots)

    if self._feedback_slot not in slot_by_index:
      self._feedback_slot = None
      return

    elapsed = max(0.0, rl.get_time() - self._feedback_started_at)
    held = self._pressed_slot == self._feedback_slot
    if not held and elapsed >= self.FEEDBACK_DURATION_SECONDS:
      self._feedback_slot = None
      return

    feedback_rect = next(button_rect for slot_index, button_rect in self._button_rects if slot_index == self._feedback_slot)
    self._draw_feedback(feedback_rect, slot_by_index[self._feedback_slot], elapsed, held)

  def _slot_at(self, pos: MousePos) -> int | None:
    for slot_index, rect in self._button_rects:
      if rl.check_collision_point_rec(pos, rect):
        return slot_index
    return None

  def _handle_mouse_press(self, mouse_pos: MousePos):
    self._pressed_slot = self._slot_at(mouse_pos)
    if self._pressed_slot is not None:
      self._press_pos = mouse_pos
      self._max_tap_travel = 0.0
      self._feedback_slot = self._pressed_slot
      self._feedback_started_at = rl.get_time()
      slot = dict(self._visible_slots()).get(self._pressed_slot, {})
      key = slot.get("key")
      if is_favorite_action_key(key) or is_enum_param(key):
        self._feedback_value = None
      else:
        self._feedback_value = not ui_state.ui_params.get_bool(key) if key else None
      self._interacting = True

  def _handle_mouse_event(self, mouse_event: MouseEvent):
    if self._pressed_slot is None or self._press_pos is None:
      return
    travel = ((mouse_event.pos.x - self._press_pos.x) ** 2 + (mouse_event.pos.y - self._press_pos.y) ** 2) ** 0.5
    self._max_tap_travel = max(self._max_tap_travel, travel)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    released_slot = self._slot_at(mouse_pos)
    valid_tap = (
      self._pressed_slot is not None and
      released_slot == self._pressed_slot and
      self._max_tap_travel <= self.MAX_TAP_TRAVEL
    )
    if valid_tap and toggle_favorite_slot(self._pressed_slot, ui_state.ui_params, ui_state.params_memory):
      self._feedback_slot = self._pressed_slot
      self._feedback_started_at = rl.get_time()
      self._interacting = True
    else:
      self._feedback_slot = None
    self._pressed_slot = None
    self._press_pos = None
    self._max_tap_travel = 0.0


class MinSteerSpeedBanner(Widget):
  """One-shot-per-drive banner shown for the full first below-min-steer interval."""

  def __init__(self):
    super().__init__()
    self._shown_this_drive = False
    self._showing_interval = False
    self._has_been_above_min = False
    self._was_under_min = False
    self._last_started_frame = -1
    self._label = UnifiedLabel(
      "",
      34,
      FontWeight.BOLD,
      text_color=rl.WHITE,
      alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
      alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_MIDDLE,
    )

  def _reset(self):
    self._shown_this_drive = False
    self._showing_interval = False
    self._has_been_above_min = False
    self._was_under_min = False

  @staticmethod
  def _get_message(min_steer_speed: float) -> str:
    speed_units = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    speed = int(round(min_steer_speed * speed_units))
    unit = "km/h" if ui_state.is_metric else "mph"
    return f"Steer Unavailable Under {speed} {unit}"

  def _update_state(self):
    if not ui_state.started:
      self._last_started_frame = -1
      self._reset()
      return

    if ui_state.started_frame != self._last_started_frame:
      self._last_started_frame = ui_state.started_frame
      self._reset()

    sm = ui_state.sm
    if sm.recv_frame["carParams"] < ui_state.started_frame or sm.recv_frame["carState"] < ui_state.started_frame:
      return

    min_steer_speed = float(sm["carParams"].minSteerSpeed)
    if min_steer_speed <= 0:
      self._showing_interval = False
      self._was_under_min = False
      return

    under_min = float(sm["carState"].vEgo) < min_steer_speed
    if not under_min:
      self._has_been_above_min = True

    crossed_below = under_min and not self._was_under_min
    if (not self._shown_this_drive) and crossed_below and self._has_been_above_min:
      self._showing_interval = True
      self._shown_this_drive = True

    if self._showing_interval and not under_min:
      self._showing_interval = False

    self._was_under_min = under_min
    if self._showing_interval:
      self._label.set_text(self._get_message(min_steer_speed))

  def _render(self, rect):
    self._update_state()
    if not self._showing_interval:
      return

    color = ALERT_COLORS[AlertStatus.userPrompt]
    color = rl.Color(color.r, color.g, color.b, int(255 * 0.9))
    translucent = rl.Color(color.r, color.g, color.b, 0)
    dropdown_height = min(170, int(rect.height * 0.7))
    solid_height = max(26, int(dropdown_height * 0.2))

    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), solid_height, color)
    rl.draw_rectangle_gradient_v(
      int(rect.x),
      int(rect.y + solid_height),
      int(rect.width),
      int(dropdown_height - solid_height),
      color,
      translucent,
    )

    text_rect = rl.Rectangle(rect.x + 26, rect.y - 2, rect.width - 52, dropdown_height)
    self._label.set_text_color(rl.Color(255, 255, 255, 242))
    self._label.render(text_rect)


class StandstillTimerOverlay:
  def __init__(self):
    self._last_started_frame = -1
    self._standstill_duration = 0
    self._standstill_started_at: float | None = None
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)

  def _reset(self):
    self._standstill_duration = 0
    self._standstill_started_at = None

  @staticmethod
  def _blend_colors(start: rl.Color, end: rl.Color, transition: float) -> rl.Color:
    transition = float(np.clip(transition, 0.0, 1.0))
    return rl.Color(
      int(start.r + transition * (end.r - start.r)),
      int(start.g + transition * (end.g - start.g)),
      int(start.b + transition * (end.b - start.b)),
      255,
    )

  def _get_duration_color(self) -> rl.Color:
    if self._standstill_duration < 60:
      return ENGAGED_COLOR
    if self._standstill_duration < 150:
      transition = (self._standstill_duration - 60) / 90.0
      return self._blend_colors(ENGAGED_COLOR, EXPERIMENTAL_COLOR, transition)
    if self._standstill_duration < 300:
      transition = (self._standstill_duration - 150) / 150.0
      return self._blend_colors(EXPERIMENTAL_COLOR, TRAFFIC_COLOR, transition)
    return TRAFFIC_COLOR

  def _update_state(self, in_reverse: bool) -> None:
    if not ui_state.started:
      self._last_started_frame = -1
      self._reset()
      return

    if ui_state.started_frame != self._last_started_frame:
      self._last_started_frame = ui_state.started_frame
      self._reset()

    if ui_state.sm.recv_frame["carState"] < ui_state.started_frame:
      self._reset()
      return

    if in_reverse or not ui_state.ui_params.get_bool("StoppedTimer"):
      self._reset()
      return

    if not ui_state.sm["carState"].standstill:
      self._reset()
      return

    now = time.monotonic()
    if self._standstill_started_at is None:
      self._standstill_started_at = now
      self._standstill_duration = 0
      return

    if now - ui_state.started_time < 60.0:
      self._standstill_duration = 0
      return

    self._standstill_duration = int(now - self._standstill_started_at)

  @staticmethod
  def _format_duration_text(total_seconds: int) -> tuple[str, str]:
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    minute_text = f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    second_text = f"{seconds} second" if seconds == 1 else f"{seconds} seconds"
    return minute_text, second_text

  def _draw_centered_text(self, rect: rl.Rectangle, text: str, y: float, font: rl.Font, font_size: int, color: rl.Color) -> None:
    text_size = measure_text_cached(font, text, font_size)
    text_pos = rl.Vector2(rect.x + rect.width / 2 - text_size.x / 2, rect.y + y - text_size.y / 2)
    shadow_pos = rl.Vector2(text_pos.x + 2, text_pos.y + 2)
    rl.draw_text_ex(font, text, shadow_pos, font_size, 0, rl.Color(0, 0, 0, 170))
    rl.draw_text_ex(font, text, text_pos, font_size, 0, color)

  @staticmethod
  def _fit_font_size(font: rl.Font, text: str, initial_size: int, max_width: float, minimum_size: int) -> int:
    font_size = max(initial_size, minimum_size)
    while font_size > minimum_size and measure_text_cached(font, text, font_size).x > max_width:
      font_size -= 2
    return font_size

  def render(self, rect: rl.Rectangle, in_reverse: bool) -> bool:
    self._update_state(in_reverse)
    if self._standstill_duration == 0:
      return False

    minute_text, second_text = self._format_duration_text(self._standstill_duration)
    duration_color = self._get_duration_color()
    max_text_width = max(rect.width - 36, 120)
    minute_font_size = self._fit_font_size(self._font_bold, minute_text, int(rect.height * 0.34), max_text_width, 28)
    second_font_size = self._fit_font_size(self._font_medium, second_text, int(rect.height * 0.15), max_text_width, 16)
    self._draw_centered_text(rect, minute_text, rect.height * 0.42, self._font_bold, minute_font_size, duration_color)
    self._draw_centered_text(rect, second_text, rect.height * 0.62, self._font_medium, second_font_size, rl.Color(255, 255, 255, 242))
    return True


class AugmentedRoadView(CameraView):
  _use_roi_upload = True

  def __init__(self, bookmark_callback=None, stream_type: VisionStreamType = VisionStreamType.VISION_STREAM_ROAD):
    super().__init__("camerad", stream_type)
    self._bookmark_callback = bookmark_callback
    self._set_placeholder_color(rl.BLACK)

    self.device_camera: DeviceCameraConfig | None = None
    self.view_from_calib = view_frame_from_device_frame.copy()
    self.view_from_wide_calib = view_frame_from_device_frame.copy()

    self._matrix_cache_key: tuple | None = None
    self._cached_matrix: np.ndarray | None = None
    self._content_rect = rl.Rectangle()
    self._last_click_time = 0.0
    self._reverse_driver_camera_frames = 0
    self._reverse_driver_camera_active = False
    self._sidebar_personality_pressed = False

    # Bookmark icon with swipe gesture
    self._bookmark_icon = BookmarkIcon(bookmark_callback)

    self._model_renderer = ModelRenderer()
    self._hud_renderer = HudRenderer()
    self._alert_renderer = AlertRenderer()
    self._driver_state_renderer = DriverStateRenderer()
    self._confidence_ball = ConfidenceBall()
    self._sidebar_widgets = MiciSidebarWidgets(self._confidence_ball)
    self._min_steer_speed_banner = MinSteerSpeedBanner()
    self._standstill_timer = StandstillTimerOverlay()
    self._favorite_slots = self._child(FavoriteSlotsOverlay())
    self._offroad_label = UnifiedLabel("start the car to\nuse openpilot", 54, FontWeight.DISPLAY,
                                       text_color=rl.Color(255, 255, 255, int(255 * 0.9)),
                                       alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
                                       alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_MIDDLE)

    self._fade_texture = gui_app.texture("icons_mici/onroad/onroad_fade.png")

    # debug
    self._pm = messaging.PubMaster(['uiDebug'])
    # C4 sidecam: fills road preview as a curved rectangle. Only shown
    # on the road camera screen, gated via widget visibility
    self._pip_sidecam = self._child(PipSideCamera(shape="curved"))
    self._pip_sidecam.set_visible(lambda: self.stream_type == ROAD_CAM)

  @staticmethod
  def _controls_ready() -> bool:
    return ui_state.sm.recv_frame["selfdriveState"] >= ui_state.started_frame

  def _update_reverse_driver_camera_state(self) -> bool:
    should_force_driver = ui_state.started and ui_state.ui_params.get_bool("DriverCamera") and self._is_in_reverse()
    if not should_force_driver:
      self._reverse_driver_camera_frames = 0
      self._reverse_driver_camera_active = False
      return False

    self._reverse_driver_camera_frames = min(self._reverse_driver_camera_frames + 1, REVERSE_DRIVER_CAMERA_DELAY_FRAMES)
    self._reverse_driver_camera_active = self._reverse_driver_camera_frames >= REVERSE_DRIVER_CAMERA_DELAY_FRAMES
    return self._reverse_driver_camera_active

  def is_swiping_left(self) -> bool:
    """Check if currently swiping left (for scroller to disable)."""
    return self._bookmark_icon.is_swiping_left()

  def _update_state(self):
    super()._update_state()

    # update offroad label
    if ui_state.panda_type == log.PandaState.PandaType.unknown:
      self._offroad_label.set_text("system booting")
    elif ui_state.started and not self._controls_ready():
      self._offroad_label.set_text("waiting for\ncontrols to start")
    else:
      self._offroad_label.set_text("start the car to\nuse openpilot")

  def _sidebar_rect(self) -> rl.Rectangle:
    return rl.Rectangle(
      self.rect.x + self.rect.width - SIDE_PANEL_WIDTH,
      self.rect.y,
      SIDE_PANEL_WIDTH,
      self.rect.height,
    )

  def _sidebar_widgets_visible(self) -> bool:
    return not ui_state.ui_params.get_bool("StockConfidenceBallWidget") or self._sidebar_widgets.demo_active

  def _sidebar_personality_touch_enabled(self) -> bool:
    return (
      ui_state.started and
      ui_state.has_longitudinal_control and
      self._sidebar_widgets_visible() and
      not ui_state.ui_params.get_bool("SafeMode")
    )

  def _touch_in_sidebar(self, mouse_pos: MousePos) -> bool:
    return rl.check_collision_point_rec(mouse_pos, self._sidebar_rect())

  def _cycle_personality_profile(self) -> None:
    current = ui_state.ui_params.get_int("LongitudinalPersonality", return_default=True, default=int(log.LongitudinalPersonality.standard))
    profiles = (
      int(log.LongitudinalPersonality.aggressive),
      int(log.LongitudinalPersonality.standard),
      int(log.LongitudinalPersonality.relaxed),
    )
    try:
      current_idx = profiles.index(int(current))
    except ValueError:
      current_idx = 1
    next_personality = profiles[(current_idx + 1) % len(profiles)]
    ui_state.ui_params.put_int("LongitudinalPersonality", next_personality)
    ui_state.personality = next_personality

  def _handle_mouse_press(self, mouse_pos: MousePos):
    self._sidebar_personality_pressed = (
      self._sidebar_personality_touch_enabled() and
      self._touch_in_sidebar(mouse_pos)
    )
    if not self._sidebar_personality_pressed:
      super()._handle_mouse_press(mouse_pos)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    if self._sidebar_personality_pressed:
      if self._sidebar_personality_touch_enabled() and self._touch_in_sidebar(mouse_pos):
        self._cycle_personality_profile()
      self._sidebar_personality_pressed = False
      return

    self._sidebar_personality_pressed = False

    # Don't trigger click callback if bookmark or HUD widgets consumed the tap.
    if not self._bookmark_icon.interacting() and not self._hud_renderer.user_interacting() and not self._favorite_slots.interacting():
      super()._handle_mouse_release(mouse_pos)

  def _render(self, _):
    start_draw = time.monotonic()
    camera_view = self._camera_view()
    camera_view_none = camera_view == CAMERA_VIEW_NONE
    self._switch_stream_if_needed(ui_state.sm, camera_view)

    # Update calibration before rendering
    self._update_calibration()

    # Create inner content area with border padding
    self._content_rect = rl.Rectangle(
      self.rect.x,
      self.rect.y,
      self.rect.width - SIDE_PANEL_WIDTH,
      self.rect.height,
    )

    # Enable scissor mode to clip all rendering within content rectangle boundaries
    # This creates a rendering viewport that prevents graphics from drawing outside the border
    rl.begin_scissor_mode(
      int(self._content_rect.x),
      int(self._content_rect.y),
      int(self._content_rect.width),
      int(self._content_rect.height)
    )

    # Render the base camera view
    if camera_view_none:
      rl.draw_rectangle_rec(self._content_rect, rl.BLACK)
    else:
      gui_app.mark_progress("mici.onroad.before_camera")
      super()._render(self._content_rect)
      gui_app.mark_progress("mici.onroad.after_camera")

    # Desktop-only camera recording: stop after the transformed camera frame.
    if RECORD_CAMERA_ONLY:
      rl.end_scissor_mode()
      return

    waiting_for_controls = ui_state.started and not self._controls_ready()
    if waiting_for_controls:
      rl.draw_rectangle(int(self._content_rect.x), int(self._content_rect.y),
                        int(self._content_rect.width), int(self._content_rect.height),
                        rl.Color(0, 0, 0, 145))
      self._offroad_label.render(self._content_rect)
      rl.end_scissor_mode()
      self._draw_border()
      self._bookmark_icon.render(self.rect)

      msg = messaging.new_message('uiDebug')
      msg.uiDebug.drawTimeMillis = (time.monotonic() - start_draw) * 1000
      self._pm.send('uiDebug', msg)
      return

    in_reverse = self._is_in_reverse()
    is_driver_stream = self.stream_type == DRIVER_CAM
    draw_road_overlays = not in_reverse and not is_driver_stream and not camera_view_none
    draw_hud_controls = camera_view_none or (not in_reverse and not is_driver_stream)
    self._hud_renderer.prepare(self._content_rect)

    # Draw all UI overlays
    if draw_road_overlays:
      gui_app.mark_progress("mici.onroad.before_model")
      self._model_renderer.render(self._content_rect)
      gui_app.mark_progress("mici.onroad.after_model")

    # Fade out bottom of overlays for looks
    rl.draw_texture_ex(self._fade_texture, rl.Vector2(self._content_rect.x, self._content_rect.y), 0.0, 1.0, rl.WHITE)
    if draw_hud_controls:
      self._hud_renderer.render_background()

    alert_to_render, not_animating_out = self._alert_renderer.will_render()

    should_draw_dmoji = ui_state.is_onroad() and (
      is_driver_stream or camera_view_none or ((not in_reverse) and (not self._hud_renderer.drawing_top_icons()))
    )
    self._driver_state_renderer.set_should_draw(should_draw_dmoji)
    self._driver_state_renderer.set_position(self._rect.x + 16, self._rect.y + 10)
    if camera_view_none or is_driver_stream or not in_reverse:
      self._driver_state_renderer.render()

    self._hud_renderer.set_can_draw_top_icons(draw_hud_controls and (alert_to_render is None))
    self._hud_renderer.set_wheel_critical_icon(draw_hud_controls and alert_to_render is not None and not not_animating_out and
                                               alert_to_render.visual_alert == car.CarControl.HUDControl.VisualAlert.steerRequired)
    # TODO: have alert renderer draw offroad mici label below
    if ui_state.started:
      self._alert_renderer.render(self._content_rect)
    if draw_hud_controls:
      gui_app.mark_progress("mici.onroad.before_hud")
      self._hud_renderer.render_foreground()
      gui_app.mark_progress("mici.onroad.after_hud")
    rendered_standstill_timer = False
    if draw_hud_controls:
      rendered_standstill_timer = self._standstill_timer.render(self._content_rect, in_reverse)
    if draw_hud_controls and not rendered_standstill_timer:
      self._min_steer_speed_banner.render(self._content_rect)

    # End clipping region
    rl.end_scissor_mode()

    # Custom UI extension point - add custom overlays here
    # Use self._content_rect for positioning within camera bounds
    if draw_road_overlays:
      gui_app.mark_progress("mici.onroad.before_sidebar")
      if ui_state.ui_params.get_bool("StockConfidenceBallWidget") and not self._sidebar_widgets.demo_active:
        self._confidence_ball.render(self.rect)
      else:
        self._sidebar_widgets.render(self.rect)
      gui_app.mark_progress("mici.onroad.after_sidebar")
    if draw_hud_controls and (camera_view_none or is_driver_stream or not in_reverse):
      self._favorite_slots.render(self._content_rect)
    # Inset by the border so the pill never covers the green/orange status border.
    border = self._get_border_width()
    preview_rect = rl.Rectangle(
      self._content_rect.x + border,
      self._content_rect.y + border,
      max(1, self._content_rect.width - 2 * border),
      max(1, self._content_rect.height - 2 * border),
    )
    self._pip_sidecam.render(preview_rect)

    if camera_view_none or is_driver_stream or not in_reverse:
      self._draw_border()

    self._bookmark_icon.render(self.rect)

    # Draw darkened background and text if not onroad
    if not ui_state.started:
      rl.draw_rectangle(int(self.rect.x), int(self.rect.y), int(self.rect.width), int(self.rect.height), rl.Color(0, 0, 0, 175))
      self._offroad_label.render(self._content_rect)

    # publish uiDebug
    msg = messaging.new_message('uiDebug')
    msg.uiDebug.drawTimeMillis = (time.monotonic() - start_draw) * 1000
    self._pm.send('uiDebug', msg)

  def _draw_border(self):
    border_size = self._get_border_width()
    # Keep the outer edge pinned to the camera bounds. Wider borders grow inward
    # so they cannot paint over the fixed right-side widget column.
    border_rect = rl.Rectangle(
      self._content_rect.x + border_size / 2,
      self._content_rect.y + border_size / 2,
      self._content_rect.width - border_size,
      self._content_rect.height - border_size,
    )
    rl.begin_scissor_mode(
      int(self._content_rect.x),
      int(self._content_rect.y),
      int(self._content_rect.width),
      int(self._content_rect.height),
    )
    border_color = get_pulse_glide_border_color(ui_state.sm, get_border_color(ui_state))
    rl.draw_rectangle_rounded_lines_ex(border_rect, 0.12, 16, border_size, border_color)

    if (colors := get_traffic_border_colors()) is not None:
      content_x = int(round(self._content_rect.x))
      content_y = int(round(self._content_rect.y))
      content_width = int(round(self._content_rect.width))
      content_height = int(round(self._content_rect.height))
      split_x = content_x + content_width // 2
      for x, w, color in (
        (content_x, split_x - content_x, colors[0]),
        (split_x, content_x + content_width - split_x, colors[1]),
      ):
        if color.a > 0:
          rl.begin_scissor_mode(x, content_y, w, content_height)
          rl.draw_rectangle_rounded_lines_ex(border_rect, 0.12, 16, border_size, color)
          rl.end_scissor_mode()

    rl.end_scissor_mode()

  def _get_border_width(self) -> int:
    return get_border_width(8, ui_state.ui_params)

  @staticmethod
  def _is_in_reverse() -> bool:
    if ui_state.sm.recv_frame["carState"] < ui_state.started_frame:
      return False

    try:
      gear = ui_state.sm["carState"].gearShifter
    except Exception:
      return False

    if gear == GEAR_SHIFTER_REVERSE:
      return True

    reverse_enum = getattr(car.CarState.GearShifter, "reverse", None)
    if reverse_enum is not None and gear == reverse_enum:
      return True

    return str(gear).split(".")[-1].lower() == "reverse"

  def is_in_reverse(self) -> bool:
    return self._is_in_reverse()

  @staticmethod
  def _camera_view() -> int:
    # Desktop-only camera-view override for recording modes.
    # Never changes CameraView or camera selection on comma hardware.
    if RECORD_HUD_ONLY or RECORD_CAMERA_ONLY or RECORD_COMBINED:
      desktop_views = {
        "auto": CAMERA_VIEW_AUTO,
        "standard": CAMERA_VIEW_STANDARD,
        "wide": CAMERA_VIEW_WIDE,
      }
      if RECORD_CAMERA_VIEW in desktop_views:
        return desktop_views[RECORD_CAMERA_VIEW]

    camera_view = ui_state.ui_params.get_int("CameraView", return_default=True, default=CAMERA_VIEW_STANDARD)
    if camera_view not in (CAMERA_VIEW_AUTO, CAMERA_VIEW_DRIVER, CAMERA_VIEW_STANDARD, CAMERA_VIEW_WIDE, CAMERA_VIEW_NONE):
      return CAMERA_VIEW_STANDARD
    return camera_view

  def _switch_stream_if_needed(self, sm, camera_view: int):
    if camera_view == CAMERA_VIEW_NONE:
      self._cancel_pending_switch()
      self._reverse_driver_camera_frames = 0
      self._reverse_driver_camera_active = False
      return

    reentry_selection_pending = (getattr(self, "_onroad_reentry_pending", False) and
                                 not getattr(self, "_reentry_stream_selected", False))
    if self._update_reverse_driver_camera_state():
      self.switch_stream(DRIVER_CAM)
      return

    if reentry_selection_pending or not self.available_streams:
      self._refresh_available_streams()

    wide_available = WIDE_CAM in self.available_streams
    if camera_view == CAMERA_VIEW_DRIVER:
      target = DRIVER_CAM
    elif camera_view == CAMERA_VIEW_STANDARD:
      target = ROAD_CAM
    elif camera_view == CAMERA_VIEW_WIDE:
      target = WIDE_CAM if wide_available else ROAD_CAM
    elif sm['selfdriveState'].experimentalMode and wide_available:
      v_ego = sm['carState'].vEgo
      if v_ego < WIDE_CAM_MAX_SPEED:
        target = WIDE_CAM
      elif v_ego > ROAD_CAM_MIN_SPEED:
        target = ROAD_CAM
      else:
        # Hysteresis zone - keep the current or pending road camera selection.
        current_road_stream = (self._target_stream_type if self._switching and
                               self._target_stream_type in (ROAD_CAM, WIDE_CAM) else self.stream_type)
        target = WIDE_CAM if current_road_stream == WIDE_CAM and wide_available else ROAD_CAM
    else:
      target = ROAD_CAM

    if (reentry_selection_pending or
        self.stream_type != target or (self._switching and self._target_stream_type != target)):
      self.switch_stream(target)

  def _update_calibration(self):
    # Update device camera if not already set
    sm = ui_state.sm
    if not self.device_camera and sm.seen['roadCameraState'] and sm.seen['deviceState']:
      self.device_camera = DEVICE_CAMERAS[(str(sm['deviceState'].deviceType), str(sm['roadCameraState'].sensor))]

    # Check if live calibration data is available and valid
    if not (sm.updated["liveCalibration"] and sm.valid['liveCalibration']):
      return

    calib = sm['liveCalibration']
    if len(calib.rpyCalib) != 3 or calib.calStatus != CALIBRATED:
      return

    # Update view_from_calib matrix
    device_from_calib = rot_from_euler(calib.rpyCalib)
    self.view_from_calib = view_frame_from_device_frame @ device_from_calib

    # Update wide calibration if available
    if hasattr(calib, 'wideFromDeviceEuler') and len(calib.wideFromDeviceEuler) == 3:
      wide_from_device = rot_from_euler(calib.wideFromDeviceEuler)
      self.view_from_wide_calib = view_frame_from_device_frame @ wide_from_device @ device_from_calib

  def _calc_frame_matrix(self, rect: rl.Rectangle) -> np.ndarray:
    if self.stream_type == DRIVER_CAM:
      base = CameraView._calc_frame_matrix(self, rect)
      driver_view_ratio = 1.5
      base[0, 0] *= driver_view_ratio
      base[1, 1] *= driver_view_ratio
      return base

    cache_key = (
      ui_state.sm.recv_frame['liveCalibration'],
      int(self._content_rect.x),
      int(self._content_rect.y),
      int(self._content_rect.width),
      int(self._content_rect.height),
      self.stream_type,
      round(float(ui_state.sm['carState'].vEgo), 1),
      id(self.device_camera),
    )
    if cache_key == self._matrix_cache_key and self._cached_matrix is not None:
      return self._cached_matrix

    # Get camera configuration
    device_camera = self.device_camera or DEFAULT_DEVICE_CAMERA
    is_wide_camera = self.stream_type == WIDE_CAM
    intrinsic = device_camera.ecam.intrinsics if is_wide_camera else device_camera.fcam.intrinsics
    calibration = self.view_from_wide_calib if is_wide_camera else self.view_from_calib
    if is_wide_camera:
      zoom = 0.7 * 1.5
    else:
      zoom = np.interp(ui_state.sm['carState'].vEgo, [10, 30], [0.8, 1.0])

    # Calculate transforms for vanishing point
    inf_point = np.array([1000.0, 0.0, 0.0])
    calib_transform = intrinsic @ calibration
    kep = calib_transform @ inf_point

    # Calculate center points and dimensions
    x, y = self._content_rect.x, self._content_rect.y
    w, h = self._content_rect.width, self._content_rect.height
    cx, cy = intrinsic[0, 2], intrinsic[1, 2]

    # Ensure zoom views the whole area
    zoom = max(zoom, w / (2 * cx), h / (2 * cy))

    # Calculate max allowed offsets with margins
    margin = 5
    max_x_offset = max(0.0, cx * zoom - w / 2 - margin)
    max_y_offset = max(0.0, cy * zoom - h / 2 - margin)

    # Calculate and clamp offsets to prevent out-of-bounds issues
    try:
      if abs(kep[2]) > 1e-6:
        x_offset = np.clip((kep[0] / kep[2] - cx) * zoom, -max_x_offset, max_x_offset)
        y_offset = np.clip((kep[1] / kep[2] - cy) * zoom + CAM_Y_OFFSET, -max_y_offset, max_y_offset)
      else:
        x_offset, y_offset = 0, 0
    except (ZeroDivisionError, OverflowError):
      x_offset, y_offset = 0, 0

    self._matrix_cache_key = cache_key
    self._cached_matrix = np.array([
      [zoom * 2 * cx / w, 0, -x_offset / w * 2],
      [0, zoom * 2 * cy / h, -y_offset / h * 2],
      [0, 0, 1.0]
    ])

    video_transform = np.array([
      [zoom, 0.0, (w / 2 + x - x_offset) - (cx * zoom)],
      [0.0, zoom, (h / 2 + y - y_offset) - (cy * zoom)],
      [0.0, 0.0, 1.0]
    ])
    self._model_renderer.set_transform(video_transform @ calib_transform)

    return self._cached_matrix


if __name__ == "__main__":
  gui_app.init_window("OnRoad Camera View")
  road_camera_view = AugmentedRoadView(ROAD_CAM)
  print("***press space to switch camera view***")
  try:
    for _ in gui_app.render():
      ui_state.update()
      if rl.is_key_released(rl.KeyboardKey.KEY_SPACE):
        if WIDE_CAM in road_camera_view.available_streams:
          stream = ROAD_CAM if road_camera_view.stream_type == WIDE_CAM else WIDE_CAM
          road_camera_view.switch_stream(stream)
      road_camera_view.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
  finally:
    road_camera_view.close()
