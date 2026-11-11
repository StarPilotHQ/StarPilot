"""Cross-drive source isolation with declared inputs, not physical-drive claims."""

from contextlib import contextmanager
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.starpilot.flm import offroad_checks as checks
from openpilot.starpilot.flm import offroad_prior_inputs as prior_inputs


def receipt(number):
  return SimpleNamespace(route_id=f'{number:08x}--aaaaaaaaaa', source_sha256=f'{number:064x}',
                         completed_wall_ns=number, cp_sha256='a' * 64)


class TestCrossDriveSourceSafety(unittest.TestCase):
  def test_single_source_native_results_keep_source_receipt(self):
    source = receipt(1)
    raw = b'declared source'
    source.segments = (('segment', hashlib.sha256(raw).hexdigest(), len(raw)),)
    assessment = SimpleNamespace(receipt=source, source_inventory={'segments': [{'files': {}}]})
    original = [{'identity': str(i), 'segment': 0, 'start_ns': i + 10, 'end_ns': i + 11} for i in range(3)]

    @contextmanager
    def closed(*args, **kwargs):
      yield raw, 'declared', lambda: True, None

    with patch.object(checks, '_closed_source', closed), patch.object(checks, 'decode_full_rlog_segment', return_value=[]), \
         patch.object(checks, '_release_unused_analysis_heap'):
      results = checks._original_results(assessment, original, ('0', '1', '2'), {}, '.', permitted=lambda: True)
    self.assertEqual(len(results), 3)
    self.assertTrue(all(row['route'] == source.route_id and row['source_sha256'] == source.source_sha256 for row in results))
    self.assertTrue(all(not row['qualified'] for row in results))

  def test_unavailable_retained_source_refuses_every_selected_window(self):
    for error in (FileNotFoundError('evicted'), checks._Incomplete('Route source changed')):
      with self.subTest(error=type(error).__name__):
        results = self._dispatch_failure(error)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(not row['qualified'] and row['reason'] == 'historical_dense_source_unavailable' for row in results))
        self.assertTrue(all(row['source_sha256'] == receipt(1).source_sha256 for row in results))

  def test_retained_source_cancellation_and_digest_mismatch_remain_fatal(self):
    for error in (checks._Incomplete('Analysis cancelled'), ValueError('Original numerical input changed')):
      with self.subTest(error=str(error)), self.assertRaises(type(error)):
        self._dispatch_failure(error)
    with self.assertRaises(FileNotFoundError):
      self._dispatch_failure(FileNotFoundError('evicted'), cancelled=True)

  def _dispatch_failure(self, error, *, cancelled=False):
    source = SimpleNamespace(receipt=receipt(1))
    assessment = SimpleNamespace(evidence_sources={source.receipt.route_id: source})
    original = [{'identity': str(i), 'route': source.receipt.route_id} for i in range(3)]
    entry = checks._original_results
    allowed = True

    def dispatch(actual, *args, **kwargs):
      nonlocal allowed
      if actual is assessment:
        return entry(actual, *args, **kwargs)
      if cancelled:
        allowed = False
      raise error

    with patch.object(checks, '_original_results', side_effect=dispatch):
      return checks._original_results(assessment, original, ('0', '1', '2'), {}, '.', permitted=lambda: allowed)

  def test_prior_search_skips_every_pooled_source_before_decoding(self):
    sources = {str(i): SimpleNamespace(receipt=receipt(i)) for i in (1, 2, 3)}
    assessment = SimpleNamespace(receipt=receipt(4), evidence_sources=sources)
    routes = [{'routeId': receipt(i).route_id, 'segments': [{'files': {'rlog': True}}]} for i in (1, 2, 3, 4)]
    with patch.object(prior_inputs.DriveHistory, 'snapshot', return_value={'scanIncomplete': False, 'routes': routes}), \
         patch.object(prior_inputs, 'collect_route_inputs') as collect:
      found = prior_inputs.prior_originals(assessment, {'original_metadata': {}}, {}, '.', permitted=lambda: True)
    self.assertIsNone(found)
    collect.assert_not_called()

  def test_independent_fit_rejects_pooled_route_even_with_distinct_windows(self):
    self._assert_source_rejected(receipt(2))

  def test_independent_fit_rejects_pooled_digest_even_with_distinct_route(self):
    prior = receipt(1)
    prior.source_sha256 = receipt(2).source_sha256
    self._assert_source_rejected(prior)

  def _assert_source_rejected(self, prior_receipt):
    assessment = SimpleNamespace(receipt=receipt(4), evidence_sources={'old': SimpleNamespace(receipt=receipt(2))})
    cohort = {'training': {'windows': ('a', 'b', 'c')}, 'heldout': {'windows': ('d', 'e', 'f')}}
    prior = SimpleNamespace(receipt=prior_receipt, windows=tuple({'identity': str(i)} for i in range(6)))
    with patch.object(checks, '_original_results') as replay:
      with self.assertRaisesRegex(ValueError, 'Independent original same-vehicle input partitions required'):
        checks.fit_independent_route(assessment, {'selected_original_windows': []}, cohort, prior, {'values': {}}, {}, '.', permitted=lambda: True)
    replay.assert_not_called()


if __name__ == '__main__':
  unittest.main()
