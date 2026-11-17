"""Live shelf lifetime, saved-layout isolation and authoritative input priority."""

import copy
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.ui import onroad_favorites as native
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.feature_settings_state import row_change
from openpilot.starpilot.ui.layout_preview_renderer import sample_state
from openpilot.starpilot.ui.onroad_cem_gauge import AetherGauge, ROAD_HEIGHT
from openpilot.starpilot.ui.onroad_customization import (
  DEFAULT_WIDGET_ORDER, PARAM_KEY, decode_document, default_document, validate_document,
)
from openpilot.starpilot.ui.onroad_favorites_geometry import COLUMN_BODY, ROW_BODY, intersects
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile


def fixture(*, reduced=False, document=None):
  document = document or default_document()
  document['reduceMotion'] = reduced
  state = OnroadState(True, True, 15, 80, SpeedLimitObservation(), customization=validate_document(document), viewport_width=1860)
  fonts = NS(profile=Profile.LARGE, draw=Mock(), measure=lambda text, role, size: NS(width=len(text) * size / 2, height=size),
             horizontal_ink=lambda text, role, size: (0, len(text) * size / 2), vertical_ink=lambda text, role, size: (0, size))
  emit = Mock(return_value=True)
  drawer = native.OnroadFavorites(fonts, emit)
  data = {'revision': 'one', 'configurable': True, 'options': [], 'slots': [
    {'key': f'key{i}', 'label': label, 'enabled': True, 'show_onroad': True, 'available': True,
     'kind': 'toggle', 'state_label': 'Off'} for i, label in enumerate(('Lane centering', 'Traffic mode', 'Bookmark'))]}
  drawer.update(state, data, 1.)
  return drawer, emit, state, data


def test_overlay_keeps_capture_and_the_original_layout_through_closing():
  drawer, emit, state, data = fixture()
  saved = copy.deepcopy(state.customization)
  assert drawer.press(50, 1020, 1.)
  drawer.release(50, 1020, 1.1)
  assert drawer.is_open and drawer.progress == 0
  assert drawer.target_at(*drawer.menu_buttons()[0][1]) is None
  drawer.update(state, data, 1.2)
  assert 0 < drawer.progress < 1
  drawer.update(state, data, 1.31)
  assert drawer.progress == 1
  presented = drawer.present(state)
  assert presented.quick_select_open
  assert presented.customization is state.customization
  assert state.customization == saved
  drawer.dismiss(2.)
  assert drawer.is_open and drawer.mode == 'closing'
  assert drawer.target_at(*drawer.menu_buttons()[0][1]) is None
  drawer.update(state, data, 2.1)
  assert drawer.is_open and 0 < drawer.progress < 1
  assert drawer.present(state).customization is state.customization
  drawer.update(state, data, 2.16)
  assert not drawer.is_open and drawer.present(state) is state
  assert state.customization == saved
  emit.assert_not_called()


