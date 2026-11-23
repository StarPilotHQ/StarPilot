"""The streaming loop asks again for the screen a head unit never granted, and never after the driver chose native."""

import threading
import time

import pytest

from openpilot.starpilot.system.starport import supervisor


class Session:
  def __init__(self, grant_on=0):
    self.focused, self.focus_epoch = False, 0
    self.touch_events: list = []
    self.unacked = 0
    self.requests = 0
    self.grant_on = grant_on  # the car grants focus on this re-request (0: never)

  def pump(self, timeout):
    time.sleep(0.002)
    return False

  def check_progress(self):
    pass

  def can_send(self):
    return False

  def stats(self):
    return {}

  def request_projection(self):
    self.requests += 1
    if self.requests == self.grant_on:
      self.focused, self.focus_epoch = True, 1

  def native(self):
    """The car shows its own screen again (focus=2), as it does when the driver leaves Android Auto."""
    self.focused = False


class Source:
  label = view = "car"
  frames = 0

  def demand(self, _):
    pass

  def release_demand(self):
    pass

  def drain_controls(self):
    return []

  def check(self, now, focused):
    pass


class Watch:
  lost = None
  wifi_dbm = None
  lease = None


def stream(monkeypatch, session, seconds=0.4, during=None, reconnects_left=0):
  monkeypatch.setattr(supervisor, "FOCUS_RETRY_FIRST", 0.03)
  monkeypatch.setattr(supervisor, "FOCUS_RETRY_INTERVAL", 0.03)
  monkeypatch.setattr(supervisor, "NATIVE_FOCUS_GIVE_UP_S", 0.1)
  monkeypatch.setattr(supervisor.device_health, "sample", lambda interface: {})
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  events = []
  sup.log = lambda name, **values: events.append((name, values))
  sup._stop, sup._status = threading.Event(), {"state": ""}
  sup._stage = lambda *a, **k: None
  sup._set = lambda **values: sup._status.update(values)
  sup._native_reconnects, sup._native_reconnect_limit = 0, reconnects_left  # the limit comes from the car profile
  sup._companion_keeper = None
  timer = threading.Timer(seconds, sup._stop.set)
  timer.start()
  if during is not None:
    threading.Timer(during[0], during[1]).start()
  try:
    sup._stream(session, type("Encoder", (), {"last_encode_ms": 0.0})(), Source(), Watch(), 1 / 30)
  finally:
    sup._stop.set()
    timer.cancel()
  return [values for name, values in events if name in ("projection_rerequested", "projection_native_give_up")]


def test_never_granted_focus_is_asked_for_again_a_bounded_number_of_times(monkeypatch):
  session = Session()
  requests = stream(monkeypatch, session)
  assert session.requests == supervisor.FOCUS_RETRIES == 5
  assert [r["attempt"] for r in requests] == [1, 2, 3, 4, 5]


def test_native_focus_after_a_grant_is_left_alone(monkeypatch):
  session = Session(grant_on=2)
  requests = stream(monkeypatch, session, during=(0.15, session.native))
  assert session.requests == 2 and len(requests) == 2 and not session.focused


def test_a_car_that_never_grants_focus_is_left_for_a_fresh_connection(monkeypatch):
  # 2026-10-30 Honda: "native" to every request. While reconnects are left, give up after NATIVE_FOCUS_GIVE_UP_S.
  session = Session()
  with pytest.raises(supervisor.NativeFocusHeld):
    stream(monkeypatch, session, seconds=2.0, reconnects_left=1)
  assert 1 <= session.requests < supervisor.FOCUS_RETRIES  # it was asked again first, not just dropped


def test_a_late_grant_cancels_the_give_up(monkeypatch):
  session = Session(grant_on=2)
  requests = stream(monkeypatch, session, seconds=0.3, reconnects_left=2)
  assert session.focused and [r["attempt"] for r in requests] == [1, 2]


@pytest.mark.parametrize("granted", [True, False])
def test_no_re_request_once_the_car_granted_focus(monkeypatch, granted):
  session = Session()
  session.focus_epoch, session.focused = 1, granted  # granted at start(); False: the driver went native since
  assert stream(monkeypatch, session, seconds=0.2) == [] and session.requests == 0
