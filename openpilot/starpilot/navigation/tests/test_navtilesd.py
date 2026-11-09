import json

import pytest
import requests

from openpilot.starpilot.navigation import navtilesd
from openpilot.starpilot.navigation.navtilesd import Environment, Fetcher, TileDaemon, ahead_tiles
from openpilot.starpilot.navigation.offline_roads import OfflineState, TileStore, Usage, estimate, validate_area, area_tiles
from openpilot.starpilot.navigation.road_tiles import DATA_ZOOM, TileKey, decode_road_tile, world_xy
from openpilot.starpilot.navigation.tests.test_road_tiles import mvt

TILE = mvt([("primary", 2, [[(0, 0), (4096, 4096)]])])


class Response:
  def __init__(self, status, body=b""):
    self.status_code, self.body = status, body

  def iter_content(self, _size):
    yield self.body

  def close(self):
    pass


class Session:
  def __init__(self, status=200, body=TILE, error=None):
    self.status, self.body, self.error = status, body, error
    self.urls = []

  def get(self, url, params=None, timeout=None, stream=False):
    self.urls.append(url)
    assert params == {"access_token": "pk.test"} and stream
    if self.error:
      raise self.error
    return Response(self.status, self.body)


class Clock:
  def __init__(self):
    self.now = 1000.0

  def __call__(self):
    return self.now


def daemon(tmp_path, session=None, clock=None):
  clock = clock or Clock()
  store = TileStore(tmp_path, min_free_bytes=0)
  usage = Usage(tmp_path, clock=lambda: 1_790_000_000.0)
  fetcher = Fetcher(session or Session(), usage, clock=clock)
  result = TileDaemon(store, OfflineState(tmp_path), fetcher, clock=clock, wall=lambda: 1_790_000_000.0)
  result.usage = usage
  return result


def run(d, env, limit=10_000):
  for _ in range(limit):
    d.plan(env)
    wait = d.step(env)
    if wait is None:
      return
    d.clock.now += max(wait, 1e-6) if hasattr(d.clock, "now") else 0
  raise AssertionError("daemon never went idle")


def test_fetcher_stores_road_lines_and_counts_usage(tmp_path):
  d = daemon(tmp_path)
  raw = d.fetcher.fetch(TileKey(14, 1, 2), "pk.test")
  assert d.fetcher.session.urls == ["https://api.mapbox.com/v4/mapbox.mapbox-streets-v8/14/1/2.vector.pbf"]
  assert not decode_road_tile(TileKey(14, 1, 2), raw).empty
  assert d.usage.snapshot()["tiles"] == 1


@pytest.mark.parametrize("status,failure", [(401, "key"), (403, "key"), (429, "server"), (503, "server")])
def test_fetcher_backs_off_on_key_and_server_failures(tmp_path, status, failure):
  clock = Clock()
  d = daemon(tmp_path, Session(status=status), clock)
  assert d.fetcher.fetch(TileKey(14, 0, 0), "pk.test") is None
  assert d.fetcher.failure == failure and d.fetcher.blocked
  assert d.fetcher.fetch(TileKey(14, 0, 1), "pk.test") is None and len(d.fetcher.session.urls) == 1
  clock.now += 3600
  assert not d.fetcher.blocked


def test_fetcher_network_failure_and_empty_ocean_tile(tmp_path):
  d = daemon(tmp_path, Session(error=requests.ConnectionError()))
  assert d.fetcher.fetch(TileKey(14, 0, 0), "pk.test") is None and d.fetcher.failure == "network"
  d = daemon(tmp_path, Session(status=404))
  assert decode_road_tile(TileKey(14, 0, 0), d.fetcher.fetch(TileKey(14, 0, 0), "pk.test")).empty


def test_near_car_tiles_are_saved_as_you_drive(tmp_path):
  d = daemon(tmp_path)
  env = Environment(token="pk.test", network="cellular", onroad=True, map_enabled=True,
                    position=(36.1147, -115.1728), bearing=0.0)
  run(d, env)
  keys = ahead_tiles(36.1147, -115.1728, 0.0)
  assert all(d.store.has("driven", key) for key in keys)
  north = TileKey(DATA_ZOOM, int(world_xy(36.1147, -115.1728)[0]), int(world_xy(36.1147, -115.1728)[1]) - 3)
  assert north in keys, "the band ahead reaches past the near circle"
  # Turning it off keeps new tiles in the trimmed cache instead.
  OfflineState(tmp_path).set_settings({"saveDriven": False})
  env.position = (36.30, -115.1728)
  run(d, env)
  assert any(d.store.has("cache", key) for key in ahead_tiles(36.30, -115.1728, 0.0))


