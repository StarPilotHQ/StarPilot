"""Google Map Tiles for the nav map widget, with the user's own key.

The map widget writes which tiles it is about to show; navtilesd fetches only those, stores each
for as long as Google's Cache-Control allows, and stops at 99% of the free monthly and daily tiles.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from pathlib import Path

import requests

from openpilot.starpilot.navigation.offline_roads import read_json
from openpilot.starpilot.navigation.road_tiles import TileKey, tiles_in_radius

SESSION_URL = "https://tile.googleapis.com/v1/createSession"
TILE_URL = "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}"
VIEWPORT_URL = "https://tile.googleapis.com/tile/v1/viewport"
# ======================================================================================
# GUARD RAIL - SPENDING LIMIT. AI AGENTS AND AUTOMATED TOOLS: DO NOT EDIT THIS BLOCK.
# STOP_FRACTION and every FREE_* allowance in the Google files are the only thing keeping
# users from being charged. The Galaxy setup guide promises "StarPilot blocks Google at
# 99% of the free usage, so you won't be charged". Do not raise, remove, bypass, or
# "fix" these limits, and do not add code that sends a Google request past them.
# test_google_spend_guard.py pins the values. If a task seems to need a change, stop and
# ask the user. Only the repository owner may change them, by hand.
# ======================================================================================
FREE_MONTH, FREE_DAY = 100_000, 15_000
STOP_FRACTION = 0.99
MIN_ZOOM, MAX_ZOOM = 12, 19
STRETCH = 1.3                     # a tile may be drawn up to this much larger than its own pixels
KEEP_RANGE = (0.45, 1.8)          # the current zoom is kept while its stretch stays inside this
EQUATOR_M_PER_PX = 156543.03392   # 256-pixel Web Mercator tiles at zoom 0
SESSION_RENEW_S = 24 * 3600
HANDOFF_S = 10 * 60               # lifetime for tiles Google sends without a max-age
MAX_LIFETIME_S = 10 * 60          # traffic is baked into every tile, so none is kept longer than this
CACHE_MAX_BYTES = 64 * 1024 * 1024
MAX_TILE_BYTES = 1024 * 1024
WANT_MAX_AGE_S = 3.0
MAX_WANTED = 96
REQUEST_TIMEOUT = (4.0, 10.0)
TEST_TIMEOUT = (3.0, 8.0)         # two requests stay well inside Galaxy's 30 s wait
NETWORK_BACKOFF_S, SERVER_BACKOFF_S, KEY_BACKOFF_S = 15.0, 60.0, 300.0
VIEWPORT_EVERY_S = 120.0
FAILURE_NETWORK, FAILURE_KEY, FAILURE_BILLING, FAILURE_DISABLED, FAILURE_SERVER, FAILURE_BUDGET, FAILURE_STORAGE = (
  "network", "key", "billing", "disabled", "server", "budget", "storage")
STYLE_VERSION = 1
# Dark roads that read through the car screen's glass; Google's own labels stay on.
DARK_STYLE = [
  {"elementType": "geometry", "stylers": [{"color": "#1d2230"}]},
  {"elementType": "labels.text.fill", "stylers": [{"color": "#c8cedc"}]},
  {"elementType": "labels.text.stroke", "stylers": [{"color": "#12151e"}]},
  {"featureType": "poi", "stylers": [{"visibility": "off"}]},
  {"featureType": "transit", "stylers": [{"visibility": "off"}]},
  {"featureType": "road", "elementType": "geometry", "stylers": [{"color": "#3a4256"}]},
  {"featureType": "road.arterial", "elementType": "geometry", "stylers": [{"color": "#4a5470"}]},
  {"featureType": "road.highway", "elementType": "geometry", "stylers": [{"color": "#7a6a46"}]},
  {"featureType": "water", "elementType": "geometry", "stylers": [{"color": "#0e1a2b"}]},
]


def google_root() -> Path:
  from openpilot.starpilot.storage import starpilot_storage_root
  return starpilot_storage_root() / "navigation" / "google-tiles"


def want_path() -> Path:
  return Path("/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()) / "starpilot-google-tiles-want.json"


def _wall() -> float:
  return time.time()  # noqa: TID251 - expiry and calendar days are wall-clock


def session_body(language: str = "en-US", region: str = "US", theme: str = "dark") -> dict:
  body = {"mapType": "roadmap", "language": language, "region": region, "imageFormat": "png", "layerTypes": ["layerTraffic"]}
  if theme != "light":
    body["styles"] = DARK_STYLE
  return body


def key_digest(api_key: str, theme: str = "dark") -> str:
  suffix = "\nlight" if theme == "light" else ""
  return hashlib.sha256(f"{api_key}\n{STYLE_VERSION}{suffix}".encode()).hexdigest()[:24]



def native_m_per_px(latitude: float, zoom: int) -> float:
  return EQUATOR_M_PER_PX * math.cos(math.radians(latitude)) / (1 << zoom)


def choose_zoom(latitude: float, m_per_px: float, current: int | None = None) -> int:
  """The lowest zoom whose tiles are stretched at most ``STRETCH``; the current one is kept while it still fits."""
  if current is not None and MIN_ZOOM <= current <= MAX_ZOOM:
    stretch = native_m_per_px(latitude, current) / m_per_px
    if KEEP_RANGE[0] <= stretch <= KEEP_RANGE[1]:
      return current
  ideal = math.log2(EQUATOR_M_PER_PX * math.cos(math.radians(latitude)) / (m_per_px * STRETCH))
  return max(MIN_ZOOM, min(MAX_ZOOM, math.ceil(ideal)))


def plan_tiles(latitude: float, longitude: float, radius_m: float, zoom: int) -> list[TileKey]:
  """Tiles touching the circle, nearest first."""
  return tiles_in_radius(latitude, longitude, radius_m, zoom)[:MAX_WANTED]



def write_want(path: Path, keys: Sequence[TileKey], latitude: float, longitude: float, now: float | None = None) -> None:
  _write_atomic(path, {"at": _wall() if now is None else now, "latitude": latitude, "longitude": longitude,
                       "tiles": [[key.z, key.x, key.y] for key in keys[:MAX_WANTED]]})


def read_want(path: Path, now: float | None = None) -> tuple[list[TileKey], tuple[float, float] | None]:
  """Wanted tiles and the car's position, or nothing when the widget stopped asking."""
  now = _wall() if now is None else now
  try:
    with path.open("rb") as handle:
      raw = handle.read(16 * 1024 + 1)
    value = json.loads(raw)
    if len(raw) > 16 * 1024 or not 0 <= now - float(value["at"]) <= WANT_MAX_AGE_S:
      return [], None
    keys = []
    for z, x, y in value["tiles"][:MAX_WANTED]:
      if (type(z) is int and type(x) is int and type(y) is int and MIN_ZOOM <= z <= MAX_ZOOM and
          0 <= x < 1 << z and 0 <= y < 1 << z):
        keys.append(TileKey(z, x, y))
    latitude, longitude = float(value["latitude"]), float(value["longitude"])
    if not (-85 < latitude < 85 and -180 <= longitude <= 180):
      return keys, None
    return keys, (latitude, longitude)
  except (OSError, ValueError, TypeError, KeyError):
    return [], None



