"""Actual MPC composition and input immutability for optional curve ceilings."""

import math
import unittest

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.curve_speed.runtime import Frame, Runtime
from openpilot.starpilot.curve_speed.target import CurveProfile
from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling, CurveCeiling
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import ACTIVE, V_EGO, messages, message_bytes, snapshot


class Inputs(dict):
  def __init__(self, values, stamp):
    super().__init__(values)
    self.logMonoTime = {'modelV2': stamp}


class TestCurvePlanner(unittest.TestCase):
  def setUp(self):
    self.cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)

  def test_lower_curve_composes_with_slc_without_mutating_messages(self):
    for slc_speed, curve_speed, selected in ((None, 15.0, True), (17.0, 15.0, True), (13.0, 15.0, False), (15.0, 15.0, False)):
      with self.subTest(slc=slc_speed, curve=curve_speed):
        current = LongitudinalPlanner(self.cp, init_v=V_EGO)
        expected = LongitudinalPlanner(self.cp, init_v=V_EGO)
        for frame in range(40):
          stamp = (frame + 1) * 50_000_000
          fields, envelopes = messages()
          fields['carControl'].longActive = True
          sm = Inputs(fields, stamp)
          before = message_bytes(envelopes)
          slc = CruiseCeiling(slc_speed, ACTIVE) if slc_speed is not None else None
          current.update(sm, cruise_ceiling=slc, curve_ceiling=CurveCeiling(curve_speed, stamp))
          if selected:
            reference, _ = messages()
            reference['carControl'].longActive = True
            reference['carState'].vCruise = curve_speed * 3.6
            expected.update(Inputs(reference, stamp))
          else:
            expected.update(sm, cruise_ceiling=slc)
          self.assertEqual(message_bytes(envelopes), before)
          self.assertEqual(snapshot(current), snapshot(expected))
          self.assertEqual(current.last_curve_ceiling_status, 'selected' if selected else 'not_binding')
          self.assertEqual(current.last_curve_ceiling_applied, selected)
          self.assertEqual(current.mpc.solution_status, 0)

  def test_inactive_invalid_or_wrong_frame_curve_keeps_existing_plan(self):
    for condition in ('absent', 'nan', 'future', 'old', 'long_off', 'disabled', 'gas', 'brake', 'unset', 'force', 'stock_acc'):
      with self.subTest(condition=condition):
        cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
        if condition == 'stock_acc':
          cp.openpilotLongitudinalControl = False
        current, expected = (LongitudinalPlanner(cp, init_v=V_EGO) for _ in range(2))
        for frame in range(10):
          stamp = (frame + 1) * 50_000_000
          fields, _ = messages(disabled=condition == 'disabled', unset=condition == 'unset', force=condition == 'force')
          fields['carControl'].longActive = condition != 'long_off'
          fields['carState'].gasPressed = condition == 'gas'
          fields['carState'].brakePressed = condition == 'brake'
          sm = Inputs(fields, stamp)
          cap = CurveCeiling(math.nan if condition == 'nan' else 12.0, stamp + (1 if condition == 'future' else -1 if condition == 'old' else 0))
          current.update(sm, cruise_ceiling=CruiseCeiling(17.0, ACTIVE), curve_ceiling=None if condition == 'absent' else cap)
          expected.update(sm, cruise_ceiling=CruiseCeiling(17.0, ACTIVE))
          self.assertEqual(snapshot(current), snapshot(expected))
          self.assertFalse(current.last_curve_ceiling_applied)

  def test_lead_e2e_and_force_deceleration_cannot_be_attributed_to_curve(self):
    for case in ('lead', 'e2e', 'force'):
      with self.subTest(case=case):
        current = LongitudinalPlanner(self.cp, init_v=V_EGO)
        expected = LongitudinalPlanner(self.cp, init_v=V_EGO)
        for frame in range(40):
          stamp = (frame + 1) * 50_000_000
          fields, _ = messages(lead=case == 'lead', e2e=case == 'e2e', force=case == 'force')
          fields['carControl'].longActive = True
          sm = Inputs(fields, stamp)
          current.update(sm, curve_ceiling=CurveCeiling(17.0, stamp))
          expected.update(sm)
          self.assertEqual(current.output_a_target, expected.output_a_target)
          self.assertFalse(current.last_curve_ceiling_applied)

  def test_lower_or_equal_slc_never_confirms_curve_feedback(self):
    for slc_speed in (10.0, 11.176):
      with self.subTest(slc=slc_speed):
        runtime = Runtime(enabled=True)
        planner = LongitudinalPlanner(self.cp, init_v=V_EGO)
        for index in range(120):
          now = (index + 1) * 50_000_000
          profile = CurveProfile((0.02,), (0.0,), now)
          frame = Frame(now, now, True, profile, True, True, True, False, 100 / 3.6, V_EGO, 0.02, False, False, False, False)
          result = runtime.step(frame)
          fields, _ = messages()
          fields['carControl'].longActive = True
          sm = Inputs(fields, now)
          cap = CurveCeiling(result.ceiling_mps, now) if result.ceiling_mps is not None else None
          planner.update(sm, cruise_ceiling=CruiseCeiling(slc_speed, ACTIVE), curve_ceiling=cap)
          runtime.confirm_applied(now, applied=planner.last_curve_ceiling_applied)
          self.assertFalse(runtime.was_controlling)
        self.assertFalse(runtime.curve.dirty)
