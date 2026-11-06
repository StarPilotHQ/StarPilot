"""All preselected original windows must pass; never substitute favorable rows."""

from pathlib import Path
from openpilot.starpilot.flm.offroad_controller_checks import check_candidate, check_original_native
from openpilot.starpilot.flm.offroad_route import RETAIN
from openpilot.starpilot.flm.log_decode import decode_full_rlog_segment
from openpilot.starpilot.galaxy.drive_analysis import _closed_source


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
  for fraction in (1.0, 0.8, 0.6, 0.4, 0.2):
    values = (
      dict(proposal['values']) if fraction == 1 else {name: previous[name] + fraction * (value - previous[name]) for name, value in proposal['values'].items()}
    )
    checked = _original_results(assessment, original, train, values, root, permitted=permitted)
    feasible = len(checked) == len(train) and all(row['qualified'] for row in checked)
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


def _original_results(assessment, original, ids, values, root, *, permitted):
  spans = {w['identity']: w for w in original if w['identity'] in ids}
  if not 3 <= len(ids) <= 24 or len(set(ids)) != len(ids) or set(spans) != set(ids):
    raise ValueError('Every preselected original window required')
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
      raise ValueError('Parked checks cancelled')
    name, digest, size = receipt.segments[number]
    with _closed_source(Path(root), name, inventory['segments'][number]['files'], permitted, prefer_rlog=True) as (raw, codec, current, _):
      if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Original numerical input changed')
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
      if not current() or not permitted():
        raise ValueError('Original input changed during checks')
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
      block = tuple(c for c in controls if checkpoint <= c.control_ns <= end)
      numerical = check_candidate(assessment.cp, block, values, score_start_ns=start, score_end_ns=end, require_change=False)
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
