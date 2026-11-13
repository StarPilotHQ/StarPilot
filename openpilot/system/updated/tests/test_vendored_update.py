"""Updater Git integration against temporary local repositories.

Native Params, logging, hardware, and consistency syncing are explicit test doubles.
Git fetch/checkout/reset/clean and finalization copies run against real temporary
repositories. No device paths, mounts, firmware calls, or network remotes are used.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch


from openpilot.common.vendor_manifest import DEPENDENCIES
from openpilot.starpilot.software.auto_install import InstallSettings

BRANCH = "test-vendored"


class MemoryParams:
  def __init__(self):
    self.values = {"UpdaterTargetBranch": BRANCH}

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key):
    return bool(self.values.get(key))

  def put(self, key, value, **kwargs):
    self.values[key] = value

  put_bool = put


def load_updater():
  """Load unchanged updater code with explicit service boundaries substituted."""
  services = {
    "openpilot.common.params": {"Params": MemoryParams},
    "openpilot.common.time_helpers": {"system_time_valid": Mock()},
    "openpilot.common.markdown": {"parse_markdown": Mock()},
    "openpilot.common.swaglog": {"cloudlog": Mock()},
    "openpilot.selfdrive.selfdrived.alertmanager": {"set_offroad_alert": Mock()},
    "openpilot.common.hardware": {"AGNOS": False, "HARDWARE": Mock()},
    "openpilot.starpilot.drive_state.evidence": {"PhysicalSource": Mock()},
    "openpilot.common.version": {"get_build_metadata": Mock()},
  }
  modules = {}
  for name, attributes in services.items():
    module = ModuleType(name)
    module.__dict__.update(attributes)
    modules[name] = module
  path = Path(__file__).resolve().parents[1] / "updated.py"
  spec = importlib.util.spec_from_file_location("updater_git_test", path)
  assert spec is not None and spec.loader is not None
  updated = importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules, modules):
    spec.loader.exec_module(updated)
  return updated


class TestVendoredUpdate(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.source = self.root / "source"
    self.merged = self.root / "merged"
    self.finalized = self.root / "finalized"
    self.source.mkdir()
    self.env_patch = patch.dict(os.environ, {
      "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
      "GIT_AUTHOR_NAME": "Updater test", "GIT_COMMITTER_NAME": "Updater test",
      "GIT_AUTHOR_EMAIL": "updater@example.invalid", "GIT_COMMITTER_EMAIL": "updater@example.invalid",
    })
    self.env_patch.start()
    self.addCleanup(self.env_patch.stop)
    self.git(self.source, "init", "-b", BRANCH)
    for name in DEPENDENCIES:
      folder = self.source / name
      folder.mkdir()
      (folder / "README.md").write_text(f"{name} tracked source\n")
    self.manifest = {
      "schema_version": 1,
      "upstream": {"url": "https://github.com/commaai/openpilot.git", "commit": "1" * 40},
      "dependencies": [
        {"path": name, "url": f"https://github.com/commaai/{name}.git", "commit": "2" * 40, "tree": "3" * 40, "exclude": []}
        for name in DEPENDENCIES
      ],
    }
    self.write_manifest()
    self.initial_commit = self.commit_source("Initial vendored tree")
    self.git(self.root, "clone", "--no-local", "--no-hardlinks", str(self.source), str(self.merged))
    # Simulate inherited Git settings that would otherwise recurse on fetch/reset.
    self.git(self.merged, "config", "submodule.recurse", "true")
    self.git(self.merged, "config", "fetch.recurseSubmodules", "true")

    self.updated = load_updater()
    self.updated.TRUSTED_LEGACY_RELEASES = {}
    self.updated.OVERLAY_MERGED = str(self.merged)
    self.updated.METADATA_ROOT = self.root / 'metadata.git'
    self.updated.FINALIZED = str(self.finalized)
    self.updated.BASEDIR = str(self.merged)
    self.updated.set_consistent_flag = self.set_consistent_flag
    self.updated.handle_agnos_update = Mock()
    self.updated.init_overlay = Mock()
    self.commands = []
    real_run = self.updated.run

    def bounded_run(command, cwd=None, **kwargs):
      if cwd is None:
        raise AssertionError("Every updater command must name its isolated working directory")
      self.assertTrue(Path(cwd).resolve().is_relative_to(self.root.resolve()))
      self.assertNotEqual(command[:2], ["git", "submodule"])
      self.commands.append(command)
      if command in (["git", "gc"], ["git", "lfs", "prune"]):
        return ""
      return real_run(command, cwd, **kwargs)

    self.updated.run = bounded_run
    self.updater = self.updated.Updater(install_settings=InstallSettings(self.root / 'install-settings'))
    self.addCleanup(self.updater.close)

  @staticmethod
  def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.STDOUT, text=True).strip()

  def write_manifest(self):
    (self.source / "upstream-sync.json").write_text(json.dumps(self.manifest))

  def commit_source(self, message):
    self.git(self.source, "add", "--all")
    self.git(self.source, "commit", "-m", message)
    return self.git(self.source, "rev-parse", "HEAD")

  def set_consistent_flag(self, consistent):
    marker = self.finalized / ".overlay_consistent"
    if consistent:
      marker.touch()
    else:
      marker.unlink(missing_ok=True)

  def assert_rejected_before_checkout(self):
    self.updated.AGNOS = True
    with self.assertRaises(ValueError):
      self.updater.fetch_update()
    self.assertEqual(self.git(self.merged, "rev-parse", "HEAD"), self.initial_commit)
    self.assertFalse(any(command[:2] == ["git", "checkout"] for command in self.commands))
    self.assertFalse((self.finalized / ".overlay_consistent").exists())
    self.updated.handle_agnos_update.assert_not_called()
    self.assertFalse(self.updater.params.values["UpdateAvailable"])

  def test_automatic_update_uses_fast_path_and_only_falls_back_on_incompatibility(self):
    fast_update = importlib.import_module("openpilot.starpilot.software.fast_update")
    for error in (None, self.updated.FastUpdateIncompatible("AGNOS changed"), self.updated.FastUpdateError("Tracked edits")):
      with self.subTest(error=error), patch.object(fast_update, "fast_update", side_effect=error, return_value="reboot-requested"), \
           patch.object(self.updater, "normal_update", return_value="reboot-requested") as normal:
        if error is not None and not isinstance(error, self.updated.FastUpdateIncompatible):
          with self.assertRaises(self.updated.FastUpdateError):
            self.updater.fast_update(BRANCH, self.initial_commit, automatic=True)
        else:
          self.assertEqual(self.updater.fast_update(BRANCH, self.initial_commit, automatic=True), "reboot-requested")
        self.assertEqual(normal.call_count, int(isinstance(error, self.updated.FastUpdateIncompatible)))
    with patch.object(fast_update, "fast_update", side_effect=self.updated.FastUpdateIncompatible("AGNOS changed")), \
         patch.object(self.updater, "normal_update") as normal:
      with self.assertRaises(self.updated.FastUpdateIncompatible):
        self.updater.fast_update(BRANCH, self.initial_commit)
      normal.assert_not_called()  # Explicit fast/rollback/version operations keep their contract.

  @patch('openpilot.starpilot.drive_state.evidence.PhysicalSource', return_value=Mock(update_ready=Mock(return_value=True)))
  def test_download_selected_version_stages_exact_commit_without_installing(self, _physical):
    installed = self.root / 'installed'
    self.git(self.root, 'clone', '--no-local', str(self.merged), str(installed))
    self.updated.BASEDIR = str(installed)
    self.git(installed, 'remote', 'set-url', 'origin', str(self.source))
    (self.source / 'version.txt').write_text('selected')
    selected = self.commit_source('Selected version')
    (self.source / 'version.txt').write_text('newest')
    latest = self.commit_source('Newer version')
    with patch.object(self.updater, 'set_params') as publish:
      self.assertEqual(self.updater.fast_update(BRANCH, self.initial_commit, selected, download_only=True), 'downloaded')
    publish.assert_called_once_with(True, 0, None)
    self.assertEqual(self.git(installed, 'rev-parse', 'HEAD'), self.initial_commit)
    self.assertEqual(self.git(self.finalized, 'rev-parse', 'HEAD'), selected)
    self.assertEqual(self.git(self.finalized, 'rev-parse', 'refs/starpilot/previous'), self.initial_commit)
    self.assertEqual(self.updater.branches[BRANCH], latest)
    self.assertTrue(self.updater.update_ready)
    self.assertFalse(self.updater.latest_update_ready)
    self.assertFalse(self.updater.params.get_bool('DoReboot'))

  @patch('openpilot.starpilot.drive_state.evidence.PhysicalSource', return_value=Mock(update_ready=Mock(return_value=True)))
  def test_download_selected_version_rejects_changed_source_and_unlisted_target(self, _physical):
    for expected, selected in (('f' * 40, self.initial_commit), (self.initial_commit, 'f' * 40)):
      with self.subTest(expected=expected), self.assertRaises(self.updated.FastUpdateError):
        self.updater.fast_update(BRANCH, expected, selected, download_only=True)
    self.updated.init_overlay.assert_not_called()
    self.assertEqual(self.git(self.merged, 'rev-parse', 'HEAD'), self.initial_commit)
    self.assertFalse(self.updater.params.get_bool('DoReboot'))

  def make_legacy_release(self, branch='StarPilot'):
    self.git(self.source, 'checkout', '-b', branch)
    (self.source / 'upstream-sync.json').unlink()
    files = {'prebuilt': '', 'launch_env.sh': 'export AGNOS_VERSION="19.8.1"\nexport AGNOS_ACCEPTED_VERSIONS="19.8.1 20.2.1"\n',
             'launch_openpilot.sh': '#!/bin/sh\n', 'launch_chffrplus.sh': '#!/bin/sh\n',
             'selfdrive/ui/lib/starpilot_version.py': 'STARPILOT_DISPLAY_VERSION = "6.7.9"\n',
             'selfdrive/car/card.py': 'pass\n', 'selfdrive/modeld/modeld.py': 'pass\n'}
    for name, content in files.items():
      path = self.source / name
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(content)
      if name in ('launch_openpilot.sh', 'launch_chffrplus.sh'):
        path.chmod(0o755)
    target = self.commit_source('Legacy prebuilt release')
    self.updated.TRUSTED_LEGACY_RELEASES[branch] = frozenset({target})
    return target

  @patch('openpilot.starpilot.drive_state.evidence.PhysicalSource', return_value=Mock(update_ready=Mock(return_value=True)))
  def test_download_legacy_starpilot_stages_without_reboot_or_flashing(self, _physical):
    installed = self.root / 'installed'
    self.git(self.root, 'clone', '--no-local', str(self.merged), str(installed))
    self.updated.BASEDIR = str(installed)
    self.git(installed, 'remote', 'set-url', 'origin', str(self.source))
    target = self.make_legacy_release()
    self.updated.AGNOS = True
    self.updated.HARDWARE.get_os_version.return_value = '20.2.1'
    with patch.object(self.updater, 'set_params'):
      self.assertEqual(self.updater.fast_update('StarPilot', self.initial_commit, target, download_only=True), 'downloaded')
    self.assertEqual(self.git(installed, 'rev-parse', 'HEAD'), self.initial_commit)
    self.assertEqual(self.git(self.finalized, 'rev-parse', 'HEAD'), target)
    self.assertTrue(self.updater.update_ready)
    self.assertFalse(self.updater.params.get_bool('DoReboot'))
    self.updated.handle_agnos_update.assert_not_called()
    self.updated.HARDWARE.get_os_version.return_value = 'unverified'
    with self.assertRaises(self.updated.FastUpdateIncompatible):
      self.updated.validate_update_revision(str(self.finalized), 'HEAD', 'StarPilot')
    self.updater.params.put('UpdaterState', 'idle')
    self.updater.params.put_bool('IsOffroad', True)
    settings = self.updater.install_settings
    settings.save(True, '* * * * *', settings.read()['revision'], lambda: True)
    with patch.object(self.updated, 'system_time_valid', return_value=True):
      self.assertFalse(self.updater.maybe_install())
    self.assertFalse(self.updater.params.get_bool('DoReboot'))

  def test_legacy_wrong_os_or_incomplete_release_rejected_before_checkout(self):
    target = self.make_legacy_release()
    self.updater.params.put('UpdaterTargetBranch', 'StarPilot')
    self.updated.HARDWARE.get_os_version.return_value = 'wrong'
    with self.assertRaises(self.updated.FastUpdateIncompatible):
      self.updater.fetch_update()
    self.assertEqual(self.git(self.merged, 'rev-parse', 'HEAD'), self.initial_commit)
    self.assertFalse(any(command[:2] == ['git', 'checkout'] for command in self.commands))
    self.assertFalse((self.finalized / '.overlay_consistent').exists())
    self.updated.HARDWARE.get_os_version.return_value = '19.8.1'
    (self.source / 'prebuilt').unlink()
    incomplete = self.commit_source('Incomplete legacy release')
    self.updated.TRUSTED_LEGACY_RELEASES['StarPilot'] = frozenset({incomplete})
    self.updater._branches_checked = False
    with self.assertRaises(self.updated.FastUpdateIncompatible):
      self.updater.fetch_update()
    self.assertFalse((self.finalized / '.overlay_consistent').exists())
    self.assertNotEqual(target, self.git(self.source, 'rev-parse', 'HEAD'))

  def test_legacy_exception_does_not_accept_other_branch_or_malformed_manifest(self):
    target = self.make_legacy_release()
    self.updated.HARDWARE.get_os_version.return_value = '19.8.1'
    with self.assertRaises(ValueError):
      self.updated.validate_update_revision(str(self.source), target, 'other')
    (self.source / 'upstream-sync.json').write_text('{}')
    target = self.commit_source('Invalid modern manifest')
    with self.assertRaises(ValueError):
      self.updated.validate_update_revision(str(self.source), target, 'StarPilot')

  def test_legacy_return_rejects_gitlinks_and_submodules(self):
    self.make_legacy_release()
    self.updated.HARDWARE.get_os_version.return_value = '19.8.1'
    self.git(self.source, 'update-index', '--add', '--cacheinfo', f'160000,{self.initial_commit},unsafe')
    self.git(self.source, 'commit', '-m', 'Invalid legacy gitlink')
    self.updated.TRUSTED_LEGACY_RELEASES['StarPilot'] = frozenset({self.git(self.source, 'rev-parse', 'HEAD')})
    with self.assertRaises(self.updated.FastUpdateIncompatible):
      self.updated.validate_update_revision(str(self.source), 'HEAD', 'StarPilot')
    self.git(self.source, 'rm', '--cached', 'unsafe')
    (self.source / '.gitmodules').write_text('[submodule "unsafe"]\npath = unsafe\n')
    self.commit_source('Invalid legacy submodule metadata')
    self.updated.TRUSTED_LEGACY_RELEASES['StarPilot'] = frozenset({self.git(self.source, 'rev-parse', 'HEAD')})
    with self.assertRaises(self.updated.FastUpdateIncompatible):
      self.updated.validate_update_revision(str(self.source), 'HEAD', 'StarPilot')

  def test_untrusted_legacy_commit_is_rejected_even_if_it_looks_complete(self):
    target = self.make_legacy_release()
    self.updated.TRUSTED_LEGACY_RELEASES.clear()
    self.updated.HARDWARE.get_os_version.return_value = '19.8.1'
    with self.assertRaises(ValueError):
      self.updated.validate_update_revision(str(self.source), target, 'StarPilot')
    audited = load_updater().TRUSTED_LEGACY_RELEASES
    self.assertEqual(audited, {'StarPilot': frozenset({'c2e5e469ee8fefc3cffb514dc2e1a1fb82a421d6'})})

  def test_reserved_readiness_marker_rejected_in_modern_and_legacy_trees(self):
    for kind in ('modern-file', 'modern-symlink', 'legacy-file', 'legacy-symlink'):
      with self.subTest(kind=kind):
        if kind.startswith('legacy') and self.git(self.source, 'branch', '--show-current') != 'StarPilot':
          self.make_legacy_release()
          self.updated.HARDWARE.get_os_version.return_value = '19.8.1'
        marker = self.source / '.overlay_consistent'
        if kind.endswith('symlink'):
          marker.symlink_to('prebuilt')
        else:
          marker.write_text('premature')
        target = self.commit_source(kind)
        branch = 'StarPilot' if kind.startswith('legacy') else BRANCH
        self.updated.TRUSTED_LEGACY_RELEASES[branch] = frozenset({target})
        with self.assertRaisesRegex(ValueError, 'reserved update readiness'):
          self.updated.validate_update_revision(str(self.source), target, branch)
        marker.unlink()
        self.commit_source('Remove marker')

  def test_copy_interruption_never_copies_an_untracked_readiness_marker(self):
    marker = self.merged / '.overlay_consistent'
    marker.symlink_to('panda/README.md')
    original_copy = shutil.copy2
    def fail_during_copy(src, dst, *args, **kwargs):
      if Path(src).name == 'README.md':
        raise OSError('interrupted copy')
      return original_copy(src, dst, *args, **kwargs)
    with patch.object(self.updated.shutil, 'copy2', side_effect=fail_during_copy):
      with self.assertRaises(shutil.Error):
        self.updated.finalize_update()
    ready = self.finalized / '.overlay_consistent'
    self.assertFalse(ready.exists())
    self.assertFalse(ready.is_symlink())

  def test_metadata_refresh_does_not_wake_background_but_check_does(self):
    (self.source / 'new.txt').write_text('available')
    self.commit_source('New update')
    self.updater.wake_automatic = Mock()
    with patch.object(self.updated, 'download_permitted', return_value=True):
      self.updater.request_check(branch=BRANCH)
      worker = self.updater._check_thread
      if worker is not None:
        worker.join(timeout=5)
      self.updater.wake_automatic.assert_not_called()
      self.updated.init_overlay.assert_not_called()
      self.updater.request_check(branch=BRANCH, download_after=True)
      worker = self.updater._check_thread
      if worker is not None:
        worker.join(timeout=5)
    self.updater.wake_automatic.assert_called_once_with()

  def test_scheduled_install_requires_clock_idle_and_fresh_offroad_evidence(self):
    installed = self.root / 'installed'
    self.git(self.root, 'clone', '--no-local', str(self.merged), str(installed))
    self.updated.BASEDIR = str(installed)
    self.git(installed, 'remote', 'set-url', 'origin', str(self.source))
    (self.source / 'scheduled.txt').write_text('prepared')
    self.commit_source('Prepared update for scheduling')
    self.assertTrue(self.updater.fetch_update())
    self.updater.params.put('UpdaterState', 'idle')
    self.updater.params.put_bool('IsOffroad', True)
    settings = self.updater.install_settings
    current = settings.read()
    assert current is not None
    settings.save(True, '* * * * *', current['revision'], lambda: True)
    physical = Mock(update_ready=Mock(return_value=True))
    with patch('openpilot.starpilot.drive_state.evidence.PhysicalSource', return_value=physical), \
         patch.object(self.updated, 'system_time_valid', return_value=False):
      self.assertFalse(self.updater.maybe_install())
    with patch('openpilot.starpilot.drive_state.evidence.PhysicalSource', return_value=physical), \
         patch.object(self.updated, 'system_time_valid', return_value=True):
      self.assertFalse(self.updater.maybe_install(lambda: False))
      self.updater.params.put_bool('IsOffroad', False)
      self.assertFalse(self.updater.maybe_install())
      self.assertFalse(self.updater.params.get_bool('DoReboot'))
      physical.update_ready.assert_not_called()
      self.updater.params.put_bool('IsOffroad', True)
      self.assertTrue(self.updater.maybe_install())
    self.assertTrue(self.updater.params.get_bool('DoReboot'))
    self.assertEqual(self.git(installed, 'rev-parse', 'HEAD'), self.initial_commit)
    self.assertEqual(physical.update_ready.call_count, 2)

  def test_normal_fallback_stages_flashes_and_requests_reboot_with_fresh_parked_state(self):
    installed = self.root / "installed"
    self.git(self.root, "clone", "--no-local", str(self.merged), str(installed))
    self.updated.BASEDIR = str(installed)
    self.git(installed, 'remote', 'set-url', 'origin', str(self.source))
    (self.source / "panda/README.md").write_text("New native revision")
    target = self.commit_source("Full update")
    self.updated.AGNOS = True
    progress = Mock()
    with patch.object(self.updated, "init_overlay"):
      self.assertEqual(self.updater.normal_update(BRANCH, self.initial_commit, lambda: True, progress), "reboot-requested")
    self.updated.handle_agnos_update.assert_called_once()
    self.assertEqual(self.git(installed, "rev-parse", "HEAD"), self.initial_commit)
    self.assertEqual(self.git(self.finalized, "rev-parse", "HEAD"), target)
    self.assertTrue(self.updater.params.values["DoReboot"])
    self.assertTrue(self.updater.params.values["UpdateAvailable"])
    progress.assert_called_with("rebooting", "Update installed. Restarting device.")

  def test_normal_fallback_never_reboots_after_parked_authority_is_lost(self):
    installed = self.root / "installed"
    self.git(self.root, "clone", "--no-local", str(self.merged), str(installed))
    self.updated.BASEDIR = str(installed)
    self.git(installed, 'remote', 'set-url', 'origin', str(self.source))
    (self.source / "panda/README.md").write_text("New native revision")
    self.commit_source("Full update")
    parked = iter((True, False))
    with patch.object(self.updated, "init_overlay"), self.assertRaises(self.updated.FastUpdateError):
      self.updater.normal_update(BRANCH, self.initial_commit, lambda: next(parked), Mock())
    self.assertNotIn("DoReboot", self.updater.params.values)

  def test_check_reports_confirmed_os_requirement_without_changing_installed_files(self):
    self.updated.AGNOS = True
    self.updated.HARDWARE.get_os_version.return_value = "12"
    for launch, required in (("export AGNOS_VERSION='12'\n", False), ("export AGNOS_VERSION='13'\n", True),
                             ("# Unknown OS version\n", None)):
      with self.subTest(launch=launch):
        (self.source / "launch_env.sh").write_text(launch)
        self.commit_source("Change target OS version")
        self.updater.check_for_update()
        expected = {} if required is None else {"branch": BRANCH, "required": required}
        self.assertEqual(self.updater.params.values["UpdaterOSUpdate"], expected)
        self.assertEqual(self.git(self.merged, "rev-parse", "HEAD"), self.initial_commit)
        self.updated.handle_agnos_update.assert_not_called()

  def test_vendored_fetch_and_finalize(self):
    (self.source / "panda" / "README.md").write_text("Updated ordinary tracked source\n")
    target = self.commit_source("Update dependency source atomically")
    self.updater.fetch_update()
    self.assertEqual(self.git(self.merged, "rev-parse", "HEAD"), target)
    self.assertEqual(self.git(self.finalized, "rev-parse", "HEAD"), target)
    self.assertEqual((self.finalized / "panda" / "README.md").read_text(), "Updated ordinary tracked source\n")
    self.assertEqual(self.git(self.finalized, "rev-parse", f"refs/starpilot/recent/{BRANCH}"), target)
    self.assertTrue((self.finalized / ".overlay_consistent").is_file())
    for name in DEPENDENCIES:
      self.assertFalse((self.finalized / name / ".git").exists())
    self.assertFalse(any(line.startswith("160000 ") for line in self.git(self.finalized, "ls-tree", "-r", "HEAD").splitlines()))
    self.assertEqual(self.git(self.merged, "config", "submodule.recurse"), "false")
    self.assertEqual(self.git(self.merged, "config", "fetch.recurseSubmodules"), "false")
    self.updated.handle_agnos_update.assert_not_called()

  def test_filtered_fetch_preserves_history_without_obsolete_artifact_contents(self):
    self.git(self.source, 'config', 'uploadpack.allowFilter', 'true')
    self.git(self.source, 'config', 'uploadpack.allowReachableSHA1InWant', 'true')
    self.git(self.merged, 'remote', 'set-url', 'origin', self.source.as_uri())
    artifact = self.source / 'artifact.bin'
    artifact.write_bytes(os.urandom(128 * 1024))
    middle = self.commit_source('Superseded large artifact')
    obsolete_blob = self.git(self.source, 'rev-parse', 'HEAD:artifact.bin')
    artifact.write_bytes(os.urandom(128 * 1024))
    target = self.commit_source('Chosen large artifact')
    target_blob = self.git(self.source, 'rev-parse', 'HEAD:artifact.bin')
    self.updater.check_for_update()
    objects = set(self.git(self.updated.METADATA_ROOT, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)').splitlines())
    self.assertIn(target, objects)
    self.assertIn(middle, objects)
    self.assertNotIn(target_blob, objects)
    self.assertNotIn(obsolete_blob, objects)
    metadata_fetches = sum('fetch' in command for command in self.commands)
    self.assertTrue(self.updater.fetch_update())
    self.assertEqual(sum('fetch' in command for command in self.commands), metadata_fetches + 2)
    objects = set(self.git(self.merged, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)').splitlines())
    self.assertIn(target, objects)
    self.assertNotIn(middle, objects)
    self.assertIn(target_blob, objects)
    self.assertNotIn(obsolete_blob, objects)
    self.assertIn(middle, self.git(self.updated.METADATA_ROOT, 'log', '--first-parent', '--format=%H', f'refs/starpilot/recent/{BRANCH}').splitlines())
    self.assertEqual((self.finalized / 'artifact.bin').read_bytes(), artifact.read_bytes())

  def test_refresh_versions_fetches_only_metadata_in_staging(self):
    self.git(self.source, 'config', 'uploadpack.allowFilter', 'true')
    self.git(self.merged, 'remote', 'set-url', 'origin', self.source.as_uri())
    artifact = self.source / 'artifact.bin'
    artifact.write_bytes(os.urandom(128 * 1024))
    target = self.commit_source('Version metadata to refresh')
    blob = self.git(self.source, 'rev-parse', 'HEAD:artifact.bin')
    installed = self.root / 'installed'
    self.git(self.root, 'clone', '--no-local', str(self.merged), str(installed))
    self.updated.BASEDIR = str(installed)
    self.git(installed, 'remote', 'set-url', 'origin', str(self.source))
    self.git(self.root, 'clone', '--no-local', str(self.merged), str(self.finalized))
    marker = self.finalized / '.overlay_consistent'
    marker.touch()
    self.updater.params.values['IsOffroad'] = True
    with patch.object(self.updated, 'init_overlay'), \
         patch('openpilot.starpilot.drive_state.evidence.PhysicalSource', return_value=Mock(update_ready=Mock(return_value=True))):
      self.assertEqual(self.updater.fast_update(BRANCH, self.initial_commit, versions_only=True), 'versions-listed')
    objects = set(self.git(self.updated.METADATA_ROOT, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)').splitlines())
    self.assertIn(target, objects)
    self.assertNotIn(blob, objects)
    self.assertEqual(self.git(installed, 'rev-parse', 'HEAD'), self.initial_commit)
    self.assertFalse((installed / '.git/FETCH_HEAD').exists())
    self.assertTrue(marker.exists())
    self.assertNotIn('DoReboot', self.updater.params.values)
    self.assertTrue(self.updater.params.values['UpdaterFetchAvailable'])
    self.assertFalse(self.updater.params.values['UpdateAvailable'])

  def test_scheduled_check_stages_new_commit_when_automatic_downloads_are_enabled(self):
    (self.source / "panda/README.md").write_text("New automatic download\n")
    target = self.commit_source("Automatic download after checking")
    params = self.updater.params
    params.values["InstallDate"] = "already installed"
    preference = self.root / "UpdaterAutomaticDownloads"
    params.get_param_path = lambda key: str(self.root / key)
    for enabled in (False, True):
      with self.subTest(enabled=enabled):
        preference.write_bytes(b"1" if enabled else b"0")
        helper = Mock()
        helper.current_request.side_effect = [
          (self.updated.UserRequest.NONE, 0, None),
          (self.updated.UserRequest.NONE, 1, None),
          RuntimeError("test loop complete"),
        ]
        with patch.object(self.updated, "Params", return_value=params), \
             patch.object(self.updated, "Updater", return_value=self.updater), \
             patch.object(self.updated, "WaitTimeHelper", return_value=helper), \
             patch.object(self.updated, "init_overlay"), \
             patch.object(self.updated, "LOCK_FILE", str(self.root / "updater.lock")), \
             patch.object(self.updater, "set_params"), \
             self.assertRaisesRegex(RuntimeError, "test loop complete"):
          self.updated.main()
        self.assertEqual((self.finalized / ".overlay_consistent").exists(), enabled)
        self.assertEqual("UpdaterLastFetchTime" in params.values, enabled)
        self.assertNotIn("DoReboot", params.values)
        if enabled:
          self.assertEqual(self.git(self.finalized, "rev-parse", "HEAD"), target)
        else:
          self.assertEqual(self.git(self.merged, "rev-parse", "HEAD"), self.initial_commit)

  def test_up_to_date_check_does_not_repeat_automatic_staging(self):
    params = self.updater.params
    params.values['InstallDate'] = 'already installed'
    params.get_param_path = lambda key: str(self.root / key)
    helper = Mock()
    helper.current_request.side_effect = [
      (self.updated.UserRequest.NONE, 0, None),
      (self.updated.UserRequest.CHECK, 1, None),
      RuntimeError('test loop complete'),
    ]
    with patch.object(self.updated, 'Params', return_value=params), \
         patch.object(self.updated, 'Updater', return_value=self.updater), \
         patch.object(self.updated, 'WaitTimeHelper', return_value=helper), \
         patch.object(self.updated, 'init_overlay'), \
         patch.object(self.updated, 'LOCK_FILE', str(self.root / 'updater.lock')), \
         patch.object(self.updater, 'set_params'), \
         patch.object(self.updater, 'fetch_update') as download, \
         self.assertRaisesRegex(RuntimeError, 'test loop complete'):
      self.updated.main()
    download.assert_not_called()
    self.assertNotIn('UpdaterLastFetchTime', params.values)
    self.assertFalse(self.finalized.exists())

  def test_metadata_refresh_completes_while_background_finalization_is_blocked(self):
    installed = self.root / 'installed'
    self.git(self.root, 'clone', '--no-local', str(self.merged), str(installed))
    self.git(installed, 'remote', 'set-url', 'origin', str(self.source))
    self.updated.BASEDIR = str(installed)
    (self.source / 'panda/README.md').write_text('New background version')
    self.commit_source('Background download')
    entered, release = threading.Event(), threading.Event()
    finalize = self.updated.finalize_update
    failures = []
    def blocked_finalize(*args, **kwargs):
      entered.set()
      if not release.wait(8):
        raise RuntimeError('Test did not release finalization')
      return finalize(*args, **kwargs)
    def download():
      try:
        self.updater.fetch_update(background=True)
      except Exception as error:
        failures.append(error)
    with patch.object(self.updated, 'finalize_update', side_effect=blocked_finalize):
      worker = threading.Thread(target=download)
      worker.start()
      try:
        self.assertTrue(entered.wait(4))
        started = time.monotonic()
        self.updater.request_check(branch=BRANCH, commit=self.initial_commit)
        deadline = started + 4
        while time.monotonic() < deadline:
          check = self.updater.params.values['UpdaterFastState'].get('check', {})
          if check.get('completedAt', 0) >= started:
            break
          time.sleep(0.01)
        self.assertEqual(check['state'], 'complete')
        self.assertGreaterEqual(check['completedAt'], started)
        self.assertEqual(self.updater.params.values['UpdaterState'], 'finalizing update...')
        self.assertTrue(self.updater.params.values['UpdaterFastState']['background'])
      finally:
        release.set()
        worker.join(5)
    self.assertFalse(worker.is_alive())
    self.assertEqual(failures, [])

  def test_finalized_runtime_files_are_independent_and_git_objects_are_shared(self):
    (self.source / 'panda/README.md').write_text('Prepared source')
    self.commit_source('New release')
    self.updater.fetch_update()
    source = self.merged / 'panda/README.md'
    copied = self.finalized / 'panda/README.md'
    self.assertNotEqual(source.stat().st_ino, copied.stat().st_ino)
    copied.write_text('Local edit in prepared tree')
    self.assertEqual(source.read_text(), 'Prepared source')
    objects = list((self.merged / '.git/objects/pack').glob('*.pack'))
    self.assertTrue(objects)
    for obj in objects:
      final = self.finalized / obj.relative_to(self.merged)
      self.assertEqual(obj.stat().st_ino, final.stat().st_ino)
    self.assertFalse(any(command[:2] == ['git', 'gc'] for command in self.commands))

  def test_invalid_manifest_rejected_before_checkout(self):
    self.manifest["schema_version"] = 999
    self.write_manifest()
    self.commit_source("Unsupported manifest")
    self.assert_rejected_before_checkout()

  def test_missing_manifest_rejected_before_checkout(self):
    (self.source / "upstream-sync.json").unlink()
    self.commit_source("Missing manifest")
    self.assert_rejected_before_checkout()

  def test_malformed_manifest_rejected_before_checkout(self):
    (self.source / "upstream-sync.json").write_text("{invalid JSON")
    self.commit_source("Malformed manifest")
    self.assert_rejected_before_checkout()

  def test_missing_dependency_rejected_before_checkout(self):
    shutil.rmtree(self.source / "panda")
    self.commit_source("Missing ordinary dependency folder")
    self.assert_rejected_before_checkout()

  def test_gitlink_rejected_before_checkout(self):
    self.git(self.source, "rm", "-r", "panda")
    self.git(self.source, "update-index", "--add", "--cacheinfo", f"160000,{self.initial_commit},panda")
    self.git(self.source, "commit", "-m", "Invalid gitlink dependency")
    self.assert_rejected_before_checkout()

  def test_gitmodules_rejected_before_checkout(self):
    (self.source / ".gitmodules").write_text('[submodule "panda"]\npath = panda\nurl = https://example.invalid/panda\n')
    self.commit_source("Invalid submodule configuration")
    self.assert_rejected_before_checkout()

  def test_finalize_invalid_tree_clears_existing_readiness(self):
    self.finalized.mkdir()
    (self.finalized / ".overlay_consistent").touch()
    self.manifest["dependencies"] = []
    self.write_manifest()
    self.commit_source("Invalid empty dependency manifest")
    shutil.rmtree(self.merged)
    self.git(self.root, "clone", "--no-local", "--no-hardlinks", str(self.source), str(self.merged))
    with self.assertRaises(ValueError):
      self.updated.finalize_update()
    self.assertFalse((self.finalized / ".overlay_consistent").exists())


if __name__ == "__main__":
  unittest.main()
