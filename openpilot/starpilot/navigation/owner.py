from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import sys
import subprocess
from urllib.parse import quote, urljoin, urlsplit
from pathlib import Path
from itertools import islice
import tempfile
import threading
import time
import uuid

import requests

from openpilot.starpilot.storage import starpilot_storage_root
from openpilot.starpilot.navigation.position import LastPositionStore
from openpilot.starpilot.gps.source import GPS_MODES

MAX_DOCUMENT = 256 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
SEARCH_TTL = 180
ACTIVE_TTL = 12 * 60 * 60
MAX_SEARCHES = 32
TILE_CACHE_TTL = 15 * 60
TILE_CACHE_ENTRIES = 256
TILE_CACHE_BYTES = 32 * 1024 * 1024
TILE_SLOTS = 16
TILE_SLOT_WAIT_S = 5.0  # a panned-away request still holds its slot until Mapbox answers; wait instead of failing
AUTOCOMPLETE_TYPES = {'poi', 'address', 'street', 'place', 'neighborhood', 'locality'}
SUGGESTS_PER_SESSION = 50  # Mapbox starts a new billable session after this many suggestions
MAX_FAVORITES = 100
MAX_RECENTS = 10
FAVORITE_LABELS = ('home', 'work')
ROUTE_AVOIDANCES = {'avoidTolls': 'toll', 'avoidHighways': 'motorway', 'avoidFerries': 'ferry'}
# Fixed provider styles matching Galaxy; cache entries are separate for each theme.
MAP_STYLES = {'light': 'mapbox/light-v11', 'dark': 'mapbox/dark-v11'}
MAP_PROVIDERS = ('mapbox', 'google')
SHORT_MAP_LINK = re.compile(r'https://(?:maps\.app\.goo\.gl|goo\.gl/maps)/[A-Za-z0-9_-]{1,64}')
GOOGLE_MAP_HOST = re.compile(r'(?:maps\.app\.goo\.gl|goo\.gl|(?:[a-z0-9-]+\.)*google\.com)')
CONFIGURABLE = {'enabled', 'token', 'gpsSource', 'mapProvider', 'routeProvider', 'googleKey'}
KEEPS_ACTIVE = {'gpsSource', 'mapProvider', 'routeProvider', 'googleKey'}
GOOGLE_KEY_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')


def valid_google_key(key: str) -> bool:
  return key == '' or (20 <= len(key) <= 128 and set(key) <= GOOGLE_KEY_CHARS)


def default_settings() -> dict:
  return {'version': 1, 'revision': '0', 'enabled': False, 'token': '', 'destination': None, 'favorites': [], 'recents': [], 'gpsSource': 'backup',
          'mapProvider': 'mapbox', 'routeProvider': 'mapbox', 'googleKey': '', **dict.fromkeys(ROUTE_AVOIDANCES, False)}


class ValidationError(ValueError):
  pass


class ShareQueryError(ValidationError):
  """A shared place the driver has to pick from search results."""
  def __init__(self, message: str, query: str):
    super().__init__(message)
    self.query = query


class ConflictError(ValueError):
  pass


class PlaceResults(list):
  """Search results that name the provider that found them."""
  def __init__(self, rows, provider: str):
    super().__init__(rows)
    self.provider = provider


def destination(value: dict) -> dict:
  if not isinstance(value, dict):
    raise ValidationError('Choose a destination')
  name = value.get('name', '')
  latitude, longitude = value.get('latitude'), value.get('longitude')
  if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 256 or
      (not isinstance(latitude, (int, float)) or isinstance(latitude, bool) or not math.isfinite(latitude) or
       not isinstance(longitude, (int, float)) or isinstance(longitude, bool) or not math.isfinite(longitude)) or
      not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
    raise ValidationError('Destination name and coordinates are invalid')
  identity = hashlib.sha256(f'{latitude:.6f},{longitude:.6f}'.encode()).hexdigest()[:20]
  result = {'id': identity, 'name': name.strip(), 'latitude': float(latitude), 'longitude': float(longitude)}
  address = value.get('address')
  if isinstance(address, str) and address.strip() and address.strip() != result['name']:
    result['address'] = address.strip()[:512]
  return result


def favorite_place(value: dict) -> dict:
  result = destination(value)
  if value.get('label') in FAVORITE_LABELS:
    result['label'] = value['label']
  return result


def remember(recents: list[dict], selected: dict) -> list[dict]:
  """Most recent first; choosing a place again moves it to the top."""
  return ([{key: selected[key] for key in ('id', 'name', 'latitude', 'longitude', 'address') if key in selected}] +
          [row for row in recents if row['id'] != selected['id']])[:MAX_RECENTS]


def rounded(points):
  # About 10 cm: plenty for drawing, and a third of the bytes of full float precision.
  return [dict(point, latitude=round(point['latitude'], 6), longitude=round(point['longitude'], 6)) for point in points]


def response_json(session, url: str, params: dict) -> dict:
  started = time.monotonic()
  try:
    with session.get(url, params=params, timeout=(3, 2), stream=True) as response:
      if response.status_code != 200:
        raise ValidationError('The map service could not complete this request')
      raw = bytearray()
      for chunk in response.iter_content(64 * 1024):
        raw.extend(chunk)
        if time.monotonic() - started > 10:
          raise ValidationError('The map service took too long to respond')
        if len(raw) > MAX_RESPONSE:
          raise ValidationError('The map service response is too large')
      result = json.loads(raw)
      if not isinstance(result, dict):
        raise ValueError
      return result
  except (requests.RequestException, ValueError) as exc:
    if isinstance(exc, ValidationError):
      raise
    raise ValidationError('The map service could not complete this request') from None


def _pooled_session() -> requests.Session:
  session = requests.Session()
  adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=24)
  session.mount('https://', adapter)
  session.mount('http://', adapter)
  return session


