#!/usr/bin/env python3
import fcntl
import os
import re
import shutil
import stat
import threading
from dataclasses import dataclass
from openpilot.common.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog
from openpilot.system.loggerd.config import get_available_bytes, get_available_percent
from openpilot.system.loggerd.uploader import get_directory_sort
from openpilot.system.loggerd.storage_roots import DIR_FLAGS, RecordingRoot, legacy_segment, open_recording_root, recording_roots, root_current
from openpilot.system.loggerd.xattr_cache import getxattr_fd

MIN_BYTES = 5 * 1024 * 1024 * 1024
MIN_PERCENT = 10

DELETE_LAST = ['boot', 'crash']

PRESERVE_ATTR_NAME = 'user.preserve'
PRESERVE_ATTR_VALUE = b'1'
PRESERVE_COUNT = 5

_COUNTED_ROUTE = re.compile(r'(?P<prefix>(?:[a-f0-9]{16}[|_])?)(?P<count>[a-f0-9]{8})--[a-f0-9]{10}\Z')


@dataclass(frozen=True)
class _Directory:
  root: RecordingRoot
  name: str
  device: int
  inode: int

  @property
  def key(self):
    return self.root, self.name


def has_preserve_xattr(d: str, root: str | None = None) -> bool:
  root_fd = fd = -1
  try:
    root_fd = open_recording_root(Paths.log_root() if root is None else root)
    fd = os.open(d, DIR_FLAGS, dir_fd=root_fd)
    return getxattr_fd(fd, PRESERVE_ATTR_NAME) == PRESERVE_ATTR_VALUE
  finally:
    if fd >= 0:
      os.close(fd)
    if root_fd >= 0:
      os.close(root_fd)


def _recorded_time(root_fd: int, d: str):
  fd = -1
  try:
    fd = os.open(d, DIR_FLAGS, dir_fd=root_fd)
    for name in ('qlog.zst', 'qlog.bz2'):
      try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode):
          opened = os.fstat(fd)
          return info.st_mtime, opened.st_dev, opened.st_ino
      except OSError:
        continue
    opened = os.fstat(fd)
    return opened.st_mtime, opened.st_dev, opened.st_ino
  except OSError:
    return None
  finally:
    if fd >= 0:
      os.close(fd)


def listdir_by_age(root: str) -> list[str]:
  # RouteCount resets on migration, so order routes by newest qlog time instead of name.
  return [item.name for item in _directories_by_age(recording_roots(root)[:1])]


def _directories_by_age(roots: tuple[RecordingRoot, ...]) -> list[_Directory]:
  candidates = []
  route_times = {}
  for root in roots:
    fd = -1
    try:
      fd = open_recording_root(root.path)
      if not root_current(root, fd):
        continue
      with os.scandir(fd) as entries:
        dirs = [entry.name for entry in entries if entry.is_dir(follow_symlinks=False) and
                (not root.legacy or entry.name in DELETE_LAST or legacy_segment(entry.name))]
      pinned = []
      incomplete = False
      for name in dirs:
        recorded = _recorded_time(fd, name)
        if recorded is None:
          incomplete = True
          break
        age, device, inode = recorded
        route = name.rpartition('--')[0] or name
        key = (root, route)
        route_times[key] = max(route_times.get(key, 0.), age)
        pinned.append(_Directory(root, name, device, inode))
      if not incomplete and root_current(root, fd):
        candidates.extend(pinned)
    except OSError:
      continue
    finally:
      if fd >= 0:
        os.close(fd)
  route_times = _chain_route_counts(route_times)
  return sorted(candidates, key=lambda item: (route_times[item.root, item.name.rpartition('--')[0] or item.name],
                                             get_directory_sort(item.name), item.root.path))


def _chain_route_counts(route_times):
  # A drive recorded before the clock syncs can carry a date weeks in the past, which would make
  # it the first one deleted. Route N was recorded after route N-1 in the same folder, so it is
  # never treated as older than N-1. Only consecutive counts are chained: a RouteCount reset
  # leaves a gap, so routes on either side of a reset keep their own times.
  chains = {}
  for key in route_times:
    root, route = key
    match = _COUNTED_ROUTE.fullmatch(route)
    if match:
      chains.setdefault((root, match['prefix']), {})[int(match['count'], 16)] = key
  chained = dict(route_times)
  for routes in chains.values():
    for count in sorted(routes):
      previous = routes.get(count - 1)
      if previous is not None:
        chained[routes[count]] = max(chained[routes[count]], chained[previous])
  return chained


def _candidate_marked(candidate):
  root_fd = fd = -1
  try:
    root_fd = open_recording_root(candidate.root.path)
    if not root_current(candidate.root, root_fd):
      raise FileNotFoundError('Recording root changed')
    fd = os.open(candidate.name, DIR_FLAGS, dir_fd=root_fd)
    info = os.fstat(fd)
    if (info.st_dev, info.st_ino) != (candidate.device, candidate.inode):
      raise FileNotFoundError('Recording segment changed')
    return getxattr_fd(fd, PRESERVE_ATTR_NAME) == PRESERVE_ATTR_VALUE
  finally:
    if fd >= 0:
      os.close(fd)
    if root_fd >= 0:
      os.close(root_fd)


