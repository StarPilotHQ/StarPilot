"""Birds-eye projection math and scene building, without a GL context."""
import ctypes
import math
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.ui.onroad import birds_eye_math as bem

RECT = SimpleNamespace(x=100.0, y=50.0, width=520.0, height=650.0)


def test_the_ground_at_x_min_is_the_bottom_edge_and_at_x_max_the_top_edge():
  cfg = bem.BirdsEyeConfig()
  pts = bem.car_to_widget(np.array([[cfg.x_min, 0.0], [cfg.x_max, 0.0]]), RECT, cfg)
  assert pts[0, 0] == pytest.approx(RECT.x + RECT.width / 2) == pytest.approx(pts[1, 0])
  assert pts[0, 1] == pytest.approx(RECT.y + RECT.height, abs=1e-3)
  assert pts[1, 1] == pytest.approx(RECT.y, abs=1e-3)


def test_the_camera_is_above_and_behind_the_car_looking_down_the_road():
  cfg = bem.DEFAULT_CONFIG
  stream = SimpleNamespace(x=0.0, y=0.0, width=1280.0, height=720.0)
  view = bem.view_params(stream, cfg)
  x, y, height = view.position
  near, far = math.radians(cfg.pitch + cfg.fov / 2), math.radians(cfg.pitch - cfg.fov / 2)
  assert y == 0.0 and x < cfg.x_min < 0.0 and height > 10.0, 'behind the bottom edge of the view, well above the road'
  assert height == pytest.approx((cfg.x_max - cfg.x_min) / (1 / math.tan(far) - 1 / math.tan(near)))
  assert x == pytest.approx(cfg.x_min - height / math.tan(near))
  assert far > 0, 'the horizon is never in view'
  assert view.focal == pytest.approx(360 / math.tan(math.radians(cfg.fov / 2)))
  # Sideways scale falls with the distance from the camera: the far end is drawn about a quarter as wide.
  near_ppm, far_ppm = bem.scale_at(np.array([cfg.x_min, cfg.x_max]), view)
  assert far_ppm / near_ppm == pytest.approx(math.sin(far) / math.sin(near), rel=1e-6)
  assert 0.2 < far_ppm / near_ppm < 0.3
  assert float(bem.meters_per_pixel(30.0, view)) == pytest.approx(1 / float(bem.scale_at(30.0, view)))
  # scale_at and depth_scale_at are the pixel derivatives of the projection.
  step = 1e-3
  pts = bem.car_to_widget(np.array([[30.0, 0.0], [30.0, step], [30.0 + step, 0.0]]), stream, cfg, view).astype(np.float64)
  wide = bem.car_to_widget(np.array([[30.0, 0.0], [30.0, 1.0], [31.0, 0.0]]), stream, cfg, view).astype(np.float64)
  assert wide[1, 0] - wide[0, 0] == pytest.approx(float(bem.scale_at(30.0, view)), rel=1e-4)
  assert wide[0, 1] - wide[2, 1] == pytest.approx(float(bem.depth_scale_at(30.5, view)), rel=1e-2)
  assert pts[0, 1] == pytest.approx(pts[1, 1]), 'no roll: a sideways step stays on its pixel row'
  # A point above the road is drawn higher on screen than the road under it.
  raised = bem.car_to_widget(np.array([[30.0, 0.0, 0.0], [30.0, 0.0, 1.5]]), stream, cfg, view)
  assert raised[1, 1] < raised[0, 1] and raised[1, 0] == pytest.approx(raised[0, 0])


def test_straight_lines_stay_straight_and_farther_is_higher():
  cfg = bem.BirdsEyeConfig()
  # A straight diagonal on the ground stays collinear on screen (the ground maps through a homography).
  xs = np.linspace(0, 70, 8)
  pts = bem.car_to_widget(np.stack((xs, 0.1 * xs - 3), axis=1), RECT, cfg).astype(np.float64)
  a, b = pts[0], pts[-1]
  cross = (b[0] - a[0]) * (pts[:, 1] - a[1]) - (b[1] - a[1]) * (pts[:, 0] - a[0])
  assert np.abs(cross).max() / np.linalg.norm(b - a) < 0.5
  ys = bem.car_to_widget(np.stack((np.linspace(-6, 90, 50), np.zeros(50)), axis=1), RECT, cfg)[:, 1]
  assert np.all(np.diff(ys) < 0)
  # A lane keeps its side and converges toward the centre with distance.
  lane = bem.car_to_widget(np.array([[0.0, 1.8], [40.0, 1.8], [80.0, 1.8]]), RECT, cfg)[:, 0] - (RECT.x + RECT.width / 2)
  assert lane[0] > lane[1] > lane[2] > 0


def test_right_is_positive_and_scale_follows_the_rect_height():
  cfg = bem.BirdsEyeConfig()
  pts = bem.car_to_widget(np.array([[0.0, -2.0], [0.0, 2.0]]), RECT, cfg)
  assert pts[0, 0] < RECT.x + RECT.width / 2 < pts[1, 0]
  small = bem.view_params(SimpleNamespace(x=0, y=0, width=900, height=510), cfg)
  tall = bem.view_params(SimpleNamespace(x=0, y=0, width=900, height=1020), cfg)
  wide = bem.view_params(SimpleNamespace(x=0, y=0, width=1800, height=1020), cfg)
  assert float(bem.scale_at(20.0, tall)) == pytest.approx(2 * float(bem.scale_at(20.0, small)))
  assert float(bem.scale_at(20.0, wide)) == pytest.approx(float(bem.scale_at(20.0, tall))), 'a wider rect shows more road, not a larger one'
  assert wide.position == tall.position == small.position, 'the camera depends on the config, not on the rect'


def test_points_behind_the_camera_stay_finite():
  cfg = bem.BirdsEyeConfig()
  pts = bem.car_to_widget(np.array([[-500.0, 1.0], [-25.0, 0.0]]), RECT, cfg)
  assert np.isfinite(pts).all()


def test_resample_line_extends_back_and_marks_missing():
  grid = bem.sample_grid()
  ys = bem.resample_line([0, 10, 20], [1, 2, 3], grid)
  assert ys[grid < 0] == pytest.approx(1.0)
  assert np.isnan(ys[grid > 20]).all()
  assert bem.lateral_at(grid, ys, 15.0) == pytest.approx(2.5)
  stopped = bem.resample_line([0, 0, 0], [0, 0, 0], grid)
  assert np.isnan(stopped).all()


