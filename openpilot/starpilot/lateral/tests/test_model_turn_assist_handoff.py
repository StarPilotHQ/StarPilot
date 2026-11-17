import unittest
from types import SimpleNamespace as N

from openpilot.starpilot.lateral.model_turn_assist import ModelTurnAssist


class TestHandoff(unittest.TestCase):
  def sample(self, direction):
    return N(vEgo=2.5, aEgo=-0.5, leftBlinker=direction < 0, rightBlinker=direction > 0, steeringPressed=False, steeringTorque=0.0), N(
      position=N(x=[0.0, 4.0, 7.0, 12.0], y=[0.0, direction * 0.8, direction * 2.0, direction * 5.0]), meta=N(laneChangeState=0)
    )

  def test_weak_action_does_not_finish_turn(self):
    for d in [-1, 1]:
      a = ModelTurnAssist()
      cs, m = self.sample(d)
      for _ in range(80):
        a.curvature = d * 0.008
        a.update(cs, m, N(latActive=True), d * 0.008)
      self.assertFalse(a.turn_hold_done)
      cs.vEgo = 1.3
      self.assertAlmostEqual(a.update(cs, m, N(latActive=True), d * 0.003), d * 0.008)

  def test_action_catching_path_hands_back(self):
    for d in [-1, 1]:
      a = ModelTurnAssist()
      cs, m = self.sample(d)
      a.turn_hold_curvature = d * 0.08
      for _ in range(35):
        a.curvature = d * 0.08
        a.update(cs, m, N(latActive=True), d * 0.08)
      self.assertTrue(a.turn_hold_done)

  def test_opposite_action_releases(self):
    for d in [-1, 1]:
      a = ModelTurnAssist()
      cs, m = self.sample(d)
      a.turn_hold_curvature = d * 0.08
      self.assertEqual(a.update(cs, m, N(latActive=True), -d * 0.02), -d * 0.02)
      self.assertTrue(a.turn_hold_done)

  def test_straight_and_invalid_path_keep_handoff(self):
    for ys in [[0.0, 0.0, 0.0, 0.0], [0.0, float('nan'), 0.0, 0.0]]:
      a = ModelTurnAssist()
      cs, m = self.sample(1)
      m.position.y = ys
      for _ in range(80):
        a.curvature = 0.008
        a.update(cs, m, N(latActive=True), 0.008)
      self.assertTrue(a.turn_hold_done)

  def test_inactive_never_adds_hold(self):
    a = ModelTurnAssist()
    cs, m = self.sample(1)
    a.turn_hold_curvature = 0.08
    self.assertEqual(a.update(cs, m, N(latActive=False), 0.003), 0.003)

  def test_high_speed_clears_hold(self):
    a = ModelTurnAssist()
    cs, m = self.sample(1)
    cs.vEgo = 8.0
    a.turn_hold_curvature = 0.08
    self.assertEqual(a.update(cs, m, N(latActive=True), 0.003), 0.003)
    self.assertEqual(a.turn_hold_curvature, 0.0)

  def test_short_path_does_not_prevent_handoff(self):
    for direction in (-1, 1):
      assist = ModelTurnAssist()
      cs, model = self.sample(direction)
      cs.vEgo = 1.3
      model.position.x = [0.0, 1.0]
      model.position.y = [0.0, direction * 0.3]
      for _ in range(100):
        assist.curvature = direction * 0.008
        assist.update(cs, model, N(latActive=True), direction * 0.008)
      self.assertTrue(assist.turn_hold_done)
      self.assertEqual(assist.update(cs, model, N(latActive=True), direction * 0.003), direction * 0.003)

  def test_deferred_handoff_releases_on_abort_or_path_loss(self):
    for direction in (-1, 1):
      for ending in ('straight', 'opposite', 'short', 'invalid'):
        with self.subTest(direction=direction, ending=ending):
          assist = ModelTurnAssist()
          cs, model = self.sample(direction)
          cs.vEgo = 1.3
          for _ in range(100):
            assist.curvature = direction * 0.008
            assist.update(cs, model, N(latActive=True), direction * 0.008)
          self.assertFalse(assist.turn_hold_done)
          if ending == 'straight':
            model.position.y = [0.0] * 4
          elif ending == 'opposite':
            model.position.y = [-y for y in model.position.y]
          elif ending == 'short':
            model.position.x = [0.0, 1.0]
            model.position.y = [0.0, direction * 0.3]
          else:
            model.position.y[1] = float('nan')
          self.assertEqual(assist.update(cs, model, N(latActive=True), direction * 0.003), direction * 0.003)
          self.assertTrue(assist.turn_hold_done)
