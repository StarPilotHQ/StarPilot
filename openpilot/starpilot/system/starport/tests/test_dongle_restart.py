"""A receiver that hangs up before saying anything is reported for every car; only an unofficial dongle is asked to restart."""
import threading
import time

import pytest

from openpilot.starpilot.system.starport import compat_report, reachability as r, setup_ladder as sl
from openpilot.starpilot.system.starport import supervisor as module
from openpilot.starpilot.system.starport.tests.fake_head_unit import make_identity
from openpilot.starpilot.system.starport.tests.test_starport import make_supervisor

RECEIVER = "AA:BB:CC:DD:EE:01"


@pytest.mark.parametrize("stage,kind,message,frames,expected", [
  ("rfcomm", "ConnectionResetError", "starting wireless setup: [Errno 104] Connection reset by peer", 0, True),
  ("rfcomm", "ConnectionRefusedError", "starting wireless setup: [Errno 111] Connection refused", 0, True),
  ("wifi_info", "ConnectionResetError", "getting car wi-fi: [Errno 104] Connection reset by peer", 0, True),
  ("wifi_info", "ConnectionResetError", "getting car wi-fi: [Errno 104] Connection reset by peer", 2, False),  # it had talked
  ("rfcomm", "OSError", "starting wireless setup: [Errno 112] Host is down", 0, False),                       # off, not refusing
  ("discovering", "RuntimeError", "org.bluez.Error.Failed: br-connection-page-timeout", 0, False),
  ("connecting_tcp", "RuntimeError", "Car did not accept the projection connection: [Errno 111] Connection refused", 0, False),
  ("rfcomm", "OSError", "[Errno 16] Device or resource busy", 0, False),
])
def test_only_a_link_dropped_before_any_message_is_a_hang_up(stage, kind, message, frames, expected):
  assert r.hung_up_early(stage, kind, message, frames) is expected


def test_each_rung_says_what_it_does_how_often_it_ran_and_why_it_last_ended(tmp_path):
  ladder = sl.SetupLadder(tmp_path / "setup_state.json", boot_id=lambda: "boot", clock=lambda: 1.0)
  assert ladder.tried(RECEIVER, "standard") == 1 and ladder.tried(RECEIVER, "standard") == 2
  ladder.note(RECEIVER, "standard", "The adapter never sent its Wi-Fi name and password")
  step = ladder.snapshot(RECEIVER, adapter=True, unofficial=True)["steps"][0]
  assert step == {"id": "standard", "label": sl.LABELS["standard"], "state": "current", "description": sl.DESCRIPTIONS["standard"],
                  "tries": 2, "reason": "The adapter never sent its Wi-Fi name and password"}
  assert set(sl.DESCRIPTIONS) == set(sl.RUNGS)
  assert not ladder.path.exists()                                   # this drive's counts are never written
  ladder.begin_cycle(RECEIVER)
  assert ladder.snapshot(RECEIVER)["steps"][0]["tries"] == 0         # a new drive counts again


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("identity"))


@pytest.fixture
def sup(identity, tmp_path, monkeypatch):
  supervisor, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: None)
  monkeypatch.setattr(module, "DONGLE_RESTART_WAIT", 30.0)

  def hang_up(lease):
    supervisor._stage("rfcomm", "channel 1")
    raise ConnectionResetError(104, "Connection reset by peer")
  monkeypatch.setattr(supervisor, "_attempt", hang_up)
  return supervisor


def run(sup):
  thread = threading.Thread(target=sup._run, args=(1, "manual"), daemon=True)
  thread.start()
  return thread


def wait_for(condition, timeout=10.0):
  deadline = time.monotonic() + timeout
  while not condition():
    assert time.monotonic() < deadline, "timed out"
    time.sleep(0.02)


def stop(sup, thread):
  sup._stop.set()
  thread.join(5)
  assert not thread.is_alive()


def events(sup, name):
  return [record for record in sup.log.recent if record["event"] == name]


def test_an_unofficial_dongle_that_keeps_hanging_up_is_asked_to_restart_and_continue_carries_on(sup):
  sup.config.update(car_type="adapter", car_profile_override="unofficial")
  thread = run(sup)
  try:
    wait_for(lambda: sup.status()["state"] == "dongle_restart")
    status = sup.status()
    assert status["dongle_restart"]["hangups"] == module.HANGUP_PROMPT and status["unofficial_dongle"]
    assert events(sup, "attempt_failed")[-1]["hung_up"] is True
    assert sup.continue_ladder() is True and sup.continue_ladder() is False
    wait_for(lambda: events(sup, "dongle_restart_done"))
    assert events(sup, "dongle_restart_done")[0]["how"] == "continue"
    wait_for(lambda: sup.status()["state"] != "dongle_restart")
  finally:
    stop(sup, thread)


def test_the_dongle_coming_back_after_going_quiet_ends_the_wait(sup):
  sup.config.update(car_type="adapter", car_profile_override="unofficial")
  thread = run(sup)
  try:
    wait_for(lambda: sup.status()["state"] == "dongle_restart")
    sup._receiver_seen_at = time.monotonic() - 5
    sup._hfp_connected(RECEIVER)                                  # still cycling on its own: not a restart
    assert not sup._restart.is_set()
    sup._receiver_seen_at = time.monotonic() - module.RESTART_QUIET - 1
    sup._hfp_connected(RECEIVER)
    wait_for(lambda: events(sup, "dongle_restart_done"))
    assert events(sup, "dongle_restart_done")[0]["how"] == "restarted" and events(sup, "dongle_restart_detected")
  finally:
    stop(sup, thread)


