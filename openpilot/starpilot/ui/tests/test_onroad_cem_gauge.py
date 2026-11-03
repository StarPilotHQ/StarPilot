"""Read-only Dom CEM visuals with real presentation state and source boundaries."""
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import copy

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.curve_speed.status import CurveObservation
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.conditional_status import ConditionalDisplay
from openpilot.starpilot.ui.onroad_cem_gauge import AetherGauge, IndicatorType, gauge_data
from openpilot.starpilot.ui.onroad_conditional import reason, status, stop_active
from openpilot.starpilot.ui.onroad_customization import CEM_GAUGE_WIDGET, DEFAULT_WIDGET_ORDER, default_document, validate_document
from openpilot.starpilot.ui.onroad_state import (AlertSize, ConditionalLead, OnroadAlert, OnroadState,
                                               SpeedLimitObservation, conditional_lead_from_message)


def state(reason='cem_stop', *, effective=False, **changes):
  value = ConditionalDisplay(ModeChoice.CEM, effective, reason, 8 if reason == 'cem_stop' else 4, 'planner', 1)
  return replace(OnroadState(effective, True, 20., 90., SpeedLimitObservation(), lateral_active=True,
                             longitudinal_active=effective, slc_system_long_available=effective,
                             conditional_configured=ModeChoice.CEM,
                             conditional_effective=value if effective else None,
                             conditional_perception=None if effective else value), **changes)


@pytest.mark.parametrize('missing', ['scene_unavailable', 'unavailable'])
@pytest.mark.parametrize('effective', [False, True])
def test_missing_scene_is_hidden_without_erasing_policy_receipt(missing, effective):
  sample = state(missing, effective=effective)
  raw = sample.conditional_effective or sample.conditional_perception
  assert raw is not None and raw.reason == missing
  assert reason(sample) is None and status(sample) is None
  assert gauge_data(sample) is None and not stop_active(sample)
  assert (sample.conditional_effective or sample.conditional_perception) is raw


@pytest.mark.parametrize('effective', [False, True])
def test_stop_graphic_preserves_informational_and_control_distinction(effective):
  sample = state(effective=effective)
  data = gauge_data(sample)
  assert data is not None and data.indicator_type is IndicatorType.STOP_LIGHT
  assert data.text == 'STOP' and not data.is_numeric and data.unit == ''
  assert stop_active(sample) is effective
  assert sample.longitudinal_active is effective


@pytest.mark.parametrize('kind', ['no_trigger', 'manual_chill', 'manual_experimental', 'ccm_veto', 'unknown_wire_reason'])
def test_gauge_never_infers_trigger_from_raw_lead_or_speed(kind):
  sample = state(kind, conditional_lead=ConditionalLead(12., 0.))
  assert gauge_data(sample) is None


def test_current_lead_stopped_slow_hysteresis_and_absence_are_distinct():
  sample = state('cem_lead', conditional_lead=ConditionalLead(12., .7))
  assert gauge_data(sample).text == 'STOPPED'
  threshold = replace(sample, conditional_lead=ConditionalLead(12., 1.))
  assert gauge_data(threshold).text == 'SLOW'
  assert gauge_data(threshold, lead_stopped=True).text == 'STOPPED'
  assert gauge_data(replace(sample, conditional_lead=None)).text == 'LEAD'


@pytest.mark.parametrize('bad', [None, NS(leadOne=NS(status=False, dRel=12., vLead=0.)),
                              NS(leadOne=NS(status=True, dRel=0., vLead=0.)),
                              NS(leadOne=NS(status=True, dRel=float('nan'), vLead=0.)),
                              NS(leadOne=NS(status=True, dRel=12., vLead=float('inf')))])
def test_missing_or_invalid_radar_metrics_are_not_defaulted_to_stopped(bad):
  assert conditional_lead_from_message(bad) is None


