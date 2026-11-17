"""Retained-trial and early-suggestion contracts using declared unit observations."""

import tempfile
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.starpilot.flm import offroad_worker as worker
from openpilot.starpilot.flm.offroad_analysis import analyze
from openpilot.starpilot.flm.offroad_cache import AssessmentCache
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.learned_lifecycle import decode
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES, RouteStorage
from openpilot.starpilot.flm.tests import test_offroad_coordinate_policy as fixtures
from openpilot.starpilot.flm.tests.test_offroad_backend import scope, values
from openpilot.starpilot.saved_source import read_saved


class TestEarlySteeringSuggestions(unittest.TestCase):
  def windows(self, count):
    return tuple(next(iter(fixtures.TestCrossDriveEvidence.inputs(n)[0].windows.values()))[0] for n in range(1, count + 1))

  def test_three_to_five_windows_offer_advice_without_automatic_proposals(self):
    for count in (3, 4, 5):
      with self.subTest(count=count):
        result = analyze(self.windows(count), dict.fromkeys(NAMES, 0.0))
        cohort = result['cohorts'][0]
        self.assertEqual(result['state'], 'suggestions')
        self.assertEqual(cohort['reason'], 'recorded_provisional_suggestion')
        self.assertEqual(cohort['provisional_proposal']['parameter'], 'ff_gain_left')
        self.assertFalse(cohort['automatic_application_allowed'])
        self.assertNotIn('proposal', cohort)
        self.assertNotIn('training', cohort)
        self.assertNotIn('heldout', cohort)
        self.assertFalse(result['automatically_saved'])
        group = {'cohorts': [cohort]}
        assessment = SimpleNamespace(groups=(), diagnostic_groups=(group,))
        rows = worker.recorded_recommendations(assessment, values(), {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['reason'], 'recorded_provisional_suggestion')
        self.assertEqual(rows[0]['whole_windows'], count)

  def test_sparse_identical_and_cancelled_sources_do_not_produce_advice(self):
    for count in (1, 2):
      result = analyze(self.windows(count), dict.fromkeys(NAMES, 0.0))
      self.assertNotIn('provisional_proposal', result['cohorts'][0])
    windows = self.windows(3)
    with self.assertRaises(ValueError):
      analyze((windows[0],) * 3, dict.fromkeys(NAMES, 0.0))
    with self.assertRaisesRegex(ValueError, 'cancelled'):
      analyze(windows, dict.fromkeys(NAMES, 0.0), permitted=lambda: False)

  def test_six_windows_still_require_the_original_independent_roles(self):
    cohort = analyze(self.windows(6), dict.fromkeys(NAMES, 0.0))['cohorts'][0]
    self.assertIn('proposal', cohort)
    self.assertEqual(len(cohort['training']['windows']), 3)
    self.assertEqual(len(cohort['heldout']['windows']), 3)
    self.assertNotIn('provisional_proposal', cohort)


class TestRetainedTrialSources(unittest.TestCase):
  def drive(self, fixture, params, cache, directory, digit, *, groups=(), stops=(), qualified=True, source_digit=None, pooled=False, route_id=None):
    runner, inventory = fixture.worker(params, cache, directory, digit)
    original_assessor = runner.assessor

    def assess(*args, **kwargs):
      result = original_assessor(*args, **kwargs)
      segments = () if source_digit == '' else ((digit, (source_digit or digit) * 64, 1),)
      return replace(result, receipt=replace(result.receipt, segments=segments, route_id=route_id or result.receipt.route_id),
                     stable_groups=groups, quality_stop_markers=stops,
                     evidence_sources={'first': result, 'second': result} if pooled else {})

    runner.assessor = assess
    runner.checker = lambda *args, **kwargs: {'qualified': qualified}  # Declared unit qualification, no native/road claim.
    row = decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]
    self.assertTrue(cache.claim_trial(row['analysis_sha256'], digit * 32))
    with patch.object(worker, 'latest_route', return_value=inventory):
      return runner.run_once()

  def test_constant_depth_history_survives_retirement_and_rejects_old_copied_segments(self):
    with tempfile.TemporaryDirectory() as directory:
      cache = AssessmentCache(directory)
      trial = cache.put({'kind': 'next_drive_trial', 'assessment': {'route': {'segments': [['original', 'a' * 64, 1]]}}})
      digest = trial
      for number in range(20):
        history, complete = worker.trial_source_history(cache, digest)
        history.add(f'{number:064x}')
        digest = cache.put({'kind': 'next_drive_outcome', 'trial_analysis_sha256': trial,
          'trial_source_segments': sorted(history), 'trial_source_history_complete': complete})
      for number in range(110):
        cache.retire([digest], authorized=lambda: True)
        cache.put({'unrelated': number})
      self.assertEqual(worker.trial_origin(cache, digest)[0], trial)
      history, complete = worker.trial_source_history(cache, digest)
      self.assertTrue(complete)
      self.assertEqual(len(history), 21)
      original, _ = fixtures.TestComparableNextDriveOutcomes.documents()
      copied = SimpleNamespace(receipt=SimpleNamespace(segments=[['renamed', '0' * 64, 1]]), quality_stop_markers=())
      result = worker.assess_next_drive(original, copied, scope(), previous_segments=history)
      self.assertEqual(result['outcome'], 'pending')
      self.assertFalse(result['counts_as_new_drive'])

  def test_invalid_summary_cannot_become_independent_evidence(self):
    with tempfile.TemporaryDirectory() as directory:
      cache = AssessmentCache(directory)
      trial = cache.put({'kind': 'next_drive_trial'})
      for changed in ({'trial_source_segments': ['x' * 64]}, {'trial_source_segments': ['a' * 64] * 2},
                      {'trial_source_history_complete': 'yes'}):
        digest = cache.put({'kind': 'next_drive_outcome', 'trial_analysis_sha256': trial,
          'trial_source_segments': [], 'trial_source_history_complete': True, **changed})
        with self.assertRaises(ValueError):
          worker.trial_source_history(cache, digest)

  def test_later_success_regression_and_explicit_rollback_keep_the_original_baseline(self):
    fixture = fixtures.TestBoundedInconclusiveTrialLifecycle()
    for conclusion in ('accepted_after_drive', 'rolled_back', 'user'):
      with self.subTest(conclusion=conclusion), tempfile.TemporaryDirectory() as directory:
        params, cache, original, root = fixture.saved_trial(directory)
        raw = read_saved(params, KEY, MAX_BYTES)[0]
        history = cache.put({'kind': 'next_drive_outcome', 'trial_analysis_sha256': root,
          'trial_source_segments': [], 'trial_source_history_complete': True, 'inconclusive_drives': 20})
        store = RouteStorage(params)
        self.assertTrue(store.resolve(scope=scope(), expected=raw, outcome='pending', route_sha256='2' * 64,
          completed_wall_ns=102, analysis_sha256=history, authorized=lambda: True))
        raw = read_saved(params, KEY, MAX_BYTES)[0]
        if conclusion == 'user':
          self.assertTrue(store.rollback(scope=scope(), expected=raw, authorized=lambda: True))
        else:
          self.assertTrue(store.resolve(scope=scope(), expected=raw, outcome=conclusion, route_sha256='3' * 64,
            completed_wall_ns=103, analysis_sha256=history, authorized=lambda: True))
        row = decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]
        self.assertEqual(row['values'], original['values'] if conclusion == 'accepted_after_drive' else values())
        self.assertEqual(worker.trial_document(cache, row['analysis_sha256']), original)

  def test_worker_can_measure_success_or_regression_after_repeated_pending_drives(self):
    fixture = fixtures.TestBoundedInconclusiveTrialLifecycle()
    for conclusion in ('success', 'regression', 'safety'):
      with self.subTest(conclusion=conclusion), tempfile.TemporaryDirectory() as directory:
        params, cache, original, _ = fixture.saved_trial(directory)
        for digit in '2345':
          self.assertEqual(self.drive(fixture, params, cache, directory, digit)['status'], 'ready_for_next_drive')
        _, observation = fixtures.TestComparableNextDriveOutcomes.documents()
        stops = ()
        if conclusion == 'regression':
          observation.stable_groups[0]['cohorts'][0]['heldout']['error_reversal_hz'] = 2.0
        elif conclusion == 'safety':
          from openpilot.starpilot.flm.offroad_surface import stop_marker

          stops = ({'reason': stop_marker(scope().key(), original['values'], 'gross_tracking_or_oscillation_worse')},)
        result = self.drive(fixture, params, cache, directory, '6', groups=observation.stable_groups, stops=stops)
        expected = 'accepted_after_drive' if conclusion == 'success' else 'rolled_back'
        self.assertEqual(result['status'], expected)
        row = decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]
        self.assertEqual(row['values'], original['values'] if conclusion == 'success' else values())
        self.assertFalse(result['road_improvement_confirmed'])

  def test_bounded_source_history_keeps_tune_without_fabricating_independent_credit(self):
    fixture = fixtures.TestBoundedInconclusiveTrialLifecycle()
    with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'MAX_TRIAL_SEGMENTS', 1):
      params, cache, original, _ = fixture.saved_trial(directory)
      for digit in '23':
        self.assertEqual(self.drive(fixture, params, cache, directory, digit)['status'], 'ready_for_next_drive')
      row = decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]
      self.assertFalse(worker.trial_source_history(cache, row['analysis_sha256'])[1])
      result = self.drive(fixture, params, cache, directory, '4')
      self.assertEqual(result['reason'], 'trial_source_history_capacity_reached')
      current = decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]
      self.assertNotEqual(current['analysis_sha256'], row['analysis_sha256'], 'Keep future guarded starts available without independent credit')
      self.assertEqual(cache.get(current['analysis_sha256'])['inconclusive_drives'], 2)
      self.assertEqual(current['values'], original['values'])

  def test_resolved_trial_allows_a_new_qualified_trial_with_its_own_rollback(self):
    fixture = fixtures.TestBoundedInconclusiveTrialLifecycle()
    with tempfile.TemporaryDirectory() as directory:
      params, cache, original, root = fixture.saved_trial(directory)
      store = RouteStorage(params)
      raw = read_saved(params, KEY, MAX_BYTES)[0]
      self.assertTrue(store.resolve(scope=scope(), expected=raw, outcome='pending', route_sha256='2' * 64,
        completed_wall_ns=102, analysis_sha256=root, authorized=lambda: True))
      raw = read_saved(params, KEY, MAX_BYTES)[0]
      self.assertTrue(store.resolve(scope=scope(), expected=raw, outcome='accepted_after_drive', route_sha256='3' * 64,
        completed_wall_ns=103, analysis_sha256=root, authorized=lambda: True))
      raw = read_saved(params, KEY, MAX_BYTES)[0]
      accepted = decode(raw)['routeSurfaces'][scope().key()]
      adjusted = dict(accepted['values'], unwind_taper_left=.005)
      document = dict(original, previous=accepted['values'], values=adjusted)
      digest = cache.put(document)
      row = dict(accepted, previous=accepted['values'], values=adjusted, analysis_sha256=digest,
                 state='ready_for_next_drive', route_sha256s=['4' * 64], completed_wall_ns=104, sequence=2)
      self.assertTrue(store.save(row, expected=raw, authorized=lambda: True))
      self.assertEqual(worker.trial_document(cache, digest), document)
      self.assertTrue(store.rollback(scope=scope(), expected=read_saved(params, KEY, MAX_BYTES)[0], authorized=lambda: True))
      restored = decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]
      self.assertEqual(restored['values'], accepted['values'], 'A later trial restores its immediate prior tune, not the first baseline')

  @staticmethod
  def neutral_groups():
    original, observed = fixtures.TestComparableNextDriveOutcomes.documents()
    group, = observed.stable_groups
    for role in ('training', 'heldout'):
      current = group['cohorts'][0][role]
      group['cohorts'][0][role] = dict(original['before'][role], windows=current['windows'], source_sha256=current['source_sha256'])
    return observed.stable_groups

  def test_two_verified_neutral_drives_finish_review_without_changing_values_or_claiming_improvement(self):
    from openpilot.starpilot.flm.offroad_quality import from_configuration
    from openpilot.starpilot.flm.offroad_runtime import configure

    fixture = fixtures.TestBoundedInconclusiveTrialLifecycle()
    with tempfile.TemporaryDirectory() as directory:
      params, cache, original, root = fixture.saved_trial(directory)
      for digit, expected in (('2', 'ready_for_next_drive'), ('3', 'retained_after_drive')):
        result = self.drive(fixture, params, cache, directory, digit, groups=self.neutral_groups())
        self.assertEqual(result['status'], expected)
      raw = read_saved(params, KEY, MAX_BYTES)[0]
      row = decode(raw)['routeSurfaces'][scope().key()]
      self.assertEqual(row['values'], original['values'])
      self.assertEqual(row['previous'], original['previous'])
      self.assertEqual(cache.get(root), original)
      review = cache.get(row['analysis_sha256'])
      self.assertEqual(review['neutral_native_drives'], 2)
      proofs = [cache.get(digest) for digest in review['neutral_native_reviews']]
      self.assertEqual({p['source_receipt']['source_sha256'] for p in proofs}, {'2' * 64, '3' * 64})
      self.assertTrue(all(p['trial_analysis_sha256'] == root for p in proofs))
      self.assertTrue(all(not r['improved'] and not r['worse'] for c in review['comparisons'] for r in c['independent']))
      self.assertFalse(result['road_improvement_confirmed'])
      self.assertFalse(result['configuration_changed'], 'Completing a neutral review is not another adjustment')
      with patch('openpilot.starpilot.flm.offroad_runtime.configuration_data', return_value=(scope(), (), ())):
        owner, _ = configure(None, None, SimpleNamespace(), params, manual_status='not_configured')
      self.assertIsNotNone(owner)
      self.assertFalse(owner.frame_allowed, 'A retained store still cannot supply actual frame permission')
      with patch('openpilot.starpilot.flm.offroad_runtime.current_scope', return_value=scope()):
        guard = from_configuration(None, params, cache.directory, vehicle_session='f' * 32)
      self.assertIsNone(guard.stopped)
      self.assertEqual(guard.baseline, tuple(original['gross_baseline']), 'The original gross-regression guard remains')
      self.assertTrue(RouteStorage(params).rollback(scope=scope(), expected=raw, authorized=lambda: True))
      self.assertEqual(decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]['values'], original['previous'])

  def test_missing_unverified_easier_or_copied_drives_cannot_finish_neutral_review(self):
    fixture = fixtures.TestBoundedInconclusiveTrialLifecycle()
    for case in ('missing', 'native_refused', 'different_input', 'copied', 'same_route', 'no_segments', 'pooled'):
      with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
        params, cache, _, _ = fixture.saved_trial(directory)
        self.drive(fixture, params, cache, directory, '2', groups=self.neutral_groups())
        row = decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]
        proof = cache.get(cache.get(row['analysis_sha256'])['neutral_native_reviews'][0])
        group = self.neutral_groups()
        if case == 'different_input':
          for role in ('training', 'heldout'):
            group[0]['cohorts'][0][role]['reference_rms'] = 1.2
        result = self.drive(fixture, params, cache, directory, '3', groups=() if case == 'missing' else group,
                            qualified=case != 'native_refused', source_digit='2' if case == 'copied' else '' if case == 'no_segments' else None,
                            pooled=case == 'pooled', route_id=proof['source_receipt']['route_id'] if case == 'same_route' else None)
        self.assertEqual(result['status'], 'ready_for_next_drive')
        row = decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]
        self.assertEqual(cache.get(row['analysis_sha256'])['neutral_native_drives'], 1)

  def test_genuine_regression_and_safety_rollback_win_over_a_prior_neutral_drive(self):
    from openpilot.starpilot.flm.offroad_surface import stop_marker

    fixture = fixtures.TestBoundedInconclusiveTrialLifecycle()
    for cause in ('measurement', 'safety'):
      with self.subTest(cause=cause), tempfile.TemporaryDirectory() as directory:
        params, cache, original, _ = fixture.saved_trial(directory)
        self.drive(fixture, params, cache, directory, '2', groups=self.neutral_groups())
        groups = self.neutral_groups()
        groups[0]['cohorts'][0]['heldout']['error_reversal_hz'] = 2.
        stops = ({'reason': stop_marker(scope().key(), original['values'], 'gross_tracking_or_oscillation_worse')},) if cause == 'safety' else ()
        result = self.drive(fixture, params, cache, directory, '3', groups=groups, stops=stops)
        self.assertEqual(result['status'], 'rolled_back')
        self.assertEqual(result['configured_values'], original['previous'])

  def test_later_measured_regression_or_safety_still_rolls_back_a_finished_neutral_review(self):
    from openpilot.starpilot.flm.offroad_surface import stop_marker

    fixture = fixtures.TestBoundedInconclusiveTrialLifecycle()
    for cause in ('measurement', 'safety'):
      with self.subTest(cause=cause), tempfile.TemporaryDirectory() as directory:
        params, cache, original, _ = fixture.saved_trial(directory)
        for digit in '23':
          self.drive(fixture, params, cache, directory, digit, groups=self.neutral_groups())
        groups = self.neutral_groups()
        groups[0]['cohorts'][0]['heldout']['error_reversal_hz'] = 2.
        stops = ({'reason': stop_marker(scope().key(), original['values'], 'gross_tracking_or_oscillation_worse')},) if cause == 'safety' else ()
        result = self.drive(fixture, params, cache, directory, '4', groups=groups, stops=stops)
        self.assertEqual(result['status'], 'rolled_back')
        self.assertEqual(result['configured_values'], original['previous'])

  def test_a_finished_neutral_review_allows_a_new_checked_knob_and_its_own_rollback(self):
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment, window_demand
    from openpilot.starpilot.flm.offroad_route import RouteReceipt, SelectedSourceReceipt
    from openpilot.starpilot.flm.offroad_prior_inputs import PriorOriginals
    from openpilot.starpilot.flm.offroad_evidence import RouteWindow, window_identity, tracking_metrics
    from openpilot.starpilot.flm.offroad_store import startup_row
    from openpilot.starpilot.flm.tests.test_pid_coordinate_proposals import center_acceleration_windows

    fixture = fixtures.TestBoundedInconclusiveTrialLifecycle()
    with tempfile.TemporaryDirectory() as directory:
      params, cache, original, _ = fixture.saved_trial(directory)
      for digit in '23':
        self.drive(fixture, params, cache, directory, digit, groups=self.neutral_groups())

      def originals(route):
        result = []
        for segment, window in enumerate(center_acceleration_windows(6)):
          offset = segment * 4_000_000_000
          points = tuple(replace(p, route=route, segment=segment, mono_ns=p.mono_ns + offset,
                                 control_header_ns=p.control_header_ns + offset, output_header_ns=p.output_header_ns + offset)
                         for p in window.points)
          result.append(RouteWindow(window_identity(points), points))
        return tuple(result)

      current, prior_windows = originals('declared-new-knob'), originals('declared-independent-knob')
      cohort, = analyze(current, {n: original['values'][n] for n in NAMES})['cohorts']
      target = dict(original['values'], **cohort['proposal']['values'])
      metadata = dict(original['original_metadata'], route_values=tuple(original['values'].items()))
      spans = [{'identity': w.identity, 'route': w.points[0].route, 'segment': w.points[0].segment,
                'start_ns': w.points[0].mono_ns, 'end_ns': w.points[-1].mono_ns,
                'metrics': tracking_metrics(w.points), 'demand': window_demand(w)} for w in current]
      group = {'original_metadata': metadata, 'original_context_sha256': 'f' * 64,
               'cohorts': [cohort], 'selected_original_windows': spans}
      receipt = RouteReceipt('declared-new-knob', '4' * 64, (('current', '6' * 64, 1),), scope().cp_sha256, 1, 2, 105, ())
      inventory = {'routeId': receipt.route_id}
      cp = SimpleNamespace(carFingerprint='declared-unit-car')
      assessment = CompletedAssessment(receipt, (group,), {}, cp, (), inventory)
      prior = PriorOriginals(cp, SelectedSourceReceipt('declared-independent-knob', '5' * 64,
        (('prior', '7' * 64, 1),), scope().cp_sha256, 1, 2, 104, (), (0,)), {},
        tuple({'identity': w.identity, 'original_metadata': metadata} for w in prior_windows), prior_windows)
      calls = []

      def assessed(params, root, previous, *, permitted):
        self.assertEqual(previous(assessment.cp), original['values'])
        return assessment

      def native_protocol(actual, source, ids, values, root, *, permitted):
        # Declared native protocol only. Real statistical estimation,
        # independent-drive selection, validation and storage run unchanged.
        calls.append((actual.receipt.route_id, tuple(ids)))
        return [{'window': wid, 'qualified': True, 'numerical': {'qualified': True, 'changed_frames': 1},
                 'registered_encoder': {'qualified': True, 'candidate_changed_emissions': 1}} for wid in ids]

      runner = worker.OffroadWorker(params, directory, cache, permitted=lambda: True, assessor=assessed,
        prior_provider=lambda *args, **kwargs: prior, scope_for=lambda cp, params: scope())
      with patch.object(worker, 'latest_route', return_value=inventory), \
           patch('openpilot.starpilot.flm.offroad_checks._original_results', native_protocol), \
           patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value=scope().cp_sha256):
        result = runner.run_once()
      self.assertEqual([r for r, _ in calls], ['declared-new-knob', 'declared-independent-knob'])
      self.assertTrue(all(len(ids) == 6 for _, ids in calls), 'All original training and independent validation rows remain required')
      self.assertEqual(result['configuration_state'], 'ready_for_next_drive')
      self.assertEqual(result['configured_values'], target)
      fresh, _ = startup_row(params, scope())
      self.assertEqual(fresh['values'], target)
      self.assertEqual(fresh['previous'], original['values'])
      self.assertEqual(set(fresh['route_sha256s']), {'4' * 64, '5' * 64})
      proof = cache.get(fresh['analysis_sha256'])['native_training_feasibility']
      self.assertEqual(proof['validation_evaluations'], 1)
      self.assertTrue(proof['independent_tracking']['qualified'])
      raw = read_saved(params, KEY, MAX_BYTES)[0]
      self.assertTrue(RouteStorage(params).rollback(scope=scope(), expected=raw, authorized=lambda: True))
      self.assertEqual(decode(read_saved(params, KEY, MAX_BYTES)[0])['routeSurfaces'][scope().key()]['values'], original['values'])


