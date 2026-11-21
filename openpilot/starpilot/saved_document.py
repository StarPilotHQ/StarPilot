"""One exact-source Params document replacement under the native Params lock."""

from collections.abc import Callable
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import tempfile
import time

from openpilot.starpilot.saved_source import read_saved


LOCK_WAIT_SECONDS = .25


def acquire_native_lock(fd: int) -> None:
  deadline = time.monotonic() + LOCK_WAIT_SECONDS
  while True:
    try:
      fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
      return
    except BlockingIOError:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise
      time.sleep(min(.01, remaining))


@dataclass(frozen=True)
class WriteResult:
  committed: bool
  verified: bool


def commit_exact(params, *, key: str, max_bytes: int, raw: bytes, expected: bytes | None,
                 authorized: Callable[[], bool], temp_prefix: str, lock_root: Path | None = None) -> WriteResult:
  """Stage outside the lock; recheck authority and source before replacement."""
  if not authorized():
    return WriteResult(False, False)
  source, readable = read_saved(params, key, max_bytes)
  if not readable or source != expected:
    return WriteResult(False, False)
  temporary = None
  lock_fd = None
  committed = False
  try:
    destination = Path(params.get_param_path(key))
    root = destination.parent.parent if lock_root is None else lock_root
    with tempfile.NamedTemporaryFile(prefix=temp_prefix, dir=root, delete=False) as stage:
      temporary = stage.name
      stage.write(raw)
      stage.flush()
      os.fsync(stage.fileno())
    lock_fd = os.open(root / ".lock", os.O_CREAT | os.O_RDONLY, 0o775)
    acquire_native_lock(lock_fd)
    if not authorized():
      return WriteResult(False, False)
    source, readable = read_saved(params, key, max_bytes)
    if not readable or source != expected or not authorized():
      return WriteResult(False, False)
    os.replace(temporary, destination)
    temporary = None
    committed = True
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
      os.fsync(directory)
    finally:
      os.close(directory)
    observed, readable = read_saved(params, key, max_bytes)
    return WriteResult(True, readable and observed == raw)
  except (OSError, ValueError, TypeError):
    return WriteResult(committed, False)
  finally:
    if lock_fd is not None:
      os.close(lock_fd)
    if temporary is not None:
      try:
        os.unlink(temporary)
      except OSError:
        pass
