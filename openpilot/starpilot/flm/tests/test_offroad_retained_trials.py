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
  def drive(self, fixture, params, cache, directory, digit, *, groups=(), stops=()):
    runner, inventory = fixture.worker(params, cache, directory, digit)
    original_assessor = runner.assessor

    def assess(*args, **kwargs):
      result = original_assessor(*args, **kwargs)
      return replace(result, receipt=replace(result.receipt, segments=((digit, digit * 64, 1),)),
                     stable_groups=groups, quality_stop_markers=stops)

    runner.assessor = assess
    runner.checker = lambda *args, **kwargs: {'qualified': True}  # Declared unit qualification, no native/road claim.
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


if __name__ == '__main__':
  unittest.main()
