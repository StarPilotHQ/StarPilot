import copy
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.selfdrive.ui.onroad import model_renderer as large
from openpilot.selfdrive.ui.mici.onroad import model_renderer as compact
from openpilot.selfdrive.ui.ui_state import UIStatus
from openpilot.starpilot.lateral.lane_feedback import BLUE
from openpilot.starpilot.ui.onroad_customization import default_document, validate_document
from openpilot.starpilot.ui.rainbow_path import RainbowPath
from openpilot.starpilot.ui.road_colors import edge_gradient, lane_color, path_mode, solid_gradient
from openpilot.starpilot.ui.layout_preview_renderer import render_sample_road, sample_state
from openpilot.starpilot.ui.presentation import Profile


def rgba(color):
  return color.r, color.g, color.b, color.a


def test_old_documents_preserve_widgets_and_have_no_new_road_override():
  old = default_document()
  old['version'] = 2
  del old['layouts']['large']['cem_gauge']  # Version 2 predates the CEM gauge.
  del old['roadColors']
  old['widgetColors']['compact']['conditional_mode'] = {'cardFill': '#12345680'}
  old['layouts']['large']['current_speed']['enabled'] = False
  expected_layouts = copy.deepcopy(old['layouts'])
  expected_layouts['large']['cem_gauge'] = default_document()['layouts']['large']['cem_gauge']
  for layout in old['layouts'].values():
    del layout['steering_wheel']['size']
  before = copy.deepcopy(old)
  new = validate_document(old)
  assert old == before
  assert new['layouts'] == expected_layouts
  assert new['widgetColors'] == old['widgetColors']
  assert new['roadColors'] == {'large': {}, 'compact': {}}


@pytest.mark.parametrize('value', [[], {'large': {}}, {'large': [], 'compact': {}},
                                 {'large': {'unknown': '#FFFFFFFF'}, 'compact': {}},
                                 {'large': {'pathMode': 'fancy'}, 'compact': {}},
                                 {'large': {'path': 1}, 'compact': {}},
                                 {'large': {'path': '#123456'}, 'compact': {}}])
def test_invalid_road_style_rejected(value):
  document = default_document()
  document['roadColors'] = value
  with pytest.raises(ValueError):
    validate_document(document)


def test_mode_inheritance_and_original_fixed_color_fades():
  assert path_mode({}) == 'acceleration'
  assert path_mode({'pathMode': 'default'}) == 'acceleration'
  assert path_mode({'pathMode': 'rainbow'}) == 'rainbow'
  assert path_mode({'pathMode': 'color'}) == 'color'
  assert [rgba(c) for c in solid_gradient('#12345680').colors] == [(18, 52, 86, 128), (18, 52, 86, 70), (18, 52, 86, 12)]
  assert [c.a for c in edge_gradient('#123456FF').colors] == [102, 89, 0]
  original = rl.Color(1, 2, 3, 4)
  assert lane_color({}, True, original, .6) is original


@pytest.mark.parametrize('module', [large, compact])
def test_custom_path_both_native_profiles_and_rainbow_takes_its_own_mode(module):
  renderer = module.ModelRenderer.__new__(module.ModelRenderer)
  renderer._rect = rl.Rectangle(0, 0, 476, 240)
  renderer._path = NS(projected_points=np.array([[0, 0], [10, 0], [0, 10]], dtype=np.float32))
  renderer._path_edges = []
  renderer._rainbow_path = RainbowPath()
  renderer._rainbow_path.refresh_enabled = Mock(return_value=True)
  renderer._longitudinal_control = True
  renderer._blend_filter = Mock()
  renderer.road_style = {'pathMode': 'color', 'path': '#12345680'}
  sm = {'longitudinalPlan': NS(allowThrottle=False)}
  ui = NS(params=Mock(), display_time_ns=lambda: 10_000_000_000)
  with patch.object(module, 'ui_state', ui), patch.object(module, 'draw_polygon') as draw:
    renderer._draw_path(sm)
    assert draw.call_args.kwargs['gradient'] is solid_gradient('#12345680')
  assert sm['longitudinalPlan'].allowThrottle is False


