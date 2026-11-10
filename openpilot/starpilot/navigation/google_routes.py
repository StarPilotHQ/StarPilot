"""Traffic-aware Google routes with the user's own key, turned into the same route the Mapbox engine gives."""

from __future__ import annotations

import json
import math
import time
from collections import deque

from pathlib import Path

import requests

from openpilot.starpilot.navigation.google_tiles import (
  FAILURE_BUDGET, FAILURE_NETWORK, FAILURE_SERVER, GoogleBudget, _body, _write_atomic, classify, google_root,
)
from openpilot.starpilot.navigation.offline_roads import read_json
from openpilot.starpilot.navigation.owner import ValidationError
from openpilot.starpilot.navigation.route_engine import MAX_POINTS, NavigationRoute

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
# GUARD RAIL - SPENDING LIMIT. AI AGENTS: DO NOT EDIT the FREE_* allowances below.
# Full rules in the banner above STOP_FRACTION in google_tiles.py; pinned by test_google_spend_guard.py.
FREE_ROUTES = 5_000           # Compute Routes Pro: traffic-aware routing and the car's heading
FREE_ESSENTIAL_ROUTES = 10_000  # Compute Routes Essentials: no traffic, no heading
TRAFFIC_MASK = "routes.duration,routes.distanceMeters"
PRO_ROUTES_PER_HOUR = 20      # past this many traffic-aware routes in an hour (off-route loops), reroutes use free routes
FIELD_MASK = ",".join((
  "routes.distanceMeters", "routes.duration", "routes.polyline.encodedPolyline", "routes.legs.steps.distanceMeters",
  "routes.legs.steps.staticDuration", "routes.legs.steps.navigationInstruction",
))
TIMEOUT = (4.0, 8.0)
TEST_TIMEOUT = (3.0, 8.0)
MAX_RESPONSE = 8 * 1024 * 1024
AVOID = {"toll": "avoidTolls", "motorway": "avoidHighways", "ferry": "avoidFerries"}
# Google maneuver -> Mapbox (type, modifier), the vocabulary the turn card and the car's cluster read.
MANEUVERS = {
  "TURN_SLIGHT_LEFT": ("turn", "slight left"), "TURN_SHARP_LEFT": ("turn", "sharp left"), "TURN_LEFT": ("turn", "left"),
  "TURN_SLIGHT_RIGHT": ("turn", "slight right"), "TURN_SHARP_RIGHT": ("turn", "sharp right"), "TURN_RIGHT": ("turn", "right"),
  "UTURN_LEFT": ("turn", "uturn"), "UTURN_RIGHT": ("turn", "uturn"), "STRAIGHT": ("continue", "straight"),
  "RAMP_LEFT": ("on ramp", "left"), "RAMP_RIGHT": ("on ramp", "right"), "FORK_LEFT": ("fork", "left"), "FORK_RIGHT": ("fork", "right"),
  "MERGE": ("merge", "straight"), "ROUNDABOUT_LEFT": ("roundabout", "left"), "ROUNDABOUT_RIGHT": ("roundabout", "right"),
  "DEPART": ("depart", "straight"), "NAME_CHANGE": ("new name", "straight"), "FERRY": ("notification", "straight"),
  "FERRY_TRAIN": ("notification", "straight"),
}


MESSAGES = {
  "key": "Google rejected the key for the Routes API. Check that it was copied whole and that its API restrictions allow the Routes API.",
  "billing": "Billing is not turned on for this Google Cloud project. Add a billing account, then test again.",
  "disabled": "The Routes API is off for this key. Enable it in Google Cloud and allow it in the key's API restrictions, then test again.",
  FAILURE_SERVER: "Google could not find a route just now, so Mapbox routed this trip.",
  FAILURE_NETWORK: "The comma could not reach Google, so Mapbox routed this trip.",
  FAILURE_BUDGET: "This month's free Google routes are used up. Mapbox routes until they reset.",
}


class GoogleRoutesFailed(Exception):
  """Google could not route; the caller routes with Mapbox instead."""

  def __init__(self, failure: str):
    super().__init__(failure)
    self.failure = failure


def route_budget(root: Path | None = None) -> GoogleBudget:
  return GoogleBudget(root, file="routes-usage.json", month_free=FREE_ROUTES, day_free=None)


