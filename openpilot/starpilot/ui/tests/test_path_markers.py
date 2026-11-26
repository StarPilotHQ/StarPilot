"""Lead and stop lines shared by the large and compact model renderers."""
from collections import defaultdict
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from openpilot.selfdrive.ui.mici.onroad import model_renderer as small_model
from openpilot.selfdrive.ui.onroad import model_renderer as large_model
from openpilot.selfdrive.ui.onroad.model_renderer import ModelPoints
from openpilot.selfdrive.ui.ui_state import UIStatus
from openpilot.starpilot.ui import path_markers as pm
from openpilot.starpilot.ui.appearance_preferences import LeadInfoMode
from openpilot.starpilot.ui.onroad_cem_gauge import COLOR_FORCE_STOP
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.raylib import rl
from openpilot.system.ui.lib.shader_polygon import triangulate

W, H, F, CX, HORIZON = 2160, 1080, 1500., 1080., 430.
LEAD0 = pm.log.LongitudinalPlan.LongitudinalPlanSource.lead0
SHOWN = pm.PathMarkerInput(indicator=True)


class Renderer(pm.PathMarkers):
  """The marker layer over a pinhole road camera: u = cx - f*y/x, v = horizon + f*z/x."""
  def __init__(self, curve=0.):
    self._init_markers()
    self._rect = rl.Rectangle(0, 0, W, H)
    self._longitudinal_control = True
    self.road_style = {}
    x = np.linspace(0., 120., 241)
    path = np.column_stack((x, curve * x * x, np.zeros_like(x))).astype(np.float32)
    self._path = ModelPoints(raw_points=path)
    self._lane_lines = [ModelPoints(raw_points=path + np.array([0, y, 0], dtype=np.float32)) for y in (-5.4, -1.8, 1.8, 5.4)]
    self._lane_line_probs = np.array([.5, .9, .9, .5], dtype=np.float32)
    self._path_offset_z = 1.22
    self._car_space_transform = np.array([[CX, -F, 0.], [HORIZON, 0., F], [1., 0., 0.]], dtype=np.float32)

  def screen_x(self, x, y):
    return CX - F * y / x


class SubMaster(dict):
  def __init__(self, **values):
    super().__init__(values)
    self.valid = defaultdict(lambda: True)
    self.updated = defaultdict(lambda: False)

  def all_checks(self, services):
    return all(self.valid[service] for service in services)


def planned_stop_model(stop_m):
  x = np.linspace(0., 80., 33)
  return NS(leadsV3=[], position=NS(x=x.tolist()), velocity=NS(x=np.maximum(0., 15. * (1. - x / stop_m)).tolist()))


def messages(lead=None, source=LEAD0, stop_m=200.):
  return SubMaster(selfdriveState=NS(enabled=True, engageable=True), carState=NS(brakePressed=False, vEgo=0.),
                   longitudinalPlan=NS(longitudinalPlanSource=source), modelV2=planned_stop_model(stop_m),
                   radarState=NS(leadOne=lead or NS(present=False), leadTwo=NS(present=False)))


@pytest.fixture(autouse=True)
def ui(monkeypatch):
  state = NS(status=UIStatus.ENGAGED, CP=None)
  monkeypatch.setattr(pm, 'ui_state', state)
  return state


def run(renderer, sm, frames=1, markers=SHOWN):
  renderer._markers = markers
  for _ in range(frames):
    renderer._update_markers(sm)


def lead(d, y=0., v=0.):
  return NS(present=True, dRel=d, yRel=y, vRel=v, vLead=10. + v)


def test_screen_choices_come_from_one_place():
  state = OnroadState(True, True, 20, 80, SpeedLimitObservation(), lateral_active=True)
  on = replace(state, appearance=replace(state.appearance, show_lead_indicator=True, lead_info_mode=LeadInfoMode.SPEED,
                                         lead_info_metric=True))
  assert pm.path_markers(on) == pm.PathMarkerInput(False, 2, True, True, False), 'stale lead data keeps the lines off'
  fresh = replace(on, lead_indicator_source_fresh=True)
  assert pm.path_markers(fresh) == pm.PathMarkerInput(True, 2, True, True, False)
  assert pm.path_markers(replace(fresh, force_stop=True)).stop_committed is True
  assert pm.path_markers(replace(state, force_stop=True, lead_indicator_source_fresh=True)).stop_committed is False
  assert pm.path_markers(NS(appearance=None)) == pm.PathMarkerInput()


