"""Ignition starts let the car connect first, and a link the kernel lost is dropped instead of retried forever."""
import errno
import socket
import subprocess

import pytest

from openpilot.starpilot.system.starport import bluez_phone
from openpilot.starpilot.system.starport.tests.fake_head_unit import make_identity
from openpilot.starpilot.system.starport.tests.test_starport import make_supervisor

CAR = "AA:BB:CC:DD:EE:01"


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("identity"))


def _events(sup, name):
  return [record for record in sup.log.recent if record["event"] == name]


def _virtual_time(sup, monkeypatch, on_wait=None):
  from openpilot.starpilot.system.starport import supervisor
  now = [100.0]
  monkeypatch.setattr(supervisor.time, "monotonic", lambda: now[0])
  monkeypatch.setattr(sup, "_lease", lambda: supervisor.NoLease())
  sup.config["connection"] = "wireless"

  def wait(seconds):
    now[0] += seconds
    if on_wait is not None:
      on_wait(now[0])
    if sup._stop.is_set():
      raise supervisor.Cancelled()

  monkeypatch.setattr(sup, "_wait", wait)
  return now


def _stop_after_first_attempt(sup, monkeypatch, now):
  starts = []

  def attempt(_):
    starts.append(now[0])
    sup._stop.set()

  monkeypatch.setattr(sup, "_attempt", attempt)
  return starts