def test_current_radar_metrics_use_native_units():
  assert conditional_lead_from_message(NS(leadOne=NS(status=True, dRel=12., vLead=2.))) == ConditionalLead(12., 2.)


def test_curve_selected_source_retains_speed_units_and_csc_reduction():
  curve = CurveObservation(True, 15., 15., True, True, False, 100., 2., 30., 'curve', 'selected', 'saved', True, False, .005)
  sample = state('cem_curve', effective=True, curve=curve, metric=True)
  data = gauge_data(sample)
  assert data.indicator_type is IndicatorType.ROAD_CURVE and data.indicator_value == .005
  assert (data.text, data.unit, data.reduction_text) == ('54', 'km/h', '-36')
  assert (gauge_data(replace(sample, metric=False)).text, gauge_data(replace(sample, metric=False)).unit) == ('34', 'mph')
  perception = state('cem_curve', curve=curve)
  assert gauge_data(perception).text == '45'  # Actual ego speed; no CSC control authority.
  assert gauge_data(perception).reduction_text == ''


@pytest.mark.parametrize('changes', [{'alert': OnroadAlert(size=AlertSize.FULL, critical=True)},
                                    {'longitudinal_overridden': True}, {'reverse_driver_camera': True}])
def test_safety_and_driver_camera_states_suppress_gauge(changes):
  assert gauge_data(state(**changes)) is None


@pytest.mark.parametrize('camera', [CameraViewChoice.DRIVER, CameraViewChoice.NONE])
def test_gauge_is_hidden_for_non_road_camera(camera):
  sample = state()
  assert gauge_data(replace(sample, appearance=replace(sample.appearance, camera_view=camera))) is None


def test_custom_placement_disable_layer_migration_preserve_existing_widgets():
  older = default_document()
  older['layouts']['large'].pop(CEM_GAUGE_WIDGET)
  older['layouts']['large']['current_speed'].update(x=740, y=90, enabled=False)
  older['widgetOrder'] = {'large': [key for key in DEFAULT_WIDGET_ORDER['large'] if key != CEM_GAUGE_WIDGET]}
  frozen = copy.deepcopy(older)
  migrated = validate_document(older)
  assert older == frozen
  assert {key: value for key, value in migrated['layouts']['large'].items() if key != CEM_GAUGE_WIDGET} == older['layouts']['large']
  assert [key for key in migrated['widgetOrder']['large'] if key != CEM_GAUGE_WIDGET] == older['widgetOrder']['large']
  migrated['layouts']['large'][CEM_GAUGE_WIDGET].update(x=960, y=600, enabled=True)
  assert validate_document(migrated) == migrated
  gauge = AetherGauge(Mock())
  sample = state(customization=migrated)
  with patch.object(gauge, '_render_unified_road') as draw:
    gauge.render(sample)
    # The saved box is inset 44 x 24 inside the gauge's drawing frame, which is centered at (172, 145 - 60).
    assert draw.call_args.args[1:3] == (960. - 44 + 172, 600. - 24 + 85)
    migrated['layouts']['large'][CEM_GAUGE_WIDGET]['enabled'] = False
    draw.reset_mock()
    gauge.render(sample)
    draw.assert_not_called()


def test_missing_receipt_clears_lead_memory_without_cooldown():
  gauge = AetherGauge(Mock())
  with patch.object(gauge, '_render_unified_road') as draw:
    gauge.render(state('cem_lead', conditional_lead=ConditionalLead(12., .7)))
    assert gauge._lead_stopped
    gauge.render(state('scene_unavailable'))
    assert not gauge._lead_stopped
    assert draw.call_count == 1


