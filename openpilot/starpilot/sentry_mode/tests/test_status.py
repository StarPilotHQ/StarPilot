import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.starpilot.sentry_mode.policy import Decision
from openpilot.starpilot.sentry_mode.preferences import Preferences, SavedPreferences, encode
from openpilot.starpilot.sentry_mode.status import RuntimeStatus, TTL_NS, STATES


class RuntimeStatusTest(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.now = 10_000_000_000
    self.boot_now = self.now + 50_000_000_000
    self.identity = "current-boot"
    self.status = RuntimeStatus(Path(self.directory.name).resolve(), clock=lambda: self.now,
                                boot=lambda: self.identity, boot_clock=lambda: self.boot_now)
    preferences = Preferences(True)
    self.saved = SavedPreferences(encode(preferences), True, True, preferences)
    self.environment = patch.dict(os.environ, STARPILOT_SENTRY_DEVELOPMENT="1")
    self.environment.start()
    self.addCleanup(self.environment.stop)

  def test_saved_enable_is_not_armed_and_fresh_countdown_is_observed(self):
    self.assertEqual(self.status.runtime_snapshot(self.saved)[0], "Waiting for monitor")
    self.status.publish(Decision("arming", seconds_remaining=82), self.saved.raw, "idle")
    self.assertEqual(self.status.runtime_snapshot(self.saved)[0], "Arming · 82s")
    self.status.publish(Decision("armed"), self.saved.raw, "stored")
    self.assertEqual(self.status.runtime_snapshot(self.saved)[0], "Monitoring motion")
    self.status.publish(Decision("disabled_ignition"), self.saved.raw, "stored")
    self.assertEqual(self.status.runtime_snapshot(self.saved)[0], "Not armed")

  def test_expired_future_reboot_resume_or_changed_settings_never_show_armed(self):
    for failure in ("expired", "future", "reboot", "resume", "settings"):
      with self.subTest(failure=failure):
        self.now = 10_000_000_000
        self.boot_now = self.now + 50_000_000_000
        self.identity = "current-boot"
        preferences = Preferences(True)
        self.saved = SavedPreferences(encode(preferences), True, True, preferences)
        self.status.last_published = None
        self.status.publish(Decision("armed"), self.saved.raw, "idle")
        if failure == "expired":
          self.now += TTL_NS + 1
        elif failure == "future":
          self.now -= 1
        elif failure == "reboot":
          self.identity = "another-boot"
        elif failure == "resume":
          self.boot_now += 300_000_000
        else:
          assert self.saved.raw is not None
          self.saved = SavedPreferences(self.saved.raw + b" ", True, True, self.saved.preferences)
        self.assertEqual(self.status.runtime_snapshot(self.saved)[0], "Waiting for monitor")

  def test_legacy_development_flag_does_not_gate_real_monitor_and_unsafe_status_is_rejected(self):
    self.status.publish(Decision("armed"), self.saved.raw, "idle")
    with patch.dict(os.environ, STARPILOT_SENTRY_DEVELOPMENT="0"):
      self.assertEqual(self.status.runtime_snapshot(self.saved)[0], "Monitoring motion")
    destination = Path(self.directory.name) / "status.json"
    original = destination.read_bytes()
    destination.unlink()
    target = Path(self.directory.name) / "other"
    target.write_bytes(original)
    destination.symlink_to(target)
    self.assertEqual(self.status.runtime_snapshot(self.saved)[0], "Waiting for monitor")

  def test_low_voltage_reports_the_battery_reading(self):
    self.status.publish(Decision("low_voltage"), self.saved.raw, "idle", power=(118, 120))
    self.assertEqual(self.status.runtime_snapshot(self.saved), ("Not armed", "Battery is 11.8 V; needs above 12.0 V"))
    self.status.publish(Decision("low_voltage"), self.saved.raw, "idle", power=(None, 120))
    self.assertEqual(self.status.runtime_snapshot(self.saved)[1], "Battery voltage cannot be read")
    self.status.publish(Decision("low_voltage"), self.saved.raw, "idle", power=(125, 120))
    self.assertEqual(self.status.runtime_snapshot(self.saved)[1], f"{STATES['low_voltage'][1]} (battery 12.5 V)")
    self.status.publish(Decision("low_voltage"), self.saved.raw, "idle", power=(125, None))
    self.assertEqual(self.status.runtime_snapshot(self.saved)[1], "Battery is 12.5 V; the low-battery cutoff cannot be read")
