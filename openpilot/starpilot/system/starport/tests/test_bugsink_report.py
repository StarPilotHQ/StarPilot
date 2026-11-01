"""Android Auto drive and pairing reports for Bugsink, built from session logs without a network."""

import json
import os
from datetime import UTC, datetime

from openpilot.starpilot.system.starport import bugsink_report as br


def write_session(log_dir, number, events, finished=True):
  log_dir.mkdir(parents=True, exist_ok=True)
  path = log_dir / f"session-{number:06d}-20261016-120000.jsonl"
  lines = [{"t": f"2026-10-16T12:{minute:02d}:{second:02d}.000+00:00", "event": name, **values}
           for minute, second, name, values in events]
  if finished:
    lines.append({"t": "2026-10-16T12:59:00.000+00:00", "event": "session_stop"})
  path.write_text("".join(json.dumps(line) + "\n" for line in lines))
  return path


STREAMED = [
  (0, 0, "session_start", {"receiver": "Honda HFL", "trigger": "auto"}),
  (0, 1, "stage", {"state": "connecting_bluetooth"}),
  (0, 3, "stage", {"state": "rfcomm"}),
  (0, 6, "stage", {"state": "joining_wifi"}),
  (0, 9, "stage", {"state": "authenticating"}),
  (0, 10, "discovered", {"head_unit": {"2": ["Honda"], "3": ["Civic"], "4": ["2022"], "7": ["Alpine"], "8": ["HU-1"],
                                       "14": ["Danny's Civic"]}, "channels": []}),
  (0, 11, "projection_ready", {"mode": {"width": 1280, "height": 720, "fps": 30, "margin_width": 0, "margin_height": 240, "density": 160}}),
  (0, 11, "frame_pipeline", {"nv12": True, "async_readback": True, "target_fps": 30, "encoder": "v4l2"}),
  (0, 12, "stage", {"state": "streaming"}),
  (0, 12, "video_acknowledged", {}),
  (0, 42, "stats", {"fps": 30.0, "encode_ms": 6.0, "encode_peak_ms": 9.0, "frame_age_p95_ms": 40, "wifi_dbm": -50,
                    "focused": True, "frames_sent": 900, "encoder_recoveries": 0}),
  (1, 12, "stats", {"fps": 20.0, "encode_ms": 8.0, "encode_peak_ms": 15.0, "frame_age_p95_ms": 90, "wifi_dbm": -70,
                    "focused": True, "frames_sent": 1500, "encoder_recoveries": 1}),
  (1, 42, "stats", {"fps": 0.0, "focused": False}),
]

FAILED = [
  (0, 0, "session_start", {"receiver": "Honda HFL", "trigger": "manual"}),
  (0, 1, "stage", {"state": "connecting_bluetooth"}),
  (0, 4, "stage", {"state": "joining_wifi"}),
  (0, 30, "attempt_failed", {"stage": "joining_wifi", "error": "Joining Wi-Fi: no route to 192.168.1.4 from AA:BB:CC:DD:EE:FF",
                             "kind": "TimeoutError"}),
]


def test_session_record_reports_stages_resolution_and_fps(tmp_path):
  record = br.session_record(write_session(tmp_path, 1, STREAMED))
  assert record["outcome"] == "streamed" and record["transport"] == "wireless"
  assert record["time_to_first_frame_s"] == 12.0
  assert [stage["stage"] for stage in record["stages"]] == ["connecting_bluetooth", "rfcomm", "joining_wifi", "authenticating", "streaming"]
  assert record["stages"][2]["at_s"] == 6.0
  video = record["video"]
  assert video["resolution"] == "1280x720" and video["car_fps"] == 30 and video["target_fps"] == 30 and video["encoder"] == "v4l2"
  assert video["screen"] == "1280x480" and video["margins"] == "0x240" and video["density_dpi"] == 160
  assert video["samples"] == 2 and video["fps_avg"] == 25.0 and video["fps_min"] == 20.0  # the unfocused sample is left out
  assert video["encode_peak_ms"] == 15.0 and video["wifi_dbm_min"] == -70 and video["encoder_recoveries"] == 1
  assert record["head_unit"] == {"head_unit_make": "Alpine", "head_unit_model": "HU-1", "car_make": "Honda",
                                 "car_model": "Civic", "car_year": "2022"}


