"""C4/Mici lane batching preserves the existing per-line projection."""

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from openpilot.selfdrive.ui.mici.onroad.model_renderer import ModelRenderer
from openpilot.selfdrive.ui.onroad.model_renderer import ModelRenderer as LargeModelRenderer
from openpilot.selfdrive.ui.onroad.model_renderer import ModelPoints


@pytest.mark.parametrize("renderer_class", [ModelRenderer, LargeModelRenderer])
@pytest.mark.parametrize("lane_count", [4, 6])
def test_legacy_adjacent_centers_preserve_the_four_lane_boundaries(renderer_class, lane_count):
  view = object.__new__(renderer_class)
  view._path = ModelPoints()
  view._lane_lines = [ModelPoints() for _ in range(4)]
  view._road_edges = [ModelPoints() for _ in range(2)]
  def line(y):
    return SimpleNamespace(x=[10.0, 20.0], y=[y, y], z=[1.0, 1.0])
  model = SimpleNamespace(position=line(0), laneLines=[line(i) for i in range(lane_count)],
                          roadEdges=[line(-i) for i in range(2)],
                          laneLineProbs=[0.1, 0.2, 0.3, 0.4], roadEdgeStds=[0.5, 0.6],
                          acceleration=SimpleNamespace(x=[0.0, 1.0]))

  view._update_raw_points(model)

  for i, points in enumerate(view._lane_lines):
    np.testing.assert_array_equal(points.raw_points, [[10, i, 1], [20, i, 1]])
  for i, points in enumerate(view._road_edges):
    np.testing.assert_array_equal(points.raw_points, [[10, -i, 1], [20, -i, 1]])
  np.testing.assert_allclose(view._lane_line_probs, model.laneLineProbs)
  np.testing.assert_allclose(view._road_edge_stds, model.roadEdgeStds)


@pytest.fixture
def renderer():
  view = object.__new__(ModelRenderer)
  view._car_space_transform = np.array([[580, -480, 0], [400, 0, -480], [1, 0, 0]], dtype=np.float32)
  view._clip_region = SimpleNamespace(x=-500, y=-500, width=2160, height=1800)
  return view


@pytest.mark.parametrize('lengths', [(33,) * 6, (100,) * 6, (0, 1, 7, 33, 15, 100), (0,) * 6])
@pytest.mark.parametrize('max_idx', [-1, 0, 15, 32, 100])
def test_batch_matches_individual_lane_and_edge_geometry(renderer, lengths, max_idx):
  rng = np.random.default_rng(20260924)
  lines = [np.column_stack((np.linspace(-5, 110, length), rng.normal(0, 6, length),
                            rng.normal(0, 1, length))).astype(np.float32) for length in lengths]
  widths = [0.12, 0.16, 0.16, 0.16, 0.16, 0.16]
  actual = renderer._map_lines_to_polygons(lines, widths, max_idx)
  expected = [renderer._map_line_to_polygon(line, width, 0.0, max_idx)
              for line, width in zip(lines, widths, strict=True)]
  assert len(actual) == len(expected)
  for polygon, reference in zip(actual, expected, strict=True):
    assert polygon.shape == reference.shape
    assert polygon.dtype == np.float32
    np.testing.assert_allclose(polygon, reference, rtol=0, atol=1e-4)


