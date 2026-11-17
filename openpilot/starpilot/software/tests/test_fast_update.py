import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
  spec = importlib.util.spec_from_file_location(name, path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules[name] = module
  spec.loader.exec_module(module)
  return module


vendor = load('openpilot.common.vendor_manifest', ROOT / 'common/vendor_manifest.py')
prebuilt = load('openpilot.common.prebuilt_manifest', ROOT / 'common/prebuilt_manifest.py')
owner = load('fast_update_owner', ROOT / 'starpilot/software/fast_update.py')


class Params:
  def __init__(self):
    self.calls = []

  def put_bool(self, *args, **kwargs):
    self.calls.append((args, kwargs))


class TestFastUpdate(unittest.TestCase):
  def git(self, repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.STDOUT).decode().strip()

  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    base = Path(self.temp.name)
    self.remote, self.repo = base / 'remote', base / 'repo'
    self.remote.mkdir()
    self.git(self.remote, 'init', '-b', 'Dom')
    self.git(self.remote, 'config', 'user.name', 'Test')
    self.git(self.remote, 'config', 'user.email', 'test@example.invalid')
    dependencies = []
    for name in sorted(vendor.DEPENDENCIES):
      (self.remote / name).mkdir()
      (self.remote / name / 'source').write_text('vendored')
      dependencies.append({'path': name, 'commit': '1' * 40, 'tree': '2' * 40, 'url': 'https://example.invalid/source', 'exclude': []})
    manifest = {'schema_version': 1, 'upstream': {'commit': '3' * 40, 'url': 'https://example.invalid/upstream'}, 'dependencies': dependencies}
    (self.remote / 'upstream-sync.json').write_text(json.dumps(manifest))
    (self.remote / 'launch_env.sh').write_text('export AGNOS_VERSION="19.8.1"\n')
    (self.remote / 'prebuilt').write_text('')
    (self.remote / 'openpilot/starpilot/ui').mkdir(parents=True)
    (self.remote / 'openpilot/starpilot/ui/source.py').write_text('old')
    self.git(self.remote, 'add', '.')
    self.git(self.remote, 'commit', '-m', 'initial')
    self.git(base, 'clone', '--no-local', str(self.remote), str(self.repo))
    self.previous = self.git(self.repo, 'rev-parse', 'HEAD')
    (self.remote / 'openpilot/starpilot/ui/source.py').write_text('new')
    self.git(self.remote, 'commit', '-am', 'update')
    self.target = self.git(self.remote, 'rev-parse', 'HEAD')
    self.params = Params()
    self.invalidations = []

  def update(self, **kwargs):
    arguments = {
      'params': self.params,
      'parked': lambda: True,
      'expected_commit': self.git(self.repo, 'rev-parse', 'HEAD'),
      'current_os': '19.8.1',
      'invalidate': lambda: self.invalidations.append(True),
      'sleep': lambda _: None,
    }
    arguments.update(kwargs)
    return owner.fast_update(self.repo, 'Dom', **arguments)

  def legacy_release(self, branch='Dom', version='6.7.9'):
    self.git(self.repo, 'checkout', '-b', 'SecretGoodStarPilot')
    self.git(self.remote, 'rm', 'upstream-sync.json')
    files = {'launch_openpilot.sh': '#!/bin/sh\n', 'launch_chffrplus.sh': '#!/bin/sh\n',
             'selfdrive/ui/lib/starpilot_version.py': f'STARPILOT_DISPLAY_VERSION = "{version}"\n',
             'selfdrive/car/card.py': 'pass\n', 'selfdrive/modeld/modeld.py': 'pass\n'}
    for name, text in files.items():
      path = self.remote / name
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(text)
      if name.endswith('.sh'):
        path.chmod(0o755)
    self.git(self.remote, 'add', '.')
    self.git(self.remote, 'commit', '-m', 'legacy release')
    self.target = self.git(self.remote, 'rev-parse', 'HEAD')
    if branch != 'Dom':
      self.git(self.remote, 'branch', branch)
    return self.target

  def test_legacy_dom_and_starpilot_return_without_manifest_or_build_receipt(self):
    for branch in ('Dom', 'StarPilot'):
      with self.subTest(branch=branch):
        if branch == 'Dom':
          self.legacy_release()
          self.git(self.remote, 'branch', 'StarPilot')
        else:
          self.git(self.repo, 'checkout', '-B', 'SecretGoodStarPilot', self.previous)
        model = self.repo / 'downloaded-model'
        model.write_text('keep')
        stages = []
        self.assertEqual(owner.fast_update(self.repo, branch, params=self.params, parked=lambda: True,
                         expected_commit=self.previous, current_os='19.8.1', invalidate=lambda: None,
                         sleep=lambda _: None, progress=lambda stage, detail, stages=stages: stages.append((stage, detail))), 'reboot-requested')
        self.assertEqual(self.git(self.repo, 'symbolic-ref', '--short', 'HEAD'), branch)
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.target)
        self.assertEqual(self.git(self.repo, 'rev-parse', 'refs/starpilot/previous'), self.previous)
        self.assertEqual(model.read_text(), 'keep')
        self.assertTrue(any('6.7.9' in detail for _, detail in stages))
        self.assertFalse((self.repo / 'upstream-sync.json').exists())
        self.assertEqual(self.params.calls[-1], (('DoReboot', True), {'block': True}))

  def test_legacy_explicit_accepted_os_and_missing_required_os(self):
    self.legacy_release()
    launch = self.remote / 'launch_env.sh'
    launch.write_text('export AGNOS_VERSION="19.8.1"\nexport AGNOS_ACCEPTED_VERSIONS="19.8.1 19.8.2"\n')
    self.git(self.remote, 'commit', '-am', 'accepted OS')
    self.assertEqual(self.update(current_os='19.8.2'), 'reboot-requested')
    self.git(self.repo, 'checkout', '-B', 'SecretGoodStarPilot', self.previous)
    self.params.calls.clear()
    launch.write_text('export AGNOS_ACCEPTED_VERSIONS="19.8.2"\n')
    self.git(self.remote, 'commit', '-am', 'missing required OS')
    with self.assertRaisesRegex(owner.FastUpdateIncompatible, 'operating system'):
      self.update(current_os='19.8.2')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertEqual(self.params.calls, [])

  def test_legacy_missing_marker_new_major_and_different_os_refused(self):
    self.legacy_release(version='7.0.0')
    with self.assertRaisesRegex(owner.FastUpdateIncompatible, '6.x'):
      self.update()
    version = self.remote / 'selfdrive/ui/lib/starpilot_version.py'
    version.write_text('STARPILOT_DISPLAY_VERSION = "6.7.9"\n')
    self.git(self.remote, 'commit', '-am', 'legacy version')
    with self.assertRaisesRegex(owner.FastUpdateIncompatible, 'operating system'):
      self.update(current_os='different')
    self.git(self.remote, 'rm', 'prebuilt')
    self.git(self.remote, 'commit', '-m', 'missing prebuilt')
    with self.assertRaisesRegex(owner.FastUpdateIncompatible, 'prebuilt'):
      self.update()
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertEqual(self.params.calls, [])

  def test_legacy_untracked_collision_and_parked_withdrawal_still_refused(self):
    self.legacy_release()
    collision = self.repo / 'selfdrive/car/card.py'
    collision.parent.mkdir(parents=True)
    collision.write_text('private data')
    with self.assertRaisesRegex(owner.FastUpdateError, 'untracked data'):
      self.update()
    collision.unlink()
    checks = iter((True, False))
    with self.assertRaisesRegex(owner.FastUpdateError, 'parked state'):
      self.update(parked=lambda: next(checks))
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertEqual(self.params.calls, [])

  def test_legacy_failed_post_apply_validation_restores_rebase(self):
    self.legacy_release()
    original = owner.validate_legacy_return
    def fail_post(git, revision, branch):
      if revision == 'HEAD':
        raise owner.FastUpdateIncompatible('legacy post-check failed')
      return original(git, revision, branch)
    with patch.object(owner, 'validate_legacy_return', fail_post):
      with self.assertRaisesRegex(owner.FastUpdateIncompatible, 'legacy post-check'):
        self.update()
    self.assertEqual(self.git(self.repo, 'symbolic-ref', '--short', 'HEAD'), 'SecretGoodStarPilot')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertTrue((self.repo / 'upstream-sync.json').is_file())
    self.assertEqual(self.params.calls, [])

  def test_legacy_exception_never_accepts_other_branches_or_malformed_manifest(self):
    self.legacy_release()
    with self.assertRaises(owner.FastUpdateIncompatible):
      owner.validate_legacy_return(lambda *args: self.git(self.remote, *args), self.target, 'other')
    (self.remote / 'upstream-sync.json').write_text('{}')
    self.git(self.remote, 'add', '.')
    self.git(self.remote, 'commit', '-m', 'malformed current manifest')
    with self.assertRaises(ValueError):
      self.update()
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)

  def test_recent_listing_preserves_installed_state_and_rollback_receipt(self):
    self.git(self.repo, 'update-ref', 'refs/starpilot/previous', self.previous)
    (self.repo / 'private-data').write_text('preserved')
    commands = []
    def traced(command, cwd):
      commands.append(command)
      return owner.run(command, cwd)
    self.assertEqual(self.update(versions_only=True, run=traced), 'versions-listed')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'refs/starpilot/previous'), self.previous)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'refs/starpilot/recent/Dom'), self.target)
    self.assertEqual((self.repo / 'private-data').read_text(), 'preserved')
    self.assertEqual(self.params.calls, [])
    self.assertEqual(self.invalidations, [])
    self.assertIn('--depth=20', next(command for command in commands if 'fetch' in command))
    self.assertIn('--filter=blob:none', next(command for command in commands if 'fetch' in command))

  def test_selected_older_version_uses_same_install_and_rollback_transaction(self):
    self.update()
    self.params.calls.clear()
    self.invalidations.clear()
    self.assertEqual(self.update(selected_commit=self.previous), 'reboot-requested')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'refs/starpilot/previous'), self.target)
    self.assertEqual(self.invalidations, [True])
    self.assertEqual(self.params.calls, [(('DoReboot', True), {'block': True})])

  def test_forged_out_of_window_and_rebased_selected_versions_rejected(self):
    def rejected(target):
      with self.assertRaisesRegex(owner.FastUpdateError, 'recent branch history'):
        self.update(selected_commit=target)
      self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
      self.assertEqual(self.params.calls, [])
      self.assertEqual(self.invalidations, [])
    rejected('f' * 40)
    for index in range(owner.MAX_COMMITS + 1):
      (self.remote / 'openpilot/starpilot/ui/source.py').write_text(str(index))
      self.git(self.remote, 'commit', '-am', 'recent build')
    rejected(self.target)
    abandoned = self.git(self.remote, 'rev-parse', 'HEAD')
    self.git(self.remote, 'reset', '--hard', self.previous)
    (self.remote / 'openpilot/starpilot/ui/source.py').write_text('replacement branch history')
    self.git(self.remote, 'commit', '-am', 'replacement')
    rejected(abandoned)

  def test_installed_parent_remains_selectable_after_leaving_recent_window(self):
    self.git(self.repo, 'fetch', 'origin')
    self.git(self.repo, 'reset', '--hard', self.target)
    for index in range(owner.MAX_COMMITS + 1):
      (self.remote / 'openpilot/starpilot/ui/source.py').write_text(str(index))
      self.git(self.remote, 'commit', '-am', 'newer build')
    self.assertEqual(self.update(selected_commit=self.previous), 'reboot-requested')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)

  def test_selected_version_fetches_only_its_artifacts(self):
    self.git(self.remote, 'config', 'uploadpack.allowFilter', 'true')
    self.git(self.remote, 'config', 'uploadpack.allowReachableSHA1InWant', 'true')
    self.git(self.repo, 'remote', 'set-url', 'origin', self.remote.as_uri())
    artifact = self.remote / 'openpilot/starpilot/galaxy/web/assets/artifact.png'
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b'chosen artifact' * 10000)
    self.git(self.remote, 'add', '.')
    self.git(self.remote, 'commit', '-m', 'Chosen version')
    selected = self.git(self.remote, 'rev-parse', 'HEAD')
    selected_blob = self.git(self.remote, 'rev-parse', 'HEAD:openpilot/starpilot/galaxy/web/assets/artifact.png')
    artifact.write_bytes(b'unselected newer artifact' * 10000)
    self.git(self.remote, 'commit', '-am', 'Unselected version')
    unselected_blob = self.git(self.remote, 'rev-parse', 'HEAD:openpilot/starpilot/galaxy/web/assets/artifact.png')
    self.assertEqual(self.update(selected_commit=selected), 'reboot-requested')
    objects = set(self.git(self.repo, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)').splitlines())
    self.assertIn(selected_blob, objects)
    self.assertNotIn(unselected_blob, objects)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), selected)

  def test_selected_version_rechecks_os_and_native_artifact_contracts(self):
    (self.remote / 'launch_env.sh').write_text('export AGNOS_VERSION="different"\n')
    self.git(self.remote, 'commit', '-am', 'different OS')
    bad_os = self.git(self.remote, 'rev-parse', 'HEAD')
    (self.remote / 'launch_env.sh').write_text('export AGNOS_VERSION="19.8.1"\n')
    self.git(self.remote, 'commit', '-am', 'compatible latest')
    with self.assertRaisesRegex(owner.FastUpdateError, 'operating system'):
      self.update(selected_commit=bad_os)
    (self.remote / 'native.cc').write_text('int missing_artifact_proof;')
    self.git(self.remote, 'add', '.')
    self.git(self.remote, 'commit', '-m', 'unqualified native inputs')
    bad_native = self.git(self.remote, 'rev-parse', 'HEAD')
    with self.assertRaisesRegex(owner.FastUpdateError, 'matching build receipt'):
      self.update(selected_commit=bad_native)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertEqual(self.params.calls, [])

  def test_version_snapshot_and_parked_state_withdrawal_prevent_listing_or_install(self):
    with self.assertRaisesRegex(owner.FastUpdateError, 'Approved source revision changed'):
      self.update(versions_only=True, expected_commit='f' * 40)
    checks = iter((True, False))
    with self.assertRaisesRegex(owner.FastUpdateError, 'parked state'):
      self.update(versions_only=True, parked=lambda: next(checks))
    self.assertEqual(self.params.calls, [])
    self.assertEqual(self.invalidations, [])

  def test_applies_exact_revision_and_retains_untracked_models(self):
    (self.repo / 'models').mkdir()
    (self.repo / 'models' / 'download').write_bytes(b'model')
    commands = []

    def traced(command, cwd):
      commands.append(command)
      return owner.run(command, cwd)

    self.assertEqual(self.update(run=traced), 'reboot-requested')
    fetch = next(command for command in commands if 'fetch' in command)
    self.assertIn('gc.auto=0', fetch)
    self.assertIn('maintenance.auto=false', fetch)
    self.assertIn('--depth=1', fetch)
    self.assertIn('--no-tags', fetch)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.target)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'refs/starpilot/previous'), self.previous)
    self.assertEqual((self.repo / 'models' / 'download').read_bytes(), b'model')
    self.assertEqual(self.params.calls, [(('DoReboot', True), {'block': True})])
    self.assertEqual(self.update(), 'up-to-date')
    self.assertEqual(len(self.invalidations), 1)

  def test_actual_updater_owned_transaction_and_durable_restart_state(self):
    from openpilot.system.updated import updated
    from openpilot.starpilot.drive_state import evidence
    from openpilot.starpilot.software import fast_update as production

    from openpilot.common.params import Params as NativeParams
    params = NativeParams(str(Path(self.temp.name) / 'params'))
    params.put_bool('IsOffroad', True, block=True)
    source = type('Physical', (), {'allowed': lambda self: True, 'effective': lambda self: False,
      'update_ready': lambda self, manager_offroad: manager_offroad and self.allowed() and self.effective() is False,
      'close': lambda self: None})()
    updater = updated.Updater.__new__(updated.Updater)
    updater.params = params
    finalized = Path(self.temp.name) / 'finalized'
    finalized.mkdir()
    marker = finalized / '.overlay_consistent'
    marker.touch()
    overlay = self.repo / '.overlay_init'
    overlay.touch()
    with (
      patch.object(updated, 'BASEDIR', str(self.repo)),
      patch.object(updated, 'FINALIZED', str(finalized)),
      patch.object(updated, 'OVERLAY_INIT', overlay),
      patch.object(updated, 'dismount_overlay'),
      patch.object(updated.HARDWARE, 'get_os_version', return_value='19.8.1'),
      patch.object(evidence, 'PhysicalSource', return_value=source),
      patch.object(production.time, 'sleep'),
    ):
      result = updater.fast_update('Dom', self.previous)
    self.assertEqual(result, 'reboot-requested')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.target)
    self.assertFalse(marker.exists())
    self.assertFalse(overlay.exists())
    self.assertEqual(params.get('UpdaterFastState')['stage'], 'complete')
    self.assertTrue(params.get_bool('DoReboot'))
    updated.recover_fast_state(params)
    self.assertEqual(params.get('UpdaterFastState')['stage'], 'complete')
    params.put('UpdaterFastState', {'version': 1, 'stage': 'applying', 'detail': 'Applying fetched commit...'}, block=True)
    updated.recover_fast_state(params)
    self.assertEqual(params.get('UpdaterFastState')['stage'], 'error')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.target)
    params.put('UpdaterFastState', {'version': 1, 'stage': 'complete', 'detail': 'Already up to date.'}, block=True)
    updated.recover_fast_state(params)
    self.assertEqual(params.get('UpdaterFastState')['stage'], 'complete')

  def test_expected_commit_changed_rejected_before_fetch(self):
    commands = []

    def traced(command, cwd):
      commands.append(command)
      return owner.run(command, cwd)

    with self.assertRaises(owner.FastUpdateError):
      self.update(expected_commit='1' * 40, run=traced)
    self.assertFalse(any('fetch' in command for command in commands))

  def test_source_only_ui_update_admitted(self):
    folder = self.remote / 'openpilot/starpilot/ui'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'runtime_app.py').write_text('value = 1')
    self.git(self.remote, 'add', '.')
    self.git(self.remote, 'commit', '-m', 'UI update')
    self.assertEqual(self.update(), 'reboot-requested')

  def test_vendored_extension_update_and_rollback_preserve_downloaded_model(self):
    self.git(self.remote, 'reset', '--hard', self.previous)
    manifest_path = self.remote / 'upstream-sync.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['dependencies'] = [entry for entry in manifest['dependencies'] if entry['path'] != 'jetlink_repo']
    manifest.pop('vendored_extensions', None)
    if (self.remote / 'jetlink_repo').exists():
      self.git(self.remote, 'rm', '-r', 'jetlink_repo')
    manifest_path.write_text(json.dumps(manifest))
    self.git(self.remote, 'add', '.')
    self.receipt_commit()
    self.previous = self.git(self.remote, 'rev-parse', 'HEAD')
    self.git(self.repo, 'fetch', 'origin', 'Dom')
    self.git(self.repo, 'reset', '--hard', self.previous)
    model = self.repo / 'models' / 'download'
    model.parent.mkdir()
    model.write_bytes(b'downloaded model')

    extension = self.remote / 'jetlink_repo'
    extension.mkdir()
    (extension / 'source.py').write_text('API_VERSION = 1\n')
    manifest['vendored_extensions'] = [{'path': 'jetlink_repo', 'commit': '4' * 40, 'tree': '5' * 40,
                                      'url': 'https://example.invalid/jetlink', 'exclude': []}]
    manifest_path.write_text(json.dumps(manifest))
    self.git(self.remote, 'add', '.')
    self.receipt_commit()
    target = self.git(self.remote, 'rev-parse', 'HEAD')

    self.assertEqual(self.update(), 'reboot-requested')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), target)
    self.assertEqual((self.repo / 'jetlink_repo/source.py').read_text(), 'API_VERSION = 1\n')
    self.assertEqual(model.read_bytes(), b'downloaded model')
    self.assertEqual(self.update(rollback=True), 'reboot-requested')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse((self.repo / 'jetlink_repo').exists())
    self.assertEqual(model.read_bytes(), b'downloaded model')
    self.assertEqual(len(self.invalidations), 2)

  def test_native_source_change_rejected_without_artifact_proof(self):
    (self.remote / 'native.cc').write_text('int main() {}')
    self.git(self.remote, 'add', '.')
    self.git(self.remote, 'commit', '-m', 'native update')
    with self.assertRaisesRegex(owner.FastUpdateError, 'full validated update'):
      self.update()
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse(self.invalidations)

  def receipt_commit(self):
    prebuilt.write_receipt(self.remote)
    self.git(self.remote, 'add', 'prebuilt.json')
    self.git(self.remote, 'commit', '-m', 'validated receipt')

  def test_matching_receipt_admits_native_source_and_artifacts(self):
    (self.remote / 'native.cc').write_text('int source;')
    (self.remote / 'native.so').write_bytes(b'compiled artifact fixture')
    self.git(self.remote, 'add', '.')
    self.receipt_commit()
    self.assertEqual(self.update(), 'reboot-requested')

  def test_receipt_stale_source_rejected(self):
    (self.remote / 'native.cc').write_text('int source;')
    self.git(self.remote, 'add', '.')
    self.receipt_commit()
    (self.remote / 'native.cc').write_text('int changed;')
    self.git(self.remote, 'commit', '-am', 'stale source')
    with self.assertRaisesRegex(owner.FastUpdateError, 'matching build receipt'):
      self.update()
    self.assertFalse(self.invalidations)

  def test_receipt_stale_artifact_rejected(self):
    (self.remote / 'native.so').write_bytes(b'artifact')
    self.git(self.remote, 'add', '.')
    self.receipt_commit()
    (self.remote / 'native.so').write_bytes(b'changed artifact')
    self.git(self.remote, 'commit', '-am', 'stale artifact')
    with self.assertRaisesRegex(owner.FastUpdateError, 'matching build receipt'):
      self.update()
    self.assertFalse(self.invalidations)

  def test_receipt_working_content_and_modes_match_committed_tree(self):
    (self.remote / 'native.cc').write_text('int unstaged;')
    self.git(self.remote, 'add', '.')
    (self.remote / 'native.cc').write_text('int working;')
    (self.remote / 'native.cc').chmod(0o755)
    (self.remote / 'native-link').symlink_to('native.cc')
    self.git(self.remote, 'add', 'native-link')
    prebuilt.write_receipt(self.remote)
    self.git(self.remote, 'add', '.')
    self.git(self.remote, 'commit', '-m', 'receipt content and modes')

    def git(*args):
      return subprocess.check_output(['git', '-C', str(self.remote), *args]).decode()

    self.assertTrue(prebuilt.valid_receipt(git, 'HEAD'))

  def test_missing_prebuilt_rejected_without_removing_local_marker(self):
    self.git(self.remote, 'rm', 'prebuilt')
    self.git(self.remote, 'commit', '-m', 'nonprebuilt')
    with self.assertRaisesRegex(owner.FastUpdateError, 'prebuilt'):
      self.update()
    self.assertTrue((self.repo / 'prebuilt').exists())

  def test_wrong_os_rejected_before_mutation(self):
    with self.assertRaises(owner.FastUpdateError):
      self.update(current_os='19.8.2')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse(self.invalidations)
    self.assertFalse(self.params.calls)

  def test_dirty_tracked_source_backed_up_before_update(self):
    path = 'openpilot/starpilot/ui/source.py'
    (self.repo / path).write_text('local repair')
    self.assertEqual(self.update(), 'reboot-requested')
    self.assertEqual((self.repo / path).read_text(), 'new')
    refs = self.git(self.repo, 'for-each-ref', '--format=%(refname)', 'refs/starpilot/local-changes').splitlines()
    self.assertEqual(len(refs), 1)
    self.assertEqual(self.git(self.repo, 'show', refs[0] + ':' + path), 'local repair')

  def test_staged_and_unstaged_source_restored_on_failure(self):
    path = 'openpilot/starpilot/ui/source.py'
    (self.repo / path).write_text('staged repair')
    self.git(self.repo, 'add', path)
    (self.repo / path).write_text('unstaged repair')
    with patch.object(self.params, 'put_bool', side_effect=RuntimeError('reboot failed')):
      with self.assertRaisesRegex(RuntimeError, 'reboot failed'):
        self.update()
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertEqual(self.git(self.repo, 'show', ':' + path), 'staged repair')
    self.assertEqual((self.repo / path).read_text(), 'unstaged repair')

  def test_dirty_version_listing_leaves_changes_and_backup_refs_untouched(self):
    path = self.repo / 'openpilot/starpilot/ui/source.py'
    path.write_text('local repair')
    self.assertEqual(self.update(versions_only=True), 'versions-listed')
    self.assertEqual(path.read_text(), 'local repair')
    self.assertEqual(self.git(self.repo, 'for-each-ref', 'refs/starpilot/local-changes'), '')

  def test_backup_failure_does_not_reset_local_changes(self):
    path = self.repo / 'openpilot/starpilot/ui/source.py'
    path.write_text('local repair')
    def failing(command, cwd):
      if 'stash' in command:
        raise RuntimeError('backup failed')
      return owner.run(command, cwd)
    with self.assertRaisesRegex(RuntimeError, 'backup failed'):
      self.update(run=failing)
    self.assertEqual(path.read_text(), 'local repair')
    self.assertFalse(self.invalidations)
    self.assertFalse(self.params.calls)

  def test_dirty_current_version_is_reinstalled_with_backup(self):
    self.git(self.remote, 'reset', '--hard', self.previous)
    path = self.repo / 'openpilot/starpilot/ui/source.py'
    path.write_text('local repair')
    self.assertEqual(self.update(), 'reboot-requested')
    self.assertEqual(path.read_text(), 'old')
    self.assertTrue(self.git(self.repo, 'for-each-ref', 'refs/starpilot/local-changes'))

  def test_backup_ref_failure_does_not_reset_or_invalidate(self):
    path = self.repo / 'openpilot/starpilot/ui/source.py'
    path.write_text('local repair')
    def failing(command, cwd):
      if 'update-ref' in command and any('local-changes/' in item for item in command):
        raise RuntimeError('backup ref failed')
      return owner.run(command, cwd)
    with self.assertRaisesRegex(RuntimeError, 'backup ref failed'):
      self.update(run=failing)
    self.assertEqual(path.read_text(), 'local repair')
    self.assertFalse(self.invalidations)
    self.assertFalse(self.params.calls)

  def test_reboot_write_failure_rolls_back(self):
    def failing(*args, **kwargs):
      raise RuntimeError('params write failed')

    with patch.object(self.params, "put_bool", side_effect=failing), self.assertRaisesRegex(RuntimeError, 'params write failed'):
      self.update()
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)

  def test_fetch_failure_does_not_mutate(self):
    def failing(command, cwd):
      if 'fetch' in command:
        raise RuntimeError('fetch failed')
      return owner.run(command, cwd)

    with self.assertRaisesRegex(RuntimeError, 'fetch failed'):
      self.update(run=failing)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse(self.invalidations)

  def test_apply_failure_rolls_back(self):
    failed = False

    def failing(command, cwd):
      nonlocal failed
      if 'reset' in command and command[-1] == self.target and not failed:
        failed = True
        owner.run(command, cwd)
        raise RuntimeError('apply failed after reset')
      return owner.run(command, cwd)

    with self.assertRaisesRegex(RuntimeError, 'apply failed'):
      self.update(run=failing)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertEqual((self.repo / 'openpilot/starpilot/ui/source.py').read_text(), 'old')
    self.assertFalse(self.params.calls)

  def test_lost_parked_before_reboot_rolls_back(self):
    states = iter([True, True, True, False])
    with self.assertRaises(owner.FastUpdateError):
      self.update(parked=lambda: next(states))
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse(self.params.calls)

  def test_lost_parked_before_source_mutation(self):
    states = iter([True, False])
    with self.assertRaises(owner.FastUpdateError):
      self.update(parked=lambda: next(states))
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse(self.invalidations)

  def test_invalid_vendor_target_rejected_before_checkout(self):
    (self.remote / 'upstream-sync.json').write_text('{}')
    self.git(self.remote, 'commit', '-am', 'invalid')
    with self.assertRaises(ValueError):
      self.update()
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse(self.invalidations)

  def test_selected_branch_applies_and_rollback_restores_exact_branch_and_commit(self):
    self.git(self.remote, 'checkout', '-b', 'Other')
    (self.remote / 'openpilot/starpilot/ui/source.py').write_text('other')
    self.git(self.remote, 'commit', '-am', 'other branch')
    other = self.git(self.remote, 'rev-parse', 'HEAD')
    stages = []
    arguments = {
      'params': self.params,
      'parked': lambda: True,
      'expected_commit': self.previous,
      'current_os': '19.8.1',
      'invalidate': lambda: None,
      'sleep': lambda seconds: stages.append(('sleep', seconds)),
      'progress': lambda stage, detail: stages.append((stage, detail)),
    }
    self.assertEqual(owner.fast_update(self.repo, 'Other', **arguments), 'reboot-requested')
    self.assertEqual(self.git(self.repo, 'symbolic-ref', '--short', 'HEAD'), 'Other')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), other)
    self.assertEqual(self.git(self.repo, 'config', '--get', 'starpilot.previousVersion'), f'{self.previous} Dom')
    self.assertIn(('sleep', 0.25), stages)
    arguments['expected_commit'] = other
    self.assertEqual(owner.fast_update(self.repo, 'Other', rollback=True, **arguments), 'reboot-requested')
    self.assertEqual(self.git(self.repo, 'symbolic-ref', '--short', 'HEAD'), 'Dom')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)

  def test_failed_branch_apply_restores_source_and_previous_version_receipt(self):
    self.git(self.remote, 'branch', 'Other')
    self.git(self.repo, 'update-ref', 'refs/starpilot/previous', self.previous)
    self.git(self.repo, 'config', 'starpilot.previousVersion', f'{self.previous} Retained')

    def failed(command, cwd):
      if 'checkout' in command and 'Other' in command:
        raise subprocess.CalledProcessError(1, command)
      return owner.run(command, cwd)

    with self.assertRaises(subprocess.CalledProcessError):
      owner.fast_update(
        self.repo,
        'Other',
        params=self.params,
        parked=lambda: True,
        expected_commit=self.previous,
        current_os='19.8.1',
        invalidate=lambda: None,
        run=failed,
        sleep=lambda _: None,
      )
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertEqual(self.git(self.repo, 'config', '--get', 'starpilot.previousVersion'), f'{self.previous} Retained')
    self.assertFalse(self.params.calls)

  def test_incomplete_previous_version_receipt_rejected_without_source_mutation(self):
    self.git(self.repo, 'update-ref', 'refs/starpilot/previous', self.previous)
    self.git(self.repo, 'config', 'starpilot.previousVersion', f"{'1' * 40} Dom")
    with self.assertRaisesRegex(owner.FastUpdateError, 'receipt is incomplete'):
      self.update(rollback=True)
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse(self.invalidations)
    self.assertFalse(self.params.calls)

  def test_restart_notice_rechecks_parked_and_rolls_back(self):
    waits = []
    parked = [True]

    def notice(seconds):
      waits.append(seconds)
      parked[0] = False

    with self.assertRaises(owner.FastUpdateError):
      self.update(parked=lambda: parked[0], sleep=notice)
    self.assertEqual(waits, [0.25])
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse(self.params.calls)

  def test_untracked_collision_rejected(self):
    (self.repo / 'new-model').write_bytes(b'model')
    (self.remote / 'new-model').write_text('tracked update')
    self.git(self.remote, 'add', 'new-model')
    self.receipt_commit()
    with self.assertRaisesRegex(owner.FastUpdateError, 'overwrite untracked data'):
      self.update()
    self.assertEqual((self.repo / 'new-model').read_bytes(), b'model')
    self.assertFalse(self.invalidations)

  def test_ignored_model_cache_is_not_scanned_or_removed(self):
    self.git(self.repo, 'config', 'core.excludesFile', str(self.repo / '.git/local-ignore'))
    (self.repo / '.git/local-ignore').write_text('models/\n')
    cache = self.repo / 'models/nested/download'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'keep downloaded model')
    commands = []

    def traced(command, cwd):
      commands.append(command)
      return owner.run(command, cwd)

    self.assertEqual(self.update(run=traced), 'reboot-requested')
    self.assertEqual(cache.read_bytes(), b'keep downloaded model')
    self.assertFalse(any('--others' in command for command in commands))

  def test_ignored_update_collision_is_rejected(self):
    self.git(self.repo, 'config', 'core.excludesFile', str(self.repo / '.git/local-ignore'))
    (self.repo / '.git/local-ignore').write_text('[[]model]\n')
    local = self.repo / '[model]'
    local.write_bytes(b'private model')
    (self.remote / '[model]').write_bytes(b'new tracked model')
    self.git(self.remote, 'add', '[model]')
    self.receipt_commit()
    with self.assertRaisesRegex(owner.FastUpdateError, 'overwrite untracked data'):
      self.update()
    self.assertEqual(local.read_bytes(), b'private model')
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)

  def test_untracked_parent_file_and_directory_contents_are_protected(self):
    for parent_file in (True, False):
      with self.subTest(parent_file=parent_file):
        folder = self.repo / 'new-data'
        target = self.remote / 'new-data'
        if parent_file:
          folder.write_bytes(b'private data')
          target.mkdir()
          (target / 'source').write_text('new tracked source')
        else:
          folder.mkdir()
          (folder / 'download').write_bytes(b'private data')
          target.write_text('new tracked source')
        self.git(self.remote, 'add', 'new-data')
        self.receipt_commit()
        with self.assertRaisesRegex(owner.FastUpdateError, 'overwrite untracked data'):
          self.update()
        self.assertEqual((folder if parent_file else folder / 'download').read_bytes(), b'private data')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
        self.git(self.remote, 'reset', '--hard', self.target)
        if parent_file:
          folder.unlink()
        else:
          (folder / 'download').unlink()
          folder.rmdir()

  def test_tracked_directory_can_be_replaced_by_file(self):
    self.git(self.remote, 'rm', '-r', 'openpilot/starpilot/ui')
    (self.remote / 'openpilot/starpilot').mkdir(parents=True, exist_ok=True)
    (self.remote / 'openpilot/starpilot/ui').write_text('replacement')
    self.git(self.remote, 'add', '.')
    self.receipt_commit()
    self.assertEqual(self.update(), 'reboot-requested')
    self.assertEqual((self.repo / 'openpilot/starpilot/ui').read_text(), 'replacement')

  def test_post_apply_validation_failure_rolls_back(self):
    original = owner.validate_revision

    def validate(repo, revision):
      if revision == 'HEAD':
        raise ValueError('post-check failed')
      return original(repo, revision)

    with patch.object(owner, 'validate_revision', validate):
      with self.assertRaisesRegex(ValueError, 'post-check failed'):
        self.update()
    self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.previous)
    self.assertFalse(self.params.calls)


if __name__ == '__main__':
  unittest.main()
