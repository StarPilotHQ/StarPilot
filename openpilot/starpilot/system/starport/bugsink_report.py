"""Bounded Android Auto diagnostics for a separately configured Bugsink project."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openpilot.starpilot.system.starport import identity as identity_store, reachability, system_slice
from openpilot.starpilot.system.starport.bugsink_diagnosis import attempt_records, diagnosis, elapsed, sanitize, stream_loss_kind
from openpilot.starpilot.system.starport.bugsink_summary import batch_summary, failure_summary, pairing_summary, session_timeline, software_tags

STATE_PATH = identity_store.DATA_DIR / "bugsink_report.json"
PAIRING_PATH = identity_store.LOG_DIR / "pairing.jsonl"
PAIRING_MAX_BYTES = 64 << 10
INTERVAL_S = 60.0
LOG_SETTLE_S = 120         # a log written this recently may still be a live session, unless it already ended
MAX_SESSIONS = 20          # per drive report; the rest go in the next one
MAX_FAILURES = 5           # distinct failures detailed per drive report
MAX_BREADCRUMBS = 100
TRACE_QUIET = frozenset({"nav_guidance_sent", "nav_focus", "nav_focus_requested", "nav_status_state", "media_key", "media_audio_state",
                         "media_audio_focus", "media_status_state", "hfp_command", "video_focus"})
TRACE_SESSION_BYTES = 64 << 10   # per failed session; the middle goes first
TRACE_TOTAL_BYTES = 200 << 10    # all traces in one report, well under the sender's event cap
EVENT_BUDGET = 400 << 10         # a report is shed down to this before sending; the sender refuses more than 512 KB
MAX_ATTENTION = 6                # sessions per report that get a trace and a device slice
SERIES_KEYS = ("fps", "encode_ms", "frame_age_p95_ms", "wifi_dbm", "wifi_link", "wifi_noise", "load1", "mem_available_mb",
               "temp_max_c", "cpu0_mhz", "cpu4_mhz", "frames_sent", "focused")
SERIES_MAX = 240
EXTRA_TAIL_LINES = 80
FINISHED_EVENT = '"event": "session_stop"'
OUTCOME_RANK = ("streamed", "recovered", "stopped", "dropped", "failed")

def scrub(text: str, limit: int = 300) -> str:
  return sanitize(str(text))[:limit]


def _number(value) -> float | None:
  return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _session_number(path: Path) -> int:
  match = re.fullmatch(r"session-(\d{6})-.*\.jsonl", path.name)
  return int(match[1]) if match else 0


def _percentile(values: list[float], fraction: float) -> float | None:
  if not values:
    return None
  ordered = sorted(values)
  return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def stage_timings(events: list[dict]) -> list[dict]:
  """Each connection stage in the order it was first entered, with seconds since the session started."""
  from openpilot.starpilot.system.starport.compat_report import STAGE_ORDER
  seen: dict[str, float | None] = {}
  for event in events:
    state = event.get("state")
    if event["event"] == "stage" and state in STAGE_ORDER and state not in seen:
      seen[state] = elapsed(events[0], event)
  return [{"stage": stage, "at_s": at} for stage, at in seen.items()]


def stats_series(events: list[dict]) -> dict:
  """Every 30 s stats sample as columns (seconds since the start, then one list per reading), thinned to SERIES_MAX."""
  stats = [event for event in events if event["event"] == "stats"]
  stride = -(-len(stats) // SERIES_MAX) if stats else 1
  stats = stats[::stride]
  if not stats:
    return {}
  columns = {"at_s": [elapsed(events[0], event) for event in stats]}
  for key in SERIES_KEYS:
    values = [event.get(key) if isinstance(event.get(key), (int, float, bool)) else None for event in stats]
    if any(value is not None for value in values):
      columns[key] = values
  return columns


def video_summary(events: list[dict]) -> dict:
  """The video mode and the frame rate delivered while the car showed StarPilot, from the 30 s stats lines."""
  ready = next((event for event in reversed(events) if event["event"] == "projection_ready"), {})
  pipeline = next((event for event in reversed(events) if event["event"] == "frame_pipeline"), {})
  mode = ready.get("mode")
  mode = mode if isinstance(mode, dict) else {}
  stats = [event for event in events if event["event"] == "stats" and event.get("focused") is not False]
  fps = [value for event in stats if (value := _number(event.get("fps"))) is not None]
  encode = [value for event in stats if (value := _number(event.get("encode_ms"))) is not None]
  peaks = [value for event in stats if (value := _number(event.get("encode_peak_ms"))) is not None]
  ages = [value for event in stats if (value := _number(event.get("frame_age_p95_ms"))) is not None]
  wifi = [value for event in stats if (value := _number(event.get("wifi_dbm"))) is not None]
  summary = {
    "resolution": f"{mode['width']}x{mode['height']}" if mode.get("width") and mode.get("height") else None,
    "car_fps": mode.get("fps"),
    "margins": f"{mode.get('margin_width')}x{mode.get('margin_height')}" if mode else None,
    "screen": f"{mode['width'] - (mode.get('margin_width') or 0)}x{mode['height'] - (mode.get('margin_height') or 0)}"
              if mode.get("width") and mode.get("height") else None,
    "density_dpi": mode.get("density") or None,
    "codec": mode.get("codec"),  # present only for H.265; H.264 reports are unchanged
    "target_fps": pipeline.get("target_fps"),
    "encoder": pipeline.get("encoder"),
    "nv12": pipeline.get("nv12"),
    "samples": len(fps),
    "fps_avg": round(sum(fps) / len(fps), 1) if fps else None,
    "fps_min": min(fps) if fps else None,
    "fps_p10": _percentile(fps, 0.1),
    "encode_ms_avg": round(sum(encode) / len(encode), 1) if encode else None,
    "encode_peak_ms": max(peaks) if peaks else None,
    "frame_age_p95_ms_max": max(ages) if ages else None,
    "wifi_dbm_min": min(wifi) if wifi else None,
    "encoder_recoveries": max((int(event.get("encoder_recoveries") or 0) for event in stats), default=0),
    "frames_sent": max((int(event.get("frames_sent") or 0) for event in stats), default=0),
    "unfocused_samples": sum(1 for event in events if event["event"] == "stats" and event.get("focused") is False),
  }
  return {key: value for key, value in summary.items() if value is not None}


def _trim(value):
  if isinstance(value, str):
    return value[:300]
  if isinstance(value, (dict, list)) and len(json.dumps(value, default=str)) > 600:
    return json.dumps(value, default=str)[:600]
  return value


def trace_events(events: list[dict]) -> list[dict]:
  """The session log without routine media and navigation chatter, each event stamped with seconds since the start."""
  trace = []
  for event in events:
    if event["event"] in TRACE_QUIET:
      continue
    values = {key: _trim(value) for key, value in event.items() if key not in ("t", "mono_s", "event")}
    trace.append({"at_s": elapsed(events[0], event), "event": event["event"], **values})
  return trace


def quiet_counts(events: list[dict]) -> dict:
  """How often each routine event the trace leaves out happened, so nav and media activity stays visible."""
  counts: dict[str, int] = {}
  for event in events:
    if event["event"] in TRACE_QUIET:
      counts[event["event"]] = counts.get(event["event"], 0) + 1
  return counts


def bounded_trace(trace: list[dict], limit: int) -> dict:
  """The first quarter and the newest events that fit in ``limit`` bytes of JSON, with how many in between were left out."""
  sizes = [len(json.dumps(event, default=str)) + 2 for event in trace]
  if sum(sizes) <= limit:
    return {"events": list(trace), "omitted": 0}
  head_end, used = 0, 0
  while head_end < len(trace) and used + sizes[head_end] <= limit // 4:
    used += sizes[head_end]
    head_end += 1
  tail_start = len(trace)
  while tail_start > head_end and used + sizes[tail_start - 1] <= limit:
    tail_start -= 1
    used += sizes[tail_start]
  return {"events": trace[:head_end] + trace[tail_start:], "omitted": tail_start - head_end, "head": head_end}


MAX_OFFERED_MODES = 16
SETUP_TYPES = ("builtin", "adapter", "usb", "app")


def _config_field(config: dict, number: int, default=None):
  values = config.get(str(number), config.get(number))
  return values[0] if isinstance(values, list) and values and isinstance(values[0], int) else default


def offered_modes(events: list[dict]) -> list[str]:
  """Every video mode the head unit offered, e.g. "1280x720 H.264 margins 0x240", from its service discovery.

  Kept for working pairings too, so the choice of mode can later be replayed against real cars."""
  from openpilot.starpilot.system.starport.session import CODEC_H264_BP, CODEC_NAMES, RESOLUTION_NAMES
  found = next((event for event in events if event["event"] == "discovered"), {})
  modes: list[str] = []
  for channel in found.get("channels") or []:
    for config in (channel.get("video_configs") or []) if isinstance(channel, dict) else []:
      if not isinstance(config, dict):
        continue

      resolution, codec = _config_field(config, 1), _config_field(config, 10, CODEC_H264_BP)
      text = f"{RESOLUTION_NAMES.get(resolution, f'resolution {resolution}')} {CODEC_NAMES.get(codec, f'codec {codec}')}"
      margins = _config_field(config, 3, 0), _config_field(config, 4, 0)
      if any(margins):
        text += f" margins {margins[0]}x{margins[1]}"
      modes.append(text)
  return modes[:MAX_OFFERED_MODES]


def session_sdp(events: list[dict]) -> dict:
  """How the car's Android Auto RFCOMM channel was found: where it came from, and how many SDP retries it took."""
  found = next((event for event in events if event["event"] == "rfcomm_channel"), {})
  retries = sum(1 for event in events if event["event"] == "sdp_retry")
  if not found and not retries:
    return {}
  return {key: value for key, value in (("channel", found.get("channel") if isinstance(found.get("channel"), int) else None),
                                         ("source", str(found["source"])[:12] if found.get("source") else None),
                                         ("retries", retries or None)) if value is not None}


