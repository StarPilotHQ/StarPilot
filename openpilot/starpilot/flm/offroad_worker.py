"""Parked-drive analysis, bounded next-drive trial, then real-drive assessment.

A stored proposal is explicitly provisional. It never means live confirmation,
a native permission, physical causal proof, or a thirty-parameter identification.
"""

from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES as NAMES
from openpilot.starpilot.flm.learned_lifecycle import decode
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, MAX_CHECKPOINTS
from openpilot.starpilot.flm.offroad_checks import qualify
from openpilot.starpilot.flm.offroad_processor import analyze_latest, CompletedAssessment
from openpilot.starpilot.flm.offroad_route import latest_route, logger_marker, route_inventory, source_revisions, RouteInputError, LOCAL_ID
from openpilot.starpilot.flm.offroad_runtime import configuration_scope
from openpilot.starpilot.flm.offroad_store import RouteScope, RouteStorage, KEY, MAX_BYTES, MAX_ROUTE_STEP, NAMESPACE
from openpilot.starpilot.flm.offroad_surface import REVISION
from openpilot.starpilot.flm.offroad_tracking import TrackingEstimate, compare_observed
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.flm.log_decode import LogDecodeCancelled
from openpilot.starpilot.flm.offroad_route import RouteInputCancelled

POLICY = 'bounded_provisional_next_drive_v1'
RECORDED_ASSESSOR = analyze_latest
MAX_INCONCLUSIVE_DRIVES = 3
ZERO = dict.fromkeys(NAMES, 0.0)
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, LEGACY_KEYS

SETTINGS = tuple(
  dict.fromkeys(
    (
      'SteeringLearningPreferences',
      'ForceAutoTuneOff',
      DOCUMENT_KEY,
      'AutoSteeringLearningReset',
      'LateralControllerSelection',
      'OpenpilotEnabledToggle',
      'SafeMode',
      'TurnAssist',
      *LEGACY_KEYS,
      KEY,
    )
  )
)