@pytest.mark.parametrize('module', [large, compact])
def test_custom_lane_colors_keep_blue_correction_and_compact_torque_warning(module):
  renderer = module.ModelRenderer.__new__(module.ModelRenderer)
  renderer._rect = rl.Rectangle(0, 0, 476, 240)
  renderer._lane_lines = [NS(projected_points=np.array([[0, 0], [5, 0], [0, 5]])) for _ in range(4)]
  renderer._road_edges = []
  renderer._lane_line_probs = [.3, .6, .7, .4]
  renderer.road_style = {'laneLines': '#AA22BB80', 'pathEdge': '#123456FF'}
  renderer._visual_status = lambda: UIStatus.ENGAGED
  renderer._torque_filter = NS(x=.9)
  with patch.object(module, 'lane_centering_direction', return_value=1), patch.object(module, 'draw_polygon') as draw:
    renderer._draw_lane_lines()
  colors = [rgba(call.args[2]) for call in draw.call_args_list]
  assert colors[2][:3] == BLUE
  assert colors[0] == (170, 34, 187, int(.3 * 128))
  if module is compact:
    assert colors[1][:3] == (255, 115, 0)
  else:
    assert colors[1][:3] == (170, 34, 187)


def test_large_path_edges_preserve_outer_width_and_default_projection(monkeypatch):
  renderer = large.ModelRenderer.__new__(large.ModelRenderer)
  renderer._clip_region = rl.Rectangle(-500, -500, 2160, 1800)
  points = np.array([[1, 0, 0], [10, 1, 0], [50, 2, 0]], dtype=np.float32)
  monkeypatch.setattr(renderer, '_path', NS(raw_points=points), raising=False)
  monkeypatch.setattr(renderer, '_lane_lines', [NS(raw_points=points.copy()) for _ in range(4)], raising=False)
  renderer._road_edges = []
  monkeypatch.setattr(renderer, '_lane_line_probs', [1] * 4, raising=False)
  renderer._path_offset_z = 1.2
  renderer._update_experimental_gradient = Mock()
  renderer._map_lines_to_polygons = Mock(side_effect=lambda lines, *a, **k: [line.copy() for line in lines])
  renderer._map_line_to_polygon = Mock(return_value=points)
  renderer.road_style = {}
  renderer._update_model(None, points[:, 0])
  assert renderer._map_lines_to_polygons.call_count == 1
  assert renderer._map_line_to_polygon.call_args.args[1] == .9
  assert renderer._path_edges == []
  renderer.road_style = {'pathEdge': '#AABBCCFF'}
  renderer._update_model(None, points[:, 0])
  lines, widths, *_ = renderer._map_lines_to_polygons.call_args.args
  assert widths == [.72, .09, .09]
  np.testing.assert_allclose(lines[1][:, 1], points[:, 1] - .81)
  np.testing.assert_allclose(lines[2][:, 1], points[:, 1] + .81)
  np.testing.assert_array_equal(renderer._path.raw_points, points)


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
def test_native_preview_uses_draft_only_and_shared_gradients(profile):
  document = default_document()
  document['roadColors'][str(profile)] = {'pathMode': 'color', 'path': '#12345680', 'pathEdge': '#AABBCCFF'}
  state = sample_state('engaged', document)
  rect = rl.Rectangle(30, 30, 1800, 1020) if profile == Profile.LARGE else rl.Rectangle(0, 0, 476, 240)
  with patch('openpilot.starpilot.ui.layout_preview_renderer.draw_polygon') as draw:
    render_sample_road(rect, state, profile)
  for call in draw.call_args_list:
    points = call.args[1]
    assert points[:, 0].min() >= rect.x and points[:, 0].max() <= rect.x + rect.width
    assert points[:, 1].min() >= rect.y and points[:, 1].max() <= rect.y + rect.height
  gradients = [call.kwargs.get('gradient') for call in draw.call_args_list]
  assert solid_gradient('#12345680') in gradients
  assert (edge_gradient('#AABBCCFF') in gradients) == (profile == Profile.LARGE)
  assert not state.camera_available