def test_map_off_or_offroad_fetches_nothing_near_the_car(tmp_path):
  d = daemon(tmp_path)
  for env in (Environment(token="pk.test", network="wifi", onroad=True, map_enabled=False, position=(36.1, -115.1)),
              Environment(token="pk.test", network="wifi", onroad=False, map_enabled=True, position=(36.1, -115.1)),
              Environment(token="", network="wifi", onroad=True, map_enabled=True, position=(36.1, -115.1))):
    run(d, env)
  assert d.fetcher.session.urls == []


def test_offline_area_downloads_only_on_wifi_then_completes(tmp_path):
  d = daemon(tmp_path)
  area = OfflineState(tmp_path).add_area({"latitude": 36.1147, "longitude": -115.1728, "radiusKm": 3, "name": "Strip"})
  run(d, Environment(token="pk.test", network="cellular"))
  assert d.fetcher.session.urls == []
  d.status(Environment(token="pk.test", network="cellular"), force=True)
  status = OfflineState(tmp_path).status()
  assert status is not None
  assert status["areas"][area["id"]]["state"] == "waiting_wifi"
  wifi = Environment(token="pk.test", network="wifi")
  run(d, wifi)
  tiles = area_tiles(area)
  assert all(d.store.has("saved", key) for key in tiles)
  d.status(wifi, force=True)
  status = OfflineState(tmp_path).status()
  assert status is not None
  assert status["areas"][area["id"]] == {"state": "complete", "total": len(tiles), "done": len(tiles), "failed": 0}
  assert OfflineState(tmp_path).areas()[0]["refreshed"] == 1_790_000_000.0
  fetched = len(d.fetcher.session.urls)
  run(d, wifi)
  assert len(d.fetcher.session.urls) == fetched, "a complete area is not downloaded twice"


def test_area_reuses_cached_tiles_and_deleted_area_tiles_become_cache(tmp_path):
  d = daemon(tmp_path)
  state = OfflineState(tmp_path)
  area = state.add_area({"latitude": 36.1147, "longitude": -115.1728, "radiusKm": 3})
  cached = area_tiles(area)[0]
  d.store.write("cache", cached, b"cached")
  run(d, Environment(token="pk.test", network="wifi"))
  assert d.store.path("saved", cached).read_bytes() == b"cached"
  assert len(d.fetcher.session.urls) == len(area_tiles(area)) - 1
  state.delete_area(area["id"])
  d.plan(Environment(token="pk.test", network="wifi"))
  d.last_trim = -1e9
  d.maintain()
  assert not any(d.store.has("saved", key) for key in area_tiles(area))
  assert d.store.has("cache", cached)


def test_route_corridor_is_prefetched_after_near_tiles(tmp_path):
  d = daemon(tmp_path)
  route = [(36.10, -115.17), (36.16, -115.17)]
  run(d, Environment(token="pk.test", network="cellular", onroad=True, route=route))
  from openpilot.starpilot.navigation.road_tiles import corridor_tiles
  assert all(d.store.find(key) is not None for key in corridor_tiles(route))


def test_reroute_and_clear_replace_download_plan(tmp_path):
  d = daemon(tmp_path)
  route = [(36.1, -115.1), (36.15, -115.1), (36.2, -115.2)]
  d.plan(Environment(route=route))
  old = list(d.route_plan)
  route[1] = (36.15, -115.3)
  d.plan(Environment(route=route))
  assert d.route_plan != old
  d.plan(Environment())
  assert d.route_plan == []


def test_storage_failure_pauses_until_retry(tmp_path, monkeypatch):
  d = daemon(tmp_path)
  d.state.add_area({"latitude": 36.1, "longitude": -115.1, "radiusKm": 2})
  env = Environment(token="pk.test", network="wifi")
  d.plan(env)
  with monkeypatch.context() as patch:
    patch.setattr(d.store, "write", lambda *args: False)
    d.step(env)
    for _ in range(3):
      d.clock.now += 1
      assert d.step(env) is None
    assert len(d.fetcher.session.urls) == 1
  d.clock.now += navtilesd.TRIM_INTERVAL_S
  assert d.step(env) == 0
  assert len(d.fetcher.session.urls) == 2


