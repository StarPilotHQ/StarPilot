"""Map overlay geometry (CPU) and rendering (STARPILOT_AUTO_GL_TEST=1 for a real GL context)."""

import math
import os
import time
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from openpilot.starpilot.navigation.offline_roads import TileStore
from openpilot.starpilot.navigation.road_tiles import (
  DATA_ZOOM, EXTENT, MAJOR, MINOR, MOTORWAY, STREET, TileKey, decode_road_tile, encode_road_tile, lat_lon, world_xy,
)
from openpilot.starpilot.ui.onroad_map import (
  CAR_ANCHOR, ZOOM_FAST_M_PER_PX, ZOOM_SLOW_M_PER_PX, CanvasRequest, MapFix, MapInput, MapOverlay, TileReader,
  build_canvas, line_strip, route_guidance, split_route, zoom_for_speed,
)
from openpilot.starpilot.system.starport.tests import test_gpu_nv12

gl = test_gpu_nv12.gpu  # the shared GL context fixture, under this module's name

CENTER = (36.1147, -115.1728)


def test_line_strip_joins_lines_with_degenerate_triangles():
  points = np.array([[0, 0], [10, 0], [10, 10], [50, 50], [60, 50]], np.float32)
  offsets = np.array([0, 3, 5], np.int32)
  strip = line_strip(points, offsets, 2.0, (-100, -100, 100, 100))
  assert len(strip) == 2 * 5 + 2, "two vertices per point plus one repeat on each side of the join"
  assert np.allclose(strip[0], [0, -2]) and np.allclose(strip[1], [0, 2]), "right then left of travel (y down)"
  assert np.allclose(strip[5], strip[6]) and np.allclose(strip[7], strip[8]), "the bridge is zero-area"
  # Mitered corner: the outer offset sits on both edges' offset lines.
  assert np.allclose(strip[2], [12, -2]) and np.allclose(strip[3], [8, 2])


def test_line_strip_culls_offscreen_lines_and_caps_spikes():
  points = np.array([[500, 500], [510, 500], [0, 0], [10, 0], [0, 0]], np.float32)
  strip = line_strip(points, np.array([0, 2, 5], np.int32), 1.0, (-5, -5, 50, 50))
  assert len(strip) == 6 and strip[:, 0].max() < 20
  assert np.abs(strip).max() < 1.0 + 10 + 2.5, "a hairpin's miter is capped"
  assert line_strip(points[:2], np.array([0, 2], np.int32), 1.0, (-5, -5, 50, 50)).shape == (0, 2)


def test_split_route_at_the_car():
  route = np.array([[0, 0], [0, 10], [10, 10]], float)
  done, ahead = split_route(route, (0.5, 4.0))
  assert done.tolist() == [[0, 0], [0, 4]] and ahead.tolist() == [[0, 4], [0, 10], [10, 10]]
  assert split_route(None, (0, 0)) == (None, None)


def test_guidance_tracks_car_without_rebuilding_road_canvas():
  center = world_xy(*CENTER)
  route = np.array([(center[0], center[1] + .1), center, (center[0], center[1] - .1)])
  done, ahead = split_route(route, center)
  request = CanvasRequest(center, 1.0, 1536, ahead, done, ())
  canvas = build_canvas(request, {}, CENTER[0])
  assert canvas.layers == [], "driven route must not leave a bar baked into the road canvas"
  position = (center[0], center[1] - .02)
  _, ahead = split_route(route, position)
  moved = route_guidance(ahead, request, CENTER[0])
  assert len(moved) == 2
  for (_, old), (_, new) in zip(canvas.guidance, moved, strict=True):
    assert new[:, 1].max() < old[:, 1].max(), "guidance ends at the live car, even with the same canvas"
  assert route_guidance(None, request, CENTER[0]) == []


def test_reroute_with_same_endpoints_updates_geometry(tmp_path):
  overlay = MapOverlay(tmp_path)
  try:
    route = ((36., -115.), (36.1, -115.1), (36.2, -115.2))
    overlay._route(route)
    assert overlay._route_world is not None
    previous = overlay._route_world.copy()
    overlay._route((route[0], (36.15, -115.1), route[2]))
    assert overlay._route_world is not None
    assert not np.array_equal(previous, overlay._route_world)
  finally:
    overlay.close()


def test_zoom_widens_with_speed():
  assert zoom_for_speed(0) == ZOOM_SLOW_M_PER_PX and zoom_for_speed(40) == ZOOM_FAST_M_PER_PX
  speeds = [zoom_for_speed(v) for v in range(40)]
  assert speeds == sorted(speeds)


