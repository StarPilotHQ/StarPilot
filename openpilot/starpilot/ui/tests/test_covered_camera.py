"""Opaque settings may omit pixels, but camera/model state must remain current."""
from collections import defaultdict
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.selfdrive.ui.mici.onroad import augmented_road_view as native
from openpilot.selfdrive.ui.mici.onroad import cameraview, model_renderer
from openpilot.starpilot.ui import runtime_app
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.tests import test_compact_settings_lifecycle
from openpilot.system.ui.widgets import Widget

lifecycle = test_compact_settings_lifecycle.lifecycle


@pytest.mark.parametrize('obstruction', ['none', 'touch', 'drag', 'dismiss', 'shown', 'bounce', 'velocity',
                                        'offset', 'width', 'hidden', 'disabled', 'other'])
def test_only_settled_opaque_settings_cover_the_camera(lifecycle, monkeypatch, obstruction):
  page, _, _, _ = lifecycle
  monkeypatch.setattr(runtime_app.gui_app, '_nav_stack', [page])
  monkeypatch.setattr(runtime_app.gui_app, '_width', 536)
  monkeypatch.setattr(runtime_app.gui_app, '_height', 240)
  if obstruction == 'touch':
    monkeypatch.setattr(runtime_app.gui_app, '_mouse_events', [object()])
  elif obstruction == 'drag':
    page._drag_start_pos = NS(x=20, y=20)
  elif obstruction == 'dismiss':
    page._playing_dismiss_animation = True
  elif obstruction == 'shown':
    page._shown_callback = Mock()
  elif obstruction == 'bounce':
    page._y_pos_filter.x = .01
  elif obstruction == 'velocity':
    page._y_pos_filter.velocity.x = .01
  elif obstruction == 'offset':
    page.set_position(0, .01)
  elif obstruction == 'width':
    page.set_rect(rl.Rectangle(0, 0, 535, 240))
  elif obstruction == 'hidden':
    page.set_visible(False)
  elif obstruction == 'disabled':
    page.set_enabled(False)
  elif obstruction == 'other':
    monkeypatch.setattr(runtime_app.gui_app, '_nav_stack', [page, object()])
  assert page.covers_camera(rl.Rectangle(0, 0, 536, 240)) is (obstruction == 'none')


@pytest.mark.parametrize('hardware', [False, True])
def test_camera_keeps_receive_disconnect_and_latest_frame_while_covered(monkeypatch, hardware):
  class Camera(cameraview.CameraView):
    def close(self):
      pass
  camera = Camera.__new__(Camera)
  camera._switching = True
  camera._handle_switch = Mock(side_effect=lambda: setattr(camera, '_switching', False))
  camera._ensure_connection = Mock(return_value=True)
  frames = [NS(width=100, height=80), None, NS(width=200, height=160)]
  camera.client = Mock()
  camera.client.recv.side_effect = frames
  camera.client.is_connected.return_value = False
  camera.frame = None
  camera._texture_needs_update = False
  camera._draw_placeholder = Mock()
  camera._calc_frame_matrix = Mock(return_value=np.eye(3))
  camera._stream_type = cameraview.VisionStreamType.VISION_STREAM_NARROW_ROAD
  camera._render_egl, camera._render_textures = Mock(), Mock()
  monkeypatch.setattr(cameraview, 'COMMA_HARDWARE', hardware)
  rect = rl.Rectangle(0, 0, 476, 240)
  camera._render(rect, paint=False)
  assert camera.frame is frames[0] and camera._texture_needs_update
  camera._handle_switch.assert_called_once()
  camera._render(rect, paint=False)
  assert camera.frame is None
  camera._draw_placeholder.assert_not_called()
  camera._calc_frame_matrix.assert_not_called()
  camera._render_egl.assert_not_called()
  camera._render_textures.assert_not_called()
  camera._render(rect)
  assert camera.frame is frames[2]
  painted = camera._render_egl if hardware else camera._render_textures
  assert painted.call_args.args[0].width == 200
  assert painted.call_args.args[0].height == 160
  assert camera.client.recv.call_count == 3