def cache_lifetime(headers) -> float:
  """Seconds a tile may be kept: its Cache-Control header, capped so traffic stays current."""
  control = str(headers.get("Cache-Control", "") or "").lower()
  if "no-store" in control or "no-cache" in control:
    return HANDOFF_S
  for part in control.split(","):
    name, _, value = part.strip().partition("=")
    if name == "max-age":
      try:
        return min(MAX_LIFETIME_S, max(0.0, float(value.strip('"'))))
      except ValueError:
        return HANDOFF_S
  return HANDOFF_S


class GoogleTileStore:
  """One PNG per tile under a folder per key and style; a file's mtime is when it expires."""

  def __init__(self, root: Path | None = None):
    self.root = Path(root) if root is not None else google_root()

  def path(self, digest: str, key: TileKey) -> Path:
    return self.root / "tiles" / digest / str(key.z) / str(key.x) / f"{key.y}.png"

  def fresh(self, digest: str, key: TileKey, now: float | None = None) -> bool:
    try:
      info = self.path(digest, key).stat()
    except OSError:
      return False
    return info.st_size > 0 and info.st_mtime > (_wall() if now is None else now)

  def read(self, digest: str, key: TileKey, now: float | None = None) -> bytes | None:
    if not self.fresh(digest, key, now):
      return None
    try:
      with self.path(digest, key).open("rb") as handle:
        data = handle.read(MAX_TILE_BYTES + 1)
    except OSError:
      return None
    return data if 0 < len(data) <= MAX_TILE_BYTES else None

  def write(self, digest: str, key: TileKey, data: bytes, lifetime: float, now: float | None = None) -> bool:
    path = self.path(digest, key)
    try:
      path.parent.mkdir(parents=True, exist_ok=True)
      fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tile-")
      try:
        with os.fdopen(fd, "wb") as handle:
          handle.write(data)
        expires = (_wall() if now is None else now) + lifetime
        os.utime(temp, (expires, expires))
        os.replace(temp, path)
      except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    except OSError:
      return False
    return True

  def trim(self, max_bytes: int = CACHE_MAX_BYTES, now: float | None = None) -> int:
    """Delete expired tiles, then the soonest to expire until under the cap; returns the size kept."""
    now = _wall() if now is None else now
    entries, total = [], 0
    for directory, _, names in os.walk(self.root / "tiles"):
      for name in names:
        full = os.path.join(directory, name)
        try:
          info = os.stat(full)
        except OSError:
          continue
        if info.st_mtime <= now:
          Path(full).unlink(missing_ok=True)
          continue
        entries.append((info.st_mtime, info.st_size, full))
        total += info.st_size
    for _, size, full in sorted(entries):
      if total <= max_bytes:
        break
      Path(full).unlink(missing_ok=True)
      total -= size
    return total



