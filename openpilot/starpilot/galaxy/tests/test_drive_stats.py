from unittest.mock import Mock
"""Durable local-drive statistics and background analysis contract."""

from datetime import UTC, datetime
from pathlib import Path
import copy
import json
import tempfile
import threading
import time
import unittest
from unittest import mock

from openpilot.starpilot.galaxy import drive_stats
from openpilot.starpilot.galaxy.drive_stats import DriveStatsOwner, MAX_CACHED_ROUTES

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC).timestamp()


class History:
  def __init__(self, root, routes):
    self.root = root
    self.routes = routes
    self.scans = 0
    self.incomplete = False

  def snapshot(self):
    self.scans += 1
    return {'routes': list(self.routes), 'scanIncomplete': self.incomplete}


def route(identifier, number=0):
  return {'routeId': identifier, 'segmentCount': 1,
          'segments': [{'number': number, 'segmentName': f'{identifier}--{number}', 'files': {'rlog': True}}]}


def analyzed(root, selected, permitted):
  return {'routeId': selected['routeId'], 'startTime': NOW - 60, 'endTime': NOW,
          'distanceMeters': 1000.0, 'durationSeconds': 60.0, 'engagedSeconds': 30.0,
          'engagedPercent': 50.0, 'model': 'Test Model', 'distractedMoments': 1,
          'unresponsiveMoments': 0, 'complete': True, 'reason': None, 'segmentCount': 1}