def session_setup(events: list[dict]) -> dict:
  """The setup type and profile the owner chose, as recorded when the session started."""
  start = next((event for event in events if event["event"] == "session_start"), {})
  setup = start.get("setup") if isinstance(start.get("setup"), dict) else {}
  kind = setup.get("type")
  return {key: value for key, value in (
    ("type", kind if kind in SETUP_TYPES else None),
    ("profile", scrub(setup.get("profile", ""), 40) or None),
    ("profile_source", setup.get("profile_source") if setup.get("profile_source") in ("override", "detected", "standard") else None),
  ) if value}


def session_ladder(events: list[dict]) -> list[dict]:
  """The handshake strategies tried in a session, in order, and how each ended (advanced, wrapped, exhausted, counted,
  dropped, or worked). Kept for working pairings too: it is what shows which strategy a head unit likes."""
  tried: list[dict] = []
  for event in events:
    name = event["event"]
    if name == "ladder_strategy":
      tried.append({"rung": str(event.get("rung", ""))[:24], "learned": bool(event.get("learned")),
                    "at_s": elapsed(events[0], event)})
    elif name == "ladder_failed" and tried:
      tried[-1]["outcome"] = str(event.get("outcome", ""))[:16]
    elif name == "ladder_worked" and tried:
      tried[-1].update(outcome="worked", streamed_s=event.get("streamed_s"), t_ready_s=event.get("t_ready_s"))
  return [{key: value for key, value in item.items() if value is not None} for item in tried[-12:]]


