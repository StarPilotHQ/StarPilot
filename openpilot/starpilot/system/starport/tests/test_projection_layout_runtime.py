"""Saved projection layout loading, live reload after a Galaxy save, and frozen-state replacement regressions."""
import copy
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.starpilot.system.starport.display_profile import record_screen, read_screen
from openpilot.starpilot.system.starport.projection_layout import ProjectionLayoutSource, default_layout_for_viewport
from openpilot.starpilot.system.starport.projection_layout_runtime import ProjectionLayoutWatcher, load_projection_layout
from openpilot.starpilot.system.starport.tests import test_projection_onroad_boundary as boundary
projection = boundary.projection
from openpilot.starpilot.ui.onroad_customization import default_document

SCREEN = {'version': 1, 'width': 1280, 'height': 720, 'margin_width': 0,
          'margin_height': 240, 'fps': 60, 'config_index': 0}


class TestProjectionLayoutRuntime(unittest.TestCase):
  def test_projection_passes_its_own_timer_visibility_to_runtime(self):
    from unittest.mock import Mock
    from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
    for native_enabled, projection_enabled in ((False, True), (True, False)):
      with self.subTest(native_enabled=native_enabled, projection_enabled=projection_enabled):
        native, _ = boundary.TestProjectionOnroad().dependencies()
        native.ui_state.started = True
        base = default_document()
        base['layouts']['compact']['stopped_timer']['enabled'] = native_enabled
        state = OnroadState(False, False, None, None, SpeedLimitObservation(), customization=base)
        build = Mock(return_value=SimpleNamespace(onroad=state))
        native.adapter = lambda *_, build=build: SimpleNamespace(build=build)
        layout = default_layout_for_viewport((2880, 1080))
        layout['widgets']['stopped_timer']['enabled'] = projection_enabled
        view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080), customization=layout)
        try:
          view.render()
          self.assertEqual(build.call_args.kwargs['stopped_timer_enabled'], projection_enabled)
          self.assertEqual(base['layouts']['compact']['stopped_timer']['enabled'], native_enabled)
        finally:
          view.close()

  def test_saved_gamma_trial_reaches_only_projection_camera(self):
    from openpilot.starpilot.ui.appearance_preferences import OnroadAppearance
    for enabled in (False, True):
      with self.subTest(enabled=enabled):
        helper = boundary.TestProjectionOnroad()
        native, _ = helper.dependencies()
        document = default_layout_for_viewport((2880, 1080))
        document['largeUiGammaTrial'] = enabled
        view = projection.ProjectionOnroad(dependencies=native, customization=document)
        try:
          self.assertIs(view.camera.large_ui_gamma_trial, enabled)
          # Pacing follows the stream selected during rendering, not before it.
          view.camera.stream_type = 0
          def render(*args, camera=view.camera, **kwargs):
            camera.stream_type = 2
          view.camera.render_camera_model_layer = render
          view._camera_layer(None, SimpleNamespace(customization=default_document(), appearance=OnroadAppearance(),
                                                   reverse_driver_camera=False))
          self.assertEqual(view.camera_stream, 2)
        finally:
          view.close()

  def test_absent_corrupt_wrong_canvas_default_without_rewriting(self):
    with tempfile.TemporaryDirectory() as directory:
      source = ProjectionLayoutSource(Path(directory) / 'layouts/document.json')
      source.prepare()
      viewport = (2880, 1080)
      defaults = default_layout_for_viewport(viewport)
      self.assertEqual(load_projection_layout(viewport, source), defaults)
      for raw in (b'bad', b'x' * 16385, json.dumps(default_layout_for_viewport((1860, 1240))).encode()):
        source.path.write_bytes(raw)
        self.assertEqual(load_projection_layout(viewport, source), defaults)
        self.assertEqual(source.path.read_bytes(), raw)

  def test_unsaved_layout_seeds_comma_display_choices(self):
    with tempfile.TemporaryDirectory() as directory:
      source = ProjectionLayoutSource(Path(directory) / 'layouts/document.json')
      source.prepare()
      viewport = (2880, 1080)
      seed = {'signalBorder': True, 'leadIndicator': True, 'leadInfo': 'Speed', 'unknown': True}
      loaded = load_projection_layout(viewport, source, lambda: seed)
      self.assertEqual(loaded, default_layout_for_viewport(viewport, seed))
      self.assertTrue(loaded['signalBorder'])
      self.assertEqual(loaded['leadInfo'], 'Speed')
      self.assertNotIn('unknown', loaded)
      def broken():
        raise OSError('params unreadable')
      self.assertEqual(load_projection_layout(viewport, source, broken), default_layout_for_viewport(viewport))
      source.path.write_bytes(b'bad')
      self.assertEqual(load_projection_layout(viewport, source, lambda: seed), default_layout_for_viewport(viewport),
                       'a corrupt saved layout is not a first layout')
      watcher = ProjectionLayoutWatcher(viewport, source, lambda: seed)
      watcher.load()
      source.path.unlink()
      self.assertTrue(watcher.poll()['signalBorder'])

  def test_saved_layout_loaded_once_and_next_connection_observes_save(self):
    with tempfile.TemporaryDirectory() as directory:
      source = ProjectionLayoutSource(Path(directory) / 'layouts/document.json')
      source.prepare()
      original = default_layout_for_viewport((2880, 1080))
      source.path.write_text(json.dumps(original))
      connection = load_projection_layout((2880, 1080), source)
      changed = copy.deepcopy(original)
      changed['widgets']['current_speed']['x'] += 60
      source.path.write_text(json.dumps(changed))
      self.assertEqual(connection, original)
      self.assertEqual(load_projection_layout((2880, 1080), source), changed)

  def test_watcher_reports_each_save_once_and_skips_reads_otherwise(self):
    with tempfile.TemporaryDirectory() as directory:
      source = ProjectionLayoutSource(Path(directory) / 'layouts/document.json')
      source.prepare()
      viewport = (2880, 1080)
      original = default_layout_for_viewport(viewport)
      source.path.write_text(json.dumps(original))
      watcher = ProjectionLayoutWatcher(viewport, source)
      self.assertEqual(watcher.load(), original)
      with patch('openpilot.starpilot.system.starport.projection_layout_runtime.read_saved',
                 side_effect=AssertionError('unchanged file reread')):
        self.assertIsNone(watcher.poll())
      changed = copy.deepcopy(original)
      changed['widgets']['current_speed']['x'] += 60
      temporary = source.path.with_suffix('.tmp')
      temporary.write_text(json.dumps(changed))
      os.replace(temporary, source.path)  # Galaxy saves by atomic replace
      self.assertEqual(watcher.poll(), changed)
      self.assertIsNone(watcher.poll())
      source.path.write_bytes(b'x' * 16385)  # unreadable: keep the current layout and retry
      self.assertIsNone(watcher.poll())
      self.assertIsNone(watcher.poll())
      temporary.write_text(json.dumps(original))
      os.replace(temporary, source.path)  # Galaxy saves by atomic replace
      self.assertEqual(watcher.poll(), original)
      source.path.unlink()
      self.assertEqual(watcher.poll(), default_layout_for_viewport(viewport))
      self.assertIsNone(watcher.poll())

  def test_background_thread_hands_over_saved_layouts_without_drawing_thread_reads(self):
    with tempfile.TemporaryDirectory() as directory:
      source = ProjectionLayoutSource(Path(directory) / 'layouts/document.json')
      source.prepare()
      viewport = (2880, 1080)
      original = default_layout_for_viewport(viewport)
      source.path.write_text(json.dumps(original))
      watcher = ProjectionLayoutWatcher(viewport, source)
      watcher.load()
      polled = threading.Event()
      poll = watcher.poll
      def observed_poll():
        try:
          return poll()
        finally:
          polled.set()
      with patch.object(watcher, 'poll', observed_poll), \
           patch('openpilot.starpilot.system.starport.projection_layout_runtime.CHECK_SECONDS', 0.01):
        watcher.start()
        try:
          changed = copy.deepcopy(original)
          changed['widgets']['current_speed']['x'] += 60
          temporary = source.path.with_suffix('.tmp')
          temporary.write_text(json.dumps(changed))
          os.replace(temporary, source.path)
          deadline = time.monotonic() + 2
          taken = None
          while taken is None and time.monotonic() < deadline:
            taken = watcher.take()
            polled.wait(0.05)
          self.assertEqual(taken, changed)
          self.assertIsNone(watcher.take(), 'each save is handed over once')
        finally:
          watcher.close()
      self.assertFalse(watcher._thread.is_alive())

  def test_take_never_touches_files(self):
    watcher = ProjectionLayoutWatcher((2880, 1080), ProjectionLayoutSource('/nonexistent/document.json'))
    with patch('openpilot.starpilot.system.starport.projection_layout_runtime.os.stat', side_effect=AssertionError('stat')), \
         patch('openpilot.starpilot.system.starport.projection_layout_runtime.read_saved', side_effect=AssertionError('read')):
      self.assertIsNone(watcher.take())

  def test_renderer_applies_a_handed_over_layout(self):
    from openpilot.starpilot.system.starport.tests.test_projection_map import FakeFeed, FakeMap
    native, _ = boundary.TestProjectionOnroad().dependencies()
    native.map_overlay, native.map_feed = FakeMap, FakeFeed
    layout = default_layout_for_viewport((2880, 1080))
    saved = copy.deepcopy(layout)
    saved['widgets']['nav_map'].update(enabled=True, x=100, y=200, width=640, height=480, opacity=45)
    saved['largeUiGammaTrial'] = True
    pending = [None, saved]
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080), customization=layout,
                                       layout_watcher=SimpleNamespace(take=lambda: pending.pop(0) if pending else None))
    self.addCleanup(view.close)
    self.assertIsNone(view.map)
    view._projection_customization = view._base_customization = object()
    view.prepare()
    self.assertIs(view.customization, layout)
    view.prepare()
    self.assertIs(view.customization, saved)
    self.assertIsNone(view._projection_customization, 'the cached projected layout is rebuilt')
    self.assertIs(view.camera.large_ui_gamma_trial, True)
    self.assertIsInstance(view.map, FakeMap, 'turning the map on in Galaxy builds it without reconnecting')
    self.assertEqual(view.onroad.map_layer, view._map_layer)

  def test_screen_writer_lock_contention_preserves_previous_receipt(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'screen.json'
      record_screen(SCREEN, path)
      before = path.read_bytes()
      descriptor = os.open(Path(directory) / '.lock', os.O_RDONLY)
      try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(BlockingIOError):
          record_screen(SCREEN | {'config_index': 1}, path)
        self.assertEqual(path.read_bytes(), before)
      finally:
        os.close(descriptor)
      record_screen(SCREEN | {'config_index': 1}, path)
      self.assertEqual(read_screen(path)['config_index'], 1)

  def test_real_onroad_dataclass_replacement_cache_and_shared_color_refresh(self):
    from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
    helper = boundary.TestProjectionOnroad()
    native, _ = helper.dependencies()
    native.ui_state.started = True
    base = default_document()
    before = copy.deepcopy(base)
    state = OnroadState(False, False, None, None, SpeedLimitObservation(), customization=base)
    rendered = []
    native.adapter = lambda *_: SimpleNamespace(build=lambda *_a, **_k: SimpleNamespace(onroad=state))
    layout = default_layout_for_viewport((2880, 1080))
    layout['widgets']['current_speed']['x'] += 60
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080), customization=layout)
    self.addCleanup(view.close)
    view.onroad.render = rendered.append
    # The frame loop must not open saved files; conversion is cached by base identity.
    with patch('openpilot.starpilot.saved_source.read_saved', side_effect=AssertionError('frame file read')):
      view.render()
      view.render()
      self.assertIs(rendered[0].customization, rendered[1].customization)
      self.assertIsNot(rendered[0], state)
      self.assertEqual(state.customization, before)
      self.assertEqual(rendered[0].customization['layouts']['compact'], before['layouts']['compact'])
      old_projected = rendered[-1].customization
      base = copy.deepcopy(base)
      base['palette']['text'] = '#123456FF'
      state = OnroadState(False, False, None, None, SpeedLimitObservation(), customization=base)
      view.render()
      self.assertIsNot(rendered[-1].customization, old_projected)
      self.assertEqual(rendered[-1].customization['palette']['text'], '#123456FF')
      self.assertEqual(rendered[-1].customization['layouts']['large']['current_speed']['x'], 700)

  def test_border_switches_come_from_the_starport_layout(self):
    from openpilot.starpilot.ui.appearance_preferences import OnroadAppearance
    from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
    helper = boundary.TestProjectionOnroad()
    native, _ = helper.dependencies()
    native.ui_state.started = True
    comma = OnroadAppearance(show_signal_border=True, show_blindspot_border=True)  # the comma screen's own choice
    state = OnroadState(False, False, None, None, SpeedLimitObservation(), customization=default_document(), appearance=comma)
    rendered = []
    native.adapter = lambda *_: SimpleNamespace(build=lambda *_a, **_k: SimpleNamespace(onroad=state))
    layout = default_layout_for_viewport((2880, 1080))
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080), customization=layout)
    self.addCleanup(view.close)
    view.onroad.render = rendered.append
    view.render()
    view.render()
    self.assertIs(rendered[0].appearance, rendered[1].appearance, 'cached while nothing changes')
    self.assertEqual((rendered[-1].appearance.show_blindspot_border, rendered[-1].appearance.show_signal_border), (False, False),
                     'both off by default on Android Auto, even when the comma screen has them on')
    self.assertEqual((comma.show_blindspot_border, comma.show_signal_border), (True, True), 'the comma setting is untouched')
    view.apply_layout({**layout, 'blindSpotBorder': True, 'signalBorder': False})
    view.render()
    self.assertEqual((rendered[-1].appearance.show_blindspot_border, rendered[-1].appearance.show_signal_border), (True, False))

  def test_projection_display_choices_keep_the_shared_camera_choice(self):
    from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice, OnroadAppearance
    from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
    helper = boundary.TestProjectionOnroad()
    native, _ = helper.dependencies()
    native.ui_state.started = True
    comma = OnroadAppearance(hide_personality_alerts=True, wheel_pedal_feedback=True, camera_view=CameraViewChoice.DRIVER,
                             driver_camera_on_reverse=True)
    state = OnroadState(False, False, None, None, SpeedLimitObservation(), customization=default_document(), appearance=comma,
                        reverse_driver_camera=True)
    rendered, invalidated = [], []
    adapter = SimpleNamespace(build=lambda *_a, **_k: SimpleNamespace(onroad=state), invalidate_appearance=lambda: invalidated.append(1))
    native.adapter = lambda *_: adapter
    layout = default_layout_for_viewport((2880, 1080))
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080), customization=layout)
    self.addCleanup(view.close)
    view.onroad.render = rendered.append
    view.render()
    appearance = rendered[-1].appearance
    self.assertEqual((appearance.hide_personality_alerts, appearance.wheel_pedal_feedback, appearance.camera_view,
                      appearance.driver_camera_on_reverse, rendered[-1].reverse_driver_camera),
                     (True, False, CameraViewChoice.DRIVER, True, False), 'the car screen never switches to the driver camera')
    self.assertIs(adapter.appearance_override(comma).hide_personality_alerts, True, 'the shared comma preference controls notices')
    view.apply_layout({**layout, 'hidePersonalityAlerts': True, 'wheelPedalFeedback': True})
    self.assertEqual(invalidated, [1])
    view.render()
    self.assertEqual((rendered[-1].appearance.hide_personality_alerts, rendered[-1].appearance.wheel_pedal_feedback), (True, True))
    self.assertIs(adapter.appearance_override(OnroadAppearance()).hide_personality_alerts, False, 'stale AA value cannot override the shared choice')

  def test_lead_marker_and_info_follow_the_aa_layout_only(self):
    from openpilot.starpilot.ui.appearance_preferences import LeadInfoMode, OnroadAppearance
    from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
    helper = boundary.TestProjectionOnroad()
    native, _ = helper.dependencies()
    native.ui_state.started = True
    comma = OnroadAppearance(show_lead_indicator=True, lead_info_mode=LeadInfoMode.DISTANCE, lead_info_metric=False)
    state = OnroadState(False, False, None, None, SpeedLimitObservation(), customization=default_document(), appearance=comma)
    rendered, painted = [], []
    native.adapter = lambda *_: SimpleNamespace(build=lambda *_a, **_k: SimpleNamespace(onroad=state))
    layout = default_layout_for_viewport((2880, 1080))
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080), customization=layout)
    self.addCleanup(view.close)
    view.onroad.render = rendered.append
    view.camera.render_camera_model_layer = lambda _rect, **kwargs: painted.append(kwargs['lead_display'])
    view.camera.stream_type = None

    def lead(document):
      view.apply_layout(document)
      view.render()
      view._camera_layer(None, rendered[-1])
      return rendered[-1].appearance.show_lead_indicator, rendered[-1].appearance.lead_info_mode, painted[-1]

    self.assertEqual(lead(layout), (False, LeadInfoMode.OFF, (False, 0, False)), 'off on the car even with the comma on')
    self.assertEqual(lead({**layout, 'leadInfo': 'Speed'}), (False, LeadInfoMode.OFF, (False, 0, False)), 'info needs the marker')
    self.assertEqual(lead({**layout, 'leadIndicator': True}), (True, LeadInfoMode.OFF, (True, 0, False)))
    self.assertEqual(lead({**layout, 'leadIndicator': True, 'leadInfo': 'Speed'}), (True, LeadInfoMode.SPEED, (True, 2, False)))
    self.assertEqual(lead({**layout, 'leadIndicator': True, 'leadInfo': 'Distance'}), (True, LeadInfoMode.DISTANCE, (True, 1, False)))

  def test_real_onroad_state_unchanged_without_projection_document(self):
    from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
    helper = boundary.TestProjectionOnroad()
    native, _ = helper.dependencies()
    native.ui_state.started = True
    state = OnroadState(False, False, None, None, SpeedLimitObservation())
    rendered = []
    native.adapter = lambda *_: SimpleNamespace(build=lambda *_a, **_k: SimpleNamespace(onroad=state))
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080))
    self.addCleanup(view.close)
    view.onroad.render = rendered.append
    view.render()
    # Only the canvas width changes: touch hit-tests must match where the view draws.
    self.assertEqual(rendered[0], replace(state, viewport_width=2880))


if __name__ == '__main__':
  unittest.main()
