"""Snapshot source reuse with real bounded filesystem reads; actions stay live."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR as HONDA
from openpilot.starpilot.lateral.learning_preferences import KEY as LEARNING_KEY, replace_preference
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


class Files:
  def __init__(self, directory):
    self.directory = Path(directory)
    self.reads = []

  def get_param_path(self, key):
    self.reads.append(key)
    return str(self.directory / key)

  def get_default_value(self, key):
    return 0


class FeatureSnapshotReadTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.params = Files(self.directory.name)
    self.owner = FeatureSettingsOwner(self.params, lambda group: False, vehicle_fingerprint=lambda: None)

  def snapshot(self, page="torque"):
    return self.owner.snapshot(page, parked=False, system_long=False, lateral_context=False, metric=False)

  def test_normal_and_developer_pages_read_only_their_sources_once_per_assembly(self):
    for page, expected in (('torque', {'AutomaticTurnInitiation'}), ('torque/developer', {'TorqueOverrideDocument'})):
      with self.subTest(page=page):
        self.params.reads.clear()
        first = self.snapshot(page)
        self.assertEqual(set(self.params.reads), expected)
        self.assertEqual(len(self.params.reads), len(expected))
        self.params.reads.clear()
        self.assertEqual(self.snapshot(page), first)
        self.assertEqual(set(self.params.reads), expected)
        self.assertEqual(len(self.params.reads), len(expected))

  def test_learning_row_reads_scoped_and_legacy_sources_once(self):
    cp = interfaces[HONDA.HONDA_FIT_4G].get_non_essential_params(HONDA.HONDA_FIT_4G)
    self.owner.vehicle_params = lambda: cp
    expected = {"LateralControllerSelection", "ForceAutoTuneOff", LEARNING_KEY}
    (self.params.directory / "ForceAutoTuneOff").write_bytes(b"0")
    scoped = self.params.directory / LEARNING_KEY
    scoped.write_bytes(replace_preference(None, cp, False))
    row = self.owner.controller.learning_row()
    assert row is not None
    self.assertEqual(row.value, "Off")
    self.assertEqual(set(self.params.reads), expected)
    self.assertEqual(len(self.params.reads), len(expected))
    self.params.reads.clear()
    scoped.unlink()
    legacy = self.owner.controller.learning_row()
    assert legacy is not None
    self.assertEqual(legacy.value, "On")
    self.assertEqual(set(self.params.reads), expected)
    self.assertEqual(len(self.params.reads), len(expected))

  def test_next_assembly_observes_replacement_and_unreadable_source(self):
    source = self.params.directory / "AlwaysOnLateral"
    source.write_bytes(b"0")
    first = self.snapshot("aol")
    source.write_bytes(b"1")
    second = self.snapshot("aol")
    self.assertNotEqual(first.rows, second.rows)
    source.write_bytes(b"x" * 129)
    invalid = self.snapshot("aol")
    master = next(row for row in invalid.rows if row.key == "AlwaysOnLateral")
    self.assertFalse(master.available)
    self.assertEqual(master.reason, "Invalid saved master preference")
    self.assertEqual(master.source, b"")
    source.write_bytes(b"0")
    self.assertEqual(self.snapshot("aol"), first)

  def test_exception_discards_assembly_reads(self):
    def broken(*args, **kwargs):
      self.owner._raw("AlwaysOnLateral")
      raise RuntimeError("assembly failed")
    with patch.object(self.owner, "_build_snapshot", side_effect=broken):
      with self.assertRaises(RuntimeError):
        self.snapshot()
    self.assertIsNone(self.owner._snapshot_reads.get())
    (self.params.directory / "AlwaysOnLateral").write_bytes(b"1")
    self.assertEqual(self.owner._raw("AlwaysOnLateral"), b"1")

  def test_reentrant_action_bypasses_snapshot_sources(self):
    source = self.params.directory / "AlwaysOnLateral"
    source.write_bytes(b"0")
    def assembly(*args, **kwargs):
      self.assertEqual(self.owner._raw("AlwaysOnLateral"), b"0")
      source.write_bytes(b"1")
      self.assertEqual(self.owner.apply(FeatureSettingsRequest("AlwaysOnLateral", b"0", "On")), b"1")
      self.assertEqual(self.owner._raw("AlwaysOnLateral"), b"0")
    with patch.object(self.owner, "_build_snapshot", side_effect=assembly), \
         patch.object(self.owner, "_apply", side_effect=lambda request, *, reset_default=False: self.owner._raw(request.key)):
      self.snapshot()
    self.assertEqual(self.owner._raw("AlwaysOnLateral"), b"1")

  def test_contexts_do_not_share_an_active_assembly(self):
    barrier = Barrier(2)
    def assembly(*args, **kwargs):
      reads = self.owner._snapshot_reads.get()
      barrier.wait(timeout=2)
      self.owner._raw("AlwaysOnLateral")
      return reads
    with patch.object(self.owner, "_build_snapshot", side_effect=assembly), ThreadPoolExecutor(2) as pool:
      futures = [pool.submit(self.snapshot) for _ in range(2)]
      first, second = [future.result() for future in futures]
    self.assertIsNot(first, second)
    self.assertIsNone(self.owner._snapshot_reads.get())


if __name__ == "__main__":
  unittest.main()
