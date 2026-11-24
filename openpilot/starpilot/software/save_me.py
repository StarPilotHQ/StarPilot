"""StarPilot data reset, without invoking the OS factory reset."""

import os
import shutil
import stat
import time
from pathlib import Path

from openpilot.common.hardware import COMMA_HARDWARE

CONFIRMATION = "ERASE STARPILOT DATA"
REQUEST = Path("/data/.starpilot-save-me")
WIPE_PATHS = (
  "data/params",
  "cache/starpilot/params",
  "cache/params",
  "data/media/0/realdata",
  "data/media/0/realdata_HD",
  "data/media/0/realdata_konik",
  "data/models",
  "data/toggle_backups",
  "data/backups",
  "data/themes",
  "data/media/0/osm/offline",
  "cache/use_HD",
  "cache/use_konik",
  "data/starpilot",
  "data/media/0/starpilot",
  "data/starpilot-model-runtime",
  "data/galaxy",
  "data/galaxy-ble",
  "data/starport",
  "data/android_auto",
  "data/log",
)


def available() -> bool:
  return COMMA_HARDWARE and not os.environ.get("OPENPILOT_PREFIX") and os.environ.get("PARAMS_ROOT", "/data/params") == "/data/params"


def request() -> None:
  REQUEST.mkdir(mode=0o700)


def clear_request() -> None:
  try:
    REQUEST.rmdir()
  except FileNotFoundError:
    pass


def take_request(*, parked: bool, started: bool) -> bool:
  try:
    marker = REQUEST.lstat()
  except FileNotFoundError:
    return False
  if not stat.S_ISDIR(marker.st_mode) or marker.st_uid != os.getuid() or stat.S_IMODE(marker.st_mode) != 0o700:
    return False
  fresh = 0 <= time.time() - marker.st_mtime <= 15
  if started or not fresh or parked:
    clear_request()
  return fresh and parked and not started


def _remove(root: Path, relative: str, *, recursive: bool = True) -> None:
  # A symlink in a parent must not redirect deletion outside the intended tree.
  flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
  fd = os.open(root, flags)
  try:
    *parents, name = Path(relative).parts
    for component in parents:
      next_fd = os.open(component, flags, dir_fd=fd)
      os.close(fd)
      fd = next_fd
    if recursive and stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
      shutil.rmtree(name, dir_fd=fd)
    else:
      os.unlink(name, dir_fd=fd)
  except FileNotFoundError:
    pass
  finally:
    os.close(fd)


def wipe(root: Path = Path("/")) -> list[str]:
  # A regular reboot would otherwise install any already-staged update.
  _remove(root, "data/safe_staging/finalized/.overlay_consistent", recursive=False)
  failures = []
  for path in WIPE_PATHS:
    try:
      _remove(root, path)
    except OSError as error:
      failures.append(f"{path}: {error}")
  return failures
