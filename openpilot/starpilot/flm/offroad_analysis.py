"""Completed-drive suggestions, separated from confirmed improvement and actuation."""

from dataclasses import asdict
import hashlib
import json

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.offroad_evidence import RouteWindow, cohort
from openpilot.starpilot.flm.offroad_tracking import estimate, suggest
from openpilot.starpilot.flm.offroad_coordinate_policy import tracking_coordinate_candidates

MAX_WINDOWS = 4096
MAX_COHORT_WINDOWS = 24




def _spread(rows, count):
  if len(rows) <= count:
    return tuple(rows)
  # Whole-window coverage, fixed before looking at actual or fit errors.
  return tuple(rows[i * (len(rows) - 1) // (count - 1)] for i in range(count))


def analyze(windows, previous, *, permitted=lambda: True, route_order=None, comparison=None):
  """Return source-bound suggestions; caller still owes controller qualification."""
  if (
    type(windows) is not tuple
    or len(windows) > MAX_WINDOWS
    or type(previous) is not dict
    or set(previous) != set(NAMES)
    or any(type(w) is not RouteWindow or not w.valid() for w in windows)
    or len({w.identity for w in windows}) != len(windows)
    or comparison is None and len({(w.domain, w.points[0].context_key) for w in windows}) > 1
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
    from openpilot.starpilot.flm.offroad_history import partition_windows

    train, heldout = partition_windows(rows, route_order)
    if len(train) < 3 or len(heldout) < 3:
      result = {'cohort': key, 'whole_windows': len(rows), 'reason': 'more_independent_windows_required'}
      if 3 <= len(rows) < 6:
        observed = estimate(tuple(rows), cohort=key, permitted=permitted, comparison=comparison)
        if observed is not None:
          _, side, phase = key.split(':')
          provisional = suggest(observed, previous, side=side, phase=phase)
          coordinates = tracking_coordinate_candidates(observed, tuple(rows), previous, side=side, phase=phase)
          if provisional is not None or coordinates:
            result.update(provisional_proposal=provisional, provisional_estimate=asdict(observed),
                          automatic_application_allowed=False, reason='recorded_provisional_suggestion')
            if coordinates:
              result['provisional_coordinate_candidates'] = [{k: v for k, v in candidate.items() if k != 'values'} for candidate in coordinates]
      results.append(result)
      continue
    first = estimate(tuple(train), cohort=key, permitted=permitted, comparison=comparison)
    second = estimate(tuple(heldout), cohort=key, permitted=permitted, comparison=comparison)
    if first is None or second is None:
      results.append({'cohort': key, 'whole_windows': len(rows), 'reason': 'reference_response_not_identified'})
      continue
    _, side, phase = key.split(':')
    proposal = suggest(first, previous, side=side, phase=phase)
    replication = suggest(second, previous, side=side, phase=phase)
    agrees = (
      proposal is not None and replication is not None and proposal['parameter'] == replication['parameter'] and proposal['delta'] == replication['delta']
    )
    training_coordinates = tracking_coordinate_candidates(first, tuple(train), previous, side=side, phase=phase)
    independent_coordinates = {
      p['parameter']: p for p in tracking_coordinate_candidates(second, tuple(heldout), previous, side=side, phase=phase)
    }
    supported_coordinates = [p for p in training_coordinates if p['parameter'] in independent_coordinates
                             and p['delta'] * independent_coordinates[p['parameter']]['delta'] > 0
                             and abs(p['delta']) <= abs(independent_coordinates[p['parameter']]['delta'])]
    provisional_coordinates = [p for p in training_coordinates if p['parameter'] not in independent_coordinates
                               or p['delta'] * independent_coordinates[p['parameter']]['delta'] > 0]
    results.append(
      {
        'cohort': key,
        'whole_windows': len(rows),
        'training': asdict(first),
        'heldout': asdict(second),
        'reason': 'candidate_requires_controller_checks' if agrees or supported_coordinates else 'independent_windows_do_not_support_same_change',
        'proposal': proposal if agrees else None,
        **({'coordinate_candidates': [{k: v for k, v in p.items() if k != 'values'} for p in supported_coordinates],
            'provisional_coordinate_candidates': [{k: v for k, v in p.items() if k != 'values'} for p in provisional_coordinates]}
           if training_coordinates else {}),
      }
    )
  receipt = hashlib.sha256(json.dumps(results, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
  return {
    'version': 1,
    'analysis_sha256': receipt,
    'cohorts': results,
    'state': 'suggestions' if any(row.get('proposal') or row.get('provisional_proposal') or row.get('provisional_coordinate_candidates')
                                for row in results) else 'more_evidence_required',
    'automatically_saved': False,
    'road_improvement_confirmed': False,
    'scope': 'Observed tracking suggestions. No independent EPS response, native permission or application is manufactured.',
  }
