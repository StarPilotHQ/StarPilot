"""Birds-eye strip drawing helpers and the extra model / curvature / speed-limit data, without a GL context."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.ui.onroad import birds_eye_math as bem
from openpilot.selfdrive.ui.tests.test_birds_eye_math import RECT, _SM, _draw_ops, _layers, _model, _op_points, _renderer_with_scene, _xyz


def _lead_v3(prob, x0, y0, dx=(0.0, -4.0, -8.0, -12.0, -16.0, -20.0), dy=(0.0, 0.3, 0.6, 0.9, 1.2, 1.5)):
  return SimpleNamespace(prob=prob, probTime=0.0, x=[x0 + d for d in dx], y=[y0 + d for d in dy], v=[20.0] * 6, a=[0.0] * 6)


def _rich_model(**meta):
  model = _model()
  model.laneLineStds = [0.1, 0.2, 0.9, 0.5]
  model.meta = SimpleNamespace(laneChangeState=meta.get('state', 'off'), laneChangeDirection=meta.get('direction', 'none'),
                               hardBrakePredicted=meta.get('hard', False))
  model.action = SimpleNamespace(shouldStop=meta.get('stop', False))
  model.velocity = SimpleNamespace(x=[20.0] * 33)
  model.orientationRate = SimpleNamespace(z=[0.0] * 16 + [0.4] * 17)  # 0.02 1/m from ~58 m on, turning right
  return model


# ---- strip helpers -------------------------------------------------------------------------------

def test_strip_points_interleave_left_and_right():
  left, right = np.array([[0.0, 0.0], [0.0, 1.0]]), np.array([[2.0, 0.0], [2.0, 1.0]])
  pts = bem.strip_points(left, right)
  assert pts.dtype == np.float32 and pts.flags.c_contiguous
  assert pts.tolist() == [[0, 0], [2, 0], [0, 1], [2, 1]]


def test_ring_strip_is_a_closed_stroke_of_the_given_width():
  square = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])
  ring = bem.ring_strip(square, 2.0)
  assert ring.shape == (10, 2) and np.allclose(ring[:2], ring[-2:]), 'closed: the first pair repeats'
  outer, inner = ring[0:8:2], ring[1:8:2]
  # Centred on the edges: one point of each corner pair lies 1 px outside the square, the other 1 px inside.
  spans = sorted([np.ptp(outer[:, 0]), np.ptp(inner[:, 0])])
  assert spans == pytest.approx([8.0, 12.0])


def test_soft_end_length_and_taper():
  assert bem.end_fade_length(-5.0, 10.0) == pytest.approx(4.0), 'standstill: 25 % of 15 m is under the 4 m minimum'
  assert bem.end_fade_length(-5.0, 35.0) == pytest.approx(10.0), '25 % of the drawn length'
  assert bem.end_fade_length(-5.0, 100.0) == 12.0, 'capped at 12 m'
  assert bem.end_fade_length(0.0, 2.0) == 2.0, 'never longer than the line'
  assert bem.taper([0.0, 7.0, 8.5, 10.0, 11.0], 10.0, 3.0).tolist() == pytest.approx([1.0, 1.0, 0.5, 0.0, 0.0])


def test_alpha_groups_cover_every_row_within_half_a_step():
  alpha = np.interp(np.linspace(0, 1, 80), [0.0, 0.35, 0.9, 1.0], [1.0, 0.8, 0.0, 0.0])
  groups = bem.alpha_groups(alpha, 0.04)
  assert groups[0][0] == 0 and groups[-1][1] == alpha.size
  assert all(a[1] - 1 == b[0] for a, b in zip(groups, groups[1:], strict=False)), 'consecutive strips share a row'
  seg = (alpha[:-1] + alpha[1:]) / 2
  for start, end, value in groups:
    assert np.abs(seg[start:end - 1] - value).max() <= 0.02 + 1e-9
  assert len(groups) <= 1 / 0.04 + 2


# ---- model data ----------------------------------------------------------------------------------

def test_lane_change_codes_accept_names_and_raw_values():
  assert [bem.lane_change_code(s) for s in ('off', 'preLaneChange', 'laneChangeStarting', 'laneChangeFinishing')] == [0, 1, 2, 3]
  assert bem.lane_change_code(2) == 2 and bem.lane_change_code(7) == 0 and bem.lane_change_code(None) == 0
  assert [bem.lane_change_side(d) for d in ('none', 'left', 'right', 1, 2, 0)] == [0, -1, 1, -1, 1, 0]


def test_lane_width_from_the_lane_lines_with_a_fallback():
  grid = bem.sample_grid()
  lanes = [np.full(grid.shape, off) for off in (-5.4, -1.8, 1.8, 5.0)]
  assert bem.lane_width(lanes, grid, 1.5, -1) == pytest.approx(3.6)
  assert bem.lane_width(lanes, grid, 1.5, 1) == pytest.approx(3.2)
  lanes[3] = np.full(grid.shape, np.nan)
  assert bem.lane_width(lanes, grid, 1.5, 1) == bem.DEFAULT_LANE_WIDTH, 'a missing line'
  lanes[3] = np.full(grid.shape, 30.0)
  assert bem.lane_width(lanes, grid, 1.5, 1) == bem.DEFAULT_LANE_WIDTH, 'an implausible width'
  assert bem.lane_width([], grid, 1.5, -1) == bem.DEFAULT_LANE_WIDTH


def test_std_alpha_and_closing_speed_label():
  assert [bem.std_alpha(s) for s in (0.0, 0.3, 0.9, 5.0, math.nan)] == pytest.approx([1.0, 0.7, 0.35, 0.35, 1.0])
  assert bem.closing_speed_label(-2.235, metric=False) == '-5 mph'
  assert bem.closing_speed_label(-2.235, metric=True) == '-8 km/h'
  assert bem.closing_speed_label(1.0) == '+4 km/h'
  assert bem.closing_speed_label(0.4) is None and bem.closing_speed_label(math.nan) is None


def test_model_lead_tracks():
  leads = [_lead_v3(0.9, 31.5, 0.2), _lead_v3(0.3, 50.0, 0.0), SimpleNamespace(prob=0.9, x=[math.nan] * 6, y=[0.0] * 6)]
  tracks = bem.model_lead_tracks(leads, 0.5)
  assert len(tracks) == 1 and tracks[0][0][0] == 31.5


def test_path_curvature_is_signed_rate_over_speed():
  model = SimpleNamespace(position=_xyz(np.arange(5.0) * 10, np.zeros(5)), velocity=SimpleNamespace(x=[2.0, 10.0, 10.0, 20.0, 20.0]),
                          orientationRate=SimpleNamespace(z=[0.5, 0.05, -0.2, 0.1, 5.0]))
  xs, kappa = bem.path_curvature(model)
  assert xs.tolist() == [0, 10, 20, 30, 40]
  assert kappa.tolist() == pytest.approx([0.0, 0.005, -0.02, 0.005, 0.1]), 'zero below 3 m/s, capped at 0.1'
  assert bem.path_curvature(SimpleNamespace(position=model.position)) is None


# ---- renderer ingest and geometry ----------------------------------------------------------------

def test_ingest_reads_the_extra_model_data():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  view = BirdsEyeRenderer(text=object())
  model = _rich_model(state='laneChangeStarting', direction='left', hard=True, stop=True)
  model.leadsV3 = [_lead_v3(0.9, 31.5, 0.2), _lead_v3(0.8, 33.0, 0.4), _lead_v3(0.7, 60.0, 3.7)]
  view.ingest(model, None, SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=True, rightBlinker=False, leftBlindspot=True,
                                           rightBlindspot=False, vEgo=20.0))
  scene = view.scene
  assert (scene.lane_change_state, scene.lane_change_direction) == (2, -1)
  assert scene.hard_brake and scene.should_stop and scene.left_blindspot and not scene.right_blindspot
  assert scene.lane_stds.tolist() == pytest.approx([0.1, 0.2, 0.9, 0.5])
  assert len(scene.model_leads) == 3
  assert [(round(lead.d_rel), lead.radar) for lead in scene.leads] == [(30, False), (58, False)], \
    'every confident model lead, one glyph per car (the 2nd entry is the same car)'
  marked = scene.grid[np.abs(np.nan_to_num(scene.path_curvature)) >= bem.CURVE_MARK]
  assert marked.size and marked.min() > 55.0 and (scene.path_curvature[np.isfinite(scene.path_curvature)] >= 0).all()
  # Lane line certainty: the third line (std 0.9) is drawn at 35 % of its alpha.
  styles = view._line_styles(scene)[len(scene.edges):]
  plain = BirdsEyeRenderer(text=object())
  plain.ingest(_model(), None, None)
  base = plain._line_styles(plain.scene)[len(plain.scene.edges):]
  assert [s[4] / b[4] for s, b in zip(styles, base, strict=True)] == pytest.approx([0.9, 0.8, 0.35, 0.5])
  # A model frame without the fields reads as absent, and keyword-only callers still work.
  view.ingest(_model(), None, None, engaged=True)
  assert scene.lane_change_state == 0 and not scene.hard_brake and not scene.should_stop and scene.path_curvature is None


def test_path_ends_short_of_a_lead_on_it_not_one_beside_it():
  view, radar = _renderer_with_scene()  # radar lead at 30 m, y 0: camera rule would end the path at 50 m
  _, path = _layers(view)
  assert path.end[0] == pytest.approx(30.0 + 1.5 - 1.0), 'rear bumper - 1 m'
  radar.leadOne.yRel = -3.6
  view.ingest(None, radar, None)
  view.scene.leads = [view.scene.leads[0].__class__(30.0, 3.6, -2.0)]
  _, path = _layers(view)
  assert path.end[0] == pytest.approx(50.0, abs=0.3), 'a lead one lane over keeps the camera rule'


def _standstill_view():
  view, radar = _renderer_with_scene()
  radar.leadOne.present = False
  short = _model()
  short.position = _xyz(np.linspace(0, 3, 33), np.zeros(33))  # standstill: the camera draws 10 m
  view.ingest(short, radar, SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False, vEgo=0.0))
  view.scene.leads = []
  stream = SimpleNamespace(x=0.0, y=0.0, width=1280.0, height=720.0)
  return view, stream, view._reference(stream.height, bem.ZOOM_NEAR_X_MAX)


def test_every_line_and_the_path_end_with_a_hard_cut_at_the_draw_distance():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  assert ber.SOFT_ENDS is False, 'no fades in the design'
  view, stream, reference = _standstill_view()
  road = view._road_draw(view.scene, reference)
  for ops in (road.road, road.lines):
    assert max(_op_points(op)[:, 0].max() for op in _draw_ops(ops)) == pytest.approx(10.0, abs=0.1), 'cut at the 10 m draw distance'
  lines = _draw_ops(road.lines)
  assert len(lines) == 6 and len(_draw_ops(road.road)) == 1, 'one strip per line and for the road: no stepped fade'
  path = view._path_draw(view.scene, reference)
  strips = _draw_ops(path.strips)
  assert len(strips) == 1, 'the path is one strip of one alpha'
  assert _op_points(strips[-1])[:, 0].max() <= 10.0 + 0.1, 'and still ends by the draw distance'


def test_each_line_ends_at_its_own_uncertainty_distance():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  model = _model()
  xs = np.asarray(model.laneLines[1].x)
  model.laneLines[1].yStd = list(0.03 * xs)  # passes 1 m at ~33 m
  model.laneLines[2].yStd = list(0.02 * xs)  # at 50 m
  view = BirdsEyeRenderer(text=object())
  view.ingest(model, None, None, lat_active=True, lane_centering_direction=-1)  # lines 1 and 2: blue and green
  grid = view.scene.grid
  ends = [bem.std_end(grid, std) for std in view.scene.lane_ystds]
  assert ends[0] == ends[3] == math.inf, 'no yStd published: the global cutoff only'
  assert 33.0 <= ends[1] < 37.0 and 50.0 <= ends[2] < 55.0
  road = view._build_road(view.scene, view._reference(720.0, view.config.x_max), 100.0)
  styles = view._line_styles(view.scene)
  first_lane = len(view.scene.edges)
  reached = []
  ops = _draw_ops(road.lines)
  for i in (1, 2):
    rgb = styles[first_lane + i][3]
    mine = [op for op in ops if (op[3].r, op[3].g, op[3].b) == tuple(int(c) for c in rgb)]
    reached.append(max(_op_points(op)[:, 0].max() for op in mine))
  assert reached[0] == pytest.approx(ends[1], abs=0.3) and reached[1] == pytest.approx(ends[2], abs=0.3), 'two different ends'
  assert bem.std_end(grid, np.zeros(grid.size)) == math.inf, 'all-zero stds (not filled) fall back'


def test_far_lines_are_widened_to_a_minimum_pixel_width():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  view, _ = _renderer_with_scene()
  reference = view._reference(720.0, view.config.x_max)
  road = view._road_draw(view.scene, reference)
  body = [op for op in road.lines if op[0] is not ber._keep and op[3].a == max(o[3].a for o in road.lines[-6:] if o[0] is not ber._keep)][-1]
  points = _op_points(body)  # the last lane line's body strip (its most opaque one): [L0, R0, L1, R1, ..]
  xs, widths = points[0::2, 0], np.abs(points[0::2, 1] - points[1::2, 1])
  near, far = np.argmin(np.abs(xs - 0.0)), np.argmax(xs)
  assert widths[near] == pytest.approx(max(ber.LANE_WIDTH, ber.LANE_MIN_PX * float(bem.meters_per_pixel(xs[near], reference.view))), rel=1e-3)
  assert widths[far] > 2 * ber.LANE_WIDTH, 'a painted line 100 m away would be under a pixel'
  assert widths[far] * float(bem.scale_at(xs[far], reference.view)) == pytest.approx(ber.LANE_MIN_PX, rel=1e-3)


def test_lane_change_ghost_sits_one_lane_over_and_turns_red_on_a_blind_spot(monkeypatch):
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  view, _ = _renderer_with_scene()
  monkeypatch.setattr(ber, 'SHOW_LANE_CHANGE_GHOST', True)
  assert view._lane_change_ghost(view.scene) == [], 'nothing without a lane change (blinkers alone are the HUD)'
  for state, blind, rgb in (('preLaneChange', False, ber.GHOST_PRE), ('laneChangeStarting', False, ber.GHOST_GO),
                            ('laneChangeStarting', True, ber.GHOST_BLOCKED)):
    view.ingest(_rich_model(state=state, direction='right'), None,
                SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=True, leftBlindspot=False, rightBlindspot=blind))
    fill, outline = view._lane_change_ghost(view.scene)
    assert _op_points(fill)[:, 1].mean() == pytest.approx(3.6, abs=0.05), 'laneLines[3] - [2] at the ego, on the ground in meters'
    assert (outline[3].r, outline[3].g, outline[3].b) == rgb


def _slc_sm(now_ns, *, limit=None, curve=None, stamp_ns=None):
  from openpilot.cereal import messaging
  msg = messaging.new_message('slcState')
  slc = msg.slcState
  if limit is not None:
    slc.observationKind = 'valid'
    slc.speedLimit = limit
    slc.sessionId = 'abc'
  if curve is not None:
    stamp = now_ns - 1000
    slc.curve = {'version': 1, 'sessionId': 'cafe', 'sequence': 3, 'observedMonoTime': stamp, 'validUntilMonoTime': stamp + 50_000_000,
                 'modelMonoTime': stamp - 1000, 'configured': True, 'documentValid': True, 'comfortAccel': 2.0,
                 'calibrationProgress': 50.0, 'bindingDistance': curve['distance'], 'plannerStatus': 'selected',
                 'hasCandidate': True, 'candidateMps': curve['candidate'], 'hasCeiling': 'ceiling' in curve,
                 'ceilingMps': curve.get('ceiling', 0.0), 'applied': curve.get('controlling', False),
                 'controlling': curve.get('controlling', False)}
  sm = _SM({'slcState': slc}, frame=12, updated=True)
  sm.alive = {'slcState': True}
  sm.logMonoTime = {'slcState': now_ns - 1000 if stamp_ns is None else stamp_ns}
  return sm


def test_poll_reads_curve_and_speed_limit_through_the_qualified_readers():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer, CurveMark
  now = 10_000_000_000
  view = BirdsEyeRenderer(text=object())
  view.clock_ns = lambda: now
  view.poll(_slc_sm(now, limit=24.6, curve={'distance': 80.0, 'candidate': 20.0, 'ceiling': 18.0, 'controlling': True}), 10)
  assert view.scene.speed_limit_mps == pytest.approx(24.6)
  assert view.scene.curve == CurveMark(pytest.approx(18.0), pytest.approx(80.0), True, True), 'the ceiling wins'
  view.poll(_slc_sm(now, curve={'distance': 40.0, 'candidate': 20.0}), 10)
  assert view.scene.speed_limit_mps is None, 'no valid observation, no sign'
  assert view.scene.curve == CurveMark(pytest.approx(20.0), pytest.approx(40.0), False, False)
  view.poll(_slc_sm(now, limit=24.6, curve={'distance': 40.0, 'candidate': 20.0}, stamp_ns=now - 1_000_000_000), 10)
  assert view.scene.speed_limit_mps is None and view.scene.curve is None, 'stale slcState shows nothing'
  view.poll(_SM({}, frame=12, updated=True), 10)
  assert view.scene.curve is None and view.scene.speed_limit_mps is None


def test_lead_labels_show_the_distance_only_and_pulse_on_hard_brake(monkeypatch):
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  drawn, pills = [], []
  view, radar = _renderer_with_scene()
  view.text = SimpleNamespace(measure=lambda text, size: (0.5 * size * len(text), size), draw=lambda text, size, *a: drawn.append(text))
  view.metric = False
  monkeypatch.setattr(ber, '_run', lambda *a: None)
  monkeypatch.setattr(ber.rl, 'draw_rectangle_rounded', lambda *a: None)
  monkeypatch.setattr(ber.rl, 'draw_rectangle_rounded_lines_ex', lambda *a: pills.append(a))
  monkeypatch.setattr(ber.rl, 'draw_circle_v', lambda *a: pytest.fail('no prediction dots ahead of a lead'))
  pv = bem.view_params(RECT, view.config)
  view._leads(view.scene, RECT, pv)
  assert drawn == ['33 yd'] and len(pills) == 1, 'no closing-speed line under the distance; the one outline is the glass rim'
  view.scene.hard_brake = True
  drawn.clear()
  pills.clear()
  view._leads(view.scene, RECT, pv)
  assert drawn == ['33 yd'] and len(pills) == 2, 'the pill border pulses over the rim'
  # A second car ahead of the lead is drawn, but only the closest lead carries a distance.
  cars = []
  monkeypatch.setattr(ber, '_run', lambda ops: cars.append(ops))
  view.scene.leads = [*view.scene.leads, ber.BirdsEyeLead(48.0, 0.0, 0.0, radar=True)]
  drawn.clear()
  view._leads(view.scene, RECT, pv)
  assert len(cars) == 2 and drawn == ['33 yd']
  assert [pose[0] for _, pose in view._lead_poses(view.scene, pv)] == sorted((pose[0] for _, pose in view._lead_poses(view.scene, pv)), reverse=True), \
    'far to near, so a nearer car covers a farther one'
  shadows = view._shadows([pose for _, pose in view._lead_poses(view.scene, pv)])
  assert len(shadows) == 2 and all(op[0] is ber._raw.DrawTriangleFan and op[3].a == ber.SHADOW_ALPHA for op in shadows)


def test_curve_mark_is_hidden_but_still_ingested(monkeypatch):
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  assert ber.SHOW_CURVE_MARK is False
  drawn = []
  view, _ = _renderer_with_scene()
  view.text = SimpleNamespace(measure=lambda text, size: (0.5 * size * len(text), size), draw=lambda text, size, *a: drawn.append(text))
  view.ingest(curve=ber.CurveMark(18.0, 40.0, True, True))
  assert view.scene.curve == ber.CurveMark(18.0, 40.0, True, True)
  view.scene.should_stop = True
  path = SimpleNamespace(end=(20.0, 0.0, 0.0))
  reference = view._reference(RECT.height, view.config.x_max)
  bars = view._marks(view.scene, reference, path)
  view._curve_label(view.scene, RECT, reference.view)
  assert len(bars) == 1 and drawn == [], 'only the shouldStop bar; no curve bar or tag'
  stop = _op_points(bars[0])
  assert stop[:, 0].mean() == pytest.approx(20.0) and np.ptp(stop[:, 1]) == pytest.approx(ber.PATH_WIDTH * 1.5), 'across the path end, in meters'


def test_the_lane_change_ghost_is_off_by_default():
  # 2026-11-09: the operator did not want the green outline one lane over; the feature is gated, not deleted.
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  assert ber.SHOW_LANE_CHANGE_GHOST is False
  view, _ = _renderer_with_scene()
  view.ingest(_rich_model(state='laneChangeStarting', direction='right'), None,
              SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=True, leftBlindspot=False, rightBlindspot=False))
  assert view._lane_change_ghost(view.scene) == []


def test_radar_lead_without_a_finite_lateral_position_is_not_drawn():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import scene_leads
  def lead(**values):
    return SimpleNamespace(**{'present': True, 'dRel': 30.0, 'yRel': 0.5, 'vRel': -1.0, **values})
  radar = SimpleNamespace(leadOne=lead(yRel=math.nan), leadTwo=lead(dRel=50.0, vRel=math.nan))
  leads = scene_leads(radar, None, [])
  assert [(found.d_rel, found.y, found.v_rel) for found in leads] == [(50.0, -0.5, 0.0)]


def test_road_of_a_stopped_or_earlier_model_is_dropped():
  renderer = _renderer_with_scene()
  assert renderer.scene.valid
  names = ('modelV2', 'radarState', 'carState', 'selfdriveState', 'longitudinalPlan', 'carControl')
  def messages(frame, alive):
    return SimpleNamespace(valid=dict.fromkeys(names, False), updated=dict.fromkeys(names, False),
                           recv_frame=dict.fromkeys(names, frame), alive={**dict.fromkeys(names, True), 'modelV2': alive},
                           seen=dict.fromkeys(names, False))
  renderer._lane_centering_direction = lambda *args: 0
  renderer._read_speed_limit = lambda *args: None
  renderer.poll(messages(10, True), 5)
  assert renderer.scene.valid, 'between model messages the road stays'
  renderer.poll(messages(10, False), 5)
  assert not renderer.scene.valid, 'the model stopped'
  renderer.scene.valid = True
  renderer.poll(messages(10, True), 20)
  assert not renderer.scene.valid, 'the model is from before this drive'