@pytest.mark.parametrize('mode', ['acceleration', 'rainbow', 'color'])
def test_covered_model_keeps_filters_geometry_leads_and_reveal_pixels(monkeypatch, mode):
  module = model_renderer
  monkeypatch.setattr(module, 'Params', lambda: NS(get=lambda _: None))
  visible, covered = module.ModelRenderer(), module.ModelRenderer()
  class Messages(dict):
    updated = defaultdict(lambda: True)
    valid = defaultdict(lambda: True)
    alive = defaultdict(lambda: True)
    recv_frame = defaultdict(lambda: 10)
  x = np.linspace(5., 100., 33).tolist()
  def line(y):
    return NS(x=x, y=[y] * 33, z=[0.] * 33)
  model = NS(position=line(0), laneLines=[line(y) for y in (-3.5, -1.8, 1.8, 3.5)],
             roadEdges=[line(-6), line(6)], laneLineProbs=[.6, .9, .8, .5], roadEdgeStds=[.2, .3],
             acceleration=NS(x=[.2] * 33))
  sm = Messages(modelV2=model, extrinsicsCalibration=NS(height=[1.2]), carParams=NS(openpilotLongitudinalControl=True),
                carOutput=NS(actuatorsOutput=NS(torque=.7)), carState=NS(vEgo=20., brakePressed=False),
                selfdriveState=NS(experimentalMode=False, enabled=True, engageable=True),
                longitudinalPlan=NS(allowThrottle=False, longitudinalPlanSource="lead0"),
                radarState=NS(leadOne=NS(present=True, dRel=25., yRel=0., vRel=-1., vLead=19.), leadTwo=NS(present=False)))
  clock = [1_000_000_000]
  ui = NS(sm=sm, started_frame=1, status=module.UIStatus.ENGAGED, params=Mock(), display_time_ns=lambda: clock[0])
  monkeypatch.setattr(module, 'ui_state', ui)
  monkeypatch.setattr(module, 'lane_centering_direction', lambda *_: -1)
  draw = Mock()
  monkeypatch.setattr(module, 'draw_polygon', draw)
  monkeypatch.setattr(module.rl, 'draw_triangle_fan', Mock())
  for renderer in (visible, covered):
    renderer._rect = rl.Rectangle(0, 0, 476, 240)
    renderer.road_style = {'pathMode': mode, 'path': '#456789AB'}
    renderer.set_transform(np.array([[238., 80., 0.], [240., 0., -100.], [1., 0., 0.]], dtype=np.float32))
    renderer._lead_indicator_enabled = True
    renderer._rainbow_path.refresh_enabled = Mock(return_value=mode == 'rainbow')
  for index in range(60):
    clock[0] += 16_000_000
    sm['longitudinalPlan'].allowThrottle = index >= 30
    sm['carOutput'].actuatorsOutput.torque = .7 if index < 40 else -.8
    sm.updated['modelV2'] = index % 3 == 0
    sm.updated['carState'] = index % 3 == 0
    sm.valid['carState'] = sm.alive['carState'] = not 20 <= index < 25
    sm['selfdriveState'].experimentalMode = index >= 45
    for renderer, paint in ((visible, True), (covered, False)):
      renderer._paint = paint
      draw.reset_mock()
      renderer._render(renderer._rect)
      if not paint:
        draw.assert_not_called()
    for field in ('_torque_filter', '_blend_filter', '_acceleration_x_filter', '_acceleration_x_filter2'):
      assert getattr(visible, field).x == getattr(covered, field).x
    assert visible._lane_centering_direction == covered._lane_centering_direction
    assert vars(visible._rainbow_path) == vars(covered._rainbow_path) | {'refresh_enabled': visible._rainbow_path.refresh_enabled}
    for a, b in zip(visible._lead_vehicles, covered._lead_vehicles, strict=True):
      assert a.info == b.info
      np.testing.assert_array_equal(a.bar, b.bar)
      for name in ("d_filter", "y_filter", "fade_filter"):
        assert vars(getattr(a, name)) == vars(getattr(b, name))
    for a, b in zip([visible._path, *visible._lane_lines, *visible._road_edges],
                    [covered._path, *covered._lane_lines, *covered._road_edges], strict=True):
      np.testing.assert_array_equal(a.projected_points, b.projected_points)
  covered._paint = True
  output = []
  def capture(rect, points, color=None, gradient=None):
    colors = [color] if color is not None else gradient.colors
    output.append((points.tolist(), [tuple(getattr(c, k) for k in 'rgba') for c in colors],
                   None if gradient is None else (gradient.start, gradient.end, gradient.stops)))
  monkeypatch.setattr(module, 'draw_polygon', capture)
  visible._render(visible._rect)
  expected = output.copy()
  output.clear()
  covered._render(covered._rect)
  assert output == expected and output


