import numpy as np

from unittest.mock import patch
from dataclasses import replace
import io
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, call
import zlib

from openpilot.starpilot.ui.layout_preview_renderer import LayoutPreviewRenderer, _Canvas, _DriverMonitorArt, _png_rgba, sample_state
from openpilot.starpilot.ui.layout_preview_sidebar import SAMPLE_METRICS, metric_rects, render_sidebar
from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.onroad_personality import PersonalityWidget
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.raylib import rl


class LayoutPreviewRendererTests(unittest.TestCase):
  def test_sidebar_choice_expands_native_preview_and_keeps_sprite_crops_in_saved_coordinates(self):
    document = default_document()
    with patch('openpilot.starpilot.ui.layout_preview_renderer.rl.is_window_ready', return_value=True), \
         patch('openpilot.starpilot.ui.layout_preview_renderer._Canvas') as canvas_type:
      canvas = Mock(profile=Profile.LARGE, viewport=None)
      canvas_type.return_value = canvas
      renderer = LayoutPreviewRenderer()
      for sidebar, width in ((True, 1860), (False, 2160)):
        payload = {'document': {**document, 'developerSidebar': sidebar}, 'profile': 'large', 'scene': 'engaged'}
        renderer(payload)
        state = canvas.render.call_args.args[0]
        self.assertEqual(state.viewport_width, width)
        self.assertIs(state.appearance.show_developer_sidebar, sidebar)
        self.assertEqual(state.customization, document)
        renderer({**payload, 'art': 'backdrop'})
        self.assertEqual(canvas.render.call_args.args[0].viewport_width, width)
        renderer({**payload, 'art': True})
        self.assertEqual(canvas.render_widget_art.call_args.args[0].viewport_width, width)
        self.assertEqual(payload['document']['developerSidebar'], sidebar)

  def test_canvas_omits_sample_sidebar_when_hidden(self):
    from contextlib import ExitStack
    canvas = _Canvas.__new__(_Canvas)
    canvas.profile, canvas.viewport, canvas.map = Profile.LARGE, None, None
    canvas.view = canvas.fonts = canvas.target = Mock()
    canvas._settle = Mock()
    state = sample_state('engaged', default_document())
    state = replace(state, appearance=replace(state.appearance, show_developer_sidebar=True))
    pixels = np.zeros(2160 * 1080 * 4, dtype=np.uint8)
    with ExitStack() as stack:
      for name in ('begin_texture_mode', 'clear_background', 'end_texture_mode', 'image_format', 'image_flip_vertical', 'unload_image'):
        stack.enter_context(patch('openpilot.starpilot.ui.layout_preview_renderer.rl.' + name))
      stack.enter_context(patch('openpilot.starpilot.ui.layout_preview_renderer.rl.load_image_from_texture', return_value=NS(data=pixels)))
      stack.enter_context(patch('openpilot.starpilot.ui.layout_preview_renderer._png_rgba', return_value=b'png'))
      sidebar = stack.enter_context(patch('openpilot.starpilot.ui.layout_preview_renderer.render_sidebar'))
      canvas.render(state, 'engaged')
      sidebar.assert_called_once()
      sidebar.reset_mock()
      canvas.render(replace(state, viewport_width=2160, appearance=replace(state.appearance, show_developer_sidebar=False)), 'engaged')
      sidebar.assert_not_called()

  def test_native_and_projection_previews_supply_timer_sample(self):
    from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport
    from openpilot.starpilot.ui.layout_preview_transport import SCENES
    from openpilot.starpilot.ui.onroad_customization import STOPPED_TIMER_WIDGET
    native = default_document()
    native['layouts']['compact'][STOPPED_TIMER_WIDGET].update(enabled=True, y=2)
    projection = default_layout_for_viewport((2880, 1080))
    projection['widgets'][STOPPED_TIMER_WIDGET].update(enabled=True, x=450, y=600)
    payloads = [('compact', native), ('projection', {'base': default_document(), 'layout': projection})]
    with patch('openpilot.starpilot.ui.layout_preview_renderer.rl.is_window_ready', return_value=True), \
         patch('openpilot.starpilot.ui.layout_preview_renderer._Canvas') as canvas_type:
      for profile, document in payloads:
        renderer = LayoutPreviewRenderer()
        renderer({'profile': profile, 'document': document, 'scene': 'stopped'})
        state, scene = canvas_type.return_value.render.call_args.args
        self.assertEqual(scene, 'stopped')
        self.assertEqual(state.speed_mps, 0)
        self.assertEqual(state.stopped_duration_s, 134)
        layout_profile = 'large' if profile == 'projection' else profile
        self.assertTrue(state.customization['layouts'][layout_profile][STOPPED_TIMER_WIDGET]['enabled'])
        if profile == 'projection':
          self.assertEqual(canvas_type.call_args.args, (Profile.LARGE, (2880, 1080)))
          self.assertEqual(state.customization['layouts']['large'][STOPPED_TIMER_WIDGET]['x'], 450)
        for scene in SCENES - {'stopped'}:
          with self.subTest(profile=profile, scene=scene):
            renderer({'profile': profile, 'document': document, 'scene': scene})
            state = canvas_type.return_value.render.call_args.args[0]
            self.assertIsNone(state.stopped_duration_s)
        renderer.close()

  def test_big_and_projection_previews_supply_route_and_timer_samples_but_compact_does_not(self):
    from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport
    from openpilot.starpilot.ui.onroad_customization import STOPPED_TIMER_WIDGET
    native = default_document()
    native['layouts']['large'][STOPPED_TIMER_WIDGET]['enabled'] = True
    native['layouts']['large']['nav_map'].update(enabled=True)
    payloads = [('large', native, Profile.LARGE, None),
                ('projection', {'base': default_document(), 'layout': default_layout_for_viewport((2880, 1080))}, Profile.LARGE, (2880, 1080)),
                ('compact', default_document(), Profile.COMPACT, None)]
    with patch('openpilot.starpilot.ui.layout_preview_renderer.rl.is_window_ready', return_value=True), \
         patch('openpilot.starpilot.ui.layout_preview_renderer._Canvas') as canvas_type:
      for profile, document, expected, viewport in payloads:
        renderer = LayoutPreviewRenderer()
        renderer({'profile': profile, 'document': document, 'scene': 'stopped'})
        self.assertEqual(canvas_type.call_args.args, (expected, viewport))
        state = canvas_type.return_value.render.call_args.args[0]
        self.assertEqual(state.navigation is not None, expected == Profile.LARGE, profile)
        if profile == 'large':
          self.assertEqual(state.stopped_duration_s, 134)
          self.assertTrue(state.customization['layouts']['large']['nav_card']['enabled'])
        renderer.close()

  def test_big_preview_fits_widgets_to_the_road_that_is_showing(self):
    from openpilot.starpilot.ui.onroad_customization import widget_size
    document = default_document()
    from openpilot.starpilot.ui.large_speed_geometry import confirmation_footprint
    area = confirmation_footprint('large')
    card = max(widget_size(document, 'large', 'cruise_limits')[0], area['x'] + area['width'])
    document['layouts']['large']['cruise_limits']['x'] = 2130 - card
    document['layouts']['large']['steering_wheel']['x'] = -270
    with patch('openpilot.starpilot.ui.layout_preview_renderer.rl.is_window_ready', return_value=True), \
         patch('openpilot.starpilot.ui.layout_preview_renderer._Canvas') as canvas_type:
      renderer = LayoutPreviewRenderer()
      for sidebar, art, width, viewport in ((False, None, 2160, 2160), (True, None, 1860, 1860), (False, True, 1860, 2160)):
        payload = {'profile': 'large', 'scene': 'engaged', 'document': {**document, 'developerSidebar': sidebar}}
        if art:
          payload['art'] = True
          canvas_type.return_value.render_widget_art.return_value = b'png'
        renderer(payload)
        call = canvas_type.return_value.render_widget_art if art else canvas_type.return_value.render
        state = call.call_args.args[0]
        large = state.customization['layouts']['large']
        self.assertEqual(state.viewport_width, viewport)
        self.assertEqual(large['cruise_limits']['x'], 2130 - card if width == 2160 else 1830 - card)
        self.assertEqual(large['steering_wheel']['x'], -270 if width == 2160 else 30)
      renderer.close()

  def test_fixed_scenes_have_no_camera_or_live_input(self):
    document = default_document()
    expected = {
      "engaged": (True, True), "aol": (True, False), "long_only": (False, True),
      "experimental": (True, True), "braking": (True, True), "slc_pending": (True, True),
      "cem_stop_light": (True, True), "cem_lead": (True, True), "cem_curve": (True, True),
    }
    for name, axes in expected.items():
      with self.subTest(name=name):
        state = sample_state(name, document)
        self.assertFalse(state.camera_available)
        self.assertEqual((state.lateral_active, state.longitudinal_active), axes)
        self.assertEqual(state.alert.size, "none")
        self.assertEqual(state.wheel_acceleration, -2.0 if name == "braking" else 0.0)
        self.assertTrue(state.appearance.show_speed_limit_sign)
        if state.lateral_active:
          self.assertGreater(state.torque_utilization, 0.5)
        else:
          self.assertEqual(state.torque_utilization, 0)
    from openpilot.starpilot.ui.onroad_state import slc_controls
    pending = sample_state("slc_pending", document)
    for profile in (Profile.LARGE, Profile.COMPACT):
      self.assertEqual([item.label for item in slc_controls(profile, pending)],
                       ["Accept", "Reject"])
      # The engaged sample already uses its accepted limit, so there is no redundant adopt action.
      self.assertEqual(slc_controls(profile, sample_state("engaged", document)), ())
    manual = sample_state("experimental", document)
    self.assertTrue(manual.experimental_enabled)
    self.assertIsNone(manual.visual_preview)
    self.assertIsNone(manual.conditional_effective)
    for scene, reason in (("cem_stop_light", "STOP LIGHT"), ("cem_lead", "LEAD"), ("cem_curve", "CURVE")):
      with self.subTest(scene=scene):
        sample = sample_state(scene, document)
        self.assertTrue(sample.experimental_enabled)
        self.assertEqual(sample.visual_preview.cem_reason, reason)
        self.assertIsNone(sample.conditional_effective)
        self.assertTrue(sample.visual_preview.layout_sample)  # Drawn as the car would, without replay labels.
        from openpilot.starpilot.ui.onroad_conditional import status
        # The gauge still reads the real reason, but the mode caption itself is not drawn in the editor.
        observed = status(sample)
        assert observed is not None
        family, detail, _ = observed
        self.assertEqual(family, "CEM")
        self.assertFalse(detail.startswith("PREVIEW"))
    self.assertEqual(sample_state("cem_lead", document).speed_mps, 0.0)
    with self.assertRaises(ValueError):
      sample_state("live", document)

  def test_invalid_draft_rejected_before_gl_resources(self):
    renderer = LayoutPreviewRenderer()
    document = default_document()
    document["layouts"]["large"]["current_speed"]["x"] = -200
    with patch("openpilot.starpilot.ui.layout_preview_renderer._Canvas") as canvas:
      with self.assertRaises(ValueError):
        renderer({"document": document, "profile": "large", "scene": "engaged"})
      canvas.assert_not_called()

  def test_profile_swap_and_inactivity_release_owned_canvas(self):
    renderer = LayoutPreviewRenderer()
    document = default_document()
    with patch("openpilot.starpilot.ui.layout_preview_renderer.rl.is_window_ready", return_value=True), \
         patch("openpilot.starpilot.ui.layout_preview_renderer._Canvas") as canvas_type, \
         patch("openpilot.starpilot.ui.layout_preview_renderer.time.monotonic", side_effect=[10.0, 11.0, 50.0]):
      first, second = Mock(profile=Profile.LARGE), Mock(profile=Profile.COMPACT)
      first.render.return_value = second.render.return_value = b"png"
      canvas_type.side_effect = [first, second]
      self.assertEqual(renderer({"document": document, "profile": "large", "scene": "aol"}), b"png")
      self.assertEqual(renderer({"document": document, "profile": "compact", "scene": "aol"}), b"png")
      first.close.assert_called_once()
      renderer.expire()
      second.close.assert_called_once()

  def test_backdrop_draws_the_real_screen_without_movable_widgets(self):
    renderer = LayoutPreviewRenderer()
    document = default_document()
    with patch("openpilot.starpilot.ui.layout_preview_renderer.rl.is_window_ready", return_value=True), \
         patch("openpilot.starpilot.ui.layout_preview_renderer._Canvas") as canvas_type:
      canvas = Mock(profile=Profile.COMPACT, viewport=None)
      canvas.render.return_value = b"png"
      canvas_type.return_value = canvas
      self.assertEqual(renderer({"document": document, "profile": "compact", "scene": "braking", "art": "backdrop"}), b"png")
    state, scene = canvas.render.call_args.args
    self.assertEqual(scene, "braking")
    self.assertEqual(canvas.render.call_args.kwargs, {})
    # An order naming no widget keeps the ordered path, so no movable widget is drawn.
    self.assertEqual(state.customization["widgetOrder"]["compact"], ["backdrop"])
    self.assertEqual(state.customization["layouts"], document["layouts"])
    with self.assertRaises(ValueError):
      renderer({"document": document, "profile": "compact", "scene": "braking", "art": "widgets"})

  def test_png_encoder_preserves_rgba_pixels(self):
    pixels = bytes((255, 0, 0, 255, 0, 255, 0, 128))
    png = _png_rgba(2, 1, pixels)
    self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
    stream = io.BytesIO(png[8:])
    chunks = {}
    while length_bytes := stream.read(4):
      length = struct.unpack(">I", length_bytes)[0]
      kind = stream.read(4)
      data = stream.read(length)
      crc = struct.unpack(">I", stream.read(4))[0]
      self.assertEqual(crc, zlib.crc32(kind + data) & 0xffffffff)
      chunks[kind] = data
    self.assertEqual(struct.unpack(">II", chunks[b"IHDR"][:8]), (2, 1))
    self.assertEqual(zlib.decompress(chunks[b"IDAT"]), b"\0" + pixels)

  def test_compact_sample_seeds_settled_native_widget_filters(self):
    canvas = _Canvas.__new__(_Canvas)
    canvas.profile = Profile.COMPACT
    canvas.viewport = None
    self.enterContext(patch.object(canvas, 'view', NS(personality=PersonalityWidget(), compact_hud=NS(_was_cruise_active=False, _set_speed_alpha=NS(x=0.0),
                                   _wheel_alpha=NS(x=0.0), _wheel_y=NS(x=25.0)),
                     torque_bar=NS(_torque_filter=NS(x=0.0), _alpha_filter=NS(x=0.0))), create=True))
    braking = sample_state("braking", default_document())
    canvas._settle(braking)
    self.assertTrue(canvas.view.compact_hud._was_cruise_active)
    self.assertEqual(canvas.view.compact_hud._set_speed_alpha.x, 1.0)
    self.assertGreater(canvas.view.compact_hud._wheel_alpha.x, 200)
    self.assertEqual(canvas.view.compact_hud._wheel_y.x, 0.0)
    self.assertEqual(canvas.view.torque_bar._torque_filter.x, braking.torque_utilization)
    self.assertEqual(canvas.view.torque_bar._alpha_filter.x, 1.0)

  def test_preview_uses_runtime_monitor_occlusion_for_default_and_moved_widgets(self):
    canvas = _Canvas.__new__(_Canvas)
    canvas.profile = Profile.COMPACT
    canvas.viewport = None
    self.enterContext(patch.object(canvas, 'view', NS(compact_hud=NS(_set_speed_alpha=NS(x=1.0))), create=True))
    canvas.monitor = _DriverMonitorArt.__new__(_DriverMonitorArt)
    canvas.monitor.profile = Profile.COMPACT
    canvas.monitor.renderer = Mock()
    matrix = Mock()
    matrix.attach_mock(canvas.monitor.renderer._render, 'render')
    state = sample_state("engaged", default_document())
    with patch("openpilot.starpilot.ui.layout_preview_renderer.draw_widget_frame"), \
         patch.multiple(rl, rl_push_matrix=matrix.push, rl_translatef=matrix.translate,
                        rl_scalef=matrix.scale, rl_pop_matrix=matrix.pop):
      canvas._render_driver_monitor(rl.Rectangle(), state)
      canvas.monitor.renderer._render.assert_not_called()
      self.assertEqual(matrix.mock_calls, [])
      state.customization["layouts"]["compact"]["driver_monitor"].update(x=200, y=100)
      canvas._render_driver_monitor(rl.Rectangle(), state)
      canvas.monitor.renderer._render.assert_called_once()
      self.assertEqual(matrix.mock_calls, [call.push(), call.translate(200, 100, 0), call.scale(1, 1, 1),
                                          call.translate(-200, -100, 0), call.render(canvas.monitor.renderer._rect), call.pop()])
      matrix.reset_mock()
      canvas.monitor.renderer._render.reset_mock()
      state.customization["layouts"]["compact"]["driver_monitor"].update(x=16, y=10)
      canvas.view.compact_hud._set_speed_alpha.x = 0.0
      canvas._render_driver_monitor(rl.Rectangle(), state)
      canvas.monitor.renderer._render.assert_called_once()
      self.assertEqual(matrix.mock_calls, [call.push(), call.translate(16, 10, 0), call.scale(1, 1, 1),
                                          call.translate(-16, -10, 0), call.render(canvas.monitor.renderer._rect), call.pop()])

  def test_big_sidebar_matches_original_right_rail_and_is_sample_only(self):
    frame = rl.Rectangle(1860, 0, 300, 1080)
    rects = metric_rects(frame, len(SAMPLE_METRICS))
    self.assertEqual(len(rects), 7)
    self.assertEqual([(r.x, r.y, r.width, r.height) for r in rects],
                     [(1873, y, 275, 126) for y in (24, 174, 324, 474, 624, 774, 924)])
    self.assertEqual([metric.label for metric in SAMPLE_METRICS],
                     ["ACCEL", "STEER DELAY", "FRICTION", "LAT ACCEL", "LATERAL %", "TORQUE %", "CHESTNUT"])
    with self.assertRaises(ValueError):
      metric_rects(frame, 8)

  def test_sidebar_render_uses_caller_supplied_values_without_live_state(self):
    fonts = Mock()
    fonts.measure.return_value = NS(width=80, height=25)
    metric = (SAMPLE_METRICS[0],)
    with patch("openpilot.starpilot.ui.layout_preview_sidebar.rl.draw_rectangle_rec"), \
         patch("openpilot.starpilot.ui.layout_preview_sidebar.rl.draw_rectangle_rounded"), \
         patch("openpilot.starpilot.ui.layout_preview_sidebar.rl.draw_rectangle_rounded_lines_ex"):
      render_sidebar(fonts, rl.Rectangle(1860, 0, 300, 1080), metric)
    labels = [call.args[0] for call in fonts.draw.call_args_list]
    self.assertEqual(labels, ["ACCEL", "1.08 ft/s²"])


if __name__ == "__main__":
  unittest.main()


def test_native_preview_draws_independent_camera_circles_without_camera_input():
  canvas = _Canvas.__new__(_Canvas)
  canvas.profile = Profile.COMPACT
  document = default_document()
  state = sample_state('engaged', document)
  with patch('openpilot.starpilot.ui.layout_preview_renderer.rl.draw_circle_v') as circle, \
       patch('openpilot.starpilot.ui.layout_preview_renderer.rl.draw_ring') as ring:
    canvas._render_side_cameras(state)
    assert circle.call_count == ring.call_count == 2
    right_center = circle.call_args.args[0]
    document['layouts']['compact']['pip_left']['enabled'] = False
    document['layouts']['compact']['pip_left']['x'] = 150
    circle.reset_mock()
    ring.reset_mock()
    canvas._render_side_cameras(sample_state('engaged', document))
    assert circle.call_count == ring.call_count == 1
    assert circle.call_args.args[0].x == right_center.x
    assert circle.call_args.args[0].y == right_center.y