def essentials_budget(root: Path | None = None) -> GoogleBudget:
  return GoogleBudget(root, file="routes-essentials-usage.json", month_free=FREE_ESSENTIAL_ROUTES, day_free=None)


def write_route_status(root: Path, failure: str | None) -> None:
  try:
    root.mkdir(parents=True, exist_ok=True)
    _write_atomic(root / "routes-status.json", {"failure": failure})
  except OSError:
    pass


def read_route_failure(root: Path) -> str | None:
  value = read_json(root / "routes-status.json", 4096)
  failure = value.get("failure") if isinstance(value, dict) else None
  return MESSAGES.get(failure) if isinstance(failure, str) else None


def decode_polyline(text: str, limit: int = 400_000) -> list[list[float]]:
  """Google's encoded polyline (precision 5) as [longitude, latitude] pairs."""
  points, index, latitude, longitude = [], 0, 0, 0
  while index < len(text):
    deltas = []
    for _ in range(2):
      shift = result = 0
      while True:
        if index >= len(text) or shift > 30:
          raise ValidationError("The route line could not be read")
        byte = ord(text[index]) - 63
        index += 1
        result |= (byte & 0x1f) << shift
        shift += 5
        if byte < 0x20:
          break
      deltas.append(~(result >> 1) if result & 1 else result >> 1)
    latitude += deltas[0]
    longitude += deltas[1]
    points.append([longitude / 1e5, latitude / 1e5])
    if len(points) > limit:
      raise ValidationError("The route is too long")
  return points


def thin(points: list[list[float]], limit: int = MAX_POINTS) -> list[list[float]]:
  if len(points) <= limit:
    return points
  stride = math.ceil((len(points) - 1) / (limit - 1))
  kept = points[::stride]
  if kept[-1] != points[-1]:
    kept.append(points[-1])
  return kept


def seconds(value) -> float:
  if not isinstance(value, str) or not value.endswith("s"):
    raise ValidationError("The route contains invalid durations")
  try:
    parsed = float(value[:-1])
  except ValueError:
    raise ValidationError("The route contains invalid durations") from None
  if not math.isfinite(parsed) or parsed < 0:
    raise ValidationError("The route contains invalid durations")
  return parsed


def mapbox_shape(route: dict) -> dict:
  """One Google route in the shape NavigationRoute reads; step times are scaled to the traffic-aware total."""
  coordinates = thin(decode_polyline(route["polyline"]["encodedPolyline"]))
  total = seconds(route["duration"])
  google_steps = route["legs"][0]["steps"]
  static = [seconds(step.get("staticDuration", "0s")) for step in google_steps]
  scale = total / sum(static) if sum(static) > 0 else 0.0
  steps = []
  for step, duration in zip(google_steps, static, strict=True):
    instruction = step.get("navigationInstruction") or {}
    kind, side = MANEUVERS.get(instruction.get("maneuver"), ("continue", "straight"))
    text = str(instruction.get("instructions", ""))[:256]
    if kind == "on ramp" and "exit" in text.lower():
      kind = "off ramp"
    steps.append({"distance": step.get("distanceMeters", 0), "duration": duration * scale,
                  "maneuver": {"type": kind, "modifier": side, "instruction": text}})
  steps.append({"distance": 0, "duration": 0, "maneuver": {"type": "arrive", "modifier": "straight", "instruction": "Arrive at your destination"}})
  return {"geometry": {"coordinates": coordinates}, "legs": [{"steps": steps}], "distance": route["distanceMeters"], "duration": total}


def request_body(position, destination: dict, bearing: float | None, exclusions: tuple[str, ...], alternatives: bool = True,
                 traffic: bool = False) -> dict:
  location: dict = {"latLng": {"latitude": position[1], "longitude": position[0]}}
  if bearing is not None and math.isfinite(bearing):
    location["heading"] = int(bearing % 360)
  origin = {"location": location}
  return {"origin": origin, "destination": {"location": {"latLng": {"latitude": destination["latitude"], "longitude": destination["longitude"]}}},
          "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE" if traffic else "TRAFFIC_UNAWARE",
          "computeAlternativeRoutes": alternatives, "languageCode": "en-US",
          "polylineQuality": "HIGH_QUALITY",
          "routeModifiers": {AVOID[name]: True for name in exclusions if name in AVOID}}