def test_sample_grid_is_increasing_and_spans_the_range():
  cfg = bem.BirdsEyeConfig()
  grid = bem.sample_grid(cfg)
  assert grid[0] < cfg.x_min and grid[-1] == cfg.x_max
  assert np.all(np.diff(grid) > 0)
  assert np.diff(grid)[0] < np.diff(grid)[-2], 'coarser far away'


def test_ribbon_shape_and_left_first():
  centre = np.array([[100.0, 500.0], [100.0, 400.0], [100.0, 300.0]])
  poly = bem.ribbon(centre, 5.0)
  assert poly.shape == (6, 2)
  assert np.allclose(poly[:3, 0], 95.0), 'first chain is screen-left for an upward line'
  assert np.allclose(poly[3:, 0], 105.0)
  assert np.allclose(poly[3:, 1], [300, 400, 500])


def test_valid_runs():
  assert bem.valid_runs(np.array([False, True, True, False, True])) == [(1, 3), (4, 5)]
  assert bem.valid_runs(np.array([], dtype=bool)) == []


def test_lead_colour_shifts_to_red_with_ttc():
  assert bem.lead_color(40, 1.0) == bem.NEUTRAL_LEAD
  assert bem.lead_color(40, -5.0) == bem.NEUTRAL_LEAD  # 8 s
  amber = bem.lead_color(30, -10.0)  # 3 s
  assert amber == bem.CAUTION_LEAD
  assert bem.lead_color(10, -10.0) == bem.ALERT_LEAD  # 1 s
  assert bem.lead_color(3.0, 0.0) == bem.ALERT_LEAD, 'very close is red even when not closing'
  mid = bem.lead_color(30, -6.0)  # 5 s, between neutral and amber
  assert mid not in (bem.NEUTRAL_LEAD, bem.CAUTION_LEAD)
  assert bem.CAUTION_LEAD[2] < mid[2] < bem.NEUTRAL_LEAD[2]
  assert bem.time_to_collision(20, 0.0) == math.inf
  assert bem.time_to_collision(20, -4.0) == pytest.approx(5.0)


def test_labels_and_wheel_angle():
  assert bem.distance_label(32.4) == '32 m'
  assert bem.distance_label(10, metric=False) == '11 yd'
  assert bem.ego_wheel_angle(90.0) < 0 < bem.ego_wheel_angle(-90.0), 'left steering turns the glyph wheels left'
  assert abs(bem.ego_wheel_angle(1e4)) == pytest.approx(math.radians(35))
  assert bem.ego_wheel_angle(float('nan')) == 0.0


def test_car_outline_is_convex_and_rotates():
  hull = bem.car_outline(10.0, 0.0, 0.0)
  assert hull[:, 0].max() == pytest.approx(12.3) and hull[:, 1].max() == pytest.approx(0.95)
  turned = bem.car_outline(10.0, 0.0, math.pi / 2)
  assert turned[:, 1].max() == pytest.approx(2.3)


def test_smooth_blends_and_accepts_new_shapes():
  a = np.array([0.0, 0.0, np.nan])
  b = np.array([1.0, 2.0, 3.0])
  out = bem.smooth(a, b, 0.5)
  assert out == pytest.approx([0.5, 1.0, 3.0])
  assert bem.smooth(None, b, 0.5) == pytest.approx(b)
  assert bem.smooth(np.zeros(2), b, 0.5) == pytest.approx(b)


def _xyz(xs, ys):
  return SimpleNamespace(x=list(xs), y=list(ys), z=[0.0] * len(xs))


def _model(lead_prob=0.0, path_y=0.0):
  xs = np.linspace(0, 120, 33)
  return SimpleNamespace(position=_xyz(xs, np.full(33, path_y)), acceleration=SimpleNamespace(x=[0.0] * 33),
                         laneLines=[_xyz(xs, np.full(33, off)) for off in (-5.4, -1.8, 1.8, 5.4)], laneLineProbs=[.5, .9, .9, .5],
                         roadEdges=[_xyz(xs, np.full(33, off)) for off in (-7, 7)], roadEdgeStds=[.2, .3],
                         leadsV3=[SimpleNamespace(prob=lead_prob, x=[31.5], y=[0.2], v=[20.0])])


def test_scene_keeps_last_good_frame_and_merges_leads():
  # The renderer module imports raylib but needs no window for ingest().
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer, scene_leads
  radar = SimpleNamespace(leadOne=SimpleNamespace(present=True, dRel=30.0, yRel=-0.3, vRel=-2.0),
                          leadTwo=SimpleNamespace(present=False, dRel=0.0, yRel=0.0, vRel=0.0))
  leads = scene_leads(radar, _model(lead_prob=0.9))
  assert len(leads) == 1 and leads[0].radar and leads[0].y == pytest.approx(0.3), 'radar yRel is left-positive'
  leads = scene_leads(None, _model(lead_prob=0.9))
  assert len(leads) == 1 and not leads[0].radar and leads[0].d_rel == pytest.approx(30.0)
  assert scene_leads(None, _model(lead_prob=0.2)) == []

  view = BirdsEyeRenderer(text=object())
  view.ingest(_model(), radar, SimpleNamespace(steeringAngleDeg=5.0, leftBlinker=True, rightBlinker=False), engaged=True)
  assert view.scene.valid and view.scene.engaged and view.scene.left_blinker
  before = view.scene.lanes[1].copy()
  view.ingest(None, None, None)
  assert np.array_equal(view.scene.lanes[1], before, equal_nan=True), 'no model keeps the last good road'
  view.ingest(_model(path_y=1.0), radar, None)
  path = view.scene.path[np.isfinite(view.scene.path)]
  assert 0.0 < path[0] < 1.0, 'a new frame is blended in, not jumped to'


class _SM(dict):
  def __init__(self, services, frame, updated):
    super().__init__(services)
    self.valid = dict.fromkeys(services, True)
    self.recv_frame = dict.fromkeys(services, frame)
    self.updated = dict.fromkeys(services, updated)


def test_poll_accepts_only_fresh_model():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  radar = SimpleNamespace(leadOne=SimpleNamespace(present=False, dRel=0, yRel=0, vRel=0), leadTwo=SimpleNamespace(present=False, dRel=0, yRel=0, vRel=0))
  services = {'modelV2': _model(), 'radarState': radar, 'carState': SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False),
              'selfdriveState': SimpleNamespace(enabled=True, experimentalMode=False), 'longitudinalPlan': SimpleNamespace(allowThrottle=False)}
  view = BirdsEyeRenderer(text=object())
  view.poll(_SM(services, frame=5, updated=True), started_frame=10)
  assert not view.scene.valid, 'data from before this drive is ignored'
  view.poll(_SM(services, frame=12, updated=True), started_frame=10)
  assert view.scene.valid and view.scene.engaged and not view.scene.allow_throttle


