"""Parked-power document, source-bound UI and stock hardware-policy regressions."""
from unittest.mock import patch


import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

from openpilot.common.params import Params
from openpilot.starpilot.power.offroad_preferences import KEY, PowerPolicy, decode, effective, encode, read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureInput, FeatureSettingsRequest, row_change
from openpilot.starpilot.ui.feature_settings_state import FeatureUiAction, value_buttons
from openpilot.starpilot.ui.power_owner import ENABLED, HOURS, VOLTS, PowerOwner, confirm_question, power_row_change
from openpilot.starpilot.ui.display_owner import DisplayOwner
from openpilot.starpilot.ui.power_compact import PowerCompact
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.runtime_app import StarShellSession
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.hardware.power_monitoring import PowerMonitoring


class PowerSettingsTests(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.params = Params(directory.name)
    self.path = Path(self.params.get_param_path(KEY))
    self.parked = True
    self.owner = PowerOwner(self.params, lambda: self.parked)

  def row(self, key):
    return next(row for row in self.owner.snapshot().rows if row.key == key)

  def request(self, key, direction=1):
    request = row_change(self.row(key), direction)
    assert request is not None
    return FeatureSettingsRequest(request.key, request.expected, request.value, confirmation=True)

  def test_absent_is_stock_with_no_write_and_legacy_bytes_are_untouched(self):
    legacy = Path(self.params.get_param_path("DeviceShutdown"))
    legacy.write_bytes(b"9")  # old indexes/minutes cannot safely be inferred as hours
    self.assertEqual(self.row(ENABLED).value, "Stock")
    self.assertEqual(self.row(HOURS).value, "30 h")
    self.assertEqual(self.row(VOLTS).value, "11.8 V")
    self.assertIsNone(read_saved(self.params).raw)
    self.assertFalse(self.path.exists())
    self.assertEqual(legacy.read_bytes(), b"9")

  def test_codec_rejects_noncanonical_shapes_and_nonregular_or_oversized_files(self):
    policy = PowerPolicy(True, 6, 123)
    self.assertEqual(decode(encode(policy)), policy)
    for raw in (b"{}", b"[]", b"{", b"{\"version\":1,\"enabled\":1,\"delayHours\":6,\"cutoffTenths\":123}",
                b"{\"version\":1,\"enabled\":true,\"delayHours\":0,\"cutoffTenths\":123}",
                b"{\"version\":1,\"enabled\":true,\"delayHours\":6,\"cutoffTenths\":126}",
                b"{\"version\":1,\"version\":1,\"enabled\":true,\"delayHours\":6,\"cutoffTenths\":123}",
                b"[" * 250 + b"0" + b"]" * 250, b"\xff"):
      self.assertIsNone(decode(raw))
    self.path.write_bytes(b"x" * 513)
    self.assertFalse(read_saved(self.params).readable)
    self.assertFalse(self.row(ENABLED).available)
    self.path.unlink()
    self.path.symlink_to(legacy := self.path.parent / "other")
    legacy.write_bytes(encode(policy))
    self.assertFalse(read_saved(self.params).readable)
    self.path.unlink()
    os.mkfifo(self.path)
    self.assertFalse(read_saved(self.params).readable)

  def test_invalid_document_uses_stock_and_requires_explicit_reset(self):
    self.path.write_bytes(b"bad")
    self.assertFalse(read_saved(self.params).valid)
    self.assertEqual(effective(read_saved(self.params)), PowerPolicy())
    self.assertEqual(self.row(ENABLED).repair_value, "Stock")
    self.assertFalse(self.row(HOURS).available)
    request = self.request(ENABLED)
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(read_saved(self.params).policy, PowerPolicy())

  def test_atomic_edits_and_final_source_and_parked_guards(self):
    self.assertIsNone(power_row_change(self.row(HOURS), 1))  # 30 h must not wrap to 1 h
    self.assertIsNone(power_row_change(self.row(VOLTS), -1))  # 11.8 V must not wrap to 12.5 V
    request = self.request(ENABLED)
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.assertFalse(self.path.exists())
    self.parked = True
    self.assertIn("30-hour maximum and 11.8-V cutoff", confirm_question(request))
    self.assertTrue(self.owner.apply(request))
    self.assertTrue(read_saved(self.params).policy.enabled)
    request = self.request(HOURS, -1)
    self.path.write_bytes(encode(PowerPolicy(True, 5, 118)))
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(read_saved(self.params).policy.delay_hours, 5)
    request = self.request(VOLTS)
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(read_saved(self.params).policy.cutoff_tenths, 119)
    self.assertFalse(self.owner.apply(request))
    request = self.request(ENABLED)
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(read_saved(self.params).policy.enabled)

  def test_hardware_stock_and_custom_policy_keep_other_shutdown_gates(self):
    now = [3601.0]
    with patch("openpilot.system.hardware.power_monitoring.Params", return_value=self.params), \
         patch("openpilot.system.hardware.power_monitoring.time.monotonic", side_effect=lambda: now[0]):
      monitor = PowerMonitoring()
      monitor.car_battery_capacity_uWh = 20e6
      monitor.car_voltage_mV = 12000
      self.assertFalse(monitor.should_shutdown(False, True, 0.0, True))  # stock 30 h
      self.path.write_bytes(encode(PowerPolicy(True, 1, 125)))
      now[0] += 1.0
      self.assertTrue(monitor.should_shutdown(False, True, 0.0, True))
      self.assertFalse(monitor.should_shutdown(True, True, 0.0, True))
      self.assertFalse(monitor.should_shutdown(False, False, 0.0, True))
      self.params.put_bool("DisablePowerDown", True, block=True)
      self.assertFalse(monitor.should_shutdown(False, True, 0.0, True))
      self.params.put_bool("DisablePowerDown", False, block=True)
      self.path.write_bytes(b"bad")
      now[0] += 1.0
      self.assertFalse(monitor.should_shutdown(False, True, 0.0, True))  # invalid returns stock

  def test_hardware_policy_is_cached_for_one_second_then_refreshes(self):
    with patch("openpilot.system.hardware.power_monitoring.Params", return_value=self.params), \
         patch("openpilot.system.hardware.power_monitoring.time.monotonic") as clock, \
         patch("openpilot.system.hardware.power_monitoring.read_saved", wraps=read_saved) as reader:
      monitor = PowerMonitoring()
      monitor.car_battery_capacity_uWh = 20e6
      monitor.car_voltage_mV = 13000
      clock.return_value = 600.0
      monitor.should_shutdown(False, True, 0.0, True)
      clock.return_value = 600.5
      monitor.should_shutdown(False, True, 0.0, True)
      reader.assert_called_once()
      self.path.write_bytes(encode(PowerPolicy(True, 1, 118)))
      clock.return_value = 601.0
      monitor.should_shutdown(False, True, 0.0, True)
      self.assertEqual(reader.call_count, 2)
      self.assertEqual(monitor._saved_power_policy.delay_hours, 1)

  def test_stock_shutdown_boundaries_and_custom_voltage_are_isolated(self):
    now = [108000.0]  # exactly the stock 30-hour limit
    with patch("openpilot.system.hardware.power_monitoring.Params", return_value=self.params), \
         patch("openpilot.system.hardware.power_monitoring.time.monotonic", side_effect=lambda: now[0]):
      monitor = PowerMonitoring()
      monitor.car_battery_capacity_uWh = 20e6
      monitor.car_voltage_mV = 11800
      self.assertFalse(monitor.should_shutdown(False, True, 0.0, True))  # strict > time, strict < voltage
      now[0] += 1.0
      self.assertTrue(monitor.should_shutdown(False, True, 0.0, True))
      self.assertFalse(monitor.should_shutdown(True, True, 0.0, True))
      self.assertFalse(monitor.should_shutdown(False, False, 0.0, True))
      self.params.put_bool("DisablePowerDown", True, block=True)
      self.assertFalse(monitor.should_shutdown(False, True, 0.0, True))
      self.params.put_bool("DisablePowerDown", False, block=True)
      now[0] = 301.0
      monitor.car_battery_capacity_uWh = 0
      self.assertTrue(monitor.should_shutdown(False, True, 0.0, True))
      now[0] = 300.0
      self.assertFalse(monitor.should_shutdown(False, True, 0.0, True))
      monitor.car_battery_capacity_uWh = 20e6
      monitor.car_voltage_mV = 11799
      now[0] = 301.0
      self.assertTrue(monitor.should_shutdown(False, True, 0.0, True))
      monitor.car_voltage_mV = 12000
      self.assertFalse(monitor.should_shutdown(False, True, 0.0, True))
      self.path.write_bytes(encode(PowerPolicy(True, 30, 125)))
      now[0] += 1.0
      self.assertTrue(monitor.should_shutdown(False, True, 0.0, True))
      self.params.put_bool("ForcePowerDown", True, block=True)
      self.assertTrue(monitor.should_shutdown(True, False, 0.0, True))

  def test_final_document_read_failure_and_parked_loss_write_nothing(self):
    request = self.request(ENABLED)
    original = self.params.get_param_path
    reads = 0
    def unavailable_on_final(key):
      nonlocal reads
      if key == KEY:
        reads += 1
        if reads == 2:
          raise OSError("read authority lost")
      return original(key)
    with patch.object(self.params, "get_param_path", side_effect=unavailable_on_final):
      self.assertFalse(self.owner.apply(request))
    self.assertFalse(self.path.exists())
    checks = 0
    def lost_parked():
      nonlocal checks
      checks += 1
      return checks < 3
    self.assertFalse(PowerOwner(self.params, lost_parked).apply(request))
    self.assertFalse(self.path.exists())

  def test_large_system_confirm_uses_displayed_source_and_recovers_from_stale_dialog(self):
    session = StarShellSession.__new__(StarShellSession)
    self.enterContext(patch.object(session, 'drive_state', NS(snapshot=lambda: {"mode": "auto", "revision": None, "available": False,
                                                    "effective": None, "overrideAllowed": False}), create=True))
    session.profile = Profile.LARGE
    session._mode = ShellMode.SETTINGS
    session.selected = Destination.SYSTEM
    session.display_owner = DisplayOwner(self.params, lambda: self.parked)
    session.power_owner = self.owner
    session.display_scroll = 0
    session._snapshot_cache = None
    object.__setattr__(session, "input", NS(cancel=Mock()))
    session.favorites = Mock()
    session.view = Mock()
    system = session.system_snapshot()
    power_index = next(index for index, row in enumerate(system.rows) if row.key == ENABLED)
    displayed = replace(system, scroll=power_index)
    actions = []
    native_input = FeatureInput(actions.append)
    native_input.press(1980, 245, displayed)
    native_input.release(1980, 245, displayed)
    self.assertEqual(actions[0].row.key, ENABLED)
    self.assertEqual(actions[0].kind, 'open')
    native_input.press(1980, 245, displayed)
    native_input.cancel()
    native_input.release(1980, 165, displayed)
    self.assertEqual(len(actions), 1)
    def request_enable():
      state = session.system_snapshot()
      if not state.editor:
        row = next(row for row in state.rows if row.key == ENABLED)
        session._display_ui(FeatureUiAction('open', row))
      state = session.system_snapshot()
      self.assertTrue(state.editor)
      self.assertEqual(state.save_hint, 'Changes require confirmation')
      row = state.rows[0]
      index = next(i for i, button in enumerate(value_buttons(state, row)) if button[0] == 'Custom limits')
      session._display_ui(FeatureUiAction('action', row, index))

    def fake_dialog(_question, _button, callback):
      return NS(_callback=callback)
    with patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget") as pushed, \
         patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", side_effect=fake_dialog):
      request_enable()
    dialog = pushed.call_args.args[0]
    self.path.write_bytes(encode(PowerPolicy(False, 4, 121)))
    dialog._callback(DialogResult.CONFIRM)
    self.assertEqual(read_saved(self.params).policy, PowerPolicy(False, 4, 121))
    with patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget") as pushed, \
         patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", side_effect=fake_dialog):
      request_enable()
    self.parked = False
    pushed.call_args.args[0]._callback(DialogResult.CONFIRM)
    self.assertFalse(read_saved(self.params).policy.enabled)
    self.parked = True
    with patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget") as pushed, \
         patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", side_effect=fake_dialog):
      request_enable()
    abandoned = pushed.call_args.args[0]
    session.cancel()  # Leaving settings revokes the old confirmation, even after returning.
    abandoned._callback(DialogResult.CONFIRM)
    self.assertFalse(read_saved(self.params).policy.enabled)
    with patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget") as pushed, \
         patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", side_effect=fake_dialog):
      request_enable()
    pushed.call_args.args[0]._callback(DialogResult.CONFIRM)
    self.assertTrue(read_saved(self.params).policy.enabled)

  def test_compact_device_child_uses_same_owner_and_refreshes_after_confirm(self):
    class Button:
      def __init__(self, label, description):
        self.label, self.description = label, description
        self.click = None

      def set_click_callback(self, click):
        self.click = click

    class Scroller:
      def __init__(self):
        self._scroller = NS(items=[], add_widgets=lambda widgets: self._scroller.items.extend(widgets))

    owner = self.owner
    class Session:
      def power_snapshot(self):
        return owner.snapshot()

      def power_request(self, request):
        return owner.apply(request)

    compact = PowerCompact(Session())
    def fake_dialog(_question, _button, callback):
      return NS(_callback=callback)
    def fake_picker(_title, choices, current, callback):
      return NS(options=choices, selection=current, _callback=callback)
    with patch("openpilot.starpilot.ui.power_compact.BigButton", Button), \
         patch("openpilot.starpilot.ui.power_compact.GreyBigButton", Button), \
         patch("openpilot.starpilot.ui.power_compact.NavScroller", Scroller), \
         patch("openpilot.starpilot.ui.power_compact.ConfirmDialog", side_effect=fake_dialog), \
         patch("openpilot.starpilot.ui.power_compact.MultiOptionDialog", side_effect=fake_picker), \
         patch("openpilot.starpilot.ui.power_compact.gui_app.get_active_widget") as active, \
         patch("openpilot.starpilot.ui.power_compact.gui_app.push_widget") as pushed:
      compact.open()
      page = pushed.call_args.args[0]
      active.return_value = page
      self.assertEqual(len(page._scroller.items), 4)
      page._scroller.items[1].click()
      dialog = pushed.call_args.args[0]
      dialog._callback(DialogResult.CONFIRM)
      self.assertTrue(read_saved(self.params).policy.enabled)
      self.assertTrue(page._scroller.items[1].description.startswith("custom limits."))
      page._scroller.items[2].click()
      picker = pushed.call_args.args[0]
      self.assertIn("6 h", picker.options)
      picker.selection = "6 h"
      picker._callback(DialogResult.CONFIRM)
      pushed.call_args.args[0]._callback(DialogResult.CONFIRM)
      self.assertEqual(read_saved(self.params).policy.delay_hours, 6)
      self.assertEqual(page._scroller.items[2].description.split(".")[0], "6 h")
      page._scroller.items[2].click()
      picker = pushed.call_args.args[0]
      picker.selection = "7 h"
      picker._callback(DialogResult.CONFIRM)
      abandoned = pushed.call_args.args[0]
      compact.open()  # A reopened page has a different identity.
      active.return_value = pushed.call_args.args[0]
      abandoned._callback(DialogResult.CONFIRM)
      self.assertEqual(read_saved(self.params).policy.delay_hours, 6)
