"""Route live large-panel requests into native owners with effects isolated."""
from unittest.mock import patch, call


import atexit
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from openpilot.common.params import Params


_param_dir = tempfile.TemporaryDirectory(prefix="starpilot-panel-owner-test-")
atexit.register(_param_dir.cleanup)
_original_params_init = Params.__init__
with patch.object(Params, "__init__", lambda self, d="": _original_params_init(self, _param_dir.name)):
  from openpilot.starpilot.ui import runtime_app
  from openpilot.selfdrive.ui.layouts.settings import device as native_device
  from openpilot.selfdrive.ui.layouts.settings import software as native_software
  from openpilot.selfdrive.ui.layouts.settings.settings import PanelType

from openpilot.starpilot.ui.device_state import DeviceAction, DeviceRequest
from openpilot.starpilot.ui.feature_settings import FeatureSettingsView
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.feature_settings_state import FEATURE_ROW_HEIGHT, FEATURE_ROW_TOP, FeaturePage, FeatureUiAction
from openpilot.starpilot.ui.toggles_state import ToggleRequest, ToggleKey
from openpilot.starpilot.ui.settings_state import Destination, SettingsAction, SettingsActionKind, tile_rects
from openpilot.starpilot.ui.shell import ShellInput, ShellMode, ShellRequest, ShellView
from openpilot.starpilot.ui.software_state import SoftwareAction, SoftwareRequest
from openpilot.starpilot.ui.tests.test_runtime_snapshot import BOOT_OFFSET_NS, NOW, ui_fake
from openpilot.system.ui.lib.application import gui_app, MouseEvent, MousePos
from openpilot.starpilot.galaxy.access import AccessStatus, GalaxyAccessOwner
from openpilot.starpilot.ui.galaxy_access import GalaxyAccessFlow, connection_url
from pathlib import Path


def make_feature_view(profile: Profile):
  fonts = BitmapFonts.__new__(BitmapFonts)
  fonts.profile = profile
  return FeatureSettingsView(fonts)