def test_ribbon_sides_matches_ribbon_on_each_run():
  rng = np.random.default_rng(0)
  centre = np.cumsum(rng.normal(size=(12, 2)), axis=0) * 10
  half = rng.uniform(1, 4, size=12)
  valid = np.array([True] * 5 + [False] + [True] * 2 + [False] + [True] * 3)
  left, right = bem.ribbon_sides(centre, half, valid)
  for start, end in bem.valid_runs(valid):
    poly = bem.ribbon(centre[start:end], half[start:end])
    n = end - start
    assert left[start:end] == pytest.approx(poly[:n], abs=1e-3)
    assert right[start:end] == pytest.approx(poly[n:][::-1], abs=1e-3)
  stacked_left, _ = bem.ribbon_sides(np.stack((centre, centre)), np.stack((half, half)), np.stack((valid, valid)))
  assert stacked_left[1] == pytest.approx(left), 'leading axes are independent ribbons'


def test_hls_to_rgb_matches_colorsys():
  import colorsys
  rng = np.random.default_rng(1)
  hue, light, sat = rng.uniform(0, 1, 50), rng.uniform(0, 1, 50), np.concatenate((rng.uniform(0, 1, 49), [0.0]))
  expected = np.array([colorsys.hls_to_rgb(*v) for v in zip(hue, light, sat, strict=True)])
  assert bem.hls_to_rgb(hue, light, sat) == pytest.approx(expected)


def _renderer_with_scene():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  view = BirdsEyeRenderer(text=SimpleNamespace(measure=lambda text, size: (10.0, 10.0), draw=lambda *args: None))
  radar = SimpleNamespace(leadOne=SimpleNamespace(present=True, dRel=30.0, yRel=0.0, vRel=-2.0),
                          leadTwo=SimpleNamespace(present=False, dRel=0.0, yRel=0.0, vRel=0.0))
  view.ingest(_model(), radar, SimpleNamespace(steeringAngleDeg=5.0, leftBlinker=False, rightBlinker=False), engaged=True)
  return view, radar


def _layers(view, rect=RECT):
  """(road, path) draws for the current scene, as draw() would fetch them."""
  reference = view._reference(rect.height, view.config.x_max)
  return view._road_draw(view.scene, reference), view._path_draw(view.scene, reference)


def _op_points(op) -> np.ndarray:
  """The (n, 2) points a DrawTriangleStrip/Fan op passes to C."""
  pointer = op[1]  # a ctypes view of the point array, or an address inside a shared one
  address = pointer if isinstance(pointer, int) else ctypes.addressof(pointer)
  return np.ctypeslib.as_array((ctypes.c_float * (2 * op[2])).from_address(address)).reshape(-1, 2).copy()


def _draw_ops(ops):
  """The C draw ops of an op list (without the trailing buffer keep-alive entry)."""
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  return [op for op in ops if op[0] is not ber._keep]


def test_scene_geometry_is_cached_until_an_input_changes():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  view, radar = _renderer_with_scene()
  (road, path) = first = _layers(view)
  assert road.road and road.lines and path.strips, 'road, lines and path all produce ops'
  lines = _draw_ops(road.lines)
  assert all(op[0] is ber._raw.DrawTriangleStrip for op in lines) and len(lines) == 2 + 4, 'one strip per edge and lane line'
  assert len(_draw_ops(road.road)) == 1, 'the road fill is one strip'
  assert not hasattr(view, '_overlay') and not hasattr(bem, 'overlay_bands'), 'no gradient is laid over the road'
  assert _layers(view)[0] is first[0] and _layers(view)[1] is first[1], 'an unchanged scene replays the cached ops'
  view.ingest(None, radar, None)
  assert _layers(view)[0] is first[0], 'leads and car state alone keep the road'
  moved = SimpleNamespace(x=40.0, y=10.0, width=900.0, height=RECT.height)
  assert _layers(view, moved)[0] is first[0], 'the ops are in meters: where the rect is and how wide does not matter'
  for x_max in (73.0, 78.5, 80.0):
    assert view._reference(RECT.height, x_max).key == view._reference(RECT.height, 80.0).key, 'a gliding zoom keeps the road'
  assert view._reference(RECT.height, 80.5).key != view._reference(RECT.height, 80.0).key, 'a zoom step of the width reference rebuilds'
  view.opacity = 0.5
  assert _layers(view)[0] is not first[0], 'opacity is part of the key'
  again = _layers(view)[0]
  assert _layers(view, SimpleNamespace(x=0.0, y=0.0, width=300.0, height=400.0))[0] is not again, 'so is the height (minimum widths)'
  again = _layers(view)
  view.ingest(_model(path_y=0.5), radar, None)
  assert _layers(view)[0] is not again[0] and _layers(view)[1] is not again[1], 'a new model frame rebuilds'
  again = _layers(view)[0]
  view.road_style = {'laneLines': '#FF8800C0'}
  assert _layers(view)[0] is not again, 'so does a lane color setting'


def test_one_strip_per_ribbon_run_and_per_vertex_only_for_row_colour_modes():
  view, _ = _renderer_with_scene()
  lane = view.scene.lanes[0].copy()
  lane[(view.scene.grid > 20) & (view.scene.grid < 30)] = np.nan  # a gap splits the line
  view.scene.lanes = [lane, *view.scene.lanes[1:]]
  road, path = _layers(view)
  assert len(_draw_ops(road.lines)) == 2 + 4 + 1, 'the line with a gap is two strips'
  assert path.strips and not path.quads, 'a single-colour path is strips'
  assert len(_draw_ops(path.strips)) <= 30, 'the stepped fade stays a few dozen calls'
  view.ingest(None, None, None, experimental=True)
  view.scene.path_accel = np.full(view.scene.grid.size, 1.0)
  _, path = _layers(view)
  assert path.quads and not path.strips, 'the experimental accel tint keeps per-vertex colours'
  assert len(path.quads) % 6 == 0


def _fan_colors(ops):
  return [(op[3].r, op[3].g, op[3].b, op[3].a) for op in _draw_ops(ops)]


