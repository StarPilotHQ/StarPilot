import unittest
from types import SimpleNamespace

import numpy as np

from openpilot.selfdrive.ui.mici.onroad.model_renderer import ModelRenderer


class TestCompactProjectionBatch(unittest.TestCase):
  def test_combined_projection_matches_independent_strips_and_path(self):
    rng = np.random.default_rng(20261111)
    renderer = object.__new__(ModelRenderer)
    renderer._clip_region = SimpleNamespace(x=-500, y=-500, width=1536, height=1240)
    for case in range(80):
      with self.subTest(case=case):
        renderer._car_space_transform = np.array([[268, 500, 0], [240, 0, -500], [1, 0, 0]], dtype=np.float32)
        if case % 13 == 0:
          renderer._car_space_transform[2] = 0
        x = np.linspace(-3, 120, 33, dtype=np.float32)
        lines = [np.column_stack((x, rng.normal(0, .7, 33).cumsum(), rng.normal(0, .03, 33).cumsum())).astype(np.float32)
                 for _ in range(7)]
        if case % 5 == 0:
          lines[case % 7] = lines[case % 7][:0]
        widths = [float(x) for x in rng.uniform(0, .9, 7)]
        heights = (0., 0., 0., 0., 0., 0., float(rng.uniform(.4, 2.)))
        max_idx = case % 33
        actual = renderer._map_lines_to_polygons(lines, widths, max_idx, z_offsets=heights, invert_last=True)
        expected = [renderer._map_line_to_polygon(line, width, height, max_idx, allow_invert=index != 6)
                    for index, (line, width, height) in enumerate(zip(lines, widths, heights, strict=True))]
        for new, old in zip(actual, expected, strict=True):
          self.assertEqual(new.dtype, np.float32)
          np.testing.assert_allclose(new, old, rtol=1e-6, atol=.002)

  def test_default_lane_batch_retains_independent_clip_and_order(self):
    renderer = object.__new__(ModelRenderer)
    renderer._car_space_transform = np.eye(3, dtype=np.float32)
    renderer._clip_region = SimpleNamespace(x=-2, y=-2, width=4, height=4)
    lines = [np.array([[0, 1, 1], [1, 2, 1], [-1, 1, 1], [2, -2, 1]], dtype=np.float32),
             np.empty((0, 3), dtype=np.float32)]
    actual = renderer._map_lines_to_polygons(lines, [.2, .3], 3)
    for line, width, result in zip(lines, [.2, .3], actual, strict=True):
      np.testing.assert_array_equal(result, renderer._map_line_to_polygon(line, width, 0., 3))


if __name__ == '__main__':
  unittest.main()
