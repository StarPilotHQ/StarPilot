from unittest.mock import Mock, patch

import numpy as np
from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.system.ui.lib import shader_polygon as polygon


def geometry_fixture(count, dtype, stride):
  values = np.random.default_rng(20260926 + count).normal(100, 250, (count * stride, 2)).astype(dtype)
  return values[::stride]


def pointer_vertices(pointer, count):
  return np.asarray(pointer, dtype=np.float32).reshape(-1, 2)[:count].copy()


@pytest.mark.parametrize('dtype', ['float32', 'float64'])
@pytest.mark.parametrize('odd', [False, True])
@pytest.mark.parametrize('stride', [1, 2])
def test_ribbon_geometry_and_native_vertices(dtype, odd, stride):
  ribbon = [[0, 0], [2, 0], [4, 1], [4, 3], [2, 2], [0, 2]]
  if odd:
    ribbon.append([99, 99])
  storage = np.zeros((len(ribbon) * stride, 2), dtype=dtype)
  storage[::stride] = ribbon
  points = storage[::stride]
  actual = polygon.triangulate(points)
  expected = np.array([[0, 0], [0, 2], [2, 0], [2, 2], [4, 1], [4, 3]], dtype=np.float32)
  np.testing.assert_array_equal(actual, expected)
  np.testing.assert_array_equal(pointer_vertices(actual, len(actual)), expected)
  np.testing.assert_array_equal(points, ribbon)
  assert actual.dtype == np.float32 and actual.flags.c_contiguous
  assert not np.shares_memory(actual, points)


def test_solid_ribbon_preserves_color_and_skips_shader():
  points = geometry_fixture(66, 'float64', 2)
  color = rl.Color(18, 140, 229, 91)
  draws = []
  with patch.object(polygon.ShaderState, 'get_instance', side_effect=AssertionError('solid does not need shader')), \
       patch.object(rl, 'begin_shader_mode') as begin, patch.object(rl, 'end_shader_mode') as end, \
       patch.object(rl, 'draw_triangle_strip', side_effect=lambda vertices, count, tint:
                    draws.append((pointer_vertices(vertices, count), tint))):
    polygon.draw_polygon(rl.Rectangle(0, 0, 100, 200), points, color)
  assert len(draws) == 1
  np.testing.assert_array_equal(draws[0][0], polygon.triangulate(points))
  assert (draws[0][1].r, draws[0][1].g, draws[0][1].b, draws[0][1].a) == (18, 140, 229, 91)
  begin.assert_not_called()
  end.assert_not_called()


def test_gradient_preserves_uniforms_and_shader_draw_order():
  with patch.object(polygon.ShaderState, '_instance', None):
    state = polygon.ShaderState()
  state.shader = object()
  state.locations = {name: name for name in state.locations}
  events = []
  state.initialize = Mock(side_effect=lambda: events.append('initialize'))
  uniforms = {}
  gradient = polygon.Gradient((.25, .75), (1, 0), [rl.Color(255, 0, 128, 64), rl.Color(0, 255, 32, 192)], [-.5, 1.5])
  points = geometry_fixture(5, 'float32', 1)

  def uniform(shader, name, value, kind):
    events.append(name)
    uniforms[name] = (value.x, value.y) if name in ('gradientStart', 'gradientEnd') else value[0]

  def uniform_array(shader, name, value, kind, count):
    events.append(name)
    uniforms[name] = list(value[0:count * (4 if name == 'gradientColors' else 1)])

  def draw(vertices, count, tint):
    events.append('draw')
    np.testing.assert_array_equal(pointer_vertices(vertices, count), polygon.triangulate(points))
    assert tint == rl.WHITE

  with patch.object(polygon.ShaderState, 'get_instance', return_value=state), \
       patch.object(rl, 'set_shader_value', side_effect=uniform), \
       patch.object(rl, 'set_shader_value_v', side_effect=uniform_array), \
       patch.object(rl, 'begin_shader_mode', side_effect=lambda shader: events.append('begin')), \
       patch.object(rl, 'end_shader_mode', side_effect=lambda: events.append('end')), \
       patch.object(rl, 'draw_triangle_strip', side_effect=draw):
    polygon.draw_polygon(rl.Rectangle(10, 20, 100, 200), points, gradient=gradient)
  assert events == ['initialize', 'useGradient', 'gradientColors', 'gradientStops', 'gradientColorCount',
                    'gradientStart', 'gradientEnd', 'begin', 'draw', 'end']
  assert uniforms['useGradient'] == 1 and uniforms['gradientColorCount'] == 2
  assert uniforms['gradientStops'] == [0, 1]
  assert uniforms['gradientStart'] == (35, 170) and uniforms['gradientEnd'] == (110, 20)
  assert uniforms['gradientColors'] == pytest.approx([1, 0, 128 / 255, 64 / 255, 0, 1, 32 / 255, 192 / 255])


@pytest.mark.parametrize('points', [np.empty((0, 2)), np.zeros((1, 2)), np.zeros((2, 2))])
def test_short_polygons_do_not_draw_or_initialize_shader(points):
  with patch.object(polygon.ShaderState, 'get_instance') as shader, patch.object(rl, 'draw_triangle_strip') as draw:
    polygon.draw_polygon(rl.Rectangle(0, 0, 100, 100), points)
  shader.assert_not_called()
  draw.assert_not_called()


def test_invalid_geometry_and_color_contract_fail_without_draw():
  rect = rl.Rectangle(0, 0, 100, 100)
  points = np.zeros((4, 2))
  gradient = polygon.Gradient((0, 0), (0, 1), [rl.WHITE], [])
  with patch.object(rl, 'draw_triangle_strip') as draw:
    with pytest.raises(AssertionError, match='Either color or gradient'):
      polygon.draw_polygon(rect, points)
    with pytest.raises(AssertionError, match='Either color or gradient'):
      polygon.draw_polygon(rect, points, color=rl.WHITE, gradient=gradient)
    for malformed in (np.zeros(4), np.zeros((4, 3))):
      with pytest.raises(AssertionError, match='points must be'):
        polygon.draw_polygon(rect, malformed, rl.WHITE)
  draw.assert_not_called()