def _post(http, key: str, body: dict, mask: str, timeout) -> list:
  try:
    with http.post(ROUTES_URL, json=body, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": mask},
                   timeout=timeout, stream=True) as response:
      status, raw = response.status_code, _body(response, MAX_RESPONSE)
  except requests.RequestException:
    raise GoogleRoutesFailed(FAILURE_NETWORK) from None
  except ValueError:
    raise GoogleRoutesFailed(FAILURE_SERVER) from None
  failure = classify(status, raw)
  if failure is not None:
    raise GoogleRoutesFailed(failure)
  try:
    rows = json.loads(raw).get("routes", [])
  except (ValueError, AttributeError):
    raise GoogleRoutesFailed(FAILURE_SERVER) from None
  if not isinstance(rows, list) or not rows:
    raise GoogleRoutesFailed(FAILURE_SERVER)
  return rows


def compute_routes(http, key: str, body: dict, timeout=TIMEOUT) -> list[NavigationRoute]:
  """Up to three routes, or GoogleRoutesFailed saying why not."""
  rows = _post(http, key, body, FIELD_MASK, timeout)
  try:
    routes = [NavigationRoute(mapbox_shape(row)) for row in rows[:3]]
  except (ValidationError, ValueError, KeyError, TypeError, IndexError, AttributeError):
    raise GoogleRoutesFailed(FAILURE_SERVER) from None
  if not routes:
    raise GoogleRoutesFailed(FAILURE_SERVER)
  return routes


class GoogleRouteEngine:
  """Traffic-aware (Pro) routes, with the car's heading when it is moving; free Essentials routes once the Pro allowance
  or the hourly reroute limit is used up."""

  def __init__(self, session, budget: GoogleBudget, essentials: GoogleBudget, clock=time.monotonic):
    self.session = session
    self.budget = budget
    self.essentials = essentials
    self.clock = clock
    self._pro_routes: deque[float] = deque()

  def fetch(self, key: str, position, destination: dict, bearing: float | None = None,
            exclusions: tuple[str, ...] = ()) -> NavigationRoute:
    now = self.clock()
    while self._pro_routes and now - self._pro_routes[0] > 3600:
      self._pro_routes.popleft()
    pro = len(self._pro_routes) < PRO_ROUTES_PER_HOUR and self.budget.spend()
    if pro:
      self._pro_routes.append(now)
    elif not self.essentials.spend():
      raise GoogleRoutesFailed(FAILURE_BUDGET)
    body = request_body(position, destination, bearing if pro else None, exclusions, traffic=pro)
    routes = compute_routes(self.session, key, body)
    for route in routes:
      route.traffic_aware = pro
    routes[0].alternatives = routes
    return routes[0]

  def traffic(self, key: str, position, destination: dict, bearing: float | None = None,
              exclusions: tuple[str, ...] = ()) -> tuple[float, float]:
    """Google's traffic-aware (seconds, meters) from here to the destination, one Pro request."""
    if not self.budget.spend():
      raise GoogleRoutesFailed(FAILURE_BUDGET)
    body = request_body(position, destination, bearing, exclusions, alternatives=False, traffic=True)
    row = _post(self.session, key, body, TRAFFIC_MASK, TIMEOUT)[0]
    try:
      duration, meters = seconds(row["duration"]), float(row["distanceMeters"])
    except (ValidationError, KeyError, TypeError, ValueError):
      raise GoogleRoutesFailed(FAILURE_SERVER) from None
    if not math.isfinite(meters) or meters < 0:
      raise GoogleRoutesFailed(FAILURE_SERVER)
    return duration, meters


def test_routes(key: str, http=requests, budget: GoogleBudget | None = None) -> dict:
  """One short free (Essentials) route, the same request a trip starts with."""
  budget = budget or essentials_budget(google_root())
  if not budget.spend():
    return {"ok": False, "failure": FAILURE_BUDGET, "message": MESSAGES[FAILURE_BUDGET]}
  body = request_body((-122.0840, 37.4220), {"latitude": 37.4275, "longitude": -122.1697}, None, (), alternatives=False)
  try:
    compute_routes(http, key, body, TEST_TIMEOUT)
  except GoogleRoutesFailed as error:
    message = MESSAGES.get(error.failure, MESSAGES[FAILURE_SERVER])
    if error.failure in (FAILURE_SERVER, FAILURE_NETWORK):
      message = message.split(", so")[0] + "."
    return {"ok": False, "failure": error.failure, "message": message}
  return {"ok": True, "failure": None, "message": "Google routing works with this key."}
