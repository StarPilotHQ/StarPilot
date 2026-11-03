"""Why the last projection reconnected, handed from the supervisor to the next car-screen renderer."""

from __future__ import annotations

import json
import time
from pathlib import Path

from openpilot.common.utils import atomic_write
from openpilot.starpilot.system.starport import identity as identity_store

NOTICE_PATH = identity_store.DATA_DIR / "reconnect_notice.json"
NOTICE_FRESH_S = 120.0  # a notice older than this is about an earlier drive or reconnect (monotonic: same boot only)
SHARED_RADIO_TEXT = "Reconnected: comma Wi-Fi Was Switching Networks"  # on the car screen after a drop caused by the comma's own Wi-Fi


def write(text: str, path: Path = NOTICE_PATH, now: float | None = None) -> None:
  try:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with atomic_write(str(path), overwrite=True) as handle:
      json.dump({"text": text, "written": time.monotonic() if now is None else now}, handle)
  except OSError:
    pass  # the notice is a courtesy; projection never depends on it


def take(path: Path = NOTICE_PATH, now: float | None = None) -> str:
  """Read and remove the pending notice; "" if there is none or it is stale."""
  try:
    record = json.loads(path.read_text())
  except (OSError, ValueError):
    return ""
  try:
    path.unlink()
  except OSError:
    pass
  now = time.monotonic() if now is None else now
  if not isinstance(record, dict) or not isinstance(record.get("text"), str):
    return ""
  written = record.get("written")
  if not isinstance(written, (int, float)) or not 0 <= now - written <= NOTICE_FRESH_S:
    return ""
  return record["text"]
