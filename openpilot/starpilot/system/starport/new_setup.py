"""The "New pairing experience" switch (Galaxy Developer > StarPort), on unless the owner turned it off.

A plain file beside the other StarPort data, not a Params key: it is the way back to the classic screens, so it must
read and change with the StarPort service down or broken, and it needs no device rebuild.
"""
from pathlib import Path

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
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text("1" if value else "0")
    temporary.replace(target)
  except OSError:
    return False
  return enabled(target) == value
