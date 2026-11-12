import os
import time
import numpy as np
import pyray as rl
from cereal import car, log, messaging
from opendbc.car import structs
from msgq.visionipc import VisionStreamType
from openpilot.selfdrive.ui import UI_BORDER_SIZE
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.lib.starpilot_visuals import get_border_width
from openpilot.selfdrive.ui.onroad.alert_renderer import AlertRenderer
from openpilot.selfdrive.ui.onroad.driver_state import DriverStateRenderer
from openpilot.selfdrive.ui.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.onroad.model_renderer import ModelRenderer
from openpilot.selfdrive.ui.onroad.cameraview import CameraView
from openpilot.selfdrive.ui.lib.starpilot_status import get_screen_edge_color
from openpilot.system.ui.lib.application import gui_app, RECORD_HUD_ONLY, RECORD_CAMERA_ONLY, RECORD_COMBINED
from openpilot.common.transformations.camera import DEVICE_CAMERAS, DeviceCameraConfig, view_frame_from_device_frame
from openpilot.common.transformations.orientation import rot_from_euler

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

BORDER_COLORS = {
  UIStatus.DISENGAGED: rl.Color(0x12, 0x28, 0x39, 0xFF),  # Blue for disengaged state
  UIStatus.OVERRIDE: rl.Color(0x89, 0x92, 0x8D, 0xFF),  # Gray for override state
  UIStatus.ENGAGED: rl.Color(0x16, 0x7F, 0x40, 0xFF),  # Green for engaged state
}

WIDE_CAM_MAX_SPEED = 10.0  # m/s (22 mph)
ROAD_CAM_MIN_SPEED = 15.0  # m/s (34 mph)
INF_POINT = np.array([1000.0, 0.0, 0.0])
REVERSE_DRIVER_CAMERA_DELAY_FRAMES = max(1, int(round(gui_app.target_fps * 0.5)))


