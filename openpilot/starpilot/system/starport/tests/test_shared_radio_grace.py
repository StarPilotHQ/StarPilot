"""2026-11-02: the comma's own Wi-Fi joining or roaming stalled the car's video acks (one shared radio)."""
import time

import pytest

from openpilot.starpilot.system.starport import reconnect_notice
from openpilot.starpilot.system.starport.bugsink_diagnosis import stream_loss_kind
from openpilot.starpilot.system.starport import supervisor as supervisor_module
from openpilot.starpilot.system.starport.session import ProjectionSession, SharedRadioStall
from openpilot.starpilot.system.starport.supervisor import SharedRadioWatch, Supervisor
from openpilot.starpilot.system.starport.tests.fake_head_unit import FakeHeadUnit, make_identity
from openpilot.starpilot.system.starport.tests.test_starport import end_session, pump_until, sent_frame, streaming_session


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("identity"))


def stalled(identity, changed: bool):
  hu = FakeHeadUnit(identity)
  session = streaming_session(hu, identity)
  session.radio_change = lambda: changed
  hu.hold_acks.set()
  return hu, session, sent_frame(hu, session)


def test_no_radio_change_keeps_the_standard_limit(identity):
  hu, session, sent = stalled(identity, False)
  with pytest.raises(TimeoutError, match="2.5 s") as raised:
    session.check_progress(now=sent + ProjectionSession.ACK_TIMEOUT + 0.05)
  assert not isinstance(raised.value, SharedRadioStall)
  end_session(session, hu)


def test_radio_change_waits_up_to_the_grace_limit(identity):
  hu, session, sent = stalled(identity, True)
  session.check_progress(now=sent + ProjectionSession.ACK_TIMEOUT + 0.05)
  session.check_progress(now=sent + ProjectionSession.ACK_GRACE_TIMEOUT - 0.1)
  with pytest.raises(SharedRadioStall):
    session.check_progress(now=sent + ProjectionSession.ACK_GRACE_TIMEOUT + 0.05)
  end_session(session, hu)


def test_grace_started_by_a_change_survives_the_change_ageing_out(identity):
  hu, session, sent = stalled(identity, True)
  session.check_progress(now=sent + ProjectionSession.ACK_TIMEOUT + 0.05)
  session.radio_change = lambda: False  # same stall: the decision was made when it began
  session.check_progress(now=sent + ProjectionSession.ACK_GRACE_TIMEOUT - 0.1)
  end_session(session, hu)


def test_acks_resuming_during_the_grace_keep_the_session_and_log_the_gap(identity):
  hu, session, sent = stalled(identity, True)
  events = []
  session.event = lambda name, **fields: events.append((name, fields))
  session.check_progress(now=sent + ProjectionSession.ACK_TIMEOUT + 0.05)
  hu.hold_acks.clear()
  hu._ack(session.session_id)
  pump_until(session, lambda: not session.pending)
  assert session.grace_frame is None
  assert [name for name, _ in events] == ["ack_grace", "ack_grace_recovered"]
  session.check_progress(now=sent + ProjectionSession.ACK_GRACE_TIMEOUT + 1)  # nothing pending: no timeout
  end_session(session, hu)


def test_car_taking_its_screen_back_during_the_grace_reconnects(identity):
  hu, session, sent = stalled(identity, True)
  session.check_progress(now=sent + ProjectionSession.ACK_TIMEOUT + 0.05)
  hu.set_focus(False)
  pump_until(session, lambda: not session.focused)
  with pytest.raises(SharedRadioStall, match="took its screen back"):
    session.check_progress(now=sent + ProjectionSession.ACK_TIMEOUT + 0.5)
  end_session(session, hu)


def test_unfocus_outside_a_grace_is_the_normal_reverse_camera(identity):
  hu = FakeHeadUnit(identity)
  session = streaming_session(hu, identity)
  hu.set_focus(False)
  pump_until(session, lambda: not session.focused)
  session.check_progress()
  end_session(session, hu)


def test_watch_reports_a_carrier_change_without_naming_the_network(tmp_path):
  counter = tmp_path / "carrier_changes"
  counter.write_text("4\n")
  logged = []
  watch = SharedRadioWatch(lambda name, **fields: logged.append((name, fields)), interval=0.01, counter=counter).start()
  try:
    time.sleep(0.05)
    assert not watch.recent()  # the starting value is not a change
    counter.write_text("5\n")
    deadline = time.monotonic() + 2
    while not watch.recent():
      assert time.monotonic() < deadline
      time.sleep(0.01)
    assert logged == [("other_wifi_changed", {"interface": "wlan0"})]
    assert watch.changed_at is not None
    assert not watch.recent(now=watch.changed_at + supervisor_module.RADIO_CHANGE_WINDOW + 0.1)
  finally:
    watch.stop()


@pytest.mark.parametrize("carrier, held", [("0\n", True), ("1\n", False), (None, True)])
def test_watch_holds_home_wifi_only_when_it_was_lost(tmp_path, carrier, held):
  counter, carrier_path = tmp_path / "carrier_changes", tmp_path / "carrier"
  counter.write_text("4\n")
  if carrier is not None:
    carrier_path.write_text(carrier)  # missing: reading carrier of a down interface fails
  holds = []
  watch = SharedRadioWatch(lambda *a, **k: None, interval=0.01, counter=counter, carrier=carrier_path,
                           on_down=lambda: holds.append(True)).start()
  try:
    time.sleep(0.05)
    counter.write_text("5\n")
    deadline = time.monotonic() + 2
    while not watch.recent():
      assert time.monotonic() < deadline
      time.sleep(0.01)
    time.sleep(0.05)
    assert holds == ([True] if held else [])
  finally:
    watch.stop()


def test_watch_without_the_counter_never_reports(tmp_path):
  watch = SharedRadioWatch(lambda *a, **k: None, interval=0.01, counter=tmp_path / "missing").start()
  try:
    time.sleep(0.05)
    assert not watch.recent()
  finally:
    watch.stop()


def test_notice_is_shown_once_and_only_while_fresh(tmp_path):
  path = tmp_path / "notice.json"
  reconnect_notice.write("Reconnected", path, now=1000.0)
  assert reconnect_notice.take(path, now=1010.0) == "Reconnected"
  assert reconnect_notice.take(path, now=1010.0) == ""  # taken
  reconnect_notice.write("Reconnected", path, now=1000.0)
  assert reconnect_notice.take(path, now=1000.0 + reconnect_notice.NOTICE_FRESH_S + 1) == ""
  assert not path.exists()


def test_notice_from_another_boot_is_discarded(tmp_path, monkeypatch):
  monkeypatch.setattr(reconnect_notice.identity_store, "boot_id", lambda: "previous")
  path = tmp_path / "notice.json"
  reconnect_notice.write("Reconnected", path, now=20.0)
  monkeypatch.setattr(reconnect_notice.identity_store, "boot_id", lambda: "current")
  assert reconnect_notice.take(path, now=30.0) == "" and not path.exists()


def test_galaxy_status_says_what_happened():
  text = Supervisor._describe_error("streaming", SharedRadioStall("stalled"))
  assert text == "The car's Wi-Fi stalled while the comma's own Wi-Fi was switching networks; reconnecting"
  assert stream_loss_kind(text) == "shared_radio"  # bug reports file it apart from ordinary stalls
  assert stream_loss_kind("projecting: Video acknowledgement older than 2.5 s") == "stalled"
