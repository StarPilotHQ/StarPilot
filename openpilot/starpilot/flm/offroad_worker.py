"""Parked-drive analysis, bounded next-drive trial, then real-drive assessment.

A stored proposal is explicitly provisional. It never means live confirmation,
a native permission, physical causal proof, or a thirty-parameter identification.
"""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES as NAMES
from openpilot.starpilot.flm.learned_lifecycle import decode
from openpilot.starpilot.flm.offroad_cache import AssessmentCache
from openpilot.starpilot.flm.offroad_checks import qualify
from openpilot.starpilot.flm.offroad_processor import analyze_latest
from openpilot.starpilot.flm.offroad_route import latest_route
from openpilot.starpilot.flm.offroad_runtime import configuration_scope
from openpilot.starpilot.flm.offroad_store import RouteStorage, KEY, MAX_BYTES, NAMESPACE
from openpilot.starpilot.flm.offroad_surface import REVISION
from openpilot.starpilot.flm.offroad_tracking import TrackingEstimate, compare_observed
from openpilot.starpilot.saved_source import read_saved

POLICY = 'bounded_provisional_next_drive_v1'
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


def _current_scope(cp, params):
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
  return configuration_scope(cp, ci, controller, params, manual_status='not_configured')[0]


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


def assess_next_drive(trial, assessment, scope):
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
  before = trial['original_metadata']
  comparisons = []
  stable = trial['before']['training'].get('kind') == 'uncommanded_residual_v1'
  for group in assessment.stable_groups if stable else assessment.groups:
    metadata = group['original_metadata']
    if (
      metadata['route_scope'] != scope.key()
      or _recorded_values(metadata) != trial['values']
      or _digest([metadata[key] for key in ('cp_sha256', 'controller_mode', 'domain', 'baseline_values', 'vehicle_model_anchor')])
      != _digest([before[key] for key in ('cp_sha256', 'controller_mode', 'domain', 'baseline_values', 'vehicle_model_anchor')])
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
  return {
    'outcome': 'accepted_after_drive' if improved else 'rolled_back',
    'reason': 'independent_actual_tracking_improved' if improved else 'next_drive_worsened_or_did_not_establish_improvement',
    'observed_only': True,
    'causal_road_benefit_proven': False,
    'comparisons': comparisons,
  }


def qualification_summary(checks):
  refusals = {}
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
    'required': len(checks['windows']),
    'refusals': sorted(refusals.items()),
  }


