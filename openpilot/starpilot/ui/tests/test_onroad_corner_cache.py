"""Synthetic GPU ownership tests; no context or visual acceptance is claimed."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from openpilot.starpilot.ui import onroad_corner as corner
from openpilot.system.ui.lib.application import GuiApplication


class TestCornerHintCache(TestCase):
  def setUp(self):
    self.cache = corner.CornerHintCache()
    self.rect = corner.rl.Rectangle(30, 30, 1800, 1020)
    self.texture = SimpleNamespace(id=41, texture=SimpleNamespace(id=42, width=125))
    self.calls = []
    self.patches = [
      patch.object(corner.rl, "is_window_ready", return_value=True),
      patch.object(corner.rl, "load_render_texture", return_value=self.texture),
      patch.object(corner.rl, "unload_render_texture"),
      patch.object(corner.rl, "begin_texture_mode"),
      patch.object(corner.rl, "end_texture_mode"),
      patch.object(corner.rl, "clear_background"),
      patch.object(corner.rl, "rl_set_blend_factors_separate"),
      patch.object(corner.rl, "begin_blend_mode"),
      patch.object(corner.rl, "end_blend_mode"),
      patch.object(corner.rl, "draw_texture_pro"),
      patch.object(corner, "_commands", side_effect=lambda *key: ((self.calls.append, (key,)),)),
    ]
    for item in self.patches:
      item.start()
      self.addCleanup(item.stop)

  def test_render_only_requests_build_and_repeated_frames_reuse_one_texture(self):
    corner.render_corner_hint(self.rect, cache=self.cache)
    corner.rl.load_render_texture.assert_not_called()
    assert self.calls == [(30, 30, 1800, 1020)]
    self.cache.prepare()
    assert self.calls[-1] == (0.0, -895.0, 1800, 1020)
    for _ in range(5):
      corner.render_corner_hint(self.rect, cache=self.cache)
      self.cache.prepare()
    corner.rl.load_render_texture.assert_called_once_with(125, 125)
    assert len(self.calls) == 2
    corner.rl.draw_texture_pro.assert_called()
    args = corner.rl.draw_texture_pro.call_args.args
    assert (args[1].height, args[2].x, args[2].y) == (-125, 30, 925)
    assert args[3].x == args[3].y == 0
    corner.rl.begin_blend_mode.assert_called_with(corner.rl.BlendMode.BLEND_ALPHA_PREMULTIPLY)

  def test_dimension_change_releases_old_texture_and_position_change_reuses(self):
    self.cache.get(self.rect)
    self.cache.prepare()
    assert self.cache.get(corner.rl.Rectangle(200, 100, 1800, 1020)) is self.texture
    changed = corner.rl.Rectangle(0, 0, 2160, 1080)
    assert self.cache.get(changed) is None
    self.cache.prepare()
    corner.rl.unload_render_texture.assert_called_once_with(self.texture)
    assert corner.rl.load_render_texture.call_count == 2
    self.cache.close()
    self.cache.close()
    assert corner.rl.unload_render_texture.call_count == 2

  def test_allocation_failure_uses_original_primitives_without_retry_per_frame(self):
    corner.rl.load_render_texture.side_effect = RuntimeError("GPU allocation failure")
    self.cache.get(self.rect)
    self.cache.prepare()
    for _ in range(4):
      corner.render_corner_hint(self.rect, cache=self.cache)
      self.cache.prepare()
    corner.rl.load_render_texture.assert_called_once()
    corner.rl.draw_texture_pro.assert_not_called()
    assert self.calls == [(30, 30, 1800, 1020)] * 4
    self.cache.close()
    self.cache.get(self.rect)
    self.cache.prepare()
    assert corner.rl.load_render_texture.call_count == 2

  def test_draw_failure_restores_target_and_unloads_then_falls_back(self):
    broken = Mock(side_effect=RuntimeError("geometry draw failure"))
    corner._commands.side_effect = lambda *key: ((broken, ()),)
    self.cache.get(self.rect)
    self.cache.prepare()
    corner.rl.end_texture_mode.assert_called_once()
    corner.rl.unload_render_texture.assert_called_once_with(self.texture)
    assert self.cache.texture is None
    corner._commands.side_effect = lambda *key: ((self.calls.append, (key,)),)
    corner.render_corner_hint(self.rect, cache=self.cache)
    assert self.calls == [(30, 30, 1800, 1020)]

  def test_invalid_native_handle_and_absent_window_do_not_leak(self):
    corner.rl.is_window_ready.return_value = False
    self.cache.get(self.rect)
    self.cache.prepare()
    corner.rl.load_render_texture.assert_not_called()
    corner.rl.is_window_ready.return_value = True
    corner.rl.load_render_texture.return_value = SimpleNamespace(id=43, texture=SimpleNamespace(id=0))
    self.cache.prepare()
    corner.rl.unload_render_texture.assert_called_once()
    corner.rl.begin_texture_mode.assert_not_called()

  def test_application_teardown_releases_view_cache_before_window(self):
    from openpilot.system.ui.lib import application

    self.cache.get(self.rect)
    self.cache.prepare()
    app = GuiApplication.__new__(GuiApplication)
    app._render_prepare = {}
    app.add_render_prepare(self.cache.prepare, self.cache.close)
    app.add_render_prepare(self.cache.prepare, self.cache.close)
    assert len(app._render_prepare) == 1
    app._textures = {}
    app._fonts = {}
    app._fallback_fonts = {}
    app._render_texture = app._burn_in_shader = None
    app.close_ffmpeg = Mock()
    trace = []
    corner.rl.unload_render_texture.side_effect = lambda value: trace.append("texture")
    with patch.object(application, "PC", True), patch.object(application.rl, "close_window", side_effect=lambda: trace.append("window")):
      app.close()
    assert trace == ["texture", "window"]
    self.cache.close()
    corner.rl.unload_render_texture.assert_called_once()
    assert app._render_prepare == {}
    app.remove_render_prepare(self.cache.prepare)

  def test_cached_premultiplied_composition_preserves_direct_rgb(self):
    # Independently compose overlapping straight-alpha primitives over a real
    # background; compare proper coverage with cached premultiplied blitting.
    background = (0.16, 0.31, 0.52)
    layers = (((0.05, 0.02, 0.10), 0.60), ((0.75, 0.55, 1.0), 0.35), ((1.0, 1.0, 1.0), 0.80))
    direct = list(background)
    rgb, coverage, squared_alpha = [0.0, 0.0, 0.0], 0.0, 0.0
    for color, alpha in layers:
      direct = [c * alpha + d * (1 - alpha) for c, d in zip(color, direct, strict=True)]
      rgb = [c * alpha + d * (1 - alpha) for c, d in zip(color, rgb, strict=True)]
      coverage = alpha + coverage * (1 - alpha)
      squared_alpha = alpha * alpha + squared_alpha * (1 - alpha)
    cached = [c + b * (1 - coverage) for c, b in zip(rgb, background, strict=True)]
    broken = [c + b * (1 - squared_alpha) for c, b in zip(rgb, background, strict=True)]
    for expected, actual in zip(direct, cached, strict=True):
      assert abs(expected - actual) < 1e-12
    assert any(abs(a - b) > 0.01 for a, b in zip(direct, broken, strict=True))
    self.cache.get(self.rect)
    self.cache.prepare()
    corner.rl.rl_set_blend_factors_separate.assert_called_once_with(0x0302, 0x0303, 1, 0x0303, 0x8006, 0x8006)
    corner.rl.begin_blend_mode.assert_called_once_with(corner.rl.BlendMode.BLEND_CUSTOM_SEPARATE)
    corner.rl.end_blend_mode.assert_called_once()