@pytest.mark.parametrize("module", [large, compact])
def test_default_gradient_reuses_quantized_colors_and_keeps_filter_cadence(monkeypatch, module):
  from types import SimpleNamespace

  renderer = module.ModelRenderer.__new__(module.ModelRenderer)
  renderer._path = SimpleNamespace(projected_points=np.array([[1, 2], [3, 4]], dtype=np.float32))
  renderer._rect = SimpleNamespace(x=10, y=20, width=476, height=240)
  renderer._longitudinal_control = True
  renderer._blend_filter = Mock(x=.731)
  renderer._experimental_mode = False
  renderer._rainbow_path = SimpleNamespace(refresh_enabled=Mock(return_value=False))
  renderer._visual_status = Mock(return_value=UIStatus.ENGAGED)
  renderer.road_style = {"pathMode": "acceleration"}
  monkeypatch.setattr(module, "ui_state", SimpleNamespace(params=object(), display_time_ns=lambda: 10_000_000_000))
  draw = Mock()
  monkeypatch.setattr(module, "draw_polygon", draw)
  blend = Mock(wraps=module.ModelRenderer._blend_colors)
  renderer._blend_colors = blend
  sm = {"longitudinalPlan": SimpleNamespace(allowThrottle=True)}
  previous = None
  for value in (.731, .734, .735):
    renderer._blend_filter.x = value
    renderer._draw_path(sm)
    gradient = draw.call_args.kwargs['gradient']
    expected = module.ModelRenderer._blend_colors(module.NO_THROTTLE_COLORS, module.THROTTLE_COLORS, round(value * 100) / 100)
    assert [rgba(color) for color in gradient.colors] == [rgba(color) for color in expected]
    assert (gradient.start, gradient.end, gradient.stops) == ((0., 1.), (0., 0.), [0., .5, 1.])
    expected_points = [[11, 22], [13, 24]] if module is compact else [[1, 2], [3, 4]]
    np.testing.assert_array_equal(draw.call_args.args[1], expected_points)
    if value == .734:
      assert gradient is previous
    previous = gradient
  assert blend.call_count == 2
  assert renderer._blend_filter.update.call_count == draw.call_count == 3


@pytest.mark.parametrize('module', [large, compact])
@pytest.mark.parametrize('missing', ['modelV2', 'extrinsicsCalibration'])
def test_replay_missing_model_or_calibration_never_paints_cached_geometry(monkeypatch, module, missing):
  renderer = module.ModelRenderer.__new__(module.ModelRenderer)
  renderer._torque_filter = Mock()
  renderer._draw_path = Mock()
  renderer._draw_lane_lines = Mock()
  services = ('modelV2', 'extrinsicsCalibration')
  sm = NS(seen=dict.fromkeys(services, True), valid=dict.fromkeys(services, True), alive=dict.fromkeys(services, True))
  sm.seen[missing] = False
  monkeypatch.setattr(module, 'ui_state', NS(sm=sm, replay_clock=object()))
  renderer._render(rl.Rectangle(0, 0, 476, 240))
  renderer._draw_path.assert_not_called()
  renderer._draw_lane_lines.assert_not_called()
  renderer._torque_filter.update.assert_not_called()


@pytest.mark.parametrize('module', [large, compact])
def test_replay_missing_longitudinal_plan_uses_neutral_path_feedback(monkeypatch, module):
  class Messages(dict):
    seen = {'longitudinalPlan': False}
    valid = {'longitudinalPlan': False}
    alive = {'longitudinalPlan': False}
  sm = Messages(longitudinalPlan=NS(allowThrottle=False))
  ui = NS(sm=sm, replay_clock=object(), display_time_ns=lambda: 10_000_000_000)
  renderer = module.ModelRenderer.__new__(module.ModelRenderer)
  renderer._rect = rl.Rectangle(0, 0, 476, 240)
  renderer._path = NS(projected_points=np.array([[0, 0], [10, 0], [0, 10]], dtype=np.float32))
  renderer._path_edges = []
  renderer._longitudinal_control = True
  renderer._experimental_mode = False
  renderer._blend_filter = Mock()
  renderer.road_style = {'pathMode': 'acceleration'}
  monkeypatch.setattr(module, 'ui_state', ui)
  draw = Mock()
  monkeypatch.setattr(module, 'draw_polygon', draw)
  renderer._draw_path(sm)
  renderer._blend_filter.update.assert_not_called()
  assert rgba(draw.call_args.args[2]) == (255, 255, 255, 30)
