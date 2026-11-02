"""Home displays the validated Galaxy cache without running route analysis."""

from concurrent.futures import Future
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from openpilot.starpilot.ui.home_stats_source import HomeStatsSource, read_home_stats

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC).timestamp()


def record(identifier, start):
  return {'routeId': identifier, 'startTime': start, 'endTime': start + 3600, 'distanceMeters': 1609.344,
          'durationSeconds': 3600., 'engagedSeconds': 2700., 'complete': True, 'ignored': False,
          'segmentCount': 1, 'sourceSignature': 'fixture'}


class TestHomeStatsSource(unittest.TestCase):
  def test_validated_cache_populates_both_units_periods_records_and_days_without_analysis(self):
    with TemporaryDirectory() as temp:
      path = Path(temp) / 'drive-stats.json'
      rows = [record('today', NOW - 3600), record('lastweek', NOW - 5 * 86400), record('older', NOW - 8 * 86400),
              {**record('ignored', NOW - 7200), 'ignored': True},
              {**record('incomplete', NOW - 7200), 'complete': False}]
      path.write_text(json.dumps({'schemaVersion': 1, 'scanned': True, 'routes': {row['routeId']: row for row in rows}}))
      original = path.read_bytes()
      with patch('openpilot.starpilot.galaxy.drive_stats.DriveHistory.snapshot', side_effect=AssertionError('No log scans')):
        imperial = read_home_stats(False, store=path, clock=lambda: NOW)
        metric = read_home_stats(True, store=path, clock=lambda: NOW)
      self.assertEqual((imperial.all_time.drives, imperial.all_time.distance, imperial.all_time.hours), (3, 3., 3.))
      self.assertEqual((imperial.past_week.drives, imperial.this_week.drives), (2, 1))
      self.assertAlmostEqual(metric.all_time.distance, 4.828032)
      self.assertEqual(metric.all_time.unit, 'kilometers')
      self.assertEqual(imperial.all_time.unit, 'miles')
      self.assertEqual(len(imperial.daily_distance), 7)
      self.assertTrue(imperial.daily_distance[0].is_today)
      self.assertEqual(imperial.daily_distance[0].distance, 1.)
      self.assertTrue(imperial.daily_distance[1].is_future)
      self.assertEqual(imperial.records[0].value, '1.0')
      self.assertEqual(imperial.records[0].detail, 'miles')
      self.assertEqual(path.read_bytes(), original)

  def test_missing_corrupt_oversized_and_invalid_records_are_unavailable(self):
    from openpilot.starpilot.galaxy.drive_stats import MAX_CACHE_BYTES
    with TemporaryDirectory() as temp:
      path = Path(temp) / 'drive-stats.json'
      self.assertIsNone(read_home_stats(False, store=path))
      for raw in ('bad JSON', 'x' * (MAX_CACHE_BYTES + 1), json.dumps({'schemaVersion': 1, 'routes': {'bad': {}}})):
        path.write_text(raw)
        self.assertIsNone(read_home_stats(False, store=path))

  def test_future_read_never_blocks_render_and_changes_units_without_mixing_results(self):
    pool = unittest.mock.Mock()
    first, second = Future(), Future()
    pool.submit.side_effect = [first, second]
    with patch('openpilot.starpilot.ui.home_stats_source.ThreadPoolExecutor', return_value=pool):
      source = HomeStatsSource(clock=lambda: 10.)
    self.addCleanup(source.close)
    self.assertIsNone(source.snapshot(False))
    for _ in range(100):
      self.assertIsNone(source.snapshot(False))
    self.assertEqual(pool.submit.call_count, 1)
    first.set_result('imperial')
    self.assertEqual(source.snapshot(False), 'imperial')
    self.assertIsNone(source.snapshot(True))
    second.set_result('metric')
    self.assertEqual(source.snapshot(True), 'metric')
    source.close()
    self.assertIsNone(source.snapshot(False))
    pool.shutdown.assert_called_with(wait=False, cancel_futures=True)

  def test_failed_read_retries_and_recovers_instead_of_sticking_unavailable(self):
    now = [10.]
    pool = unittest.mock.Mock()
    failed, recovered = Future(), Future()
    failed.set_exception(OSError('cache temporarily unavailable'))
    recovered.set_result('recovered')
    pool.submit.side_effect = [failed, recovered]
    with patch('openpilot.starpilot.ui.home_stats_source.ThreadPoolExecutor', return_value=pool):
      source = HomeStatsSource(clock=lambda: now[0])
    self.addCleanup(source.close)
    self.assertIsNone(source.snapshot(False))
    self.assertIsNone(source.snapshot(False))
    self.assertEqual(pool.submit.call_count, 1)
    now[0] += source.REFRESH_SECONDS
    self.assertIsNone(source.snapshot(False))
    self.assertEqual(source.snapshot(False), 'recovered')
    self.assertEqual(pool.submit.call_count, 2)

  def test_actual_read_runs_on_background_thread(self):
    entered, release = threading.Event(), threading.Event()
    threads = []
    def read(metric):
      threads.append(threading.get_ident())
      entered.set()
      release.wait(2.)
      return None
    source = HomeStatsSource(request=read)
    try:
      self.assertIsNone(source.snapshot(False))
      self.assertTrue(entered.wait(2.))
      self.assertNotEqual(threads, [threading.get_ident()])
      self.assertIsNone(source.snapshot(False))
    finally:
      release.set()
      source.close()