def test_drive_event_groups_sessions_by_head_unit_and_weights_fps(tmp_path):
  sessions = [br.session_record(write_session(tmp_path, 1, STREAMED)), br.session_record(write_session(tmp_path, 2, FAILED))]
  event = br.drive_event(sessions)
  assert event["fingerprint"] == ["starport-drive", "wireless", "Alpine HU-1", "failures"]
  assert event["message"].startswith("Android Auto failures: Alpine HU-1 (wireless)")
  crumbs = event["breadcrumbs"]["values"]
  assert [crumb["level"] for crumb in crumbs] == ["info", "error"]
  assert crumbs[1]["message"] == "Attempt 1: wifi_join_failed at joining_wifi: TimeoutError" and crumbs[1]["category"] == "session 2"
  assert crumbs[1]["timestamp"] == "2026-10-16T12:00:30+00:00"
  assert event["level"] == "error" and event["tags"]["aa.outcome"] == "failed"
  assert event["tags"]["aa.resolution"] == "1280x720" and event["tags"]["aa.screen"] == "1280x480" and event["tags"]["aa.fps"] == "25.0"
  drive = event["contexts"]["starport_drive"]
  assert drive["sessions"] == 2 and drive["failures"] == 1 and drive["fps_avg"] == 25.0 and drive["resolutions"] == ["1280x720"]
  assert "_report" not in event["contexts"]["starport_session_1"]
  assert "Danny's Civic" not in json.dumps(event)


def test_failures_are_part_of_the_drive_report_and_hide_names(tmp_path):
  event = br.drive_event([br.session_record(write_session(tmp_path, 1, FAILED))])
  assert event["fingerprint"] == ["starport-drive", "wireless", "unknown head unit", "failures"]
  assert "1. wifi_join_failed at joining_wifi: TimeoutError (session 1, attempt 1)" in event["message"]
  assert event["tags"]["aa.cause"] == "wifi_join_failed" and event["tags"]["aa.failures"] == "1"
  text = json.dumps(event)
  for secret in ("AA:BB:CC:DD:EE:FF", "192.168.1.4", "Honda HFL"):
    assert secret not in text
  assert "Stages: connecting_bluetooth > joining_wifi" in event["contexts"]["starport_compat_report_session_1"]["text"]


def test_send_reports_once_as_one_event_per_batch(tmp_path):
  logs, state = tmp_path / "logs", tmp_path / "state.json"
  write_session(logs, 1, STREAMED)
  for number in range(2, 4):
    write_session(logs, number, FAILED)
  captured, flushes = [], []

  def capture(**event):
    captured.append(event)
    return True

  queued = br.send_reports(capture, lambda: flushes.append(1), state_path=state, log_dir=logs, pairing_path=tmp_path / "p.jsonl")
  assert queued == 1 and len(flushes) == 1
  assert [event["tags"]["aa.report"] for event in captured] == ["drive"]
  assert captured[0]["tags"]["aa.failures"] == "1"  # two failures of the same kind are detailed once
  assert json.loads(state.read_text())["last_seq"] == 3

  assert br.send_reports(capture, state_path=state, log_dir=logs, pairing_path=tmp_path / "p.jsonl") == 0


def test_dropped_stream_breadcrumb_says_it_streamed_first(tmp_path):
  session = br.session_record(write_session(tmp_path, 1, STREAMED + [
    (2, 0, "attempt_failed", {"stage": "streaming", "kind": "EOFError", "error": "peer closed"}),
  ]))
  crumb = br.drive_event([session])["breadcrumbs"]["values"][0]
  assert crumb["level"] == "error"
  assert crumb["message"] == "Attempt 1: streamed 108.0s, then projection_interrupted at streaming: EOFError"


