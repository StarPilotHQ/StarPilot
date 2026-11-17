"""Input-only independent-drive windows. No candidate or error selects a span."""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import logging
import time

from openpilot.starpilot.flm.offroad_analysis import cohort, _spread
from openpilot.starpilot.flm.offroad_processor import collect_route_inputs, window_demand
from openpilot.starpilot.flm.offroad_route import (
  LOCAL_ID, MAX_SEGMENTS, MAX_TOTAL_BYTES, MAX_ANALYSIS_SECONDS, RETAIN, SelectedSourceReceipt, RouteInputError, route_reader, route_inventory,
)
from openpilot.starpilot.flm.log_decode import decode_full_rlog_segment
from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
from openpilot.starpilot.galaxy.drive_analysis import _selected, _route_names, _closed_source


@dataclass(frozen=True)
class PriorOriginals:
  cp: object
  receipt: SelectedSourceReceipt
  source_inventory: dict
  windows: tuple
  tracking_windows: tuple
  profile_windows: tuple = ()
  profile_metadata: dict | None = None


class PriorIdentityMismatch(RouteInputError):
  def __init__(self, reason):
    super().__init__(None, reason)


def interior_segments(count):
  if not 1 <= count <= MAX_SEGMENTS:
    raise ValueError('Bounded contiguous original segments required')
  return tuple(sorted({(i + 1) * (count - 1) // 13 for i in range(12)} - {0, count - 1}))


def selected_segments(count, *, expanded=False):
  selected = set(interior_segments(count)) | {0, count - 1}
  if expanded:
    selected.update([n for n in range(count) if n not in selected][:29])
  return tuple(sorted(selected))


@route_reader
def _selected_sources(root, route, *, permitted, consume, cp_sha256, expanded=False, validate_preroll=False):
  root = Path(root)
  route_id, names = _selected(route)
  if not LOCAL_ID.fullmatch(route_id) or not 1 <= len(names) <= MAX_SEGMENTS or set(names) != _route_names(root, route_id, permitted):
    raise ValueError('Complete local prior recording required')
  selected = set(selected_segments(len(names), expanded=expanded))
  receipts, revisions, contexts = [], [], set()
  total = 0
  start, end, clock, actual_cp = None, 0, None, None
  ended = False
  expected_cp = cp_sha256
  for number, name in enumerate(names):
    if not permitted():
      raise ValueError('Independent input collection cancelled')
    expected = route['segments'][number]['files']
    with _closed_source(root, name, expected, permitted, prefer_rlog=True) as (raw, codec, current, revision):
      total += len(raw)
      if total > MAX_TOTAL_BYTES:
        raise ValueError('Prior recording input bound')
      receipts.append((name, hashlib.sha256(raw).hexdigest(), len(raw)))
      revisions.append(revision)
      if validate_preroll and number not in selected and number + 1 in selected:
        # Native replay needs this original warm-up segment too. Reject an
        # unreadable prior before freezing its independent input partition.
        decode_full_rlog_segment(raw, codec, cancelled=lambda: not permitted(), retain=RETAIN)
      if number in selected:
        events = decode_full_rlog_segment(raw, codec, cancelled=lambda: not permitted(), retain=RETAIN)
        for event in events:
          kind, stamp = event.which(), int(event.logMonoTime)
          if kind not in ('initData', 'carParams', 'sentinel') and stamp > 0:
            start = stamp if start is None else min(start, stamp)
            end = max(end, stamp)
          if kind == 'carParams':
            actual_cp = car_params_digest(event.carParams)
            if expected_cp is None:
              from openpilot.starpilot.flm.offroad_evidence import torque_vehicle

              if not torque_vehicle(event.carParams):
                raise PriorIdentityMismatch('Original torque vehicle required')
              expected_cp = actual_cp
            if actual_cp != expected_cp:
              raise PriorIdentityMismatch('Prior vehicle differs')
          elif kind == 'starpilotLateralState' and event.valid and event.starpilotLateralState.learningEvidence.version == 1:
            value = event.starpilotLateralState.learningEvidence
            contexts.add((str(value.controllerMode), str(value.controllerRevision), str(value.baselineRevision)))
          elif kind == 'clocks' and event.valid and int(event.clocks.wallTimeNanos) > 946684800_000_000_000:
            clock = stamp, int(event.clocks.wallTimeNanos)
          elif kind == 'sentinel' and str(event.sentinel.type) == 'endOfRoute':
            if number != len(names) - 1:
              raise ValueError('Prior route ended before its last source')
            ended = True
        consume(number, events)
        if events:
          del event
        del events
      if not current() or not permitted():
        raise ValueError('Independent source changed')
  if (not ended or actual_cp is None or actual_cp != expected_cp or start is None or
      not start < end or clock is None or not 0 <= end - clock[0] <= 10_000_000_000):
    raise ValueError('Closed prior input identity/clock unavailable')
  if set(names) != _route_names(root, route_id, permitted):
    raise ValueError('Prior recording inventory changed')
  for number, name in enumerate(names):
    with _closed_source(root, name, route['segments'][number]['files'], permitted, inspect_only=True, prefer_rlog=True) as (_, _, current, revision):
      if revision != revisions[number] or not current():
        raise ValueError('Prior source revision changed')
  source_sha = hashlib.sha256(json.dumps((route_id, receipts), separators=(',', ':')).encode()).hexdigest()
  return SelectedSourceReceipt(route_id, source_sha, tuple(receipts), actual_cp, start, end, clock[1], tuple(sorted(contexts)), tuple(sorted(selected)))


def _prior_originals(assessment, group, selected_cohort, root, *, permitted, stable):
  """Freeze the latest compatible older input set BEFORE any strength is fitted."""
  inventory = route_inventory(root)
  current = assessment.receipt.route_id
  if not LOCAL_ID.fullmatch(current):
    raise ValueError('Original local latest route required')
  counter = int(current.split('--')[0], 16)
  candidates = []
  for row in inventory['routes']:
    if LOCAL_ID.fullmatch(row['routeId']):
      distance = (counter - int(row['routeId'].split('--')[0], 16)) % 2**32
      if 1 <= distance <= 3 and row['segments'] and all(s['files']['rlog'] for s in row['segments']):
        candidates.append((distance, row['routeId'], row))
  expected = {k: v for k, v in group['original_metadata'].items() if k != 'vehicle_model_anchor'}
  started = time.monotonic()

  def allowed():
    return permitted() and time.monotonic() - started <= (MAX_ANALYSIS_SECONDS if stable else 900.0)

  for _, _, route in sorted(candidates):

    def walk(actual_root, original_route, *, permitted, consume):
      return _selected_sources(actual_root, original_route, permitted=permitted, consume=consume,
                               cp_sha256=assessment.receipt.cp_sha256, expanded=stable, validate_preroll=True)

    try:
      collected = collect_route_inputs(root, route, permitted=allowed, walker=walk)
    except PriorIdentityMismatch:
      continue
    except RouteInputError as error:
      logging.getLogger(__name__).warning('Skipping prior drive %s: %s', route['routeId'], error)
      continue
    if collected.receipt.completed_wall_ns >= assessment.receipt.completed_wall_ns:
      logging.getLogger(__name__).warning('Skipping prior drive %s: independent chronology invalid', route['routeId'])
      continue
    from openpilot.starpilot.flm.offroad_route import original_segment_hashes

    if original_segment_hashes(collected.receipt.segments).intersection(original_segment_hashes(assessment.receipt.segments)):
      logging.getLogger(__name__).warning('Skipping prior drive %s: original recording bytes overlap the current drive', route['routeId'])
      continue
    compatible = []
    profile_windows = {}
    if stable:
      from openpilot.starpilot.flm.offroad_baseline_windows import stable_proposal_windows

      profile_windows, _ = stable_proposal_windows(collected.stable_windows, collected.baseline_tracking)
    for context, windows in collected.windows.items():
      metadata = collected.context_metadata[context]
      if json.dumps({k: metadata.get(k) for k in expected}, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
        continue
      # Keep the original metadata key: point comparison keys deliberately omit route values.
      compatible.extend((w, metadata, context) for w in windows if cohort(w) == selected_cohort['cohort'])
    # Choose a single original model context by input coverage, never by error.
    contexts = {w.points[0].context_key for w, _, _ in compatible}
    if not contexts:
      continue
    context = min(contexts, key=lambda key: (-len({w.points[0].segment for w, _, _ in compatible if w.points[0].context_key == key}), key))
    compatible = [(w, metadata, original_context) for w, metadata, original_context in compatible if w.points[0].context_key == context]
    chosen = []
    tracking = []
    input_segments = (
      tuple(n for n in collected.receipt.decoded_segments if 0 < n < len(route['segments']) - 1) if stable else interior_segments(len(route['segments']))
    )
    for number in input_segments:
      windows = sorted(((w, m) for w, m, _ in compatible if w.points[0].segment == number), key=lambda row: row[0].points[0].mono_ns)
      if windows:
        w, metadata = windows[0]
        tracking.append(w)
        chosen.append(
          {
            'identity': w.identity,
            'segment': number,
            'start_ns': w.points[0].mono_ns,
            'end_ns': w.points[-1].mono_ns,
            'demand': window_demand(w),
            'original_metadata': metadata,
          }
        )
    profile_spans = []
    if stable:
      for number in input_segments:
        candidates = sorted(
          (
            w
            for original_context in sorted({key for _, _, key in compatible})
            for w in profile_windows.get(original_context, ())
            if w.points[0].context_key == context and w.points[0].segment == number and cohort(w) == selected_cohort['cohort']
          ),
          key=lambda w: w.points[0].mono_ns,
        )
        if candidates:
          w = candidates[0]
          profile_spans.append({'identity': w.identity, 'segment': number, 'start_ns': w.points[0].mono_ns, 'end_ns': w.points[-1].mono_ns})
    if stable:
      chosen = _spread(chosen, 12)
      tracking = _spread(tracking, 12)
      profile_spans = _spread(profile_spans, 12)
    if 6 <= len(chosen) <= 12 and (not stable or 3 <= len(profile_spans) <= 12):
      return PriorOriginals(
        collected.state['cp'],
        collected.receipt,
        route,
        tuple(chosen),
        tuple(tracking),
        tuple(profile_spans),
        collected.context_metadata[compatible[0][2]] if stable else None,
      )
  return None


def prior_originals(assessment, group, selected_cohort, root, *, permitted):
  return _prior_originals(assessment, group, selected_cohort, root, permitted=permitted, stable=False)


def prior_stable_originals(assessment, group, selected_cohort, root, *, permitted):
  return _prior_originals(assessment, group, selected_cohort, root, permitted=permitted, stable=True)
