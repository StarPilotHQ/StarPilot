"""Persist the setup UI choice independently of the StarPort daemon."""
from pathlib import Path

from openpilot.common.utils import atomic_write
from openpilot.starpilot.system.starport.identity import DATA_DIR

FLAG_PATH = DATA_DIR / "new_setup"


def enabled(path: Path | None = None) -> bool:
  """Absent, unreadable or unrecognized means on; only an explicit 0 turns the new flow off."""
  try:
    return (path or FLAG_PATH).read_text().strip() != "0"
  except (OSError, UnicodeError):
    return True


def set_enabled(value: bool, path: Path | None = None) -> bool:
  target = path or FLAG_PATH
  try:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with atomic_write(str(target), overwrite=True) as handle:
      handle.write("1" if value else "0")
  except OSError:
    return False
  return enabled(target) == value