def synthetic_tile(key):
  """A street grid, plus a north-south motorway and an east-west major road crossing at CENTER."""
  x, y = world_xy(*CENTER)
  mx, my = round((x % 1) * EXTENT), round((y % 1) * EXTENT)
  roads = {STREET: [], MINOR: [], MAJOR: [np.array([[0, my], [4096, my]])], MOTORWAY: [np.array([[mx, 0], [mx, 4096]])]}
  for i in range(256, 4096, 512):
    roads[STREET].append(np.array([[i, 0], [i, 4096]]))
    roads[STREET].append(np.array([[0, i], [4096, i]]))
    roads[MINOR].append(np.array([[i + 128, i], [i + 300, i + 200], [i + 128, i + 400]]))
  return encode_road_tile(roads)


def tiles_around(latitude, longitude, radius=2):
  x, y = world_xy(latitude, longitude)
  return [TileKey(DATA_ZOOM, int(x) + dx, int(y) + dy) for dy in range(-radius, radius + 1) for dx in range(-radius, radius + 1)]


def test_canvas_layers_order_and_highway_simplification():
  keys = tiles_around(*CENTER, 1)
  tiles = {key: decode_road_tile(key, synthetic_tile(key)) for key in keys}
  center = world_xy(*CENTER)
  route = np.array([center, (center[0], center[1] - 0.3)])
  slow = build_canvas(CanvasRequest(center, ZOOM_SLOW_M_PER_PX, 1536, route, None, tuple(keys)), tiles, CENTER[0])
  fast = build_canvas(CanvasRequest(center, ZOOM_FAST_M_PER_PX, 1536, route, None, tuple(keys)), tiles, CENTER[0])
  assert slow.tiles_missing == 0
  # Casings, then fills, then the route glow and core on top.
  alphas = [color.a for color, _ in slow.guidance]
  assert len(slow.layers) == 2 * 4 and len(slow.guidance) == 2 and alphas[-2] < 255 and alphas[-1] == 255
  assert len(fast.layers) == 2 * 3 and len(fast.guidance) == 2, "minor roads drop out at highway zoom"
  overview = build_canvas(CanvasRequest(center, 5.0, 1536, None, None, tuple(keys)), tiles, CENTER[0])
  assert len(overview.layers) == 2 * 2, "and streets when zoomed out further"
  missing = build_canvas(CanvasRequest(center, 1.0, 1536, None, None, tuple(keys) + (TileKey(14, 0, 0),)), tiles, CENTER[0])
  assert missing.tiles_missing == 1


def test_tile_reader_loads_from_disk_and_retries_missing(tmp_path):
  store = TileStore(tmp_path, min_free_bytes=0)
  key = TileKey(DATA_ZOOM, 10, 10)
  clock = [0.0]
  reader = TileReader(tmp_path, clock=lambda: clock[0])
  try:
    reader.want([key])
    time.sleep(0.2)
    assert reader.snapshot([key]) == {}
    store.write("cache", key, synthetic_tile(key))
    clock[0] += 10
    reader.want([key])
    deadline = time.monotonic() + 3
    while not reader.snapshot([key]) and time.monotonic() < deadline:
      time.sleep(0.02)
    assert key in reader.snapshot([key]) and reader.generation == 1
  finally:
    reader.close()


def test_tile_reader_finishes_canvas_larger_than_cache(tmp_path):
  from openpilot.starpilot.ui.onroad_map import TILE_CACHE
  store = TileStore(tmp_path, min_free_bytes=0)
  keys = [TileKey(DATA_ZOOM, x, 10) for x in range(TILE_CACHE + 1)]
  for key in keys:
    store.write("saved", key, encode_road_tile({}))
  reader = TileReader(tmp_path)
  finished = threading.Event()
  try:
    reader.want(keys)
    reader.submit(finished.set)
    assert finished.wait(2), "requested tiles must not evict each other and starve the canvas"
    assert len(reader.snapshot(keys)) == len(keys)
  finally:
    reader.close()


def test_lost_gps_marks_previous_position_stale(tmp_path):
  overlay = MapOverlay(tmp_path)
  try:
    overlay._advance(MapInput(MapFix(*CENTER, 0., 10., 100.)), 100.)
    assert overlay.status == "live"
    overlay._advance(MapInput(None), 106.)
    assert overlay.status == "waiting"
  finally:
    overlay.close()


def test_phone_map_keeps_moving_between_delayed_fixes(tmp_path):
  overlay = MapOverlay(tmp_path)
  try:
    data = MapInput(MapFix(*CENTER, 90., 20., 100.), source='phone')
    positions = []
    for frame in range(101):
      overlay._advance(data, 100. + frame / 50)
      assert overlay._world is not None
      positions.append(overlay._world[0])
    assert all(b > a for a, b in zip(positions, positions[1:], strict=False)), 'no one-second freeze between phone fixes'
    assert overlay.status == 'live'
  finally:
    overlay.close()