def test_dom_stop_road_has_fill_chevrons_line_and_pulsing_light():
  gauge = AetherGauge(Mock())
  with ExitStack() as stack:
    draws = {name: stack.enter_context(patch.object(rl, name)) for name in dir(rl) if name.startswith('draw_')}
    stack.enter_context(patch.object(rl, 'get_frame_time', return_value=1 / 60))
    stack.enter_context(patch.object(rl, 'get_time', return_value=1.))
    stack.enter_context(patch.object(gauge, '_draw_mini_cradle'))
    gauge.render(state())
  assert draws['draw_triangle'].call_count == 48
  assert draws['draw_line_ex'].call_count > 48  # Road edges, stop-line glow, and flowing chevrons.
  assert draws['draw_circle_v'].call_count == 5  # Three bulbs plus red-light bloom.


def test_projection_saved_gauge_migration_keeps_native_layout_and_exit_layer():
  from openpilot.starpilot.system.starport.projection_layout import (CAR_EXIT, default_layout, projection_customization,
                                                                      validate_layout)
  from openpilot.starpilot.system.starport.tests.test_projection_layout import SCREEN
  document = default_layout(SCREEN)
  document['widgets'].pop(CEM_GAUGE_WIDGET)
  document['widgetOrder'] = [key for key in document.get('widgetOrder', []) if key != CEM_GAUGE_WIDGET]
  if not document['widgetOrder']:
    document.pop('widgetOrder')
  frozen = copy.deepcopy(document)
  migrated = validate_layout(document, SCREEN)
  assert document == frozen
  assert {key: value for key, value in migrated['widgets'].items() if key != CEM_GAUGE_WIDGET} == document['widgets']
  assert migrated['widgets'][CEM_GAUGE_WIDGET]['enabled']
  base = default_document()
  unchanged = copy.deepcopy(base)
  projected = projection_customization(migrated, base)
  assert base == unchanged and projected['layouts']['compact'] == base['layouts']['compact']
  assert projected['layouts']['large'][CEM_GAUGE_WIDGET]['enabled']
  if 'widgetOrder' in migrated:
    assert migrated['widgetOrder'][-1] == CAR_EXIT


def test_numeric_cradle_measurement_and_draw_share_explicit_bitmap_font_roles():
  from openpilot.starpilot.ui.onroad_cem_gauge import AetherGaugeData
  from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole
  fonts = Mock(spec=BitmapFonts)
  metrics = {'54': NS(width=110., height=90.), '-36': NS(width=55., height=40.), 'km/h': NS(width=66., height=38.)}
  fonts.measure.side_effect = lambda text, _role, _size: metrics[text]
  gauge = AetherGauge(fonts)
  data = AetherGaugeData('54', unit='km/h', reduction_text='-36', is_numeric=True)
  with patch.object(rl, 'draw_line_ex'):
    gauge._draw_mini_cradle(500., 145., data)
  assert [call.args for call in fonts.measure.call_args_list] == [
    ('54', FontRole.BOLD, 75), ('-36', FontRole.MEDIUM, 33), ('km/h', FontRole.MEDIUM, 30)]
  expected = {'54': (FontRole.BOLD, 75, 445., 154.), '-36': (FontRole.MEDIUM, 33, 564., 179.),
              'km/h': (FontRole.MEDIUM, 30, 467., 251.)}
  for text, layout in expected.items():
    calls = [call.args for call in fonts.draw.call_args_list if call.args[0] == text]
    assert len(calls) == 5  # Four Dom shadow samples plus the measured foreground glyph.
    assert calls[-1][1:5] == layout
    assert [call[3:5] for call in calls[:4]] == [(layout[2] + dx, layout[3] + dy)
                                                   for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1))]


def test_text_cradle_uses_profile_measurement_and_keeps_shadow_geometry():
  from openpilot.starpilot.ui.onroad_cem_gauge import AetherGaugeData
  from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole
  fonts = Mock(spec=BitmapFonts)
  fonts.measure.return_value = NS(width=200., height=58.)
  gauge = AetherGauge(fonts)
  gauge._draw_mini_cradle(500., 145., AetherGaugeData('STOPPED'))
  fonts.measure.assert_called_once_with('STOPPED', FontRole.BOLD, 48)
  calls = [call.args for call in fonts.draw.call_args_list]
  assert len(calls) == 5
  assert calls[-1][:5] == ('STOPPED', FontRole.BOLD, 48, 400., 160.)
  assert [call[3:5] for call in calls[:4]] == [(399., 159.), (401., 159.), (399., 161.), (401., 161.)]


