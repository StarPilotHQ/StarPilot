"""Native line arguments retain logical geometry and current style."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import onroad_compact_widgets as widgets
from openpilot.system.ui.lib.application import gui_app


class CompactLineGeometryTests(unittest.TestCase):
  def setUp(self):
    widgets._line_points.cache_clear()
    self.addCleanup(widgets._line_points.cache_clear)

  def test_native_line_retains_fractional_points_width_and_current_color(self):
    native = Mock()
    api = SimpleNamespace(Vector2=rl.Vector2, draw_line_ex=native)
    first, second = rl.Color(20, 30, 40, 255), rl.Color(100, 110, 120, 63)
    with patch.object(widgets, "rl", api):
      widgets._line(-12.25, 3.5, 40.75, -6.25, first, 4.75)
      widgets._line(-12.25, 3.5, 40.75, -6.25, second, 2)
    start, end, width, color = native.call_args_list[0].args
    self.assertEqual((start.x, start.y, end.x, end.y, width), (-12.25, 3.5, 40.75, -6.25, 4.75))
    self.assertIs(color, first)
    self.assertIs(native.call_args_list[1].args[0], start)
    self.assertIs(native.call_args_list[1].args[1], end)
    self.assertEqual(native.call_args_list[1].args[2], 2)
    self.assertIs(native.call_args_list[1].args[3], second)

  def test_global_scaling_keeps_cached_points_in_logical_coordinates(self):
    native = Mock()
    api = SimpleNamespace(Vector2=rl.Vector2, draw_line_ex=native)
    with patch.object(widgets, "rl", api):
      for scale in (.5, 1., 2.):
        with patch.object(gui_app, "_scale", scale):
          widgets._line(11.25, 22.5, 33.75, 44., rl.WHITE, 3)
        start, end, width, _ = native.call_args.args
        self.assertEqual((start.x, start.y, end.x, end.y, width), (11.25, 22.5, 33.75, 44., 3))
    self.assertEqual(widgets._line_points.cache_info().currsize, 1)

  def test_repeated_editor_positions_have_bounded_geometry_retention(self):
    for x in range(512):
      widgets._line_points(x, 2, x + 4, 8)
    self.assertEqual(widgets._line_points.cache_info().currsize, 128)


if __name__ == "__main__":
  unittest.main()
