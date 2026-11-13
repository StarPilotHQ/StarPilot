"""Bounded latest completed LOCAL drive input for the parked tuner; no downloads."""

from dataclasses import dataclass
from functools import wraps
import hashlib
import json
from pathlib import Path
import re
import time

from openpilot.starpilot.flm.log_decode import LogDecodeCancelled, decode_full_rlog_segment
from openpilot.starpilot.galaxy.drive_history import DriveHistory, DriveHistoryUnavailable
from openpilot.starpilot.galaxy.drive_analysis import _Incomplete, _selected, _route_names, _closed_source
from openpilot.starpilot.saved_source import read_saved

RETAIN = frozenset(
  (
    'initData',
    'carParams',
    'carState',
    'carControl',
    'carOutput',
    'controlsState',
    'starpilotLateralState',
    'starpilotCarState',
    'autoSteeringLearning',
    'clocks',
    'sentinel',
  )
)
MAX_SEGMENTS = 180
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
# A 103-segment, 1.34 GB drive takes over 30 minutes on parked low-power cores.
# Keep the full-drive checks and a finite one-hour ceiling; do not accept a prefix.
MAX_ANALYSIS_SECONDS = 3600.0
LOCAL_ID = re.compile(r'[0-9a-f]{8}--[0-9a-f]{10}')


class RouteInputError(ValueError):
  """One recording is unavailable; none of its partial evidence may escape."""

  def __init__(self, route_id, reason):
    self.route_id = route_id
    super().__init__(reason)


class RouteInputCancelled(ValueError):
  pass


def route_permission(permitted):
  """A failed authority callback cannot turn a good recording into bad input."""
  lost = False

  def allowed():
    nonlocal lost
    if lost:
      return False
    try:
      okay = bool(permitted())
    except Exception as error:
      lost = True
      raise RouteInputCancelled(f'Parked permission unavailable: {error}'[:240]) from error
    lost = not okay
    return okay

  return allowed


def route_reader(function):
  @wraps(function)
  def read(root, route, *, permitted, **kwargs):
    permitted = route_permission(permitted)
    lost_permission = False

    def allowed():
      nonlocal lost_permission
      okay = permitted()
      lost_permission |= not okay
      return okay and not lost_permission

    try:
      return function(root, route, permitted=allowed, **kwargs)
    except (LogDecodeCancelled, RouteInputCancelled):
      raise
    except RouteInputError:
      if lost_permission:
        raise RouteInputCancelled('Parked route input collection cancelled') from None
      raise
    except (OSError, ValueError, RuntimeError, _Incomplete) as error:
      if lost_permission or not permitted():
        raise RouteInputCancelled('Parked route input collection cancelled') from error
      raise RouteInputError(route['routeId'], str(error)) from error

  return read


def route_inventory(root):
  try:
    inventory = DriveHistory(Path(root), max_segments_per_route=MAX_SEGMENTS).snapshot(isolate_route_errors=True)
  except DriveHistoryUnavailable as error:
    raise ValueError('Local drive inventory is unavailable') from error
  if inventory['scanIncomplete']:
    raise ValueError('Local drive inventory is incomplete')
  return inventory


def logger_marker(params):
  return read_saved(params, 'CurrentRoute', 180), read_saved(params, 'RouteCount', 32)


def source_revisions(root, route):
  """Failure memoization and repair detection only; never proof of log contents."""
  paths = [Path(root)]
  for segment in route.get('segments', ()):
    name = segment.get('segmentName')
    if type(name) is str and '/' not in name and name not in ('.', '..'):
      path = Path(root) / name
      paths.extend((path, *(path / name for name in ('rlog.zst', 'rlog.bz2', 'qlog.zst', 'qlog.bz2'))))
  revisions = []
  for number, path in enumerate(paths):
    try:
      value = path.lstat()
      revisions.append((value.st_dev, value.st_ino, value.st_mode) if number == 0 else
                       (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns))
    except OSError as error:
      revisions.append(('unavailable', error.errno))
  return revisions


def latest_route(params, root, *, permitted, inventory_only=False, on_skip=None):
  """CurrentRoute is logger-owned. After restart use its persistent counter.

  File mtimes, directory access order and fitting outcomes never pick a drive.
  Imported/downloaded dongle-prefixed routes cannot impersonate a local drive.
  """
  permitted = route_permission(permitted)
  if not permitted():
    raise ValueError('Parked analysis is not permitted')
  raw, readable = read_saved(params, 'CurrentRoute', 180)
  counter, okay = read_saved(params, 'RouteCount', 32)
  if not readable or not okay:
    raise ValueError('Unreadable local logger identity')
  route_id = None if raw is None else raw.decode('ascii')
  if route_id is not None and not LOCAL_ID.fullmatch(route_id):
    raise ValueError('Invalid logger route identity')
  inventory = route_inventory(root)
  anchor = int(route_id.split('--')[0], 16) if route_id is not None else None

  def unchanged():
    if logger_marker(params) != ((raw, True), (counter, True)):
      raise ValueError('A new drive started during selection')

  def unavailable(reason):
    unchanged()
    if not inventory_only:
      raise RouteInputError(route_id, reason)
    return {'routeId': route_id, 'loggerCounter': anchor, 'segments': [], 'selection_error': reason}

  if route_id is None:
    if counter is None or not counter.isdigit() or not 0 < int(counter) <= 2**32:
      raise ValueError('Last logger counter is unavailable')
    prefix = f'{(int(counter) - 1) % (2**32):08x}--'
    anchor = (int(counter) - 1) % (2**32)
    identities = {r['routeId'] for r in [*inventory['routes'], *inventory.get('routeErrors', [])]
               if LOCAL_ID.fullmatch(r['routeId']) and r['routeId'].startswith(prefix)}
    if len(identities) != 1:
      return unavailable('Last local drive is missing or ambiguous')
    route_id = next(iter(identities))
  if on_skip is not None:
    assert anchor is not None
    for row in inventory.get('routeErrors', ()):
      name = row['routeId']
      if LOCAL_ID.fullmatch(name) and (anchor - int(name.split('--')[0], 16)) % 2**32 < 8:
        on_skip(name, row['reason'])
  choices = [r for r in inventory['routes'] if r['routeId'] == route_id]
  if len(choices) != 1:
    return unavailable('Latest drive is open, incomplete or unavailable')
  route = choices[0]
  try:
    selected_id, names = _selected(route)
    if selected_id != route_id or len(names) > MAX_SEGMENTS or any(not s['files']['rlog'] for s in route['segments']):
      raise ValueError('All original dense segments required')
    if set(names) != _route_names(Path(root), route_id, permitted):
      raise ValueError('Drive inventory changed')
  except RouteInputCancelled:
    raise
  except (ValueError, OSError, _Incomplete) as error:
    if not permitted():
      raise RouteInputCancelled('Parked route selection cancelled') from error
    if not inventory_only:
      raise RouteInputError(route_id, str(error)) from error
    route = dict(route, selection_error=str(error))
  unchanged()
  return route


