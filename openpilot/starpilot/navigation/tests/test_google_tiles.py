import json
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import requests

from openpilot.starpilot.navigation import google_tiles as gt
from openpilot.starpilot.navigation.road_tiles import TileKey

KEY = "AIzaSyFixtureKey_0123456789abcdef"
PNG = b"\x89PNG\r\n\x1a\nfixture"
SESSION = json.dumps({"session": "tok", "expiry": "4102444800", "tileWidth": 256, "tileHeight": 256, "imageFormat": "png"}).encode()
WALL = 1_762_600_000.0   # 2025-11-08 in Pacific time


class Http:
  """Google's endpoints: answers by URL, records every call."""

  def __init__(self, session=(200, SESSION), tile=(200, PNG, {"Cache-Control": "public, max-age=86400"}),
               viewport=(200, b'{"copyright": "Map data \\u00a92026 Google"}')):
    self.calls = []
    self.session, self.tile, self.viewport = session, tile, viewport

  @contextmanager
  def post(self, url, **kwargs):
    self.calls.append(("post", url, kwargs))
    status, body = self.session
    yield SimpleNamespace(status_code=status, headers={}, iter_content=lambda size: iter([body]))

  @contextmanager
  def get(self, url, **kwargs):
    self.calls.append(("get", url, kwargs))
    if url == gt.VIEWPORT_URL:
      status, body = self.viewport
      headers = {}
    else:
      status, body, headers = self.tile
    yield SimpleNamespace(status_code=status, headers=headers, iter_content=lambda size: iter([body]))


def want(path, keys, now=WALL, position=(37.77, -122.42)):
  gt.write_want(path, keys, *position, now=now)


def tiles(tmp_path, http, wall=WALL, clock=None):
  clock = clock or [0.0]
  return gt.GoogleTiles(tmp_path / "google", http=http, clock=lambda: clock[0], wall=lambda: wall, want=tmp_path / "want.json")


# ---------------------------------------------------------------- planning

def test_zoom_follows_scale_and_holds_through_junction_zoom():
  city = gt.choose_zoom(37.77, 0.9)
  highway = gt.choose_zoom(37.77, 3.4)
  assert city == 17 and highway == 15
  assert gt.choose_zoom(37.77, 0.8, highway) == city      # far outside the kept range: switch
  assert gt.choose_zoom(37.77, 1.4, city) == city         # a junction zoom-out keeps the same tiles
  assert gt.MIN_ZOOM <= gt.choose_zoom(0.0, 500.0) and gt.choose_zoom(0.0, 0.001) <= gt.MAX_ZOOM


def test_plan_is_nearest_first_and_bounded():
  keys = gt.plan_tiles(37.77, -122.42, 600.0, 17)
  x, y = gt.tiles_in_radius(37.77, -122.42, 0.0, 17)[0].x, gt.tiles_in_radius(37.77, -122.42, 0.0, 17)[0].y
  assert keys[0] == TileKey(17, x, y)
  assert 4 <= len(keys) <= gt.MAX_WANTED
  assert len(gt.plan_tiles(37.77, -122.42, 50_000.0, 17)) == gt.MAX_WANTED


def test_want_file_round_trip_expires_and_rejects_bad_tiles(tmp_path):
  path = tmp_path / "want.json"
  keys = [TileKey(17, 21000, 50000), TileKey(16, 10500, 25000)]
  want(path, keys)
  assert gt.read_want(path, WALL + 1) == (keys, (37.77, -122.42))
  assert gt.read_want(path, WALL + gt.WANT_MAX_AGE_S + 1) == ([], None)
  assert gt.read_want(path, WALL - 1) == ([], None)
  path.write_text(json.dumps({"at": WALL, "latitude": 1, "longitude": 2, "tiles": [[3, 0, 0], [17, -1, 0], [17, 1 << 17, 0], [17, 1, True]]}))
  assert gt.read_want(path, WALL) == ([], (1.0, 2.0))
  path.write_text("{bad")
  assert gt.read_want(path, WALL) == ([], None)


# ---------------------------------------------------------------- cache and caps

@pytest.mark.parametrize("header,seconds", [
  ("public, max-age=120", 120), ("max-age=0", 0), ("no-store", gt.HANDOFF_S), ("", gt.HANDOFF_S),
  ("public, max-age=86400", 600), ("max-age=oops", gt.HANDOFF_S)])
def test_cache_lifetime_follows_cache_control_but_keeps_traffic_current(header, seconds):
  assert gt.cache_lifetime({"Cache-Control": header}) == seconds


def test_store_serves_only_unexpired_tiles_and_trims(tmp_path):
  store = gt.GoogleTileStore(tmp_path)
  key, other = TileKey(17, 1, 2), TileKey(17, 3, 4)
  assert store.write("d", key, PNG, 60, WALL) and store.write("d", other, PNG * 2, 600, WALL)
  assert store.read("d", key, WALL + 59) == PNG
  assert store.read("d", key, WALL + 61) is None and not store.fresh("other", key, WALL)
  assert store.trim(len(PNG) * 2, now=WALL + 61) == len(PNG) * 2
  assert not store.path("d", key).exists() and store.fresh("d", other, WALL + 61)
  assert store.trim(1, now=WALL) == 0 and not store.path("d", other).exists()