def _drawn_faces(ops, car, mesh) -> set:
  """The mesh face indices one car's op list draws, read back from each fan's place in the shared point buffer."""
  base = ops[-1][1].ctypes.data
  return {mesh.starts.index((op[1] - base) // 8 - car * mesh.index.size) for op in _draw_ops(ops)}


def test_car_bodies_draw_only_the_faces_that_look_toward_the_camera():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  stream = SimpleNamespace(x=0.0, y=0.0, width=1280.0, height=720.0)
  view = bem.view_params(stream, bem.DEFAULT_CONFIG)
  mesh = ber._car_mesh(ber.LEAD_LENGTH, ber.LEAD_WIDTH)
  assert mesh.vertices[:, 0].min() == pytest.approx(-2.31) and mesh.vertices[:, 0].max() == pytest.approx(2.3), '4.6 m plus the lights'
  assert np.ptp(mesh.vertices[:, 1]) == pytest.approx(1.9) and mesh.vertices[:, 2].max() == ber.ROOF_HEIGHT
  assert np.allclose(np.linalg.norm(mesh.normals, axis=1), 1.0)
  up = mesh.normals[:, 2] > 0.99
  assert up.sum() == 2 and [mesh.parts[i] for i in np.flatnonzero(up)] == [ber.BODY, ber.BODY], 'the deck and the roof face up'
  body, glass, light = ((200, 100, 50), 255), ((10, 20, 30), 255), ((250, 0, 0), 255)
  cars, screens = ber.BirdsEyeRenderer._cars(view, [(40.0, 0.0, 0.0), (40.0, -9.0, 0.0), (40.0, 9.0, 0.0)], mesh, [(body, glass, light)] * 3)
  assert screens.shape == (3, mesh.vertices.shape[0], 2)
  ahead, left, right = (_draw_ops(ops) for ops in cars)
  assert all(op[0] is ber._raw.DrawTriangleFan for op in ahead)
  assert len(ahead) < len(mesh.parts), 'the front of a car ahead is not drawn'
  assert _fan_colors(ahead).count((250, 0, 0, 255)) == 2, 'both tail lights, unshaded'
  left_wall, right_wall, front_wall = 1, 5, 7  # hull edges: the two flanks and the nose
  faces = [_drawn_faces(ops, car, mesh) for car, ops in enumerate(cars)]
  assert not faces[0] & {left_wall, right_wall, front_wall}, 'dead ahead: neither flank, and never the nose'
  assert right_wall in faces[1] and left_wall not in faces[1], 'a car to the left shows its right flank'
  assert left_wall in faces[2] and right_wall not in faces[2], 'and one to the right its left flank'
  assert len(left) == len(right)
  shades = {color for color in _fan_colors(ahead) if color[2] != 0 and color[0] > color[1] > color[2] and color[0] > 100}
  assert len(shades) >= 2, 'faces are shaded by their normals: the roof is brighter than the rear'
  # Hidden parts: a palette alpha of zero drops that part's faces.
  cars, _ = ber.BirdsEyeRenderer._cars(view, [(40.0, 0.0, 0.0)], mesh, [(body, (glass[0], 0), light)])
  assert len(_draw_ops(cars[0])) < len(ahead)


def test_the_ego_tail_lights_follow_braking(monkeypatch):
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  view, _ = _renderer_with_scene()
  drawn = []
  monkeypatch.setattr(ber, '_run', lambda ops: drawn.append(_fan_colors(ops)))
  camera = bem.view_params(RECT, view.config)
  view._ego(view.scene, camera)
  assert (*ber.EGO_LIGHT, 255) in drawn[-1] and (*ber.EGO_BRAKE, 255) not in drawn[-1]
  car = SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False, vEgo=20.0, aEgo=-0.4, brakePressed=True)
  view.ingest(None, None, car)
  view._ego(view.scene, camera)
  assert drawn[-1].count((*ber.EGO_BRAKE, 255)) == 2, 'the brake pedal lights them'
  car.brakePressed, car.aEgo = False, ber.BRAKE_LIGHT_ACCEL - 0.5
  view.ingest(None, None, car)
  assert view.scene.braking, 'so does a firm deceleration without the pedal (openpilot braking)'
  car.aEgo = 0.0
  view.ingest(None, None, car)
  assert not view.scene.braking


def _text_recorder():
  drawn = []
  return SimpleNamespace(measure=lambda text, size: (0.5 * size * len(text), size), draw=lambda text, size, *a: drawn.append((text, size))), drawn


def test_label_font_is_about_twice_the_first_version():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  view = BirdsEyeRenderer(text=object())
  stream = SimpleNamespace(x=0.0, y=0.0, width=1280.0, height=720.0)
  old = max(15.0, min(26.0, stream.height * 0.026))
  assert view._font_size(stream) == pytest.approx(2 * old, rel=0.05)
  assert view._font_size(SimpleNamespace(height=200.0)) >= 30.0
  assert view._font_size(SimpleNamespace(height=4000.0)) <= 54.0
  assert view._font_size(stream) == bem.label_font_size(stream.height)


def test_ego_lane_lines_follow_the_lateral_state():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  from openpilot.starpilot.lateral.lane_feedback import BLUE
  assert ber.LANE_CENTERING_LANE == BLUE
  view, radar = _renderer_with_scene()

  def colors():
    lanes = view._line_styles(view.scene)[len(view.scene.edges):]
    return [line[3] for line in lanes]

  idle = colors()
  assert ber.LAT_ACTIVE_LANE not in idle and BLUE not in idle
  first = _layers(view)[0]
  view.ingest(None, radar, None, lat_active=True)
  assert colors()[1:3] == [ber.LAT_ACTIVE_LANE] * 2, 'the ego lane own lines turn green'
  assert colors()[0] == idle[0] and colors()[3] == idle[3], 'the outer lines keep their color'
  green = _layers(view)[0]
  assert green is not first, 'the lateral state is part of the scene cache key'
  assert _layers(view)[0] is green, 'and an unchanged state still replays the cache'
  view.ingest(None, radar, None, lane_centering_direction=-1)
  assert colors()[1:3] == [BLUE, ber.LAT_ACTIVE_LANE], 'only the left line, the side being corrected toward, is blue'
  left = _layers(view)[0]
  assert left is not green, 'the direction is part of the scene cache key'
  view.ingest(None, radar, None, lane_centering_direction=1)
  assert colors()[1:3] == [ber.LAT_ACTIVE_LANE, BLUE], 'then only the right line'
  assert _layers(view)[0] is not left
  view.ingest(None, radar, None, lat_active=False)
  assert colors() == idle, 'centering without lateral control is not shown'


def _lateral_sm(frame, lat_active, applied, now_ns, fresh_ns=None):
  services = {'modelV2': _model(), 'carControl': SimpleNamespace(latActive=lat_active),
              'starpilotLateralState': SimpleNamespace(laneCentering=SimpleNamespace(
                version=1, lateralActive=lat_active, requestedCorrection=applied, appliedCorrection=applied,
                modelMonoTime=now_ns - 1000, carControlMonoTime=now_ns - 1000, reason='ok'))}
  sm = _SM(services, frame=frame, updated=True)
  sm.alive = dict.fromkeys(services, True)
  sm.logMonoTime = dict.fromkeys(services, now_ns - 1000 if fresh_ns is None else fresh_ns)
  return sm


def test_poll_reads_lateral_active_and_lane_centering():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  now = 10_000_000_000
  view = BirdsEyeRenderer(text=object())
  view.clock_ns = lambda: now
  view.poll(_lateral_sm(12, True, 0.0, now), started_frame=10)
  assert view.scene.lat_active and view.scene.lane_centering_direction == 0, 'active with no applied correction is green'
  view.poll(_lateral_sm(12, True, 0.001, now), started_frame=10)
  assert view.scene.lat_active and view.scene.lane_centering_direction == 1
  view.poll(_lateral_sm(12, True, -0.001, now), started_frame=10)
  assert view.scene.lat_active and view.scene.lane_centering_direction == -1, 'the sign is carried, not just on/off'
  view.poll(_lateral_sm(12, True, 0.001, now, fresh_ns=now - 1_000_000_000), started_frame=10)
  assert view.scene.lat_active and view.scene.lane_centering_direction == 0, 'a stale lane-centering message is not blue'
  view.poll(_lateral_sm(12, False, 0.0, now), started_frame=10)
  assert not view.scene.lat_active and view.scene.lane_centering_direction == 0
  view.poll(_SM({'carControl': SimpleNamespace(latActive=True)}, frame=12, updated=True), started_frame=10)
  assert view.scene.lat_active and view.scene.lane_centering_direction == 0, 'a SubMaster without the fork service is green only'


def test_distance_rungs_are_gone():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  assert not hasattr(BirdsEyeRenderer, '_ticks')


def test_cars_are_true_to_scale_and_the_drawn_gap_is_the_printed_gap():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  cfg = bem.DEFAULT_CONFIG
  stream = SimpleNamespace(x=0.0, y=0.0, width=1280.0, height=720.0)
  view = bem.view_params(stream, cfg)
  front = ber.EGO_FRONT
  # The printed number is radar dRel unchanged.
  radar = SimpleNamespace(leadOne=SimpleNamespace(present=True, dRel=30.0, yRel=0.0, vRel=0.0),
                          leadTwo=SimpleNamespace(present=False, dRel=0.0, yRel=0.0, vRel=0.0))
  lead = ber.scene_leads(radar, None)[0]
  assert lead.d_rel == 30.0
  assert bem.distance_label(lead.d_rel) == '30 m' and bem.distance_label(lead.d_rel, metric=False) == '33 yd'
  assert bem.distance_label(91.44, metric=False) == '100 yd'
  # Lead and ego bodies: 4.6 x 1.9 m on the road, the lead's rear bumper at dRel ahead of the ego's front bumper.
  assert (ber.EGO_LENGTH, ber.EGO_WIDTH, ber.LEAD_LENGTH, ber.LEAD_WIDTH) == (4.6, 1.9, 4.6, 1.9)
  renderer = ber.BirdsEyeRenderer(text=object())
  renderer.scene.leads = [lead]
  (_, pose), = renderer._lead_poses(renderer.scene, view)
  mesh = ber._car_mesh(ber.LEAD_LENGTH, ber.LEAD_WIDTH)
  hull = mesh.vertices[:8]
  assert pose[0] + hull[:, 0].min() == pytest.approx(front + 30.0), 'the body starts exactly the printed gap ahead'
  _, screens = renderer._cars(view, [pose], mesh, [(((1, 1, 1), 1),) * 3])
  expected = bem.car_to_widget(mesh.vertices + (pose[0], pose[1], 0.0), stream, cfg, view)
  assert screens[0] == pytest.approx(expected, abs=1e-3), 'the body is the mesh at its pose, through the camera'
  lane = bem.car_to_widget(np.array([[pose[0], -1.85], [pose[0], 1.85]]), stream, cfg, view)[:, 0]
  body = bem.car_to_widget(np.array([[pose[0], -0.95], [pose[0], 0.95]]), stream, cfg, view)[:, 0]
  assert np.ptp(lane) / np.ptp(body) == pytest.approx(3.7 / 1.9, rel=1e-3), 'a lane is ~2 car widths'
  # A 60 m lead's whole body and the whole ego fit on the stream frame.
  far = bem.car_to_widget(np.array([[60.0 + front + ber.LEAD_LENGTH, 0.0, ber.ROOF_HEIGHT], [front - ber.EGO_LENGTH, 0.0, 0.0]]), stream, cfg, view)
  assert far[0, 1] > 0 and far[1, 1] < stream.height


def test_lead_heading_follows_the_lanes_not_the_planned_path():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  xs = np.linspace(0, 120, 33)
  model = _model()
  model.position = _xyz(xs, 0.004 * xs ** 2)  # the plan curves right (steering), the road is straight
  view = BirdsEyeRenderer(text=object())
  view.ingest(model, None, None)
  assert view._heading_at(view.scene, 30.0) > 0.1, 'the path heading bends with the plan'
  assert view._road_heading(view.scene, 30.0) == pytest.approx(0.0, abs=1e-9), 'the lead follows the road'
  bent = _model()
  bent.laneLines = [_xyz(xs, 0.1 * xs + off) for off in (-5.4, -1.8, 1.8, 5.4)]
  bent.roadEdges = [_xyz(xs, np.full(33, off)) for off in (-7, 7)]
  view = BirdsEyeRenderer(text=object())
  view.ingest(bent, None, None)
  assert view._road_heading(view.scene, 30.0) == pytest.approx(math.atan(0.1))
  profiles = view._road_profiles(view.scene)
  assert view._road_profiles(view.scene) is profiles, 'cached per model frame'
  # Unconfident ego lane lines fall back to the road edges, then to the path.
  bent.laneLineProbs = [0.9, 0.1, 0.1, 0.9]
  view = BirdsEyeRenderer(text=object())
  view.ingest(bent, None, None)
  assert view._road_heading(view.scene, 30.0) == pytest.approx(0.0, abs=1e-9), 'edges are straight here'
  bent.roadEdges = []
  model.laneLineProbs, model.roadEdges, model.position = [0.0] * 4, [], _xyz(xs, 0.1 * xs)
  view = BirdsEyeRenderer(text=object())
  view.ingest(model, None, None)
  assert view._road_heading(view.scene, 30.0) == pytest.approx(math.atan(0.1), abs=1e-6), 'path only without lanes/edges'


def test_zoom_follows_speed_and_keeps_the_view_anchored():
  cfg = bem.DEFAULT_CONFIG
  assert bem.zoom_x_max(0.0) == bem.zoom_x_max(10.0) == bem.ZOOM_NEAR_X_MAX
  assert bem.zoom_x_max(30.0) == bem.zoom_x_max(40.0) == cfg.x_max
  assert bem.zoom_x_max(20.0) == pytest.approx((bem.ZOOM_NEAR_X_MAX + cfg.x_max) / 2)
  assert bem.zoomed_config(cfg, cfg.x_max) is cfg
  stream = SimpleNamespace(x=0.0, y=0.0, width=1280.0, height=720.0)
  near = bem.zoomed_config(cfg, bem.ZOOM_NEAR_X_MAX)
  assert (near.x_min, near.pitch, near.fov) == (cfg.x_min, cfg.pitch, cfg.fov)
  far_view, near_view = bem.view_params(stream, cfg), bem.view_params(stream, near)
  assert near_view.position[2] < far_view.position[2] and near_view.position[0] > far_view.position[0], 'zooming in lowers the camera and brings it closer'
  assert float(bem.scale_at(0.0, near_view)) > 1.5 * float(bem.scale_at(0.0, far_view)), 'and the car is drawn larger'
  ego = np.array([[cfg.x_min, 0.0]])
  assert bem.car_to_widget(ego, stream, near)[0] == pytest.approx(bem.car_to_widget(ego, stream, cfg)[0], abs=1e-3), 'the view is anchored at x_min'
  top = bem.car_to_widget(np.array([[bem.ZOOM_NEAR_X_MAX, 0.0]]), stream, near)[0]
  assert top[1] == pytest.approx(0.0, abs=1e-3), 'x_max is the top edge at every zoom'


def test_zoom_filter_is_smooth_and_quantized():
  zoom = bem.ZoomFilter()
  assert zoom.update(None, 0.0) == bem.DEFAULT_CONFIG.x_max, 'no speed yet: the default view'
  assert zoom.update(5.0, 0.0) == bem.ZOOM_NEAR_X_MAX, 'the first speed is taken directly'
  values = [zoom.update(30.0, 1 / 30) for _ in range(30)]
  assert values[0] < bem.ZOOM_NEAR_X_MAX + 2.0, 'a speed step does not jump the view'
  assert 40.0 < values[-1] <= bem.ZOOM_NEAR_X_MAX + bem.ZOOM_MAX_RATE, 'a 57 m step glides at the rate limit: ~12 m after 1 s'
  assert all(v % bem.ZOOM_STEP == 0 for v in values) and values == sorted(values)
  # A real acceleration (1.5 m/s^2 from 10 to 30 m/s) changes the view on a minority of frames.
  zoom = bem.ZoomFilter()
  ramp = [zoom.update(min(30.0, 10.0 + 1.5 * i / 30), 1 / 30) for i in range(600)]
  changes = sum(a != b for a, b in zip(ramp, ramp[1:], strict=False))
  assert ramp[-1] == bem.DEFAULT_CONFIG.x_max and changes < 0.3 * len(ramp)
  for i in range(300):
    zoom.update(29.8 if i % 2 else 30.2, 1 / 30)
  assert zoom.x_max == bem.DEFAULT_CONFIG.x_max, 'speed noise under a step does not move the view'


def test_zoom_rate_is_limited():
  cfg = bem.DEFAULT_CONFIG
  zoom = bem.ZoomFilter()
  assert zoom.update(30.0, 0.0) == cfg.x_max
  dt = 1 / 30
  previous, values = cfg.x_max, []
  for _ in range(300):
    values.append(zoom.update(30.0, dt, 200.0))
    assert values[-1] - previous <= bem.ZOOM_MAX_RATE * dt + bem.ZOOM_STEP + 1e-9, 'x_max never grows faster than the limit'
    previous = values[-1]
  assert values[-1] == bem.ZOOM_FAR_X_MAX
  first_at_cap = next(i for i, v in enumerate(values) if v == bem.ZOOM_FAR_X_MAX)
  assert first_at_cap * dt >= (bem.ZOOM_FAR_X_MAX - cfg.x_max) / bem.ZOOM_MAX_RATE - dt, 'the glide takes its time'
  start = zoom.value
  for _ in range(60):
    values.append(zoom.update(30.0, dt, None))
    assert previous - values[-1] <= bem.ZOOM_MAX_RATE * dt + bem.ZOOM_STEP + 1e-9, 'x_max never drops faster than the limit'
    previous = values[-1]
  assert start - zoom.value <= bem.ZOOM_MAX_RATE * 2 + 0.01, 'tightening is limited the same way'


def test_zoom_widens_to_keep_a_radar_lead_on_screen():
  cfg = bem.DEFAULT_CONFIG
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import EGO_FRONT, LEAD_LENGTH
  def front(d_rel):
    return d_rel + EGO_FRONT + LEAD_LENGTH
  assert bem.lead_zoom_x_max(30.0, None) == cfg.x_max == 102.0, 'no lead: the speed preset'
  assert bem.lead_zoom_x_max(30.0, front(20.0)) == cfg.x_max, 'a near lead never tightens the preset'
  assert bem.lead_zoom_x_max(30.0, front(65.0)) == cfg.x_max, 'a lead that fits keeps the preset'
  assert bem.lead_zoom_x_max(30.0, front(100.0)) == pytest.approx(100.0 + EGO_FRONT + LEAD_LENGTH + bem.ZOOM_LEAD_BUFFER)
  assert bem.lead_zoom_x_max(30.0, front(200.0)) == bem.ZOOM_FAR_X_MAX == bem.far_x_max(cfg), 'capped at the widest zoom'
  assert bem.lead_zoom_x_max(5.0, front(10.0)) == bem.ZOOM_NEAR_X_MAX, 'never tighter than the city view'
  assert bem.lead_zoom_x_max(5.0, front(40.0)) == pytest.approx(front(40.0) + bem.ZOOM_LEAD_BUFFER), 'widens the city view too'
  zoom = bem.ZoomFilter()
  assert zoom.update(30.0, 0.0, front(100.0)) == 112.0, 'quantized to ZOOM_STEP (112.1 m)'
  assert zoom.value == pytest.approx(112.1), 'the camera follows the unquantized value'
  assert zoom.update(30.0, 0.0, None) == 112.0, 'the change is filtered, not a jump'
  for _ in range(300):
    zoom.update(30.0, 1 / 30, None)
  assert zoom.x_max == cfg.x_max, 'the lead gone: back to the preset'
  zoom.update(30.0, 0.0, front(200.0))
  for _ in range(300):
    zoom.update(30.0, 1 / 30, front(200.0))
  assert zoom.x_max == bem.ZOOM_FAR_X_MAX, 'x_max may pass cfg.x_max, up to ZOOM_FAR_X_MAX'


def test_renderer_zoom_follows_the_radar_lead_one():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  view = BirdsEyeRenderer(text=object())
  view.clock = lambda: 0.0
  view.ingest(None, None, SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False, vEgo=30.0))
  view.scene.lead_one_d = 20.0
  assert view.view_config().x_max == view.config.x_max, 'a near lead keeps the preset'
  view = BirdsEyeRenderer(text=object())
  view.clock = lambda: 0.0
  view.ingest(None, None, SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False, vEgo=30.0))
  view.scene.lead_one_d = 100.0
  wide = view.view_config()
  assert wide.x_max == 112.0 and (wide.x_min, wide.pitch, wide.fov) == (view.config.x_min, view.config.pitch, view.config.fov)
  view = BirdsEyeRenderer(text=object())
  view.clock = lambda: 0.0
  view.ingest(None, None, SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False, vEgo=30.0))
  assert view.view_config().x_max == view.config.x_max, 'no lead: the preset'


