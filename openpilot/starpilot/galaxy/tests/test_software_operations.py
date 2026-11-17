from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.software_operations import (
  SoftwareOperations, SoftwareOperationError, UpdaterProcess, PROCESS_TITLE,
)
from openpilot.starpilot.software.update_control import UpdaterControlError
from openpilot.starpilot.software.auto_install import InstallSettings


class FakeProcess:
  def __init__(self):
    self.running = True
    self.sent = []
    self.fast_requests = []

  def available(self):
    return self.running

  def send(self, sig, **identity):
    if not self.running:
      raise SoftwareOperationError("Updater stopped", 503)
    self.sent.append(sig)
    if sig in ("update", "fast"):
      self.fast_requests.append(identity)

  def close(self):
    pass


class SoftwareOperationsTest(unittest.TestCase):
  def setUp(self):
    hardware = mock.patch('openpilot.starpilot.galaxy.software_operations.COMMA_HARDWARE', False)
    hardware.start()
    self.addCleanup(hardware.stop)
    downloads = mock.patch('openpilot.starpilot.galaxy.software_operations.downloaded_versions', return_value=[])
    downloads.start()
    self.addCleanup(downloads.stop)
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    root = Path(self.temp.name)
    self.params = Params(str(root / "params"))
    self.params.put("Version", "test", block=True)
    self.params.put("GitBranch", "main", block=True)
    self.params.put("GitCommit", "a" * 40, block=True)
    self.params.put("UpdaterState", "idle", block=True)
    self.params.put("UpdaterTargetBranch", "main", block=True)
    self.params.put("UpdaterAvailableBranches", "main,release", block=True)
    self.params.put_bool("IsOffroad", True, block=True)
    self.params.put_bool("UpdateAvailable", False, block=True)
    self.params.put_bool("DisableUpdates", False, block=True)
    self.params.put("UpdateFailedCount", 0, block=True)
    self.process = FakeProcess()
    self.parked = True
    self.authorized = True
    self.metadata = root / "metadata.git"
    self.finalized = root / "finalized"
    self.finalized.mkdir()
    self.installed = root / "installed"
    self.installed.mkdir()
    self.git: dict[Path, tuple[str, str] | None] = {self.installed: ("main", "a" * 40), self.finalized: ("release", "b" * 40)}
    self.owner = SoftwareOperations(self.params, parked=lambda: self.parked, process=self.process,
                                    finalized=self.finalized, metadata=self.metadata, installed=self.installed,
                                    install_settings=InstallSettings(root / 'install-settings'),
                                    git_identity=lambda path: self.git.get(path))
    self.addCleanup(self.owner.close)

  def act(self, action, branch=None):
    body = {"action": action}
    if branch is not None:
      body["branch"] = branch
    return self.owner.action(action, body, authorized=lambda: self.authorized)

  def denied(self, action, status, branch=None):
    with self.assertRaises(SoftwareOperationError) as caught:
      self.act(action, branch)
    self.assertEqual(caught.exception.status, status)

  def test_snapshot_refresh_precedes_parked_read_and_relocks_without_signaling(self):
    order = []

    def refresh():
      order.append('refresh')
      self.parked = not self.parked
      return True

    def parked():
      order.append('parked')
      return self.parked

    self.parked = False
    self.owner = SoftwareOperations(self.params, parked=parked, process=self.process, refresh=refresh,
                                    finalized=self.finalized, metadata=self.metadata, installed=self.installed, git_identity=lambda path: self.git.get(path))
    self.addCleanup(self.owner.close)
    for expected in (True, False):
      order.clear()
      view = self.owner.snapshot(include_history=False)
      self.assertEqual(order, ['refresh', 'parked'])
      self.assertIs(view['parked'], expected)
      for key in ('canCheck', 'canDownload', 'canSelect'):
        self.assertIs(view[key], expected)
      self.assertIsNone(view['request'])
      self.assertEqual(self.process.sent, [])

  def test_failed_refresh_never_uses_cached_parked_authority(self):
    self.parked = True
    for outcome in (False, None, 1, OSError('collector failed')):
      with self.subTest(outcome=outcome):
        refresh = mock.Mock(side_effect=outcome) if isinstance(outcome, Exception) else mock.Mock(return_value=outcome)
        parked = mock.Mock(return_value=True)
        owner = SoftwareOperations(self.params, parked=parked, process=self.process, refresh=refresh,
                                   finalized=self.finalized, metadata=self.metadata, installed=self.installed, git_identity=lambda path: self.git.get(path))
        self.addCleanup(owner.close)
        view = owner.snapshot(include_history=False)
        refresh.assert_called_once_with()
        parked.assert_not_called()
        self.assertIs(view['parked'], False)
        for key in ('canCheck', 'canDownload', 'canSelect', 'canInstall', 'canFastUpdate', 'canConfigure'):
          self.assertIs(view[key], False)
        self.assertIsNone(view['request'])
        self.assertEqual(self.process.sent, [])

  def test_idle_is_not_reported_as_update_progress(self):
    view = self.owner.snapshot(include_history=False)
    self.assertIsNone(view["progress"]["stage"])
    self.assertEqual(view["progress"]["detail"], "")

  def test_normal_finalization_completes_only_after_idle_success(self):
    for failed in (False, True):
      with self.subTest(failed=failed):
        self.owner.request = None
        self.params.put("UpdateFailedCount", 0, block=True)
        self.params.put("UpdaterState", "idle", block=True)
        self.params.remove("LastUpdateTime")
        self.act("download", "main")
        self.params.put("UpdaterState", "finalizing update...", block=True)
        self.params.put("UpdaterFastState", {"version": 1, "stage": "complete", "detail": "Old fast update"}, block=True)
        view = self.owner.snapshot()
        self.assertEqual(view["request"]["state"], "pending")
        self.assertEqual(view["progress"], {"stage": "finalizing update...", "detail": "", "percent": None})
        self.params.put("UpdaterState", "idle", block=True)
        if failed:
          self.params.put("UpdateFailedCount", 1, block=True)
          self.params.put("LastUpdateException", "Finalization copy failed", block=True)
        else:
          self.params.put("LastUpdateTime", datetime(2026, 10, 6), block=True)
          self.params.put("UpdaterLastFetchTime", datetime(2026, 10, 6), block=True)
        result = self.owner.snapshot()["request"]
        self.assertEqual(result["state"], "failed" if failed else "complete")
        if failed:
          self.assertEqual(result["error"], "Finalization copy failed")

  def test_unavailable_finalizing_preserves_request_until_status_recovers(self):
    self.act("download", "main")
    self.params.put("UpdaterState", "finalizing update...", block=True)
    self.process.running = False
    view = self.owner.snapshot()
    self.assertEqual(view["progress"]["stage"], "unavailable")
    self.assertIn("device updater service", view["progress"]["detail"])
    self.assertNotIn("last reported state", view["progress"]["detail"])
    self.assertEqual(view["request"]["state"], "pending")
    self.assertEqual(view["reason"], "Updater status is unavailable")
    self.assertFalse(view["canCheck"])
    self.assertEqual(self.params.get("UpdaterState"), "finalizing update...")
    self.process.running = True
    self.params.put("UpdaterState", "idle", block=True)
    self.params.put("LastUpdateTime", datetime(2026, 10, 6), block=True)
    self.params.put("UpdaterLastFetchTime", datetime(2026, 10, 6), block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "complete")

  def test_running_finalization_timeout_does_not_fabricate_completion(self):
    self.owner.clock = lambda: 0.0
    self.act("download", "main")
    self.params.put("UpdaterState", "finalizing update...", block=True)
    self.owner.clock = lambda: 901.0
    view = self.owner.snapshot()
    self.assertEqual(view["progress"], {"stage": "timed_out",
                                      "detail": "Request timed out; the updater may still be running", "percent": None})
    self.assertTrue(view["updaterAvailable"])
    self.assertEqual(view["request"]["state"], "failed")
    self.assertTrue(view["canCheck"])
    self.assertEqual(self.params.get("UpdaterState"), "finalizing update...")

  def test_recent_version_admission_pins_installed_snapshot_and_listed_target(self):
    selected = 'c' * 40
    recent = {'branch': 'main', 'head': 'b' * 40,
              'entries': [{'hash': selected, 'date': '2026-09-29T12:00:00+00:00', 'subject': 'Previous build'}]}
    payload = {'action': 'version', 'branch': 'main', 'expectedCommit': 'a' * 40, 'selectedCommit': selected}
    with mock.patch('openpilot.starpilot.galaxy.software_operations.recent_versions', return_value=recent), \
         mock.patch.object(self.process, 'send') as send:
      for field, value in (('selectedCommit', 'f' * 40), ('expectedCommit', 'b' * 40), ('branch', 'release')):
        with self.subTest(field=field), self.assertRaises(SoftwareOperationError):
          self.owner.action('version', dict(payload, **{field: value}), authorized=lambda: True)
      self.parked = False
      with self.assertRaises(SoftwareOperationError):
        self.owner.action('version', payload, authorized=lambda: True)
      self.parked = True
      self.owner.action('version', payload, authorized=lambda: True)
      send.assert_called_once_with('version', branch='main', commit='a' * 40, selected_commit=selected)
      self.assertIsNotNone(self.owner.request)
      assert self.owner.request is not None
      self.assertEqual(self.owner.request['selectedCommit'], selected)

  def test_branch_browsing_fetches_metadata_without_changing_updater_target(self):
    result = self.act('versions', 'release')
    self.assertEqual(self.params.get('UpdaterTargetBranch'), 'main')
    self.assertEqual(result['selectedTarget'], 'release')
    self.assertEqual(self.process.sent, ['versions'])
    self.assertFalse(self.params.get_bool('DoReboot'))

  def test_download_selected_commit_pins_request_and_does_not_reboot(self):
    selected = 'c' * 40
    recent = {'branch': 'main', 'head': selected, 'entries': [{'hash': selected, 'date': '', 'subject': 'Selected'}]}
    payload = {'action': 'download-version', 'branch': 'main', 'expectedCommit': 'a' * 40, 'selectedCommit': selected}
    with mock.patch('openpilot.starpilot.galaxy.software_operations.recent_versions', return_value=recent), \
         mock.patch.object(self.process, 'send') as send:
      self.owner.action('download-version', payload, authorized=lambda: True)
      send.assert_called_once_with('download-version', branch='main', commit='a' * 40, selected_commit=selected)
    request = self.owner.request
    assert request is not None
    self.assertEqual(request['selectedCommit'], selected)
    self.assertFalse(self.params.get_bool('DoReboot'))
    self.git[self.finalized] = ('main', selected)
    (self.finalized / '.overlay_consistent').touch()
    self.params.put('UpdaterNewDescription', 'test / main / ccccccc / Oct 30', block=True)
    self.params.put_bool('UpdateAvailable', True, block=True)
    self.params.put('LastUpdateTime', datetime(2026, 10, 30), block=True)
    self.params.put('UpdaterLastFetchTime', datetime(2026, 10, 30), block=True)
    with mock.patch('openpilot.starpilot.galaxy.software_operations.validate_revision', return_value={}):
      self.assertEqual(self.owner.snapshot()['request']['state'], 'complete')
    self.assertFalse(self.params.get_bool('DoReboot'))

  def test_download_completion_handles_finalized_identity_disappearing(self):
    request = {'action': 'download-version', 'state': 'pending', 'target': 'main', 'selectedCommit': 'c' * 40}
    self.owner.request = request
    self.owner._baseline = (None, None, 0)
    self.params.put('LastUpdateTime', datetime(2026, 10, 30), block=True)
    self.params.put('UpdaterLastFetchTime', datetime(2026, 10, 30), block=True)
    with mock.patch.object(self.owner, '_ready', return_value=True), \
         mock.patch.object(self.owner, 'git_identity', return_value=None):
      self.owner._refresh_request(self.owner.status.snapshot())
    self.assertEqual(request['state'], 'failed')
    self.assertEqual(request['error'], 'Selected version was not prepared; refresh and try again')

  def test_version_installed_snapshot_cannot_change_at_final_dispatch(self):
    selected = 'c' * 40
    recent = {'branch': 'main', 'head': selected,
              'entries': [{'hash': selected, 'date': '2026-09-29T12:00:00+00:00', 'subject': 'Build'}]}
    calls = 0
    def authorized():
      nonlocal calls
      calls += 1
      if calls == 3:
        self.params.put('GitCommit', 'd' * 40, block=True)
        self.git[self.installed] = ('main', 'd' * 40)
      return True
    with mock.patch('openpilot.starpilot.galaxy.software_operations.recent_versions', return_value=recent), \
         mock.patch.object(self.process, 'send') as send, self.assertRaises(SoftwareOperationError):
      self.owner.action('version', {'action': 'version', 'branch': 'main', 'expectedCommit': 'a' * 40,
                                    'selectedCommit': selected}, authorized=authorized)
    send.assert_not_called()

  def test_listing_request_completes_without_install_timestamp_or_reboot(self):
    self.params.put('LastUpdateTime', datetime(2026, 9, 1), block=True)
    self.params.put('UpdaterLastFetchTime', datetime(2026, 9, 1), block=True)
    with mock.patch.object(self.process, 'send') as send:
      self.act('versions', 'main')
      send.assert_called_once_with('versions', branch='main', commit='a' * 40)
    self.params.put('UpdaterLastFetchTime', datetime(2026, 9, 2), block=True)
    state = self.owner.snapshot()['request']
    self.assertEqual(state['state'], 'complete')
    self.assertNotIn('outcome', state)
    self.assertEqual(self.params.get('LastUpdateTime'), datetime(2026, 9, 1))
    self.assertFalse(self.params.get_bool('DoReboot'))

  def test_select_then_check_then_download_signals_exact_updater(self):
    self.assertTrue(self.owner.snapshot()["canSelect"])
    self.assertEqual(self.owner.snapshot()["selectedTarget"], "main")
    selected = self.act("select", "release")
    self.assertEqual(selected["selectedTarget"], "release")
    self.assertEqual(self.params.get("UpdaterTargetBranch"), "release")
    self.assertEqual(self.process.sent, [])
    checked = self.act("check")
    self.assertEqual(checked["request"]["state"], "pending")
    self.assertEqual(self.process.sent, ["check"])
    self.denied("download", 409, "release")
    self.params.put("UpdaterState", "checking...", block=True)
    self.owner.snapshot()
    self.params.put("UpdaterState", "idle", block=True)
    self.params.put("LastUpdateTime", datetime(2026, 9, 27, 12, 0), block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "complete")
    downloaded = self.act("download", "release")
    self.assertEqual(downloaded["request"]["target"], "release")
    self.assertEqual(self.process.sent, ["check", "download"])
    self.denied("download", 409, "release")

  def test_fresh_manager_without_target_allows_check_and_select(self):
    self.params.remove("UpdaterTargetBranch")
    initial = self.owner.snapshot()
    self.assertEqual(initial["selectedTarget"], "main")
    self.assertTrue(initial["canCheck"])
    self.assertTrue(initial["canSelect"])
    check = self.act("check")
    self.assertIsNone(check["request"]["target"])
    self.assertEqual(check["request"]["state"], "pending")
    self.params.put("UpdaterTargetBranch", "main", block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "pending")
    self.params.put("LastUpdateTime", datetime(2026, 9, 27, 12, 4), block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "complete")
    self.act("select", "release")
    self.assertEqual(self.params.get("UpdaterTargetBranch"), "release")

  def test_bad_or_stale_branch_and_missing_updater_never_mutate(self):
    self.denied("select", 400, "--help")
    self.denied("select", 409, "unknown")
    self.assertEqual(self.params.get("UpdaterTargetBranch"), "main")
    self.process.running = False
    self.denied("check", 503)
    self.assertEqual(self.process.sent, [])

  def test_parked_authority_and_session_rechecked_before_effect(self):
    for offroad, parked, authorized in ((False, True, True), (True, False, True), (True, True, False)):
      with self.subTest(offroad=offroad, parked=parked, authorized=authorized):
        self.params.put_bool("IsOffroad", offroad, block=True)
        self.parked = parked
        self.authorized = authorized
        self.denied("check", 403 if not authorized else 409)
    self.assertEqual(self.process.sent, [])
    self.params.put_bool("IsOffroad", True, block=True)
    self.parked = self.authorized = True
    checks = iter((True, False))
    with self.assertRaises(SoftwareOperationError):
      self.owner.action("check", {"action": "check"}, authorized=lambda: next(checks))
    self.assertEqual(self.process.sent, [])

  def test_install_requires_exact_finalized_target_and_reboot_once(self):
    self.act("select", "release")
    self.params.put_bool("UpdateAvailable", True, block=True)
    self.params.put("UpdaterNewDescription", "test / release / bbbbbbb / Sep 27", block=True)
    with mock.patch("openpilot.starpilot.galaxy.software_operations.validate_revision", return_value={}):
      self.assertFalse(self.owner.snapshot()["canInstall"])
      (self.finalized / ".overlay_consistent").touch()
      self.assertTrue(self.owner.snapshot()["canInstall"])
      self.denied("install", 409, "main")
      self.git[self.finalized] = ("release", "c" * 40)
      self.assertFalse(self.owner.snapshot()["canInstall"])
      self.denied("install", 409, "release")
      self.git[self.finalized] = ("release", "b" * 40)
      result = self.act("install", "release")
      self.assertEqual(result["request"]["state"], "pending")
      self.assertEqual(result["request"]["action"], "install")
      self.assertTrue(self.params.get_bool("DoReboot"))
      self.denied("install", 409, "release")
      self.assertEqual(self.process.sent, [])

  def test_metadata_check_is_fast_and_completes_during_background_finalization(self):
    self.params.put('UpdaterState', 'finalizing update...', block=True)
    fast = {'version': 1, 'stage': 'finalizing', 'detail': 'Copying files: 44%', 'percent': 44, 'background': True}
    self.params.put('UpdaterFastState', fast, block=True)
    with mock.patch.object(self.owner, '_history_snapshot') as history:
      view = self.owner.snapshot(include_history=False)
      self.assertTrue(view['canCheck'])
      self.assertFalse(view['canFastUpdate'])
      result = self.act('check')
      history.assert_not_called()
    self.assertEqual(result['request']['state'], 'pending')
    fast['check'] = {'state': 'complete', 'branch': 'main', 'detail': 'Metadata ready', 'completedAt': self.owner._started + 1}
    self.params.put('UpdaterFastState', fast, block=True)
    result = self.owner.snapshot(include_history=False)
    self.assertEqual(result['request']['state'], 'complete')
    self.assertEqual(result['progress']['percent'], 44)
    self.assertTrue(result['progress']['background'])
    self.assertEqual(self.params.get('UpdaterState'), 'finalizing update...')

  def test_target_change_or_failure_is_reported(self):
    self.act("select", "release")
    self.act("download", "release")
    self.params.put("UpdaterTargetBranch", "main", block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "failed")
    self.act("select", "release")
    self.act("check")
    self.params.put("UpdateFailedCount", 1, block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "failed")

  def test_selected_install_rejects_a_different_downloaded_commit(self):
    self.act('select', 'release')
    self.params.put_bool('UpdateAvailable', True, block=True)
    self.params.put('UpdaterNewDescription', 'test / release / bbbbbbb / Sep 27', block=True)
    (self.finalized / '.overlay_consistent').touch()
    payload = {'action': 'install', 'branch': 'release', 'expectedCommit': 'a' * 40, 'selectedCommit': 'c' * 40}
    with mock.patch('openpilot.starpilot.galaxy.software_operations.validate_revision', return_value={}):
      with self.assertRaisesRegex(SoftwareOperationError, 'Selected version changed'):
        self.owner.action('install', payload, authorized=lambda: True)
      self.assertFalse(self.params.get_bool('DoReboot'))
      payload['selectedCommit'] = 'b' * 40
      result = self.owner.action('install', payload, authorized=lambda: True)
      self.assertEqual(result['request']['selectedCommit'], 'b' * 40)
      self.assertTrue(self.params.get_bool('DoReboot'))

  def test_selected_install_rechecks_commit_after_authorization(self):
    self.act('select', 'release')
    self.params.put_bool('UpdateAvailable', True, block=True)
    self.params.put('UpdaterNewDescription', 'test / release / bbbbbbb / Sep 27', block=True)
    (self.finalized / '.overlay_consistent').touch()
    payload = {'action': 'install', 'branch': 'release', 'expectedCommit': 'a' * 40, 'selectedCommit': 'b' * 40}
    calls = 0
    def authorized():
      nonlocal calls
      calls += 1
      if calls == 3:
        self.git[self.finalized] = ('release', 'c' * 40)
      return True
    with mock.patch('openpilot.starpilot.galaxy.software_operations.validate_revision', return_value={}):
      with self.assertRaisesRegex(SoftwareOperationError, 'Selected version changed'):
        self.owner.action('install', payload, authorized=authorized)
    self.assertFalse(self.params.get_bool('DoReboot'))

  def test_counter_reset_does_not_claim_a_completed_check(self):
    self.params.put("UpdateFailedCount", 2, block=True)
    self.act("check")
    self.params.put("UpdateFailedCount", 0, block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "pending")
    self.params.put("LastUpdateTime", datetime(2026, 9, 27, 12, 1), block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "complete")

  def test_download_without_fetch_cannot_claim_unpublished_target(self):
    self.act("select", "release")
    self.act("download", "release")
    self.params.put_bool("UpdaterFetchAvailable", False, block=True)
    self.params.put("LastUpdateTime", datetime(2026, 9, 27, 12, 2), block=True)
    request = self.owner.snapshot()["request"]
    self.assertEqual(request["state"], "failed")
    self.assertIn("No new download", request["error"])

  def test_download_does_not_call_missing_remote_branch_up_to_date(self):
    self.act("download", "main")
    self.params.put_bool("UpdaterFetchAvailable", False, block=True)
    self.params.put("LastUpdateTime", datetime(2026, 9, 27, 12, 3), block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "failed")

  def test_catalog_keeps_other_branches_when_one_entry_is_invalid(self):
    self.params.put("UpdaterAvailableBranches", "main,feature+lab,bad branch,feature@lab", block=True)
    branches = self.owner.snapshot()["availableBranches"]
    self.assertEqual(branches, ["main", "feature+lab", "feature@lab"])
    self.act("select", "feature+lab")
    self.assertEqual(self.params.get("UpdaterTargetBranch"), "feature+lab")

  def test_published_catalog_with_more_than_64_branches_remains_usable(self):
    branches = [f"release/{number:03d}" for number in range(76)]
    self.params.put("UpdaterAvailableBranches", ",".join(branches), block=True)
    snapshot = self.owner.snapshot()
    self.assertEqual(snapshot["availableBranches"], branches)
    self.assertTrue(snapshot["canSelect"])
    self.act("select", branches[-1])
    self.assertEqual(self.params.get("UpdaterTargetBranch"), branches[-1])

  def test_slash_branch_description_can_match_finalized_commit(self):
    self.params.put("UpdaterAvailableBranches", "main,release/2026", block=True)
    self.act("select", "release/2026")
    self.git[self.finalized] = ("release/2026", "b" * 40)
    self.params.put_bool("UpdateAvailable", True, block=True)
    self.params.put("UpdaterNewDescription", "test / release/2026 / bbbbbbb / Sep 27", block=True)
    (self.finalized / ".overlay_consistent").touch()
    with mock.patch("openpilot.starpilot.galaxy.software_operations.validate_revision", return_value={}):
      self.assertTrue(self.owner.snapshot()["canInstall"])

  def save_downloads(self, enabled, expected):
    return self.owner.action('preferences', {'action': 'preferences', 'automaticDownloads': enabled,
                                             'expectedAutomaticDownloads': expected}, authorized=lambda: self.authorized)

  def test_download_preference_is_independent_of_manual_updater_commands(self):
    self.assertIs(self.owner.snapshot()['automaticDownloads'], True)
    result = self.save_downloads(False, True)
    self.assertIs(result['automaticDownloads'], False)
    self.assertIsNone(result['request'])
    self.assertEqual(self.process.sent, [])
    self.assertTrue(result['canDownload'])
    self.act('download', 'main')
    self.assertEqual(self.process.sent, ['download'])

  def test_preference_can_be_saved_without_running_updater_but_not_after_authority_changes(self):
    self.process.running = False
    self.assertTrue(self.owner.snapshot()['canConfigure'])
    self.assertFalse(self.save_downloads(False, True)['automaticDownloads'])
    for case in ('stale', 'onroad', 'unauthorized', 'reboot'):
      self.parked = case != 'onroad'
      self.authorized = case != 'unauthorized'
      self.params.put_bool('DoReboot', case == 'reboot', block=True)
      with self.assertRaises(SoftwareOperationError):
        self.save_downloads(True, True if case == 'stale' else False)
      self.assertFalse(self.params.get_bool('UpdaterAutomaticDownloads'))
    self.assertEqual(self.process.sent, [])

  def test_invalid_preference_can_be_repaired_without_implicit_enabling(self):
    Path(self.params.get_param_path('UpdaterAutomaticDownloads')).write_bytes(b'bad')
    self.assertIsNone(self.owner.snapshot()['automaticDownloads'])
    self.assertFalse(self.save_downloads(False, None)['automaticDownloads'])
    with self.assertRaises(SoftwareOperationError):
      self.save_downloads(1, False)

  def test_repository_link_uses_only_normalized_github_origins(self):
    for remote in ('https://github.com/owner/repo.git', 'git@github.com:owner/repo.git', 'ssh://git@github.com/owner/repo'):
      self.params.put('GitRemote', remote, block=True)
      self.assertEqual(self.owner.snapshot(include_history=False)['repository'], 'owner/repo')
    self.params.put('GitRemote', 'https://foreign.test/owner/repo', block=True)
    self.assertIsNone(self.owner.snapshot(include_history=False)['repository'])

  def test_lightweight_status_preserves_controls_without_reading_history(self):
    with mock.patch.object(self.owner, '_history_snapshot') as history:
      light = self.owner.snapshot(include_history=False)
      self.assertNotIn('history', light)
      history.assert_not_called()
      full = self.owner.snapshot()
      history.assert_called_once()
      self.assertEqual(light, {key: value for key, value in full.items() if key != 'history'})

  def test_install_preferences_default_off_and_validate_cron_and_revision(self):
    initial = self.owner.snapshot(include_history=False)
    self.assertFalse(initial['automaticInstalls'])
    self.assertEqual(initial['restartSchedule'], '0 0 * * *')
    body = {'action': 'install-preferences', 'automaticInstalls': True, 'restartSchedule': '0 0 * * *',
            'expectedRevision': initial['installPreferenceRevision'], 'restartTimezone': 'America/Los_Angeles'}
    result = self.owner.action('install-preferences', body, authorized=lambda: True)
    self.assertTrue(result['automaticInstalls'])
    self.assertEqual(result['restartTimezone'], 'America/Los_Angeles')
    self.assertFalse(self.params.get_bool('DoReboot'))
    self.assertEqual(self.process.sent, [])
    with self.assertRaises(SoftwareOperationError) as caught:
      self.owner.action('install-preferences', body, authorized=lambda: True)
    self.assertEqual(caught.exception.status, 409)
    body['expectedRevision'] = result['installPreferenceRevision']
    body['restartTimezone'] = 'Invalid/Timezone'
    with self.assertRaises(SoftwareOperationError) as caught:
      self.owner.action('install-preferences', body, authorized=lambda: True)
    self.assertEqual(caught.exception.status, 400)
    body['restartTimezone'] = 'America/Los_Angeles'
    body['restartSchedule'] = '0 24 * * *'
    with self.assertRaises(SoftwareOperationError) as caught:
      self.owner.action('install-preferences', body, authorized=lambda: True)
    self.assertEqual(caught.exception.status, 400)
    saved = self.owner.install_settings.read()
    assert saved is not None
    self.assertEqual(saved['cron'], '0 0 * * *')

  def test_install_preferences_recheck_offroad_before_saving(self):
    body = {'action': 'install-preferences', 'automaticInstalls': True, 'restartSchedule': '0 0 * * *', 'expectedRevision': '0' * 32}
    with self.assertRaises(SoftwareOperationError) as caught:
      self.owner.action('install-preferences', body, authorized=lambda: False)
    self.assertEqual(caught.exception.status, 403)
    checks = iter((True, False))
    with mock.patch.object(self.owner, '_parked', side_effect=lambda: next(checks)), self.assertRaises(SoftwareOperationError) as caught:
      self.owner.action('install-preferences', body, authorized=lambda: True)
    self.assertEqual(caught.exception.status, 403)
    self.assertFalse(self.owner.install_settings.path.exists())
    self.assertFalse(self.params.get_bool('DoReboot'))

  def test_history_includes_previous_installed_version(self):
    previous = {'hash': 'd' * 40, 'date': '2026-09-28T12:00:00+00:00', 'subject': 'Previous installed build'}
    with mock.patch('openpilot.starpilot.galaxy.software_operations.previous_version', return_value=previous):
      self.assertEqual(self.owner.snapshot()['history']['previous'], previous)

  def test_history_caches_local_reads_and_refreshes_when_download_identity_changes(self):
    reader = mock.Mock(side_effect=lambda path, sha, **kwargs: [{'hash': sha, 'date': '2026-09-29T12:00:00+00:00', 'subject': path.name}])
    self.owner.history_reader = reader
    self.params.put('UpdaterCurrentReleaseNotes', b'<p>Current</p>', block=True)
    self.params.put('UpdaterNewReleaseNotes', b'<p>Next</p>', block=True)
    with mock.patch('openpilot.starpilot.galaxy.software_operations.recent_versions',
                    return_value={'branch': 'main', 'head': 'c' * 40, 'entries': []}) as recent:
      first = self.owner.snapshot()['history']
      self.assertEqual(first['installed'][0]['hash'], 'a' * 40)
      self.assertEqual(first['downloaded'][0]['hash'], 'b' * 40)
      self.assertEqual(first['currentReleaseNotes'], 'Current')
      self.assertEqual(first['downloadedReleaseNotes'], 'Next')
      self.assertEqual(reader.call_count, 2)
      self.assertEqual(self.owner.snapshot()['history'], first)
      self.assertEqual(reader.call_count, 2)
      self.assertEqual(recent.call_count, 3)
      self.params.put('UpdaterNewDescription', 'new description', block=True)
      self.owner.snapshot()
      self.assertEqual(reader.call_count, 4)
      self.assertEqual(recent.call_count, 6)
      self.params.put('UpdaterLastFetchTime', datetime(2026, 10, 4, 12), block=True)
      self.owner.snapshot()
      self.assertEqual(recent.call_count, 9)
      self.assertEqual(reader.call_count, 6)
      self.params.put('UpdaterLastCheckTime', datetime(2026, 10, 4, 13), block=True)
      self.owner.snapshot()
      self.assertEqual(recent.call_count, 12)

  def test_unified_update_pins_source_and_tracks_completion(self):
    result = self.act("update", "release")
    self.assertEqual(result["request"]["action"], "update")
    self.assertEqual(result["request"]["target"], "release")
    self.assertEqual(self.process.sent, ["update"])
    self.assertEqual(self.process.fast_requests, [{"branch": "release", "commit": "a" * 40}])
    self.params.put("LastUpdateTime", datetime(2026, 10, 3, 12), block=True)
    self.params.put("UpdaterLastFetchTime", datetime(2026, 10, 3, 12), block=True)
    self.params.put_bool("DoReboot", True, block=True)
    result = self.owner.snapshot()["request"]
    self.assertEqual(result["state"], "complete")
    self.assertEqual(result["outcome"], "restarting")

  def test_version_list_uses_checked_history_before_download_finishes(self):
    old = {'branch': 'main', 'head': 'b' * 40, 'entries': []}
    latest = {'branch': 'main', 'head': 'c' * 40,
              'entries': [{'hash': 'c' * 40, 'date': '2026-10-29T12:00:00+00:00', 'subject': 'Not downloaded yet'}]}
    with mock.patch('openpilot.starpilot.galaxy.software_operations.recent_versions',
                    side_effect=lambda path, branch: latest if path == self.metadata else old):
      result = self.owner.snapshot()['history']
    self.assertEqual(result['recent'], latest)
    self.assertEqual(result['downloaded'], [])

  def test_older_installed_history_is_selectable_without_being_in_recent_list(self):
    older = {'hash': 'b' * 40, 'date': '2026-10-01T12:00:00+00:00', 'subject': 'Older installed history'}
    self.owner.history_reader = lambda path, sha, **kwargs: [older] if path == self.installed else []
    recent = {'branch': 'main', 'head': 'c' * 40, 'entries': []}
    with mock.patch('openpilot.starpilot.galaxy.software_operations.recent_versions', return_value=recent):
      result = self.owner.action('version', {'action': 'version', 'branch': 'main',
                                'expectedCommit': 'a' * 40, 'selectedCommit': older['hash']}, authorized=lambda: True)
    self.assertEqual(result['request']['selectedCommit'], older['hash'])
    self.assertEqual(self.process.sent, ['version'])

  def test_fast_current_branch_without_branch_list_or_selected_target(self):
    self.params.remove("UpdaterAvailableBranches")
    self.params.put("UpdaterTargetBranch", "release", block=True)
    self.assertTrue(self.owner.snapshot()["canFastUpdate"])
    result = self.act("fast", "main")
    self.assertEqual(result["request"]["target"], "main")
    self.assertEqual(self.process.sent, ["fast"])
    self.assertEqual(self.process.fast_requests, [{"branch": "main", "commit": "a" * 40}])
    self.assertEqual(self.params.get("UpdaterTargetBranch"), "release")
    self.assertFalse(self.params.get_bool("DoReboot"))
    self.params.put("LastUpdateTime", datetime(2026, 10, 3, 12), block=True)
    self.params.put("UpdaterLastFetchTime", datetime(2026, 10, 3, 12), block=True)
    result = self.owner.snapshot()["request"]
    self.assertEqual(result["state"], "complete")
    self.assertEqual(result["outcome"], "up_to_date")

  def test_fast_uses_registered_offroad_and_live_parked_authority(self):
    Path(self.params.get_param_path("IsOnroad")).write_bytes(b"1")
    self.assertTrue(self.owner.snapshot()["canFastUpdate"])
    self.parked = False
    self.assertFalse(self.owner.snapshot()["canFastUpdate"])
    self.denied("fast", 409, "main")
    self.parked = True
    self.act("fast", "main")
    self.assertEqual(self.process.sent, ["fast"])

  def test_fast_refreshes_stale_cached_identity_without_rewriting_running_metadata(self):
    self.git[self.installed] = ("main", "b" * 40)
    self.act("fast", "main")
    self.assertEqual(self.process.fast_requests, [{"branch": "main", "commit": "b" * 40}])
    self.assertEqual(self.params.get("GitCommit"), "a" * 40)
    self.assertEqual(self.owner._fast_commit, "b" * 40)
    self.params.put("LastUpdateTime", datetime(2026, 10, 30, 12), block=True)
    self.params.put("UpdaterLastFetchTime", datetime(2026, 10, 30, 12), block=True)
    self.assertEqual(self.owner.snapshot()["request"]["outcome"], "up_to_date")
    self.assertFalse(self.params.get_bool("DoReboot"))

  def test_changed_cached_branch_does_not_block_requested_live_branch(self):
    self.git[self.installed] = ("release", "b" * 40)
    self.params.remove("UpdaterAvailableBranches")
    self.act("update", "release")
    self.assertEqual(self.process.fast_requests, [{"branch": "release", "commit": "b" * 40}])
    self.assertEqual(self.params.get("GitBranch"), "main")

  def test_real_git_commit_after_manager_start_can_update(self):
    import subprocess
    from openpilot.starpilot.galaxy.software_operations import _git_identity
    def git(*args):
      return subprocess.check_output(["git", "-c", "user.name=Updater Test", "-c", "user.email=test@example.invalid",
                                      "-c", "core.hooksPath=/dev/null", "-C", str(self.installed), *args], text=True).strip()
    git("init", "-b", "main")
    git("commit", "--allow-empty", "-m", "First")
    previous = git("rev-parse", "HEAD")
    self.params.put("GitCommit", previous, block=True)
    git("commit", "--allow-empty", "-m", "Hotfix")
    current = git("rev-parse", "HEAD")
    self.owner.git_identity = _git_identity
    self.act("fast", "main")
    self.assertEqual(self.process.fast_requests, [{"branch": "main", "commit": current}])
    self.assertEqual(self.params.get("GitCommit"), previous)

  def test_actual_checkout_race_still_prevents_dispatch(self):
    calls = 0
    def authorized():
      nonlocal calls
      calls += 1
      if calls == 3:
        self.git[self.installed] = ("main", "b" * 40)
      return True
    with self.assertRaises(SoftwareOperationError):
      self.owner.action("fast", {"action": "fast", "branch": "main"}, authorized=authorized)
    self.assertEqual(self.process.sent, [])

  def test_missing_live_checkout_and_unknown_target_still_reject(self):
    self.git[self.installed] = None
    self.denied("fast", 409, "main")
    self.git[self.installed] = ("main", "b" * 40)
    self.denied("fast", 409, "unknown-branch")
    self.assertEqual(self.process.sent, [])

  def test_fast_keeps_existing_admission_and_observes_failure(self):
    for key in ("DisableUpdates", "DoReboot"):
      self.params.put_bool(key, True, block=True)
      self.denied("fast", 409, "main")
      self.params.put_bool(key, False, block=True)
    self.authorized = False
    self.denied("fast", 403, "main")
    self.authorized = True
    self.act("fast", "main")
    self.denied("fast", 409, "main")
    self.params.put("UpdateFailedCount", 1, block=True)
    self.assertEqual(self.owner.snapshot()["request"]["state"], "failed")
    self.assertEqual(self.process.sent, ["fast"])