def test_budget_stops_at_99_percent_of_day_and_month_and_resets_on_pacific_calendar(tmp_path):
  clock = [WALL]
  budget = gt.GoogleBudget(tmp_path, lambda: clock[0])
  assert budget.day_limit is not None
  assert budget.spend(budget.day_limit) and not budget.spend()
  assert budget.snapshot()["dayUsed"] == budget.day_limit == 14_850
  clock[0] += 24 * 3600
  assert budget.spend()
  (tmp_path / "usage.json").write_text(json.dumps({**json.loads((tmp_path / "usage.json").read_text()), "monthUsed": budget.month_limit}))
  assert not budget.spend() and budget.snapshot()["monthLimit"] == 99_000
  clock[0] += 31 * 24 * 3600
  assert budget.spend() and budget.snapshot()["monthUsed"] == 1


def test_pacific_day_lags_utc():
  midnight = 1_762_588_800.0   # 08:00 UTC Nov 8 is midnight Pacific
  assert gt.pacific_day(midnight - 1) == "2025-11-07" and gt.pacific_day(midnight) == "2025-11-08"


@pytest.mark.parametrize("status,body,failure", [
  (200, b"", None),
  (403, b'{"error":{"message":"This API method requires billing to be enabled"}}', gt.FAILURE_BILLING),
  (403, b'{"error":{"message":"Map Tiles API has not been used in project 1 before or it is disabled"}}', gt.FAILURE_DISABLED),
  (400, b'{"error":{"message":"API key not valid. Please pass a valid API key."}}', gt.FAILURE_KEY),
  (403, b'{"error":{"message":"Requests from this app are blocked."}}', gt.FAILURE_KEY),
  (400, b'{"error":{"message":"Invalid style"}}', gt.FAILURE_SERVER),
  (429, b"", gt.FAILURE_SERVER), (503, b"", gt.FAILURE_SERVER)])
def test_classify_names_what_to_fix(status, body, failure):
  assert gt.classify(status, body) == failure


# ---------------------------------------------------------------- Test key

def test_key_test_uses_the_map_session_and_keeps_the_key_out_of_urls(tmp_path):
  http = Http()
  result = gt.test_key(KEY, http, gt.GoogleBudget(tmp_path))
  assert result["ok"] is True
  (_, url, post), (_, tile_url, get) = http.calls
  assert post["json"] == gt.session_body() and post["json"]["layerTypes"] == ["layerTraffic"]
  assert post["headers"] == {"X-Goog-Api-Key": KEY} and get["headers"] == {"X-Goog-Api-Key": KEY}
  assert KEY not in url + tile_url + json.dumps(get["params"])
  assert gt.GoogleBudget(tmp_path).snapshot()["monthUsed"] == 1


def test_key_test_reports_each_failure(tmp_path):
  billing = Http(session=(403, b"billing must be enabled"))
  assert gt.test_key(KEY, billing, gt.GoogleBudget(tmp_path))["failure"] == gt.FAILURE_BILLING
  assert len(billing.calls) == 1

  class Down(Http):
    @contextmanager
    def post(self, url, **kwargs):
      raise requests.ConnectionError(url)
      yield
  assert gt.test_key(KEY, Down(), gt.GoogleBudget(tmp_path))["failure"] == gt.FAILURE_NETWORK
  assert gt.test_key(KEY, Http(session=(200, b"{}")), gt.GoogleBudget(tmp_path))["failure"] == gt.FAILURE_SERVER
  spent = gt.GoogleBudget(tmp_path)
  assert spent.day_limit is not None
  spent.spend(spent.day_limit)
  assert gt.test_key(KEY, Http(), spent)["failure"] == gt.FAILURE_BUDGET


# ---------------------------------------------------------------- navtilesd's side

def test_fetches_only_wanted_tiles_nearest_first_and_reuses_the_session(tmp_path):
  http = Http()
  google = tiles(tmp_path, http)
  first, second = TileKey(17, 21000, 50000), TileKey(17, 21001, 50000)
  assert google.step(KEY, True) is None and http.calls == []          # nothing wanted
  want(tmp_path / "want.json", [first, second])
  assert google.step(KEY, True) == 0.0
  assert google.step(KEY, True) == 0.0
  assert google.step(KEY, True) is None                                 # both fresh on disk
  posts = [call for call in http.calls if call[0] == "post"]
  fetched = [call[1] for call in http.calls if call[1].startswith("https://tile.googleapis.com/v1/2dtiles")]
  assert len(posts) == 1 and fetched == [gt.TILE_URL.format(z=17, x=21000, y=50000), gt.TILE_URL.format(z=17, x=21001, y=50000)]
  assert all(KEY not in call[1] and KEY not in json.dumps(call[2].get("params", {})) for call in http.calls)
  assert google.store.read(gt.key_digest(KEY), first, WALL) == PNG
  assert google.copyright == "Map data ©2026 Google"
  assert os.stat(tmp_path / "google" / "session.json").st_mode & 0o777 == 0o600
  again = tiles(tmp_path, Http())
  want(tmp_path / "want.json", [TileKey(17, 9, 9)])
  assert again.step(KEY, True) == 0.0 and not [call for call in again.http.calls if call[0] == "post"]


