"""Lead source, labels, geometry and paint ownership across upstream integration."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import numpy as np
from openpilot.selfdrive.ui.mici.onroad import model_renderer as native


def fixture():
  renderer = native.ModelRenderer.__new__(native.ModelRenderer)
  renderer._lead_vehicles = [native.LeadVehicle(), native.LeadVehicle()]
  points = np.array([[0., 0., 0.], [20., 0., 0.], [100., 0., 0.]], dtype=np.float32)
  renderer._path = native.ModelPoints(raw_points=points)
  renderer._lane_lines = [native.ModelPoints(raw_points=points.copy()) for _ in range(4)]
  renderer._path_offset_z = 0.
  renderer._visual_status = Mock(return_value=native.UIStatus.ENGAGED)
  renderer._get_lead_bar = Mock(return_value=np.ones((4, 2), dtype=np.float32))
  good = NS(present=True, dRel=20., yRel=.1, vRel=-1., vLead=10.)
  values = {'radarState': NS(leadOne=good, leadTwo=NS(present=False)),
            'longitudinalPlan': NS(longitudinalPlanSource=native.log.LongitudinalPlan.LongitudinalPlanSource.lead0),
            'modelV2': NS(leadsV3=[]), 'selfdriveState': NS(enabled=True, engageable=True),
            'carState': NS(brakePressed=False, vEgo=14.)}
  class SubMaster(dict):
    valid = {'radarState': True, 'longitudinalPlan': True, 'modelV2': True, 'carState': True}

    def all_checks(self, services):
      return all(self.valid[service] for service in services)
  return renderer, SubMaster(values)


def test_radar_offset_labels_and_duplicate_lead_are_consistent():
  renderer, sm = fixture()
  sm['radarState'].leadTwo = NS(present=True, dRel=21., yRel=.2, vRel=0., vLead=11.)
  renderer._update_leads(sm)
  assert renderer._get_lead_bar.call_count == 1
  assert renderer._get_lead_bar.call_args.args[1:] == (20. + native.RADAR_TO_CAMERA, .1)
  assert renderer._format_lead_info(renderer._lead_vehicles[0].info, 1, True) == '20 m'
  assert renderer._format_lead_info(renderer._lead_vehicles[0].info, 2, True) == '36 km/h'
  assert renderer._format_lead_info(renderer._lead_vehicles[0].info, 3, True) == '-4 km/h'
  assert renderer._lead_vehicles[1].info is None and not renderer._lead_vehicles[1].bar.size


def test_e2e_without_radar_uses_matching_label_and_fails_closed_when_stale():
  renderer, sm = fixture()
  sm.valid = dict(sm.valid, radarState=False)
  sm['longitudinalPlan'].longitudinalPlanSource = native.log.LongitudinalPlan.LongitudinalPlanSource.e2e
  sm['modelV2'].leadsV3 = [NS(prob=.9, x=[30.], y=[-.2], v=[12.]), NS(prob=.1, x=[60.], y=[0.], v=[12.])]
  renderer._update_leads(sm)
  assert renderer._get_lead_bar.call_args.args[1:] == (30., .2)
  assert renderer._lead_vehicles[0].info.dRel == 30. - native.RADAR_TO_CAMERA
  assert renderer._format_lead_info(renderer._lead_vehicles[0].info, 2, False) == '27 mph'
  assert renderer._format_lead_info(renderer._lead_vehicles[0].info, 3, False) == '-4 mph'
  sm['carState'].vEgo = 10.
  renderer._update_leads(sm)
  assert renderer._format_lead_info(renderer._lead_vehicles[0].info, 3, False) == '+4 mph'
  sm.valid['carState'] = False
  renderer._update_leads(sm)
  assert renderer._format_lead_info(renderer._lead_vehicles[0].info, 3, False) == ''
  assert renderer._format_lead_info(renderer._lead_vehicles[0].info, 2, False) == '27 mph'
  sm.valid['modelV2'] = False
  renderer._update_leads(sm)
  assert all(lead.info is None and not lead.bar.size for lead in renderer._lead_vehicles)


def test_invalid_numbers_and_invalid_path_geometry_do_not_project():
  for field, value in [('dRel', float('nan')), ('yRel', float('inf')), ('vRel', 'bad'), ('dRel', -1.)]:
    renderer, sm = fixture()
    setattr(sm['radarState'].leadOne, field, value)
    renderer._update_leads(sm)
    renderer._get_lead_bar.assert_not_called()
    assert renderer._lead_vehicles[0].info is None
  for points in (np.empty((0, 3), dtype=np.float32), np.full((3, 3), np.nan, dtype=np.float32)):
    renderer, sm = fixture()
    renderer._path.raw_points = points
    renderer._update_leads(sm)
    renderer._get_lead_bar.assert_not_called()
    assert all(lead.info is None and not lead.bar.size for lead in renderer._lead_vehicles)


def test_missing_lane_lines_still_project_using_exact_model_path():
  renderer, sm = fixture()
  renderer._lane_lines[2].raw_points = np.empty((0, 3), dtype=np.float32)
  renderer._update_leads(sm)
  renderer._get_lead_bar.assert_called_once()
  assert renderer._get_lead_bar.call_args.args[0] is renderer._path.raw_points
  assert renderer._get_lead_bar.call_args.args[1:] == (20. + native.RADAR_TO_CAMERA, .1)
  assert renderer._lead_vehicles[0].info is not None and renderer._lead_vehicles[0].bar.size


def test_brake_and_independent_lateral_availability_retain_snap_and_fade():
  renderer, sm = fixture()
  sm['selfdriveState'].enabled = sm['selfdriveState'].engageable = False
  sm['carState'].brakePressed = True
  renderer._update_leads(sm)
  assert renderer._lead_vehicles[0].info is not None
  sm['carState'].brakePressed = False
  renderer._render_lateral_active = True
  renderer._update_leads(sm)
  assert renderer._lead_vehicles[0].info is not None
  sm['radarState'].leadOne.yRel = 8.
  renderer._update_leads(sm)
  assert renderer._get_lead_bar.call_args.args[2] == 8., 'new lane lead snaps rather than slides'


def test_projection_rejects_zero_depth_degenerate_and_nonfinite_results():
  renderer, _ = fixture()
  del renderer._get_lead_bar
  lane = renderer._lane_lines[1].raw_points
  for transform in (np.zeros((3, 3)), np.full((3, 3), float('nan')), np.array([[0., 0., 1.], [0., 0., 1.], [1., 0., 0.]])):
    renderer._car_space_transform = transform
    assert not renderer._get_lead_bar(lane, 20., 0.).size


def test_render_paint_and_saved_label_context_stay_owned_by_caller():
  renderer, sm = fixture()
  renderer._lead_indicator_enabled = False
  renderer._torque_filter = Mock()
  renderer._transform_dirty = False
  renderer._update_model = Mock()
  renderer._draw_lead_indicator = Mock()
  renderer._draw_lead_info = Mock()
  renderer._draw_lane_lines = Mock()
  renderer._draw_path = Mock()
  sm['carOutput'] = NS(actuatorsOutput=NS(torque=0.))
  sm['extrinsicsCalibration'] = NS(height=[])
  sm['selfdriveState'].experimentalMode = False
  sm.recv_frame = {'extrinsicsCalibration': 2, 'modelV2': 2}
  sm.updated = {'carParams': False, 'modelV2': False, 'radarState': False}
  rect = native.rl.Rectangle(0., 0., 476., 240.)
  with patch.object(native, 'ui_state', NS(sm=sm, started_frame=1, status=native.UIStatus.ENGAGED)), \
       patch.object(native.ModelRenderer, 'render', autospec=True, side_effect=lambda owner, area: owner._render(area)):
    renderer.render_with_lead(rect, True, 1, True, paint=False)
    renderer._draw_lead_indicator.assert_not_called()
    renderer._draw_lead_info.assert_not_called()
    renderer.render_with_lead(rect, True, 2, True)
    renderer._draw_lead_indicator.assert_called_once()
    renderer._draw_lead_info.assert_called_once_with(rect, '36 km/h')
    assert not renderer._lead_indicator_enabled and renderer._lead_info_mode == 0 and renderer._lead_info_metric is None