@pytest.mark.parametrize('ordered', (False, True))
def test_live_compositor_keeps_widgets_below_the_shelf_and_alerts_above_it(monkeypatch, ordered):
  from openpilot.starpilot.ui import onroad
  drawer, _, state, data = fixture(reduced=True)
  if ordered:
    state.customization['widgetOrder'] = {'large': list(DEFAULT_WIDGET_ORDER['large'])}
  state = replace(state, stopped_duration_s=120)
  drawer.update(state, data, 1.)
  drawer.open(1.)
  seen = []

  def draw(name):
    return lambda *args, **kwargs: seen.append(name)

  view = onroad.OnroadView.__new__(onroad.OnroadView)
  view.personality = Mock()
  view.fonts = Mock(spec=BitmapFonts, profile=Profile.LARGE)
  view.projection_viewport = None
  view.camera_layer = draw('camera')
  view.background_layer = view.extra_overlays = view.pip_layer = view.map_layer = None
  view.driver_monitor_layer = draw('driver_monitor')
  view.navigation = Mock(spec=onroad.NavigationCard, render=draw('navigation'))
  view.navigation_favorites = Mock(spec=onroad.NavigationFavorites, render=draw('navigation_favorite'))
  view.alert = Mock(spec=onroad.AlertRenderer, render=draw('alert'))
  for attr, label in (('quick_select_layer', 'shelf'), ('_driving_mode', 'mode'), ('_clock', 'clock'),
                      ('_slc_actions', 'guidance'), ('_stopped_timer', 'timer'), ('_traffic_badge', 'traffic'), ('_preview_badge', 'preview')):
    monkeypatch.setattr(view, attr, draw(label), raising=attr != 'quick_select_layer')
  for attr, widget in (('unified_speed', onroad.UnifiedSpeedWidget), ('current_speed', onroad.CurrentSpeedHud),
                       ('cem_gauge', AetherGauge), ('steering_wheel', onroad.SteeringWheelWidget), ('torque_bar', onroad.TorqueBarWidget)):
    setattr(view, attr, Mock(spec=widget, render=draw(attr)))
  for method in ('draw_rectangle_rec', 'draw_rectangle_gradient_v', 'draw_rectangle_lines_ex', 'draw_rectangle_rounded_lines_ex'):
    monkeypatch.setattr(onroad.rl, method, lambda *args: None)
  monkeypatch.setattr(onroad.clip, 'begin_scissor_mode', lambda *args: None)
  monkeypatch.setattr(onroad.clip, 'end_scissor_mode', lambda: None)
  monkeypatch.setattr(onroad, 'status_label', lambda state: None)
  monkeypatch.setattr(onroad, 'render_glow', draw('border'))
  monkeypatch.setattr(onroad, 'render_large_half_borders', lambda *args: None)
  monkeypatch.setattr(onroad, 'render_corner_hint', draw('corner'))
  onroad.OnroadView._large(view, drawer.present(state))
  for widget in ('cem_gauge', 'current_speed', 'driver_monitor', 'steering_wheel', 'torque_bar', 'timer', 'guidance'):
    assert seen.index(widget) < seen.index('shelf')
  assert seen.index('shelf') < seen.index('alert') < seen.index('border')
  assert 'corner' not in seen


def test_corner_reversal_continues_from_current_progress_without_clickthrough():
  drawer, emit, state, data = fixture()
  drawer.open(1.)
  drawer.update(state, data, 1.21)
  drawer.dismiss(2.)
  drawer.update(state, data, 2.04)
  progress = drawer.progress
  assert drawer.press(70, 1010, 2.04)
  assert drawer.mode == 'shelf' and drawer.progress == progress
  drawer.release(70, 1010, 2.05)
  drawer.update(state, data, 2.3)
  assert drawer.is_open and drawer.progress == 1
  emit.assert_not_called()


def test_reduced_motion_opens_and_closes_immediately():
  drawer, _, state, _ = fixture(reduced=True)
  drawer.open(1.)
  assert drawer.progress == 1 and drawer.present(state).quick_select_open
  drawer.dismiss(1.1)
  assert not drawer.is_open and drawer.present(state) is state


def test_pointer_hold_pauses_idle_timeout_and_release_never_activates_edit():
  drawer, emit, state, data = fixture(reduced=True)
  drawer.open(1.)
  point = drawer.menu_buttons()[0][1]
  drawer.press(*point, 2.)
  drawer.update(state, data, 9.)
  assert drawer.is_open and drawer.editing == 0
  drawer.release(*point, 9.1)
  emit.assert_not_called()
  drawer.update(state, data, 15.2)
  assert not drawer.is_open


@pytest.mark.parametrize('change', ('layout', 'viewport', 'drive'))
def test_changed_geometry_or_drive_cancels_held_action_but_equal_reload_does_not(change):
  drawer, emit, state, data = fixture(reduced=True)
  drawer.open(1.)
  obstacles = drawer._corner_obstacles
  for _ in range(10):
    drawer.update(replace(state, customization=copy.deepcopy(state.customization)), data, 1.1)
  assert drawer.is_open and drawer._corner_obstacles is obstacles
  point = drawer.menu_buttons()[0][1]
  drawer.press(*point, 1.2)
  if change == 'layout':
    state.customization['layouts']['large']['clock'].update(enabled=True, x=500, y=900)
  else:
    state = replace(state, **{'viewport': {'viewport_width': 2160}, 'drive': {'drive_frame': 2}}[change])
  drawer.update(state, data, 1.3)
  drawer.release(*point, 1.4)
  assert not drawer.is_open
  emit.assert_not_called()


