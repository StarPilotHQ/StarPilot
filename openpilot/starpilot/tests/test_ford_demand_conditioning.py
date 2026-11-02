"""Planner authority and bounded smoothing across operating points."""
import math

import numpy as np
import pytest

from opendbc.car.ford.values import CAR

from openpilot.starpilot.car.ford.starpilot_controller import CurvatureConditioner
from openpilot.starpilot.tests.test_ford_starpilot_angle import controller, set_curve


@pytest.mark.parametrize('speed', [5., 15., 25., 35.])
@pytest.mark.parametrize('center', [-2., -.9, -.6, -.4, -.2, 0., .2, .4, .6, .9, 2.])
@pytest.mark.parametrize('frequency', [.25, .5, 1., 2., 4.])
def test_unclipped_sweeper_ripple_preserves_mean(speed, center, frequency):
  time = np.arange(0., 20., .05)
  target = (center + .04 * np.sin(2. * np.pi * frequency * time)) / speed ** 2
  filt = CurvatureConditioner()
  output = np.array([filt.update(float(value), speed) for value in target])
  settled = time >= 12.
  assert abs(float(np.mean(output[settled] - target[settled]))) * speed ** 2 < 1e-10
  assert np.max(np.abs(output - target)) * speed ** 2 <= .04 + 1e-12
  assert np.std(output[settled]) < np.std(target[settled])
  wave = np.exp(-2j * np.pi * frequency * time[settled])
  response = np.sum((output[settled] - center / speed ** 2) * wave) / np.sum((target[settled] - center / speed ** 2) * wave)
  expected = .5 / (1. - .5 * np.exp(-2j * np.pi * frequency * .05))
  assert response == pytest.approx(expected, abs=1e-10)


@pytest.mark.parametrize('sign', [-1., 1.])
@pytest.mark.parametrize('speed', [5., 15., 25., 35.])
def test_smoothing_step_and_exit_are_bounded_without_overshoot(sign, speed):
  filt = CurvatureConditioner()
  filt.update(0., speed)
  target = sign * 1.5 / speed ** 2
  previous = 0.
  for _ in range(40):
    output = filt.update(target, speed)
    assert min(previous, target) <= output <= max(previous, target)
    assert abs(output - target) * speed ** 2 <= .04 + 1e-12
    previous = output
  assert output == pytest.approx(target, abs=1e-12)
  for magnitude in np.linspace(1.5, .15, 40):
    target = sign * float(magnitude) / speed ** 2
    output = filt.update(target, speed)
    assert min(previous, target) <= output <= max(previous, target)
    assert abs(output - target) * speed ** 2 <= .04 + 1e-12
    previous = output
  assert filt.update(0., speed) == 0.
  assert filt.update(-sign * 1.5 / speed ** 2, speed) == -sign * 1.5 / speed ** 2


def test_speed_changes_do_not_expand_request_error_budget():
  filt = CurvatureConditioner()
  for index in range(1000):
    speed = 1. + 34. * (1. + math.sin(index / 7.)) / 2.
    target = .003 * math.sin(index / 13.)
    output = filt.update(target, speed)
    assert math.isfinite(output)
    assert abs(output - target) * max(speed, 1.) ** 2 <= .04 + 1e-12


@pytest.mark.parametrize('speed', [5., 15., 25., 35.])
@pytest.mark.parametrize('desired', [-.001, 0., .001])
@pytest.mark.parametrize('forecast', [-.01, 0., .01])
@pytest.mark.parametrize('car', [CAR.FORD_ESCAPE_MK4, CAR.FORD_F_150_MK14, CAR.FORD_MUSTANG_MACH_E_MK1])
@pytest.mark.parametrize('pinion', [False, True])
def test_forecast_disagreement_cannot_cancel_or_create_planner_demand(speed, desired, forecast, car, pinion):
  angle, cc, cs = controller(speed=speed, car=car, pinion=pinion)
  set_curve(angle, cc, cs, desired, model=forecast)
  for _ in range(30):
    result = angle.update_angle_strategy(cc, cs, cc.actuators)
  assert angle.bp_kappa_cmd == cc.actuators.curvature
  assert angle.bp_curvature_lookup_time == 0.
  if desired == 0.:
    assert result.path_angle == 0.
  else:
    assert result.path_angle * desired > 0.


@pytest.mark.parametrize('sign', [-1., 1.])
def test_planner_correction_has_unity_steady_gain_before_native_limits(sign):
  angle, cc, cs = controller(speed=20.)
  base, correction = sign * .001, sign * .0002
  set_curve(angle, cc, cs, base, model=base)
  for _ in range(30):
    angle.update_angle_strategy(cc, cs, cc.actuators)
  baseline = angle.bp_kappa_cmd
  base_request = cc.actuators.curvature
  set_curve(angle, cc, cs, base + correction, model=base)
  for _ in range(40):
    angle.update_angle_strategy(cc, cs, cc.actuators)
  assert angle.bp_kappa_cmd - baseline == pytest.approx(cc.actuators.curvature - base_request, abs=1e-12)
  assert not angle.enable_lane_positioning_ang
