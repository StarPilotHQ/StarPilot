from pathlib import Path
import tempfile
import threading
import time
import sys
from types import ModuleType
import unittest
from unittest import mock
from unittest.mock import Mock, patch

from openpilot.system.updated.tests.test_vendored_update import load_updater
from openpilot.starpilot.drive_state.evidence import PhysicalSource


class StopLoop(Exception):
  pass


class TestFastRequest(unittest.TestCase):
  def setUp(self):
    self.updated = load_updater()
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    root = Path(self.directory.name)
    self.updated.LOCK_FILE = str(root / 'lock')
    self.updated.STAGING_ROOT = str(root / 'staging')
    self.updated.OVERLAY_INIT = root / 'overlay'
    self.params = Mock()
    self.values = {'InstallDate': '2026-10-03', 'UpdateFailedCount': 2}
    self.writes = []
    self.params.get.side_effect = lambda key, **kwargs: self.values.get(key)
    self.params.get_bool.side_effect = lambda key: bool(self.values.get(key, False))
    def put(key, value, **kwargs):
      self.writes.append((key, value))
      self.values[key] = value
    self.params.put.side_effect = put
    self.params.put_bool.side_effect = put
    self.params.remove.side_effect = lambda key: self.values.pop(key, None)
    self.updated.Params = Mock(return_value=self.params)
    self.helper = self.updated.WaitTimeHelper.__new__(self.updated.WaitTimeHelper)
    self.helper.ready_event = threading.Event()
    self.helper.request_lock = threading.RLock()
    self.helper.request_generation = 0
    self.helper.fast_target = None
    self.helper.metadata = None
    self.helper.user_request = self.updated.UserRequest.NONE
    self.helper._control_request('fast', branch='SecretGoodStarPilot', commit='a' * 40)
    self.helper.sleep = Mock(side_effect=StopLoop)
    self.updated.WaitTimeHelper = Mock(return_value=self.helper)
    self.updater = Mock()
    self.updater.fast_update.return_value = 'up-to-date'
    self.updated.Updater = Mock(return_value=self.updater)
    self.updated.init_overlay = Mock()
    self.updated.set_consistent_flag = Mock()

  def run_cycle(self):
    with self.assertRaises(StopLoop):
      self.updated.main()
    self.updated.init_overlay.assert_not_called()
    self.updater.check_for_update.assert_not_called()
    self.updater.fetch_update.assert_not_called()
    self.updater.set_params.assert_not_called()
    self.updated.system_time_valid.assert_not_called()
    self.updater.fast_update.assert_called_once_with('SecretGoodStarPilot', 'a' * 40, rollback=False)

  def test_normal_fetch_finalization_success_and_failure_return_idle(self):
    # Retain the real fetch implementation while replacing its external Git/filesystem operations.
    module = load_updater()
    actual = module.Updater()
    actual.params = self.params
    actual._branches_checked = True
    actual._checked_branch = "SecretGoodStarPilot"
    actual.branches = {"SecretGoodStarPilot": "a" * 40}
    self.values["UpdaterTargetBranch"] = "SecretGoodStarPilot"
    module.run = Mock(return_value="a" * 40)
    module.setup_git_options = Mock()
    module.set_consistent_flag = Mock()
    module.init_overlay = Mock()
    module.validate_revision = Mock()
    module.validate_update_revision = Mock(return_value=False)
    module.AGNOS = False
    self.updater.target_branch = "SecretGoodStarPilot"
    self.updater._checked_branch = "SecretGoodStarPilot"
    self.updater._checked_at = time.monotonic()
    for failure in (False, True):
      with self.subTest(failure=failure):
        module.init_overlay.reset_mock()
        module.validate_update_revision.reset_mock()
        self.writes.clear()
        self.values.pop("LastUpdateTime", None)
        self.helper._control_request("download")
        self.helper.sleep = Mock(side_effect=[None, StopLoop])
        self.updated.system_time_valid = Mock(return_value=True)
        module.finalize_update = Mock(side_effect=RuntimeError("copy failed") if failure else None)
        self.updater.fetch_update.side_effect = actual.fetch_update
        def set_params(success, count, error):
          self.params.put("UpdateFailedCount", count)
          if success:
            self.updated.write_time_to_param(self.params, "LastUpdateTime")
        self.updater.set_params.side_effect = set_params
        with self.assertRaises(StopLoop):
          self.updated.main()
        states = [value for key, value in self.writes if key == "UpdaterState"]
        self.assertIn("finalizing update...", states)
        self.assertEqual(states[-1], "idle")
        self.assertEqual("LastUpdateTime" in self.values, not failure)
        module.finalize_update.assert_called_once()
        module.init_overlay.assert_called_once_with()
        self.assertEqual(module.validate_update_revision.call_args_list, [
          mock.call(module.OVERLAY_MERGED, 'a' * 40, 'SecretGoodStarPilot'),
          mock.call(module.OVERLAY_MERGED, 'HEAD', 'SecretGoodStarPilot'),
        ])

  def test_fast_request_skips_staging_and_startup_delay(self):
    self.run_cycle()
    self.assertEqual(self.values['UpdaterState'], 'idle')
    self.assertEqual(self.values['UpdateFailedCount'], 0)
    self.assertIn('UpdaterLastFetchTime', self.values)
    self.assertIn('LastUpdateTime', self.values)
    self.assertNotIn('DoReboot', self.values)
    self.assertLess(self.writes.index(('UpdateFailedCount', 0)), len(self.writes) - 1)
    self.assertEqual(self.writes[-1], ('UpdaterState', 'idle'))

  def test_fast_failure_reports_error_without_reboot(self):
    self.updater.fast_update.side_effect = RuntimeError('Tracked source has local changes')
    self.run_cycle()
    self.assertEqual(self.values['UpdaterState'], 'idle')
    self.assertEqual(self.values['UpdateFailedCount'], 3)
    self.assertEqual(self.values['LastUpdateException'], 'Tracked source has local changes')
    self.assertNotIn('DoReboot', self.values)
    self.assertNotIn('LastUpdateTime', self.values)

  def test_long_failure_remains_readable_by_galaxy(self):
    self.updater.fast_update.side_effect = RuntimeError('download failed: ' + 'é' * 5000)
    self.run_cycle()
    self.assertLessEqual(len(self.values['LastUpdateException'].encode('utf-8')), 4096)
    self.assertTrue(self.values['LastUpdateException'].startswith('download failed: '))
    self.assertEqual(self.values['UpdaterState'], 'idle')

  def test_success_waits_for_manager_restart(self):
    def apply(branch, commit, *, rollback=False):
      self.assertFalse(rollback)
      self.params.put_bool('DoReboot', True, block=True)
      return 'reboot-requested'
    self.updater.fast_update.side_effect = apply
    self.run_cycle()
    self.assertEqual(self.values['UpdaterState'], 'updating...')
    self.assertTrue(self.values['DoReboot'])

  def test_versions_listing_is_not_installation_or_staging(self):
    self.helper._control_request('versions', branch='Dom', commit='a' * 40)
    self.updater.fast_update.return_value = 'versions-listed'
    with self.assertRaises(StopLoop):
      self.updated.main()
    self.updater.fast_update.assert_called_once_with('Dom', 'a' * 40, versions_only=True)
    self.updated.init_overlay.assert_not_called()
    self.updater.set_params.assert_not_called()
    self.assertIn('UpdaterLastFetchTime', self.values)
    self.assertNotIn('LastUpdateTime', self.values)
    self.assertNotIn('DoReboot', self.values)
    self.assertEqual(self.values['UpdaterState'], 'idle')

  def test_failed_listing_preserves_existing_staging_marker(self):
    self.updated.OVERLAY_INIT.write_text('retained validated staging')
    self.helper._control_request('versions', branch='Dom', commit='a' * 40)
    self.updater.fast_update.side_effect = RuntimeError('version download unavailable')
    with self.assertRaises(StopLoop):
      self.updated.main()
    self.assertEqual(self.updated.OVERLAY_INIT.read_text(), 'retained validated staging')
    self.assertNotIn('DoReboot', self.values)
    self.assertNotIn('LastUpdateTime', self.values)

  def test_selected_version_dispatch_and_superseding_request(self):
    self.helper._control_request('version', branch='Dom', commit='a' * 40, selected_commit='b' * 40)
    _, generation, target = self.helper.current_request()
    self.assertEqual(target, ('Dom', 'a' * 40, 'b' * 40))
    with self.assertRaises(StopLoop):
      self.updated.main()
    self.updater.fast_update.assert_called_once_with('Dom', 'a' * 40, 'b' * 40, rollback=False)
    self.helper._control_request('versions', branch='Dom', commit='a' * 40)
    self.assertFalse(self.helper.finish_request(generation))
    self.assertEqual(self.helper.current_request()[2], ('Dom', 'a' * 40))

  def test_new_request_is_not_lost_when_previous_work_finishes(self):
    _, generation, target = self.helper.current_request()
    self.assertEqual(target, ('SecretGoodStarPilot', 'a' * 40))
    self.helper._control_request('check')
    self.assertFalse(self.helper.finish_request(generation))
    self.assertTrue(self.helper.ready_event.is_set())
    request, generation, target = self.helper.current_request()
    self.assertIsNone(target)
    self.assertEqual(request, self.updated.UserRequest.CHECK)
    self.assertTrue(self.helper.finish_request(generation))
    self.assertEqual(self.helper.user_request, self.updated.UserRequest.NONE)
    self.assertFalse(self.helper.ready_event.is_set())


