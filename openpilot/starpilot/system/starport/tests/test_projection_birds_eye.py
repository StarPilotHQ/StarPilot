"""The birds-eye view follows the AA layout: built only when enabled, drawn as a panel or instead of the camera, toggled by a tap."""
import copy
import unittest
from types import SimpleNamespace

from openpilot.starpilot.system.starport import projection_onroad as projection
from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport
from openpilot.starpilot.system.starport.tests import test_projection_onroad_boundary as boundary

VIEWPORT = (2880, 1080)


class FakeBirdsEye:
  instances: list = []

  def __init__(self, *, text, source):
    self.text, self.source = text, source
    self.rendered = []
    self.panel = self.opacity = self.parent_clip = None
    FakeBirdsEye.instances.append(self)

  def render(self, rect):
    self.rendered.append((rect, self.panel, self.opacity, self.parent_clip))


class TestProjectionBirdsEye(unittest.TestCase):
  def setUp(self):
    FakeBirdsEye.instances = []

  def native(self, *, with_birds_eye=True):
    native, events = boundary.TestProjectionOnroad().dependencies()
    if with_birds_eye:
      native.birds_eye = FakeBirdsEye
      native.birds_eye_text = lambda fonts: ('text', fonts)
    return native, events

  def layout(self, **placement):
    document = default_layout_for_viewport(VIEWPORT)
    document['widgets']['birds_eye'].update(placement)
    return document

  def view(self, native, layout):
    view = projection.ProjectionOnroad(dependencies=native, viewport=VIEWPORT, customization=layout)
    self.addCleanup(view.close)
    return view

  def state(self):
    return SimpleNamespace(customization={'roadColors': {'large': {'pathMode': 'color'}}}, appearance=None)

  def test_disabled_builds_nothing_and_older_dependencies_work(self):
    native, _ = self.native()
    view = self.view(native, self.layout(enabled=False))
    self.assertIsNone(view.birds_eye)
    self.assertIsNone(view.birds_eye_display())
    native, _ = self.native(with_birds_eye=False)
    view = self.view(native, self.layout())
    self.assertIsNone(view.birds_eye)

  def test_overlay_draws_at_placement_above_camera(self):
    native, events = self.native()
    layout = self.layout()
    view = self.view(native, layout)
    self.assertEqual(view.onroad.birds_eye_layer, view._birds_eye_layer)
    renderer = FakeBirdsEye.instances[0]
    self.assertEqual(renderer.text[0], 'text')
    self.assertEqual(renderer.source(), (native.ui_state.sm, native.ui_state.started_frame))
    placed = layout['widgets']['birds_eye']
    content = (30, 30, 2820, 1020)
    camera_state = self.state()
    camera_state.appearance = SimpleNamespace(camera_view=None, show_lead_indicator=False, lead_info_mode=0, lead_info_metric=None)
    camera_state.reverse_driver_camera = False
    view.camera.stream_type = 'road'
    view._camera_layer(content, camera_state)
    self.assertIn('camera_render', events, 'the camera still draws under a panel')
    self.assertEqual(renderer.rendered, [])
    view._birds_eye_layer(content, self.state())
    self.assertEqual(renderer.rendered, [((placed['x'], placed['y'], placed['width'], placed['height']), True, 0.9, content)])
    self.assertEqual(renderer.road_style, {'pathMode': 'color'})

  def test_full_replaces_the_camera(self):
    native, events = self.native()
    view = self.view(native, self.layout(display='full'))
    renderer = FakeBirdsEye.instances[0]
    content = (30, 30, 2820, 1020)
    view.camera_stream = 'stale'
    view._camera_layer(content, self.state())
    self.assertNotIn('camera_render', events)
    self.assertIsNone(view.camera_stream, 'no camera frame is drawn, so none is paced')
    self.assertEqual(renderer.rendered, [(content, False, 1.0, content)])
    view._birds_eye_layer(content, self.state())
    self.assertEqual(len(renderer.rendered), 1, 'the panel layer stays empty in full mode')

  def tap(self, view, x, y, *, dx=0):
    view.handle_touches([SimpleNamespace(kind='down', x=x / VIEWPORT[0], y=y / VIEWPORT[1]),
                         SimpleNamespace(kind='up', x=(x + dx) / VIEWPORT[0], y=y / VIEWPORT[1])])

  def test_tap_toggles_display_for_this_connection_only(self):
    native, _ = self.native()
    layout = self.layout()
    saved = copy.deepcopy(layout)
    view = self.view(native, layout)
    native.ui_state.started = True
    view.render()
    placed = layout['widgets']['birds_eye']
    inside = (placed['x'] + 100, placed['y'] + 100)
    self.tap(view, 20 + 500, 500)
    self.assertEqual(view.birds_eye_display(), 'overlay', 'a tap elsewhere does nothing')
    self.tap(view, *inside, dx=80)
    self.assertEqual(view.birds_eye_display(), 'overlay', 'a drag is not a tap')
    self.tap(view, *inside)
    self.assertEqual(view.birds_eye_display(), 'full')
    self.tap(view, 500, 500)
    self.assertEqual(view.birds_eye_display(), 'overlay', 'in full mode the whole frame switches back')
    self.assertEqual(layout, saved, 'the saved layout is never written from the car screen')
    self.tap(view, *inside)
    view.apply_layout(copy.deepcopy(saved))
    self.assertEqual(view.birds_eye_display(), 'overlay', 'a newly saved layout wins over an earlier tap')

  def test_tap_is_ignored_offroad(self):
    native, _ = self.native()
    layout = self.layout()
    view = self.view(native, layout)
    placed = layout['widgets']['birds_eye']
    self.tap(view, placed['x'] + 50, placed['y'] + 50)
    self.assertEqual(view.birds_eye_display(), 'overlay')

  def test_turned_on_later_builds_on_apply(self):
    native, _ = self.native()
    view = self.view(native, self.layout(enabled=False))
    self.assertIsNone(view.birds_eye)
    view.apply_layout(self.layout())
    self.assertIsNotNone(view.birds_eye)
    self.assertEqual(view.birds_eye_display(), 'overlay')


if __name__ == '__main__':
  unittest.main()