def _predecessors(root, name):
  route, _, segment = name.rpartition('--')
  if not route:
    return set()
  try:
    number = int(segment)
  except ValueError:
    return set()
  return {(root, f'{route}--{prior}') for prior in range(max(0, number - 2), number + 1)}


def _preserved_directories(candidates):
  preserved, unreadable = set(), set()
  count = 0
  for candidate in reversed(candidates):
    root, name = candidate.root, candidate.name
    try:
      marked = _candidate_marked(candidate)
    except OSError:
      # An unreadable bookmark may also protect the preceding two segments.
      unreadable.add((root, name))
      unreadable.update(_predecessors(root, name))
      continue
    if not marked or count >= PRESERVE_COUNT:
      continue
    count += 1
    preserved.update(_predecessors(root, name))
  return preserved, unreadable


def _same_candidate(root, root_fd, name, fd):
  if not root_current(root, root_fd):
    return False
  opened = os.fstat(fd)
  current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
  return stat.S_ISDIR(current.st_mode) and (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)


def _delete_legacy(root, root_fd, name, fd):
  # Logger segments are flat. Unexpected directories (including nested mounts)
  # are not automatically reclaimed. Unlink through the locked inode, not a
  # pathname that can be replaced after its identity was checked.
  contents = []
  with os.scandir(fd) as entries:
    for entry in entries:
      if len(contents) >= 128 or entry.name.endswith('.lock'):
        return False
      info = entry.stat(follow_symlinks=False)
      if info.st_dev != root.device or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        return False
      contents.append((entry.name, info.st_dev, info.st_ino))
  for child, device, inode in contents:
    if not _same_candidate(root, root_fd, name, fd) or any(n.endswith('.lock') for n in os.listdir(fd)):
      return False
    current = os.stat(child, dir_fd=fd, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (device, inode) or stat.S_ISDIR(current.st_mode):
      return False
    os.unlink(child, dir_fd=fd)
  if not _same_candidate(root, root_fd, name, fd) or any(n.endswith('.lock') for n in os.listdir(fd)):
    return False
  os.rmdir(name, dir_fd=root_fd)
  return True


def get_preserved_segments(dirs_by_creation: list[str]) -> set[str]:
  # skip deleting most recent N preserved segments (and their prior segment)
  preserved = set()
  for n, d in enumerate(filter(has_preserve_xattr, reversed(dirs_by_creation))):
    if n == PRESERVE_COUNT:
      break
    date_str, _, seg_str = d.rpartition("--")

    # ignore non-segment directories
    if not date_str:
      continue
    try:
      seg_num = int(seg_str)
    except ValueError:
      continue

    # preserve segment and two prior
    for _seg_num in range(max(0, seg_num - 2), seg_num + 1):
      preserved.add(f"{date_str}--{_seg_num}")

  return preserved


def deleter_step() -> tuple[bool, str | None]:
  out_of_bytes = get_available_bytes(default=MIN_BYTES + 1) < MIN_BYTES
  out_of_percent = get_available_percent(default=MIN_PERCENT + 1) < MIN_PERCENT
  out_of_space = out_of_percent or out_of_bytes
  if not out_of_space:
    return False, None

  dirs = _directories_by_age(recording_roots())
  preserved_dirs, unreadable_dirs = _preserved_directories(dirs)

  # remove the earliest directory we can
  for candidate in sorted(dirs, key=lambda item: (item.name in DELETE_LAST, item.key in preserved_dirs)):
    root, delete_dir = candidate.root, candidate.name
    if candidate.key in unreadable_dirs:
      continue
    delete_path = os.path.join(root.path, delete_dir)

    root_fd = fd = -1
    try:
      root_fd = open_recording_root(root.path)
      if not root_current(root, root_fd):
        continue
      fd = os.open(delete_dir, DIR_FLAGS, dir_fd=root_fd)
      info = os.fstat(fd)
      if (info.st_dev, info.st_ino) != (candidate.device, candidate.inode):
        continue
      try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
      except BlockingIOError:
        continue
      if any(name.endswith(".lock") for name in os.listdir(fd)):
        continue
      if not _same_candidate(root, root_fd, delete_dir, fd):
        continue
      if root.legacy:
        if not _delete_legacy(root, root_fd, delete_dir, fd):
          continue
      else:
        shutil.rmtree(delete_dir, dir_fd=root_fd)
      cloudlog.info(f"deleting {delete_path}")
      return True, delete_path
    except FileNotFoundError:
      continue
    except OSError:
      cloudlog.exception(f"issue deleting {delete_path}")
    finally:
      if fd >= 0:
        os.close(fd)
      if root_fd >= 0:
        os.close(root_fd)
  return True, None


def deleter_thread(exit_event: threading.Event):
  while not exit_event.is_set():
    out_of_space, _ = deleter_step()
    exit_event.wait(.1 if out_of_space else 30)


def main():
  deleter_thread(threading.Event())


if __name__ == "__main__":
  main()
