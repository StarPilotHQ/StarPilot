"""Road-line tile downloader for the Android Auto map overlay and offline areas.

The only process that requests map tiles. The renderer reads tiles from disk
and never touches the network, so a slow or metered connection can never stall
a frame. Priorities, highest first:

  1. Tiles around the car and ahead of it, while the map overlay is on a layout.
  2. The active route's corridor.
  3. Offline areas, only on Wi-Fi or Ethernet.

Every request counts toward the month's Mapbox usage shown in Galaxy.
"""

from __future__ import annotations

from openpilot.starpilot.galaxy.companion import phone_uplink_active

import math
import os
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import requests

from openpilot.starpilot.navigation.google_tiles import GoogleTiles
from openpilot.starpilot.navigation.offline_roads import (
  AREA_REFRESH_SECONDS, CACHE_MAX_BYTES, DRIVEN_MAX_BYTES, SAVED_MAX_BYTES, OfflineState, TileStore, Usage, area_tiles,
)
from openpilot.starpilot.navigation.road_tiles import (
  DATA_ZOOM, MAX_DOWNLOAD_BYTES, MVT_URL, TileFormatError, TileKey, corridor_tiles, decode_mvt_roads, encode_road_tile,
  meters_per_tile, tiles_in_radius, world_xy,
)

NEAR_RADIUS_M = 2500.0
AHEAD_M = 6000.0
REQUEST_TIMEOUT = (4.0, 10.0)
NETWORK_BACKOFF_S, SERVER_BACKOFF_S, KEY_BACKOFF_S = 15.0, 30.0, 300.0
AREA_INTERVAL_S = 0.12          # at most ~8 offline-area tiles a second
STATUS_INTERVAL_S = 2.0
TRIM_INTERVAL_S = 300.0
LAYOUT_CHECK_S = 15.0
FAILURE_NETWORK, FAILURE_KEY, FAILURE_SERVER, FAILURE_BUDGET = "network", "key", "server", "budget"
BUDGET_RECHECK_S = 3600.0


@dataclass
class Environment:
  """What the loop observed this second."""
  token: str = ""
  google_key: str = ""              # only while Google is the chosen map style
  network: str = "offline"          # offline, cellular, wifi
  onroad: bool = False
  map_enabled: bool = False
  position: tuple[float, float] | None = None
  bearing: float | None = None
  route: list[tuple[float, float]] = field(default_factory=list)


def ahead_tiles(latitude: float, longitude: float, bearing: float | None, near_m: float = NEAR_RADIUS_M,
                ahead_m: float = AHEAD_M) -> list[TileKey]:
  """Tiles around the car, then a band along its heading."""
  keys = tiles_in_radius(latitude, longitude, near_m)
  if bearing is None or not math.isfinite(bearing):
    return keys
  x, y = world_xy(latitude, longitude)
  step = 0.5
  reach = ahead_m / max(1.0, meters_per_tile(latitude))
  dx, dy = math.sin(math.radians(bearing)), -math.cos(math.radians(bearing))
  seen = set(keys)
  wrap = 1 << DATA_ZOOM
  distance = 0.0
  while distance <= reach:
    for side in (-1, 0, 1):
      sx, sy = x + dx * distance - dy * side * 0.6, y + dy * distance + dx * side * 0.6
      key = TileKey(DATA_ZOOM, int(math.floor(sx)) % wrap, int(math.floor(sy)))
      if 0 <= key.y < wrap and key not in seen:
        seen.add(key)
        keys.append(key)
    distance += step
  return keys


