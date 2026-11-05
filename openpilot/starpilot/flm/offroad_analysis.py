"""Completed-drive suggestions, separated from confirmed improvement and actuation."""

from dataclasses import asdict
import hashlib
import json

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.offroad_evidence import RouteWindow
from openpilot.starpilot.flm.offroad_tracking import estimate, suggest

MAX_WINDOWS = 4096
MAX_COHORT_WINDOWS = 24


def cohort(window):
  """Classify only original reference and speed; never errors or response."""
  if type(window) is not RouteWindow or not window.valid():
    raise ValueError('Original complete tracking window required')
  points = window.points
  speed = tuple(p.speed_mps for p in points)
  bands = {0 if v < 10 else 1 if v < 20 else 2 for v in speed}
  if len(bands) != 1:
    return None
  floor = 0.03 if window.domain == 'lateral_acceleration' else 0.1
  signs = {1 if p.desired > floor else -1 if p.desired < -floor else 0 for p in points}
  center_limit = 0.3 if window.domain == 'lateral_acceleration' else 3.0
  if len(signs) != 1 or 0 in signs:
    if max(abs(p.desired) for p in points) <= center_limit:
      return str(next(iter(bands))) + ':both:center'
    return None
  # A whole maneuver section is used; no error-based crop or sample matching.
  start = sum(abs(p.desired) for p in points[:10]) / 10
  end = sum(abs(p.desired) for p in points[-10:]) / 10
  duration = (points[-1].mono_ns - points[0].mono_ns) / 1e9
  slope = (end - start) / duration
  threshold = max(floor, 0.15 * max(start, end))
  phase = 'turn_in' if slope > threshold else 'unwind' if slope < -threshold else 'steady'
  side = 'left' if next(iter(signs)) > 0 else 'right'
  return str(next(iter(bands))) + ':' + side + ':' + phase


def _spread(rows, count):
  if len(rows) <= count:
    return tuple(rows)
  # Whole-window coverage, fixed before looking at actual or fit errors.
  return tuple(rows[i * (len(rows) - 1) // (count - 1)] for i in range(count))


def analyze(windows, previous, *, permitted=lambda: True):
  """Return source-bound suggestions; caller still owes controller qualification."""
  if (
    type(windows) is not tuple
    or len(windows) > MAX_WINDOWS
    or type(previous) is not dict
    or set(previous) != set(NAMES)
    or any(type(w) is not RouteWindow or not w.valid() for w in windows)
    or len({w.identity for w in windows}) != len(windows)
    or len({(w.domain, w.points[0].context_key) for w in windows}) > 1
  ):
    raise ValueError('Bounded, distinct, same-controller whole windows required')
  if not permitted():
    raise ValueError('Parked tracking analysis cancelled')
  groups = {}
  for window in windows:
    key = cohort(window)
    if key is not None:
      groups.setdefault(key, []).append(window)
  results = []
  for key, rows in sorted(groups.items()):
    rows = sorted(rows, key=lambda w: (w.points[0].segment, w.points[0].mono_ns))
    chosen = _spread(rows, MAX_COHORT_WINDOWS)
    # Partition original whole windows before fitting and before selecting knobs.
    train, heldout = chosen[::2], chosen[1::2]
    if len(train) < 3 or len(heldout) < 3:
      results.append({'cohort': key, 'whole_windows': len(rows), 'reason': 'more_independent_windows_required'})
      continue
    first, second = estimate(tuple(train), cohort=key, permitted=permitted), estimate(tuple(heldout), cohort=key, permitted=permitted)
    if first is None or second is None:
      results.append({'cohort': key, 'whole_windows': len(rows), 'reason': 'reference_response_not_identified'})
      continue
    _, side, phase = key.split(':')
    proposal = suggest(first, previous, side=side, phase=phase)
    replication = suggest(second, previous, side=side, phase=phase)
    agrees = (
      proposal is not None and replication is not None and proposal['parameter'] == replication['parameter'] and proposal['delta'] == replication['delta']
    )
    results.append(
      {
        'cohort': key,
        'whole_windows': len(rows),
        'training': asdict(first),
        'heldout': asdict(second),
        'reason': 'candidate_requires_controller_checks' if agrees else 'independent_windows_do_not_support_same_change',
        'proposal': proposal if agrees else None,
      }
    )
  receipt = hashlib.sha256(json.dumps(results, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
  return {
    'version': 1,
    'analysis_sha256': receipt,
    'cohorts': results,
    'state': 'suggestions' if any(row.get('proposal') for row in results) else 'more_evidence_required',
    'automatically_saved': False,
    'road_improvement_confirmed': False,
    'scope': 'Observed tracking suggestions. No independent EPS response, native permission or application is manufactured.',
  }
