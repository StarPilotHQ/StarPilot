from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, RuntimeSnapshotAdapter, ui_fake
from openpilot.tools.replay.display_clock import ClockSample


class TestOnroadLeafScope(unittest.TestCase):
  def test_opt_in_preserves_consumed_state_and_skips_leaf_queries(self):
    full_ui, view_ui = ui_fake(), ui_fake()
    full = RuntimeSnapshotAdapter(full_ui)
    view = RuntimeSnapshotAdapter(view_ui, galaxy_access=SimpleNamespace(status=Mock(side_effect=AssertionError("unused Galaxy query"))))
    forbidden = {"DongleId", "HardwareSerial", "UpdaterState", "UpdaterFetchAvailable", "DisableUpdates",
                 "UpdaterTargetBranch", "OpenpilotEnabledToggle", "DisengageOnAccelerator", "IsLdwEnabled", "AlwaysOnDM", "RecordFront", "RecordAudio"}
    get, get_bool = view_ui.params.get, view_ui.params.get_bool
    def checked_get(key):
      self.assertNotIn(key, forbidden)
      return get(key)
    def checked_bool(key):
      self.assertNotIn(key, forbidden)
      return get_bool(key)
    view_ui.params.get, view_ui.params.get_bool = checked_get, checked_bool
    for frame in range(5):
      now = NOW + frame * 600_000_000
      for ui in (full_ui, view_ui):
        ui.sm.frame += 1
        ui.params.values["ShowCSCStatus"] = str(frame % 2)
      expected = full.build(ShellMode.ONROAD, now_ns=now)
      actual = view.build(ShellMode.ONROAD, now_ns=now, onroad_view_only=True)
      self.assertEqual(actual.onroad.developer_metrics, ())
      self.assertTrue(expected.onroad.developer_metrics)
      self.assertEqual(actual.onroad, replace(expected.onroad, developer_metrics=()))
      self.assertEqual(actual.home, expected.home)
      self.assertEqual(actual.settings, expected.settings)
      self.assertEqual(actual.device.offroad, expected.device.offroad)
      self.assertEqual(actual.selected, expected.selected)
    view.galaxy_access.status.assert_not_called()

  def test_narrow_cache_cannot_replace_default_full_snapshot(self):
    ui = ui_fake()
    ui.params.values["DongleId"] = "actual-device"
    adapter = RuntimeSnapshotAdapter(ui)
    narrow = adapter.build(ShellMode.ONROAD, now_ns=NOW, onroad_view_only=True)
    full = adapter.build(ShellMode.ONROAD, now_ns=NOW)
    self.assertNotEqual(narrow.device.dongle_id, full.device.dongle_id)
    self.assertEqual(full.device.dongle_id, "actual-device")
    self.assertEqual(narrow.onroad.developer_metrics, ())
    self.assertTrue(full.onroad.developer_metrics)
    self.assertEqual(narrow.onroad, replace(full.onroad, developer_metrics=()))
    live = adapter.build(ShellMode.ONROAD, now_ns=NOW + 1, onroad_view_only=True)
    self.assertEqual(live.onroad.observed_ns, NOW + 1)
    self.assertEqual(narrow.onroad.observed_ns, NOW)
    self.assertIsNot(live.onroad, narrow.onroad)
    self.assertFalse(narrow.device.available_actions)
    self.assertIsNotNone(adapter._onroad_ancillary)
    self.assertIsNotNone(adapter._onroad_view_ancillary)

  def test_settings_and_home_rebuild_immediately_after_onroad(self):
    ui = ui_fake()
    adapter = RuntimeSnapshotAdapter(ui)
    adapter.build(ShellMode.ONROAD, now_ns=NOW, onroad_view_only=True)
    ui.params.values.update(DongleId="new-device", UpdaterState="downloading", GitCommit="new-commit")
    settings = adapter.build(ShellMode.SETTINGS, Destination.DEVICE, now_ns=NOW + 1, onroad_view_only=True)
    self.assertEqual(settings.device.dongle_id, "new-device")
    self.assertEqual(settings.software.download_status, "downloading")
    self.assertIsNone(adapter._onroad_view_ancillary)
    home = adapter.build(ShellMode.HOME, now_ns=NOW + 2, onroad_view_only=True)
    self.assertEqual(home.home.commit, "new-commit")
    adapter.build(ShellMode.ONROAD, now_ns=NOW + 3, onroad_view_only=True)
    self.assertIsNotNone(adapter._onroad_view_ancillary)

  def test_onroad_key_change_recomputes_fresh_parked_evidence(self):
    ui = ui_fake()
    adapter = RuntimeSnapshotAdapter(ui)
    first = adapter.build(ShellMode.ONROAD, now_ns=NOW, onroad_view_only=True)
    cached = adapter._onroad_view_ancillary
    ui.started_frame += 1
    ui.sm.frame += 1
    second = adapter.build(ShellMode.ONROAD, now_ns=NOW + 1, onroad_view_only=True)
    self.assertIsNot(adapter._onroad_view_ancillary, cached)
    self.assertFalse(first.device.offroad)
    self.assertFalse(second.device.offroad)
    self.assertFalse(second.device.available_actions)

  def test_replay_epoch_reinitializes_narrow_cache_and_temporal_projectors(self):
    ui = ui_fake()
    ui.replay_clock = object()
    ui.replay_sample = ClockSample(NOW, None, NOW * 1000, epoch=1, valid=True)
    ui.sm.replay_sample = ui.replay_sample
    adapter = RuntimeSnapshotAdapter(ui)
    first = adapter.build(ShellMode.ONROAD, onroad_view_only=True)
    cache, projector = adapter._onroad_view_ancillary, adapter._conditional
    ui.replay_sample = ClockSample(NOW - 1_000_000_000, None, NOW * 1000, epoch=2, valid=True)
    ui.sm.replay_sample = ui.replay_sample
    second = adapter.build(ShellMode.ONROAD, onroad_view_only=True)
    self.assertIsNot(adapter._onroad_view_ancillary, cache)
    self.assertIsNot(adapter._conditional, projector)
    self.assertFalse(first.device.offroad)
    self.assertFalse(second.device.offroad)
    self.assertFalse(second.onroad.engaged)

  def test_full_onroad_cache_does_not_suppress_narrow_refresh_or_settings(self):
    ui = ui_fake()
    adapter = RuntimeSnapshotAdapter(ui)
    adapter.build(ShellMode.ONROAD, now_ns=NOW)
    adapter.build(ShellMode.ONROAD, now_ns=NOW + 1, onroad_view_only=True)
    ui.params.values["UpdaterState"] = "downloading"
    full = adapter.build(ShellMode.SETTINGS, Destination.SOFTWARE, now_ns=NOW + 2)
    self.assertEqual(full.software.download_status, "downloading")
    self.assertIsNone(adapter._onroad_view_ancillary)
    self.assertIsNone(adapter._onroad_ancillary)
