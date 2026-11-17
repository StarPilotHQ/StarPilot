"""C4 camera choice, bounded reverse evidence, and native stream selection."""

from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
import numpy as np
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from opendbc.car.structs import car as car_schema

from openpilot.common.params import Params
from openpilot.selfdrive.ui.mici.onroad import augmented_road_view as native_camera
from openpilot.starpilot.ui import appearance_compact
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice, onroad_appearance, read_camera_view
from openpilot.starpilot.ui.appearance_preferences import OnroadAppearance
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest, row_change
from openpilot.starpilot.ui.onroad_camera import ReverseDriverCamera
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import BOOT_OFFSET_NS, NOW, RuntimeSnapshotAdapter, ui_fake


class CameraWithoutResources(native_camera.AugmentedRoadView):
  def close(self):
    pass


class CameraViewWithoutResources(native_camera.CameraView):
  def close(self):
    pass


class CameraDisplayTests(unittest.TestCase):
  def test_saved_choice_defaults_to_standard_and_is_parked_source_bound(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      parked = True
      owner = AppearanceOwner(params, lambda: parked)
      row = next(row for row in owner.snapshot(Profile.COMPACT).rows if row.key == "CameraView")
      self.assertEqual((row.value, row.source), ("Standard", None))
      self.assertEqual(onroad_appearance(params).camera_view, CameraViewChoice.STANDARD)
      request = FeatureSettingsRequest(row.key, row.source, "Wide")
      parked = False
      self.assertFalse(owner.apply(request))
      parked = True
      self.assertTrue(owner.apply(request))
      self.assertEqual(read_camera_view(params).raw, b"3")
      self.assertEqual(onroad_appearance(params).camera_view, CameraViewChoice.WIDE)
      self.assertFalse(owner.apply(request))
      source = Path(params.get_param_path("CameraView"))
      source.write_bytes(b"bad")
      self.assertEqual(onroad_appearance(params).camera_view, CameraViewChoice.STANDARD)
      repair_row = next(row for row in owner.snapshot(Profile.COMPACT).rows if row.key == "CameraView")
      self.assertEqual(repair_row.repair_value, "Standard")
      repair = row_change(repair_row)
      assert repair is not None
      self.assertTrue(owner.apply(repair))
      self.assertEqual(source.read_bytes(), b"2")
      source.write_bytes(b"0")
      self.assertEqual(onroad_appearance(params).camera_view, CameraViewChoice.AUTO)
      reverse = next(row for row in owner.snapshot(Profile.COMPACT).rows if row.key == "DriverCamera")
      toggle = row_change(reverse)
      assert toggle is not None
      self.assertTrue(owner.apply(toggle))
      self.assertTrue(onroad_appearance(params).driver_camera_on_reverse)

  def test_compact_selector_lists_all_choices_and_saves_selected_source(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      owner = AppearanceOwner(params, lambda: True)
      source = next(row for row in owner.snapshot(Profile.COMPACT).rows if row.key == "CameraView")
      class Button:
        def __init__(self, label, value):
          self.label, self.value, self.click = label, value, None
        def set_click_callback(self, callback):
          self.click = callback
        def set_enabled(self, enabled):
          self.enabled = enabled
      class Scroller:
        def __init__(self):
          self._scroller = self
          self.items = []
        def add_widgets(self, cards):
          self.items.extend(cards)
        def dismiss(self, callback):
          callback()
      class Session:
        def appearance_snapshot(self):
          return owner.snapshot(Profile.COMPACT)
        def pip_snapshot(self):
          return owner.snapshot(Profile.COMPACT)
        def appearance_request(self, request):
          return owner.apply(request)
        def feature_snapshot(self, page):
          return owner.snapshot(Profile.COMPACT)
        def feature_request(self, request):
          return owner.apply(request)
      shown = []
      refreshed = []
      with patch.object(appearance_compact, "BigButton", Button), patch.object(appearance_compact, "GreyBigButton", Button), \
           patch.object(appearance_compact, "NavScroller", Scroller), \
           patch.object(appearance_compact.gui_app, "push_widget", shown.append), \
           patch.object(appearance_compact.gui_app, "get_active_widget", lambda: shown[-1]):
        appearance_compact.AppearanceCompact(Session())._camera_options(source, lambda: refreshed.append(True))
        self.assertEqual([item.label for item in shown[0].items],
                         ["camera view", "auto", "driver", "standard", "wide", "none"])
        shown[0].items[4].click()
      self.assertEqual(read_camera_view(params).value, CameraViewChoice.WIDE)
      self.assertEqual(refreshed, [True])

  def test_reverse_needs_continuous_fresh_evidence_and_clears_immediately(self):
    policy = ReverseDriverCamera()
    def step(ms, *, drive=(1, 1), enabled=True, fresh=True, reverse=True):
      return policy.step(now_ns=ms * 1_000_000, drive_key=drive, enabled=enabled,
                         car_fresh=fresh, reverse=reverse)
    self.assertFalse(step(0))
    self.assertFalse(step(499))
    self.assertTrue(step(500))
    self.assertFalse(step(501, fresh=False))
    self.assertFalse(step(502))
    self.assertTrue(step(1002))
    self.assertFalse(step(1003, reverse=False))
    self.assertFalse(step(1004))
    self.assertFalse(step(1504, enabled=False))
    self.assertFalse(step(1505))
    self.assertFalse(step(2005, drive=(2, 2)))
    self.assertFalse(step(2506, drive=None))
    self.assertFalse(step(2507, drive=(3, 3)))
    self.assertFalse(step(2000, drive=(3, 3)))

  def test_runtime_discards_stale_or_prestart_reverse(self):
    ui = ui_fake()
    car = ui.sm.messages["carState"]
    car.canValid = True
    car.canTimeout = False
    car.standstill = False
    car.gearShifter = car_schema.CarState.GearShifter.reverse
    adapter = RuntimeSnapshotAdapter(ui)
    def sample(delta_ms, *, age_ms=0, choice=CameraViewChoice.AUTO):
      now = NOW + delta_ms * 1_000_000
      for name in ("deviceState", "pandaStates", "carState"):
        ui.sm.logMonoTime[name] = now + (BOOT_OFFSET_NS if name == "pandaStates" else 0) - (age_ms * 1_000_000 if name == "carState" else 0)
        ui.sm.recv_time[name] = now / 1e9
      with patch("openpilot.starpilot.ui.runtime_snapshot.onroad_appearance",
                 return_value=OnroadAppearance(camera_view=choice, driver_camera_on_reverse=True)):
        return adapter.build(ShellMode.ONROAD, now_ns=now).onroad.reverse_driver_camera
    self.assertFalse(sample(0))
    self.assertFalse(sample(499))
    self.assertTrue(sample(500))
    self.assertFalse(sample(600, age_ms=201))
    self.assertFalse(sample(601))
    self.assertTrue(sample(1101))
    adapter.invalidate_appearance()
    self.assertFalse(sample(1102, choice=CameraViewChoice.NONE), "None overrides reverse camera")
    adapter.invalidate_appearance()
    self.assertFalse(sample(1103), "leaving None must start a new reverse dwell")
    self.assertTrue(sample(1603))
    ui.started_frame = 4
    self.assertFalse(sample(1604))
    ui.sm.recv_frame["carState"] = 5
    self.assertFalse(sample(1605))
    car.gearShifter = car_schema.CarState.GearShifter.drive
    self.assertFalse(sample(2105))

  def test_native_choice_preserves_auto_and_cancels_stale_driver_candidate(self):
    camera = CameraWithoutResources.__new__(CameraWithoutResources)
    camera._stream_type = native_camera.NARROW_ROAD_CAM
    camera._target_stream_type = None
    camera._target_client = None
    camera._switching = False
    camera.available_streams = [native_camera.NARROW_ROAD_CAM, native_camera.WIDE_CAM,
                                native_camera.DRIVER_CAM]
    selected = []
    self.enterContext(patch.object(camera, "switch_stream", selected.append))
    sm = {"selfdriveState": NS(experimentalMode=True), "carState": NS(vEgo=0.0)}
    self.assertEqual(camera._switch_stream_if_needed(sm), native_camera.WIDE_CAM)
    self.assertEqual(selected, [native_camera.WIDE_CAM])
    selected.clear()
    self.assertEqual(camera._switch_stream_if_needed(sm, native_camera.CAMERA_VIEW_STANDARD), native_camera.NARROW_ROAD_CAM)
    self.assertFalse(selected)
    self.assertEqual(camera._switch_stream_if_needed(sm, native_camera.CAMERA_VIEW_DRIVER), native_camera.DRIVER_CAM)
    self.assertEqual(selected, [native_camera.DRIVER_CAM])
    camera._target_stream_type = native_camera.DRIVER_CAM
    camera._target_client = object()
    camera._switching = True
    self.assertEqual(camera._switch_stream_if_needed(sm, native_camera.CAMERA_VIEW_AUTO), native_camera.WIDE_CAM)
    self.assertEqual(selected[-1], native_camera.WIDE_CAM)
    self.assertIsNone(camera._switch_stream_if_needed(sm, native_camera.CAMERA_VIEW_NONE, True))
    self.assertIsNone(camera._target_client)
    camera.available_streams = [native_camera.NARROW_ROAD_CAM]
    selected.clear()
    self.assertEqual(camera._switch_stream_if_needed(sm, native_camera.CAMERA_VIEW_WIDE), native_camera.NARROW_ROAD_CAM)
    self.assertFalse(selected)
    self.assertEqual(camera._switch_stream_if_needed(sm, native_camera.CAMERA_VIEW_DRIVER), native_camera.DRIVER_CAM)
    self.assertFalse(selected)
    camera.available_streams = [native_camera.NARROW_ROAD_CAM, native_camera.WIDE_CAM, native_camera.DRIVER_CAM]
    camera._stream_type = native_camera.DRIVER_CAM
    sm["carState"].vEgo = 7.0
    selected.clear()
    self.assertEqual(camera._switch_stream_if_needed(sm, native_camera.CAMERA_VIEW_AUTO), native_camera.NARROW_ROAD_CAM)
    self.assertEqual(selected, [native_camera.NARROW_ROAD_CAM], "Auto hysteresis cannot retain the old driver stream")
    camera._stream_type = native_camera.NARROW_ROAD_CAM
    camera._target_stream_type = native_camera.DRIVER_CAM
    camera._target_client = object()
    camera._switching = True
    sm["selfdriveState"].experimentalMode = False
    selected.clear()
    self.assertEqual(camera._switch_stream_if_needed(sm, native_camera.CAMERA_VIEW_AUTO), native_camera.NARROW_ROAD_CAM)
    self.assertIsNone(camera._target_client)
    self.assertFalse(camera._switching)
    self.assertFalse(selected)

  def test_native_layer_blacks_out_old_frame_during_driver_switch_or_unavailable_stream(self):
    camera = CameraWithoutResources.__new__(CameraWithoutResources)
    camera._stream_type = native_camera.NARROW_ROAD_CAM
    camera._target_stream_type = native_camera.DRIVER_CAM
    camera._switching = True
    camera.available_streams = [native_camera.DRIVER_CAM]
    camera._ensure_connection = Mock(return_value=True)
    camera._switch_stream_if_needed = Mock(return_value=native_camera.DRIVER_CAM)
    camera._handle_switch = Mock()
    rect = rl.Rectangle(0, 0, 476, 240)
    with patch.object(native_camera.ui_state, "started", True), \
         patch.object(native_camera.CameraView, "_render") as render, \
         patch.object(native_camera.rl, "draw_rectangle_rec") as blackout, \
         patch.object(native_camera, "gui_label") as label:
      camera.render_camera_model_layer(rect, camera_view=native_camera.CAMERA_VIEW_DRIVER)
      render.assert_not_called()
      blackout.assert_called_once()
      label.assert_called_once()
      camera._stream_type = native_camera.DRIVER_CAM
      camera._target_stream_type = native_camera.NARROW_ROAD_CAM
      camera._switch_stream_if_needed.return_value = native_camera.NARROW_ROAD_CAM
      blackout.reset_mock()
      camera.render_camera_model_layer(rect, camera_view=native_camera.CAMERA_VIEW_AUTO)
      render.assert_not_called()
      blackout.assert_called_once()
      camera.available_streams = []
      camera._stream_type = native_camera.NARROW_ROAD_CAM
      camera._switch_stream_if_needed.return_value = native_camera.DRIVER_CAM
      blackout.reset_mock()
      camera.render_camera_model_layer(rect, camera_view=native_camera.CAMERA_VIEW_DRIVER)
      render.assert_not_called()
      blackout.assert_called_once()

  def test_driver_color_filter_changes_only_when_native_stream_switch_completes(self):
    camera = CameraViewWithoutResources.__new__(CameraViewWithoutResources)
    camera._stream_type = native_camera.NARROW_ROAD_CAM
    camera.client = object()
    camera._engaged_val = np.array([0], dtype=np.int32)
    camera._color_strength_val = np.array([0.0], dtype=np.float32)
    camera._color_strength_loc = 3
    camera._enhance_driver_val = np.array([0], dtype=np.int32)
    camera._engaged_loc = 1
    camera._enhance_driver_loc = 2
    camera.shader = object()
    camera._initialize_textures = Mock()
    with patch.object(rl, "set_shader_value"):
      for target, previous, applied in ((native_camera.DRIVER_CAM, 0, 1),
                                        (native_camera.NARROW_ROAD_CAM, 1, 0)):
        camera._target_client = object()
        camera._target_stream_type = target
        camera._switching = True
        camera._update_texture_color_filtering()
        self.assertEqual(camera._enhance_driver_val[0], previous)
        camera._complete_switch()
        camera._update_texture_color_filtering()
        self.assertEqual(camera._enhance_driver_val[0], applied)
        self.assertFalse(camera._switching)


if __name__ == "__main__":
  unittest.main()