@pytest.mark.parametrize('speed, bearing', [(0., 90.), (20., None), (float('nan'), 90.)])
def test_phone_map_hold_does_not_invent_stationary_or_unknown_heading_movement(tmp_path, speed, bearing):
  overlay = MapOverlay(tmp_path)
  try:
    data = MapInput(MapFix(*CENTER, bearing, speed, 100.), source='phone')
    overlay._advance(data, 100.)
    initial = overlay._world
    for frame in range(1, 501):
      overlay._advance(data, 100. + frame / 50)
      assert overlay._world == initial
      assert overlay.status == 'live'
    overlay._advance(data, 110.001)
    assert overlay.status == 'waiting'
  finally:
    overlay.close()


@pytest.mark.parametrize('source, limit', [('phone', 2.5), ('comma', 1.), ('car', 1.)])
def test_map_prediction_is_bounded_separately_from_fix_hold(tmp_path, source, limit):
  from openpilot.starpilot.navigation.road_tiles import meters_per_tile
  overlay = MapOverlay(tmp_path)
  try:
    overlay._advance(MapInput(MapFix(*CENTER, 90., 20., 100.), source=source), 109.)
    x, y = world_xy(*CENTER)
    assert overlay._world == pytest.approx((x + 20. * limit / meters_per_tile(CENTER[0]), y))
  finally:
    overlay.close()


# ---------------------------------------------------------------- GL

def render_overlay(rl, tmp_path, bearing, width=560, height=420, opacity=1.0, route=True, steps=40):
  store = TileStore(tmp_path, min_free_bytes=0)
  for key in tiles_around(*CENTER):
    store.write("saved", key, synthetic_tile(key))
  clock = [100.0]
  overlay = MapOverlay(tmp_path, clock=lambda: clock[0])
  x, y = world_xy(*CENTER)
  path = tuple(lat_lon(x + dx, y + dy) for dx, dy in ((0, 0.2), (0, 0.02), (0.08, 0.02), (0.08, -0.4)))
  data = MapInput(MapFix(*CENTER, bearing, 3.0, clock[0]), path if route else ())
  try:
    for _ in range(steps):
      overlay.prepare(data, width, height)
      if overlay.has_roads and overlay._pending is overlay._canvas_request and overlay.reader.generation == overlay._generation_built:
        break
      time.sleep(0.05)
    overlay.prepare(data, width, height)
    target = rl.load_render_texture(width, height)
    rl.begin_texture_mode(target)
    rl.clear_background(rl.Color(70, 90, 70, 255))          # stands in for the camera
    overlay.draw(rl.Rectangle(0, 0, width, height), opacity)
    rl.end_texture_mode()
    image = rl.load_image_from_texture(target.texture)
    rl.image_flip_vertical(image)
    pixels = np.frombuffer(image.data[:width * height * 4], np.uint8).reshape(height, width, 4).copy()
    rl.unload_image(image)
    rl.unload_render_texture(target)
    return pixels
  finally:
    overlay.close()


def yellow(pixels):
  r, g, b = (pixels[..., i].astype(int) for i in range(3))
  return (r > 200) & (g > 150) & (b < 140)


