"""Coordinate proposal contracts using declared unit fixtures."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.offroad_runtime import RouteEffects
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES, RouteStorage, startup_row
from openpilot.starpilot.flm.offroad_surface import REVISION, ROUTE_NAMES, RouteSurfaceEffects
from openpilot.starpilot.flm.tests.test_offroad_backend import scope, trial, values
from openpilot.starpilot.flm.torque_stages import StageFrame
from openpilot.starpilot.saved_source import read_saved


class TestAllThirtyCoordinateProposals(unittest.TestCase):
  @staticmethod
  def observations(bucket, speed, side='left', severity=1.0):
    return [(str(index), {'bucket': bucket, 'direction': side, 'severity': severity, 'speed_mps': speed}) for index in range(3)]

  def test_all_thirty_have_symptom_bound_candidates_that_store_load_and_change_their_actual_hook(self):
    from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates
    from openpilot.starpilot.flm.offroad_surface import route_bounds

    # Declared symptoms, not measured road improvement. Nonzero prior trims make
    # their window edges meaningful; zero-amplitude windows must not be moved.
    previous = dict.fromkeys(ROUTE_NAMES, 0.01)
    cases: list[tuple[str, float]] = [('center_chatter', speed) for speed in (2, 7, 12, 20, 30)]
    buckets = ('understeer', 'oversteer', 'late_turn_in', 'early_turn_in', 'low_speed_unwillingness', 'unwind_too_slow', 'unwind_too_fast')
    cases += [(bucket, speed) for bucket in buckets for speed in (2, 7.2, 11.5, 20.5, 21.5, 26)]
    candidates = {}
    for bucket, speed in cases:
      for side in ('left', 'right'):
        for candidate in coordinate_candidates(self.observations(bucket, speed, side), previous):
          candidates.setdefault(candidate['parameter'].removeprefix('stable:'), candidate)
    self.assertEqual(set(candidates), set(NAMES), 'A declared 30-knob learner must have a reachable proposal for every knob')
    for name, candidate in candidates.items():
      assert candidate is not None
      with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
        changed = {key for key in ROUTE_NAMES if candidate['values'][key] != previous[key]}
        self.assertEqual(changed, {name})
        self.assertLessEqual(abs(candidate['delta']), 0.01)
        low, high = route_bounds(name)
        self.assertTrue(low <= candidate['values'][name] <= high)
        params = Params(directory)
        row = {'scope': scope().document(), 'values': candidate['values'], 'previous': previous, 'route_sha256s': ['d' * 64],
               'source_start_ns': 1, 'source_end_ns': 2, 'completed_wall_ns': 3, 'analysis_sha256': 'e' * 64,
               'sequence': 1, 'state': 'ready_for_next_drive'}
        initial = dict(row, values=previous, previous=values())
        self.assertTrue(RouteStorage(params).save(initial, expected=None, authorized=lambda: True))
        baseline_raw, readable = read_saved(params, KEY, MAX_BYTES)
        self.assertTrue(readable)
        row.update(sequence=2, route_sha256s=['f' * 64], source_start_ns=4, source_end_ns=5, completed_wall_ns=6)
        self.assertTrue(RouteStorage(params).save(row, expected=baseline_raw, authorized=lambda: True))
        restored, _ = startup_row(params, scope())
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored['values'], candidate['values'])
        fresh = RouteEffects(scope(), tuple((key, restored['values'][key]) for key in ROUTE_NAMES), (), previous=tuple(previous.items()))
        self.assertEqual(fresh.status, 'configured_pending_actual_stage')
        self.assertEqual(fresh.admitted_ns, 0, 'Stored coordinates cannot supply live authority')
        before = RouteSurfaceEffects({NAMES[0]: previous[NAMES[0]]}, scope().baseline_revision, accumulated=previous)
        after = fresh.source
        differences = []
        stamp = 0
        for speed in (2, 5, 10, 15, 21, 26, 30):
          for requested in (-1.5, -0.05, 0.05, 1.5):
            for jerk in (-1.0, 1.0):
              stamp += 1
              output = []
              for effect in (before, after):
                effect.begin_frame(stamp)
                frame = StageFrame(speed, requested, jerk, 0.3, 0.3, True, False, False)
                output.append((
                  *effect.selected_stage(frame, original_anchor=effect.original_effect_anchor),
                  effect.selected_deadzone(speed, 0.0, 0.1, 0.1, requested, original_anchor=effect.original_effect_anchor),
                  effect.selected_output(speed, requested, 30.0, 5.0, -0.2, 0.1, original_anchor=effect.original_effect_anchor),
                ))
              differences.append(any(abs(a - b) > 1e-12 for a, b in zip(*output, strict=True)))
        self.assertTrue(any(differences), 'The proposed knob must affect its actual existing selected hook')

  def test_missing_conflicting_or_unsupported_evidence_cannot_generate_changes(self):
    from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates

    previous = values()
    source = self.observations('understeer', 15)
    self.assertEqual(coordinate_candidates(source[:2], previous), [])
    self.assertEqual(coordinate_candidates(source + self.observations('oversteer', 15), previous), [])
    for bucket in ('model_limited', 'saturation_limited', 'unknown'):
      self.assertEqual(coordinate_candidates(self.observations(bucket, 15), previous), [])
    for bad in (None, True, float('nan'), float('inf'), -1):
      invalid = self.observations('understeer', 15)
      for _, summary in invalid:
        summary['speed_mps'] = bad
      self.assertEqual(coordinate_candidates(invalid, previous), [])
    self.assertEqual(previous, values())

  def test_speed_knots_side_phase_and_relative_units_are_preserved(self):
    from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates

    previous = values()
    for speed, expected in ((2, {'crawl', 'low'}), (5, {'low'}), (10, {'mid'}), (15, {'fast'}), (30, {'highway'})):
      proposed = coordinate_candidates(self.observations('center_chatter', speed), previous)
      deadbands = {p['parameter'].split('_')[2] for p in proposed if 'center_deadband_' in p['parameter']}
      self.assertEqual(deadbands, expected)
      self.assertTrue(all(p['coordinate_units'] == 'physical_normalized' for p in proposed if 'center_deadband_' in p['parameter']))
    proposed = coordinate_candidates(self.observations('early_turn_in', 11.5, 'right'), previous)
    self.assertFalse(any('left' in p['parameter'] or 'speed_min' in p['parameter'] or 'speed_max' in p['parameter'] for p in proposed))
    self.assertTrue(any(p['parameter'] == 'stable:curvy_turn_in_trim_right' and p['delta'] > 0 for p in proposed))
    previous['curvy_turn_in_trim_right'] = 0.02
    expanded = coordinate_candidates(self.observations('early_turn_in', 11.5, 'right'), previous)
    edge = next(p for p in expanded if p['parameter'] == 'stable:curvy_turn_in_trim_speed_min')
    self.assertLess(edge['delta'], 0, 'Earlier trim onset reduces an observed early turn-in')
    self.assertEqual(edge['coordinate_units'], 'relative_speed_window')
    self.assertFalse(any('speed_min' in p['parameter'] for p in coordinate_candidates(self.observations('early_turn_in', 30, 'right'), previous)))

  def test_coordinate_alternatives_freeze_before_heldout_and_require_independent_support(self):
    from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates
    from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector

    roles = trial()['before']
    selector = object.__new__(StableRouteSelector)
    selector.previous = values()
    selector.rows = dict.fromkeys(roles['training']['windows'] + roles['heldout']['windows'], [])
    training = coordinate_candidates(self.observations('low_speed_unwillingness', 2), values())
    frozen = json.loads(json.dumps(training))
    for heldout in (training, [], coordinate_candidates(self.observations('early_turn_in', 2), values())):
      events = []

      def profile(windows, cohort, events=events, heldout=heldout):
        role = 'training' if windows == tuple(roles['training']['windows']) else 'heldout'
        events.append(role)
        return None, {'coordinate_candidates': training if role == 'training' else heldout}

      with patch.object(selector, 'profile', new=profile):
        legacy, proof = selector.propose({}, {'cohort': 'declared', **roles})
      self.assertIsNone(legacy)
      self.assertEqual(events, ['training', 'heldout'])
      self.assertEqual(proof['coordinate_candidates'], training if heldout == training else [])
      self.assertEqual(training, frozen, 'Heldout must not resize or replace the frozen proposals')


class TestShortTurnProposalCoverage(unittest.TestCase):
  def test_selector_retains_both_original_short_and_stable_partitions(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt
    from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector

    roles = trial()['before']
    ids = roles['training']['windows'] + roles['heldout']['windows']
    metadata = {'domain': 'lateral_acceleration', 'route_values': tuple(values().items()), 'route_revision': REVISION}

    def group(prefix):
      windows = tuple(prefix + wid[1:] for wid in ids)
      return {
        'original_metadata': metadata,
        'cohorts': [{'training': {'windows': windows[:3]}, 'heldout': {'windows': windows[3:]}}],
        'selected_original_windows': [{'identity': wid, 'segment': 0, 'start_ns': i * 10**9, 'end_ns': (i + 1) * 10**9} for i, wid in enumerate(windows)],
      }

    short, stable = group('a'), group('b')
    cp = SimpleNamespace(brand='gm', lateralTuning=SimpleNamespace(which=lambda: 'torque'))
    receipt = RouteReceipt('declared-window-reader', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
    assessment = CompletedAssessment(receipt, (short,), {}, cp, (), {}, stable_groups=(stable,))
    selector = StableRouteSelector(assessment, '.', values(), scope(), permitted=lambda: True)
    self.assertEqual(set(selector.spans), {w['identity'] for g in (short, stable) for w in g['selected_original_windows']})
    self.assertEqual(len(selector.spans), 12)

  def dispatch(self, *, training_count=3, overlap=False):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_worker as module
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt

    events = []
    ids = tuple(letter * 64 for letter in 'abcdef')
    roles = {'training': {'windows': ids[:training_count], 'reference_rms': 0.1}, 'heldout': {'windows': ids[3:], 'reference_rms': 0.1}}
    metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION, 'controller_mode': 'starpilot', 'route_scope': scope().key()}
    short = {'cohort': '2:left:turn_in', 'whole_windows': 6, 'proposal': None, **roles}
    original = [{'identity': wid, 'segment': 0, 'start_ns': (100 + i * 10) * 10**9, 'end_ns': (102 + i * 10) * 10**9} for i, wid in enumerate(ids)]
    group = {'original_metadata': metadata, 'original_context_sha256': 'f' * 64, 'cohorts': [short], 'selected_original_windows': original}
    selected = values()
    selected['turn_in_boost_left'] = 0.005
    proposal = {'parameter': 'stable:turn_in_boost_left', 'delta': 0.005, 'values': selected,
                'policy': 'original_stable_recommended_bounded_route_v1', 'coordinate_policy': 'symptom_bound_all30_v1'}
    stable_groups = ()
    if overlap:
      long_ids = tuple(str(i) * 64 for i in range(1, 7))
      long_roles = {'training': {'windows': long_ids[:3], 'reference_rms': 0.6}, 'heldout': {'windows': long_ids[3:], 'reference_rms': 0.6}}
      long_cohort = {'cohort': '2:both:center', 'whole_windows': 6, **long_roles}
      long_original = [{'identity': wid, 'segment': 0, 'start_ns': i * 10**10, 'end_ns': i * 10**10 + 6 * 10**9} for i, wid in enumerate(long_ids)]
      stable_groups = (dict(group, cohorts=[long_cohort], selected_original_windows=long_original),)
      for i in range(3):
        start = long_original[i]['start_ns']
        assert isinstance(start, int)
        original[3 + i].update(start_ns=start + 10**9, end_ns=start + 3 * 10**9)
    cp = SimpleNamespace(carFingerprint='declared-short-turn-dispatch')
    receipt = RouteReceipt('declared-short-turn', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
    assessment = CompletedAssessment(receipt, (group,), {}, cp, (), {'routeId': receipt.route_id}, stable_groups=stable_groups)

    class DeclaredSelector:
      def __init__(self, *args, **kwargs):
        pass

      def propose(self, actual_group, cohort):
        events.append(('profile', cohort['cohort']))
        if cohort['cohort'].endswith('center'):
          return proposal, {'coordinate_candidates': []}
        return None, {'coordinate_candidates': [proposal]}

    def assessed(params, root, previous, *, permitted):
      previous(cp)
      return assessment

    def training(actual, actual_group, cohort, selected_proposal, previous, root, *, permitted):
      events.append(('training', cohort['cohort']))
      if overlap:
        self.assertTrue(cohort['cohort'].endswith('center'), 'Overlapping short held-out input must never enter another training attempt')
        return None, {'selected_fraction': None, 'attempts': []}
      self.assertIs(actual, assessment)
      self.assertIs(actual_group, group)
      self.assertEqual(tuple(cohort['training']['windows']), ids[:3])
      self.assertEqual(tuple(cohort['heldout']['windows']), ids[3:])
      return selected_proposal, {'declared_dispatch_only': True}

    def refused(actual, actual_group, cohort, candidate_values, root, *, permitted):
      events.append(('native_refusal', cohort['cohort']))
      self.assertEqual(candidate_values, selected)
      return {'qualified': False, 'windows': [], 'reason': 'declared_native_refusal'}

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      with (
        patch.object(module, 'latest_route', return_value=assessment.source_inventory),
        patch.object(module, 'qualify', new=refused),
        patch('openpilot.starpilot.flm.offroad_stable_bridge.StableRouteSelector', new=DeclaredSelector),
        patch('openpilot.starpilot.flm.offroad_checks.fit_training_step', new=training),
      ):
        result = module.OffroadWorker(params, directory, Path(directory) / 'cache', permitted=lambda: True,
          assessor=assessed, checker=refused, scope_for=lambda cp, params: scope()).run_once()
      self.assertFalse(result['configuration_changed'])
      self.assertIsNone(params.get(KEY), 'A proposal or mocked native refusal cannot save an automatic tune')
    return events

  def test_short_turn_reaches_original_native_refusal_only_with_both_full_partitions(self):
    self.assertEqual(self.dispatch(training_count=2), [])
    self.assertEqual(self.dispatch(), [('profile', '2:left:turn_in'), ('training', '2:left:turn_in'), ('native_refusal', '2:left:turn_in')])

  def test_different_window_ids_cannot_hide_training_validation_time_overlap(self):
    self.assertEqual(self.dispatch(overlap=True), [('profile', '2:both:center'), ('profile', '2:left:turn_in'), ('training', '2:both:center')])


class TestRecordedProposalScoreUnits(unittest.TestCase):
  def test_real_classifier_score_keeps_measurement_units_and_uses_dimensionless_strength(self):
    from openpilot.starpilot.flm.offroad_stable_policy import FLMSample, classify_current_stable
    from openpilot.starpilot.flm.offroad_coordinate_policy import recorded_observation, coordinate_candidates

    # Declared signal, not a road or acceptance claim. Use the actual classifier
    # so its m/s^2 peak cannot be mistaken for an already-normalized severity.
    samples = [FLMSample('declared-score-units', 0, i / 10, 20.0, True, False, False,
      0.75, 1.0, 0.0, -0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 5.0, 0.0, 0.0, 0.0, 0.0) for i in range(60)]
    summaries, _ = classify_current_stable(samples)
    event = next(s for s in summaries if s['bucket'] == 'understeer')['events'][0]
    self.assertEqual(event['peakScore'], 0.25)
    observed = recorded_observation('understeer', event['direction'], event['peakScore'], 20.0)
    self.assertEqual(observed['raw_peak_score'], 0.25)
    self.assertEqual(observed['severity'], 1.25)
    candidates = coordinate_candidates([(str(i), observed) for i in range(3)], values())
    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0]['parameter'], 'stable:ff_gain_left')
    self.assertAlmostEqual(candidates[0]['delta'], 0.006)
    self.assertLessEqual(abs(candidates[0]['delta']), 0.01)
    chatter = recorded_observation('center_chatter', 'center', 0.8, 25.0)
    self.assertEqual(chatter['severity'], 0.8, 'Already normalized chatter must keep its original strength')
    for score in (float('nan'), float('inf'), -1, True):
      with self.subTest(score=score), self.assertRaises(ValueError):
        recorded_observation('understeer', 'left', score, 20.0)


if __name__ == '__main__':
  unittest.main()


class TestComparableNextDriveOutcomes(unittest.TestCase):
  """Declared numerical fixtures reproduce the captured missing-cohort/model-drift case."""

  @staticmethod
  def residual(ids, *, improved=False, **changes):
    from dataclasses import asdict
    from openpilot.starpilot.flm.offroad_residual import ResidualEstimate

    result = asdict(ResidualEstimate(
      context='d' * 64, domain='lateral_acceleration', cohort='2:left:steady',
      windows=tuple(ids), source_sha256='e' * 64, reference_rms=0.8, reference_variability=0.3,
      mean_reference=-0.6, residual_rms=0.05 if improved else 0.1,
      residual_interval=(0.04, 0.06) if improved else (0.09, 0.11),
      normalized_residual_interval=(0.05, 0.075) if improved else (0.1125, 0.1375),
      error_reversal_hz=0.4, overshoot_rms=0.01, peak_error=0.2, error_slew_rms=0.3,
    ))
    result.update(changes)
    return result

  @classmethod
  def documents(cls):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_worker import POLICY

    proposed = values()
    proposed.update(friction_threshold_mid=0.002775, friction_threshold_fast=0.0037125, friction_threshold_highway=0.0037125)
    metadata = {
      'cp_sha256': scope().cp_sha256, 'controller_mode': 'starpilot', 'domain': 'lateral_acceleration',
      'controller_revision': scope().controller_revision, 'baseline_revision': scope().baseline_revision,
      'baseline_values': [1.0, 3.66, 0.09],
      'vehicle_model_anchor': [14.656122207641602, 178034.5324200876, 187624.19209961593],
      'route_scope': scope().key(), 'route_revision': REVISION, 'route_values': list(values().items()),
    }
    original = dict(trial(), policy=POLICY, cohort='2:left:steady', previous=values(), values=proposed,
      original_metadata=metadata, assessment={'current_scope': scope().document()},
      all_original_window_checks={'qualified': True, 'policy': 'every_preselected_window_controller_and_registered_encoder_v1',
        'windows': [{'qualified': True} for _ in range(6)]})
    original['before'] = {
      'training': cls.residual([c * 64 for c in '123']),
      'heldout': cls.residual([c * 64 for c in '456']),
    }
    observed = dict(metadata, route_values=list(proposed.items()),
      vehicle_model_anchor=[14.709903717041016, 181913.73862427846, 191712.34803293645])
    cohort = {'cohort': original['cohort'], 'whole_windows': 6,
      'training': cls.residual([c * 64 for c in '789'], improved=True),
      'heldout': cls.residual([c * 64 for c in 'abc'], improved=True)}
    group = {'original_context_sha256': 'f' * 64, 'original_metadata': observed, 'cohorts': [cohort]}
    assessment = SimpleNamespace(groups=(), stable_groups=(group,), quality_stop_markers=())
    original = json.loads(json.dumps(original))
    return original, assessment

  def test_changed_model_estimates_do_not_change_fixed_vehicle_identity(self):
    import copy
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive

    original, assessment = self.documents()
    frozen = copy.deepcopy((original, assessment.stable_groups))
    result = assess_next_drive(original, assessment, scope())
    self.assertEqual(result['outcome'], 'accepted_after_drive')
    self.assertEqual(len(result['comparisons']), 1)
    self.assertTrue(all(r['improved'] for r in result['comparisons'][0]['independent']))
    self.assertEqual((original, assessment.stable_groups), frozen, 'Original source/model contexts must never be rewritten')
    self.assertFalse(result['causal_road_benefit_proven'])

  def test_started_trial_with_captured_missing_turn_shape_waits_for_evidence(self):
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive

    original, assessment = self.documents()
    # Captured estimate values; identifiers are anonymized and no raw driving logs are included.
    original['before'] = {'heldout': {'cohort': '2:left:steady',
                 'context': 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                 'domain': 'lateral_acceleration',
                 'error_reversal_hz': 1.3359018276085968,
                 'error_slew_rms': 1.0287518347929518,
                 'kind': 'uncommanded_residual_v1',
                 'mean_reference': 0.5766378949508849,
                 'normalized_residual_interval': [0.0, 0.38579609024647626],
                 'overshoot_rms': 0.039950583376129625,
                 'peak_error': 0.22540056705474854,
                 'reference_rms': 0.6731862627921118,
                 'reference_variability': 1.3793414489457103,
                 'residual_interval': [0.0, 0.13742212284197303],
                 'residual_rms': 0.05405658825978766,
                 'source_sha256': '2222222222222222222222222222222222222222222222222222222222222222',
                 'windows': ['0000000000000000000000000000000000000000000000000000000000000011',
                             '0000000000000000000000000000000000000000000000000000000000000012',
                             '0000000000000000000000000000000000000000000000000000000000000013',
                             '0000000000000000000000000000000000000000000000000000000000000014',
                             '0000000000000000000000000000000000000000000000000000000000000015',
                             '0000000000000000000000000000000000000000000000000000000000000016',
                             '0000000000000000000000000000000000000000000000000000000000000017']},
     'training': {'cohort': '2:left:steady',
                  'context': 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                  'domain': 'lateral_acceleration',
                  'error_reversal_hz': 1.1206295109082092,
                  'error_slew_rms': 0.9809542236167832,
                  'kind': 'uncommanded_residual_v1',
                  'mean_reference': 0.45779800231957724,
                  'normalized_residual_interval': [0.0, 0.5260785168657509],
                  'overshoot_rms': 0.04482235257882055,
                  'peak_error': 0.23435038328170776,
                  'reference_rms': 0.5203595961412223,
                  'reference_variability': 1.5713898010832872,
                  'residual_interval': [0.0, 0.11580012586272131],
                  'residual_rms': 0.05241822844153545,
                  'source_sha256': '1111111111111111111111111111111111111111111111111111111111111111',
                  'windows': ['0000000000000000000000000000000000000000000000000000000000000001',
                              '0000000000000000000000000000000000000000000000000000000000000002',
                              '0000000000000000000000000000000000000000000000000000000000000003',
                              '0000000000000000000000000000000000000000000000000000000000000004',
                              '0000000000000000000000000000000000000000000000000000000000000005',
                              '0000000000000000000000000000000000000000000000000000000000000006',
                              '0000000000000000000000000000000000000000000000000000000000000007']}}
    assessment.stable_groups[0]['cohorts'] = [
      {'cohort': '1:both:center', 'whole_windows': 2, 'reason': 'more_independent_stable_windows_required'},
      {'cohort': '2:both:center', 'whole_windows': 3, 'reason': 'more_independent_stable_windows_required'},
    ]
    result = assess_next_drive(original, assessment, scope(), trial_started=True)
    self.assertEqual(result['outcome'], 'pending', 'The captured five windows cannot establish a failed left-turn trial')
    self.assertEqual(result['comparisons'], [])
    self.assertEqual(result['reason'], 'trial_started_waiting_for_comparable_drive')

  def test_vehicle_baseline_domain_and_actual_map_must_still_match(self):
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive

    for changed in (
      {'cp_sha256': 'b' * 64}, {'controller_mode': 'stock'}, {'domain': 'steering_angle'},
      {'baseline_values': [1.0, 4.0, 0.09]}, {'route_scope': 'c' * 64},
      {'route_values': list(values().items())}, {'vehicle_model_anchor': [14.0, 1.0]},
      {'vehicle_model_anchor': [14.0, float('nan'), 1.0]},
    ):
      with self.subTest(changed=changed):
        original, assessment = self.documents()
        assessment.stable_groups[0]['original_metadata'].update(changed)
        result = assess_next_drive(original, assessment, scope())
        self.assertEqual(result['outcome'], 'pending')
        self.assertEqual(result['comparisons'], [])

  def test_both_independent_roles_must_improve(self):
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive

    original, assessment = self.documents()
    assessment.stable_groups[0]['cohorts'][0]['heldout'] = self.residual([c * 64 for c in 'abc'])
    result = assess_next_drive(original, assessment, scope())
    self.assertEqual(result['outcome'], 'pending')
    self.assertEqual(result['reason'], 'trial_measurement_inconclusive')
    self.assertEqual(len(result['comparisons']), 1)

  def test_easier_inputs_do_not_confirm_an_improvement(self):
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive

    original, assessment = self.documents()
    for role in ('training', 'heldout'):
      assessment.stable_groups[0]['cohorts'][0][role]['reference_rms'] = 1.2
    self.assertEqual(assess_next_drive(original, assessment, scope())['outcome'], 'pending')

  def test_measured_smoothness_regression_still_rolls_back(self):
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive

    original, assessment = self.documents()
    assessment.stable_groups[0]['cohorts'][0]['heldout']['error_reversal_hz'] = 2.0
    result = assess_next_drive(original, assessment, scope())
    self.assertEqual(result['outcome'], 'rolled_back')
    self.assertEqual(result['reason'], 'next_drive_observed_tracking_worse')

  def test_gross_quality_withdrawal_still_wins_without_comparisons(self):
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive
    from openpilot.starpilot.flm.offroad_surface import stop_marker

    original, assessment = self.documents()
    assessment.quality_stop_markers = ({'reason': stop_marker(scope().key(), original['values'], 'gross_tracking_or_oscillation_worse')},)
    result = assess_next_drive(original, assessment, scope())
    self.assertEqual(result['outcome'], 'rolled_back')
    self.assertEqual(result['reason'], 'onroad_quality_guard_withdrew_map')

  def test_reused_source_windows_cannot_confirm_an_improvement(self):
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive

    original, assessment = self.documents()
    assessment.stable_groups[0]['cohorts'][0]['training']['windows'] = original['before']['training']['windows']
    with self.assertRaisesRegex(ValueError, 'Independent actual residual'):
      assess_next_drive(original, assessment, scope())


class TestBoundedInconclusiveTrialLifecycle(unittest.TestCase):
  """Declared lifecycle fixtures; these do not grant native or road authority."""

  def saved_trial(self, directory):
    from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params

    params = Params(directory)
    cache = AssessmentCache(directory_for_params(params))
    original, _ = TestComparableNextDriveOutcomes.documents()
    digest = cache.put(original)
    row = {'scope': scope().document(), 'values': original['values'], 'previous': values(),
      'route_sha256s': ['1' * 64], 'source_start_ns': 1, 'source_end_ns': 2,
      'analysis_sha256': digest, 'completed_wall_ns': 3, 'sequence': 1, 'state': 'ready_for_next_drive'}
    self.assertTrue(RouteStorage(params).save(row, expected=None, authorized=lambda: True))
    return params, cache, original, digest

  def worker(self, params, cache, directory, digit):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_worker as module
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt

    receipt = RouteReceipt('declared-inconclusive-' + digit, digit * 64, (), 'a' * 64, 1, 2, 100 + int(digit), ())
    inventory = {'routeId': receipt.route_id}
    assessment = CompletedAssessment(receipt, (), {}, SimpleNamespace(carFingerprint='declared-unit-car'), (), inventory)

    def assessed(params, root, previous, *, permitted):
      previous(assessment.cp)
      return assessment

    def checker(*args, **kwargs):
      self.fail('Missing cohort evidence cannot fabricate a controller/encoder acceptance')

    worker = module.OffroadWorker(params, directory, cache, permitted=lambda: True, assessor=assessed,
      checker=checker, scope_for=lambda cp, params: scope())
    return worker, inventory

  def test_bounded_retries_keep_single_session_and_atomic_source_guards(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_worker as module
    from openpilot.starpilot.flm.learned_lifecycle import decode
    from openpilot.starpilot.flm.offroad_runtime import configure
    from openpilot.starpilot.flm.offroad_quality import from_configuration

    with tempfile.TemporaryDirectory() as directory:
      params, cache, original, first_digest = self.saved_trial(directory)
      digests = []
      for attempt, digit in enumerate('234', 1):
        raw = read_saved(params, KEY, MAX_BYTES)[0]
        row = decode(raw)['routeSurfaces'][scope().key()]
        digest = row['analysis_sha256']
        digests.append(digest)
        with patch('openpilot.starpilot.flm.offroad_runtime.configuration_data', return_value=(scope(), (), ())):
          owner, status = configure(None, None, SimpleNamespace(), params, manual_status='not_configured')
          self.assertIsNotNone(owner)
          assert owner is not None
          self.assertFalse(owner.frame_allowed, 'A saved retry cannot supply a frame permission')
          self.assertEqual(status, 'configured_pending_actual_stage')
          self.assertTrue(cache.claim_trial(digest, digit * 32))
          blocked, status = configure(None, None, SimpleNamespace(), params, manual_status='not_configured')
          self.assertIsNone(blocked, 'Every trial attempt stays one-use until parked assessment')
          self.assertEqual(status, 'provisional_trial_already_started')
        worker, inventory = self.worker(params, cache, directory, digit)
        with patch.object(module, 'latest_route', return_value=inventory):
          result = worker.run_once()
          saved_raw = read_saved(params, KEY, MAX_BYTES)[0]
          self.assertEqual(worker.run_once(), result)
          self.assertEqual(read_saved(params, KEY, MAX_BYTES)[0], saved_raw, 'Polling the same drive cannot consume another retry')
        saved = decode(saved_raw)['routeSurfaces'][scope().key()]
        record = cache.get(saved['analysis_sha256'])
        self.assertEqual(record['inconclusive_drives'], attempt)
        self.assertFalse(record['causal_road_benefit_proven'])
        self.assertEqual(cache.get(first_digest), original, 'Original qualification stays immutable')
        if attempt < 3:
          self.assertEqual(saved['state'], 'ready_for_next_drive')
          self.assertEqual(saved['values'], original['values'])
          self.assertEqual(saved['previous'], values())
          self.assertNotEqual(saved['analysis_sha256'], digest)
          self.assertFalse((cache.directory / ('trial-' + saved['analysis_sha256'] + '.json')).exists())
          with patch.object(module, '_current_scope', return_value=scope()):
            guard = from_configuration(None, params, cache.directory, vehicle_session='f' * 32)
          self.assertIsNotNone(guard)
          assert guard is not None
          self.assertIsNone(guard.stopped, 'The unchanged onroad guard must load the original baseline through retry lineage')
        else:
          self.assertEqual(saved['state'], 'rolled_back')
          self.assertEqual(saved['values'], values())
          self.assertEqual(result['reason'], 'trial_inconclusive_drive_limit')
      self.assertEqual(len(set(digests)), 3)

  def test_pending_store_rejects_stale_bytes_unauthorized_and_repeated_drives(self):
    from openpilot.starpilot.flm.learned_lifecycle import decode

    with tempfile.TemporaryDirectory() as directory:
      params, cache, original, digest = self.saved_trial(directory)
      raw = read_saved(params, KEY, MAX_BYTES)[0]
      store = RouteStorage(params)
      args = {'scope': scope(), 'outcome': 'pending', 'route_sha256': '2' * 64, 'completed_wall_ns': 102, 'analysis_sha256': digest}
      self.assertFalse(store.resolve(expected=None, authorized=lambda: True, **args))
      self.assertFalse(store.resolve(expected=raw, authorized=lambda: False, **args))
      self.assertEqual(read_saved(params, KEY, MAX_BYTES)[0], raw)
      self.assertTrue(store.resolve(expected=raw, authorized=lambda: True, **args))
      pending = read_saved(params, KEY, MAX_BYTES)[0]
      self.assertFalse(store.resolve(expected=pending, authorized=lambda: True, **args))
      self.assertEqual(read_saved(params, KEY, MAX_BYTES)[0], pending)
      row = decode(pending)['routeSurfaces'][scope().key()]
      self.assertEqual(row['values'], original['values'])
      self.assertEqual(row['previous'], values())
      self.assertEqual(cache.get(digest), original)
