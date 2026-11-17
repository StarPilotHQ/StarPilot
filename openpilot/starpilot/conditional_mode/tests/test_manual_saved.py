"""Actual temporary Params and bounded saved-manual transaction tests."""

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.conditional_mode.manual_saved import (
  KEY, ManualSavedOwner, SavedCodes, decode, encode, read_codes,
)
from openpilot.starpilot.conditional_mode.policy import ManualIntent, ModeChoice
from openpilot.starpilot.conditional_mode.preferences import CEMOptions, CCMOptions, SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner, SettingsSnapshot
from openpilot.starpilot.conditional_mode import manual_saved


class ManualSavedTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.params = Params(self.directory.name)
    self.now = time.monotonic_ns()
    self.drive_id = self.now - 1_000_000_000
    self.settings = ConditionalSettingsOwner(self.params)

  def configure(self, choice: ModeChoice, *, persist: bool) -> SettingsSnapshot:
    preferences = SavedPreferences(mode=choice, cem=CEMOptions(persist_manual=persist), ccm=CCMOptions(persist_manual=persist))
    self.params.put('ConditionalModeConfig', json.loads(encode_preferences(preferences)), block=True)
    return self.settings.refresh(self.now)

  def owner(self, choice: ModeChoice, *, persist: bool) -> tuple[ManualSavedOwner, SettingsSnapshot]:
    snapshot = self.configure(choice, persist=persist)
    owner = ManualSavedOwner(self.params, self.settings)
    self.addCleanup(owner.close)
    return owner, snapshot

  def settle(self, owner: ManualSavedOwner, snapshot: SettingsSnapshot, *, advance: int = 0):
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
      result = owner.poll(snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now + advance)
      if result is not None:
        return result
      time.sleep(0.005)
    self.fail('manual saved worker did not finish')

  def test_strict_codec_absence_and_invalid_bytes_untouched(self):
    self.assertEqual(read_codes(self.params).codes, SavedCodes())
    self.assertEqual(decode(encode(SavedCodes(1, 2))), SavedCodes(1, 2))
    for raw in (b'', b'{}', b'{"version":1,"cem":true,"ccm":0}',
                b'{"version":1,"cem":3,"ccm":0}', b'{"version":1,"cem":1,"cem":2,"ccm":0}',
                b'{"version":2,"cem":0,"ccm":0}', b'{"version":1,"cem":0,"ccm":0,"extra":1}'):
      with self.subTest(raw=raw):
        Path(self.params.get_param_path(KEY)).write_bytes(raw)
        self.assertEqual(read_codes(self.params).status, 'invalid')
        self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), raw)

  def test_auto_zero_is_saved_and_retired_manual_choice_cannot_restore(self):
    self.params.put(KEY, json.loads(encode(SavedCodes(2, 1))), block=True)
    owner, snapshot = self.owner(ModeChoice.CEM, persist=True)
    start = owner.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    self.assertEqual((start.code, start.intent), (2, ManualIntent.FORCE_EXPERIMENTAL))
    self.assertTrue(owner.queue_code(0, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
    result = self.settle(owner, snapshot)
    self.assertEqual((result.status, result.committed), ('saved', True))
    self.assertEqual(read_codes(self.params).codes, SavedCodes(0, 1))
    owner.close()

    self.now += 2_000_000_000
    second_snapshot = self.configure(ModeChoice.CCM, persist=True)
    second = ManualSavedOwner(self.params, self.settings)
    self.addCleanup(second.close)
    restored = second.begin_drive(second_snapshot, choice=ModeChoice.CCM, drive_id=self.drive_id + 2_000_000_000,
                                  now_ns=self.now)
    self.assertEqual((restored.code, restored.intent), (None, None))
    self.assertEqual(second_snapshot.preferences.mode, ModeChoice.CEM)
    self.assertEqual(read_codes(self.params).codes, SavedCodes(0, 1))

  def test_optout_clears_selected_code_only(self):
    self.params.put(KEY, json.loads(encode(SavedCodes(1, 2))), block=True)
    owner, snapshot = self.owner(ModeChoice.CEM, persist=False)
    start = owner.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    self.assertEqual((start.code, start.intent), (0, ManualIntent.NONE))
    self.assertFalse(owner.queue_code(2, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
    self.assertEqual(self.settle(owner, snapshot).status, 'saved')
    self.assertEqual(read_codes(self.params).codes, SavedCodes(0, 2))

  def test_external_saved_or_config_edit_blocks_cas_without_clobber(self):
    for edited_key in (KEY, 'ConditionalModeConfig', 'SafeMode'):
      with self.subTest(key=edited_key):
        self.settings = ConditionalSettingsOwner(self.params)
        self.now += 2_000_000_000
        owner, snapshot = self.owner(ModeChoice.CEM, persist=True)
        owner.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
        self.assertTrue(owner.queue_code(1, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
        if edited_key == KEY:
          external = encode(SavedCodes(2, 2))
          Path(self.params.get_param_path(KEY)).write_bytes(external)
        elif edited_key == 'ConditionalModeConfig':
          external = b'{"version":1,"mode":"stock"}'
          Path(self.params.get_param_path('ConditionalModeConfig')).write_bytes(external)
        else:
          external = b'1'
          Path(self.params.get_param_path('SafeMode')).write_bytes(external)
        result = self.settle(owner, snapshot)
        self.assertEqual(result.status, 'external_edit')
        self.assertEqual(Path(self.params.get_param_path(edited_key)).read_bytes(), external)
        owner.close()

  def test_wrong_drive_revision_and_close_cancel(self):
    owner, snapshot = self.owner(ModeChoice.CEM, persist=True)
    owner.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    self.assertFalse(owner.queue_code(1, snapshot=snapshot, drive_id=self.drive_id + 1, now_ns=self.now))
    self.assertFalse(owner.queue_code(True, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
    self.assertTrue(owner.queue_code(1, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
    owner.close()
    self.assertIsNone(owner.poll(snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
    self.assertIsNone(read_codes(self.params).raw)

  def test_second_writer_lease_denied_and_queued_revision_reaches_disk(self):
    owner, snapshot = self.owner(ModeChoice.CEM, persist=True)
    owner.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    second = ManualSavedOwner(self.params, self.settings)
    self.addCleanup(second.close)
    blocked = second.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    self.assertEqual(blocked.status, 'active_elsewhere')
    self.assertTrue(owner.queue_code(1, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
    owner.poll(snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now)
    self.assertTrue(owner.queue_code(2, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
    self.assertEqual(self.settle(owner, snapshot).status, 'saved')
    self.assertEqual(self.settle(owner, snapshot).status, 'saved')
    self.assertEqual(read_codes(self.params).codes, SavedCodes(2, 0))

  def test_close_during_blocked_staging_prevents_rename(self):
    owner, snapshot = self.owner(ModeChoice.CEM, persist=True)
    owner.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    self.assertTrue(owner.queue_code(1, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
    entered = threading.Event()
    release = threading.Event()
    actual_fsync = manual_saved.os.fsync

    def blocked_fsync(fd):
      if not entered.is_set():
        entered.set()
        self.assertTrue(release.wait(1.0))
      return actual_fsync(fd)

    with patch.object(manual_saved.os, 'fsync', side_effect=blocked_fsync):
      owner.poll(snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now)
      self.assertTrue(entered.wait(1.0))
      before = time.monotonic()
      owner.close()
      self.assertLess(time.monotonic() - before, 0.1)
      release.set()
      owner.worker.join(timeout=1.0)
    self.assertIsNone(read_codes(self.params).raw)

  def test_close_after_unpolled_completed_worker_releases_lease(self):
    owner, snapshot = self.owner(ModeChoice.CEM, persist=True)
    owner.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    owner.queue_code(1, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now)
    owner.poll(snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now)
    assert owner.worker is not None
    owner.worker.join(timeout=1.0)
    self.assertFalse(owner.worker.is_alive())
    owner.close()
    second = ManualSavedOwner(self.params, self.settings)
    self.addCleanup(second.close)
    self.assertNotEqual(second.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id,
                                           now_ns=self.now).status, 'active_elsewhere')

  def test_native_params_edit_during_staging_wins_cas(self):
    owner, snapshot = self.owner(ModeChoice.CEM, persist=True)
    owner.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    owner.queue_code(1, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now)
    entered = threading.Event()
    release = threading.Event()
    actual_fsync = manual_saved.os.fsync

    def blocked_fsync(fd):
      if not entered.is_set():
        entered.set()
        self.assertTrue(release.wait(1.0))
      return actual_fsync(fd)

    with patch.object(manual_saved.os, 'fsync', side_effect=blocked_fsync):
      owner.poll(snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now)
      self.assertTrue(entered.wait(1.0))
      self.params.put(KEY, json.loads(encode(SavedCodes(2, 2))), block=True)
      release.set()
      assert owner.worker is not None
      owner.worker.join(timeout=1.0)
    self.assertEqual(owner.poll(snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now).status, 'external_edit')
    self.assertEqual(read_codes(self.params).codes, SavedCodes(2, 2))

  def test_same_source_refresh_allows_queue_and_staged_commit(self):
    owner, initial = self.owner(ModeChoice.CEM, persist=True)
    owner.begin_drive(initial, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    self.now += 1_100_000_000
    refreshed = self.settings.refresh(self.now)
    self.assertIsNot(refreshed, initial)
    self.assertEqual(refreshed.revision, initial.revision)
    self.assertTrue(owner.queue_code(1, snapshot=refreshed, drive_id=self.drive_id, now_ns=self.now))
    entered = threading.Event()
    release = threading.Event()
    actual_fsync = manual_saved.os.fsync

    def blocked_fsync(fd):
      if not entered.is_set():
        entered.set()
        self.assertTrue(release.wait(1.0))
      return actual_fsync(fd)

    with patch.object(manual_saved.os, 'fsync', side_effect=blocked_fsync):
      owner.poll(snapshot=refreshed, drive_id=self.drive_id, now_ns=self.now)
      self.assertTrue(entered.wait(1.0))
      self.now += 1_100_000_000
      again = self.settings.refresh(self.now)
      self.assertIsNot(again, refreshed)
      self.assertEqual(again.revision, initial.revision)
      release.set()
      assert owner.worker is not None
      owner.worker.join(timeout=1.0)
    self.assertEqual(owner.poll(snapshot=again, drive_id=self.drive_id, now_ns=self.now).status, 'saved')
    self.assertEqual(read_codes(self.params).codes, SavedCodes(1, 0))

  def test_invalid_saved_bytes_do_not_disable_nonpersistent_manual_start(self):
    raw = b'{"version":1,"cem":true,"ccm":0}'
    Path(self.params.get_param_path(KEY)).write_bytes(raw)
    owner, snapshot = self.owner(ModeChoice.CEM, persist=False)
    start = owner.begin_drive(snapshot, choice=ModeChoice.CEM, drive_id=self.drive_id, now_ns=self.now)
    self.assertEqual((start.status, start.intent, start.code), ('invalid', ManualIntent.NONE, 0))
    self.assertFalse(owner.queue_code(2, snapshot=snapshot, drive_id=self.drive_id, now_ns=self.now))
    self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), raw)


if __name__ == '__main__':
  unittest.main()
