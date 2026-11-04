"""Noisy model curvature must not strobe the curve glow between green and red."""

import random
import unittest
from unittest.mock import patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.curve_speed.status import CurvatureDisplay, CurveObservation
from openpilot.starpilot.ui.developer_preview import OnroadVisualPreview
from openpilot.starpilot.ui.onroad_curve import GlowPhase, render_glow
from openpilot.starpilot.ui.onroad_state import OnroadAlert, OnroadState, SpeedLimitObservation

FRAME_NS = 50_000_000
START_NS = 5_000 * 1_000_000_000  # observed time is monotonic uptime, not seconds since the glow appeared


def curve(curvature: float | None) -> CurveObservation:
  return CurveObservation(True, 20.0, 20.0, True, True, False, 100.0, 2.0, 50.0, 'curve', 'selected', 'ok', True, False,
                          curvature)


def jitter(frames: int) -> list[float | None]:
  rng = random.Random(7)
  return [None if rng.random() < .1 else rng.uniform(.004, .016) for _ in range(frames)]


def edges(curvatures, phase):
  rect = rl.Rectangle(30, 30, 1800, 1020)
  colors = []
  with patch.object(rl, 'draw_rectangle_gradient_v') as vertical, patch.object(rl, 'draw_rectangle_gradient_h'):
    for frame, curvature in enumerate(curvatures):
      state = OnroadState(engaged=False, camera_available=False, speed_mps=0.0, cruise_kph=None,
                          speed_limit=SpeedLimitObservation(), alert=OnroadAlert(),
                          visual_preview=OnroadVisualPreview(None, curvature))
      vertical.reset_mock()
      render_glow(rect, state, border_width=30, time_s=(START_NS + frame * FRAME_NS) / 1e9, phase=phase)
      colors.append(vertical.call_args_list[0].args[4])
  return colors


def worst_step(colors) -> int:
  return max(max(abs(a.r - b.r), abs(a.g - b.g), abs(a.b - b.b), abs(a.a - b.a)) for a, b in zip(colors, colors[1:], strict=False))


class CurveGlowFlickerTests(unittest.TestCase):
  def test_smoothed_curvature_holds_dropouts_and_resets_after_gap(self):
    display = CurvatureDisplay()
    self.assertEqual(display.update(curve(.01), START_NS).road_curvature, .01)
    held = display.update(curve(None), START_NS + FRAME_NS)
    self.assertEqual(held.road_curvature, .01)
    stepped = display.update(curve(.02), START_NS + 2 * FRAME_NS).road_curvature
    self.assertTrue(.01 < stepped < .013, stepped)
    self.assertIsNone(display.update(None, START_NS + 3 * FRAME_NS))
    self.assertEqual(display.update(curve(.003), START_NS + 3 * FRAME_NS + 2_000_000_000).road_curvature, .003)

  def test_glow_changes_gradually_under_jittery_curvature(self):
    display = CurvatureDisplay()
    smoothed = [display.update(curve(value), START_NS + frame * FRAME_NS).road_curvature
                for frame, value in enumerate(jitter(200))]
    self.assertLessEqual(worst_step(edges(smoothed, GlowPhase())), 40)

  def test_raw_jitter_without_phase_state_strobes(self):
    raw = [value if value is not None else .0001 for value in jitter(200)]
    self.assertGreater(worst_step(edges(raw, None)), 150)

  def test_tiny_period_changes_do_not_jump_the_breath(self):
    # 1% curvature noise barely moves the color, but re-deriving the phase from uptime strobed the alpha.
    curvatures = [.01 if frame % 2 else .0101 for frame in range(200)]
    self.assertGreater(worst_step(edges(curvatures, None)), 30)
    self.assertLessEqual(worst_step(edges(curvatures, GlowPhase())), 8)

  def test_constant_period_matches_stateless_breath(self):
    curvatures = [.01] * 40
    stateless, phased = edges(curvatures, None), edges(curvatures, GlowPhase())
    self.assertLessEqual(worst_step(phased), worst_step(stateless) + 1)


if __name__ == '__main__':
  unittest.main()