def session_strategy(events: list[dict], setup: dict) -> dict:
  """What a streaming session ran with; the raw material for "this head unit usually likes this profile"."""
  if not any(event["event"] == "video_acknowledged" for event in events):
    return {}
  ready = next((event for event in reversed(events) if event["event"] == "video_setup"), {})
  reply = next((event for event in events if event["event"] == "bootstrap_version_reply"), {})
  mode = ready.get("mode") if isinstance(ready.get("mode"), dict) else {}
  # Only a wireless bootstrap has a version reply; the modern one is logged, the standard one is its absence.
  style = reply.get("style") or ("standard" if any(event["event"] == "bootstrap_start_response" for event in events) else None)
  strategy = {"profile": setup.get("profile"), "window_offered": ready.get("window"), "window_used": ready.get("used_window"),
              "reply_style": style, "channel_type": reply.get("channel_type"),
              "config_index": mode.get("config_index")}
  return {key: value for key, value in strategy.items() if value is not None}


def session_record(path: Path) -> dict:
  """One finished session log reduced to its outcome, stage timings, video and the car it talked to."""
  from openpilot.starpilot.system.starport import compat_report
  events = compat_report.load_events(path)
  report = compat_report.summarize(events)
  started = events[0].get("t", "") if events else ""
  attempts = attempt_records(events)
  relevant = [a for a in attempts if not a["after_car_shutdown"]]
  failures = [a for a in relevant if a.get("error_kind") or a.get("error")]
  streamed = [a for a in relevant if a["video_acknowledged"]]
  drops = [a for a in failures if a["video_acknowledged"]]
  if streamed:
    outcome = "recovered" if failures and relevant[-1]["video_acknowledged"] and relevant[-1] not in failures else "streamed"
    if failures and relevant[-1] in failures:
      outcome = "dropped"
  else:
    outcome = "failed" if failures else "stopped"
  last_failure = failures[-1] if failures else {}
  first_frame = next((e for e in events if e["event"] == "video_acknowledged"), None)
  car_ended = any(a["ended_by_car"] for a in attempts)
  car = report["car"]
  start = next((e for e in events if e["event"] == "session_start"), {})
  software, settings = start.get("software", {}), start.get("settings")
  setup = session_setup(events)
  opened = next((e for e in events if e["event"] == "bluetooth_opened"), None)
  record = {
    "setup": setup,
    "offered_modes": offered_modes(events),
    # True when the comma's address went to the car in the pairing request; None when the car never opened that channel.
    "phone_address_sent": bool(opened.get("phone_address")) if opened is not None else None,
    "strategy": session_strategy(events, setup),
    "ladder": session_ladder(events),
    "sdp": session_sdp(events),
    "software": {key: str(software.get(key) or "unknown") for key in ("commit", "branch")} if isinstance(software, dict) else {},
    "settings": settings if isinstance(settings, dict) else {},
    "seq": _session_number(path),
    "name": path.name,
    "started": started,
    "transport": report["transport"],
    "trigger": report["trigger"],
    "outcome": outcome,
    "furthest_stage": report["furthest_stage"],
    "failed_stage": str(last_failure.get("failed_stage", "")),
    "error_kind": str(last_failure.get("error_kind", "")),
    "reach": str(last_failure.get("reach", "")) or (reachability.classify(
      str(last_failure.get("failed_stage", "")), str(last_failure.get("error_kind", "")), str(last_failure.get("error", "")))
      if last_failure else ""),
    "error": scrub(last_failure.get("error", "")),
    "failures": len(failures),
    "drops": len(drops),
    "ended_by_car": car_ended,
    "attempts": attempts,
    "timing_source": "monotonic" if events and all("mono_s" in e for e in events) else "wall clock (legacy log)",
    "time_to_first_frame_s": elapsed(events[0], first_frame) if first_frame else None,
    "streamed_s": round(sum(a["streamed_s"] or 0 for a in attempts), 1) if first_frame else None,
    "stages": stage_timings(events),
    "video": video_summary(events),
    "focus_lost": report["focus"]["lost"],
    "stalls": report["health"],
    "wifi_security": report["wifi"].get("security") or None,
    "tls": report["tls"].get("version") or None,
    "head_unit": {key: car[key] for key in ("head_unit_make", "head_unit_model", "head_unit_software_version",
                                            "car_make", "car_model", "car_year") if car.get(key)},
    "series": stats_series(events),
    "quiet_events": quiet_counts(events),
    "_report": report,
    "_trace": trace_events(events),
    "_system": system_slice.read_sidecar(path),
    "_dir": path.parent,
    "_names": [car.get("bluetooth_name", ""), car.get("display_name", "")],
  }
  record["attention"] = attention_reasons(record)
  return record