def test_incomplete_area_retries_missing_tiles(tmp_path):
  d = daemon(tmp_path, Session(body=b"invalid tile"))
  d.state.add_area({"latitude": 36.1, "longitude": -115.1, "radiusKm": 2})
  env = Environment(token="pk.test", network="wifi")
  run(d, env)
  progress = next(iter(d.progress.values()))
  assert progress.finished and progress.failed
  d.fetcher.session.body = TILE
  d.clock.now += navtilesd.SERVER_BACKOFF_S + 1
  run(d, env)
  progress = next(iter(d.progress.values()))
  assert progress.finished and progress.done == progress.total and not progress.failed


def test_repeated_refresh_does_not_double_count_saved_bytes(tmp_path, monkeypatch):
  d = daemon(tmp_path)
  area = d.state.add_area({"latitude": 36.1, "longitude": -115.1, "radiusKm": 2})
  d.state.mark_refreshed(area, d.wall() - navtilesd.AREA_REFRESH_SECONDS - 1)
  env = Environment(token="pk.test", network="wifi")
  run(d, env)
  saved = d.sizes["saved"]
  fetched = len(d.fetcher.session.urls)
  now = d.wall() + navtilesd.AREA_REFRESH_SECONDS + 1
  monkeypatch.setattr(d, "wall", lambda: now)
  monkeypatch.setattr(d.store, "age", lambda *args: navtilesd.AREA_REFRESH_SECONDS + 1)
  run(d, env)
  assert len(d.fetcher.session.urls) == fetched * 2
  assert d.sizes["saved"] == saved == d.store.size("saved")


def test_saved_cap_includes_promoted_tiles(tmp_path, monkeypatch):
  d = daemon(tmp_path)
  area = d.state.add_area({"latitude": 36.1, "longitude": -115.1, "radiusKm": 2})
  key = area_tiles(area)[0]
  d.store.write("cache", key, b"cached")
  monkeypatch.setattr(navtilesd, "SAVED_MAX_BYTES", 5)
  env = Environment(token="pk.test", network="wifi")
  d.plan(env)
  for _ in range(3):
    assert d.step(env) is None
  assert d.no_space and not d.store.has("saved", key)
  assert not d.fetcher.session.urls


def test_saved_coverage_wraps_at_date_line():
  area = {"latitude": 0., "longitude": 179.99, "radiusKm": 25}
  assert all(navtilesd.covered(key, [area]) for key in area_tiles(area))


def test_area_planning_is_bounded(tmp_path):
  with pytest.raises(ValueError, match="smaller"):
    validate_area({"latitude": 84, "longitude": 0, "radiusKm": 200})
  state = OfflineState(tmp_path)
  for _ in range(2):
    state.add_area({"latitude": 60, "longitude": 0, "radiusKm": 200})
  with pytest.raises(ValueError, match="smaller"):
    state.add_area({"latitude": 60, "longitude": 0, "radiusKm": 200})


def test_refresh_cannot_resurrect_concurrently_deleted_area(tmp_path, monkeypatch):
  import threading
  from openpilot.starpilot.navigation import offline_roads
  state = OfflineState(tmp_path)
  area = state.add_area({"latitude": 36.1, "longitude": -115.1, "radiusKm": 2})
  entered, release, deleted = threading.Event(), threading.Event(), threading.Event()
  write = offline_roads.write_json
  def blocked_write(*args):
    entered.set()
    assert release.wait(2)
    write(*args)
  monkeypatch.setattr(offline_roads, "write_json", blocked_write)
  refresh = threading.Thread(target=state.mark_refreshed, args=(area, 1))
  def remove():
    state.delete_area(area["id"])
    deleted.set()
  deletion = threading.Thread(target=remove)
  refresh.start()
  try:
    assert entered.wait(1)
    deletion.start()
    assert not deleted.wait(.05), "delete must serialize with the refresh write"
  finally:
    release.set()
    refresh.join(2)
    if deletion.ident is not None:
      deletion.join(2)
  assert deleted.is_set() and state.areas() == []


