"""Traffic profiles composed with the real native planner and acados solver."""

import shutil
import tempfile
import unittest
from dataclasses import replace

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling, CurveCeiling
from openpilot.starpilot.longitudinal.profile_document import default_personality_profiles, profile_document
from openpilot.starpilot.longitudinal.profile_runtime import read_traffic_settings, resolve
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import ACTIVE, V_EGO, message_bytes, messages
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR


class StampedMessages(dict):
  logMonoTime = {'modelV2': 1_000_000_000}


class TrafficAccelerationPlannerTests(unittest.TestCase):
  def setUp(self):
    root = tempfile.mkdtemp(prefix='traffic-planner-')
    self.addCleanup(shutil.rmtree, root, ignore_errors=True)
    self.params = Params(root)
    # These are planner-composition checks, independent of vehicle admission.
    self.cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)

  def tuning(self, *, acceleration=None, braking=None):
    if acceleration is not None or braking is not None:
      self.params.put_bool('CustomPersonalities', True, block=True)
      profiles = default_personality_profiles(False)
      for category, value in (('acceleration', acceleration), ('braking', braking)):
        if value is not None:
          profiles['traffic'][category] = {'preset': 'custom', 'curve': [value] * 10, 'legacyActivation': True}
      self.params.put('LongitudinalPersonalityProfiles', profile_document(profiles, enabled=True, global_braking_response='standard'), block=True)
    target = resolve(read_traffic_settings(self.params), log.LongitudinalPersonality.standard,
                     V_EGO, self.cp, traffic_mode=True)
    self.assertIsNotNone(target)
    return target

  def test_default_traffic_reaches_native_solver_without_mutating_messages(self):
    target = self.tuning()
    self.assertAlmostEqual(target.acceleration_max, 0.44)
    self.assertFalse(target.traffic_braking_custom)
    baseline = LongitudinalPlanner(self.cp, init_v=V_EGO)
    traffic = LongitudinalPlanner(self.cp, init_v=V_EGO)
    for _ in range(60):
      sm, envelopes = messages()
      sm['carControl'].longActive = True
      before = message_bytes(envelopes)
      baseline.update(sm)
      traffic.update(sm, profile_tuning=target, traffic_mode=True)
      self.assertEqual(message_bytes(envelopes), before)
      self.assertEqual(traffic.mpc.solution_status, 0)
    self.assertAlmostEqual(traffic.last_profile.acceleration_max, 0.44)
    self.assertLessEqual(traffic.mpc.params[0, 1], 0.44 + 1e-8)
    self.assertLessEqual(traffic.output_a_target, 0.44 + 1e-8)
    self.assertLess(traffic.a_cruise, baseline.a_cruise)

  def test_slc_cap_preserves_soft_traffic_response_and_native_force_decel(self):
    target = self.tuning(acceleration=0.6, braking=0.35)
    self.assertAlmostEqual(target.acceleration_max, 0.6)
    self.assertAlmostEqual(target.cruise_brake_magnitude, 0.35)
    for force in (False, True):
      with self.subTest(force=force):
        baseline = LongitudinalPlanner(self.cp, init_v=V_EGO)
        traffic = LongitudinalPlanner(self.cp, init_v=V_EGO)
        ceiling = CruiseCeiling(15.0, ACTIVE)
        for _ in range(60):
          sm, _ = messages(force=force)
          sm['carControl'].longActive = True
          traffic.update(sm, cruise_ceiling=ceiling, profile_tuning=target, traffic_mode=True)
          sm['carState'].vCruise = 15.0 * 3.6
          baseline.update(sm, profile_tuning=target, traffic_mode=True)
          self.assertEqual(traffic.mpc.solution_status, 0)
          self.assertLessEqual(traffic.a_cruise, baseline.a_cruise + 1e-8)
        self.assertEqual(traffic.last_cruise_ceiling_status, 'force_decel' if force else 'applied')
        self.assertIsNotNone(traffic.last_profile)
        self.assertAlmostEqual(traffic.last_profile.cruise_brake_magnitude, 0.35)
        self.assertLessEqual(traffic.a_cruise, (-0.5 if force else -0.35) + 1e-8)

  def test_soft_saved_traffic_preserves_lead_and_model_stop_candidates(self):
    target = self.tuning(acceleration=0.6, braking=0.5)
    for case in ('lead', 'e2e'):
      with self.subTest(case=case):
        traffic = LongitudinalPlanner(self.cp, init_v=V_EGO)
        for _ in range(60):
          sm, _ = messages(lead=case == 'lead', e2e=case == 'e2e')
          sm['carControl'].longActive = True
          traffic.update(sm, profile_tuning=target, traffic_mode=True)
          self.assertEqual(traffic.mpc.solution_status, 0)
        self.assertEqual(str(traffic.mpc.source), '1' if case == 'lead' else '4')
        self.assertLess(traffic.output_a_target, -0.5)
        if case == 'e2e':
          self.assertLessEqual(traffic.output_a_target, -2.0)
          self.assertTrue(traffic.output_should_stop)

  def test_unknown_traffic_or_lost_long_axis_clears_applied_curve_immediately(self):
    target = self.tuning()
    for unknown, long_active in ((True, True), (False, False)):
      with self.subTest(unknown=unknown, long_active=long_active):
        traffic = LongitudinalPlanner(self.cp, init_v=V_EGO)
        for _ in range(30):
          sm, _ = messages()
          sm['carControl'].longActive = True
          traffic.update(sm, profile_tuning=target, traffic_mode=True)
        self.assertAlmostEqual(traffic.last_profile.acceleration_max, 0.44)
        sm, _ = messages()
        sm['carControl'].longActive = long_active
        traffic.update(sm, profile_tuning=target, traffic_mode=None if unknown else True)
        self.assertIsNone(traffic.last_profile)
        self.assertIsNone(traffic.profile_smoother.applied)
        self.assertEqual(traffic.mpc.solution_status, 0)

  def test_saved_traffic_braking_category_uses_fixed_baseline_for_either_lead_or_standstill(self):
    for magnitude in (0.35, 2.0):
      target = self.tuning(braking=magnitude)
      self.assertTrue(target.traffic_braking_custom)
      no_lead = LongitudinalPlanner(self.cp, init_v=V_EGO)
      for _ in range(65):
        sm, _ = messages()
        sm['carControl'].longActive = True
        sm['carState'].vCruise = 50.0
        no_lead.update(sm, profile_tuning=target, traffic_mode=True)
      self.assertAlmostEqual(no_lead.last_profile.cruise_brake_magnitude, magnitude)
      self.assertAlmostEqual(no_lead.a_cruise, -magnitude)
      for hazard in ('leadOne', 'leadTwo', 'standstill'):
        with self.subTest(magnitude=magnitude, hazard=hazard):
          planner = LongitudinalPlanner(self.cp, init_v=V_EGO)
          baseline = LongitudinalPlanner(self.cp, init_v=V_EGO)
          baseline_target = replace(target, cruise_brake_magnitude=0.42, traffic_braking_custom=False)
          for _ in range(65):
            sm, _ = messages(lead=hazard in ('leadOne', 'leadTwo'))
            sm['carControl'].longActive = True
            sm['carState'].vCruise = 50.0
            if hazard == 'leadOne':
              sm['radarState'].leadTwo.present = False
            elif hazard == 'leadTwo':
              sm['radarState'].leadOne.present = False
            else:
              sm['carState'].standstill = True
            planner.update(sm, profile_tuning=target, traffic_mode=True)
            baseline.update(sm, profile_tuning=baseline_target, traffic_mode=True)
            self.assertEqual(planner.mpc.solution_status, 0)
          self.assertAlmostEqual(planner.last_profile.cruise_brake_magnitude, 0.42)
          self.assertAlmostEqual(planner.a_cruise, baseline.a_cruise, places=5)
          self.assertAlmostEqual(planner.output_a_target, baseline.output_a_target, places=5)
          if hazard != 'standstill':
            self.assertLessEqual(planner.output_a_target, planner.a_cruise + 1e-8)

  def test_traffic_braking_recovers_smoothly_and_unknown_clears_saved_category(self):
    target = self.tuning(braking=2.0)
    planner = LongitudinalPlanner(self.cp, init_v=V_EGO)
    for _ in range(65):
      sm, _ = messages()
      sm['carControl'].longActive = True
      sm['carState'].vCruise = 50.0
      planner.update(sm, profile_tuning=target, traffic_mode=True)
    self.assertAlmostEqual(planner.last_profile.cruise_brake_magnitude, 2.0)
    sm, _ = messages(lead=True)
    sm['carControl'].longActive = True
    sm['carState'].vCruise = 50.0
    planner.update(sm, profile_tuning=target, traffic_mode=True)
    self.assertAlmostEqual(planner.last_profile.cruise_brake_magnitude, 0.42)
    recovered = []
    for _ in range(16):
      sm, _ = messages()
      sm['carControl'].longActive = True
      sm['carState'].vCruise = 50.0
      planner.update(sm, profile_tuning=target, traffic_mode=True)
      recovered.append(planner.last_profile.cruise_brake_magnitude)
    self.assertAlmostEqual(recovered[0], 0.42 + 2.0 * planner.dt)
    self.assertTrue(all(a <= b for a, b in zip(recovered, recovered[1:], strict=False)))
    self.assertAlmostEqual(recovered[-1], 2.0)
    sm, _ = messages(lead=True)
    sm['carControl'].longActive = True
    planner.update(sm, profile_tuning=target, traffic_mode=True)
    self.assertAlmostEqual(planner.last_profile.cruise_brake_magnitude, 0.42)
    sm, _ = messages(lead=True)
    sm['carControl'].longActive = True
    planner.update(sm, traffic_mode=False)
    self.assertGreater(planner.last_profile.cruise_brake_magnitude, 0.42)
    self.assertAlmostEqual(planner.last_profile.cruise_brake_magnitude, 0.42 + 2.0 * planner.dt)
    sm, _ = messages(lead=True)
    sm['carControl'].longActive = True
    planner.update(sm, profile_tuning=target, traffic_mode=True)
    self.assertAlmostEqual(planner.last_profile.cruise_brake_magnitude, 0.42)
    sm, _ = messages()
    sm['carControl'].longActive = True
    planner.update(sm, profile_tuning=target, traffic_mode=None)
    self.assertIsNone(planner.last_profile)
    self.assertIsNone(planner.profile_smoother.applied)

  def test_traffic_hazard_preserves_native_stop_override_and_lower_ceilings(self):
    target = self.tuning(braking=0.35)
    for case in ('model_stop', 'force_decel', 'brake_override', 'slc', 'curve'):
      with self.subTest(case=case):
        planner = LongitudinalPlanner(self.cp, init_v=V_EGO)
        baseline = LongitudinalPlanner(self.cp, init_v=V_EGO)
        baseline_target = replace(target, cruise_brake_magnitude=0.42, traffic_braking_custom=False)
        for _ in range(65):
          sm, _ = messages(lead=True, e2e=case == 'model_stop', force=case == 'force_decel')
          sm['carControl'].longActive = True
          sm['carState'].vCruise = 100.0
          sm['carState'].brakePressed = case == 'brake_override'
          cruise_ceiling = CruiseCeiling(15.0, ACTIVE) if case == 'slc' else None
          curve_ceiling = CurveCeiling(15.0, 1_000_000_000) if case == 'curve' else None
          if case == 'curve':
            sm = StampedMessages(sm)
          planner.update(sm, profile_tuning=target, traffic_mode=True,
                         cruise_ceiling=cruise_ceiling, curve_ceiling=curve_ceiling)
          baseline.update(sm, profile_tuning=baseline_target, traffic_mode=True,
                          cruise_ceiling=cruise_ceiling, curve_ceiling=curve_ceiling)
          self.assertEqual(planner.mpc.solution_status, 0)
        self.assertAlmostEqual(planner.output_a_target, baseline.output_a_target, places=5)
        if case == 'curve':
          self.assertLessEqual(planner.a_cruise, -0.5 + 1e-8)
        elif case == 'slc':
          self.assertLessEqual(planner.a_cruise, -0.42 + 1e-8)
        elif case == 'force_decel':
          self.assertLessEqual(planner.a_cruise, -0.5 + 1e-8)
        elif case == 'model_stop':
          self.assertEqual(str(planner.mpc.source), '4')
          self.assertTrue(planner.output_should_stop)
        else:
          self.assertEqual(planner.last_curve_ceiling_status, 'absent')


if __name__ == '__main__':
  unittest.main()