@pytest.mark.parametrize('projection', [False, True])
def test_numeric_caption_declared_footprint_fits_actual_bottom_content_edge(projection):
  from openpilot.starpilot.system.starport.projection_layout import (default_layout_for_viewport, layout_metadata_for_viewport,
                                                                      projection_customization, validate_layout_for_viewport)
  from openpilot.starpilot.ui.onroad_customization import customization_metadata
  from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile
  viewport = (2160, 1080)
  if projection:
    document = default_layout_for_viewport(viewport)
    metadata = layout_metadata_for_viewport(viewport)
    placed = document['widgets'][CEM_GAUGE_WIDGET]
    placed.update(x=980, y=774, enabled=True)
    validated = validate_layout_for_viewport(document, viewport)
    customization = projection_customization(validated, default_document())
  else:
    document = default_document()
    metadata = customization_metadata()['profiles']['large']
    placed = document['layouts']['large'][CEM_GAUGE_WIDGET]
    placed.update(x=980, y=774, enabled=True)
    customization = validate_document(document)
  assert metadata['widgets'][CEM_GAUGE_WIDGET]['height'] == 276
  # Both screens clip the onroad content 30 pixels above the physical bottom.
  assert metadata['bounds']['y'] + metadata['bounds']['height'] == 1050
  assert placed['y'] + 276 == 1050
  fonts = Mock(spec=BitmapFonts)
  fonts.measure.side_effect = lambda _text, _role, size: NS(width=80., height=size * Profile.LARGE.font_scale)
  gauge = AetherGauge(fonts)
  with patch.object(rl, 'draw_line_ex'):
    sample = state('cem_curve', customization=customization)
    gauge._draw_mini_cradle(1152., placed['y'] - 24 + 145, gauge_data(sample))
  unit_calls = [call.args for call in fonts.draw.call_args_list if call.args[0] == 'mph']
  assert len(unit_calls) == 5
  unit_size = unit_calls[-1][2]
  assert unit_calls[-1][1] is FontRole.MEDIUM
  assert max(call[4] for call in unit_calls) + unit_size * Profile.LARGE.font_scale <= 1050
  for invalid_y in (775, 800):
    placed['y'] = invalid_y
    with pytest.raises(ValueError):
      if projection:
        validate_layout_for_viewport(document, viewport)
      else:
        validate_document(document)


@pytest.mark.parametrize('projection', [False, True])
@pytest.mark.parametrize('kind', [None, 'scene_unavailable', 'unavailable', 'no_trigger', 'manual_chill',
                                'manual_experimental', 'unknown_wire_reason', 'cem_signal', 'cem_speed',
                                'cem_stop', 'cem_lead', 'cem_curve', 'curve_control'])
def test_gauge_draws_only_for_visible_reasons(projection, kind):
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, projection_customization
  from openpilot.starpilot.ui.tests.test_speed_stack import road

  document = default_document()
  if projection:
    document = projection_customization(default_layout_for_viewport((2160, 1080)), document)
  sample = replace(state(kind if kind and kind != 'curve_control' else 'no_trigger'), customization=document,
                   longitudinal_active=True, slc_system_long_available=True, speed_limit=road().speed_limit)
  if kind is None:
    sample = replace(sample, conditional_perception=None)
  elif kind == 'curve_control':
    curve = CurveObservation(True, 15., 15., True, True, False, 100., 2., 30., 'curve', 'selected', 'saved', True, False, .005)
    sample = replace(sample, curve=curve)
  gauge = AetherGauge(Mock())
  with patch.object(gauge, '_render_unified_road') as draw:
    gauge.render(sample)
  visible = kind in ('cem_stop', 'cem_lead', 'cem_curve', 'curve_control')
  assert (gauge_data(sample) is not None) is visible
  assert draw.called is visible


