import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import requests

from openpilot.starpilot.navigation import google_routes as gr
from openpilot.starpilot.navigation.owner import NavigationOwner
from openpilot.starpilot.navigation.runtime import RouteRuntime
from openpilot.starpilot.navigation.tests.test_route_runtime import Executor, configured

KEY = "AIzaSyFixtureKey_0123456789abcdef"
WALL = 1_762_600_000.0
LINE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"   # Google's documented example polyline


def google_route(duration="150s"):
  return {"distanceMeters": 1200, "duration": duration, "polyline": {"encodedPolyline": LINE}, "legs": [{"steps": [
    {"distanceMeters": 500, "staticDuration": "40s", "navigationInstruction": {"maneuver": "DEPART", "instructions": "Head north on Main St"}},
    {"distanceMeters": 400, "staticDuration": "30s", "navigationInstruction": {"maneuver": "TURN_SLIGHT_RIGHT", "instructions": "Slight right onto Oak St"}},
    {"distanceMeters": 300, "staticDuration": "30s", "navigationInstruction": {"maneuver": "RAMP_RIGHT", "instructions": "Take exit 12 toward Airport"}},
  ]}]}


class Http:
  def __init__(self, status=200, body=None, error=None):
    self.calls = []
    self.status, self.error = status, error
    self.body = json.dumps({"routes": [google_route(), google_route("200s")]}).encode() if body is None else body

  @contextmanager
  def post(self, url, **kwargs):
    self.calls.append((url, kwargs))
    if self.error is not None:
      raise self.error
    yield SimpleNamespace(status_code=self.status, iter_content=lambda size: iter([self.body]))


def budget(tmp_path):
  return gr.GoogleBudget(tmp_path, clock=lambda: WALL, file="routes-usage.json", month_free=gr.FREE_ROUTES, day_free=None)


def essentials(tmp_path):
  return gr.GoogleBudget(tmp_path, clock=lambda: WALL, file="routes-essentials-usage.json", month_free=gr.FREE_ESSENTIAL_ROUTES, day_free=None)


def engine(http, tmp_path):
  return gr.GoogleRouteEngine(http, budget(tmp_path), essentials(tmp_path))


def test_polyline_decodes_googles_example():
  points = [value for point in gr.decode_polyline(LINE) for value in point]
  assert points == pytest.approx([-120.2, 38.5, -120.95, 40.7, -126.453, 43.252])
  with pytest.raises(gr.ValidationError):
    gr.decode_polyline("_p~iF~ps|")


def encode(points):
  out, last = [], (0, 0)
  for latitude, longitude in points:
    current = (round(latitude * 1e5), round(longitude * 1e5))
    for delta in (current[0] - last[0], current[1] - last[1]):
      value = ~(delta << 1) if delta < 0 else delta << 1
      while value >= 0x20:
        out.append(chr((0x20 | (value & 0x1f)) + 63))
        value >>= 5
      out.append(chr(value + 63))
    last = current
  return "".join(out)


def test_long_detailed_route_is_thinned_not_refused():
  points = [(37.0 + index * 1e-5, -122.0) for index in range(gr.MAX_POINTS * 2 + 7)]
  route = dict(google_route(), polyline={"encodedPolyline": encode(points)})
  shaped = gr.mapbox_shape(route)["geometry"]["coordinates"]
  assert len(shaped) <= gr.MAX_POINTS and shaped[0] == [-122.0, 37.0] and shaped[-1] == pytest.approx([-122.0, points[-1][0]])
  gr.NavigationRoute(gr.mapbox_shape(route))


def test_google_route_becomes_a_navigation_route_with_traffic_times():
  shaped = gr.mapbox_shape(google_route())
  steps = shaped["legs"][0]["steps"]
  assert [step["maneuver"]["type"] for step in steps] == ["depart", "turn", "off ramp", "arrive"]
  assert steps[1]["maneuver"]["modifier"] == "slight right"
  assert sum(step["duration"] for step in steps) == pytest.approx(150)
  route = gr.NavigationRoute(shaped)
  assert route.steps[0].text == "Slight right onto Oak St" and route.steps[-2].text == "Arrive at your destination"
  assert route.total_duration == 150 and route.total_distance == 1200