def attention_reasons(session: dict) -> list[str]:
  """Why a session needs its full trace and device slice; empty for one that streamed cleanly."""
  video, reasons = session["video"], []
  if session["outcome"] != "streamed":
    reasons.append(session["outcome"])
  if session["stalls"]:
    reasons.append("stalls")
  if session["focus_lost"]:
    reasons.append("focus_lost")
  if video.get("encoder_recoveries"):
    reasons.append("encoder_recoveries")
  target = video.get("target_fps") or video.get("car_fps")
  if target and video.get("fps_p10") is not None and video["fps_p10"] < 0.5 * target:
    reasons.append("low_fps")
  if video.get("unfocused_samples"):
    reasons.append("unfocused")
  if video.get("wifi_dbm_min") is not None and video["wifi_dbm_min"] <= -75:
    reasons.append("weak_wifi")
  return reasons


def _finished(path: Path) -> bool:
  with path.open("rb") as log:
    log.seek(0, os.SEEK_END)
    log.seek(max(0, log.tell() - 512))
    lines = log.read().decode(errors="replace").strip().splitlines()
  return bool(lines) and FINISHED_EVENT in lines[-1]


def new_sessions(log_dir: Path, last_seq: int, now: float) -> list[dict]:
  """Finished sessions newer than ``last_seq``, oldest first; a log still being written stops the scan."""
  sessions = []
  for path in sorted(log_dir.glob("session-*.jsonl"), key=identity_store.session_log_order):
    if _session_number(path) <= last_seq:
      continue
    if len(sessions) >= MAX_SESSIONS:
      break
    try:
      if now - path.stat().st_mtime < LOG_SETTLE_S and not _finished(path):
        break
      sessions.append(session_record(path))
    except (OSError, ValueError, TypeError, KeyError):
      continue
  return sessions


def drive_summary(sessions: list[dict]) -> dict:
  """Totals across a batch of completed sessions, with sample-weighted frame rate."""
  weighted = [(s["video"]["fps_avg"], s["video"]["samples"]) for s in sessions if s["video"].get("fps_avg") is not None]
  samples = sum(count for _, count in weighted)
  outcome = max((s["outcome"] for s in sessions), key=OUTCOME_RANK.index)
  return {
    "sessions": len(sessions),
    "outcome": outcome,
    "streamed_sessions": sum(1 for s in sessions if s["time_to_first_frame_s"] is not None),
    "streamed_s": round(sum(s["streamed_s"] or 0 for s in sessions), 1),
    "failures": sum(s["failures"] for s in sessions),
    "drops": sum(s["drops"] for s in sessions),
    "fps_avg": round(sum(fps * count for fps, count in weighted) / samples, 1) if samples else None,
    "fps_min": min((s["video"]["fps_min"] for s in sessions if "fps_min" in s["video"]), default=None),
    "resolutions": sorted({s["video"]["resolution"] for s in sessions if s["video"].get("resolution")}),
    "time_to_first_frame_s": next((s["time_to_first_frame_s"] for s in sessions if s["time_to_first_frame_s"] is not None), None),
    "transport": sessions[-1]["transport"] if len({s["transport"] for s in sessions}) == 1 else "mixed",
  }