def test_the_camera_glides_with_the_zoom_and_is_reused_while_it_rests():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  view = BirdsEyeRenderer(text=object())
  now = [0.0]
  view.clock = lambda: now[0]
  assert view.view_config() == view.config, 'no carState yet'
  view.ingest(None, None, SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False, vEgo=3.0))
  first = view.view_config()
  assert first.x_max == bem.ZOOM_NEAR_X_MAX and first.x_min == view.config.x_min
  camera, reference = view._views(RECT, first)
  assert camera.x_max == bem.ZOOM_NEAR_X_MAX
  now[0] += 1 / 30
  assert view.view_config() == first
  assert view._views(RECT, first)[0] is camera, 'a resting zoom reuses the camera'
  assert view.scene.grid[-1] == bem.ZOOM_FAR_X_MAX > view.config.x_max, 'the scene grid spans the widest zoom'
  # Accelerating: the camera moves every frame, in steps far finer than ZOOM_STEP, on one width reference.
  view.ingest(None, None, SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False, vEgo=30.0))
  seen = []
  for _ in range(5):
    now[0] += 1 / 30
    seen.append(view._views(RECT, view.view_config()))
  steps = np.diff([c.x_max for c, _ in seen])
  assert (steps > 0).all() and steps.max() < bem.ZOOM_STEP
  assert len({r.key for _, r in seen}) == 1 and seen[0][1].key == reference.key


