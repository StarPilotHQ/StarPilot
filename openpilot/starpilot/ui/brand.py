"""User-facing StarPilot identity, separate from upstream build metadata."""

DISPLAY_VERSION = "7.0.1"


def home_description(updater_description: str) -> str:
  """Keep branch/detail suffixes without showing the upstream base version."""
  if not updater_description:
    return DISPLAY_VERSION
  parts = updater_description.split(" / ")
  parts[0] = DISPLAY_VERSION
  return " / ".join(parts)
