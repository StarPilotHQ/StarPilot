import numpy as np
from openpilot.starpilot.ui.camera_selection import (selected_camera_stream, WIDE_CAM_MAX_SPEED as WIDE_CAM_MAX_SPEED,
                                                    ROAD_CAM_MIN_SPEED as ROAD_CAM_MIN_SPEED)
from openpilot.starpilot.ui.raylib import rl
from openpilot.cereal import log
from opendbc.car.structs import car
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.mici.onroad.alert_renderer import AlertRenderer
from openpilot.selfdrive.ui.mici.onroad.driver_state import DriverStateRenderer
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.mici.onroad.long_indicator import LongIndicator
from openpilot.selfdrive.ui.mici.onroad.model_renderer import ModelRenderer
from openpilot.selfdrive.ui.mici.onroad.navigation import NavigationPanel, PANEL_WIDTH
from openpilot.selfdrive.ui.mici.onroad.confidence_ball import ConfidenceBall
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.system.ui.widgets.label import gui_label
from openpilot.system.ui.lib.application import FontWeight, gui_app, MousePos, MouseEvent, TextAlignment, TextAlignmentVertical
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets import Widget
from openpilot.common.filter_simple import BounceFilter, FirstOrderFilter
from openpilot.common.transformations.camera import DEVICE_CAMERAS, DeviceCameraConfig, view_frame_from_device_frame
from openpilot.common.transformations.orientation import rot_from_euler
from enum import IntEnum

OpState = log.SelfdriveState.OpenpilotState
CALIBRATED = log.ExtrinsicsCalibration.Status.calibrated
NARROW_ROAD_CAM = VisionStreamType.VISION_STREAM_NARROW_ROAD
WIDE_CAM = VisionStreamType.VISION_STREAM_WIDE_ROAD
DRIVER_CAM = VisionStreamType.VISION_STREAM_CABIN
CAMERA_VIEW_AUTO, CAMERA_VIEW_DRIVER, CAMERA_VIEW_STANDARD, CAMERA_VIEW_WIDE, CAMERA_VIEW_NONE = range(5)
DEFAULT_DEVICE_CAMERA = DEVICE_CAMERAS["tici", "ar0231"]


class BookmarkState(IntEnum):
  HIDDEN = 0
  DRAGGING = 1
  TRIGGERED = 2


CAM_Y_OFFSET = 20


