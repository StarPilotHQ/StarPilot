"""Recovered Stable proposals from every preselected original tracking window.

This reader supplies no source clock, current permission or fabricated response.
The parked worker still owes its unchanged all-window controller/native checks,
atomic source-bound save and next-drive comparison/rollback.
"""

import hashlib
import math
from pathlib import Path
from statistics import fmean

from openpilot.starpilot.flm.offroad_stable_policy import (
  FLMSample,
  StablePolicy,
  cadenced_runs,
  current_catalog,
  recommended_route_profile,
  ORIGINAL_COMMIT,
  classify_current_stable,
  CURRENT_CLASSIFIER_SOURCE,
)
from openpilot.starpilot.flm.offroad_route import RETAIN
from openpilot.starpilot.flm.offroad_evidence import read_point, MAX_GAP_NS, RouteWindow
from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
from openpilot.starpilot.flm.log_decode import decode_full_rlog_segment
from openpilot.starpilot.galaxy.drive_analysis import _closed_source


def independent_profile_support(training, heldout, previous):
  """Held-out estimates must support the frozen training step, not match its bits."""
  if training is None or heldout is None:
    return False
  if any(type(row.get('values')) is not dict or set(row['values']) != set(previous) for row in (training, heldout)):
    return False
  changed = False
  for name, baseline in previous.items():
    values = (baseline, training['values'][name], heldout['values'][name])
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
      return False
    proposed, observed = values[1] - baseline, values[2] - baseline
    if proposed:
      changed = True
      if proposed * observed <= 0 or abs(proposed) > abs(observed):
        return False
  return changed


def coordinate_review(training, heldout, previous):
  """Review each frozen setting without letting a failed bundle hide usable ideas."""
  observed = {}
  for proposal in heldout:
    if proposal is not None:
      for name, value in proposal['values'].items():
        if value != previous[name]:
          observed.setdefault(name, []).append(value - previous[name])
  result = {}
  for proposal in training:
    if proposal is None:
      continue
    for name, value in proposal['values'].items():
      delta = value - previous[name]
      if delta == 0:
        continue
      support = observed.get(name, ())
      reason = 'training_candidate_not_independently_confirmed'
      if any(delta * other < 0 for other in support):
        reason = 'conflicting_recorded_candidates'
      elif any(delta * other > 0 and abs(delta) <= abs(other) for other in support):
        reason = 'independent_support_for_frozen_training_profile'
      result[name, value] = {'parameter': name, 'target': value, 'reason': reason}
  return list(result.values())


def profile_sections(windows, rows, contexts):
  """Join adjacent observations within ONE role; never fill a source gap."""
  if any(not rows.get(key) for key in windows):
    raise ValueError('Original profile samples required')
  ordered = sorted(windows, key=lambda key: (rows[key][0].route, rows[key][0].segment, rows[key][0].t))
  sections, owners = [], {}
  previous_context = None
  for key in ordered:
    values = rows[key]
    if not values:
      raise ValueError('Original profile samples required')
    first = values[0]
    previous = sections[-1][-1] if sections else None
    if previous is not None and (first.route, first.segment) == (previous.route, previous.segment) and first.t <= previous.t:
      raise ValueError('Profile roles cannot repeat or overlap original samples')
    if (
      previous is None or contexts[key] != previous_context
      or (first.route, first.segment) != (previous.route, previous.segment)
      or first.t - previous.t > MAX_GAP_NS / 1e9 + 1e-9
    ):
      sections.append([])
    for sample in values:
      stamp = (sample.route, sample.segment, sample.t)
      if stamp in owners:
        raise ValueError('Profile samples cannot be counted twice')
      owners[stamp] = key
    sections[-1].extend(values)
    previous_context = contexts[key]
  return sections, owners