def test_far_highway_lead_is_drawn_once_the_zoom_settles(monkeypatch):
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  view = ber.BirdsEyeRenderer(text=SimpleNamespace(measure=lambda text, size: (10.0, 10.0), draw=lambda *args: None))
  now = [0.0]
  view.clock = lambda: now[0]
  radar = SimpleNamespace(leadOne=SimpleNamespace(present=True, dRel=105.0, yRel=0.0, vRel=0.0),
                          leadTwo=SimpleNamespace(present=False, dRel=0.0, yRel=0.0, vRel=0.0))
  view.ingest(_model(), None, SimpleNamespace(steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False, vEgo=30.0))
  assert view.view_config().x_max == view.config.x_max, 'no lead yet: the highway preset'
  view.ingest(None, radar, None)  # a lead appears 105 m ahead
  poses = []
  monkeypatch.setattr(view, '_cars', lambda v, p, *a: poses.extend(p) or ([[]] * len(p), np.zeros((len(p), 32, 2))))
  monkeypatch.setattr(ber, '_run', lambda *a: None)
  monkeypatch.setattr(ber.rl, 'draw_rectangle_rounded', lambda *a: None)
  monkeypatch.setattr(ber.rl, 'draw_rectangle_rounded_lines_ex', lambda *a: None)

  def draw_leads():
    cfg = view.view_config()
    view._leads(view.scene, RECT, view._views(RECT, cfg)[0])
    return cfg
  assert draw_leads().x_max < 104.0
  assert not poses, 'beyond the preset view until the zoom catches up'
  for _ in range(300):  # 10 s at 30 Hz
    now[0] += 1 / 30
    cfg = draw_leads()
  assert cfg.x_max == 117.0, '105 + 1.5 + 4.6 + 6 = 117.1, quantized'
  assert poses and poses[-1][0] == pytest.approx(105.0 + ber.EGO_FRONT + ber.LEAD_LENGTH / 2)
  assert view.scene.grid[-1] >= cfg.x_max, 'the grid reaches the top edge'
  road = view._road_draw(view.scene, view._reference(RECT.height, cfg.x_max))
  reach = min(cfg.x_max, view.scene.draw_distance)  # the camera's MAX_DRAW_DISTANCE (100 m) is below this top edge
  assert max(_op_points(op)[:, 0].max() for op in _draw_ops(road.lines)) >= reach - 0.5, 'the lines are not cut at the preset'