def test_overlay_draws_heading_up_roads_route_and_soft_edges(gl, tmp_path):
  rl = gl
  north = render_overlay(rl, tmp_path / "n", bearing=0.0)
  east = render_overlay(rl, tmp_path / "e", bearing=90.0)
  height, width = north.shape[:2]
  # The motorway runs north-south through the car: vertical when heading north, horizontal when heading east.
  if os.getenv("STARPILOT_MAP_PREVIEW_DIR"):
    for name, pixels in (("map_north.png", north), ("map_east.png", east)):
      image = rl.Image(np.ascontiguousarray(pixels), width, height, 1, 7)
      rl.export_image(image, os.path.join(os.environ["STARPILOT_MAP_PREVIEW_DIR"], name))
  ys, xs = np.nonzero(yellow(north))
  assert len(xs) > 200 and np.ptp(ys) > height * 0.6 and np.ptp(xs) < 40
  ys, xs = np.nonzero(yellow(east))
  assert len(xs) > 200 and np.ptp(xs) > width * 0.6 and np.ptp(ys) < 40
  # The car puck is white at its anchor; the corners fade to the camera underneath.
  car = north[int(height * CAR_ANCHOR), width // 2]
  assert car[:3].min() > 200
  assert np.abs(north[2, 2, :3].astype(int) - (70, 90, 70)).max() < 6
  # The route (violet) shows ahead of the car.
  r, g, b = (north[..., i].astype(int) for i in range(3))
  assert ((b > 200) & (r > 120) & (r < 210) & (g < 175)).sum() > 300


def test_overlay_opacity_fades_map_background(gl, tmp_path):
  rl = gl
  full = render_overlay(rl, tmp_path / "a", bearing=0.0, opacity=1.0, route=False).astype(int)
  half = render_overlay(rl, tmp_path / "b", bearing=0.0, opacity=0.35, route=False).astype(int)
  camera = np.array([70, 90, 70])
  center = (slice(100, 240), slice(100, 360))
  full_delta = np.abs(full[center][..., :3] - camera).mean()
  half_delta = np.abs(half[center][..., :3] - camera).mean()
  assert 0.25 < half_delta / full_delta < 0.45


def test_overlay_without_gps_waits_without_drawing_roads(gl, tmp_path):
  rl = gl
  overlay = MapOverlay(tmp_path)
  try:
    overlay.prepare(MapInput(None), 300, 220)
    assert overlay.status == "waiting" and not overlay.has_roads
    target = rl.load_render_texture(300, 220)
    rl.begin_texture_mode(target)
    overlay.draw(rl.Rectangle(0, 0, 300, 220), 0.7)
    rl.end_texture_mode()
    rl.unload_render_texture(target)
  finally:
    overlay.close()
  assert math.isfinite(EXTENT)


class FakeGpsMaster:
  """SubMaster shape that starpilot.gps.source.select_location reads."""

  def __init__(self):
    from types import SimpleNamespace
    self.ns = SimpleNamespace
    self.messages, self.valid, self.logMonoTime = {}, {}, {}

  def update(self, _timeout):
    pass

  def __getitem__(self, service):
    return self.messages[service]

  def put(self, service, stamp, **gps):
    fields = {"hasFix": True, "latitude": 36.1, "longitude": -115.1, "horizontalAccuracy": 5.0, "speed": 10.0,
              "bearingDeg": 90.0, "bearingAccuracyDeg": 5.0, **gps}
    if service == "starpilotCarState":
      self.messages[service] = self.ns(gps=self.ns(sourceMonoTime=stamp, **fields))
    else:
      from openpilot.starpilot.gps.source import GPS_SOURCES
      self.messages[service] = self.ns(source=GPS_SOURCES[service], **fields)
    self.valid[service], self.logMonoTime[service] = True, stamp


def test_map_feed_uses_navigation_gps_choice_with_car_fallback(monkeypatch):
  from openpilot.starpilot.gps.source import GPS_SOURCES
  from openpilot.starpilot.ui.onroad_map import MapFeed
  now = 50_000_000_000
  monkeypatch.setattr(time, "monotonic_ns", lambda: now)
  monkeypatch.setattr(time, "monotonic", lambda: now / 1e9)
  sm = FakeGpsMaster()
  for service in GPS_SOURCES:
    sm.valid[service], sm.logMonoTime[service] = False, 0
  acquisition = Mock(since=now / 1e9)
  acquisition.satellites.return_value = None
  feed = MapFeed(sm, acquisition=acquisition)
  assert feed.read().fix is None
  sm.put("starpilotCarState", now - 200_000_000, latitude=36.2)
  fix = feed.read().fix
  assert fix.latitude == 36.2 and fix.monotonic == (now - 200_000_000) / 1e9, "the car's GPS when no receiver has a fix"
  sm.put("gpsLocationExternal", now - 100_000_000, latitude=36.3, bearingAccuracyDeg=200.0)
  fix = feed.read().fix
  assert fix.latitude == 36.3 and fix.bearing is None, "a receiver wins; an unusable heading is dropped"
  nav = sm.ns(version=2, enabled=True, status="guiding", frameMonoTime=now, destinationName="Home",
              route=[sm.ns(latitude=36.1, longitude=-115.1)])
  published = {"starpilotNavigation": sm.ns(navigation=nav)}
  class NavigationMaster(dict):
    valid = {"starpilotNavigation": True}
  navigation = NavigationMaster(published)
  assert feed.read(navigation).route == ((36.1, -115.1),)
  nav.frameMonoTime = now - 3_000_000_001
  assert feed.read(navigation).route == (), "a stopped route publisher must not leave stale guidance"


def test_map_feed_waiting_tracks_satellites_and_keeps_navigation_requested(monkeypatch):
  from openpilot.starpilot.ui.onroad_map import MapFeed
  clock = [50.0]
  monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
  monkeypatch.setattr(time, 'monotonic_ns', lambda: int(clock[0] * 1e9))
  acquisition = Mock(since=50.0)
  acquisition.satellites.return_value = 3
  sm = FakeGpsMaster()
  feed = MapFeed(sm, acquisition=acquisition)
  nav = NS(enabled=True, destinationName='Home', status='waitingForLocation', version=2)
  navigation_sm = FakeGpsMaster()
  navigation_sm.messages['starpilotNavigation'] = NS(navigation=nav)
  navigation_sm.valid['starpilotNavigation'] = True
  clock[0] += 75
  data = feed.read(navigation_sm)
  assert (data.navigation_active, data.satellites, data.gps_wait_seconds) == (True, 3, 75)
  assert data.destination == 'Home'
  assert feed.read(navigation_sm, destination='Work').destination == 'Work'
  assert feed.read(navigation_sm, navigation_requested=False).navigation_active is False
  sm.put('starpilotCarState', int(clock[0] * 1e9))
  data = feed.read(navigation_sm)
  assert data.fix is not None and data.satellites is None and data.gps_wait_seconds == 0
  acquisition.reset.assert_called_once()
  clock[0] += 3
  # Keep the map position briefly, but show acquisition again when that fix becomes stale.
  acquisition.since = clock[0]
  data = feed.read(navigation_sm)
  assert data.fix is not None and data.satellites == 3
  assert feed.read(navigation_requested=True).navigation_active is True


@pytest.mark.parametrize('active', [True, False])
@pytest.mark.parametrize('satellites', [None, 1, 3])
@pytest.mark.parametrize('size', [(280, 200), (560, 420), (900, 600)])
def test_gps_status_card_is_inside_bottom_of_map(tmp_path, active, satellites, size):
  from openpilot.starpilot.ui.raylib import rl
  fonts = Mock()
  fonts.measure.side_effect = lambda text, _role, size: NS(width=len(text) * size * .65, height=size * 1.25)
  overlay = MapOverlay(tmp_path, fonts=fonts)
  try:
    overlay._advance(MapInput(None, navigation_active=active, satellites=satellites, gps_wait_seconds=75), 100)
    rect = rl.Rectangle(100, 200, *size)
    with patch('raylib_nb.draw_rectangle_rounded') as backgrounds:
      overlay._draw_gps_status(rect)
    detail = ('Searching for satellites' if satellites is None else
              f"{satellites} satellite{'s' if satellites != 1 else ''} locked")
    expected = (['Navigation active'] if active else []) + ['Waiting for GPS', f'{detail}  •  1:15']
    assert [call.args[0] for call in fonts.draw.call_args_list] == expected
    card, track, fill = [call.args[0] for call in backgrounds.call_args_list]
    from openpilot.starpilot.gps.acquisition import acquisition_progress
    assert fill.width == pytest.approx(track.width * acquisition_progress(satellites))
    assert card.y > rect.y + rect.height / 2
    assert card.y + card.height < rect.y + rect.height
    for call in fonts.draw.call_args_list:
      text, role, size, x, y, _ = call.args
      measured = fonts.measure(text, role, size)
      assert card.x <= x and x + measured.width <= card.x + card.width
      assert card.y <= y and y + measured.height < track.y
  finally:
    overlay.close()


def test_gps_source_chip_is_solid_and_inside_the_widget(tmp_path):
  from openpilot.starpilot.ui.raylib import rl
  fonts = Mock()
  fonts.measure.side_effect = lambda text, _role, size: NS(width=len(text) * size * .65, height=size * 1.25)
  overlay = MapOverlay(tmp_path, fonts=fonts)
  try:
    rect = rl.Rectangle(100, 200, 560, 420)
    for source, label in (("comma", "Comma GPS"), ("car", "Car GPS"), ("phone", "Phone GPS")):
      overlay._input = MapInput(MapFix(*CENTER, 0, 0, 100), source=source)
      fonts.draw.reset_mock()
      with patch('raylib_nb.draw_rectangle_rounded') as chip, patch('raylib_nb.draw_rectangle_rounded_lines_ex'):
        overlay._draw_source_chip(rect)
      assert [call.args[0] for call in fonts.draw.call_args_list] == [label]
      assert chip.call_args.args[3].a == 255 and fonts.draw.call_args.args[5].a == 255
      box = chip.call_args.args[0]
      assert rect.x < box.x and box.x + box.width < rect.x + rect.width and box.y >= rect.y
    fonts.draw.reset_mock()
    overlay._input = MapInput(MapFix(*CENTER, 0, 0, 100))
    overlay._draw_source_chip(rect)
    assert not fonts.draw.called
  finally:
    overlay.close()


def test_starting_navigation_overlay_until_route_is_drawn(tmp_path):
  from openpilot.starpilot.ui.raylib import rl
  fonts = Mock()
  fonts.measure.side_effect = lambda text, _role, size: NS(width=len(text) * size * .65, height=size * 1.25)
  overlay = MapOverlay(tmp_path, fonts=fonts)
  try:
    rect = rl.Rectangle(100, 200, 560, 420)
    overlay._input = MapInput(MapFix(*CENTER, 0, 0, 100), navigation_active=True)
    with patch('raylib_nb.draw_rectangle_rounded') as card:
      overlay._draw_starting_navigation(rect)
    assert [call.args[0] for call in fonts.draw.call_args_list] == ['Starting navigation', 'Finding your route…']
    assert card.call_args.args[3].a == 255, 'solid whatever the map opacity'
    assert all(call.args[5].a == 255 for call in fonts.draw.call_args_list)
    fonts.draw.reset_mock()
    overlay._input = MapInput(MapFix(*CENTER, 0, 0, 100), navigation_active=True, destination='Work')
    with patch('raylib_nb.draw_rectangle_rounded'):
      overlay._draw_starting_navigation(rect)
    assert [call.args[0] for call in fonts.draw.call_args_list] == ['Starting navigation', 'to Work', 'Finding your route…']
    fonts.draw.reset_mock()
    overlay._input = MapInput(MapFix(*CENTER, 0, 0, 100), navigation_active=True, destination='A' * 40)
    overlay._route_world = np.zeros((2, 2))
    with patch('raylib_nb.draw_rectangle_rounded'):
      overlay._draw_starting_navigation(rect)
    assert [call.args[0] for call in fonts.draw.call_args_list] == ['Starting navigation', 'to ' + 'A' * 31 + '…']
  finally:
    overlay.close()


def test_lost_route_only_restores_starting_overlay_after_ten_seconds(tmp_path):
  clock = [100.0]
  overlay = MapOverlay(tmp_path, clock=lambda: clock[0])
  try:
    route = tuple((CENTER[0] + i * 1e-4, CENTER[1]) for i in range(4))
    fix = MapFix(*CENTER, 0, 0, 100)
    def step(data):
      overlay._input = data
      overlay._route(data.route)
      overlay._track_route(data, clock[0])
      return overlay._starting_navigation()
    assert step(MapInput(fix, (), True))            # never had a route: show immediately
    clock[0] += 5
    assert not step(MapInput(fix, route, True))
    clock[0] += 5
    assert not step(MapInput(fix, (), True))        # brief gap: no flash
    clock[0] += 6
    assert step(MapInput(fix, (), True))
  finally:
    overlay.close()


def test_starting_overlay_stays_up_briefly_when_the_route_loads_fast(tmp_path):
  clock = [100.0]
  overlay = MapOverlay(tmp_path, clock=lambda: clock[0])
  try:
    route = tuple((CENTER[0] + i * 1e-4, CENTER[1]) for i in range(4))
    fix = MapFix(*CENTER, 0, 0, 100)
    def step(data):
      overlay._input = data
      overlay._route(data.route)
      overlay._track_route(data, clock[0])
      return overlay._starting_navigation()
    assert step(MapInput(fix, (), True))
    clock[0] += .5
    assert step(MapInput(fix, route, True)), 'route already loaded, still readable'
    clock[0] += 4.4
    assert step(MapInput(fix, route, True))
    clock[0] += .2
    assert not step(MapInput(fix, route, True))
    assert not step(MapInput(fix, (), False))
    clock[0] += 1
    assert step(MapInput(fix, (), True)), 'a new navigation starts the card again'
    assert not step(MapInput(fix, (), False)), 'ending navigation drops it at once'
  finally:
    overlay.close()


def test_losing_gps_shows_waiting_even_with_a_cached_map_position(tmp_path):
  overlay = MapOverlay(tmp_path)
  try:
    overlay._advance(MapInput(MapFix(*CENTER, 0, 0, 100)), 100)
    assert overlay.status == 'live'
    overlay._advance(MapInput(MapFix(*CENTER, 0, 0, 100)), 103)
    assert overlay.status == 'waiting' and overlay._world is not None
    overlay._advance(MapInput(None), 106)
    assert overlay.status == 'waiting'
    overlay._advance(MapInput(MapFix(*CENTER, 0, 0, 107)), 107)
    assert overlay.status == 'live'
  finally:
    overlay.close()


def test_route_stays_bright_at_minimum_map_opacity(gl, tmp_path):
  full = render_overlay(gl, tmp_path / "full", bearing=0.0, opacity=1.0)
  faded = render_overlay(gl, tmp_path / "faded", bearing=0.0, opacity=0.15)
  r, g, b = (full[..., i].astype(int) for i in range(3))
  core = (b > 245) & (r > 130) & (r < 210) & (g < 180)
  assert core.sum() > 100
  assert np.abs(full[core, :3].astype(int) - faded[core, :3].astype(int)).mean() < 3
  assert np.abs(faded[2, 2, :3].astype(int) - (70, 90, 70)).max() < 6


class FakeNavigationMaster(FakeGpsMaster):
  def put_route(self, stamp, points, status="guiding"):
    from types import SimpleNamespace as ns
    from openpilot.starpilot.navigation.wire import VERSION
    route = [ns(latitude=lat, longitude=lon) for lat, lon in points]
    self.messages["starpilotNavigation"] = ns(navigation=ns(version=VERSION, status=status, route=route,
                                                           enabled=True, destinationName='Home', frameMonoTime=time.monotonic_ns()))
    self.valid["starpilotNavigation"], self.logMonoTime["starpilotNavigation"] = True, stamp


def test_map_feed_decodes_the_route_once_per_navigation_message(monkeypatch):
  from openpilot.starpilot.gps.source import GPS_SOURCES
  from openpilot.starpilot.navigation import wire
  from openpilot.starpilot.ui.onroad_map import MapFeed
  sm = FakeNavigationMaster()
  for service in GPS_SOURCES:
    sm.valid[service], sm.logMonoTime[service] = False, 0
  decoded = []
  clock = [100.0]
  monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
  monkeypatch.setattr(time, 'monotonic_ns', lambda: int(clock[0] * 1e9))
  real = wire.navigation_state
  monkeypatch.setattr(wire, "navigation_state", lambda envelope: decoded.append(1) or real(envelope))
  acquisition = Mock(since=time.monotonic())
  acquisition.satellites.return_value = 3
  feed = MapFeed(sm, acquisition=acquisition)
  points = [(36.0 + i * 1e-4, -115.0) for i in range(400)]
  sm.put_route(1, points)
  first = feed.read(sm).route
  assert len(first) == 400 and all(feed.read(sm).route is first for _ in range(20))
  assert len(decoded) == 1, "same navigation message: no re-decode"
  clock[0] += .6
  acquisition.satellites.return_value = 4
  cached = feed.read(sm)
  assert cached.route is first and cached.navigation_active and cached.satellites == 4
  assert cached.gps_wait_seconds == pytest.approx(.6)
  assert len(decoded) == 1, 'GPS status advances without decoding the cached route'
  assert feed.read(sm, navigation_requested=False).navigation_active is False
  assert not feed.read().navigation_active and feed.read().route == ()
  sm.put_route(2, points)
  assert feed.read(sm).route is first and len(decoded) == 2, "a new message with the same route keeps the same tuple"
  sm.put_route(3, points[1:])
  assert feed.read(sm).route == tuple(points[1:])
  sm.put_route(4, points, status="idle")
  assert feed.read(sm).route == ()
  sm.valid['starpilotNavigation'] = False
  assert not feed.read(sm).navigation_active and feed.read(sm).route == ()


def test_cached_map_route_expires_without_a_new_navigation_message(monkeypatch):
  from openpilot.starpilot.ui.onroad_map import MapFeed
  clock = [100.0]
  monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
  monkeypatch.setattr(time, 'monotonic_ns', lambda: int(clock[0] * 1e9))
  sm = FakeNavigationMaster()
  acquisition = Mock(since=clock[0])
  acquisition.satellites.return_value = None
  feed = MapFeed(sm, acquisition=acquisition)
  points = [(36.0, -115.0), (36.1, -115.1)]
  sm.put_route(1, points)
  assert feed.read(sm).route == tuple(points)
  clock[0] += 3.1
  assert feed.read(sm).route == (), 'a cached message must not extend the guidance lease'
  sm.put_route(2, points)
  assert feed.read(sm).route == tuple(points)


def guidance_overlay(tmp_path, monkeypatch, clock):
  from openpilot.starpilot.ui import onroad_map
  overlay = MapOverlay(tmp_path, clock=lambda: clock[0])
  x, y = world_xy(*CENTER)
  route = tuple(lat_lon(x, y + 0.02 - i * 1e-4) for i in range(400))       # due north through the car, 400 points
  overlay._route(route)
  overlay._latitude, overlay._m_per_px, overlay._world = CENTER[0], ZOOM_SLOW_M_PER_PX, (x, y)
  overlay._canvas_request = CanvasRequest((x, y), ZOOM_SLOW_M_PER_PX, 1024, None, None, ())
  builds = []
  real = onroad_map.route_guidance
  monkeypatch.setattr(onroad_map, "route_guidance", lambda *args: builds.append(1) or real(*args))
  for name in ("begin_texture_mode", "end_texture_mode", "clear_background", "begin_blend_mode", "end_blend_mode",
               "draw_rectangle", "draw_texture_pro", "rl_push_matrix", "rl_pop_matrix", "rl_translatef", "rl_rotatef",
               "rl_scalef", "draw_triangle_strip", "draw_circle_v", "draw_triangle"):
    monkeypatch.setattr(onroad_map.rl, name, lambda *args: None)
  overlay._canvas = object()
  return overlay, route, builds


def test_guidance_is_not_rebuilt_every_frame(tmp_path, monkeypatch):
  from openpilot.starpilot.navigation.road_tiles import meters_per_tile
  from openpilot.starpilot.ui.onroad_map import GUIDANCE_SLACK_PX
  overlay, route, builds = guidance_overlay(tmp_path, monkeypatch, [100.0])
  try:
    overlay._compose_layer(400, 300, True)
    assert len(builds) == 1 and overlay._route_layers
    slack = GUIDANCE_SLACK_PX * ZOOM_SLOW_M_PER_PX / meters_per_tile(CENTER[0])
    x, y = overlay._world
    for step in range(1, 11):                                              # creeping forward, under the slack
      overlay._world = (x, y - slack * 0.09 * step)
      overlay._route(route)
      overlay._compose_layer(400, 300, True)
    assert len(builds) == 1, "consecutive frames on the same route and canvas reuse the guidance"
    overlay._world = (x, y - slack * 1.5)
    overlay._compose_layer(400, 300, True)
    assert len(builds) == 2, "past the slack, guidance is trimmed at the car again"
    overlay._route(route[1:])
    overlay._compose_layer(400, 300, True)
    assert len(builds) == 3, "a new route rebuilds"
    overlay._canvas_request = CanvasRequest(overlay._world, ZOOM_SLOW_M_PER_PX, 1024, None, None, ())
    overlay._compose_layer(400, 300, True)
    assert len(builds) == 4, "a new road canvas rebuilds"
  finally:
    overlay._canvas = None
    overlay.close()


def test_visible_runs_keeps_only_stretches_that_reach_the_bounds():
  from openpilot.starpilot.ui.onroad_map import visible_runs
  bounds = (0.0, 0.0, 100.0, 100.0)
  inside = np.array([[10, 10], [50, 50], [90, 20]], np.float32)
  points, offsets = visible_runs(inside, bounds)
  assert points is inside and offsets.tolist() == [0, 3]
  # Leaves through the top, wanders far away, comes back in on the left.
  route = np.array([[50, 50], [50, -20], [300, -300], [300, 300], [-20, 60], [40, 60]], np.float32)
  points, offsets = visible_runs(route, bounds)
  assert offsets.tolist() == [0, 2, 5]   # the diagonal back in from (300, 300) touches the bounds near (0, 75)
  np.testing.assert_array_equal(points, route[[0, 1, 3, 4, 5]])
  # A segment crossing the bounds with both ends outside is kept.
  points, offsets = visible_runs(np.array([[-50, 50], [150, 50]], np.float32), bounds)
  assert offsets.tolist() == [0, 2]
  points, offsets = visible_runs(np.array([[200, 200], [300, 300]], np.float32), bounds)
  assert len(points) == 0 and len(offsets) == 1


def test_culled_guidance_draws_the_same_inside_the_canvas():
  from openpilot.starpilot.ui.onroad_map import line_strip
  x, y = world_xy(*CENTER)
  request = CanvasRequest((x, y), ZOOM_SLOW_M_PER_PX, 1024, None, None, ())
  # 300 points heading north for many canvases: only the first stretch is on this one.
  route = np.array([(x + 0.0001 * math.sin(i / 7), y - i * 0.004) for i in range(300)])
  layers = route_guidance(route, request, CENTER[0])
  assert len(layers) == 2
  from openpilot.starpilot.navigation.road_tiles import meters_per_tile
  from openpilot.starpilot.ui.onroad_map import REFERENCE_M_PER_PX, SUPERSAMPLE
  points = ((route - request.center) * meters_per_tile(CENTER[0]) * SUPERSAMPLE / request.m_per_px + request.side / 2).astype(np.float32)
  width_scale = SUPERSAMPLE * min(1.35, max(0.7, math.sqrt(REFERENCE_M_PER_PX / request.m_per_px)))
  full = line_strip(points, np.array([0, len(points)], np.int32), 7.0 * width_scale / 2, (-8.0, -8.0, 1032.0, 1032.0))
  core = layers[1][1]
  assert len(core) < len(full) / 3, "most of the route is off this canvas"
  inside = (full[:, 1] > 0) & (full[:, 1] < 1024)
  assert np.isin(full[inside].view(np.complex64), core.view(np.complex64)).all(), "the on-canvas strip is unchanged"


def test_map_feed_subscribes_to_registered_gps_carriers(monkeypatch):
  import sys
  from openpilot.cereal.services import SERVICE_LIST
  from openpilot.starpilot.ui.onroad_map import MapFeed

  subscriptions = []

  def submaster(services):
    assert len(services) == len(set(services))
    assert set(services) <= SERVICE_LIST.keys()
    assert {"gpsLocationExternal", "gpsLocation", "starpilotCarState"} <= set(services)
    assert "phoneGps" not in services
    assert services.count("starpilotNavigation") == 1
    subscriptions.extend(services)
    return NS()

  messaging = NS(SubMaster=submaster)
  from openpilot import cereal
  monkeypatch.setattr(cereal, "messaging", messaging, raising=False)
  monkeypatch.setitem(sys.modules, "openpilot.cereal.messaging", messaging)
  feed = MapFeed(acquisition=NS())
  assert feed.sm is not None
  assert subscriptions