def test_slot_configuration_change_clears_edit_and_picker_selection():
  drawer, emit, state, data = fixture(reduced=True)
  drawer.open(1.)
  drawer.editing = drawer.selected = 0
  drawer.mode = 'picker'
  drawer.update(state, {**data, 'revision': 'two'}, 1.2)
  assert drawer.mode == 'shelf' and drawer.selected is drawer.editing is None
  emit.assert_not_called()


def test_unavailable_large_control_neither_invokes_nor_opens_configuration():
  profile = Profile.LARGE
  _, emit, state, data = fixture(reduced=True)
  drawer = native.OnroadFavorites(NS(profile=profile), emit)
  data['slots'][0]['available'] = False
  drawer.update(state, data, 1.)
  drawer.open(1.)
  x, y = drawer.menu_buttons()[0][1]
  drawer.press(x, y, 1.1)
  drawer.release(x, y, 1.2)
  assert drawer.mode == 'shelf'
  emit.assert_not_called()


@pytest.mark.parametrize('state_change', ('alert', 'camera', 'confirmation'))
def test_higher_priority_observation_aborts_before_render_and_held_release(state_change):
  drawer, emit, state, data = fixture(reduced=True)
  drawer.open(1.)
  point = drawer.menu_buttons()[0][1]
  drawer.press(*point, 1.1)
  if state_change == 'alert':
    state = replace(state, alert=OnroadAlert(size=AlertSize.SMALL))
  elif state_change == 'camera':
    state = replace(state, camera_available=False)
  else:
    state = replace(sample_state('slc_pending', state.customization), camera_available=True)
  drawer.update(state, data, 1.2)
  drawer.release(*point, 1.3)
  assert not drawer.enabled and not drawer.is_open and drawer.present(state) is state
  emit.assert_not_called()


@pytest.mark.parametrize('confirmation', (False, True))
def test_live_session_routes_the_visible_cue_and_pending_confirmation_to_the_right_owner(confirmation):
  from openpilot.starpilot.ui.runtime_app import StarShellSession
  from openpilot.starpilot.ui.onroad import OnroadView, NavigationCard
  from openpilot.starpilot.ui.shell import ShellMode, ShellView
  drawer, emit, state, data = fixture(reduced=True)
  if confirmation:
    drawer.open(1.)
    state = replace(sample_state('slc_pending', state.customization), camera_available=True, viewport_width=state.viewport_width)
  else:
    state.customization['layouts']['large']['driver_monitor'].update(x=40, y=920)
  session = StarShellSession.__new__(StarShellSession)
  session.favorites = drawer
  session.profile = Profile.LARGE
  session.snapshot = Mock(return_value=NS(onroad=state))
  session._input_snapshot = session.snapshot
  session._update_favorites = Mock(side_effect=lambda observed, now: drawer.update(observed, data, now))
  session.input = Mock(onroad=NS(claimed=True))
  session.view = ShellView.__new__(ShellView)
  session.view.onroad = OnroadView.__new__(OnroadView)
  session.view.onroad.navigation = Mock(spec=NavigationCard)
  with patch('openpilot.starpilot.ui.runtime_app.time.monotonic', return_value=1.1):
    session.press(ShellMode.ONROAD, 70, 1010)
  if confirmation:
    session.input.press.assert_called_once()
    assert not session._favorite_claimed and not drawer.is_open
  else:
    session.input.press.assert_not_called()
    assert session._favorite_claimed
  emit.assert_not_called()


@pytest.mark.parametrize('x,opens,vertical', ((470, True, True), (50, False, False)))
def test_primary_readings_select_one_fixed_fallback_or_refuse_coverage(x, opens, vertical):
  document = default_document()
  document['layouts']['large']['current_speed'].update(x=x, y=730)
  drawer, _, state, _ = fixture(reduced=True, document=document)
  saved = copy.deepcopy(state.customization)
  assert drawer.open(1.) is opens
  assert drawer.vertical is vertical
  if opens:
    assert all(not intersects(COLUMN_BODY, rect) for rect in drawer._protected)
    assert any(intersects(ROW_BODY, rect) for rect in drawer._protected)
    assert drawer.present(state).customization is state.customization
  else:
    assert not drawer.is_open and drawer._notice_until > 1
  assert state.customization == saved


