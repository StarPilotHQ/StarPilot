"""On-device Android Auto diagnostics: richer session logs, readable reports and the renderer profile."""

from collections import deque
import io
import json
import socket
import ssl
import sys
import time
import zipfile
from types import SimpleNamespace

import pytest

from openpilot.starpilot.system.starport import bootstrap as bs
from openpilot.starpilot.system.starport import compat_report
from openpilot.starpilot.system.starport.render_profile import RenderSampler, RenderSummary
from openpilot.starpilot.system.starport.session import ProjectionSession, choose_video_mode, redact_head_unit
from openpilot.starpilot.system.starport.tests.fake_head_unit import FakeHeadUnit, make_identity
from openpilot.starpilot.system.starport.wire import DESCRIBE_TEXT, describe, field, parse_fields


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("identity"))


def connect(hu: FakeHeadUnit, identity, log, ca=None) -> ProjectionSession:
  sock = socket.create_connection(("127.0.0.1", hu.port), timeout=5)
  return ProjectionSession(sock, str(identity["phone_cert"]), str(identity["phone_key"]), log,
                           str(ca or identity["root"]))


# ------------------------------------------------------------- session logs

def test_describe_keeps_text_and_bounds_opaque_bytes():
  described = describe(field(1, "Honda") + field(2, 7) + field(3, field(1, "ALPSALPINE")) + field(4, b"\xff" * 100))
  assert described[1] == ["Honda"] and described[2] == [7] and described[3] == [{1: ["ALPSALPINE"]}]
  assert described[4] == [{"hex": "ff" * 64, "bytes": 100}]
  assert describe(field(1, "x" * (DESCRIBE_TEXT + 1)))[1] == ["x" * DESCRIBE_TEXT + "…"]


def test_ignored_messages_are_rate_limited():
  counts = []
  session = ProjectionSession.__new__(ProjectionSession)
  session._log = lambda name, **values: counts.append(values["count"])
  session.ignored_counts = {}
  for _ in range(250):
    session.ignored("channel_ignored", 9, 0x8001, b"\x08\x01")
  assert counts == [1, 2, 3, 4, 5, 100, 200]


def test_no_supported_mode_error_lists_the_offer():
  channels = [{"id": 3, "video_configs": [parse_fields(field(1, 4) + field(10, 3)), parse_fields(field(1, 2) + field(10, 7))]},
              {"id": 5, "display_type": 1, "video_configs": [parse_fields(field(1, 1) + field(10, 5))]}]
  with pytest.raises(ValueError, match=r"2560x1440 H\.264, 1280x720 H\.265, 800x480 VP9 \(display 1\)"):
    choose_video_mode(channels)


def test_discovery_log_describes_the_car_and_its_channels(identity):
  records = []
  hu = FakeHeadUnit(identity)
  session = connect(hu, identity, lambda name, **values: records.append((name, values)))
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  discovered = next(values for name, values in records if name == "discovered")
  assert discovered["head_unit"][2] == ["Honda"] and discovered["head_unit"][3] == ["Civic"]
  video = next(channel for channel in discovered["channels"] if channel.get("video_configs"))
  assert video["descriptor"] and "display_type" in video
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)


def test_vehicle_ids_are_redacted_from_discovery_and_version_logs():
  described = redact_head_unit({2: ["Honda"], 5: ["VIN-SECRET"], 17: [{1: ["Honda"], 4: ["VIN-SECRET"]}]})
  assert described[2] == ["Honda"] and "VIN-SECRET" not in json.dumps(described)
  version = bs.describe_version_request(field(1, 4) + field(2, 1) + field(5, field(1, "Honda") + field(4, "VIN-SECRET")) +
                                        field(4, field(1, "192.168.1.1") + field(2, 5288) + field(4, 36)))
  assert version[5][0] == {1: ["Honda"], 4: ["redacted"]}
  assert version[4][0][4] == [36], "a channel number in the endpoint is kept"


def test_tls_failure_is_logged_with_its_reason(identity, tmp_path):
  records = []
  hu = FakeHeadUnit(identity)
  other_root = make_identity(tmp_path)["root"]  # the car's certificate does not chain to this root
  session = connect(hu, identity, lambda name, **values: records.append((name, values)), ca=other_root)
  with pytest.raises(ssl.SSLError):
    session.authenticate()
  failed = next(values for name, values in records if name == "tls_failed")
  assert failed["reason"] and failed["error"]
  session.peer.close()
  hu.close()


