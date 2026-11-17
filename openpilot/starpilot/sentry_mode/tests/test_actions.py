"""Exact-source saved motion edits use disposable native Params only."""

import fcntl
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from openpilot.common.params import Params
from openpilot.starpilot.sentry_mode.actions import commit
from openpilot.starpilot.sentry_mode.preferences import KEY, Preferences, decode, encode
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.sentry_owner import ENABLED, RESET, SENSITIVITY, WARNING, SentryOwner


class SentryActionsTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.path = Path(self.params.get_param_path(KEY))

  def test_absent_defaults_and_confirmed_edit(self):
    owner = SentryOwner(self.params, lambda: True)
    Path(self.params.get_param_path("SentryModeEnabled")).write_bytes(b"1")
    state = owner.snapshot()
    self.assertFalse(self.path.exists())
    self.assertIn("Off: Motion monitoring is disabled", state.subtitle)
    self.assertEqual([row.value for row in state.rows], ["Off", "0.040", "1.0"])
    self.assertFalse(owner.apply(FeatureSettingsRequest(ENABLED, None, "On")))
    self.assertFalse(self.path.exists())
    self.assertTrue(owner.apply(FeatureSettingsRequest(ENABLED, None, "On", confirmation=True)))
    self.assertTrue(owner.last_write.committed and owner.last_write.verified)
    self.assertIn(b'"enabled":true', self.path.read_bytes())

  def test_live_status_does_not_change_edit_sources_and_parked_loss_hides_armed(self):
    self.path.write_bytes(encode(Preferences(True)))
    status = mock.Mock()
    status.runtime_snapshot.return_value = ("Arming · 82s", "Fresh evidence")
    owner = SentryOwner(self.params, lambda: True, status=status)
    before = owner.snapshot()
    saved = status.runtime_snapshot.call_args.args[0]
    self.assertTrue(saved.valid and saved.preferences.enabled)
    self.assertEqual(saved.raw, self.path.read_bytes())
    status.runtime_snapshot.return_value = ("Arming · 81s", "Fresh evidence")
    after = owner.snapshot()
    self.assertEqual(before.rows, after.rows)
    self.assertNotEqual(before.subtitle, after.subtitle)
    status.runtime_snapshot.return_value = ("Monitoring motion", "Fresh evidence")
    denied = SentryOwner(self.params, lambda: False, status=status).snapshot()
    self.assertIn("Not armed", denied.subtitle)
    self.assertNotIn("Monitoring motion", denied.subtitle)
    self.assertFalse(any(row.available for row in denied.rows))
    status.runtime_snapshot.return_value = ("Waiting for monitor", "The monitor has not responded recently")
    onroad = SentryOwner(self.params, lambda: False, status=status).snapshot()
    self.assertIn("Not armed: The car is on", onroad.subtitle)
    parked = SentryOwner(self.params, lambda: True, status=status).snapshot()
    self.assertIn("Waiting for monitor", parked.subtitle)

  def test_invalid_source_requires_explicit_reset_and_exact_bytes(self):
    owner = SentryOwner(self.params, lambda: True)
    self.path.write_bytes(b'{"version":2}')
    state = owner.snapshot()
    self.assertEqual([row.key for row in state.rows], ["", RESET])
    self.assertFalse(owner.apply(FeatureSettingsRequest(RESET, b'{"version":2}', "confirm")))
    self.path.write_bytes(b'{"version":3}')
    self.assertFalse(owner.apply(FeatureSettingsRequest(RESET, b'{"version":2}', "confirm", confirmation=True)))
    self.assertEqual(self.path.read_bytes(), b'{"version":3}')
    self.assertTrue(owner.apply(FeatureSettingsRequest(RESET, b'{"version":3}', "confirm", confirmation=True)))
    self.assertEqual(self.path.read_bytes(), encode(Preferences()))

  def test_unreadable_symlink_oversize_and_lock_contention_preserve_source(self):
    owner = SentryOwner(self.params, lambda: True)
    self.path.write_bytes(b'X' * 513)
    self.assertFalse(any(row.available for row in owner.snapshot().rows))
    self.assertFalse(owner.apply(FeatureSettingsRequest(RESET, b"", "confirm", confirmation=True)))
    self.assertEqual(self.path.read_bytes(), b'X' * 513)
    self.path.unlink()
    target = self.path.parent / 'elsewhere'
    target.write_bytes(b'{"version":2}')
    self.path.symlink_to(target)
    self.assertFalse(any(row.available for row in owner.snapshot().rows))
    self.path.unlink()
    lock = os.open(self.path.parent.parent / '.lock', os.O_CREAT | os.O_RDONLY, 0o775)
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      self.assertFalse(commit(self.params, encode(Preferences(True)), None, lambda: True).committed)
    finally:
      os.close(lock)
    self.assertFalse(self.path.exists())

  def test_numeric_limits_and_final_parked_recheck(self):
    owner = SentryOwner(self.params, lambda: True)
    self.assertTrue(owner.apply(FeatureSettingsRequest(SENSITIVITY, None, "0.005", confirmation=True)))
    first = self.path.read_bytes()
    self.assertEqual(decode(first).settings.sensitivity, 0.005)
    self.assertFalse(owner.apply(FeatureSettingsRequest(WARNING, first, "10.1", confirmation=True)))
    self.assertEqual(self.path.read_bytes(), first)
    self.assertTrue(owner.apply(FeatureSettingsRequest(WARNING, first, "10.0", confirmation=True)))
    self.assertEqual(decode(self.path.read_bytes()).settings.warning_time_seconds, 10.0)
    self.path.unlink()
    calls = 0
    def parked():
      nonlocal calls
      calls += 1
      return calls < 3
    self.assertFalse(commit(self.params, encode(Preferences(True)), None, parked).committed)
    self.assertFalse(self.path.exists())

  def test_bad_raw_and_post_replace_uncertainty(self):
    self.assertFalse(commit(self.params, b'{"version":9}', None, lambda: True).committed)
    raw = encode(Preferences(True))
    real_fsync = os.fsync
    calls = 0
    def uncertain(fd):
      nonlocal calls
      calls += 1
      if calls == 2:
        raise OSError('directory fsync unavailable')
      return real_fsync(fd)
    with mock.patch('openpilot.starpilot.saved_document.os.fsync', side_effect=uncertain):
      outcome = commit(self.params, raw, None, lambda: True)
    self.assertTrue(outcome.committed)
    self.assertFalse(outcome.verified)
    self.assertEqual(self.path.read_bytes(), raw)


if __name__ == '__main__':
  unittest.main()