def test_painter_reuses_text_layout_and_keeps_each_action_inside_its_cell():
  drawer, _, state, data = fixture(reduced=True)
  drawer.open(1.)
  with patch.object(drawer, '_wrapped_label', wraps=drawer._wrapped_label) as wrap, \
       patch.object(native.rl, 'draw_rectangle_rounded'), patch.object(native.rl, 'draw_rectangle_rounded_lines_ex'), \
       patch.object(native.rl, 'draw_line_ex'), patch.object(native.rl, 'draw_circle'), \
       patch.object(native.rl, 'load_render_texture') as allocate:
    drawer.render(1.)
    sizes = len(drawer._fit_cache), len(drawer._label_cache)
    for _ in range(100):
      drawer.update(state, data, 1.1)
      drawer.render(1.1)
    assert wrap.call_count == 3
    assert sizes == (len(drawer._fit_cache), len(drawer._label_cache))
    allocate.assert_not_called()
  for rect, center in drawer.menu_buttons():
    assert drawer.target_at(*center)[0] == 'slot'
    assert rect[2:] == (176, 176)


def test_value_truncation_measures_the_font_used_to_paint_it():
  from openpilot.starpilot.ui.presentation import FontRole
  drawer, _, _, _ = fixture()
  drawer.fonts.measure = lambda text, role, size: NS(width=len(text) * (20 if role == FontRole.SEMI_BOLD else 10))
  assert drawer._fit('Unavailable', 22, 144, FontRole.MEDIUM) == 'Unavailable'
  assert drawer._fit('Unavailable', 22, 144) != 'Unavailable'


def test_cem_keeps_its_normal_paint_path_and_live_observation_behind_overlay():
  state = sample_state('cem_curve', default_document())
  gauge = AetherGauge(Mock(spec=BitmapFonts))
  gauge._drive_frame = state.drive_frame
  gauge._road_h_filter.x = ROAD_HEIGHT / 2
  with patch.object(gauge, '_render_unified_road') as paint:
    gauge.render(replace(state, quick_select_open=True))
  paint.assert_called_once()
  assert gauge._speed_mps == state.speed_mps


def test_reduce_motion_uses_guarded_layout_document_without_moving_widgets(tmp_path):
  class Params:
    def get_param_path(self, key):
      return str(tmp_path / key)
  params = Params()
  document = default_document()
  document['layouts']['large']['clock'].update(x=700, y=700, enabled=True)
  path = tmp_path / PARAM_KEY
  import json
  path.write_text(json.dumps(document))
  parked = True
  owner = AppearanceOwner(params, lambda: parked)
  row = next(row for row in owner.snapshot(Profile.LARGE).rows if row.key == 'ReduceMotion')
  request = row_change(row, 1)
  assert request is not None
  assert owner.apply(request)
  saved = decode_document(path.read_bytes())
  assert saved['reduceMotion'] and saved['layouts'] == document['layouts']
  assert not owner.apply(request)
  row = next(row for row in owner.snapshot(Profile.LARGE).rows if row.key == 'ReduceMotion')
  request = row_change(row, -1)
  assert request is not None
  parked = False
  assert not owner.apply(request)
  parked = True
  path.write_bytes(b'corrupt layout')
  assert not owner.apply(request)
  assert path.read_bytes() == b'corrupt layout'
  assert not next(row for row in owner.snapshot(Profile.LARGE).rows if row.key == 'ReduceMotion').available


def test_compact_retains_owner_validation_and_ignores_large_motion_preference():
  _, emit, state, data = fixture(reduced=True)
  data['slots'][0]['available'] = False
  drawer = native.OnroadFavorites(NS(profile=Profile.COMPACT), emit)
  drawer.update(state, data, 1.)
  assert drawer.reduce_motion is False
  drawer.press(80, 80, 1.1)
  drawer.release(80, 80, 1.2)
  emit.assert_called_once()
  assert emit.call_args.args[0].kind == 'activate'
  drawer.update(state, {**data, 'revision': 'two'}, 1.3)
  assert drawer.feedback == 0
