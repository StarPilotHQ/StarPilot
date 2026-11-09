"""Pins what StarPort sends to a car that has no rule and no recorded failure.

These tests describe today's behavior. They must stay green and unchanged as car-specific fixes are added: a car that works
now has to keep receiving the same bytes.
"""

import pytest

from openpilot.starpilot.system.starport import bootstrap as bs
from openpilot.starpilot.system.starport.tests.fake_head_unit import BLUETOOTH_CHANNEL, SENSOR_CHANNEL, FakeHeadUnit, make_identity
from openpilot.starpilot.system.starport.tests.test_starport import connect, pump_until, run_bootstrap
from openpilot.starpilot.system.starport.wire import field, one, signed


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("golden-identity"))


def run_default_session(identity, *, configure=None, **hu_options):
  hu = FakeHeadUnit(identity, **hu_options)
  session = connect(hu, identity)
  if configure is not None:
    configure(session)
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused, timeout=8.0)
  return hu, session


def finish(hu, session):
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None, hu.error


def test_discovery_request_is_name_and_brand_only(identity):
  hu, session = run_default_session(identity)
  finish(hu, session)
  assert hu.discovery_requests == [field(4, "StarPilot") + field(5, "comma.ai")]


def test_default_opens_video_and_input_only(identity):
  hu, session = run_default_session(identity, phone_services=True)
  finish(hu, session)
  assert sorted(hu.opened) == [1, 3]
  assert SENSOR_CHANNEL not in hu.opened and BLUETOOTH_CHANNEL not in hu.opened
  assert hu.pairing_requests == [] and hu.sensor_requests == []


def test_default_asks_for_projected_focus_once_and_never_stops_video(identity):
  hu, session = run_default_session(identity)
  finish(hu, session)
  assert hu.focus_requests == [(1, 4)]
  assert hu.focus_request_bodies == [field(2, 1) + field(3, 4)]
  assert hu.video_stops == []


def test_native_request_has_no_display_field_and_sends_no_stop(identity):
  hu, session = run_default_session(identity)
  assert session.request_native()
  pump_until(session, lambda: not session.focused)
  finish(hu, session)
  assert hu.focus_request_bodies[-1] == field(2, 2) + field(3, 2)
  assert hu.video_stops == []


def test_phone_services_profile_opens_exactly_sensors_and_bluetooth(identity):
  def configure(session):
    session.phone_services = True
    session.phone_address = "39:90:21:64:07:00"

  hu, session = run_default_session(identity, phone_services=True, configure=configure)
  pump_until(session, lambda: session.sensor_events)
  finish(hu, session)
  assert sorted(hu.opened) == [1, 3, SENSOR_CHANNEL, BLUETOOTH_CHANNEL]
  assert hu.sensor_requests == [13, 10]
  assert hu.pairing_requests == [("39:90:21:64:07:00", 2)]


def test_delayed_driver_selected_focus_adds_no_messages(identity):
  hu = FakeHeadUnit(identity, grant_focus_after_s=3.0)
  session = connect(hu, identity)
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused, timeout=15.0)
  finish(hu, session)
  assert hu.focus_requests == [(1, 4)]
  assert hu.video_stops == [] and sorted(hu.opened) == [1, 3]
  assert hu.discovery_requests == [field(4, "StarPilot") + field(5, "comma.ai")]


def test_standard_bootstrap_replies_are_unchanged():
  result, _, seen, _ = run_bootstrap(version_first=True)
  assert result.version == (1, 3)
  assert one(seen["version_response"], 1) == 1 and signed(one(seen["version_response"], 4)) == 0
  assert set(seen["start_response"]) == {3} and signed(one(seen["start_response"], 3)) == 0
  assert set(seen["connect_status"]) == {1} and signed(one(seen["connect_status"], 1)) == 0


def test_bootstrap_status_constants_are_unchanged():
  assert (bs.STATUS_SUCCESS, bs.STATUS_NETWORK_UNAVAILABLE, bs.STATUS_NOT_READY) == (0, -1, -6)
