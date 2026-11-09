"""A short, shareable account of how one Android Auto session went with one car.

Reads a session log (``/data/starport/logs/session-*.jsonl``) and reports what a
compatibility report needs: which car and head unit, wired or wireless, how far the
connection got and why it stopped, the Wi-Fi security, TLS suite and video modes the
car offered, and the messages StarPilot does not act on yet. Used by
``openpilot/tools/starport/compat_report.py``.

The report carries no Wi-Fi name, key, address or vehicle identifier; the raw logs,
which the bundle also includes, already leave out keys and vehicle ids.
"""

from __future__ import annotations

import io
import json
import os
import stat
import re
import zipfile
from collections.abc import Callable
from pathlib import Path

from openpilot.starpilot.system.starport import identity as identity_store, reachability
from openpilot.starpilot.system.starport import system_snapshot
from openpilot.starpilot.system.starport.session import describe_video_config

SESSION_GLOB = "session-*.jsonl"
SESSION_NAME = re.compile(r"session-(?:\d{6}-)?[\w.-]+\.jsonl")
MAX_LOG_BYTES = 8 << 20
MAX_BUNDLE_BYTES = 64 << 20
MAX_SESSION_LOGS = 20
MAX_EVENTS = 200_000         # a day of 30 s stats is ~3000 lines; this only stops a runaway file
EXTRA_LOGS = ("render_profile.txt", "render_profile.1.txt", "car_ui.log")
EXTRA_LOG_BYTES = 1 << 20    # the tail of each extra log kept in a bundle

# Connection stages in the order a session passes them, for "how far did it get".
STAGE_ORDER = ("connecting_bluetooth", "discovering", "rfcomm", "wifi_start", "wifi_info", "joining_wifi", "connecting_tcp",
               "waiting_for_usb", "usb_accessory", "authenticating", "negotiating", "streaming")
# ServiceDiscoveryResponse and its headunit_info, by field number (JSON turns the numbers into strings).
SDR_FIELDS = {"2": "car_make", "3": "car_model", "4": "car_year", "7": "head_unit_make", "8": "head_unit_model",
              "9": "head_unit_software_build", "10": "head_unit_software_version", "14": "display_name"}
HEAD_UNIT_INFO_FIELDS = {"1": "car_make", "2": "car_model", "3": "car_year", "5": "head_unit_make", "6": "head_unit_model",
                         "7": "head_unit_software_build", "8": "head_unit_software_version"}
IGNORED_EVENTS = ("control_ignored", "channel_ignored", "video_ignored", "input_ignored", "unexpected_while_waiting",
                  "handshake_ignored", "bootstrap_ignored")


def read_log(path: Path, limit: int, *, tail: bool = False) -> bytes:
  directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
  try:
    fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
  finally:
    os.close(directory)
  with os.fdopen(fd, "rb") as handle:
    info = os.fstat(handle.fileno())
    if not stat.S_ISREG(info.st_mode):
      raise OSError("Not a regular Android Auto log")
    if tail:
      handle.seek(max(0, info.st_size - limit))
    elif info.st_size > limit:
      raise OSError("Android Auto log exceeds the size limit")
    data = handle.read(limit + 1)
    if len(data) > limit:
      raise OSError("Android Auto log exceeds the size limit")
    return data


def events_from_bytes(data: bytes) -> list[dict]:
  events = []
  for line in data.decode("utf-8", "replace").splitlines():
    if len(events) >= MAX_EVENTS:
      break
    try:
      record = json.loads(line)
    except ValueError:
      continue
    if isinstance(record, dict) and isinstance(record.get("event"), str):
      events.append(record)
  return events


def load_events(path: Path) -> list[dict]:
  try:
    return events_from_bytes(read_log(path, MAX_LOG_BYTES))
  except OSError:
    return []


def _first_text(values) -> str:
  if isinstance(values, list) and values and isinstance(values[0], str):
    return values[0]
  return ""


def _car_from_discovery(head_unit: dict) -> dict:
  car = {label: _first_text(head_unit.get(number)) for number, label in SDR_FIELDS.items()}
  info = head_unit.get("17")
  if isinstance(info, list) and info and isinstance(info[0], dict):
    for number, label in HEAD_UNIT_INFO_FIELDS.items():
      car[label] = _first_text(info[0].get(number)) or car.get(label, "")
  return {key: value for key, value in car.items() if value}