def test_model_paint_flag_cannot_leak_after_render_exception(monkeypatch):
  renderer = model_renderer.ModelRenderer.__new__(model_renderer.ModelRenderer)
  def fail(_):
    assert renderer._paint is False
    raise RuntimeError('render failed')
  monkeypatch.setattr(renderer, 'render', fail, raising=False)
  with pytest.raises(RuntimeError, match='render failed'):
    renderer.render_with_lead(rl.Rectangle(0, 0, 476, 240), True, paint=False)
  assert renderer._paint is True
  assert renderer._render_lateral_active is False


def make_session(monkeypatch, request, profile, camera):
  for name in ('BitmapFonts', 'PiPRenderer', 'PiPWarningSource', 'BluetoothStatusSource', 'RuntimeSnapshotAdapter',
               'GalaxyAccessFlow', 'FeatureSettingsOwner', 'SoundsOwner', 'AppearanceOwner', 'PiPOwner',
               'DisplayOwner', 'PowerOwner', 'ShellInput', 'FavoritesOwner', 'OnroadFavorites', 'DriveStateOwner', 'PhysicalSource'):
    monkeypatch.setattr(runtime_app, name, Mock())
  monkeypatch.setattr(runtime_app, '_galaxy_access_owner', Mock())
  monkeypatch.setattr(runtime_app, 'slc_action_transport_enabled', lambda *_: False)
  monkeypatch.setattr(runtime_app, 'ui_state', NS(params=object(), CP=None, sm={}))
  monkeypatch.delenv('SP_ONROAD_VISUAL_PREVIEW', raising=False)
  def view(*_, camera_layer, **__):
    return NS(camera_layer=camera_layer, onroad=NS(unified_speed=NS(source_bounds=Mock()), compact_sidebar=NS(), jetlink_status=Mock()))
  monkeypatch.setattr(runtime_app, 'ShellView', view)
  session = runtime_app.StarShellSession(profile, camera)
  for owner in (session._projection_bookmarks, session._projection_slc, session.onroad_map, session.onroad_jetlink):
    if owner is not None:
      request.addfinalizer(owner.close)
  return session


