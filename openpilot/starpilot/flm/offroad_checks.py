"""All preselected original windows must pass; never substitute favorable rows."""

from pathlib import Path
from typing import Any
from openpilot.starpilot.flm.offroad_controller_checks import check_candidate, check_original_native
from openpilot.starpilot.flm.offroad_route import RETAIN
from openpilot.starpilot.flm.log_decode import LogDecodeCancelled, LogDecodeError, decode_full_rlog_segment
from openpilot.starpilot.flm.offroad_route import RouteInputCancelled, RouteInputError, route_permission
from openpilot.starpilot.galaxy.drive_analysis import _Incomplete, _closed_source


TRAINING_FRACTIONS = (1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.05)


def qualify(assessment, group, cohort, values, root, *, permitted, require_change=True):
  roles = cohort.get('qualification_roles', cohort)
  ids = tuple(roles['training']['windows']) + tuple(roles['heldout']['windows'])
  original = group['qualification_original_windows'] if 'qualification_roles' in cohort else group['selected_original_windows']
  if not all(3 <= len(roles[role]['windows']) <= 12 for role in ('training', 'heldout')) or len(set(ids)) != len(ids):
    raise ValueError('Disjoint original training and held-out windows required')
  results = _original_results(assessment, original, ids, values, root, permitted=permitted)
  return {
    'qualified': qualified_originals(results, roles, require_change=require_change),
    'policy': 'every_preselected_window_controller_and_registered_encoder_v1',
    'windows': results,
    'physical_improvement_proven': False,
    'current_permission_provided': False,
    'requires_changed_emission': require_change,
    'changed_emission_scope': 'both_preselected_roles' if require_change else 'observed_original_map',
  }


def fit_training_step(assessment, group, cohort, proposal, previous, root, *, permitted):
  """Fit deterministic actuator feasibility on training only; never retry held-out."""
  roles = cohort.get('qualification_roles', cohort)
  train, heldout = (tuple(roles[role]['windows']) for role in ('training', 'heldout'))
  if not 3 <= len(train) <= 12 or not 3 <= len(heldout) <= 12 or len(set(train + heldout)) != len(train + heldout):
    raise ValueError('Original disjoint training and held-out windows required')
  original = group['qualification_original_windows'] if 'qualification_roles' in cohort else group['selected_original_windows']
  if set(previous) != set(proposal['values']):
    raise ValueError('Exact original full-map prior required')
  attempts = []
  for fraction in TRAINING_FRACTIONS:
    values = (
      dict(proposal['values']) if fraction == 1 else {name: previous[name] + fraction * (value - previous[name]) for name, value in proposal['values'].items()}
    )
    checked = _original_results(assessment, original, train, values, root, permitted=permitted)
    feasible = len(checked) == len(train) and all(row['qualified'] for row in checked) and bool(_changed_windows(checked))
    attempts.append({'fraction': fraction, 'feasible': feasible, 'original_training_windows': checked})
    if feasible:
      fitted = dict(proposal, values=values, delta=proposal['delta'] * fraction)
      return fitted, {
        'policy': 'training_only_native_feasibility_v1',
        'training_windows': train,
        'heldout_windows_not_evaluated': heldout,
        'attempts': attempts,
        'selected_fraction': fraction,
      }
  return None, {
    'policy': 'training_only_native_feasibility_v1',
    'training_windows': train,
    'heldout_windows_not_evaluated': heldout,
    'attempts': attempts,
    'selected_fraction': None,
  }