def _head_unit_label(head_unit: dict) -> str:
  return " ".join(head_unit.get(key, "") for key in ("head_unit_make", "head_unit_model")).strip() or "unknown head unit"


def _car_label(head_unit: dict) -> str:
  return " ".join(head_unit.get(key, "") for key in ("car_year", "car_make", "car_model")).strip() or "unknown car"


def _public(session: dict) -> dict:
  result = {key: value for key, value in session.items() if not key.startswith("_")}
  result["attempt_count"] = len(result.get("attempts", []))
  result["attempts_omitted"] = max(0, result["attempt_count"] - 20)
  result["attempts"] = result.get("attempts", [])[-20:]
  return sanitize(result, session.get("_names", ()))


def failure_signature(session: dict) -> str:
  return f"{diagnosis(session)['category']}:{session['failed_stage'] or 'start'}:{session['error_kind'] or 'none'}"


def failure_records(sessions):
  for session in reversed(sessions):
    for attempt in reversed(session["attempts"]):
      if attempt["after_car_shutdown"] or not (attempt.get("error") or attempt.get("error_kind")):
        continue
      yield {**session, "failed_stage": attempt.get("failed_stage", ""), "error_kind": attempt.get("error_kind", ""),
             "error": attempt.get("error", ""), "failed_attempt": attempt["attempt"]}


def drive_failures(sessions: list[dict]) -> list[dict]:
  """Distinct failures across the batch, newest first."""
  seen, failures = set(), []
  for failure in failure_records(sessions):
    signature = failure_signature(failure)
    if signature not in seen:
      seen.add(signature)
      failures.append(failure)
  return failures[:MAX_FAILURES]


def failure_detail(session: dict) -> dict:
  stage = session["failed_stage"] or session["furthest_stage"] or "start"
  kind = session["error_kind"] or "no error"
  assessment = diagnosis(session)
  names = session.get("_names", ())
  return {
    "title": f"{assessment['category']} at {stage}: {kind}",
    "session": session["seq"],
    "attempt": session.get("failed_attempt"),
    "cause": assessment["category"], "stage": stage, "error_kind": kind, "reach": session.get("reach", ""),
    "loss": stream_loss_kind(session.get("error", "")) if assessment["category"] == "projection_interrupted" else "",
    "summary": sanitize(failure_summary(assessment), names),
    "diagnosis": sanitize(assessment, names),
  }


def compat_text(session: dict) -> str:
  from openpilot.starpilot.system.starport import compat_report
  names = session.get("_names", ())
  report = dict(session["_report"])
  report["car"] = {key: value for key, value in report["car"].items() if key not in ("bluetooth_name", "display_name")}
  report["errors"] = [{**error, "error": scrub(error.get("error", ""))} for error in report["errors"]]
  report["ended"] = [scrub(reason) for reason in report["ended"]]
  report["tls"] = {key: value for key, value in report["tls"].items() if key != "head_unit"}
  return sanitize(compat_report.render_text(report), names)


def _attempt_time(session: dict, at_s) -> str | None:
  try:
    return (datetime.fromisoformat(session["started"]) + timedelta(seconds=at_s or 0)).isoformat()
  except (TypeError, ValueError):
    return None


def attempt_breadcrumbs(sessions: list[dict]) -> list[dict]:
  """Every attempt in the batch, oldest first; failures are errors so they stand out in the timeline."""
  crumbs = []
  for session in sessions:
    for attempt in session["attempts"]:
      failed = bool(attempt.get("error") or attempt.get("error_kind"))
      if failed:
        failure = {**session, "failed_stage": attempt.get("failed_stage", ""), "error_kind": attempt.get("error_kind", "")}
        message = f"{diagnosis(failure)['category']} at {attempt.get('failed_stage') or 'start'}: {attempt.get('error_kind') or 'no error'}"
        if attempt["video_acknowledged"]:
          message = (f"streamed {attempt['streamed_s']}s, then " if attempt["streamed_s"] is not None else "streamed, then ") + message
      elif attempt["video_acknowledged"]:
        message = f"streamed {attempt['streamed_s']}s" if attempt["streamed_s"] is not None else "streamed"
      else:
        message = f"stopped at {attempt['stages'][-1]['stage'] if attempt['stages'] else 'start'}"
      if attempt["after_car_shutdown"]:
        message += " (after car shutdown)"
      level = "info" if not failed else "debug" if attempt["after_car_shutdown"] else "error"
      crumb = {"category": f"session {session['seq']}", "message": f"Attempt {attempt['attempt']}: {message}", "level": level,
               "data": {key: attempt[key] for key in ("error", "failed_at_s") if attempt.get(key)}}
      if (timestamp := _attempt_time(session, attempt.get("failed_at_s") if failed else attempt.get("at_s"))) is not None:
        crumb["timestamp"] = timestamp
      crumbs.append(sanitize(crumb, session.get("_names", ())))
  return crumbs[-MAX_BREADCRUMBS:]


