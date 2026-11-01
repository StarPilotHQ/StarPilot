"""Hot-path shortcuts in shared UI renderers must draw exactly what the long way did."""

from types import SimpleNamespace

import numpy as np
import pytest


class TestShaderUniformCache:
  @pytest.fixture
  def state(self, monkeypatch):
    from openpilot.system.ui.lib import shader_polygon
    uploads = []
    monkeypatch.setattr(shader_polygon.rl, "set_shader_value", lambda shader, location, value, kind: uploads.append(location))
    monkeypatch.setattr(shader_polygon.rl, "set_shader_value_v",
                        lambda shader, location, value, kind, count: uploads.append(location))
    state = object.__new__(shader_polygon.ShaderState)  # the singleton's state without its GL shader
    state.shader = object()
    state.locations = {name: name for name in ("fillColor", "useGradient", "gradientStart", "gradientEnd",
                                               "gradientColors", "gradientStops", "gradientColorCount")}
    for name, ctype, size in (("fill_color_ptr", "float[]", 4), ("use_gradient_ptr", "int[]", 1), ("color_count_ptr", "int[]", 1),
                              ("gradient_colors_ptr", "float[]", shader_polygon.MAX_GRADIENT_COLORS * 4),
                              ("gradient_stops_ptr", "float[]", shader_polygon.MAX_GRADIENT_COLORS)):
      setattr(state, name, shader_polygon.rl.ffi.new(ctype, size))
    state.uniform_values = {}
    return shader_polygon, state, uploads

  def test_unchanged_color_is_uploaded_once(self, state):
    shader_polygon, state, uploads = state
    rect = shader_polygon.rl.Rectangle(0, 0, 100, 100)
    color = shader_polygon.rl.Color(10, 20, 30, 255)
    shader_polygon._configure_shader_color(state, color, None, rect)
    assert sorted(uploads) == ["fillColor", "useGradient"]
    shader_polygon._configure_shader_color(state, color, None, rect)
    assert len(uploads) == 2
    color.g = 21  # edited in place: the value changed, the object did not
    shader_polygon._configure_shader_color(state, color, None, rect)
    assert uploads[-1] == "fillColor" and len(uploads) == 3

  def test_gradient_uploads_only_what_changed(self, state):
    shader_polygon, state, uploads = state
    rl = shader_polygon.rl
    gradient = shader_polygon.Gradient(start=(0.0, 1.0), end=(0.0, 0.0),
                                       colors=[rl.Color(255, 0, 0, 255), rl.Color(0, 0, 255, 128)], stops=[0.0, 1.0])
    shader_polygon._configure_shader_color(state, None, gradient, rl.Rectangle(0, 0, 100, 100))
    first = len(uploads)
    assert first == 6
    shader_polygon._configure_shader_color(state, None, gradient, rl.Rectangle(0, 0, 100, 100))
    assert len(uploads) == first
    shader_polygon._configure_shader_color(state, None, gradient, rl.Rectangle(0, 10, 100, 100))
    assert sorted(uploads[first:]) == ["gradientEnd", "gradientStart"]
    np.testing.assert_allclose(list(state.gradient_colors_ptr[0:8]), [1, 0, 0, 1, 0, 0, 1, 128 / 255], rtol=1e-6)

  def test_reloading_the_shader_forgets_uploaded_values(self, state, monkeypatch):
    shader_polygon, state, uploads = state
    color = shader_polygon.rl.Color(1, 2, 3, 4)
    shader_polygon._configure_shader_color(state, color, None, shader_polygon.rl.Rectangle(0, 0, 1, 1))
    monkeypatch.setattr(shader_polygon.rl, "unload_shader", lambda shader: None)
    state.initialized = True
    state.cleanup()
    assert state.uniform_values == {}


class TestModelLineEndpoint:
  @pytest.fixture
  def renderer(self):
    from openpilot.selfdrive.ui.onroad.model_renderer import ModelRenderer
    renderer = object.__new__(ModelRenderer)
    renderer._car_space_transform = np.eye(3, dtype=np.float32)
    renderer._clip_region = SimpleNamespace(x=-500, y=-500, width=2000, height=2000)
    return renderer

  @pytest.mark.parametrize("dtype", [np.float32, np.float64])
  @pytest.mark.parametrize("endpoints", [(10, 20), (10, 10), (20, 10)])
  @pytest.mark.parametrize("distance", [5, 10, 15, 20, 25])
  def test_endpoint_matches_numpy_interp(self, renderer, dtype, endpoints, distance):
    line = np.array([[0, 0, 2], [endpoints[0], 2, 3], [endpoints[1], 6, 5], [30, 8, 7]], dtype=dtype)
    endpoint = [distance, np.interp(distance, line[1:3, 0], line[1:3, 1]), np.interp(distance, line[1:3, 0], line[1:3, 2])]
    expanded = np.concatenate((line[:2], np.array([endpoint], dtype=dtype)))
    expected = renderer._map_line_to_polygon(expanded, 0.9, 1.2, 2, distance)
    actual = renderer._map_line_to_polygon(line, 0.9, 1.2, 1, distance)
    np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)