def test_ignition_start_waits_for_the_car_to_open_hands_free(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  now = _virtual_time(sup, monkeypatch, on_wait=lambda t: t >= 102.0 and not sup._hfp_link.is_set() and sup._hfp_connected(CAR))
  starts = _stop_after_first_attempt(sup, monkeypatch, now)
  sup._run(1, trigger="onroad")
  assert starts and 102.0 <= starts[0] < 102.5  # paged right after the car reached out, not before
  assert _events(sup, "car_first_wait")[0]["outcome"] == "hfp"


def test_ignition_start_pages_the_car_after_the_wait(identity, tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import supervisor
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  now = _virtual_time(sup, monkeypatch)
  starts = _stop_after_first_attempt(sup, monkeypatch, now)
  sup._run(1, trigger="onroad")
  assert starts[0] == pytest.approx(100.0 + supervisor.CAR_FIRST_WAIT, abs=0.2)
  assert _events(sup, "car_first_wait")[0]["outcome"] == "timeout"


@pytest.mark.parametrize("trigger", ["manual", "car_connected"])
def test_other_starts_page_the_car_at_once(identity, tmp_path, monkeypatch, trigger):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  now = _virtual_time(sup, monkeypatch)
  starts = _stop_after_first_attempt(sup, monkeypatch, now)
  sup._run(1, trigger=trigger)
  assert starts == [100.0] and not _events(sup, "car_first_wait")


def test_car_already_reaching_out_skips_the_wait(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  now = _virtual_time(sup, monkeypatch)
  sup._hfp_connected(CAR)
  starts = _stop_after_first_attempt(sup, monkeypatch, now)
  sup._run(1, trigger="onroad")
  assert starts == [100.0]


def _stuck(sup, monkeypatch, *, cached: bool):
  from openpilot.starpilot.system.starport import bt_sockets
  calls = []

  def ealready(*args, **kwargs):
    calls.append(args)
    if len(calls) == 2:
      sup._stop.set()
    raise OSError(errno.EALREADY, "Operation already in progress")

  if cached:
    sup.config["rfcomm_cache"] = {CAR: 6}
    monkeypatch.setattr(bt_sockets, "connect_rfcomm", ealready)
  else:
    monkeypatch.setattr(bt_sockets, "connect_l2cap", ealready)
  bluez = sup._phone()
  bluez.connected = False  # BlueZ says not connected while the controller still holds the link
  return bluez, calls


@pytest.mark.parametrize("cached", [False, True])
def test_stuck_link_is_dropped_and_the_car_gets_to_connect_first(identity, tmp_path, monkeypatch, cached):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  _virtual_time(sup, monkeypatch)
  bluez, calls = _stuck(sup, monkeypatch, cached=cached)
  sup._run(1)
  assert len(calls) == 2                      # one try per attempt: retrying SDP cannot clear it
  assert bluez.stray_disconnects == 1
  assert _events(sup, "car_first_wait")       # the retry waits for the car instead of crossing it again
  assert _events(sup, "attempt_failed")[0]["kind"] == "StrayLinkError"
  if cached:
    assert sup.config["rfcomm_cache"] == {CAR: 6}  # the car's channel was fine; keep it


def test_stuck_link_is_left_alone_while_bluez_reports_the_car_connected(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  _virtual_time(sup, monkeypatch)
  bluez, _ = _stuck(sup, monkeypatch, cached=False)
  bluez.connected = True
  sup._run(1)
  assert not getattr(bluez, "stray_disconnects", 0)
  assert _events(sup, "stray_link_skipped")


KERNEL_LOG = """\
Bluetooth: hci0 ACL packet for unknown connection handle 3804
Bluetooth: hci0 ACL packet for unknown connection handle 5
Bluetooth: hci0 ACL packet for unknown connection handle 7
Bluetooth: hci0 ACL packet for unknown connection handle 3804
"""


def test_stray_handle_ignores_the_qualcomm_debug_handle():
  assert bluez_phone.stray_acl_handle(KERNEL_LOG) == 7
  assert bluez_phone.stray_acl_handle("Bluetooth: hci0 ACL packet for unknown connection handle 3804\n") is None
  assert bluez_phone.stray_acl_handle("") is None
  assert bluez_phone.known_acl_handles("Connections:\n\t< ACL C4:B7:57:6E:AC:E2 handle 7 state 1 lm MASTER\n") == {7}


def _phone_with(monkeypatch, outputs):
  commands, events = [], []

  def run(command, **kwargs):
    commands.append(command)
    stdout, code = outputs.get(command[0] if command[0] != "sudo" else "sudo", ("", 0))
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr="")

  monkeypatch.setattr(bluez_phone.subprocess, "run", run)
  phone = bluez_phone.BluezPhone.__new__(bluez_phone.BluezPhone)
  phone.log = lambda name, **values: events.append(name)
  return phone, commands, events


def test_disconnect_stray_link_sends_hci_disconnect_for_that_handle(monkeypatch):
  phone, commands, events = _phone_with(monkeypatch, {"journalctl": (KERNEL_LOG, 0), "hcitool": ("Connections:\n", 0)})
  assert phone.disconnect_stray_link() == 7
  assert commands[-1] == ["sudo", "-n", "hcitool", "-i", "hci0", "cmd", "0x01", "0x0006", "0x07", "0x00", "0x13"]
  assert events == ["stray_link_disconnected"]


def test_disconnect_stray_link_never_touches_a_link_the_kernel_knows(monkeypatch):
  phone, commands, events = _phone_with(monkeypatch, {"journalctl": (KERNEL_LOG, 0),
                                                      "hcitool": ("\t< ACL C4:B7:57:6E:AC:E2 handle 7 state 1\n", 0)})
  assert phone.disconnect_stray_link() is None
  assert not any(command[0] == "sudo" for command in commands)
  assert events == ["stray_link_skipped"]


def test_disconnect_stray_link_without_a_handle_only_logs(monkeypatch):
  phone, commands, events = _phone_with(monkeypatch, {"journalctl": ("", 0)})
  assert phone.disconnect_stray_link() is None
  assert len(commands) == 1 and events == ["stray_link_not_found"]


def _rfcomm_attempts(sup, monkeypatch, outcomes):
  from openpilot.starpilot.system.starport import bt_sockets
  calls = []

  def connect(*args, **kwargs):
    calls.append(args)
    outcome = outcomes[min(len(calls), len(outcomes)) - 1]
    if isinstance(outcome, int):
      raise OSError(outcome, "boom")
    return outcome

  monkeypatch.setattr(bt_sockets, "connect_rfcomm", connect)
  return calls


def test_busy_rfcomm_channel_is_retried_in_the_same_attempt(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  _virtual_time(sup, monkeypatch)
  good = socket.socketpair()[0]
  calls = _rfcomm_attempts(sup, monkeypatch, [errno.EBUSY, good])
  assert sup._connect_rfcomm(CAR, 6) is good
  assert len(calls) == 2 and len(_events(sup, "rfcomm_busy_retry")) == 1


def test_busy_rfcomm_channel_gives_up_after_the_waits(identity, tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import supervisor
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  _virtual_time(sup, monkeypatch)
  calls = _rfcomm_attempts(sup, monkeypatch, [errno.EBUSY])
  with pytest.raises(OSError) as raised:
    sup._connect_rfcomm(CAR, 6)
  assert raised.value.errno == errno.EBUSY and len(calls) == len(supervisor.RFCOMM_BUSY_WAITS) + 1


def test_other_rfcomm_errors_are_not_retried(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  _virtual_time(sup, monkeypatch)
  calls = _rfcomm_attempts(sup, monkeypatch, [errno.ECONNRESET])
  with pytest.raises(OSError):
    sup._connect_rfcomm(CAR, 6)
  assert len(calls) == 1 and not _events(sup, "rfcomm_busy_retry")


def test_a_retried_busy_channel_logs_how_it_ended(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  now = _virtual_time(sup, monkeypatch)
  sup._attempt_ended_at = now[0] - 4.0
  _rfcomm_attempts(sup, monkeypatch, [errno.EBUSY, errno.EBUSY, socket.socketpair()[0]])
  sup._connect_rfcomm(CAR, 6)
  assert _events(sup, "rfcomm_busy_cleared")[0]["tries"] == 3
  assert _events(sup, "rfcomm_busy_retry")[0]["since_last_attempt_s"] == 4.0


def test_a_channel_that_stays_busy_logs_that_it_gave_up(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  _virtual_time(sup, monkeypatch)
  _rfcomm_attempts(sup, monkeypatch, [errno.EBUSY])
  with pytest.raises(OSError):
    sup._connect_rfcomm(CAR, 6)
  assert _events(sup, "rfcomm_busy_gave_up")[0]["waited_s"] == 3.0


def test_hands_free_logs_who_started_the_link(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  now = _virtual_time(sup, monkeypatch)
  sup._paged_at = now[0] - 1.0
  sup._hfp_connected(CAR)
  sup._paged_at = now[0] - 60.0
  sup._hfp_connected(CAR)
  assert [e["initiator"] for e in _events(sup, "hfp_initiator")] == ["comma", "car"]


def test_unusable_sdp_record_is_logged_with_what_the_car_sent(identity, tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import bt_sockets, sdp
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: socket.socketpair()[0])
  _virtual_time(sup, monkeypatch)
  monkeypatch.setattr(bt_sockets, "connect_l2cap", lambda *a, **k: socket.socketpair()[0])

  def query(_):
    raise sdp.SdpError("Android Auto Wireless SDP record has no RFCOMM channel", b"\x35\x03\x19\x11\x1f")

  monkeypatch.setattr(sdp, "query_channel", query)
  with pytest.raises(RuntimeError):
    sup._discover_channel(CAR)
  assert _events(sup, "sdp_retry")[0]["record_hex"] == "350319111f"
