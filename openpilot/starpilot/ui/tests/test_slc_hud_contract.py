"""Controller output and native layouts agree across driver actions and saved layouts."""
from dataclasses import replace
from datetime import datetime
from unittest.mock import Mock

import pytest

from openpilot.starpilot.speed_limits import runtime_settings as settings
from openpilot.starpilot.speed_limits.acceptance import Policy
from openpilot.starpilot.speed_limits.runtime import Action
from openpilot.starpilot.speed_limits.tests import test_runtime_replay as replay
from openpilot.starpilot.speed_limits.tests.test_core_flow import controller, tick
from openpilot.starpilot.ui.onroad_customization import default_document, validate_document
from openpilot.starpilot.ui.onroad import OnroadView
from openpilot.starpilot.ui.onroad_dm import monitor_visible
from openpilot.starpilot.ui.onroad_large_widgets import CurrentSpeedHud, UnifiedSpeedWidget
from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
from openpilot.starpilot.ui.onroad_state import OnroadState, speed_limit_from_message
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.raylib import rl
from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget, set_speed_status, speed_status
from openpilot.starpilot.ui.tests.test_unified_speed_native import drawing as drawing, fonts, foreground_calls, state
from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed


@pytest.mark.parametrize('metric', [False, True])
@pytest.mark.parametrize('offset', [0, 5])
@pytest.mark.parametrize('cluster_delta', [0, 2])
def test_set_limited_tracks_controller_target_without_changing_set(metric, offset, cluster_delta):
  fixture = replay.RuntimeReplayTests()
  fixture.setUp()
  sm, cp = fixture.sm, fixture.cp
  factor = 3.6 if metric else 2.2369362921
  runtime = controller(offset / factor)
  sm['slcDashboardObservation'].speedMps = 55 / factor
  sm['carState'].vEgo = (40 - cluster_delta) / factor
  sm['carState'].vEgoCluster = 40 / factor
  for step, selected in enumerate((45, 55 + offset, 70, 45)):
    sm['carState'].vCruise = (selected - cluster_delta) / factor * 3.6
    sm['carState'].vCruiseCluster = selected / factor * 3.6
    message = tick(runtime, sm, cp, step).message.slcState
    current = OnroadState(True, False, 40 / factor, float(sm['carState'].vCruiseCluster),
                          speed_limit_from_message(message), metric=metric, longitudinal_active=True)
    shown = resolve_unified_speed(current)
    assert shown.set_limited == (selected == 45)
    assert shown.limit_text == str(55 + offset)
    assert shown.max_text == str(selected)
    assert sm['carState'].vCruise == pytest.approx((selected - cluster_delta) / factor * 3.6)


@pytest.mark.parametrize('mode', [settings.PedalOverride.SPEED_REACHED, settings.PedalOverride.SET_SPEED])
def test_controller_messages_keep_set_stable_through_pedal_release_stop_and_resume(mode):
  fixture = replay.RuntimeReplayTests()
  fixture.setUp()
  sm, cp = fixture.sm, fixture.cp
  runtime = controller()
  runtime.settings = replace(runtime.settings, pedal_override=mode)

  def display(step):
    output = tick(runtime, sm, cp, step)
    view = OnroadState(True, False, max(0., sm['carState'].vEgoCluster), 100., speed_limit_from_message(output.message.slcState),
                       metric=True, longitudinal_active=True)
    return view, resolve_unified_speed(view)

  _, first = display(0)
  assert first.limit_in_use and first.max_text == '100'
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 95. / 3.6
  sm['carState'].gasPressed = True
  pressed, shown = display(1)
  assert shown.override_text is None and not shown.limit_in_use
  assert speed_status(pressed, Profile.COMPACT, shown) == 'PEDAL'
  assert set_speed_status(pressed, Profile.COMPACT, shown) is None
  sm['carState'].gasPressed = False
  _, released = display(2)
  assert released.override_text == ('95' if mode is settings.PedalOverride.SPEED_REACHED else '100')
  sm['carState'].standstill = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = -.08
  _, stopped = display(3)
  assert stopped.override_text == released.override_text and stopped.max_text == '100'
  sm['carState'].standstill = False
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 1.
  assert display(4)[1].override_text == released.override_text


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('mode', ('limit', 'override', 'pedal'))
def test_hidden_set_keeps_control_context_inside_moved_confirmation(profile, mode, drawing):
  document = default_document()
  document['showMaxSpeed'] = False
  document['layouts']['compact']['max_speed']['enabled'] = False
  document['layouts']['compact']['speed_limit'].update(x=200, y=16)
  document = validate_document(document)
  current = state(customization=document, slc_prompt_open=True)
  current = replace(current, longitudinal_overridden=mode == 'pedal', speed_limit=replace(current.speed_limit,
    source='vision', pending_source='vision', speed_limit_mps=110 / 3.6, pending_speed_limit_mps=110 / 3.6,
    pending_adjusted_limit_mps=120 / 3.6, presentation_adjusted_limit_mps=120 / 3.6,
    limiting_max_set=mode == 'limit', driver_override_active=mode != 'limit',
    override_basis='pedal' if mode == 'pedal' else 'persistent', effective_cluster_target_mps=128 / 3.6))
  font = fonts(profile)
  if profile == Profile.COMPACT:
    CompactHudRenderer(font, Mock()).render_max_speed(current)
  else:
    UnifiedSpeedWidget(font).render(rl.Rectangle(30, 30, 1800, 1020), current)
  assert not foreground_calls(font.draw)
  SpeedLimitWidget(font, profile).render_confirmation(current)
  rows = [call.args for call in foreground_calls(font.draw)]
  expected = {'limit': 'Now: LIMIT 128', 'override': 'Now: OVERRIDE 128', 'pedal': 'Now: PEDAL'}[mode]
  assert [row[0] for row in rows] == [expected, 'ACCEPT', '120', 'REJECT', 'NEW LIMIT']
  status, accept = rows[:2]
  assert status[4] + font.vertical_ink(*status[:3])[1] < accept[4]
  assert not monitor_visible(Profile.COMPACT, current, fresh=True, onroad=True, top_icons=False)


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('choice', ('accept', 'reject'))
def test_displayed_proposal_matches_the_decision_during_a_held_override(profile, choice, drawing):
  fixture = replay.RuntimeReplayTests()
  fixture.setUp()
  sm, cp = fixture.sm, fixture.cp
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 130.
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 100 / 3.6
  sm['slcDashboardObservation'].speedMps = 100 / 3.6
  runtime = controller(offset=10 / 3.6)
  runtime.settings = replace(runtime.settings, pedal_override=settings.PedalOverride.SPEED_REACHED)
  tick(runtime, sm, cp, 0)
  sm['carState'].gasPressed = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 128 / 3.6
  tick(runtime, sm, cp, 1)
  sm['carState'].gasPressed = False
  tick(runtime, sm, cp, 2)
  runtime.settings = replace(runtime.settings, acceptance=Policy())
  sm['slcDashboardObservation'].speedMps = 110 / 3.6
  pending = tick(runtime, sm, cp, 3).message.slcState
  current = OnroadState(True, False, 128 / 3.6, 130., speed_limit_from_message(pending),
                        metric=True, longitudinal_active=True, slc_prompt_open=True)
  font = fonts(profile)
  SpeedLimitWidget(font, profile).render_confirmation(current)
  assert [call.args[0] for call in foreground_calls(font.draw)] == ['Now: OVERRIDE 128', 'ACCEPT', '120', 'REJECT', 'NEW LIMIT']
  assert resolve_unified_speed(current).override_text == '128'
  action = Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, choice)
  result = tick(runtime, sm, cp, 4, request=action).message.slcState
  assert not result.hasPending
  assert result.acceptedSpeedLimit == pytest.approx((110 if choice == 'accept' else 100) / 3.6)
  assert result.effectiveClusterTarget == pytest.approx((120 if choice == 'accept' else 128) / 3.6)
  assert result.driverOverrideActive == (choice == 'reject')
  assert sm['carState'].vCruise == 130.


