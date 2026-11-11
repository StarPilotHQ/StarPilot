"""Bounded device logs captured beside an Android Auto session log."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

from openpilot.starpilot.system.starport import identity as identity_store
from openpilot.starpilot.system.starport import system_snapshot

MARGIN_S = 30
DEADLINE_S = 6.0
MAX_LINES = 250          # per source; the newest lines of the window
MAX_LINE = 300
SIDECAR_SUFFIX = ".system.json"
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
SSID = re.compile(r"(?i)\b(e?ssid)\b[=: ]+\S+")


def _timestamp(value: float) -> str:
  return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")  # journalctl reads a bare time as local


def commands(start: float, end: float) -> dict[str, list[str]]:
  """Window-limited journals plus point-in-time state; every command runs at the lowest priority."""
  journal = ["nice", "-n", "19", "journalctl", "--no-pager", "-q", "-o", "short-iso-precise", "--utc",
             "--since", _timestamp(start - MARGIN_S), "--until", _timestamp(end + MARGIN_S), "-n", "2000"]
  return {
    "network": [*journal, "-u", "NetworkManager", "-u", "wpa_supplicant"],
    "bluetooth": [*journal, "-u", "bluetooth"],
    "kernel": [*journal, "-k"],
    "wifi_link": ["nice", "-n", "19", "nmcli", "-t", "-f", "DEVICE,ACTIVE,CHAN,FREQ,SIGNAL", "device", "wifi", "list", "--rescan", "no"],
    "devices": ["nice", "-n", "19", "nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
    "processes": ["nice", "-n", "19", "ps", "-eo", "pid,psr,ni,pcpu,pmem,rss,etime,stat,comm", "--sort=-pcpu"],
  }


def scrub_line(line: str, names=()) -> str:
  """One log line without hardware addresses' leading bytes, quoted names, SSIDs or any known car or phone name."""
  for name in names:
    if name:
      line = line.replace(str(name), "<name>")
  line = system_snapshot._MAC.sub("<address>", line)
  return SSID.sub(lambda match: match[1] + " <ssid>", QUOTED.sub("'<name>'", line))[:MAX_LINE]


def _lines(data: bytes) -> list[str]:
  text = system_snapshot.redact(data).decode("utf-8", errors="replace")
  return [line for line in text.splitlines() if line.strip()][-MAX_LINES:]


def collect(start: float, end: float, *, collector=system_snapshot.collect) -> dict:
  """The slice for [start, end] (epoch seconds), each source as {"lines": [...]} or {"error": ...}."""
  files = collector(start, DEADLINE_S, command_set=commands(start, end))
  try:
    manifest = json.loads(files.get("manifest.json", b"{}"))["commands"]
  except (ValueError, KeyError, TypeError):
    manifest = {}
  result = {"window": {"start": _timestamp(start), "end": _timestamp(end)}, "sources": {}}
  for name in commands(start, end):
    entry = manifest.get(name, {})
    if name in files:
      result["sources"][name] = {"lines": _lines(files[name])}
    else:
      result["sources"][name] = {"error": entry.get("error") or f"no output (exit {entry.get('exit')})"}
    if entry.get("error") and name in files:
      result["sources"][name]["error"] = entry["error"]
  proc = files.get("proc.txt")
  if proc:
    result["sources"]["proc"] = {"lines": _lines(proc)}
  return result


def sidecar_path(log_path: Path, tag: str = "") -> Path:
  match = re.fullmatch(r"session-(\d{6})-.*\.jsonl", log_path.name)
  return log_path.with_name(f"session-{match[1] if match else log_path.stem}{tag}{SIDECAR_SUFFIX}")


def write_sidecar(log_path: Path, start: float, end: float, *, collector=system_snapshot.collect, tag: str = "") -> None:
  """Capture the window and save it beside the session log; never raises."""
  try:
    path = sidecar_path(log_path, tag)
    text = json.dumps(collect(start, end, collector=collector))
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
      handle.write(text)
    os.replace(temporary, path)
  except Exception:
    pass


def write_sidecar_async(log_path: Path, start: float, end: float, tag: str = "") -> threading.Thread:
  thread = threading.Thread(target=write_sidecar, args=(log_path, start, end), kwargs={"tag": tag}, name="aa_system_slice", daemon=True)
  thread.start()
  return thread


def read_sidecar(log_path: Path) -> dict:
  try:
    value = json.loads(sidecar_path(log_path).read_text())
    return value if isinstance(value, dict) else {}
  except (OSError, ValueError):
    return {}


def prune_sidecars(directory: Path | None = None) -> None:
  """Remove sidecars whose session log has been pruned."""
  directory = directory or identity_store.LOG_DIR
  kept = {identity_store.session_log_order(path)[0] for path in directory.glob("session-*.jsonl")}
  for path in directory.glob(f"session-*{SIDECAR_SUFFIX}*"):
    match = re.match(r"session-(\d{6})\.", path.name)
    if not match or int(match[1]) not in kept:
      path.unlink(missing_ok=True)
