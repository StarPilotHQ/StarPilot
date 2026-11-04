import unittest
from types import SimpleNamespace

from openpilot.starpilot.lateral.model_turn_assist import (
  CURVATURE_HOLD_CONFIRM_MIN, ModelTurnAssist, TURN_LEAD_CAP,
)


def turn_sample(direction=1, curvature=.004, acceleration=-3., active=True, speed=6., lane_change=0):
  xs = list(range(31))
  model = SimpleNamespace(position=SimpleNamespace(x=xs, y=[direction * .025 * x*x for x in xs]),
                          meta=SimpleNamespace(laneChangeState=lane_change))
  cs = SimpleNamespace(vEgo=speed, aEgo=acceleration, rightBlinker=direction == 1,
                       leftBlinker=direction == -1, steeringPressed=False, steeringTorque=0.)
  assist = ModelTurnAssist()
  assist.curvature = direction * curvature
  return assist.update(cs, model, SimpleNamespace(latActive=active), direction * .005)


class TestTurnAssistBraking(unittest.TestCase):
  def test_braking_does_not_remove_assistance_during_established_turn(self):
    for direction in (-1, 1):
      for acceleration in (-1., -3., -6.):
        with self.subTest(direction=direction, acceleration=acceleration):
          output = turn_sample(direction, acceleration=acceleration)
          self.assertGreater(output * direction, .005)
          self.assertEqual(output, turn_sample(direction, acceleration=0.))
          self.assertLessEqual(abs(output), TURN_LEAD_CAP)

  def test_stopping_before_turn_keeps_original_request(self):
    for direction in (-1, 1):
      for curvature in (0., CURVATURE_HOLD_CONFIRM_MIN, -.004):
        with self.subTest(direction=direction, curvature=curvature):
          self.assertEqual(turn_sample(direction, curvature=curvature), direction * .005)
      self.assertGreater(turn_sample(direction, curvature=0., acceleration=0.) * direction, .005)

  def test_lateral_inactive_or_lane_change_never_adds_turn_lead(self):
    for direction in (-1, 1):
      self.assertEqual(turn_sample(direction, active=False), direction * .005)
      self.assertEqual(turn_sample(direction, lane_change=1), direction * .005)

  def test_turn_lead_speed_bounds_are_unchanged(self):
    for direction in (-1, 1):
      for speed in (3., 7., 8.):
        with self.subTest(direction=direction, speed=speed):
          self.assertEqual(turn_sample(direction, speed=speed), direction * .005)

  def test_opposing_model_and_no_signal_keep_request(self):
    for direction in (-1, 1):
      for signal in (False, True):
        assist = ModelTurnAssist()
        assist.curvature = direction * .01
        cs = SimpleNamespace(vEgo=6., aEgo=-3., leftBlinker=signal and direction == -1,
                             rightBlinker=signal and direction == 1, steeringPressed=False, steeringTorque=0.)
        model = SimpleNamespace(position=SimpleNamespace(x=[0., 10.], y=[0., -direction * 3.]),
                                meta=SimpleNamespace(laneChangeState=0))
        desired = -direction * .01
        self.assertEqual(assist.update(cs, model, SimpleNamespace(latActive=True), desired), desired)


if __name__ == '__main__':
  unittest.main()