def test_session_timeline_and_compat_report_appear_once(tmp_path):
  path = write_session(tmp_path / "logs", 1, FAILED + [
    (1, 0, "stage", {"state": "authenticating"}),
    (1, 20, "attempt_failed", {"stage": "authenticating", "kind": "TimeoutError", "error": "timed out"}),
  ])
  event = br.drive_event([br.session_record(path)])
  assert event["message"].count("Attempt timeline:") == 1 and event["message"].count("Next steps:") == 2
  assert [key for key in event["contexts"] if key.startswith("starport_compat_report")] == ["starport_compat_report_session_1"]
  assert "compat_report" not in event["contexts"]["starport_failure_1"]


def test_clean_drives_keep_their_own_issue(tmp_path):
  event = br.drive_event([br.session_record(write_session(tmp_path, 1, STREAMED))])
  assert event["fingerprint"] == ["starport-drive", "wireless", "Alpine HU-1"]
  assert event["message"].startswith("Android Auto sessions: Alpine HU-1 (wireless)")
  assert [crumb["level"] for crumb in event["breadcrumbs"]["values"]] == ["info"]


def test_unaccepted_reports_are_retried(tmp_path):
  logs, state = tmp_path / "logs", tmp_path / "state.json"
  write_session(logs, 1, STREAMED)
  assert br.send_reports(lambda **_: False, state_path=state, log_dir=logs, pairing_path=tmp_path / "p.jsonl") == 0
  assert not state.exists()
  assert br.send_reports(lambda **_: True, state_path=state, log_dir=logs, pairing_path=tmp_path / "p.jsonl") == 1


def test_live_session_waits_until_finished(tmp_path):
  logs = tmp_path / "logs"
  path = write_session(logs, 1, STREAMED, finished=False)
  assert br.new_sessions(logs, 0, datetime.now(UTC).timestamp()) == []
  old = datetime.now(UTC).timestamp() - br.LOG_SETTLE_S - 1
  os.utime(path, (old, old))
  assert len(br.new_sessions(logs, 0, datetime.now(UTC).timestamp())) == 1


def test_pairing_attempts_are_journaled_and_reported(tmp_path):
  journal = tmp_path / "pairing.jsonl"
  clock = iter([0.0, 0.0, 4.0, 9.5, 12.0, 12.0])
  attempt = br.PairingAttempt(180.0, clock=lambda: next(clock), sink=lambda record: br.record_pairing(record, journal))
  attempt.step("pair_requested")
  attempt.step("status", state="paired", prompt=False, approved=True)
  attempt.finish("paired")
  records = br.new_pairings(0, journal)
  assert len(records) == 1 and records[0]["result"] == "paired" and records[0]["duration_s"] == 12.0
  assert [step["step"] for step in records[0]["steps"]] == ["opened", "pair_requested", "status", "paired"]

  captured = []
  assert br.send_reports(lambda **event: captured.append(event) or True, state_path=tmp_path / "s.json",
                         log_dir=tmp_path / "logs", pairing_path=journal) == 1
  assert captured[0]["fingerprint"] == ["starport-pairing", "paired"] and captured[0]["level"] == "info"
  assert br.new_pairings(json.loads((tmp_path / "s.json").read_text())["pairing_seq"], journal) == []


def test_pairing_journal_stays_bounded(tmp_path):
  journal = tmp_path / "pairing.jsonl"
  for index in range(400):
    br.record_pairing({"result": "expired", "steps": [{"step": "opened", "at_s": 0}] * 4, "index": index}, journal)
  assert journal.stat().st_size <= br.PAIRING_MAX_BYTES
  assert br.new_pairings(0, journal)[-1]["seq"] == 400


def test_recovered_attempts_still_report_the_original_failure(tmp_path):
  path = write_session(tmp_path / "logs", 1, FAILED + [(1, 0, "attempt_start", {})] + STREAMED[1:])
  session = br.session_record(path)
  assert session["outcome"] == "recovered"
  assert len(session["attempts"]) == 2
  events = []
  br.send_reports(lambda **e: events.append(e) or True, state_path=tmp_path / "state.json", log_dir=path.parent,
                  pairing_path=tmp_path / "pairing.jsonl")
  assert len(events) == 1
  diagnosis = events[0]["contexts"]["starport_failure_1"]["diagnosis"]
  assert diagnosis["recovered"] is True and diagnosis["confidence"] == "suspected"