def test_moving_reroute_is_traffic_aware_with_heading_and_key_in_a_header(tmp_path):
  http = Http()
  route = engine(http, tmp_path).fetch(KEY, (-122.42, 37.77), {"latitude": 37.8, "longitude": -122.4}, 365.0, ("toll", "ferry"))
  url, kwargs = http.calls[0]
  assert url == gr.ROUTES_URL and KEY not in url
  assert kwargs["headers"] == {"X-Goog-Api-Key": KEY, "X-Goog-FieldMask": gr.FIELD_MASK}
  body = kwargs["json"]
  assert body["routingPreference"] == "TRAFFIC_AWARE" and body["origin"]["location"]["heading"] == 5
  assert body["polylineQuality"] == "HIGH_QUALITY"
  assert body["routeModifiers"] == {"avoidTolls": True, "avoidFerries": True}
  assert len(route.alternatives) == 2 and route.alternatives[1].total_duration == 200 and route.traffic_aware
  assert budget(tmp_path).snapshot()["monthUsed"] == 1 and essentials(tmp_path).snapshot()["monthUsed"] == 0


def test_trip_start_is_traffic_aware_without_heading(tmp_path):
  http = Http()
  route = engine(http, tmp_path).fetch(KEY, (-122.42, 37.77), {"latitude": 37.8, "longitude": -122.4})
  body = http.calls[0][1]["json"]
  assert body["routingPreference"] == "TRAFFIC_AWARE" and "heading" not in body["origin"]["location"]
  assert body["computeAlternativeRoutes"] and body["polylineQuality"] == "HIGH_QUALITY"
  assert route.traffic_aware
  assert budget(tmp_path).snapshot()["monthUsed"] == 1 and essentials(tmp_path).snapshot()["monthUsed"] == 0


def test_off_route_loop_switches_to_free_routes_for_the_hour(tmp_path):
  clock = [1000.]
  http = Http()
  routes = gr.GoogleRouteEngine(http, budget(tmp_path), essentials(tmp_path), clock=lambda: clock[0])
  for _ in range(gr.PRO_ROUTES_PER_HOUR):
    assert routes.fetch(KEY, (0., 0.), {"latitude": 0., "longitude": .01}, 90.).traffic_aware
  extra = routes.fetch(KEY, (0., 0.), {"latitude": 0., "longitude": .01}, 90.)
  assert not extra.traffic_aware and http.calls[-1][1]["json"]["routingPreference"] == "TRAFFIC_UNAWARE"
  assert "heading" not in http.calls[-1][1]["json"]["origin"]["location"]
  clock[0] += 3601
  assert routes.fetch(KEY, (0., 0.), {"latitude": 0., "longitude": .01}, 90.).traffic_aware


def test_routes_fall_back_to_essentials_when_traffic_requests_are_used_up(tmp_path):
  spent = budget(tmp_path)
  assert spent.spend(spent.month_limit)
  http = Http()
  engine(http, tmp_path).fetch(KEY, (0., 0.), {"latitude": 0., "longitude": .01}, 90.)
  body = http.calls[0][1]["json"]
  assert body["routingPreference"] == "TRAFFIC_UNAWARE" and "heading" not in body["origin"]["location"]


def test_traffic_request_asks_only_for_time_and_distance(tmp_path):
  http = Http(body=b'{"routes": [{"duration": "900s", "distanceMeters": 12000}]}')
  assert engine(http, tmp_path).traffic(KEY, (0., 0.), {"latitude": 0., "longitude": .1}, 90.) == (900., 12000.)
  kwargs = http.calls[0][1]
  assert kwargs["headers"]["X-Goog-FieldMask"] == gr.TRAFFIC_MASK and kwargs["json"]["routingPreference"] == "TRAFFIC_AWARE"
  assert not kwargs["json"]["computeAlternativeRoutes"] and budget(tmp_path).snapshot()["monthUsed"] == 1
  with pytest.raises(gr.GoogleRoutesFailed):
    engine(Http(body=b'{"routes": [{"duration": "bad"}]}'), tmp_path).traffic(KEY, (0., 0.), {"latitude": 0., "longitude": .1})


