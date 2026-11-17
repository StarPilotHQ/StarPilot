from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from openpilot.starpilot.software.auto_install import DEFAULT_CRON, InstallSettings, cron_fields, cron_matches, next_restart, scheduled_restart


class Params:
  def __init__(self):
    self.values = {'IsOffroad': True}
    self.writes = []

  def get_bool(self, key):
    return bool(self.values.get(key))

  def put_bool(self, key, value, **kwargs):
    self.values[key] = value
    self.writes.append((key, value))


class AutoInstallTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.settings = InstallSettings(Path(temporary.name) / 'install')
    self.params = Params()
    self.now = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)

  def enable(self, expression=DEFAULT_CRON):
    current = self.settings.read()
    assert current is not None
    return self.settings.save(True, expression, current['revision'], lambda: True)

  def test_local_midnight_tracks_daylight_savings(self):
    current = self.settings.read()
    assert current is not None
    self.settings.save(True, DEFAULT_CRON, current['revision'], lambda: True, timezone='America/Los_Angeles')
    for month, utc_hour in [(7, 7), (12, 8)]:
      with self.subTest(month=month):
        now = datetime(2026, month, 15, utc_hour, tzinfo=UTC)
        self.params.values['DoReboot'] = False
        self.assertFalse(scheduled_restart(self.params, self.settings, lambda: True, lambda: True, now - timedelta(hours=1)))
        self.assertTrue(scheduled_restart(self.params, self.settings, lambda: True, lambda: True, now))
    zone = ZoneInfo('America/Los_Angeles')
    before = datetime(2026, 3, 8, 0, tzinfo=zone)
    self.assertEqual(next_restart('30 2 * * *', before), datetime(2026, 3, 9, 2, 30, tzinfo=zone))

  def test_next_restart(self):
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    self.assertEqual(next_restart(DEFAULT_CRON, now), datetime(2026, 10, 31, tzinfo=UTC))
    self.assertEqual(next_restart('15 2 * * 1-5', now), datetime(2026, 11, 2, 2, 15, tzinfo=UTC))
    self.assertEqual(next_restart('0 0 29 2 *', now), datetime(2028, 2, 29, tzinfo=UTC))
    self.assertIsNone(next_restart('0 0 31 2 *', now))

  def test_default_is_disabled_and_daily_at_midnight(self):
    value = self.settings.read()
    assert value is not None
    self.assertFalse(value['enabled'])
    self.assertEqual(value['cron'], '0 0 * * *')
    self.assertFalse(scheduled_restart(self.params, self.settings, lambda: True, lambda: True, self.now))
    self.assertEqual(self.params.writes, [])

  def test_numeric_cron_ranges_lists_steps_and_calendar_semantics(self):
    self.assertTrue(cron_matches(DEFAULT_CRON, self.now))
    self.assertFalse(cron_matches(DEFAULT_CRON, self.now + timedelta(minutes=1)))
    self.assertTrue(cron_matches('*/15 1-3 * * 1-5', self.now.replace(hour=2, minute=30)))
    self.assertFalse(cron_matches('*/15 1-3 * * 1-5', (self.now + timedelta(days=1)).replace(hour=2, minute=30)))
    sunday = datetime(2026, 11, 1, tzinfo=UTC)
    self.assertTrue(cron_matches('0 0 * * 0,7', sunday))
    self.assertTrue(cron_matches('0 0 1 * 5', self.now))
    self.assertFalse(cron_matches('0 0 1 * */1', self.now))

  def test_invalid_cron_is_rejected(self):
    for value in ('@midnight', '* * * *', '60 0 * * *', '0 24 * * *', '0 0 0 * *', '0 0 * 13 *',
                  '0 0 * * 8', '*/0 * * * *', '10-2 * * * *', '0, * * * *', '1/100 * * * *', 'x' * 129):
      with self.subTest(value=value), self.assertRaises(ValueError):
        cron_fields(value)

  def test_settings_revision_and_last_moment_authorization(self):
    saved = self.enable('  0   0 * * * ')
    self.assertEqual(saved['cron'], DEFAULT_CRON)
    with self.assertRaises(ValueError):
      self.settings.save(False, DEFAULT_CRON, '0' * 32, lambda: True)
    with self.assertRaises(PermissionError):
      self.settings.save(False, DEFAULT_CRON, saved['revision'], lambda: False)
    self.assertEqual(self.settings.read(), saved)

  def test_corrupt_or_symlink_settings_never_enable_restarts(self):
    self.enable()
    for raw in (b'not json', b'{}', b'x' * 4097):
      self.settings.path.write_bytes(raw)
      self.assertIsNone(self.settings.read())
      self.assertFalse(scheduled_restart(self.params, self.settings, lambda: True, lambda: True, self.now))
    self.settings.path.unlink()
    self.settings.path.symlink_to(Path(__file__))
    self.assertIsNone(self.settings.read())

  def test_onroad_disabled_updater_no_update_and_no_park_proof_block_restart(self):
    self.enable()
    for flags, ready, parked in (({'IsOffroad': False}, True, True), ({'DisableUpdates': True}, True, True),
                                  ({'DoReboot': True}, True, True), ({}, False, True), ({}, True, False)):
      with self.subTest(flags=flags, ready=ready, parked=parked):
        params = Params()
        params.values.update(flags)
        self.assertFalse(scheduled_restart(params, self.settings, lambda ready=ready: ready, lambda parked=parked: parked, self.now))
        self.assertEqual(params.writes, [])

  def test_restart_once_per_scheduled_minute_is_durable(self):
    self.enable()
    self.assertTrue(scheduled_restart(self.params, self.settings, lambda: True, lambda: True, self.now))
    self.params.values['DoReboot'] = False
    other = InstallSettings(self.settings.root)
    self.assertFalse(scheduled_restart(self.params, other, lambda: True, lambda: True, self.now))
    self.assertEqual(self.params.writes, [('DoReboot', True)])
    self.assertTrue(scheduled_restart(self.params, other, lambda: True, lambda: True, self.now + timedelta(days=1)))

  def test_offroad_and_prepared_update_are_rechecked_before_reboot(self):
    for ready, parked in ((Mock(side_effect=[True, False]), Mock(return_value=True)),
                          (Mock(return_value=True), Mock(side_effect=[True, False]))):
      with self.subTest(ready=ready, parked=parked):
        self.settings.path.unlink(missing_ok=True)
        self.enable()
        self.assertFalse(scheduled_restart(self.params, self.settings, ready, parked, self.now))
        self.assertEqual(self.params.writes, [])

  def test_no_catchup_restart_after_the_scheduled_minute(self):
    self.enable()
    ready, parked = Mock(return_value=True), Mock(return_value=True)
    self.assertFalse(scheduled_restart(self.params, self.settings, ready, parked, self.now + timedelta(minutes=1)))
    ready.assert_not_called()
    parked.assert_not_called()