def _digest(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _assessment_source_revision():
  """Invalidate old results when the controller or native replay changes too."""
  from opendbc import car
  from openpilot.starpilot.flm.native_primitives import native_identity

  here = Path(__file__).resolve()
  car_root = Path(car.__file__).resolve().parent
  roots = (here.parent, here.parents[1] / 'lateral', here.parents[2] / 'common',
           here.parents[2] / 'selfdrive/controls/lib', car_root,
           *(path for path in car_root.iterdir() if path.is_dir() and path.name != 'tests'))
  files = sorted({path for root in roots for path in root.glob('*.py')})
  if not files or len(files) > 2048:
    raise ValueError('Bounded assessment implementation sources required')
  hashes = []
  for path in files:
    raw = path.read_bytes()
    if len(raw) > 2 * 1024 * 1024:
      raise ValueError('Assessment implementation source bound')
    hashes.append((str(path), hashlib.sha256(raw).hexdigest()))
  receipt = here.parents[3] / 'prebuilt.json'
  binding = hashlib.sha256(receipt.read_bytes()).hexdigest() if receipt.is_file() else None
  return _digest((hashes, native_identity(), binding))


def _current_configuration(cp, params):
  from opendbc.car.car_helpers import interfaces
  from openpilot.starpilot.lateral.controller_selection import read_selection
  from openpilot.starpilot.flm.controller_replay import SelectedControllerFactory
  from openpilot.starpilot.flm.pid_controller_replay import PidControllerFactory
  from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences

  startup = VehicleStartupPreferences.read(params, enabled=params.get_bool('OpenpilotEnabledToggle'))
  mode = 'native' if cp.lateralTuning.which() == 'pid' else str(read_selection(params, cp).mode)
  factory = PidControllerFactory(cp, mode) if cp.lateralTuning.which() == 'pid' else SelectedControllerFactory(cp, mode, turn_assist=startup.turn_assist)
  controller, _ = factory.create()
  ci = interfaces[str(cp.carFingerprint)](cp)
  from openpilot.starpilot.flm.runtime_adapter_registry import selected_descriptor

  scope, numeric = configuration_scope(cp, ci, controller, params, manual_status='not_configured')
  descriptor = selected_descriptor(cp, ci, controller)
  if descriptor is None:
    raise ValueError('Current registered controller required')
  return scope, descriptor, numeric


def _current_scope(cp, params):
  return _current_configuration(cp, params)[0]


def recorded_source_contract(cp, metadata, scope, params):
  if metadata['route_scope'] == scope.key():
    return {'kind': 'same_recorded_scope', 'current_scope': scope.key()}
  if not metadata['route_scope']:
    return None
  from openpilot.starpilot.flm.runtime_adapter_registry import baseline_revision

  current, descriptor, numeric = _current_configuration(cp, params)
  if current != scope:
    raise ValueError('Current controller/settings changed during source comparison')
  revision = metadata['controller_revision']
  if type(revision) is not str or not 1 <= len(revision) <= 192:
    return None
  recorded_descriptor = replace(descriptor, controller_revision=revision)
  baseline = baseline_revision(recorded_descriptor, numeric)
  recorded_scope = replace(scope, controller_revision=revision, baseline_revision=baseline)
  if (
    metadata['cp_sha256'] != scope.cp_sha256
    or metadata['controller_mode'] != scope.controller_mode
    or tuple(metadata['baseline_values']) != numeric
    or metadata['baseline_revision'] != baseline
    or metadata['route_scope'] != recorded_scope.key()
  ):
    return None
  # This admits original inputs for NEW reconstruction, never an old tune or permission.
  return {
    'kind': 'original_inputs_require_current_controller_native_requalification_v1',
    'original_scope': recorded_scope.document(),
    'current_scope': scope.document(),
    'original_numeric_baseline': numeric,
    'stored_tune_migrated': False,
  }


def _recorded_values(metadata):
  pairs = metadata['route_values']
  if not pairs:
    return dict(ZERO) if not metadata['route_revision'] else None
  if metadata['route_revision'] != REVISION or tuple(n for n, _ in pairs) != tuple(NAMES):
    return None
  return dict(pairs)


def _est(value):
  value = dict(value)
  if value.get('kind') == 'uncommanded_residual_v1':
    from openpilot.starpilot.flm.offroad_residual import ResidualEstimate

    for key in ('windows', 'residual_interval', 'normalized_residual_interval'):
      value[key] = tuple(value[key])
    return ResidualEstimate(**value)
  for key in ('gain_interval', 'lag_interval_ns', 'bias_interval', 'windows', 'residual_interval'):
    if key in value:
      value[key] = tuple(value[key])
  return TrackingEstimate(**value)


def trial_document(cache, digest):
  for _ in range(8):
    value = cache.get(digest)
    if value.get('kind') == 'next_drive_trial':
      return value
    if value.get('kind') != 'next_drive_outcome':
      break
    digest = value['trial_analysis_sha256']
  raise ValueError('Bounded original next-drive trial lineage required')


def quality_withdrawn(assessment, scope, values):
  from openpilot.starpilot.flm.offroad_surface import stop_marker, STOP_REASONS

  reasons = {r['reason'] for r in assessment.quality_stop_markers}
  return any(stop_marker(scope.key(), values, r) in reasons for r in STOP_REASONS)


def pending_routes(root, latest, *, on_skip=None):
  """Review the eight most recent logger inputs, newest first; no fit selection."""
  name = latest['routeId']
  if type(name) is not str or not LOCAL_ID.fullmatch(name):
    if type(latest.get('loggerCounter')) is not int:
      return [latest]
    counter = latest['loggerCounter']
  else:
    counter = int(name.split('--')[0], 16)
  inventory = route_inventory(root)
  if on_skip is not None:
    for row in inventory.get('routeErrors', ()):
      if LOCAL_ID.fullmatch(row['routeId']) and (counter - int(row['routeId'].split('--')[0], 16)) % 2**32 < MAX_CHECKPOINTS:
        on_skip(row['routeId'], row['reason'])
  candidates = []
  for route in inventory['routes']:
    if not LOCAL_ID.fullmatch(route['routeId']):
      continue
    distance = (counter - int(route['routeId'].split('--')[0], 16)) % 2**32
    if 1 <= distance < MAX_CHECKPOINTS:
      if route.get('segments') and all(s['files']['rlog'] for s in route['segments']):
        candidates.append((distance, route['routeId'], route))
      elif on_skip is not None:
        on_skip(route['routeId'], 'Original dense recording is incomplete')
  ambiguous = {distance for distance, _, _ in candidates if sum(row[0] == distance for row in candidates) > 1}
  if on_skip is not None:
    for distance, name, _ in candidates:
      if distance in ambiguous:
        on_skip(name, 'Ambiguous logger counter')
  return [latest] + [route for distance, _, route in sorted(candidates) if distance not in ambiguous]


def assess_next_drive(trial, assessment, scope, *, trial_started=True, recorded_backlog=False):
  """Compare independently held-out actual observations; no raw-RMS reward."""
  if trial.get('policy') != POLICY or trial.get('kind') != 'next_drive_trial':
    raise ValueError('Exact provisional assessment provenance required')
  if quality_withdrawn(assessment, scope, trial['values']):
    return {
      'outcome': 'rolled_back',
      'reason': 'onroad_quality_guard_withdrew_map',
      'observed_only': True,
      'causal_road_benefit_proven': False,
      'comparisons': [],
    }
  boundary = trial.get('configured_after_route')
  if boundary is None and recorded_backlog:
    return {
      'outcome': 'pending', 'reason': 'trial_configuration_chronology_unavailable',
      'counts_as_new_drive': False, 'observed_only': True, 'causal_road_benefit_proven': False, 'comparisons': [],
    }
  if boundary is not None:
    observed = assessment.receipt.route_id
    if not LOCAL_ID.fullmatch(boundary) or not LOCAL_ID.fullmatch(observed):
      raise ValueError('Exact local trial chronology required')
    distance = (int(observed.split('--')[0], 16) - int(boundary.split('--')[0], 16)) % 2**32
    if not 0 < distance < 2**31:
      return {
        'outcome': 'pending', 'reason': 'recorded_drive_precedes_trial_configuration',
        'counts_as_new_drive': False, 'observed_only': True, 'causal_road_benefit_proven': False, 'comparisons': [],
      }
  before = trial['original_metadata']
  comparisons = []
  stable = trial['before']['training'].get('kind') == 'uncommanded_residual_v1'
  for group in assessment.stable_groups if stable else assessment.groups:
    metadata = group['original_metadata']
    if (
      metadata['route_scope'] != scope.key()
      or _recorded_values(metadata) != trial['values']
      # Live vehicle-model estimates are evidence, not fixed vehicle identity.
      or _digest([metadata[key] for key in ('cp_sha256', 'controller_mode', 'domain', 'baseline_values')])
      != _digest([before[key] for key in ('cp_sha256', 'controller_mode', 'domain', 'baseline_values')])
    ):
      continue
    anchors = (before['vehicle_model_anchor'], metadata['vehicle_model_anchor'])
    if (
      any(type(a) not in (list, tuple) or len(a) not in (0, 3) for a in anchors)
      or len(anchors[0]) != len(anchors[1])
      or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for a in anchors for v in a)
    ):
      continue
    # The actual original bindings remain in both immutable assessments.
    # This is a NEW derived numerical comparison key, not a rewritten source
    # identity, stored permission, clock, live trial ACK or registry hash.
    contract = _digest(
      (
        POLICY,
        scope.document(),
        before['controller_revision'],
        metadata['controller_revision'],
        before['baseline_values'],
        before['vehicle_model_anchor'],
        metadata['vehicle_model_anchor'],
        trial['cohort'],
      )
    )
    for cohort in group['cohorts']:
      if cohort['cohort'] != trial['cohort'] or 'training' not in cohort or 'heldout' not in cohort:
        continue
      results = []
      for role in ('training', 'heldout'):
        old = replace(_est(trial['before'][role]), context=contract)
        new = replace(_est(cohort[role]), context=contract)
        if stable:
          from openpilot.starpilot.flm.offroad_residual import compare_residual

          results.append(compare_residual(old, new))
        else:
          results.append(compare_observed(old, new))
      comparisons.append({'original_context_sha256': group['original_context_sha256'], 'comparison_contract_sha256': contract, 'independent': results})
  improved = bool(comparisons and all(all(r['improved'] for r in c['independent']) for c in comparisons))
  worse = any(r['worse'] for c in comparisons for r in c['independent'])
  if worse:
    outcome, reason = 'rolled_back', 'next_drive_observed_tracking_worse'
  elif improved:
    outcome, reason = 'accepted_after_drive', 'independent_actual_tracking_improved'
  else:
    outcome = 'pending'
    reason = 'trial_measurement_inconclusive' if comparisons else (
      'trial_started_waiting_for_comparable_drive' if trial_started else 'trial_not_applied_waiting_for_eligible_drive'
    )
  return {
    'outcome': outcome,
    'reason': reason,
    'observed_only': True,
    'causal_road_benefit_proven': False,
    'comparisons': comparisons,
  }


