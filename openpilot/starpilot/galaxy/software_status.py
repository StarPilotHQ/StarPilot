"""Bounded read-only snapshot of manager and updater status Params."""

from __future__ import annotations

import json

from datetime import UTC, datetime
import os
from pathlib import Path
import re
import stat

from openpilot.common.hardware import PC
from openpilot.common.hardware.hw import Paths
from openpilot.common.params import Params
from openpilot.starpilot.ui.brand import DISPLAY_VERSION


MAX_FIELD_BYTES = 256
PARAMS_TARGET = re.compile(r"(?:\.tmp_[A-Za-z0-9]{1,128}|[A-Za-z0-9][A-Za-z0-9._-]{0,127})\Z")
FIELDS = ("Version", "GitBranch", "GitCommit", "UpdaterState", "UpdaterTargetBranch",
          "LastUpdateTime", "UpdaterLastCheckTime", "UpdaterLastFetchTime", "UpdaterFetchAvailable", "UpdateAvailable",
          "UpdateFailedCount", "UpdaterFastState", "UpdaterOSUpdate")


def _fast_state(raw):
  try:
    value = json.loads(raw) if raw is not None else None
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("stage") not in (
        "preparing", "fetching", "validating", "applying", "submodules", "rebooting", "complete", "error", "finalizing"):
      return None
    detail = value.get("detail")
    if not isinstance(detail, str) or len(detail) > 512:
      return None
    result = {"stage": value["stage"], "detail": detail}
    if type(value.get('background')) is bool:
      result['background'] = value['background']
    percent = value.get('percent')
    if type(percent) in (int, float) and 0 <= percent <= 100:
      result['percent'] = percent
    check = value.get('check')
    if (isinstance(check, dict) and check.get('state') in ('checking', 'complete', 'error') and
        isinstance(check.get('branch'), str) and len(check['branch']) <= 128 and
        isinstance(check.get('detail'), str) and len(check['detail']) <= 512):
      completed = check.get('completedAt')
      if completed is None or type(completed) in (int, float) and 0 <= completed < float('inf'):
        result['check'] = {key: check[key] for key in ('state', 'branch', 'detail', 'completedAt') if key in check}
    return result
  except (TypeError, ValueError):
    return None


class SoftwareUnavailable(Exception):
  pass


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
  return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _text(raw: bytes | None) -> str | None:
  if raw is None:
    return None
  try:
    value = raw.decode("utf-8")
  except UnicodeDecodeError:
    return None
  return value if value and value.isprintable() else None


def _flag(raw: bytes | None) -> bool | None:
  return None if raw not in (b"0", b"1") else raw == b"1"


def _count(raw: bytes | None) -> int | None:
  if raw is None or len(raw) > 6 or not raw.isdigit():
    return None
  value = int(raw)
  return value if value <= 999999 and str(value).encode() == raw else None


def _time(raw: bytes | None) -> str | None:
  text = _text(raw)
  if text is None:
    return None
  try:
    parsed = datetime.fromisoformat(text)
    if parsed.isoformat() != text:
      return None
    if parsed.tzinfo is None:
      parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
  except (ValueError, OverflowError):
    return None


class SoftwareStatus:
  def __init__(self, params: Params | None = None):
    if params is not None:
      self.directory = Path(params.get_param_path("Version")).parent
    else:
      # Params::Params uses OPENPILOT_PREFIX as the active link name. Resolve
      # it without constructing Params, which may create a missing store.
      prefix = os.environ.get("OPENPILOT_PREFIX", "d")
      if not prefix or prefix in (".", "..") or any(character in prefix for character in ("/", "\\", "\0")):
        raise SoftwareUnavailable
      parent = Path(os.environ.get("PARAMS_ROOT", str(Path(Paths.comma_home()) / "params") if PC else "/data/params"))
      self.directory = parent / prefix

  def _root(self) -> Path:
    return self.directory

  @staticmethod
  def _read(root_fd: int, key: str) -> tuple[bytes | None, tuple[int, int, int, int, int] | None]:
    try:
      fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    except FileNotFoundError:
      return None, None
    except OSError:
      raise SoftwareUnavailable from None
    try:
      before = os.fstat(fd)
      if not stat.S_ISREG(before.st_mode):
        raise SoftwareUnavailable
      limit = 8192 if key == "UpdaterFastState" else MAX_FIELD_BYTES
      content = bytearray()
      while len(content) <= limit:
        chunk = os.read(fd, limit + 1 - len(content))
        if not chunk:
          break
        content.extend(chunk)
      after = os.fstat(fd)
      try:
        named = os.stat(key, dir_fd=root_fd, follow_symlinks=False)
      except OSError:
        raise SoftwareUnavailable from None
      if _identity(before) != _identity(after) or _identity(before) != _identity(named) or len(content) > limit:
        raise SoftwareUnavailable
      return bytes(content), _identity(before)
    except OSError:
      raise SoftwareUnavailable from None
    finally:
      os.close(fd)

  def snapshot(self) -> dict:
    root = self._root()
    try:
      # Params stores its active directory behind a symlink. A retained device
      # namespace may use a named sibling instead of Params' .tmp_ directory.
      target = Path(os.readlink(root))
      if not target.is_absolute() or target.parent != root.parent or not PARAMS_TARGET.fullmatch(target.name):
        raise SoftwareUnavailable
      fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
      raise SoftwareUnavailable from None
    try:
      opened = os.fstat(fd)
      captured = {key: self._read(fd, key) for key in FIELDS}
      for key, (_, selected) in captured.items():
        try:
          named = os.stat(key, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
          if selected is None:
            continue
          raise SoftwareUnavailable from None
        if selected is None or _identity(named) != selected or not stat.S_ISREG(named.st_mode):
          raise SoftwareUnavailable
      current = os.stat(root)
      if Path(os.readlink(root)) != target or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise SoftwareUnavailable
    except OSError:
      raise SoftwareUnavailable from None
    finally:
      os.close(fd)
    data = {key: raw for key, (raw, _) in captured.items()}
    try:
      os_update = json.loads(data["UpdaterOSUpdate"] or b"{}")
      if not isinstance(os_update, dict) or type(os_update.get("required")) is not bool or not isinstance(os_update.get("branch"), str):
        os_update = {}
    except (ValueError, TypeError):
      os_update = {}
    return {
      "schemaVersion": 1,
      "installed": {"version": _text(data["Version"]), "displayVersion": DISPLAY_VERSION, "branch": _text(data["GitBranch"]),
                    "commit": _text(data["GitCommit"])},
      "updater": {"state": _text(data["UpdaterState"]), "targetBranch": _text(data["UpdaterTargetBranch"]),
                  "lastCheckedAt": _time(data["UpdaterLastCheckTime"]), "lastSuccessAt": _time(data["LastUpdateTime"]),
                  "lastFetchAt": _time(data["UpdaterLastFetchTime"]),
                  "targetChangeFound": _flag(data["UpdaterFetchAvailable"]),
                  "finalizedUpdateReady": _flag(data["UpdateAvailable"]),
                  "osUpdate": os_update,
                  "failedCount": _count(data["UpdateFailedCount"]), "fast": _fast_state(data["UpdaterFastState"])},
    }
