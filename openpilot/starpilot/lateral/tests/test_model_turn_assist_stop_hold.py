import unittest
from types import SimpleNamespace

from openpilot.starpilot.lateral.model_turn_assist import (
  STOP_HOLD_MAX_SPEED, TWITCH_GUARD_FLOOR, TWITCH_GUARD_MAX_SPEED, ModelTurnAssist, update_twitch_guard,
)

STRAIGHT = SimpleNamespace(position=SimpleNamespace(x=[0., 1., 2., 3.], y=[0., 0., 0., 0.]),
                           meta=SimpleNamespace(laneChangeState=0))
LONG_STRAIGHT = SimpleNamespace(position=SimpleNamespace(x=[float(x) for x in range(0, 80, 4)], y=[0.] * 20),
                                meta=SimpleNamespace(laneChangeState=0))


def car(speed, accel, blinker=0, pressed=False):
  return SimpleNamespace(vEgo=speed, aEgo=accel, rightBlinker=blinker == 1, leftBlinker=blinker == -1,
                         steeringPressed=pressed, steeringTorque=0.)


def step(assist, speed, accel, desired, active=True, model=STRAIGHT, **kwargs):
  return assist.update(car(speed, accel, **kwargs), model, SimpleNamespace(latActive=active), desired)


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

  def test_latches_guarded_command_not_raw_model(self):
    assist = ModelTurnAssist()
    assist.twitch_guard_armed = True
    self.assertEqual(step(assist, 1.0, 0., .012, model=LONG_STRAIGHT), TWITCH_GUARD_FLOOR)
    self.assertEqual(step(assist, .6, -2.0, .012, model=LONG_STRAIGHT), TWITCH_GUARD_FLOOR)

  def test_does_not_freeze_a_turn_in_progress(self):
    assist = ModelTurnAssist()
    assist.curvature = .02
    self.assertEqual(step(assist, 1.0, -2.0, .04), .04)
    self.assertIsNone(assist.stop_hold_curvature)


class TestTwitchGuardArming(unittest.TestCase):
  def test_armed_from_standstill_until_max_speed(self):
    self.assertTrue(update_twitch_guard(False, 0., True))
    self.assertTrue(update_twitch_guard(False, .3, False))
    self.assertFalse(update_twitch_guard(False, 1., False))
    for speed in (.5, 2., TWITCH_GUARD_MAX_SPEED - .01):
      self.assertTrue(update_twitch_guard(True, speed, False))
    self.assertFalse(update_twitch_guard(True, TWITCH_GUARD_MAX_SPEED, False))
    self.assertFalse(update_twitch_guard(True, float('nan'), False))

  def test_released_stop_hold_hands_off_to_guard(self):
    assist = ModelTurnAssist()
    step(assist, 1.0, -2.0, .001, model=LONG_STRAIGHT)
    self.assertTrue(assist.twitch_guard_armed)
    self.assertEqual(step(assist, 1.0, 0., .012, model=LONG_STRAIGHT), TWITCH_GUARD_FLOOR)

  def test_unarmed_guard_leaves_creep_command(self):
    assist = ModelTurnAssist()
    self.assertEqual(step(assist, 1.0, 0., .012, model=LONG_STRAIGHT), .012)

  def test_stop_approach_arms_guard_only_with_straight_wheel(self):
    assist = ModelTurnAssist()
    self.assertEqual(step(assist, 3.0, -1.5, .012, model=LONG_STRAIGHT), TWITCH_GUARD_FLOOR)
    assist = ModelTurnAssist()
    assist.curvature = .006
    self.assertEqual(step(assist, 3.0, -1.5, .012, model=LONG_STRAIGHT), .012)
    assist = ModelTurnAssist()
    self.assertEqual(step(assist, 3.0, -.2, .012, model=LONG_STRAIGHT), .012)

  def test_standstill_keeps_last_command_instead_of_raw_model(self):
    assist = ModelTurnAssist()
    assist.twitch_guard_armed = True
    self.assertEqual(step(assist, 1.0, 0., .012, model=LONG_STRAIGHT), TWITCH_GUARD_FLOOR)
    self.assertEqual(assist.update_stopped(car(0., 0.), .012), TWITCH_GUARD_FLOOR)
    assist.curvature = -.004
    self.assertEqual(assist.update_stopped(car(0., 0., pressed=True), .012), -.004)
    self.assertEqual(assist.update_stopped(car(0., 0.), .012), -.004)

  def test_standstill_passes_raw_model_with_blinker_or_no_history(self):
    self.assertEqual(ModelTurnAssist().update_stopped(car(0., 0.), .012), .012)
    assist = ModelTurnAssist()
    step(assist, 1.0, 0., .003)
    self.assertEqual(assist.update_stopped(car(0., 0., blinker=1), .012), .012)
    self.assertIsNone(assist.output_curvature)

  def test_blinker_disarms_guard(self):
    assist = ModelTurnAssist()
    assist.twitch_guard_armed = True
    step(assist, 2.0, 0., .012, model=LONG_STRAIGHT, blinker=1)
    self.assertFalse(assist.twitch_guard_armed)
    self.assertEqual(step(assist, 2.0, 0., .012, model=LONG_STRAIGHT), .012)


if __name__ == '__main__':
  unittest.main()
