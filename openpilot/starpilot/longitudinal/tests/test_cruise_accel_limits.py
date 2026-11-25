"""The cruise floor and ceiling the planner publishes are the ones get_cruise_accel clips to."""
import pytest

from opendbc.car.interfaces import ACCEL_MAX, ACCEL_MIN
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.selfdrive.controls.lib.longitudinal_planner import A_CRUISE_MIN, cruise_accel_limits, get_cruise_accel
from openpilot.starpilot.longitudinal.profile_runtime import AppliedProfile


def camry():
  return CarInterface.get_params(CAR.TOYOTA_CAMRY, {0: {0x2FF: 4}, 1: {}, 2: {}}, [], True, False, False)


def test_defaults_are_the_cruise_floor_and_the_speed_ceiling():
  floor, ceiling = cruise_accel_limits(False, 20., 0., camry(), 0., True)
  assert floor == A_CRUISE_MIN and 0. < ceiling <= ACCEL_MAX
  assert cruise_accel_limits(True, 20., 0., camry(), 0., True) == (A_CRUISE_MIN, ACCEL_MAX), 'experimental mode'


def test_profiles_a_withheld_throttle_and_a_downhill_correction_move_the_limits():
  cp = camry()
  profile = AppliedProfile(follow_seconds=1.45, acceleration_max=0.7, cruise_brake_magnitude=2.4, acceleration_jerk=1.,
                           deceleration_jerk=1., speed_jerk=1., speed_decrease_jerk=1., danger_jerk=1.)
  assert cruise_accel_limits(False, 20., 0., cp, 0., True, profile) == (-2.4, 0.7)
  assert cruise_accel_limits(False, 20., 0., cp, 0., True, None, -1.2, 0.9) == (-1.2, 0.9)
  assert cruise_accel_limits(False, 20., 0., cp, 0., True, None, -9.)[0] == ACCEL_MIN
  assert cruise_accel_limits(False, 20., 0., cp, -0.3, False)[1] == pytest.approx(-0.3), 'coast only'
  assert cruise_accel_limits(False, 20., 0., cp, 0., True, downhill=True)[1] == 0.
  assert cruise_accel_limits(False, 30., 40., cp, 0., True)[1] < cruise_accel_limits(False, 30., 0., cp, 0., True)[1]


@pytest.mark.parametrize('experimental', (False, True))
@pytest.mark.parametrize('error', (-30., -0.2, 0.3, 30.))
@pytest.mark.parametrize('allow_throttle', (True, False))
def test_the_cruise_target_stays_inside_the_published_limits(experimental, error, allow_throttle):
  cp, speed = camry(), 20.
  floor, ceiling = cruise_accel_limits(experimental, speed, 0., cp, -0.3, allow_throttle)
  previous = 0.
  for _ in range(400):
    previous = get_cruise_accel(experimental, speed + error, speed, previous, 0., cp, .05, -0.3, allow_throttle)
  assert floor - 1e-6 <= previous <= ceiling + 1e-6
  assert previous == pytest.approx(min(max(previous, floor), ceiling))
  if abs(error) == 30.:
    assert previous == pytest.approx(ceiling if error > 0 else floor), 'a large error settles on the limit'
