"""Saved display choices in real disposable Params and native UI adapters."""
from unittest.mock import Mock, patch
from dataclasses import replace


import os
from types import SimpleNamespace as NS
from pathlib import Path
import tempfile
import unittest

from openpilot.common.params import Params
from openpilot.starpilot.ui import display_compact
from openpilot.starpilot.ui.display_owner import DisplayOwner
from openpilot.starpilot.ui.display_preferences import MASTER, SCREEN_OFF, read_choice, read_preferences
from openpilot.starpilot.ui.feature_settings_state import (
  FEATURE_CONTROL_LEFT, FEATURE_CONTROL_RIGHT, FEATURE_ROW_HEIGHT, FEATURE_VISIBLE_ROWS,
  FeatureInput, FeatureSettingsRequest, feature_row_top, row_change,
  FeatureSettingsState, FeatureUiAction, feature_action_rects, value_buttons, value_done_rect,
)
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.settings_state import Destination, SettingsAction, SettingsActionKind, SettingsInput, SettingsState, tile_rects
from openpilot.starpilot.ui.shell import ShellInput, ShellMode, ShellRequest, ShellSnapshot
from openpilot.starpilot.ui.power_owner import PowerOwner, HOURS, VOLTS
from openpilot.system.ui.widgets import DialogResult
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, ui_fake


class DisplaySettingsTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params_root = temporary.name
    self.params = Params(temporary.name)
    self.parked = True
    self.owner = DisplayOwner(self.params, lambda: self.parked, burn_in_capable=True)   # as on a comma 4

  def row(self, key, profile=Profile.LARGE):
    return next(row for row in self.owner.snapshot(profile).rows if row.key == key)

  def request(self, key, profile=Profile.LARGE):
    request = row_change(self.row(key, profile))
    assert request is not None
    return request

  def large_session(self, expanded=True):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    session = StarShellSession.__new__(StarShellSession)
    session.profile = Profile.LARGE
    session._mode, session.selected = ShellMode.SETTINGS, Destination.SYSTEM
    session.display_owner = self.owner
    session.power_owner = PowerOwner(self.params, lambda: self.parked)
    session.map_snapshot = Mock(return_value=FeatureSettingsState())
    self.enterContext(patch.object(session, 'drive_state',
                                  NS(snapshot=lambda: {"mode": "auto", "revision": None, "available": False,
                                                       "effective": None, "overrideAllowed": False}), create=True))
    session.display_scroll, session.display_edit_key = 0, None
    session.display_page = "system"
    session.sidebar_expanded = expanded
    session._snapshot_cache = None
    session._on_destination_change = None
    session.confirmed_offroad = Mock(return_value=True)
    session.favorites, session.view = Mock(), Mock()
    session.view.display.reset.return_value = False
    session._unavailable = Mock()
    session.input = ShellInput(Profile.LARGE, session._emit)
    return session

  def test_system_pages_keep_every_control_in_a_stable_group(self):
    from openpilot.starpilot.ui.maps_state import map_page
    session = self.large_session()
    first = ("drive_state", "device_power", MASTER, "power:enabled", HOURS)
    for enabled in (False, True):
      self.params.put_bool(MASTER, enabled, block=True)
      for brightness in (50, 101):
        self.params.put("ScreenBrightness", brightness, block=True)
        for status in (None, "idle", "transferring", "failed"):
          maps = map_page(None if status is None else {"schemaVersion": 1, "state": status,
                          "selectedGeneration": "", "completedGroups": 1, "totalGroups": 2})
          session.map_snapshot.return_value = maps
          rows = session.system_snapshot().rows
          self.assertEqual(tuple(row.key for row in rows[:5]), first)
          self.assertEqual(rows[5].key, VOLTS)
          self.assertEqual(rows[6:], maps.rows)
          session._display_ui(FeatureUiAction("open", rows[2]))
          submenu = session.system_snapshot()
          self.assertEqual(tuple(row.key for row in submenu.rows), (MASTER, "ScreenBrightness", "ScreenBrightnessOnroad",
                                                                   "ScreenTimeout", "ScreenTimeoutOnroad", SCREEN_OFF))
          self.assertEqual(submenu.rows[1].source, self.row("ScreenBrightness").source)
          self.assertEqual(submenu.rows[1].dependencies, self.row("ScreenBrightness").dependencies)
          session._display_ui(FeatureUiAction("back"))

  def test_system_modes_select_directly_and_cancel_changed_or_dragged_touches(self):
    session = self.large_session()
    session._drive_change = Mock()
    status = {"mode": "auto", "revision": "shown-revision", "available": True,
              "effective": "offroad", "overrideAllowed": True}
    session.drive_state.snapshot = Mock(return_value=status)
    inputs = FeatureInput(session._display_ui)
    for expanded in (False, True):
      for current in ("auto", "offroad", "onroad"):
        status["mode"] = current
        state = replace(session.system_snapshot(), sidebar_expanded=expanded)
        row = state.rows[0]
        rects = feature_action_rects(row, feature_row_top(state))
        for index, desired in enumerate(("auto", "offroad", "onroad")):
          session._drive_change.reset_mock()
          x, y, width, height = rects[index]
          point = x + width / 2, y + height / 2
          inputs.press(*point, state)
          session._drive_change.assert_not_called()
          inputs.release(*point, state)
          if desired == current:
            session._drive_change.assert_not_called()
          else:
            session._drive_change.assert_called_once_with(desired, "shown-revision")
        self.assertIsNone(inputs.target(rects[0][0] + rects[0][2] + 8, point[1], state))
    status["mode"] = "auto"
    state = session.system_snapshot()
    row = state.rows[0]
    x, y, width, height = feature_action_rects(row, feature_row_top(state))[2]
    point = x + width / 2, y + height / 2
    for changed in (replace(state, scroll=5), replace(state, sidebar_expanded=False),
                    replace(state, rows=(replace(row, source=b"new-revision"),) + state.rows[1:]),
                    replace(state, rows=(replace(row, available=False),) + state.rows[1:])):
      session._drive_change.reset_mock()
      inputs.press(*point, state)
      inputs.release(*point, changed)
      session._drive_change.assert_not_called()
    inputs.press(*point, state)
    inputs.move(point[0] - 50, point[1], state)
    inputs.release(*point, state)
    session._drive_change.assert_not_called()
    status["overrideAllowed"] = False
    fresh = session.system_snapshot()
    self.assertIsNotNone(inputs.target(*point, fresh))
    session._drive_change.reset_mock()
    session._display_ui(FeatureUiAction("action", fresh.rows[0], 2))
    session._drive_change.assert_called_once_with("onroad", "shown-revision")
    self.assertTrue(fresh.rows[0].actions[1][1])  # Local Offroad remains available while moving.
    session._drive_change.reset_mock()
    session._display_ui(FeatureUiAction("action", fresh.rows[0], 1))
    session._drive_change.assert_called_once_with("offroad", "shown-revision")

  def test_display_submenu_opens_while_off_enabling_enters_and_back_keeps_the_hierarchy(self):
    with patch("openpilot.starpilot.ui.runtime_app.native_device.invalidate_display_preferences"):
      for expanded in (True, False):
        session = self.large_session(expanded)
        inputs = FeatureInput(session._display_ui)
        root = replace(session.system_snapshot(), sidebar_expanded=expanded)
        y = feature_row_top(root) + 2.5 * FEATURE_ROW_HEIGHT
        inputs.press(800, y, root)
        inputs.release(800, y, root)
        submenu = session.system_snapshot()
        self.assertEqual(submenu.page, "display")
        self.assertEqual(len(submenu.rows), 6)
        self.assertEqual(submenu.rows[0].key, MASTER)
        self.assertTrue(all(not row.available for row in submenu.rows[1:] if row.key != SCREEN_OFF))
        self.assertTrue(submenu.rows[-1].available)
        self.assertFalse(read_preferences(self.params, large=True).enabled)
        session._emit(ShellRequest("settings", SettingsAction(SettingsActionKind.CLOSE)))
        self.assertEqual(session.display_page, "system")
        root = replace(session.system_snapshot(), sidebar_expanded=expanded)
        inputs.press(2000, y, root)
        inputs.release(2000, y, root)
        self.assertTrue(read_preferences(self.params, large=True).enabled)
        submenu = session.system_snapshot()
        self.assertEqual(submenu.page, "display")
        self.assertTrue(all(row.available for row in submenu.rows))
        self.assertEqual(submenu.parent_title, "System")
        session._display_ui(FeatureUiAction("open", submenu.rows[1]))
        self.assertEqual(session.system_snapshot().parent_title, "Custom Display Settings")
        session._emit(ShellRequest("settings", SettingsAction(SettingsActionKind.CLOSE)))
        self.assertIsNone(session.display_edit_key)
        self.assertEqual(session.system_snapshot().page, "display")
        session._display_ui(FeatureUiAction("change", session.system_snapshot().rows[0]))
        self.assertEqual(session.system_snapshot().page, "display")
        self.assertFalse(read_preferences(self.params, large=True).enabled)
        self.assertTrue(all(not row.available for row in session.system_snapshot().rows[1:] if row.key != SCREEN_OFF))
        session._display_ui(FeatureUiAction("back"))
        self.parked = False
        root = session.system_snapshot()
        session._display_ui(FeatureInput.target(800, y, root))
        self.assertEqual(session.system_snapshot().page, "display")
        self.parked = True

  def test_display_parent_does_not_enter_after_failed_enable_or_a_changed_page(self):
    session = self.large_session()
    Path(self.params.get_param_path("ScreenBrightness")).write_bytes(b"bad")
    root = session.system_snapshot()
    session._display_ui(FeatureUiAction("change", root.rows[2]))
    self.assertEqual(session.display_page, "system")
    self.assertFalse(read_preferences(self.params, large=True).enabled)
    session._unavailable.assert_called_once()
    inputs = FeatureInput(session._display_ui)
    y = feature_row_top(root) + 2.5 * FEATURE_ROW_HEIGHT
    inputs.press(2000, y, root)
    session._display_ui(FeatureUiAction("open", root.rows[2]))
    inputs.release(2000, y, session.system_snapshot())
    self.assertFalse(read_preferences(self.params, large=True).enabled)
    self.assertTrue(session.system_snapshot().rows[1].available)  # Invalid saved choices remain repairable.

  def test_system_power_touches_revalidate_engagement_and_offroad(self):
    from openpilot.starpilot.ui import runtime_app
    session = self.large_session()
    session.system_power_action = Mock()
    ui = NS(engaged=False, is_offroad=lambda: self.parked)
    with patch.object(runtime_app, "ui_state", ui):
      state = session.system_snapshot()
      row = state.rows[1]
      inputs = FeatureInput(session._display_ui)
      for index, rect in enumerate(feature_action_rects(row, feature_row_top(state) + FEATURE_ROW_HEIGHT)):
        x, y, width, height = rect
        inputs.press(x + width / 2, y + height / 2, state)
        session.system_power_action.assert_not_called()
        inputs.release(x + width / 2, y + height / 2, state)
        session.system_power_action.assert_called_once_with(index)
        session.system_power_action.reset_mock()
      for index in (-1, 2):
        session._display_ui(FeatureUiAction("action", row, index))
      ui.engaged = True
      session._display_ui(FeatureUiAction("action", row, 0))
      ui.engaged = False
      self.parked = False
      session._display_ui(FeatureUiAction("action", row, 1))
      session.system_power_action.assert_not_called()
      self.assertEqual(session.system_snapshot().rows[1].actions, (("Reboot", True), ("Power Off", False)))

  def touch(self, session, state, bounds):
    from openpilot.starpilot.ui import runtime_app
    state = replace(state, sidebar_expanded=session.sidebar_expanded)
    snapshot = ShellSnapshot(ShellMode.SETTINGS, Mock(), SettingsState(sidebar_expanded=session.sidebar_expanded), Mock(),
                             selected=Destination.SYSTEM, display=state)
    session.snapshot = Mock(return_value=snapshot)
    x, y, width, height = bounds
    with patch.object(runtime_app, "ui_state", NS(started=False, started_frame=0)):
      session.press(ShellMode.SETTINGS, x + width / 2, y + height / 2)
      session.move(ShellMode.SETTINGS, x + width / 2, y + height / 2)
      return session.release(ShellMode.SETTINGS, x + width / 2, y + height / 2)

  def test_system_presets_touch_save_confirm_and_done_from_overview_pages(self):
    from openpilot.starpilot.ui.runtime_app import SYSTEM_PRESETS
    self.params.put_bool(MASTER, True, block=True)
    steps = {"ScreenBrightness": ("25", "26"), "ScreenBrightnessOnroad": ("25", "26"),
             "ScreenTimeout": ("15", "20"), "ScreenTimeoutOnroad": ("15", "20"),
             HOURS: ("6 h", "7 h"), VOLTS: ("12.0 V", "12.1 V")}
    self.assertEqual(set(SYSTEM_PRESETS), set(steps))
    def fake_dialog(_question, _button, callback):
      return NS(callback=callback)
    with patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget") as pushed, \
         patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", side_effect=fake_dialog), \
         patch("openpilot.starpilot.ui.runtime_app.native_device.invalidate_display_preferences"):
      for expanded in (True, False):
        for key, presets in SYSTEM_PRESETS.items():
          session = self.large_session(expanded)
          if key not in (HOURS, VOLTS):
            session._display_ui(FeatureUiAction("open", session.system_snapshot().rows[2]))
          overview = session.system_snapshot()
          index = next(i for i, row in enumerate(overview.rows) if row.key == key)
          session.display_scroll = index // FEATURE_VISIBLE_ROWS * FEATURE_VISIBLE_ROWS
          overview = replace(overview, scroll=session.display_scroll)
          self.assertFalse(any(row.key.startswith("display:auto:") for row in overview.rows))
          y = feature_row_top(overview) + (index - overview.scroll) * FEATURE_ROW_HEIGHT
          self.assertTrue(self.touch(session, overview, (700, y, 200, FEATURE_ROW_HEIGHT)))
          self.assertEqual(session.display_edit_key, key)
          for value, _label in presets:
            editor = replace(session.system_snapshot(), sidebar_expanded=expanded)
            row = editor.rows[0]
            button = next(button for button in value_buttons(editor, row, feature_row_top(editor)) if button[0] == value)
            if not button[2]:
              continue
            pushed.reset_mock()
            self.assertTrue(self.touch(session, editor, button[1]))
            if key in (HOURS, VOLTS):
              self.assertEqual(session.system_snapshot().rows[0].source, row.source)
              dialog = pushed.call_args.args[0]
              dialog.callback(DialogResult.CANCEL)
              self.assertEqual(session.system_snapshot().rows[0].source, row.source)
              pushed.reset_mock()
              self.assertTrue(self.touch(session, editor, button[1]))
              pushed.call_args.args[0].callback(DialogResult.CONFIRM)
            else:
              pushed.assert_not_called()
            fresh = session.system_snapshot()
            self.assertEqual(fresh.rows[0].value, value)
            self.assertFalse(next(button for button in value_buttons(fresh, fresh.rows[0]) if button[0] == value)[2])
            if value == steps[key][0]:
              fresh = replace(fresh, sidebar_expanded=expanded)
              plus = value_buttons(fresh, fresh.rows[0], feature_row_top(fresh))[-1]
              self.assertTrue(self.touch(session, fresh, plus[1]))
              if key in (HOURS, VOLTS):
                pushed.call_args.args[0].callback(DialogResult.CONFIRM)
              self.assertEqual(session.system_snapshot().rows[0].value, steps[key][1])
          editor = replace(session.system_snapshot(), sidebar_expanded=expanded)
          before = editor.rows[0].source
          self.assertTrue(self.touch(session, editor, value_done_rect(editor)))
          self.assertIsNone(session.display_edit_key)
          self.assertEqual(session.selected, Destination.SYSTEM)
          self.assertEqual(session.display_scroll, overview.scroll)
          self.assertEqual(next(row for row in session.system_snapshot().rows if row.key == key).source, before)
          session._unavailable.assert_not_called()

  def test_system_editor_stale_dependencies_repairs_and_compact_snapshot(self):
    from openpilot.starpilot.ui.feature_settings_state import FeatureUiAction
    self.params.put_bool(MASTER, True, block=True)
    session = self.large_session()
    session.display_edit_key = "ScreenTimeout"
    editor = session.system_snapshot()
    button_index = next(i for i, button in enumerate(value_buttons(editor, editor.rows[0])) if button[0] == "60")
    self.params.put_bool(MASTER, False, block=True)
    session._display_ui(FeatureUiAction("action", editor.rows[0], button_index))
    self.assertIsNone(read_choice(self.params, "ScreenTimeout", large=True).raw)
    disabled = session.system_snapshot()
    self.assertTrue(all(not enabled for _, _, enabled in value_buttons(disabled, disabled.rows[0])))
    self.assertTrue(self.touch(session, disabled, value_done_rect(disabled)))
    Path(self.params.get_param_path("ScreenTimeout")).write_bytes(b"bad")
    session.display_edit_key = "ScreenTimeout"
    editor = session.system_snapshot()
    self.assertEqual(value_buttons(editor, editor.rows[0]), ())
    repair = (FEATURE_CONTROL_LEFT, feature_row_top(editor) + 36, FEATURE_CONTROL_RIGHT - FEATURE_CONTROL_LEFT, 82)
    with patch("openpilot.starpilot.ui.runtime_app.native_device.invalidate_display_preferences"):
      self.assertTrue(self.touch(session, editor, repair))
    self.assertEqual(read_choice(self.params, "ScreenTimeout", large=True).value, 30)
    session.profile = Profile.COMPACT
    self.assertEqual(session.display_snapshot(), self.owner.snapshot(Profile.COMPACT))
    self.assertTrue(all(not row.presets for row in session.system_snapshot().rows))

  def test_system_editor_bounds_and_changed_editor_cancel_touch(self):
    from openpilot.starpilot.ui import runtime_app
    self.params.put_bool(MASTER, True, block=True)
    self.params.put("ScreenBrightness", 100, block=True)
    session = self.large_session()
    session.display_edit_key = "ScreenBrightness"
    editor = session.system_snapshot()
    self.assertFalse(value_buttons(editor, editor.rows[0])[-1][2])
    self.params.put("ScreenBrightness", 5, block=True)
    editor = session.system_snapshot()
    self.assertFalse(value_buttons(editor, editor.rows[0])[0][2])
    session.display_edit_key = HOURS
    editor = session.system_snapshot()
    self.assertFalse(value_buttons(editor, editor.rows[0])[-1][2])
    session.display_edit_key = VOLTS
    editor = session.system_snapshot()
    self.assertFalse(value_buttons(editor, editor.rows[0])[0][2])
    session.display_edit_key = "ScreenBrightness"
    editor = session.system_snapshot()
    snapshot = ShellSnapshot(ShellMode.SETTINGS, Mock(), SettingsState(), Mock(), selected=Destination.SYSTEM, display=editor)
    session.snapshot = Mock(return_value=snapshot)
    with patch.object(runtime_app, "ui_state", NS(started=False, started_frame=0)):
      session.press(ShellMode.SETTINGS, 1200, 950)
      session.display_edit_key = "ScreenTimeout"
      self.assertFalse(session.release(ShellMode.SETTINGS, 1200, 950))
    self.assertEqual(session.display_edit_key, "ScreenTimeout")

  def test_absent_defaults_and_registered_keys_do_not_write(self):
    self.assertEqual(self.row(MASTER).value, "Off")
    self.assertEqual(self.row("ScreenBrightness").value, "Auto")
    self.assertEqual((self.row("ScreenBrightness").step, self.row("ScreenBrightness").maximum,
                      self.row("ScreenBrightness").unit), (1.0, 100.0, "%"))
    self.assertEqual((self.row("ScreenTimeout").value, self.row("ScreenTimeout").unit), ("30", "s"))
    self.assertEqual(self.row("ScreenTimeoutOnroad").value, "10")
    self.assertEqual(self.row("ScreenTimeoutOnroad", Profile.COMPACT).value, "5")
    self.assertEqual(list(Path(self.params.get_param_path(MASTER)).parent.iterdir()), [])
    for key in (MASTER, "ScreenBrightness", "ScreenBrightnessOnroad", "ScreenTimeout", "ScreenTimeoutOnroad", SCREEN_OFF):
      self.assertIsNone(read_choice(self.params, key, large=True).raw)

  def test_screen_off_is_independent_default_off_persistent_and_source_guarded(self):
    for profile in Profile:
      row = self.row(SCREEN_OFF, profile)
      self.assertEqual((row.value, row.default_value, row.choices, row.step, row.unit), ("Off", "Off", ("Off", "On"), 0, ""))
      self.assertTrue(row.available)
      self.assertIn("CPU/GPU", row.reason)
      self.assertIn("Tap once to wake", row.reason)
    request = self.request(SCREEN_OFF)
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(self.owner.apply(request))  # The displayed source is stale.
    saved = read_preferences(self.params, large=True)
    self.assertFalse(saved.enabled)
    self.assertTrue(saved.turn_off_screen)
    self.assertTrue(Params(self.params_root).get_bool(SCREEN_OFF))
    self.assertTrue(self.owner.apply(self.request(MASTER)))
    self.assertTrue(self.owner.apply(self.request(MASTER)))
    self.assertTrue(read_preferences(self.params, large=True).turn_off_screen)
    request = self.request(SCREEN_OFF)
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(read_preferences(self.params, large=True).turn_off_screen)

  def test_burn_in_protection_rows_are_on_the_comma_4_only(self):
    from unittest.mock import patch as replace_attribute
    from openpilot.starpilot.ui.display_preferences import STATIC_DIM, STATIC_DIM_STRENGTH
    both = {STATIC_DIM, STATIC_DIM_STRENGTH}
    self.assertEqual(tuple(row.key for row in self.owner.snapshot(Profile.COMPACT).rows)[-2:], (STATIC_DIM, STATIC_DIM_STRENGTH))
    self.assertFalse(both & {row.key for row in self.owner.snapshot(Profile.LARGE).rows})   # never on the large layout
    for device, shown in (("mici", True), ("tici", False), ("tizi", False), ("pc", False)):
      with replace_attribute("openpilot.common.hardware.HARDWARE.get_device_type", return_value=device):
        owner = DisplayOwner(self.params, lambda: True)
      self.assertEqual(both <= {row.key for row in owner.snapshot(Profile.COMPACT).rows}, shown, device)
      self.assertFalse(both & {row.key for row in owner.snapshot(Profile.LARGE).rows}, device)
      self.assertEqual(owner.apply(row_change(self.row(STATIC_DIM, Profile.COMPACT))), shown, device)   # cannot be switched on elsewhere
      if shown:
        self.assertTrue(owner.apply(row_change(self.row(STATIC_DIM, Profile.COMPACT))))                  # back off

  def test_dim_static_pixels_is_independent_default_off_and_its_strength_follows_the_switch(self):
    from openpilot.starpilot.ui.display_preferences import STATIC_DIM, STATIC_DIM_STRENGTH, STATIC_DIM_STRENGTH_DESCRIPTION
    from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
    switch, strength = self.row(STATIC_DIM, Profile.COMPACT), self.row(STATIC_DIM_STRENGTH, Profile.COMPACT)
    self.assertEqual((switch.label, switch.value, switch.default_value, switch.choices), ("Burn In Protection", "Off", "Off", ("Off", "On")))
    self.assertTrue(switch.available)
    self.assertEqual(switch.reason, "Dims any static areas of the display to reduce burn in on the comma screen.")
    self.assertEqual((strength.label, strength.value, strength.default_value, strength.unit, strength.step, strength.minimum, strength.maximum),
                     ("Static Pixel Dimming", "40", "40", "%", 5.0, 5.0, 50.0))
    self.assertFalse(strength.available)                       # grayed while the switch is off
    self.assertEqual(strength.reason, "Saved; Burn In Protection is off")
    saved = read_preferences(self.params, large=False)
    self.assertEqual((saved.static_dim, saved.static_dim_strength, saved.enabled), (False, 40, False))

    request = self.request(STATIC_DIM, Profile.COMPACT)
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(self.owner.apply(request))                # the displayed source is stale
    self.assertTrue(read_preferences(self.params, large=False).static_dim)
    self.assertFalse(read_preferences(self.params, large=False).enabled)   # works without Custom Display Settings
    strength = self.row(STATIC_DIM_STRENGTH, Profile.COMPACT)
    self.assertTrue(strength.available)
    self.assertEqual(strength.reason, STATIC_DIM_STRENGTH_DESCRIPTION)
    self.assertIn("Higher means more dimming", strength.reason)
    self.assertEqual(row_change(strength, 1).value, "45.0")    # the slider moves in steps of 5
    for value, accepted in (("50%", True), ("5", True), ("45", True), ("60", False), ("55%", False), ("0", False), ("42", False),
                            ("lots", False), ("35", True)):
      row = self.row(STATIC_DIM_STRENGTH, Profile.COMPACT)
      request = FeatureSettingsRequest(row.key, row.source, value, dependencies=row.dependencies)
      self.assertEqual(self.owner.apply(request), accepted, value)
    self.assertEqual(read_preferences(self.params, large=False).static_dim_strength, 35)
    self.assertEqual(Params(self.params_root).get(STATIC_DIM_STRENGTH), 35)

    row = self.row(STATIC_DIM_STRENGTH, Profile.COMPACT)
    self.parked = False
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(row.key, row.source, "30", dependencies=row.dependencies)))
    self.parked = True
    self.assertTrue(self.owner.apply(self.request(STATIC_DIM, Profile.COMPACT)))   # switch off
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(row.key, row.source, "30", dependencies=row.dependencies)))
    saved = read_preferences(self.params, large=False)
    self.assertEqual((saved.static_dim, saved.static_dim_strength), (False, 35))

  def test_invalid_dim_static_pixels_choices_fail_off_and_can_be_repaired(self):
    from openpilot.starpilot.ui.display_preferences import STATIC_DIM, STATIC_DIM_STRENGTH
    Path(self.params.get_param_path(STATIC_DIM)).write_bytes(b"1")
    Path(self.params.get_param_path(STATIC_DIM_STRENGTH)).write_bytes(b"60")   # outside 5-50
    saved = read_preferences(self.params, large=False)
    self.assertEqual((saved.static_dim, saved.static_dim_strength), (True, 40))
    row = self.row(STATIC_DIM_STRENGTH, Profile.COMPACT)
    self.assertEqual((row.value, row.repair_value), ("Invalid saved choice", "40%"))
    self.assertTrue(self.owner.apply(self.request(STATIC_DIM_STRENGTH, Profile.COMPACT)))
    self.assertEqual(self.row(STATIC_DIM_STRENGTH, Profile.COMPACT).value, "40")

  def test_invalid_screen_off_fails_off_and_can_be_repaired(self):
    Path(self.params.get_param_path(SCREEN_OFF)).write_bytes(b"2")
    self.assertFalse(read_preferences(self.params, large=True).turn_off_screen)
    self.assertEqual(self.row(SCREEN_OFF).repair_value, "Off")
    self.assertTrue(self.owner.apply(self.request(SCREEN_OFF)))
    self.assertEqual(self.row(SCREEN_OFF).value, "Off")

  def test_screen_off_toggle_in_display_submenu_refreshes_device_preferences(self):
    session = self.large_session()
    session._display_ui(FeatureUiAction("open", session.system_snapshot().rows[2]))
    state = replace(session.system_snapshot(), scroll=5)
    inputs = FeatureInput(session._display_ui)
    with patch("openpilot.starpilot.ui.runtime_app.native_device.invalidate_display_preferences") as refresh:
      inputs.press(2000, feature_row_top(state) + FEATURE_ROW_HEIGHT / 2, state)
      self.assertFalse(read_preferences(self.params, large=True).turn_off_screen)
      inputs.release(2000, feature_row_top(state) + FEATURE_ROW_HEIGHT / 2, state)
      self.assertTrue(read_preferences(self.params, large=True).turn_off_screen)
      refresh.assert_called_once()

  def test_master_enable_requires_valid_values_but_off_remains_available(self):
    path = Path(self.params.get_param_path("ScreenBrightnessOnroad"))
    path.write_bytes(b"0")
    self.assertEqual(self.row("ScreenBrightnessOnroad").value, "Saved Off (unsupported)")
    self.assertEqual(self.row("ScreenBrightnessOnroad").repair_value, "Auto")
    self.assertFalse(self.owner.apply(self.request(MASTER)))
    self.assertEqual(path.read_bytes(), b"0")
    self.assertTrue(self.owner.apply(self.request("ScreenBrightnessOnroad")))
    self.assertTrue(self.owner.apply(self.request(MASTER)))
    self.assertTrue(read_preferences(self.params, large=True).enabled)
    path.write_bytes(b"broken")
    self.assertTrue(self.owner.apply(self.request(MASTER)))
    self.assertFalse(read_preferences(self.params, large=True).enabled)
    self.assertEqual(path.read_bytes(), b"broken")

  def test_all_display_children_disable_without_losing_saved_values(self):
    session = self.large_session()
    values = {"ScreenBrightness": 50, "ScreenBrightnessOnroad": 75, "ScreenTimeout": 30, "ScreenTimeoutOnroad": 10}
    self.params.put_bool(MASTER, True, block=True)
    for key, value in values.items():
      self.params.put(key, value, block=True)
    requests = {key: self.request(key) for key in values}
    self.assertTrue(self.owner.apply(self.request(MASTER)))
    session._display_ui(FeatureUiAction("open", session.system_snapshot().rows[2]))
    disabled = session.system_snapshot()
    self.assertTrue(next(row for row in disabled.rows if row.key == MASTER).available)
    for index, row in enumerate(disabled.rows):
      if row.key in values:
        self.assertFalse(row.available)
        self.assertIn("custom display is off", row.reason)
        page = replace(disabled, scroll=index // FEATURE_VISIBLE_ROWS * FEATURE_VISIBLE_ROWS)
        target = FeatureInput.target(800, feature_row_top(page) + (index % FEATURE_VISIBLE_ROWS + .5) * FEATURE_ROW_HEIGHT, page)
        self.assertEqual(target.kind, "details")
        self.assertFalse(self.owner.apply(requests[row.key]))
        self.assertEqual(read_choice(self.params, row.key, large=True).value, values[row.key])
    self.assertTrue(self.owner.apply(self.request(MASTER)))
    self.assertTrue(all(row.available for row in session.system_snapshot().rows if row.key in values))

  def test_source_dependency_and_parked_guards(self):
    self.assertFalse(self.row("ScreenTimeout").available)
    self.params.put_bool(MASTER, True, block=True)
    request = row_change(self.row("ScreenTimeout"))
    assert request is not None
    self.params.put_bool(MASTER, False, block=True)
    self.assertFalse(self.owner.apply(request))
    self.params.put_bool(MASTER, True, block=True)
    request = row_change(self.row("ScreenTimeout"))
    assert request is not None
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    checks = 0
    def parked_once():
      nonlocal checks
      checks += 1
      return checks == 1
    self.assertFalse(DisplayOwner(self.params, parked_once).apply(request))
    self.assertIsNone(read_choice(self.params, "ScreenTimeout", large=True).raw)
    self.params.put_bool(MASTER, False, block=True)
    self.assertFalse(self.owner.apply(request))

  def test_numeric_metadata_and_master_off_cannot_be_bypassed(self):
    row = self.row("ScreenTimeout")
    self.assertEqual((row.value, row.step, row.minimum, row.maximum, row.unit, row.choices), ("30", 5.0, 5.0, 60.0, "s", ()))
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(row.key, row.source, "35", dependencies=row.dependencies)))
    self.params.put_bool(MASTER, True, block=True)
    self.params.put("ScreenBrightness", 50, block=True)
    row = self.row("ScreenBrightness")
    self.assertEqual((row.value, row.step, row.minimum, row.maximum, row.unit, row.choices), ("50", 1.0, 5.0, 100.0, "%", ("Auto",)))
    self.assertTrue(self.owner.apply(self.request("ScreenBrightness")))
    self.assertEqual(read_choice(self.params, "ScreenBrightness", large=True).value, 51)
    self.assertTrue(self.owner.apply(self.request("display:auto:ScreenBrightness")))
    self.assertEqual(self.row("ScreenBrightness").value, "Auto")

  def test_final_source_and_master_freshness_prevent_write(self):
    self.params.put_bool(MASTER, True, block=True)
    request = self.request("ScreenTimeout")
    original_path = self.params.get_param_path
    key_reads = 0
    def unreadable_on_final(key):
      nonlocal key_reads
      if key == "ScreenTimeout":
        key_reads += 1
        if key_reads == 2:
          raise OSError("file became unreadable")
      return original_path(key)
    with patch.object(self.params, "get_param_path", side_effect=unreadable_on_final):
      self.assertFalse(self.owner.apply(request))
    self.assertIsNone(read_choice(self.params, "ScreenTimeout", large=True).raw)
    master_reads = 0
    def changed_master_on_final(key):
      nonlocal master_reads
      if key == MASTER:
        master_reads += 1
        if master_reads == 2:
          Path(original_path(MASTER)).write_bytes(b"0")
      return original_path(key)
    with patch.object(self.params, "get_param_path", side_effect=changed_master_on_final):
      self.assertFalse(self.owner.apply(request))
    self.assertIsNone(read_choice(self.params, "ScreenTimeout", large=True).raw)

  def test_corrupt_oversized_and_nonregular_are_preserved(self):
    path = Path(self.params.get_param_path("ScreenBrightness"))
    path.write_bytes(b"\xff")
    self.assertEqual(self.row("ScreenBrightness").value, "Invalid saved choice")
    self.assertTrue(self.owner.apply(self.request("ScreenBrightness")))
    self.assertEqual(path.read_bytes(), b"101")
    oversized = b"1" * 100_000
    path.write_bytes(oversized)
    row = self.row("ScreenBrightness")
    self.assertFalse(row.available)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(row.key, row.source, "Auto", dependencies=row.dependencies)))
    self.assertEqual(path.read_bytes(), oversized)
    path.unlink()
    target = path.parent / "target"
    target.write_bytes(b"101")
    path.symlink_to(target)
    self.assertFalse(self.row("ScreenBrightness").available)
    path.unlink()
    os.mkfifo(path)
    self.assertFalse(self.row("ScreenBrightness").available)

  def test_native_press_and_compact_refresh(self):
    changed = []
    input_owner = FeatureInput(changed.append)
    state = self.owner.snapshot(Profile.LARGE)
    visible_row = next(index for index, row in enumerate(state.rows) if row.key == MASTER) - state.scroll
    self.assertTrue(0 <= visible_row < FEATURE_VISIBLE_ROWS)
    x = (FEATURE_CONTROL_LEFT + FEATURE_CONTROL_RIGHT) / 2
    y = feature_row_top(state) + (visible_row + .5) * FEATURE_ROW_HEIGHT
    input_owner.press(x, y, state)
    input_owner.release(x, y, state)
    self.assertEqual(changed[0].row.key, MASTER)
    input_owner.press(x, y, state)
    input_owner.cancel()
    input_owner.release(x, y, state)
    self.assertEqual(len(changed), 1)
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
    class Session:
      def display_snapshot(inner):
        return self.owner.snapshot(Profile.COMPACT)
      def display_request(inner, request):
        return self.owner.apply(request)
    shown = []
    with patch.object(display_compact, "BigButton", Button), patch.object(display_compact, "GreyBigButton", Button), \
         patch.object(display_compact, "NavScroller", Scroller), patch.object(display_compact.gui_app, "push_widget", shown.append):
      display_compact.DisplayCompact(Session()).entry_button().click()
      page = shown[0]
      self.assertEqual(page.items[0].label, "display")
      screen_off = next(item for item in page.items if item.label == "turn off comma screen")
      self.assertIn("cpu/gpu", screen_off.value)
      screen_off.click()
      self.assertTrue(read_preferences(self.params, large=False).turn_off_screen)
      self.assertFalse(read_preferences(self.params, large=False).enabled)
      page.items[1].click()
      self.assertEqual(page.items[1].value, "on")
      self.assertTrue(read_preferences(self.params, large=False).enabled)
      for key, label, maximum, step in (("ScreenTimeout", "parked screen timeout", 60, 5),
                                        ("ScreenTimeoutOnroad", "driving interaction timeout", 60, 5),
                                        ("ScreenBrightness", "parked brightness", 100, 1),
                                        ("ScreenBrightnessOnroad", "driving brightness", 100, 1)):
        with self.subTest(key=key):
          self.params.put(key, maximum, block=True)
          display_compact.DisplayCompact(Session())._populate(page)
          minus = next(item for item in page.items if item.label == label + " -")
          plus = next(item for item in page.items if item.label == label + " +")
          self.assertTrue(minus.enabled)
          self.assertFalse(plus.enabled)
          minus.click()
          self.assertEqual(self.params.get(key), maximum - step)
          next(item for item in page.items if item.label == label + " +").click()
          self.assertEqual(self.params.get(key), maximum)
          self.params.put(key, 5, block=True)
          display_compact.DisplayCompact(Session())._populate(page)
          self.assertFalse(next(item for item in page.items if item.label == label + " -").enabled)
      next(item for item in page.items if item.label == "use auto driving brightness set auto").click()
      self.assertEqual([item.label for item in page.items if item.label.startswith("driving brightness")], ["driving brightness"])
      next(item for item in page.items if item.label == "driving brightness").click()
      self.assertEqual(self.params.get("ScreenBrightnessOnroad"), 5)
      Path(self.params.get_param_path("ScreenTimeout")).write_bytes(b"invalid")
      display_compact.DisplayCompact(Session())._populate(page)
      repair = [item for item in page.items if item.label.startswith("parked screen timeout")]
      self.assertEqual([item.label for item in repair], ["parked screen timeout set 30 s"])
      repair[0].click()
      self.assertEqual(self.params.get("ScreenTimeout"), 30)

  def test_large_system_tile_routes_to_display_and_back(self):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
    from openpilot.starpilot.ui.feature_settings_state import FeatureUiAction
    ui = ui_fake()
    ui.params = self.params
    ui.started = False
    ui.sm.messages["deviceState"].started = False
    ui.sm.messages["pandaStates"][0].ignitionLine = False
    adapter = RuntimeSnapshotAdapter(ui)
    snapshot = adapter.build(ShellMode.SETTINGS, now_ns=NOW)
    self.assertTrue(snapshot.settings.destination(Destination.SYSTEM).available)
    emitted = []
    settings_input = SettingsInput(Profile.LARGE, emitted.append)
    x, y, width, height = tile_rects(snapshot.settings)[3]
    settings_input.press(x + width / 2, y + height / 2, snapshot.settings)
    settings_input.release(x + width / 2, y + height / 2, snapshot.settings)
    self.assertEqual(emitted[0].destination.destination, Destination.SYSTEM)
    session = StarShellSession.__new__(StarShellSession)
    self.enterContext(patch.object(session, 'drive_state', NS(snapshot=lambda: {"mode": "auto", "revision": None, "available": False,
                                                    "effective": None, "overrideAllowed": False}), create=True))
    session._mode = ShellMode.SETTINGS
    session.selected = Destination.SYSTEM
    session.display_owner = self.owner
    from openpilot.starpilot.ui.power_owner import PowerOwner
    session.power_owner = PowerOwner(self.params, lambda: self.parked)
    session.display_scroll = 0
    session._snapshot_cache = None
    session.input = ShellInput(Profile.LARGE, lambda _: None)
    session.profile = Profile.LARGE
    session._display_ui(FeatureUiAction("back"))
    self.assertEqual(session.selected, Destination.STAR)