class NavigationOwner:
  def __init__(self, root: Path | None = None, runtime_source=None, session=None, transient_root: Path | None = None):
    self.root = Path(root) if root is not None else starpilot_storage_root() / 'navigation'
    self.path = self.root / 'settings.json'
    self.position_store = LastPositionStore(self.root)
    self.google_root = self.root / 'google-tiles'
    self.runtime_source = runtime_source
    self.session = _pooled_session() if session is None or session is requests else session
    self._tile_slots = threading.BoundedSemaphore(TILE_SLOTS)
    from openpilot.starpilot.navigation.mapbox_budget import MonthlyBudget
    self.search_budget = MonthlyBudget('searchSessions', self.root / 'mapbox-usage')
    self.tile_budget = MonthlyBudget('staticTiles', self.root / 'mapbox-usage', flush_every=25)
    self.geocode_budget = MonthlyBudget('geocoding', self.root / 'mapbox-usage')
    self._tile_lock = threading.Lock()
    self._tiles = OrderedDict()
    self._tile_key = None
    self._tile_bytes = 0
    self._lock = threading.RLock()
    self._searches = {}
    self._read_cache = None
    self._params = None
    identity = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:24]
    if transient_root is not None:
      self.transient_root = Path(transient_root)
    elif sys.platform == "linux":
      self.transient_root = Path("/dev/shm") / ("starpilot-navigation-" + identity)
    else:
      boot = subprocess.check_output(["/usr/sbin/sysctl", "-n", "kern.boottime"]).strip()
      boot_id = hashlib.sha256(boot).hexdigest()[:16]
      self.transient_root = Path("/tmp") / ("starpilot-navigation-" + boot_id + "-" + identity)

  def read(self) -> dict:
    # Galaxy polls and every map tile read settings; reparse only after a write replaced the file.
    try:
      info = os.stat(self.path)
    except FileNotFoundError:
      return default_settings()
    key = (info.st_ino, info.st_mtime_ns, info.st_size)
    cached = getattr(self, '_read_cache', None)
    if cached is not None and cached[0] == key:
      return copy.deepcopy(cached[1])
    try:
      with self.path.open('rb') as source:
        raw = source.read(MAX_DOCUMENT + 1)
    except FileNotFoundError:
      return default_settings()
    try:
      value = json.loads(raw)
      if (len(raw) > MAX_DOCUMENT or not isinstance(value, dict) or value.get('version') != 1 or
          type(value.get('enabled')) is not bool or not isinstance(value.get('token'), str) or
          not isinstance(value.get('revision'), str) or not 1 <= len(value['revision']) <= 64 or
          any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for ch in value['revision']) or
          len(value['token']) > 2048 or 'destination' not in value or
          type(value.get('routeChoice', 0)) is not int or not 0 <= value.get('routeChoice', 0) <= 2 or
          not isinstance(value.get('favorites'), list) or len(value['favorites']) > MAX_FAVORITES or
          not isinstance(value.get('recents', []), list)):
        raise ValueError
      if value.get('destination') is not None:
        value['destination'] = destination(value['destination'])
      value['favorites'] = [favorite_place(item) for item in value['favorites']]
      value['recents'] = [destination(item) for item in value.get('recents', [])][:MAX_RECENTS]
      for setting in ROUTE_AVOIDANCES:
        if type(value.setdefault(setting, False)) is not bool:
          raise ValueError
      if value.setdefault('gpsSource', 'backup') not in GPS_MODES:
        raise ValueError
      if value.setdefault('mapProvider', 'mapbox') not in MAP_PROVIDERS or value.setdefault('routeProvider', 'mapbox') not in MAP_PROVIDERS:
        raise ValueError
      if not isinstance(value.setdefault('googleKey', ''), str) or not valid_google_key(value['googleKey']):
        raise ValueError
    except (ValueError, TypeError, KeyError):
      raise ValidationError('Saved navigation settings could not be read') from None
    self._read_cache = (key, copy.deepcopy(value))
    return value

  def _temporary_directory(self):
    self.transient_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(self.transient_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
      os.close(fd)
      raise ValidationError('Navigation temporary storage is unavailable')
    return fd

  def _sweep_active(self, current):
    directory = self._temporary_directory()
    try:
      with os.scandir(directory) as entries:
        for entry in islice(entries, MAX_SEARCHES + 1):
          if ((entry.name.endswith('.json') and entry.name != current['revision'] + '.json') or
              entry.name.startswith('.active-')):
            os.unlink(entry.name, dir_fd=directory)
    finally:
      os.close(directory)

  def _read_active(self, current):
    directory = self._temporary_directory()
    try:
      try:
        fd = os.open(current['revision'] + '.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
      except FileNotFoundError:
        return None
      with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
          raise ValidationError('Navigation temporary storage is unavailable')
        raw = source.read(16385)
      value = json.loads(raw)
      if (len(raw) > 16384 or set(value) != {'version', 'revision', 'expires', 'destination'} or type(value['version']) is not int or value['version'] != 1 or
          not isinstance(value['revision'], str) or type(value['expires']) not in (float, int) or
          not math.isfinite(value['expires'])):
        raise ValueError
      selected = destination(value['destination'])
      if value['revision'] != current['revision'] or not time.monotonic() < value['expires'] <= time.monotonic() + ACTIVE_TTL:
        os.unlink(current['revision'] + '.json', dir_fd=directory)
        return None
      return dict(value, destination=selected)
    except (ValueError, TypeError, KeyError):
      raise ValidationError('Navigation temporary destination could not be read') from None
    finally:
      os.close(directory)

  def _write_active(self, value):
    directory = self._temporary_directory()
    name = '.active-' + uuid.uuid4().hex
    try:
      fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
      with os.fdopen(fd, 'w') as out:
        json.dump(value, out, allow_nan=False, separators=(',', ':'))
        out.flush()
        os.fsync(out.fileno())
      os.replace(name, value['revision'] + '.json', src_dir_fd=directory, dst_dir_fd=directory)
      os.fsync(directory)
    finally:
      try:
        os.unlink(name, dir_fd=directory)
      except FileNotFoundError:
        pass
      os.close(directory)

  def read_routing(self):
    with self._exclusive():
      current = self.read()
      self._sweep_active(current)
      active = self._read_active(current)
      if active is not None:
        current['destination'] = dict(active['destination'], temporary=True)
      return current

  @contextmanager
  def _exclusive(self):
    with self._lock:
      self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
      with (self.root / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield

  def _change(self, transform, expected_revision: str, authorized, *, active_destination=None, preserve_active=False) -> dict:
    with self._exclusive():
      current = self.read()
      if not isinstance(expected_revision, str) or current['revision'] != expected_revision:
        raise ConflictError('Navigation changed; refresh and try again')
      self._sweep_active(current)
      active = self._read_active(current) if preserve_active else None
      transform(current)
      if not (authorized() if callable(authorized) else authorized is True):
        raise PermissionError('Navigation changes are not available right now')
      current['revision'] = uuid.uuid4().hex
      if active_destination is not None:
        active = {'version':1, 'destination':active_destination, 'expires':time.monotonic() + ACTIVE_TTL}
      if active is not None:
        self._write_active(dict(active, revision=current['revision']))
      committed = False
      fd, temporary = tempfile.mkstemp(dir=self.root, prefix='.settings-')
      try:
        with os.fdopen(fd, 'w') as out:
          json.dump(current, out, allow_nan=False, separators=(',', ':'))
          out.flush()
          os.fsync(out.fileno())
        if not (authorized() if callable(authorized) else authorized is True):
          raise PermissionError('Navigation changes are not available right now')
        os.replace(temporary, self.path)
        committed = True
        temporary_directory = self._temporary_directory()
        try:
          with os.scandir(temporary_directory) as entries:
            for stale in islice(entries, MAX_SEARCHES + 1):
              if stale.name.endswith('.json') and stale.name != current['revision'] + '.json':
                os.unlink(stale.name, dir_fd=temporary_directory)
        finally:
          os.close(temporary_directory)
        directory = os.open(self.root, os.O_RDONLY)
        try:
          os.fsync(directory)
        finally:
          os.close(directory)
      finally:
        Path(temporary).unlink(missing_ok=True)
        if not committed and active is not None:
          directory = self._temporary_directory()
          try:
            os.unlink(current['revision'] + '.json', dir_fd=directory)
          except FileNotFoundError:
            pass
          finally:
            os.close(directory)
    return self.snapshot()

  def snapshot(self, route_key: str | None = None) -> dict:
    with self._lock:
      return self._snapshot(route_key)

  def _is_metric(self) -> bool:
    # One Params handle for the 1 Hz status polls instead of a new one per request.
    from openpilot.common.params import Params
    if self._params is None or self._params[0] is not Params:
      self._params = (Params, Params())
    return self._params[1].get_bool('IsMetric')

  def _snapshot(self, route_key: str | None = None) -> dict:
    document = self.read_routing()
    status = ('disabled' if not document['enabled'] else 'needsKey' if not document['token'] else
              'noDestination' if document['destination'] is None else 'waitingForLocation')
    result = {key: document[key] for key in ('enabled', 'destination', 'recents', 'revision')}
    result.update({key: document[key] for key in ROUTE_AVOIDANCES})
    result['gpsSource'] = document['gpsSource']
    from openpilot.starpilot.gps.phone import phone_gps_status
    result['phoneGps'] = phone_gps_status()
    # Home and Work lead, then places in the order they were saved.
    result['favorites'] = sorted(document['favorites'], key=lambda row: FAVORITE_LABELS.index(row['label']) if 'label' in row else 2)
    from openpilot.starpilot.navigation.mapbox_budget import read_usage
    result['mapboxUsage'] = read_usage(self.root / 'mapbox-usage')
    result['mapProvider'] = document['mapProvider']
    result['hasGoogleKey'] = bool(document['googleKey'])
    from openpilot.starpilot.navigation.google_tiles import MESSAGES, GoogleBudget, read_status
    google_status = read_status(self.google_root)
    failure = google_status.get('failure') if document['mapProvider'] == 'google' else None
    copyright_text = google_status.get('copyright')
    result['googleMap'] = {'usage': GoogleBudget(self.google_root).snapshot(), 'failure': MESSAGES.get(failure),
                           **({'copyright': copyright_text} if isinstance(copyright_text, str) and copyright_text else {})}
    from openpilot.starpilot.navigation.google_routes import essentials_budget, read_route_failure, route_budget
    result['routeProvider'] = document['routeProvider']
    google_routing = document['routeProvider'] == 'google' and bool(document['googleKey'])
    result['googleRoutes'] = {'usage': route_budget(self.google_root).snapshot(), 'essentials': essentials_budget(self.google_root).snapshot(),
                              'failure': read_route_failure(self.google_root) if google_routing else None}
    from openpilot.starpilot.navigation.google_places import places_budget, suggestions_budget
    result['googlePlaces'] = {'usage': places_budget(self.google_root).snapshot(), 'suggestions': suggestions_budget(self.google_root).snapshot()}
    result['alternatives'] = [dict(row, geometry=rounded(row['geometry'])) for row in self.route_options(document['revision'])]
    result['selectedRoute'] = document.get('routeChoice', 0)
    result.update(hasKey=bool(document['token']), status=status, instruction=None, route=[], isMetric=self._is_metric(), location=None,
                  network='unknown')
    if document['enabled'] and document['token']:
      if self.runtime_source is None:
        from openpilot.starpilot.navigation.status import NavigationStatusSource
        self.runtime_source = NavigationStatusSource()
      if hasattr(self.runtime_source, 'network_status'):
        try:
          network = self.runtime_source.network_status()
          result['network'] = network if network in ('online', 'offline', 'unknown') else 'unknown'
        except (OSError, ValueError, RuntimeError):
          pass
      if hasattr(self.runtime_source, 'map_position'):
        try:
          result['location'] = self.runtime_source.map_position()
        except (OSError, ValueError, RuntimeError):
          result['location'] = None
      if result['location'] is not None and result['location'].get('validForMs', 0) > 0:
        # navigationd is the sole durable writer; HTTP reads must not checkpoint older samples.
        saved = self.position_store.read()
        bearing = result['location'].get('bearing')
        if saved and 'bearing' in saved and (not isinstance(bearing, (int, float)) or isinstance(bearing, bool) or not math.isfinite(bearing)):
          result['location'] = dict(result['location'], bearing=saved['bearing'])
      if result['location'] is None:
        result['location'] = self.position_store.read()
      state = (self.runtime_source() if callable(self.runtime_source) else self.runtime_source.snapshot()) if document['destination'] else None
      if document['destination'] and isinstance(state, dict) and state.get('revision') == document['revision']:
        for key in ('status', 'instruction', 'route'):
          result[key] = state[key]
        result['route'] = rounded(result['route'])
    # Route lines are most of each status poll; a client that already holds them gets only the key.
    result['routeKey'] = hashlib.sha256(json.dumps([result['route'], result['alternatives']], separators=(',', ':')).encode()).hexdigest()[:16]
    if route_key is not None and route_key == result['routeKey']:
      del result['route'], result['alternatives']
      result['routeUnchanged'] = True
    return result

  def route_options(self, revision):
    try:
      value = json.loads((self.transient_root / 'routes.cache').read_text())
      return value['routes'] if value['revision'] == revision and time.monotonic() < value['expires'] else []
    except (OSError, ValueError, KeyError):
      return []

  def record_routes(self, revision, routes):
    rows = [{'index': index, 'durationSeconds': route.total_duration, 'distanceMeters': route.total_distance,
             'geometry': route.preview()} for index, route in enumerate(routes)]
    with self._exclusive():
      if self.read()['revision'] != revision:
        return
      directory = self._temporary_directory()
      os.close(directory)
      fd, name = tempfile.mkstemp(dir=self.transient_root, prefix='.routes-')
      try:
        with os.fdopen(fd, 'w') as out:
          json.dump({'revision': revision, 'expires': time.monotonic() + ACTIVE_TTL, 'routes': rows}, out, allow_nan=False)
        os.replace(name, self.transient_root / 'routes.cache')
      finally:
        Path(name).unlink(missing_ok=True)

  def select_route(self, index, expected_revision, authorized):
    if self.read()['revision'] != expected_revision:
      raise ConflictError('Navigation changed; refresh and try again')
    rows = self.route_options(expected_revision)
    if type(index) is not int or not 0 <= index < len(rows):
      raise ValidationError('This route is no longer available; refresh and try again')
    result = self._change(lambda doc: doc.update(routeChoice=index), expected_revision, authorized, preserve_active=True)
    # Retain choices across the preference revision without requesting another route.
    with self._exclusive():
      value = {'revision': result['revision'], 'expires': time.monotonic() + ACTIVE_TTL, 'routes': rows}
      (self.transient_root / 'routes.cache').write_text(json.dumps(value, allow_nan=False))
    return self.snapshot()

  def map_tile(self, z: int, x: int, y: int, theme: str = 'light', *, provider: str = 'mapbox') -> bytes:
    if theme not in MAP_STYLES or provider not in ('mapbox', 'google'):
      raise ValidationError('Invalid map theme')
    if any(type(v) is not int for v in (z, x, y)) or not 0 <= z <= 18 or not 0 <= x < 2 ** z or not 0 <= y < 2 ** z:
      raise ValidationError('Invalid map tile')
    settings = self.read()
    token = settings['token']
    if not token:
      raise ValidationError('Save a Mapbox key to view the map')
    google_key = settings.get('googleKey', '') if provider == 'google' and settings.get('mapProvider') == 'google' else ''
    if google_key:
      if not self._tile_slots.acquire(timeout=TILE_SLOT_WAIT_S):
        raise ValidationError('Map is busy; try again')
      try:
        from openpilot.starpilot.navigation.google_tiles import fetch_tile
        tile = fetch_tile(google_key, z, x, y, theme, http=self.session, root=self.google_root)
        current = self.read()
        if current.get('googleKey', '') != google_key or current.get('mapProvider') != 'google':
          raise ValidationError('Map key changed; try again')
        return tile
      except ValidationError:
        raise
      except (requests.RequestException, ValueError, OSError):
        pass
      finally:
        self._tile_slots.release()
    key = hashlib.sha256(token.encode()).digest()
    coordinates = (theme, z, x, y)
    with self._tile_lock:
      self._expire_tiles(key)
      cached = self._tiles.get(coordinates)
      if cached is not None:
        self._tiles.move_to_end(coordinates)
    if cached is not None:
      if self.read()['token'] != token:
        raise ValidationError('Map key changed; try again')
      return cached[1]
    self.tile_budget.spend(enforce=False)  # upstream's map: counted for Setup, never refused
    if not self._tile_slots.acquire(timeout=TILE_SLOT_WAIT_S):
      raise ValidationError('Map is busy; try again')
    try:
      started = time.monotonic()
      with self.session.get(f'https://api.mapbox.com/styles/v1/{MAP_STYLES[theme]}/tiles/512/{z}/{x}/{y}.png',
                            params={'access_token': token}, timeout=(3.05, 5.0), stream=True, allow_redirects=False) as response:
        if response.status_code != 200 or response.headers.get('Content-Type', '').split(';')[0] != 'image/png':
          raise ValidationError('Map tiles are unavailable')
        raw = bytearray()
        for chunk in response.iter_content(64 * 1024):
          raw.extend(chunk)
          if len(raw) > 2 * 1024 * 1024 or time.monotonic() - started > 7:
            raise ValidationError('Map tile exceeded the request limit')
        if bytes(raw[:8]) != b'\x89PNG\r\n\x1a\n' or bytes(raw[12:16]) != b'IHDR' or bytes(raw[16:24]) != b'\x00\x00\x02\x00' * 2:
          raise ValidationError('Invalid map tile response')
      if self.read()['token'] != token:
        raise ValidationError('Map key changed; try again')
      tile = bytes(raw)
      with self._tile_lock:
        if self.read()['token'] != token:
          raise ValidationError('Map key changed; try again')
        self._expire_tiles(key)
        previous = self._tiles.pop(coordinates, None)
        if previous is not None:
          self._tile_bytes -= len(previous[1])
        self._tiles[coordinates] = (time.monotonic() + TILE_CACHE_TTL, tile)
        self._tile_bytes += len(tile)
        while len(self._tiles) > TILE_CACHE_ENTRIES or self._tile_bytes > TILE_CACHE_BYTES:
          _, removed = self._tiles.popitem(last=False)
          self._tile_bytes -= len(removed[1])
      return tile
    except requests.RequestException:
      raise ValidationError('Map tiles are unavailable') from None
    finally:
      self._tile_slots.release()

  def _expire_tiles(self, key):
    # Called with the metadata lock held; provider requests never hold this lock.
    if self._tile_key != key:
      self._tiles.clear()
      self._tile_bytes = 0
      self._tile_key = key
    now = time.monotonic()
    for coordinates, (expires, tile) in list(self._tiles.items()):
      if expires <= now:
        del self._tiles[coordinates]
        self._tile_bytes -= len(tile)

  def configure(self, patch: dict, expected_revision: str, authorized) -> dict:
    if not isinstance(patch, dict) or not patch or set(patch) - (CONFIGURABLE | ROUTE_AVOIDANCES.keys()):
      raise ValidationError('Unknown navigation setting')
    if 'mapProvider' in patch and patch['mapProvider'] not in MAP_PROVIDERS:
      raise ValidationError('Choose a map style')
    if 'routeProvider' in patch and patch['routeProvider'] not in MAP_PROVIDERS:
      raise ValidationError('Choose a route provider')
    if 'googleKey' in patch and (not isinstance(patch['googleKey'], str) or not valid_google_key(patch['googleKey'])):
      raise ValidationError('Enter a valid Google Maps API key')
    if 'gpsSource' in patch and patch['gpsSource'] not in GPS_MODES:
      raise ValidationError('Choose a GPS source')
    if 'enabled' in patch and type(patch['enabled']) is not bool:
      raise ValidationError('Navigation enabled must be on or off')
    if any(type(patch[key]) is not bool for key in ROUTE_AVOIDANCES if key in patch):
      raise ValidationError('Route avoidance settings must be on or off')
    if 'token' in patch and (not isinstance(patch['token'], str) or len(patch['token']) > 2048 or
                             any(ch.isspace() for ch in patch['token'])):
      raise ValidationError('Enter a valid Mapbox access token')
    with self._lock:
      self._searches.clear()
    def update(value):
      if any(key in patch and patch[key] != value[key] for key in ROUTE_AVOIDANCES):
        value['routeChoice'] = 0
      value.update(patch)
    result = self._change(update, expected_revision, authorized, preserve_active=set(patch) <= ROUTE_AVOIDANCES.keys() | KEEPS_ACTIVE)
    if 'token' in patch:
      with self._tile_lock:
        self._tiles.clear()
        self._tile_bytes = 0
        self._tile_key = None
    return result

  def test_google_key(self, key=None) -> dict:
    """Tries the typed key, or the saved one, on all three Google APIs: one tile, one route and one place lookup."""
    key = self.read()['googleKey'] if key is None else key
    if not isinstance(key, str) or not key or not valid_google_key(key):
      raise ValidationError('Enter a valid Google Maps API key')
    from concurrent.futures import ThreadPoolExecutor
    from openpilot.starpilot.navigation.google_tiles import GoogleBudget, test_key
    from openpilot.starpilot.navigation.google_routes import essentials_budget, test_routes
    from openpilot.starpilot.navigation.google_places import places_budget, test_places
    with ThreadPoolExecutor(max_workers=3) as pool:
      routes = pool.submit(test_routes, key, self.session, essentials_budget(self.google_root))
      places = pool.submit(test_places, key, self.session, places_budget(self.google_root))
      return dict(test_key(key, self.session, GoogleBudget(self.google_root)), routes=routes.result(), places=places.result())

  def resolve_share(self, text) -> str:
    """A shared Google Maps short link followed to the full link, which carries the place's coordinates."""
    if not isinstance(text, str) or len(text) > 8192:
      raise ValidationError('The shared text could not be read')
    match = SHORT_MAP_LINK.search(text)
    if match is None:
      return text
    current = match.group(0)
    for _ in range(4):
      try:
        with self.session.get(current, allow_redirects=False, stream=True, timeout=(2., 2.)) as response:
          location = response.headers.get('Location') if 300 <= response.status_code < 400 else None
      except requests.RequestException:
        break
      if not location:
        break
      following = urljoin(current, location)
      parts = urlsplit(following)
      if parts.scheme != 'https' or not GOOGLE_MAP_HOST.fullmatch(parts.hostname or '') or len(following) > 8192:
        break
      current = following
    return text.replace(match.group(0), current) if current != match.group(0) else text

  def share(self, text, expected_revision: str, authorized) -> dict:
    """A place shared from a phone: turns navigation on and starts the route."""
    if not isinstance(text, str) or not text.strip() or len(text) > 8192:
      raise ValidationError('The shared text could not be read')
    current = self.read()
    if not current['token']:
      raise ValidationError('Add your Mapbox access token first')
    if not isinstance(expected_revision, str) or current['revision'] != expected_revision:
      raise ConflictError('Navigation changed; refresh and try again')
    if not (authorized() if callable(authorized) else authorized is True):
      raise PermissionError('Navigation changes are not available right now')
    from openpilot.starpilot.navigation.share import map_link_query, search_text, shared_coordinates
    text = self.resolve_share(text)
    shared = shared_coordinates(text)
    if shared is None:
      query = map_link_query(text)
      if query is None:
        query = search_text(text)
        if not query:
          raise ValidationError('This map link could not be opened')
        raise ShareQueryError(f'Open StarPilot to search for {query}', query)
      shared = self._geocode_share(query)
    selected = destination(shared)
    def update(doc):
      self._reject_temporary_promotion(doc, selected)
      doc['enabled'] = True
      self._navigate(doc, selected)
    result = self._change(update, expected_revision, authorized)
    return dict(result, shared={key: selected[key] for key in ('name', 'latitude', 'longitude')})

  def _geocode_share(self, query: str) -> dict:
    from openpilot.starpilot.navigation.share import place
    name, _, rest = query.partition(',')
    found = self._google_text_search(self.read(), query, 1)
    if found == []:
      raise ShareQueryError(f'No map location found for {name.strip() or query}', query[:200])
    if found:
      top = found[0]
      return place(top['name'], top['latitude'], top['longitude'], top['address'] or top['name'])
    # Geocoding finds addresses, not names: "Name, 123 Main St, City" tries the street address first.
    attempts = ([rest.strip()] if any(ch.isdigit() for ch in rest.split(',')[0]) else []) + [query]
    for attempt in attempts:
      results = self.search(attempt) if len(attempt.strip()) >= 2 else []
      if results:
        top = results[0]
        return place(name.strip() or query, top['latitude'], top['longitude'], top.get('address') or top['name'])
    raise ShareQueryError(f'No map location found for {name.strip() or query}', query[:200])

  @staticmethod
  def _google_places_key(settings) -> str:
    """Places are found with Google when Google gives directions."""
    return settings['googleKey'] if settings['routeProvider'] == 'google' else ''

  def _proximity(self):
    proximity = self._search_context().get('proximity')
    return tuple(float(value) for value in proximity.split(',')) if proximity else None

  def _google_text_search(self, settings, query: str, limit: int) -> list[dict] | None:
    """Google's places for the text; None when Google is not chosen or can't answer, so Mapbox looks instead."""
    key = self._google_places_key(settings)
    if not key:
      return None
    from openpilot.starpilot.navigation.google_places import GooglePlacesFailed, places_budget, text_search
    try:
      return text_search(self.session, key, query.strip(), places_budget(self.google_root), self._proximity(), limit=limit)
    except GooglePlacesFailed:
      return None

  def search(self, query: str) -> list[dict]:
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 256:
      raise ValidationError('Enter at least two characters to search')
    token = self.read()['token']
    if not token:
      raise ValidationError('Add your Mapbox access token first')
    from openpilot.starpilot.navigation.mapbox_budget import BudgetExhausted
    try:
      self.geocode_budget.spend()
    except BudgetExhausted as error:
      raise ValidationError(str(error)) from None
    data = response_json(self.session, 'https://api.mapbox.com/search/geocode/v6/forward',
                         {'q': query.strip(), 'access_token': token, 'limit': 8, 'autocomplete': 'false', 'permanent': 'false', **self._search_context()})
    results = []
    features = data.get('features')
    if not isinstance(features, list):
      raise ValidationError('The map service returned invalid search results')
    for feature in features[:8]:
      try:
        properties = feature['properties']
        coordinates = feature['geometry']['coordinates']
        address = properties.get('full_address') or properties.get('place_formatted')
        name = properties.get('name_preferred') or properties.get('name') or address
        results.append(destination({'name': name, 'address': address, 'longitude': coordinates[0], 'latitude': coordinates[1]}))
      except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        continue
    return results

  def cancel_search(self, caller, search_id):
    if not isinstance(search_id, str):
      raise ValidationError('Start a new destination search')
    with self._lock:
      self._searches.pop((caller, search_id), None)

  def _search_context(self) -> dict:
    with self._lock:
      try:
        if self.runtime_source is None:
          from openpilot.starpilot.navigation.status import NavigationStatusSource
          self.runtime_source = NavigationStatusSource()
        position = getattr(self.runtime_source, 'search_position', None)
        if position is None:
          return {}
        coordinates = position()
        if coordinates is None:
          saved = self.position_store.read()
          if saved is not None:
            coordinates = (saved['longitude'], saved['latitude'])
        if (type(coordinates) is not tuple or len(coordinates) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in coordinates) or
            not -180 <= coordinates[0] <= 180 or not -90 <= coordinates[1] <= 90):
          return {}
        return {'proximity': f'{coordinates[0]:.6f},{coordinates[1]:.6f}'}
      except (AttributeError, ImportError, OSError, ValueError, TypeError, OverflowError, RuntimeError):
        return {}

  def search_places(self, query, caller, search_id, client_id, autocomplete=False):
    if not isinstance(client_id, str) or len(client_id) != 36:
      raise ValidationError('Start a new destination search')
    if not isinstance(search_id, str) or len(search_id) != 36:
      raise ValidationError('Start a new destination search')
    try:
      if str(uuid.UUID(search_id, version=4)) != search_id or str(uuid.UUID(client_id, version=4)) != client_id:
        raise ValueError
    except ValueError:
      raise ValidationError('Start a new destination search') from None
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 256:
      raise ValidationError('Enter at least two characters to search')
    current = self.read()
    if not current['token']:
      raise ValidationError('Add your Mapbox access token first')
    if not autocomplete:
      found = self._google_text_search(current, query, 8)
      if found is not None:
        results = []
        for row in found:
          try:
            results.append(destination(row))
          except ValidationError:
            continue
        return PlaceResults(results, 'google')
    key = (caller, search_id)
    from openpilot.starpilot.navigation.mapbox_budget import BudgetExhausted
    with self._lock:
      now = time.monotonic()
      previous = self._searches.get(key)
      # Typing continues one Mapbox search session: keystrokes share a session token until a place is chosen.
      continuing = (autocomplete and previous is not None and previous['expires'] > now and previous['client_id'] == client_id and
                    previous.get('autocomplete') and previous['revision'] == current['revision'] and
                    previous['token_digest'] == hashlib.sha256(current['token'].encode()).digest())
      self._searches = {other:value for other,value in self._searches.items()
                        if value['expires'] > now and (other == key and continuing or
                                                       not (other[0] == caller and value['client_id'] == client_id))}
      if key in self._searches and not continuing:
        raise ValidationError('Start a new destination search')
      if continuing:
        entry = previous
        entry['expires'] = now + SEARCH_TTL
        entry['suggests'] = entry.get('suggests', 0) + 1
        if entry['provider'] == 'mapbox' and entry['suggests'] % SUGGESTS_PER_SESSION == 0:
          try:
            self.search_budget.spend()
          except BudgetExhausted as error:
            raise ValidationError(str(error)) from None
      else:
        if len(self._searches) >= MAX_SEARCHES or sum(key[0] == caller for key in self._searches) >= 8:
          raise ValidationError('Too many destination searches; try again shortly')
        provider = 'google' if autocomplete and self._google_places_key(current) else 'mapbox'
        # The Search button keeps upstream's behavior (counted, never refused); typing suggestions stop at the cap.
        try:
          if provider == 'mapbox':
            self.search_budget.spend(enforce=autocomplete)
        except BudgetExhausted as error:
          raise ValidationError(str(error)) from None
        entry = {'client_id':client_id, 'session':str(uuid.uuid4()), 'expires':now + SEARCH_TTL, 'revision':current['revision'],
                 'token_digest':hashlib.sha256(current['token'].encode()).digest(), 'ids':set(), 'autocomplete':autocomplete,
                 'provider':provider, 'google':{}, 'google_session':str(uuid.uuid4())}
        self._searches[key] = entry
    try:
      context = self._search_context()
      with self._lock:
        if (self._searches.get(key) is not entry or entry['expires'] <= time.monotonic() or
            self.read()['token'] != current['token']):
          raise ValidationError('Start a new destination search')
      results = self._google_suggest(entry, current, query, search_id, context) if entry['provider'] == 'google' else None
      if results is None:
        data = response_json(self.session, 'https://api.mapbox.com/search/searchbox/v1/suggest',
                             {'q':query.strip(), 'access_token':current['token'], 'session_token':entry['session'],
                              'types':','.join(sorted(AUTOCOMPLETE_TYPES)) if autocomplete else 'poi', 'limit':8, **context})
        suggestions = data.get('suggestions')
        if not isinstance(suggestions, list):
          raise ValidationError('The map service returned invalid search results')
        results = self._mapbox_suggestions(suggestions, search_id, autocomplete)
      with self._lock:
        if (self._searches.get(key) is not entry or entry['expires'] <= time.monotonic() or
            self.read()['token'] != current['token']):
          raise ValidationError('Start a new destination search')
        # Any suggestion shown during this typing session can still be chosen.
        entry['ids'] = (entry['ids'] if autocomplete else set()) | {item['id'] for item in results}
      if results or autocomplete:
        return PlaceResults(results, entry['provider'])
    except ValidationError:
      with self._lock:
        if (self._searches.get(key) is not entry or entry['expires'] <= time.monotonic() or
            self.read()['token'] != current['token']):
          raise ValidationError('Start a new destination search') from None
      if autocomplete:
        return PlaceResults([], entry['provider'])  # suggestions are best effort; typing never spends address lookups
      self.cancel_search(caller, search_id)
      # Address search remains available if place search is unavailable.
      return self.search(query)
    self.cancel_search(caller, search_id)
    return self.search(query)

  def _google_suggest(self, entry, current, query, search_id, context) -> list[dict] | None:
    """Google's typing suggestions; None after Google fails, and the session continues on Mapbox."""
    from openpilot.starpilot.navigation.google_places import GooglePlacesFailed, autocomplete, suggestions_budget
    from openpilot.starpilot.navigation.mapbox_budget import BudgetExhausted
    proximity = tuple(float(value) for value in context['proximity'].split(',')) if context.get('proximity') else None
    try:
      rows = autocomplete(self.session, current['googleKey'], query.strip(), entry['google_session'], suggestions_budget(self.google_root), proximity)
    except GooglePlacesFailed:
      with self._lock:
        entry['provider'] = 'mapbox'
      try:
        self.search_budget.spend(enforce=True)
      except BudgetExhausted as error:
        raise ValidationError(str(error)) from None
      return None
    results = [dict(row, searchId=search_id, temporary=True) for row in rows]
    with self._lock:
      entry['google'].update({row['id']: row for row in results})
    return results

  @staticmethod
  def _mapbox_suggestions(suggestions, search_id, autocomplete) -> list[dict]:
    results = []
    for item in suggestions[:8]:
      if not isinstance(item, dict):
        continue
      identity, name = item.get('mapbox_id'), item.get('name')
      kinds = AUTOCOMPLETE_TYPES if autocomplete else {'poi'}
      if (item.get('feature_type') in kinds and isinstance(identity, str) and 1 <= len(identity) <= 256 and
          isinstance(name, str) and 1 <= len(name.strip()) <= 256):
        description = item.get('full_address') or item.get('place_formatted') or ''
        description = description.strip()[:512] if isinstance(description, str) else ''
        results.append({'id':identity, 'name':name.strip(), 'description':description, 'searchId':search_id, 'temporary':True})
    return results

  def _retrieve(self, identity, search_id, caller, expected_revision, authorized) -> dict:
    """Temporary coordinates for the selected route."""
    if not isinstance(identity, str) or not isinstance(search_id, str):
      raise ValidationError('Choose a place from search results')
    with self._lock:
      entry = self._searches.pop((caller, search_id), None)
    current = self.read()
    if (entry is None or entry['expires'] <= time.monotonic() or identity not in entry['ids'] or
        current['revision'] != expected_revision or entry['revision'] != expected_revision or
        entry['token_digest'] != hashlib.sha256(current['token'].encode()).digest()):
      raise ValidationError('Search again before choosing this place')
    if not (authorized() if callable(authorized) else authorized is True):
      raise PermissionError('Navigation changes are not available right now')
    suggested = entry.get('google', {}).get(identity)
    if suggested is not None:
      from openpilot.starpilot.navigation.google_places import GooglePlacesFailed, place_details
      try:
        found = place_details(self.session, current['googleKey'], identity, entry['google_session'])
      except GooglePlacesFailed:
        raise ValidationError('Google could not open this place; search again') from None
      return destination(dict(found, name=suggested['name'], address=found['address'] or suggested['description']))
    data = response_json(self.session, 'https://api.mapbox.com/search/searchbox/v1/retrieve/' + quote(identity, safe=''),
                         {'access_token':current['token'], 'session_token':entry['session']})
    try:
      feature = data['features'][0]
      properties = feature['properties']
      if properties.get('mapbox_id') != identity:
        raise ValueError
      coordinates = feature['geometry']['coordinates']
      return destination({'name':properties.get('name'), 'address':properties.get('full_address') or properties.get('place_formatted'),
                          'longitude':coordinates[0], 'latitude':coordinates[1]})
    except (ValueError, TypeError, KeyError, IndexError):
      raise ValidationError('The map service returned an invalid place') from None

  def select_place(self, identity, search_id, caller, expected_revision, authorized):
    selected = self._retrieve(identity, search_id, caller, expected_revision, authorized)
    return self._change(lambda doc: doc.update(destination=None, routeChoice=0), expected_revision, authorized, active_destination=selected)

  def favorite_place(self, identity, search_id, caller, expected_revision, authorized, label=None):
    raise ValidationError('Search suggestions can be used for a route but cannot be saved; search for an address instead')

  @staticmethod
  def _navigate(doc, selected):
    doc.update(destination=selected, routeChoice=0, recents=remember(doc.get('recents', []), selected))

  @staticmethod
  def _add_favorite(doc, selected):
    existing = next((row for row in doc['favorites'] if row['id'] == selected['id']), None)
    if existing is not None:
      return
    if len(doc['favorites']) >= MAX_FAVORITES:
      raise ValidationError('Remove a saved place before adding another')
    doc['favorites'] = doc['favorites'] + [selected]

  def _reject_temporary_promotion(self, doc, selected):
    active = self._read_active(doc)
    if active is not None and active['destination']['id'] == selected['id']:
      raise ValidationError('This place can be used for a route but cannot be saved')

  def select(self, value: dict, expected_revision: str, authorized) -> dict:
    if isinstance(value, dict) and value.get('temporary'):
      raise ValidationError('This place can be used for a route but cannot be saved')
    selected = destination(value)
    def update(doc):
      self._reject_temporary_promotion(doc, selected)
      self._navigate(doc, selected)
    return self._change(update, expected_revision, authorized)

  def clear(self, expected_revision: str, authorized) -> dict:
    with self._lock:
      self._searches.clear()
    return self._change(lambda doc: doc.update(destination=None, routeChoice=0), expected_revision, authorized)

  def favorite(self, value: dict, expected_revision: str, authorized, label=None) -> dict:
    self._validate_favorite_label(label)
    if isinstance(value, dict) and value.get('temporary'):
      raise ValidationError('This place can be used for a route but cannot be saved')
    selected = destination(value)
    def update(doc):
      self._reject_temporary_promotion(doc, selected)
      self._add_favorite(doc, selected)
      if label is not None:
        self._label_favorite(doc, selected['id'], label)
    return self._change(update, expected_revision, authorized, preserve_active=True)

  def remove_favorite(self, identity: str, expected_revision: str, authorized) -> dict:
    return self._change(lambda doc: doc.update(favorites=[row for row in doc['favorites'] if row['id'] != identity]),
                        expected_revision, authorized, preserve_active=True)

  def label_favorite(self, identity: str, label, expected_revision: str, authorized) -> dict:
    """Mark a saved place as Home or Work (one each), or clear its label with None."""
    self._validate_favorite_label(label)
    return self._change(lambda doc: self._label_favorite(doc, identity, label), expected_revision, authorized, preserve_active=True)

  @staticmethod
  def _validate_favorite_label(label):
    if label is not None and label not in FAVORITE_LABELS:
      raise ValidationError('Choose Home or Work')

  @staticmethod
  def _label_favorite(doc, identity, label):
    if not any(row['id'] == identity for row in doc['favorites']):
      raise ValidationError('Choose a saved place')
    for row in doc['favorites']:
      if row['id'] == identity or label is not None and row.get('label') == label:
        row.pop('label', None)
      if row['id'] == identity and label is not None:
        row['label'] = label

  def remove_recent(self, identity: str, expected_revision: str, authorized) -> dict:
    return self._change(lambda doc: doc.update(recents=[row for row in doc['recents'] if row['id'] != identity]),
                        expected_revision, authorized, preserve_active=True)

  def clear_recents(self, expected_revision: str, authorized) -> dict:
    return self._change(lambda doc: doc.update(recents=[]), expected_revision, authorized, preserve_active=True)

  def close(self):
    with self._lock:
      if self.runtime_source is not None and hasattr(self.runtime_source, 'close'):
        self.runtime_source.close()
      self.runtime_source = None
