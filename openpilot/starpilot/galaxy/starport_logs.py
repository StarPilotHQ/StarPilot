"""Read-only access to the Android Auto logs under /data/starport/logs, for Logs & Diagnostics.

Lists the kept session logs with a one-line summary each, and serves one log or the shareable
bundle (``compat_report.bundle``: every session with its report, the settings without Bluetooth
addresses, and the renderer logs). Only names the log directory itself lists are served.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from openpilot.starpilot.system.starport import compat_report
from openpilot.starpilot.system.starport import identity as identity_store

MAX_FILE_BYTES = 8 << 20  # a session log is normally well under 1 MiB; the renderer logs rotate at 256 KiB


class LogsUnavailable(Exception):
  pass


class LogMissing(Exception):
  pass


def _car(report: dict) -> str:
  car = report.get("car") or {}
  make = car.get("car_make") or car.get("head_unit_make") or ""
  model = car.get("car_model") or car.get("head_unit_model") or ""
  return " ".join(part for part in (make, model) if part)


class StarPortLogs:
  def __init__(self, directory: Path | None = None, config_path: Path | None = None):
    self.directory = directory if directory is not None else identity_store.LOG_DIR
    self.config_path = config_path

  def _files(self) -> Sequence[Path]:
    if not self.directory.is_dir():
      return []
    extras = [self.directory / name for name in compat_report.EXTRA_LOGS]
    return compat_report.session_logs(self.directory) + [path for path in extras if path.is_file() and not path.is_symlink()]

  def list(self) -> dict:
    try:
      sessions, others = [], []
      for path in self._files():
        info = path.stat()
        item = {"name": path.name, "size": info.st_size, "modifiedAt": int(info.st_mtime)}
        if path.name.startswith("session-"):
          report = compat_report.summarize(compat_report.load_events(path))
          item.update(outcome=report.get("outcome") or "", started=report.get("started") or "",
                      transport=report.get("transport") or "", trigger=report.get("trigger") or "", car=_car(report))
          sessions.append(item)
        else:
          others.append(item)
    except OSError:
      raise LogsUnavailable from None
    return {"schemaVersion": 1, "sessions": sessions, "others": others}

  def file(self, name: str) -> tuple[str, bytes]:
    path = next((path for path in self._files() if path.name == name), None)
    if path is None or path.is_symlink():
      raise LogMissing
    try:
      return path.name, compat_report.read_log(path, MAX_FILE_BYTES)
    except FileNotFoundError:
      raise LogMissing from None
    except OSError:
      raise LogsUnavailable from None

  def bundle(self, now: datetime | None = None, *, system_snapshot: bool = True) -> tuple[str, bytes]:
    try:
      data = compat_report.bundle(self.directory, self.config_path, **({} if system_snapshot else {"snapshot": None}))
    except OSError:
      raise LogsUnavailable from None
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"starpilot-starport-logs-{stamp}.zip", data