def ack_session():
  session = ProjectionSession.__new__(ProjectionSession)
  session.retired_sessions = deque()
  session.session_id = 1
  session.config_ack_slack = 1
  session.unacked = 1
  session.pending = deque([time.monotonic()])
  session.max_ack_seconds = 0
  session.acked = session.epoch_acked = 0
  session.video_confirmed = False
  events = []
  session._log = lambda name, **values: events.append(name)
  return session, events


def test_configuration_ack_alone_does_not_confirm_video():
  session, events = ack_session()
  session._handle_ack(1, 1)
  assert not events
  assert session.unacked == 0  # preserve the existing flow-control behavior
  session._handle_ack(1, 1)  # the additional ACK proves at least one was a frame
  assert events == ["video_acknowledged"]


@pytest.mark.parametrize("separate_config", [False, True])
def test_video_confirmation_with_optional_or_separate_config_ack(separate_config):
  session, events = ack_session()
  if separate_config:
    session._handle_ack(0, 1)
    assert not events
  session._handle_ack(1, 1)
  if not separate_config:
    assert not events  # one ACK is still ambiguous
  session.unacked = 1
  session.pending.append(time.monotonic())
  session._handle_ack(1, 1)
  assert events == ["video_acknowledged"]


def test_combined_config_and_frame_ack_confirms_video():
  session, events = ack_session()
  session._handle_ack(1, 2)
  assert events == ["video_acknowledged"]


# ------------------------------------------------------------------ reports

# Shaped like the Civic's session 25 (2026-09-28): a refused start, then a projection that later dropped.
CIVIC_HEAD_UNIT = {"car_make": "Honda", "car_model": "T20", "car_year": "2025", "head_unit_make": "ALPSALPINE",
                   "head_unit_model": "8A501-T20-A1"}
CIVIC_SESSION = [
  {"t": "2026-09-28T21:13:51+00:00", "event": "session_start", "receiver": "Honda CIVIC", "trigger": "onroad"},
  {"event": "stage", "state": "connecting_bluetooth"},
  {"event": "stage", "state": "rfcomm"},
  {"event": "bootstrap_version", "major": 4, "minor": 1, "head_unit": CIVIC_HEAD_UNIT},
  {"event": "bootstrap_start_refused", "status": -1, "endpoint": False},
  {"event": "bootstrap_credentials", "ssid": "Car-4E91", "security": "wpa2/wpa3", "ap_type": 0, "key_length": 12},
  {"event": "stage", "state": "joining_wifi"},
  {"event": "wifi_joined", "interface": "wlan0", "local_ip": "192.168.180.183"},
  {"event": "stage", "state": "authenticating"},
  {"event": "version", "major": 4, "minor": 1, "reply": "6.1"},
  {"event": "tls_established", "version": "TLSv1.2", "cipher": "ECDHE-RSA-AES256-GCM-SHA384"},
  {"event": "discovered", "head_unit": {"2": ["Honda"], "3": ["Civic"], "5": ["redacted"]},
   "channels": [{"id": 22, "services": [3], "display_type": 0, "video_configs": [{"1": [2], "10": [3]}, {"1": [1], "10": [3]}]}]},
  {"event": "projection_ready", "mode": {"width": 1280, "height": 720, "fps": 60, "margin_width": 0, "margin_height": 0}},
  {"event": "stage", "state": "streaming"},
  {"event": "video_focus", "focused": True},
  {"event": "video_acknowledged", "session": 1, "ack_ms": 29},
  {"event": "channel_ignored", "channel": 9, "kind": 32769, "count": 1, "message": {"1": [1]}},
  {"event": "attempt_failed", "stage": "streaming", "error": "projecting: Video acknowledgement older than 1.5 s"},
]


def test_report_summarizes_a_civic_session():
  report = compat_report.summarize(CIVIC_SESSION)
  assert report["transport"] == "wireless" and report["trigger"] == "onroad"
  assert report["car"]["head_unit_make"] == "ALPSALPINE" and report["car"]["car_model"] == "Civic"
  assert report["furthest_stage"] == "streaming" and report["wifi"]["security"] == "wpa2/wpa3"
  assert report["video"]["offered"] == ["1280x720 H.264", "800x480 H.264"]
  assert report["outcome"] == "projected, then failed: projecting: Video acknowledgement older than 1.5 s"
  text = compat_report.render_text(report, "session-000025.jsonl")
  assert "Outcome: projected, then failed" in text and "cipher: ECDHE-RSA-AES256-GCM-SHA384" in text
  assert "Not handled: channel_ignored channel 9" in text
  assert "Car-4E91" not in text and "192.168.180.183" not in text  # no Wi-Fi name or address in a shared report


