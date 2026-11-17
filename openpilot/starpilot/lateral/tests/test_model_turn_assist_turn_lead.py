import unittest
from types import SimpleNamespace

from openpilot.starpilot.lateral.model_turn_assist import ModelTurnAssist

# A path curving steadily in the positive direction: the dual probe at
# the lead distances reads ~0.0159 curvature, so the shared turn lead
# would apply ~0.0135 against a 0.002 baseline.
CURVING = SimpleNamespace(position=SimpleNamespace(x=[float(i) for i in range(31)],
                                                   y=[0.008 * i * i for i in range(31)]),
                          meta=SimpleNamespace(laneChangeState=0))


def car(speed, blinker=1):
  return SimpleNamespace(vEgo=speed, aEgo=0., rightBlinker=blinker == 1, leftBlinker=blinker == -1,
                         steeringPressed=False, steeringTorque=0.)


def step(assist, desired, speed=4.0):
  return assist.update(car(speed), CURVING, SimpleNamespace(latActive=True), desired)


class TestTurnLeadByCar(unittest.TestCase):
  def test_base_package_applies_lead_and_seeds_hold(self):
    # Ioniq 6 configuration: turn lead on, no high-speed preview.
    assist = ModelTurnAssist()
    out = step(assist, 0.002)
    self.assertGreater(out, 0.01)
    self.assertAlmostEqual(out, 0.0135, places=3)
    # The lead also seeds the turn hold (0.0008 after one frame: the
    # hold path alone would have ratcheted to 0.0004).
    self.assertAlmostEqual(assist.turn_hold_curvature, 0.0008, places=6)

  def test_gv70_gets_no_lead_and_no_lead_seeded_hold(self):
    # GV70 configuration: turn lead off, high-speed preview on (inert
    # at this speed). Desired curvature passes through unchanged and
    # the hold is seeded only by the hold path itself.
    assist = ModelTurnAssist(turn_lead=False, gv70_high_speed_preview=True)
    self.assertEqual(step(assist, 0.002), 0.002)
    # One frame of hold-path ratchet only (the lead would have seeded
    # 0.0008, as in the base-package test above).
    self.assertAlmostEqual(assist.turn_hold_curvature, 0.0004, places=6)
    for _ in range(4):
      self.assertEqual(step(assist, 0.002), 0.002)

  def test_gv70_no_lead_at_higher_lead_speed(self):
    assist = ModelTurnAssist(turn_lead=False, gv70_high_speed_preview=True)
    self.assertEqual(step(assist, 0.002, speed=5.0), 0.002)
    self.assertEqual(assist.turn_hold_curvature, 0.0)


if __name__ == '__main__':
  unittest.main()
