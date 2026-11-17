"""Dom stopping thresholds across HKG; preserve the current planner acceleration."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.selfdrive.controls.lib import longitudinal_planner as planner_module
from openpilot.starpilot.longitudinal.vehicle_policy import forecast_should_stop
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages


class TestHKGForecastStop(unittest.TestCase):
  def cp(self, candidate):
    return SimpleNamespace(carFingerprint=candidate, openpilotLongitudinalControl=True, flags=candidate.config.flags,
                           pcmCruise=False, passive=False, dashcamOnly=False, notCar=False)

  def test_all_hkg_platform_thresholds_and_release(self):
    overrides = {CAR.GENESIS_G90: .8, CAR.HYUNDAI_PALISADE_2023: .35, CAR.KIA_NIRO_PHEV_2022: .7}
    for candidate in CAR:
      cp = self.cp(candidate)
      expected = float(np.float32(overrides.get(candidate, .3 if cp.flags & HyundaiFlags.CANFD else .5)))
      for future_speed, expected_stop in ((np.nextafter(expected, -np.inf), True), (expected, False),
                                           (np.nextafter(expected, np.inf), False)):
        with self.subTest(car=candidate, speed=future_speed):
          self.assertEqual(forecast_should_stop(cp, [future_speed] * 3, [0., 1., 2.], .5, False), expected_stop)
      with self.subTest(car=candidate, release=True):
        self.assertFalse(forecast_should_stop(cp, [0., 0., expected * 3], [0., 1., 2.], .5, True))

  def test_stock_foreign_ineligible_and_invalid_keep_fallback(self):
    for changes in ({'openpilotLongitudinalControl': False}, {'carFingerprint': 'TOYOTA_RAV4'},
                    {'pcmCruise': True}, {'passive': True}, {'dashcamOnly': True}, {'notCar': True}):
      cp = self.cp(CAR.HYUNDAI_SONATA)
      for name, value in changes.items():
        setattr(cp, name, value)
      for fallback in (False, True):
        self.assertEqual(forecast_should_stop(cp, [0., 0.], [0., 2.], .5, fallback), fallback)
    for speeds, times in (([], []), ([0.], [0.]), ([0., 0.], [0.])):
      for fallback in (False, True):
        self.assertEqual(forecast_should_stop(self.cp(CAR.HYUNDAI_SONATA), speeds, times, .5, fallback), fallback)

  def test_actual_planner_forecast_changes_only_stop_intent(self):
    candidates = (CAR.HYUNDAI_SONATA, CAR.HYUNDAI_SONATA_HYBRID, CAR.HYUNDAI_SONATA_HEV_2024,
                  CAR.KIA_EV6, CAR.KIA_EV9, CAR.HYUNDAI_IONIQ_5_PE, CAR.GENESIS_G90,
                  CAR.HYUNDAI_PALISADE_2023, CAR.KIA_NIRO_PHEV_2022, CAR.KIA_STINGER_2022)
    for candidate in candidates:
      with self.subTest(car=candidate):
        cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], True, False, False)
        # Exercise the planner with an admitted OP-long profile; availability remains interface-owned.
        cp.openpilotLongitudinalControl = True
        cp.pcmCruise = False
        cp.passive = cp.dashcamOnly = cp.notCar = False
        actual = planner_module.LongitudinalPlanner(cp, init_v=1.)
        baseline = planner_module.LongitudinalPlanner(cp, init_v=1.)
        def trajectory(planner):
          def update(*args, **kwargs):
            planner.mpc.v_solution[:] = .2
            planner.mpc.a_solution[:] = 0.
            planner.mpc.j_solution[:] = 0.
          return update
        sm, _ = messages()
        sm['carState'].vEgo = 1.
        with patch.object(actual.mpc, 'update', trajectory(actual), create=True):
          actual.update(sm)
        with patch.object(baseline.mpc, 'update', trajectory(baseline), create=True), \
             patch.object(planner_module, 'forecast_should_stop', side_effect=lambda *args, **kw: kw['fallback']):
          baseline.update(sm)
        self.assertTrue(actual.output_should_stop, str(candidate))
        self.assertFalse(baseline.output_should_stop)
        self.assertEqual(actual.output_a_target, baseline.output_a_target)
        self.assertEqual(actual.a_cruise, baseline.a_cruise)
        np.testing.assert_array_equal(actual.v_desired_trajectory, baseline.v_desired_trajectory)