def test_successful_retry_clears_outcome_failure_but_keeps_history():
  failure = {"event": "attempt_failed", "stage": "streaming", "error": "link lost"}
  events = [{"event": "video_acknowledged"}, failure]
  assert compat_report.summarize(events)["outcome"] == "projected, then failed: link lost"
  events.append({"event": "video_acknowledged"})
  report = compat_report.summarize(events)
  assert report["outcome"] == "projected"
  assert report["errors"] == [{"stage": "streaming", "error": "link lost"}]


def write_log(directory, name, events):
  path = directory / name
  path.write_text("".join(json.dumps(event) + "\n" for event in events) + '{"cut short')
  return path


def test_retry_after_car_shutdown_is_not_the_outcome():
  events = [{"event": "video_acknowledged"}, {"event": "session_ended", "reason": "Head unit ended projection: shutdown"},
            {"event": "attempt_failed", "stage": "rfcomm", "error": "Host is down"}]
  report = compat_report.summarize(events)
  assert report["outcome"] == "projected"
  assert report["errors"] == [{"stage": "rfcomm", "error": "Host is down", "after_car_shutdown": True}]
  assert "Error (rfcomm, after car shutdown): Host is down" in compat_report.render_text(report)


def test_session_logs_are_newest_first_by_number(tmp_path):
  write_log(tmp_path, "session-000009-20260927-121412.jsonl", [])
  write_log(tmp_path, "session-000010-20260728-080505.jsonl", [])  # the clock had not synced yet
  write_log(tmp_path, "session-20260101-000000.jsonl", [])
  (tmp_path / "notes.txt").write_text("")
  assert [path.name[:14] for path in compat_report.session_logs(tmp_path)] == \
    ["session-000010", "session-000009", "session-202601"]
  assert compat_report.session_path("../config.json", tmp_path) is None


def test_bundle_has_logs_reports_and_settings_without_addresses(tmp_path):
  logs = tmp_path / "logs"
  logs.mkdir()
  write_log(logs, "session-000025-20260928-141351.jsonl", CIVIC_SESSION)
  (logs / "render_profile.txt").write_text("==== profile ====\n")
  config = tmp_path / "config.json"
  config.write_text(json.dumps({"config_version": 2, "receiver_address": "AA:BB:CC:DD:EE:FF", "receiver_name": "Honda CIVIC",
                                "rfcomm_cache": {"AA:BB:CC:DD:EE:FF": 5}}))
  with zipfile.ZipFile(io.BytesIO(compat_report.bundle(logs, config))) as archive:
    names = set(archive.namelist())
    assert {"REPORT.txt", "config.json", "logs/session-000025-20260928-141351.jsonl",
            "reports/session-000025-20260928-141351.json", "logs/render_profile.txt"} <= names
    shared = archive.read("config.json").decode()
    assert "AA:BB:CC:DD:EE:FF" not in shared and json.loads(shared)["rfcomm_cache"] == [5]
    assert "projected, then failed" in archive.read("REPORT.txt").decode()
    assert not any(name.endswith((".pem", ".key")) for name in names)
    # The bundle (and so the Discord send) carries what a Bugsink report carries: tags, and per-session detail.
    report = json.loads(archive.read("report.json"))
    assert report["tags"]["aa.report"] == "drive" and "aa.head_unit" in report["tags"]
    assert report["sessions"][0]["offered_modes"] is not None and "setup" in report["sessions"][0]
    assert "AA:BB:CC:DD:EE:FF" not in archive.read("report.json").decode()


