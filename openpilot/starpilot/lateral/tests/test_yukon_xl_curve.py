import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import CAR
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.torque_extension import selected_policy


def controller(identity, vin):
  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False, vin=vin).as_reader()
  return cp, LatControlTorque(cp, CarInterface(cp), 0.01)


@pytest.mark.parametrize('direction', [-1, 1])
def test_xl_adds_bounded_feedforward_as_curvature_builds(direction):
  xl_cp, xl = controller(CAR.GMC_YUKON_CC, '1GKS2GKC0JR000001')
  short_cp, short = controller(CAR.GMC_YUKON_CC, '1GKS2BKC0JR000001')
  assert xl_cp.safetyConfigs[0].to_dict() == short_cp.safetyConfigs[0].to_dict()
  assert xl.pid.pos_limit == short.pid.pos_limit
  xl, short = selected_policy(xl), selected_policy(short)
  assert xl.yukon_xl and not short.yukon_xl

  value = direction * 0.9
  target, jerk = direction * 0.8, direction * 0.6
  before = short.feedforward(value, target, jerk, 15.0)
  after = xl.feedforward(value, target, jerk, 15.0)
  assert 0 < direction * (after - before) <= abs(value) * 0.05
  for args in ((value, target, -jerk, 15.0), (value, 0.2 * direction, jerk, 15.0),
               (value, target, jerk, 9.0), (-value, target, jerk, 15.0)):
    assert xl.feedforward(*args) == short.feedforward(*args)
