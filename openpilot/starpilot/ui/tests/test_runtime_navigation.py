"""Request routing checks with in-memory owners and no Params or IPC writes."""

import atexit
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.params import Params
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.settings_state import COMPACT_MENU, Destination, SettingsInput, SettingsState


# Importing the existing UI owner constructs UIState, whose PrimeState writes an
# offroad alert. Keep even that import-time side effect inside temporary Params.
_param_dir = tempfile.TemporaryDirectory(prefix="starpilot-ui-owner-test-")
atexit.register(_param_dir.cleanup)
_original_params_init = Params.__init__
with patch.object(Params, "__init__", lambda self, d="": _original_params_init(self, _param_dir.name)):
  from openpilot.selfdrive.ui.layouts.settings.toggles import TogglesLayout


class TestCompactSettingsNavigation(unittest.TestCase):
  def test_nav_widget_constructs_and_dispatches_one_press(self):
    from openpilot.starpilot.ui.runtime_app import StarCompactSettings
    from openpilot.system.ui.lib.application import MousePos
    session = Mock()
    page = StarCompactSettings(session)
    page.set_rect(rl.Rectangle(0, 0, 536, 240))
    page._handle_mouse_press(MousePos(45, 75))
    page._handle_mouse_release(MousePos(45, 75))
    session.press.assert_called_once()
    session.release.assert_called_once()

  def test_scrolled_device_and_developer_cards_emit_distinct_destinations(self):
    emitted = []
    touch = SettingsInput(Profile.COMPACT, emitted.append)
    for destination in (Destination.DEVICE, Destination.GALAXY, Destination.DEVELOPER, Destination.DRIVING_CONTROLS):
      index = next(index for index, item in enumerate(COMPACT_MENU) if item[0] == destination)
      state = SettingsState(compact_scroll_x=-422 * index)
      touch.press(45, 75, state)
      touch.release(45, 75, state)
      self.assertEqual(emitted[-1].destination.destination, destination)

  def test_drag_does_not_activate_a_scrolled_card(self):
    emitted = []
    touch = SettingsInput(Profile.COMPACT, emitted.append)
    state = SettingsState(compact_scroll_x=-844)
    touch.press(45, 75, state)
    touch.move(90, 75, state)
    touch.release(90, 75, state)
    self.assertEqual(emitted, [])

  def test_compact_main_menu_includes_shared_driving_controls(self):
    self.assertEqual([label.lower() for _, label, _, _, _ in COMPACT_MENU],
                     ["toggles", "network", "bluetooth", "force drive state", "vehicle", "device", "software",
                      "driving model", "driving controls", "visuals", "galaxy", "pair to connect", "developer"])

  def test_compact_driving_controls_opens_the_shared_feature_editor(self):
    from openpilot.starpilot.ui.runtime_app import StarMiciMainLayout
    from openpilot.starpilot.ui.feature_settings_state import FeaturePage
    layout = StarMiciMainLayout.__new__(StarMiciMainLayout)
    layout.star = Mock()
    with patch('openpilot.starpilot.ui.feature_settings_compact.FeatureSettingsCompact') as editor:
      layout._open_compact_destination(Destination.DRIVING_CONTROLS)
    editor.assert_called_once_with(layout.star)
    editor.return_value.open.assert_called_once_with(FeaturePage.HUB)
    layout.star._unavailable.assert_not_called()