def test_batch_clips_each_line_independently_and_path_hill_filter_is_unchanged(renderer):
  renderer._car_space_transform = np.eye(3, dtype=np.float32)
  renderer._clip_region = SimpleNamespace(x=0, y=0, width=100, height=100)
  lines = [np.array([[0, 0, 0], [20, 50, 1], [30, 99, 1]], dtype=np.float32),
           np.empty((0, 3), dtype=np.float32),
           np.array([[-1, 10, 1], [40, 80, 1], [50, 10, 1]], dtype=np.float32)]
  with np.errstate(divide='raise', invalid='raise'):
    polygons = renderer._map_lines_to_polygons(lines, [2, 1, 15], 100)
  np.testing.assert_array_equal(polygons[0], [[20, 48], [20, 52]])
  assert polygons[1].shape == (0, 2)
  np.testing.assert_array_equal(polygons[2], [[40, 65], [40, 95]])

  hill = np.array([[10, 50, 1], [20, 40, 1], [30, 45, 1], [40, 30, 1]], dtype=np.float32)
  path = renderer._map_line_to_polygon(hill, 2, 0, len(hill), allow_invert=False)
  np.testing.assert_array_equal(path[:len(path) // 2], [[10, 48], [20, 38], [40, 28]])


@pytest.mark.parametrize("dirty", [False, True])
@pytest.mark.parametrize("renderer_class", [ModelRenderer, LargeModelRenderer])
def test_equal_float32_transform_preserves_pending_projection(renderer, dirty, renderer_class):
  renderer.set_transform = renderer_class.set_transform.__get__(renderer)
  renderer._transform_dirty = dirty
  original = renderer._car_space_transform
  incoming = original.astype(np.float64)
  incoming[0, 0] += 1e-7  # Distinct input, identical matrix at projection precision.
  renderer.set_transform(incoming)
  assert renderer._car_space_transform is original
  assert renderer._transform_dirty is dirty


@pytest.mark.parametrize("value", [581, np.nan])
@pytest.mark.parametrize("renderer_class", [ModelRenderer, LargeModelRenderer])
def test_changed_or_nan_transform_remains_dirty_and_owned(renderer, value, renderer_class):
  renderer.set_transform = renderer_class.set_transform.__get__(renderer)
  renderer._transform_dirty = False
  incoming = renderer._car_space_transform.astype(np.float64)
  incoming[0, 0] = value
  renderer.set_transform(incoming)
  assert renderer._transform_dirty
  assert renderer._car_space_transform.dtype == np.float32
  assert not np.shares_memory(renderer._car_space_transform, incoming)
  renderer._transform_dirty = False
  renderer.set_transform(incoming)
  assert renderer._transform_dirty == bool(np.isnan(value))


def test_transform_guard_preserves_render_projection_updates(renderer, monkeypatch):
  from openpilot.selfdrive.ui.mici.onroad import model_renderer

  class Messages(dict):
    recv_frame = {"extrinsicsCalibration": 1, "modelV2": 1}
    updated = {"carParams": False, "modelV2": False, "radarState": False}
    valid = {"radarState": False}

  sm = Messages(carOutput=SimpleNamespace(actuatorsOutput=SimpleNamespace(torque=0)),
                selfdriveState=SimpleNamespace(experimentalMode=False),
                extrinsicsCalibration=SimpleNamespace(height=[]), modelV2=object())
  monkeypatch.setattr(model_renderer, "ui_state", SimpleNamespace(sm=sm, started_frame=0))
  renderer._torque_filter = Mock()
  renderer._path = SimpleNamespace(raw_points=np.array([[10, 0, 0]], dtype=np.float32))
  renderer._lead_indicator_enabled = False
  renderer._visual_status = lambda: model_renderer.UIStatus.DISENGAGED
  renderer._update_model = Mock()
  renderer._update_raw_points = Mock()
  renderer._transform_dirty = True

  renderer._render(renderer._clip_region)
  assert renderer._update_model.call_count == 1
  assert not renderer._transform_dirty
  disabled_leads = renderer._lead_vehicles
  renderer.set_transform(renderer._car_space_transform.copy())
  renderer._render(renderer._clip_region)
  assert renderer._update_model.call_count == 1
  assert renderer._lead_vehicles is disabled_leads
  renderer._lead_indicator_enabled = True
  renderer._update_leads = Mock()
  renderer._draw_lead_indicator = Mock()
  renderer._render(renderer._clip_region)
  renderer._update_leads.assert_called_once_with(sm)
  renderer._lead_indicator_enabled = False
  renderer._render(renderer._clip_region)
  reset_leads = renderer._lead_vehicles
  assert reset_leads is not disabled_leads
  assert all(lead.info is None and not lead.bar.size for lead in reset_leads)
  renderer._render(renderer._clip_region)
  assert renderer._lead_vehicles is reset_leads
  changed = renderer._car_space_transform.copy()
  changed[0, 0] += 1
  renderer.set_transform(changed)
  renderer._render(renderer._clip_region)
  assert renderer._update_model.call_count == 2
  for service in ("modelV2", "radarState"):
    sm.updated[service] = True
    renderer._render(renderer._clip_region)
    sm.updated[service] = False
  assert renderer._update_model.call_count == 4
  assert renderer._update_model.call_args.kwargs == {"update_lane_geometry": False}
  renderer._update_raw_points.assert_called_once_with(sm["modelV2"])


@pytest.mark.parametrize("renderer_class", [ModelRenderer, LargeModelRenderer])
def test_radar_only_reuses_lane_geometry_without_changing_path_filters_or_clip(renderer, renderer_class, monkeypatch):
  import copy

  if renderer_class is LargeModelRenderer:
    large_renderer = object.__new__(renderer_class)
    large_renderer._car_space_transform = renderer._car_space_transform
    large_renderer._clip_region = renderer._clip_region
    renderer = large_renderer

  points = np.column_stack((np.linspace(5, 100, 33), np.zeros(33), np.zeros(33))).astype(np.float32)
  renderer._path = ModelPoints(raw_points=points)
  renderer._lane_lines = [ModelPoints(raw_points=points + np.array([0, y, 0], dtype=np.float32)) for y in (-3.5, -1.8, 1.8, 3.5)]
  renderer._road_edges = [ModelPoints(raw_points=points + np.array([0, y, 0], dtype=np.float32)) for y in (-6, 6)]
  renderer._lane_line_probs = np.array([.6, .9, .8, .5], dtype=np.float32)
  renderer._acceleration_x = np.array([.2] * 33, dtype=np.float32)
  monkeypatch.setattr(renderer, "_acceleration_x_filter", Mock(), raising=False)
  monkeypatch.setattr(renderer, "_acceleration_x_filter2", Mock(), raising=False)
  renderer._experimental_mode = False
  renderer._car_space_transform[1, 2] = 480
  renderer._path_offset_z = 1.22
  renderer._update_experimental_gradient = Mock()
  reference = copy.deepcopy(renderer)
  renderer._update_model(None, points[:, 0])
  reference._update_model(None, points[:, 0])
  lane_arrays = [line.projected_points for line in [*renderer._lane_lines, *renderer._road_edges]]
  old_path = renderer._path.projected_points.copy()
  renderer._map_lines_to_polygons = Mock(wraps=renderer._map_lines_to_polygons)
  lead = SimpleNamespace(present=True, dRel=10.)
  renderer._update_model(lead, points[:, 0], update_lane_geometry=False)
  reference._update_model(lead, points[:, 0])
  # The large renderer also routes its single path polygon through the batch.
  batch_calls = renderer._map_lines_to_polygons.call_args_list
  assert len(batch_calls) == (1 if renderer_class is LargeModelRenderer else 0)
  assert all(len(call.args[0]) == 1 for call in batch_calls)
  for line, expected, previous in zip([*renderer._lane_lines, *renderer._road_edges],
                                     [*reference._lane_lines, *reference._road_edges], lane_arrays, strict=True):
    assert line.projected_points is previous
    np.testing.assert_array_equal(line.projected_points, expected.projected_points)
  np.testing.assert_array_equal(renderer._path.projected_points, reference._path.projected_points)
  assert renderer._path.projected_points.shape != old_path.shape
  filter_updates = 2 if renderer_class is ModelRenderer else 0
  assert renderer._acceleration_x_filter.update.call_count == reference._acceleration_x_filter.update.call_count == filter_updates
  assert renderer._acceleration_x_filter2.update.call_count == reference._acceleration_x_filter2.update.call_count == filter_updates
  assert renderer._update_experimental_gradient.call_count == reference._update_experimental_gradient.call_count == 2
  renderer._car_space_transform[0, 0] += 1
  renderer._update_model(lead, points[:, 0], update_lane_geometry=True)
  batch_calls = renderer._map_lines_to_polygons.call_args_list
  assert len(batch_calls) == (3 if renderer_class is LargeModelRenderer else 1)
  strips = 6 if renderer_class is LargeModelRenderer else 7
  assert sum(len(call.args[0]) == strips for call in batch_calls) == 1
  if renderer_class is ModelRenderer:
    assert batch_calls[-1].kwargs['invert_last'] is True
    assert batch_calls[-1].kwargs['z_offsets'] == (0, 0, 0, 0, 0, 0, renderer._path_offset_z)
  renderer._map_lines_to_polygons.reset_mock()
  renderer._clip_region.x += 1
  renderer._update_model(lead, points[:, 0], update_lane_geometry=False)
  batch_calls = renderer._map_lines_to_polygons.call_args_list
  assert len(batch_calls) == (2 if renderer_class is LargeModelRenderer else 1)
  strips = 6 if renderer_class is LargeModelRenderer else 7
  assert sum(len(call.args[0]) == strips for call in batch_calls) == 1
  if renderer_class is ModelRenderer:
    assert batch_calls[-1].kwargs['invert_last'] is True
    assert batch_calls[-1].kwargs['z_offsets'] == (0, 0, 0, 0, 0, 0, renderer._path_offset_z)


def test_large_render_preserves_lead_cadence_and_lane_invalidation(renderer, monkeypatch):
  from openpilot.selfdrive.ui.onroad import model_renderer

  class Messages(dict):
    recv_frame = {"extrinsicsCalibration": 1, "modelV2": 1}
    updated = {"carParams": False, "modelV2": False, "radarState": False}
    valid = {"radarState": True}

  sm = Messages(selfdriveState=SimpleNamespace(experimentalMode=False),
                extrinsicsCalibration=SimpleNamespace(height=[]), modelV2=object(),
                radarState=SimpleNamespace(leadOne=None))
  monkeypatch.setattr(model_renderer, "ui_state", SimpleNamespace(sm=sm, started_frame=0))
  view = object.__new__(LargeModelRenderer)
  view.lead_display = None
  view._path = ModelPoints(raw_points=np.array([[10, 0, 0]], dtype=np.float32))
  view._longitudinal_control = True
  view._update_model = Mock()
  view._update_raw_points = Mock()
  view._update_leads = Mock()
  view._draw_lane_lines = Mock()
  view._draw_path = Mock()
  view._draw_lead_indicator = Mock()
  view._transform_dirty = True
  view._render(renderer._clip_region)
  assert view._update_model.call_args.kwargs == {"update_lane_geometry": True}
  assert not view._transform_dirty
  view._render(renderer._clip_region)
  assert view._update_model.call_count == view._update_leads.call_count == 1
  sm.updated["radarState"] = True
  view._render(renderer._clip_region)
  assert view._update_model.call_args.kwargs == {"update_lane_geometry": False}
  sm.updated["radarState"] = False
  sm.updated["modelV2"] = True
  view._render(renderer._clip_region)
  assert view._update_model.call_args.kwargs == {"update_lane_geometry": True}
  view._update_raw_points.assert_called_once_with(sm["modelV2"])
  assert view._update_model.call_count == view._update_leads.call_count == 3
  assert view._draw_path.call_count == view._draw_lead_indicator.call_count == 4
