"""Place-based lockouts and their learning. Local to the device and never part of the route logs."""
import json
import math
import os
import stat
import uuid
from pathlib import Path

from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.uniden.protocol import LOCK_BANDS, RULE_BOUNDS

ROOT = Path('/data/starpilot/uniden')
MAX_LOCKS, MAX_CANDIDATES = 256, 200
MERGE_M, REPEAT_S, EXPIRE_S = 150, 600, 90 * 86400
VISIT_M, MIN_SPEED = 150, 3.0
LEARN_COUNT = {'X': 3, 'K': 3, 'KA': 5, 'LASER': 5}
LOCK_KEYS = {'id', 'source', 'enabled', 'band', 'frequency', 'tolerance_mhz', 'lat', 'lon', 'radius_m', 'heading',
             'heading_tolerance', 'max_strength', 'created', 'seen', 'count', 'misses'}
CANDIDATE_KEYS = {'id', 'band', 'frequency', 'lat', 'lon', 'count', 'misses', 'last', 'max_strength'}
MAX_BYTES = 1 << 20


def distance_m(lat1, lon1, lat2, lon2):
  x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
  return 6371000 * math.hypot(x, math.radians(lat2 - lat1))


def heading_gap(a, b):
  return abs((a - b + 180) % 360 - 180)


def number(value, low, high):
  return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def normalize_lock(lock):
  if type(lock) is not dict or set(lock) != LOCK_KEYS:
    raise ValueError('Invalid lockout')
  band = lock['band']
  if band not in LOCK_BANDS or lock['source'] not in ('tap', 'auto') or type(lock['enabled']) is not bool or type(lock['id']) is not str:
    raise ValueError('Invalid lockout')
  if band == 'LASER':
    if lock['frequency'] is not None:
      raise ValueError('Laser lockouts have no frequency')
  elif not number(lock['frequency'], *RULE_BOUNDS[band]):
    raise ValueError('Invalid lockout frequency')
  if not (number(lock['tolerance_mhz'], 0, 10) and number(lock['lat'], -90, 90) and number(lock['lon'], -180, 180) and
          number(lock['radius_m'], 50, 1000) and number(lock['max_strength'], 1, 8) and number(lock['created'], 0, 1e12) and
          number(lock['seen'], 0, 1e12) and type(lock['count']) is int and lock['count'] >= 1 and
          type(lock['misses']) is int and lock['misses'] >= 0):
    raise ValueError('Invalid lockout')
  if lock['heading'] is not None and not (number(lock['heading'], 0, 360) and number(lock['heading_tolerance'], 10, 180)):
    raise ValueError('Invalid lockout heading')
  return dict(lock)


def normalize_candidate(candidate):
  if type(candidate) is not dict:
    raise ValueError('Invalid learning candidate')
  candidate = {'misses': 0, 'id': uuid.uuid4().hex[:12], **candidate}
  band = candidate.get('band')
  if set(candidate) != CANDIDATE_KEYS or type(candidate['id']) is not str or band not in LOCK_BANDS:
    raise ValueError('Invalid learning candidate')
  if not ((candidate['frequency'] is None) if band == 'LASER' else number(candidate['frequency'], *RULE_BOUNDS[band])):
    raise ValueError('Invalid learning candidate')
  if not (number(candidate['lat'], -90, 90) and number(candidate['lon'], -180, 180) and number(candidate['last'], 0, 1e12) and
          number(candidate['max_strength'], 1, 8) and type(candidate['count']) is int and candidate['count'] >= 1 and
          type(candidate['misses']) is int and candidate['misses'] >= 0):
    raise ValueError('Invalid learning candidate')
  return candidate


def valid_entries(normalize, items):
  result = []
  for item in items if type(items) is list else []:
    try:
      result.append(normalize(item))
    except (TypeError, ValueError):
      continue
  return result


def read_store(path):
  fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      raise ValueError('Lockout store is not a regular file')
    raw = os.read(fd, MAX_BYTES + 1)
  finally:
    os.close(fd)
  if len(raw) > MAX_BYTES:
    raise ValueError('Lockout store is too large')
  data = json.loads(raw)
  return data if type(data) is dict else {}


def new_lock(source, event, now, **fields):
  lock = {'id': uuid.uuid4().hex[:12], 'source': source, 'enabled': True, 'band': event['band'], 'frequency': event['frequency'],
          'tolerance_mhz': 0 if event['band'] == 'LASER' else 3, 'lat': event['lat'], 'lon': event['lon'], 'radius_m': 300,
          'heading': None, 'heading_tolerance': 60, 'max_strength': 8, 'created': int(now), 'seen': int(now), 'count': 1, 'misses': 0}
  lock.update(fields)
  return normalize_lock(lock)


def matches_lock(alert, lock, context):
  if context is None or not lock['enabled'] or alert['band'] != lock['band']:
    return False
  if lock['band'] != 'LASER':
    frequency = alert.get('frequency')
    if frequency is None or abs(frequency - lock['frequency']) > lock['tolerance_mhz'] / 1000 + 1e-9:
      return False
  # A signal stronger than any seen here is not the known source.
  if alert['strength'] > lock['max_strength'] + 2:
    return False
  if distance_m(context['lat'], context['lon'], lock['lat'], lock['lon']) > lock['radius_m']:
    return False
  return lock['heading'] is None or (context.get('heading') is not None and
                                     heading_gap(context['heading'], lock['heading']) <= lock['heading_tolerance'])


