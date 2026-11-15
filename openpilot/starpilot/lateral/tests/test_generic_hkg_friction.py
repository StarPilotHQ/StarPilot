from contextlib import ExitStack
from unittest.mock import patch

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from opendbc.car.lateral import get_friction
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode
from openpilot.starpilot.lateral.generic_torque_policy import GenericTorquePolicy
from openpilot.starpilot.lateral.torque_extension import selected_policy


PROFILES = ((CAR.KIA_NIRO_EV_2ND_GEN, False), (CAR.KIA_NIRO_EV_2ND_GEN, True),
            (CAR.HYUNDAI_KONA_EV_2ND_GEN, False), (CAR.HYUNDAI_KONA_EV_2ND_GEN, True),
            (CAR.GENESIS_GV60_EV_1ST_GEN, False))


def factory(car, alpha, stack):
  fp = gen_empty_fingerprint()
  bus = 0
  if car == CAR.HYUNDAI_KONA_EV_2ND_GEN:
    fp[2].update({0x50: 16, 0x2A4: 24})
    bus = 1
  fp[bus].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1BA: 24})
  fp[2][0x1A0] = 32
  cp = CarInterface.get_params(car, fp, [], alpha, False, False)
  if alpha and not cp.openpilotLongitudinalControl:
    from opendbc.car.hyundai.tests import test_torque_ev_startup as startup_fixture
    startup, _ = startup_fixture.owner(cp)
    stack.callback(startup.close)
    with patch('opendbc.car.hyundai.torque_ev_startup.time.sleep'):
      cp = startup.prepare(admission=lambda: True)
  assert cp.openpilotLongitudinalControl == alpha
  return cp.as_reader()


@pytest.mark.parametrize('car,alpha', PROFILES)
@pytest.mark.parametrize('speed', (0.0, 2.0, 10.0, 20.0, 35.0))
def test_generic_canfd_matches_stable_hkg_friction(car, alpha, speed):
  with ExitStack() as stack:
    cp = factory(car, alpha, stack)
    control = LatControlTorque(cp, CarInterface(cp), .01, controller_mode=ControllerMode.STARPILOT)
    policy = selected_policy(control)
    assert isinstance(policy, GenericTorquePolicy)
    state = structs.CarState()
    state.vEgo = speed
    for setpoint in (-1.0, 1.0):
      threshold, scale = policy.friction_context(setpoint, .2, .1, speed)
      assert threshold == pytest.approx(.39)
      assert scale == 1.0
      for deadzone in (0.0, .01):
        for error in (-.5, -.08, -.01, 0.0, .01, .08, .5):
          actual = scale * control.friction(error, deadzone, threshold, state, setpoint)
          assert actual == pytest.approx(get_friction(error, deadzone, .39, control.torque_params))


@pytest.mark.parametrize('alpha', (False, True))
def test_explicit_standard_controller_is_unchanged(alpha):
  with ExitStack() as stack:
    cp = factory(CAR.KIA_NIRO_EV_2ND_GEN, alpha, stack)
    control = LatControlTorque(cp, CarInterface(cp), .01, controller_mode=ControllerMode.STANDARD)
    assert selected_policy(control) is None
    state = structs.CarState()
    state.vEgo = 10.0
    assert control.friction(.08, 0.0, .30, state, 1.0) == pytest.approx(
      get_friction(.08, 0.0, .30, control.torque_params))


@pytest.mark.parametrize('family', ('classic_hyundai', 'toyota'))
def test_generic_other_family_keeps_standard_base(family):
  if family == 'classic_hyundai':
    cp = CarInterface.get_params(CAR.HYUNDAI_SONATA, gen_empty_fingerprint(), [], False, False, False).as_reader()
    interface = CarInterface(cp)
  else:
    from opendbc.car.toyota.interface import CarInterface as ToyotaInterface
    from opendbc.car.toyota.values import CAR as TOYOTA
    cp = ToyotaInterface.get_params(TOYOTA.TOYOTA_COROLLA_TSS2, gen_empty_fingerprint(), [], False, False, False).as_reader()
    interface = ToyotaInterface(cp)
  parent = LatControlTorque(cp, interface, .01, controller_mode=ControllerMode.STANDARD)
  policy = GenericTorquePolicy(parent, cp)
  assert policy.friction(1.0, .2, 10.0) == (.30, 1.0)


@pytest.mark.parametrize('car', (CAR.HYUNDAI_IONIQ_5, CAR.KIA_EV6))
def test_existing_vehicle_policy_is_not_replaced(car):
  fp = gen_empty_fingerprint()
  fp[0][0x1CF] = 8
  cp = CarInterface.get_params(car, fp, [], False, False, False).as_reader()
  control = LatControlTorque(cp, CarInterface(cp), .01, controller_mode=ControllerMode.STARPILOT)
  assert selected_policy(control) is not None
  assert not isinstance(selected_policy(control), GenericTorquePolicy)