def test_compact_layout_propagates_paint_and_resets_after_first_touch_and_exception(lifecycle, monkeypatch, request):
  settings, _, _, _ = lifecycle
  monkeypatch.setattr(runtime_app.gui_app, '_nav_stack', [settings])
  monkeypatch.setattr(runtime_app.gui_app, '_width', 536)
  monkeypatch.setattr(runtime_app.gui_app, '_height', 240)
  class Camera(native.AugmentedRoadView):
    def close(self):
      pass
  camera = Camera.__new__(Camera)
  camera.available_streams = [native.NARROW_ROAD_CAM]
  camera._stream_type = native.NARROW_ROAD_CAM
  camera._target_stream_type = None
  camera._switching = False
  camera.frame = object()
  camera._switch_stream_if_needed = Mock(return_value=native.NARROW_ROAD_CAM)
  camera._update_calibration = Mock()
  camera._model_renderer = Mock()
  camera.reset_stock_confidence_layer = Mock()
  monkeypatch.setattr(native, 'ui_state', NS(started=True, sm={}))
  camera_paint = Mock()
  monkeypatch.setattr(native.CameraView, '_render', camera_paint)
  session = make_session(monkeypatch, request, runtime_app.Profile.COMPACT, camera)
  layout = runtime_app.StarMiciMainLayout.__new__(runtime_app.StarMiciMainLayout)
  Widget.__init__(layout)
  layout._rect = rl.Rectangle(0, 0, 536, 240)
  layout.star = session
  layout._settings_layout = settings
  monkeypatch.setattr(layout, '_native_onroad', NS(_bookmark_icon=Mock()), raising=False)
  monkeypatch.setattr(layout, '_car_onroad_layout', NS(rect=layout.rect, enabled=True, is_visible=True, _touch_valid=lambda: True), raising=False)
  monkeypatch.setattr(layout, '_scroller', NS(is_auto_scrolling=False), raising=False)
  state = OnroadState(True, True, 20, 80, SpeedLimitObservation(), lateral_active=True)
  should_raise = [False]
  def normal_render(_, rect):
    session.view.camera_layer(rect, state)
    if should_raise[0]:
      raise RuntimeError('frame failed')
  monkeypatch.setattr(runtime_app.MiciMainLayout, '_render', normal_render)
  for touched, failing in ((False, False), (True, False), (False, True)):
    monkeypatch.setattr(runtime_app.gui_app, '_mouse_events', [object()] if touched else [])
    should_raise[0] = failing
    if failing:
      with pytest.raises(RuntimeError, match='frame failed'):
        layout._render(layout.rect)
    else:
      layout._render(layout.rect)
    assert camera_paint.call_args.kwargs['paint'] is touched
    assert camera._model_renderer.render_with_lead.call_args.kwargs['paint'] is touched
    assert session.camera_paint is True
  assert camera._update_calibration.call_count == 3
  assert layout._native_onroad._bookmark_icon.process_gesture.call_count == 3


def test_large_camera_paint_is_unchanged(monkeypatch, request):
  camera = Mock()
  session = make_session(monkeypatch, request, runtime_app.Profile.LARGE, camera)
  session.camera_paint = False
  state = OnroadState(True, True, 20, 80, SpeedLimitObservation())
  session.view.camera_layer(rl.Rectangle(0, 0, 2160, 1080), state)
  kwargs = camera.render_camera_model_layer.call_args.kwargs
  assert set(kwargs) == {'road_style', 'camera_view', 'reverse_driver'}
  assert kwargs['camera_view'] == state.appearance.camera_view
  assert kwargs['reverse_driver'] == state.reverse_driver_camera
  assert 'paint' not in kwargs


@pytest.mark.parametrize('profile', [runtime_app.Profile.LARGE, runtime_app.Profile.COMPACT])
def test_shell_wires_native_gpu_status_layer(monkeypatch, request, profile):
  camera = Mock()
  session = make_session(monkeypatch, request, profile, camera)
  rect, state = rl.Rectangle(30, 30, 2100, 1020), Mock()
  session.view.onroad.extra_overlays(rect, state)
  kwargs = {'show_long_indicator': None} if profile == runtime_app.Profile.COMPACT else {}
  camera.render_model_source_layer.assert_called_once_with(rect, **kwargs)


@pytest.mark.parametrize('choice', list(runtime_app.CameraViewChoice))
@pytest.mark.parametrize('reverse', [False, True])
def test_large_shell_forwards_the_saved_camera_and_temporary_reverse(monkeypatch, request, choice, reverse):
  from dataclasses import replace
  camera = Mock()
  session = make_session(monkeypatch, request, runtime_app.Profile.LARGE, camera)
  state = OnroadState(True, True, 20, 80, SpeedLimitObservation(), reverse_driver_camera=reverse)
  state = replace(state, appearance=replace(state.appearance, camera_view=choice))
  session.view.camera_layer(rl.Rectangle(0, 0, 2160, 1080), state)
  kwargs = camera.render_camera_model_layer.call_args.kwargs
  assert kwargs['camera_view'] is choice and kwargs['reverse_driver'] is reverse
