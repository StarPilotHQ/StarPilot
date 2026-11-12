"""Bounded latest completed LOCAL drive input for the parked tuner; no downloads."""

from dataclasses import dataclass
from functools import wraps
import hashlib
import json
from pathlib import Path
import re
import time

from openpilot.starpilot.flm.log_decode import decode_full_rlog_segment
from openpilot.starpilot.galaxy.drive_history import DriveHistory
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


def _unusable_drive_is_value_error(fn):
  """Report drive_analysis's _Incomplete as this module's ValueError.

  _selected/_route_names/_closed_source raise _Incomplete (a plain Exception) for a
  drive that cannot be analysed, e.g. a route whose first segment is missing. The
  parked callers handle ValueError, OSError and RuntimeError only, so an escaping
  _Incomplete killed offroad_daemon, and manager restarted it into the same drive
  every ~30 s.
  """
  @wraps(fn)
  def wrapper(*args, **kwargs):
    try:
      return fn(*args, **kwargs)
    except _Incomplete as error:
      raise ValueError(str(error)) from error
  return wrapper


@_unusable_drive_is_value_error
def latest_route(params, root, *, permitted):
  """CurrentRoute is logger-owned. After restart use its persistent counter.

  File mtimes, directory access order and fitting outcomes never pick a drive.
  Imported/downloaded dongle-prefixed routes cannot impersonate a local drive.
  """
  if not permitted():
    raise ValueError('Parked analysis is not permitted')
  raw, readable = read_saved(params, 'CurrentRoute', 180)
  counter, okay = read_saved(params, 'RouteCount', 32)
  if not readable or not okay:
    raise ValueError('Unreadable local logger identity')
  route_id = None if raw is None else raw.decode('ascii')
  if route_id is not None and not LOCAL_ID.fullmatch(route_id):
    raise ValueError('Invalid logger route identity')
  inventory = DriveHistory(Path(root), max_segments_per_route=MAX_SEGMENTS).snapshot()
  if inventory['scanIncomplete']:
    raise ValueError('Local drive inventory is incomplete')
  if route_id is None:
    if counter is None or not counter.isdigit() or not 0 < int(counter) <= 2**32:
      raise ValueError('Last logger counter is unavailable')
    prefix = f'{(int(counter) - 1) % (2**32):08x}--'
    choices = [r['routeId'] for r in inventory['routes'] if LOCAL_ID.fullmatch(r['routeId']) and r['routeId'].startswith(prefix)]
    if len(choices) != 1:
      raise ValueError('Last local drive is missing or ambiguous')
    route_id = choices[0]
  choices = [r for r in inventory['routes'] if r['routeId'] == route_id]
  if len(choices) != 1:
    raise ValueError('Latest drive is open, incomplete or unavailable')
  route = choices[0]
  selected_id, names = _selected(route)
  if selected_id != route_id or len(names) > MAX_SEGMENTS or any(not s['files']['rlog'] for s in route['segments']):
    raise ValueError('All original dense segments required')
  if set(names) != _route_names(Path(root), route_id, permitted):
    raise ValueError('Drive inventory changed')
  if read_saved(params, 'CurrentRoute', 180) != (raw, True) or read_saved(params, 'RouteCount', 32) != (counter, True):
    raise ValueError('A new drive started during selection')
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


@_unusable_drive_is_value_error
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