def recorded_recommendations(assessment, previous, report):
  """Publish original candidates independently of automatic qualification.

  These are read-only starting points, never an approval, Params write or a
  replacement profile. Disagreement retains a finding without inventing a
  single suggested setting. All original fitting/held-out/native gates remain.
  """
  from openpilot.starpilot.flm.offroad_surface import route_bounds

  found: dict[str, dict[str, Any]] = {}

  def add(proposal, windows, reason, baseline=None):
    baseline = previous if baseline is None else baseline
    if type(proposal) is not dict or type(windows) is not int or windows <= 0:
      return
    symptoms = proposal.get('symptoms', ())
    if len(symptoms) == 1 and len(symptoms[0]) == 2 and reason != 'heldout_profile_does_not_support_training_step':
      reason = 'observed_' + '_'.join(symptoms[0])
    values = proposal.get('values')
    if type(values) is not dict:
      name = proposal.get('parameter', '')
      if type(name) is not str:
        return
      name = name.removeprefix('stable:')
      delta = proposal.get('delta')
      if name not in NAMES or type(delta) not in (int, float) or not math.isfinite(delta):
        return
      values = {name: baseline[name] + delta}
    for name in NAMES:
      base, target = previous[name], values.get(name, previous[name])
      if type(target) not in (int, float) or not math.isfinite(target):
        continue
      if target == baseline[name]:
        continue
      if base != baseline[name] and (target - base) * (target - baseline[name]) <= 0:
        continue  # A change already taken past this old target is not a new recommendation.
      low, high = route_bounds(name)
      if not low <= target <= high or not 1e-12 < abs(target - base) <= MAX_ROUTE_STEP + 1e-12:
        continue
      if name in found:
        row = found[name]
        if row['target'] is None or (row['target'] - base) * (target - base) < 0:
          row.update(target=None, reason='conflicting_recorded_candidates')
        elif abs(target - base) < abs(row['target'] - base) - 1e-12:
          row.update(target=target, whole_windows=windows, reason=reason)
        elif abs(row['target'] - target) <= 1e-12:
          row['whole_windows'] = max(windows, row['whole_windows'])
          if reason == 'heldout_profile_does_not_support_training_step':
            row['reason'] = reason
      else:
        found[name] = {'parameter': name, 'baseline': base, 'target': target, 'whole_windows': windows, 'reason': reason}

  for group in (*assessment.groups, *assessment.diagnostic_groups):
    for cohort in group['cohorts']:
      baseline = _recorded_values(group['original_metadata']) if 'original_metadata' in group else previous
      if baseline is not None:
        add(cohort.get('proposal'), cohort.get('whole_windows', 0), 'recorded_candidate_requires_automatic_checks', baseline)
  for proof in (*report.get('stable_profile_reports', ()), *report.get('short_turn_profile_reports', ())):
    roles = proof.get('roles', {})
    windows = sum(len(roles.get(role, {}).get('original_windows', ())) for role in ('training', 'heldout'))
    add(proof.get('frozen_training_profile'), windows, proof.get('reason', 'recorded_candidate_requires_automatic_checks'))
    add(proof.get('observed_candidate'), windows, 'recorded_candidate_not_independently_confirmed')
    for proposal in roles.get('training', {}).get('coordinate_candidates', ()):
      add(proposal, windows, 'training_candidate_not_independently_confirmed')
  for proof in report.get('observed_profile_reports', ()):
    for proposal in proof.get('coordinate_candidates', ()):
      add(proposal, len(proof['original_windows']), 'recorded_candidate_not_independently_confirmed', proof.get('source_baseline'))
  for name in report.get('refused_parameters', ()):
    if name in found:
      found[name]['reason'] = 'controller_checks_did_not_support_change'
  return [found[name] for name in NAMES if name in found]


def qualification_summary(checks):
  refusals = {}
  unperformed = checks.get('unperformed_windows', 0)
  if unperformed:
    refusals['independent_native_validation_not_run'] = unperformed
  for window in checks['windows']:
    if window['qualified']:
      continue
    check = window.get('numerical') or {}
    if check.get('qualified'):
      check = window.get('registered_encoder') or {}
    reason = window.get('reason') or check.get('detail') or check.get('reason') or 'original_window_not_qualified'
    refusals[reason] = refusals.get(reason, 0) + 1
  return {
    'passed': sum(w['qualified'] for w in checks['windows']),
    'required': len(checks['windows']) + unperformed,
    'refusals': sorted(refusals.items()),
  }


def input_coverage(cohort):
  references = []
  for role in ('training', 'heldout'):
    value = cohort.get(role, {}).get('reference_rms')
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
      return 0.0
    references.append(float(value))
  return cohort['whole_windows'] * min(references) ** 2


def _input_spans(group, cohort, ids):
  name = 'qualification_original_windows' if 'qualification_roles' in cohort else 'selected_original_windows'
  return {
    (row.get('route', ''), row['segment'], row['start_ns'], row['end_ns'])
    for row in group.get(name, ())
    if row['identity'] in ids and all(key in row for key in ('segment', 'start_ns', 'end_ns'))
  }


def newer_original_refusal(cache, receipt, scope, values):
  """An older replay cannot overrule the same change failing on newer driving."""
  for row in reversed(cache.checkpoints()):
    result = row['result']
    if (result.get('reason') != 'original_controller_or_registered_encoder_check_refused'
        or result.get('suggestion', {}).get('values') != values):
      continue
    document = cache.get(result['assessment_sha256'])
    assessment = document['assessment']
    source = assessment['route']
    if (document['kind'] != 'completed_assessment' or source['route_id'] != result['completed_route']
        or source['source_sha256'] != result['source_sha256']):
      raise ValueError('Original refused assessment source changed')
    if (RouteScope(**assessment['current_scope']).validate() == scope
        and assessment.get('all_original_window_checks', {}).get('qualified') is False
        and source['completed_wall_ns'] >= receipt.completed_wall_ns):
      return {'completed_route': source['route_id'], 'assessment_sha256': result['assessment_sha256']}
  return None


