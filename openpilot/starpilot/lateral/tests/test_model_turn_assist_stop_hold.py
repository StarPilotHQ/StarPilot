import unittest
from types import SimpleNamespace

from openpilot.starpilot.lateral.model_turn_assist import STOP_HOLD_MAX_SPEED, ModelTurnAssist

STRAIGHT = SimpleNamespace(position=SimpleNamespace(x=[0., 1., 2., 3.], y=[0., 0., 0., 0.]),
                           meta=SimpleNamespace(laneChangeState=0))


def car(speed, accel, blinker=0, pressed=False):
  return SimpleNamespace(vEgo=speed, aEgo=accel, rightBlinker=blinker == 1, leftBlinker=blinker == -1,
                         steeringPressed=pressed, steeringTorque=0.)


def step(assist, speed, accel, desired, active=True, **kwargs):
  return assist.update(car(speed, accel, **kwargs), STRAIGHT, SimpleNamespace(latActive=active), desired)


class TestStopHold(unittest.TestCase):
  def test_holds_command_through_final_metre_of_stop(self):
    assist = ModelTurnAssist()
    self.assertEqual(step(assist, 2.0, -2.0, .002), .002)
    for speed, desired in ((1.5, .004), (1.0, .010), (.6, .014)):
      self.assertEqual(step(assist, speed, -2.0, desired), .002)

  def test_releases_when_car_stops_slowing(self):
    assist = ModelTurnAssist()
    step(assist, 1.0, -2.0, .001)
    self.assertEqual(step(assist, 1.0, -.5, .01), .001)
    self.assertEqual(step(assist, 1.0, 0., .01), .01)
    self.assertIsNone(assist.stop_hold_curvature)

  def test_does_not_engage_far_from_stop_or_above_speed(self):
    for speed, accel in ((2.0, -.5), (1.0, -.2), (STOP_HOLD_MAX_SPEED, -5.), (1.0, 1.)):
      with self.subTest(speed=speed, accel=accel):
        assist = ModelTurnAssist()
        self.assertEqual(step(assist, speed, accel, .01), .01)
        self.assertIsNone(assist.stop_hold_curvature)

  def test_blinker_or_inactive_never_holds(self):
    for direction in (-1, 1):
      assist = ModelTurnAssist()
      step(assist, 1.0, -2.0, .001)
      self.assertNotEqual(step(assist, 1.0, -2.0, direction * .02, blinker=direction), .001)
      self.assertIsNone(assist.stop_hold_curvature)
    assist = ModelTurnAssist()
    self.assertEqual(step(assist, 1.0, -2.0, .01, active=False), .01)
    self.assertIsNone(assist.stop_hold_curvature)

  def test_driver_steering_moves_hold_to_measured_curvature(self):
    assist = ModelTurnAssist()
    step(assist, 1.0, -2.0, .001)
    assist.curvature = -.003
    self.assertEqual(step(assist, .8, -2.0, .015, pressed=True), -.003)
    assist.curvature = .009
    self.assertEqual(step(assist, .6, -2.0, .015), -.003)


if __name__ == '__main__':
  unittest.main()