@dataclass(frozen=True)
class RouteReceipt:
  route_id: str
  source_sha256: str
  segments: tuple
  cp_sha256: str
  original_start_ns: int
  original_end_ns: int
  completed_wall_ns: int
  original_controller_contexts: tuple


@dataclass(frozen=True)
class SelectedSourceReceipt(RouteReceipt):
  """Whole-file identity with selected dense inputs, NOT a whole-drive assessment."""

  decoded_segments: tuple = ()
  whole_route_decoded: bool = False


@route_reader
def walk_completed_route(root, route, *, permitted, consume):
  """Process complete segments one at a time, then revalidate all source identities.

  consume receives original readers only during a segment. It must not write or
  apply a tune. No successful receipt escapes an incomplete/cancelled drive.
  """
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  from openpilot.starpilot.flm.offroad_evidence import torque_vehicle

  root = Path(root)
  route_id, names = _selected(route)
  if len(names) > MAX_SEGMENTS or set(names) != _route_names(root, route_id, permitted):
    raise ValueError('Complete original route required')
  started = time.monotonic()
  receipts = []
  revisions = []
  total = 0
  cp_hash = None
  source_start = None
  source_end = 0
  last_clock = None
  contexts = set()
  ended = False

  def allowed():
    return permitted() and time.monotonic() - started <= MAX_ANALYSIS_SECONDS

  for number, name in enumerate(names):
    if not allowed():
      raise ValueError('Parked analysis cancelled')
    expected = route['segments'][number]['files']
    with _closed_source(root, name, expected, allowed, prefer_rlog=True) as (raw, codec, current, revision):
      total += len(raw)
      if total > MAX_TOTAL_BYTES:
        raise ValueError('Completed drive exceeds bounded input size')
      events = decode_full_rlog_segment(raw, codec, cancelled=lambda: not allowed(), retain=RETAIN)
      digest = hashlib.sha256(raw).hexdigest()
      for event in events:
        kind = event.which()
        stamp = int(event.logMonoTime)
        # Repeated initData has the route's original initialization stamp.
        if kind not in ('initData', 'carParams', 'sentinel') and stamp > 0:
          source_start = stamp if source_start is None else min(source_start, stamp)
          source_end = max(source_end, stamp)
        if kind == 'carParams':
          cp = event.carParams
          actual = car_params_digest(cp)
          if not torque_vehicle(cp) or cp_hash is not None and cp_hash != actual:
            raise ValueError('Non-torque or changing original vehicle')
          cp_hash = actual
        elif kind == 'starpilotLateralState' and event.valid:
          value = event.starpilotLateralState.learningEvidence
          if value.version == 1:
            contexts.add((str(value.controllerMode), str(value.controllerRevision), str(value.baselineRevision)))
        elif kind == 'clocks' and event.valid and int(event.clocks.wallTimeNanos) > 946684800_000_000_000:
          last_clock = (stamp, int(event.clocks.wallTimeNanos))
        elif kind == 'sentinel' and str(event.sentinel.type) == 'endOfRoute':
          if number != len(names) - 1:
            raise ValueError('Route ended before its final original segment')
          ended = True
      consume(number, events)
      if not current() or not allowed():
        raise ValueError('Recording changed during analysis')
      receipts.append((name, digest, len(raw)))
      revisions.append(revision)
  if not ended or cp_hash is None or source_start is None or not source_start < source_end:
    raise ValueError('Incomplete original drive or missing vehicle identity')
  if last_clock is None or not 0 <= source_end - last_clock[0] <= 10_000_000_000:
    raise ValueError('Original completion wall-clock evidence unavailable')
  if set(names) != _route_names(root, route_id, allowed):
    raise ValueError('Route changed after analysis')
  for number, name in enumerate(names):
    with _closed_source(root, name, route['segments'][number]['files'], allowed, inspect_only=True, prefer_rlog=True) as (_, _, current, revision):
      if revision != revisions[number] or not current():
        raise ValueError('Original drive source revision changed')
  if not allowed():
    raise ValueError('Parked authority lost before completing receipt')
  digest = hashlib.sha256(json.dumps((route_id, receipts), separators=(',', ':')).encode()).hexdigest()
  return RouteReceipt(route_id, digest, tuple(receipts), cp_hash, source_start, source_end, last_clock[1], tuple(sorted(contexts)))