class TestRuntimePanelActions(unittest.TestCase):
  def test_live_slc_action_source_accepts_system_and_stock_long_but_rejects_stale(self):
    ui = ui_fake()
    ui.CP = NS(carFingerprint="TOYOTA_HIGHLANDER_TSS2", openpilotLongitudinalControl=True, pcmCruise=True)
    ui.sm.messages["carState"].canValid = True
    ui.sm.messages["carState"].canTimeout = False
    ui.sm.messages["carControl"].longActive = True
    ui.sm.messages["selfdriveState"].enabled = True
    ui.sm.put("controlsState", NS(longControlState="pid"))
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.adapter = runtime_app.RuntimeSnapshotAdapter(ui)
    with patch("openpilot.starpilot.ui.runtime_snapshot._clock_pair", return_value=(NOW, NOW + BOOT_OFFSET_NS, BOOT_OFFSET_NS)):
      self.assertIs(session._live_slc_message(NOW), ui.sm["slcState"])
      ui.CP.openpilotLongitudinalControl = False
      ui.sm.messages["carControl"].longActive = False
      ui.sm.messages["selfdriveState"].enabled = False
      ui.sm.put("controlsState", NS(longControlState="off"))
      self.assertIs(session._live_slc_message(NOW), ui.sm["slcState"])
      ui.sm.logMonoTime["slcState"] = NOW - 1_000_000_000
      self.assertIsNone(session._live_slc_message(NOW))

  def _vehicle_session(self, sidebar_expanded=True):
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import CAR
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.ui.params = Params(directory.name)
    self.ui.params.put_bool("LongPitch", False, block=True)
    fingerprint = gen_empty_fingerprint()
    fingerprint[1][0x460] = 8
    self.ui.CP = CarInterface.get_params(CAR.CHEVROLET_SUBURBAN, fingerprint, [], False, False, False)
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.profile = runtime_app.Profile.LARGE
    session.adapter = self._adapter()
    session._mode, session.selected = ShellMode.SETTINGS, Destination.STAR
    session.compact_y = session.compact_scroll_x = 0
    session.sidebar_expanded = sidebar_expanded
    session.feature_page = session.feature_root_page = FeaturePage.HUB
    session.feature_scroll = 0
    session._snapshot_cache = None
    session._on_destination_change = Mock()
    session._on_compact_destination = Mock()
    session.input = ShellInput(session.profile, session._emit)
    session.view = ShellView.__new__(ShellView)
    session.view.features = make_feature_view(runtime_app.Profile.LARGE)
    session.feature_owner = runtime_app.FeatureSettingsOwner(self.ui.params, session._feature_authority,
                                                           vehicle_fingerprint=lambda: getattr(self.ui.CP, "carFingerprint", None),
                                                           vehicle_params=lambda: self.ui.CP)
    return session

  def _settings_tap(self, session, x, y):
    session.press(ShellMode.SETTINGS, x, y)
    self.assertTrue(session.release(ShellMode.SETTINGS, x, y))

  def _open_vehicle_picker(self, session, pushed):
    self._settings_tap(session, 1800, 800)
    self._settings_tap(session, 2000, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT * 1.5)
    return pushed.call_args.args[0]

  @staticmethod
  def _option_dialog(title, options, current="", callback=None):
    return NS(title=title, options=options, selection=current, callback=callback)

  def test_large_manual_vehicle_selection_without_detected_vehicle_and_auto_restore(self):
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.vehicle_selection import choices, read_selection, startup_candidate
    from openpilot.system.ui.widgets import DialogResult
    session = self._vehicle_session()
    self.ui.CP = None
    choice = next(choice for choice in choices() if choice.platform == CAR.KIA_XCEED_PHEV)
    with patch("openpilot.system.ui.widgets.option_dialog.MultiOptionDialog", side_effect=self._option_dialog), \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      make = self._open_vehicle_picker(session, pushed)
      self.assertIn("Auto detection", make.options)
      shown = session.snapshot(ShellMode.SETTINGS).features
      self.assertEqual([(row.label, row.value) for row in shown.rows],
                       [("Reported vehicle", "Not detected"), ("Vehicle selection", "Auto detection")])
      make.selection = choice.make
      make.callback(DialogResult.CONFIRM)
      model = pushed.call_args.args[0]
      self.assertIn(choice.label, model.options)
      model.selection = choice.label
      model.callback(DialogResult.CONFIRM)
      self.assertEqual(read_selection(self.ui.params).platform, choice.platform)
      self.assertEqual(startup_candidate(self.ui.params), choice.platform)
      self.assertIsNone(self.ui.CP)
      self.assertEqual(session.snapshot(ShellMode.SETTINGS).features.rows[1].value, choice.label)
      self._settings_tap(session, 2000, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT * 1.5)
      auto = pushed.call_args.args[0]
      auto.selection = "Auto detection"
      auto.callback(DialogResult.CONFIRM)
      self.assertIsNone(startup_candidate(self.ui.params))
      self.assertEqual(session.snapshot(ShellMode.SETTINGS).features.rows[1].value, "Auto detection")

  def test_large_vehicle_selection_offroad_with_available_telemetry(self):
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.vehicle_selection import choices, startup_candidate
    from openpilot.system.ui.widgets import DialogResult
    choice = next(choice for choice in choices() if choice.platform == CAR.KIA_XCEED_PHEV)
    for evidence in ("missing", "stale", "ignition"):
      with self.subTest(evidence=evidence):
        self.ui.sm = ui_fake().sm
        self.ui.sm["deviceState"].started = False
        self.ui.sm["pandaStates"][0].ignitionLine = False
        session = self._vehicle_session()
        self.ui.CP = None
        if evidence == "missing":
          self.ui.sm.messages["pandaStates"] = []
          self.ui.sm.valid["deviceState"] = self.ui.sm.valid["pandaStates"] = False
        elif evidence == "stale":
          self.ui.sm.logMonoTime["deviceState"] -= 2_000_000_000
          self.ui.sm.logMonoTime["pandaStates"] -= 2_000_000_000
        else:
          self.ui.sm["pandaStates"][0].ignitionLine = True
        self.assertTrue(self.ui.is_offroad())
        self.assertEqual(session.confirmed_offroad(), evidence == "ignition")
        with patch("openpilot.system.ui.widgets.option_dialog.MultiOptionDialog", side_effect=self._option_dialog), \
             patch.object(runtime_app.gui_app, "push_widget") as pushed, patch.object(session, "_unavailable") as alert:
          make = self._open_vehicle_picker(session, pushed)
          make.selection = choice.make
          make.callback(DialogResult.CONFIRM)
          model = pushed.call_args.args[0]
          model.selection = choice.label
          model.callback(DialogResult.CONFIRM)
          alert.assert_not_called()
          self.assertEqual(startup_candidate(self.ui.params), choice.platform)
          self.assertIsNone(self.ui.CP)
          self._settings_tap(session, 2000, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT * 1.5)
          auto = pushed.call_args.args[0]
          auto.selection = "Auto detection"
          auto.callback(DialogResult.CONFIRM)
          alert.assert_not_called()
          self.assertIsNone(startup_candidate(self.ui.params))

  def test_large_picker_lists_and_saves_every_gm_platform(self):
    from opendbc.car.gm.values import CAR
    from openpilot.starpilot.vehicle_selection import choices, startup_candidate
    from openpilot.system.ui.widgets import DialogResult

    manufacturers = {'CHEVROLET': 'Chevrolet', 'CADILLAC': 'Cadillac', 'BUICK': 'Buick', 'GMC': 'GMC', 'HOLDEN': 'Holden'}
    catalog = {choice.platform: choice for choice in choices()}
    for platform in CAR:
      with self.subTest(platform=platform):
        session = self._vehicle_session()
        self.ui.CP = None
        manufacturer = manufacturers[str(platform).split('_', 1)[0]]
        with patch("openpilot.system.ui.widgets.option_dialog.MultiOptionDialog", side_effect=self._option_dialog), \
             patch.object(runtime_app.gui_app, "push_widget") as pushed:
          make = self._open_vehicle_picker(session, pushed)
          self.assertIn(manufacturer, make.options)
          self.assertNotIn("Gm", make.options)
          make.selection = manufacturer
          make.callback(DialogResult.CONFIRM)
          model = pushed.call_args.args[0]
          self.assertEqual(model.title, f"Select {manufacturer} model")
          self.assertEqual(set(model.options), {choice.label for choice in catalog.values() if choice.make == manufacturer})
          self.assertEqual(model.options.count(catalog[platform].label), 1)
          model.selection = catalog[platform].label
          model.callback(DialogResult.CONFIRM)
          self.assertEqual(startup_candidate(self.ui.params), platform)
          self.ui.CP = NS(carFingerprint=str(platform), notCar=False)
          rows = session._vehicle_selector().rows()
          self.assertEqual(rows[0].value, catalog[platform].label)
          self.assertEqual(rows[1].value, catalog[platform].label)

  def test_large_vehicle_picker_uses_real_dialog_stack_and_returns_to_same_panel(self):
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.vehicle_selection import choices, startup_candidate
    from openpilot.system.ui.widgets import DialogResult
    from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
    session = self._vehicle_session()
    self.ui.CP = None
    page = runtime_app.StarShellPage(session, ShellMode.SETTINGS)
    choice = next(choice for choice in choices() if choice.platform == CAR.KIA_XCEED_PHEV)
    with patch.object(runtime_app.gui_app, "_nav_stack", [page]), \
         patch("openpilot.system.ui.widgets.button.Label"), patch.object(page, "hide_event") as hidden:
      self._settings_tap(session, 1800, 800)
      self._settings_tap(session, 2000, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT * 1.5)
      make = runtime_app.gui_app.get_active_widget()
      self.assertIsInstance(make, MultiOptionDialog)
      self.assertFalse(page.enabled)
      make._on_option_clicked(choice.make)
      make._set_result(DialogResult.CONFIRM)
      model = runtime_app.gui_app.get_active_widget()
      self.assertIsInstance(model, MultiOptionDialog)
      self.assertIsNot(model, make)
      model._on_option_clicked(choice.label)
      model._set_result(DialogResult.CONFIRM)
      self.assertEqual(startup_candidate(self.ui.params), choice.platform)
      self.assertIs(runtime_app.gui_app.get_active_widget(), page)
      self.assertTrue(page.enabled)
      self.assertEqual(session.snapshot(ShellMode.SETTINGS).features.page, FeaturePage.VEHICLE)
      hidden.assert_not_called()

  def test_large_vehicle_picker_rejects_cancel_stale_source_onroad_and_old_page(self):
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.vehicle_selection import VehicleSelectionOwner, choices, read_selection
    from openpilot.system.ui.widgets import DialogResult
    choice = next(choice for choice in choices() if choice.platform == CAR.KIA_XCEED_PHEV)
    for failure in ("cancel", "source", "onroad", "page"):
      with self.subTest(failure=failure):
        session = self._vehicle_session()
        self.ui.CP = None
        self.ui.started = False
        self.ui.sm["deviceState"].started = False
        self.ui.is_offroad = lambda: not self.ui.started
        with patch("openpilot.system.ui.widgets.option_dialog.MultiOptionDialog", side_effect=self._option_dialog), \
             patch.object(runtime_app.gui_app, "push_widget") as pushed:
          make = self._open_vehicle_picker(session, pushed)
          make.selection = choice.make
          make.callback(DialogResult.CONFIRM)
          model = pushed.call_args.args[0]
          model.selection = choice.label
          if failure == "source":
            owner = VehicleSelectionOwner(self.ui.params, lambda: True)
            self.assertTrue(owner.choose(None, str(CAR.KIA_CEED)).verified)
          elif failure == "onroad":
            self.ui.started = True
          elif failure == "page":
            self._settings_tap(session, 250, 160)
            self._settings_tap(session, 1800, 800)
          original = read_selection(self.ui.params)
          model.callback(DialogResult.CANCEL if failure == "cancel" else DialogResult.CONFIRM)
          self.assertEqual(read_selection(self.ui.params), original)

  def test_large_vehicle_picker_repairs_invalid_selection_and_keeps_duplicate_labels(self):
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.vehicle_selection import KEY, VehicleChoice, read_selection
    from openpilot.system.ui.widgets import DialogResult
    session = self._vehicle_session()
    self.ui.CP = None
    Path(self.ui.params.get_param_path(KEY)).write_bytes(b"invalid")
    catalog = (VehicleChoice(str(CAR.KIA_CEED), "Kia", "Same model"),
               VehicleChoice(str(CAR.KIA_XCEED_PHEV), "Kia", "Same model"))
    with patch("openpilot.starpilot.vehicle_selection.VehicleSelectionOwner.choices", return_value=catalog), \
         patch("openpilot.system.ui.widgets.option_dialog.MultiOptionDialog", side_effect=self._option_dialog), \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      make = self._open_vehicle_picker(session, pushed)
      self.assertEqual(session.snapshot(ShellMode.SETTINGS).features.rows[1].value, "Needs review")
      make.selection = "Auto detection"
      make.callback(DialogResult.CONFIRM)
      self.assertTrue(read_selection(self.ui.params).valid)
      self._settings_tap(session, 2000, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT * 1.5)
      make = pushed.call_args.args[0]
      make.selection = "Kia"
      make.callback(DialogResult.CONFIRM)
      model = pushed.call_args.args[0]
      self.assertEqual(len(model.options), 2)
      model.selection = next(option for option in model.options if str(CAR.KIA_XCEED_PHEV) in option)
      model.callback(DialogResult.CONFIRM)
      self.assertEqual(read_selection(self.ui.params).platform, CAR.KIA_XCEED_PHEV)

  def test_large_vehicle_tile_reuses_editor_and_preserves_both_back_paths(self):
    for sidebar_expanded in (True, False):
      with self.subTest(sidebar_expanded=sidebar_expanded):
        session = self._vehicle_session(sidebar_expanded)
        session.feature_scroll = 5
        root = session.snapshot(ShellMode.SETTINGS)
        self.assertTrue(root.settings.destination(Destination.VEHICLE).available)
        x, y, width, height = tile_rects(root.settings)[5]
        self._settings_tap(session, x + width / 2, y + height / 2)
        shown = session.snapshot(ShellMode.SETTINGS)
        self.assertEqual(shown.selected, Destination.DRIVING_CONTROLS)
        self.assertEqual((shown.features.page, shown.features.title, shown.features.parent_title, shown.features.scroll),
                         (FeaturePage.VEHICLE, "Vehicle Settings", "StarPilot", 0))
        self.assertIn("LongPitch", [row.key for row in shown.features.rows])
        session._on_compact_destination.assert_not_called()
        view = ShellView.__new__(ShellView)
        view.profile, view.onroad, view.settings, view.features = session.profile, Mock(), Mock(), Mock()
        view.render(shown)
        view.features.render.assert_called_once_with(shown.features, 0.0)
        view.settings.render.assert_not_called()
        view.settings.render_rail.assert_called_once_with(shown.settings, selected=Destination.STAR)
        self._settings_tap(session, *((250, 160) if sidebar_expanded else (70, 50)))
        self.assertEqual(session.selected, Destination.STAR)

        # Reopening through Driving Controls must reset the entry page.
        root = session.snapshot(ShellMode.SETTINGS)
        x, y, width, height = tile_rects(root.settings)[2]
        self._settings_tap(session, x + width / 2, y + height / 2)
        hub = session.snapshot(ShellMode.SETTINGS).features
        self.assertEqual(hub.page, FeaturePage.HUB)
        vehicle = next(row for row in hub.rows if row.page == FeaturePage.VEHICLE)
        session._feature_ui(FeatureUiAction("open", vehicle))
        shown = session.snapshot(ShellMode.SETTINGS)
        self.assertEqual(shown.features.parent_title, "Driving Controls")
        if sidebar_expanded:
          self._settings_tap(session, 20, 581)
        self._settings_tap(session, 70, 50)
        self.assertEqual(session.snapshot(ShellMode.SETTINGS).features.page, FeaturePage.HUB)
        self.assertEqual(session.selected, Destination.DRIVING_CONTROLS)
        self._settings_tap(session, 70, 50)
        self.assertEqual(session.selected, Destination.STAR)
        session._on_home = Mock()
        self._settings_tap(session, 70, 50)
        session._on_home.assert_called_once_with()

  def test_large_vehicle_control_uses_confirmation_and_cancels_after_leaving(self):
    from openpilot.system.ui.widgets import DialogResult
    session = self._vehicle_session()
    self._settings_tap(session, 1800, 800)
    with patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", side_effect=lambda *a, **kw: NS(callback=kw["callback"])), \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      shown = session.snapshot(ShellMode.SETTINGS).features
      index = next(index for index, row in enumerate(shown.rows) if row.key == "LongPitch")
      y = FEATURE_ROW_TOP + index * FEATURE_ROW_HEIGHT + FEATURE_ROW_HEIGHT / 2
      self._settings_tap(session, 2000, y)
      self.assertFalse(self.ui.params.get_bool("LongPitch"))
      pushed.call_args.args[0].callback(DialogResult.CONFIRM)
      self.assertTrue(self.ui.params.get_bool("LongPitch"))
      self._settings_tap(session, 2000, y)
      confirmation = pushed.call_args.args[0].callback
      self._settings_tap(session, 250, 160)
      self._settings_tap(session, 1800, 800)
      confirmation(DialogResult.CONFIRM)
      self.assertTrue(self.ui.params.get_bool("LongPitch"))

  def test_compact_vehicle_destination_keeps_its_native_route(self):
    session = self._vehicle_session()
    session.profile = runtime_app.Profile.COMPACT
    session.drive_state = NS(snapshot=lambda: {"mode": "auto", "revision": None, "available": False,
                                             "effective": None, "overrideAllowed": False})
    destination = session.snapshot(ShellMode.SETTINGS).settings.destination(Destination.VEHICLE)
    session._emit(ShellRequest("settings", SettingsAction(SettingsActionKind.REQUEST_DESTINATION, destination)))
    session._on_compact_destination.assert_called_once_with(Destination.VEHICLE)
    self.assertEqual(session.selected, Destination.STAR)
    self.assertEqual(session.feature_page, FeaturePage.HUB)

  def test_feature_details_preserve_literals_and_reject_stale_rows(self):
    from dataclasses import replace
    from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState, FeatureUiAction
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session._mode, session.selected = ShellMode.SETTINGS, Destination.DRIVING_CONTROLS
    session.input = Mock()
    row = FeatureRow("saved", "A < B & C", "75", unit="%", reason="Full explanation " * 30)
    session.feature_snapshot = Mock(return_value=FeatureSettingsState(rows=(row,)))
    with patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget") as push, \
         patch.object(runtime_app.gui_app, "font", return_value=runtime_app.rl.Font()):
      session._feature_details("features", FeatureUiAction("details", row))
      elements = push.call_args.args[0]._html_renderer.elements
      self.assertEqual([element.content for element in elements], [row.label, "75 %", row.reason.strip()])
      self.assertTrue(all(element.font_size == 62 for element in elements))
      session.input.cancel.assert_called_once()
      push.reset_mock()
      session._feature_details("features", FeatureUiAction("details", replace(row, value="80")))
      session.selected = Destination.STAR
      session._feature_details("features", FeatureUiAction("details", row))
      push.assert_not_called()

  def test_large_feature_pages_clamp_saved_offset_when_rows_shrink(self):
    from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState
    pages = ((Destination.DRIVING_CONTROLS, "features", "feature_scroll", "feature_snapshot", "_feature_ui"),
             (Destination.SOUNDS, "sounds", "sounds_scroll", "sounds_snapshot", "_sounds_ui"),
             (Destination.APPEARANCE, "appearance", "appearance_scroll", "appearance_snapshot", "_appearance_ui"),
             (Destination.SYSTEM, "display", "display_scroll", "system_snapshot", "_display_ui"),
             (Destination.DRIVING_MODEL, "models", "model_scroll", "model_snapshot", "_models_ui"))
    rows = tuple(FeatureRow(str(i), str(i), "") for i in range(15))
    for destination, field, offset, source, handler in pages:
      with self.subTest(destination=destination):
        session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
        session.profile = runtime_app.Profile.LARGE
        session.sounds_edit_key = None
        session.adapter = self._adapter()
        session._mode, session.selected = ShellMode.SETTINGS, destination
        session.compact_y = session.compact_scroll_x = 0
        session.sidebar_expanded = True
        session.feature_page = session.feature_root_page = FeaturePage.HUB
        session._snapshot_cache = None
        setattr(session, offset, 10)
        state = Mock()
        setattr(session, source, state)
        for row_count, expected in ((15, 10), (11, 10), (8, 5)):
          state.return_value = FeatureSettingsState(rows=rows[:row_count])
          self.ui.sm.frame += 1
          shown = getattr(session.snapshot(ShellMode.SETTINGS), field)
          self.assertEqual(shown.scroll, expected)
          self.assertEqual(getattr(session, offset), expected)
          self.assertTrue(shown.rows[shown.scroll:shown.scroll + 5])
        getattr(session, handler)(FeatureUiAction("scroll", direction=-1))
        self.assertEqual(getattr(session.snapshot(ShellMode.SETTINGS), field).scroll, 0)
        getattr(session, handler)(FeatureUiAction("scroll", direction=1))
        self.assertEqual(getattr(session.snapshot(ShellMode.SETTINGS), field).scroll, 5)
        state.return_value = FeatureSettingsState()
        self.ui.sm.frame += 1
        self.assertEqual(getattr(session.snapshot(ShellMode.SETTINGS), field).scroll, 0)
        self.assertEqual(getattr(session, offset), 0)
        if destination in (Destination.SOUNDS, Destination.SYSTEM):
          setattr(session, offset, 5)
          setattr(session, prefix := offset.removesuffix("_scroll") + "_edit_key", "7")
          state.return_value = FeatureSettingsState(rows=(rows[7],), editor=True)
          self.ui.sm.frame += 1
          self.assertEqual(getattr(session.snapshot(ShellMode.SETTINGS), field).scroll, 0)
          self.assertEqual(getattr(session, offset), 5)
          setattr(session, prefix, None)

  def test_large_feature_swipes_route_once_and_cancel_when_context_changes(self):
    from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState
    pages = ((Destination.DRIVING_CONTROLS, "feature", "feature_snapshot", "hub"),
             (Destination.SOUNDS, "sounds", "sounds_snapshot", "sounds"),
             (Destination.APPEARANCE, "appearance", "appearance_snapshot", "appearance"),
             (Destination.SYSTEM, "display", "system_snapshot", "display"),
             (Destination.DRIVING_MODEL, "model", "model_snapshot", "models"))
    rows = tuple(FeatureRow(str(i), str(i), "", available=True, page="child") for i in range(13))
    for destination, prefix, source, page in pages:
      for expanded in (True, False):
        with self.subTest(destination=destination, expanded=expanded):
          session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
          session.profile = runtime_app.Profile.LARGE
          session.sounds_edit_key = None
          session.adapter = self._adapter()
          session._mode, session.selected = ShellMode.SETTINGS, destination
          session.compact_y = session.compact_scroll_x = 0
          session.sidebar_expanded = expanded
          session.feature_page = session.feature_root_page = FeaturePage.HUB
          session.appearance_page = "appearance"
          session._snapshot_cache = None
          session.confirmed_offroad = Mock(return_value=True)
          session.favorites = Mock()
          session.view = ShellView.__new__(ShellView)
          session.view.onroad = Mock()
          for name, _, _ in runtime_app._FEATURE_SETTINGS_PANES.values():
            setattr(session.view, name, make_feature_view(session.profile))
          session.pip_warning = Mock()
          session.settings_layer = None
          session.notice = ""
          offset = prefix + "_scroll"
          setattr(session, offset, 0)
          setattr(session, source, Mock(return_value=FeatureSettingsState(page=page, rows=rows)))
          emit = Mock(wraps=session._emit)
          session.input = ShellInput(session.profile, emit)
          for start, end, expected in ((2000, 1700, 5), (2000, 1700, 10), (1800, 2100, 5), (1800, 2100, 0)):
            before = getattr(session, offset)
            session.press(ShellMode.SETTINGS, start, 200)
            session.move(ShellMode.SETTINGS, end, 200)
            self.assertEqual(getattr(session, offset), before)
            self.assertTrue(session.release(ShellMode.SETTINGS, end, 200))
            self.assertEqual(getattr(session, offset), expected)
          widget = runtime_app.StarShellPage(session, ShellMode.SETTINGS)
          bounds = runtime_app.rl.Rectangle(0, 0, 2160, 1080)
          with (patch.object(widget, "_render"), patch.object(gui_app, "_show_touches", False),
                patch("openpilot.system.ui.widgets.device", NS(awake=True)), patch.object(gui_app, "_mouse_events", [])):
            widget.render(bounds)
            for start, direction, duration, last_down, expected in ((2000, 1, .075, 80, 5), (1800, -1, .075, 80, 0),
                                                                   (2000, 1, .5, 80, 0), (2000, 1, .075, 55, 0)):
              events = [MouseEvent(MousePos(start - direction * distance, 200), 0,
                                   index == 0, index == 3, index != 3, 1 + timestamp)
                        for index, (timestamp, distance) in enumerate(((0, 0), (duration / 2, last_down / 2),
                                                                      (duration, last_down), (duration + .01, 80)))]
              with patch.object(gui_app, "_mouse_events", events):
                widget.render(bounds)
              self.assertEqual(getattr(session, offset), expected)
          self.assertEqual([call.args[0].action.kind for call in emit.call_args_list], ["scroll"] * 6)
          changes = [("sidebar_expanded", not expanded), (offset, 5)]
          if prefix in ("feature", "appearance"):
            changes.append((prefix + "_page", "other"))
          for attribute, changed in changes:
            before = getattr(session, attribute)
            session.press(ShellMode.SETTINGS, 2000, 200)
            setattr(session, attribute, changed)
            with patch.object(session, "snapshot") as build:
              session.move(ShellMode.SETTINGS, 1700, 200)
              self.assertFalse(session.release(ShellMode.SETTINGS, 1700, 200))
              build.assert_not_called()
            self.assertEqual(emit.call_count, 6)
            setattr(session, attribute, before)

  def _adapter(self, owner=None):
    self._mono_now = NOW - 100_000_000
    adapter = runtime_app.RuntimeSnapshotAdapter(
      self.ui, owner, mono_clock=lambda: self._mono_now, boot_clock=lambda: self._mono_now + BOOT_OFFSET_NS)
    self._mono_now = NOW
    self.ui.sm.logMonoTime["deviceState"] = NOW - 50_000_000
    self.ui.sm.logMonoTime["pandaStates"] = NOW + BOOT_OFFSET_NS - 50_000_000
    self.ui.sm.recv_time["deviceState"] = (NOW - 30_000_000) / 1e9
    self.ui.sm.recv_time["pandaStates"] = (NOW - 30_000_000) / 1e9
    self.ui.sm.updated["deviceState"] = True
    self.ui.sm.updated["pandaStates"] = True
    return adapter

  def setUp(self):
    self.ui = ui_fake()
    self.ui.started = False
    self.ui.sm["deviceState"].started = False
    self.ui.sm["pandaStates"][0].ignitionLine = False
    self.ui.is_offroad = lambda: True
    self.ui.engaged = False
    self.ui.prime_state = NS(is_paired=lambda: False)
    self.device = native_device.DeviceLayout.__new__(native_device.DeviceLayout)
    self.device._params = Mock()
    self.device._update_calib_description = Mock()
    self.software = native_software.SoftwareLayout.__new__(native_software.SoftwareLayout)
    object.__setattr__(self.software, "_download_btn", NS(action_item=NS(text="CHECK", enabled=True, set_enabled=Mock())))
    self.software._waiting_for_updater = False
    self.software._update_state = Mock()
    panels = {PanelType.DEVICE: NS(instance=self.device), PanelType.SOFTWARE: NS(instance=self.software)}
    self.layout = runtime_app.StarMainLayout.__new__(runtime_app.StarMainLayout)
    object.__setattr__(self.layout, "_layouts", {runtime_app.MainState.SETTINGS: NS(_panels=panels)})
    object.__setattr__(self.layout, "star", NS(adapter=self._adapter(), selected=Destination.DEVICE))
    self.clock = patch.object(runtime_app.time, "monotonic_ns", return_value=NOW)
    self.state = patch.object(runtime_app, "ui_state", self.ui)
    self.clock.start()
    self.state.start()
    self.addCleanup(self.clock.stop)
    self.addCleanup(self.state.stop)

  def test_connectivity_power_uses_native_offroad_without_panda_telemetry(self):
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.adapter = self.layout.star.adapter
    self.ui.sm.messages['pandaStates'] = []
    self.assertFalse(session.adapter.connectivity_allowed())
    self.assertTrue(session.connectivity_allowed())
    self.ui.is_offroad = lambda: False
    self.assertFalse(session.connectivity_allowed())

  def test_large_pairing_uses_native_dialog_only_while_unpaired_and_parked(self):
    with patch("openpilot.selfdrive.ui.widgets.pairing_dialog.PairingDialog", return_value="pairing") as dialog, \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      self.layout._show_pairing()
      dialog.assert_called_once_with()
      pushed.assert_called_once_with("pairing")
      pushed.reset_mock()
      self.ui.prime_state = NS(is_paired=lambda: True)
      self.layout._show_pairing()
      pushed.assert_not_called()
      self.ui.prime_state = NS(is_paired=lambda: False)
      self.ui.sm["deviceState"].started = True
      self.layout._show_pairing()
      pushed.assert_not_called()

  def test_camera_and_calibration_use_native_dialogs_and_confirmation(self):
    with patch.object(runtime_app.gui_app, "push_widget") as pushed, \
         patch.object(native_device, "ConfirmDialog", side_effect=lambda *a, **k: NS(callback=k["callback"])), \
         patch.object(native_device, "ui_state", self.ui), \
         patch.object(native_device, "calibration_reset") as reset:
      self.device._calibration_reset_pending = False
      reset.request.return_value = True
      with patch("openpilot.selfdrive.ui.onroad.cabin_camera_dialog.CabinCameraDialog", return_value="camera"):
        self.assertTrue(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.PREVIEW_DRIVER_CAMERA)))
      pushed.assert_called_once_with("camera")
      self.assertTrue(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.RESET_CALIBRATION)))
      dialog = pushed.call_args.args[0]
      reset.request.assert_not_called()
      dialog.callback(native_device.DialogResult.CANCEL)
      reset.request.assert_not_called()
      self.ui.sm["pandaStates"][0].ignitionLine = True
      self.ui.sm["deviceState"].started = True
      dialog.callback(native_device.DialogResult.CONFIRM)
      reset.request.assert_not_called()
      self.ui.sm["deviceState"].started = False
      dialog.callback(native_device.DialogResult.CONFIRM)
      reset.request.assert_called_once_with(self.device._params)
      self.assertTrue(self.device._calibration_reset_pending)

  def test_galaxy_never_opens_comma_pairing_or_infers_manage_from_home_pairing(self):
    with patch("openpilot.selfdrive.ui.widgets.pairing_dialog.PairingDialog") as comma_dialog, \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      self.assertFalse(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.OPEN_GALAXY)))
      self.ui.prime_state = NS(is_paired=lambda: True)
      shown = self.layout.star.adapter.build(ShellMode.SETTINGS, Destination.DEVICE, now_ns=NOW)
      self.assertTrue(shown.home.paired)
      self.assertIsNone(shown.device.galaxy_paired)
      self.assertNotIn(DeviceRequest.OPEN_GALAXY, shown.device.available_actions)
      self.assertFalse(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.OPEN_GALAXY)))
      comma_dialog.assert_not_called()
      pushed.assert_not_called()

  def test_galaxy_owner_runtime_allows_view_while_started(self):
    with tempfile.TemporaryDirectory() as directory:
      owner = GalaxyAccessOwner(Path(directory) / "access")
      self.layout.star.adapter = self._adapter(owner)
      self.layout.star.galaxy_flow = GalaxyAccessFlow(owner, self.layout._confirmed_offroad)
      initial = self.layout.star.adapter.build(ShellMode.SETTINGS, Destination.DEVICE, now_ns=NOW)
      self.assertIn(DeviceRequest.OPEN_GALAXY, initial.device.available_actions)
      self.assertFalse(initial.device.galaxy_configured)
      self.assertIsNone(initial.device.galaxy_paired)
      self.assertTrue(initial.device.galaxy_local_only)
      with patch("openpilot.starpilot.ui.galaxy_access.GalaxyConnectionView", return_value="connection page"), \
           patch.object(runtime_app.gui_app, "push_widget") as pushed, \
           patch("openpilot.selfdrive.ui.widgets.pairing_dialog.PairingDialog") as comma_dialog:
        self.assertTrue(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.OPEN_GALAXY)))
        pushed.assert_called_once_with("connection page")
        pushed.reset_mock()
        self.ui.started = True
        self.layout.star.galaxy_flow.open_large()
        pushed.assert_called_once_with("connection page")
        self.assertFalse(self.layout.star.galaxy_flow.pair("password123"))
        comma_dialog.assert_not_called()

  def test_unavailable_credentials_do_not_block_local_galaxy(self):
    owner = Mock(spec=GalaxyAccessOwner)
    owner.status.return_value = NS(status=AccessStatus.UNAVAILABLE)
    self.layout.star.adapter = self._adapter(owner)
    self.layout.star.galaxy_flow = GalaxyAccessFlow(owner, self.layout._confirmed_offroad)
    shown = self.layout.star.adapter.build(ShellMode.SETTINGS, Destination.DEVICE, now_ns=NOW)
    self.assertIn(DeviceRequest.OPEN_GALAXY, shown.device.available_actions)
    with patch("openpilot.starpilot.ui.galaxy_access.GalaxyConnectionView", return_value="connection page"), \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      self.assertTrue(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.OPEN_GALAXY)))
      pushed.assert_called_once_with("connection page")
    owner.configure.assert_not_called()
    owner.remove.assert_not_called()

  def test_compact_galaxy_is_on_main_menu_and_device_keeps_native_rows(self):
    from openpilot.selfdrive.ui.mici.layouts.settings.device import device_layout as compact_device
    from openpilot.starpilot.ui import display_compact, replay_settings
    replay_button = Mock()
    display_entry = object()
    panel = NS(_scroller=NS(add_widgets=Mock()))
    layout = runtime_app.StarMiciMainLayout.__new__(runtime_app.StarMiciMainLayout)
    object.__setattr__(layout, "_compact_panels", {})
    galaxy = NS(open_compact=Mock())
    object.__setattr__(layout, "star", NS(galaxy_flow=galaxy))
    with patch.object(compact_device, "DeviceLayoutMici", return_value=panel) as built, \
         patch.object(display_compact.DisplayCompact, "entry_button", return_value=display_entry), \
         patch.object(replay_settings, "BigButton", return_value=replay_button) as replay_constructor, \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      layout._open_compact_destination(Destination.DEVICE)
    built.assert_called_once_with()
    panel._scroller.add_widgets.assert_called_once_with([display_entry, replay_button])
    pushed.assert_called_once_with(panel)
    layout._open_compact_destination(Destination.GALAXY)
    galaxy.open_compact.assert_called_once_with()

    replay_constructor.assert_called_once_with("replay", "recorded driving routes on this device")
    replay_button.set_click_callback.assert_called_once()
    with patch.object(replay_settings, "open_replay") as open_replay:
      replay_button.set_click_callback.call_args.args[0]()
    open_replay.assert_called_once_with(layout.star)

  def test_compact_toggles_and_software_keep_only_native_rows(self):
    from openpilot.selfdrive.ui.mici.layouts.settings import toggles, software
    panels = []
    layout = runtime_app.StarMiciMainLayout.__new__(runtime_app.StarMiciMainLayout)
    object.__setattr__(layout, "_compact_panels", {})
    object.__setattr__(layout, "star", NS())
    def panel():
      value = NS(_scroller=NS(add_widgets=Mock()))
      panels.append(value)
      return value
    with patch.object(toggles, "TogglesLayoutMici", side_effect=panel), \
         patch.object(software, "SoftwareLayoutMici", side_effect=panel), \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      layout._open_compact_destination(Destination.TOGGLES)
      layout._open_compact_destination(Destination.SOFTWARE)
    self.assertEqual(len(panels), 2)
    for item in panels:
      item._scroller.add_widgets.assert_not_called()
    self.assertEqual([call.args[0] for call in pushed.call_args_list], panels)

  def test_compact_model_visuals_and_pair_use_existing_owners(self):
    from openpilot.starpilot.ui import replay_settings
    replay_button = Mock()
    from openpilot.starpilot.ui import appearance_compact, models_compact, display_compact
    from openpilot.selfdrive.ui.mici.layouts.settings.device import device_layout as compact_device
    layout = runtime_app.StarMiciMainLayout.__new__(runtime_app.StarMiciMainLayout)
    object.__setattr__(layout, "_compact_panels", {})
    object.__setattr__(layout, "star", NS())
    panel = NS(scroll_to_pairing=Mock(), _scroller=NS(add_widgets=Mock()))
    with patch.object(models_compact.ModelsCompact, "open") as model_open, \
         patch.object(appearance_compact.AppearanceCompact, "open") as visuals_open, \
         patch.object(compact_device, "DeviceLayoutMici", return_value=panel), \
         patch.object(display_compact.DisplayCompact, "entry_button", return_value="display entry"), \
         patch.object(replay_settings, "BigButton", return_value=replay_button) as replay_constructor, \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      layout._open_compact_destination(Destination.DRIVING_MODEL)
      layout._open_compact_destination(Destination.APPEARANCE)
      layout._open_compact_destination(Destination.PAIR)
    model_open.assert_called_once_with()
    visuals_open.assert_called_once_with()
    pushed.assert_called_once_with(panel)
    panel.scroll_to_pairing.assert_called_once_with()
    panel._scroller.add_widgets.assert_called_once_with(["display entry", replay_button])

    replay_constructor.assert_called_once_with("replay", "recorded driving routes on this device")
    replay_button.set_click_callback.assert_called_once()
    with patch.object(replay_settings, "open_replay") as open_replay:
      replay_button.set_click_callback.call_args.args[0]()
    open_replay.assert_called_once_with(layout.star)

  def test_compact_connection_can_be_viewed_while_started_but_not_mutated(self):
    with tempfile.TemporaryDirectory() as directory:
      owner = GalaxyAccessOwner(Path(directory) / "access")
      self.layout.star.adapter = self._adapter(owner)
      flow = GalaxyAccessFlow(owner, self.layout._confirmed_offroad)
      with patch("openpilot.starpilot.ui.galaxy_access.GalaxyConnectionPage", return_value="connection page"), \
           patch.object(runtime_app.gui_app, "push_widget") as pushed:
        flow.open_compact()
        pushed.assert_called_once_with("connection page")
        pushed.reset_mock()
        self.ui.started = True
        flow.open_compact()
        pushed.assert_called_once_with("connection page")
        self.assertFalse(flow.pair("password123"))
        self.ui.started = False
        pushed.reset_mock()
        flow.open_compact()
        pushed.assert_called_once_with("connection page")

  def test_galaxy_connection_url_contains_only_numeric_lan_address(self):
    self.assertEqual(connection_url("192.168.1.111"), "http://192.168.1.111:8082/#/")
    self.assertEqual(connection_url("10.0.0.1"), "http://10.0.0.1:8082/#/")
    for address in ("", "localhost", "127.0.0.1", "0.0.0.0", "169.254.1.1", "224.0.0.1", "::1",
                    "192.168.1.111/?password=secret"):
      with self.subTest(address=address):
        self.assertIsNone(connection_url(address))

  def test_stale_or_started_state_blocks_owner_and_unowned_rows(self):
    with patch.object(runtime_app.gui_app, "push_widget") as pushed:
      self.assertFalse(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.RESET_DRIVER_MONITORING)))
      self.ui.sm.logMonoTime["deviceState"] = NOW - 2_000_000_000
      self.assertFalse(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.OPEN_GALAXY)))
      self.ui.sm.logMonoTime["deviceState"] = NOW
      self.ui.started = True
      self.assertFalse(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.PREVIEW_DRIVER_CAMERA)))
      pushed.assert_not_called()

  def test_in_drive_toggle_uses_native_item_guard_while_device_action_stays_parked(self):
    self.layout.star.selected = Destination.TOGGLES
    request = ToggleRequest(ToggleKey.METRIC, True)
    with patch.object(self.layout, "_confirmed_offroad", return_value=False), \
         patch.object(self.layout, "_deliver_toggles", return_value=True) as deliver:
      self.assertTrue(self.layout._deliver_settings_request(request))
      deliver.assert_called_once_with(request)
      self.layout.star.selected = Destination.DEVICE
      self.assertFalse(self.layout._deliver_settings_request(DeviceAction(DeviceRequest.RESET_CALIBRATION)))

  def test_runtime_snapshot_marks_only_evidenced_controls_available(self):
    shown = self.layout.star.adapter.build(ShellMode.SETTINGS, Destination.SOFTWARE, now_ns=NOW)
    self.assertTrue(shown.software.automatic_updates)
    self.assertEqual(shown.software.available_actions, frozenset({
      SoftwareRequest.OPEN_UNINSTALL_CONFIRMATION, SoftwareRequest.OPEN_ERROR_LOG,
    }))
    self.assertNotIn(DeviceRequest.RESET_DRIVER_MONITORING, shown.device.available_actions)
    self.assertNotIn(DeviceRequest.OPEN_GALAXY, shown.device.available_actions)
    self.assertIsNone(shown.device.galaxy_paired)
    self.ui.params.values.pop("DisableUpdates", None)
    self.assertIsNone(self.layout.star.adapter.build(ShellMode.SETTINGS, Destination.SOFTWARE, now_ns=NOW).software.automatic_updates)
    self.ui.params.values["UpdaterState"] = "idle"
    self.ui.params.values["UpdaterAvailableBranches"] = "new,old"
    known = self.layout.star.adapter.build(ShellMode.SETTINGS, Destination.SOFTWARE, now_ns=NOW)
    self.assertIn(SoftwareRequest.CHECK_FOR_UPDATES, known.software.available_actions)
    self.assertIn(SoftwareRequest.OPEN_BRANCH_CHOOSER, known.software.available_actions)

  def test_held_device_press_cancels_when_offroad_evidence_changes(self):
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    self.enterContext(patch.object(session, 'drive_state', NS(snapshot=lambda: {"mode": "auto", "revision": None, "available": False,
                                                    "effective": None, "overrideAllowed": False}), create=True))
    session.profile = runtime_app.Profile.LARGE
    session.selected = Destination.DEVICE
    session.adapter = self.layout.star.adapter
    session._mode = ShellMode.SETTINGS
    session._request_owner = self.layout._deliver_settings_request
    session._request_emitted = False
    session._snapshot_cache = None
    session.input = ShellInput(session.profile, session._emit)
    session.favorites = Mock()
    session.view = Mock()
    session._favorite_claimed = False
    with patch.object(session, "snapshot", side_effect=lambda mode: session.adapter.build(mode, session.selected, now_ns=NOW)), \
         patch.object(runtime_app.gui_app, "push_widget") as pushed:
      session.press(ShellMode.SETTINGS, 1900, 630)
      self.ui.started = True
      self.assertFalse(session.release(ShellMode.SETTINGS, 1900, 630))
      pushed.assert_not_called()

  def test_onroad_settings_touch_uses_displayed_snapshot_without_rebuilding(self):
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    self.enterContext(patch.object(session, 'drive_state', NS(snapshot=lambda: {"mode": "auto", "revision": None, "available": False,
                                                    "effective": None, "overrideAllowed": False}), create=True))
    session.selected = Destination.DEVICE
    session._mode = ShellMode.SETTINGS
    session._request_emitted = False
    session.input = Mock()
    displayed = NS(selected=Destination.DEVICE, device=NS(offroad=False))
    self.enterContext(patch.object(session, '_rendered_settings', (NOW, displayed), create=True))
    session._rendered_settings_pipeline = (bool(self.ui.started), self.ui.started_frame)
    session._settings_touch = None
    with patch.object(self.ui, "is_offroad", return_value=False), \
         patch.object(session, "snapshot") as build, patch.object(session, "confirmed_offroad") as parked:
      session.press(ShellMode.SETTINGS, 100, 200)
      session.move(ShellMode.SETTINGS, 101, 200)
      self.assertFalse(session.release(ShellMode.SETTINGS, 101, 200))
    build.assert_not_called()
    parked.assert_not_called()
    self.assertIs(session.input.press.call_args.args[-1], displayed)
    self.assertIs(session.input.move.call_args.args[-1], displayed)
    self.assertIs(session.input.release.call_args.args[-1], displayed)
    self.assertIsNone(session._settings_touch)

  def test_settings_touch_cancels_if_parked_evidence_is_lost(self):
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    self.enterContext(patch.object(session, 'drive_state', NS(snapshot=lambda: {"mode": "auto", "revision": None, "available": False,
                                                    "effective": None, "overrideAllowed": False}), create=True))
    session.selected = Destination.DEVICE
    session._mode = ShellMode.SETTINGS
    session._request_emitted = False
    session.input = Mock()
    session.favorites = Mock()
    session.view = Mock()
    session._favorite_claimed = False
    self.enterContext(patch.object(session, '_rendered_settings', (NOW, NS(selected=Destination.DEVICE, device=NS(offroad=True))), create=True))
    session._rendered_settings_pipeline = (bool(self.ui.started), self.ui.started_frame)
    session._settings_touch = None
    with patch.object(session, "confirmed_offroad", side_effect=(True, False)), \
         patch.object(session, "snapshot") as build:
      session.press(ShellMode.SETTINGS, 100, 200)
      self.assertFalse(session.release(ShellMode.SETTINGS, 100, 200))
    build.assert_not_called()
    session.input.release.assert_not_called()
    session.input.cancel.assert_called_once()

  def test_compact_normal_pages_remain_readable_in_drive(self):
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    self.enterContext(patch.object(session, 'drive_state', NS(snapshot=lambda: {"mode": "auto", "revision": None, "available": False,
                                                    "effective": None, "overrideAllowed": False}), create=True))
    session.profile = runtime_app.Profile.COMPACT
    session.adapter = self.layout.star.adapter
    session.selected = Destination.STAR
    session.compact_y = session.compact_scroll_x = 0
    session.sidebar_expanded = True
    session._snapshot_cache = None
    self.ui.started = True
    self.ui.is_offroad = lambda: False
    state = session.snapshot(ShellMode.SETTINGS).settings
    for destination in (Destination.NETWORK, Destination.BLUETOOTH, Destination.VEHICLE, Destination.DEVELOPER):
      self.assertTrue(state.destination(destination).available, destination)
    self.assertFalse(state.destination(Destination.PAIR).available)

  def test_compact_network_in_drive_uses_native_network_owner(self):
    layout = runtime_app.StarMiciMainLayout.__new__(runtime_app.StarMiciMainLayout)
    self.enterContext(patch.object(layout, 'star', NS(connectivity_allowed=Mock(return_value=False)), create=True))
    self.enterContext(patch.object(layout, '_compact_panels', {}, create=True))
    page = NS()
    with patch("openpilot.selfdrive.ui.mici.layouts.settings.network.network_layout.NetworkLayoutMici", return_value=page) as native, \
         patch.object(runtime_app.gui_app, "push_widget") as push:
      layout._open_compact_destination(Destination.NETWORK)
      layout._open_compact_destination(Destination.NETWORK)
    native.assert_called_once_with()
    self.assertEqual(push.call_args_list, [call(page), call(page)])
    layout.star.connectivity_allowed.assert_not_called()

  def test_software_check_owner_and_waiting_gate_with_mocked_final_effect(self):
    self.layout.star.selected = Destination.SOFTWARE
    self.ui.params.values["UpdaterState"] = "idle"
    with patch.object(native_software.subprocess, "run") as effect:
      self.assertTrue(self.layout._deliver_settings_request(SoftwareAction(SoftwareRequest.CHECK_FOR_UPDATES)))
      effect.assert_called_once()
      self.assertTrue(self.software._waiting_for_updater)
      self.assertFalse(self.layout._deliver_settings_request(SoftwareAction(SoftwareRequest.CHECK_FOR_UPDATES)))
      self.ui.params.values["UpdaterFetchAvailable"] = "1"
      self.software._waiting_for_updater = False
      self.assertFalse(self.layout._deliver_settings_request(SoftwareAction(SoftwareRequest.CHECK_FOR_UPDATES)))
      object.__setattr__(self.software._download_btn.action_item, "text", "DOWNLOAD")
      self.assertTrue(self.layout._deliver_settings_request(SoftwareAction(SoftwareRequest.DOWNLOAD_UPDATE)))
      self.assertEqual(effect.call_count, 2)

  def test_software_native_uninstall_confirmation_and_branch_owner(self):
    self.layout.star.selected = Destination.SOFTWARE
    self.ui.params.values.update(UpdaterState="idle", UpdaterAvailableBranches="new,old")
    self.ui.params.put_bool = Mock()
    self.ui.params.put = Mock()
    object.__setattr__(self.software, "_branch_btn", NS(action_item=NS(set_value=Mock())))
    with patch.object(native_software, "ConfirmDialog", side_effect=lambda *a, **k: NS(callback=k["callback"])), \
         patch.object(native_software, "MultiOptionDialog", side_effect=lambda *a, **k: NS(callback=k["callback"], selection="new")), \
         patch.object(native_software.gui_app, "push_widget") as pushed, \
         patch.object(native_software.subprocess, "run") as effect, \
         patch.object(native_software, "ui_state", self.ui):
      self.assertTrue(self.layout._deliver_settings_request(SoftwareAction(SoftwareRequest.OPEN_UNINSTALL_CONFIRMATION)))
      dialog = pushed.call_args.args[0]
      self.ui.params.put_bool.assert_not_called()
      dialog.callback(native_software.DialogResult.CANCEL)
      self.ui.params.put_bool.assert_not_called()
      self.ui.sm["deviceState"].started = True
      dialog.callback(native_software.DialogResult.CONFIRM)
      self.ui.params.put_bool.assert_not_called()
      self.ui.sm["deviceState"].started = False
      dialog.callback(native_software.DialogResult.CONFIRM)
      self.ui.params.put_bool.assert_called_once_with("DoUninstall", True, block=True)
      self.assertTrue(self.layout._deliver_settings_request(SoftwareAction(SoftwareRequest.OPEN_BRANCH_CHOOSER)))
      branch_dialog = pushed.call_args.args[0]
      self.ui.params.put.assert_not_called()
      self.ui.sm["pandaStates"][0].ignitionLine = True
      self.ui.sm["deviceState"].started = True
      branch_dialog.callback(native_software.DialogResult.CONFIRM)
      self.ui.params.put.assert_not_called()
      self.ui.sm["deviceState"].started = False
      self.assertTrue(self.layout._deliver_settings_request(SoftwareAction(SoftwareRequest.OPEN_BRANCH_CHOOSER)))
      branch_dialog = pushed.call_args.args[0]
      branch_dialog.callback(native_software.DialogResult.CONFIRM)
      self.ui.params.put.assert_called_once_with("UpdaterTargetBranch", "new", block=True)
      effect.assert_called_once()
      with patch.object(self.software, "_on_error_log") as open_error_log:
        self.assertTrue(self.layout._deliver_settings_request(SoftwareAction(SoftwareRequest.OPEN_ERROR_LOG)))
        open_error_log.assert_called_once_with()
      self.assertFalse(self.layout._deliver_settings_request(SoftwareAction(SoftwareRequest.SET_AUTOMATIC_UPDATES, False)))