@pytest.mark.parametrize("http, failure", [
  (Http(403, b'{"error": {"message": "Routes API has not been used in project 1"}}'), "disabled"),
  (Http(400, b'{"error": {"message": "API key not valid"}}'), "key"),
  (Http(error=requests.ConnectionError()), "network"),
  (Http(body=b'{"routes": []}'), "server"),
  (Http(body=b'{"routes": [{"duration": "1s"}]}'), "server"),
])
def test_fetch_failures_say_why(tmp_path, http, failure):
  with pytest.raises(gr.GoogleRoutesFailed) as raised:
    engine(http, tmp_path).fetch(KEY, (0., 0.), {"latitude": 0., "longitude": .01})
  assert raised.value.failure == failure


def test_fetch_stops_at_the_free_allowance(tmp_path):
  spent = essentials(tmp_path)
  assert spent.spend(spent.month_limit)
  assert budget(tmp_path).spend(budget(tmp_path).month_limit)
  http = Http()
  with pytest.raises(gr.GoogleRoutesFailed) as raised:
    engine(http, tmp_path).fetch(KEY, (0., 0.), {"latitude": 0., "longitude": .01})
  assert raised.value.failure == "budget" and not http.calls
  assert spent.snapshot()["monthLimit"] == 9900 and spent.snapshot()["dayLimit"] is None
  assert budget(tmp_path).snapshot()["monthLimit"] == 4950 == budget(tmp_path).snapshot()["monthUsed"]


def test_key_test_reports_routes_plainly(tmp_path):
  http = Http()
  assert gr.test_routes(KEY, http, essentials(tmp_path))["ok"]
  assert http.calls[0][1]["json"]["routingPreference"] == "TRAFFIC_UNAWARE"
  failed = gr.test_routes(KEY, Http(error=requests.Timeout()), budget(tmp_path))
  assert not failed["ok"] and "Mapbox" not in failed["message"]
  assert "Routes API is off" in gr.test_routes(KEY, Http(403, b"SERVICE_DISABLED"), budget(tmp_path))["message"]


class RecordingExecutor(Executor):
  def __init__(self):
    super().__init__()
    self.calls = []

  def submit(self, *args):
    self.calls.append(args)
    return super().submit(*args)


def google_runtime(tmp_path, google_fetch):
  owner = configured(tmp_path)
  revision = owner.read()["revision"]
  owner.configure({"routeProvider": "google", "googleKey": KEY}, revision, True)
  mapbox = SimpleNamespace(calls=[])
  def mapbox_fetch(*args):
    mapbox.calls.append(args)
    return "mapbox route"
  mapbox.fetch = mapbox_fetch
  executor = RecordingExecutor()
  runtime = RouteRuntime(owner, engine=mapbox, executor=executor, google_engine=SimpleNamespace(fetch=google_fetch))
  return owner, runtime, executor, mapbox


def test_google_provider_routes_with_google_and_falls_back_to_mapbox(tmp_path, monkeypatch):
  monkeypatch.setattr(NavigationOwner, "_is_metric", lambda self: True)
  def refuse(*args):
    raise gr.GoogleRoutesFailed("budget")
  owner, runtime, executor, mapbox = google_runtime(tmp_path, refuse)
  runtime.update(10, (10, (0., 0.), 10., 90.), 1)
  fetch, credentials, coordinates = executor.calls[0][:3]
  assert credentials == ("pk.test", KEY) and coordinates == (0., 0.)
  assert fetch(*executor.calls[0][1:]) == "mapbox route" and mapbox.calls[0][0] == "pk.test"
  assert "used up" in owner.snapshot()["googleRoutes"]["failure"]
  runtime.google_engine.fetch = lambda *args: "google route"
  assert fetch(*executor.calls[0][1:]) == "google route"
  assert owner.snapshot()["googleRoutes"]["failure"] is None


