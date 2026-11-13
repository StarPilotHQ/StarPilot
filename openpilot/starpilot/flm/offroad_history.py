"""Bounded cross-drive observations; original logs still supply native checks.

Only small, input-selected tracking windows are retained. This is neither a log
archive nor permission to use an old tune with a different controller/baseline.
"""

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import logging
import math
from pathlib import Path
import zlib
from openpilot.starpilot.flm.log_decode import LogDecodeCancelled

from openpilot.starpilot.flm.offroad_analysis import cohort, _spread
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, _pairs
from openpilot.starpilot.flm.offroad_evidence import RoutePoint, RouteWindow
from openpilot.starpilot.flm.offroad_route import RouteInputCancelled, RouteReceipt, SelectedSourceReceipt, route_permission

MAX_DRIVES = 8
MAX_WINDOWS_PER_DRIVE = 64
MAX_PACKED = 8 * 1024 * 1024
MAX_UNPACKED = 64 * 1024 * 1024
POINT_FIELDS = tuple(f.name for f in fields(RoutePoint))


@dataclass(frozen=True)
class TrackingComparison:
  originals: tuple

  def original_context(self, window):
    for key, metadata in self.originals:
      recorded = {k: v for k, v in metadata.items() if k not in ('route_values', 'route_revision')}
      if window.points[0].context_key == hashlib.sha256(repr(recorded).encode()).hexdigest():
        return key, metadata
    raise ValueError('Original window context missing')

  def context(self, windows):
    common = None
    original_keys = set()
    anchor_size = None
    for original_key, metadata in self.originals:
      if hashlib.sha256(repr(metadata).encode()).hexdigest() != original_key:
        raise ValueError('Original numerical comparison binding changed')
      anchor = metadata['vehicle_model_anchor']
      if (len(anchor) not in (0, 3) or anchor_size is not None and len(anchor) != anchor_size
          or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in anchor)):
        raise ValueError('Original vehicle-model observation required')
      anchor_size = len(anchor)
      baseline = {k: v for k, v in metadata.items() if k != 'vehicle_model_anchor'}
      if common is not None and baseline != common:
        raise ValueError('Cross-drive vehicle/controller/tune baseline differs')
      common = baseline
      recorded = {k: v for k, v in metadata.items() if k not in ('route_values', 'route_revision')}
      original_keys.add(hashlib.sha256(repr(recorded).encode()).hexdigest())
    if common is None or any(w.points[0].context_key not in original_keys or w.domain != common['domain'] for w in windows):
      raise ValueError('Every observed window needs an exact original context')
    # Only the derived statistic shares a comparison key. Points, original
    # identities and each controller/native replay retain their actual anchor.
    return hashlib.sha256(repr(('cross_drive_observed_tracking_v1', common)).encode()).hexdigest()


def statistical_inputs(collected):
  from copy import copy

  merged = copy(collected)
  contexts, references, originals = {}, {}, {}
  for name in ('windows', 'stable_windows', 'baseline_tracking'):
    destination = {}
    for key, windows in getattr(collected, name).items():
      metadata = collected.context_metadata[key]
      baseline = {k: v for k, v in metadata.items() if k != 'vehicle_model_anchor'}
      comparison_key = hashlib.sha256(repr(baseline).encode()).hexdigest()
      contexts.setdefault(comparison_key, metadata)
      references.setdefault(comparison_key, {})[key] = metadata
      for window in windows:
        if window.identity in originals and originals[window.identity] != metadata:
          raise ValueError('Conflicting original observation metadata')
        originals[window.identity] = metadata
      destination.setdefault(comparison_key, []).extend(windows)
    setattr(merged, name, destination)
  merged.context_metadata = contexts
  comparisons = {key: TrackingComparison(tuple(rows.items())) for key, rows in references.items()}
  return merged, comparisons, originals


def bind_original_contexts(report, windows, comparison):
  ids = {
    row['identity'] for name in ('selected_original_windows', 'qualification_original_windows', 'observed_original_windows') for row in report.get(name, ())
  }
  originals, bindings = {}, {}
  for window in windows:
    if window.identity in ids:
      key, metadata = comparison.original_context(window)
      originals[key] = metadata
      bindings[window.identity] = key
  report.update(original_contexts=originals, original_window_contexts=bindings)


def _tuples(value):
  return tuple(_tuples(v) for v in value) if type(value) is list else value


def _metadata(rows):
  # Preserve the original field order and tuple types used by source identities.
  return {name: _tuples(value) for name, value in rows}


