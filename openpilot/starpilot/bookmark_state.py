"""Atomic, boot/namespace-bound bookmark checkpoint in volatile runtime storage.

Follows the private-directory + atomic rename convention used by sentry runtime
status and modeld receipts. This is feedback state, never driving authority.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from contextlib import contextmanager

from openpilot.common.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog


def checkpoint_root():
  identity = (os.getuid(), os.environ.get('OPENPILOT_PREFIX', ''), os.environ.get('PARAMS_ROOT', ''))
  namespace = hashlib.sha256(repr(identity).encode()).hexdigest()[:24]
  return Path(Paths.shm_path()) / f'starpilot-bookmarks-{namespace}'


def boot_id():
  try:
    return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
  except (OSError, ValueError):
    return ''


class BookmarkCheckpoint:
  def __init__(self, root=None, *, boot=None):
    self.root = Path(root) if root is not None else checkpoint_root()
    self.boot = boot_id() if boot is None else boot

  @contextmanager
  def _directory(self, create=False):
    if create:
      try:
        self.root.mkdir(mode=0o700)
      except FileExistsError:
        pass
    fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
      info = os.fstat(fd)
      if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise OSError('Unsafe bookmark checkpoint directory')
      yield fd
    finally:
      os.close(fd)

  def read(self):
    if not self.boot:
      return None
    try:
      with self._directory() as directory:
        fd = os.open('count.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
          info = os.fstat(stream.fileno())
          if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise OSError('Unsafe bookmark checkpoint')
          raw = stream.read(1025)
      saved = json.loads(raw) if len(raw) <= 1024 else None
      return saved if isinstance(saved, dict) and saved.get('boot') == self.boot else None
    except FileNotFoundError:
      return None
    except (ValueError, UnicodeError):
      return None

  def write(self, value):
    if not self.boot:
      raise OSError('Boot identity unavailable for bookmark checkpoint')
    raw = json.dumps({**value, 'boot': self.boot}, separators=(',', ':')).encode()
    if len(raw) > 1024:
      raise ValueError('Bookmark checkpoint too large')
    temporary = f'.{uuid.uuid4().hex}'
    with self._directory(create=True) as directory:
      try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
          stream.write(raw)
        os.replace(temporary, 'count.json', src_dir_fd=directory, dst_dir_fd=directory)
      finally:
        try:
          os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
          pass

  def clear(self):
    try:
      with self._directory() as directory:
        os.unlink('count.json', dir_fd=directory)
    except FileNotFoundError:
      pass


def clear_bookmark_checkpoint():
  try:
    BookmarkCheckpoint().clear()
  except OSError:
    cloudlog.exception('Unable to clear bookmark checkpoint')
