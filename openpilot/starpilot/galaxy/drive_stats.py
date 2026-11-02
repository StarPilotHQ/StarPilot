"""Durable, bounded statistics for analyzed local Galaxy drives."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import threading
import time

from openpilot.starpilot.galaxy.drive_history import DriveHistory, MAX_ROOT_ENTRIES, MAX_STATS_SEGMENTS_PER_ROUTE
from openpilot.starpilot.galaxy.drive_analysis import ANALYSIS_VERSION
from openpilot.starpilot.storage import galaxy_storage_root

SOURCE_LIMITATIONS = frozenset({'Route has no observed duration', 'End of route was not recorded',
                                'Route has missing segments or logs', 'Route samples are missing or gapped'})

SCHEMA_VERSION = 1
MAX_CACHED_ROUTES = 2048
MAX_CACHE_BYTES = 4_000_000
MAX_SCAN_ROUTES = 100
MAX_ANALYSES_PER_SCAN = 2
SCAN_INTERVAL_SECONDS = 60
RETRY_SECONDS = 300
MAX_RETRY_ATTEMPTS = 5
MAX_RETRY_AGE_SECONDS = 86400
MAX_RETRY_DELAY_SECONDS = 3600
RECENT_LIMIT = 5


def _finite(value, minimum=0.0):
  if type(value) not in (int, float):
    return None
  try:
    if not math.isfinite(value) or value < minimum:
      return None
  except (OverflowError, ValueError):
    return None
  return float(value)


def _valid_record(value: object) -> bool:
  if not isinstance(value, dict) or type(value.get('routeId')) is not str or len(value['routeId']) > 180:
    return False
  if type(value.get('complete')) is not bool or type(value.get('ignored')) is not bool:
    return False
  if type(value.get('segmentCount')) is not int or not 0 <= value['segmentCount'] <= MAX_STATS_SEGMENTS_PER_ROUTE:
    return False
  for key in ('startTime', 'endTime'):
    if value.get(key) is not None and (_finite(value[key]) is None or value[key] > 253402300799):
      return False
  for key in ('distanceMeters', 'durationSeconds', 'engagedSeconds', 'aolSeconds'):
    if value.get(key) is not None and _finite(value[key]) is None:
      return False
  for key in ('distractedMoments', 'unresponsiveMoments'):
    if value.get(key) is not None and (type(value[key]) is not int or value[key] < 0):
      return False
  if value.get('model') is not None and (type(value['model']) is not str or len(value['model']) > 120):
    return False
  if value.get('reason') is not None and (type(value['reason']) is not str or len(value['reason']) > 200):
    return False
  if value.get('engagedPercent') is not None and (_finite(value['engagedPercent']) is None or value['engagedPercent'] > 100):
    return False
  if value.get('aolPercent') is not None and (_finite(value['aolPercent']) is None or value['aolPercent'] > 100):
    return False
  if (value.get('aolSeconds') is None) != (value.get('aolPercent') is None):
    return False
  if value.get('aolSeconds') is not None and (value.get('durationSeconds') is None or value['aolSeconds'] > value['durationSeconds']):
    return False
  if type(value.get('sourceSignature')) is not str or len(value['sourceSignature']) > 128:
    return False
  if type(value.get('analysisVersion', 0)) is not int or not 0 <= value.get('analysisVersion', 0) < 2**31:
    return False
  manifest = value.get('sourceManifest')
  if manifest is not None and (not isinstance(manifest, dict) or len(manifest) > MAX_STATS_SEGMENTS_PER_ROUTE * 2 or
      any(type(key) is not str or len(key) > 220 or not isinstance(revision, list) or
          len(revision) != 5 or any(type(part) is not int or part < 0 for part in revision)
          for key, revision in manifest.items())):
    return False
  if value['complete'] and (value.get('startTime') is None or value.get('endTime') is None or
      any(value.get(key) is None for key in ('distanceMeters', 'durationSeconds', 'engagedSeconds')) or
      value['endTime'] < value['startTime'] or value['engagedSeconds'] > value['durationSeconds']):
    return False
  return True


def _route_signature(root: Path, route: dict) -> tuple[str, dict]:
  parts = []
  manifest = {}
  for segment in route.get('segments', []):
    name = segment.get('segmentName', '')
    files = segment.get('files', {})
    for filename in ('rlog.zst', 'rlog.bz2', 'qlog.zst', 'qlog.bz2'):
      try:
        info = (root / name / filename).stat(follow_symlinks=False)
        if stat.S_ISREG(info.st_mode):
          manifest[f'{name}/{filename}'] = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
      except OSError:
        pass
    parts.append((segment.get('number'), name, files))
  encoded = json.dumps(parts, sort_keys=True, separators=(',', ':')).encode()
  source = hashlib.sha256(encoded + json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
  return source, manifest


def _only_deleted_sources(previous: dict, current: dict) -> bool:
  old = previous.get('sourceManifest')
  return isinstance(old, dict) and all(old.get(key) == revision for key, revision in current.items())


def _personal_records(drives: list[dict], zone: tzinfo) -> list[dict]:
  if not drives:
    return []
  ordered = sorted(drives, key=lambda r: (r['startTime'], r['routeId']))
  daily = defaultdict(lambda: {'duration': 0.0, 'engaged': 0.0})
  weekly = defaultdict(float)
  longest = max(drives, key=lambda r: r['distanceMeters'])
  undistracted = []
  clean_streak = best_clean_streak = 0
  for drive in ordered:
    day = datetime.fromtimestamp(drive['startTime'], zone).date()
    daily[day]['duration'] += drive['durationSeconds']
    daily[day]['engaged'] += drive['engagedSeconds']
    weekly[day - timedelta(days=day.weekday())] += drive['distanceMeters']
    if drive.get('distractedMoments') == 0:
      undistracted.append(drive)
    clean = drive.get('unresponsiveMoments') == 0
    if clean:
      clean_streak += 1
      best_clean_streak = max(best_clean_streak, clean_streak)
    else:
      clean_streak = 0
  engaged_days = [(100 * data['engaged'] / data['duration'], day) for day, data in daily.items() if data['duration'] > 0]
  best_day = max(engaged_days, default=(None, None))
  best_week = max(((distance, day) for day, distance in weekly.items()), default=(None, None))
  streak = best_streak = 0
  previous_day = None
  for day in sorted(daily):
    streak = streak + 1 if previous_day is not None and (day - previous_day).days == 1 else 1
    best_streak = max(best_streak, streak)
    previous_day = day
  best_undistracted = max(undistracted, key=lambda r: r['durationSeconds']) if undistracted else None
  return [
    {'id': 'longestDrive', 'label': 'Longest drive', 'value': longest['distanceMeters'], 'unit': 'm'},
    {'id': 'mostEngagedDay', 'label': 'Most engaged day', 'value': round(best_day[0], 1) if best_day[0] is not None else None, 'unit': '%'},
    {'id': 'bestWeek', 'label': 'Best week', 'value': best_week[0], 'unit': 'm'},
    {'id': 'highestStreak', 'label': 'Highest streak', 'value': best_streak, 'unit': 'days'},
    {'id': 'longestUndistractedDrive', 'label': 'Longest undistracted drive',
     'value': best_undistracted['durationSeconds'] if best_undistracted else None, 'unit': 's'},
    {'id': 'cleanDriveStreak', 'label': 'Clean drive streak', 'value': best_clean_streak, 'unit': 'drives'},
  ]


class DriveStatsOwner:
  def __init__(self, root: Path | None = None, store: Path | None = None, history: DriveHistory | None = None,
               analyzer: Callable | None = None, permitted: Callable[[], bool] | None = None,
               clock: Callable[[], float] | None = None, monotonic: Callable[[], float] | None = None,
               metric: Callable[[], bool] | None = None):
    self.root = Path(root) if root is not None else None
    self.store = Path(store) if store is not None else galaxy_storage_root() / 'drive-stats.json'
    self.history = history if history is not None else DriveHistory(self.root, max_segments_per_route=MAX_STATS_SEGMENTS_PER_ROUTE,
                                                                    max_segments=MAX_ROOT_ENTRIES)
    self.root = Path(self.history.root) if self.root is None else self.root
    if analyzer is None:
      from openpilot.starpilot.galaxy.drive_analysis import analyze_route
      analyzer = analyze_route
    self.analyzer = analyzer
    self.permitted = permitted if permitted is not None else lambda: False
    self.clock = clock if clock is not None else lambda: datetime.now(UTC).timestamp()
    self.monotonic = monotonic if monotonic is not None else time.monotonic
    self.metric = metric if metric is not None else lambda: True
    self._lock = threading.RLock()
    self._permit_lock = threading.Lock()
    self._permit_checked_at = float('-inf')
    self._permit_cached = False
    self._closed = threading.Event()
    self._worker: threading.Thread | None = None
    self._scheduler: threading.Thread | None = None
    self._next_scan = 0.0
    self._records: dict[str, dict] = {}
    self._failures: dict[str, dict] = {}
    self._scan_incomplete = False
    self._scanned = False
    self._truncated = False
    self._corrupt = False
    self._pending = 0
    self._cursor = 0
    self._load()

  def _load(self):
    try:
      with self.store.open('rb') as stream:
        raw = stream.read(MAX_CACHE_BYTES + 1)
      if len(raw) > MAX_CACHE_BYTES:
        raise ValueError('Oversized cache')
      data = json.loads(raw)
      records = data['routes']
      if data.get('schemaVersion') != SCHEMA_VERSION or not isinstance(records, dict) or len(records) > MAX_CACHED_ROUTES:
        raise ValueError('Invalid cache')
      if any(key != value.get('routeId') or not _valid_record(value) for key, value in records.items()):
        raise ValueError('Invalid route')
      failures = data.get('failures', {})
      if not isinstance(failures, dict) or len(failures) > MAX_CACHED_ROUTES:
        raise ValueError('Invalid retry cache')
      for key, failure in failures.items():
        if (type(key) is not str or len(key) > 180 or not isinstance(failure, dict) or
            type(failure.get('signature')) is not str or len(failure['signature']) > 128 or
            type(failure.get('attempts')) is not int or not 1 <= failure['attempts'] <= MAX_RETRY_ATTEMPTS or
            any(_finite(failure.get(field)) is None for field in ('firstAt', 'retryAt')) or
            type(failure.get('analysisVersion', 0)) is not int or not 0 <= failure.get('analysisVersion', 0) < 2**31 or
            type(failure.get('reason')) is not str or len(failure['reason']) > 200):
          raise ValueError('Invalid retry record')
      self._failures = failures
      self._records = records
      self._scan_incomplete = data.get('scanIncomplete') is True
      self._scanned = data.get('scanned') is True
      self._truncated = data.get('truncated') is True
    except FileNotFoundError:
      return
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
      self._corrupt = True

  def _save(self):
    self.store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {'schemaVersion': SCHEMA_VERSION, 'routes': self._records,
               'scanned': self._scanned, 'scanIncomplete': self._scan_incomplete, 'truncated': self._truncated,
               'failures': self._failures}
    encoded = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > MAX_CACHE_BYTES:
      raise OSError('Drive statistics cache exceeds storage limit')
    temporary = self.store.with_name(f'.{self.store.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
      fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
      with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
      os.replace(temporary, self.store)
      directory_fd = os.open(self.store.parent, os.O_RDONLY | os.O_DIRECTORY)
      try:
        os.fsync(directory_fd)
      finally:
        os.close(directory_fd)
      self._corrupt = False
    finally:
      try:
        temporary.unlink()
      except FileNotFoundError:
        pass

  def _allowed(self) -> bool:
    if self._closed.is_set():
      return False
    with self._permit_lock:
      if self._closed.is_set():
        return False
      now = self.monotonic()
      if now - self._permit_checked_at >= .1 or now < self._permit_checked_at:
        try:
          self._permit_cached = bool(self.permitted())
        except Exception:
          self._permit_cached = False
        self._permit_checked_at = now
      return self._permit_cached

  def _run(self):
    if not self._allowed():
      return
    assert self.root is not None
    try:
      inventory = self.history.snapshot()
      routes = inventory.get('routes', [])
      if not isinstance(routes, list):
        raise ValueError('Invalid route inventory')
      with self._lock:
        self._scanned = True
        self._scan_incomplete = inventory.get('scanIncomplete') is True or len(routes) > MAX_SCAN_ROUTES
        self._pending = 0
        route_ids = {route.get('routeId') for route in routes if isinstance(route, dict) and type(route.get('routeId')) is str}
        self._failures = {key: value for key, value in self._failures.items() if key in route_ids}
      candidates = []
      for route in routes[:MAX_SCAN_ROUTES]:
        if not self._allowed():
          break
        route_id = route.get('routeId')
        if type(route_id) is not str or len(route_id) > 180:
          continue
        signature, manifest = _route_signature(self.root, route)
        with self._lock:
          existing = self._records.get(route_id)
          failed = self._failures.get(route_id)
          if (existing and existing.get('sourceSignature') == signature and existing.get('complete') and
              existing.get('analysisVersion', 0) >= ANALYSIS_VERSION):
            continue
          if (existing and existing.get('complete') and manifest != existing.get('sourceManifest') and
              _only_deleted_sources(existing, manifest)):
            prior_signature = existing['sourceSignature']
            existing['sourceSignature'] = signature
            try:
              self._save()
            except OSError:
              existing['sourceSignature'] = prior_signature
            continue
          if failed and failed['signature'] == signature and failed.get('analysisVersion', 0) == ANALYSIS_VERSION:
            now = self.clock()
            if (failed['reason'] in SOURCE_LIMITATIONS or failed['attempts'] >= MAX_RETRY_ATTEMPTS or now - failed['firstAt'] >= MAX_RETRY_AGE_SECONDS or
                failed['firstAt'] <= now < failed['retryAt']):
              continue
          candidates.append((route, route_id, signature, manifest))
      with self._lock:
        self._pending = len(candidates)
      if candidates:
        start = self._cursor % len(candidates)
        candidates = candidates[start:] + candidates[:start]
      attempted = 0
      for offset, (route, route_id, signature, manifest) in enumerate(candidates):
        if not self._allowed() or attempted >= MAX_ANALYSES_PER_SCAN:
          break
        with self._lock:
          existing = self._records.get(route_id)
          failed = self._failures.get(route_id)
          if existing and existing.get('complete') and existing.get('sourceSignature') != signature:
            prior = existing.copy()
            existing['complete'] = False
            existing['reason'] = 'sourceChanged'
            try:
              self._save()
            except OSError:
              self._records[route_id] = prior
              raise
        attempted += 1
        self._cursor = (start + offset + 1) % len(candidates)
        try:
          result = self.analyzer(self.root, route, permitted=self._allowed)
          if _route_signature(self.root, route) != (signature, manifest):
            raise ValueError('Route source changed during analysis')
          if not isinstance(result, dict) or result.get('routeId') != route_id:
            raise ValueError('Invalid route analysis')
          if not self._allowed():
            break  # A drive starting or shutdown is not an unreadable recording.
          record = {key: result.get(key) for key in ('routeId', 'startTime', 'endTime', 'distanceMeters',
                    'durationSeconds', 'engagedSeconds', 'engagedPercent', 'aolSeconds', 'aolPercent', 'model',
                    'distractedMoments', 'unresponsiveMoments', 'complete', 'reason', 'segmentCount')}
          record['ignored'] = bool(existing and existing.get('ignored'))
          record['analysisVersion'] = result.get('analysisVersion', ANALYSIS_VERSION)
          record['sourceSignature'] = signature
          record['sourceManifest'] = manifest
          if not _valid_record(record):
            raise ValueError('Invalid route analysis')
          with self._lock:
            if self._closed.is_set():
              break
            current = self._records.get(route_id)
            prior_records = self._records.copy()
            prior_failure = self._failures.get(route_id)
            prior_truncated = self._truncated
            record['ignored'] = bool(current and current.get('ignored'))
            refreshing_complete = (current and current.get('complete') and current.get('sourceSignature') == signature)
            if record['complete'] or not refreshing_complete:
              self._records[route_id] = record
            if record['complete']:
              self._failures.pop(route_id, None)
            else:
              self._failures[route_id] = self._failure(signature, prior_failure, record.get('reason') or 'Incomplete recording')
            self._bound()
            try:
              self._save()
            except OSError:
              self._records = prior_records
              self._truncated = prior_truncated
              if prior_failure is None:
                self._failures.pop(route_id, None)
              else:
                self._failures[route_id] = prior_failure
              raise
        except Exception as error:
          with self._lock:
            self._failures[route_id] = self._failure(signature, failed, type(error).__name__)
            if len(self._failures) > MAX_CACHED_ROUTES:
              self._failures.pop(next(iter(self._failures)))
        finally:
          with self._lock:
            self._pending = max(0, self._pending - 1)
      with self._lock:
        if not self._closed.is_set():
          self._save()
    except Exception:
      with self._lock:
        self._scan_incomplete = True

  def _failure(self, signature, previous, reason):
    now = self.clock()
    same = (previous is not None and previous['signature'] == signature and
            previous.get('analysisVersion', 0) == ANALYSIS_VERSION)
    attempts = min(MAX_RETRY_ATTEMPTS, previous['attempts'] + 1 if same else 1)
    return {'signature': signature, 'analysisVersion': ANALYSIS_VERSION, 'attempts': attempts,
            'firstAt': previous['firstAt'] if same else now,
            'retryAt': now + min(MAX_RETRY_DELAY_SECONDS, RETRY_SECONDS * 2 ** (attempts - 1)),
            'reason': str(reason)[:200]}

  def _bound(self):
    estimated = 1000 + sum(len(key) + len(json.dumps(record, separators=(',', ':'))) + 8
                           for key, record in self._records.items())
    limit = max(1000, MAX_CACHE_BYTES - 1000)
    if len(self._records) <= MAX_CACHED_ROUTES and estimated <= limit:
      return
    ordered = sorted(self._records.values(), key=lambda r: (r.get('startTime') or 0, r['routeId']))
    for record in ordered:
      if len(self._records) <= MAX_CACHED_ROUTES and estimated <= limit:
        break
      del self._records[record['routeId']]
      estimated -= len(record['routeId']) + len(json.dumps(record, separators=(',', ':'))) + 8
      self._truncated = True

  def _start(self):
    with self._lock:
      if self._closed.is_set() or (self._worker is not None and self._worker.is_alive()):
        return
      now = self.monotonic()
      if now < self._next_scan or not self._allowed():
        return
      self._next_scan = now + SCAN_INTERVAL_SECONDS
      self._worker = threading.Thread(target=self._run, name='galaxy-drive-stats', daemon=True)
      self._worker.start()

  def start(self):
    """Start the device-owned analysis lifecycle independently of HTTP requests."""
    with self._lock:
      if self._closed.is_set() or self._scheduler is not None:
        return
      self._scheduler = threading.Thread(target=self._schedule, name='galaxy-drive-history', daemon=True)
      self._scheduler.start()

  def _schedule(self):
    was_allowed = False
    while not self._closed.is_set():
      allowed = self._allowed()
      if allowed:
        if not was_allowed:
          with self._lock:
            self._next_scan = 0.0
        self._start()
      was_allowed = allowed
      self._closed.wait(.1)

  def recording_dates(self, route_ids) -> dict:
    """Read already analyzed capture dates; listing recordings does not start analysis."""
    with self._lock:
      return {identifier: self._records[identifier]['startTime'] for identifier in route_ids
              if identifier in self._records and self._records[identifier].get('startTime') is not None}

  def snapshot(self, timezone: str = "UTC") -> dict:
    with self._lock:
      result = self._snapshot_locked(timezone)
    return result

  def _snapshot_locked(self, timezone: str = "UTC") -> dict:
    from zoneinfo import ZoneInfo
    if type(timezone) is not str or not 1 <= len(timezone) <= 128:
      raise ValueError("Invalid timezone")
    try:
      zone = ZoneInfo(timezone)
    except (KeyError, ValueError) as error:
      raise ValueError("Invalid timezone") from error
    try:
      is_metric = bool(self.metric())
    except Exception:
      is_metric = True
    now = datetime.fromtimestamp(self.clock(), zone)
    day = now.date()
    monday = day - timedelta(days=day.weekday())
    dates = [(monday + timedelta(days=n)).isoformat() for n in range(7)]
    complete = [r for r in self._records.values() if r['complete'] and not r['ignored']]
    complete.sort(key=lambda r: (r.get('startTime') or 0, r['routeId']), reverse=True)
    recent = sorted(self._records.values(), key=lambda r: (r.get('startTime') or 0, r['routeId']), reverse=True)
    week = [r for r in complete if datetime.fromtimestamp(r['startTime'], zone).date().isoformat() in dates]
    daily = defaultdict(float)
    for record in week:
      daily[datetime.fromtimestamp(record['startTime'], zone).date().isoformat()] += record['distanceMeters']
    def sums(drives):
      return {'distanceMeters': sum(r['distanceMeters'] for r in drives),
              'durationSeconds': sum(r['durationSeconds'] for r in drives),
              'engagedSeconds': sum(r['engagedSeconds'] for r in drives), 'drives': len(drives)}
    def aol_sums(drives):
      # Old or gapped recordings do not silently become zero-AOL drives.
      observed = [r for r in drives if r.get('aolSeconds') is not None]
      duration = sum(r['durationSeconds'] for r in observed)
      seconds = sum(r['aolSeconds'] for r in observed)
      return {'aolSeconds': seconds if observed else None,
              'aolPercent': round(100 * seconds / duration, 1) if duration else None,
              'aolDurationSeconds': duration, 'aolDrives': len(observed)}
    totals = sums(complete)
    past_week = sums([r for r in complete if day - timedelta(days=6) <=
                      datetime.fromtimestamp(r['startTime'], zone).date() <= day])
    totals.update(aol_sums(complete))
    week_totals = sums(week)
    week_result = {'days': [{'date': date, 'distanceMeters': daily[date]} for date in dates],
                   'distanceMeters': week_totals['distanceMeters'], 'durationSeconds': week_totals['durationSeconds'],
                   'engagedPercent': round(100 * week_totals['engagedSeconds'] / week_totals['durationSeconds'], 1)
                   if week_totals['durationSeconds'] else None, 'drives': len(week), 'timezone': timezone}
    week_result.update(aol_sums(week))
    model_totals = defaultdict(lambda: {'distanceMeters': 0.0, 'durationSeconds': 0.0, 'drives': 0})
    for record in complete:
      if type(record.get('model')) is str and record['model']:
        model = model_totals[record['model']]
        model['distanceMeters'] += record['distanceMeters']
        model['durationSeconds'] += record['durationSeconds']
        model['drives'] += 1
    models = [{'name': name, **amounts} for name, amounts in model_totals.items()]
    models.sort(key=lambda m: (-m['distanceMeters'], m['name']))
    records = _personal_records(complete, zone)
    running = self._worker is not None and self._worker.is_alive()
    pending = max(self._pending, int(not self._scanned))
    state = ('analyzing' if running else 'waitingParked' if pending and not self._allowed() else
             'queued' if pending else 'retrying' if any(
               f['reason'] not in SOURCE_LIMITATIONS and f['attempts'] < MAX_RETRY_ATTEMPTS and self.clock() - f['firstAt'] < MAX_RETRY_AGE_SECONDS
               for f in self._failures.values()) else 'failed' if self._failures else 'ready')
    def public(record):
      return {key: value for key, value in record.items() if key not in ('sourceSignature', 'sourceManifest')}
    included_recent = [record for record in recent if not record['ignored']]
    return {'schemaVersion': SCHEMA_VERSION, 'isMetric': is_metric,
            'analysis': {'running': running, 'pending': pending, 'state': state,
                         'failed': len(self._failures),
                         'quarantined': sum(f['reason'] in SOURCE_LIMITATIONS or f['attempts'] >= MAX_RETRY_ATTEMPTS or
                                            self.clock() - f['firstAt'] >= MAX_RETRY_AGE_SECONDS for f in self._failures.values()),
                         'failures': [{'routeId': key, 'reason': f['reason'], 'attempts': f['attempts'],
                                       'retryStopped': f['reason'] in SOURCE_LIMITATIONS or f['attempts'] >= MAX_RETRY_ATTEMPTS or
                                                       self.clock() - f['firstAt'] >= MAX_RETRY_AGE_SECONDS}
                                      for key, f in self._failures.items()],
                         'scanIncomplete': self._scan_incomplete},
            'totals': totals, 'pastWeek': past_week, 'week': week_result, 'lastDrive': public(included_recent[0]) if included_recent else None,
            'recentDrives': [public(r) for r in recent[:RECENT_LIMIT]], 'records': records, 'models': models,
            'coverage': {'scope': 'analyzedLocalRoutes', 'timezone': timezone, 'partialHistory': True,
                         'scanIncomplete': self._scan_incomplete, 'scanned': self._scanned,
                         'cachedRoutes': len(self._records), 'completeRoutes': sum(r['complete'] for r in self._records.values()),
                         'incompleteRoutes': sum(not r['complete'] for r in self._records.values()),
                         'ignoredRoutes': sum(r['ignored'] for r in self._records.values()),
                         'truncated': self._truncated, 'cacheCorrupt': self._corrupt}}

  def ignore(self, route_id: str, ignored: bool, authorized: Callable[[], bool]) -> dict:
    if not authorized():
      raise PermissionError('Local Galaxy session expired')
    if type(route_id) is not str or type(ignored) is not bool:
      raise ValueError('Invalid route selection')
    with self._lock:
      record = self._records.get(route_id)
      if record is None:
        raise ValueError('Unknown route')
      if record['ignored'] != ignored:
        prior = record['ignored']
        record['ignored'] = ignored
        try:
          self._save()
        except OSError:
          record['ignored'] = prior
          raise
    return self.snapshot()

  def close(self):
    self._closed.set()
    scheduler = self._scheduler
    if scheduler is not None and scheduler is not threading.current_thread():
      scheduler.join(timeout=2)
    worker = self._worker
    if worker is not None and worker is not threading.current_thread():
      worker.join(timeout=31)