class BookmarkIcon(Widget):
  PEEK_THRESHOLD = 150  # If icon peeks out this much, snap it fully visible
  FLICK_DISTANCE = 60
  FLICK_VELOCITY = 800
  FLICK_DURATION = 0.15
  FLICK_PAUSE = 0.06
  FULL_VISIBLE_OFFSET = 200  # How far onscreen when fully visible
  HIDDEN_OFFSET = -50  # How far offscreen when hidden

  def __init__(self, bookmark_callback):
    super().__init__()
    self._bookmark_callback = bookmark_callback
    self._icon = gui_app.texture("icons_mici/onroad/bookmark.png", 180, 180)
    self._filled_icon = gui_app.texture("icons_mici/onroad/bookmark_fill.png", 180, 180)
    self._offset_filter = BounceFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._active_alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)

    # State
    self._interacting = False
    self._state = BookmarkState.HIDDEN
    self._swipe_start_x = 0.0
    self._swipe_start_y = 0.0
    self._gesture_frame = -1
    self._claimed_frame = -1
    self._swipe_current_x = 0.0
    self._is_swiping = False
    self._is_swiping_left: bool = False
    self._triggered_time: float = 0.0
    self._triggered_duration = 1.5
    self._swipe_expired = False
    self._swipe_start_t = 0.0
    self._last_swipe_move = (0.0, 0.0)
    self._swipe_moves = 0

  def is_swiping_left(self) -> bool:
    """Check if currently swiping left (for scroller to disable)."""
    return self._is_swiping_left or self._claimed_frame == gui_app.frame

  def interacting(self):
    interacting, self._interacting = self._interacting, False
    return interacting

  def cancel_gesture(self):
    self._is_swiping = self._is_swiping_left = self._interacting = False
    if self._state == BookmarkState.DRAGGING:
      self._state = BookmarkState.HIDDEN

  def process_gesture(self, *, can_start: bool) -> None:
    # The page scroller must give a settled onroad press first refusal. Once
    # owned, its own scroll rejection must not cancel the bookmark gesture.
    if self._gesture_frame == gui_app.frame:
      return
    self._gesture_frame = gui_app.frame
    if not ui_state.started or not self.enabled:
      self.cancel_gesture()
      return
    for event in gui_app.mouse_events:
      if event.slot != 0:
        continue
      if event.left_pressed:
        self.cancel_gesture()
        if not can_start or not rl.check_collision_point_rec(event.pos, self._hit_rect):
          continue
      elif not self._is_swiping:
        continue
      self._handle_mouse_event(event)

  def _process_mouse_events(self):
    self.process_gesture(can_start=self._touch_valid())

  def hide_event(self):
    self.cancel_gesture()
    super().hide_event()

  def _update_state(self):
    if self._state == BookmarkState.TRIGGERED and rl.get_time() - self._triggered_time >= self._triggered_duration:
      self._state = BookmarkState.HIDDEN
      self._swipe_expired = self._is_swiping

    swipe_offset = self._swipe_start_x - self._swipe_current_x
    armed = self._state == BookmarkState.DRAGGING and swipe_offset > self.PEEK_THRESHOLD
    if self._state == BookmarkState.DRAGGING:
      # Snap to the released position when armed, while allowing further dragging or cancellation.
      if armed:
        swipe_offset += self.FULL_VISIBLE_OFFSET - self.PEEK_THRESHOLD
      swipe_offset = min(swipe_offset, self.FULL_VISIBLE_OFFSET + 50)
      self._offset_filter.update(swipe_offset)

    elif self._state == BookmarkState.TRIGGERED:
      # Let another left swipe move the same bookmark.
      offset = self.FULL_VISIBLE_OFFSET
      if self._is_swiping and self._is_swiping_left:
        offset += min(swipe_offset, 50)
      self._offset_filter.update(offset)

    elif self._state == BookmarkState.HIDDEN:
      self._offset_filter.update(self.HIDDEN_OFFSET)

      if self._offset_filter.x < 1e-3:
        self._interacting = False

    self._active_alpha.update(float(armed or self._state == BookmarkState.TRIGGERED))

  def _handle_mouse_event(self, mouse_event: MouseEvent):
    if not ui_state.started:
      return

    if mouse_event.left_pressed:
      if self._state == BookmarkState.TRIGGERED:
        self._triggered_time = rl.get_time()
        self._triggered_duration = 0.5

      # Store relative position within widget
      self._swipe_start_x = mouse_event.pos.x
      self._swipe_start_y = mouse_event.pos.y
      self._swipe_current_x = mouse_event.pos.x
      self._is_swiping = True
      self._is_swiping_left = False
      self._swipe_expired = False
      self._swipe_start_t = mouse_event.t
      self._last_swipe_move = (mouse_event.pos.x, mouse_event.t)
      self._swipe_moves = 0

    elif (mouse_event.left_down or mouse_event.left_released) and self._is_swiping:
      self._swipe_current_x = mouse_event.pos.x
      swipe_offset = self._swipe_start_x - self._swipe_current_x
      vertical = abs(mouse_event.pos.y - self._swipe_start_y)
      if swipe_offset < -12 or (vertical > 12 and vertical > abs(swipe_offset)):
        self.cancel_gesture()
        return
      if mouse_event.left_down and not mouse_event.left_released:
        if mouse_event.t <= self._last_swipe_move[1]:
          self._swipe_moves = 0
        else:
          self._swipe_moves += 1
        self._last_swipe_move = (mouse_event.pos.x, mouse_event.t)
      self._is_swiping_left = swipe_offset > 12
      if self._is_swiping_left:
        self._interacting = True
        self._claimed_frame = gui_app.frame
        if self._state == BookmarkState.HIDDEN and not self._swipe_expired:
          self._state = BookmarkState.DRAGGING

    if mouse_event.left_released:
      if self._is_swiping and self._state == BookmarkState.DRAGGING:
        swipe_distance = self._swipe_start_x - self._swipe_current_x

        # If peeking past threshold, transition to animating to fully visible and bookmark
        move_x, move_t = self._last_swipe_move
        elapsed = move_t - self._swipe_start_t
        held_distance = self._swipe_start_x - move_x
        flick = (self._swipe_moves >= 2 and 0.01 <= elapsed <= self.FLICK_DURATION and
                 0 <= mouse_event.t - move_t <= self.FLICK_PAUSE and
                 held_distance >= self.FLICK_DISTANCE and swipe_distance >= self.FLICK_DISTANCE and
                 swipe_distance >= held_distance - 12 and held_distance / elapsed >= self.FLICK_VELOCITY)
        if swipe_distance > self.PEEK_THRESHOLD or flick:
          self._state = BookmarkState.TRIGGERED
          self._triggered_time = rl.get_time()
          self._triggered_duration = 1.5
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
      for icon, alpha in ((self._icon, 1.0 - self._active_alpha.x), (self._filled_icon, self._active_alpha.x)):
        rl.draw_texture_ex(icon, rl.Vector2(icon_x, icon_y), 0.0, 1.0, rl.Color(255, 255, 255, round(255 * alpha)))


