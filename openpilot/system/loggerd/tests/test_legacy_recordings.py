import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.hardware.hw import Paths
from openpilot.system.loggerd import deleter, storage_roots, xattr_cache
from openpilot.starpilot.galaxy.drive_history import DriveHistory, recording_details
from openpilot.starpilot.galaxy.recording_library import RecordingLibrary


class TestLegacyRecordings(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.base = Path(temporary.name).resolve()
    self.current = self.base / 'realdata'
    self.hd = self.base / 'realdata_HD'
    self.konik = self.base / 'realdata_konik'
    for root in (self.current, self.hd, self.konik):
      root.mkdir()
    self.enterContext(patch.dict(os.environ))
    os.environ.pop('LOG_ROOT', None)
    self.enterContext(patch.object(storage_roots, 'DEFAULT_LOG_ROOT', str(self.current)))
    self.enterContext(patch.object(Paths, 'log_root', return_value=str(self.current) + '/'))
    self.enterContext(patch.object(deleter, 'get_available_bytes', return_value=0))
    self.enterContext(patch.object(deleter, 'get_available_percent', return_value=0))

  def segment(self, root, route='00000001--abcdef1234', number=0, age=1, *, locked=False, preserved=False):
    folder = root / f'{route}--{number}'
    folder.mkdir()
    log = folder / 'qlog.zst'
    log.write_bytes(b'recorded drive')
    when = 1_700_000_000 + age
    os.utime(log, (when, when))
    if locked:
      (folder / 'rlog.lock').touch()
    if preserved:
      xattr_cache._setxattr(str(folder), 'user.preserve', b'1')
    return folder

  def delete(self, expected):
    self.assertEqual(deleter.deleter_step(), (True, str(expected)))
    self.assertFalse(expected.exists())

  def test_old_legacy_recording_is_reclaimed_before_new_current_recording(self):
    old = self.segment(self.konik, route='000000e8--abcdef1234', age=1)
    new = self.segment(self.current, route='00000004--15a87c3085', age=100)
    self.delete(old)
    self.assertTrue(new.exists())

  def test_all_three_roots_share_recording_age_order(self):
    last = self.segment(self.current, age=30)
    first = self.segment(self.hd, route='000000ff--abcdef1234', age=10)
    middle = self.segment(self.konik, age=20)
    for folder in (first, middle, last):
      self.delete(folder)

  def test_identical_route_names_in_different_roots_are_not_one_route(self):
    first = self.segment(self.konik, age=1)
    last = self.segment(self.current, age=100)
    middle = self.segment(self.hd, route='00000002--abcdef1234', age=50)
    for folder in (first, middle, last):
      self.delete(folder)

  def test_unsynchronized_early_segment_and_changed_directory_mtime(self):
    before = self.segment(self.current, route='000000e8--abcdef1234', age=20)
    unsynced = self.segment(self.konik, number=0, age=1)
    synced = self.segment(self.konik, number=1, age=30)
    os.utime(before, (1_900_000_000, 1_900_000_000))
    for folder in (before, unsynced, synced):
      self.delete(folder)

  def test_route_counts_are_chained_only_within_one_root(self):
    # Route 2 in the legacy root does not follow route 1 in the current root.
    unsynced = self.segment(self.konik, route='00000002--abcdef1234', age=1)
    current = self.segment(self.current, route='00000001--abcdef1234', age=50)
    for folder in (unsynced, current):
      self.delete(folder)

  def test_preserve_predecessors_do_not_preserve_same_named_other_root(self):
    protected = [self.segment(self.konik, number=i, age=i + 1, preserved=i == 2) for i in range(3)]
    other = self.segment(self.hd, number=1, age=10)
    self.delete(other)
    for folder in protected:
      self.assertTrue(folder.exists())
    for folder in protected:
      self.delete(folder)  # Preservation defers deletion; it is not permanent.

  def test_five_recent_marked_segments_are_selected_globally(self):
    marked = [self.segment((self.current, self.hd, self.konik)[i % 3],
                           route=f'{i + 1:08x}--abcdef1234', age=i, preserved=True) for i in range(6)]
    ordinary = self.segment(self.current, route='000000ff--abcdef1234', age=20)
    self.delete(marked[0])
    self.delete(ordinary)
    for folder in marked[1:]:
      self.assertTrue(folder.exists())

  def test_boot_and_crash_remain_after_preserved_segments(self):
    preserved = self.segment(self.konik, age=10, preserved=True)
    ordinary = self.segment(self.current, route='00000002--abcdef1234', age=20)
    late = []
    for name in ('boot', 'crash'):
      folder = self.hd / name
      folder.mkdir()
      (folder / 'saved.log').write_bytes(b'log')
      late.append(folder)
    self.delete(ordinary)
    self.delete(preserved)
    for _ in late:
      pressure, deleted = deleter.deleter_step()
      self.assertTrue(pressure)
      self.assertIn(deleted, [str(folder) for folder in late])
    self.assertTrue(all(not folder.exists() for folder in late))

  def test_lock_files_are_respected_in_each_root(self):
    for root in (self.current, self.hd, self.konik):
      self.segment(root, locked=True)
    self.assertEqual(deleter.deleter_step(), (True, None))

  def test_shared_replay_lock_prevents_legacy_deletion(self):
    old = self.segment(self.konik)
    child = subprocess.Popen([sys.executable, '-B', '-c',
      'import os,fcntl,sys; f=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY); ' +
      'fcntl.flock(f,fcntl.LOCK_SH); print("ready",flush=True); sys.stdin.read(1); os.close(f)', str(old)],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
      self.assertEqual(child.stdout.readline().strip(), 'ready')
      self.assertEqual(deleter.deleter_step(), (True, None))
      self.assertTrue((old / 'qlog.zst').exists())
    finally:
      child.communicate('\n', timeout=5)
      if child.poll() is None:
        child.kill()
        child.wait()

  def test_no_pressure_does_not_remove_any_recording(self):
    old = self.segment(self.konik)
    with patch.object(deleter, 'get_available_bytes', return_value=deleter.MIN_BYTES + 1), \
         patch.object(deleter, 'get_available_percent', return_value=deleter.MIN_PERCENT + 1):
      self.assertEqual(deleter.deleter_step(), (False, None))
    self.assertTrue(old.exists())

  def test_byte_floor_and_percent_floor_remain_independent(self):
    first = self.segment(self.konik, age=1)
    second = self.segment(self.hd, age=2)
    with patch.object(deleter, 'get_available_bytes', return_value=deleter.MIN_BYTES + 1):
      self.delete(first)
    with patch.object(deleter, 'get_available_percent', return_value=deleter.MIN_PERCENT + 1):
      self.delete(second)

  def test_custom_root_does_not_admit_default_or_legacy_roots(self):
    custom = self.base / 'custom'
    custom.mkdir()
    chosen = self.segment(custom, age=100)
    old = self.segment(self.konik, age=1)
    with patch.object(Paths, 'log_root', return_value=str(custom)):
      self.assertEqual([root.path for root in storage_roots.recording_roots()], [str(custom)])
      self.delete(chosen)
    self.assertTrue(old.exists())

  def test_explicit_log_root_even_equal_to_default_stays_isolated(self):
    chosen = self.segment(self.current, age=100)
    old = self.segment(self.konik, age=1)
    os.environ['LOG_ROOT'] = str(self.current)
    self.assertEqual(len(storage_roots.recording_roots()), 1)
    self.delete(chosen)
    self.assertTrue(old.exists())

  def test_other_realdata_siblings_and_unknown_legacy_folders_are_untouched(self):
    backup = self.base / 'realdata_backup'
    backup.mkdir()
    saved = self.segment(backup)
    unknown = self.konik / 'family-photos'
    unknown.mkdir()
    (unknown / 'photo.jpg').write_bytes(b'photo')
    self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue(saved.exists())
    self.assertTrue((unknown / 'photo.jpg').exists())

  def test_symlink_legacy_root_is_not_admitted_even_with_trailing_slash(self):
    self.konik.rmdir()
    outside = self.base / 'outside'
    outside.mkdir()
    saved = self.segment(outside)
    self.konik.symlink_to(outside, target_is_directory=True)
    self.assertNotIn(str(self.konik), [root.path for root in storage_roots.recording_roots()])
    with self.assertRaises(OSError):
      storage_roots.open_recording_root(str(self.konik) + '/')
    self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue(saved.exists())

  def test_symlink_segment_does_not_reach_outside_recordings(self):
    outside = self.base / 'outside'
    outside.mkdir()
    saved = self.segment(outside)
    (self.konik / saved.name).symlink_to(saved, target_is_directory=True)
    self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue((saved / 'qlog.zst').exists())

  def test_symlink_file_is_unlinked_without_following_its_target(self):
    folder = self.segment(self.konik)
    outside = self.base / 'outside.log'
    outside.write_bytes(b'keep')
    (folder / 'qlog.zst').unlink()
    (folder / 'qlog.zst').symlink_to(outside)
    self.delete(folder)
    self.assertEqual(outside.read_bytes(), b'keep')

  def test_legacy_segment_with_nested_directory_is_not_recursed_into(self):
    folder = self.segment(self.konik)
    nested = folder / 'mounted-or-unexpected-directory'
    nested.mkdir()
    (nested / 'keep').write_bytes(b'keep')
    self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue((folder / 'qlog.zst').exists())
    self.assertTrue((nested / 'keep').exists())

  def test_other_filesystem_legacy_root_is_not_admitted(self):
    opened = {}
    original_open = storage_roots.open_recording_root
    original_fstat = os.fstat
    def opening(path):
      fd = original_open(path)
      opened[fd] = str(path)
      return fd
    def fstat(fd):
      info = original_fstat(fd)
      if opened.get(fd) == str(self.konik):
        fields = list(info)
        fields[2] = info.st_dev + 1
        return os.stat_result(fields)
      return info
    with patch.object(storage_roots, 'open_recording_root', side_effect=opening), patch.object(os, 'fstat', side_effect=fstat):
      self.assertNotIn(str(self.konik), [root.path for root in storage_roots.recording_roots()])

  def test_replaced_root_cannot_inherit_enumerated_age_or_permission(self):
    old = self.segment(self.konik)
    original = deleter._directories_by_age
    def scanning(roots):
      candidates = original(roots)
      self.konik.rename(self.base / 'old-root')
      self.konik.mkdir()
      self.segment(self.konik)
      return candidates
    with patch.object(deleter, '_directories_by_age', side_effect=scanning):
      self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue((self.konik / old.name / 'qlog.zst').exists())
    self.assertTrue((self.base / 'old-root' / old.name / 'qlog.zst').exists())

  def test_replaced_segment_cannot_inherit_enumerated_age_or_permission(self):
    old = self.segment(self.konik)
    original = deleter._directories_by_age
    parked = self.base / 'original-segment'
    def scanning(roots):
      candidates = original(roots)
      old.rename(parked)
      self.segment(self.konik)
      return candidates
    with patch.object(deleter, '_directories_by_age', side_effect=scanning):
      self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue((old / 'qlog.zst').exists())
    self.assertTrue((parked / 'qlog.zst').exists())

  def test_replaced_segment_at_delete_boundary_is_not_deleted(self):
    folder = self.segment(self.konik)
    original = deleter._delete_legacy
    def replacing(root, root_fd, name, fd):
      folder.rename(self.base / 'original-segment')
      self.segment(self.konik, locked=True)
      return original(root, root_fd, name, fd)
    with patch.object(deleter, '_delete_legacy', side_effect=replacing):
      self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue((folder / 'rlog.lock').exists())
    self.assertTrue((folder / 'qlog.zst').exists())

  def test_last_moment_replacement_cannot_redirect_unlink_to_new_inode(self):
    folder = self.segment(self.konik)
    original_unlink = os.unlink
    replaced = False
    def unlinking(name, *, dir_fd=None):
      nonlocal replaced
      if not replaced and name == 'qlog.zst':
        replaced = True
        folder.rename(self.base / 'original-segment')
        self.segment(self.konik, locked=True)
      return original_unlink(name, dir_fd=dir_fd)
    with patch.object(deleter.os, 'unlink', side_effect=unlinking):
      self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue((folder / 'rlog.lock').exists())
    self.assertEqual((folder / 'qlog.zst').read_bytes(), b'recorded drive')

  def test_late_lock_at_delete_boundary_prevents_any_unlink(self):
    folder = self.segment(self.konik)
    original = deleter._delete_legacy
    def locking(root, root_fd, name, fd):
      (folder / 'qcamera.ts.lock').touch()
      return original(root, root_fd, name, fd)
    with patch.object(deleter, '_delete_legacy', side_effect=locking):
      self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue((folder / 'qlog.zst').exists())

  def test_new_bookmark_after_cached_miss_is_read_from_pinned_fd(self):
    folder = self.segment(self.konik)
    self.assertIsNone(xattr_cache.getxattr(str(folder), 'user.preserve'))
    xattr_cache._setxattr(str(folder), 'user.preserve', b'1')
    self.assertIsNone(xattr_cache.getxattr(str(folder), 'user.preserve'))
    self.assertTrue(deleter.has_preserve_xattr(folder.name, str(self.konik)))
    other = self.segment(self.current, age=100)
    self.delete(other)
    self.assertTrue(folder.exists())

  def test_unknown_preservation_read_failure_prevents_deletion(self):
    folder = self.segment(self.konik)
    with patch.object(deleter, 'getxattr_fd', side_effect=PermissionError(errno.EACCES, 'denied')):
      self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue(folder.exists())

  def test_unreadable_bookmark_also_protects_two_same_root_predecessors(self):
    protected = [self.segment(self.konik, number=i, age=i + 1, preserved=i == 2) for i in range(3)]
    other = self.segment(self.hd, number=1, age=100)
    original = deleter.getxattr_fd
    marked = protected[2].stat()
    def reading(fd, attribute):
      info = os.fstat(fd)
      if (info.st_dev, info.st_ino) == (marked.st_dev, marked.st_ino):
        raise PermissionError(errno.EACCES, 'denied')
      return original(fd, attribute)
    with patch.object(deleter, 'getxattr_fd', side_effect=reading):
      self.delete(other)
      self.assertEqual(deleter.deleter_step(), (True, None))
    for folder in protected:
      self.assertTrue((folder / 'qlog.zst').exists())

  def test_uninspectable_segment_defers_its_whole_root(self):
    protected = [self.segment(self.konik, number=i, age=i + 1, preserved=i == 2) for i in range(3)]
    other = self.segment(self.hd, age=100)
    original = os.open
    identity = self.konik.stat()
    def opening(path, flags, *args, **kwargs):
      parent = kwargs.get('dir_fd')
      if path == protected[2].name and parent is not None:
        info = os.fstat(parent)
        if (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
          raise PermissionError(errno.EACCES, 'denied')
      return original(path, flags, *args, **kwargs)
    with patch.object(deleter.os, 'open', side_effect=opening):
      self.delete(other)
      self.assertEqual(deleter.deleter_step(), (True, None))
    for folder in protected:
      self.assertTrue((folder / 'qlog.zst').exists())

  def test_noncanonical_legacy_segment_numbers_are_not_auto_deleted(self):
    folder = self.segment(self.konik, number=2, preserved=True)
    saved = folder.with_name(folder.name[:-1] + '0002')
    folder.rename(saved)
    self.assertFalse(storage_roots.legacy_segment(saved.name))
    current = self.segment(self.current, age=100)
    self.delete(current)
    self.assertTrue((saved / 'qlog.zst').exists())
    self.assertEqual(deleter.deleter_step(), (True, None))

  def test_legacy_segment_at_flat_entry_limit_can_be_reclaimed(self):
    folder = self.segment(self.konik)
    for number in range(127):
      (folder / f'log-{number}').write_bytes(b'fixture')
    self.delete(folder)

  def test_legacy_segment_over_flat_entry_limit_is_not_partly_deleted(self):
    folder = self.segment(self.konik)
    for number in range(128):
      (folder / f'log-{number}').write_bytes(b'fixture')
    self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertEqual(len(list(folder.iterdir())), 129)

  def test_nonregular_legacy_child_is_not_unlinked(self):
    folder = self.segment(self.konik)
    pipe = folder / 'unexpected-pipe'
    os.mkfifo(pipe)
    self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue(pipe.exists())
    self.assertTrue((folder / 'qlog.zst').exists())

  def test_symlink_ancestor_of_automatic_roots_is_rejected(self):
    old = self.segment(self.konik)
    alias = self.base / 'alias'
    alias.symlink_to(self.base, target_is_directory=True)
    path = str(alias / 'realdata')
    with patch.object(storage_roots, 'DEFAULT_LOG_ROOT', path), patch.object(Paths, 'log_root', return_value=path):
      self.assertEqual(storage_roots.recording_roots(), ())
      self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertTrue((old / 'qlog.zst').exists())

  def test_fd_xattr_read_is_uncached_and_invalid_fd_is_not_missing(self):
    folder = self.segment(self.konik)
    fd = os.open(folder, storage_roots.DIR_FLAGS)
    try:
      self.assertIsNone(xattr_cache.getxattr_fd(fd, 'user.preserve'))
      xattr_cache._setxattr(str(folder), 'user.preserve', b'1')
      self.assertEqual(xattr_cache.getxattr_fd(fd, 'user.preserve'), b'1')
    finally:
      os.close(fd)
    with self.assertRaises(OSError):
      xattr_cache.getxattr_fd(-1, 'user.preserve')

  def test_missing_roots_and_empty_roots_are_harmless(self):
    self.konik.rmdir()
    self.assertEqual(deleter.deleter_step(), (True, None))
    self.assertEqual(len(storage_roots.recording_roots()), 2)
    self.current.rmdir()
    self.assertEqual(storage_roots.recording_roots(), ())

  def test_galaxy_warns_with_legacy_only_recordings_without_wrong_root_entries(self):
    self.segment(self.konik)
    library = RecordingLibrary(DriveHistory(self.current))
    result = recording_details(library.describe(library.history.snapshot()), {}, None)
    self.assertTrue(result['legacyRecordingsPresent'])
    self.assertEqual(result['routes'], [])
    os.environ['LOG_ROOT'] = str(self.current)
    self.assertFalse(library.describe(library.history.snapshot())['legacyRecordingsPresent'])

  def test_galaxy_current_recording_actions_never_resolve_to_legacy_duplicate(self):
    legacy = self.segment(self.konik)
    current = self.segment(self.current, age=100)
    library = RecordingLibrary(DriveHistory(self.current))
    result = library.describe(library.history.snapshot())
    self.assertTrue(result['legacyRecordingsPresent'])
    self.assertEqual(len(result['routes']), 1)
    payload = {'action': 'delete', 'routeId': '00000001--abcdef1234', 'confirmed': True}
    with self.assertRaises(PermissionError):
      library.action(payload, permitted=lambda: False)
    self.assertTrue(current.exists())
    library.action(payload, permitted=lambda: True)
    self.assertFalse(current.exists())
    self.assertTrue((legacy / 'qlog.zst').exists())


if __name__ == '__main__':
  unittest.main()
