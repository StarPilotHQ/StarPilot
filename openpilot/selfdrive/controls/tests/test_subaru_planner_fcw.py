import unittest

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import should_trigger_planner_fcw


class TestSubaruPlannerFcw(unittest.TestCase):
  def test_recorded_distant_follow_and_collision_boundaries(self):
    for present, probability, distance, lead_speed, ego_speed, expected in (
      (True, .99, 97.49, 25.63, 26.70, False),
      (True, .99, 15., 10., 20., True),
      (True, .99, 5., 20., 20., False),
      (True, .99, 5., 21., 20., False),
      (False, .99, 2., 0., 20., False),
      (True, .9, 5., 0., 20., False),
      (True, .99, 40., 10., 20., False),
      (True, .99, 39.99, 10., 20., True),
      (True, .99, 1., 19.51, 20., False),
      (True, .99, 1., 19.5, 20., True),
    ):
      with self.subTest(distance=distance, lead_speed=lead_speed, probability=probability):
        radar = log.RadarState.new_message()
        radar.leadOne.present = present
        radar.leadOne.modelProb = probability
        radar.leadOne.dRel = distance
        radar.leadOne.vLead = lead_speed
        self.assertEqual(should_trigger_planner_fcw(radar.leadOne, ego_speed), expected)
    self.assertFalse(should_trigger_planner_fcw(None, 20.))


def test_actual_planner_impreza_keeps_subaru_following_guard_and_imminent_collision_warning():
  from unittest.mock import patch
  from opendbc.car.subaru.interface import CarInterface
  from opendbc.car.subaru.values import CAR
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.selfdrive.controls.tests.test_curve_runtime_loop import bus, NOW

  with OpenpilotPrefix():
    for car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025):
      cp = CarInterface.get_non_essential_params(car)
      assert not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
      for distance, lead_speed, expected in ((97.49, 25.63, False), (15., 10., True)):
        planner = LongitudinalPlanner(cp, init_v=26.70)
        sm = bus(NOW, lead_present=True, lead_distance=distance)
        radar = sm.messages['radarState'].as_builder()
        radar.leadOne.vLead = lead_speed
        sm.messages['radarState'] = radar.as_reader()
        control = sm.messages['carControl'].as_builder()
        control.longActive = False
        sm.messages['carControl'] = control.as_reader()
        original = planner.mpc.update
        def predicted_collision(*args, _original=original, _planner=planner, **kwargs):
          _original(*args, **kwargs)
          _planner.mpc.crash_cnt = 3
        with patch.object(planner.mpc, 'update', side_effect=predicted_collision):
          planner.update(sm)
        assert planner.fcw == expected, (car, distance, lead_speed)
