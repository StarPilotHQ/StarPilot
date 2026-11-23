"""User-facing StarPilot identity, separate from upstream build metadata."""

from pathlib import Path
import re


DISPLAY_VERSION = "7.0.2"


def read_display_version(root: str) -> str:
  """Read an installed or staged release label without executing its source."""
  for file, name in (
    ("openpilot/starpilot/ui/brand.py", "DISPLAY_VERSION"),
    ("selfdrive/ui/lib/starpilot_version.py", "STARPILOT_DISPLAY_VERSION"),
  ):
    try:
      text = (Path(root) / file).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
      continue
    versions = re.findall(rf"""^{name} = (["'])([1-9][0-9]*\.[0-9]+\.[0-9]+)\1[ \t]*$""", text, re.MULTILINE)
    if len(versions) == 1:
      return versions[0][1]
  return ""


def home_description(updater_description: str) -> str:
  """Keep branch/detail suffixes without showing the upstream base version."""
  if not updater_description:
    return DISPLAY_VERSION
  parts = updater_description.split(" / ")
  parts[0] = DISPLAY_VERSION
  return " / ".join(parts)
