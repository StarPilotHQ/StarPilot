import gc

import numpy as np
import pytest

from openpilot.starpilot.ui.raylib import rl


def test_native_values_keep_tuple_callers_compatible():
  point = rl.Vector2((2., 3.))
  assert (point.x, point.y) == (2., 3.)
  assert rl.check_collision_point_rec((2., 3.), rl.Rectangle(0, 0, 10, 10))
  color = rl.color_lerp((0, 0, 0, 255), (100, 200, 50, 255), .5)
  assert (color.r, color.g, color.b, color.a) == (50, 100, 25, 255)


def test_python_image_buffer_retains_ownership_and_readonly_state():
  pixels = bytearray([1, 2, 3, 4])
  image = rl.Image(pixels, 1, 1, 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
  gc.collect()
  assert bytes(image.data) == bytes(pixels)
  image.data[0] = 9
  assert pixels[0] == 9
  with pytest.raises(ValueError, match='Python-backed'):
    rl.image_flip_vertical(image)
  image.width = 2
  with pytest.raises(ValueError, match='buffer'):
    _ = image.data
  image.width = 1
  readonly = rl.Image(bytes(pixels), 1, 1, 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
  with pytest.raises(TypeError):
    readonly.data[0] = 0


def test_native_image_mutation_and_format_keep_exact_pixels():
  image = rl.gen_image_color(2, 1, rl.Color(10, 20, 30, 255))
  try:
    rl.image_format(image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
    assert bytes(image.data) == bytes([10, 20, 30, 255]) * 2
    image.data[0] = 11
    assert rl.get_image_color(image, 0, 0).r == 11
  finally:
    rl.unload_image(image)


def test_empty_font_views_reject_out_of_bounds():
  font = rl.Font()
  assert len(font.glyphs) == len(font.recs) == 0
  for view in (font.glyphs, font.recs):
    with pytest.raises(IndexError):
      _ = view[0]


def test_zero_copy_strip_checks_bounds_before_drawing():
  points = np.zeros((4, 2), dtype=np.float32)
  for count in (-1, 5):
    with pytest.raises(ValueError, match='bounds'):
      rl.draw_triangle_strip(points, count, rl.WHITE)
