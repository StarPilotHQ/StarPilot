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

  def update(self, planner, frame, cruise_kph, *, gas=False, lead=False, force=False):
    sm, _ = messages(lead=lead, force=force)
    sm['carState'].vCruise = cruise_kph
    sm['carState'].canValid = True
    sm['carState'].gasPressed = gas
    sm['carControl'].longActive = not gas
    idle_coast = Mock(sample=Mock(return_value=CoastPlan()))
    planner.update(sm, now_ns=1_000_000_000 + frame * 50_000_000, wheel_coast=(idle_coast, None))

  def test_manual_up_and_down(self):
    for speed in (65, 79):
      with self.subTest(speed=speed):
        planner = self.setup_planner()
        for frame in range(20):
          self.update(planner, frame, 72)
        previous = planner.a_cruise
        self.update(planner, 20, speed)
        self.assertGreater(abs(planner.a_cruise - previous), 0.0)
        self.assertLessEqual(abs(planner.a_cruise - previous), 0.023)
        self.assertLessEqual(abs(planner.output_a_target - previous), 0.023)

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