class AugmentedRoadView(CameraView):
  def __init__(self, stream_type: VisionStreamType = VisionStreamType.VISION_STREAM_ROAD):
    super().__init__("camerad", stream_type)
    self._set_placeholder_color(BORDER_COLORS[UIStatus.DISENGAGED])

    self.device_camera: DeviceCameraConfig | None = None
    self.view_from_calib = view_frame_from_device_frame.copy()
    self.view_from_wide_calib = view_frame_from_device_frame.copy()

    self._matrix_cache_key = (0, 0.0, 0.0, stream_type)
    self._cached_matrix: np.ndarray | None = None
    self._content_rect = rl.Rectangle()
    self._reverse_driver_camera_frames = 0
    self._reverse_driver_camera_active = False
    self._camera_view_none = False
    self._driver_stream_active = False
    self._draw_road_overlays = True
    self._draw_hud_controls = True
    self._draw_driver_state = True

    self.model_renderer = ModelRenderer()
    self._hud_renderer = HudRenderer()
    self.alert_renderer = AlertRenderer()
    self.driver_state_renderer = DriverStateRenderer()

    # debug
    self._pm = messaging.PubMaster(['uiDebug'])

  def _render(self, rect):
    # Only render when system is started to avoid invalid data access
    start_draw = time.monotonic()
    if not ui_state.started:
      return

    camera_view = self._camera_view()
    self._camera_view_none = camera_view == CAMERA_VIEW_NONE
    self._switch_stream_if_needed(ui_state.sm, camera_view)
    in_reverse = self._is_in_reverse()
    self._driver_stream_active = self.stream_type == DRIVER_CAM
    self._draw_road_overlays = not in_reverse and not self._driver_stream_active and not self._camera_view_none
    self._draw_hud_controls = self._camera_view_none or (not in_reverse and not self._driver_stream_active)

    # Update calibration before rendering
    self._update_calibration()

    border_width = self._get_border_width()

    # Create inner content area with border padding
    self._content_rect = rl.Rectangle(
      rect.x + border_width,
      rect.y + border_width,
      rect.width - 2 * border_width,
      rect.height - 2 * border_width,
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
    if self._camera_view_none:
      rl.draw_rectangle_rec(self._content_rect, rl.BLACK)
    else:
      super()._render(self._content_rect)

    # Draw all UI overlays
    if self._draw_road_overlays:
      self.model_renderer.render(self._content_rect)
      self._render_extra_road_overlays(self._content_rect)
    if self._draw_hud_controls:
      self._hud_renderer.render(self._content_rect)
    if self._draw_driver_state:
      self.driver_state_renderer.render(self._content_rect)
    self.alert_renderer.render(self._content_rect)

    # Custom UI extension point - add custom overlays here
    # Use self._content_rect for positioning within camera bounds

    # End clipping region
    rl.end_scissor_mode()

    # Draw colored border based on driving state
    self._draw_border(rect)

    # publish uiDebug
    msg = messaging.new_message('uiDebug')
    msg.uiDebug.drawTimeMillis = (time.monotonic() - start_draw) * 1000
    self._pm.send('uiDebug', msg)

  def _render_extra_road_overlays(self, rect: rl.Rectangle) -> None:
    """Render subclass road overlays inside the content scissor, above the model and below the HUD."""

  def _handle_mouse_press(self, _):
    if not self._hud_renderer.user_interacting() and self._click_callback is not None:
      self._click_callback()

  def _handle_mouse_release(self, _):
    # We only call click callback on press if not interacting with HUD
    pass

  def _get_border_width(self) -> int:
    return get_border_width(UI_BORDER_SIZE, ui_state.ui_params)

  def _draw_border(self, rect: rl.Rectangle):
    border_width = self._get_border_width()
    rl.draw_rectangle_lines_ex(rect, border_width, rl.BLACK)
    border_roundness = 0.12
    border_color = get_screen_edge_color(ui_state)
    border_rect = rl.Rectangle(rect.x + border_width, rect.y + border_width,
                               rect.width - 2 * border_width, rect.height - 2 * border_width)
    rl.draw_rectangle_rounded_lines_ex(border_rect, border_roundness, 10, border_width, border_color)

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

  def _update_reverse_driver_camera_state(self) -> bool:
    params = ui_state.ui_params
    should_force_driver = ui_state.started and params.get_bool("DriverCamera") and self._is_in_reverse()
    if not should_force_driver:
      self._reverse_driver_camera_frames = 0
      self._reverse_driver_camera_active = False
      return False

    self._reverse_driver_camera_frames = min(self._reverse_driver_camera_frames + 1, REVERSE_DRIVER_CAMERA_DELAY_FRAMES)
    self._reverse_driver_camera_active = self._reverse_driver_camera_frames >= REVERSE_DRIVER_CAMERA_DELAY_FRAMES
    return self._reverse_driver_camera_active

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

    params = ui_state.ui_params
    camera_view = params.get_int("CameraView", return_default=True, default=CAMERA_VIEW_STANDARD)
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
      target = DRIVER_CAM
    else:
      if reentry_selection_pending or not self.available_streams:
        self._refresh_available_streams()

      if camera_view == CAMERA_VIEW_DRIVER:
        target = DRIVER_CAM
      elif camera_view == CAMERA_VIEW_STANDARD:
        target = ROAD_CAM
      elif camera_view == CAMERA_VIEW_WIDE:
        target = WIDE_CAM if WIDE_CAM in self.available_streams else ROAD_CAM
      elif sm['selfdriveState'].experimentalMode and WIDE_CAM in self.available_streams:
        v_ego = sm['carState'].vEgo
        if v_ego < WIDE_CAM_MAX_SPEED:
          target = WIDE_CAM
        elif v_ego > ROAD_CAM_MIN_SPEED:
          target = ROAD_CAM
        else:
          # Hysteresis zone - keep the current or pending road camera selection.
          current_road_stream = (self._target_stream_type if self._switching and
                                 self._target_stream_type in (ROAD_CAM, WIDE_CAM) else self.stream_type)
          target = WIDE_CAM if current_road_stream == WIDE_CAM else ROAD_CAM
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
      driver_view_ratio = 2.0
      base[0, 0] *= driver_view_ratio
      base[1, 1] *= driver_view_ratio
      return base

    # Check if we can use cached matrix
    cache_key = (
      ui_state.sm.recv_frame['liveCalibration'],
      self._content_rect.width,
      self._content_rect.height,
      self.stream_type
    )
    if cache_key == self._matrix_cache_key and self._cached_matrix is not None:
      return self._cached_matrix

    # Get camera configuration
    device_camera = self.device_camera or DEFAULT_DEVICE_CAMERA
    is_wide_camera = self.stream_type == WIDE_CAM
    intrinsic = device_camera.ecam.intrinsics if is_wide_camera else device_camera.fcam.intrinsics
    calibration = self.view_from_wide_calib if is_wide_camera else self.view_from_calib
    zoom = 2.0 if is_wide_camera else 1.1

    # Calculate transforms for vanishing point
    calib_transform = intrinsic @ calibration
    kep = calib_transform @ INF_POINT

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
        y_offset = np.clip((kep[1] / kep[2] - cy) * zoom, -max_y_offset, max_y_offset)
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

    video_transform = np.array([
      [zoom, 0.0, (w / 2 + x - x_offset) - (cx * zoom)],
      [0.0, zoom, (h / 2 + y - y_offset) - (cy * zoom)],
      [0.0, 0.0, 1.0]
    ])
    self.model_renderer.set_transform(video_transform @ calib_transform)

    return self._cached_matrix


if __name__ == "__main__":
  gui_app.init_window("OnRoad Camera View")
  road_camera_view = AugmentedRoadView(ROAD_CAM)
  gui_app.push_widget(road_camera_view)
  print("***press space to switch camera view***")
  try:
    for _ in gui_app.render():
      ui_state.update()
      if rl.is_key_released(rl.KeyboardKey.KEY_SPACE):
        if WIDE_CAM in road_camera_view.available_streams:
          stream = ROAD_CAM if road_camera_view.stream_type == WIDE_CAM else WIDE_CAM
          road_camera_view.switch_stream(stream)
  finally:
    road_camera_view.close()
