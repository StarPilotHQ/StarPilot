"""Validated saved volume choices for the existing stock audible alerts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

AUTO = 101
MAX_RAW_BYTES = 16

# The first two warnings retain an audible floor. The immediate warning still
# ramps to full volume in soundd, including the selfdrive timeout warning.
VOLUMES = (
  ("WarningImmediateVolume", "Immediate Warning", 25),
  ("WarningSoftVolume", "Soft Warning", 25),
  ("RefuseVolume", "Engagement Refused", 0),
  ("PromptDistractedVolume", "Distracted Driver", 0),
  ("EngageVolume", "Engagement Chime", 0),
  ("DisengageVolume", "Disengagement Alert", 0),
  ("PromptVolume", "General Prompt", 0),
  ("SLCWarningVolume", "SLC Limit Change", 0),
)
# Optional sounds are silent when their saved volume is unreadable or invalid; an unsaved volume is Auto, like the others.
OPTIONAL = frozenset(("SLCWarningVolume",))
SPECS = {key: (label, minimum) for key, label, minimum in VOLUMES}


@dataclass(frozen=True)
class SavedVolume:
  raw: bytes | None
  value: int | None
  readable: bool = True

  @property
  def valid(self) -> bool:
    return self.readable and self.value is not None


def parse_volume(key: str, raw: bytes | None) -> int | None:
  if key not in SPECS:
    raise KeyError(key)
  if raw is None:
    return AUTO
  if not raw or len(raw) > MAX_RAW_BYTES or not raw.isascii() or not raw.isdigit():
    return None
  value = int(raw)
  minimum = SPECS[key][1]
  return value if value == AUTO or minimum <= value <= 100 else None


def read_volume(params: Any, key: str) -> SavedVolume:
  if key not in SPECS:
    raise KeyError(key)
  try:
    with Path(params.get_param_path(key)).open("rb") as file:
      raw = file.read(MAX_RAW_BYTES + 1)
  except FileNotFoundError:
    raw = None
  except (OSError, ValueError):
    return SavedVolume(None, None, False)
  if raw is not None and len(raw) > MAX_RAW_BYTES:
    return SavedVolume(raw, None, False)
  return SavedVolume(raw, parse_volume(key, raw))


def effective_volume(key: str, saved: int | None, ambient: float, *, immediate_ramp: float | None = None) -> float:
  """Return gain without changing the stock Auto path or immediate-warning ramp."""
  if key not in SPECS:
    raise KeyError(key)
  if saved is None:
    return 0.0 if key in OPTIONAL else ambient
  if saved == AUTO:
    return ambient
  selected = saved / 100.0
  return max(selected, immediate_ramp) if immediate_ramp is not None else selected
