"""Large value-editor adoption: real owners, touch routing, and unchanged save guards."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import Mock, patch

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot.lateral.lane_change_preferences import read_saved
from openpilot.starpilot.ui import runtime_app
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings import FeatureSettingsView
from openpilot.starpilot.ui.feature_settings_state import (
  FeatureInput, FeaturePage, FeatureRow, FeatureSettingsState, FeatureUiAction, feature_row_top,
  row_change, row_request, value_buttons, value_done_rect, value_editor_rect, value_editor_state, value_request,
)
from openpilot.starpilot.ui.pip_owner import PiPOwner
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_feature_settings_visuals import fake_fonts
from openpilot.system.ui.widgets import DialogResult


class ValueEditorTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    cp.openpilotLongitudinalControl = True
    self.owner = FeatureSettingsOwner(self.params, lambda _group: True,
                                      vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp)
    self.session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    self.session.profile = Profile.LARGE
    self.session._mode, self.session.selected = ShellMode.SETTINGS, Destination.DRIVING_CONTROLS
    self.session.feature_page, self.session.feature_root_page = FeaturePage.LANE_CHANGE, FeaturePage.HUB
    self.session.feature_scroll, self.session.feature_edit_key = 5, None
    self.session.feature_owner = self.owner
    self.session.configuration_allowed = Mock(return_value=True)
    self.session._feature_authority = Mock(return_value=True)
    self.session._settings_preference_authority = Mock(return_value=True)
    self.session._snapshot_cache = None
    self.session._lane_change_request_epoch = 0
    self.session.input, self.session.adapter, self.session._unavailable = Mock(), Mock(), Mock()
    self.session.vehicle_selector = Mock(spec=runtime_app.VehicleLarge, rows=Mock(return_value=()))
    self.enterContext(patch.object(runtime_app, "ui_state", NS(is_metric=False)))

  @staticmethod
  def tap(state, bounds, emit):
    x, y, width, height = bounds
    point = x + width / 2, y + height / 2
    touch = FeatureInput(emit)
    touch.press(*point, state)
    touch.release(*point, state)

  def open_feature(self, key):
    row = next(row for row in self.session.feature_snapshot().rows if row.key == key)
    self.session._feature_ui(FeatureUiAction("open", row))
    return self.session.feature_snapshot()

  def test_only_value_controls_change_and_all_request_evidence_is_preserved(self):
    number = FeatureRow("number", "Time", "1.5", b"saved", step=.1, minimum=0., maximum=3., unit="s", available=True,
                        default_value="1", related_source=b"units", vehicle_fingerprint="car", capability=("cap",),
                        dependencies=(("dependency", b"source"),), display_unit="s")
    unchanged = (FeatureRow("toggle", "Toggle", "On", choices=("Off", "On"), available=True),
                 replace(number, page="child"), replace(number, repair_value="0"), replace(number, actions=(("Reset", True),)),
                 replace(number, value="Invalid"), FeatureRow("reset", "Reset", "Reset", choices=("Reset",), available=True))
    state = FeatureSettingsState(rows=(number, *unchanged))
    summary = value_editor_state(state, None)
    row = summary.rows[0]
    self.assertEqual(summary.rows[1:], unchanged)
    self.assertEqual(replace(row, presets=(), page=""), number)
    self.assertEqual(tuple(value for value, _ in row.presets), ("0.0", "1.0", "3.0"))
    self.assertIsNone(value_request(summary, FeatureUiAction('change', row, 1)))
    for expanded in (False, True):
      editor = replace(value_editor_state(state, "number"), sidebar_expanded=expanded)
      row = editor.rows[0]
      for index, (value, bounds, enabled) in enumerate(value_buttons(editor, row, feature_row_top(editor))):
        actions = []
        self.tap(editor, bounds, actions.append)
        self.assertEqual(len(actions), int(enabled))
        if enabled:
          expected = row_change(row, -1 if value == "-" else 1) if value in ("-", "+") else row_request(row, value)
          self.assertEqual(value_request(editor, actions[0]), expected)
          self.assertIsNone(value_request(replace(editor, rows=(replace(row, source=b"changed"),)), actions[0]))
        disabled = replace(editor, rows=(replace(row, available=False),))
        self.assertIsNone(value_request(disabled, FeatureUiAction("action", disabled.rows[0], index)))

  def test_every_choice_remains_reachable_and_quick_picks_are_bounded(self):
    for count in (2, 3, 5, 15):
      choices = tuple(f"Choice {i}" for i in range(count))
      row = FeatureRow("choice", "Choice", choices[0], choices=choices, available=True, default_value=choices[1])
      editor = value_editor_state(FeatureSettingsState(rows=(row,)), row.key)
      current = editor.rows[0]
      self.assertLessEqual(len(current.presets), 3)
      self.assertIn((choices[1], choices[1]), current.presets)
      for choice in choices[1:]:
        request = value_request(replace(editor, rows=(current,)), FeatureUiAction("change", current, 1))
        self.assertEqual(request.value, choice)
        current = replace(current, value=choice)
      self.assertIsNone(value_request(replace(editor, rows=(current,)), FeatureUiAction("change", current, 1)))

  def test_long_choices_and_precise_numbers_fit_the_existing_editor(self):
    from openpilot.starpilot.ui.raylib import rl
    rows = (FeatureRow("choice", "Choice", "Use the selected vehicle controller", available=True,
                       choices=("Use the selected vehicle controller", "Use the other controller")),
            FeatureRow("number", "Number", "123.45678901234567", step=.01, minimum=0., maximum=200., available=True, unit="m/s²"))
    for row in rows:
      for expanded in (False, True):
        state = replace(value_editor_state(FeatureSettingsState(rows=(row,)), row.key), sidebar_expanded=expanded)
        fonts = fake_fonts()
        renderer = FeatureSettingsView(fonts)
        renderer._center = Mock()
        with patch("openpilot.starpilot.ui.feature_settings.draw_rounded_fill"), patch("openpilot.starpilot.ui.feature_settings.draw_rounded_stroke"):
          renderer._value_row(state, state.rows[0], rl.Rectangle(*value_editor_rect(state)),
                              value_buttons(state, state.rows[0], feature_row_top(state)), None)
        for call in renderer._center.call_args_list:
          text, size, bounds, *_ = call.args
          self.assertLessEqual(fonts.measure(text, None, size).width, bounds.width + .001)

  def test_lane_speed_presets_confirm_save_and_reject_changed_units_or_abandoned_editor(self):
    dialogs = []
    self.enterContext(patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog",
                            side_effect=lambda *args, **kw: NS(callback=kw["callback"])))
    self.enterContext(patch.object(runtime_app.gui_app, "push_widget", dialogs.append))
    for metric in (False, True):
      self.params.put_bool("IsMetric", metric, block=True)
      editor = self.open_feature("lane_change:speed")
      self.assertEqual(editor.save_hint, "Changes require confirmation")
      self.assertEqual(editor.rows[0].unit, "km/h" if metric else "mph")
      self.assertEqual({float(value) for value, _ in editor.rows[0].presets},
                       {10, 30, 50, 70, 100} if metric else {5, 15, 30, 45, 60})
      bounds = next(bounds for value, bounds, _ in value_buttons(editor, editor.rows[0], feature_row_top(editor)) if float_or_none(value) == 30.)
      before = read_saved(self.params).raw
      self.tap(editor, bounds, self.session._feature_ui)
      self.assertEqual(read_saved(self.params).raw, before)
      dialogs[-1].callback(DialogResult.CANCEL)
      self.assertEqual(read_saved(self.params).raw, before)
      self.tap(editor, bounds, self.session._feature_ui)
      dialogs[-1].callback(DialogResult.CONFIRM)
      self.assertAlmostEqual(read_saved(self.params).policy.minimum_speed_mps, 30 / 3.6 if metric else 30 * .44704)
      self.session._feature_ui(FeatureUiAction("back"))
      self.assertEqual(self.session.feature_scroll, 5)
      editor = self.open_feature("lane_change:speed")
      plus = value_buttons(editor, editor.rows[0], feature_row_top(editor))[-1][1]
      self.tap(editor, plus, self.session._feature_ui)
      before = read_saved(self.params).raw
      self.params.put_bool("IsMetric", not metric, block=True)
      dialogs[-1].callback(DialogResult.CONFIRM)
      self.assertEqual(read_saved(self.params).raw, before)
      self.params.put_bool("IsMetric", metric, block=True)
      self.tap(editor, plus, self.session._feature_ui)
      self.tap(editor, value_done_rect(editor), self.session._feature_ui)
      dialogs[-1].callback(DialogResult.CONFIRM)
      self.assertEqual(read_saved(self.params).raw, before)
      self.assertEqual(self.session.feature_page, FeaturePage.LANE_CHANGE)

  def test_all_feature_pages_convert_valid_numbers_and_choices_without_affecting_compact_or_favorites(self):
    for page in (*FeaturePage, "aggressive/acceleration", "standard/braking", "relaxed/following"):
      self.session.feature_page = page
      large = self.session.feature_snapshot()
      original = self.owner.snapshot(page, parked=True, system_long=True, lateral_context=True, metric=False,
                                     configure_while_driving=True)
      for before, after in zip(original.rows, large.rows, strict=True):
        if before.step and not before.repair_value and float_or_none(before.value) is not None:
          self.assertTrue(after.presets, before.key)
        self.assertEqual(replace(after, page=before.page, presets=before.presets), before)
      self.session.profile = Profile.COMPACT
      self.assertEqual(self.session.feature_snapshot(), original)
      self.session.profile = Profile.LARGE
      self.assertTrue(all(not row.presets for row in self.session.feature_snapshot(page, favorite=True).rows))

  def test_cruise_interval_editor_keeps_the_owners_required_confirmation(self):
    self.owner.show_cruise_intervals = True
    self.owner.vehicle_params().pcmCruise = False
    self.session.feature_page = FeaturePage.WHEEL
    editor = self.open_feature("CustomCruise")
    dialogs = []
    with patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", side_effect=lambda *args, **kw: NS(callback=kw["callback"])), \
         patch.object(runtime_app.gui_app, "push_widget", dialogs.append):
      self.tap(editor, value_buttons(editor, editor.rows[0], feature_row_top(editor))[-1][1], self.session._feature_ui)
      self.assertIsNone(self.params.get("CustomCruise"))
      dialogs[-1].callback(DialogResult.CONFIRM)
    self.assertEqual(float(self.params.get("CustomCruise")), 2.)
    self.session._unavailable.assert_not_called()

  def test_slc_steps_keep_direction_and_presets_keep_exact_source_guards(self):
    self.session.feature_page = FeaturePage.SLC
    editor = self.open_feature("slc:offset:2")
    row = editor.rows[0]
    self.assertEqual(value_request(editor, FeatureUiAction("change", row, 1)).direction, 1)
    self.tap(editor, value_buttons(editor, row, feature_row_top(editor))[-1][1], self.session._feature_ui)
    self.assertEqual(float(self.session.feature_snapshot().rows[0].value), 1.)
    before = Path(self.params.get_param_path("SLCOffsetSchedule")).read_bytes()
    self.tap(editor, value_buttons(editor, row, feature_row_top(editor))[0][1], self.session._feature_ui)
    self.assertEqual(Path(self.params.get_param_path("SLCOffsetSchedule")).read_bytes(), before)
    self.session._unavailable.assert_not_called()

  def test_camera_crop_editor_saves_and_done_preserves_parent_position(self):
    self.session.selected = Destination.APPEARANCE
    self.session.appearance_page, self.session.appearance_scroll = "pip", 5
    self.session.appearance_owner = AppearanceOwner(self.params, lambda: True)
    self.session.pip_owner = PiPOwner(self.params, lambda: True, lambda: False)
    row = next(row for row in self.session.appearance_snapshot().rows if row.key == "pip:mask:size")
    self.session._appearance_ui(FeatureUiAction("open", row))
    editor = self.session.appearance_snapshot()
    self.assertTrue(editor.editor)
    self.tap(editor, value_buttons(editor, editor.rows[0], feature_row_top(editor))[0][1], self.session._appearance_ui)
    self.assertEqual(float(self.session.appearance_snapshot().rows[0].value), float(row.value) - row.step)
    self.tap(editor, value_done_rect(editor), self.session._appearance_ui)
    self.assertEqual((self.session.appearance_page, self.session.appearance_scroll), ("pip", 5))
    self.session._unavailable.assert_not_called()


def float_or_none(value):
  try:
    return float(value)
  except ValueError:
    return None
