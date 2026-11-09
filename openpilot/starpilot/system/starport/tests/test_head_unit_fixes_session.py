import time

import pytest

from openpilot.starpilot.system.starport import head_unit_fixes as hf
from openpilot.starpilot.system.starport.quirk_state import QuirkState
from openpilot.starpilot.system.starport.tests.fake_head_unit import (
  BLUETOOTH_CHANNEL, PHONE_STATUS_CHANNEL, SENSOR_CHANNEL, VENDOR_CHANNEL, FakeHeadUnit, make_identity)
from openpilot.starpilot.system.starport.tests.test_starport import connect, delta_au, keyframe_au, pump_until
from openpilot.starpilot.system.starport.wire import field

RULE = hf.RULES[0]
ON = {"head_unit_experiments": True, "head_unit_experiment_rules": [RULE.id]}


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("fixes-identity"))


def run(identity, tmp_path, config, level=None, **hu_options):
  quirks = QuirkState(tmp_path / "q.json")
  hu = FakeHeadUnit(identity, bolt=True, **hu_options)
  session = connect(hu, identity)
  session.phone_address = "39:90:21:64:07:00"

  def choose(info):
    labels = hf.head_unit_labels(info)
    if level is not None:
      quirks.begin("bt:AA", labels, RULE.id)["level"] = level
    selection = hf.select(config, labels, "wireless", quirks, "bt:AA", car_type="builtin", profile="default")
    return selection.actions if selection else ()

  session.on_discovered = choose
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused, timeout=8.0)
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None, hu.error
  return hu, session


def test_discovery_labels_identify_the_bolt_build(identity, tmp_path):
  hu, session = run(identity, tmp_path, {})
  assert hf.matches(RULE, hf.head_unit_labels(session.head_unit_info))


def test_without_opt_in_the_bolt_gets_the_default_session(identity, tmp_path):
  hu, _ = run(identity, tmp_path, {})
  assert sorted(hu.opened) == [1, 3] and hu.pairing_requests == []
  assert hu.focus_request_bodies == [field(2, 1) + field(3, 4)]


def test_experiments_off_gives_default_session(identity, tmp_path):
  hu, _ = run(identity, tmp_path, {**ON, "head_unit_experiments": False})
  assert sorted(hu.opened) == [1, 3]


def test_level_one_opens_only_bluetooth_without_pairing(identity, tmp_path):
  hu, _ = run(identity, tmp_path, ON)
  assert sorted(hu.opened) == [1, 3, BLUETOOTH_CHANNEL]
  assert hu.pairing_requests == [] and hu.sensor_requests == []


def test_ladder_adds_one_channel_per_level(identity, tmp_path):
  assert sorted(run(identity, tmp_path, ON, level=2)[0].opened) == [1, 3, BLUETOOTH_CHANNEL, PHONE_STATUS_CHANNEL]
  hu, _ = run(identity, tmp_path, ON, level=3)
  assert SENSOR_CHANNEL in hu.opened and VENDOR_CHANNEL not in hu.opened
  hu, _ = run(identity, tmp_path, ON, level=4)
  assert VENDOR_CHANNEL in hu.opened and hu.pairing_requests == []


def test_pairing_request_only_at_the_last_level(identity, tmp_path):
  hu, _ = run(identity, tmp_path, ON, level=5)
  assert hu.pairing_requests == [("39:90:21:64:07:00", 2)]


def bolt_hangs_up(identity, tmp_path, config):
  hu = FakeHeadUnit(identity, bolt=True, hang_up_after_s=0.2, hang_up_unless=frozenset({BLUETOOTH_CHANNEL}))
  session = connect(hu, identity)
  quirks = QuirkState(tmp_path / "q.json")
  session.on_discovered = lambda info: getattr(
    hf.select(config, hf.head_unit_labels(info), "wireless", quirks, "bt:AA", car_type="builtin", profile="default"), "actions", ())
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused, timeout=8.0)
  session.send_frame(keyframe_au(1), 1, keyframe=True)
  pump_until(session, lambda: bool(hu.frames), timeout=5.0)
  deadline = time.monotonic() + 1.0
  while time.monotonic() < deadline and not hu.hung_up:
    try:
      session.pump(0.05)
    except Exception:
      break
    session.send_frame(delta_au(2), 2, keyframe=False)
  hung_up = hu.hung_up
  session.peer.close()
  hu.thread.join(5)
  return hung_up


def test_fix_stops_the_bolt_hang_up(identity, tmp_path):
  assert bolt_hangs_up(identity, tmp_path, {}) is True
  assert bolt_hangs_up(identity, tmp_path, ON) is False