def test_moved_transparent_prompt_suppresses_intersecting_current_speed_only(drawing):
  document = default_document()
  document['layouts']['large']['cruise_limits']['x'] = 600
  current = state(customization=validate_document(document), slc_prompt_open=True, viewport_width=2160)
  font = fonts(Profile.LARGE)
  hud = CurrentSpeedHud(font)
  hud.render(rl.Rectangle(180, 30, 2100, 1020), current)
  font.draw.assert_not_called()
  clear = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  hud.render(rl.Rectangle(180, 30, 2100, 1020), clear)
  assert foreground_calls(font.draw)
  font.draw.reset_mock()
  hud.render(rl.Rectangle(30, 30, 1800, 1020), state(slc_prompt_open=True))
  assert foreground_calls(font.draw), 'The default prompt only intersects layout padding, not current-speed ink.'


@pytest.mark.parametrize('widget,method', [('clock', '_clock'), ('driving_mode_descriptions', '_driving_mode'), ('weather', '_weather')])
def test_hidden_set_prompt_suppresses_only_overlapping_optional_text(widget, method, drawing, monkeypatch):
  monkeypatch.setattr('openpilot.starpilot.ui.onroad_weather.draw_weather_icon', Mock())
  document = default_document()
  document['layouts']['compact']['max_speed']['enabled'] = False
  document['layouts']['compact']['speed_limit'].update(x=200, y=16)
  document['layouts']['compact'][widget].update(enabled=True, x=330 if widget == 'weather' else 200, y=0)
  current = state(customization=validate_document(document), slc_prompt_open=True)
  view = OnroadView.__new__(OnroadView)
  view.fonts = fonts(Profile.COMPACT)
  view.wall_clock = lambda: datetime(2026, 11, 5, 12)
  getattr(view, method)(current)
  view.fonts.draw.assert_not_called()
  clear = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  getattr(view, method)(clear)
  assert view.fonts.draw.call_count > 0


def test_projection_monitor_checks_the_same_shifted_bounds_it_draws(drawing):
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, projection_customization
  from openpilot.starpilot.ui.onroad_dm import DriverMonitorLayer
  viewport = (2880, 1440)
  layout = default_layout_for_viewport(viewport)
  layout['widgets']['cruise_limits'].update(x=88, y=1200)
  layout['widgets']['driver_monitor'].update(x=270, y=1200)
  layout['widgets']['torque_bar']['enabled'] = False
  document = projection_customization(layout, default_document())
  current = state(customization=document, slc_prompt_open=True)
  layer = DriverMonitorLayer.__new__(DriverMonitorLayer)
  layer.profile = Profile.LARGE
  layer.renderer = Mock(_is_rhd=False)
  layer.render(current, monitor=None, driver=None, fresh=False, onroad=True, y_offset=360)
  layer.renderer.set_position.assert_called_once_with(270, 1200)
  layer.renderer.set_should_draw.assert_called_once_with(False)
  layer.renderer._render.assert_not_called()
