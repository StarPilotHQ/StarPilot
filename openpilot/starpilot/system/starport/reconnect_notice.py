"""Why the last projection reconnected, handed from the supervisor to the next car-screen renderer."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from openpilot.starpilot.system.starport import identity as identity_store

NOTICE_PATH = identity_store.DATA_DIR / "reconnect_notice.json"
NOTICE_FRESH_S = 120.0  # a notice older than this is about an earlier drive or reconnect (monotonic: same boot only)
NAME_MAX = 24


def shared_radio_text(network: str) -> str:
  """The car-screen line for a drop caused by the comma's own Wi-Fi joining or roaming."""
  name = network.strip()
  if len(name) > NAME_MAX:
    name = name[:NAME_MAX - 3].rstrip() + "..."  # plain dots: the car-screen fonts may lack an ellipsis glyph
  return f"Reconnected: comma Wi-Fi Joined {name}" if name else "Reconnected: comma Wi-Fi Was Switching Networks"


def write(text: str, path: Path = NOTICE_PATH, now: float | None = None) -> None:
  try:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"text": text, "written": time.monotonic() if now is None else now}))
    os.replace(temporary, path)
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