def _video_offer(channels: list | None) -> list[str]:
  """The logged video configurations (JSON turned their field numbers into strings), named as the session names them."""
  offered = []
  for channel in channels if isinstance(channels, list) else []:
    if not isinstance(channel, dict):
      continue
    for config in channel.get("video_configs") or []:
      if isinstance(config, dict):
        fields = {int(number): values for number, values in config.items() if str(number).isdigit() and isinstance(values, list)}
        offered.append(describe_video_config(fields, channel.get("display_type")))
  return offered


def summarize(events: list[dict]) -> dict:
  """Everything a compatibility report needs from one session log, as plain JSON-able values."""
  report: dict = {"started": events[0].get("t", "") if events else "", "events": len(events), "transport": "",
                  "trigger": "", "car": {}, "stages": [], "furthest_stage": "", "outcome": "no session",
                  "errors": [], "ended": [], "wifi": {}, "tls": {}, "video": {}, "usb": {}, "bluetooth": {},
                  "focus": {"granted": 0, "lost": 0}, "ignored": [], "stats": {}, "health": {},
                  "ladder": {}, "hangups": 0, "restarts": []}
  ignored: dict[tuple, dict] = {}
  projected = False  # the car acknowledged a frame; focus or the "streaming" stage alone do not show that
  failed_after = None
  car_off = False
  frames = 0  # handshake messages since the attempt began, for logs written before attempt_failed carried hung_up
  for record in events:
    name = record["event"]
    if name == "session_start":
      report["transport"] = {"usb": "wired", "headunitpad": "headunitpad", "openheadunit": "openheadunit"}.get(record.get("receiver"), "wireless")
      report["trigger"] = record.get("trigger", "")
      if record.get("receiver") and record.get("receiver") != "usb":
        report["car"].setdefault("bluetooth_name", record["receiver"])
    elif name == "stage":
      state = record.get("state", "")
      if state and (not report["stages"] or report["stages"][-1] != state):
        report["stages"].append(state)
    elif name == "attempt_start":
      frames = 0
    elif name == "bootstrap_rx":
      frames += 1
    elif name in ("ladder_strategy", "ladder_failed", "ladder_worked") and record.get("rung"):
      rung = report["ladder"].setdefault(record["rung"], {"tries": 0, "failed": 0, "why": ""})
      if name == "ladder_strategy":
        rung["tries"] += 1
      elif name == "ladder_failed":
        rung["failed"] += 1
        rung["why"] = record.get("why") or record.get("reason", "")
      else:
        rung["why"] = "worked"
    elif name == "dongle_restart_requested":
      report["restarts"].append({"t": record.get("t", ""), "hangups": record.get("hangups"), "how": ""})
    elif name == "dongle_restart_done" and report["restarts"]:
      report["restarts"][-1]["how"] = record.get("how", "")
    elif name == "bootstrap_version" and isinstance(record.get("head_unit"), dict):
      report["car"].update({key: value for key, value in record["head_unit"].items() if isinstance(value, str) and value})
      report["wifi"]["version"] = f"{record.get('major')}.{record.get('minor')}"
    elif name == "discovered":
      report["car"].update(_car_from_discovery(record.get("head_unit") or {}))
      report["video"]["offered"] = _video_offer(record.get("channels"))
      services = sorted({service for channel in record.get("channels") or [] if isinstance(channel, dict)
                         for service in channel.get("services") or []})
      report["video"]["car_services"] = services
    elif name == "bootstrap_credentials":
      report["wifi"].update(security=record.get("security", ""), ap_type=record.get("ap_type"))
    elif name == "wifi_joined":
      report["wifi"]["joined"] = True
      report["wifi"]["interface"] = record.get("interface", "")
      if record.get("freq"):
        report["wifi"]["car_freq"] = record["freq"]
      if record.get("wlan0_freq"):
        report["wifi"]["wlan0_freq"] = record["wlan0_freq"]
        if record.get("freq") and record["freq"] != record["wlan0_freq"]:
          report["wifi"]["radio"] = "MCC (2 channels)"
    elif name == "version":
      report["tls"]["protocol"] = f"{record.get('major')}.{record.get('minor')} (replied {record.get('reply')})"
    elif name == "tls_established":
      report["tls"].update(version=record.get("version", ""), cipher=record.get("cipher", ""))
    elif name == "tls_failed":
      report["tls"]["failed"] = record.get("reason") or record.get("error", "")
    elif name == "head_unit_verified":
      report["tls"]["head_unit"] = record.get("subject", "")
    elif name == "authentication_rejected":
      report["tls"]["rejected_status"] = record.get("status")
    elif name == "projection_ready":
      mode = record.get("mode") or {}
      margins = f"{mode.get('margin_width')}x{mode.get('margin_height')}"
      report["video"]["chosen"] = f"{mode.get('width')}x{mode.get('height')} @ {mode.get('fps')} fps, margins {margins}"
    elif name == "video_focus":
      report["focus"]["granted" if record.get("focused") else "lost"] += 1
    elif name == "video_acknowledged":
      projected = True
      failed_after = None
      car_off = False
    elif name == "usb_gadget_prepared":
      report["usb"]["started_as"] = record.get("mode", "")
    elif name == "usb_no_accessory_start":
      report["usb"]["no_handshake_after_s"] = record.get("waited")
    elif name == "usb_accessory_ready":
      report["usb"]["method"] = record.get("method", "")
      strings = record.get("strings") or {}
      report["usb"]["accessory_strings"] = strings  # AOA requires "Android" / "Android Auto"; description and version vary
    elif name == "usb_state":
      report["usb"].setdefault("states", [])
      if not report["usb"]["states"] or report["usb"]["states"][-1] != record.get("state"):
        report["usb"]["states"] = (report["usb"]["states"] + [record.get("state")])[-12:]
    elif name == "hfp_wait":
      report["bluetooth"]["hands_free_before_rfcomm"] = record.get("linked")
    elif name == "hfp_connected":
      report["bluetooth"]["hands_free"] = True
    elif name == "rfcomm_channel":
      report["bluetooth"]["rfcomm_channel"] = record.get("channel")
    elif name == "attempt_failed":
      hung_up = record.get("hung_up")
      if hung_up is None:
        hung_up = reachability.hung_up_early(record.get("stage", ""), record.get("kind", ""), record.get("error", ""), frames)
      report["hangups"] += bool(hung_up)
      error = {"stage": record.get("stage", ""), "error": record.get("error", "")}
      if record.get("where"):
        error["where"] = record["where"][-1]  # the innermost frame: which call actually raised
      if car_off:
        error["after_car_shutdown"] = True
      if error not in report["errors"]:
        report["errors"].append(error)
      if projected and not car_off:
        failed_after = error["error"]
    elif name == "session_ended" and record.get("reason"):
      report["ended"].append(record["reason"])
      car_off = car_off or str(record["reason"]).startswith("Head unit ended projection")
    elif name in ("stream_stall", "link_check_slow", "link_check_failed", "link_lost"):
      entry = report["health"].setdefault(name, {"count": 0, "max_ms": 0})
      entry["count"] += 1
      entry["max_ms"] = max(entry["max_ms"], int(record.get("ms") or 0))
    elif name == "stats":
      report["stats"] = {key: value for key, value in record.items() if key not in ("t", "event")}
    elif name in IGNORED_EVENTS:
      key = (name, record.get("channel"), record.get("kind", record.get("message")) if name == "bootstrap_ignored" else record.get("kind"))
      entry = ignored.setdefault(key, {"event": name, "channel": record.get("channel"), "kind": key[2], "count": 0})
      entry["count"] = max(entry["count"] + 1, int(record.get("count") or 0))
      if "message" in record and name != "bootstrap_ignored":
        entry["example"] = record["message"]
  report["ignored"] = sorted(ignored.values(), key=lambda entry: -entry["count"])[:20]
  reached = [stage for stage in report["stages"] if stage in STAGE_ORDER]
  report["furthest_stage"] = max(reached, key=STAGE_ORDER.index) if reached else ""
  if projected:
    report["outcome"] = f"projected, then failed: {failed_after}" if failed_after else "projected"
  elif errors := [error for error in report["errors"] if not error.get("after_car_shutdown")]:
    report["outcome"] = f"failed: {errors[-1]['error']}"
  elif events:
    report["outcome"] = f"stopped at {report['furthest_stage'] or 'start'}"
  return report