def fit_independent_route(assessment, group, cohort, prior, proposal, previous, root, *, permitted, scope=None):
  """Known current windows train strength; one independent drive validates it."""
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  import json

  roles = cohort.get('qualification_roles', cohort)
  train = tuple(roles['training']['windows']) + tuple(roles['heldout']['windows'])
  original = group['qualification_original_windows'] if 'qualification_roles' in cohort else group['selected_original_windows']
  validation = tuple(row['identity'] for row in prior.windows)
  if (
    not all(3 <= len(roles[role]['windows']) <= 12 for role in ('training', 'heldout'))
    or not 6 <= len(validation) <= 12
    or len(set(train + validation)) != len(train + validation)
    or assessment.receipt.source_sha256 == prior.receipt.source_sha256
    or {row[1] for row in assessment.receipt.segments}.intersection(row[1] for row in prior.receipt.segments)
    or assessment.receipt.route_id == prior.receipt.route_id
    or prior.receipt.completed_wall_ns >= assessment.receipt.completed_wall_ns
    or prior.receipt.cp_sha256 != assessment.receipt.cp_sha256
    or car_params_digest(prior.cp) != assessment.receipt.cp_sha256
    or set(previous) != set(proposal['values'])
  ):
    raise ValueError('Independent original same-vehicle input partitions required')
  expected = {k: v for k, v in group['original_metadata'].items() if k != 'vehicle_model_anchor'}
  for row in prior.windows:
    metadata = row['original_metadata']
    if json.dumps({k: metadata.get(k) for k in expected}, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
      raise ValueError('Independent original controller/settings identity differs')
  attempts = []
  proof: dict[str, Any] = {
    'policy': 'all_current_native_windows_training_independent_drive_validation_v1',
    'training_source_sha256': assessment.receipt.source_sha256,
    'training_windows': train,
    'validation_source_sha256': prior.receipt.source_sha256,
    'validation_windows': validation,
    'attempts': attempts,
    'selected_fraction': None,
    'validation_evaluations': 0,
  }
  stable = proposal.get('policy') == 'original_stable_recommended_bounded_route_v1'
  if stable and (scope is None or not 3 <= len(prior.profile_windows) <= 12):
    raise ValueError('Independent original Stable profile inputs and current scope required')
  for fraction in TRAINING_FRACTIONS:
    values = (
      dict(proposal['values']) if fraction == 1 else {name: previous[name] + fraction * (value - previous[name]) for name, value in proposal['values'].items()}
    )
    checked = _original_results(assessment, original, train, values, root, permitted=permitted)
    feasible = (
      len(checked) == len(train)
      and {r['window'] for r in checked} == set(train)
      and all(r['qualified'] for r in checked)
      and bool(_changed_windows(checked))
    )
    attempts.append({'fraction': fraction, 'feasible': feasible, 'original_training_windows': checked})
    if not feasible:
      continue
    proof['selected_fraction'] = fraction
    # The independent drive's actual response is read only after strength freezes.
    from dataclasses import asdict
    from openpilot.starpilot.flm.offroad_tracking import estimate, suggest
    from openpilot.starpilot.flm.canonical_surface_adapter import NAMES

    if stable:
      from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector, independent_profile_support

      selector = StableRouteSelector(prior, root, previous, scope, permitted=permitted)
      replicated, profile_proof = selector.profile(tuple(w['identity'] for w in prior.profile_windows), cohort['cohort'])
      if proposal.get('coordinate_policy') == 'symptom_bound_all30_v1':
        replicated = next((p for p in profile_proof.get('coordinate_candidates', []) if p['parameter'] == proposal['parameter']), None)
      supported = independent_profile_support({'values': values}, replicated, previous)
      proof['independent_tracking'] = {'qualified': supported, 'profile': profile_proof, 'replication': replicated}
    else:
      observed = estimate(prior.tracking_windows, cohort=cohort['cohort'], permitted=permitted)
      _, side, phase = cohort['cohort'].split(':')
      replication = None if observed is None else suggest(observed, {name: previous[name] for name in NAMES}, side=side, phase=phase)
      supported = (
        replication is not None
        and replication['parameter'] == proposal['parameter']
        and replication['delta'] * proposal['delta'] > 0
        and abs(proposal['delta'] * fraction) <= abs(replication['delta'])
      )
      proof['independent_tracking'] = {'qualified': supported, 'estimate': None if observed is None else asdict(observed), 'replication': replication}
    proof['validation_evaluations'] = 1
    if not supported:
      checks = {
        'qualified': False,
        'policy': proof['policy'],
        'windows': checked,
        'reason': 'independent_drive_tracking_does_not_support_change',
        'physical_improvement_proven': False,
        'current_permission_provided': False,
        'independent_tracking': proof['independent_tracking'],
        'independent_native_windows_checked': 0,
        'unperformed_windows': len(validation),
      }
      return dict(proposal, values=values, delta=proposal['delta'] * fraction), proof, checks
    # This is the sole native validation evaluation; failure cannot resize or retry.
    final = _original_results(prior, prior.windows, validation, values, root, permitted=permitted)
    proof['validation_evaluations'] = 1
    joined = checked + final
    split = {'training': {'windows': train}, 'heldout': {'windows': validation}}
    # Existing per-call original input bounds remain24; all24 training and up
    # to12 independent validation rows are required in this combined evidence.
    qualified = (
      len(final) == len(validation)
      and {r['window'] for r in final} == set(validation)
      and all(r['qualified'] for r in joined)
      and _changed_in_each(joined, split)
    )
    checks = {
      'qualified': qualified,
      'policy': proof['policy'],
      'windows': joined,
      'physical_improvement_proven': False,
      'current_permission_provided': False,
      'requires_changed_emission': True,
      'source_partitions': {'training': assessment.receipt.source_sha256, 'heldout': prior.receipt.source_sha256},
    }
    return dict(proposal, values=values, delta=proposal['delta'] * fraction), proof, checks
  return None, proof, None


def _changed_windows(results):
  return {
    r['window']
    for r in results
    if r.get('numerical', {}).get('qualified')
    and r['numerical'].get('changed_frames', 0) > 0
    and r.get('registered_encoder') is not None
    and r['registered_encoder'].get('qualified')
    and r['registered_encoder'].get('candidate_changed_emissions', 0) > 0
  }


def _changed_in_each(results, roles):
  changed = _changed_windows(results)
  return all(changed.intersection(roles[role]['windows']) for role in ('training', 'heldout'))


def _release_unused_analysis_heap():
  # Parked only: return unused analysis pages before decoding native evidence.
  import gc
  import sys

  gc.collect()
  if sys.platform.startswith('linux'):
    import ctypes

    trim = getattr(ctypes.CDLL(None), 'malloc_trim', None)
    if trim is not None:
      trim.argtypes = (ctypes.c_size_t,)
      trim.restype = ctypes.c_int
      trim(0)


def _original_results(assessment, original, ids, values, root, *, permitted, source_batch=False):
  permitted = route_permission(permitted)
  try:
    return _read_original_results(assessment, original, ids, values, root, permitted=permitted, source_batch=source_batch)
  except LogDecodeCancelled:
    raise
  except (LogDecodeError, OSError, _Incomplete) as error:
    if not permitted():
      raise RouteInputCancelled('Parked checks cancelled') from error
    name = getattr(getattr(assessment, 'receipt', None), 'route_id', None)
    if name is None:
      raise
    raise RouteInputError(name, str(error)) from error


def _read_original_results(assessment, original, ids, values, root, *, permitted, source_batch=False):
  spans = {w['identity']: w for w in original if w['identity'] in ids}
  if not (1 if source_batch else 3) <= len(ids) <= 24 or len(set(ids)) != len(ids) or set(spans) != set(ids):
    raise ValueError('Every preselected original window required')
  if not permitted():
    raise RouteInputCancelled('Parked checks cancelled')
  if getattr(assessment, 'evidence_sources', None):
    results = []
    if any(w.get('route') not in assessment.evidence_sources for w in spans.values()):
      raise ValueError('Every historical window needs its own original source')
    for route, source in sorted(assessment.evidence_sources.items()):
      selected = tuple(key for key in ids if spans[key]['route'] == route)
      if selected:
        checked = _original_results(source, original, selected, values, root, permitted=permitted, source_batch=True)
        results.extend(dict(row, route=route, source_sha256=source.receipt.source_sha256) for row in checked)
    return results
  _release_unused_analysis_heap()
  from openpilot.starpilot.flm.learning_wire import read_control, read_applied
  import hashlib

  needed = {w['segment'] for w in spans.values()}
  segments = needed | {v - 1 for v in needed if v > 0}
  controls = []
  outputs = []
  missing = []
  results = []
  receipt = assessment.receipt
  inventory = assessment.source_inventory
  ranges = tuple((w['start_ns'], w['end_ns']) for w in spans.values())
  for number in sorted(segments):
    if not permitted():
      raise RouteInputCancelled('Parked checks cancelled')
    name, digest, size = receipt.segments[number]
    with _closed_source(Path(root), name, inventory['segments'][number]['files'], permitted, prefer_rlog=True) as (raw, codec, current, _):
      if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise RouteInputError(receipt.route_id, 'Original numerical input changed')
      events = decode_full_rlog_segment(raw, codec, cancelled=lambda: not permitted(), retain=RETAIN)
      for event in events:
        if event.which() == 'starpilotLateralState':
          stamp = int(event.logMonoTime)
          if not needed_original(event, ranges, 'control'):
            continue
          if not event.valid or event.starpilotLateralState.learningEvidence.version != 1:
            missing.append(stamp)
            continue
          try:
            controls.append(read_control(event))
          except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            missing.append(stamp)
        elif event.which() == 'starpilotCarState' and event.valid and event.starpilotCarState.appliedEvidence.version == 1:
          if not needed_original(event, ranges, 'applied'):
            continue
          try:
            outputs.append(read_applied(event))
          except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            missing.append(int(event.logMonoTime))
      if not permitted():
        raise RouteInputCancelled('Parked checks cancelled')
      if not current():
        raise RouteInputError(receipt.route_id, 'Original input changed during checks')
    # Parsed evidence owns primitives; release segment readers before replay.
    del events, raw
    for wid, w in sorted(spans.items()):
      if w['segment'] != number:
        continue
      start, end = w['start_ns'], w['end_ns']
      if any(start - 30_000_000 <= stamp <= end + 30_000_000 for stamp in missing):
        results.append({'window': wid, 'qualified': False, 'reason': 'original_control_or_delivery_metadata_omitted'})
        continue
      before = [c for c in controls if c.control_ns <= start and c.replay_snapshot and start - c.control_ns <= 1_000_000_000]
      if not before:
        results.append({'window': wid, 'qualified': False, 'reason': 'original_checkpoint_preroll_missing'})
        continue
      checkpoint = max(c.control_ns for c in before)
      block = tuple(c for c in controls if checkpoint <= c.control_ns <= end + 30_000_000)
      original_outputs = tuple(outputs)
      numerical = check_candidate(assessment.cp, block, values, score_start_ns=start, score_end_ns=end, require_change=False, original_outputs=original_outputs)
      commands = numerical.pop('commands', ())
      native = None
      if numerical['qualified']:
        stamps = {r[0] for r in commands}
        scored = tuple(c for c in block if c.control_ns in stamps)
        emissions = tuple(o for o in outputs if o.control_ns in stamps)
        native = check_original_native(assessment.cp, scored, emissions, commands, require_change=False)
      okay = bool(numerical['qualified'] and native is not None and native['qualified'])
      results.append({'window': wid, 'qualified': okay, 'numerical': numerical, 'registered_encoder': native})
    # Retain only one original segment of pre-roll for the following segment.
    if number + 1 in segments:
      tail = min((w['start_ns'] for w in spans.values() if w['segment'] == number + 1), default=2**64) - 1_000_000_000
      controls = [c for c in controls if c.control_ns >= tail]
      outputs = [o for o in outputs if o.control_ns >= tail]
      missing = [v for v in missing if v >= tail]
    else:
      controls = []
      outputs = []
      missing = []
  return results


def qualified_originals(results, cohort, *, require_change):
  """All windows verified; positive stage/native contribution in both roles."""
  roles = tuple(tuple(cohort[role]['windows']) for role in ('training', 'heldout'))
  ids = roles[0] + roles[1]
  if (
    not 3 <= len(roles[0]) <= 12
    or not 3 <= len(roles[1]) <= 12
    or len(set(ids)) != len(ids)
    or len(results) != len(ids)
    or {r['window'] for r in results} != set(ids)
    or not all(r['qualified'] for r in results)
  ):
    return False
  if not require_change:
    return True
  changed = {
    r['window']
    for r in results
    if r.get('numerical', {}).get('qualified')
    and r.get('numerical', {}).get('changed_frames', 0) > 0
    and r.get('registered_encoder') is not None
    and r['registered_encoder'].get('qualified')
    and r['registered_encoder'].get('candidate_changed_emissions', 0) > 0
  }
  return all(changed.intersection(role) for role in roles)


def needed_original(event, ranges, kind):
  header = int(event.logMonoTime)
  if any(start - 30_000_000 <= header <= end + 30_000_000 for start, end in ranges):
    return True
  try:
    row = event.starpilotLateralState.learningEvidence if kind == 'control' else event.starpilotCarState.appliedEvidence
    stamp = int(row.controlMonoTime)
  except (ValueError, TypeError, AttributeError, OverflowError):
    return False
  return any(start - 1_000_000_000 <= stamp <= end for start, end in ranges)