def test_lines_stop_at_the_camera_draw_distance_and_the_path_at_the_camera_lead_cut():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  model = _model()
  model.position = _xyz(np.linspace(0, 40, 33), np.zeros(33))  # a slow plan: the camera draws 40 m
  assert ber.camera_draw_distance(model) == 40.0
  model.position = _xyz(np.linspace(0, 3, 33), np.zeros(33))
  assert ber.camera_draw_distance(model) == ber.MIN_DRAW_DISTANCE
  assert ber.camera_path_end(None, 80.0) == 80.0
  assert ber.camera_path_end(30.0, 80.0) == pytest.approx(50.0), 'lead_d = 60, minus min(21, 10)'
  assert ber.camera_path_end(10.0, 80.0) == pytest.approx(13.0), 'lead_d = 20, minus 7'
  assert ber.camera_path_end(60.0, 80.0) == 80.0
  view, radar = _renderer_with_scene()  # the plan reaches 120 m, a radar lead at 30 m
  assert view.scene.draw_distance == ber.MAX_DRAW_DISTANCE and view.scene.lead_one_d == 30.0
  short = _model()
  short.position = _xyz(np.linspace(0, 40, 33), np.zeros(33))
  view.ingest(short, radar, None)
  assert view.scene.draw_distance == 40.0
  captured = {}
  original = view._build_road

  def spy(scene, reference, reach):
    captured.update(reach=reach)
    return original(scene, reference, reach)
  view._build_road = spy
  road, path = _layers(view)
  assert captured['reach'] <= 40.0 and captured['reach'] > 38.0
  assert path.end is not None and path.end[0] <= 40.0, 'the lead cut (50 m) is clipped to the draw distance'
  for op in _draw_ops(road.road + road.lines):
    assert _op_points(op)[:, 0].max() <= 40.0 + 0.2, 'nothing beyond the draw distance (ops are in meters)'