def test_gauge_box_shrinks_to_its_content_and_saved_positions_keep_the_drawing_in_place():
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, validate_layout_for_viewport
  from openpilot.starpilot.ui.onroad_customization import CEM_GAUGE_FRAME, CEM_GAUGE_INSET, CEM_GAUGE_SIZE, customization_metadata
  widget = customization_metadata()['profiles']['large']['widgets'][CEM_GAUGE_WIDGET]
  assert (widget['width'], widget['height']) == CEM_GAUGE_SIZE == (256, 276)
  # The box stays inside the frame the gauge draws in, so content that fit before still fits.
  assert CEM_GAUGE_INSET[0] + CEM_GAUGE_SIZE[0] <= CEM_GAUGE_FRAME[0]
  assert CEM_GAUGE_INSET[1] + CEM_GAUGE_SIZE[1] == CEM_GAUGE_FRAME[1]
  # The legacy default clears the larger controls; custom positions only gain the inset.
  old = default_document()
  old['version'] = 6
  old['layouts']['large'][CEM_GAUGE_WIDGET].update(x=436, y=75, enabled=True)
  migrated = validate_document(old)
  assert migrated['layouts']['large'][CEM_GAUGE_WIDGET] == {'x': 56, 'y': 536, 'enabled': True}
  assert validate_document(migrated) == migrated
  # An old gauge flush with the bottom edge is still valid and lands flush with it again.
  old['layouts']['large'][CEM_GAUGE_WIDGET].update(x=1486, y=750)
  assert validate_document(old)['layouts']['large'][CEM_GAUGE_WIDGET] == {'x': 1530, 'y': 774, 'enabled': True}
  gauge = AetherGauge(Mock())
  with patch.object(gauge, '_render_unified_road') as draw:
    gauge.render(state('cem_stop', customization=migrated))
  assert draw.call_args.args[1:3] == (184., 597.)
  # Android Auto version 2 layouts move the same way.
  viewport = (2880, 1080)
  aa = default_layout_for_viewport(viewport)
  assert aa['version'] == 6
  aa['version'] = 2
  aa['widgets'][CEM_GAUGE_WIDGET].update(x=900, y=200)
  moved = validate_layout_for_viewport(aa, viewport)
  assert moved['version'] == 6 and (moved['widgets'][CEM_GAUGE_WIDGET]['x'], moved['widgets'][CEM_GAUGE_WIDGET]['y']) == (944, 224)
  assert validate_layout_for_viewport(moved, viewport) == moved


@pytest.mark.parametrize('projection', [False, True])
def test_default_cem_gauge_keeps_left_column_with_personality_monitor_pair(projection):
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, layout_metadata_for_viewport
  from openpilot.starpilot.ui.onroad_customization import customization_metadata

  if projection:
    viewport = (2880, 1080)
    widgets = layout_metadata_for_viewport(viewport)['widgets']
    layout = default_layout_for_viewport(viewport)['widgets']
  else:
    widgets = customization_metadata()['profiles']['large']['widgets']
    layout = default_document()['layouts']['large']
  gauge, monitor, actions = (layout[key] for key in (CEM_GAUGE_WIDGET, 'driver_monitor', 'speed_limit_actions'))
  personality = layout['following_distance']
  assert gauge['x'] + widgets[CEM_GAUGE_WIDGET]['width'] / 2 == 184
  assert personality['x'] + widgets['following_distance']['width'] + 32 == monitor['x']
  assert personality['y'] + widgets['following_distance']['height'] / 2 == monitor['y'] + monitor['size'] / 2
  assert actions['y'] + widgets['speed_limit_actions']['height'] < gauge['y']
  assert gauge['y'] + widgets[CEM_GAUGE_WIDGET]['height'] < monitor['y']
