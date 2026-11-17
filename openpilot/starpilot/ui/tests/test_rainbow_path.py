"""Rainbow path stays display-only and requires fresh speed for phase changes."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np
from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.mici.onroad import model_renderer as compact
from openpilot.selfdrive.ui.onroad import model_renderer as large
from openpilot.starpilot.ui.rainbow_path import RainbowPath
from openpilot.system.ui.lib.shader_polygon import Gradient


class TestRainbowPath(unittest.TestCase):
  def test_phase_uses_only_fresh_finite_speed_and_bounded_frame_time(self):
    path = RainbowPath()
    path.update(1_000_000_000, 10.0, source_alive=True)
    path.update(1_100_000_000, None, source_alive=True)
    self.assertAlmostEqual(path.phase_deg, 5.0)
    path.update(1_700_000_000, None, source_alive=True)
    self.assertAlmostEqual(path.phase_deg, 5.0)
    path.update(1_800_000_000, float("nan"), source_alive=True)
    path.update(1_900_000_000, None, source_alive=True)
    self.assertAlmostEqual(path.phase_deg, 5.0)
    path.update(2_000_000_000, 10.0, source_alive=False)
    path.update(2_100_000_000, None, source_alive=True)
    self.assertAlmostEqual(path.phase_deg, 5.0)
    path.update(2_200_000_000, 10.0, source_alive=True)
    self.assertAlmostEqual(path.phase_deg, 10.0)
    path.update(2_100_000_000, None, source_alive=True)
    self.assertAlmostEqual(path.phase_deg, 10.0)

  def test_gradient_matches_original_twelve_stop_hue_and_alpha_range(self):
    gradient = RainbowPath().gradient()
    self.assertEqual(len(gradient.colors), 12)
    self.assertEqual((gradient.start, gradient.end), ((0.0, 0.0), (0.0, 1.0)))
    self.assertEqual((gradient.stops[0], gradient.stops[-1]), (0.0, 1.0))
    self.assertEqual((gradient.colors[0].r, gradient.colors[0].g, gradient.colors[0].b), (255, 0, 0))
    self.assertEqual((gradient.colors[-1].r, gradient.colors[-1].g, gradient.colors[-1].b), (0, 255, 0))
    self.assertEqual((gradient.colors[0].a, gradient.colors[-1].a), (127, 25))

  def test_both_native_profiles_keep_off_gradient_and_select_rainbow_from_layout(self):
    for module in (large, compact):
      with self.subTest(profile=module.__name__):
        renderer = module.ModelRenderer.__new__(module.ModelRenderer)
        renderer._rect = rl.Rectangle(0, 0, 100, 100)
        points = module.ModelPoints(projected_points=np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32))
        self.enterContext(patch.object(renderer, "_path", points, create=True))
        renderer._longitudinal_control = False
        renderer._experimental_mode = True
        renderer._blend_filter = Mock()
        renderer._exp_gradient = Gradient(start=(0.0, 1.0), end=(0.0, 0.0),
                                          colors=[rl.WHITE, rl.BLACK], stops=[0.0, 1.0])
        renderer._rainbow_path = RainbowPath()
        messages = {"longitudinalPlan": NS(allowThrottle=True), "carState": NS(vEgo=10.0)}
        class SubMaster:
          valid = {"carState": True}
          alive = {"carState": True}
          updated = {"carState": True}
          recv_frame = {"carState": 5}
          def __init__(self, values):
            self.values = values
          def __getitem__(self, key):
            return self.values[key]
        sm = SubMaster(messages)
        ui = NS(status=compact.UIStatus.ENGAGED, started_frame=5, display_time_ns=Mock())
        with patch.object(module, "ui_state", ui), patch.object(ui, "display_time_ns", return_value=1_000_000_000), \
             patch.object(module, "draw_polygon") as draw:
          renderer._draw_path(sm)
          self.assertIs(draw.call_args.kwargs["gradient"], renderer._exp_gradient)
        renderer.road_style = {"pathMode": "rainbow"}
        with patch.object(module, "ui_state", ui), patch.object(ui, "display_time_ns", return_value=2_000_000_000), \
             patch.object(module, "draw_polygon") as draw:
          renderer._draw_path(sm)
          self.assertEqual(len(draw.call_args.kwargs["gradient"].colors), 12)
        sm.recv_frame["carState"] = 4
        with patch.object(module, "ui_state", ui), patch.object(ui, "display_time_ns", return_value=2_100_000_000), \
             patch.object(module, "draw_polygon"):
          renderer._draw_path(sm)
        self.assertEqual(renderer._rainbow_path.phase_deg, 0.0)
        sm.recv_frame["carState"] = 5
        with patch.object(module, "ui_state", ui), patch.object(ui, "display_time_ns", return_value=2_200_000_000), \
             patch.object(module, "draw_polygon"):
          renderer._draw_path(sm)
        self.assertGreater(renderer._rainbow_path.phase_deg, 0.0)