class TestLegacyInconclusiveRejection(unittest.TestCase):
  def legacy(self, cache, *, reason='trial_inconclusive_drive_limit', comparisons=None, markers=()):
    original = cache.put({'kind': 'next_drive_trial', 'parameter': 'ff_gain_left', 'delta': .005})
    outcome = cache.put({'kind': 'next_drive_outcome', 'trial_analysis_sha256': original,
      'outcome': 'rolled_back', 'reason': reason, 'observed_only': True,
      'comparisons': [] if comparisons is None else comparisons, 'assessment': {'quality_stop_markers': list(markers)}})
    return {'state': 'rolled_back', 'analysis_sha256': outcome}

  def test_timeout_does_not_blacklist_a_fresh_qualified_change_or_restore_any_params(self):
    with tempfile.TemporaryDirectory() as directory:
      cache = AssessmentCache(directory)
      row = self.legacy(cache)
      before = cache.get(row['analysis_sha256'])
      proposal = {'parameter': 'ff_gain_left', 'delta': .005}
      self.assertFalse(worker.rejected_trial_change(cache, row, proposal))
      self.assertEqual(cache.get(row['analysis_sha256']), before)
      self.assertFalse(worker.rejected_trial_change(cache, None, proposal))
      self.assertFalse(worker.rejected_trial_change(cache, dict(row, state='ready_for_next_drive'), proposal))

  def test_actual_regression_safety_and_explicit_user_rollback_still_reject_same_change(self):
    with tempfile.TemporaryDirectory() as directory:
      cache = AssessmentCache(directory)
      proposal = {'parameter': 'ff_gain_left', 'delta': .005}
      for reason in ('next_drive_observed_tracking_worse', 'actual_next_drive_controller_or_native_delivery_unproven', 'explicit_user_rollback'):
        row = self.legacy(cache, reason=reason)
        self.assertTrue(worker.rejected_trial_change(cache, row, proposal))
      row = self.legacy(cache, markers=[{'reason': 'declared-safety-withdrawal'}])
      self.assertTrue(worker.rejected_trial_change(cache, row, proposal))
      self.assertFalse(worker.rejected_trial_change(cache, row, dict(proposal, delta=.006)))
      # Explicit RouteStorage.rollback keeps the trial document, rather than
      # fabricating a next-drive measurement or timeout outcome.
      original = cache.get(row['analysis_sha256'])['trial_analysis_sha256']
      self.assertTrue(worker.rejected_trial_change(cache, dict(row, analysis_sha256=original), proposal))

  def test_malformed_or_worsening_comparison_does_not_become_a_timeout_exception(self):
    with tempfile.TemporaryDirectory() as directory:
      cache = AssessmentCache(directory)
      proposal = {'parameter': 'ff_gain_left', 'delta': .005}
      for comparisons in ([None], [{'independent': []}], [{'independent': [{}, {'worse': False}]}],
                          [{'independent': [{'worse': True}, {'worse': False}]}]):
        row = self.legacy(cache, comparisons=comparisons)
        self.assertTrue(worker.rejected_trial_change(cache, row, proposal))
      row = self.legacy(cache, comparisons=[{'independent': [{'worse': False}, {'worse': False}]}])
      self.assertFalse(worker.rejected_trial_change(cache, row, proposal))


if __name__ == '__main__':
  unittest.main()