def test_shutdown_does_not_turn_later_retries_into_stream_drops(tmp_path):
  session = br.session_record(write_session(tmp_path, 1, STREAMED + [
    (2, 0, "session_ended", {"reason": "Head unit ended projection: shutdown"}),
    (2, 2, "stage", {"state": "connecting_bluetooth"}),
    (2, 10, "attempt_failed", {"stage": "connecting_bluetooth", "kind": "OSError", "error": "Host is down"}),
  ]))
  assert session["outcome"] == "streamed" and session["ended_by_car"]
  assert session["failures"] == session["drops"] == 0
  assert session["streamed_s"] == 108
  assert session["attempts"][-1]["after_car_shutdown"]


def test_clock_correction_uses_monotonic_order_and_durations(tmp_path):
  path = write_session(tmp_path, 1, STREAMED)
  events = [json.loads(line) for line in path.read_text().splitlines()]
  for i, event in enumerate(events):
    event["mono_s"] = 20.0 + i
    event["t"] = "2026-07-01T00:00:00+00:00" if i < 5 else "2026-10-19T00:00:00+00:00"
  path.write_text("".join(json.dumps(event) + "\n" for event in events))
  session = br.session_record(path)
  assert session["time_to_first_frame_s"] == 9
  assert session["streamed_s"] == 4
  assert session["timing_source"] == "monotonic"


def test_distinct_failures_within_one_session_are_actionable(tmp_path):
  path = write_session(tmp_path / "logs", 1, FAILED + [
    (1, 0, "stage", {"state": "authenticating"}),
    (1, 20, "attempt_failed", {"stage": "authenticating", "kind": "TimeoutError", "error": "timed out"}),
  ])
  events = []
  br.send_reports(lambda **e: events.append(e) or True, state_path=tmp_path / "state.json", log_dir=path.parent,
                  pairing_path=tmp_path / "pairing.jsonl")
  assert len(events) == 1
  causes = {context["diagnosis"]["category"] for key, context in events[0]["contexts"].items() if key.startswith("starport_failure_")}
  assert causes == {"wifi_join_failed", "authentication_incomplete"}


def test_nested_pairing_and_failure_fields_are_scrubbed(tmp_path):
  event = br.pairing_event({"result": "failed", "steps": [{"error": "peer fe80::1234 password=secret ssid='My Car'"}]})
  assert "fe80::1234" not in json.dumps(event) and "secret" not in json.dumps(event) and "My Car" not in json.dumps(event)
  path = write_session(tmp_path, 1, FAILED + [(0, 31, "attempt_failed", {
    "stage": "joining_wifi", "kind": "OSError", "error": "Honda HFL password=secret",
  })])
  event = br.drive_event([br.session_record(path)])
  assert "Honda HFL" not in json.dumps(event) and "secret" not in json.dumps(event)


def test_unmanaged_is_observed_but_scan_contention_is_only_suspected(tmp_path):
  session = br.session_record(write_session(tmp_path, 1, FAILED))
  session["error"] = "wlan0 is strictly unmanaged"
  observed = br.diagnosis(session)
  assert observed["category"] == "wifi_not_ready" and observed["confidence"] == "observed"
  session["error"] = "Wi-Fi join timed out"
  suspected = br.diagnosis(session)
  assert suspected["category"] == "wifi_join_failed" and suspected["confidence"] == "suspected"


def test_companion_tablet_modes_keep_their_transport(tmp_path):
  for index, transport in enumerate(("headunitpad", "openheadunit"), 1):
    events = [(0, 0, "session_start", {"receiver": transport})] + FAILED[1:]
    session = br.session_record(write_session(tmp_path, index, events))
    assert session["transport"] == transport
    assert br.drive_event([session])["tags"]["aa.transport"] == transport