class AugmentedRoadView(CameraView):
  def __init__(self, bookmark_callback=None, stream_type: VisionStreamType = VisionStreamType.VISION_STREAM_NARROW_ROAD):
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

    # Bookmark icon with swipe gesture
    self._bookmark_icon = BookmarkIcon(bookmark_callback)

    self._navigation = NavigationPanel()
    self._model_renderer = ModelRenderer()
    self._hud_renderer = HudRenderer()
    self._long_indicator = LongIndicator()
    self._alert_renderer = AlertRenderer()
    self._driver_state_renderer = DriverStateRenderer()
    self._confidence_ball = ConfidenceBall()
    self._offroad_label = UnifiedLabel("start the car to\nuse openpilot", 54, FontWeight.DISPLAY,
                                       text_color=rl.Color(255, 255, 255, int(255 * 0.9)),
                                       alignment=TextAlignment.CENTER,
                                       alignment_vertical=TextAlignmentVertical.MIDDLE)

    self._fade_texture = gui_app.texture("icons_mici/onroad/onroad_fade.png")

  def render_camera_model_layer(self, rect: rl.Rectangle, *, camera_view: int = CAMERA_VIEW_AUTO,
                                reverse_driver: bool = False, lead_indicator: bool = False,
                                lead_info_mode: int = 0, lead_info_metric: bool | None = None,
                                lateral_active: bool = False, road_style: dict | None = None, paint: bool = True,
                                poll_camera: bool = True) -> None:
    """Draw the existing calibrated camera/model without stock HUD or alert."""
    if not ui_state.started and ui_state.replay_clock is None:
      return
    if poll_camera and (camera_view == CAMERA_VIEW_DRIVER or reverse_driver):
      self._ensure_connection()
    target = self._switch_stream_if_needed(ui_state.sm, camera_view, reverse_driver)
    if target is None:
      if paint:
        rl.draw_rectangle_rec(rect, rl.BLACK)
      return
    driver_transition = DRIVER_CAM in (self.stream_type, target, self._target_stream_type)
    if poll_camera and self._switching and driver_transition:
      self._handle_switch()
    if (target == DRIVER_CAM and DRIVER_CAM not in self.available_streams) or \
       (driver_transition and self.stream_type != target):
      if paint:
        rl.draw_rectangle_rec(rect, rl.BLACK)
        gui_label(rect, "driver camera unavailable" if target == DRIVER_CAM and DRIVER_CAM not in self.available_streams else
                  "camera switching", font_size=28, font_weight=FontWeight.MEDIUM,
                  alignment=TextAlignment.CENTER)
      return
    self._update_calibration()
    self._content_rect = rect
    self._model_renderer.road_style = road_style or {}
    gui_app.measure_frame_phase("camera", CameraView._render, self, rect, paint=paint, poll_camera=poll_camera)
    if target != DRIVER_CAM and self.frame is not None:
      gui_app.measure_frame_phase("model", self._model_renderer.render_with_lead, rect, lead_indicator,
                                  lead_info_mode, lead_info_metric, lateral_active=lateral_active, paint=paint)

  def render_model_source_layer(self, rect: rl.Rectangle, *, show_long_indicator: bool | None = False) -> None:
    self._hud_renderer._draw_model_source(rect)
    if show_long_indicator is not None:
      self._long_indicator.set_should_draw(show_long_indicator)
      self._long_indicator.render(rect)

  def render_stock_confidence_layer(self, rect: rl.Rectangle, *, lateral_active: bool = False) -> None:
    self._confidence_ball.render_with_lateral(rect, lateral_active)

  def reset_stock_confidence_layer(self) -> None:
    self._confidence_ball.reset()

  def is_swiping_left(self) -> bool:
    """Check if currently swiping left (for scroller to disable)."""
    return self._bookmark_icon.is_swiping_left()

  def _update_state(self):
    super()._update_state()

    # update offroad label
    if ui_state.panda_type == log.PandaState.PandaType.unknown:
      self._offroad_label.set_text("system booting")
    elif ui_state.ignition and not ui_state.started:
      self._offroad_label.set_text("openpilot can't start\ncheck alerts")
    else:
      self._offroad_label.set_text("start the car to\nuse openpilot")

  def _handle_mouse_release(self, mouse_pos: MousePos):
    # Don't trigger click callback if bookmark was triggered
    if not self._bookmark_icon.interacting():
      super()._handle_mouse_release(mouse_pos)

  def _render(self, rect: rl.Rectangle, *, paint: bool = True, poll_camera: bool = True):
    if not paint:
      super()._render(rect, paint=False, poll_camera=poll_camera)
      return
    # Draw text if not onroad
    if not ui_state.started:
      rl.draw_rectangle_rec(self.rect, rl.BLACK)
      self._offroad_label.render(self._rect)
      return

    self._switch_stream_if_needed(ui_state.sm)

    # Update calibration before rendering
    self._update_calibration()

    alert_to_render, not_animating_out = self._alert_renderer.will_render()
    show_navigation = ui_state.navigation_enabled and ui_state.nav_destination_set and alert_to_render is None

    # Create inner content area with border padding
    self._content_rect = rl.Rectangle(
      self.rect.x,
      self.rect.y,
      self.rect.width - (PANEL_WIDTH if show_navigation else SIDE_PANEL_WIDTH),
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
    super()._render(self._content_rect, poll_camera=poll_camera)

    # Draw all UI overlays
    self._model_renderer.render(self._content_rect)

    # Fade out bottom of overlays for looks
    rl.draw_texture_ex(self._fade_texture, rl.Vector2(self._content_rect.x, self._content_rect.y), 0.0, 1.0, rl.WHITE)

    # Hide DMoji when disengaged unless AlwaysOnDM is enabled
    should_draw_dmoji = (not self._hud_renderer.drawing_top_icons() and
                         (ui_state.status != UIStatus.DISENGAGED or ui_state.always_on_dm))
    self._driver_state_renderer.set_should_draw(should_draw_dmoji)
    self._driver_state_renderer.set_position(self._rect.x + 16, self._rect.y + 10)
    self._driver_state_renderer.render()
    self._long_indicator.set_should_draw(not self._hud_renderer.drawing_top_icons())
    self._long_indicator.render(self._content_rect)

    self._hud_renderer.set_can_draw_top_icons(alert_to_render is None)
    self._hud_renderer.set_wheel_critical_icon(alert_to_render is not None and not not_animating_out and
                                               alert_to_render.visual_alert == car.CarControl.HUDControl.VisualAlert.steerRequired)
    self._alert_renderer.render(self._content_rect)
    self._hud_renderer.render(self._content_rect)

    # Draw fake rounded border
    rl.draw_rectangle_rounded_lines_ex(self._content_rect, 0.2 * 1.02, 10, 50, rl.BLACK)

    # End clipping region
    rl.end_scissor_mode()

    # Custom UI extension point - add custom overlays here
    # Use self._content_rect for positioning within camera bounds
    if show_navigation:
      self._navigation.render(rl.Rectangle(self._content_rect.x + self._content_rect.width, self.rect.y, PANEL_WIDTH, self.rect.height))
    else:
      self._confidence_ball.render(self.rect)

    self._bookmark_icon.render(self.rect)

  def close(self):
    self._navigation.close()
    super().close()

  def _cancel_pending_switch(self) -> None:
    self._target_client = None
    self._target_stream_type = None
    self._switching = False

  def _switch_stream_if_needed(self, sm, camera_view: int = CAMERA_VIEW_AUTO,
                               reverse_driver: bool = False) -> VisionStreamType | None:
    target = selected_camera_stream(camera_view, reverse_driver=reverse_driver, current=self.stream_type,
      available=self.available_streams, experimental=sm['selfdriveState'].experimentalMode,
      speed=sm['carState'].vEgo, narrow=NARROW_ROAD_CAM, wide=WIDE_CAM, driver=DRIVER_CAM)
    if target is None:
      self._cancel_pending_switch()
      return None
    if target == DRIVER_CAM and DRIVER_CAM not in self.available_streams:
      self._cancel_pending_switch()
      return target
    if self.stream_type == target and self._switching and self._target_stream_type != target:
      self._cancel_pending_switch()
    elif self.stream_type != target or (self._switching and self._target_stream_type != target):
      self.switch_stream(target)
    return target

  def _update_calibration(self):
    # Update device camera if not already set
    sm = ui_state.sm
    if not self.device_camera and sm.seen['narrowRoadCameraState'] and sm.seen['deviceState']:
      self.device_camera = DEVICE_CAMERAS[(str(sm['deviceState'].deviceType), str(sm['narrowRoadCameraState'].sensor))]

    # Check if camera calibration data is available and valid
    if not (sm.updated["extrinsicsCalibration"] and sm.valid['extrinsicsCalibration']):
      return

    calib = sm['extrinsicsCalibration']
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
      base[0, 0] *= 1.5
      base[1, 1] *= 1.5
      return base
    cache_key = (
      ui_state.sm.recv_frame['extrinsicsCalibration'],
      int(self._content_rect.width),
      int(self._content_rect.height),
      self.stream_type,
      round(ui_state.sm['carState'].vEgo, 1),
    )

    if cache_key == self._matrix_cache_key and self._cached_matrix is not None:
      return self._cached_matrix

    # Get camera configuration
    device_camera = self.device_camera or DEFAULT_DEVICE_CAMERA
    is_wide_camera = self.stream_type == WIDE_CAM
    intrinsic = device_camera.wide_road.intrinsics if is_wide_camera else device_camera.narrow_road.intrinsics
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

    # Cache the computed transformation matrix to avoid recalculations
    self._matrix_cache_key = cache_key
    self._cached_matrix = np.array([
      [zoom * 2 * cx / w, 0, -x_offset / w * 2],
      [0, zoom * 2 * cy / h, -y_offset / h * 2],
      [0, 0, 1.0]
    ])

    # built without rect.x/y so cache stays hot during scroll. ModelRenderer adds offset at draw time
    video_transform = np.array([
      [zoom, 0.0, (w / 2 - x_offset) - (cx * zoom)],
      [0.0, zoom, (h / 2 - y_offset) - (cy * zoom)],
      [0.0, 0.0, 1.0]
    ])
    self._model_renderer.set_transform(video_transform @ calib_transform)

    return self._cached_matrix


if __name__ == "__main__":
  gui_app.init_window("OnRoad Camera View")
  road_camera_view = AugmentedRoadView(lambda: None, stream_type=NARROW_ROAD_CAM)
  print("***press space to switch camera view***")
  try:
    for _ in gui_app.render():
      ui_state.update()
      if rl.is_key_released(rl.KeyboardKey.KEY_SPACE):
        if WIDE_CAM in road_camera_view.available_streams:
          stream = NARROW_ROAD_CAM if road_camera_view.stream_type == WIDE_CAM else WIDE_CAM
          road_camera_view.switch_stream(stream)
      road_camera_view.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
  finally:
    road_camera_view.close()
