import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from typing import cast
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.live_developer_sidebar import DeveloperMetrics, render_sidebar
from openpilot.starpilot.ui.layout_preview_sidebar import metric_rects


class TestLiveDeveloperSidebar(unittest.TestCase):
  def observe(self, owner, **changes):
    values = {'drive': 10, 'car': NS(aEgo=-1.), 'delay': NS(lateralDelay=.15),
              'torque': NS(useParams=True, frictionCoefficientFiltered=.12, latAccelFactorFiltered=2.5),
              'parameters': NS(steerRatio=16.8, stiffnessFactor=.9), 'metric': True}
    return owner.observe(**(values | changes))

  def test_original_default_order_real_values_and_drive_reset(self):
    owner = DeveloperMetrics()
    rows = self.observe(owner)
    self.assertEqual([r.label for r in rows], ['ACCEL', 'MAX ACCEL', 'LEARNED DELAY', 'LEARNED FRICTION', 'LEARNED LAT ACCEL', 'STEER RATIO', 'STEER STIFF'])
    self.assertEqual(rows[0].value, '-1.00 m/s²')
    self.observe(owner, car=NS(aEgo=5., gasPressed=True))
    self.assertEqual(self.observe(owner)[1].value, '0.00 m/s²')
    self.observe(owner, car=NS(aEgo=2.))
    self.assertEqual(self.observe(owner)[1].value, '2.00 m/s²')
    self.assertEqual(self.observe(owner, drive=11)[1].value, '0.00 m/s²')
    self.assertEqual(self.observe(owner, metric=False)[0].value, '-3.28 ft/s²')

  def test_missing_nonfinite_sources_and_explicit_cp_fallback(self):
    rows = self.observe(DeveloperMetrics(), car=None, delay=None, torque=None, parameters=None)
    self.assertTrue(all(row.value == '—' for row in rows))
    rows = self.observe(DeveloperMetrics(), car=NS(aEgo=float('nan')), delay=NS(lateralDelay=float('inf')))
    self.assertEqual(rows[0].value, '—')
    self.assertEqual(rows[2].value, '—')
    cp = NS(steerActuatorDelay=.2, lateralTuning=NS(which=lambda: 'torque', torque=NS(friction=.13, latAccelFactor=2.)))
    rows = self.observe(DeveloperMetrics(), delay=None, torque=None, cp=cp)
    self.assertEqual(rows[2].label, 'BASE DELAY')
    self.assertEqual(rows[2].value, '0.20000')
    self.assertEqual(rows[3].value, '0.13000')

  def test_live_render_uses_given_metrics_inside_shifted_rail(self):
    fonts = Mock()
    fonts.measure.return_value = NS(width=100, height=30)
    frame = rl.Rectangle(1560, 0, 300, 1080)
    rows = self.observe(DeveloperMetrics())
    with patch('openpilot.starpilot.ui.live_developer_sidebar.rl.draw_rectangle_rec'), \
         patch('openpilot.starpilot.ui.live_developer_sidebar.rl.draw_rectangle_rounded_lines_ex'):
      render_sidebar(fonts, frame, rows)
    self.assertEqual(fonts.draw.call_count, 14)
    self.assertNotIn('SAMPLE DATA', [call.args[0] for call in fonts.draw.call_args_list])
    self.assertTrue(all(frame.x <= r.x and r.x + r.width <= frame.x + frame.width for r in metric_rects(frame, 7)))

  def test_snapshot_current_stale_and_previous_drive_observations(self):
    from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, RuntimeSnapshotAdapter, ui_fake
    from openpilot.starpilot.ui.shell import ShellMode

    ui = ui_fake()
    ui.sm['carState'].aEgo = 1.5
    ui.sm['carState'].canValid = True
    ui.sm['carState'].canTimeout = False
    ui.sm.put('lateralDelay', NS(lateralDelay=.12))
    ui.sm.put('lateralTorqueParameters', NS(useParams=True, frictionCoefficientFiltered=.15, latAccelFactorFiltered=2.4))
    ui.sm.put('vehicleParameters', NS(steerRatio=17., stiffnessFactor=1.1))
    adapter = RuntimeSnapshotAdapter(ui)
    rows = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.developer_metrics
    self.assertEqual(rows[0].value, '4.92 ft/s²')
    self.assertEqual(rows[3].value, '0.15000')
    for service in ('lateralDelay', 'lateralTorqueParameters'):
      ui.sm.logMonoTime[service] = NOW - 300_000_000
    rows = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.developer_metrics
    self.assertEqual(rows[2].value, '0.12000')
    self.assertEqual(rows[3].value, '0.15000')
    for service in ('carState', 'lateralDelay', 'lateralTorqueParameters', 'vehicleParameters'):
      ui.sm.logMonoTime[service] = NOW - 600_000_000
    rows = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.developer_metrics
    self.assertTrue(all(row.value == '—' for row in rows))
    for service in ('carState', 'lateralDelay', 'lateralTorqueParameters', 'vehicleParameters'):
      ui.sm.logMonoTime[service] = NOW
      ui.sm.recv_frame[service] = ui.started_frame
    rows = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.developer_metrics
    self.assertTrue(all(row.value == '—' for row in rows))

  def test_onroad_view_physical_rail_and_projection_omission(self):
    from contextlib import ExitStack
    from pathlib import Path
    from openpilot.starpilot.ui.onroad import OnroadView
    from openpilot.starpilot.ui.appearance_preferences import OnroadAppearance
    from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
    from openpilot.starpilot.ui.presentation import Profile

    fonts = Mock(profile=Profile.LARGE)
    fonts.measure.return_value = NS(width=100, height=30)
    state = OnroadState(False, False, None, None, SpeedLimitObservation(), viewport_width=1560,
                        appearance=OnroadAppearance(show_developer_sidebar=True),
                        developer_metrics=self.observe(DeveloperMetrics()))
    with ExitStack() as stack:
      for name in ('draw_rectangle_rec', 'draw_rectangle_gradient_v', 'draw_rectangle_lines_ex',
                   'draw_rectangle_rounded', 'draw_rectangle_rounded_lines_ex', 'draw_line_ex', 'draw_circle', 'draw_texture_pro',
                   'rl_push_matrix', 'rl_translatef', 'rl_pop_matrix'):
        stack.enter_context(patch('openpilot.starpilot.ui.onroad.rl.' + name))
      stack.enter_context(patch('openpilot.starpilot.ui.onroad.clip.begin_scissor_mode'))
      stack.enter_context(patch('openpilot.starpilot.ui.onroad.clip.end_scissor_mode'))
      stack.enter_context(patch('openpilot.starpilot.ui.onroad.render_corner_hint'))
      stack.enter_context(patch('openpilot.starpilot.ui.onroad.gui_app.add_render_prepare'))
      rail = stack.enter_context(patch('openpilot.starpilot.ui.onroad.render_live_sidebar'))
      view = OnroadView(fonts, Path('/unused'))
      view.personality = Mock()
      view.unified_speed = view.current_speed = view.steering_wheel = Mock()
      view.navigation = view.alert = Mock()
      view._large(state)
      self.assertEqual(rail.call_args.args[1].x, 1560)
      self.assertEqual(rail.call_args.args[1].width, 300)
      for width in (1860, 2160):
        rail.reset_mock()
        expanded = replace(state, viewport_width=width, appearance=replace(state.appearance, show_developer_sidebar=False))
        view._large(expanded)
        rail.assert_not_called()
        self.assertEqual(view.current_speed.render.call_args.args[0].width, width - 60)
        self.assertEqual(view.alert.render.call_args.args[0].width, width - 60)
      rail.reset_mock()
      view.projection_viewport = (1280, 720)
      view._large(state)
      rail.assert_not_called()

  def test_runtime_viewport_follows_sidebar_choice_and_left_menu_without_lag(self):
    from openpilot.starpilot.ui.tests.test_runtime_layouts import runtime_app
    from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
    from openpilot.starpilot.ui.presentation import Profile
    from openpilot.starpilot.ui.settings_state import Destination, SettingsState
    from openpilot.starpilot.ui.shell import ShellMode, ShellSnapshot

    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.profile = Profile.LARGE
    session.selected = Destination.STAR
    session.compact_y = session.compact_scroll_x = 0
    session.sidebar_expanded = True
    state = OnroadState(False, False, None, None, SpeedLimitObservation())
    session.adapter = cast(runtime_app.RuntimeSnapshotAdapter, NS(ui_state=NS(sm=NS(frame=1)), build=Mock()))
    for page_width, shown, expected in ((2160, True, 1860), (2160, False, 2160),
                                        (1860, False, 1860), (1860, True, 1560), (2160, True, 1860)):
      session._onroad_page_width = page_width
      session._snapshot_cache = None
      session.adapter.build.return_value = ShellSnapshot(ShellMode.ONROAD, Mock(), SettingsState(),
        replace(state, appearance=replace(state.appearance, show_developer_sidebar=shown)))
      snapshot = session.snapshot(ShellMode.ONROAD)
      self.assertEqual(snapshot.onroad.viewport_width, expected)
      self.assertIs(session.snapshot(ShellMode.ONROAD), snapshot)
