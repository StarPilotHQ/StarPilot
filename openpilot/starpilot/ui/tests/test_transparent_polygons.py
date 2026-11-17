import unittest
from unittest.mock import patch

import numpy as np

from openpilot.system.ui.lib import shader_polygon as polygon


class TestTransparentPolygons(unittest.TestCase):
  def test_transparent_solid_skips_triangulation_and_native_draw(self):
    points = np.array([[0, 0], [0, 10], [10, 10], [10, 0]], dtype=np.float32)
    with patch.object(polygon, 'triangulate', side_effect=AssertionError('Invisible polygon')), \
         patch.object(polygon.rl, 'draw_triangle_strip') as draw:
      polygon.draw_polygon(polygon.rl.Rectangle(0, 0, 100, 100), points, polygon.rl.Color(255, 255, 255, 0))
      draw.assert_not_called()

  def test_even_lowest_visible_alpha_keeps_geometry_and_color(self):
    points = np.array([[0, 0], [0, 10], [10, 10], [10, 0]], dtype=np.float32)
    color = polygon.rl.Color(100, 200, 250, 1)
    with patch.object(polygon.rl, 'draw_triangle_strip') as draw:
      polygon.draw_polygon(polygon.rl.Rectangle(0, 0, 100, 100), points, color)
      actual, count, painted = draw.call_args.args
      np.testing.assert_array_equal(actual, polygon.triangulate(points))
      self.assertEqual(count, 4)
      self.assertEqual(painted.a, 1)

  def test_transparency_does_not_skip_input_contracts(self):
    with self.assertRaises(AssertionError):
      polygon.draw_polygon(polygon.rl.Rectangle(), np.zeros((4, 3)), polygon.rl.Color(0, 0, 0, 0))
    with self.assertRaises(AssertionError):
      polygon.draw_polygon(polygon.rl.Rectangle(), np.zeros((4, 2)))


if __name__ == '__main__':
  unittest.main()