def drive_event(sessions: list[dict]) -> dict:
  """One report per batch: totals, every session, and each distinct failure, grouped by head unit."""
  summary = drive_summary(sessions)
  head_unit = next((s["head_unit"] for s in reversed(sessions) if s["head_unit"]), {})
  video = next((s["video"] for s in reversed(sessions) if s["video"].get("resolution")), {})
  label = _head_unit_label(head_unit)
  setup = next((s["setup"] for s in reversed(sessions) if s.get("setup")), {})
  sent = next((s["phone_address_sent"] for s in reversed(sessions) if s.get("phone_address_sent") is not None), None)
  ladder = next((s["ladder"][-1] for s in reversed(sessions) if s.get("ladder")), {})
  strategy = next((s["strategy"] for s in reversed(sessions) if s.get("strategy")), {})
  failures = [failure_detail(failure) for failure in drive_failures(sessions)]
  failed = [session for session in sessions if session["seq"] in {failure["session"] for failure in failures}]
  heading = "failures" if failures else "sessions"
  message = f"Android Auto {heading}: {label} ({summary['transport'] or 'unknown'})\n\n" + batch_summary(sessions, summary)
  if failures:
    message += "\n\nFailures:\n" + "\n".join(f"  {index}. {failure['title']} (session {failure['session']}, attempt {failure['attempt']})"
                                               for index, failure in enumerate(failures, 1))
    message += "".join(f"\n\n--- {index}. {failure['title']} ---\n{failure['summary']}" for index, failure in enumerate(failures, 1))
    message += "".join(f"\n\n--- Session {session['seq']} ---\n" + sanitize(session_timeline(session), session.get("_names", ()))
                       for session in failed)
  latest = failures[0] if failures else {}
  watched = [session for session in sessions if session["attention"]][-MAX_ATTENTION:]
  per_trace = min(TRACE_SESSION_BYTES, TRACE_TOTAL_BYTES // max(1, len(watched)))
  traces = {f"starport_trace_session_{session['seq']}": sanitize(
    {**bounded_trace(session.get("_trace", []), per_trace), "routine_events": session.get("quiet_events", {})},
    session.get("_names", ())) for session in watched}
  slices = {f"starport_system_session_{session['seq']}": slice_context(session["_system"], session.get("_names", ()))
            for session in watched[-3:] if session.get("_system")}
  extra = extra_logs(watched[-1]) if watched else {}
  reasons = sorted({reason for session in watched for reason in session["attention"]})
  event = {
    "message": message,
    "level": {"streamed": "info", "recovered": "info", "failed": "error"}.get(summary["outcome"], "warning"),
    "fingerprint": ["starport-drive", summary["transport"] or "unknown", label] + (["failures"] if failures else []),
    "tags": {**software_tags(sessions), "aa.report": "drive", "aa.outcome": summary["outcome"], "aa.transport": summary["transport"] or "unknown",
             "aa.head_unit": label, "aa.car": _car_label(head_unit), "aa.resolution": video.get("resolution", "none"),
             "aa.screen": video.get("screen", "none"),
             "aa.encoder": video.get("encoder", "unknown"),
             "aa.fps": str(summary["fps_avg"]) if summary["fps_avg"] is not None else "none",
             "aa.failures": str(len(failures)),
             # Low-cardinality choices only; long values (offered modes, strategy) live in the contexts below.
             **({"aa.setup_type": setup["type"]} if setup.get("type") else {}),
             **({"aa.adapter_model": setup["profile"]} if setup.get("profile") else {}),
             **({"aa.profile_source": setup["profile_source"]} if setup.get("profile_source") else {}),
             **({"aa.phone_address_sent": "yes" if sent else "no"} if sent is not None else {}),
             **({"aa.reply_style": strategy["reply_style"]} if strategy.get("reply_style") else {}),
             **({"aa.wifi_channel_type": str(strategy["channel_type"])} if strategy.get("channel_type") else {}),
             **({"aa.ladder_rung": ladder["rung"], "aa.ladder_learned": "yes" if ladder.get("learned") else "no"} if ladder else {}),
             **({"aa.attention": ",".join(reasons)[:190]} if reasons else {}),
             **({"aa.cause": latest["cause"], "aa.stage": latest["stage"], "aa.error_kind": latest["error_kind"]} if latest else {}),
             **({"aa.reach_class": latest["reach"]} if latest and latest.get("reach") else {}),
             **({"aa.loss_kind": latest["loss"]} if latest and latest.get("loss") else {})},
    "breadcrumbs": {"values": attempt_breadcrumbs(sessions)},
    "contexts": {"starport_drive": summary, "starport_head_unit": head_unit,
                 **{f"starport_session_{index + 1}": _public(session) for index, session in enumerate(sessions)},
                 **{f"starport_failure_{index + 1}": {key: value for key, value in failure.items() if key != "summary"}
                    for index, failure in enumerate(failures)},
                 **{f"starport_compat_report_session_{session['seq']}": {"text": compat_text(session)} for session in watched},
                 **traces, **slices, **extra},
  }
  return fit_event(event)


def slice_context(system: dict, names=()) -> dict:
  """A saved device slice with every line scrubbed of names and SSIDs."""
  sources = system.get("sources", {})
  return {"window": system.get("window", {}),
          **{name: ({"lines": [system_slice.scrub_line(line, names) for line in source["lines"]]} if "lines" in source
                    else {"error": scrub(source.get("error", ""))}) for name, source in sources.items() if isinstance(source, dict)}}


def extra_logs(session: dict) -> dict:
  """The newest lines of the renderer profile and the car UI log, which sit beside the session logs."""
  from openpilot.starpilot.system.starport import compat_report
  contexts = {}
  for name, key in (("render_profile.txt", "starport_render_profile"), ("car_ui.log", "starport_car_ui")):
    data = compat_report._tail(session["_dir"] / name, 32 << 10)
    if data:
      lines = system_slice.system_snapshot.redact(data).decode(errors="replace").splitlines()[-EXTRA_TAIL_LINES:]
      contexts[key] = {"lines": [system_slice.scrub_line(line, session.get("_names", ())) for line in lines]}
  return contexts


def event_size(event: dict) -> int:
  return len(json.dumps(sanitize(event), default=str, allow_nan=False))


def shed_order(contexts: dict) -> list[str]:
  """Context keys from the least to the most valuable, so an oversized report loses detail before it loses the story."""
  def numbered(prefix):
    return sorted((key for key in contexts if key.startswith(prefix)), key=lambda key: int(key.rsplit("_", 1)[-1]))
  return [*(key for key in ("starport_render_profile", "starport_car_ui") if key in contexts),
          *numbered("starport_system_session_"), *numbered("starport_compat_report_session_"),
          *numbered("starport_trace_session_")]


def fit_event(event: dict, budget: int = EVENT_BUDGET) -> dict:
  """Drop optional detail, least valuable first, until the report fits; sessions then lose their series and attempts."""
  shed = []
  contexts = event["contexts"]
  for key in shed_order(contexts):
    if event_size(event) <= budget:
      break
    contexts.pop(key)
    shed.append(key)
  for key in [key for key in contexts if key.startswith("starport_session_")]:
    if event_size(event) <= budget:
      break
    contexts[key] = {name: value for name, value in contexts[key].items() if name not in ("series", "attempts", "stages", "settings")}
    shed.append(f"{key}:detail")
  if shed:
    event["tags"]["aa.truncated"] = "yes"
    contexts["starport_shed"] = {"dropped": shed}
  if event_size(event) > budget:
    event["breadcrumbs"] = {"values": event["breadcrumbs"]["values"][-20:]}
  if event_size(event) > budget:
    keep = ("starport_drive", "starport_head_unit", "starport_shed")
    event["contexts"] = {key: value for key, value in contexts.items() if key in keep or key.startswith("starport_failure_")}
    event["contexts"]["starport_shed"] = {"dropped": ["everything but the totals and failures"]}
  return event


def record_pairing(record: dict, path: Path = PAIRING_PATH) -> None:
  """Append one finished pairing attempt; the journal keeps only its newest lines."""
  try:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    seq = 1 + max((json.loads(line).get("seq", 0) for line in lines if line.strip()), default=0)
    lines.append(json.dumps({"seq": seq, **record}, default=str))
    while len(lines) > 1 and sum(len(line) + 1 for line in lines) > PAIRING_MAX_BYTES:
      lines.pop(0)
    _write_atomic(path, "\n".join(lines) + "\n")
  except (OSError, ValueError):
    pass


def new_pairings(last_seq: int, path: Path = PAIRING_PATH) -> list[dict]:
  try:
    lines = path.read_text().splitlines()
  except OSError:
    return []
  records = []
  for line in lines:
    try:
      record = json.loads(line)
    except ValueError:
      continue
    if isinstance(record, dict) and int(record.get("seq", 0)) > last_seq:
      records.append(record)
  return records


def pairing_system(record: dict, collector: Callable[..., dict]) -> dict:
  """The device's Wi-Fi and Bluetooth activity during a pairing window that did not end in a pairing."""
  try:
    start = datetime.fromisoformat(record["started"]).timestamp()
    system = collector(start, start + float(record.get("duration_s") or record.get("window_s") or 0))
    return {"starport_pairing_system": slice_context(system)}
  except Exception:
    return {}


def pairing_event(record: dict, collector: Callable[..., dict] | None = None) -> dict:
  result = str(record.get("result", "unknown"))
  system = pairing_system(record, collector) if collector is not None and result != "paired" else {}
  return {
    "message": f"Android Auto pairing {result}\n\n" + sanitize(pairing_summary(record)),
    "level": "info" if result == "paired" else "warning",
    "fingerprint": ["starport-pairing", result],
    "tags": {**software_tags([record]), "aa.report": "pairing", "aa.pairing": result, "aa.transport": record.get("transport", "unknown")},
    "contexts": {"starport_pairing": sanitize(record), **system},
  }


class PairingAttempt:
  """The steps of one pairing window, with seconds since it opened; ``finish`` writes it to the journal."""

  def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic, sink: Callable[[dict], None] = record_pairing):
    from openpilot.starpilot.system.starport.bugsink_transport import device_metadata
    software = device_metadata()
    self._clock, self._sink = clock, sink
    self._opened = clock()
    self.record: dict = {"started": datetime.now().astimezone().isoformat(timespec="seconds"), "window_s": seconds, "steps": [],
                         "transport": "wireless", "software": {key: software.get(key, "unknown") for key in ("commit", "branch")}}
    self.step("opened")

  def step(self, name: str, **values) -> None:
    self.record["steps"].append({"step": name, "at_s": round(self._clock() - self._opened, 1), **values})
    if len(self.record["steps"]) > 100:
      self.record["steps"].pop(0)
      self.record["steps_omitted"] = self.record.get("steps_omitted", 0) + 1

  def reached_connection(self) -> bool:
    """The car was approved or connected before the window ended."""
    return any(step.get("step") == "status" and (step.get("approved") or step.get("state") in ("paired", "connected"))
               for step in self.record["steps"])

  def finish(self, result: str, **values) -> None:
    self.step(result, **values)
    self._sink({**self.record, "result": result, "duration_s": round(self._clock() - self._opened, 1)})