def test_area_validation_and_estimate(tmp_path):
  with pytest.raises(ValueError):
    validate_area({"latitude": 36, "longitude": -115, "radiusKm": 500})
  with pytest.raises(ValueError):
    validate_area({"latitude": float("nan"), "longitude": -115, "radiusKm": 10})
  area = validate_area({"latitude": 36.11471234, "longitude": -115.1, "radiusKm": 10, "name": "  Las   Vegas "})
  assert area["name"] == "Las Vegas" and area["latitude"] == 36.114712
  guess = estimate(36.1, -115.1, 10)
  actual = len(area_tiles({**area, "latitude": 36.1}))
  assert 0.7 < guess["tiles"] / actual < 1.4
  state = OfflineState(tmp_path)
  with pytest.raises(ValueError):
    state.delete_area("../../etc")
  with pytest.raises(ValueError):
    state.set_settings({"saveDriven": "yes"})
  (tmp_path / "areas").mkdir(exist_ok=True)
  (tmp_path / "areas" / ("a" * 32 + ".json")).write_text(json.dumps({"latitude": 1}))
  assert state.areas() == []


def test_usage_resets_each_month(tmp_path):
  now = [1_790_000_000.0]
  usage = Usage(tmp_path, clock=lambda: now[0])
  for _ in range(30):
    usage.add()
  assert usage.snapshot()["tiles"] == 30
  assert Usage(tmp_path, clock=lambda: now[0]).snapshot()["tiles"] == 25, "persisted every 25 requests"
  now[0] += 40 * 86400
  assert usage.snapshot()["tiles"] == 0


def test_status_reports_sizes_and_failure(tmp_path):
  d = daemon(tmp_path, Session(status=401))
  env = Environment(token="pk.test", network="wifi", onroad=True, map_enabled=True, position=(36.1, -115.1))
  run(d, env)
  d.status(env, force=True)
  status = OfflineState(tmp_path).status()
  assert status is not None
  assert status["failure"] == "key" and status["hasKey"] and status["network"] == "wifi"
  assert set(status["bytes"]) == {"saved", "driven", "cache"}
  assert navtilesd.FAILURE_KEY == "key"


def test_main_subscribes_to_registered_gps_carriers(monkeypatch):
  import sys
  from types import SimpleNamespace
  from openpilot.cereal.services import SERVICE_LIST
  from openpilot.starpilot.navigation import navtilesd, owner

  class SubscriptionsChecked(Exception):
    pass

  def submaster(services):
    assert len(services) == len(set(services))
    assert set(services) <= SERVICE_LIST.keys()
    assert {"gpsLocationExternal", "gpsLocation", "starpilotCarState"} <= set(services)
    assert "phoneGps" not in services
    assert services.count("starpilotNavigation") == 1
    assert "deviceState" in services
    raise SubscriptionsChecked

  messaging = SimpleNamespace(SubMaster=submaster)
  from openpilot import cereal
  monkeypatch.setattr(cereal, "messaging", messaging, raising=False)
  monkeypatch.setitem(sys.modules, "openpilot.cereal.messaging", messaging)
  monkeypatch.setattr(navtilesd, "TileDaemon", lambda: None)
  monkeypatch.setattr(owner, "NavigationOwner", lambda: None)
  monkeypatch.setattr(navtilesd.os, "nice", lambda _: None)
  with pytest.raises(SubscriptionsChecked):
    navtilesd.main()


def test_environment_passes_the_google_key_only_while_google_is_chosen():
  from types import SimpleNamespace
  from unittest.mock import patch
  from openpilot.starpilot.navigation import navtilesd
  class SubMaster:
    valid = {"deviceState": False, "starpilotNavigation": False}
    logMonoTime = {"deviceState": 0}

    def __getitem__(self, name):
      return None
  sm = SubMaster()
  settings = {"token": "pk.test", "mapProvider": "google", "googleKey": "AIzaSyFixtureKey_0123456789abcdef"}
  owner = SimpleNamespace(read=lambda: dict(settings))
  with patch("openpilot.starpilot.navigation.runtime.location", return_value=None):
    assert navtilesd._environment(sm, owner, True, 0).google_key == settings["googleKey"]
    settings["mapProvider"] = "mapbox"
    env = navtilesd._environment(sm, owner, True, 0)
  assert env.google_key == "" and env.token == "pk.test"