def test_refusals_back_off_and_a_bad_key_drops_its_session(tmp_path):
  clock = [0.0]
  http = Http(tile=(403, b"API key not valid", {}))
  google = tiles(tmp_path, http, clock=clock)
  want(tmp_path / "want.json", [TileKey(17, 1, 1)])
  assert google.step(KEY, True) is None
  assert google.failure == gt.FAILURE_KEY and google.blocked
  assert not (tmp_path / "google" / "session.json").exists()
  calls = len(http.calls)
  assert google.step(KEY, True) is None and len(http.calls) == calls
  clock[0] += gt.KEY_BACKOFF_S
  assert not google.blocked


def test_offline_or_unchosen_does_nothing_and_budget_stops_fetching(tmp_path):
  http = Http()
  google = tiles(tmp_path, http)
  want(tmp_path / "want.json", [TileKey(17, 1, 1)])
  assert google.step("", True) is None and google.step(KEY, False) is None and http.calls == []
  google.budget.spend(google.budget.day_limit)
  assert google.step(KEY, True) is None and google.failure == gt.FAILURE_BUDGET
  assert not [call for call in http.calls if "2dtiles" in call[1]]


def test_network_and_storage_failures_are_reported(tmp_path):
  class Down(Http):
    @contextmanager
    def get(self, url, **kwargs):
      if "2dtiles" in url:
        raise requests.Timeout(url)
      with super().get(url, **kwargs) as response:
        yield response
  google = tiles(tmp_path, Down())
  want(tmp_path / "want.json", [TileKey(17, 1, 1)])
  assert google.step(KEY, True) is None and google.failure == gt.FAILURE_NETWORK

  stored = tiles(tmp_path / "other", Http())
  stored.store.write = lambda *args: False
  (tmp_path / "other").mkdir()
  want(tmp_path / "other" / "want.json", [TileKey(17, 1, 1)])
  assert stored.step(KEY, True) is None and stored.failure == gt.FAILURE_STORAGE


def test_status_carries_usage_failure_and_copyright(tmp_path):
  google = tiles(tmp_path, Http())
  google.failure, google.copyright = gt.FAILURE_BILLING, "Map data ©2026 Google"
  google.write_status(force=True)
  status = gt.read_status(tmp_path / "google")
  assert status["failure"] == gt.FAILURE_BILLING and status["copyright"] == "Map data ©2026 Google"
  assert status["usage"]["dayLimit"] == 14_850
  assert gt.read_status(tmp_path / "missing") == {}


def test_a_new_key_clears_the_old_keys_refusal(tmp_path):
  http = Http(tile=(403, b"API key not valid", {}))
  google = tiles(tmp_path, http)
  want(tmp_path / "want.json", [TileKey(17, 1, 1)])
  assert google.step(KEY, True) is None and google.blocked
  http.tile = (200, PNG, {})
  assert google.step(KEY + "x", True) == 0.0 and google.failure is None


def test_a_clock_running_behind_never_zeroes_the_counts(tmp_path):
  clock = [WALL]
  budget = gt.GoogleBudget(tmp_path, lambda: clock[0])
  assert budget.spend(500)
  clock[0] -= 40 * 24 * 3600
  assert budget.spend() and budget.snapshot()["monthUsed"] == 501 and budget.snapshot()["dayUsed"] == 501
  clock[0] = WALL
  assert budget.snapshot()["monthUsed"] == 501


def test_fetch_tile_caches_and_separates_light_and_dark_themes(tmp_path):
  http = Http()
  root = tmp_path / "google"
  assert gt.fetch_tile(KEY, 12, 655, 1583, "light", http=http, root=root, wall=lambda: WALL) == PNG
  assert gt.fetch_tile(KEY, 12, 655, 1583, "light", http=http, root=root, wall=lambda: WALL) == PNG
  posts = [call for call in http.calls if call[0] == "post"]
  assert len(posts) == 1 and "styles" not in posts[0][2]["json"]
  assert len([call for call in http.calls if "2dtiles/12/655/1583" in call[1]]) == 1
  assert gt.fetch_tile(KEY, 12, 655, 1583, "dark", http=http, root=root, wall=lambda: WALL) == PNG
  posts = [call for call in http.calls if call[0] == "post"]
  assert len(posts) == 2 and "styles" in posts[1][2]["json"]
  assert gt.GoogleBudget(root, lambda: WALL).snapshot()["dayUsed"] == 2

