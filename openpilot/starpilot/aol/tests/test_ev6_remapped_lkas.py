"""EV6 route25: an adopted speed limit must not silently withdraw lateral."""
import pytest
from openpilot.starpilot.aol.intent import AOL_TOGGLE, read_settings
from openpilot.starpilot.aol.tests import test_ev6_physical_authorization as fixture
from opendbc.car.hyundai.values import Buttons
from opendbc.car import structs
from openpilot.starpilot.controllers.wheel_actions import DELIVERED_ACTIONS


@pytest.mark.parametrize("action", (1, 2, 5, 6, 7, 8, 10, 11, 12, 13, 14))
def test_remapped_lkas_keeps_established_lateral_after_brake(action, tmp_path):
  from openpilot.common.params import Params
  params = Params(str(tmp_path))
  params.put_bool("AlwaysOnLateral", True, block=True)
  params.put("LKASButtonControl", action, block=True)
  params.put("MainCruiseButtonControl", 0, block=True)
  case = fixture.TestEv6ConfiguredDesire(methodName="runTest")
  owner = case.owner()
  owner.settings = read_settings(params)
  case.sample(owner, [(0, 0, Buttons.NONE)])
  # Fresh SET/release and successful engagement establish independent lateral.
  cs = case.sample(owner, [(0, 0, Buttons.SET_DECEL), (0, 0, Buttons.NONE)], active=True, cruise_enabled=True)
  assert owner.output(cs)[0]
  press = structs.CarState.ButtonEvent(type="lkas", pressed=True)
  release = structs.CarState.ButtonEvent(type="lkas", pressed=False)
  cs = case.sample(owner, [(0, 1, Buttons.NONE)], events=[press], active=True, cruise_enabled=True)
  assert owner.output(cs)[0]
  assert not owner._engagement_manual_withdrawal
  case.sample(owner, [(0, 0, Buttons.NONE)], events=[release], active=True, cruise_enabled=True)
  # Cruise withdrawal at the later brake must leave this established latch alone.
  cs = case.sample(owner, [], active=False, cruise_enabled=False, brake=True)
  assert owner.output(cs)[0]
  # Explicit OFF still owns the latch and cannot be reversed by a remapped press.
  owner._perform(AOL_TOGGLE)
  assert not owner.output(cs)[0]
  cs = case.sample(owner, [(0, 1, Buttons.NONE), (0, 0, Buttons.NONE)], active=True)
  assert not owner.output(cs)[0]


def test_remapped_lkas_never_creates_authority(tmp_path):
  from openpilot.common.params import Params
  params = Params(str(tmp_path))
  params.put_bool("AlwaysOnLateral", True, block=True)
  params.put("LKASButtonControl", 10, block=True)
  params.put("MainCruiseButtonControl", 0, block=True)
  case = fixture.TestEv6ConfiguredDesire(methodName="runTest")
  owner = case.owner()
  owner.settings = read_settings(params)
  case.sample(owner, [(0, 0, Buttons.NONE)])
  cs = case.sample(owner, [(0, 1, Buttons.NONE), (0, 0, Buttons.NONE)])
  assert not owner.output(cs)[0]
  assert not owner.physical.authorized


def test_lkas_wheel_action_catalog_matches_publisher(tmp_path):
  from openpilot.common.params import Params
  params = Params(str(tmp_path))
  for action in range(15):
    params.put("LKASButtonControl", action, block=True)
    assert read_settings(params).lkas_wheel_action == (action in DELIVERED_ACTIONS)


@pytest.mark.parametrize("boundary", ("fault", "acc_fault", "invalid", "native_rejection"))
def test_remapped_press_does_not_restore_rejected_authority(boundary):
  from dataclasses import replace
  case = fixture.TestEv6ConfiguredDesire(methodName="runTest")
  owner = case.owner()
  owner.settings = replace(owner.settings, lkas_wheel_action=True)
  case.sample(owner, [(0, 0, Buttons.NONE)])
  cs = case.sample(owner, [(0, 0, Buttons.SET_DECEL), (0, 0, Buttons.NONE)], active=True)
  assert owner.output(cs)[0]
  if boundary == "native_rejection":
    owner.update(cs, now_ns=owner._test_clock_ns, native_rejection_ns=owner._test_clock_ns)
  else:
    case.sample(owner, [], fault=boundary == "fault", acc_fault=boundary == "acc_fault", valid=boundary != "invalid")
  case.sample(owner, [(0, 0, Buttons.NONE)])
  cs = case.sample(owner, [(0, 1, Buttons.NONE), (0, 0, Buttons.NONE)])
  assert not owner.physical.authorized
  assert not owner.output(cs)[0]


def test_remapped_lkas_still_delivers_speed_limit_action(tmp_path):
  from openpilot.common.params import Params
  from openpilot.starpilot.controllers.wheel_actions import WheelPublisher
  params = Params(str(tmp_path))
  params.put("LKASButtonControl", 10, block=True)
  cp = fixture.ev6_params()
  owner = WheelPublisher()
  now = 3_000_000_000
  def state(pressed=None):
    return structs.CarState(canValid=True, buttonEvents=[] if pressed is None else [
      structs.CarState.ButtonEvent(type="lkas", pressed=pressed)])
  owner.observe(params, cp, state(), now_ns=now, drive_id=1_000_000_000)
  assert owner.observe(params, cp, state(True), now_ns=now + 10_000_000, drive_id=1_000_000_000) == (("LKASButtonControl", 10),)
  assert not owner.observe(params, cp, state(False), now_ns=now + 20_000_000, drive_id=1_000_000_000)