if __name__ == "__main__":
  unittest.main()


def test_compact_developer_fps_is_live_onroad_but_physical_controls_keep_native_gates(tmp_path):
  from openpilot.selfdrive.ui.mici.layouts.settings import developer

  ui = NS(params=Params(str(tmp_path)), CP=None, is_release=False, engaged=True,
           is_offroad=lambda: False, add_offroad_transition_callback=Mock())
  with patch.object(developer, 'ui_state', ui), patch.object(developer, 'SshKeyFetcher'), \
       patch.object(developer.gui_app, 'texture', return_value=NS(width=64, height=64)), \
       patch.object(developer, 'BigButton'), patch.object(developer, 'BigToggle', side_effect=lambda *a, **k: Mock()), \
       patch.object(developer, 'BigCircleParamControl', side_effect=lambda *a, **k: Mock()), \
       patch.object(developer, 'BigParamControl') as debug, \
       patch.object(developer.gui_app, 'set_show_touches') as touches, patch.object(developer.gui_app, 'set_show_fps') as fps:
    page = developer.DeveloperLayoutMici()
    assert page._adb_toggle.set_enabled.call_args.args[0]() is False
    assert page._joystick_toggle.set_enabled.call_args.args[0]() is False
    assert page._alpha_long_toggle.set_enabled.call_args.args[0]() is False
    page._debug_mode_toggle.set_enabled.assert_not_called()
    next(c for c in debug.call_args_list if c.args[0] == "ui debug mode").kwargs['toggle_callback'](True)
    touches.assert_called_once_with(True)
    fps.assert_called_once_with(True)
