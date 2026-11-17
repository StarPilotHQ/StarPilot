"""A head unit that shows video but never acknowledges it."""
import time
from pathlib import Path

import pytest

from openpilot.starpilot.system.starport import head_unit_fixes as hf
from openpilot.starpilot.system.starport.quirk_state import QuirkState
from openpilot.starpilot.system.starport.session import CODEC_H265, MSG_PING_REQUEST, ProjectionSession
from openpilot.starpilot.system.starport.tests.fake_head_unit import FakeHeadUnit, make_identity
from openpilot.starpilot.system.starport.tests.test_starport import connect, delta_au, keyframe_au, pump_until
from openpilot.starpilot.system.starport.wire import field

POLESTAR_RULE = next(rule for rule in hf.RULES if rule.id == "polestar_ps2_aar_301433653")
POLESTAR = dict(POLESTAR_RULE.match)
ON = {"head_unit_experiments": True, "head_unit_experiment_rules": [POLESTAR_RULE.id]}


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("unacked-identity"))


def pick(config, transport="wired", key="usb:polestar"):
  return hf.select(config, POLESTAR, transport, QuirkState(Path("/nonexistent/q.json")), key, car_type="usb", profile="default")


def silent_car_session(identity, *, fix: bool):
  """A session with a car that takes video and never acknowledges it, config or frames."""
  hu = FakeHeadUnit(identity, ack_codec_config=False)
  hu.hold_acks.set()
  session = connect(hu, identity)
  events: list[tuple[str, dict]] = []
  session._log = lambda name, **values: events.append((name, values))
  if fix:
    session.on_discovered = lambda info: (hf.UNACKED_VIDEO,)
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  return hu, session, events


def send_frames(hu, session, count):
  sent = 0
  for tag in range(count):
    if not session.can_send():
      break
    session.send_frame(keyframe_au(tag) if tag == 0 else delta_au(tag), tag + 1, keyframe=tag == 0)
    sent += 1
  deadline = time.monotonic() + 5
  while len(hu.frames) < sent:
    assert time.monotonic() < deadline, "the car never received the frames"
    time.sleep(0.01)
  return sent


def end(session, hu):
  hu.close()
  session.peer.close()


def names(events):
  return [name for name, _ in events]


# ------------------------------------------------------------------ the rule

def test_the_polestar_rule_is_the_one_action_and_nothing_a_phone_opens():
  assert POLESTAR_RULE.ladder == (hf.UNACKED_VIDEO,)
  assert hf.validate_rules() == []


def test_the_fix_needs_the_owners_opt_in_and_works_wired_or_wireless():
  assert pick({}) is None
  assert pick({**ON, "head_unit_experiments": False}) is None
  for transport, key in (("wired", "usb:polestar"), ("wireless", "bt:AA")):
    selection = pick(ON, transport=transport, key=key)
    assert selection is not None and selection.actions == (hf.UNACKED_VIDEO,)


def test_another_build_of_the_same_car_does_not_get_it():
  other = {**POLESTAR, "head_unit_software_version": "AAR.301433654 (6.1) 1"}
  quirks = QuirkState(Path("/nonexistent/q.json"))
  assert hf.select(ON, other, "wired", quirks, "usb:other", car_type="usb", profile="default") is None
  assert hf.near_miss(POLESTAR_RULE, other)


# --------------------------------------------------------------- the session

def test_without_the_fix_the_car_stalls_at_the_window_and_times_out(identity):
  hu, session, events = silent_car_session(identity, fix=False)
  assert not session.unacked_video
  assert send_frames(hu, session, 10) == session.window  # the window never reopens
  assert not session.can_send()
  session.last_rx = time.monotonic()  # the car is still talking: it pings throughout
  with pytest.raises(TimeoutError, match="Video acknowledgement older than 2.5 s"):
    session.check_progress(now=session.pending[0] + ProjectionSession.ACK_TIMEOUT + 0.05)
  never = [values for name, values in events if name == "video_never_acknowledged"]
  assert len(never) == 1 and never[0]["frames_sent"] == session.window and never[0]["last_rx_ms"] < 1000
  end(session, hu)


def test_a_car_that_acknowledged_before_is_not_reported_as_never_acknowledging(identity):
  hu = FakeHeadUnit(identity)
  session = connect(hu, identity)
  events: list[tuple[str, dict]] = []
  session._log = lambda name, **values: events.append((name, values))
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  session.send_frame(keyframe_au(1), 1, keyframe=True)
  pump_until(session, lambda: not session.pending and not session.config_ack_slack)  # frame and codec config ACKs both in
  hu.hold_acks.set()
  session.send_frame(delta_au(2), 2, keyframe=False)
  with pytest.raises(TimeoutError, match="Video acknowledgement older"):
    session.check_progress(now=session.pending[0] + ProjectionSession.ACK_TIMEOUT + 0.05)
  assert "video_never_acknowledged" not in names(events)
  end(session, hu)


