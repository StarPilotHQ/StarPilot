"""Actual temporary Params and native owner requests for Lane Change Assist."""

from pathlib import Path
from types import SimpleNamespace
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR as HYUNDAI
from openpilot.common.params import Params
from openpilot.starpilot.lateral.lane_change_preferences import KEY, LaneChangePolicy, decode, effective, read_saved, to_value
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, FeatureSettingsRequest, row_change, row_default
from openpilot.starpilot.ui.lane_change_feature import AUTO, CLOSE, DELAY, ENABLED, GAP, ONE, PACE, REQUIRE_CRUISE, RESET, SPEED, WIDTH
from openpilot.starpilot.ui import feature_settings_compact as compact
from openpilot.starpilot import saved_document


class LaneChangeFeatureTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    self.parked = True
    self.cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    self.cp.carVin = "VIN1"
    self.cp.openpilotLongitudinalControl = True
    self.fingerprint = str(self.cp.carFingerprint)
    self.owner = FeatureSettingsOwner(self.params, lambda group: self.parked and group in ("lane_change", "parked_preferences"),
                                      vehicle_fingerprint=lambda: self.fingerprint, vehicle_params=lambda: self.cp)

  def test_cruise_requirement_is_optional_saved_and_resettable_for_stock_and_openpilot_long(self):
    for openpilot_long in (False, True):
      self.cp.openpilotLongitudinalControl = openpilot_long
      row = self.row(REQUIRE_CRUISE)
      self.assertTrue(row.available)
      self.assertEqual(row.default_value, "Off")
      self.assertTrue(self.owner.apply(self.request(row)))
      self.assertTrue(read_saved(self.params).policy.auto_requires_cruise)
      reset = row_default(self.row(REQUIRE_CRUISE))
      self.assertTrue(self.owner.apply(reset))
      self.assertFalse(read_saved(self.params).policy.auto_requires_cruise)

  def test_default_resets_one_lane_field_without_resetting_other_choices(self):
    row = self.row(AUTO)
    self.assertTrue(self.owner.apply(self.request(row)))
    speed = self.row(SPEED)
    self.assertEqual(float(speed.default_value), round(LaneChangePolicy().minimum_speed_mps * 2.2369362920544, 3))
    request = row_default(speed)
    assert request is not None
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(self.row(AUTO).value, "On")

  def test_saved_lane_choice_can_be_configured_during_drive_with_current_vehicle(self):
    self.parked = False
    self.owner = FeatureSettingsOwner(self.params, lambda group: group == "lane_change",
                                      vehicle_fingerprint=lambda: self.fingerprint, vehicle_params=lambda: self.cp)
    row = self.row(ONE)
    self.assertTrue(row.available)
    request = self.request(row)
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(read_saved(self.params).policy.one_per_signal)
    self.assertFalse(self.owner.apply(request))
    other = self.request(self.row(ENABLED), -1)
    self.cp.carVin = "CHANGED"
    self.assertFalse(self.owner.apply(other))

  def test_onroad_lane_repair_is_unavailable_with_edit_authority(self):
    Path(self.params.get_param_path(KEY)).write_bytes(b"{bad")
    self.parked = False
    self.owner = FeatureSettingsOwner(self.params, lambda group: group == "lane_change",
                                      vehicle_fingerprint=lambda: self.fingerprint, vehicle_params=lambda: self.cp)
    reset = self.row(RESET)
    self.assertFalse(reset.available)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(reset.key, reset.source, "confirm", confirmation=True,
                                                             vehicle_fingerprint=reset.vehicle_fingerprint,
                                                             capability=reset.capability)))
    self.assertEqual(read_saved(self.params).raw, b"{bad")

  def test_lane_repair_rechecks_park_before_replacement(self):
    path = Path(self.params.get_param_path(KEY))
    path.write_bytes(b"{bad")
    reset = self.row(RESET)
    request = FeatureSettingsRequest(reset.key, reset.source, "confirm", confirmation=True,
                                     vehicle_fingerprint=reset.vehicle_fingerprint, capability=reset.capability)
    actual_fsync = os.fsync

    def revoke(fd):
      actual_fsync(fd)
      self.parked = False

    with patch.object(saved_document.os, "fsync", side_effect=revoke):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(path.read_bytes(), b"{bad")

  def row(self, key):
    state = self.owner.snapshot(FeaturePage.LANE_CHANGE, parked=self.parked, system_long=False,
                                lateral_context=True, metric=False)
    return next(row for row in state.rows if row.key == key)

  def request(self, row, direction=1):
    request = row_change(row, direction)
    self.assertIsNotNone(request)
    return FeatureSettingsRequest(request.key, request.expected, request.value, confirmation=True,
                                  vehicle_fingerprint=request.vehicle_fingerprint, capability=request.capability,
                                  dependencies=request.dependencies, display_unit=request.display_unit)

  def test_legacy_zero_display_and_speed_floor_preserve_raw_document(self):
    raw = json.dumps(to_value(LaneChangePolicy(minimum_speed_mps=0.))).encode()
    Path(self.params.get_param_path(KEY)).write_bytes(raw)
    speed = self.row(SPEED)
    self.assertEqual(speed.value, "5.0")
    self.assertAlmostEqual(speed.minimum, 5.)
    self.assertEqual(read_saved(self.params).raw, raw)
    invalid = FeatureSettingsRequest(speed.key, speed.source, "4", confirmation=True,
      vehicle_fingerprint=speed.vehicle_fingerprint, capability=speed.capability,
      dependencies=speed.dependencies, display_unit=speed.display_unit)
    self.assertFalse(self.owner.apply(invalid))
    self.assertEqual(read_saved(self.params).raw, raw)
    self.params.put_bool("IsMetric", True, block=True)
    snapshot = self.owner.snapshot(FeaturePage.LANE_CHANGE, parked=self.parked, system_long=False,
                                   lateral_context=True, metric=True)
    metric = next(row for row in snapshot.rows if row.key == SPEED)
    self.assertEqual(metric.value, "8.047")
    self.assertAlmostEqual(metric.minimum, 5 * .44704 * 3.6)
    self.assertEqual(read_saved(self.params).raw, raw)

  def test_absent_document_stock_and_atomic_edits(self):
    self.assertIsNone(read_saved(self.params).raw)
    self.assertEqual(effective(read_saved(self.params)), LaneChangePolicy())
    self.assertIn(FeaturePage.LANE_CHANGE, [row.page for row in self.owner.snapshot(
      FeaturePage.HUB, parked=True, system_long=False, lateral_context=True, metric=False).rows])
    speed = self.row(SPEED)
    self.assertEqual(speed.unit, "mph")
    self.assertEqual(speed.value, "5.0")
    unconfirmed = row_change(speed)
    self.assertIsNotNone(unconfirmed)
    assert unconfirmed is not None
    self.assertFalse(self.owner.apply(unconfirmed))
    self.assertTrue(self.owner.apply(self.request(speed)))
    self.assertEqual(round(read_saved(self.params).policy.minimum_speed_mps, 5), 6 * 0.44704)
    self.assertTrue(self.owner.apply(self.request(self.row(ONE))))
    self.assertFalse(read_saved(self.params).policy.one_per_signal)
    self.assertTrue(self.owner.apply(self.request(self.row(ENABLED), -1)))
    self.assertFalse(read_saved(self.params).policy.enabled)

  def test_one_lane_change_speed_control_owns_the_smoothing_duration(self):
    row = self.row(PACE)
    self.assertEqual(row.label, 'Lane Change Speed')
    self.assertEqual(float(row.value), 5.)
    self.assertEqual((row.minimum, row.maximum), (1., 10.))
    self.assertTrue(self.owner.apply(self.request(row)))
    self.assertAlmostEqual(read_saved(self.params).policy.duration_s, 8. - 5. * 5. / 9.)
    self.assertEqual(float(self.row(PACE).value), 6.)
    self.assertEqual(read_saved(self.params).policy.auto_delay_s, 1.)

  def test_metric_conversion_and_unit_source_guard(self):
    self.params.put_bool("IsMetric", True, block=True)
    row = self.row(SPEED)
    self.assertEqual(row.unit, "km/h")
    stale = self.request(row)
    self.params.put_bool("IsMetric", False, block=True)
    self.assertFalse(self.owner.apply(stale))
    self.assertIsNone(read_saved(self.params).raw)
    self.params.put_bool("IsMetric", True, block=True)
    self.assertTrue(self.owner.apply(self.request(self.row(SPEED))))
    self.assertAlmostEqual(read_saved(self.params).policy.minimum_speed_mps,
                           (round(LaneChangePolicy().minimum_speed_mps * 3.6, 3) + 1) / 3.6, places=6)
    Path(self.params.get_param_path("IsMetric")).write_bytes(b"invalid")
    self.assertFalse(self.row(SPEED).available)

  def test_close_gap_requires_system_long_and_confirmed_current_source(self):
    self.assertEqual(self.row(CLOSE).value, "Off")
    self.assertEqual(self.row(GAP).value, "0.75")
    stale = self.request(self.row(CLOSE))
    self.cp.openpilotLongitudinalControl = False
    self.assertFalse(self.owner.apply(stale))
    self.assertFalse(self.row(CLOSE).available)
    self.cp.openpilotLongitudinalControl = True
    self.assertTrue(self.owner.apply(self.request(self.row(CLOSE))))
    self.assertTrue(read_saved(self.params).policy.close_gap)
    self.assertTrue(self.owner.apply(self.request(self.row(GAP))))
    self.assertAlmostEqual(read_saved(self.params).policy.close_gap_seconds, 0.8)
    self.assertFalse(self.owner.apply(stale))

  def test_auto_saved_v3_and_fresh_parked_confirmation(self):
    self.assertEqual(self.row(AUTO).value, "Off")
    auto = self.request(self.row(AUTO))
    self.cp.carVin = "CHANGED"
    self.assertFalse(self.owner.apply(auto))
    self.cp.carVin = "VIN1"
    self.parked = False
    self.assertFalse(self.owner.apply(auto))
    self.parked = True
    self.assertTrue(self.owner.apply(auto))
    self.assertTrue(read_saved(self.params).policy.auto_lane_change)
    self.assertEqual(to_value(read_saved(self.params).policy)["version"], 6)
    self.assertTrue(self.owner.apply(self.request(self.row(DELAY))))
    self.assertEqual(read_saved(self.params).policy.auto_delay_s, 1.1)
    self.assertTrue(self.owner.apply(self.request(self.row(WIDTH))))
    self.assertAlmostEqual(read_saved(self.params).policy.minimum_lane_width_m,
                           0.1 * 0.3048)

  def test_staged_save_rejects_concurrent_source_change(self):
    request = self.request(self.row(ONE))
    path = Path(self.params.get_param_path(KEY))
    concurrent = json.dumps(to_value(LaneChangePolicy(enabled=False))).encode()
    actual_fsync = os.fsync
    calls = 0

    def stage_fsync(fd):
      nonlocal calls
      actual_fsync(fd)
      calls += 1
      if calls == 1:
        path.write_bytes(concurrent)

    with patch.object(saved_document.os, "fsync", side_effect=stage_fsync):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(path.read_bytes(), concurrent)

  def test_staged_save_rejects_revoked_parked_and_units(self):
    actual_fsync = os.fsync
    for key, revoke in ((ONE, lambda: setattr(self, "parked", False)),
                        (SPEED, lambda: Path(self.params.get_param_path("IsMetric")).write_bytes(b"1"))):
      with self.subTest(key=key):
        self.parked = True
        self.params.put_bool("IsMetric", False, block=True)
        request = self.request(self.row(key))
        calls = 0

        def stage_fsync(fd, revoke=revoke):
          nonlocal calls
          actual_fsync(fd)
          calls += 1
          if calls == 1:
            revoke()

        with patch.object(saved_document.os, "fsync", side_effect=stage_fsync):
          self.assertFalse(self.owner.apply(request))
        self.assertIsNone(read_saved(self.params).raw)

  def test_unverified_readback_never_reports_success_or_retries(self):
    request = self.request(self.row(ONE))
    path = Path(self.params.get_param_path(KEY))
    actual_read = saved_document.read_saved
    calls = 0

    def unverified(*args):
      nonlocal calls
      calls += 1
      if calls == 3:
        return b"unverified", True
      return actual_read(*args)

    with patch.object(saved_document, "read_saved", side_effect=unverified):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(calls, 3)
    self.assertFalse(read_saved(self.params).policy.one_per_signal)
    self.assertEqual(decode(path.read_bytes()), read_saved(self.params).policy)

  def test_runtime_reads_saved_policy_next_session(self):
    from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
    self.assertTrue(self.owner.apply(self.request(self.row(ONE))))
    with patch.object(self.params, "get_param_path", wraps=self.params.get_param_path) as source:
      helper = DesireHelper(effective(read_saved(self.params)))
      self.assertEqual(source.call_count, 1)
      helper.update(SimpleNamespace(vEgo=0.0, leftBlinker=False, rightBlinker=False), False, 1.0)
      self.assertEqual(source.call_count, 1)
    self.assertFalse(helper.policy.one_per_signal)
    self.assertEqual(helper.policy.minimum_speed_mps, LaneChangePolicy().minimum_speed_mps)

  def test_fifo_and_symlink_sources_are_unavailable_without_repair(self):
    path = Path(self.params.get_param_path(KEY))
    os.mkfifo(path)
    self.assertFalse(read_saved(self.params).readable)
    self.assertFalse(self.row(RESET).available)
    path.unlink()
    target = Path(self.temp.name) / "outside"
    target.write_bytes(b"{broken")
    path.symlink_to(target)
    self.assertFalse(read_saved(self.params).readable)
    self.assertFalse(self.row(RESET).available)
    self.assertEqual(target.read_bytes(), b"{broken")

  def test_corrupt_reset_requires_confirmation_and_fresh_sources(self):
    path = Path(self.params.get_param_path(KEY))
    path.write_bytes(b"{bad")
    self.assertEqual(effective(read_saved(self.params)), LaneChangePolicy())
    reset = self.row(RESET)
    self.assertTrue(reset.available)
    request = FeatureSettingsRequest(reset.key, reset.source, "confirm", confirmation=True,
                                     vehicle_fingerprint=reset.vehicle_fingerprint, capability=reset.capability)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(reset.key, reset.source, "confirm",
                                                             vehicle_fingerprint=reset.vehicle_fingerprint, capability=reset.capability)))
    path.write_bytes(b"{other")
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(path.read_bytes(), b"{other")
    reset = self.row(RESET)
    request = FeatureSettingsRequest(reset.key, reset.source, "confirm", confirmation=True,
                                     vehicle_fingerprint=reset.vehicle_fingerprint, capability=reset.capability)
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    self.cp.carVin = "VIN2"
    self.assertFalse(self.owner.apply(request))
    self.cp.carVin = "VIN1"
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(decode(path.read_bytes()), LaneChangePolicy())

  def test_invalid_document_and_oversize_fail_closed(self):
    for raw in (b'{"version":1,"enabled":true,"minimumSpeedMps":NaN,"onePerSignal":false}',
                b'{"version":1,"enabled":true,"minimumSpeedMps":10,"onePerSignal":false,"enabled":false}',
                b'{"version":1,"enabled":true,"minimumSpeedMps":' + b'9' * 350 + b',"onePerSignal":false}',
                b"[" * 200 + b"0" + b"]" * 200, b"[" * 510 + b"]" * 510, b"x" * 513):
      with self.subTest(raw=raw[:20]):
        path = Path(self.params.get_param_path(KEY))
        path.write_bytes(raw)
        self.assertEqual(effective(read_saved(self.params)), LaneChangePolicy())
        self.assertIsNone(decode(raw))
        if len(raw) > 512:
          self.assertFalse(self.row(RESET).available)
    self.assertEqual(to_value(LaneChangePolicy())["version"], 6)
    self.assertFalse(decode(b'{"version":1,"enabled":true,"minimumSpeedMps":8.9408,"onePerSignal":false}').auto_lane_change)
    with self.assertRaises(ValueError):
      to_value(LaneChangePolicy(minimum_speed_mps=int("9" * 350)))

  def test_large_native_confirmation_and_page_cancel(self):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    from openpilot.starpilot.ui.settings_state import Destination
    from openpilot.starpilot.ui.shell import ShellMode
    from openpilot.system.ui.widgets import DialogResult
    session = StarShellSession.__new__(StarShellSession)
    session._mode = ShellMode.SETTINGS
    session.selected = Destination.DRIVING_CONTROLS
    session.feature_page = FeaturePage.LANE_CHANGE
    session._lane_change_request_epoch = 0
    request = self.request(self.row(ONE))
    dialogs = []
    with patch.object(StarShellSession, "feature_request", side_effect=self.owner.apply), \
         patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog",
               side_effect=lambda *args, **kwargs: SimpleNamespace(callback=kwargs["callback"])), \
         patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget", dialogs.append):
      session._confirm_lane_change(request)
      dialogs[-1].callback(DialogResult.CANCEL)
      self.assertIsNone(read_saved(self.params).raw)
      session._confirm_lane_change(request)
      session._lane_change_request_epoch += 1
      dialogs[-1].callback(DialogResult.CONFIRM)
      self.assertIsNone(read_saved(self.params).raw)
      session._confirm_lane_change(request)
      dialogs[-1].callback(DialogResult.CONFIRM)
      self.assertFalse(read_saved(self.params).policy.one_per_signal)
      session._confirm_lane_change(self.request(self.row(AUTO)))
      dialogs[-1].callback(DialogResult.CONFIRM)
      self.assertTrue(read_saved(self.params).policy.auto_lane_change)

  def test_compact_native_confirmation_rejects_abandoned_page(self):
    class Session:
      def __init__(self, owner):
        self.owner = owner
      def feature_snapshot(self, page):
        return self.owner.snapshot(page, parked=True, system_long=False, lateral_context=True, metric=False)
      def feature_request(self, request):
        return self.owner.apply(request)
    adapter = compact.FeatureSettingsCompact(Session(self.owner))
    page = compact.NavScroller.__new__(compact.NavScroller)
    active = [page]
    dialogs = []
    confirmations = []
    dialog = Mock()
    dialog.dismiss.side_effect = lambda callback: callback()
    request = self.request(self.row(ENABLED), -1)
    with patch.object(compact.gui_app, "get_active_widget", side_effect=lambda: active[0]), \
         patch.object(compact.gui_app, "push_widget", dialogs.append), \
         patch.object(compact.gui_app, "texture"), patch.object(compact, "SettingDescriptionDialog", return_value=dialog), \
         patch.object(compact, "BigConfirmationCircleButton", side_effect=lambda _title, _icon, callback: confirmations.append(callback)):
      adapter._confirm(request, compact.lane_change_question(request), lambda: None, page)
      self.assertIsNone(read_saved(self.params).raw)
      active[0] = object()
      confirmations[-1]()
      self.assertIsNone(read_saved(self.params).raw)
      active[0] = page
      adapter._confirm(request, compact.lane_change_question(request), lambda: None, page)
      confirmations[-1]()
      self.assertFalse(read_saved(self.params).policy.enabled)
      request = self.request(self.row(AUTO))
      adapter._confirm(request, compact.lane_change_question(request), lambda: None, page)
      confirmations[-1]()
      self.assertTrue(read_saved(self.params).policy.auto_lane_change)

  def test_compact_parent_child_and_confirmed_edit(self):
    class Button:
      def __init__(self, text, value):
        self.text, self.value, self.click = text, value, None
      def set_click_callback(self, callback):
        self.click = callback
      def set_enabled(self, enabled):
        self.enabled = enabled
    class Scroller:
      def __init__(self, *_args):
        self.items = []
        self._scroller = self
      def add_widgets(self, widgets):
        self.items.extend(widgets)
      def dismiss(self, callback):
        callback()
    class Session:
      def __init__(self, owner):
        self.owner = owner
      def feature_snapshot(self, page):
        return self.owner.snapshot(page, parked=True, system_long=False, lateral_context=True, metric=False)
      def feature_request(self, request):
        return self.owner.apply(request)
    stack = []
    with patch.object(compact, "BigButton", Button), patch.object(compact, "GreyBigButton", Button), \
         patch.object(compact, "NavScroller", Scroller), \
         patch.object(compact, "SettingDescriptionDialog", Scroller), patch.object(compact.gui_app, "texture"), \
         patch.object(compact, "BigConfirmationCircleButton", side_effect=lambda _title, _icon, callback: SimpleNamespace(callback=callback)), \
         patch.object(compact.gui_app, "push_widget", stack.append), \
         patch.object(compact.gui_app, "get_active_widget", side_effect=lambda: stack[-1]):
      adapter = compact.FeatureSettingsCompact(Session(self.owner))
      adapter.open(FeaturePage.HUB)
      next(card for card in stack[-1].items if card.text == "lane changes").click()
      child = stack[-1]
      self.assertTrue(any(card.text == "one change per signal" for card in child.items))
      next(card for card in child.items if card.text == "one change per signal").click()
      dialog = stack.pop()
      dialog.items[-1].callback()
      self.assertFalse(read_saved(self.params).policy.one_per_signal)
      self.assertTrue(any(card.text == "one change per signal" and card.value.startswith("Off") for card in child.items),
                      repr([(card.text, card.value) for card in child.items]))


if __name__ == "__main__":
  unittest.main()
