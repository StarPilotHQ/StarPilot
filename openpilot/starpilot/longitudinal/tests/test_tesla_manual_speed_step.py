import unittest
from unittest.mock import Mock

from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.controllers.coast import CoastPlan
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import V_EGO, messages
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.values import CAR


class TestTeslaManualSpeedStep(unittest.TestCase):
  def setup_planner(self):
    cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_Y)
    cp.openpilotLongitudinalControl = True
    return LongitudinalPlanner(cp, init_v=V_EGO)

  def update(self, planner, frame, cruise_kph, *, gas=False, lead=False, force=False, accel=0.0, brake=False, off=False):
    sm, _ = messages(lead=lead, force=force)
    sm['carState'].vCruise = cruise_kph
    sm['carState'].canValid = True
    sm['carState'].gasPressed = gas
    sm['carState'].brakePressed = brake
    sm['carState'].aEgo = accel
    sm['carState'].cruiseState.enabled = True
    if gas or off:
      sm['controlsState'].longControlState = 'off'
    sm['carControl'].longActive = not (gas or off)
    idle_coast = Mock(sample=Mock(return_value=CoastPlan()))
    planner.update(sm, now_ns=1_000_000_000 + frame * 50_000_000, wheel_coast=(idle_coast, None))

  def test_pedal_handoff_previews_only_eligible_cruise_acceleration(self):
    planner = self.setup_planner()
    self.update(planner, 0, 95, gas=True, accel=0.10)
    preview = float(planner.output_a_target)
    self.assertGreater(preview, 0.30)
    self.assertLess(preview, 0.42)
    self.update(planner, 1, 95, accel=0.10)
    self.assertGreaterEqual(planner.output_a_target, preview)

    for context in ({'lead': True}, {'force': True}, {'brake': True}):
      with self.subTest(context=context):
        planner = self.setup_planner()
        self.update(planner, 0, 95, gas=True, accel=0.10, **context)
        self.assertLess(planner.output_a_target, 0.20)

  def test_pedal_handoff_preserves_preview_through_near_zero_coast(self):
    for measured_accel, preview_min, preview_max in ((-0.02, 0.17, 0.26), (-0.09, 0.04, 0.12), (-0.30, -0.30, -0.20)):
      with self.subTest(measured_accel=measured_accel):
        planner = self.setup_planner()
        self.update(planner, 0, 95, gas=True, accel=measured_accel)
        preview = float(planner.output_a_target)
        self.assertGreater(preview, preview_min)
        self.assertLess(preview, preview_max)

  def test_pedal_handoff_bridges_one_inactive_frame(self):
    planner = self.setup_planner()
    self.update(planner, 0, 95, gas=True, accel=-0.02)
    self.update(planner, 1, 95, off=True, accel=-0.02)
    self.assertGreater(planner.output_a_target, 0.17)
    self.update(planner, 2, 95, off=True, accel=-0.02)
    self.assertLess(planner.output_a_target, 0.05)

    for frame, blocker in ((1, {'lead': True}), (1, {'brake': True}), (3, {})):
      with self.subTest(frame=frame, blocker=blocker):
        planner = self.setup_planner()
        self.update(planner, 0, 95, gas=True, accel=-0.02)
        self.update(planner, frame, 95, off=True, accel=-0.02, **blocker)
        self.assertLess(planner.output_a_target, 0.05)

  def test_manual_up_and_down(self):
    for speed in (65, 79):
      with self.subTest(speed=speed):
        planner = self.setup_planner()
        for frame in range(20):
          self.update(planner, frame, 72)
        previous = planner.a_cruise
        self.update(planner, 20, speed)
        self.assertGreater(abs(planner.a_cruise - previous), 0.0)
        self.assertLessEqual(abs(planner.a_cruise - previous), 0.013)
        self.assertLessEqual(abs(planner.output_a_target - previous), 0.013)

  def test_small_positive_accel_eases_into_manual_slowdown(self):
    planner = self.setup_planner()
    self.update(planner, 0, 72)
    planner.a_cruise = planner.output_a_target = 0.08
    self.update(planner, 1, 65)
    self.assertAlmostEqual(planner.output_a_target, 0.0675)
    self.assertGreater(planner.manual_cruise_step_until_ns, 4_000_000_000)

  def test_pedal_lead_and_forced_decel_cancel_ramp(self):
    for blocker in ('gas', 'lead', 'force'):
      with self.subTest(blocker=blocker):
        planner = self.setup_planner()
        for frame in range(20):
          self.update(planner, frame, 72)
        self.update(planner, 20, 79)
        self.assertGreater(planner.manual_cruise_step_until_ns, 0)
        self.update(planner, 21, 79, **{blocker: True})
        self.assertEqual(planner.manual_cruise_step_until_ns, 0)
        if blocker != 'gas':
          for frame in range(22, 32):
            self.update(planner, frame, 79, **{blocker: True})
          self.assertLess(planner.output_a_target, 0.0)
