"""CPU-only ownership and cleanup checks for the projection-only road view."""

from typing import Any, cast

import ast
from dataclasses import dataclass
import importlib.util
from pathlib import Path
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).parents[1]
UI_STATE = ROOT.parents[2] / 'selfdrive/ui/ui_state.py'
PRESENTATION = ROOT.parents[1] / 'ui/presentation.py'

spec = importlib.util.spec_from_file_location('private_projection_onroad', ROOT / 'projection_onroad.py')
assert spec is not None and spec.loader is not None
projection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projection)


class FakeGraphics:
  BLACK = object()
  WHITE = object()

  @staticmethod
  def Color(*values): return values

  @staticmethod
  def Rectangle(*values): return values

  @staticmethod
  def draw_rectangle(*_): pass

  @staticmethod
  def draw_rectangle_rounded(*_): pass

  @staticmethod
  def draw_rectangle_rounded_lines_ex(*_): pass

  @staticmethod
  def draw_texture(*_): pass

  @staticmethod
  def unload_texture(*_): pass

  @staticmethod
  def rl_push_matrix(): pass

  @staticmethod
  def rl_pop_matrix(): pass

  @staticmethod
  def rl_translatef(*_): pass


class TestProjectionOnroad(unittest.TestCase):
  def test_jetlink_feed_follows_placement_and_drive_and_closes(self):
    from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport
    from openpilot.starpilot.models.jetlink_status import JetlinkStatus
    native, _ = self.dependencies()
    native.ui_state.params = object()
    feed = Mock()
    feed.snapshot.return_value = JetlinkStatus('iPhone', 'active', 24.6)
    native.jetlink_feed = Mock(return_value=feed)
    layout = default_layout_for_viewport((2880, 1080))
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080), customization=layout)
    self.addCleanup(view.close)
    view.onroad.jetlink_status = SimpleNamespace()
    view.prepare()
    feed.set_enabled.assert_called_with(False)
    layout['widgets']['jetlink_status']['enabled'] = True
    native.ui_state.started = True
    view.prepare()
    feed.set_enabled.assert_called_with(True)
    self.assertEqual(view.onroad.jetlink_status.status, feed.snapshot.return_value)
    native.ui_state.started = False
    view.prepare()
    feed.set_enabled.assert_called_with(False)
    view.close()
    feed.close.assert_called_once()

  def test_navigation_touch_owner_receives_coordinates_and_cancels_offroad(self):
    native, _ = self.dependencies()
    owner = Mock(document={'favorites': []}, error='')
    native.favorites = lambda authorized: owner
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080))
    self.addCleanup(view.close)
    view.onroad.navigation_favorites = SimpleNamespace()
    view.prepare()
    self.assertEqual(view.onroad.navigation_favorites.document, owner.document)
    native.ui_state.started = True
    native.ui_state.started_frame = 9
    view.render()
    view.handle_touches([SimpleNamespace(kind='down', x=.25, y=.5)])
    owner.touch.assert_called_once_with('down', 720, 540, view._state, 9)
    native.ui_state.started = False
    view.handle_touches([SimpleNamespace(kind='up', x=.25, y=.5)])
    owner.cancel.assert_called_once()
    self.assertEqual(owner.touch.call_count, 1)

  def test_car_escape_control_remains_active_on_standby(self):
    from openpilot.starpilot.system.starport.projection_layout import CAR_EXIT, default_layout_for_viewport
    native, _ = self.dependencies()
    requested = Mock()
    layout = default_layout_for_viewport((2880, 1080))
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080), customization=layout,
                                       native_focus=requested)
    self.addCleanup(view.close)
    placed = layout['widgets'][CAR_EXIT]
    x, y = placed['x'] + 48, placed['y'] + 48
    events = [SimpleNamespace(kind='down', x=x / 2880, y=y / 1080),
              SimpleNamespace(kind='up', x=x / 2880, y=y / 1080)]
    view.handle_touches(events)
    requested.assert_called_once_with()

  def dependencies(self, *, fail_view=False, fail_camera=False):
    events = []
    ui = SimpleNamespace(projection_read_only=True, started=False, _offroad_transition_callbacks=[],
                         sm=object(), started_frame=0, is_onroad=lambda: False)

    class Fonts:
      def __init__(self, profile, directory, *, headless_context):
        self.profile = profile
        events.append(('fonts', profile, headless_context))

      def draw(self, *_): events.append('draw')
      def close(self): events.append('fonts_closed')

    class Camera:
      def __init__(self):
        if fail_camera:
          raise RuntimeError('camera shader failed')
        events.append('camera')
        ui._offroad_transition_callbacks.append(self._offroad_transition)

      def _offroad_transition(self): pass
      def close(self): events.append('camera_closed')
      def render_camera_model_layer(self, *_args, **_kwargs): events.append('camera_render')

    class View:
      def __init__(self, *_args, **kwargs):
        if fail_view:
          raise RuntimeError('view texture failed')
        events.append('view')
        self.viewport = kwargs['projection_viewport']
        self.steering_wheel = SimpleNamespace(_texture=None)
        self.driver_monitor_layer = None
        self.alert = SimpleNamespace(text_only_alert_names=frozenset())

      def render(self, *_): events.append('road_render')
      def close(self): events.append('view_closed')

    class Adapter:
      def __init__(self, *_): events.append('adapter')
      def build(self, *_args, **_kwargs): return SimpleNamespace(onroad=OnroadState(False, False, None, None, SpeedLimitObservation()))

    native = SimpleNamespace(rl=FakeGraphics, ui_state=ui, camera=Camera, onroad=View,
                             monitor=lambda *_: SimpleNamespace(render=lambda *_a, **_k: None),
                             fonts=Fonts, font_role=SimpleNamespace(BRAND='brand', MEDIUM='medium'),
                             profile=SimpleNamespace(LARGE='large'), font_directory=lambda: Path('/unused'),
                             adapter=Adapter, current_message=lambda *_a, **_k: None, display_message=lambda *_a, **_k: None,
                             shell_mode=SimpleNamespace(ONROAD='onroad'))
    return native, events

  def test_standby_and_live_view_only_construct_display_owners(self):
    native, events = self.dependencies()
    view = projection.ProjectionOnroad(dependencies=native)
    self.assertEqual((view.width, view.height), (1860, 1240))
    self.assertEqual(view.onroad.viewport, (1860, 1240))
    self.assertEqual(events[:4], [('fonts', 'large', True), 'camera', 'view', 'adapter'])
    view.render()
    self.assertIn('draw', events)
    self.assertNotIn('road_render', events)
    native.ui_state.started = True
    view.render()
    self.assertIn('road_render', events)
    # Touch hit-tests use the car canvas, not the comma's 2160-wide default.
    self.assertEqual(view._state.viewport_width, 1860)
    view.close()
    self.assertEqual(events[-3:], ['view_closed', 'camera_closed', 'fonts_closed'])
    self.assertEqual(native.ui_state._offroad_transition_callbacks, [])

  def test_standby_logo_loads_once_and_is_freed_on_close(self):
    native, _ = self.dependencies()
    logo = SimpleNamespace(width=300, height=250)
    heights, drawn, unloaded = [], [], []
    native.standby_logo = lambda height: heights.append(height) or logo
    native.rl = SimpleNamespace(**{name: getattr(FakeGraphics, name) for name in dir(FakeGraphics) if not name.startswith('_')})
    native.rl.draw_texture = lambda texture, *_: drawn.append(texture)
    native.rl.unload_texture = unloaded.append
    view = projection.ProjectionOnroad(dependencies=native)
    view.render()
    view.render()
    self.assertEqual(heights, [576])
    self.assertEqual(drawn, [logo, logo])
    view.close()
    self.assertEqual(unloaded, [logo])

  def test_standby_without_logo_still_draws_text(self):
    native, events = self.dependencies()
    native.standby_logo = lambda _height: None
    view = projection.ProjectionOnroad(dependencies=native)
    self.addCleanup(view.close)
    view.render()
    self.assertEqual(events.count('draw'), 2)

  def test_side_camera_layer_uses_blinkers_saved_preview_and_projection_placements(self):
    native, events = self.dependencies()
    calls = []

    class Renderer:
      def __init__(self, shape): events.append(('pip', shape))
      def render(self, rect, mask, signals, **kwargs):
        calls.append((rect, mask, signals, kwargs))
        return 'rendered'
      def deactivate(self): events.append('pip_off')
      def close(self): events.append('pip_closed')

    saved = SimpleNamespace(enabled=True, mask='mask', invert=False, on_blinker=True, on_bsm=True)
    car = SimpleNamespace(leftBlinker=True, rightBlinker=False, leftBlindspot=False, rightBlindspot=True)
    layouts = {'pip_left': {'x': 10, 'y': 20, 'enabled': True}, 'pip_right': {'x': 30, 'y': 40, 'enabled': False}}
    native.ui_state.params = object()
    native.display_message = lambda _sm, name, *_a, **_k: car if name == 'carState' else None
    native.pip_renderer = Renderer
    native.read_pip = lambda _params: saved
    native.pip_signals = lambda *values: values
    native.pip_rect = lambda *values: values
    native.pip_widgets = ('pip_left', 'pip_right')
    native.placement = lambda _doc, profile, key: layouts[key] if profile == 'large' else None
    native.widget_size = lambda *_: (600, 600)
    view = projection.ProjectionOnroad(dependencies=native, customization={'blindSpotCamera': True, 'widgets': {}})
    self.assertEqual(view.onroad.pip_layer, view._pip_layer)

    view._pip_layer('content', SimpleNamespace(customization={}))
    self.assertEqual(calls, [('content', 'mask', (True, True, False, False, True),
                              {'enabled': True, 'on_blinker': True, 'on_bsm': True, 'invert': False,
                               'placements': {'left': (10, 20, 600, 600)}})])
    self.assertEqual(view.onroad.alert.text_only_alert_names, projection.BUBBLE_REDUNDANT_ALERTS)

    saved.enabled = False
    view.apply_layout({'blindSpotCamera': False, 'widgets': {}})
    view._pip_read_ns = None
    view._pip_layer('content', SimpleNamespace(customization={}))
    self.assertEqual(len(calls), 1)
    self.assertEqual(events[-1], 'pip_off')
    self.assertEqual(view.onroad.alert.text_only_alert_names, frozenset())
    before = len(events)
    view.render()  # offroad standby releases the cabin camera
    self.assertEqual(events[before:before + 2], ['pip_off', 'draw'])
    view.close()
    self.assertIn('pip_closed', events)

  def test_blind_spot_camera_uses_its_own_switch_fresh_signals_and_enabled_sides(self):
    from openpilot.starpilot.ui.pip_preferences import DEFAULT_MASK
    from openpilot.starpilot.ui.pip_sidecam import Mask, Signals, selected_sides

    native, events = self.dependencies()
    shown = []

    class Renderer:
      def __init__(self, _shape): pass
      def render(self, _rect, mask, signals, **kwargs):
        sides = selected_sides(mask, signals, started=True, enabled=kwargs['enabled'],
                               on_blinker=kwargs['on_blinker'], on_bsm=kwargs['on_bsm'])
        shown.append(tuple(side for side in sides if side in kwargs['placements']))
        return 'rendered' if shown[-1] else 'inactive'
      def deactivate(self): events.append('pip_off')
      def close(self): pass

    # The Android Auto switch works even with the comma camera switches off.
    saved = SimpleNamespace(enabled=False, mask=Mask.parse(DEFAULT_MASK), invert=False, on_blinker=False, on_bsm=False)
    car = SimpleNamespace(leftBlinker=False, rightBlinker=False, leftBlindspot=True, rightBlindspot=False)
    layouts = {key: {'x': 10, 'y': 20, 'enabled': True} for key in ('pip_left', 'pip_right')}
    native.ui_state.params = object()
    native.display_message = lambda *_a, **_k: car
    native.pip_renderer = Renderer
    native.read_pip = lambda _params: saved
    native.pip_signals = Signals
    native.pip_rect = lambda *values: values
    native.pip_widgets = ('pip_left', 'pip_right')
    native.placement = lambda _doc, _profile, key: layouts[key]
    native.widget_size = lambda *_: (600, 600)
    view = projection.ProjectionOnroad(dependencies=native, customization={'blindSpotCamera': True, 'widgets': {}})
    self.addCleanup(view.close)
    state = SimpleNamespace(customization={})
    view._pip_layer('content', state)
    self.assertEqual(shown[-1], ('left',))
    car.leftBlindspot, car.rightBlindspot = False, True
    view._pip_layer('content', state)
    self.assertEqual(shown[-1], ('right',))
    layouts['pip_right']['enabled'] = False
    view._pip_layer('content', state)
    self.assertEqual(shown[-1], ())
    layouts['pip_right']['enabled'] = True
    car.leftBlindspot = True
    view._pip_layer('content', state)
    self.assertEqual(shown[-1], ('right', 'left'))
    native.display_message = lambda *_a, **_k: None
    view._pip_layer('content', state)
    self.assertEqual(shown[-1], (), 'stale car state cannot keep a popup visible')
    native.display_message = lambda *_a, **_k: car
    car.leftBlindspot, car.rightBlindspot, car.leftBlinker = False, False, True
    view._pip_layer('content', state)
    self.assertEqual(shown[-1], (), 'the blind-spot switch alone does not enable blinker popups')
    saved.enabled, saved.on_bsm = True, True
    car.leftBlindspot = True
    view.apply_layout({'blindSpotCamera': False, 'widgets': {}})
    before = len(shown)
    view._pip_layer('content', state)
    self.assertEqual(len(shown), before, 'comma blind-spot settings cannot override Android Auto off')
    self.assertEqual(events[-1], 'pip_off')
    view.apply_layout({'blindSpotCamera': True, 'widgets': {}})
    saved.mask = None
    view._pip_layer('content', state)
    self.assertEqual(len(shown), before, 'invalid camera alignment cannot be rendered')
    self.assertEqual(events[-1], 'pip_off')
    saved.mask = Mask.parse(DEFAULT_MASK)
    view._pip_layer('content', state)
    self.assertEqual(shown[-1], ('left',))
    before = len(events)
    view.render()  # Ending a drive releases the camera even with the AA switch on.
    self.assertEqual(events[before:before + 2], ['pip_off', 'draw'])
    self.assertEqual(view.onroad.alert.text_only_alert_names, frozenset())

  def test_driver_monitor_holds_a_joint_display_observation_across_brief_service_skew(self):
    native, _ = self.dependencies()
    calls = []
    values = {'driverMonitoringState': object(), 'driverStateV2': object()}
    native.ui_state.is_onroad = lambda: True
    native.display_message = lambda _sm, name, *_a, **_k: values[name]
    native.monitor = lambda *_: SimpleNamespace(render=lambda *_a, **kwargs: calls.append(kwargs))
    view = projection.ProjectionOnroad(dependencies=native)
    clock = [1_000_000_000]
    original = projection.time
    projection.time = SimpleNamespace(monotonic_ns=lambda: clock[0])
    try:
      view._driver_monitor_layer(None, object())
      first = calls[-1]
      values['driverMonitoringState'] = None
      clock[0] += projection.DRIVER_MONITOR_HOLD_NS
      view._driver_monitor_layer(None, object())
      held = calls[-1]
      clock[0] += 1
      view._driver_monitor_layer(None, object())
      expired = calls[-1]
    finally:
      projection.time = original
      view.close()
    self.assertTrue(first['fresh'])
    self.assertTrue(held['fresh'])
    self.assertIs(held['monitor'], first['monitor'])
    self.assertFalse(expired['fresh'])

  def test_certificate_notice_text_covers_only_the_last_two_weeks(self):
    self.assertEqual(projection.certificate_notice(None), '')
    self.assertEqual(projection.certificate_notice(15), '')
    self.assertEqual(projection.certificate_notice(-1), '')
    self.assertEqual(projection.certificate_notice(14), 'Heads Up: AA Certificate Expires in 14 Days')
    self.assertEqual(projection.certificate_notice(1), 'Heads Up: AA Certificate Expires in 1 Day')
    self.assertEqual(projection.certificate_notice(0), 'Heads Up: AA Certificate Expires Within a Day')

  def test_certificate_notice_shows_ten_seconds_per_drive_under_real_alerts(self):
    native, _ = self.dependencies()

    @dataclass(frozen=True)
    class Alert:
      size: str = 'none'
      text1: str = ''
      alert_type: str = ''

    real = {'alert': Alert()}
    native.alert, native.alert_size = Alert, SimpleNamespace(NONE='none', SMALL='small')
    native.adapter = lambda *_: SimpleNamespace(build=lambda *_a, **_k: SimpleNamespace(
      onroad=OnroadState(False, False, None, None, SpeedLimitObservation(), alert=cast(Any, real['alert']))))
    view = projection.ProjectionOnroad(dependencies=native, certificate_days=3)
    shown = []
    view.onroad.render = lambda state: shown.append(state.alert.text1)
    clock = [0]
    original = projection.time
    projection.time = SimpleNamespace(monotonic_ns=lambda: clock[0])
    try:
      native.ui_state.started = True
      for clock[0] in (100, 9_000_000_000):
        view.render()
      real['alert'] = Alert('mid', 'Lane Departure Detected')
      view.render()  # a real alert always wins
      real['alert'] = Alert()
      clock[0] = 10_000_000_100
      view.render()  # ten seconds after the first road frame
      native.ui_state.started = False
      view.render()
      native.ui_state.started = True
      view.render()  # the next drive shows it again
    finally:
      projection.time = original
    notice = 'Heads Up: AA Certificate Expires in 3 Days'
    self.assertEqual(shown, [notice, notice, 'Lane Departure Detected', '', notice])
    view.close()

  def test_reconnect_notice_shows_eight_seconds_under_real_alerts_and_before_the_certificate(self):
    native, _ = self.dependencies()

    @dataclass(frozen=True)
    class Alert:
      size: str = 'none'
      text1: str = ''
      alert_type: str = ''

    real = {'alert': Alert()}
    native.alert, native.alert_size = Alert, SimpleNamespace(NONE='none', SMALL='small')
    native.adapter = lambda *_: SimpleNamespace(build=lambda *_a, **_k: SimpleNamespace(
      onroad=OnroadState(False, False, None, None, SpeedLimitObservation(), alert=cast(Any, real['alert']))))
    reconnected = 'Reconnected: comma Wi-Fi Joined Home'
    view = projection.ProjectionOnroad(dependencies=native, certificate_days=3, reconnect_notice=reconnected)
    shown = []
    view.onroad.render = lambda state: shown.append(state.alert.text1)
    clock = [0]
    original = projection.time
    projection.time = SimpleNamespace(monotonic_ns=lambda: clock[0])
    try:
      native.ui_state.started = True
      for clock[0] in (100, 7_000_000_000):
        view.render()
      real['alert'] = Alert('mid', 'Lane Departure Detected')
      view.render()  # a real alert always wins
      real['alert'] = Alert()
      clock[0] = 8_000_000_200
      view.render()  # past eight seconds: the certificate heads-up gets its remaining time
    finally:
      projection.time = original
    self.assertEqual(shown, [reconnected, reconnected, 'Lane Departure Detected', 'Heads Up: AA Certificate Expires in 3 Days'])
    view.close()

  def test_no_certificate_notice_when_far_from_expiry(self):
    native, _ = self.dependencies()
    view = projection.ProjectionOnroad(dependencies=native, certificate_days=200)
    state = SimpleNamespace(alert=None)
    self.assertIs(view._with_certificate_notice(state, 0), state)
    view.close()

  def test_bookmark_saved_shows_two_seconds_under_real_alerts(self):
    native, _ = self.dependencies()

    @dataclass(frozen=True)
    class Alert:
      size: str = 'none'
      text1: str = ''
      alert_type: str = ''

    @dataclass(frozen=True)
    class State:
      alert: Alert = Alert()

    native.alert, native.alert_size = Alert, SimpleNamespace(NONE='none', SMALL='small')
    view = projection.ProjectionOnroad(dependencies=native)
    quiet = State()
    self.assertIs(view._with_bookmark_notice(quiet, 0), quiet, 'nothing until a bookmark is saved')
    view.bookmark_saved(now_ns=1_000)
    shown = view._with_bookmark_notice(quiet, 1_000)
    self.assertEqual((shown.alert.size, shown.alert.text1), ('small', 'Bookmark Saved'))
    self.assertEqual(view._with_bookmark_notice(quiet, 1_000 + projection.BOOKMARK_NOTICE_NS - 1).alert.text1, 'Bookmark Saved')
    real = State(Alert(size='full', text1='Take Control'))
    self.assertIs(view._with_bookmark_notice(real, 2_000), real, 'a real alert wins')
    self.assertIs(view._with_bookmark_notice(quiet, 1_000 + projection.BOOKMARK_NOTICE_NS), quiet)
    view.close()

  def test_mode_change_shows_a_brief_banner(self):
    from openpilot.starpilot.conditional_mode.policy import ModeChoice
    native, _ = self.dependencies()

    @dataclass(frozen=True)
    class Alert:
      size: str = 'none'
      text1: str = ''
      alert_type: str = ''

    @dataclass(frozen=True)
    class State:
      alert: Alert = Alert()
      experimental_enabled: bool = False
      conditional_configured: object = None
      conditional_effective: object = None
      engaged: bool = True

    native.alert, native.alert_size = Alert, SimpleNamespace(NONE='none', SMALL='small')
    view = projection.ProjectionOnroad(dependencies=native)
    chill, experimental = State(), State(experimental_enabled=True)
    self.assertIs(view._with_mode_notice(chill, 0), chill, "the drive's starting mode is not announced")
    shown = view._with_mode_notice(experimental, 1_000)
    self.assertEqual((shown.alert.size, shown.alert.text1), ('small', 'Experimental Mode'))
    real = State(Alert(size='full', text1='Take Control'), experimental_enabled=True)
    self.assertIs(view._with_mode_notice(real, 2_000), real, 'a real alert wins')
    self.assertIs(view._with_mode_notice(experimental, 1_000 + projection.MODE_NOTICE_NS), experimental)
    self.assertEqual(view._with_mode_notice(chill, 5_000_000_000).alert.text1, 'Chill Mode')

    view = projection.ProjectionOnroad(dependencies=native)
    def conditional(reason, effective=True):
      return State(experimental_enabled=effective, conditional_configured=ModeChoice.CEM,
                   conditional_effective=SimpleNamespace(reason=reason, effective_experimental=effective))
    view._with_mode_notice(conditional('lead', True), 0)
    for now, frame in enumerate((conditional('cruising', False), State(conditional_configured=ModeChoice.CEM),
                                 conditional('lead', True)), start=1):
      self.assertIs(view._with_mode_notice(frame, now), frame)
    self.assertEqual(view._with_mode_notice(conditional('manual_chill', False), 10).alert.text1, 'Chill Mode')

    view = projection.ProjectionOnroad(dependencies=native)
    def parked(experimental):
      return State(experimental_enabled=experimental, conditional_configured=ModeChoice.CCM, engaged=False)
    view._with_mode_notice(parked(False), 0)
    self.assertEqual(view._with_mode_notice(parked(True), 1).alert.text1, 'Experimental Mode')
    self.assertEqual(view._with_mode_notice(parked(False), 3_000_000_000).alert.text1, 'Chill Mode')

    view = projection.ProjectionOnroad(dependencies=native)
    view._with_mode_notice(conditional('manual_chill', False), 0)
    for now, frame in enumerate((conditional('lead', True), conditional('manual_experimental', True)), start=1):
      shown = view._with_mode_notice(frame, now)
    self.assertEqual(shown.alert.text1, 'Experimental Mode')
    quiet = view._with_mode_notice(conditional('cruising', False), 10_000_000_000)
    self.assertEqual(quiet.alert.text1, '')
    stopped = State(experimental_enabled=True, conditional_configured=ModeChoice.CEM, engaged=False)
    self.assertIs(view._with_mode_notice(stopped, 20_000_000_000), stopped)
    view.close()

  def test_negotiated_viewport_overrides_landscape_fallback(self):
    native, _ = self.dependencies()
    view = projection.ProjectionOnroad(dependencies=native, viewport=(2880, 1080))
    self.assertEqual(view.onroad.viewport, (2880, 1080))
    view.close()

  def test_failed_renderer_construction_closes_prior_resources(self):
    native, events = self.dependencies(fail_view=True)
    with self.assertRaisesRegex(RuntimeError, 'view texture failed'):
      projection.ProjectionOnroad(dependencies=native)
    self.assertEqual(events[-2:], ['camera_closed', 'fonts_closed'])
    self.assertEqual(native.ui_state._offroad_transition_callbacks, [])

  def test_failed_camera_construction_closes_fonts(self):
    native, events = self.dependencies(fail_camera=True)
    with self.assertRaisesRegex(RuntimeError, 'camera shader failed'):
      projection.ProjectionOnroad(dependencies=native)
    self.assertEqual(events[-1], 'fonts_closed')

  def test_source_avoids_action_owners_and_native_ui_state_has_explicit_read_only_path(self):
    source = (ROOT / 'current_car_ui.py').read_text()
    composition = (ROOT / 'projection_onroad.py').read_text()
    self.assertIn("os.environ['STARPILOT_PROJECTION_READ_ONLY'] = '1'", source)
    self.assertNotIn('StarMainLayout', source)
    self.assertNotIn('PubMaster', source + composition)
    self.assertNotIn('WifiManager', source + composition)
    self.assertNotIn('GalaxyAccessOwner', source + composition)
    self.assertIn('CameraView.__init__(self', composition)
    self.assertNotIn('AugmentedRoadView.__init__(self', composition)
    self.assertIn('headless_context=True', composition)
    self.assertIn('if self.projection_read_only:', UI_STATE.read_text())
    self.assertIn('if ui_state.projection_read_only:', UI_STATE.read_text())
    self.assertIn('headless_context: bool = False', PRESENTATION.read_text())

  def test_projection_update_skips_prime_thread_and_device_power(self):
    tree = ast.parse(UI_STATE.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'UIState')
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'update')
    namespace: dict = {'time': SimpleNamespace(monotonic=lambda: 42.0),
                 'device': SimpleNamespace(update=lambda: self.fail('device power invoked'))}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(UI_STATE), 'exec'), namespace)
    events = []
    fake = SimpleNamespace(projection_read_only=True,
                           sm=SimpleNamespace(update=lambda *_: events.append('sm')),
                           _update_state=lambda: events.append('state'),
                           _update_status=lambda: events.append('status'),
                           update_params=lambda: events.append('params'),
                           _projection_params_at=None,
                           prime_state=SimpleNamespace(start=lambda: self.fail('prime API invoked')))
    namespace['update'](fake)
    namespace['update'](fake)
    self.assertEqual(events, ['sm', 'state', 'status', 'params', 'sm', 'state', 'status'])

  def test_projection_params_exposes_reads_without_write_methods(self):
    tree = ast.parse(UI_STATE.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ProjectionParams')
    namespace: dict = {'Params': object}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(UI_STATE), 'exec'), namespace)
    source = SimpleNamespace(get=lambda key: b'value', get_bool=lambda key: True,
                             get_int=lambda key: 2, get_float=lambda key: 0.5,
                             get_param_path=lambda key: Path('/unused'))
    params = namespace['ProjectionParams'](source)
    self.assertEqual(params.get('x'), b'value')
    self.assertTrue(params.get_bool('x'))
    for method in ('put', 'remove', 'clear_all', 'delete'):
      self.assertFalse(hasattr(params, method))


if __name__ == '__main__':
  unittest.main()
