"""stream_content 'birds_eye': the car screen draws only the birds-eye view, its HUD and the now-playing card while
driving and builds nothing else."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from openpilot.starpilot.system.starport import projection_onroad as projection
from openpilot.starpilot.system.starport.projection_layout import CAR_EXIT, default_layout_for_viewport
from openpilot.starpilot.system.starport.render_profile import RenderSummary
from openpilot.starpilot.system.starport.tests.test_projection_onroad_boundary import FakeGraphics

VIEWPORT = (2880, 1080)
ROOT = Path(projection.__file__).parent


class FakeBirdsEye:
  def __init__(self, *, text, source):
    self.text, self.source = text, source
    self.rendered = []
    self.long_control = True
    self.panel = self.opacity = self.parent_clip = self.road_style = self.metric = None

  def render(self, rect):
    self.rendered.append((rect, self.panel, self.opacity, self.parent_clip))


class TestBirdsEyeOnly(unittest.TestCase):
  def native(self, *, road_style=None, long_control=None):
    events = []
    ui = SimpleNamespace(projection_read_only=True, started=False, sm=object(), started_frame=3, is_metric=False,
                         params=object())

    class Fonts:
      def __init__(self, profile, directory, *, headless_context):
        events.append('fonts')

      def draw(self, text, *_):
        events.append(('text', text))

      def close(self):
        events.append('fonts_closed')

    graphics = SimpleNamespace(**{name: getattr(FakeGraphics, name) for name in dir(FakeGraphics) if not name.startswith('_')})
    graphics.draw_rectangle = lambda *_: events.append('rectangle')
    graphics.Vector2 = lambda *values: values
    graphics.draw_line_ex = lambda *_: events.append('exit_button')
    # Only what the birds-eye-only screen needs: any camera/view/map/pip access raises AttributeError, and without
    # the now-playing factories no card, feed or touch control is built.
    native = SimpleNamespace(rl=graphics, ui_state=ui, fonts=Fonts, font_role=SimpleNamespace(BRAND='brand', MEDIUM='medium'),
                             profile=SimpleNamespace(LARGE='large'), font_directory=lambda: Path('/unused'),
                             birds_eye=FakeBirdsEye, birds_eye_text=lambda fonts: ('text', fonts),
                             road_style=road_style or (lambda ui: {'pathMode': 'color'}),
                             long_control=long_control or (lambda ui: False))
    return native, events

  def view(self, native, layout=None, **kwargs):
    view = projection.ProjectionOnroad(dependencies=native, viewport=VIEWPORT, content='birds_eye',
                                       customization=layout or default_layout_for_viewport(VIEWPORT), **kwargs)
    self.addCleanup(view.close)
    return view

  def test_construction_builds_only_the_birds_eye_view(self):
    native, events = self.native()
    view = self.view(native)
    for name in ('camera', 'onroad', 'monitor', 'pip', 'map', 'map_feed', 'adapter', 'favorites',
                 'now_playing', 'now_playing_feed', 'now_playing_touch', 'jetlink_feed', '_slc_input'):
      self.assertIsNone(getattr(view, name), name)
    self.assertIsInstance(view.birds_eye, FakeBirdsEye)
    self.assertEqual(view.birds_eye.source(), (native.ui_state.sm, 3))
    view.prepare()  # nothing to feed offroad
    view.cancel_touch()
    view.close()
    self.assertEqual(events, ['fonts', 'fonts_closed'])

  def test_unknown_content_is_refused(self):
    native, _ = self.native()
    with self.assertRaises(ValueError):
      projection.ProjectionOnroad(dependencies=native, viewport=VIEWPORT, content='camera')

  def test_driving_draws_only_the_birds_eye_view_over_the_whole_frame(self):
    native, events = self.native()
    view = self.view(native)
    native.ui_state.started = True
    view.prepare()
    view.render()
    renderer = view.birds_eye
    self.assertEqual(renderer.rendered, [((0, 0, 2880, 1080), False, 1.0, None)])
    self.assertEqual(renderer.road_style, {'pathMode': 'color'})
    self.assertFalse(renderer.metric)
    self.assertFalse(renderer.long_control)
    self.assertIsNone(view.camera_stream, 'nothing is camera-paced')
    self.assertNotIn('rectangle', events, 'no standby, border or HUD drawing')
    self.assertNotIn('exit_button', events)
    self.assertFalse([event for event in events if isinstance(event, tuple)], 'no StarPilot brand text')

  def test_birds_eye_view_yields_the_hud_areas_and_the_hud_draws_after_it(self):
    native, _ = self.native()
    order = []
    native.ui_state.CP = 'CP'

    class Hud:
      def __init__(self, text, source):
        self.text, self.source = text, source

      @staticmethod
      def view_rect(width, height):
        return 84, 0, width - 84, height - 64

      def render(self, width, height):
        order.append(('hud', len(view.birds_eye.rendered), width, height, self.source()[1]))

      def close(self):
        order.append('hud_closed')

    native.birds_eye_hud = Hud
    view = self.view(native)
    self.assertEqual(view.hud.text[0], 'text', 'the HUD shares the birds-eye bitmap text drawer')
    native.ui_state.started = True
    view.render()
    self.assertEqual(view.birds_eye.rendered[0][0], (84, 0, VIEWPORT[0] - 84, VIEWPORT[1] - 64))
    self.assertEqual(order, [('hud', 1, *VIEWPORT, 'CP')], 'drawn after the birds-eye view')
    view.close()
    self.assertEqual(order[-1], 'hud_closed')

  def test_dependencies_build_the_hud_lazily(self):
    source = (ROOT / 'projection_onroad.py').read_text()
    self.assertNotIn('birds_eye_hud import', source.split('def birds_eye_dependencies')[0], 'no module-level HUD import')
    self.assertIn('from openpilot.starpilot.system.starport.birds_eye_hud import BirdsEyeHud', source)

  def test_settings_are_read_once_a_second_and_failures_keep_defaults(self):
    reads = []

    def road_style(ui):
      reads.append('style')
      raise ValueError('bad document')
    native, _ = self.native(road_style=road_style, long_control=lambda ui: None)
    view = self.view(native)
    native.ui_state.started = True
    view.prepare()
    view.prepare()
    self.assertEqual(reads, ['style'])
    view.render()
    self.assertEqual(view.birds_eye.road_style, {})
    self.assertTrue(view.birds_eye.long_control, 'unknown longitudinal control keeps the renderer default')

  def test_standby_keeps_the_standby_screen_and_exit_button(self):
    native, events = self.native()
    focused = []
    layout = default_layout_for_viewport(VIEWPORT)
    view = self.view(native, layout, native_focus=lambda: focused.append(True))
    view.render()
    self.assertIn('rectangle', events)
    self.assertIn(('text', 'StarPilot'), events)
    self.assertIn('exit_button', events)
    self.assertEqual(view.birds_eye.rendered, [])
    placed = layout['widgets'][CAR_EXIT]
    self.tap(view, placed['x'] + 48, placed['y'] + 48)
    self.assertEqual(focused, [True])

  def tap(self, view, x, y):
    view.handle_touches([SimpleNamespace(kind='down', x=x / VIEWPORT[0], y=y / VIEWPORT[1]),
                         SimpleNamespace(kind='up', x=x / VIEWPORT[0], y=y / VIEWPORT[1])])

  def test_taps_while_driving_do_nothing(self):
    native, _ = self.native()
    focused = []
    layout = default_layout_for_viewport(VIEWPORT)
    view = self.view(native, layout, native_focus=lambda: focused.append(True))
    native.ui_state.started = True
    view.render()
    self.tap(view, 1440, 540)
    placed = layout['widgets'][CAR_EXIT]
    self.tap(view, placed['x'] + 48, placed['y'] + 48)
    self.assertEqual(focused, [], 'the exit button is not drawn while driving, so it takes no taps')
    self.assertEqual(view.birds_eye_display(), 'full')
    view.render()
    self.assertEqual(len(view.birds_eye.rendered), 2)
    self.assertTrue(all(call[1] is False for call in view.birds_eye.rendered), 'no panel/full toggle')

  def test_layout_changes_only_move_the_exit_button(self):
    native, _ = self.native()
    view = self.view(native)
    layout = default_layout_for_viewport(VIEWPORT)
    view.apply_layout(layout)
    self.assertIs(view.customization, layout)
    self.assertIsNone(view.map)

  def test_dependencies_import_nothing_the_birds_eye_view_does_not_draw(self):
    tree = ast.parse((ROOT / 'projection_onroad.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'birds_eye_dependencies')
    lazy = {node.module for helper in function.body if isinstance(helper, ast.FunctionDef)
            for node in ast.walk(helper) if isinstance(node, ast.ImportFrom)}
    # Imported while the dependencies are built: the function's own statements, not its helpers' bodies.
    modules = {node.module for statement in function.body if not isinstance(statement, ast.FunctionDef)
               for node in ast.walk(statement) if isinstance(node, ast.ImportFrom)}
    self.assertFalse(modules & lazy, 'a module is imported up front or only when its helper builds something')
    for module in ('openpilot.starpilot.system.starport.projection_now_playing', 'openpilot.starpilot.ui.onroad_now_playing',
                   'openpilot.starpilot.system.starport.media_audio', 'openpilot.starpilot.system.starport.birds_eye_hud'):
      self.assertIn(module, lazy)
    for forbidden in ('openpilot.cereal.visionipc', 'openpilot.selfdrive.ui.onroad.cameraview',
                      'openpilot.selfdrive.ui.onroad.augmented_road_view', 'openpilot.selfdrive.ui.onroad.model_renderer',
                      'openpilot.starpilot.ui.onroad', 'openpilot.starpilot.ui.onroad_map', 'openpilot.starpilot.ui.pip_render',
                      'openpilot.starpilot.ui.onroad_dm', 'openpilot.starpilot.system.starport.projection_favorites',
                      'openpilot.starpilot.system.starport.projection_now_playing', 'openpilot.starpilot.models.jetlink_status'):
      self.assertNotIn(forbidden, modules)
    for never in ('openpilot.cereal.visionipc', 'openpilot.selfdrive.ui.onroad.cameraview', 'openpilot.starpilot.ui.onroad',
                  'openpilot.starpilot.ui.onroad_map', 'openpilot.starpilot.models.jetlink_status'):
      self.assertNotIn(never, lazy)
    self.assertIn('openpilot.selfdrive.ui.onroad.birds_eye_renderer', modules)

  # --- now-playing card ---
  TRACK = {'status': 'playing', 'title': 'Song', 'artist': 'Band', 'source': 'spotify'}

  def music(self, layout=None, *, with_hud=True):
    """A birds-eye-only view with the now-playing factories; returns (view, native, widget, feed, order)."""
    from openpilot.starpilot.system.starport import birds_eye_hud
    from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
    native, _ = self.native()
    order = []
    widget = SimpleNamespace(track=None, artwork=None, press=Mock(), release=Mock(), close=lambda: order.append('widget_closed'))
    widget.showing = lambda: widget.track
    widget.render = lambda state: order.append(('card', state))
    feed = Mock(snapshot=Mock(return_value=self.TRACK), artwork=Mock(return_value=('url', None)))
    feed.close = lambda: order.append('feed_closed')

    class Hud:
      view_rect = staticmethod(birds_eye_hud.view_rect)

      def __init__(self, text, source):
        pass

      def render(self, width, height):
        order.append('hud')

      def close(self):
        pass

    if with_hud:
      native.birds_eye_hud = Hud
    native.now_playing_widget = lambda fonts: widget
    native.now_playing_feed = lambda: feed
    native.now_playing_control = Mock()
    native.now_playing_touch = NowPlayingTouch
    view = self.view(native, layout)
    return view, native, widget, feed, order

  def test_card_is_placed_bottom_left_above_the_torque_bar_and_right_of_the_strip(self):
    from openpilot.starpilot.system.starport import birds_eye_hud
    for viewport in (VIEWPORT, (1860, 1080), (1920, 1080), (1280, 720)):
      width, height = viewport
      rect = birds_eye_hud.view_rect(width, height)
      left, bottom = (round(value * height / 1080) for value in projection.BIRDS_EYE_CARD_MARGIN)
      default = dict(default_layout_for_viewport(VIEWPORT)['widgets']['now_playing'], x=width - 30 - 520, y=height - 30 - 96)
      saved = dict(default)
      moved = projection.birds_eye_card_placement(default, width, height, rect)
      self.assertEqual(default, saved, 'the layout document is not changed')
      self.assertEqual((moved['x'], moved['y']), (rect[0] + left, rect[3] - bottom - 96))
      self.assertEqual({key: moved[key] for key in moved if key not in 'xy'}, {key: default[key] for key in default if key not in 'xy'})
      big = projection.birds_eye_card_placement({**default, 'size': 'big', 'x': 0, 'y': 0}, width, height, rect)
      self.assertEqual((big['x'], big['y']), (rect[0] + left, rect[3] - bottom - 264), 'the layout position is for the camera composition')

  def test_card_is_fed_only_while_driving_and_placed_and_draws_after_the_hud(self):
    view, native, widget, feed, order = self.music()
    self.assertIs(view.now_playing, widget)
    view.prepare()
    feed.set_enabled.assert_called_with(False)
    view.render()
    self.assertNotIn('hud', order, 'standby draws neither the HUD nor the card')
    native.ui_state.started = True
    view.prepare()
    feed.set_enabled.assert_called_with(True)
    self.assertEqual((widget.track, widget.artwork), (self.TRACK, ('url', None)))
    view.render()
    self.assertEqual([entry if isinstance(entry, str) else entry[0] for entry in order], ['hud', 'card'])
    state = order[-1][1]
    placed = state.customization['layouts']['large']['now_playing']
    saved = view.customization['widgets']['now_playing']
    from openpilot.starpilot.system.starport import birds_eye_hud
    strip = birds_eye_hud.accel_geometry(1080).strip
    self.assertEqual((placed['x'], placed['y']), (strip + 28, 1080 - 140 - 20 - 96), 'bottom left, above the torque bar')
    self.assertNotEqual(placed['x'], saved['x'])
    self.assertEqual((state.alert.size, state.viewport_width), ('none', VIEWPORT[0]))
    view.render()
    self.assertIs(order[-1][1], state, 'the moved placement is built once per layout, not per frame')
    # A layout with the card off stops the feed; a new layout is read again.
    layout = default_layout_for_viewport(VIEWPORT)
    layout['widgets']['now_playing'].update(enabled=False, x=600, y=300)
    view.apply_layout(layout)
    view.prepare()
    feed.set_enabled.assert_called_with(False)
    view.render()
    redrawn = order[-1][1].customization['layouts']['large']['now_playing']
    self.assertEqual({**redrawn, 'x': 600, 'y': 300}, layout['widgets']['now_playing'], 'a new layout is picked up; only its position is replaced')
    self.assertEqual((redrawn['x'], redrawn['y']), (placed['x'], placed['y']))
    view.close()
    self.assertEqual(order[-2:], ['feed_closed', 'widget_closed'])

  def test_card_without_a_hud_keeps_the_layout_placement(self):
    view, native, _, _, order = self.music(with_hud=False)
    native.ui_state.started = True
    view.prepare()
    view.render()
    self.assertIs(order[-1][1].customization['layouts']['large']['now_playing'], view.customization['widgets']['now_playing'])

  def test_taps_on_the_card_while_driving_control_the_music_where_it_is_drawn(self):
    from openpilot.starpilot.ui.onroad_now_playing import card_layout
    focused = []
    layout = default_layout_for_viewport(VIEWPORT)
    view, native, widget, _, order = self.music(layout)
    view.native_focus = lambda: focused.append(True)
    control = native.now_playing_control
    saved = layout['widgets']['now_playing']
    # Standby: the card is not drawn and takes no taps; the exit button still works.
    view.prepare()
    widget.track = self.TRACK
    self.tap(view, saved['x'] + 48, 880)
    control.assert_not_called()
    placed = layout['widgets'][CAR_EXIT]
    self.tap(view, placed['x'] + 48, placed['y'] + 48)
    self.assertEqual(focused, [True])
    native.ui_state.started = True
    view.prepare()
    view.render()
    drawn = order[-1][1].customization['layouts']['large']['now_playing']
    button = card_layout(drawn, self.TRACK, VIEWPORT[0])[1]['play_pause']
    self.tap(view, saved['x'] + 48, saved['y'] + 60)
    control.assert_not_called()  # where the layout put the card: the torque dock, nothing is drawn there
    self.tap(view, 1440, 400)
    control.assert_not_called()  # the road
    self.tap(view, button.x + button.width / 2, button.y + button.height / 2)
    control.assert_called_once_with('play_pause', {})
    widget.press.assert_called_once_with('play_pause')
    widget.release.assert_called_with('play_pause')
    self.tap(view, placed['x'] + 48, placed['y'] + 48)
    self.assertEqual(focused, [True], 'the exit button still takes no taps while driving')
    # A press is dropped when the session cancels touches.
    control.reset_mock()
    view.handle_touches([SimpleNamespace(kind='down', x=(button.x + 10) / VIEWPORT[0], y=(button.y + 10) / VIEWPORT[1])])
    view.cancel_touch()
    view.handle_touches([SimpleNamespace(kind='up', x=(button.x + 10) / VIEWPORT[0], y=(button.y + 10) / VIEWPORT[1])])
    control.assert_not_called()

  def test_renderer_reads_the_mode_and_paces_birds_eye_by_the_encoder(self):
    source = (ROOT / 'current_car_ui.py').read_text()
    self.assertIn('stream_content = saved_stream_content(ui_state.params)', source)
    self.assertIn('content=stream_content)', source)
    self.assertIn('camera_pacer = CameraPacer() if stream_content == "full" else None', source)
    self.assertIn('print(f"car view content: {stream_content}", flush=True)', source)

def test_render_summary_reports_phases_drops_content_and_pacing():
  summary = RenderSummary(0.0, interval=1.0, content='birds_eye')
  for began in (0.0, 0.5):
    summary.add('update', 0.001)
    summary.add('prepare', 0.0005)
    summary.add('draw', 0.010)
    summary.add('submit', 0.002)
    summary.add('readback_wait', 0.004)
    assert summary.frame_done(began, began + 0.02) is None
  summary.drop()
  report = summary.frame_done(0.98, 1.0, camera_paced=True)
  assert report == {'event': 'render_stats', 'fps': 3.0, 'frame_ms': 20.0, 'update_ms': 0.67, 'prepare_ms': 0.33,
                    'draw_ms': 6.67, 'submit_ms': 1.33, 'readback_wait_ms': 2.67, 'dropped': 1,
                    'content': 'birds_eye', 'paced': 'interval'}
  for began in (1.0, 1.5):
    summary.frame_done(began, began + 0.01, camera_paced=True)
  report = summary.frame_done(1.99, 2.0, camera_paced=True)
  assert report['paced'] == 'camera' and report['dropped'] == 0 and report['draw_ms'] == 0.0


if __name__ == '__main__':
  unittest.main()
