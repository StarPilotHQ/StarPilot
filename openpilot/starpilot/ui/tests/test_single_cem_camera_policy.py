"""Actual shared Big/AA composition and camera dispatch; no native resources."""
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.selfdrive.ui.onroad import augmented_road_view as large
from openpilot.selfdrive.ui.mici.onroad import augmented_road_view as compact
from openpilot.starpilot.system.starport.projection_layout import projection_appearance
from openpilot.starpilot.system.starport.projection_onroad import ProjectionOnroad
from openpilot.starpilot.ui import onroad
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice, OnroadAppearance
from openpilot.starpilot.ui.developer_preview import OnroadVisualPreview
from openpilot.starpilot.ui.onroad_customization import DEFAULT_WIDGET_ORDER, default_document
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import Profile


@pytest.mark.parametrize('viewport', [None, (2880, 1080)])
@pytest.mark.parametrize('ordered', [False, True])
@pytest.mark.parametrize('editor', [False, True])
def test_big_and_projected_editor_share_one_graphic_cem_without_legacy_caption(viewport, ordered, editor):
  document=default_document()
  if ordered:
    document['widgetOrder']={'large':list(DEFAULT_WIDGET_ORDER['large'])}
  else:
    document.pop('widgetOrder',None)
  state=OnroadState(True, True, 20., 80., SpeedLimitObservation(), customization=document,
    longitudinal_active=True, visual_preview=OnroadVisualPreview(cem_reason='STOP LIGHT',layout_sample=editor))
  view=onroad.OnroadView.__new__(onroad.OnroadView)
  view.personality = Mock()
  view.fonts=Mock(profile=Profile.LARGE)
  view.projection_viewport=viewport
  view.camera_layer=view.background_layer=view.extra_overlays=None
  view.pip_layer=view.map_layer=view.driver_monitor_layer=None
  view.unified_speed=view.current_speed=view.steering_wheel=view.torque_bar=Mock()
  view.cem_gauge=Mock()
  view.alert=Mock()
  view.navigation=view.navigation_favorites=Mock()
  for name in ('_slc_actions','_clock','_driving_mode','_stopped_timer','_traffic_badge','_preview_badge'):
    setattr(view,name,Mock())
  view._corner_cache=Mock()
  with ExitStack() as stack:
    ordered_draw = stack.enter_context(patch.object(view,'_ordered_widgets',wraps=view._ordered_widgets))
    for name in ('draw_rectangle_rec','draw_rectangle_gradient_v','draw_rectangle_lines_ex','draw_rectangle_rounded_lines_ex',
                 'rl_push_matrix','rl_translatef','rl_pop_matrix'):
      stack.enter_context(patch.object(rl,name))
    stack.enter_context(patch.object(onroad.clip,'begin_scissor_mode'))
    stack.enter_context(patch.object(onroad.clip,'end_scissor_mode'))
    for name in ('render_glow','render_corner_hint'):
      stack.enter_context(patch.object(onroad,name))
    border = stack.enter_context(patch.object(onroad,'render_large_half_borders'))
    view._large(state)
  assert ordered_draw.call_count == int(ordered)
  border.assert_called_once()
  view.cem_gauge.render.assert_called_once_with(state)
  assert not any(str(call.args[0]).startswith(('CEM ','CCM ')) for call in view.fonts.draw.call_args_list)


def camera(module, current=None):
  view=module.AugmentedRoadView.__new__(module.AugmentedRoadView)
  view.close=Mock()
  view._stream_type=module.NARROW_ROAD_CAM if current is None else current
  view.available_streams=[module.NARROW_ROAD_CAM,module.WIDE_CAM,module.DRIVER_CAM]
  view._switching=False
  view._target_client=view._target_stream_type=None
  view.switch_stream=Mock(side_effect=lambda target:setattr(view,'_stream_type',target))
  return view


@pytest.mark.parametrize('module',[large,compact])
@pytest.mark.parametrize('speed',[0.,5.,7.,10.,30.])
def test_saved_standard_beats_auto_and_revokes_pending_wide(module,speed):
  view=camera(module)
  pending=view._target_client=Mock()
  view._target_stream_type=module.WIDE_CAM
  view._switching=True
  sm={'selfdriveState':NS(experimentalMode=True),'carState':NS(vEgo=speed)}
  assert view._switch_stream_if_needed(sm,CameraViewChoice.STANDARD)==module.NARROW_ROAD_CAM
  assert not view._switching and view._target_client is None and view._target_stream_type is None
  pending.recv.assert_not_called()
  view.switch_stream.assert_not_called()