def stage_line(stages: list[str]) -> str:
  """Stages in order, one lap per attempt, with identical laps in a row shown once with a count."""
  laps: list[list[str]] = []
  for stage in stages:
    if not laps or stage == "connecting_bluetooth":
      laps.append([])
    laps[-1].append(stage)
  parts: list[tuple[list[str], int]] = []
  for lap in laps:
    if parts and parts[-1][0] == lap:
      parts[-1] = (lap, parts[-1][1] + 1)
    else:
      parts.append((lap, 1))
  return " | ".join(" > ".join(lap) + (f" (x{count})" if count > 1 else "") for lap, count in parts)


def render_text(report: dict, name: str = "") -> str:
  """The report as plain text, to paste into an issue or a message."""
  lines = [f"StarPilot Android Auto compatibility report{f' ({name})' if name else ''}",
           f"Started: {report['started'] or 'unknown'}   Connection: {report['transport'] or 'unknown'}   Trigger: {report['trigger'] or '-'}",
           f"Outcome: {report['outcome']}"]
  car = report["car"]
  if car:
    lines.append("Car: " + ", ".join(f"{key.replace('_', ' ')}: {value}" for key, value in car.items()))
  lines.append("Stages: " + (stage_line(report["stages"]) or "none"))
  for rung, entry in (report.get("ladder") or {}).items():
    lines.append(f"Handshake {rung}: {entry['tries']} tries, {entry['failed']} counted failures" +
                 (f" (last: {entry['why']})" if entry.get("why") else ""))
  if report.get("hangups"):
    lines.append(f"Hung up before any handshake message: {report['hangups']} attempts")
  for restart in report.get("restarts") or []:
    lines.append(f"Asked to restart the dongle at {restart['t'] or '?'} after {restart['hangups']} hang-ups: {restart['how'] or 'still waiting'}")
  for error in report["errors"][-5:]:
    lines.append(f"Error ({error['stage']}{', after car shutdown' if error.get('after_car_shutdown') else ''}): {error['error']}" +
                 (f"  [at {error['where']}]" if error.get("where") else ""))
  for reason in report["ended"][-3:]:
    lines.append(f"Ended by car: {reason}")
  for title, section in (("Wi-Fi", report["wifi"]), ("Bluetooth", report["bluetooth"]), ("TLS", report["tls"]),
                         ("USB", report["usb"]), ("Video", report["video"])):
    if section:
      lines.append(f"{title}: " + "; ".join(f"{key.replace('_', ' ')}: {value}" for key, value in section.items()))
  lines.append(f"Focus: granted {report['focus']['granted']}, lost {report['focus']['lost']}")
  if report["health"]:
    lines.append("Stalls: " + "; ".join(f"{key.replace('_', ' ')} x{value['count']} (max {value['max_ms']} ms)"
                                        for key, value in report["health"].items()))
  if report["stats"]:
    lines.append("Last stats: " + json.dumps(report["stats"], default=str)[:400])
  for entry in report["ignored"][:10]:
    lines.append(f"Not handled: {entry['event']} channel {entry['channel']} kind {entry['kind']} x{entry['count']}"
                 + (f" e.g. {json.dumps(entry['example'], default=str)[:200]}" if entry.get("example") is not None else ""))
  return "\n".join(lines) + "\n"


