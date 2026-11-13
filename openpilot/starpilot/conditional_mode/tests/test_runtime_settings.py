"""Actual temporary-Params receipts for the read-only conditional owner."""

from dataclasses import replace
import fcntl
import os
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import CEMOptions, SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.runtime_settings import (
  DOCUMENT_KEY, REFRESH_NS, SAFE_MODE_KEY, ConditionalSettingsOwner,
  DocumentState, SafeModeState, experimental_requested, read_mode_sources,
)


class ConditionalRuntimeSettingsTest(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.params = Params(self.directory.name)
    self.owner = ConditionalSettingsOwner(self.params)

  def path(self, key: str) -> Path:
    return Path(self.params.get_param_path(key))

  def saved(self, mode: ModeChoice = ModeChoice.CEM) -> SavedPreferences:
    return SavedPreferences(mode=mode, cem=replace(CEMOptions(), speed_mps=10.0))

  def test_mode_reader_excludes_inflight_pair_and_rejects_invalid_baseline(self):
    self.params.put_bool("ExperimentalMode", True, block=True)
    self.params.put_bool("SafeMode", False, block=True)
    cp = SimpleNamespace(openpilotLongitudinalControl=True, passive=False)
    first = self.owner.refresh(1_000_000_000)
    self.assertTrue(experimental_requested(first.experimental_raw, first.experimental_readable,
                                           first.safe_mode_raw, True, cp))
    lock = os.open(self.path(DOCUMENT_KEY).parent.parent / ".lock", os.O_RDONLY)
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      self.assertEqual(read_mode_sources(self.params), ((b"", False), (b"", False), (b"", False)))
      deferred = self.owner.refresh(1_100_000_000, force=True)
      self.assertIs(deferred, first)
      self.assertEqual(deferred.verified_mono_ns, 1_000_000_000)
      self.assertTrue(self.owner.affirm(deferred, now_mono_ns=1_100_000_000))
      expired = self.owner.refresh(2_000_000_001, force=True)
      self.assertIs(expired, first)
      self.assertFalse(self.owner.affirm(expired, now_mono_ns=2_000_000_001))
    finally:
      os.close(lock)
    recovered = self.owner.refresh(1_200_000_000, force=True)
    self.assertTrue(recovered.experimental_readable)
    self.path("ExperimentalMode").write_bytes(b"01")
    invalid = self.owner.refresh(1_300_000_000, force=True)
    self.assertGreater(invalid.revision, recovered.revision)
    self.assertFalse(experimental_requested(invalid.experimental_raw, invalid.experimental_readable,
                                            invalid.safe_mode_raw, True, cp))
    self.assertEqual(self.path("ExperimentalMode").read_bytes(), b"01")

  def test_legacy_params_without_path_api_fail_closed(self):
    self.assertEqual(read_mode_sources(SimpleNamespace()), ((b"", False), (b"", False), (b"", False)))

  def test_absent_is_known_factory_cem_and_safe_mode_false(self):
    snapshot = self.owner.refresh(1_000_000_000)
    self.assertIs(snapshot.document_state, DocumentState.ABSENT)
    self.assertIs(snapshot.safe_mode_state, SafeModeState.ABSENT_FALSE)
    self.assertIsNone(snapshot.document_raw)
    self.assertIsNone(snapshot.safe_mode_raw)
    self.assertEqual(snapshot.revision, 1)
    verdict = self.owner.verdict(snapshot, now_mono_ns=1_100_000_000, drive_id=7)
    self.assertEqual(verdict.status, 'ready')
    self.assertIs(verdict.selection.choice, ModeChoice.CEM)
    self.assertFalse(verdict.safe_mode)
    self.assertIsNone(self.params.get(DOCUMENT_KEY))
    self.assertIsNone(self.params.get(SAFE_MODE_KEY))

  def test_valid_document_immutable_revision_and_original_stamp(self):
    document = self.saved()
    self.params.put(DOCUMENT_KEY, json.loads(encode_preferences(document)), block=True)
    owner = self.owner
    first = owner.refresh(1_000_000_000)
    self.assertIs(first.document_state, DocumentState.VALID)
    self.assertEqual(first.preferences, document)
    self.assertEqual(owner.verdict(first, now_mono_ns=1_050_000_000, drive_id=9).selection.settings.cem_speed_mps, 10.0)
    with patch('openpilot.starpilot.conditional_mode.runtime_settings.read_saved') as saved_read:
      for i in range(20):
        self.assertTrue(owner.affirm(first, now_mono_ns=1_050_000_000 + i * 10_000_000))
        self.assertEqual(owner.verdict(first, now_mono_ns=1_050_000_000 + i * 10_000_000,
                                       drive_id=9).status, 'ready')
      saved_read.assert_not_called()
    same = owner.refresh(1_000_000_000 + REFRESH_NS)
    self.assertEqual(same.revision, first.revision)
    self.assertEqual(same.observed_mono_ns, first.observed_mono_ns)
    self.assertEqual(same.verified_mono_ns, 1_000_000_000 + REFRESH_NS)
    self.assertTrue(owner.affirm(first, now_mono_ns=2_100_000_000))
    self.assertFalse(owner.affirm(first, now_mono_ns=3_100_000_001))

  def test_periodic_refresh_keeps_revision_valid_between_thread_ticks(self):
    start = 1_000_000_000
    snapshot = self.owner.refresh(start)
    for elapsed in range(0, 3_000_000_000, 10_000_000):
      if elapsed % 110_000_000 == 0:
        self.owner.refresh(start + elapsed)
      self.assertTrue(self.owner.affirm(snapshot, now_mono_ns=start + elapsed), elapsed)
    self.assertEqual(self.owner.current.revision, snapshot.revision)
    self.assertFalse(self.owner.affirm(snapshot, now_mono_ns=self.owner.current.verified_mono_ns + REFRESH_NS + 1))

  def test_external_revision_and_safe_mode_are_independent(self):
    first = self.owner.refresh(1_000_000_000)
    self.params.put(DOCUMENT_KEY, json.loads(encode_preferences(self.saved())), block=True)
    self.params.put_bool(SAFE_MODE_KEY, True, block=True)
    changed = self.owner.refresh(2_000_000_000)
    self.assertEqual(changed.revision, 2)
    self.assertIs(changed.document_state, DocumentState.VALID)
    self.assertIs(changed.safe_mode_state, SafeModeState.TRUE)
    self.assertFalse(self.owner.affirm(first, now_mono_ns=2_000_000_000))
    verdict = self.owner.verdict(changed, now_mono_ns=2_050_000_000, drive_id=9)
    self.assertEqual(verdict.status, 'safe_mode')
    self.assertTrue(verdict.safe_mode)
    self.assertIsNone(verdict.selection)
    self.params.put_bool(SAFE_MODE_KEY, False, block=True)
    recovered = self.owner.refresh(3_000_000_000)
    self.assertEqual(recovered.revision, 3)
    self.assertIs(recovered.safe_mode_state, SafeModeState.FALSE)
    self.assertIs(self.owner.verdict(recovered, now_mono_ns=3_050_000_000, drive_id=9).selection.choice, ModeChoice.CEM)

  def test_corrupt_document_preserves_source_then_recovers(self):
    raw = b'{"version":1,"version":1}'
    self.path(DOCUMENT_KEY).write_bytes(raw)
    snapshot = self.owner.refresh(1_000_000_000)
    self.assertIs(snapshot.document_state, DocumentState.INVALID)
    self.assertEqual(snapshot.document_raw, raw)
    self.assertEqual(self.owner.verdict(snapshot, now_mono_ns=1_050_000_000, drive_id=1).status,
                     'unavailable_document')
    self.assertEqual(self.path(DOCUMENT_KEY).read_bytes(), raw)
    self.path(DOCUMENT_KEY).write_bytes(encode_preferences(self.saved(ModeChoice.CCM)))
    recovered = self.owner.refresh(2_000_000_000)
    self.assertEqual(recovered.revision, 2)
    self.assertEqual(self.owner.verdict(recovered, now_mono_ns=2_050_000_000, drive_id=1).selection.choice,
                     ModeChoice.CEM)
    self.assertEqual(self.path(DOCUMENT_KEY).read_bytes(), encode_preferences(self.saved(ModeChoice.CCM)))

  def test_read_error_is_distinct_from_absence_and_stops_old_affirmation(self):
    first = self.owner.refresh(1_000_000_000)
    self.path(DOCUMENT_KEY).mkdir()
    failed = self.owner.refresh(2_000_000_000)
    self.assertIs(failed.document_state, DocumentState.READ_ERROR)
    self.assertEqual(self.owner.verdict(failed, now_mono_ns=2_050_000_000, drive_id=1).status, 'read_error')
    self.assertFalse(self.owner.affirm(first, now_mono_ns=2_050_000_000))
    self.path(DOCUMENT_KEY).rmdir()
    recovered = self.owner.refresh(3_000_000_000)
    self.assertIs(recovered.document_state, DocumentState.ABSENT)
    self.assertEqual(recovered.revision, 3)

  def test_safe_mode_raw_is_strict_and_unreadable_is_not_false(self):
    self.path(SAFE_MODE_KEY).write_bytes(b'true')
    bad = self.owner.refresh(1_000_000_000)
    self.assertIs(bad.safe_mode_state, SafeModeState.INVALID)
    self.assertIsNone(self.owner.verdict(bad, now_mono_ns=1_010_000_000, drive_id=1).safe_mode)
    self.assertEqual(self.owner.verdict(bad, now_mono_ns=1_010_000_000, drive_id=1).status,
                     'invalid_safe_mode')
    self.path(SAFE_MODE_KEY).unlink()
    self.path(SAFE_MODE_KEY).mkdir()
    failed = self.owner.refresh(2_000_000_000)
    self.assertIs(failed.safe_mode_state, SafeModeState.READ_ERROR)
    self.assertEqual(self.owner.verdict(failed, now_mono_ns=2_010_000_000, drive_id=1).status, 'read_error')
    self.path(SAFE_MODE_KEY).rmdir()
    restored = self.owner.refresh(3_000_000_000)
    self.assertIs(restored.safe_mode_state, SafeModeState.ABSENT_FALSE)
    self.assertEqual(self.owner.verdict(restored, now_mono_ns=3_010_000_000, drive_id=1).status, 'ready')

  def test_document_failure_remains_visible_when_safe_mode_is_true(self):
    raw = b'{bad json'
    self.path(DOCUMENT_KEY).write_bytes(raw)
    self.params.put_bool(SAFE_MODE_KEY, True, block=True)
    snapshot = self.owner.refresh(1_000_000_000)
    verdict = self.owner.verdict(snapshot, now_mono_ns=1_010_000_000, drive_id=1)
    self.assertEqual(verdict.status, 'unavailable_document')
    self.assertTrue(verdict.safe_mode)
    self.assertIsNone(verdict.selection)
    self.assertEqual(self.path(DOCUMENT_KEY).read_bytes(), raw)

  def test_foreign_owner_old_revision_clock_rollback_and_rate_limit(self):
    first = self.owner.refresh(1_000_000_000)
    other = ConditionalSettingsOwner(self.params)
    other.refresh(1_000_000_000)
    self.assertFalse(other.affirm(first, now_mono_ns=1_010_000_000))
    self.path(DOCUMENT_KEY).write_bytes(encode_preferences(self.saved()))
    not_due = self.owner.refresh(1_250_000_000)
    self.assertIs(not_due, first)
    self.assertIs(self.owner.verdict(first, now_mono_ns=1_250_000_000, drive_id=1).selection.choice,
                  ModeChoice.CEM)
    changed = self.owner.refresh(2_000_000_000)
    self.assertIs(changed.document_state, DocumentState.VALID)
    self.assertFalse(self.owner.affirm(first, now_mono_ns=2_000_000_000))
    rollback = self.owner.refresh(1_999_999_999)
    self.assertIs(rollback.document_state, DocumentState.READ_ERROR)
    self.assertFalse(self.owner.affirm(changed, now_mono_ns=2_000_000_000))


if __name__ == '__main__':
  unittest.main()