class UpdaterIdentityTest(unittest.TestCase):
  def test_fresh_manager_pid_and_same_proc_start_before_signal(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      proc = root / "123"
      proc.mkdir()
      (proc / "stat").write_text("123 (updater) " + " ".join(["S", *(["0"] * 18), "3456"]))
      (proc / "cmdline").write_bytes(PROCESS_TITLE + b"\0")
      state = SimpleNamespace(name="updated", running=True, shouldBeRunning=True, pid=123)
      class ManagerMessages:
        seen = {"managerState": True}
        alive = {"managerState": True}
        valid = {"managerState": True}
        recv_time = {"managerState": 1.25}
        logMonoTime = {"managerState": 1_250_000_000}

        def update(self, timeout):
          pass

        def __getitem__(self, key):
          return SimpleNamespace(processes=[state])

      sm = ManagerMessages()
      clock = [1_000_000_000]
      class FakeControl:
        def __init__(self):
          self.available_calls = []
          self.sent = []

        def available(self, pid, start):
          self.available_calls.append((pid, start))
          return True

        def send(self, pid, start, action):
          self.sent.append((pid, start, action))

      control = FakeControl()
      process = UpdaterProcess(sm, control=control, proc_root=root, mono_ns=lambda: clock[0])
      clock[0] = 1_500_000_000
      with mock.patch("os.kill") as kill:
        self.assertTrue(process.available())
        process.send("check")
        self.assertEqual(control.available_calls, [(123, 3456)])
        self.assertEqual(control.sent, [(123, 3456, "check")])
        with mock.patch.object(control, "send", side_effect=UpdaterControlError("declined")):
          with self.assertRaises(SoftwareOperationError) as denied:
            process.send("download")
          self.assertEqual(denied.exception.status, 503)
        with mock.patch.object(process, "_manager_pid", side_effect=[123, 124]):
          with self.assertRaises(SoftwareOperationError):
            process.send("download")
        self.assertEqual(control.sent, [(123, 3456, "check")])
        kill.assert_not_called()
      sm.recv_time["managerState"] = 0.9
      self.assertFalse(process.available())
      sm.recv_time["managerState"] = 1.25
      (proc / "cmdline").write_bytes(b"other\0")
      self.assertFalse(process.available())
      with self.assertRaises(SoftwareOperationError):
        process.send("download")


if __name__ == "__main__":
  unittest.main()