def session_logs(log_dir: Path | None = None) -> list[Path]:
  """Session logs, newest first."""
  directory = log_dir or identity_store.LOG_DIR
  try:
    logs = [path for path in directory.glob(SESSION_GLOB) if not path.is_symlink() and path.is_file() and SESSION_NAME.fullmatch(path.name)]
  except OSError:
    return []
  return sorted(logs, key=identity_store.session_log_order, reverse=True)[:MAX_SESSION_LOGS]


def session_path(name: str, log_dir: Path | None = None) -> Path | None:
  """The log called ``name``, only if it is one of the session logs (no paths from outside)."""
  return next((path for path in session_logs(log_dir) if path.name == name), None)


def _tail(path: Path, limit: int) -> bytes | None:
  try:
    return read_log(path, limit, tail=True)
  except OSError:
    return None


def shareable_setup_state(path: Path, chosen: str = "") -> dict | None:
  """The handshake ladder's saved state with car addresses replaced by whether each is the chosen car."""
  try:
    data = json.loads(read_log(path, MAX_LOG_BYTES))
  except (OSError, ValueError, UnicodeError):
    return None
  cars = data.get("cars") if isinstance(data, dict) else None
  if not isinstance(cars, dict):
    return None
  return {"version": data.get("version"),
          "cars": [{"chosen": bool(chosen) and str(address).upper() == chosen.upper(), **entry}
                   for address, entry in cars.items() if isinstance(entry, dict)]}