class TestExperimentalGradient:
  @staticmethod
  def renderer(dtype, count, seed):
    from openpilot.selfdrive.ui.mici.onroad.model_renderer import ModelPoints, ModelRenderer
    from openpilot.system.ui.lib.shader_polygon import Gradient
    import pyray as rl
    rng = np.random.default_rng(seed)
    result = object.__new__(ModelRenderer)
    result._experimental_mode = True
    result._rect = rl.Rectangle(-12, 21, 536, 240)
    result._path = ModelPoints(projected_points=rng.uniform(-200, 500, (count, 2)).astype(dtype))
    result._acceleration_x = rng.uniform(-3, 3, 33).astype(dtype)
    result._exp_gradient = Gradient((0., 0.), (0., 1.), [], [])
    return result

  @staticmethod
  def reference(renderer):
    points = renderer._path.projected_points + np.array([renderer._rect.x, renderer._rect.y], dtype=np.float32)
    end = min(len(points) // 2, len(renderer._acceleration_x))
    colors, stops = [], []
    index = 0
    while index < end:
      y = points[index][1]
      if y < renderer._rect.y or y > renderer._rect.y + renderer._rect.height:
        index += 1
        continue
      stop = 1 - (y - renderer._rect.y) / renderer._rect.height
      hue = np.clip(60 + renderer._acceleration_x[index] * 35, 0, 120)
      saturation = min(abs(renderer._acceleration_x[index] * 1.5), 1)
      lightness = np.interp(saturation, [0.0, 1.0], [0.95, 0.62])
      alpha = np.interp(stop, [0.75 / 2.0, 0.75], [0.4, 0.0])
      color = renderer._hsla_to_color(hue / 360.0, saturation, lightness, alpha)
      colors.append((color.r, color.g, color.b, color.a))
      stops.append(stop)
      index += 1 + (1 if index + 2 < end else 0)
    return colors, stops

  @pytest.mark.parametrize('dtype', [np.float32, np.float64])
  @pytest.mark.parametrize('count', [0, 1, 2, 7, 33, 66])
  @pytest.mark.parametrize('seed', [0, 1, 9])
  def test_batched_gradient_matches_original_scalar_colors_and_clipping(self, dtype, count, seed):
    renderer = self.renderer(dtype, count, seed)
    expected_colors, expected_stops = self.reference(renderer)
    renderer._update_experimental_gradient()
    assert [(c.r, c.g, c.b, c.a) for c in renderer._exp_gradient.colors] == expected_colors
    assert renderer._exp_gradient.stops == expected_stops

  def test_interpolation_is_batched_without_dropping_gradient_points(self, monkeypatch):
    renderer = self.renderer(np.float32, 66, 0)
    renderer._path.projected_points[:, 1] = np.linspace(0, 240, 66, dtype=np.float32)
    expected_colors, expected_stops = self.reference(renderer)
    assert len(expected_colors) > 2
    interp, calls = np.interp, []
    clip, clip_calls = np.clip, []

    def observed(*args, **kwargs):
      calls.append(np.asarray(args[0]).size)
      return interp(*args, **kwargs)

    def observed_clip(*args, **kwargs):
      clip_calls.append(np.asarray(args[0]).size)
      return clip(*args, **kwargs)

    monkeypatch.setattr(np, 'interp', observed)
    monkeypatch.setattr(np, 'clip', observed_clip)
    renderer._update_experimental_gradient()
    assert calls == [len(expected_colors), len(expected_colors)]
    assert clip_calls == [len(expected_colors)]
    assert [(c.r, c.g, c.b, c.a) for c in renderer._exp_gradient.colors] == expected_colors
    assert renderer._exp_gradient.stops == expected_stops

  def test_classic_mode_preserves_existing_gradient(self):
    renderer = self.renderer(np.float32, 66, 0)
    colors, stops = renderer._exp_gradient.colors, renderer._exp_gradient.stops
    renderer._experimental_mode = False
    renderer._update_experimental_gradient()
    assert renderer._exp_gradient.colors is colors and renderer._exp_gradient.stops is stops