def _window(row):
  window = RouteWindow(row['identity'], tuple(RoutePoint(*point) for point in row['points']))
  if not window.valid():
    raise ValueError('Historical original tracking window changed')
  return window


def _receipt(row):
  values = dict(row['receipt'])
  values['segments'] = _tuples(values['segments'])
  values['original_controller_contexts'] = _tuples(values['original_controller_contexts'])
  if row['selected_sections']:
    values['decoded_segments'] = _tuples(values['decoded_segments'])
    return SelectedSourceReceipt(**values)
  return RouteReceipt(**values)


def partition_windows(rows, route_order=None, *, contiguous=False):
  """Freeze source roles before fitting; different drives never share a role."""
  if route_order is None:
    chosen = _spread(sorted(rows, key=lambda w: (w.points[0].segment, w.points[0].mono_ns)), 24)
    split = len(chosen) // 2
    return (chosen[:split], chosen[split:]) if contiguous else (chosen[::2], chosen[1::2])
  routes = sorted({w.points[0].route for w in rows}, key=lambda route: (route_order[route], route))
  if len(routes) < 2:
    return partition_windows(rows, contiguous=contiguous)
  result = []
  for sources in (set(routes[::2]), set(routes[1::2])):
    ordered = sorted(
      (w for w in rows if w.points[0].route in sources),
      key=lambda w: (route_order[w.points[0].route], w.points[0].route, w.points[0].segment, w.points[0].mono_ns),
    )
    result.append(_spread(ordered, 12))
  return tuple(result)