def test_readable_summary_uses_session_commit_and_wired_connection(tmp_path):
  events = [(0, 0, "session_start", {"receiver": "usb", "software": {"commit": "abc123-session", "branch": "session-branch"}})] + FAILED[1:]
  session = br.session_record(write_session(tmp_path, 1, events))
  event = br.drive_event([session])
  assert "Connection: Wired USB" in event["message"]
  assert "Session commit: abc123-session" in event["message"]
  assert "Branch: session-branch" in event["message"]
  assert "Attempt timeline:" in event["message"] and "Next steps:" in event["message"]
  assert event["tags"]["commit"] == "abc123-session"


def test_legacy_summary_does_not_guess_commit(tmp_path):
  event = br.drive_event([br.session_record(write_session(tmp_path, 1, FAILED))])
  assert "Connection: Wireless" in event["message"]
  assert "Session commit: unknown" in event["message"]
  assert event["tags"]["commit"] == "unknown"


def test_batch_with_different_builds_and_connections_is_explicit(tmp_path):
  sessions = []
  for number, receiver, commit in ((1, "usb", "old-build"), (2, "Car", "new-build")):
    events = [(0, 0, "session_start", {"receiver": receiver, "software": {"commit": commit}})] + FAILED[1:]
    sessions.append(br.session_record(write_session(tmp_path, number, events)))
  event = br.drive_event(sessions)
  assert event["tags"]["aa.transport"] == "mixed" and event["tags"]["commit"] == "mixed"
  assert "Session commit: mixed" in event["message"]
  assert "Wired USB" in event["message"] and "Wireless" in event["message"]
  assert "old-build" in event["message"] and "new-build" in event["message"]


def test_pairing_records_software_when_window_opens(monkeypatch):
  from openpilot.starpilot.system.starport import bugsink_transport
  monkeypatch.setattr(bugsink_transport, "device_metadata", lambda: {"commit": "pairing-build", "branch": "pairing-branch"})
  records = []
  attempt = br.PairingAttempt(180, sink=records.append)
  attempt.finish("paired")
  event = br.pairing_event(records[0])
  assert "Session commit: pairing-build" in event["message"]
  assert "Connection: Wireless" in event["message"]
  assert event["tags"]["commit"] == "pairing-build"


def test_supervisor_records_build_at_session_start(monkeypatch):
  import threading
  from openpilot.starpilot.system.starport import supervisor, bugsink_transport
  monkeypatch.setattr(bugsink_transport, "device_metadata", lambda: {"commit": "session-build", "branch": "session-branch"})
  records = []

  class Log:
    def open(self):
      pass

    def close(self):
      pass

    def capture_system(self):
      pass

    def __call__(self, name, **values):
      records.append({"event": name, **values})

  sup = object.__new__(supervisor.Supervisor)
  sup.log = Log()  # ty: ignore[invalid-assignment]  # test double
  sup.config = {"connection": "wired", "receiver_name": ""}
  sup._stop = threading.Event()
  sup._stop.set()
  sup._set = lambda **_: None  # ty: ignore[invalid-assignment]  # test double
  sup._count_dedicated = lambda _: None  # ty: ignore[invalid-assignment]  # test double
  sup._release_phone = lambda: None  # ty: ignore[invalid-assignment]  # test double
  sup._run(1)
  assert records[0]["software"] == {"commit": "session-build", "branch": "session-branch"}


HANDSHAKE_FAILED = [
  (0, 0, "session_start", {"receiver": "Honda HFL", "trigger": "auto"}),
  (0, 1, "stage", {"state": "rfcomm"}),
  (0, 2, "bootstrap_tx", {"message": "WifiVersionRequest", "bytes": 4}),
  (0, 3, "hfp_command", {"command": "AT+CIND"}),
  (0, 3, "nav_guidance_sent", {}),
  (0, 4, "bootstrap_rx", {"message": "WifiVersionResponse", "bytes": 6}),
  (0, 5, "sdp_retry", {"error": "[Errno 112] Host is down to AA:BB:CC:DD:EE:FF", "settle": 1.5}),
  (0, 25, "attempt_failed", {"stage": "wifi_start", "error": "head unit did not answer in time", "kind": "BootstrapTimeout"}),
]


