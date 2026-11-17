"""Source-backed compact road cues without a camera or device."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.mici.onroad import model_renderer as native_model
from openpilot.selfdrive.ui.ui_state import UIStatus
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer, MiciSidebarWidgets
from openpilot.starpilot.ui.onroad_border import render_compact_turn_signals
from openpilot.starpilot.ui.onroad import OnroadView
from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.onroad_state import AlertSize, BorderSignals, ObservationKind, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.speed_limit_shapes import SpeedLimitShapes
from openpilot.starpilot.ui.tests.test_unified_speed_native import foreground_calls
from openpilot.starpilot.ui.runtime_snapshot import _model_confidence


def road(**changes):
  state = OnroadState(True, True, 15.0, 80.0, SpeedLimitObservation(), lateral_active=True)
  return replace(state, **changes)


def test_confidence_uses_model_probabilities_and_marks_unknown_as_unknown():
  model = NS(meta=NS(disengagePredictions=NS(brakeDisengageProbs=[0.1, 0.2], steerOverrideProbs=[0.1])))
  confidence = _model_confidence(model)
  assert confidence is not None
  assert abs(confidence - 0.72) < 1e-9
  assert _model_confidence(NS()) is None
  assert _model_confidence(NS(meta=NS(disengagePredictions=NS(brakeDisengageProbs=[], steerOverrideProbs=[])))) is None
  assert _model_confidence(NS(meta=NS(disengagePredictions=NS(brakeDisengageProbs=[float('nan')],
                                                       steerOverrideProbs=[0.1])))) is None

  widget = MiciSidebarWidgets()
  rect = rl.Rectangle(476, 0, 60, 80)
  with patch.object(rl, 'draw_rectangle_gradient_v') as gradient, patch.object(rl, 'draw_ring'):
    widget._confidence_ball(rect, road(model_confidence=None, stock_confidence_source_fresh=False))
    assert gradient.call_args.args[4].r == 50
    for index in range(80):
      widget._confidence_ball(rect, road(model_confidence=0.9, stock_confidence_source_fresh=True,
                                        stock_confidence_source_stamp_ns=1_000_000_000 + index * 16_000_000,
                                        stock_confidence_drive_frame=1))
    assert gradient.call_args.args[4].g == 255
    widget._confidence_ball(rect, road(model_confidence=None, stock_confidence_source_fresh=False))
    assert gradient.call_args.args[4].r == 50


def test_aol_model_geometry_uses_fresh_lateral_display_context(monkeypatch):
  renderer = native_model.ModelRenderer.__new__(native_model.ModelRenderer)
  renderer._render_lateral_active = False
  renderer._torque_filter = Mock()
  renderer._transform_dirty = False
  renderer._lead_indicator_enabled = False
  monkeypatch.setattr(renderer, '_path', NS(raw_points=np.array([[1.0, 0.0, 0.0]], dtype=np.float32)), raising=False)
  renderer._draw_lane_lines = Mock()
  renderer._draw_path = Mock()
  renderer._rect = rl.Rectangle(0, 0, 476, 240)
  sm = NS(recv_frame={'extrinsicsCalibration': 2, 'modelV2': 2},
          updated={'carParams': False, 'modelV2': False, 'radarState': False},
          valid={'radarState': False})
  values = {'carOutput': NS(actuatorsOutput=NS(torque=0.0)),
            'selfdriveState': NS(experimentalMode=False),
            'extrinsicsCalibration': NS(height=[]), 'modelV2': NS()}
  class SubMaster:
    def __getitem__(self, key):
      return values[key]
  source = SubMaster()
  source.recv_frame, source.updated, source.valid = sm.recv_frame, sm.updated, sm.valid
  ui = NS(sm=source, started_frame=1, status=UIStatus.DISENGAGED, display_time_ns=lambda: 1_000_000_000)
  with patch.object(native_model, 'ui_state', ui):
    renderer._render(rl.Rectangle(0, 0, 476, 240))
    renderer._draw_lane_lines.assert_not_called()
    renderer._render_lateral_active = True
    renderer._render(rl.Rectangle(0, 0, 476, 240))
    renderer._draw_lane_lines.assert_called_once()
    renderer._draw_path.assert_called_once_with(source)
    assert renderer._visual_status() == UIStatus.ENGAGED
    renderer._render_lateral_active = False
    assert renderer._visual_status() == UIStatus.DISENGAGED


def test_aol_lane_and_path_colors_do_not_use_disengaged_black(monkeypatch):
  renderer = native_model.ModelRenderer.__new__(native_model.ModelRenderer)
  renderer._render_lateral_active = False
  monkeypatch.setattr(renderer, '_torque_filter', NS(x=0.0), raising=False)
  renderer._rect = rl.Rectangle(0, 0, 476, 240)
  monkeypatch.setattr(renderer, '_path', NS(projected_points=np.array([[10.0, 10.0], [20.0, 20.0]], dtype=np.float32)), raising=False)
  renderer._longitudinal_control = False
  renderer._blend_filter = Mock(x=1.0)
  renderer._rainbow_path = Mock()
  renderer._rainbow_path.refresh_enabled.return_value = False
  renderer._experimental_mode = True
  monkeypatch.setattr(renderer, '_exp_gradient', NS(colors=[]), raising=False)
  ui = NS(status=UIStatus.DISENGAGED, params=Mock(), display_time_ns=lambda: 1_000_000_000)
  with patch.object(native_model, 'ui_state', ui), patch.object(native_model, 'draw_polygon') as polygon:
    assert renderer._get_ll_color(0.7, True, True).r == 0
    renderer._draw_path({'longitudinalPlan': NS(allowThrottle=True)})
    assert polygon.call_args.args[2].a == 90
    renderer._render_lateral_active = True
    assert renderer._get_ll_color(0.7, True, True).g == 255
    renderer._draw_path({'longitudinalPlan': NS(allowThrottle=True)})
    assert polygon.call_args.args[2].r == 255
    assert polygon.call_args.args[2].a == 30


def test_accepted_reasons_dispatch_original_compact_icons():
  fonts = Mock()
  fonts.measure.return_value = NS(width=20, height=20)
  widget = MiciSidebarWidgets(fonts)
  methods = ('_lead_icon', '_stop_icon', '_curve_icon', '_turn_icon', '_speed_icon', '_chill_icon')
  with patch.object(rl, 'draw_rectangle'), patch.object(widget, '_confidence_ball'), patch.object(widget, '_personality'):
    for reason, expected in (('cem_lead', '_lead_icon'), ('cem_stop', '_stop_icon'),
                             ('cem_curve', '_curve_icon'), ('cem_signal', '_turn_icon'),
                             ('cem_speed_limit', '_speed_icon')):
      patches = [patch.object(widget, method) for method in methods]
      mocks = [item.__enter__() for item in patches]
      try:
        widget.render(rl.Rectangle(0, 0, 536, 240), road(longitudinal_active=True,
                      conditional_effective=NS(choice=ModeChoice.CEM, effective_experimental=True, reason=reason)))
        mocks[methods.index(expected)].assert_called_once()
        assert sum(mock.call_count for mock in mocks) == 1
      finally:
        for item in reversed(patches):
          item.__exit__(None, None, None)


def test_hold_reason_survives_renderer_recreation_without_local_icon_history():
  fonts = Mock()
  fonts.measure.return_value = NS(width=20, height=20)
  for code, expected in ((3, '_curve_icon'), (4, '_lead_icon'), (5, '_turn_icon'), (6, '_speed_icon'),
                         (7, '_speed_icon'), (8, '_stop_icon')):
    widget = MiciSidebarWidgets(fonts)
    held = NS(choice=ModeChoice.CEM, effective_experimental=True, reason='cem_hold', status_code=code)
    with patch.object(widget, expected) as icon:
      widget._conditional(rl.Rectangle(476, 80, 60, 80), road(longitudinal_active=True, conditional_effective=held))
      icon.assert_called_once()


def test_configured_cem_idle_and_unknown_reason_use_original_couch():
  widget = MiciSidebarWidgets(Mock())
  unknown = NS(choice=ModeChoice.CEM, effective_experimental=True, reason='cem_hold', status_code=0)
  inactive = NS(choice=ModeChoice.CEM, effective_experimental=False, reason='no_trigger', status_code=0)
  with patch.object(widget, '_chill_icon') as couch, patch.object(widget, '_active_icon') as manual_e:
    base = road(longitudinal_active=True, conditional_configured=ModeChoice.CEM, experimental_enabled=True)
    for value in (None, unknown, inactive):
      widget._conditional(rl.Rectangle(476, 80, 60, 80), replace(base, conditional_effective=value))
    widget._conditional(rl.Rectangle(476, 80, 60, 80), replace(base, conditional_effective=inactive, conditional_configured=None))
    assert couch.call_count == 4
    manual_e.assert_not_called()


def test_stop_reason_precedes_curve_and_clears_without_a_second_visual_latch():
  from openpilot.starpilot.ui.onroad_conditional import stop_active
  widget = MiciSidebarWidgets(Mock())
  stopped = NS(choice=ModeChoice.CEM, effective_experimental=True, reason='cem_stop', status_code=8)
  held = NS(choice=ModeChoice.CEM, effective_experimental=True, reason='cem_hold', status_code=8)
  inactive = NS(choice=ModeChoice.CEM, effective_experimental=False, reason='no_trigger', status_code=0)
  base = road(longitudinal_active=True, conditional_configured=ModeChoice.CEM, experimental_enabled=True)
  with patch.object(widget, '_stop_icon') as light, patch.object(widget, '_curve_icon') as curve, \
       patch('openpilot.starpilot.ui.onroad_compact_widgets.curve_controlling', return_value=True):
    for value in (stopped, held):
      current = replace(base, conditional_effective=value)
      assert stop_active(current)
      widget._conditional(rl.Rectangle(476, 80, 60, 80), current)
    assert light.call_count == 2
    curve.assert_not_called()
    assert not stop_active(replace(base, conditional_effective=inactive))
    assert not stop_active(replace(base, conditional_effective=None))
    overridden = replace(base, conditional_effective=stopped, longitudinal_overridden=True)
    assert not stop_active(overridden)
    widget._conditional(rl.Rectangle(476, 80, 60, 80), overridden)
    assert light.call_count == 2


def test_valid_vision_limit_has_persistent_sign_without_action_receipt():
  fonts = Mock(profile=Profile.COMPACT)
  fonts.measure.return_value = NS(width=30, height=20)
  fonts.vertical_ink.return_value = (0, 20)
  fonts.horizontal_ink.return_value = (0, 30)
  renderer = CompactHudRenderer(fonts, Mock())
  valid = SpeedLimitObservation(kind=ObservationKind.VALID, source='vision', speed_limit_mps=24.6)
  shown = replace(road(), appearance=replace(road().appearance, show_speed_limit_sign=True), speed_limit=valid)
  with patch.object(rl, 'draw_rectangle_rounded') as fill, patch.object(SpeedLimitShapes, 'outline') as outline:
    renderer._speed_limit_sign(shown)
    fill.assert_not_called()
    outline.assert_called_once()
    assert outline.call_args.args[:2] == (384, 90)
    assert [call.args[0] for call in foreground_calls(fonts.draw)] == ['VISION', '55']
    outline.reset_mock()
    renderer._speed_limit_sign(replace(shown, speed_limit=SpeedLimitObservation(kind=ObservationKind.STALE)))
    outline.assert_not_called()
    renderer._speed_limit_sign(replace(road(speed_limit=valid), appearance=replace(shown.appearance, show_speed_limit_sign=False)))
    outline.assert_not_called()
  fonts.draw.reset_mock()
  with patch.object(renderer._speed_limit_widget.shapes, 'circle') as circle:
    renderer._speed_limit_sign(replace(shown, appearance=replace(shown.appearance, use_vienna_sign=True)))
    circle.assert_called_once()
    assert [call.args[0] for call in foreground_calls(fonts.draw)] == ['VISION', '55']


def test_pending_confirmation_omits_redundant_speed_heading():
  observation = SpeedLimitObservation(kind=ObservationKind.VALID, source='vision', speed_limit_mps=20.117,
                                      pending_speed_limit_mps=15.6464, session_id='drive', decision_id=4,
                                      presentation_id=5, action_enabled=True)
  state = road(longitudinal_active=True, slc_system_long_available=True, speed_limit=observation, slc_prompt_open=True)
  view = OnroadView.__new__(OnroadView)
  view.personality = Mock()
  view.fonts = Mock(profile=Profile.COMPACT)
  view.fonts.measure.return_value = NS(width=50, height=20)
  view.fonts.vertical_ink.return_value = (0, 20)
  view.fonts.horizontal_ink.return_value = (0, 50)
  from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
  view.compact_hud = CompactHudRenderer(view.fonts, Mock())
  with patch.object(rl, 'draw_line_ex'), patch.object(rl, 'draw_rectangle_rounded'), \
       patch.object(SpeedLimitShapes, 'button'):
    view._slc_actions(state)
  assert [call.args[0] for call in foreground_calls(view.fonts.draw)] == ['NEW LIMIT', 'ACCEPT', '35', 'REJECT', 'NEW LIMIT']


def test_compact_pending_label_and_button_follow_saved_action_placement():
  from openpilot.starpilot.ui.onroad_state import slc_controls
  document = default_document()
  document['layouts']['compact']['speed_limit_actions']['x'] = 120
  observation = SpeedLimitObservation(kind=ObservationKind.VALID, speed_limit_mps=20,
                                      pending_speed_limit_mps=15, session_id='drive', decision_id=4,
                                      presentation_id=5, action_enabled=True)
  state = road(customization=document, longitudinal_active=True, slc_system_long_available=True,
               speed_limit=observation, slc_prompt_open=True)
  view = OnroadView.__new__(OnroadView)
  view.personality = Mock()
  view.fonts = Mock(profile=Profile.COMPACT)
  view.fonts.measure.return_value = NS(width=50, height=20)
  view.fonts.vertical_ink.return_value = (0, 20)
  view.fonts.horizontal_ink.return_value = (0, 50)
  from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
  view.compact_hud = CompactHudRenderer(view.fonts, Mock())
  with patch.object(rl, 'draw_rectangle_rounded') as button, patch.object(rl, 'draw_line_ex'), \
       patch.object(SpeedLimitShapes, 'button'):
    view._slc_actions(state)
  assert [call.args[0] for call in foreground_calls(view.fonts.draw) if call.args[0] in ('ACCEPT', 'REJECT')] == ['ACCEPT', 'REJECT']
  button.assert_not_called()
  assert slc_controls(Profile.COMPACT, state)[0].bounds == (156., 48., 306., 160.)


def test_fresh_physical_blinker_uses_upstream_art_without_optional_border():
  current = road(border_signals=BorderSignals(True, True, False, False))
  with patch('openpilot.starpilot.ui.onroad_border.gui_app.texture', return_value=object()) as texture, \
       patch.object(rl, 'draw_texture_ex') as draw:
    render_compact_turn_signals(current, 0.1)
    assert draw.call_count == 2
    assert [call.kwargs['flip_x'] for call in texture.call_args_list] == [False, True]
    assert all(call.args == ('icons_mici/onroad/turn_signal_left.png', 104, 96) for call in texture.call_args_list)
    render_compact_turn_signals(current, 0.5)
    render_compact_turn_signals(replace(current, border_signals=None), 0.1)
    render_compact_turn_signals(replace(current, alert=OnroadAlert(AlertSize.SMALL, 'Lane change')), 0.1)
    assert draw.call_count == 2