class EvidenceBank:
  def __init__(self, directory):
    self.cache = AssessmentCache(Path(directory).with_name(Path(directory).name + '-evidence'))

  def _profiles(self):
    try:
      packed = self.cache._read('history.z', MAX_PACKED)
    except FileNotFoundError:
      return {}
    decoder = zlib.decompressobj()
    try:
      raw = decoder.decompress(packed, MAX_UNPACKED + 1)
    except zlib.error as error:
      raise ValueError('Stored steering history is undecodable') from error
    if len(raw) > MAX_UNPACKED or not decoder.eof or decoder.unused_data:
      raise ValueError('Historical steering evidence bound or framing changed')
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict or value.get('version') != 1 or type(value.get('profiles')) is not dict or len(value['profiles']) > 8:
      raise ValueError('Bounded historical steering evidence required')
    return value['profiles']

  def revision(self):
    try:
      packed = self.cache._read('history.z', MAX_PACKED)
    except FileNotFoundError:
      return None
    return hashlib.sha256(packed).hexdigest()

  def read(self, identity):
    rows = self._profiles().get(identity, [])
    if type(rows) is not list or len(rows) > MAX_DRIVES:
      raise ValueError('Bounded vehicle evidence history required')
    if len({row['receipt']['route_id'] for row in rows}) != len(rows):
      raise ValueError('Historical drives cannot be counted twice')
    return rows

  def retain(self, collected, inventory, identity, *, permitted):
    """Selection uses speed, side, phase and source order, never fit outcomes."""
    permitted = route_permission(permitted)
    buckets = []
    for kind in ('windows', 'stable_windows', 'baseline_tracking'):
      groups = {}
      for context, windows in getattr(collected, kind).items():
        for window in windows:
          condition = cohort(window)
          if condition is not None:
            groups.setdefault((context, condition), []).append(window)
      for (context, condition), windows in sorted(groups.items()):
        ordered = sorted(windows, key=lambda w: (w.points[0].segment, w.points[0].mono_ns))
        buckets.append((kind, context, condition, _spread(ordered, 4)))
    retained = []
    for offset in range(4):
      for kind, context, condition, windows in buckets:
        if offset < len(windows) and len(retained) < MAX_WINDOWS_PER_DRIVE:
          window = windows[offset]
          retained.append({
            'kind': kind, 'context': context, 'condition': condition, 'identity': window.identity,
            'points': [[getattr(point, name) for name in POINT_FIELDS] for point in window.points],
            'bookends': collected.bookends.get(window.identity, ()),
          })
    if not retained or collected.quality_stops:
      return self.read(identity)  # Parked restarts and withdrawn evidence must not evict actual steering history.
    row = {
      'receipt': asdict(collected.receipt), 'selected_sections': type(collected.receipt) is SelectedSourceReceipt,
      'inventory': inventory, 'windows': retained,
      'metadata': {key: list(value.items()) for key, value in collected.context_metadata.items() if key in {w['context'] for w in retained}},
      'quality_stops': collected.quality_stops,
    }
    history = self.read(identity)
    previous = next((r for r in history if r['receipt']['route_id'] == collected.receipt.route_id), None)
    if previous is not None and previous['receipt']['source_sha256'] != collected.receipt.source_sha256:
      raise ValueError('A retained original drive changed')
    history = [r for r in history if r['receipt']['route_id'] != collected.receipt.route_id] + [row]
    history.sort(key=lambda r: (r['receipt']['completed_wall_ns'], r['receipt']['route_id']))
    history = history[-MAX_DRIVES:]
    profiles = self._profiles()
    if identity not in profiles and len(profiles) >= 8:
      oldest = min(profiles, key=lambda key: max((r['receipt']['completed_wall_ns'] for r in profiles[key]), default=0))
      del profiles[oldest]  # Derived evidence only; original recordings and saved tune history remain untouched.
    profiles[identity] = history
    while True:
      if not permitted():
        raise RouteInputCancelled('Parked authority lost before retaining steering history')
      raw = json.dumps({'version': 1, 'profiles': profiles}, separators=(',', ':'), allow_nan=False).encode()
      packed = zlib.compress(raw, 6) if len(raw) <= MAX_UNPACKED else None
      if packed is not None and len(packed) <= MAX_PACKED:
        break
      # Capacity is not conflicting driving evidence. Retire derived history
      # by source age, never by fit error, before refusing a new drive.
      others = [key for key in profiles if key != identity]
      if others:
        oldest = min(others, key=lambda key: (max((r['receipt']['completed_wall_ns'] for r in profiles[key]), default=0), key))
        del profiles[oldest]
      elif len(profiles[identity]) > 1:
        profiles[identity] = profiles[identity][1:]
      else:
        current = profiles[identity][0]
        if len(current['windows']) <= 1:
          # A single oversized whole window need not stop this drive's own
          # analysis. Leave the existing optional history file untouched.
          return self.read(identity)
        count = len(current['windows']) // 2
        windows = list(_spread(current['windows'], count)) if count > 1 else current['windows'][:1]
        profiles[identity] = [{**current, 'windows': windows,
          'metadata': {key: value for key, value in current['metadata'].items() if key in {w['context'] for w in windows}}}]
    if not permitted():
      raise RouteInputCancelled('Parked authority lost before retaining steering history')
    self.cache._write('history.z', packed, maximum=MAX_PACKED)
    return profiles[identity]

  def exclude(self, route_ids, *, permitted):
    """Remove unavailable derived inputs, never recordings or learned tunes."""
    permitted = route_permission(permitted)
    rejected = set(route_ids)
    if not rejected:
      return
    before = self._profiles()
    profiles = {key: [row for row in rows if row['receipt']['route_id'] not in rejected] for key, rows in before.items()}
    profiles = {key: rows for key, rows in profiles.items() if rows}
    if profiles == before:
      return
    raw = json.dumps({'version': 1, 'profiles': profiles}, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_UNPACKED or not permitted():
      raise RouteInputCancelled('Parked historical input exclusion unavailable')
    self.cache._write('history.z', zlib.compress(raw, 6), maximum=MAX_PACKED)


def _verify_history_source(root, row, permitted, needed):
  from openpilot.starpilot.flm.offroad_route import MAX_SEGMENTS, MAX_TOTAL_BYTES, RETAIN, route_reader
  from openpilot.starpilot.flm.log_decode import decode_full_rlog_segment
  from openpilot.starpilot.galaxy.drive_analysis import _selected, _route_names, _closed_source

  @route_reader
  def verify(root, route, *, permitted):
    name, segments = _selected(route)
    receipts = row['receipt']['segments']
    if (len(segments) > MAX_SEGMENTS or any(not s['files'].get('rlog') for s in route['segments'])
        or segments != [value[0] for value in receipts]
        or set(segments) != _route_names(Path(root), name, permitted)):
      raise ValueError('Historical route inventory changed')
    total = 0
    for number, (segment, digest, size) in enumerate(receipts):
      with _closed_source(Path(root), segment, route['segments'][number]['files'], permitted, prefer_rlog=True) as (raw, codec, current, _):
        total += len(raw)
        if total > MAX_TOTAL_BYTES or len(raw) != size or hashlib.sha256(raw).hexdigest() != digest or not current():
          raise ValueError('Historical original recording changed')
        if number in needed or number + 1 in needed:
          decode_full_rlog_segment(raw, codec, cancelled=lambda: not permitted(), retain=RETAIN)
    if set(segments) != _route_names(Path(root), name, permitted) or not permitted():
      raise ValueError('Historical source changed during validation')

  verify(root, row['inventory'], permitted=permitted)


def combine(collected, inventory, history, *, root=None, permitted=lambda: True, on_skip=None):
  """Add exact-compatible original windows without rebinding a source identity."""
  permitted = route_permission(permitted)
  from copy import copy
  from openpilot.starpilot.flm.offroad_processor import CompletedAssessment

  pooled = copy(collected)
  for name in ('windows', 'stable_windows', 'baseline_tracking'):
    setattr(pooled, name, {key: list(values) for key, values in getattr(collected, name).items()})
  pooled.context_metadata = dict(collected.context_metadata)
  pooled.bookends = dict(collected.bookends)
  sources = {collected.receipt.route_id: CompletedAssessment(collected.receipt, (), {}, collected.state['cp'], (), inventory)}
  order = {collected.receipt.route_id: collected.receipt.completed_wall_ns}
  for row in history:
    saved = row.get('receipt') if type(row) is dict else None
    route_id = saved.get('route_id', 'Unavailable stored recording') if type(saved) is dict else 'Unavailable stored recording'
    if type(route_id) is not str:
      route_id = 'Unavailable stored recording'
    try:
      if not permitted():
        raise RouteInputCancelled('Historical input collection cancelled')
      receipt = _receipt(row)
      if receipt.cp_sha256 != collected.receipt.cp_sha256 or receipt.route_id in sources or row['quality_stops']:
        continue
      if type(row['inventory']) is not dict or type(row['metadata']) is not dict or type(row['windows']) is not list:
        raise ValueError('Historical drive input structure changed')
      if row['inventory'].get('routeId') != receipt.route_id or len(row['windows']) > MAX_WINDOWS_PER_DRIVE:
        raise ValueError('Historical route inventory or window bound changed')
      metadata = {key: _metadata(value) for key, value in row['metadata'].items()}
      for key, value in metadata.items():
        if hashlib.sha256(repr(value).encode()).hexdigest() != key:
          raise ValueError('Historical original context changed')
        if key in pooled.context_metadata and pooled.context_metadata[key] != value:
          raise ValueError('Conflicting historical controller context')
      checked, seen = [], set()
      for record in row['windows']:
        if not permitted_window(record, metadata, receipt):
          raise ValueError('Historical source window binding changed')
        window = _window(record)
        binding = (record['kind'], window.identity)
        if binding in seen:
          raise ValueError('Historical observations cannot be counted twice')
        seen.add(binding)
        checked.append((record, window))
      if root is not None:
        _verify_history_source(root, row, permitted, {window.points[0].segment for _, window in checked})
    except (RouteInputCancelled, LogDecodeCancelled):
      raise
    except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
      if not permitted():
        raise RouteInputCancelled('Historical input collection cancelled') from error
      logging.getLogger(__name__).warning('Skipping stored drive %s: %s', route_id, error)
      if on_skip is not None:
        on_skip(route_id, str(error)[:160])
      continue
    # Validate the whole row and its sources before ANY point, context or
    # source receipt enters the pool. A bad late window invalidates its drive.
    pooled.context_metadata.update(metadata)
    sources[receipt.route_id] = CompletedAssessment(receipt, (), {}, collected.state['cp'], (), row['inventory'])
    order[receipt.route_id] = receipt.completed_wall_ns
    for record, window in checked:
      getattr(pooled, record['kind']).setdefault(record['context'], []).append(window)
      pooled.bookends[window.identity] = tuple(record['bookends'])
  return pooled, sources, order


def permitted_window(row, metadata, receipt):
  if row['kind'] not in ('windows', 'stable_windows', 'baseline_tracking') or row['context'] not in metadata:
    return False
  window = _window(row)
  actual = metadata[row['context']]
  comparison = dict(actual)
  comparison.pop('route_values')
  comparison.pop('route_revision')
  return bool(
    actual['cp_sha256'] == receipt.cp_sha256
    and window.points[0].route == receipt.route_id
    and 0 <= window.points[0].segment < len(receipt.segments)
    and window.points[0].context_key == hashlib.sha256(repr(comparison).encode()).hexdigest()
    and cohort(window) == row['condition']
  )