class DriveStatsTest(unittest.TestCase):
  def test_past_week_is_seven_calendar_dates_and_excludes_old_future_ignored_and_incomplete(self):
    self.now[0] = datetime(2026, 11, 2, 12, tzinfo=UTC).timestamp()
    owner = self.owner(permitted=lambda: False)
    for identifier, offset in (('today', 0), ('boundary', 6), ('old', 7), ('future', -1)):
      value = analyzed(self.root, route(identifier), lambda: True)
      value.update(startTime=self.now[0] - offset * 86400, endTime=self.now[0] - offset * 86400 + 60,
                   ignored=False, sourceSignature='fixture')
      owner._records[identifier] = value
    owner._records['ignored'] = dict(owner._records['today'], routeId='ignored', ignored=True)
    owner._records['incomplete'] = dict(owner._records['today'], routeId='incomplete', complete=False)
    snapshot = owner.snapshot()
    self.assertEqual(snapshot['pastWeek']['drives'], 2)
    self.assertEqual(snapshot['pastWeek']['distanceMeters'], 2000.)
    self.assertEqual(snapshot['pastWeek']['durationSeconds'], 120.)
    self.assertEqual(snapshot['totals']['drives'], 4)

  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name) / 'logs'
    self.root.mkdir()
    self.store = Path(self.temp.name) / 'state' / 'drive-stats.json'
    self.history = History(self.root, [route('abc')])
    self.now = [NOW]
    self.owners = []

  def owner(self, analyzer=analyzed, permitted=lambda: True, monotonic=None, metric=None):
    owner = DriveStatsOwner(root=self.root, store=self.store, history=Mock(wraps=self.history),
                            analyzer=analyzer, permitted=permitted, clock=lambda: self.now[0],
                            monotonic=monotonic if monotonic is not None else lambda: self.now[0], metric=metric)
    self.owners.append(owner)
    self.addCleanup(owner.close)
    return owner

  def completed(self, owner, scans=1):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
      with owner._lock:
        worker = owner._worker
      if self.history.scans >= scans and worker and not worker.is_alive():
        return owner.snapshot()
      time.sleep(.01)
    self.fail('background scan did not finish')

  def test_recording_dates_reads_cache_without_starting_analysis(self):
    owner = self.owner()
    record = analyzed(self.root, route('abc'), lambda: True)
    owner._records = {'abc': record}
    self.assertEqual(owner.recording_dates(['abc', 'missing']), {'abc': NOW - 60})
    self.assertIsNone(owner._worker)
    self.assertEqual(self.history.scans, 0)

  def test_aol_persists_and_exclusion_preserves_ordinary_engagement(self):
    def with_aol(root, selected, permitted):
      return {**analyzed(root, selected, permitted), 'aolSeconds': 15.0, 'aolPercent': 25.0}
    owner = self.owner(analyzer=with_aol)
    owner._start()
    result = self.completed(owner)
    self.assertEqual(result['totals']['aolSeconds'], 15)
    self.assertEqual(result['totals']['engagedSeconds'], 30)
    self.assertEqual(result['week']['aolPercent'], 25)
    self.assertEqual(result['lastDrive']['aolSeconds'], 15)
    loaded = self.owner().snapshot()
    self.assertEqual(loaded['recentDrives'][0]['aolPercent'], 25)
    ignored = owner.ignore('abc', True, lambda: True)
    self.assertIsNone(ignored['totals']['aolSeconds'])
    self.assertEqual(ignored['totals']['aolDrives'], 0)
    self.assertEqual(ignored['recentDrives'][0]['aolSeconds'], 15)

  def test_aol_aggregates_only_known_routes_and_weights_by_duration(self):
    owner = self.owner(permitted=lambda: False)
    base = {**analyzed(self.root, route('abc'), lambda: True), 'ignored': False}
    owner._records = {
      'abc': {**base, 'aolSeconds': 15, 'aolPercent': 25},
      'long': {**base, 'routeId': 'long', 'durationSeconds': 180, 'aolSeconds': 135, 'aolPercent': 75},
      'old': {**base, 'routeId': 'old'},
      'gap': {**base, 'routeId': 'gap', 'aolSeconds': None, 'aolPercent': None},
      'ignored': {**base, 'routeId': 'ignored', 'ignored': True, 'aolSeconds': 60, 'aolPercent': 100},
      'incomplete': {**base, 'routeId': 'incomplete', 'complete': False, 'aolSeconds': 60, 'aolPercent': 100},
    }
    result = owner.snapshot()
    for summary in (result['totals'], result['week']):
      self.assertEqual(summary['aolSeconds'], 150)
      self.assertEqual(summary['aolPercent'], 62.5)
      self.assertEqual(summary['aolDurationSeconds'], 240)
      self.assertEqual(summary['aolDrives'], 2)
      self.assertEqual(summary['drives'], 4)
    self.assertEqual(result['totals']['engagedSeconds'], 120)

  def test_cache_rejects_invalid_aol_without_requiring_it_in_old_records(self):
    owner = self.owner()
    owner._start()
    self.completed(owner)
    record = json.loads(self.store.read_text())['routes']['abc']
    self.assertTrue(drive_stats._valid_record(record))
    for fields in ({'aolSeconds': -1, 'aolPercent': 0}, {'aolSeconds': True, 'aolPercent': 0},
                   {'aolSeconds': float('nan'), 'aolPercent': 0}, {'aolSeconds': 61, 'aolPercent': 100},
                   {'aolSeconds': 1, 'aolPercent': 101}, {'aolSeconds': 1, 'aolPercent': None}):
      with self.subTest(fields=fields):
        self.assertFalse(drive_stats._valid_record({**record, **fields}))

  def test_complete_aggregation_week_models_and_exclusion_persist(self):
    owner = self.owner()
    owner._start()
    initial = owner.snapshot()
    self.assertEqual(initial['totals']['drives'], 0)
    result = self.completed(owner)
    self.assertEqual(result['totals']['distanceMeters'], 1000)
    self.assertEqual(result['totals']['engagedSeconds'], 30)
    self.assertEqual(result['week']['engagedPercent'], 50)
    self.assertEqual(result['week']['days'][-1]['distanceMeters'], 1000)
    self.assertEqual(result['models'][0]['name'], 'Test Model')
    self.assertEqual(result['records'][0]['id'], 'longestDrive')
    ignored = owner.ignore('abc', True, lambda: True)
    self.assertEqual(ignored['totals']['drives'], 0)
    self.assertTrue(ignored['recentDrives'][0]['ignored'])
    self.assertIsNone(ignored['lastDrive'])
    second = self.owner()
    loaded = second.snapshot()
    self.assertEqual(loaded['totals']['drives'], 0)
    self.assertTrue(loaded['recentDrives'][0]['ignored'])
    with self.assertRaises(PermissionError):
      second.ignore('abc', False, lambda: False)
    self.assertEqual(second.ignore('abc', False, lambda: True)['totals']['drives'], 1)

  def test_week_uses_viewer_calendar_at_midnight_and_dst(self):
    owner = self.owner(permitted=lambda: False)
    self.now[0] = datetime(2026, 9, 28, 2, tzinfo=UTC).timestamp()
    record = analyzed(self.root, route('abc'), lambda: True)
    record.update(startTime=self.now[0] - 60, ignored=False)
    owner._records = {'abc': record}
    local = owner.snapshot('America/Chicago')
    self.assertEqual(local['week']['days'][-1]['date'], '2026-09-27')
    self.assertEqual(local['week']['days'][-1]['distanceMeters'], 1000)
    self.assertEqual(owner.snapshot()['week']['days'][0]['date'], '2026-09-28')
    for stamp in ('2026-11-01T06:30:00+00:00', '2026-11-01T07:30:00+00:00'):
      self.now[0] = datetime.fromisoformat(stamp).timestamp()
      record['startTime'] = self.now[0]
      self.assertEqual(owner.snapshot('America/Chicago')['week']['days'][-1]['distanceMeters'], 1000)
    for zone in ('../UTC', '/etc/passwd', 'Unknown/Zone', '', 'x' * 129):
      with self.subTest(zone=zone), self.assertRaises(ValueError):
        owner.snapshot(zone)

  def test_metric_preference_changes_presentation_flag_only(self):
    owner = self.owner(metric=lambda: False)
    owner._start()
    owner.snapshot()
    output = self.completed(owner)
    self.assertFalse(output['isMetric'])
    self.assertEqual(output['totals']['distanceMeters'], 1000)

  def test_personal_records_share_the_viewer_calendar_with_week_graph(self):
    owner = self.owner(permitted=lambda: False)
    self.now[0] = datetime(2026, 9, 28, 6, tzinfo=UTC).timestamp()
    for identifier, stamp, distance, engaged in (
      ('sunday', '2026-09-28T04:30:00+00:00', 1000, 60),
      ('monday', '2026-09-28T05:30:00+00:00', 2000, 0),
    ):
      record = analyzed(self.root, route(identifier), lambda: True)
      start = datetime.fromisoformat(stamp).timestamp()
      record.update(startTime=start, endTime=start + 60, ignored=False,
                    distanceMeters=distance, engagedSeconds=engaged, engagedPercent=100 * engaged / 60)
      owner._records[identifier] = record
    saved = copy.deepcopy(owner._records)

    local = owner.snapshot('America/Chicago')
    records = {record['id']: record['value'] for record in local['records']}
    self.assertEqual(local['week']['distanceMeters'], 2000)
    self.assertEqual(local['week']['days'][0]['distanceMeters'], 2000)
    self.assertEqual(records['bestWeek'], 2000)
    self.assertEqual(records['mostEngagedDay'], 100)
    self.assertEqual(records['highestStreak'], 2)
    self.assertEqual(local['coverage']['timezone'], local['week']['timezone'])

    utc = owner.snapshot()
    records = {record['id']: record['value'] for record in utc['records']}
    self.assertEqual(utc['week']['distanceMeters'], 3000)
    self.assertEqual(records['bestWeek'], 3000)
    self.assertEqual(records['mostEngagedDay'], 50)
    self.assertEqual(records['highestStreak'], 1)
    self.assertEqual(utc['coverage']['timezone'], 'UTC')
    self.assertEqual(local['totals'], utc['totals'])
    self.assertEqual(owner._records, saved)
    self.assertFalse(self.store.exists(), 'viewer timezone must not rewrite stored driving history')

  def test_personal_record_streak_counts_calendar_days_across_dst(self):
    owner = self.owner(permitted=lambda: False)
    for stamps in (
      ('2026-03-07T18:00:00+00:00', '2026-03-08T17:00:00+00:00', '2026-03-09T17:00:00+00:00'),
      ('2026-10-31T17:00:00+00:00', '2026-11-01T18:00:00+00:00', '2026-11-02T18:00:00+00:00'),
    ):
      with self.subTest(stamps=stamps):
        owner._records.clear()
        for index, stamp in enumerate(stamps):
          record = analyzed(self.root, route(str(index)), lambda: True)
          start = datetime.fromisoformat(stamp).timestamp()
          record.update(startTime=start, endTime=start + 60, ignored=False)
          owner._records[str(index)] = record
        records = {record['id']: record['value'] for record in owner.snapshot('America/Chicago')['records']}
        self.assertEqual(records['highestStreak'], 3)

  def test_repeated_dst_hour_is_one_record_day(self):
    owner = self.owner(permitted=lambda: False)
    self.now[0] = datetime(2026, 11, 1, 8, tzinfo=UTC).timestamp()
    for index, stamp in enumerate(('2026-11-01T06:30:00+00:00', '2026-11-01T07:30:00+00:00')):
      record = analyzed(self.root, route(str(index)), lambda: True)
      start = datetime.fromisoformat(stamp).timestamp()
      record.update(startTime=start, endTime=start + 60, ignored=False, engagedSeconds=60 * index,
                    engagedPercent=100 * index)
      owner._records[str(index)] = record
    local = owner.snapshot('America/Chicago')
    records = {record['id']: record['value'] for record in local['records']}
    self.assertEqual(local['week']['days'][-1]['distanceMeters'], 2000)
    self.assertEqual(records['highestStreak'], 1)
    self.assertEqual(records['mostEngagedDay'], 50)

  def test_default_store_inventory_uses_bounded_long_route_limit(self):
    owner = DriveStatsOwner(root=self.root, store=self.store, analyzer=analyzed, permitted=lambda: False)
    self.addCleanup(owner.close)
    self.assertEqual(owner.history.max_segments_per_route, 512)
    self.assertEqual(owner.history.max_segments, 2048)

  def test_cleaned_logs_retain_complete_records_and_changed_logs_invalidate(self):
    segment = self.root / 'abc--0'
    segment.mkdir()
    log = segment / 'rlog.zst'
    log.write_bytes(b'first')
    owner = self.owner()
    owner._start()
    owner.snapshot()
    self.completed(owner)
    self.history.routes = []
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.assertEqual(self.completed(owner, scans=2)['totals']['drives'], 1)
    self.history.routes = [route('abc')]
    log.write_bytes(b'changed')
    analyzing = threading.Event()
    release = threading.Event()
    def delayed(root, selected, permitted):
      analyzing.set()
      release.wait(2)
      return analyzed(root, selected, permitted)
    owner.analyzer = delayed
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.assertTrue(analyzing.wait(2))
    self.assertEqual(owner.snapshot()['totals']['drives'], 0)
    release.set()
    self.assertEqual(self.completed(owner, scans=3)['totals']['drives'], 1)

  def test_incomplete_and_failed_routes_never_contribute(self):
    def incomplete(root, selected, permitted):
      result = analyzed(root, selected, permitted)
      result.update(complete=False, reason='gap', distanceMeters=None, engagedSeconds=None)
      return result
    owner = self.owner(analyzer=incomplete)
    owner._start()
    owner.snapshot()
    result = self.completed(owner)
    self.assertEqual(result['totals']['drives'], 0)
    self.assertEqual(result['coverage']['incompleteRoutes'], 1)
    self.assertFalse(result['recentDrives'][0]['complete'])
    self.assertEqual(result['records'], [])
    self.assertTrue(result['coverage']['partialHistory'])

  def test_log_cleanup_keeps_completed_drive_and_qlog_change_retries(self):
    segment = self.root / 'abc--0'
    segment.mkdir()
    rlog = segment / 'rlog.zst'
    qlog = segment / 'qlog.zst'
    rlog.write_bytes(b'rlog')
    qlog.write_bytes(b'qlog')
    calls = []
    def count(root, selected, permitted):
      calls.append(1)
      return analyzed(root, selected, permitted)
    owner = self.owner(analyzer=count)
    owner._start()
    owner.snapshot()
    self.completed(owner)
    rlog.unlink()
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.assertEqual(self.completed(owner, scans=2)['totals']['drives'], 1)
    self.assertEqual(len(calls), 1)
    qlog.write_bytes(b'changed qlog')
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.assertEqual(self.completed(owner, scans=3)['totals']['drives'], 1)
    self.assertEqual(len(calls), 2)

  def test_attention_records_use_independent_observations(self):
    def attention(root, selected, permitted):
      result = analyzed(root, selected, permitted)
      result.update(distractedMoments=2, unresponsiveMoments=0)
      return result
    owner = self.owner(analyzer=attention)
    owner._start()
    owner.snapshot()
    records = {record['id']: record for record in self.completed(owner)['records']}
    self.assertEqual(records['cleanDriveStreak']['value'], 1)
    self.assertIsNone(records['longestUndistractedDrive']['value'])

  def test_last_drive_can_be_incomplete_without_inflating_totals(self):
    self.history.routes = [route('old'), route('new')]
    def mixed(root, selected, permitted):
      result = analyzed(root, selected, permitted)
      if selected['routeId'] == 'old':
        result.update(startTime=NOW - 3600, endTime=NOW - 3540)
      else:
        result.update(complete=False, reason='gap', distanceMeters=None, engagedSeconds=None)
      return result
    owner = self.owner(analyzer=mixed)
    owner._start()
    owner.snapshot()
    output = self.completed(owner)
    self.assertEqual(output['totals']['drives'], 1)
    self.assertEqual(output['lastDrive']['routeId'], 'new')
    self.assertFalse(output['lastDrive']['complete'])

  def test_parked_check_is_throttled_and_close_is_immediate(self):
    ticks = [100.0]
    calls = []
    def parked():
      calls.append(1)
      return True
    owner = self.owner(permitted=parked, monotonic=lambda: ticks[0])
    self.assertTrue(owner._allowed())
    for _ in range(100):
      self.assertTrue(owner._allowed())
    self.assertEqual(len(calls), 1)
    ticks[0] += .11
    self.assertTrue(owner._allowed())
    self.assertEqual(len(calls), 2)
    owner.close()
    self.assertFalse(owner._allowed())
    self.assertEqual(len(calls), 2)

  def test_backward_wall_clock_does_not_freeze_scan(self):
    ticks = [100.0]
    owner = self.owner(monotonic=lambda: ticks[0])
    owner._start()
    owner.snapshot()
    self.completed(owner)
    self.now[0] -= 86400
    ticks[0] += 61
    owner._start()
    owner.snapshot()
    self.completed(owner, scans=2)

  def test_failed_cache_write_rolls_back_ignore(self):
    owner = self.owner()
    owner._start()
    owner.snapshot()
    self.completed(owner)
    with mock.patch.object(owner, '_save', side_effect=OSError('full disk')):
      with self.assertRaises(OSError):
        owner.ignore('abc', True, lambda: True)
    self.assertFalse(owner.snapshot()['recentDrives'][0]['ignored'])

  def test_background_work_is_bounded_and_resumes_other_routes(self):
    self.history.routes = [route(f'route-{index}') for index in range(5)]
    owner = self.owner()
    owner._start()
    owner.snapshot()
    first = self.completed(owner)
    self.assertEqual(first['totals']['drives'], 2)
    self.assertEqual(first['analysis']['pending'], 3)
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.assertEqual(self.completed(owner, scans=2)['totals']['drives'], 4)
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.assertEqual(self.completed(owner, scans=3)['totals']['drives'], 5)

  def test_incomplete_recording_waits_for_changed_source_without_repeated_analysis(self):
    for reason in drive_stats.SOURCE_LIMITATIONS:
      with self.subTest(reason=reason):
        analyzer = mock.Mock(side_effect=lambda *args, reason=reason, **kwargs: {
          **analyzed(*args, **kwargs), 'complete': False, 'reason': reason})
        owner = self.owner(analyzer=analyzer)
        owner._start()
        owner.snapshot()
        result = self.completed(owner)
        self.assertTrue(result['analysis']['failures'][0]['retryStopped'])
        self.assertEqual(result['totals']['drives'], 0)
        self.now[0] += 3600
        owner._start()
        owner.snapshot()
        self.completed(owner, scans=self.history.scans)
        self.assertEqual(analyzer.call_count, 1)
        self.history.routes = [route('abc', number=self.history.scans)]
        owner.analyzer = mock.Mock(wraps=analyzed)
        self.now[0] += 61
        owner._start()
        owner.snapshot()
        recovered = self.completed(owner, scans=self.history.scans)
        owner.analyzer.assert_called_once()
        self.assertEqual(recovered['analysis']['failed'], 0)
        self.assertEqual(recovered['totals']['drives'], 1)
        owner.close()
        self.store.unlink()

  def test_incomplete_source_waits_for_retry_or_revision(self):
    calls = []
    def incomplete(root, selected, permitted):
      calls.append(1)
      result = analyzed(root, selected, permitted)
      result.update(complete=False, reason='gap')
      return result
    owner = self.owner(analyzer=incomplete)
    owner._start()
    owner.snapshot()
    result = self.completed(owner)
    self.assertEqual(result['analysis']['pending'], 0)
    self.assertEqual(result['analysis']['state'], 'retrying')
    self.assertFalse(result['analysis']['running'])
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.completed(owner, scans=2)
    self.assertEqual(len(calls), 1)
    self.now[0] += 300
    owner.analyzer = analyzed
    owner._start()
    owner.snapshot()
    self.assertEqual(self.completed(owner, scans=3)['totals']['drives'], 1)

  def test_exhausted_failure_recovers_once_after_analyzer_upgrade(self):
    owner = self.owner(analyzer=lambda *args, **kwargs: {**analyzed(*args, **kwargs),
                                                       'complete': False, 'reason': 'End of route was not recorded'})
    owner._start()
    owner.snapshot()
    self.completed(owner)
    owner._failures['abc']['attempts'] = drive_stats.MAX_RETRY_ATTEMPTS
    owner._save()
    self.now[0] += 61
    owner = self.owner(analyzer=mock.Mock(wraps=analyzed))
    owner._start()
    owner.snapshot()
    paused = self.completed(owner)
    owner.analyzer.assert_not_called()
    self.assertEqual(paused['analysis']['state'], 'failed')
    self.assertTrue(paused['analysis']['failures'][0]['retryStopped'])
    self.assertEqual(paused['analysis']['failures'][0]['reason'], 'End of route was not recorded')
    self.assertEqual(paused['totals']['drives'], 0)
    self.now[0] += 61
    with mock.patch.object(drive_stats, 'ANALYSIS_VERSION', drive_stats.ANALYSIS_VERSION + 1):
      owner._start()
      owner.snapshot()
      recovered = self.completed(owner, scans=3)
    owner.analyzer.assert_called_once()
    self.assertEqual(recovered['analysis']['failed'], 0)
    self.assertEqual(recovered['totals']['drives'], 1)

  def test_legacy_failure_gets_fresh_bounded_attempts(self):
    owner = self.owner(analyzer=lambda *args, **kwargs: {**analyzed(*args, **kwargs),
                                                       'complete': False, 'reason': 'gap'})
    owner._start()
    owner.snapshot()
    self.completed(owner)
    failure = owner._failures['abc']
    failure.pop('analysisVersion')
    failure['attempts'] = drive_stats.MAX_RETRY_ATTEMPTS
    failure['firstAt'] = self.now[0] - drive_stats.MAX_RETRY_AGE_SECONDS
    owner._save()
    owner = self.owner(analyzer=owner.analyzer)
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    result = self.completed(owner, scans=2)
    self.assertEqual(result['analysis']['state'], 'retrying')
    self.assertEqual(owner._failures['abc']['attempts'], 1)
    self.assertEqual(owner._failures['abc']['analysisVersion'], drive_stats.ANALYSIS_VERSION)
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.completed(owner, scans=3)
    self.assertEqual(owner._failures['abc']['attempts'], 1)

  def test_failed_analysis_leaves_queue_and_retries_after_cooldown(self):
    owner = self.owner(analyzer=mock.Mock(side_effect=TimeoutError('bounded route timeout')))
    owner._start()
    owner.snapshot()
    result = self.completed(owner)
    self.assertEqual(result['analysis']['pending'], 0)
    self.assertEqual(result['analysis']['failed'], 1)
    self.assertEqual(result['analysis']['state'], 'retrying')
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.completed(owner, scans=2)
    self.assertEqual(owner.analyzer.call_count, 1)
    owner.analyzer = analyzed
    self.now[0] += 300
    owner._start()
    owner.snapshot()
    result = self.completed(owner, scans=3)
    self.assertEqual(result['analysis']['state'], 'ready')
    self.assertEqual(result['analysis']['failed'], 0)

  def test_analysis_upgrade_refreshes_retained_logs_but_preserves_deleted_history(self):
    segment = self.root / 'abc--0'
    segment.mkdir()
    log = segment / 'rlog.zst'
    log.write_bytes(b'original source')
    owner = self.owner()
    owner._start()
    owner.snapshot()
    self.completed(owner)
    owner._records['abc'].update(analysisVersion=1, model=None)
    owner._save()
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    result = self.completed(owner, scans=2)
    self.assertEqual(result['lastDrive']['model'], 'Test Model')
    self.assertEqual(result['lastDrive']['analysisVersion'], drive_stats.ANALYSIS_VERSION)
    owner._records['abc']['analysisVersion'] = 1
    log.unlink()
    self.now[0] += 61
    owner.analyzer = mock.Mock(side_effect=AssertionError('deleted source must retain prior measurements'))
    owner._start()
    owner.snapshot()
    result = self.completed(owner, scans=3)
    self.assertEqual(result['totals']['distanceMeters'], 1000)
    owner.analyzer.assert_not_called()

  def test_metadata_upgrade_failure_preserves_previous_complete_totals(self):
    owner = self.owner()
    owner._start()
    owner.snapshot()
    self.completed(owner)
    owner._records['abc']['analysisVersion'] = 1
    owner.analyzer = lambda *args, **kwargs: {**analyzed(*args, **kwargs), 'complete': False, 'reason': 'timeout'}
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    result = self.completed(owner, scans=2)
    self.assertEqual(result['totals']['distanceMeters'], 1000)
    self.assertEqual(result['analysis']['state'], 'retrying')

  def test_offroad_wait_is_distinct_from_running_analysis(self):
    result = self.owner(permitted=lambda: False).snapshot()
    self.assertEqual(result['analysis']['state'], 'waitingParked')
    self.assertFalse(result['analysis']['running'])

  def test_byte_limit_evicts_oldest_with_partial_coverage(self):
    owner = self.owner()
    with owner._lock, mock.patch('openpilot.starpilot.galaxy.drive_stats.MAX_CACHE_BYTES', 2200):
      for index in range(10):
        record = analyzed(None, {'routeId': f'route-{index}'}, lambda: True)
        record.update(ignored=False, sourceSignature='x', sourceManifest={}, model='x' * 120)
        owner._records[record['routeId']] = record
      owner._bound()
      owner._save()
      self.assertLess(len(owner._records), 10)
      self.assertTrue(owner.snapshot()['coverage']['truncated'])

  def test_corrupt_cache_not_replaced_until_valid_scan(self):
    self.store.parent.mkdir(parents=True)
    self.store.write_text('broken json')
    permitted = [False]
    owner = self.owner(permitted=lambda: permitted[0])
    self.assertTrue(owner.snapshot()['coverage']['cacheCorrupt'])
    self.assertEqual(self.store.read_text(), 'broken json')
    permitted[0] = True
    self.now[0] += .11
    owner._start()
    owner.snapshot()
    result = self.completed(owner)
    self.assertFalse(result['coverage']['cacheCorrupt'])
    self.assertEqual(json.loads(self.store.read_text())['schemaVersion'], 1)

  def test_oversized_number_in_cache_is_treated_as_corrupt(self):
    self.store.parent.mkdir(parents=True)
    record = analyzed(None, {'routeId': 'abc'}, lambda: True)
    record.update(ignored=False, sourceSignature='x', sourceManifest={}, distanceMeters=10**400)
    self.store.write_text(json.dumps({'schemaVersion': 1, 'routes': {'abc': record}}))
    owner = self.owner(permitted=lambda: False)
    result = owner.snapshot()
    self.assertTrue(result['coverage']['cacheCorrupt'])
    self.assertEqual(result['totals']['drives'], 0)
    self.assertIn('abc', self.store.read_text())

  def test_changed_source_during_analysis_cannot_commit_stale_result(self):
    segment = self.root / 'abc--0'
    segment.mkdir()
    log = segment / 'qlog.zst'
    log.write_bytes(b'initial')
    def changes_log(root, selected, permitted):
      log.write_bytes(b'replaced while decoding')
      return analyzed(root, selected, permitted)
    owner = self.owner(analyzer=changes_log)
    owner._start()
    owner.snapshot()
    first = self.completed(owner)
    self.assertEqual(first['totals']['drives'], 0)
    self.assertEqual(first['analysis']['failed'], 1)
    owner.analyzer = analyzed
    self.now[0] += 61
    owner._start()
    owner.snapshot()
    self.assertEqual(self.completed(owner, scans=2)['totals']['drives'], 1)

  def test_cancel_prevents_late_commit_and_storage_is_bounded(self):
    entered = threading.Event()
    release = threading.Event()
    def blocked(root, selected, permitted):
      entered.set()
      release.wait(2)
      return analyzed(root, selected, permitted)
    owner = self.owner(analyzer=blocked)
    owner._start()
    owner.snapshot()
    self.assertTrue(entered.wait(2))
    owner.close()
    release.set()
    self.assertFalse(self.store.exists())
    bounded = self.owner()
    with bounded._lock:
      for index in range(MAX_CACHED_ROUTES + 1):
        record = analyzed(None, {'routeId': f'route-{index}'}, lambda: True)
        record.update(ignored=False, sourceSignature='x')
        bounded._records[record['routeId']] = record
      bounded._bound()
      self.assertEqual(len(bounded._records), MAX_CACHED_ROUTES)
      self.assertTrue(bounded._truncated)


if __name__ == '__main__':
  unittest.main()