def test_bundle_still_builds_when_the_report_cannot(tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import bugsink_report
  logs = tmp_path / "logs"
  logs.mkdir()
  write_log(logs, "session-000025-20260928-141351.jsonl", CIVIC_SESSION)
  monkeypatch.setattr(bugsink_report, "session_record", lambda path: (_ for _ in ()).throw(RuntimeError("broken")))
  config = tmp_path / "config.json"
  config.write_text("{}")
  with zipfile.ZipFile(io.BytesIO(compat_report.bundle(logs, config))) as archive:
    assert "REPORT.txt" in archive.namelist() and "report.json" not in archive.namelist()


def test_report_tool_prints_a_log(tmp_path, monkeypatch, capsys):
  from openpilot.tools.starport import compat_report as tool
  path = write_log(tmp_path, "session-000025-20260928-141351.jsonl", CIVIC_SESSION)
  monkeypatch.setattr(sys, "argv", ["compat_report.py", str(path)])
  assert tool.main() == 0
  assert "Outcome: projected, then failed" in capsys.readouterr().out
  monkeypatch.setattr(sys, "argv", ["compat_report.py", str(path), "--json"])
  assert tool.main() == 0
  assert json.loads(capsys.readouterr().out)["car"]["head_unit_make"] == "ALPSALPINE"


# ----------------------------------------------------------- render profile

def hot_render_function(sampler):
  sampler.sample(sys._getframe())


def test_render_sampler_counts_render_stacks_and_idle(tmp_path):
  clock = [0.0]
  sampler = RenderSampler(tmp_path / "render_profile.txt", clock=lambda: clock[0])
  sampler.sample(sys._getframe())  # renderer idle
  sampler.rendering, sampler.onroad = True, True
  for _ in range(3):
    hot_render_function(sampler)
  sampler.summary = {"event": "render_stats", "fps": 29.5, "frame_ms": 21.0}
  clock[0] = 60.0
  text = sampler.report()
  assert "onroad (100% onroad), rendering 75% of 4 samples" in text
  assert "render_stats fps=29.5 frame_ms=21.0" in text
  innermost = text.split("-- innermost function")[1].split("-- innermost line")[0]
  assert "100.0%  test_diagnostics_logging.py" in innermost and "hot_render_function" in innermost
  sampler.flush()
  assert (tmp_path / "render_profile.txt").read_text() == text
  assert sampler.samples == 0 and sampler.report() == ""


def test_sampler_reports_parameter_keys_without_values(tmp_path):
  sampler = RenderSampler(tmp_path / "profile.txt")
  sampler.rendering = True

  class Code:
    co_name = "get"
    co_filename = "/data/openpilot/openpilot/common/params.py"
    co_firstlineno = 220

  sampler.sample(SimpleNamespace(f_code=Code(), f_lineno=222, f_locals={"key": "BorderWidth", "value": "private-value"}, f_back=None))
  report = sampler.report()
  assert "100.0%  get(BorderWidth)" in report and "private-value" not in report


def test_render_sampler_file_rotates_at_its_size_cap(tmp_path):
  path = tmp_path / "render_profile.txt"
  sampler = RenderSampler(path, max_bytes=300)
  for index in range(5):
    sampler._write(f"window {index} " + "x" * 100 + "\n")
  assert path.read_text().startswith("window 4")
  assert (tmp_path / "render_profile.1.txt").read_text().startswith("window 2")
  assert path.stat().st_size <= 300 and not (tmp_path / "render_profile.2.txt").exists()


def test_render_sampler_thread_samples_the_render_thread(tmp_path):
  sampler = RenderSampler(tmp_path / "render_profile.txt", interval=0.001, window=3600)
  sampler.rendering = True
  sampler.start()
  deadline = time.monotonic() + 2.0
  while sampler.samples < 5 and time.monotonic() < deadline:
    sum(range(10_000))
  sampler.close()
  assert "test_render_sampler_thread_samples_the_render_thread" in (tmp_path / "render_profile.txt").read_text()


def test_render_summary_reports_frame_rate_and_time_per_window():
  summary = RenderSummary(0.0, interval=1.0)
  for began in (0.0, 0.25, 0.5, 0.75):
    assert summary.frame_done(began, began + 0.05) is None
  assert summary.frame_done(0.95, 1.0) == {"event": "render_stats", "fps": 5.0, "frame_ms": 50.0}
  assert summary.frames == 0 and summary.started == 1.0  # the next window starts where this one ended


# ------------------------------------------------------------- session log writer

def _read_records(directory):
  (path,) = directory.glob("session-*.jsonl")
  return [json.loads(line) for line in path.read_text().splitlines()]


def test_session_log_writes_in_order_off_the_caller_thread(tmp_path):
  from openpilot.starpilot.system.starport.supervisor import EventLog
  log = EventLog(tmp_path)
  log.open()
  for index in range(50):
    log("stats", index=index)
  log.close()
  records = _read_records(tmp_path)
  assert [record["index"] for record in records] == list(range(50))
  assert len(log.recent) == 40 and log.recent[-1]["index"] == 49


def test_session_log_never_blocks_on_a_stalled_disk(tmp_path, monkeypatch):
  import threading
  import time
  from openpilot.starpilot.system.starport import supervisor
  monkeypatch.setattr(supervisor, "LOG_QUEUE_MAX", 5)
  release = threading.Event()
  original = supervisor.EventLog._write

  def stalled(handle, records, stopped):
    release.wait(5)  # the disk is stuck until the test lets it go
    original(handle, records, stopped)

  monkeypatch.setattr(supervisor.EventLog, "_write", staticmethod(stalled))
  log = supervisor.EventLog(tmp_path)
  log.open()
  started = time.monotonic()
  for index in range(20):
    log("stats", index=index)
  assert time.monotonic() - started < 0.5  # the caller never waited for the disk
  release.set()
  deadline = time.monotonic() + 2
  while not log._queue.empty() and time.monotonic() < deadline:
    time.sleep(0.01)  # the disk catches up
  log("after", index=20)
  log.close()
  records = _read_records(tmp_path)
  dropped = [record for record in records if record["event"] == "log_dropped"]
  assert dropped and dropped[0]["count"] == 15
  assert [record["index"] for record in records if record["event"] != "log_dropped"] == [0, 1, 2, 3, 4, 20]


def test_session_log_without_a_file_keeps_the_recent_tail(tmp_path):
  from openpilot.starpilot.system.starport.supervisor import EventLog
  (tmp_path / "file").write_text("")
  log = EventLog(tmp_path / "file" / "logs")  # cannot be created: a file is in the way
  log.open()
  log("stage", stage="rfcomm")
  log.close()
  assert log.recent[-1]["event"] == "stage"


def test_log_reads_reject_replaced_symlinks_and_directories(tmp_path, monkeypatch):
  logs = tmp_path / "logs"
  logs.mkdir()
  outside = tmp_path / "private"
  outside.write_text("private material")
  session = logs / "session-000001-test.jsonl"
  session.symlink_to(outside)
  assert compat_report.session_logs(logs) == []
  with pytest.raises(OSError):
    compat_report.read_log(session, compat_report.MAX_LOG_BYTES)
  # A path substituted after listing must not bypass the opened-file checks.
  monkeypatch.setattr(compat_report, "session_logs", lambda *_: [session])
  with pytest.raises(OSError):
    compat_report.bundle(logs)
  session.unlink()
  session.mkdir()
  with pytest.raises(OSError):
    compat_report.read_log(session, compat_report.MAX_LOG_BYTES)


def test_log_and_bundle_reads_enforce_size_budgets(tmp_path, monkeypatch):
  session = tmp_path / "session-000001-test.jsonl"
  session.write_bytes(b"x" * 9)
  with pytest.raises(OSError):
    compat_report.read_log(session, 8)
  assert compat_report.read_log(session, 8, tail=True) == b"x" * 8
  data = (json.dumps({"event": "session_stop"}) + "\n").encode() * 400
  session.write_bytes(data)
  (tmp_path / "session-000002-test.jsonl").write_bytes(data)
  monkeypatch.setattr(compat_report, "MAX_BUNDLE_BYTES", len(data) + 4096)
  with pytest.raises(OSError):
    compat_report.bundle(tmp_path)


def test_session_log_full_queue_close_then_reopen_retires_old_writer(tmp_path, monkeypatch):
  import threading
  from openpilot.starpilot.system.starport import supervisor
  monkeypatch.setattr(supervisor, "LOG_QUEUE_MAX", 2)
  release = threading.Event()
  entered = threading.Event()
  original = supervisor.EventLog._write
  handles = []

  def stalled_once(handle, records, stopped):
    handles.append(handle)
    if len(handles) == 1:
      entered.set()
      release.wait(5)
    original(handle, records, stopped)

  monkeypatch.setattr(supervisor.EventLog, "_write", staticmethod(stalled_once))
  log = supervisor.EventLog(tmp_path)
  log.open()
  old_writer = log._writer
  assert entered.wait(1)
  try:
    log("first", index=0)
    log("first", index=1)
    assert log._queue.full()
    log.close()
    log.open()
    log("second", index=2)
    log.close()
  finally:
    release.set()
    old_writer.join(timeout=2)
    log.close()
  assert not old_writer.is_alive()
  assert all(handle.closed for handle in handles)
  files = sorted(tmp_path.glob("session-*.jsonl"))
  first = [json.loads(line) for line in files[0].read_text().splitlines()]
  second = [json.loads(line) for line in files[1].read_text().splitlines()]
  assert [item["event"] for item in first] == ["first", "first"]
  assert [item["event"] for item in second] == ["second"]