def reported_checkpoint(cache, results):
  """Display useful current-profile evidence without changing any saved decision."""
  skipped = [row for row in results if row.get('route_skipped') is True]
  usable = [row for row in results if row.get('route_skipped') is not True]
  if skipped:
    value: dict[str, Any] = dict(reported_checkpoint(cache, usable), source_is_latest=False) if usable else dict(results[0])
    faults = []
    for row in skipped:
      for fault in row.get('skipped_routes', ()):
        if fault not in faults and len(faults) < 16:
          faults.append(fault)
    value['skipped_routes'] = faults
    return value
  latest = results[0]

  def has_windows(value):
    counts = value.get('analysis_counts', {})
    return type(counts) is dict and any(type(counts.get(key)) is int and counts[key] > 0
               for key in ('qualified_whole_windows', 'stable_qualified_whole_windows', 'diagnostic_whole_windows'))

  def scope(value):
    document = cache.get(value['assessment_sha256'])
    assessment = document['assessment']
    source = assessment['route']
    if (document['kind'] != 'completed_assessment' or source['route_id'] != value['completed_route']
        or source['source_sha256'] != value['source_sha256']):
      raise ValueError('Source-bound completed assessment required for retained findings')
    return RouteScope(**assessment['current_scope']).validate()

  if (latest.get('status') != 'collecting' or latest.get('reason') != 'more_independent_eligible_tracking_required'
      or has_windows(latest)):
    return latest
  try:
    current_scope = scope(latest)
  except (KeyError, OSError, TypeError, ValueError):
    return latest
  compatible = []
  for candidate in results:
    try:
      if candidate.get('status') == 'collecting' and scope(candidate) == current_scope:
        compatible.append(candidate)
    except (KeyError, OSError, TypeError, ValueError):
      continue
  # Recording order and input coverage choose the report, never fit quality or approval.
  retained = next((value for value in compatible if has_windows(value)), None)
  if retained is None:
    return latest
  value = dict(latest)
  for key in ('completed_route', 'vehicle', 'analysis_counts', 'recorded_tracking', 'source_sha256', 'assessment_sha256',
              'recommendations', 'suggestions', 'suggestion_count', 'qualification_summary'):
    if key in retained:
      value[key] = retained[key]
    else:
      value.pop(key, None)
  value['analysis_counts'] = dict(value['analysis_counts'])
  for key in ('evidence_drives', 'evidence_windows'):
    counts = [r.get('analysis_counts', {}).get(key, 0) for r in compatible if type(r.get('analysis_counts')) is dict]
    value['analysis_counts'][key] = max((n for n in counts if type(n) is int and 0 <= n < 2**63), default=0)
  value['source_is_latest'] = False
  value['retained_decision'] = retained['reason']
  value['latest_recording'] = {key: latest[key] for key in ('completed_route', 'reason', 'analysis_counts')}
  return value  # Display only: current save, application and rollback fields stay with the latest assessment.


