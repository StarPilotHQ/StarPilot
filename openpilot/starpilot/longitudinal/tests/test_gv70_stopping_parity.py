"""Electrified GV70 stop forecast and ramp through production planner/control callers."""
import unittest
from unittest.mock import patch

import numpy as np
from opendbc.car import structs
from opendbc.car.hyundai.values import CAR
from openpilot.selfdrive.controls.lib import longitudinal_planner as planner_module
from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState
from openpilot.starpilot.longitudinal.force_stop import StopPlan
from openpilot.starpilot.longitudinal.vehicle_policy import forecast_should_stop, stopping_decel_rate
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
from openpilot.starpilot.tests.test_ev6_startup import params


class TestGV70StoppingParity(unittest.TestCase):
  def cp(self, alpha=True):
    return params(alpha=alpha, car=CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN)

  def planner(self, speed):
    planner = planner_module.LongitudinalPlanner(self.cp(), init_v=1.)

    def trajectory(*args, **kwargs):
      planner.mpc.v_solution[:] = speed
      planner.mpc.a_solution[:] = 0.
      planner.mpc.j_solution[:] = 0.

    self.enterContext(patch.object(planner.mpc, 'update', trajectory, create=True))
    return planner

  def test_actual_factory_and_controller_restore_platform_ramp(self):
    cp = self.cp()
    self.assertTrue(cp.openpilotLongitudinalControl)
    self.assertFalse(cp.pcmCruise)
    before = cp.to_dict()
    controller = LongControl(cp)
    self.assertEqual(controller.stopping_decel_rate, .4)
    cs = structs.CarState()
    cs.vEgo = .2
    for tick in range(1, 11):
      result = controller.update(True, cs, -.2, True, (-3.5, 2.))
      self.assertEqual(controller.long_control_state, LongCtrlState.stopping)
      self.assertAlmostEqual(result, -.004 * tick)
    self.assertEqual(controller.update(False, cs, -.2, True, (-3.5, 2.)), 0.)
    self.assertEqual(cp.to_dict(), before)

  def test_both_forecasts_use_original_strict_float32_boundary(self):
    cp = self.cp()
    times = (0., .5, 1., 1.5, 2.)
    threshold = float(np.float32(.3))
    for speeds, action, expected in (
      ((.2,) * 5, .5, True), ((threshold,) * 5, .5, False),
      ((np.nextafter(threshold, -np.inf),) * 5, .5, True),
      ((np.nextafter(threshold, np.inf),) * 5, .5, False),
      ((.2, .2, .2, threshold, .2), .5, False),
      ((.2, threshold, .2, .2, .2), .5, False),
      ((.1, .2, .4, .2, .1), .25, True),
    ):
      with self.subTest(speeds=speeds, action=action):
        self.assertEqual(forecast_should_stop(cp, speeds, times, action, False), expected)

  def test_missing_or_insufficient_forecasts_preserve_existing_fallback(self):
    cp = self.cp()
    for speeds, times in (((), ()), ((), (0., 2.)), ((.2,), (0.,)), ((.2, .2), (0.,)), ((.2,), (0., 2.))):
      for fallback in (False, True):
        with self.subTest(speeds=speeds, times=times, fallback=fallback):
          self.assertEqual(forecast_should_stop(cp, speeds, times, .5, fallback), fallback)

  def test_stock_ineligible_keep_fallback_and_other_hkg_gain_forecast(self):
    base = self.cp()
    candidates = [self.cp(False), params(car=CAR.KIA_EV6)]
    for car in (CAR.GENESIS_GV70_ELECTRIFIED_2ND_GEN, CAR.GENESIS_GV70_1ST_GEN):
      cp = base.as_reader().as_builder()
      cp.carFingerprint = car
      candidates.append(cp)
    for field in ('pcmCruise', 'passive', 'dashcamOnly', 'notCar'):
      cp = base.as_reader().as_builder()
      setattr(cp, field, True)
      candidates.append(cp)
    for cp in candidates:
      self.assertEqual(stopping_decel_rate(cp, None), 1.)
      eligible = cp.openpilotLongitudinalControl and not any(
        getattr(cp, key) for key in ('pcmCruise', 'passive', 'dashcamOnly', 'notCar'))
      for fallback in (False, True):
        self.assertEqual(forecast_should_stop(cp, (.2, .2), (0., 2.), .5, fallback), True if eligible else fallback)

  def test_actual_planner_forecast_changes_only_stop_intent(self):
    sm, _ = messages()
    sm['carState'].vEgo = 1.
    actual, baseline = self.planner(.2), self.planner(.2)
    actual.update(sm)
    with patch.object(planner_module, 'forecast_should_stop', side_effect=lambda *args, **kw: kw['fallback']):
      baseline.update(sm)
    self.assertTrue(actual.output_should_stop)
    self.assertFalse(baseline.output_should_stop)
    self.assertEqual(actual.output_a_target, baseline.output_a_target)
    self.assertEqual(actual.a_cruise, baseline.a_cruise)
    np.testing.assert_array_equal(actual.v_desired_trajectory, baseline.v_desired_trajectory)

  def test_model_false_and_nonstopping_forecast_does_not_acquire_stop(self):
    sm, _ = messages()
    sm['carState'].vEgo = 4.223090171813965
    sm['modelV2'].action.shouldStop = False
    planner = self.planner(4.2)
    planner.update(sm)
    self.assertFalse(planner.output_should_stop)

  def test_actual_planner_preserves_e2e_and_force_stop(self):
    for e2e, force in ((True, False), (False, True)):
      sm, _ = messages(e2e=e2e)

      class CurrentMessages(dict):
        logMonoTime = {'modelV2': 100}

      sm = CurrentMessages(sm)
      sm['carState'].vEgo = 1.
      sm['carControl'].longActive = True
      planner = self.planner(.4)
      planner.update(sm, force_stop_provider=(lambda _: StopPlan(model_ns=100, should_stop=True)) if force else None)
      self.assertTrue(planner.output_should_stop)
