"""The saved global braking response changes only the qualified cruise candidate."""

import unittest
from unittest.mock import patch

from openpilot.selfdrive.controls.lib import longitudinal_planner as planner_module
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import update_curve_frame
from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling
from openpilot.starpilot.longitudinal.profile_runtime import ProfileTuning
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import ACTIVE, V_EGO, messages, snapshot
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR


class GlobalBrakingTests(unittest.TestCase):
  def setUp(self):
    self.cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)

  def run_planner(self, response=None, *, ceiling=None, lead=False, stop=False, force=False,
                  profile=None, traffic=False):
    planner = LongitudinalPlanner(self.cp, init_v=V_EGO)
    with patch.object(planner_module, 'get_cruise_accel', wraps=planner_module.get_cruise_accel) as cruise:
      for _ in range(65):
        sm, _ = messages(lead=lead, force=force)
        sm['carState'].vCruise = 100.0 if ceiling is not None else 50.0
        sm['carControl'].longActive = True
        sm['modelV2'].action.shouldStop = stop
        update_curve_frame(planner, sm, self.cp, 1_000_000_000,
                           cruise_ceiling=ceiling, profile_tuning=profile, traffic_mode=traffic,
                           global_braking_response=response)
        self.assertEqual(planner.mpc.solution_status, 0)
    return planner, cruise.call_args.kwargs

  def test_real_cruise_candidate_uses_global_floor_without_changing_native_mpc(self):
    ordinary, _ = self.run_planner()
    standard, _ = self.run_planner('standard')
    eco, _ = self.run_planner('eco')
    sport, _ = self.run_planner('sport')
    # The upstream native floor is independent of StarPilot's named presets.
    self.assertAlmostEqual(ordinary.a_cruise, -0.5)
    self.assertAlmostEqual(standard.a_cruise, -1.2)
    self.assertAlmostEqual(eco.a_cruise, -0.6)
    self.assertAlmostEqual(sport.a_cruise, -2.4)
    self.assertGreater(eco.a_cruise, standard.a_cruise)
    self.assertLess(sport.a_cruise, standard.a_cruise)
    for planner in (ordinary, standard, eco, sport):
      self.assertEqual(planner.output_a_target, planner.a_cruise)
      self.assertEqual(str(planner.mpc.source), '0')
      self.assertFalse(planner.output_should_stop)
      self.assertIsNone(planner.last_profile)
      self.assertEqual(planner.mpc._cached_cost_weights, ordinary.mpc._cached_cost_weights)
      self.assertEqual(planner.mpc.params[:, [0, 1, 4, 5]].tolist(),
                       ordinary.mpc.params[:, [0, 1, 4, 5]].tolist())

  def test_ceiling_hazards_and_category_override(self):
    ceiling = CruiseCeiling(18.0, ACTIVE)  # 2 m/s over: the halved SLC correction (-1.0) still reaches the eco floor (-0.6)
    eco, cruise = self.run_planner('eco', ceiling=ceiling)
    self.assertEqual(cruise['base_brake_floor'], -0.6)
    self.assertAlmostEqual(eco.a_cruise, -0.6)
    lead, _ = self.run_planner('eco', ceiling=ceiling, lead=True)
    self.assertLess(lead.output_a_target, lead.a_cruise)
    native_lead, _ = self.run_planner(ceiling=ceiling, lead=True)
    sport_lead, _ = self.run_planner('sport', ceiling=ceiling, lead=True)
    self.assertEqual(snapshot(lead), snapshot(native_lead))
    self.assertEqual(snapshot(sport_lead), snapshot(native_lead))
    for kwargs in ({'stop': True}, {'force': True}):
      with self.subTest(kwargs=kwargs):
        protected, cruise = self.run_planner('eco', ceiling=ceiling, **kwargs)
        native, _ = self.run_planner(ceiling=ceiling, **kwargs)
        self.assertEqual(cruise['base_brake_floor'], planner_module.A_CRUISE_MIN)
        self.assertEqual(snapshot(protected), snapshot(native))
    selected = ProfileTuning('standard', 1.45, 1.0, 1.0, 1.0, 1.0, 1.0,
                             cruise_brake_magnitude=1.0)
    baseline, _ = self.run_planner(profile=selected, ceiling=ceiling)
    with_global, _ = self.run_planner('sport', profile=selected, ceiling=ceiling)
    self.assertEqual(snapshot(baseline), snapshot(with_global))

  def test_inactive_and_traffic_never_consume_global_choice(self):
    ordinary, _ = self.run_planner()
    traffic, cruise = self.run_planner('sport', traffic=True)
    ordinary_traffic, _ = self.run_planner(traffic=True)
    self.assertEqual(snapshot(traffic), snapshot(ordinary_traffic))
    self.assertEqual(cruise['base_brake_floor'], planner_module.A_CRUISE_MIN)
    self.assertEqual(snapshot(ordinary), snapshot(self.run_planner('unknown')[0]))
    self.assertEqual(snapshot(ordinary), snapshot(self.run_planner([])[0]))


if __name__ == '__main__':
  unittest.main()