class StableRouteSelector:
  def __init__(self, assessment, root, previous, scope, *, permitted):
    from openpilot.starpilot.flm.offroad_prior_inputs import PriorOriginals
    from openpilot.starpilot.flm.offroad_route import SelectedSourceReceipt

    if type(assessment) is CompletedAssessment:
      groups = []
      for group in (*assessment.stable_groups, *assessment.groups):
        ids = {
          wid for c in group['cohorts'] if all(role in c for role in ('training', 'heldout')) for role in ('training', 'heldout') for wid in c[role]['windows']
        }
        ids.update(w['identity'] for w in group.get('observed_original_windows', ()))
        groups.append((group, ids))
    elif type(assessment) is PriorOriginals and type(assessment.receipt) is SelectedSourceReceipt and assessment.profile_metadata is not None:
      groups = [
        (
          {'original_metadata': assessment.profile_metadata, 'selected_original_windows': assessment.profile_windows},
          {w['identity'] for w in assessment.profile_windows},
        )
      ]
    else:
      raise ValueError('Original completed route or explicit selected independent inputs required')
    if assessment.cp.lateralTuning.which() != 'torque' or scope.controller_domain != 'torque':
      raise ValueError('Original Stable policy uses torque lateral-acceleration units')
    scope.validate()
    self.assessment, self.root, self.previous, self.scope = assessment, Path(root), dict(previous), scope
    self.permitted = permitted
    from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
    from openpilot.starpilot.flm.offroad_surface import CURVE_NAMES

    self.catalog = current_catalog({n: previous[n] for n in NAMES}, scope.baseline_revision)
    brand = str(assessment.cp.brand)
    self.family = 'gm' if brand == 'gm' else 'standard'
    if brand == 'hyundai':
      from opendbc.car.hyundai.values import HyundaiFlags

      if assessment.cp.flags & HyundaiFlags.CANFD:
        self.family = 'hkg_canfd'
    factors = [1.0 + previous[name] for name in CURVE_NAMES]
    # No old platform preset or invented baseline/friction curve is installed.
    self.policy = StablePolicy(self.catalog, {self.family: factors})
    self.rows: dict[str, list[FLMSample] | None] | None = None
    self.spans = {}
    self.contexts = {}
    for group, ids in groups:
      if group['original_metadata']['domain'] != 'lateral_acceleration':
        continue
      for window in (*group['selected_original_windows'], *group.get('observed_original_windows', ())):
        if window['identity'] in ids:
          if window['identity'] in self.spans and self.spans[window['identity']] != window:
            raise ValueError('Conflicting original window identity')
          self.spans[window['identity']] = window
          context = group.get('original_window_contexts', {}).get(window['identity'])
          original = group['original_metadata'] if context is None else group['original_contexts'][context]
          comparison = dict(original)
          comparison.pop('route_values')
          comparison.pop('route_revision')
          context_key = hashlib.sha256(repr(comparison).encode()).hexdigest()
          if window['identity'] in self.contexts and self.contexts[window['identity']] != context_key:
            raise ValueError('Original comparison context changed')
          self.contexts[window['identity']] = context_key
    if len(self.spans) > 4096:
      raise ValueError('Original whole-window bound')

  def _load(self):
    if getattr(self.assessment, 'evidence_sources', None):
      from copy import copy

      self.rows = {}
      for route, source in self.assessment.evidence_sources.items():
        reader = copy(self)
        reader.assessment = source
        reader.spans = {key: span for key, span in self.spans.items() if span['route'] == route}
        try:
          reader._load()
        except FileNotFoundError:
          reader.rows = dict.fromkeys(reader.spans)
        assert reader.rows is not None
        self.rows.update(reader.rows)
      return
    rows = {key: [] for key in self.spans}
    original_points = {key: [] for key in self.spans}
    invalid = set()
    by_segment = {}
    for key, span in self.spans.items():
      by_segment.setdefault(span['segment'], []).append((key, span))
    receipt = self.assessment.receipt
    inventory = self.assessment.source_inventory
    for segment, spans in sorted(by_segment.items()):
      if not self.permitted():
        raise ValueError('Parked Stable analysis cancelled')
      name, digest, size = receipt.segments[segment]
      companions = {}
      with _closed_source(self.root, name, inventory['segments'][segment]['files'], self.permitted, prefer_rlog=True) as (raw, codec, current, _):
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
          raise ValueError('Original Stable input changed')
        events = decode_full_rlog_segment(raw, codec, cancelled=lambda: not self.permitted(), retain=RETAIN)
        for index, event in enumerate(events):
          if index % 128 == 0 and not self.permitted():
            raise ValueError('Parked Stable analysis cancelled')
          kind = event.which()
          if kind in ('carState', 'carControl', 'carOutput'):
            companions[kind] = event
          if kind != 'controlsState':
            continue
          stamp = int(event.logMonoTime)
          members = [key for key, span in spans if span['start_ns'] <= stamp <= span['end_ns']]
          if not members:
            continue
          contexts = {self.contexts[key] for key in members}
          if len(contexts) != 1:
            invalid.update(members)
            continue
          subtype = event.controlsState.lateralControlState.which()
          point = read_point(event, companions, route=inventory['routeId'], segment=segment, context_key=next(iter(contexts)))
          if point is None:
            continue
          if subtype != 'torqueState' or not point.active or not point.healthy or point.driver_override or point.saturated:
            invalid.update(members)
            continue
          state = event.controlsState.lateralControlState.torqueState
          required = ('desiredLateralAccel', 'actualLateralAccel', 'desiredLateralJerk', 'output')
          if any(field not in state.schema.fields for field in required):
            invalid.update(members)
            continue
          cs = companions['carState'].carState
          # These are original observed fields; no derivative interpolation,
          # zero replacement for a consumed field, or nearest-valid metadata.
          sample = FLMSample(
            point.route,
            segment,
            stamp / 1e9,
            point.speed_mps,
            point.active,
            point.driver_override,
            point.saturated,
            float(state.actualLateralAccel),
            float(state.desiredLateralAccel),
            float(state.desiredLateralJerk),
            point.actual - point.desired,
            float(getattr(state, 'errorRate', 0.0)),
            float(getattr(state, 'p', 0.0)),
            float(getattr(state, 'i', 0.0)),
            float(getattr(state, 'd', 0.0)),
            float(getattr(state, 'f', 0.0)),
            float(state.output),
            float(cs.steeringAngleDeg),
            float(cs.steeringTorque),
            float(point.commanded_torque),
            0.0 if point.emitted_torque is None else float(point.emitted_torque),
            0.0,
          )
          for key in members:
            rows[key].append(sample)
            original_points[key].append(point)
        if not current() or not self.permitted():
          raise ValueError('Original Stable source changed during analysis')
    for key, span in self.spans.items():
      values = rows[key]
      if (
        key in invalid
        or not values
        or not RouteWindow(key, tuple(original_points[key])).valid()
        or abs(values[0].t * 1e9 - span['start_ns']) > MAX_GAP_NS
        or abs(values[-1].t * 1e9 - span['end_ns']) > MAX_GAP_NS
        or any(not 0 < b.t - a.t <= MAX_GAP_NS / 1e9 + 1e-9 for a, b in zip(values, values[1:], strict=False))
      ):
        invalid.add(key)
    self.rows = {key: None if key in invalid else values for key, values in rows.items()}

  def profile(self, windows, cohort):
    if not 3 <= len(windows) <= 12 or len(set(windows)) != len(windows):
      raise ValueError('Bounded unique original profile windows required')
    if self.rows is None:
      self._load()
    assert self.rows is not None
    rows = self.rows
    if any(key not in rows or rows[key] is None for key in windows):
      return None, {'reason': 'original_stable_input_fields_missing', 'original_windows': windows}
    brand = str(self.assessment.cp.brand)
    family = 'gm' if brand == 'gm' else 'standard'
    if brand == 'hyundai':
      from opendbc.car.hyundai.values import HyundaiFlags

      if self.assessment.cp.flags & HyundaiFlags.CANFD:
        family = 'hkg_canfd'
    capabilities = {'richProfileKey': 'torque_universal', 'frictionFamily': family, 'nonlinearTorqueMap': {'asymmetric': False}}
    current = {'FLMActiveOverrides': {}}
    selected = []
    unavailable = []
    observations = []
    from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates, recorded_observation

    sections, owners = profile_sections(windows, rows, self.contexts)
    for values in sections:
      for run in cadenced_runs(values):
        summaries, _ = classify_current_stable(list(run))
        for summary in summaries:
          if not summary['events']:
            continue
          for event in summary['events']:
            event_samples = run[event['startIdx']:event['endIdx'] + 1]
            sample = event_samples[0]
            key = owners[(sample.route, sample.segment, sample.t)]
            observations.append((key, recorded_observation(
              summary['bucket'], event['direction'], event['peakScore'], fmean(sample.v_ego for sample in event_samples),
            )))
          # This is a finding's source, not a second count for an event that
          # crosses two adjacent original windows in the same role.
          sample = run[summary['events'][0]['startIdx']]
          key = owners[(sample.route, sample.segment, sample.t)]
          # Capability-based exclusions affect proposals only. No scored
          # window is discarded; ALL original spans go to the native checker.
          if summary['bucket'] == 'saturation_limited':
            unavailable.append({'window': key, 'dimension': summary['dimensionId'], 'reason': 'legacy_base_or_friction_curve_application_not_verified'})
            continue
          try:
            adjustment = self.policy._primary_delta_from_summary(summary, capabilities, current)
          except KeyError:
            unavailable.append({'window': key, 'dimension': summary['dimensionId'], 'reason': 'original_numeric_base_value_not_available'})
            continue
          if adjustment is not None:
            if adjustment['type'] not in ('vehicle_knob', 'friction_curve'):
              unavailable.append({'window': key, 'dimension': summary['dimensionId'], 'reason': 'legacy_parameter_has_no_verified_canonical_conversion'})
              continue
            selected.append({**summary, 'original_window': key, 'primaryAdjustmentRaw': adjustment})
    profiles = self.policy.build_trial_profiles('stable:' + cohort, selected, {}, capabilities)
    recommended = next((p for p in profiles if p['label'] == 'Recommended'), None)
    if recommended is not None:
      recommended['frictionCurveUnits'] = 'relative_actual_selected_threshold'
    report = {'original_windows': windows, 'supported_dimensions': len(selected), 'unavailable_dimensions': unavailable, 'profile': recommended}
    report['coordinate_candidates'] = coordinate_candidates(observations, self.previous)
    if recommended is None:
      mapped = None
    else:
      try:
        mapped = recommended_route_profile(recommended, self.previous, self.scope.baseline_revision, domain='torque')
      except ValueError as error:
        mapped = None
        report['conversion_refusal'] = str(error)
    return mapped, report

  def propose(self, group, cohort):
    ids = {role: tuple(cohort[role]['windows']) for role in ('training', 'heldout')}
    if (
      any(not 3 <= len(ids[role]) <= 12 for role in ids)
      or set(ids['training']) & set(ids['heldout'])
      or len(set(ids['training'] + ids['heldout'])) != len(ids['training'] + ids['heldout'])
    ):
      raise ValueError('Disjoint original training and held-out windows required')
    if self.rows is None:
      self._load()
    rows = self.rows
    assert rows is not None
    all_ids = ids['training'] + ids['heldout']
    if any(key not in rows or rows[key] is None for key in all_ids):
      return None, {'reason': 'original_stable_input_fields_missing', 'original_windows': all_ids}
    training, training_report = self.profile(ids['training'], cohort['cohort'])
    selection = 'training_values_only_no_heldout_resizing'
    if training is not None:
      from openpilot.starpilot.flm.offroad_surface import CURVE_NAMES

      if any(training['values'][name] != self.previous[name] for name in CURVE_NAMES):
        # Quieting is one trial; unrelated turn changes need their own evidence.
        values = dict(self.previous)
        for name in CURVE_NAMES:
          if training['values'][name] != self.previous[name]:
            values[name] = round(self.previous[name] + 0.6 * (training['values'][name] - self.previous[name]), 12)
        training = dict(
          training,
          values=values,
          changed_parameters=tuple(name for name in CURVE_NAMES if values[name] != self.previous[name]),
          maximum_coordinate_step=max(abs(values[name] - self.previous[name]) for name in CURVE_NAMES),
          bounded_profile_scale=training['bounded_profile_scale'] * 0.6,
        )
        selection = 'training_only_conservative_friction_trial_before_heldout'
    # These alternatives are frozen before held-out summaries are read.
    coordinate_training = training_report.get('coordinate_candidates', [])
    heldout, heldout_report = self.profile(ids['heldout'], cohort['cohort'])
    feedback = coordinate_review([training, *coordinate_training], [heldout, *heldout_report.get('coordinate_candidates', ())], self.previous)
    independent = {p['parameter']: p for p in heldout_report.get('coordinate_candidates', [])}
    coordinate_supported = [p for p in coordinate_training if independent_profile_support(p, independent.get(p['parameter']), self.previous)]
    # A one-coordinate recipe plus the recorded prior is exact; avoid repeating
    # thirty complete maps in both role reports and exhausting the cache bound.
    for report in (training_report, heldout_report):
      report['coordinate_candidates'] = [{k: v for k, v in p.items() if k != 'values'} for p in report.get('coordinate_candidates', [])]
    agrees = independent_profile_support(training, heldout, self.previous)
    proof = {
      'source_policy_commit': ORIGINAL_COMMIT,
      'source_classifier_commit': CURRENT_CLASSIFIER_SOURCE,
      'policy': 'original_stable_recommended_bounded_route_v1',
      'reason': 'independent_support_for_frozen_training_profile' if agrees else 'heldout_profile_does_not_support_training_step',
      'selection': selection,
      'frozen_training_profile': training,
      'observed_candidate': heldout if training is None else None,
      'roles': {'training': training_report, 'heldout': heldout_report},
      'all_original_windows_still_required': True,
      'physical_improvement_proven': False,
      'coordinate_candidates': coordinate_supported,
      'coordinate_review': feedback,
    }
    if not agrees:
      return None, proof
    assert training is not None
    return {
      'parameter': 'stable:' + ','.join(training['changed_parameters']),
      'delta': training['maximum_coordinate_step'],
      'values': training['values'],
      'policy': 'original_stable_recommended_bounded_route_v1',
    }, proof
