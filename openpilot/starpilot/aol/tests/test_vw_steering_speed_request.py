import pytest
from opendbc.car import structs
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import CAR
from openpilot.starpilot.car.volkswagen.aol import lateral_output_inhibited, policy_for


def params(alpha):
  fp = {bus: {} for bus in range(8)}
  fp[0] = {0xAD: 8, 0x6B8: 8}
  fp[1] = {0xFD: 8}
  fp[2] = {0x126: 8}
  cp = CarInterface.get_params(CAR.SEAT_ATECA_MK1, fp, [], alpha, False, False)
  policy = policy_for(cp)
  cp.alternativeExperience |= policy.alternative_experience_addition
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  assert cp.flags == 5 and cp.alternativeExperience == 32
  assert cp.safetyConfigs[-1].safetyParam == 8 + int(alpha)
  return cp


@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('relative_speed,paused', [(-.01, True), (0., True), (.01, False)])
def test_request_uses_the_existing_actuation_speed_boundary(alpha, relative_speed, paused):
  cp = params(alpha)
  cs = structs.CarState(vEgo=cp.minSteerSpeed + relative_speed)
  assert lateral_output_inhibited(cp, cs) is paused


@pytest.mark.parametrize('alpha', [False, True])
def test_physical_standstill_inhibits_steering_even_while_speed_filter_settles(alpha):
  cp = params(alpha)
  cs = structs.CarState(standstill=True, vEgo=2.)
  assert lateral_output_inhibited(cp, cs)


def test_unadmitted_configuration_is_not_reinterpreted():
  cp = params(True)
  cp.safetyConfigs[0].safetyParam = 1
  assert not lateral_output_inhibited(cp, structs.CarState(standstill=True))
  cp.brand = 'hyundai'
  assert not lateral_output_inhibited(cp, structs.CarState(standstill=True))
  assert not lateral_output_inhibited(None, structs.CarState(standstill=True))