def test_both_renderers_share_the_layer_with_their_own_weight_and_font():
  assert (large_model.ModelRenderer.LINE_PX, large_model.ModelRenderer.LEAD_INFO_FONT) == ((4, 16), 40)
  assert (small_model.ModelRenderer.LINE_PX, small_model.ModelRenderer.LEAD_INFO_FONT) == ((2, 4), 32)
  assert large_model.ModelRenderer._update_markers is small_model.ModelRenderer._update_markers


def test_only_the_followed_car_gets_a_line_at_its_camera_distance():
  renderer = Renderer()
  sm = messages(lead(20., y=.1, v=-1.))
  sm['radarState'].leadTwo = lead(45., y=.2)
  assert renderer._lead_source(sm) == (20. + pm.RADAR_TO_CAMERA, sm['radarState'].leadOne)
  assert not hasattr(renderer, '_lead_markers'), 'the second lead is a two-second guess, not a second car'


def test_e2e_lead_labels_and_stale_speed():
  renderer = Renderer()
  sm = messages(source=pm.E2E)
  sm.valid['radarState'] = False
  sm['modelV2'].leadsV3 = [NS(prob=.9, x=[30.], y=[-.2], v=[12.]), NS(prob=.9, x=[60.], y=[0.], v=[12.])]
  sm['carState'].vEgo = 14.
  distance, info = renderer._lead_source(sm)
  assert distance == 30. and info.dRel == 30. - pm.RADAR_TO_CAMERA and info.vRel == -2.
  sm.valid['carState'] = False
  assert renderer._lead_source(sm)[1].vRel is None


@pytest.mark.parametrize('field,value', [('dRel', float('nan')), ('yRel', float('inf')), ('vRel', 'bad'), ('dRel', -1.)])
def test_invalid_lead_numbers_draw_nothing(field, value):
  renderer, bad = Renderer(), lead(20.)
  setattr(bad, field, value)
  run(renderer, messages(bad))
  assert renderer._lead.box is None and renderer._lead.info is None


@pytest.mark.parametrize('d', [60., 25., 8., 3., 1.4])
def test_lead_line_is_a_painted_strip_that_stays_thin_on_screen_and_never_spans_more_than_30_percent(d):
  renderer = Renderer()
  run(renderer, messages(lead(d)))
  strip, box = renderer._lead.strip, renderer._lead.box
  assert 4 - 1e-3 <= box.height <= 16 + 1e-3, 'perspective depth, clamped so it never thins out or becomes a block'
  assert box.width <= 0.3 * W + 1e-3
  assert box.y + box.height <= H - 16 + 1e-3
  far_edge = abs(strip[2, 0] - strip[1, 0])
  assert far_edge == pytest.approx(min(1.8 * F / (d + pm.RADAR_TO_CAMERA), far_edge if box.width >= 0.3 * W - 1 else 1e9), rel=1e-3)
  assert strip[0, 1] > strip[1, 1], 'the near edge sits lower on screen, lying on the road'


def test_strips_wind_like_the_path_ribbon_so_raylib_does_not_cull_them():
  renderer = Renderer(curve=.002)
  renderer._clip_region = rl.Rectangle(-500, -500, W + 1000, H + 1000)
  path = renderer._path.raw_points
  ribbon = large_model.ModelRenderer._map_lines_to_polygons(renderer, [path], [.9], renderer._path_offset_z, len(path) - 1, 100.)[0]
  run(renderer, messages(lead(25.)))

  def winding(points):
    a, b, c = triangulate(points)[:3]
    return np.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
  assert winding(renderer._lead.strip) == winding(ribbon) != 0


def test_urgency_matches_the_old_chevron_scale_and_sets_opacity(ui):
  assert pm.urgency(40., -5.) == 0. and pm.urgency(20., 0.) == .5 and pm.urgency(20., -2.) == pytest.approx(.7)
  assert pm.urgency(5., -10.) == 1. and pm.urgency(20., None) == .5
  for status, expected in ((UIStatus.ENGAGED, .7 + .3 * .5), (UIStatus.DISENGAGED, .35 + .15 * .5)):
    ui.status = status
    renderer = Renderer()
    run(renderer, messages(lead(20.)), frames=200)
    assert renderer._lead.fade_filter.x == pytest.approx(expected, abs=1e-3)