class Fetcher:
  """One Mapbox request per tile, reduced to road lines before it is stored."""

  def __init__(self, session=None, usage: Usage | None = None, clock: Callable[[], float] = time.monotonic):
    self.session = session or requests.Session()
    self.usage = usage
    self.clock = clock
    self.blocked_until = 0.0
    self.failure: str | None = None

  @property
  def blocked(self) -> bool:
    return self.clock() < self.blocked_until

  def _block(self, failure: str, seconds: float) -> None:
    self.failure = failure
    self.blocked_until = self.clock() + seconds

  def fetch(self, key: TileKey, token: str) -> bytes | None:
    """Encoded road tile, or None after recording why it could not be fetched."""
    if self.blocked or not token:
      return None
    if self.usage is not None:
      from openpilot.starpilot.navigation.mapbox_budget import FREE, STOP_FRACTION
      if self.usage.snapshot()["tiles"] >= int(FREE["vectorTiles"] * STOP_FRACTION):
        self._block(FAILURE_BUDGET, BUDGET_RECHECK_S)  # free tiles for the month are used up; resumes on the 1st
        return None
    if self.usage is not None:
      self.usage.add()
    try:
      response = self.session.get(MVT_URL.format(z=key.z, x=key.x, y=key.y), params={"access_token": token},
                                  timeout=REQUEST_TIMEOUT, stream=True)
      try:
        status = response.status_code
        data = b""
        if status == 200:
          chunks = []
          size = 0
          for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
              raise TileFormatError("tile too large")
            chunks.append(chunk)
          data = b"".join(chunks)
      finally:
        response.close()
    except requests.RequestException:
      self._block(FAILURE_NETWORK, NETWORK_BACKOFF_S)
      return None
    except TileFormatError:
      return None
    if status in (401, 403):
      self._block(FAILURE_KEY, KEY_BACKOFF_S)
      return None
    if status == 429 or status >= 500:
      self._block(FAILURE_SERVER, SERVER_BACKOFF_S)
      return None
    if status in (204, 404):
      data = b""          # nothing mapped here (open water): store an empty tile so it is not asked again
    elif status != 200:
      return None
    try:
      roads = decode_mvt_roads(data) if data else {}
    except (TileFormatError, OSError, EOFError, ValueError):
      return None
    self.failure = None
    return encode_road_tile(roads)


class AreaProgress:
  """Walks one offline area's tiles once, counting what is already saved."""

  def __init__(self, area: dict, store: TileStore, refresh: bool):
    self.area = area
    self.tiles = area_tiles(area)
    self.total = len(self.tiles)
    self.done = 0
    self.index = 0
    self.failed = 0
    self.refresh = refresh
    self.store = store
    self.recorded = False
    self.completed_at = area.get("refreshed", 0)
    self.retry_after = 0.0

  @property
  def finished(self) -> bool:
    return self.index >= self.total

  def pending(self) -> Iterator[TileKey]:
    """Keys still to download; tiles found on disk are moved into saved/ along the way."""
    while self.index < self.total:
      key = self.tiles[self.index]
      age = self.store.age("saved", key)
      if age is not None and (not self.refresh or age < AREA_REFRESH_SECONDS):
        self.index += 1
        self.done += 1
        continue
      yield key
      return


def _shape(area: dict) -> tuple:
  return area["latitude"], area["longitude"], area["radiusKm"]


def covered(key: TileKey, areas: list[dict]) -> bool:
  """Whether any offline area still needs this tile (circle against the tile square)."""
  for area in areas:
    cx, cy = world_xy(area["latitude"], area["longitude"], key.z)
    reach = area["radiusKm"] * 1000.0 / max(1.0, meters_per_tile(area["latitude"], key.z))
    tx = key.x + round((cx - key.x) / (1 << key.z)) * (1 << key.z)
    nx, ny = min(max(cx, tx), tx + 1), min(max(cy, key.y), key.y + 1)
    if (nx - cx) ** 2 + (ny - cy) ** 2 <= reach * reach:
      return True
  return False