def _load_state(path: Path) -> dict:
  try:
    state = json.loads(path.read_text())
    return state if isinstance(state, dict) else {}
  except (OSError, ValueError):
    return {}


def _write_atomic(path: Path, text: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
    temporary_path = Path(temporary.name)
    try:
      temporary.write(text)
      temporary.flush()
      os.fsync(temporary.fileno())
      os.replace(temporary_path, path)
    finally:
      temporary_path.unlink(missing_ok=True)


def send_reports(capture: Callable[..., bool], flush: Callable[[], None] = lambda: None, *, state_path: Path = STATE_PATH,
                 log_dir: Path | None = None, pairing_path: Path = PAIRING_PATH, now: float | None = None,
                 collector: Callable[..., dict] | None = None) -> int:
  """Send what is new since the last call; returns how many reports were queued.

  Capture must return True only after the server accepts the event."""
  now = datetime.now(UTC).timestamp() if now is None else now
  state = _load_state(state_path)
  last_seq, pairing_seq = int(state.get("last_seq", 0)), int(state.get("pairing_seq", 0))
  queued = 0

  for record in new_pairings(pairing_seq, pairing_path):
    if not capture(**pairing_event(record, collector or system_slice.collect)):
      break
    pairing_seq, queued = int(record["seq"]), queued + 1

  sessions = new_sessions(log_dir or identity_store.LOG_DIR, last_seq, now)
  if sessions and capture(**drive_event(sessions)):
    last_seq, queued = sessions[-1]["seq"], queued + 1

  if queued:
    flush()
    _write_atomic(state_path, json.dumps({"last_seq": last_seq, "pairing_seq": pairing_seq}))
  return queued


class Reporter:
  """Background thread in starportd: every minute, while parked, sends what is new."""

  def __init__(self, parked: Callable[[], bool], interval: float = INTERVAL_S):
    self._parked, self._interval = parked, interval
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None

  def start(self) -> Reporter:
    self._thread = threading.Thread(target=self._run, name="aa_bugsink_report", daemon=True)
    self._thread.start()
    return self

  def close(self) -> None:
    self._stop.set()

  def _run(self) -> None:
    from openpilot.starpilot.system.starport.bugsink_transport import configured_sender
    while not self._stop.wait(self._interval):
      try:
        if self._parked():
          capture = configured_sender(active=lambda: not self._stop.is_set() and self._parked())
          if capture is not None:
            send_reports(capture)
      except Exception as error:
        print(json.dumps({"event": "bugsink_report_failed", "error_kind": type(error).__name__}), flush=True)