def test_availability_brake_lateral_and_dashcam(ui):
  sm = messages(lead(20.))
  sm['selfdriveState'].enabled = sm['selfdriveState'].engageable = False
  renderer = Renderer()
  run(renderer, sm)
  assert renderer._lead.info is None
  sm['carState'].brakePressed = True
  run(renderer, sm)
  assert renderer._lead.info is not None
  sm['carState'].brakePressed = False
  run(renderer, sm, markers=pm.PathMarkerInput(indicator=True, lateral_active=True))
  assert renderer._lead.info is not None
  ui.CP = NS(passive=True)
  sm['carState'].brakePressed = True
  run(renderer, sm)
  assert renderer._lead.info is None, 'dashcam mode never shows markers'
  ui.CP = None
  run(renderer, sm, markers=pm.PathMarkerInput())
  assert renderer._lead.info is None


def test_line_stays_on_the_path_while_the_lead_drifts_in_its_lane():
  renderer, sm = Renderer(curve=.004), messages(lead(25.))
  centers = []
  for y in (-.8, 0., .8, -1.5):
    sm['radarState'].leadOne = lead(25., y=y)
    run(renderer, sm)
    centers.append(float(renderer._lead.strip[[1, 2], 0].mean()))
  d = 25. + pm.RADAR_TO_CAMERA
  assert len(set(centers)) == 1, 'the car drifting in its lane never moves the line sideways'
  assert centers[0] == pytest.approx(renderer.screen_x(d, .004 * d * d), abs=.1)


def frames(seconds):
  return round(seconds * pm.gui_app.target_fps)


def test_a_farther_flicker_is_ignored_until_it_lasts():
  renderer, sm = Renderer(), messages(lead(20.))
  run(renderer, sm, frames=30)
  near, far = sm['radarState'].leadOne, lead(40.)
  for _ in range(frames(1.)):
    sm['radarState'].leadOne = far
    run(renderer, sm)
    sm['radarState'].leadOne = near
    run(renderer, sm)
  assert renderer._lead.d_filter.x == pytest.approx(20. + pm.RADAR_TO_CAMERA) and renderer._lead.info is near
  sm['radarState'].leadOne = far
  run(renderer, sm, frames=frames(pm.LEAD_HOLD) - 1)
  assert renderer._lead.d_filter.x == pytest.approx(20. + pm.RADAR_TO_CAMERA), 'held until the farther car lasts'
  run(renderer, sm, frames=2)
  assert renderer._lead.gliding and renderer._lead.info is far


def test_a_closer_car_glides_in_at_once_and_snaps_onto_it():
  renderer, sm = Renderer(), messages(lead(40.))
  run(renderer, sm, frames=30)
  sm['radarState'].leadOne = lead(15.)
  run(renderer, sm)
  target = 15. + pm.RADAR_TO_CAMERA
  assert renderer._lead.gliding and target < renderer._lead.d_filter.x < 40. + pm.RADAR_TO_CAMERA, 'glides, no jump'
  run(renderer, sm, frames=frames(2.))
  assert renderer._lead.d_filter.x == target and not renderer._lead.gliding, 'snapped exactly onto the car'


def test_recovery_ui_follows_upstream_without_text():
  renderer = Renderer()
  renderer._markers = None
  assert renderer._marker_input() == pm.PathMarkerInput(indicator=True)
  renderer._longitudinal_control = False
  assert renderer._marker_input().indicator is False


def test_render_context_resets_after_a_failed_frame():
  renderer = Renderer()
  renderer.render = Mock(side_effect=RuntimeError('lost frame'))
  with pytest.raises(RuntimeError, match='lost frame'):
    renderer.render_with_markers(renderer._rect, pm.PathMarkerInput(indicator=True), paint=False)
  assert renderer._markers is None and renderer._paint is True


def stop_box(renderer, sm, markers=SHOWN, frames=90):
  sm.updated['modelV2'] = True
  run(renderer, sm, frames=frames, markers=markers)
  return renderer._stop_point.box


def test_stop_line_shows_for_e2e_plans_and_turns_red_when_committed():
  renderer = Renderer()
  box = stop_box(renderer, messages(source=pm.E2E, stop_m=28.))
  strip = renderer._stop_point.strip
  assert box is not None and 4 - 1e-3 <= box.height <= 16 + 1e-3 and renderer._stop_point.committed is False
  assert abs(strip[2, 0] - strip[1, 0]) == pytest.approx(3.6 * F / (renderer._stop_point.d + pm.RADAR_TO_CAMERA), rel=1e-2), 'spans the lane'
  stop_box(renderer, messages(source=pm.E2E, stop_m=28.), markers=pm.PathMarkerInput(indicator=True, stop_committed=True), frames=1)
  assert renderer._stop_point.committed is True
  draws = Mock()
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(pm, 'draw_polygon', draws)
    renderer._markers = pm.PathMarkerInput(indicator=True, stop_committed=True)
    renderer._draw_markers()
  color = draws.call_args.args[2]
  assert (color.r, color.g, color.b) == (COLOR_FORCE_STOP.r, COLOR_FORCE_STOP.g, COLOR_FORCE_STOP.b)