class OffroadWorker:
  def __init__(
    self, params, log_root, cache, *, permitted, assessor=analyze_latest, checker=qualify, scope_for=_current_scope, on_analysis=None, on_assessment=None
  ):
    self.params, self.root = params, log_root
    self.cache = cache if isinstance(cache, AssessmentCache) else AssessmentCache(cache)
    self.permitted, self.assessor, self.checker, self.scope_for = permitted, assessor, checker, scope_for
    self.source_revision = _digest([(p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(Path(__file__).parent.glob('*.py'))])
    self.pending_input = None
    self.on_analysis = on_analysis
    self.on_assessment = on_assessment

  def _snapshot(self, route):
    values = []
    for key in SETTINGS:
      raw, okay = read_saved(self.params, key, MAX_BYTES)
      if not okay:
        raise ValueError('Unreadable automatic tuning setting')
      values.append((key, None if raw is None else hashlib.sha256(raw).hexdigest()))
    return _digest((POLICY, self.source_revision, route, values))

  def record_failure(self, error):
    # Preserve a failed immutable input without re-decoding it every poll.
    # Settings/source/code changes create a different fingerprint. Onroad
    # cancellation never marks a new drive completed or writes a tune.
    if self.pending_input is None or not self.permitted():
      return
    route, fingerprint = self.pending_input
    if self._snapshot(route) != fingerprint or latest_route(self.params, self.root, permitted=self.permitted) != route:
      return
    self.cache.checkpoint(
      {
        'input_fingerprint': fingerprint,
        'result': {
          'status': 'unavailable',
          'reason': str(error)[:300],
          'configuration_changed': False,
          'actual_application_proven': False,
          'road_improvement_confirmed': False,
        },
      }
    )

  def run_once(self):
    if not self.permitted():
      raise ValueError('Offroad analysis not permitted')
    route = latest_route(self.params, self.root, permitted=self.permitted)
    fingerprint = self._snapshot(route)
    self.pending_input = (route, fingerprint)
    last = self.cache.latest()
    if last is not None and last.get('input_fingerprint') == fingerprint:
      return last['result']
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
    assessment = self.assessor(self.params, self.root, prior, permitted=self.permitted)
    if self.on_assessment is not None:
      self.on_assessment(assessment)
    if not state:
      prior(assessment.cp)
    scope, row, expected = state['scope'], state['row'], state['raw']

    def authorized():
      return (
        self.permitted()
        and self.scope_for(assessment.cp, self.params) == scope
        and latest_route(self.params, self.root, permitted=self.permitted) == assessment.source_inventory
      )

    if not authorized():
      raise ValueError('Source or settings changed after fitting')
    report = assessment.report()
    report.update(policy=POLICY, current_scope=scope.document(), road_improvement_confirmed=False)
    reason = 'more_independent_eligible_tracking_required'
    status = 'collecting'
    saved = False
    offered = None
    if row is not None and assessment.receipt.source_sha256 in row['route_sha256s']:
      reason = 'this_completed_drive_already_assessed'
    elif row is not None and (row['state'] == 'ready_for_next_drive' or quality_withdrawn(assessment, scope, row['values'])):
      trial = trial_document(self.cache, row['analysis_sha256'])
      observed_checks = []
      if not quality_withdrawn(assessment, scope, row['values']):
        stable = trial['before']['training'].get('kind') == 'uncommanded_residual_v1'
        for group in assessment.stable_groups if stable else assessment.groups:
          if group['original_metadata']['route_scope'] != scope.key() or _recorded_values(group['original_metadata']) != row['values']:
            continue
          for cohort in group['cohorts']:
            if cohort['cohort'] == trial['cohort'] and 'training' in cohort and 'heldout' in cohort:
              actual = self.checker(assessment, group, cohort, row['values'], self.root, permitted=self.permitted, require_change=False)
              contribution = self.checker(assessment, group, cohort, row['previous'], self.root, permitted=self.permitted, require_change=True)
              observed_checks.append(
                {'qualified': actual['qualified'] and contribution['qualified'], 'actual_recorded_map': actual, 'compared_with_prior_map': contribution}
              )
      outcome = assess_next_drive(trial, assessment, scope)
      if outcome['outcome'] == 'accepted_after_drive' and (not observed_checks or not all(r['qualified'] for r in observed_checks)):
        outcome = {
          'outcome': 'rolled_back',
          'reason': 'actual_next_drive_controller_or_native_delivery_unproven',
          'observed_only': True,
          'causal_road_benefit_proven': False,
          'comparisons': outcome['comparisons'],
        }
      record = {
        'kind': 'next_drive_outcome',
        'policy': POLICY,
        'trial_analysis_sha256': row['analysis_sha256'],
        'assessment': report,
        'all_actual_next_drive_checks': observed_checks,
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
      status = outcome['outcome']
      reason = outcome['reason']
    else:
      current = dict(ZERO) if row is None else dict(row['values'])
      choices = []
      for group in assessment.groups:
        metadata = group['original_metadata']
        if _recorded_values(metadata) != current or metadata['controller_mode'] != scope.controller_mode:
          continue
        if metadata['route_scope'] not in ('', scope.key()):
          continue
        for cohort in group['cohorts']:
          if 'qualification_roles' in cohort and any(len(cohort['qualification_roles'][role]['windows']) < 3 for role in ('training', 'heldout')):
            continue
          proposal = cohort.get('proposal')
          if proposal is not None:
            proposal = dict(proposal, values=dict(current, **proposal['values']))
            choices.append((group, cohort, proposal))
      # Recovered Stable policy is an additional proposal source only. An
      # existing justified proposal retains priority; a later controller/native
      # refusal never starts a search for another favorable profile/window.
      if not choices and self.assessor is analyze_latest and self.checker is qualify and scope.controller_domain == 'torque':
        from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector

        selector = StableRouteSelector(assessment, self.root, current, scope, permitted=self.permitted)
        report['stable_profile_reports'] = []
        for group in assessment.stable_groups:
          metadata = group['original_metadata']
          if _recorded_values(metadata) != current or metadata['controller_mode'] != scope.controller_mode or metadata['route_scope'] not in ('', scope.key()):
            continue
          for cohort in group['cohorts']:
            if not all(role in cohort for role in ('training', 'heldout')):
              continue
            if 'qualification_roles' in cohort and any(len(cohort['qualification_roles'][role]['windows']) < 3 for role in ('training', 'heldout')):
              continue
            proposal, proof = selector.propose(group, cohort)
            report['stable_profile_reports'].append({'context': group['original_context_sha256'], 'cohort': cohort['cohort'], **proof})
            if proposal is not None:
              choices.append((group, {**cohort, 'stable_profile_proof': proof}, proposal))
      # Input coverage, followed by a fixed cohort/context tie break. A failed
      # original window does not cause a search for another favorable candidate.
      choices.sort(key=lambda v: (-v[1]['whole_windows'], v[1]['cohort'], v[0]['original_context_sha256']))
      if choices:
        group, cohort, proposal = choices[0]
        offered = {'values': proposal['values'], 'parameter': proposal['parameter'], 'delta': proposal['delta']}
        blocked = False
        if row is not None and row['state'] == 'rolled_back':
          trial = trial_document(self.cache, row['analysis_sha256'])
          blocked = trial['parameter'] == proposal['parameter'] and trial['delta'] == proposal['delta']
        if blocked:
          reason = 'same_rejected_change_requires_different_evidence_or_reset'
        else:
          feasible = True
          if self.checker is qualify:
            from openpilot.starpilot.flm.offroad_checks import fit_training_step

            fitted, fit_proof = fit_training_step(assessment, group, cohort, proposal, current, self.root, permitted=self.permitted)
            report['native_training_feasibility'] = fit_proof
            feasible = fitted is not None
            if fitted is not None:
              proposal = fitted
              offered = {'values': proposal['values'], 'parameter': proposal['parameter'], 'delta': proposal['delta']}
              if row is not None and row['state'] == 'rolled_back':
                trial = trial_document(self.cache, row['analysis_sha256'])
                feasible = not (trial['parameter'] == proposal['parameter'] and trial['delta'] == proposal['delta'])
          if not feasible:
            reason = 'no_new_native_feasible_training_step'
          else:
            checked = self.checker(assessment, group, cohort, proposal['values'], self.root, permitted=self.permitted)
            report['all_original_window_checks'] = checked
            if checked['qualified']:
              record = {
                'kind': 'next_drive_trial',
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
              new = {
                'scope': scope.document(),
                'values': proposal['values'],
                'previous': current,
                'route_sha256s': [assessment.receipt.source_sha256],
                'source_start_ns': assessment.receipt.original_start_ns,
                'source_end_ns': assessment.receipt.original_end_ns,
                'completed_wall_ns': assessment.receipt.completed_wall_ns,
                'analysis_sha256': digest,
                'sequence': 1 if row is None else row['sequence'] + 1,
                'state': 'ready_for_next_drive',
              }
              saved = RouteStorage(self.params).save(new, expected=expected, authorized=authorized)
              if not saved:
                raise ValueError('Next-drive tune compare-and-swap refused')
              status = 'ready_for_next_drive'
              reason = 'bounded_trial_configured_for_next_drive'
            else:
              reason = 'original_controller_or_registered_encoder_check_refused'
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
      if any(p.get('reason') == 'original_stable_input_fields_missing' for p in proofs):
        reason = 'recorded_steering_window_inputs_unavailable'
      elif proofs:
        reason = 'no_consistent_adjustment_in_completed_drive'
    result = {
      'status': status,
      'reason': reason,
      'configuration_changed': saved,
      'policy': POLICY,
      'completed_route': assessment.receipt.route_id,
      'analysis_counts': dict(assessment.counts),
      'actual_application_proven': False,
      'road_improvement_confirmed': False,
      'assessment_sha256': self.cache.put({'kind': 'completed_assessment', 'assessment': report}),
      'source_sha256': assessment.receipt.source_sha256,
      'vehicle': str(assessment.cp.carFingerprint),
      'recorded_tracking': report.get('recorded_tracking'),
      'suggestion_count': len(diagnostics),
      'suggestions': [
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
    # A new started/changed source cannot be marked processed under its new ID.
    if not authorized():
      raise ValueError('Offroad source changed before checkpoint')
    self.cache.checkpoint({'input_fingerprint': self._snapshot(route), 'result': result})
    return result
