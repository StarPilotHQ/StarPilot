"""Measured statistics from a bounded, immutable local route recording."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import stat
import time

from openpilot.starpilot.flm.local_logs import DIR_FLAGS, FILE_FLAGS, MAX_COMPRESSED_BYTES, LocalLogUnavailable
from openpilot.starpilot.flm.log_decode import LogDecodeError, decode_segment
from openpilot.starpilot.galaxy.lateral_stats import ControlSamples, aol_statistics
from openpilot.starpilot.galaxy.drive_history import MAX_ROOT_ENTRIES, MAX_SEGMENT_ENTRIES, MAX_STATS_SEGMENTS_PER_ROUTE, SEGMENT_NAME
from openpilot.starpilot.models.catalog import model_entries
from openpilot.starpilot.models.receipt import logged_model_load
from openpilot.starpilot.models.status import ModelVariant


MAX_ANALYSIS_SECONDS = 30.0
MAX_PAIR_SECONDS = 1.0
MAX_SPEED_MPS = 70.0
MAX_ROUTE_COMPRESSED_BYTES = 256 * 1024 * 1024
ANALYSIS_VERSION = 5
MAX_MODEL_METADATA_SECONDS = 4.0
DISTRACTED_EVENT = 'driverDistracted2'
UNRESPONSIVE_EVENT = 'driverUnresponsive3'
LOGS = {'qlog.zst': 'zst', 'qlog.bz2': 'bz2', 'rlog.zst': 'zst', 'rlog.bz2': 'bz2'}


class _Incomplete(Exception):
  pass


def _revision(info: os.stat_result) -> tuple[int, ...]:
  return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def _closed_source(root: Path, segment_name: str, expected: dict, allowed: Callable[[], bool], *, inspect_only: bool = False,
                   prefer_rlog: bool = False):
  fds: list[int] = []
  try:
    root_fd = os.open(root, DIR_FLAGS)
    fds.append(root_fd)
    segment_fd = os.open(segment_name, DIR_FLAGS, dir_fd=root_fd)
    fds.append(segment_fd)
    root_identity = os.fstat(root_fd).st_dev, os.fstat(root_fd).st_ino
    segment_identity = os.fstat(segment_fd).st_dev, os.fstat(segment_fd).st_ino
    found: list[str] = []
    with os.scandir(segment_fd) as entries:
      for count, entry in enumerate(entries, 1):
        if not allowed():
          raise _Incomplete('Analysis cancelled')
        if count > MAX_SEGMENT_ENTRIES or entry.name.endswith('.lock'):
          raise _Incomplete('Segment is open or unavailable')
        if entry.name in LOGS:
          found.append(entry.name)
    preferred = [name for name in found if name.startswith('rlog.' if prefer_rlog else 'qlog.')]
    if any(name.startswith('qlog.') for name in found) != (expected.get('qlog') is True) or \
       any(name.startswith('rlog.') for name in found) != (expected.get('rlog') is True):
      raise _Incomplete('Route source changed')
    if len(preferred) > 1 or (not preferred and len(found) != 1):
      raise _Incomplete('One closed qlog or full rlog is required')
    filename = preferred[0] if preferred else found[0]
    fd = os.open(filename, FILE_FLAGS, dir_fd=segment_fd)
    fds.append(fd)
    initial = os.fstat(fd)
    if not stat.S_ISREG(initial.st_mode) or not 0 < initial.st_size <= MAX_COMPRESSED_BYTES:
      raise _Incomplete('Route log is empty, oversized or unavailable')
    revision = _revision(initial)
    evidence = (root_identity, segment_identity, filename, revision, tuple(sorted(found)))

    def current() -> bool:
      try:
        root_now = os.stat(root, follow_symlinks=False)
        segment_now = os.stat(segment_name, dir_fd=root_fd, follow_symlinks=False)
        source_now = os.stat(filename, dir_fd=segment_fd, follow_symlinks=False)
        if not stat.S_ISDIR(root_now.st_mode) or not stat.S_ISDIR(segment_now.st_mode) or \
           not stat.S_ISREG(source_now.st_mode) or \
           (root_now.st_dev, root_now.st_ino) != root_identity or \
           (segment_now.st_dev, segment_now.st_ino) != segment_identity or \
           _revision(source_now) != revision or _revision(os.fstat(fd)) != revision:
          return False
        with os.scandir(segment_fd) as entries:
          names = []
          for count, entry in enumerate(entries, 1):
            if count > MAX_SEGMENT_ENTRIES or entry.name.endswith('.lock'):
              return False
            if entry.name in LOGS:
              names.append(entry.name)
          return sorted(names) == sorted(found)
      except OSError:
        return False

    if not current():
      raise _Incomplete('Route source changed')
    if inspect_only:
      yield None, LOGS[filename], current, evidence
      return
    chunks: list[bytes] = []
    remaining = initial.st_size
    while remaining:
      if not allowed():
        raise _Incomplete('Analysis cancelled')
      chunk = os.read(fd, min(1024 * 1024, remaining))
      if not chunk:
        raise _Incomplete('Route source changed')
      chunks.append(chunk)
      remaining -= len(chunk)
    if os.read(fd, 1) or not current():
      raise _Incomplete('Route source changed')
    yield b''.join(chunks), LOGS[filename], current, evidence
  except OSError as error:
    raise _Incomplete('Route source is unavailable') from error
  finally:
    for fd in reversed(fds):
      os.close(fd)


def _route_names(root: Path, route_id: str, allowed: Callable[[], bool]) -> set[str]:
  names: set[str] = set()
  try:
    with os.scandir(root) as entries:
      for count, entry in enumerate(entries, 1):
        if not allowed():
          raise _Incomplete('Analysis cancelled')
        if count > MAX_ROOT_ENTRIES:
          raise _Incomplete('Route inventory is incomplete')
        matched = SEGMENT_NAME.fullmatch(entry.name)
        if matched is not None and matched.group('route').replace('_', '|') == route_id:
          names.add(entry.name)
  except OSError as error:
    raise _Incomplete('Route directory is unavailable') from error
  return names


def _selected(route: dict) -> tuple[str, list[str]]:
  if not isinstance(route, dict):
    raise _Incomplete('Invalid route selection')
  route_id = route.get('routeId')
  segments = route.get('segments')
  if type(route_id) is not str or type(segments) is not list or not 1 <= len(segments) <= MAX_STATS_SEGMENTS_PER_ROUTE or \
     route.get('segmentCount') != len(segments):
    raise _Incomplete('Invalid route selection')
  names: list[str] = []
  for index, segment in enumerate(segments):
    if not isinstance(segment, dict) or segment.get('number') != index or \
       not isinstance(segment.get('files'), dict) or \
       not (segment['files'].get('qlog') is True or segment['files'].get('rlog') is True):
      raise _Incomplete('Route has missing segments or logs')
    name = segment.get('segmentName')
    matched = SEGMENT_NAME.fullmatch(name) if type(name) is str and len(name) <= 180 else None
    if matched is None or matched.group('route').replace('_', '|') != route_id or int(matched.group('number')) != index:
      raise _Incomplete('Invalid route selection')
    assert isinstance(name, str)
    names.append(name)
  if len(set(names)) != len(names):
    raise _Incomplete('Duplicate route segment')
  return route_id, names


def _route_wall_time(route_id: str) -> float | None:
  token = route_id.rsplit('|', maxsplit=1)[-1]
  try:
    if len(token) != 20 or token[4] != '-' or token[10:12] != '--':
      return None
    value = datetime.strptime(token, '%Y-%m-%d--%H-%M-%S').replace(tzinfo=UTC).timestamp()
    return value if 946684800 <= value <= datetime.now(UTC).timestamp() + 86400 else None
  except ValueError:
    return None


def _recorded_model(init_data) -> str | None:
  try:
    values = {str(entry.key): bytes(entry.value).decode('utf-8', 'replace').strip()
              for entry in init_data.params.entries if str(entry.key) in ('DrivingModelName', 'DrivingModel', 'Model')}
  except (AttributeError, TypeError, ValueError):
    return None
  for key in ('DrivingModelName', 'DrivingModel', 'Model'):
    value = values.get(key, '')
    if 0 < len(value) <= 100 and value.isprintable():
      return value
  return None


def _model_label(outputs, loads, selected):
  """Join actual output variants to recorded load boundaries; saved intent is not execution."""
  if not outputs:
    return f'Selected: {selected}' if selected else None
  loads = sorted(set(loads), key=lambda item: item[0])
  labels = []
  index = -1
  for stamp, big in sorted(set(outputs)):
    while index + 1 < len(loads) and loads[index + 1][0] <= stamp:
      index += 1
    load = loads[index][1] if index >= 0 else None
    variant = 'Chestnut big' if big else 'Small'
    if load is not None and (load.variant is ModelVariant.CHESTNUT) == big:
      entry = model_entries().get(load.model_id)
      label = f'{entry.name if entry is not None else load.model_id} ({variant})'
    else:
      label = f'{variant} model (identity not recorded)'
    if label not in labels:
      labels.append(label)
  combined = ' → '.join(labels)
  return combined if len(combined) <= 120 else 'Multiple recorded models'


def _rlog_model_loads(root, route, names, allowed):
  """Optional exact identities. A missing/oversized log never prevents drive statistics."""
  deadline = time.monotonic() + MAX_MODEL_METADATA_SECONDS
  def permitted():
    return time.monotonic() < deadline and allowed()
  loads, evidence = [], []
  total = 0
  try:
    for index, name in enumerate(names):
      files = route['segments'][index]['files']
      if not files.get('rlog'):
        return []  # Missing intervals cannot prove which model ran for the whole route.
      with _closed_source(root, name, files, permitted, prefer_rlog=True) as (compressed, codec, current, revision):
        if not current():
          return []
      total += len(compressed)
      if total > MAX_ROUTE_COMPRESSED_BYTES:
        return []
      for event in decode_segment(compressed, codec, cancelled=lambda: not permitted()):
        if event.valid and event.which() == 'logMessage':
          raw = str(event.logMessage)
          if (load := logged_model_load(raw)) is not None and 0 < load.loaded_mono_ns <= event.logMonoTime:
            loads.append((load.loaded_mono_ns, load))
          elif len(raw) <= 8192:
            try:
              record = json.loads(raw)
              if isinstance(record, dict) and record.get('msg') == 'modeld init' and record.get('module') == 'modeld':
                loads.append((int(event.logMonoTime), None))
            except (ValueError, TypeError, RecursionError):
              pass
      evidence.append(revision)
    for index, name in enumerate(names):
      with _closed_source(root, name, route['segments'][index]['files'], permitted,
                          inspect_only=True, prefer_rlog=True) as (_, _, current, revision):
        if not current() or revision != evidence[index]:
          return []
    return loads
  except (_Incomplete, LocalLogUnavailable, LogDecodeError, OSError, ValueError):
    return []


def analyze_route(root: Path, route: dict, *, permitted: Callable[[], bool], prefer_rlog: bool = False) -> dict:
  """Return a complete route only when every selected source and sample span is sound."""
  result = {'routeId': route.get('routeId') if isinstance(route, dict) else None,
            'startTime': None, 'endTime': None, 'distanceMeters': None, 'durationSeconds': None,
            'engagedSeconds': None, 'engagedPercent': None, 'aolSeconds': None, 'aolPercent': None, 'model': None,
            'distractedMoments': None, 'unresponsiveMoments': None,
            'complete': False, 'reason': None, 'segmentCount': 0, 'analysisVersion': ANALYSIS_VERSION}
  segment_count = len(route.get('segments', [])) if isinstance(route, dict) else 0
  deadline = time.monotonic() + min(120.0, max(60.0, segment_count * 14.0))
  def allowed() -> bool:
    if time.monotonic() > deadline:
      return False
    try:
      return permitted()
    except Exception:
      return False
  try:
    if not isinstance(root, Path) or not root.is_absolute() or '..' in root.parts:
      raise _Incomplete('Invalid recording root')
    if not allowed():
      raise _Incomplete('Analysis cancelled')
    route_id, names = _selected(route)
    result['segmentCount'] = len(names)
    if _route_names(root, route_id, allowed) != set(names):
      raise _Incomplete('Route segments changed or are incomplete')

    first_car = last_car = first_state = last_state = None
    previous_car = previous_state = None
    control_samples = ControlSamples()
    distance = engaged = 0.0
    car_pairs = state_pairs = 0
    coverage_gap = False
    end_of_route = False
    clocks_offset = None
    model = None
    model_outputs = []
    model_loads = []
    previous_events: set[str] = set()
    distracted = unresponsive = 0
    first_attention = last_attention = None
    attention_gap = False
    compressed_total = 0
    evidence = []
    for index, name in enumerate(names):
      if not allowed():
        raise _Incomplete('Analysis cancelled')
      with _closed_source(root, name, route['segments'][index]['files'], allowed, prefer_rlog=prefer_rlog) as (compressed, codec, current, source_evidence):
        if not current():
          raise _Incomplete('Route source changed')
      evidence.append(source_evidence)
      compressed_total += len(compressed)
      if compressed_total > MAX_ROUTE_COMPRESSED_BYTES:
        raise _Incomplete('Route source changed or exceeds analysis limit')
      events = decode_segment(compressed, codec, cancelled=lambda: not allowed(),
                              retain=frozenset(('clocks', 'initData', 'logMessage', 'modelV2', 'drivingModelData',
                                                'carState', 'selfdriveState', 'carControl', 'onroadEvents', 'sentinel')))
      segment_end = False
      for event in events:
        if not allowed():
          raise _Incomplete('Analysis cancelled')
        kind = event.which()
        stamp = int(event.logMonoTime)
        if kind == 'carControl':
          control_samples.append(stamp, bool(event.valid), bool(event.carControl.latActive and not event.carControl.enabled))
        if stamp <= 0 or not event.valid:
          continue
        if kind == 'clocks':
          wall = int(event.clocks.wallTimeNanos)
          if 946684800 * 1_000_000_000 <= wall <= (datetime.now(UTC).timestamp() + 86400) * 1_000_000_000:
            offset = (wall - stamp) / 1e9
            if clocks_offset is not None and abs(offset - clocks_offset) > 5:
              raise _Incomplete('Conflicting logged wall clocks')
            clocks_offset = offset if clocks_offset is None else clocks_offset
        elif kind == 'initData' and model is None:
          model = _recorded_model(event.initData)
        elif kind == 'logMessage':
          load = logged_model_load(str(event.logMessage))
          if load is not None and 0 < load.loaded_mono_ns <= stamp:
            model_loads.append((load.loaded_mono_ns, load))
          elif len(str(event.logMessage)) <= 8192:
            try:
              marker = json.loads(str(event.logMessage)).get('msg', {})
              if marker == 'modeld init':
                model_loads.append((stamp, None))
              if (isinstance(marker, dict) and marker.get('event') == 'modeld.identity-unavailable' and
                  type(marker.get('sinceMonoNs')) is int and 0 < marker['sinceMonoNs'] <= stamp):
                model_loads.append((marker['sinceMonoNs'], None))
            except (ValueError, TypeError, AttributeError):
              pass
        elif kind in ('modelV2', 'drivingModelData'):
          model_outputs.append((stamp, bool(getattr(event, kind).big)))
        elif kind == 'carState':
          speed = float(event.carState.vEgo)
          if not math.isfinite(speed) or not -.5 <= speed <= MAX_SPEED_MPS or (last_car is not None and stamp <= last_car):
            coverage_gap = True
            previous_car = None
            continue
          speed = max(0.0, speed)
          if previous_car is not None:
            delta = (stamp - previous_car[0]) / 1e9
            if delta > MAX_PAIR_SECONDS:
              coverage_gap = True
            else:
              distance += (previous_car[1] + speed) * .5 * delta
              car_pairs += 1
          first_car = stamp if first_car is None else first_car
          last_car = stamp
          previous_car = stamp, speed
        elif kind == 'selfdriveState':
          if last_state is not None and stamp <= last_state:
            coverage_gap = True
            previous_state = None
            continue
          if previous_state is not None:
            delta = (stamp - previous_state[0]) / 1e9
            if delta > MAX_PAIR_SECONDS:
              coverage_gap = True
            else:
              engaged += delta if previous_state[1] else 0.0
              state_pairs += 1
          first_state = stamp if first_state is None else first_state
          last_state = stamp
          previous_state = stamp, bool(event.selfdriveState.enabled)
        elif kind == 'onroadEvents':
          if last_attention is not None and not 0 < (stamp - last_attention) / 1e9 <= 5:
            attention_gap = True
          first_attention = stamp if first_attention is None else first_attention
          last_attention = stamp
          active = {str(item.name).split('.')[-1] for item in event.onroadEvents}
          distracted += DISTRACTED_EVENT in active and DISTRACTED_EVENT not in previous_events
          unresponsive += UNRESPONSIVE_EVENT in active and UNRESPONSIVE_EVENT not in previous_events
          previous_events = active
        elif kind == 'sentinel':
          sentinel_type = str(event.sentinel.type).split('.')[-1]
          if sentinel_type == 'endOfRoute':
            if index != len(names) - 1:
              raise _Incomplete('Route ended before its last segment')
            segment_end = True
      if index == len(names) - 1:
        end_of_route = segment_end
      del events, compressed
    if _route_names(root, route_id, allowed) != set(names):
      raise _Incomplete('Route source changed')
    for index, name in enumerate(names):
      if not allowed():
        raise _Incomplete('Analysis cancelled')
      with _closed_source(root, name, route['segments'][index]['files'], allowed, inspect_only=True, prefer_rlog=prefer_rlog) as (_, _, current, observed):
        if evidence[index] != observed or not current():
          raise _Incomplete('Route source changed')

    if first_car is None or last_car is None or first_state is None or last_state is None:
      raise _Incomplete('Route has no observed duration')
    first = min(first_car, first_state)
    last = max(last_car, last_state)
    if last <= first:
      raise _Incomplete('Route has no observed duration')
    duration = (last - first) / 1e9
    if clocks_offset is not None:
      start_time = first / 1e9 + clocks_offset
    else:
      start_time = _route_wall_time(route_id)
    if start_time is not None:
      result['startTime'] = start_time
      result['endTime'] = start_time + duration
    result['durationSeconds'] = round(duration, 3)
    result['model'] = _model_label(model_outputs, model_loads, model)
    if first_attention is not None and last_attention is not None and not attention_gap and \
       first_attention - first <= 5 * 1e9 and last - last_attention <= 5 * 1e9:
      result['distractedMoments'] = distracted
      result['unresponsiveMoments'] = unresponsive
    if car_pairs and not coverage_gap:
      result['distanceMeters'] = round(distance, 2)
    if state_pairs and not coverage_gap:
      result['engagedSeconds'] = round(min(engaged, duration), 3)
      result['engagedPercent'] = round(min(engaged, duration) / duration * 100, 1)
    result.update(aol_statistics(control_samples, first, last, max_pair_seconds=MAX_PAIR_SECONDS))
    if not end_of_route:
      raise _Incomplete('End of route was not recorded')
    if not car_pairs or not state_pairs or coverage_gap or \
       max(first_car - first, first_state - first, last - last_car, last - last_state) > MAX_PAIR_SECONDS * 1e9:
      raise _Incomplete('Route samples are missing or gapped')
    if start_time is None:
      raise _Incomplete('Route has no logged or named wall time')
    result['complete'] = True
    if model_outputs:
      result['model'] = _model_label(model_outputs, model_loads or _rlog_model_loads(root, route, names, allowed), model)
    return result
  except (_Incomplete, LocalLogUnavailable, LogDecodeError, OSError, ValueError) as error:
    result['reason'] = str(error) or 'Route analysis unavailable'
    if (not prefer_rlog and result['reason'] in ('Route samples are missing or gapped', 'Route has no observed duration', 'End of route was not recorded') and
        isinstance(route, dict) and route.get('segments') and
        all(segment.get('files', {}).get('rlog') is True for segment in route['segments']) and permitted()):
      return analyze_route(root, route, permitted=allowed, prefer_rlog=True)
    return result
