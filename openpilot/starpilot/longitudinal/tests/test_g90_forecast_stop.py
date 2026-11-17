"""G90 forecast stop intent; current acceleration and stop arbitration remain intact."""
from unittest.mock import patch

import unittest
from types import SimpleNamespace

import numpy as np
from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.selfdrive.controls.lib import longitudinal_planner as planner_module
from openpilot.starpilot.longitudinal.force_stop import StopPlan
from openpilot.starpilot.longitudinal.vehicle_policy import forecast_should_stop
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages


class TestG90ForecastStop(unittest.TestCase):
  def test_both_forecasts_strict_boundary_and_delay(self):
    cp = SimpleNamespace(carFingerprint=CAR.GENESIS_G90, openpilotLongitudinalControl=True)
    times = (0., .5, 1., 1.5, 2.)
    threshold = float(np.float32(.8))
    for speeds, action, expected in (
      ((.7,) * 5, .5, True), ((.8,) * 5, .5, True),
      ((threshold,) * 5, .5, False),
      ((np.nextafter(threshold, -np.inf),) * 5, .5, True),
      ((np.nextafter(threshold, np.inf),) * 5, .5, False),
      ((.7, .7, .7, threshold, .7), .5, False),
      ((.7, threshold, .7, .7, .7), .5, False),
      ((.5, .7, .9, .7, .5), .25, True),
      ((.5, .7, .9, .7, .5), .5, True),
      ((), .5, True),
    ):
      with self.subTest(speeds=speeds, action=action):
        self.assertEqual(forecast_should_stop(cp, speeds, times, action, False), expected)

  def test_stock_and_other_cars_retain_actual_fallback(self):
    for car, owned in ((CAR.GENESIS_G90, False), ('TOYOTA_RAV4', True)):
      cp = SimpleNamespace(carFingerprint=car, openpilotLongitudinalControl=owned)
      for fallback in (False, True):
        self.assertEqual(forecast_should_stop(cp, (.7, .7), (0., 2.), .5, fallback), fallback)

  def planner(self, speed):
    cp = CarInterface.get_params(CAR.GENESIS_G90, gen_empty_fingerprint(), [], True, False, False)
    self.assertTrue(cp.openpilotLongitudinalControl)
    planner = planner_module.LongitudinalPlanner(cp, init_v=1.)
    # Replace only the solver's trajectory production, leaving the real planner
    # acceleration calculation, smoothing and stop candidate arbitration reached.
    def trajectory(*args, **kwargs):
      planner.mpc.v_solution[:] = speed
      planner.mpc.a_solution[:] = 0.
      planner.mpc.j_solution[:] = 0.
    self.enterContext(patch.object(planner.mpc, 'update', trajectory, create=True))
    return planner

  def test_actual_planner_forecast_changes_only_stop_intent(self):
    sm, _ = messages()
    sm['carState'].vEgo = 1.
    actual, baseline = self.planner(.7), self.planner(.7)
    actual.update(sm)
    with patch.object(planner_module, 'forecast_should_stop', side_effect=lambda *args, **kw: kw['fallback']):
      baseline.update(sm)
    self.assertTrue(actual.output_should_stop)
    self.assertFalse(baseline.output_should_stop)
    self.assertEqual(actual.output_a_target, baseline.output_a_target)
    self.assertEqual(actual.a_cruise, baseline.a_cruise)
    np.testing.assert_array_equal(actual.v_desired_trajectory, baseline.v_desired_trajectory)

  def test_actual_planner_preserves_e2e_and_force_stop(self):
    for e2e, force in ((True, False), (False, True)):
      sm, _ = messages(e2e=e2e)
      class CurrentMessages(dict):
        logMonoTime = {'modelV2': 100}
      sm = CurrentMessages(sm)
      sm['carState'].vEgo = 1.
      sm['carControl'].longActive = True
      planner = self.planner(.9)
      planner.update(sm, force_stop_provider=(lambda _: StopPlan(model_ns=100, should_stop=True)) if force else None)
      self.assertTrue(planner.output_should_stop)