class OffroadWorker:
  def __init__(
    self,
    params,
    log_root,
    cache,
    *,
    permitted,
    assessor=analyze_latest,
    checker=qualify,
    scope_for=_current_scope,
    on_analysis=None,
    on_assessment=None,
    on_recommendations=None,
    prior_provider=None,
    stable_prior_provider=None,
  ):
    self.params, self.root = params, log_root
    self.cache = cache if isinstance(cache, AssessmentCache) else AssessmentCache(cache)
    self.permitted, self.assessor, self.checker, self.scope_for = permitted, assessor, checker, scope_for
    self.source_revision = _assessment_source_revision()
    self.pending_input = None
    self.pending_latest = None
    self.pending_logger = None
    self.skipped_routes = []
    self.pending_failure_data = None
    self.on_analysis = on_analysis
    self.on_assessment = on_assessment
    self.on_recommendations = on_recommendations
    self.prior_provider = prior_provider
    self.stable_prior_provider = stable_prior_provider
    self.evidence_bank = None
    if assessor is RECORDED_ASSESSOR:
      from openpilot.starpilot.flm.offroad_history import EvidenceBank

      self.evidence_bank = EvidenceBank(self.cache.directory)

  def _history_identity(self, cp):
    settings = []
    for key in SETTINGS:
      if key == KEY:
        continue
      raw, okay = read_saved(self.params, key, MAX_BYTES)
      if not okay:
        raise ValueError('Unreadable evidence history setting')
      settings.append((key, None if raw is None else hashlib.sha256(raw).hexdigest()))
    return _digest((self.scope_for(cp, self.params).document(), settings))

  def _snapshot(self, route):
    values = []
    for key in SETTINGS:
      raw, okay = read_saved(self.params, key, MAX_BYTES)
      if not okay:
        raise ValueError('Unreadable automatic tuning setting')
      values.append((key, None if raw is None else hashlib.sha256(raw).hexdigest()))
    return _digest((POLICY, self.source_revision, route, source_revisions(self.root, route), values))

  def _skip_route(self, route_id, reason):
    fault = [route_id or 'Last local recording', str(reason)[:160]]
    if fault not in self.skipped_routes and len(self.skipped_routes) < 16:
      self.skipped_routes.append(fault)

  def record_failure(self, error):
    # Preserve a failed immutable input without re-decoding it every poll.
    # Settings/source/code changes create a different fingerprint. Onroad
    # cancellation never marks a new drive completed or writes a tune.
    pending, self.pending_failure_data = self.pending_failure_data, None
    if self.pending_input is None or isinstance(error, (RouteInputCancelled, LogDecodeCancelled)) or not self.permitted():
      return
    route, fingerprint = self.pending_input
    if (self._snapshot(route) != fingerprint or logger_marker(self.params) != self.pending_logger
        or latest_route(self.params, self.root, permitted=self.permitted, inventory_only=True) != self.pending_latest):
      return
    if isinstance(error, RouteInputError):
      self._skip_route(error.route_id, error)
      if self.evidence_bank is not None:
        try:
          self.evidence_bank.exclude((error.route_id,), permitted=self.permitted)
        except RouteInputCancelled:
          return
        except (OSError, ValueError) as history_error:
          self._skip_route('Stored steering history', history_error)
      if error.route_id is not None and error.route_id != route['routeId']:
        return {'status': 'collecting', 'reason': 'skipped_unreadable_historical_drive', 'skipped_routes': list(self.skipped_routes),
                'configuration_changed': False, 'actual_application_proven': False}
    result = {
      'status': 'unavailable',
      'reason': str(error)[:300],
      'configuration_changed': False,
      'actual_application_proven': False,
      'road_improvement_confirmed': False,
      'source_is_latest': route == self.pending_latest,
      'route_skipped': isinstance(error, RouteInputError),
      'skipped_routes': list(self.skipped_routes),
    }
    if pending is not None and not isinstance(error, RouteInputError):
      authorized, recorded = pending
      if not authorized():
        return
      result.update(recorded)
    if self.evidence_bank is not None:
      result['evidence_revision'] = self.evidence_bank.revision()
    self.cache.checkpoint({'input_fingerprint': fingerprint, 'result': result})
    return result

  def run_once(self):
    self.pending_failure_data = None
    self.pending_input = None
    self.skipped_routes = []
    if not self.permitted():
      raise ValueError('Offroad analysis not permitted')
    saved, readable = read_saved(self.params, KEY, MAX_BYTES)
    if not readable:
      raise ValueError('Unreadable automatic tuning history')
    protected = [row['analysis_sha256'] for row in decode(saved).get(NAMESPACE, {}).values()]
    self.cache.retire(protected, authorized=lambda: self.permitted() and read_saved(self.params, KEY, MAX_BYTES) == (saved, True))
    self.pending_logger = logger_marker(self.params)
    latest = latest_route(self.params, self.root, permitted=self.permitted, inventory_only=True, on_skip=self._skip_route)
    if logger_marker(self.params) != self.pending_logger:
      raise ValueError('Logger identity changed during route selection')
    self.pending_latest = latest
    checkpoints = {row['input_fingerprint']: row['result'] for row in self.cache.checkpoints()}
    fingerprints = []
    for route in pending_routes(self.root, latest, on_skip=self._skip_route):
      fingerprint = self._snapshot(route)
      fingerprints.append(fingerprint)
      if (fingerprint not in checkpoints or self.evidence_bank is not None and route == latest
          and not checkpoints[fingerprint].get('route_skipped')
          and checkpoints[fingerprint].get('evidence_revision') != self.evidence_bank.revision()):
        break
    else:
      result = reported_checkpoint(self.cache, [checkpoints[key] for key in fingerprints])
      source_is_latest = result['completed_route'] == latest['routeId'] if 'completed_route' in result else result.get('source_is_latest', True)
      return dict(result, source_is_latest=source_is_latest, skipped_routes=[*result.get('skipped_routes', ()), *self.skipped_routes][:16])
    self.pending_input = (route, fingerprint)
    revisions = source_revisions(self.root, route)
    state = {}

    def prior(cp):
      scope = self.scope_for(cp, self.params)
      raw, okay = read_saved(self.params, KEY, MAX_BYTES)
      if not okay:
        raise ValueError('Unreadable automatic tuning history')
      row = decode(raw).get(NAMESPACE, {}).get(scope.key())
      state.update(scope=scope, raw=raw, row=row)
      return dict(ZERO) if row is None else dict(row['values'])

    if self.on_analysis is not None:
      self.on_analysis()
    options: dict[str, Any] = {} if self.evidence_bank is None else {'evidence_bank': self.evidence_bank, 'history_identity': self._history_identity}
    if route != latest:
      options['selected_route'] = route
    assessment = self.assessor(self.params, self.root, prior, permitted=self.permitted, **options)
    if self.on_assessment is not None:
      self.on_assessment(assessment)
    if not state:
      prior(assessment.cp)
    scope, row, expected = state['scope'], state['row'], state['raw']

    def authorized():
      if not (
        self.permitted() and self.scope_for(assessment.cp, self.params) == scope
        and logger_marker(self.params) == self.pending_logger and source_revisions(self.root, route) == revisions
        and latest_route(self.params, self.root, permitted=self.permitted, inventory_only=True) == latest
      ):
        return False
      if route == latest and assessment.source_inventory == route:
        return True
      inventory = route_inventory(self.root)
      return not inventory['scanIncomplete'] and sum(r == assessment.source_inventory for r in inventory['routes']) == 1

    if not authorized():
      raise ValueError('Source or settings changed after fitting')
    # A qualification failure must not erase completed, source-bound measurements.
    # Keep their original authority check; no suggestion, save or application is implied.
    recorded: dict[str, Any] = {
      'completed_route': assessment.receipt.route_id,
      'source_sha256': assessment.receipt.source_sha256,
      'vehicle': str(assessment.cp.carFingerprint),
      'analysis_counts': dict(assessment.counts),
      'recorded_tracking': assessment.recorded_tracking,
      'source_is_latest': assessment.receipt.route_id == latest['routeId'],
      'skipped_routes': [*self.skipped_routes, *getattr(assessment, 'route_skips', ())][:16],
    }
    self.pending_failure_data = (authorized, recorded)
    report = assessment.report()
    report.update(policy=POLICY, current_scope=scope.document(), road_improvement_confirmed=False)
    current = dict(ZERO) if row is None else dict(row['values'])
    recorded['recommendations'] = recorded_recommendations(assessment, current, report)

    choices = []
    for group in assessment.groups:
      metadata = group['original_metadata']
      if _recorded_values(metadata) != current or metadata['controller_mode'] != scope.controller_mode:
        continue
      contract = recorded_source_contract(assessment.cp, metadata, scope, self.params)
      if contract is None:
        continue
      report.setdefault('original_source_requalification', []).append(contract)
      for cohort in group['cohorts']:
        if 'qualification_roles' in cohort and any(len(cohort['qualification_roles'][role]['windows']) < 3 for role in ('training', 'heldout')):
          continue
        proposal = cohort.get('proposal')
        if proposal is not None:
          proposal = dict(proposal, values=dict(current, **proposal['values']))
          choices.append((group, cohort, proposal))
    # Freeze all proposal sources before any controller/native fit. One
    # marginal tracking proposal must not hide the Recommended profile.
    short_groups = tuple(
      group for group in assessment.groups
      if any(
        c['cohort'].rsplit(':', 1)[-1] in ('steady', 'turn_in', 'unwind')
        and all(3 <= len(c.get(role, {}).get('windows', ())) <= 12 for role in ('training', 'heldout'))
        for c in group['cohorts']
      )
    ) if type(assessment) is CompletedAssessment else ()
    if (
      type(assessment) is CompletedAssessment and (
        assessment.stable_groups or short_groups or any(g.get('observed_original_windows') for g in assessment.groups)
      )
      and self.checker is qualify and scope.controller_domain == 'torque'
    ):
      from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector

      selector = StableRouteSelector(assessment, self.root, current, scope, permitted=self.permitted)
      report['observed_profile_reports'] = []
      for group in assessment.groups:
        metadata = group['original_metadata']
        baseline = _recorded_values(metadata)
        if baseline is None or recorded_source_contract(assessment.cp, metadata, scope, self.params) is None:
          continue
        observed = group.get('observed_original_windows', ())
        source_selector = selector
        if baseline != current:
          source_selector = StableRouteSelector(assessment, self.root, baseline, scope, permitted=self.permitted)
          source_selector.rows = selector.rows
        for condition in sorted({w['cohort'] for w in observed}):
          ids = tuple(w['identity'] for w in observed if w['cohort'] == condition)
          if 3 <= len(ids) <= 12:
            _, proof = source_selector.profile(ids, condition)
            selector.rows = source_selector.rows
            report['observed_profile_reports'].append(dict(proof, source_baseline=baseline, automatic_application_allowed=False))
      report['stable_profile_reports'] = []
      for group in assessment.stable_groups:
        metadata = group['original_metadata']
        if _recorded_values(metadata) != current or metadata['controller_mode'] != scope.controller_mode:
          continue
        contract = recorded_source_contract(assessment.cp, metadata, scope, self.params)
        if contract is None:
          continue
        report.setdefault('original_source_requalification', []).append(contract)
        for cohort in group['cohorts']:
          if not all(role in cohort for role in ('training', 'heldout')):
            continue
          if 'qualification_roles' in cohort and any(len(cohort['qualification_roles'][role]['windows']) < 3 for role in ('training', 'heldout')):
            continue
          proposal, proof = selector.propose(group, cohort)
          report['stable_profile_reports'].append({'context': group['original_context_sha256'], 'cohort': cohort['cohort'], **proof})
          if proposal is not None:
            choices.append((group, {**cohort, 'stable_profile_proof': proof}, proposal))
          for alternative in proof.get('coordinate_candidates', []):
            if proposal is None or alternative['values'] != proposal['values']:
              choices.append((group, {**cohort, 'stable_profile_proof': proof}, alternative))
      # Turn entry/exit need the existing short controller windows, not a
      # six-second steady interval. Keep their original partitions and all
      # native checks; legacy quieting profiles still use stable windows.
      report['short_turn_profile_reports'] = []
      for group in short_groups:
        metadata = group['original_metadata']
        if _recorded_values(metadata) != current or metadata['controller_mode'] != scope.controller_mode:
          continue
        contract = recorded_source_contract(assessment.cp, metadata, scope, self.params)
        if contract is None:
          continue
        for cohort in group['cohorts']:
          if cohort['cohort'].rsplit(':', 1)[-1] not in ('steady', 'turn_in', 'unwind'):
            continue
          if not all(role in cohort for role in ('training', 'heldout')):
            continue
          roles = cohort.get('qualification_roles', cohort)
          if any(not 3 <= len(roles[role]['windows']) <= 12 for role in ('training', 'heldout')):
            continue
          _, proof = selector.propose(group, cohort)
          report['short_turn_profile_reports'].append({'context': group['original_context_sha256'], 'cohort': cohort['cohort'], **proof})
          for alternative in proof.get('coordinate_candidates', []):
            choices.append((group, {**cohort, 'stable_profile_proof': proof}, alternative))


    def recommendations_ready():
      recorded['recommendations'] = recorded_recommendations(assessment, current, report)
      if self.on_recommendations is not None:
        self.on_recommendations(dict(recorded))

    recommendations_ready()
    reason = 'more_independent_eligible_tracking_required'
    status = 'collecting'
    saved = False
    offered = None
    profile_suggestions = []
    selected_profile_windows = 0
    actual_drive = getattr(assessment, 'single_drive', None)
    actual_drive = actual_drive if type(actual_drive) is CompletedAssessment else assessment
    if row is not None and assessment.receipt.source_sha256 in row['route_sha256s']:
      reason = 'this_completed_drive_already_assessed'
    elif row is not None and (row['state'] == 'ready_for_next_drive' or quality_withdrawn(actual_drive, scope, row['values'])):
      trial = trial_document(self.cache, row['analysis_sha256'])
      observed_checks = []
      if not quality_withdrawn(actual_drive, scope, row['values']):
        stable = trial['before']['training'].get('kind') == 'uncommanded_residual_v1'
        for group in actual_drive.stable_groups if stable else actual_drive.groups:
          if group['original_metadata']['route_scope'] != scope.key() or _recorded_values(group['original_metadata']) != row['values']:
            continue
          for cohort in group['cohorts']:
            if cohort['cohort'] == trial['cohort'] and 'training' in cohort and 'heldout' in cohort:
              actual = self.checker(actual_drive, group, cohort, row['values'], self.root, permitted=self.permitted, require_change=False)
              contribution = self.checker(actual_drive, group, cohort, row['previous'], self.root, permitted=self.permitted, require_change=True)
              observed_checks.append(
                {'qualified': actual['qualified'] and contribution['qualified'], 'actual_recorded_map': actual, 'compared_with_prior_map': contribution}
              )
      started = os.path.lexists(self.cache.directory / ('trial-' + row['analysis_sha256'] + '.json'))
      attempts = self.cache.get(row['analysis_sha256']).get('inconclusive_drives', 0)
      if type(attempts) is not int or not 0 <= attempts < MAX_INCONCLUSIVE_DRIVES:
        raise ValueError('Bounded inconclusive trial history required')
      outcome = assess_next_drive(trial, actual_drive, scope, trial_started=started, recorded_backlog=route != latest)
      counts_as_new_drive = outcome.get('counts_as_new_drive', True)
      if outcome['outcome'] == 'pending' and started and counts_as_new_drive:
        attempts += 1
        if attempts >= MAX_INCONCLUSIVE_DRIVES:
          outcome = dict(outcome, outcome='rolled_back', reason='trial_inconclusive_drive_limit')
      if outcome['outcome'] == 'accepted_after_drive' and (not observed_checks or not all(r['qualified'] for r in observed_checks)):
        outcome = {
          'outcome': 'rolled_back',
          'reason': 'actual_next_drive_controller_or_native_delivery_unproven',
          'observed_only': True,
          'causal_road_benefit_proven': False,
          'comparisons': outcome['comparisons'],
        }
      if outcome['outcome'] == 'pending' and (not started or not counts_as_new_drive):
        status, reason = 'ready_for_next_drive', outcome['reason']
        report['unused_recording_review'] = outcome
        report['recorded_tracking'] = trial.get('assessment', {}).get('recorded_tracking')
      else:
        record = {
          'kind': 'next_drive_outcome',
          'policy': POLICY,
          'trial_analysis_sha256': row['analysis_sha256'],
          'assessment': report,
          'all_actual_next_drive_checks': observed_checks,
          'inconclusive_drives': attempts,
          **outcome,
        }
        digest = self.cache.put(record)
        saved = RouteStorage(self.params).resolve(
          scope=scope,
          expected=expected,
          outcome=outcome['outcome'],
          route_sha256=assessment.receipt.source_sha256,
          completed_wall_ns=assessment.receipt.completed_wall_ns,
          analysis_sha256=digest,
          authorized=authorized,
        )
        if not saved:
          raise ValueError('Next-drive outcome compare-and-swap refused')
        status = 'ready_for_next_drive' if outcome['outcome'] == 'pending' else outcome['outcome']
        reason = outcome['reason']
    else:
      # Freeze input-only ranking; training may select a candidate, validation may not.
      choices.sort(key=lambda v: (-input_coverage(v[1]), -v[1]['whole_windows'], v[1]['cohort'], v[0]['original_context_sha256']))
      recommendations_ready()
      training_seen = set()
      training_spans = set()
      for group, cohort, proposal in choices:
        selected_profile_windows = cohort['whole_windows']
        offered = {'values': proposal['values'], 'parameter': proposal['parameter'], 'delta': proposal['delta']}
        blocked = False
        if row is not None and row['state'] == 'rolled_back':
          trial = trial_document(self.cache, row['analysis_sha256'])
          blocked = trial['parameter'] == proposal['parameter'] and trial['delta'] == proposal['delta']
        if blocked:
          reason = 'same_rejected_change_requires_different_evidence_or_reset'
          continue
        else:
          feasible = True
          independent_checks = None
          if self.checker is qualify:
            from openpilot.starpilot.flm.offroad_checks import fit_training_step

            independent = None
            stable = proposal.get('policy') == 'original_stable_recommended_bounded_route_v1'
            provider = self.stable_prior_provider if stable else self.prior_provider
            independent = None if provider is None else provider(assessment, group, cohort, self.root, permitted=self.permitted)
            if independent is not None and row is not None and independent.receipt.source_sha256 in row['route_sha256s']:
              independent = None
            roles = cohort.get('qualification_roles', cohort)
            training = tuple(roles['training']['windows'])
            validation = tuple(roles['heldout']['windows'])
            if independent is not None:
              training += validation
              validation = tuple(w['identity'] for w in independent.windows)
            validation_spans = _input_spans(group, cohort, validation) if independent is None else ()
            if training_seen.intersection(validation) or any(
              a[:2] == b[:2] and max(a[2], b[2]) <= min(a[3], b[3]) for a in training_spans for b in validation_spans
            ):
              reason = 'candidate_validation_already_used_for_training'
              continue
            training_seen.update(training)
            training_spans.update(_input_spans(group, cohort, training))
            if independent is None:
              fitted, fit_proof = fit_training_step(assessment, group, cohort, proposal, current, self.root, permitted=self.permitted)
            else:
              from openpilot.starpilot.flm.offroad_checks import fit_independent_route

              fitted, fit_proof, independent_checks = fit_independent_route(
                assessment, group, cohort, independent, proposal, current, self.root, permitted=self.permitted, scope=scope
              )
            report['native_training_feasibility'] = fit_proof
            report.setdefault('native_candidate_training', []).append(
              {'parameter': proposal['parameter'], 'cohort': cohort['cohort'], 'feasibility': fit_proof}
            )
            if fitted is None and (independent_checks is not None or fit_proof.get('validation_evaluations', 0)):
              raise ValueError('A validation result cannot select another candidate')
            feasible = fitted is not None
            if fitted is not None:
              proposal = fitted
              offered = {'values': proposal['values'], 'parameter': proposal['parameter'], 'delta': proposal['delta']}
              if row is not None and row['state'] == 'rolled_back':
                trial = trial_document(self.cache, row['analysis_sha256'])
                feasible = not (trial['parameter'] == proposal['parameter'] and trial['delta'] == proposal['delta'])
          if not feasible:
            reason = 'no_new_native_feasible_training_step'
            if independent_checks is not None:
              break
            continue
          else:
            refusal = newer_original_refusal(self.cache, assessment.receipt, scope, proposal['values'])
            if refusal is not None:
              report['newer_original_refusal'] = refusal
              report['refused_parameters'] = [name for name in NAMES if proposal['values'][name] != current[name]]
              reason = 'newer_drive_refused_same_adjustment'
              break
            checked = (
              independent_checks
              if independent_checks is not None
              else self.checker(assessment, group, cohort, proposal['values'], self.root, permitted=self.permitted)
            )
            report['all_original_window_checks'] = checked
            if checked['qualified']:
              record = {
                'kind': 'next_drive_trial',
                'configured_after_route': latest['routeId'],
                'policy': POLICY,
                'assessment': report,
                'parameter': proposal['parameter'],
                'delta': proposal['delta'],
                'values': proposal['values'],
                'previous': current,
                'cohort': cohort['cohort'],
                'original_metadata': group['original_metadata'],
                'before': {role: cohort[role] for role in ('training', 'heldout')},
                'all_original_window_checks': checked,
                'confirmed_improvement': False,
                'proposal_policy': proposal.get('policy', 'observed_tracking_v1'),
                'stable_profile_proof': cohort.get('stable_profile_proof'),
                'native_training_feasibility': report.get('native_training_feasibility'),
                'gross_policy': 'fixed_input_matched_2s_v1' if cohort.get('stable_profile_proof') is not None else 'original_2s_v1',
                'gross_baseline': [
                  (w.get('gross_reference') if cohort.get('stable_profile_proof') is not None else w)
                  for w in group['selected_original_windows']
                  if w['identity'] in set(cohort['training']['windows']) | set(cohort['heldout']['windows'])
                ],
              }
              if 'qualification_roles' in cohort:
                roles = cohort['qualification_roles']
                ids = set(roles['training']['windows']) | set(roles['heldout']['windows'])
                record.update(
                  gross_policy='separate_original_2s_v1',
                  gross_original_roles=roles,
                  tracking_source_grade=group['tracking_source_grade'],
                  gross_baseline=[w for w in group['qualification_original_windows'] if w['identity'] in ids],
                )
              from openpilot.starpilot.flm.offroad_quality import TrialGuard

              if TrialGuard(scope.key(), proposal['values'], record).stopped is not None:
                raise ValueError('Original onroad gross-guard baseline unavailable')
              digest = self.cache.put(record)
              new: dict[str, Any] = {
                'scope': scope.document(),
                'values': proposal['values'],
                'previous': current,
                'route_sha256s': sorted({w['source_sha256'] for w in checked['windows']})
                if assessment.evidence_sources else [assessment.receipt.source_sha256]
                + ([] if independent_checks is None else [independent_checks['source_partitions']['heldout']]),
                'source_start_ns': assessment.receipt.original_start_ns,
                'source_end_ns': assessment.receipt.original_end_ns,
                'completed_wall_ns': max(
                  [assessment.receipt.completed_wall_ns] + [source.receipt.completed_wall_ns for source in assessment.evidence_sources.values()]
                ),
                'analysis_sha256': digest,
                'sequence': 1 if row is None else row['sequence'] + 1,
                'state': 'ready_for_next_drive',
              }
              def original_sources_current(new=new):
                if not authorized():
                  return False
                required = [s.source_inventory for s in assessment.evidence_sources.values() if s.receipt.source_sha256 in new['route_sha256s']]
                if not required:
                  return True
                inventory = route_inventory(self.root)
                return not inventory['scanIncomplete'] and all(sum(r == source for r in inventory['routes']) == 1 for source in required)

              saved = RouteStorage(self.params).save(new, expected=expected, authorized=original_sources_current)
              if not saved:
                raise ValueError('Next-drive tune compare-and-swap refused')
              status = 'ready_for_next_drive'
              reason = 'bounded_trial_configured_for_next_drive'
            else:
              reason = checked.get('reason', 'original_controller_or_registered_encoder_check_refused')
              if any(name in ('candidate encoder clipping regression', 'candidate changed an actual limiter/support')
                     for name, _ in qualification_summary(checked)['refusals']):
                report['refused_parameters'] = [name for name in NAMES if proposal['values'][name] != current[name]]
            break
      if not saved and offered is not None and offered['parameter'].startswith('stable:'):
        profile_suggestions = [
          {'parameter': name, 'delta': value - current[name], 'whole_windows': selected_profile_windows, 'qualification': 'controller_native_checks_not_passed'}
          for name, value in offered['values'].items()
          if value != current[name]
        ]
    diagnostics = [(g, c, c['proposal']) for g in assessment.diagnostic_groups for c in g['cohorts'] if c.get('proposal') is not None]
    diagnostics.sort(key=lambda v: (-v[1]['whole_windows'], v[1]['cohort'], v[0]['original_tracking_context_sha256']))
    if offered is None:
      if diagnostics:
        _, c, v = diagnostics[0]
        offered = {
          'values': v['values'],
          'parameter': v['parameter'],
          'delta': v['delta'],
          'qualification': 'historical_starting_point_requires_controller_native_checks',
        }
        if status == 'collecting':
          reason = 'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application'
    if status == 'collecting' and offered is None:
      proofs = report.get('stable_profile_reports', ())
      if any(p.get('coordinate_candidates') for p in report.get('observed_profile_reports', ())):
        reason = 'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application'
      elif any(p.get('reason') == 'original_stable_input_fields_missing' for p in proofs):
        reason = 'recorded_steering_window_inputs_unavailable'
      elif proofs:
        reason = 'no_consistent_adjustment_in_completed_drive'
    result = {
      'status': status,
      'reason': reason,
      'configuration_changed': saved,
      'policy': POLICY,
      'completed_route': assessment.receipt.route_id,
      'source_is_latest': assessment.receipt.route_id == latest['routeId'],
      'analysis_counts': dict(assessment.counts),
      'actual_application_proven': False,
      'road_improvement_confirmed': False,
      'assessment_sha256': self.cache.put({'kind': 'completed_assessment', 'assessment': report}),
      'source_sha256': assessment.receipt.source_sha256,
      'vehicle': str(assessment.cp.carFingerprint),
      'recorded_tracking': report.get('recorded_tracking'),
      'skipped_routes': [*self.skipped_routes, *getattr(assessment, 'route_skips', ())][:16],
      'suggestion_count': len(diagnostics) + len(profile_suggestions),
      'recommendations': recorded_recommendations(assessment, current, report),
      'suggestions': profile_suggestions
      + [
        {'parameter': v['parameter'], 'delta': v['delta'], 'whole_windows': c['whole_windows'], 'qualification': 'controller_native_checks_not_passed'}
        for _, c, v in diagnostics[:16]
      ],
    }
    if offered is not None:
      result['suggestion'] = offered
    checks = report.get('all_original_window_checks')
    if checks is not None:
      result['qualification_summary'] = qualification_summary(checks)
    current_raw, readable = read_saved(self.params, KEY, MAX_BYTES)
    if not readable:
      raise ValueError('Saved route configuration unreadable after assessment')
    configured = decode(current_raw).get(NAMESPACE, {}).get(scope.key())
    result['saved_adjustments'] = 0 if configured is None else sum(value != 0 for value in configured['values'].values())
    if configured is not None:
      result['configured_values'] = dict(configured['values'])
      result['configuration_state'] = configured['state']
      result['recommendations'] = [r for r in result['recommendations'] if r['target'] is None or r['target'] != configured['values'][r['parameter']]]
    result['recommendation_count'] = len(result['recommendations'])
    # A new started/changed source cannot be marked processed under its new ID.
    if not authorized():
      raise ValueError('Offroad source changed before checkpoint')
    if self.evidence_bank is not None:
      result['evidence_revision'] = self.evidence_bank.revision()
    self.cache.checkpoint({'input_fingerprint': self._snapshot(route), 'result': result})
    self.pending_failure_data = None
    return result