class TileDaemon:
  def __init__(self, store: TileStore | None = None, state: OfflineState | None = None, fetcher: Fetcher | None = None,
               clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):  # noqa: TID251
    self.store = store or TileStore()
    self.state = state or OfflineState(self.store.root)
    self.usage = Usage(self.store.root)
    self.fetcher = fetcher or Fetcher(usage=self.usage, clock=clock)
    self.clock, self.wall = clock, wall
    self.urgent: deque[TileKey] = deque()
    self.urgent_kind = "cache"
    self.route_plan: list[TileKey] = []
    self.route_index = 0
    self.route_key: tuple | None = None
    self.near_key: tuple | None = None
    self.progress: dict[str, AreaProgress] = {}
    self.known_areas: dict[str, dict] = {}
    self.sweep_needed = True
    self.last_area_fetch = 0.0
    self.last_status = -math.inf
    self.last_trim = -math.inf
    self.no_space = False
    self.storage_retry_after = 0.0
    self.saved_full_at: int | None = None
    self.sizes = dict.fromkeys(("saved", "driven", "cache"), 0)

  # ---- planning

  def plan(self, env: Environment) -> None:
    settings = self.state.settings()
    if env.onroad and env.map_enabled and env.position is not None:
      lat, lon = env.position
      cell = (round(lat * 200), round(lon * 200), None if env.bearing is None else round(env.bearing / 30))
      if cell != self.near_key:
        self.near_key = cell
        self.urgent = deque(key for key in ahead_tiles(lat, lon, env.bearing))
    else:
      self.near_key = None
      self.urgent.clear()
    self.urgent_kind = "driven" if settings["saveDriven"] else "cache"

    route_key = tuple(env.route)
    if route_key != self.route_key:
      self.route_key = route_key
      self.route_plan = corridor_tiles(env.route) if len(env.route) >= 2 else []
      self.route_index = 0

    areas = {area["id"]: area for area in self.state.areas()}
    removed = [area_id for area_id in self.known_areas if area_id not in areas]
    if removed:
      self.sweep_needed = True
    for area_id in removed:
      self.progress.pop(area_id, None)
    for area_id, area in areas.items():
      progress = self.progress.get(area_id)
      stale = bool(area.get("refreshed")) and self.wall() - area["refreshed"] > AREA_REFRESH_SECONDS
      retry = progress is not None and progress.finished and (
        progress.failed and self.clock() >= progress.retry_after or
        progress.recorded and stale and self.wall() - progress.completed_at > AREA_REFRESH_SECONDS)
      if progress is None or _shape(progress.area) != _shape(area) or retry:
        self.progress[area_id] = AreaProgress(area, self.store, refresh=stale)
    self.known_areas = areas

  # ---- work

  def _store(self, kind: str, key: TileKey, data: bytes) -> bool:
    sizes = {name: self._tile_size(name, key) for name in self.sizes}
    ok = self.store.write(kind, key, data)
    if ok:
      for name in self.sizes:
        self.sizes[name] = max(0, self.sizes[name] + self._tile_size(name, key) - sizes[name])
    self.no_space = not ok
    if not ok:
      # A replace may have succeeded before a directory-sync failure.
      self.sizes = {name: self.store.size(name) for name in self.sizes}
      self.storage_retry_after = self.clock() + TRIM_INTERVAL_S
    return ok

  def _tile_size(self, kind: str, key: TileKey) -> int:
    try:
      return self.store.path(kind, key).stat().st_size
    except OSError:
      return 0

  def _promote(self, kind: str, key: TileKey) -> bool:
    sizes = {name: self._tile_size(name, key) for name in self.sizes}
    promoted = self.store.promote(kind, key)
    for name in self.sizes:
      self.sizes[name] = max(0, self.sizes[name] + self._tile_size(name, key) - sizes[name])
    return promoted

  def _urgent_job(self) -> TileKey | None:
    while self.urgent:
      key = self.urgent.popleft()
      if self.store.find(key) is None:
        return key
      if self.urgent_kind == "driven":
        self._promote("driven", key)
      self.store.touch(key)
    return None

  def _route_job(self) -> TileKey | None:
    while self.route_index < len(self.route_plan):
      key = self.route_plan[self.route_index]
      self.route_index += 1
      if self.store.find(key) is None:
        return key
    return None

  def step(self, env: Environment) -> float | None:
    """Do at most one download. Returns seconds to wait before the next step, or None when idle."""
    if env.network == "offline" or not env.token or self.fetcher.blocked:
      return None
    if self.clock() < self.storage_retry_after:
      return None
    if not self.store.has_space():
      self.no_space = True
      return None
    self.no_space = False
    key = self._urgent_job()
    if key is not None:
      data = self.fetcher.fetch(key, env.token)
      if data is not None:
        if not self._store(self.urgent_kind, key, data):
          self.urgent.appendleft(key)
      elif self.fetcher.blocked:
        self.urgent.appendleft(key)
      return 0.0
    key = self._route_job()
    if key is not None:
      data = self.fetcher.fetch(key, env.token)
      if data is not None:
        if not self._store("cache", key, data):
          self.route_index -= 1
      elif self.fetcher.blocked:
        self.route_index -= 1
      return 0.0
    if env.network != "wifi":
      return None
    if self.saved_full_at == self.sizes["saved"]:
      self.no_space = True
      return None
    for progress in self.progress.values():
      key = None if progress.finished else next(progress.pending(), None)
      if key is None:
        if progress.finished and not progress.recorded and progress.failed == 0:
          progress.recorded = True
          progress.completed_at = self.wall()
          self.state.mark_refreshed(progress.area, progress.completed_at)
        continue
      cached = self.store.find(key) if not progress.refresh else None
      if cached is not None:
        size = cached.stat().st_size
        if self.sizes["saved"] + size > SAVED_MAX_BYTES:
          self.no_space = True
          self.saved_full_at = self.sizes["saved"]
          return None
        if self._promote("saved", key):
          progress.done += 1
          progress.index += 1
          return 0.0
      if self.sizes["saved"] >= SAVED_MAX_BYTES and not self.store.has("saved", key):
        self.no_space = True
        return None
      wait = AREA_INTERVAL_S - (self.clock() - self.last_area_fetch)
      if wait > 0:
        return wait
      self.last_area_fetch = self.clock()
      data = self.fetcher.fetch(key, env.token)
      saved = self.store.path("saved", key)
      old_size = saved.stat().st_size if saved.is_file() else 0
      if data is not None and self.sizes["saved"] + len(data) - old_size > SAVED_MAX_BYTES:
        self.no_space = True
        self.saved_full_at = self.sizes["saved"]
        return None
      if data is not None and self._store("saved", key, data):
        progress.done += 1
        progress.index += 1
      elif not self.fetcher.blocked and not self.no_space:
        progress.failed += 1
        progress.index += 1
        progress.retry_after = self.clock() + SERVER_BACKOFF_S
      return 0.0
    return None

  def sweep_saved(self) -> None:
    """Tiles of deleted areas become ordinary cache."""
    if not self.sweep_needed:
      return
    self.sweep_needed = False
    areas = list(self.known_areas.values())
    orphans = set()
    for directory, _, names in os.walk(self.store.root / "saved"):
      parts = Path(directory).parts[-2:]
      for name in names:
        try:
          key = TileKey(int(parts[0]), int(parts[1]), int(name.split(".")[0]))
        except (ValueError, IndexError):
          continue
        if not covered(key, areas):
          orphans.add(key)
    self.store.demote_saved(orphans)

  def maintain(self) -> None:
    now = self.clock()
    if now - self.last_trim < TRIM_INTERVAL_S:
      return
    self.last_trim = now
    self.sweep_saved()
    self.sizes["cache"] = self.store.trim("cache", CACHE_MAX_BYTES)
    self.sizes["driven"] = self.store.trim("driven", DRIVEN_MAX_BYTES)
    self.sizes["saved"] = self.store.size("saved")
    self.usage.flush()

  def status(self, env: Environment, *, force: bool = False) -> None:
    now = self.clock()
    if not force and now - self.last_status < STATUS_INTERVAL_S:
      return
    self.last_status = now
    areas = {}
    active = next((area_id for area_id, progress in self.progress.items() if not progress.finished), None)
    for area_id, progress in self.progress.items():
      if progress.finished:
        state = "complete" if progress.failed == 0 else "incomplete"
      elif self.no_space:
        state = "no_space"
      elif env.network != "wifi":
        state = "waiting_wifi"
      else:
        state = "downloading" if area_id == active else "queued"
      areas[area_id] = {"state": state, "total": progress.total, "done": progress.done, "failed": progress.failed}
    route_total = len(self.route_plan)
    self.state.write_status({
      "version": 1, "heartbeat": self.wall(), "network": env.network, "hasKey": bool(env.token),
      "failure": self.fetcher.failure, "noSpace": self.no_space, "areas": areas,
      "route": {"total": route_total, "done": self.route_index} if route_total else None,
      "bytes": dict(self.sizes), "limits": {"saved": SAVED_MAX_BYTES, "driven": DRIVEN_MAX_BYTES, "cache": CACHE_MAX_BYTES},
      "usage": self.usage.snapshot(),
    })