@pytest.mark.parametrize('module',[large,compact])
def test_reverse_is_temporary_and_none_cancels_pending(module):
  view=camera(module)
  sm={'selfdriveState':NS(experimentalMode=True),'carState':NS(vEgo=1.)}
  choice=CameraViewChoice.STANDARD
  assert view._switch_stream_if_needed(sm,choice,True)==module.DRIVER_CAM
  assert view._switch_stream_if_needed(sm,choice,False)==module.NARROW_ROAD_CAM
  assert choice is CameraViewChoice.STANDARD
  view._switching=True
  view._target_client=Mock()
  view._target_stream_type=module.WIDE_CAM
  assert view._switch_stream_if_needed(sm,CameraViewChoice.NONE,True) is None
  assert not view._switching and view._target_client is None


@pytest.mark.parametrize('choice',list(CameraViewChoice))
def test_projection_keeps_shared_camera_choice_and_passes_it_to_actual_camera(choice):
  appearance=OnroadAppearance(camera_view=choice,driver_camera_on_reverse=True)
  projected=projection_appearance(appearance,{})
  assert projected.camera_view is choice and projected.driver_camera_on_reverse
  state=OnroadState(True,True,20.,80.,SpeedLimitObservation(),appearance=projected,reverse_driver_camera=True)
  view=ProjectionOnroad.__new__(ProjectionOnroad)
  view.camera=Mock(stream_type=large.NARROW_ROAD_CAM)
  view._camera_layer(rl.Rectangle(0,0,2880,1080),state)
  kwargs=view.camera.render_camera_model_layer.call_args.kwargs
  assert kwargs['camera_view'] is choice and kwargs['reverse_driver'] is True
  assert appearance.camera_view is choice


def test_large_driver_presentation_uses_plain_camera_transform_without_road_geometry():
  view=camera(large,large.DRIVER_CAM)
  with patch.object(large.CameraView,'_calc_frame_matrix',return_value='driver-matrix') as plain:
    assert view._calc_frame_matrix(rl.Rectangle(0,0,100,80))=='driver-matrix'
    plain.assert_called_once()


@pytest.mark.parametrize('choice,available', [(CameraViewChoice.DRIVER, True), (CameraViewChoice.DRIVER, False),
                                              (CameraViewChoice.NONE, True)])
def test_large_camera_never_draws_road_geometry_over_driver_or_hidden_view(choice, available):
  view=camera(large,large.DRIVER_CAM if available else large.NARROW_ROAD_CAM)
  if not available:
    view.available_streams.remove(large.DRIVER_CAM)
  view._ensure_connection=Mock(return_value=True)
  view._update_calibration=Mock()
  view.model_renderer=Mock(road_style={})
  rect=rl.Rectangle(0,0,100,80)
  sm={'selfdriveState':NS(experimentalMode=True),'carState':NS(vEgo=1.)}
  with patch.object(large,'ui_state',NS(started=True,sm=sm)), \
       patch.object(large.gui_app,'measure_frame_phase',side_effect=lambda name,func,*args,**kwargs:func(*args,**kwargs)), \
       patch.object(large.CameraView,'_render') as render_camera, patch.object(large.rl,'draw_rectangle_rec') as neutral:
    view.render_camera_model_layer(rect,camera_view=choice)
    view.model_renderer.render.assert_not_called()
    if choice is CameraViewChoice.NONE or not available:
      render_camera.assert_not_called()
      neutral.assert_called_once_with(rect,large.rl.BLACK)
    else:
      render_camera.assert_called_once_with(view,rect,poll_camera=True)
      # Ending the temporary driver presentation returns to the saved Standard
      # and resumes calibrated road geometry on the actual native method.
      view.render_camera_model_layer(rect,camera_view=CameraViewChoice.STANDARD)
      assert view.stream_type==large.NARROW_ROAD_CAM
      view.model_renderer.render.assert_called_once_with(rect)