class TestFastAdapter(unittest.TestCase):
  def test_source_admission_and_overlay_invalidation_are_owned_by_updater(self):
    updated = load_updater()
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      updated.FINALIZED = str(root / 'finalized')
      Path(updated.FINALIZED).mkdir()
      marker = Path(updated.FINALIZED, '.overlay_consistent')
      marker.touch()
      updated.OVERLAY_INIT = root / '.overlay_init'
      updated.OVERLAY_INIT.touch()
      updated.BASEDIR = str(root)
      updated.dismount_overlay = Mock()
      updated.HARDWARE.get_os_version.return_value = '19.8.1'
      updater = updated.Updater()
      updater.params = Mock()
      def registered_flag(key):
        if key != 'IsOffroad':
          raise KeyError(key.encode())
        return True
      updater.params.get_bool.side_effect = registered_flag
      source = Mock()
      source.allowed.return_value = True
      source.effective.return_value = False
      source.status.side_effect = lambda *, updating_offroad=False, source=source: {'allowed': source.allowed(), 'reason': None}
      source.update_status.side_effect = lambda manager_offroad, source=source: PhysicalSource.update_status(source, manager_offroad)
      source.update_ready.side_effect = lambda manager_offroad, source=source: PhysicalSource.update_ready(source, manager_offroad)
      physical = ModuleType('openpilot.starpilot.drive_state.evidence')
      physical.PhysicalSource = Mock(return_value=source)
      owner = ModuleType('openpilot.starpilot.software.fast_update')
      def install(repo, branch, **options):
        self.assertEqual((repo, branch, options['expected_commit'], options['current_os']),
                         (root, 'SecretGoodStarPilot', 'b' * 40, '19.8.1'))
        self.assertTrue(options['parked']())
        self.assertTrue(marker.exists())
        options['invalidate']()
        self.assertFalse(marker.exists())
        self.assertFalse(updated.OVERLAY_INIT.exists())
        updated.dismount_overlay.assert_called_once()
        return 'reboot-requested'
      owner.fast_update = install
      with patch.dict(sys.modules, {physical.__name__: physical, owner.__name__: owner}):
        self.assertEqual(updater.fast_update('SecretGoodStarPilot', 'b' * 40), 'reboot-requested')
      source.close.assert_called_once()
      source.update_ready.assert_called_once_with(True)
      source.status.assert_called_once_with(updating_offroad=True)
      self.assertEqual(updater.params.get_bool.call_args_list, [mock.call('IsOffroad')])
      updater.params.put_bool.assert_called_once_with('UpdateAvailable', False, block=True)


  def test_live_onroad_invalid_physical_or_offroad_false_deny_fast_admission(self):
    for offroad, allowed, effective in ((False, True, False), (True, False, False), (True, True, True), (True, True, None)):
      with self.subTest(offroad=offroad, allowed=allowed, effective=effective):
        updated = load_updater()
        updater = updated.Updater()
        updater.params = Mock()
        def registered_flag(key, offroad=offroad):
          if key != "IsOffroad":
            raise KeyError(key.encode())
          return offroad
        updater.params.get_bool.side_effect = registered_flag
        source = Mock()
        source.allowed.return_value = allowed
        source.effective.return_value = effective
        source.status.side_effect = lambda *, updating_offroad=False, source=source: {'allowed': source.allowed(), 'reason': None}
        source.update_status.side_effect = lambda manager_offroad, source=source: PhysicalSource.update_status(source, manager_offroad)
        source.update_ready.side_effect = lambda manager_offroad, source=source: PhysicalSource.update_ready(source, manager_offroad)
        physical = ModuleType('openpilot.starpilot.drive_state.evidence')
        physical.PhysicalSource = Mock(return_value=source)
        owner = ModuleType('openpilot.starpilot.software.fast_update')
        def admit(*args, **kwargs):
          self.assertFalse(kwargs['parked']())
          return 'denied'
        owner.fast_update = admit
        with patch.dict(sys.modules, {physical.__name__: physical, owner.__name__: owner}), \
             patch.object(updated.time, 'monotonic', side_effect=(0, 1)):
          self.assertEqual(updater.fast_update('SecretGoodStarPilot', 'b' * 40), 'denied')
        source.close.assert_called_once()
        source.update_ready.assert_called_once_with(offroad)
        if not offroad:
          source.status.assert_not_called()
        else:
          source.status.assert_called_once_with(updating_offroad=True)


if __name__ == '__main__':
  unittest.main()