def pacific_day(wall: float) -> str:
  """Google's quota day, which starts at midnight US Pacific time; a fixed offset when tzdata is missing."""
  from datetime import datetime
  from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
  try:
    return datetime.fromtimestamp(wall, ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
  except ZoneInfoNotFoundError:
    return time.strftime("%Y-%m-%d", time.gmtime(wall - 8 * 3600))


class GoogleBudget:
  """Tiles counted on the comma, shared by navtilesd and Galaxy's Test key under a file lock."""

  def __init__(self, root: Path | None = None, clock: Callable[[], float] = _wall, file: str = "usage.json",
               month_free: int = FREE_MONTH, day_free: int | None = FREE_DAY):
    self.root = Path(root) if root is not None else google_root()
    self.path = self.root / file
    self.clock = clock
    self.month_free, self.day_free = month_free, day_free
    self.month_limit = int(month_free * STOP_FRACTION)
    self.day_limit = int(day_free * STOP_FRACTION) if day_free is not None else None

  @contextmanager
  def _locked(self):
    self.root.mkdir(parents=True, exist_ok=True)
    with open(self.root / ".usage.lock", "a") as lock:
      fcntl.flock(lock, fcntl.LOCK_EX)
      try:
        yield
      finally:
        fcntl.flock(lock, fcntl.LOCK_UN)

  def _load(self) -> dict:
    day = pacific_day(self.clock())
    value = {"day": day, "month": day[:7], "dayUsed": 0, "monthUsed": 0}
    saved = read_json(self.path, 4096)
    if not isinstance(saved, dict):
      return value
    # Counts only reset when the calendar moves forward, so a clock that is briefly behind cannot zero them.
    if isinstance(saved.get("month"), str) and saved["month"] >= value["month"] and type(saved.get("monthUsed")) is int:
      value["month"], value["monthUsed"] = saved["month"], max(0, saved["monthUsed"])
      if isinstance(saved.get("day"), str) and saved["day"] >= day and type(saved.get("dayUsed")) is int:
        value["day"], value["dayUsed"] = saved["day"], max(0, saved["dayUsed"])
    return value

  def snapshot(self) -> dict:
    value = self._load()
    return {"monthUsed": value["monthUsed"], "monthLimit": self.month_limit, "monthFree": self.month_free,
            "dayUsed": value["dayUsed"], "dayLimit": self.day_limit, "dayFree": self.day_free}

  def spend(self, count: int = 1) -> bool:
    """Count tiles about to be requested; False, counting nothing, when either allowance is used up."""
    with self._locked():
      value = self._load()
      if value["monthUsed"] + count > self.month_limit or (self.day_limit is not None and value["dayUsed"] + count > self.day_limit):
        return False
      value["monthUsed"] += count
      value["dayUsed"] += count
      _write_atomic(self.path, value)
      return True



def classify(status: int, body: bytes) -> str | None:
  """Why Google refused, from its status and error text; None for a success."""
  if status == 200:
    return None
  text = body[:4096].decode("utf-8", "replace").lower()
  if "billing" in text:
    return FAILURE_BILLING
  if "has not been used" in text or "is disabled" in text or "service_disabled" in text or "api_key_service_blocked" in text:
    return FAILURE_DISABLED
  if status in (401, 403) or (status == 400 and ("api key" in text or "api_key" in text)):
    return FAILURE_KEY
  return FAILURE_SERVER


MESSAGES = {
  FAILURE_KEY: "Google rejected the key for the Map Tiles API. Check that it was copied whole and that its API restrictions allow the Map Tiles API.",
  FAILURE_BILLING: "Billing is not turned on for this Google Cloud project. Add a billing account, then test again.",
  FAILURE_DISABLED: "The Map Tiles API is off for this key. Enable it in Google Cloud and allow it in the key's API restrictions, then test again.",
  FAILURE_SERVER: "Google is busy or refused the request. Try again in a minute.",
  FAILURE_NETWORK: "The comma could not reach Google. Check its internet connection.",
  FAILURE_BUDGET: "This month's or today's free Google tiles are used up. The map uses Mapbox road lines until they reset.",
  FAILURE_STORAGE: "The comma could not save Google map tiles. Check its free storage.",
}


def _post_session(http, api_key: str, timeout=REQUEST_TIMEOUT, theme: str = "dark") -> tuple[int, bytes]:
  with http.post(SESSION_URL, json=session_body(theme=theme), headers={"X-Goog-Api-Key": api_key}, timeout=timeout, stream=True) as response:
    return response.status_code, _body(response, 64 * 1024)


def _body(response, limit: int) -> bytes:
  chunks, size = [], 0
  for chunk in response.iter_content(64 * 1024):
    size += len(chunk)
    if size > limit:
      raise ValueError("response too large")
    chunks.append(chunk)
  return b"".join(chunks)


def parse_session(body: bytes, now: float) -> dict:
  value = json.loads(body)
  session, expiry = value["session"], float(value["expiry"])
  if not isinstance(session, str) or not 0 < len(session) <= 1024 or not math.isfinite(expiry) or expiry <= now:
    raise ValueError("bad session")
  return {"session": session, "expiry": expiry}


def test_key(api_key: str, http=requests, budget: GoogleBudget | None = None) -> dict:
  """Creates a session exactly as the map does and fetches one tile; a plain answer either way."""
  budget = budget or GoogleBudget()
  try:
    status, body = _post_session(http, api_key, TEST_TIMEOUT)
    failure = classify(status, body)
    if failure is not None:
      return {"ok": False, "failure": failure, "message": MESSAGES[failure]}
    session = parse_session(body, _wall())
    if not budget.spend():
      return {"ok": False, "failure": FAILURE_BUDGET, "message": MESSAGES[FAILURE_BUDGET]}
    with http.get(TILE_URL.format(z=0, x=0, y=0), params={"session": session["session"]}, headers={"X-Goog-Api-Key": api_key},
                  timeout=TEST_TIMEOUT, stream=True) as response:
      status, body = response.status_code, _body(response, MAX_TILE_BYTES)
  except requests.RequestException:
    return {"ok": False, "failure": FAILURE_NETWORK, "message": MESSAGES[FAILURE_NETWORK]}
  except (ValueError, KeyError, TypeError):
    return {"ok": False, "failure": FAILURE_SERVER, "message": MESSAGES[FAILURE_SERVER]}
  failure = classify(status, body)
  if failure is not None:
    return {"ok": False, "failure": failure, "message": MESSAGES[failure]}
  return {"ok": True, "failure": None, "message": "The key works. The map can use Google."}


def viewport_copyright(http, api_key: str, session: str, position: tuple[float, float], zoom: int, span: float = 0.02) -> str:
  """Google's data attribution around the car (viewport requests are not billed); empty when it cannot be read."""
  latitude, longitude = position
  params = {"session": session, "zoom": zoom, "north": min(84.9, latitude + span), "south": max(-84.9, latitude - span),
            "east": min(179.9, longitude + span), "west": max(-179.9, longitude - span)}
  try:
    with http.get(VIEWPORT_URL, params=params, headers={"X-Goog-Api-Key": api_key}, timeout=REQUEST_TIMEOUT, stream=True) as response:
      value = json.loads(_body(response, 256 * 1024)) if response.status_code == 200 else {}
  except (requests.RequestException, ValueError):
    return ""
  text = value.get("copyright") if isinstance(value, dict) else None
  return " ".join(text.split())[:160] if isinstance(text, str) else ""


def fetch_tile(api_key: str, z: int, x: int, y: int, theme: str = "dark", *, http=requests, root: Path | None = None,
               wall: Callable[[], float] = _wall) -> bytes:
  """One 2D Google map tile for Galaxy's preview map, sharing the disk store and free-allowance budget."""
  root = Path(root) if root is not None else google_root()
  store = GoogleTileStore(root)
  budget = GoogleBudget(root, wall)
  digest = key_digest(api_key, theme)
  key = TileKey(z, x, y)
  now = wall()
  cached = store.read(digest, key, now)
  if cached is not None:
    return cached
  session_file = root / ("session.json" if theme != "light" else "session-light.json")
  saved = read_json(session_file, 4096)
  session = None
  if isinstance(saved, dict) and saved.get("digest") == digest:
    try:
      if float(saved["expiry"]) - now > SESSION_RENEW_S and isinstance(saved["session"], str):
        session = saved["session"]
    except (KeyError, TypeError, ValueError):
      pass
  if session is None:
    status, body = _post_session(http, api_key, REQUEST_TIMEOUT, theme=theme)
    failure = classify(status, body)
    if failure is not None:
      raise ValueError(MESSAGES[failure])
    parsed = dict(parse_session(body, now), digest=digest)
    root.mkdir(parents=True, exist_ok=True)
    _write_atomic(session_file, parsed, 0o600)
    session = parsed["session"]
  if not budget.spend():
    raise ValueError(MESSAGES[FAILURE_BUDGET])
  with http.get(TILE_URL.format(z=z, x=x, y=y), params={"session": session}, headers={"X-Goog-Api-Key": api_key},
                timeout=REQUEST_TIMEOUT, stream=True) as response:
    status, body, lifetime = response.status_code, _body(response, MAX_TILE_BYTES), cache_lifetime(response.headers)
  failure = classify(status, body)
  if failure is not None:
    if failure in (FAILURE_KEY, FAILURE_BILLING, FAILURE_DISABLED):
      session_file.unlink(missing_ok=True)
    raise ValueError(MESSAGES[failure])
  if not body or not body.startswith(b"\x89PNG\r\n\x1a\n"):
    raise ValueError(MESSAGES[FAILURE_SERVER])
  store.write(digest, key, body, lifetime, now)
  return body



class GoogleTiles:
  """Fetches the widget's wanted tiles, one per step, within the free allowances."""

  def __init__(self, root: Path | None = None, http=None, budget: GoogleBudget | None = None,
               clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = _wall, want: Path | None = None):
    self.root = Path(root) if root is not None else google_root()
    self.http = http or requests.Session()
    self.store = GoogleTileStore(self.root)
    self.budget = budget or GoogleBudget(self.root, wall)
    self.clock, self.wall = clock, wall
    self.want = want or want_path()
    self.blocked_until = 0.0
    self.failure: str | None = None
    self.copyright = ""
    self._session: dict | None = None
    self._digest: str | None = None
    self._viewport_at = -math.inf
    self._last_status = -math.inf
    self._last_trim = -math.inf

  @property
  def blocked(self) -> bool:
    return self.clock() < self.blocked_until

  def _block(self, failure: str, seconds: float) -> None:
    self.failure = failure
    self.blocked_until = self.clock() + seconds

  def _session_for(self, api_key: str) -> str | None:
    digest = key_digest(api_key)
    now = self.wall()
    if self._session is None or self._session.get("digest") != digest:
      saved = read_json(self.root / "session.json", 4096)
      self._session = saved if isinstance(saved, dict) and saved.get("digest") == digest else None
    if self._session is not None:
      try:
        if float(self._session["expiry"]) - now > SESSION_RENEW_S and isinstance(self._session["session"], str):
          return self._session["session"]
      except (KeyError, TypeError, ValueError):
        pass
    status, body = _post_session(self.http, api_key)
    failure = classify(status, body)
    if failure is not None:
      self._refused(failure)
      return None
    session = dict(parse_session(body, now), digest=digest)
    self.root.mkdir(parents=True, exist_ok=True)
    _write_atomic(self.root / "session.json", session, 0o600)
    self._session = session
    return session["session"]

  def _refused(self, failure: str) -> None:
    if failure in (FAILURE_KEY, FAILURE_BILLING, FAILURE_DISABLED):
      self._session = None
      (self.root / "session.json").unlink(missing_ok=True)
      self._block(failure, KEY_BACKOFF_S)
    else:
      self._block(failure, SERVER_BACKOFF_S)

  def step(self, api_key: str, online: bool) -> float | None:
    """Fetch at most one wanted tile. Returns seconds to wait before the next step, or None when idle."""
    self.maintain()
    if not api_key or not online:
      return None
    digest = key_digest(api_key)
    if digest != self._digest:
      self._digest, self._session, self.failure, self.blocked_until = digest, None, None, 0.0
    if self.blocked:
      return None
    keys, position = read_want(self.want, self.wall())
    if not keys:
      return None
    missing = next((key for key in keys if not self.store.fresh(digest, key, self.wall())), None)
    try:
      session = self._session_for(api_key)
      if session is None:
        return None
      if position is not None and self.clock() - self._viewport_at >= VIEWPORT_EVERY_S:
        self._viewport_at = self.clock()
        self.copyright = viewport_copyright(self.http, api_key, session, position, keys[0].z) or self.copyright
      if missing is None:
        return None
      if not self.budget.spend():
        self._block(FAILURE_BUDGET, 3600.0)
        return None
      with self.http.get(TILE_URL.format(z=missing.z, x=missing.x, y=missing.y), params={"session": session},
                         headers={"X-Goog-Api-Key": api_key}, timeout=REQUEST_TIMEOUT, stream=True) as response:
        status, body, lifetime = response.status_code, _body(response, MAX_TILE_BYTES), cache_lifetime(response.headers)
    except requests.RequestException:
      self._block(FAILURE_NETWORK, NETWORK_BACKOFF_S)
      return None
    except (ValueError, KeyError, TypeError):
      self._block(FAILURE_SERVER, SERVER_BACKOFF_S)
      return None
    except OSError:
      self._block(FAILURE_STORAGE, SERVER_BACKOFF_S)
      return None
    failure = classify(status, body)
    if failure is not None:
      self._refused(failure)
      return None
    if not body or not self.store.write(digest, missing, body, lifetime, self.wall()):
      self._block(FAILURE_STORAGE if body else FAILURE_SERVER, SERVER_BACKOFF_S)
      return None
    self.failure = None
    return 0.0

  def maintain(self) -> None:
    if self.clock() - self._last_trim >= 300.0:
      self._last_trim = self.clock()
      self.store.trim(now=self.wall())

  def write_status(self, force: bool = False) -> None:
    if not force and self.clock() - self._last_status < 2.0:
      return
    self._last_status = self.clock()
    value = {"heartbeat": self.wall(), "failure": self.failure, "copyright": self.copyright,
             "usage": self.budget.snapshot()}
    try:
      self.root.mkdir(parents=True, exist_ok=True)
      _write_atomic(self.root / "status.json", value)
    except OSError:
      self.failure = FAILURE_STORAGE


def _write_atomic(path: Path, value: dict, mode: int | None = None) -> None:
  fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
  try:
    with os.fdopen(fd, "w") as handle:
      json.dump(value, handle, separators=(",", ":"))
    if mode is not None:
      os.chmod(temp, mode)
    os.replace(temp, path)
  except BaseException:
    Path(temp).unlink(missing_ok=True)
    raise


def read_status(root: Path | None = None) -> dict:
  value = read_json((Path(root) if root is not None else google_root()) / "status.json", 64 * 1024)
  return value if isinstance(value, dict) else {}