def test_chill_stop_line_waits_for_a_committed_force_stop():
  renderer = Renderer()
  assert stop_box(renderer, messages(stop_m=28.)) is None
  assert stop_box(renderer, messages(stop_m=28.), markers=pm.PathMarkerInput(indicator=True, stop_committed=True)) is not None
  renderer._longitudinal_control = False
  assert stop_box(renderer, messages(stop_m=28.), markers=pm.PathMarkerInput(indicator=True, stop_committed=True), frames=400) is None


def test_stop_line_fades_out_close_in_and_is_never_held_at_the_bottom():
  renderer = Renderer()
  stop_box(renderer, messages(source=pm.E2E, stop_m=28.))
  stop_box(renderer, messages(source=pm.E2E, stop_m=7.), frames=400)
  fade = (renderer._stop_point.d + pm.RADAR_TO_CAMERA - 6.) / 4.
  assert 0 < fade < 1 and renderer._stop_point.fade_filter.x == pytest.approx(fade, abs=1e-2)
  stop_box(renderer, messages(source=pm.E2E, stop_m=2.), frames=400)
  assert renderer._stop_point.box is None


def test_stop_line_hides_when_a_lead_is_just_past_the_stop():
  renderer = Renderer()
  assert stop_box(renderer, messages(lead(28. + 20.), source=pm.E2E, stop_m=28.)) is not None
  assert stop_box(renderer, messages(lead(28. + 8.), source=pm.E2E, stop_m=28.), frames=400) is None


def test_stop_line_is_centered_on_the_path_on_a_curve():
  renderer = Renderer(curve=.004)
  stop_box(renderer, messages(source=pm.E2E, stop_m=28.))
  d = renderer._stop_point.d + pm.RADAR_TO_CAMERA
  on_path = renderer.screen_x(d, .004 * d * d)
  assert float(renderer._stop_point.strip[[1, 2], 0].mean()) == pytest.approx(on_path, abs=2)
  assert abs(on_path - CX) > 100, 'upstream centered it on the straight-ahead line instead'


def draw_text(monkeypatch, line, avoid=None, text='66 ft', alpha=.5):
  renderer, draw = Renderer(), Mock()
  monkeypatch.setattr(pm.gui_app, 'font', lambda *_: object())
  monkeypatch.setattr(pm, 'measure_text_cached', lambda _font, label, size: NS(x=size * len(label) / 2, y=size))
  monkeypatch.setattr(pm.rl, 'draw_text_ex', draw)
  renderer._draw_lead_info(line, text, alpha, avoid)
  return [(call.args[1], call.args[2].x, call.args[2].y, call.args[3], call.args[5].r, call.args[5].a)
          for call in draw.call_args_list if call.args[5].r == 255]


@pytest.mark.parametrize('line_y,avoid,top', [
  (500., None, 500. + 16 + 16),  # below the line, on the path
  (H - 40., None, H - 40. - 16 - 64),  # no room below
  (500., rl.Rectangle(0, 540, W, 16), 500. - 16 - 64),  # would cover the stop line
])
def test_number_sits_over_a_smaller_unit_below_the_line_unless_it_cannot(monkeypatch, line_y, avoid, top):
  drawn = draw_text(monkeypatch, rl.Rectangle(1000, line_y, 200, 16), avoid)
  assert drawn == [('66', 1080., top, 40, 255, 127), ('ft', 1100. - 12, top + 40, 24., 255, 127)]


def test_text_shrinks_to_fit_the_path_and_fades_out_below_the_minimum(monkeypatch):
  number, unit = draw_text(monkeypatch, rl.Rectangle(1000, 500, 30, 16), alpha=1.)
  assert (number[3], unit[3]) == pytest.approx((30., 18.)), 'the widest line spans the path under the lead line'
  assert draw_text(monkeypatch, rl.Rectangle(1000, 500, 15, 16), alpha=1.)[0][5] == 127, 'half faded at 15 px'
  assert draw_text(monkeypatch, rl.Rectangle(1000, 500, 12, 16), alpha=1.) == [], 'unreadably small: not drawn'