def same_signal(a, b):
  if a['band'] != b['band']:
    return False
  if a['frequency'] is None or b['frequency'] is None:
    return a['frequency'] is b['frequency'] or a['frequency'] == b['frequency']
  return abs(a['frequency'] - b['frequency']) <= .003 + 1e-9


class LockStore:
  def __init__(self, root=ROOT):
    self.path = Path(root) / 'locks.json'
    self.visits = {}
    try:
      data = read_store(self.path)
    except FileNotFoundError:
      data = {}
    except (OSError, ValueError):
      cloudlog.exception('uniden: unreadable lockout store')
      data = {}
    self.locks = valid_entries(normalize_lock, data.get('locks'))[:MAX_LOCKS]
    self.candidates = valid_entries(normalize_candidate, data.get('candidates'))[:MAX_CANDIDATES]

  def save(self, locks=None):
    self.path.parent.mkdir(parents=True, exist_ok=True)
    temporary = self.path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'locks': self.locks if locks is None else locks, 'candidates': self.candidates}))
    os.replace(temporary, self.path)

  def active(self):
    return [dict(lock) for lock in self.locks if lock['enabled']]

  def add(self, lock):
    if len(self.locks) >= MAX_LOCKS:
      raise ValueError('Remove a lockout before adding another')
    locks = [*self.locks, lock]
    self.save(locks)
    self.locks = locks

  def remove(self, lock_id):
    kept = [lock for lock in self.locks if lock['id'] != lock_id]
    if len(kept) == len(self.locks):
      raise ValueError('Unknown lockout')
    self.save(kept)
    self.locks = kept

  def prune(self, now):
    self.locks = [lock for lock in self.locks if lock['source'] == 'tap' or now - lock['seen'] < EXPIRE_S]
    self.candidates = sorted((c for c in self.candidates if now - c['last'] < EXPIRE_S), key=lambda c: -c['last'])[:MAX_CANDIDATES]

  def observe(self, event, *, learn, bands):
    """A confirmed pass of a fixed source. Returns the lockout it just earned, if any."""
    now = event['at']
    self.prune(now)
    for lock in self.locks:
      if same_signal(lock, event) and distance_m(event['lat'], event['lon'], lock['lat'], lock['lon']) <= lock['radius_m']:
        self.visits[lock['id']] = True
        if now - lock['seen'] >= REPEAT_S:
          lock['seen'], lock['count'] = int(now), lock['count'] + 1
          if lock['source'] == 'auto':
            lock['max_strength'] = max(lock['max_strength'], event['strength'])
          self.save()
        return None
    candidate = next((c for c in self.candidates if same_signal(c, event) and
                      distance_m(event['lat'], event['lon'], c['lat'], c['lon']) <= MERGE_M), None)
    if candidate is None:
      new_id = uuid.uuid4().hex[:12]
      self.visits[new_id] = True
      self.candidates.append({'id': new_id, 'band': event['band'], 'frequency': event['frequency'], 'lat': event['lat'],
                              'lon': event['lon'], 'count': 1, 'misses': 0, 'last': now, 'max_strength': event['strength']})
      self.save()
      return None
    self.visits[candidate['id']] = True
    if now - candidate['last'] < REPEAT_S:
      return None
    count = candidate['count']
    candidate['lat'] = (candidate['lat'] * count + event['lat']) / (count + 1)
    candidate['lon'] = (candidate['lon'] * count + event['lon']) / (count + 1)
    candidate.update(count=count + 1, last=now, max_strength=max(candidate['max_strength'], event['strength']))
    lock = None
    if (learn and event['band'] in bands and candidate['count'] >= LEARN_COUNT[event['band']] and
        candidate['misses'] * 2 <= candidate['count'] and len(self.locks) < MAX_LOCKS):
      lock = new_lock('auto', candidate, now, count=candidate['count'],
                      max_strength=min(8, candidate['max_strength']))
      self.locks.append(lock)
      self.visits[lock['id']] = True
      self.candidates.remove(candidate)
    self.save()
    return lock

  def recentre(self, event, now, window):
    """A lockout held on approach moves to where the source really is once the car has passed it."""
    moved = False
    for lock in self.locks:
      if lock['source'] == 'tap' and now - lock['created'] < window and same_signal(lock, event):
        lock['lat'], lock['lon'], moved = event['lat'], event['lon'], True
    if moved:
      self.save()

  def visit(self, alerts, context):
    """Counts each drive-by of a known place where the signal never appeared; a regular patrol spot is not a fixed source."""
    if context is None or context['speed'] < MIN_SPEED:
      return
    changed = False
    for item in [*self.candidates, *(lock for lock in self.locks if lock['source'] == 'auto')]:
      near = distance_m(context['lat'], context['lon'], item['lat'], item['lon']) <= VISIT_M
      if near:
        self.visits[item['id']] = self.visits.get(item['id'], False) or any(same_signal(item, alert) for alert in alerts)
      elif item['id'] in self.visits and not self.visits.pop(item['id']):
        item['misses'] += 1
        changed = True
    if changed:
      self.candidates = [c for c in self.candidates if c['misses'] <= c['count']]
      self.locks = [lock for lock in self.locks if lock['source'] == 'tap' or lock['misses'] <= lock['count']]
      self.save()
