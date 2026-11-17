import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]


def load(name):
  spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


runtime = load('branch_migration')
creator = load('create_branch_migration')


class BranchMigrationTest(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)

  def git(self, repo, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(repo), *args],
                                   stderr=subprocess.STDOUT, text=True).strip()

  def fixture(self, source='Dom', destination='testing', modern=None):
    modern = source == 'Dom' if modern is None else modern
    remote = self.root / ('remote-' + source)
    remote.mkdir()
    self.git(remote, 'init', '-b', source)
    self.git(remote, 'config', 'user.name', 'Migration test')
    self.git(remote, 'config', 'user.email', 'test@example.invalid')
    (remote / 'launch_chffrplus.sh').write_text('#!/bin/bash\nDIR="."\nsource "$DIR/launch_env.sh"\n')
    (remote / 'launch_chffrplus.sh').chmod(0o755)
    (remote / 'payload').write_text('existing software and data')
    (remote / 'prebuilt').write_text('')
    if source == 'StarPilot':
      version = remote / 'selfdrive/ui/lib/starpilot_version.py'
      version.parent.mkdir(parents=True)
      version.write_text('STARPILOT_DISPLAY_VERSION = "6.7.9"\n')
    self.git(remote, 'add', '.')
    if modern:
      spec = importlib.util.spec_from_file_location('receipt', SCRIPTS.parent / 'openpilot/common/prebuilt_manifest.py')
      receipt = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(receipt)
      tree = self.git(remote, 'write-tree')
      (remote / 'prebuilt.json').write_text(json.dumps({'schema_version': 1,
        'protected_inventory_sha256': receipt.revision_digest(lambda *args: self.git(remote, *args), tree)}))
      self.git(remote, 'add', 'prebuilt.json')
    self.git(remote, 'commit', '-m', 'Existing release')
    parent = self.git(remote, 'rev-parse', 'HEAD')
    migration = creator.prepare(remote, source, destination, parent, 'refs/heads/review/' + destination)
    self.git(remote, 'update-ref', 'refs/heads/' + source, migration['commit'], parent)
    self.git(remote, 'update-ref', 'refs/heads/' + destination, migration['commit'])
    self.git(remote, 'reset', '--hard', migration['commit'])
    repo = self.root / ('device-' + source)
    self.git(self.root, 'clone', '--depth=1', '--single-branch', '--branch', source, remote.as_uri(), str(repo))
    params = self.root / ('params-' + source)
    params.mkdir()
    (params / 'UpdaterTargetBranch').write_text(source)
    (params / 'preserved-settings-models-recordings').write_bytes(b'unchanged')
    return remote, repo, params, migration

  def assert_migrated(self, repo, params, migration):
    self.assertEqual(self.git(repo, 'symbolic-ref', '--short', 'HEAD'), migration['destination'])
    self.assertEqual(self.git(repo, 'rev-parse', 'HEAD'), migration['commit'])
    self.assertEqual(self.git(repo, 'rev-parse', 'refs/heads/' + migration['source']), migration['commit'])
    self.assertEqual(self.git(repo, 'config', 'branch.' + migration['destination'] + '.remote'), 'origin')
    self.assertEqual(self.git(repo, 'config', 'branch.' + migration['destination'] + '.merge'), 'refs/heads/' + migration['destination'])
    self.assertIn('+refs/heads/' + migration['destination'] + ':refs/remotes/origin/' + migration['destination'],
                  self.git(repo, 'config', '--get-all', 'remote.origin.fetch'))
    self.assertEqual((params / 'UpdaterTargetBranch').read_text(), migration['destination'])
    self.assertEqual((params / 'preserved-settings-models-recordings').read_bytes(), b'unchanged')
    self.assertEqual(self.git(repo, 'status', '--porcelain'), '')

  def test_both_channels_depth_one_without_parent_and_offline(self):
    for source, destination in (('Dom', 'testing'), ('StarPilot', 'stable')):
      with self.subTest(source=source):
        remote, repo, params, migration = self.fixture(source, destination)
        parent = subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', migration['parent']], capture_output=True)
        self.assertNotEqual(parent.returncode, 0)
        self.git(repo, 'remote', 'set-url', 'origin', str(self.root / 'offline'))
        self.assertEqual(runtime.migrate(repo, params), 'migrated to ' + destination)
        self.assert_migrated(repo, params, migration)
        self.assertEqual(runtime.migrate(repo, params), 'already migrated')
        self.assertFalse((repo / '.git/starpilot-branch-migration.pending').exists())

  def test_every_interrupted_step_retries_without_data_loss(self):
    for step in ('pending', 'destination', 'head', 'remote', 'upstream', 'fetch', 'params', 'completed'):
      with self.subTest(step=step):
        case = self.root / step
        case.mkdir()
        original = self.root
        self.root = case
        try:
          _, repo, params, migration = self.fixture()
          def interrupt(current, interrupted_step=step):
            if current == interrupted_step:
              raise RuntimeError('simulated interruption')
          with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
            runtime.migrate(repo, params, interrupt)
          runtime.migrate(repo, params)
          self.assert_migrated(repo, params, migration)
          self.assertFalse((repo / '.git/starpilot-branch-migration.pending').exists())
        finally:
          self.root = original

  def test_destination_advance_is_fetched_without_resetting_installed_code(self):
    remote, repo, params, migration = self.fixture()
    self.git(remote, 'checkout', 'testing')
    (remote / 'payload').write_text('new testing release')
    self.git(remote, 'commit', '-am', 'Later destination release')
    latest = self.git(remote, 'rev-parse', 'HEAD')
    runtime.migrate(repo, params)
    self.assert_migrated(repo, params, migration)
    self.git(repo, 'fetch', 'origin')
    self.assertEqual(self.git(repo, 'rev-parse', '@{upstream}'), latest)
    self.assertEqual(self.git(repo, 'rev-parse', 'HEAD'), migration['commit'])

  def test_rollback_or_manual_switch_never_chains(self):
    _, repo, params, migration = self.fixture()
    runtime.migrate(repo, params)
    self.git(repo, 'checkout', 'Dom')
    (params / 'UpdaterTargetBranch').write_text('Dom')
    self.assertEqual(runtime.migrate(repo, params), 'already migrated')
    self.assertEqual(self.git(repo, 'symbolic-ref', '--short', 'HEAD'), 'Dom')
    self.git(repo, 'checkout', '-b', 'StarPilot')
    (params / 'UpdaterTargetBranch').write_text('StarPilot')
    self.assertEqual(runtime.migrate(repo, params), 'already migrated')
    self.assertEqual(self.git(repo, 'symbolic-ref', '--short', 'HEAD'), 'StarPilot')
    self.assertFalse((repo / '.git/refs/heads/stable').exists())

  def test_inherited_plan_is_inert_even_after_future_force_push(self):
    _, repo, params, _ = self.fixture()
    self.git(repo, 'config', 'user.name', 'Test')
    self.git(repo, 'config', 'user.email', 'test@example.invalid')
    (repo / 'payload').write_text('future rebase')
    self.git(repo, 'commit', '-am', 'Future rebase release')
    self.git(repo, 'branch', '-m', 'StarPilot')
    (params / 'UpdaterTargetBranch').write_text('StarPilot')
    self.assertEqual(runtime.migrate(repo, params), 'not migration release')
    self.assertEqual(self.git(repo, 'symbolic-ref', '--short', 'HEAD'), 'StarPilot')

  def test_manual_updater_target_is_preserved(self):
    _, repo, params, _ = self.fixture()
    (params / 'UpdaterTargetBranch').write_text('dev')
    self.assertEqual(runtime.migrate(repo, params), 'manual updater selection')
    self.assertEqual((params / 'UpdaterTargetBranch').read_text(), 'dev')
    self.assertEqual(self.git(repo, 'symbolic-ref', '--short', 'HEAD'), 'Dom')

  def test_existing_different_destination_is_not_overwritten(self):
    _, repo, params, migration = self.fixture()
    tree = self.git(repo, 'rev-parse', 'HEAD^{tree}')
    other = subprocess.check_output(['git', '-C', str(repo), '-c', 'user.name=Test', '-c',
                                     'user.email=test@example.invalid', 'commit-tree', tree], input=b'Other work\n').decode().strip()
    self.git(repo, 'update-ref', 'refs/heads/testing', other)
    with self.assertRaisesRegex(ValueError, 'different work'):
      runtime.migrate(repo, params)
    self.assertEqual(self.git(repo, 'rev-parse', 'refs/heads/testing'), other)

  def test_missing_plan_is_inert(self):
    repo = self.root / 'no-plan'
    repo.mkdir()
    self.assertEqual(runtime.migrate(repo, self.root / 'no-params'), 'no plan')

  def test_changed_target_during_migration_is_not_overwritten(self):
    _, repo, params, _ = self.fixture()
    def change(step):
      if step == 'fetch':
        (params / 'UpdaterTargetBranch').write_text('dev')
    with self.assertRaisesRegex(ValueError, 'selection changed'):
      runtime.migrate(repo, params, change)
    self.assertEqual((params / 'UpdaterTargetBranch').read_text(), 'dev')
    self.assertEqual(runtime.migrate(repo, params), 'manual updater selection')

  def test_generator_refreshes_canonical_modern_receipt(self):
    _, repo, params, migration = self.fixture(modern=True)
    spec = importlib.util.spec_from_file_location('receipt', SCRIPTS.parent / 'openpilot/common/prebuilt_manifest.py')
    receipt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(receipt)
    self.assertTrue(receipt.valid_receipt(lambda *args: self.git(repo, *args), 'HEAD'))
    self.assertEqual(self.git(repo, 'log', '-1', '--format=%B').splitlines(),
                     [f"Branch-Migration: Dom -> testing ({migration['token']})"])
    runtime.migrate(repo, params)
    self.assert_migrated(repo, params, migration)

  def test_local_source_edits_and_param_mode_survive(self):
    _, repo, params, _ = self.fixture()
    (repo / 'payload').write_text('local work')
    (params / 'UpdaterTargetBranch').chmod(0o640)
    runtime.migrate(repo, params)
    self.assertEqual((repo / 'payload').read_text(), 'local work')
    self.assertEqual((params / 'UpdaterTargetBranch').stat().st_mode & 0o777, 0o640)

  def test_wrong_parent_or_title_cannot_trigger_migration(self):
    _, repo, params, _ = self.fixture()
    plan = json.loads((repo / runtime.PLAN).read_text())
    plan['parent'] = '0' * 40
    (repo / runtime.PLAN).write_text(json.dumps(plan))
    self.assertEqual(runtime.migrate(repo, params), 'not migration release')
    self.assertEqual(self.git(repo, 'symbolic-ref', '--short', 'HEAD'), 'Dom')

  def test_pending_does_not_follow_manual_branch_switch(self):
    _, repo, params, _ = self.fixture()
    def stop(step):
      if step == 'pending':
        raise RuntimeError('interrupted')
    with self.assertRaises(RuntimeError):
      runtime.migrate(repo, params, stop)
    self.git(repo, 'checkout', '-b', 'dev')
    self.assertEqual(runtime.migrate(repo, params), 'manual branch selection')
    self.assertEqual(self.git(repo, 'symbolic-ref', '--short', 'HEAD'), 'dev')

  def test_conflicting_pending_marker_is_not_overwritten(self):
    _, repo, params, _ = self.fixture()
    marker = repo / '.git/starpilot-branch-migration.pending'
    marker.write_text('{"different": true}')
    with self.assertRaisesRegex(ValueError, 'Another migration'):
      runtime.migrate(repo, params)
    self.assertEqual(marker.read_text(), '{"different": true}')

  def test_old_installs_without_updater_preference_are_supported(self):
    _, repo, params, migration = self.fixture('StarPilot', 'stable')
    (params / 'UpdaterTargetBranch').unlink()
    runtime.migrate(repo, params)
    self.assert_migrated(repo, params, migration)

  def test_generator_refuses_cross_channel_plan(self):
    with self.assertRaisesRegex(ValueError, 'Only Dom'):
      creator.prepare(self.root, 'testing', 'stable', 'HEAD', 'refs/heads/review/bad')

  def test_malformed_plan_is_refused_without_moving_branch(self):
    _, repo, params, _ = self.fixture()
    (repo / runtime.PLAN).write_text('[]')
    with self.assertRaisesRegex(ValueError, 'Invalid channel'):
      runtime.migrate(repo, params)
    self.assertEqual(self.git(repo, 'symbolic-ref', '--short', 'HEAD'), 'Dom')


if __name__ == '__main__':
  unittest.main()