def test_with_the_fix_video_keeps_going_past_the_window(identity):
  hu, session, events = silent_car_session(identity, fix=True)
  assert session.unacked_video
  assert ("fix_action", {"action": hf.UNACKED_VIDEO, "done": True}) in events
  count = session.window * 4
  assert send_frames(hu, session, count) == count
  assert len(hu.frames) == count and session.frames_sent == count
  assert session.can_send() and not session.pending and session.unacked == 0  # nothing is waited for or accumulated
  assert session.stats()["unacked_video"] is True
  session.last_rx = time.monotonic()
  session.check_progress(now=session.unacked_started_at + ProjectionSession.ACK_TIMEOUT - 0.5)
  assert not session.video_confirmed  # too soon: an acknowledging car would not have been given up on yet
  session.check_progress(now=session.unacked_started_at + ProjectionSession.ACK_TIMEOUT + 0.05)
  assert session.video_confirmed and session.video_confirmed_at == session.unacked_started_at
  confirmed = [values for name, values in events if name == "video_acknowledged"]
  assert confirmed == [{"session": session.session_id, "ack_ms": None, "unacked": True, "frames_sent": count}]
  session.last_rx = time.monotonic()
  session.check_progress()  # and it stays up while the car talks
  end(session, hu)


def test_with_the_fix_a_silent_car_still_ends_the_session(identity):
  hu, session, _ = silent_car_session(identity, fix=True)
  send_frames(hu, session, 3)
  started = session.unacked_started_at
  session.last_rx = started - 1.0  # nothing from the car since before the first frame
  session.check_progress(now=started + ProjectionSession.UNACKED_SILENCE_TIMEOUT - 0.5)
  with pytest.raises(TimeoutError, match="Head unit stopped answering for 5.0 s while projecting without video acknowledgments"):
    session.check_progress(now=started + ProjectionSession.UNACKED_SILENCE_TIMEOUT + 1.0)
  assert not session.video_confirmed  # a car that went quiet straight away never counted as showing video
  end(session, hu)


def test_with_the_fix_a_message_already_in_the_socket_is_read_before_calling_the_car_silent(identity):
  hu, session, _ = silent_car_session(identity, fix=True)
  send_frames(hu, session, 1)
  while session.pump(0.2):
    pass
  session.unacked_started_at -= 30.0  # well into the stream
  session.last_rx = time.monotonic() - ProjectionSession.UNACKED_SILENCE_TIMEOUT - 1.0  # this loop stalled; the car did not
  session.send(0, MSG_PING_REQUEST, field(1, 1))  # our ping: the car's answer is now waiting unread
  deadline = time.monotonic() + 5
  while hu.messages_seen.get((0, MSG_PING_REQUEST), 0) == 0:
    assert time.monotonic() < deadline
    time.sleep(0.01)
  time.sleep(0.1)
  session.check_progress()  # reads the answer instead of raising
  assert time.monotonic() - session.last_rx < 1.0
  end(session, hu)


def test_with_the_fix_an_acknowledgment_is_counted_and_never_fatal(identity):
  hu, session, events = silent_car_session(identity, fix=True)
  send_frames(hu, session, 2)
  session._handle_ack(session.session_id, 5)  # more than was ever sent: fatal for a car that is waited on
  session._handle_ack(session.session_id, 1)
  assert session.acked == 6 and session.unacked == 0
  assert names(events).count("unexpected_video_ack") == 1
  end(session, hu)


def test_the_fix_does_not_touch_a_car_that_acknowledges(identity):
  hu = FakeHeadUnit(identity)
  session = connect(hu, identity)
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  assert not session.unacked_video and "unacked_video" not in session.stats()
  session.send_frame(keyframe_au(1), 1, keyframe=True)
  assert session.unacked == 1 and len(session.pending) == 1 and session.unacked_started_at is None
  pump_until(session, lambda: session.acked == 1 and not session.pending)
  end(session, hu)


def test_the_owner_toggle_works_on_h264_without_any_fix(identity):
  hu = FakeHeadUnit(identity, ack_codec_config=False)
  hu.hold_acks.set()
  session = connect(hu, identity)
  session.unacked_video = True
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  assert session.mode.codec != CODEC_H265
  count = session.window * 3
  assert send_frames(hu, session, count) == count
  assert session.can_send() and not session.pending
  end(session, hu)