class TestExistingToggleOwner(unittest.TestCase):
  def test_rhd_toggle_saves_through_shared_owner_and_requests_next_drive(self):
    from openpilot.starpilot.galaxy.localization_settings import LocalizationSettingsOwner
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      owner = TogglesLayout.__new__(TogglesLayout)
      owner._params = params
      owner._localization = LocalizationSettingsOwner(params, lambda: True, lambda: True)
      with patch.object(owner, '_update_toggles'):
        for enabled in (True, False):
          owner._rhd_source = next(row for row in owner._localization.snapshot().rows if row.key == "IsRhdDetected").source
          self.assertTrue(owner._toggle_callback(enabled, 'IsRhdDetected'))
          self.assertEqual(params.get_bool('IsRhdDetected'), enabled)
          self.assertTrue(params.get_bool('OnroadCycleRequested'))
          params.put_bool('OnroadCycleRequested', False, block=True)
        params.put_bool('IsDriverViewEnabled', True, block=True)
        owner._rhd_source = b'0'
        self.assertFalse(owner._toggle_callback(True, 'IsRhdDetected'))
        self.assertFalse(params.get_bool('IsRhdDetected'))
        self.assertFalse(params.get_bool('OnroadCycleRequested'))

  def test_personality_no_op_uses_typed_integer_value(self):
    owner = TogglesLayout.__new__(TogglesLayout)
    owner._long_personality_setting = NS(action_item=NS(enabled=True))
    with patch.object(owner, "_params", NS(get=lambda *_args, **_kwargs: 1), create=True), \
         patch.object(owner, "_update_toggles"), patch.object(owner, "_set_longitudinal_personality") as write:
      self.assertTrue(owner.request_personality(1))
      write.assert_not_called()
      self.assertTrue(owner.request_personality(2))
      write.assert_called_once_with(2)

  def test_locked_control_rejects_request_without_write(self):
    owner = TogglesLayout.__new__(TogglesLayout)
    owner._toggle_defs = {"IsMetric": (lambda: "title", "description", "icon", False)}
    owner._toggles = {"IsMetric": NS(action_item=NS(enabled=False))}
    with patch.object(owner, "_params", NS(get_bool=lambda _: False), create=True), \
         patch.object(owner, "_update_toggles"), patch.object(owner, "_toggle_callback") as write:
      self.assertFalse(owner.request_toggle("IsMetric", True))
      write.assert_not_called()

  def test_acknowledged_control_calls_existing_guarded_owner_once(self):
    owner = TogglesLayout.__new__(TogglesLayout)
    owner._toggle_defs = {"IsMetric": (lambda: "title", "description", "icon", False)}
    owner._toggles = {"IsMetric": NS(action_item=NS(enabled=True))}
    with patch.object(owner, "_params", NS(get_bool=lambda _: False), create=True), \
         patch.object(owner, "_update_toggles"), patch.object(owner, "_toggle_callback") as write:
      self.assertTrue(owner.request_toggle("IsMetric", True))
      write.assert_called_once_with(True, "IsMetric")


class TestNativeCameraComposition(unittest.TestCase):
  def test_both_profiles_draw_only_current_camera_and_model(self):
    from openpilot.selfdrive.ui.onroad import augmented_road_view as large
    from openpilot.selfdrive.ui.mici.onroad import augmented_road_view as compact

    rect = rl.Rectangle(0, 0, 476, 240)
    for module, model_field in ((large, "model_renderer"), (compact, "_model_renderer")):
      with self.subTest(module=module.__name__):
        view = Mock()
        model = Mock(road_style={})
        setattr(view, model_field, model)
        with patch.object(module, "ui_state", NS(started=True, sm=object())), \
             patch.object(module.CameraView, "_render") as camera:
          module.AugmentedRoadView.render_camera_model_layer(view, rect)
        view._switch_stream_if_needed.assert_called_once()
        view._update_calibration.assert_called_once_with()
        camera.assert_called_once_with(view, rect, poll_camera=True, **({"paint": True} if module is compact else {}))
        if model_field == "_model_renderer":
          model.render_with_lead.assert_called_once_with(rect, False, 0, None, lateral_active=False, paint=True)
          model.render_with_lead.reset_mock()
          with patch.object(module, "ui_state", NS(started=True, sm=object())), \
               patch.object(module.CameraView, "_render"):
            compact.AugmentedRoadView.render_camera_model_layer(view, rect, lead_indicator=True,
                                                                lead_info_mode=1, lead_info_metric=True)
          model.render_with_lead.assert_called_once_with(rect, True, 1, True, lateral_active=False, paint=True)
        else:
          model.render.assert_called_once_with(rect)


if __name__ == "__main__":
  unittest.main()