def test_ego_lane_lines_are_at_least_three_pixels_zoomed_out():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  view, _ = _renderer_with_scene()
  styles = view._line_styles(view.scene)
  edges, lanes = styles[:len(view.scene.edges)], styles[len(view.scene.edges):]
  assert all(line[2] == ber.EDGE_MIN_PX == 3.5 for line in edges)
  assert all(line[2] == ber.LANE_MIN_PX == 3.0 for line in lanes)
  assert not hasattr(view, '_blinkers') and not hasattr(ber, 'TRIANGLE_LAYOUT'), 'the HUD owns the blinker arrow'


def test_radar_lead_lateral_is_radard_yrel_only():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import scene_leads
  radar = SimpleNamespace(leadOne=SimpleNamespace(present=True, dRel=30.0, yRel=0.0, vRel=0.0),
                          leadTwo=SimpleNamespace(present=False, dRel=0.0, yRel=0.0, vRel=0.0))
  model = _model(lead_prob=0.9)
  model.leadsV3[0].y = [2.5]
  assert scene_leads(radar, model)[0].y == 0.0, 'the model never overrides the fused lead'
  radar.leadOne.yRel = -6.0
  assert scene_leads(radar, model)[0].y == 6.0, 'a lead two lanes over is not clamped'


def test_lead_smoothing_follows_a_lane_change():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeLead, BirdsEyeRenderer
  leads = [BirdsEyeLead(30.0, 0.0, 0.0)]
  for _ in range(10):  # 0.5 s at 20 Hz after a 1.2 m step
    leads = BirdsEyeRenderer._smooth_leads(leads, [BirdsEyeLead(30.0, 1.2, 0.0)])
  assert leads[0].y == pytest.approx(1.2, abs=0.01), 'lateral motion is tracked, nothing clamps y'
  jumped = BirdsEyeRenderer._smooth_leads(leads, [BirdsEyeLead(30.0, 4.9, 0.0)])
  assert jumped[0].y == 4.9, 'a whole-lane jump is taken directly, not held back'


def test_lead_body_is_drawn_at_its_own_lateral_position(monkeypatch):
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  view, radar = _renderer_with_scene()
  poses = []
  monkeypatch.setattr(view, '_cars', lambda v, p, *a: poses.extend(p) or ([[]] * len(p), np.zeros((len(p), 32, 2))))
  monkeypatch.setattr(ber, '_run', lambda *a: None)
  monkeypatch.setattr(ber.rl, 'draw_rectangle_rounded', lambda *a: None)
  monkeypatch.setattr(ber.rl, 'draw_rectangle_rounded_lines_ex', lambda *a: None)
  for y_rel in (0.0, -3.0):  # the label filter follows the body; it never moves the body
    radar.leadOne.yRel = y_rel
    view.scene.leads = ber.scene_leads(radar, None)
    view._leads(view.scene, RECT, bem.view_params(RECT, view.config))
  assert [p[1] for p in poses] == [0.0, 3.0]


def test_label_position_is_filtered_and_whole_pixels():
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer
  view = BirdsEyeRenderer(text=object())
  x, y = view._label_position([], 100.4, 200.6)
  assert (x, y) == (100.0, 201.0)
  prev, view._labels = view._labels, []
  x, y = view._label_position(prev, 110.4, 200.6)
  assert (x, y) == (105.0, 201.0), 'half way toward the glyph'
  prev, view._labels = view._labels, []
  assert view._label_position(prev, 400.0, 200.0) == (400.0, 200.0), 'a side flip snaps'


def test_a_turned_car_body_is_rigid_on_the_road():
  # 2026-11-09: on a curve the flat lead glyph was drawn as a parallelogram, because the old view was wider sideways
  # than forward. The body is now turned in meters on the road and only then goes through the camera.
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  stream = SimpleNamespace(x=0.0, y=0.0, width=1280.0, height=720.0)
  view = bem.view_params(stream, bem.DEFAULT_CONFIG)
  mesh = ber._car_mesh(ber.LEAD_LENGTH, ber.LEAD_WIDTH)
  heading = math.radians(20.0)
  palette = [(((1, 1, 1), 1),) * 3]
  turned = ber.BirdsEyeRenderer._cars(view, [(40.0, 1.0, heading)], mesh, palette)[1][0]
  world = mesh.vertices.copy()
  world[:, :2] = bem.rotate_offsets(mesh.vertices[:, :2], 40.0, 1.0, heading)
  assert turned == pytest.approx(bem.car_to_widget(world, stream, view=view), abs=1e-3)
  sides = lambda quad: [float(np.linalg.norm(quad[i] - quad[(i + 1) % 4])) for i in range(4)]  # noqa: E731
  roof = [20, 21, 22, 23]
  assert sides(world[roof]) == pytest.approx(sides(mesh.vertices[roof])), 'same size as when it points straight ahead'
  nose = world[[0, 7], :2].mean(axis=0) - world[:8, :2].mean(axis=0)
  assert nose == pytest.approx(2.3 * np.array([math.cos(heading), math.sin(heading)])), 'its nose points along the heading'