def shareable_config(config: dict) -> dict:
  """The settings without the Bluetooth addresses of the car and its companion device."""
  shared = dict(config)
  for key in ("receiver_address", "companion_address"):
    if shared.get(key):
      shared[key] = "redacted"
  shared["rfcomm_cache"] = sorted((shared.get("rfcomm_cache") or {}).values())  # the channels, not whose they are
  return shared


RECENT_DIAGNOSIS_SESSIONS = 5
MAX_DIAGNOSIS_BYTES = 512 << 10


def _diagnosis_report(logs: list[Path]) -> bytes:
  """The same tags and per-session detail a Bugsink report carries (setup, offered modes, reach class, ladder, SDP...),
  for the newest few sessions, sanitized. An addition to the bundle: if it cannot be built the bundle is unaffected."""
  try:
    from openpilot.starpilot.system.starport import bugsink_report
    records = []
    for path in logs[:RECENT_DIAGNOSIS_SESSIONS]:
      try:
        records.append(bugsink_report.session_record(path))
      except (OSError, ValueError, KeyError, TypeError):
        continue
    if not records:
      return b""
    records.reverse()   # oldest first, as the drive report expects
    event = bugsink_report.drive_event(records)
    report = {"tags": event["tags"], "message": event["message"], "sessions": [bugsink_report._public(record) for record in records]}
    data = json.dumps(report, indent=1, default=str).encode()
    if len(data) > MAX_DIAGNOSIS_BYTES:
      data = json.dumps({key: report[key] for key in ("tags", "message")}, indent=1, default=str).encode()
    return data
  except Exception:
    return b""


def bundle(log_dir: Path | None = None, config_path: Path | None = None, *,
           snapshot: Callable[[float], dict[str, bytes]] | None = system_snapshot.collect) -> bytes:
  """A zip for a bug report: every session log with its report, the settings, the renderer logs and a snapshot of the
  device (system journals, kernel log, build, load) under system/; never the identity or a full hardware address."""
  directory = log_dir or identity_store.LOG_DIR
  logs = session_logs(directory)
  output = io.BytesIO()
  total = 0
  with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    def add(name: str, data: bytes):
      nonlocal total
      total += len(data)
      if total > MAX_BUNDLE_BYTES:
        raise OSError("Android Auto bundle exceeds the size limit")
      archive.writestr(name, system_snapshot.redact(data))

    summaries, starts = [], []
    for path in logs:
      try:
        data = read_log(path, MAX_LOG_BYTES)
      except FileNotFoundError:
        continue
      report = summarize(events_from_bytes(data))
      starts.append(report.get("started") or "")
      summaries.append(render_text(report, path.name))
      add(f"logs/{path.name}", data)
      add(f"reports/{path.stem}.json", json.dumps(report, indent=2, default=str).encode())
    add("REPORT.txt", ("\n".join(summaries) or "No Android Auto sessions have been logged yet.\n").encode())
    report = _diagnosis_report(logs)
    if report:
      add("report.json", report)
    config = identity_store.load_config(config_path)
    add("config.json", json.dumps(shareable_config(config), indent=2, default=str).encode())
    setup_state = shareable_setup_state((config_path or identity_store.CONFIG_PATH).with_name("setup_state.json"),
                                        config.get("receiver_address", ""))
    if setup_state is not None:
      add("setup_state.json", json.dumps(setup_state, indent=2, default=str).encode())
    for name in EXTRA_LOGS:
      data = _tail(directory / name, EXTRA_LOG_BYTES)
      if data is not None:
        add(f"logs/{name}", data)
    if snapshot is not None:
      # Optional context: whatever does not fit the size budget is left out rather than failing the download.
      for name, data in snapshot(system_snapshot.window_start(starts)).items():
        if total + len(data) > MAX_BUNDLE_BYTES:
          continue
        add(f"system/{name}", data)
  return output.getvalue()