# ---------------------------------------------------------------- process

def _map_enabled() -> bool:
  from openpilot.starpilot.system.starport.projection_layout import DOCUMENT_PATH
  from openpilot.starpilot.navigation.offline_roads import read_json
  document = read_json(Path(DOCUMENT_PATH), 1 << 16)
  try:
    return bool(document["widgets"]["nav_map"]["enabled"])
  except (TypeError, KeyError):
    return False


def _environment(sm, owner, map_enabled: bool, now_ns: int) -> Environment:
  from openpilot.starpilot.navigation.runtime import location
  env = Environment(map_enabled=map_enabled)
  try:
    settings = owner.read()
    env.token = settings["token"]
    env.google_key = settings["googleKey"] if settings["mapProvider"] == "google" else ""
  except Exception:
    env.token = env.google_key = ""
  device = sm["deviceState"]
  if sm.valid["deviceState"] and sm.logMonoTime["deviceState"] > 0:
    kind = str(device.networkType)
    env.network = ("offline" if kind == "none" else
                   "wifi" if kind in ("wifi", "ethernet") and not device.networkMetered else "cellular")
    if phone_uplink_active():
      env.network = "cellular"
    env.onroad = bool(device.started)
  fix = location(sm, now_ns)
  if fix is not None:
    _, (longitude, latitude), _, bearing = fix
    env.position, env.bearing = (latitude, longitude), bearing
  try:
    from openpilot.starpilot.navigation.wire import navigation_state
    nav = navigation_state(sm["starpilotNavigation"]) if sm.valid["starpilotNavigation"] else None
    if (nav is not None and nav.enabled and nav.status == "guiding" and
        0 < nav.frameMonoTime <= now_ns <= nav.frameMonoTime + 3_000_000_000):
      env.route = [(point.latitude, point.longitude) for point in nav.route]
  except Exception:
    env.route = []
  return env


def main() -> None:
  from openpilot.cereal import messaging
  from openpilot.starpilot.navigation.owner import NavigationOwner
  try:
    os.nice(10)
  except OSError:
    pass
  daemon = TileDaemon()
  google = GoogleTiles()
  owner = NavigationOwner()
  from openpilot.starpilot.gps.source import GPS_SERVICES
  sm = messaging.SubMaster(["deviceState", *GPS_SERVICES])
  map_enabled, layout_checked = False, -math.inf
  while True:
    sm.update(0)
    now = time.monotonic()
    if now - layout_checked > LAYOUT_CHECK_S:
      layout_checked, map_enabled = now, _map_enabled()
    env = _environment(sm, owner, map_enabled, time.monotonic_ns())
    daemon.plan(env)
    daemon.maintain()
    idle = False
    deadline = time.monotonic() + 0.9
    while time.monotonic() < deadline:
      wait = google.step(env.google_key, env.network != "offline")
      if wait is None:
        wait = daemon.step(env)
      if wait is None:
        idle = True
        break
      if wait > 0:
        time.sleep(min(wait, max(0.0, deadline - time.monotonic())))
    daemon.status(env)
    google.write_status()
    if idle:
      time.sleep(1.0)


if __name__ == "__main__":
  main()