def test_google_route_guides_and_reaches_the_car_cluster(tmp_path, monkeypatch):
  monkeypatch.setattr(NavigationOwner, "_is_metric", lambda self: True)
  owner, runtime, executor, _ = google_runtime(tmp_path, None)
  points = [(0.0, index * 1e-4) for index in range(101)]
  route = gr.NavigationRoute(gr.mapbox_shape(dict(google_route(), polyline={"encodedPolyline": encode(points)})))
  route.alternatives = [route]
  runtime.update(10, (10, (0., 0.), 10., 90.), 1)
  executor.jobs[0].set_result(route)
  result = runtime.update(11, (11, (.002, 0.), 10., 90.), 1)
  assert result["status"] == "guiding" and result["instruction"]["text"] == "Slight right onto Oak St"
  assert result["nextManeuver"]["maneuverType"] == "off ramp"
  assert len(owner.snapshot()["alternatives"]) == 1
  from openpilot.starpilot.system.starport.nav_status import EVENT_OFF_RAMP, EVENT_SLIGHT_TURN, maneuver_event
  assert maneuver_event(route.steps[0].maneuver_type, route.steps[0].modifier)[0] == EVENT_SLIGHT_TURN
  assert maneuver_event(route.steps[1].maneuver_type, route.steps[1].modifier)[0] == EVENT_OFF_RAMP
  for kind, side in gr.MANEUVERS.values():
    maneuver_event(kind, side)


def test_mapbox_provider_never_asks_google(tmp_path, monkeypatch):
  monkeypatch.setattr(NavigationOwner, "_is_metric", lambda self: True)
  owner, runtime, executor, mapbox = google_runtime(tmp_path, None)
  owner.configure({"routeProvider": "mapbox"}, owner.read()["revision"], True)
  runtime.update(10, (10, (0., 0.), 10., 90.), 1)
  assert executor.calls[0][0] == mapbox.fetch and executor.calls[0][1] == "pk.test"


def test_traffic_corrects_the_arrival_time_of_a_free_route(tmp_path, monkeypatch):
  monkeypatch.setattr(NavigationOwner, "_is_metric", lambda self: True)
  owner, runtime, executor, _ = google_runtime(tmp_path, None)
  runtime.google_engine.traffic = lambda *args: None
  route = gr.NavigationRoute({"distance": 5000., "duration": 500., "geometry": {"coordinates": [[0., 0.], [.045, 0.]]}, "legs": [{"steps": [
    {"distance": 5000., "duration": 500., "maneuver": {"type": "depart"}}, {"distance": 0., "duration": 0., "maneuver": {"type": "arrive"}}]}]})
  route.traffic_aware, route.alternatives = False, [route]
  runtime.update(10, (10, (0., 0.), 1., 90.), 1)
  assert executor.calls[0][4] is None, "a trip that starts slowly asks without the car's heading"
  executor.jobs[0].set_result(route)
  first = runtime.update(11, (11, (.0045, 0.), 10., 90.), 1)
  assert first["instruction"]["remainingDurationSeconds"] == pytest.approx(450., rel=.01)
  traffic = executor.calls[1]
  assert traffic[0] == runtime.google_engine.traffic and traffic[1] == KEY and traffic[2] == (.0045, 0.) and traffic[4] == 90.
  executor.jobs[1].set_result((900., 4500.))
  corrected = runtime.update(12, (12, (.0045, 0.), 10., 90.), 1)
  assert corrected["instruction"]["remainingDurationSeconds"] == pytest.approx(900., rel=.01)
  assert len(executor.jobs) == 2, "the next traffic request waits its turn"
  runtime.update(12 + 451_000_000_000, (12 + 451_000_000_000, (.0045, 0.), 10., 90.), 1)
  executor.jobs[2].set_result((300., 20000.))
  ignored = runtime.update(13 + 451_000_000_000, (13 + 451_000_000_000, (.0045, 0.), 10., 90.), 1)
  assert ignored["instruction"]["remainingDurationSeconds"] == pytest.approx(900., rel=.01), "an answer for another path is ignored"