def test_failed_sessions_carry_a_scrubbed_handshake_trace(tmp_path):
  event = br.drive_event([br.session_record(write_session(tmp_path, 1, HANDSHAKE_FAILED))])
  trace = event["contexts"]["starport_trace_session_1"]
  names = [item["event"] for item in trace["events"]]
  assert names[:3] == ["session_start", "stage", "bootstrap_tx"] and "bootstrap_rx" in names and "sdp_retry" in names
  assert "hfp_command" not in names and "nav_guidance_sent" not in names
  assert trace["omitted"] == 0 and trace["events"][2]["at_s"] == 2.0
  text = json.dumps(event)
  assert "AA:BB:CC:DD:EE:FF" not in text and "Honda HFL" not in text


def test_trace_keeps_the_newest_events_within_its_budget(tmp_path):
  noisy = [(0, 0, "session_start", {})] + [(i // 60, i % 60, "sdp_retry", {"error": "x" * 250, "n": i}) for i in range(1, 400)]
  noisy.append((9, 0, "attempt_failed", {"stage": "discovering", "error": "gone", "kind": "RuntimeError"}))
  event = br.drive_event([br.session_record(write_session(tmp_path, 1, noisy))])
  trace = event["contexts"]["starport_trace_session_1"]
  assert trace["omitted"] > 0 and [e["event"] for e in trace["events"]][-2:] == ["attempt_failed", "session_stop"]
  assert len(json.dumps(trace)) <= br.TRACE_SESSION_BYTES + 1024
  assert len(json.dumps(event)) < 400 << 10


CLEAN = [(0, 0, "session_start", {"receiver": "Honda HFL", "trigger": "auto"}),
         (0, 11, "projection_ready", {"mode": {"width": 1280, "height": 720, "fps": 30, "margin_width": 0, "margin_height": 0}}),
         (0, 11, "frame_pipeline", {"nv12": True, "target_fps": 30, "encoder": "v4l2"}),
         (0, 12, "stage", {"state": "streaming"}),
         (0, 12, "video_acknowledged", {}),
         (0, 42, "stats", {"fps": 30.0, "wifi_dbm": -50, "focused": True, "frames_sent": 900, "load1": 1.5, "temp_max_c": 61.0}),
         (1, 12, "stats", {"fps": 29.0, "wifi_dbm": -52, "focused": True, "frames_sent": 1800, "load1": 1.7, "temp_max_c": 63.0})]


def test_clean_drives_send_no_trace(tmp_path):
  event = br.drive_event([br.session_record(write_session(tmp_path, 1, CLEAN))])
  assert not any(key.startswith("starport_trace_session_") for key in event["contexts"])
  assert "aa.attention" not in event["tags"]


def test_session_carries_stats_series_and_routine_event_counts(tmp_path):
  events = [*CLEAN, (1, 20, "nav_guidance_sent", {}), (1, 21, "nav_guidance_sent", {}), (1, 22, "media_key", {})]
  record = br.session_record(write_session(tmp_path, 1, events))
  assert record["series"]["at_s"] == [42.0, 72.0] and record["series"]["load1"] == [1.5, 1.7]
  assert record["series"]["temp_max_c"] == [61.0, 63.0] and "cpu0_mhz" not in record["series"]
  assert record["quiet_events"] == {"nav_guidance_sent": 2, "media_key": 1}


def test_sessions_that_streamed_poorly_get_a_trace(tmp_path):
  event = br.drive_event([br.session_record(write_session(tmp_path, 1, STREAMED))])
  assert "starport_trace_session_1" in event["contexts"]
  assert {"encoder_recoveries", "unfocused"} <= set(event["tags"]["aa.attention"].split(","))
  assert event["contexts"]["starport_trace_session_1"]["routine_events"] == {}


def test_sessions_that_never_streamed_get_a_trace(tmp_path):
  stopped = [(0, 0, "session_start", {"receiver": "Honda HFL"}), (0, 5, "stage", {"state": "negotiating_video"})]
  event = br.drive_event([br.session_record(write_session(tmp_path, 1, stopped))])
  assert "starport_trace_session_1" in event["contexts"] and event["tags"]["aa.attention"] == "stopped"


def sidecar(tmp_path, number, lines):
  path = tmp_path / f"session-{number:06d}.system.json"
  path.write_text(json.dumps({"window": {"start": "a", "end": "b"}, "sources": {"network": {"lines": lines}, "kernel": {"error": "timed out"}}}))


def test_device_slice_is_attached_scrubbed_for_sessions_that_need_attention(tmp_path):
  path = write_session(tmp_path, 1, HANDSHAKE_FAILED)
  sidecar(tmp_path, 1, ["wpa: Trying to associate with SSID 'HomeNet' bssid aa:bb:cc:dd:ee:ff", "wpa: ssid=Secret1 Honda HFL gone"])
  (tmp_path / "car_ui.log").write_text("ui line Honda HFL\n" * 3)
  event = br.drive_event([br.session_record(path)])
  system = event["contexts"]["starport_system_session_1"]
  assert system["kernel"] == {"error": "timed out"} and len(system["network"]["lines"]) == 2
  text = json.dumps(event)
  assert "HomeNet" not in text and "Secret1" not in text and "Honda HFL" not in text and "AA:BB:CC" not in text.upper()
  assert len(event["contexts"]["starport_car_ui"]["lines"]) == 3


def test_clean_sessions_do_not_attach_a_device_slice(tmp_path):
  path = write_session(tmp_path, 1, CLEAN)
  sidecar(tmp_path, 1, ["line"])
  assert not any(key.startswith("starport_system") for key in br.drive_event([br.session_record(path)])["contexts"])


def test_oversized_reports_shed_detail_and_still_fit(tmp_path):
  path = write_session(tmp_path, 1, HANDSHAKE_FAILED)
  big = br.drive_event([br.session_record(path)])
  big["contexts"]["starport_system_session_1"] = {"network": {"lines": ["y" * 290] * 3000}}
  fitted = br.fit_event(big)
  assert br.event_size(fitted) <= br.EVENT_BUDGET
  assert fitted["tags"]["aa.truncated"] == "yes" and "starport_system_session_1" in fitted["contexts"]["starport_shed"]["dropped"]
  assert "starport_drive" in fitted["contexts"]


def test_pairing_that_failed_carries_a_device_slice_but_success_does_not(tmp_path):
  record = {"seq": 1, "started": "2026-10-16T12:00:00+00:00", "window_s": 60, "duration_s": 30, "steps": [], "result": "timeout"}
  calls = []
  collector = lambda start, end: calls.append((start, end)) or {"window": {}, "sources": {"bluetooth": {"lines": ["bt 'Phone X' up"]}}}  # noqa: E731
  event = br.pairing_event(record, collector)
  assert event["contexts"]["starport_pairing_system"]["bluetooth"]["lines"] == ["bt '<name>' up"]
  assert calls[0][1] - calls[0][0] == 30
  assert "starport_pairing_system" not in br.pairing_event({**record, "result": "paired"}, collector)["contexts"]


OFFER = {"channels": [{"id": 3, "video_configs": [
  {"1": [2], "10": [3], "3": [0], "4": [240]},
  {"1": [8], "10": [7], "3": [288], "4": [1378]},
  {"1": [7], "10": [7], "3": [0], "4": [811]}]}], "head_unit": {"2": ["Honda"]}}


def adapter_session():
  events = [list(item) for item in STREAMED]
  events[0] = (0, 0, "session_start", {"receiver": "AAWireless", "trigger": "auto",
                                       "setup": {"type": "adapter", "profile": "aawireless", "profile_source": "override"}})
  events[5] = (0, 10, "discovered", {**OFFER, "head_unit": STREAMED[5][3]["head_unit"]})
  events.insert(6, (0, 10, "bootstrap_start_response", {}))
  events.insert(7, (0, 10, "bootstrap_version_reply", {"style": "modern", "channel_type": 1}))
  events.insert(8, (0, 10, "bluetooth_opened", {"channel": 6, "phone_address": "39:90:21:64:07:00"}))
  events.insert(9, (0, 11, "video_setup", {"mode": {"config_index": 0}, "window": 30, "used_window": 30}))
  return events


def test_report_keeps_setup_offered_modes_and_the_working_strategy(tmp_path):
  record = br.session_record(write_session(tmp_path, 1, adapter_session()))
  assert record["setup"] == {"type": "adapter", "profile": "aawireless", "profile_source": "override"}
  assert record["offered_modes"] == ["1280x720 H.264 margins 0x240", "1440x2560 H.265 margins 288x1378", "1080x1920 H.265 margins 0x811"]
  assert record["phone_address_sent"] is True
  assert record["strategy"] == {"profile": "aawireless", "window_offered": 30, "window_used": 30,
                                "reply_style": "modern", "channel_type": 1, "config_index": 0}
  event = br.drive_event([record])
  tags = event["tags"]
  assert (tags["aa.setup_type"], tags["aa.adapter_model"], tags["aa.profile_source"], tags["aa.phone_address_sent"]) == (
    "adapter", "aawireless", "override", "yes")
  context = event["contexts"]["starport_session_1"]
  assert context["offered_modes"] == record["offered_modes"] and context["strategy"]["reply_style"] == "modern"
  assert "39:90:21:64:07:00" not in json.dumps(event)  # the comma's own address is sanitized from every field


def test_report_records_that_the_address_was_not_sent_and_old_logs_still_report(tmp_path):
  events = [list(item) for item in STREAMED]
  events.insert(6, (0, 10, "bluetooth_opened", {"channel": 6, "phone_address": ""}))
  assert br.drive_event([br.session_record(write_session(tmp_path, 1, events))])["tags"]["aa.phone_address_sent"] == "no"
  old = br.session_record(write_session(tmp_path, 2, STREAMED))  # a log written before these fields existed
  assert old["setup"] == {} and old["offered_modes"] == [] and old["phone_address_sent"] is None and old["strategy"] == {}
  tags = br.drive_event([old])["tags"]
  assert not any(key in tags for key in ("aa.setup_type", "aa.adapter_model", "aa.profile_source", "aa.phone_address_sent"))


def test_unknown_setup_values_are_dropped(tmp_path):
  events = [list(item) for item in STREAMED]
  events[0] = (0, 0, "session_start", {"setup": {"type": "<script>", "profile": "x" * 90, "profile_source": "guess"}})
  setup = br.session_record(write_session(tmp_path, 1, events))["setup"]
  assert "type" not in setup and "profile_source" not in setup and len(setup["profile"]) <= 40


def test_report_shows_what_the_ladder_tried_and_what_worked(tmp_path):
  events = [list(item) for item in STREAMED]
  events.insert(1, (0, 1, "ladder_strategy", {"rung": "standard", "learned": False}))
  events.insert(2, (0, 5, "ladder_failed", {"outcome": "advanced", "rung": "standard", "next": "other_reply"}))
  events.insert(3, (0, 6, "ladder_strategy", {"rung": "other_reply", "learned": False}))
  events.append((1, 50, "ladder_worked", {"rung": "other_reply", "streamed_s": 62.0, "t_ready_s": 14.5}))
  record = br.session_record(write_session(tmp_path, 1, events))
  assert record["ladder"] == [{"rung": "standard", "learned": False, "at_s": 1.0, "outcome": "advanced"},
                              {"rung": "other_reply", "learned": False, "at_s": 6.0, "outcome": "worked", "streamed_s": 62.0, "t_ready_s": 14.5}]
  tags = br.drive_event([record])["tags"]
  assert (tags["aa.ladder_rung"], tags["aa.ladder_learned"]) == ("other_reply", "no")
  assert br.session_record(write_session(tmp_path, 2, STREAMED))["ladder"] == []   # older logs report nothing, without error


def test_report_says_how_the_rfcomm_channel_was_found(tmp_path):
  events = [list(item) for item in STREAMED]
  events.insert(1, (0, 1, "sdp_retry", {"error": "x"}))
  events.insert(2, (0, 2, "rfcomm_channel", {"channel": 8, "source": "sdp"}))
  assert br.session_record(write_session(tmp_path, 1, events))["sdp"] == {"channel": 8, "source": "sdp", "retries": 1}
  assert br.session_record(write_session(tmp_path, 2, STREAMED))["sdp"] == {}