@pytest.mark.parametrize("car_type,profile", [("builtin", ""), ("adapter", "aawireless"), ("adapter", "default")])
def test_other_receivers_keep_retrying_as_before_and_only_the_count_is_shown(sup, car_type, profile):
  sup.config.update(car_type=car_type, car_profile_override=profile)
  thread = run(sup)
  try:
    wait_for(lambda: sup.status()["hangups"] >= module.HANGUP_PROMPT + 2)
    assert not events(sup, "dongle_restart_requested") and not sup.status()["unofficial_dongle"]
    assert sup.continue_ladder() is False
  finally:
    stop(sup, thread)


def test_handing_over_wifi_details_clears_the_count_and_each_event_is_put_into_words(sup):
  sup._hangups = 4
  sup._bootstrap_log("bootstrap_rx", message="WifiVersionRequest", bytes=10)
  sup._bootstrap_log("bootstrap_version", major=4, minor=0)
  assert sup._attempt_frames == 1 and "said hello (protocol 4.0)" in sup.status()["handshake_step"]
  sup._bootstrap_log("bootstrap_credentials", ssid="Mavlou-1FF1", security="wpa2")
  assert sup._hangups == 0 and sup.status()["handshake_step"] == "Got the Wi-Fi details for Mavlou-1FF1"


def test_the_report_counts_hang_ups_rungs_and_restarts_even_from_older_logs():
  def failed(error, **extra):
    return {"event": "attempt_failed", "stage": "rfcomm", "kind": "ConnectionResetError", "error": error, **extra}
  log = [{"event": "session_start", "t": "2026-11-07T03:26:50", "receiver": "Mavlou"},
         *[record for _ in range(3) for record in ({"event": "attempt_start"}, {"event": "stage", "state": "rfcomm"},
                                                    {"event": "ladder_strategy", "rung": "modern_dual"},
                                                    failed("starting wireless setup: [Errno 104] Connection reset by peer"))],
         {"event": "attempt_start"}, {"event": "bootstrap_rx", "message": "WifiVersionRequest"},
         {"event": "ladder_strategy", "rung": "modern_dual"},
         {"event": "ladder_failed", "rung": "modern_dual", "why": "The adapter said hello but never sent its address"},
         failed("getting car wi-fi: [Errno 104] Connection reset by peer"),
         {"event": "dongle_restart_requested", "t": "2026-11-07T03:28:13", "hangups": 6},
         {"event": "dongle_restart_done", "how": "restarted"}]
  report = compat_report.summarize(log)
  assert report["hangups"] == 3                                    # the one after a message is not a hang-up
  assert report["ladder"] == {"modern_dual": {"tries": 4, "failed": 1, "why": "The adapter said hello but never sent its address"}}
  text = compat_report.render_text(report)
  assert "Stages: rfcomm" in text and "Hung up before any handshake message: 3 attempts" in text
  assert "Handshake modern_dual: 4 tries, 1 counted failures (last: The adapter said hello but never sent its address)" in text
  assert "after 6 hang-ups: restarted" in text


def test_the_stages_line_keeps_the_real_order_and_folds_identical_retries():
  stages = ["connecting_bluetooth", "discovering", "rfcomm", "streaming", *["connecting_bluetooth", "discovering", "rfcomm"] * 3,
            "connecting_bluetooth", "discovering"]
  assert compat_report.stage_line(stages).split(" | ") == ["connecting_bluetooth > discovering > rfcomm > streaming",
                                                           "connecting_bluetooth > discovering > rfcomm (x3)", "connecting_bluetooth > discovering"]
  assert compat_report.stage_line(["waiting_for_usb", "usb_accessory"]) == "waiting_for_usb > usb_accessory"


def test_a_stop_while_waiting_for_the_restart_is_logged(sup):
  sup.config.update(car_type="adapter", car_profile_override="unofficial")
  thread = run(sup)
  wait_for(lambda: sup.status()["state"] == "dongle_restart")
  stop(sup, thread)
  assert [record["how"] for record in events(sup, "dongle_restart_done")] == ["stopped"]


def test_a_companion_car_opening_hands_free_is_not_the_dongle_restarting(sup):
  sup.config.update(car_type="adapter", car_profile_override="unofficial", companion_address="AA:BB:CC:DD:EE:02")
  thread = run(sup)
  try:
    wait_for(lambda: sup.status()["state"] == "dongle_restart")
    sup._receiver_seen_at = time.monotonic() - module.RESTART_QUIET - 1
    sup._hfp_connected("AA:BB:CC:DD:EE:02")
    assert not sup._restart.is_set()
  finally:
    stop(sup, thread)


def test_only_galaxy_can_continue_the_ladder():
  from openpilot.starpilot.system.starport import daemon
  assert "continue_ladder" in daemon.AUTHENTICATED_COMMANDS


def test_every_status_label_starts_with_a_capital_and_spells_names_properly():
  labels = module.STATE_LABELS.values()
  assert all(label[0].isupper() for label in labels)
  assert not any(word in label for label in labels for word in ("wi-fi", "android auto", "usb", "ipad", "headunitpad"))
  assert module.STATE_LABELS["wifi_info"] == "Getting car Wi-Fi" and module.STATE_LABELS["waiting_for_ipad"] == "Open HeadunitPad on the iPad"
