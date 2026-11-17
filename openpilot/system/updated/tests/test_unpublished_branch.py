"""An unpublished local target is not a failed download or a phantom update."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from openpilot.system.updated.tests.test_vendored_update import load_updater


TARGET = "local-domathon"
CURRENT = "a" * 40
REMOTE = "b" * 40


class TestUnpublishedBranch(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.merged = self.root / "merged"
    self.merged.mkdir()
    self.finalized = self.root / "finalized"
    self.updated = load_updater()
    self.updated.OVERLAY_MERGED = str(self.merged)
    self.metadata = self.root / "metadata.git"
    self.updated.METADATA_ROOT = self.metadata
    self.updated.FINALIZED = str(self.finalized)
    self.updated.BASEDIR = str(self.merged)
    self.updated.setup_git_options = Mock()
    self.updated.set_consistent_flag = Mock()
    self.updater = self.updated.Updater()
    self.updater.params.values["UpdaterTargetBranch"] = TARGET
    self.updater.get_branch = Mock(return_value=TARGET)
    self.updater.get_commit_hash = Mock(return_value=CURRENT)
    self.commands = []
    self.remote_heads = f"{REMOTE}\trefs/heads/another-branch\n"

    def run(command, cwd, **kwargs):
      self.commands.append(command)
      if command == ["git", "init", "--bare", str(self.metadata)]:
        self.assertEqual(cwd, str(self.root))
        self.metadata.mkdir()
        (self.metadata / "HEAD").write_text("ref: refs/heads/main\n")
        return ""
      if command == ["git", "remote", "get-url", "origin"]:
        self.assertEqual(cwd, str(self.merged))
        return "https://example.invalid/updater-test.git"
      self.assertEqual(cwd, str(self.metadata))
      if command == ["git", "config", "remote.origin.url", "https://example.invalid/updater-test.git"]:
        return ""
      if command == ["git", "ls-remote", "--heads", "origin"]:
        return self.remote_heads
      if command in (
        ["git", "fetch", "--progress", f"--depth={self.updated.MAX_COMMITS}", "--filter=blob:none", "--no-tags", "--no-recurse-submodules",
         "origin", f"+{commit}:refs/remotes/origin/{TARGET}"] for commit in (CURRENT, REMOTE)
      ):
        return ""
      if command in (["git", "update-ref", f"refs/starpilot/recent/{TARGET}", commit] for commit in (CURRENT, REMOTE)):
        return ""
      raise AssertionError(f"Unexpected command: {command}")

    self.updated.run = run

  def test_check_clock_requires_successful_remote_check(self):
    self.assertNotIn("UpdaterLastCheckTime", self.updater.params.values)
    self.updater.check_for_update()
    checked = self.updater.params.values["UpdaterLastCheckTime"]
    self.updated.run = Mock(side_effect=subprocess.CalledProcessError(1, "git ls-remote"))
    with self.assertRaises(subprocess.CalledProcessError):
      self.updater.check_for_update()
    self.assertEqual(self.updater.params.values["UpdaterLastCheckTime"], checked)
    self.assertNotIn("LastUpdateTime", self.updater.params.values)

  def test_missing_target_never_becomes_a_branch_or_fetches(self):
    self.updater.check_for_update()
    self.assertEqual(self.updater.branches, {"another-branch": REMOTE})
    self.assertFalse(self.updater.update_available)
    self.finalized.mkdir()
    (self.finalized / ".overlay_consistent").touch()
    self.assertFalse(self.updater.update_ready)
    self.assertFalse(self.updater.fetch_update())
    self.assertNotIn(TARGET, self.updater.branches)
    self.assertEqual(self.commands.count(["git", "ls-remote", "--heads", "origin"]), 1)
    self.updated.set_consistent_flag.assert_not_called()
    self.assertNotIn("UpdaterState", self.updater.params.values)

  def test_direct_fetch_checks_before_any_mutation(self):
    self.assertFalse(self.updater.fetch_update())
    self.assertEqual(self.updater.branches, {"another-branch": REMOTE})
    self.updated.set_consistent_flag.assert_not_called()
    self.assertIs(self.updater.params.values["UpdateAvailable"], False)
    self.assertIs(self.updater.params.values["UpdaterFetchAvailable"], False)
    self.assertIn("UpdaterLastCheckTime", self.updater.params.values)
    self.assertEqual(self.commands.count(["git", "ls-remote", "--heads", "origin"]), 1)
    self.assertFalse(any(command[:2] == ["git", "fetch"] for command in self.commands))
    self.assertNotIn("UpdaterState", self.updater.params.values)

  def test_published_same_and_different_commit(self):
    self.remote_heads += f"{CURRENT}\trefs/heads/{TARGET}\n"
    self.updater.check_for_update()
    self.assertFalse(self.updater.update_available)
    self.assertEqual(self.updater.branches[TARGET], CURRENT)
    self.remote_heads = f"{REMOTE}\trefs/heads/{TARGET}\n"
    self.updater.check_for_update()
    self.assertTrue(self.updater.update_available)
    self.assertEqual(self.updater.branches[TARGET], REMOTE)
    self.remote_heads = f"{REMOTE}\trefs/heads/another-branch\n"
    self.updater.check_for_update()
    self.assertFalse(self.updater.update_available)
    self.assertNotIn(TARGET, self.updater.branches)

  def test_stale_finalized_overlay_is_not_ready(self):
    self.remote_heads = f"{REMOTE}\trefs/heads/{TARGET}\n"
    self.updater.check_for_update()
    self.finalized.mkdir()
    (self.finalized / ".overlay_consistent").touch()
    self.assertFalse(self.updater.update_ready)
    self.updater.get_commit_hash = Mock(side_effect=lambda path: REMOTE if path == str(self.finalized) else CURRENT)
    self.assertTrue(self.updater.update_ready)

  def test_remote_error_remains_an_error(self):
    self.remote_heads = f"{REMOTE}\trefs/heads/{TARGET}\n"
    self.updater.check_for_update()
    self.assertTrue(self.updater.update_available)

    def failed_run(command, cwd, **kwargs):
      if command == ["git", "ls-remote", "--heads", "origin"]:
        raise subprocess.CalledProcessError(128, command, output="network failed")
      return f"{REMOTE}\tHEAD\n"

    self.updated.run = failed_run
    self.updater._branches_checked = False
    with self.assertRaises(subprocess.CalledProcessError):
      self.updater.fetch_update()
    self.assertFalse(self.updater._branches_checked)
    self.assertFalse(self.updater.update_available)
    self.updated.set_consistent_flag.assert_not_called()


if __name__ == "__main__":
  unittest.main()
