"""Declared corrupt-input fixtures; no road improvement or accepted tune is mocked."""

import bz2
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import zstandard as zstd

from opendbc.car import structs
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.starpilot.flm.offroad_route import RouteInputError, latest_route, route_inventory, walk_completed_route
from openpilot.starpilot.flm.offroad_store import KEY
from openpilot.starpilot.flm.offroad_worker import OffroadWorker
from openpilot.starpilot.flm.tests.test_offroad_backend import scope, values
from openpilot.starpilot.flm.tests import test_offroad_coordinate_policy as fixtures
from openpilot.starpilot.galaxy.drive_history import DriveHistory


def recording(root, counter, *, corrupt=False, closed=True, segment=0):
  """A real compressed Cereal recording with explicitly inactive steering."""
  name = f'{counter:08x}--aaaaaaaaaa'
  folder = root / (name + '--' + str(segment))
  folder.mkdir(parents=True)
  cp = structs.CarParams.new_message(carFingerprint='declared-fault-isolation', steerControlType='torque')
  cp.lateralTuning.init('torque')
  cp.carVin = '1HGCM82633A004352'
  messages = [log.Event.new_message(logMonoTime=1_000_000_000, valid=True, carParams=cp).to_bytes()]
  for index in range(50):
    stamp = 1_000_000_000 + index * 10_000_000
    event = log.Event.new_message(logMonoTime=stamp, valid=True)
    event.init('carState').vEgo = 20
    messages.append(event.to_bytes())
    event = log.Event.new_message(logMonoTime=stamp, valid=True)
    event.init('carControl').latActive = False
    messages.append(event.to_bytes())
  event = log.Event.new_message(logMonoTime=1_500_000_000, valid=True)
  event.init('clocks').wallTimeNanos = 1_800_000_000_000_000_000 + counter * 1_000_000_000
  messages.append(event.to_bytes())
  if closed:
    event = log.Event.new_message(logMonoTime=1_500_000_000, valid=True)
    event.init('sentinel').type = 'endOfRoute'
    messages.append(event.to_bytes())
  raw = bz2.compress(b''.join(messages))
  bad = bytearray(raw)
  bad[-5] ^= 1
  path = folder / 'rlog.bz2'
  path.write_bytes(bytes(bad) if corrupt else raw)
  return name, path, raw


def zstd_recording(root, counter, *, fault=None, **kwargs):
  name, original, compressed = recording(root, counter, **kwargs)
  data = bz2.decompress(compressed)
  compressor = zstd.ZstdCompressor(write_content_size=False)
  complete = compressor.compress(data)
  if fault == 'unfinished':
    stream = compressor.compressobj()
    compressed = stream.compress(data) + stream.flush(zstd.COMPRESSOBJ_FLUSH_BLOCK)
  elif fault == 'trailing':
    compressed = complete + b'declared trailing bytes'
  else:
    compressed = complete
  path = original.with_name('rlog.zst')
  path.write_bytes(compressed)
  original.unlink()
  return name, path, complete


class TestRouteFaultIsolation(unittest.TestCase):
  def test_many_short_drives_do_not_invalidate_the_flm_inventory(self):
    # Inventory-only fixture; these bytes are never admitted as learning evidence.
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      for counter in range(673):
        for segment in range(3 if counter < 39 else 2):
          path = root / f'{counter:08x}--aaaaaaaaaa--{segment}'
          path.mkdir()
          (path / 'rlog.zst').write_bytes(b'inventory only')
      self.assertTrue(DriveHistory(root, max_segments=2048).snapshot()['scanIncomplete'])
      actual = route_inventory(root)
      self.assertFalse(actual['scanIncomplete'])
      self.assertEqual(len(actual['routes']), 673)
      self.assertEqual(sum(row['segmentCount'] for row in actual['routes']), 1385)

  def test_total_inventory_matches_bounded_root_scan_without_raising_per_drive_limit(self):
    # Metadata-only fixtures: file presence never establishes valid log data.
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      for counter, count in ((1, 180), (2, 180), (3, 152), (4, 1)):
        for segment in range(count):
          path = root / f'{counter:08x}--aaaaaaaaaa--{segment}'
          path.mkdir()
          (path / 'rlog.zst').write_bytes(b'not decoded by inventory')
      self.assertTrue(DriveHistory(root, max_segments_per_route=180).snapshot(isolate_route_errors=True)['scanIncomplete'])
      actual = route_inventory(root)
      self.assertFalse(actual['scanIncomplete'])
      self.assertEqual([row['segmentCount'] for row in actual['routes']], [180, 180, 152, 1])
      path = root / '00000001--aaaaaaaaaa--180'
      path.mkdir()
      (path / 'rlog.zst').write_bytes(b'not decoded by inventory')
      actual = route_inventory(root)
      self.assertEqual([row['routeId'] for row in actual['routes']], [f'{n:08x}--aaaaaaaaaa' for n in (2, 3, 4)])
      self.assertEqual(actual['routeErrors'][0]['routeId'], '00000001--aaaaaaaaaa')

  def setup_worker(self, directory, latest):
    root = Path(directory)
    params = Params(str(root / 'params'))
    params.put('CurrentRoute', latest, block=True)
    params.put('RouteCount', int(latest.split('--')[0], 16) + 1, block=True)
    worker = OffroadWorker(params, root / 'logs', root / 'cache', permitted=lambda: True, scope_for=lambda *args: scope())
    return params, worker

  def test_interrupted_zstd_tail_does_not_stop_daemon_or_admit_its_valid_prefix(self):
    from openpilot.starpilot.flm import offroad_daemon as daemon

    for fault in ('unfinished', 'trailing'):
      with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / 'logs'
        bad, _, _ = zstd_recording(root, 18, closed=False)
        _, damaged, complete = zstd_recording(root, 18, segment=1, fault=fault)
        good, _, _ = zstd_recording(root, 17)
        params, worker = self.setup_worker(directory, bad)
        report = daemon.StatusReport(path=Path(directory) / 'status.json')
        with patch.object(daemon.traceback, 'print_exc') as trace, patch.object(daemon.cloudlog, 'warning') as warning:
          self.assertTrue(daemon.run_parked_analysis(worker, report))
          self.assertTrue(report.current['route_skipped'])
          self.assertNotIn('completed_route', report.current)
          self.assertNotIn('recorded_tracking', report.current)
          self.assertEqual(report.current['skipped_routes'][0][0], bad)
          if fault == 'unfinished':
            self.assertIn('incomplete or trailing zstd frame', report.current['reason'])
          warning.assert_called_once()
          self.assertFalse(daemon.run_parked_analysis(worker, report))
          self.assertEqual(report.current['completed_route'], good)
          self.assertEqual(report.current['analysis_counts']['recorded_lateral_request_rows'], 50)
          trace.assert_not_called()
        self.assertIsNone(params.get(KEY))
        restarted = OffroadWorker(params, root, worker.cache, permitted=lambda: True, scope_for=lambda *args: scope())
        with patch('openpilot.starpilot.flm.offroad_route.decode_full_rlog_segment', side_effect=AssertionError('Do not retry damaged input')):
          self.assertFalse(daemon.run_parked_analysis(restarted, report))
        self.assertEqual(report.current['completed_route'], good)
        damaged.write_bytes(complete)
        self.assertFalse(daemon.run_parked_analysis(restarted, report))
        self.assertEqual(report.current['completed_route'], bad)
        self.assertEqual(report.current['analysis_counts']['recorded_lateral_request_rows'], 100)
        self.assertIsNone(params.get(KEY))

  def test_unexpected_analysis_failure_keeps_diagnostics_and_normal_retry_delay(self):
    from openpilot.starpilot.flm import offroad_daemon as daemon

    def failed_run():
      raise RuntimeError('declared unexpected analysis failure')

    report = daemon.StatusReport()
    worker = SimpleNamespace(run_once=failed_run, record_failure=lambda error: None)
    with patch.object(daemon.traceback, 'print_exc') as trace, patch.object(daemon.cloudlog, 'warning') as warning:
      self.assertFalse(daemon.run_parked_analysis(worker, report))
      trace.assert_called_once()
      warning.assert_not_called()
    self.assertEqual(report.current['status'], 'unavailable')
    self.assertIn('declared unexpected analysis failure', report.current['reason'])

  def test_failed_skip_checkpoint_does_not_trigger_immediate_retry(self):
    from openpilot.starpilot.flm import offroad_daemon as daemon

    def failed_run():
      raise RouteInputError('00000012--aaaaaaaaaa', 'declared incomplete zstd recording')

    def failed_checkpoint(error):
      raise OSError('declared unavailable checkpoint storage')

    report = daemon.StatusReport()
    worker = SimpleNamespace(run_once=failed_run, record_failure=failed_checkpoint)
    with patch.object(daemon.traceback, 'print_exc') as trace, patch.object(daemon.cloudlog, 'warning') as warning:
      self.assertFalse(daemon.run_parked_analysis(worker, report))
      trace.assert_called_once()
      warning.assert_called_once()
    self.assertIn('Could not record route failure', report.current['reason'])

  def test_corrupt_newest_does_not_block_real_older_analysis_or_repeat_each_poll(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      bad, _, _ = recording(root, 18, corrupt=True)
      good, _, _ = recording(root, 17)
      params, worker = self.setup_worker(directory, bad)
      with self.assertRaises(RouteInputError) as caught:
        worker.run_once()
      failed = worker.record_failure(caught.exception)
      self.assertTrue(failed['route_skipped'])
      self.assertFalse(failed['configuration_changed'])
      self.assertNotIn('assessment_sha256', failed)
      self.assertNotIn('recorded_tracking', failed)
      result = worker.run_once()
      self.assertEqual(result['completed_route'], good)
      self.assertEqual(result['analysis_counts']['recorded_lateral_request_rows'], 50)
      self.assertFalse(result['configuration_changed'])
      self.assertIsNone(params.get(KEY), 'Skipping bad evidence cannot create a learned tune')
      restarted = OffroadWorker(params, root, worker.cache, permitted=lambda: True, scope_for=lambda *args: scope())
      with patch('openpilot.starpilot.flm.offroad_route.decode_full_rlog_segment', side_effect=AssertionError('Immutable failure must stay skipped')):
        report = restarted.run_once()
      self.assertEqual(report['completed_route'], good)
      self.assertFalse(report['source_is_latest'])
      self.assertEqual(report['skipped_routes'][0][0], bad)

  def test_same_size_repair_with_preserved_mtime_is_retried(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      name, path, valid = recording(root, 18, corrupt=True)
      _, worker = self.setup_worker(directory, name)
      before = path.stat()
      with self.assertRaises(RouteInputError) as caught:
        worker.run_once()
      failed = worker.record_failure(caught.exception)
      self.assertTrue(failed['route_skipped'])
      path.write_bytes(valid)
      os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
      self.assertEqual(path.stat().st_size, before.st_size)
      self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
      result = worker.run_once()
      self.assertEqual(result['completed_route'], name)
      self.assertFalse(result['configuration_changed'])
      self.assertEqual(result['analysis_counts']['recorded_lateral_request_rows'], 50)

  def test_locked_or_unreadable_route_is_omitted_whole_without_changing_default_inventory(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      bad, path, _ = recording(root, 18)
      good, _, _ = recording(root, 17)
      (path.parent / 'rlog.lock').touch()
      extra = root / (bad + '--1')
      extra.mkdir()
      (extra / 'rlog.bz2').write_bytes(b'declared opaque inventory data')
      strict = DriveHistory(root).snapshot()
      self.assertNotIn('routeErrors', strict, 'Interactive inventory retains its existing contract')
      isolated = route_inventory(root)
      self.assertFalse(isolated['scanIncomplete'])
      self.assertEqual([r['routeId'] for r in isolated['routes']], [good])
      self.assertEqual(isolated['routeErrors'][0]['routeId'], bad)
      (path.parent / 'rlog.lock').unlink()
      original = __import__('openpilot.starpilot.galaxy.drive_history', fromlist=['_segment_files'])._segment_files

      def inaccessible(fd, name):
        return (None, True, None, None) if name == path.parent.name else original(fd, name)

      with patch('openpilot.starpilot.galaxy.drive_history._segment_files', side_effect=inaccessible):
        self.assertTrue(DriveHistory(root).snapshot()['scanIncomplete'])
        self.assertEqual([r['routeId'] for r in route_inventory(root)['routes']], [good])

  def test_missing_latest_recording_still_allows_older_valid_route(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      good, _, _ = recording(root, 17)
      params, worker = self.setup_worker(directory, '00000012--aaaaaaaaaa')
      params.remove('CurrentRoute')
      anchor = latest_route(params, root, permitted=lambda: True, inventory_only=True)
      self.assertIsNone(anchor['routeId'], 'A missing route hash must never be invented')
      self.assertEqual(anchor['loggerCounter'], 18)
      with self.assertRaises(RouteInputError) as caught:
        worker.run_once()
      worker.record_failure(caught.exception)
      self.assertEqual(worker.run_once()['completed_route'], good)
      self.assertIsNone(params.get(KEY))

  def test_global_scan_limit_and_cancellation_are_not_route_corruption(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      name, _, _ = recording(root, 18)
      _, worker = self.setup_worker(directory, name)
      for number in range(513):
        (root / f'ignored-{number}').touch()
      with patch('openpilot.starpilot.galaxy.drive_history.MAX_ROOT_ENTRIES', 512):
        with self.assertRaisesRegex(ValueError, 'inventory is incomplete') as caught:
          worker.run_once()
        self.assertIsNone(worker.record_failure(caught.exception))
      worker.permitted = lambda: False
      with self.assertRaisesRegex(ValueError, 'not permitted') as caught:
        worker.run_once()
      self.assertIsNone(worker.record_failure(caught.exception))
      self.assertEqual(worker.cache.checkpoints(), [])

  def test_valid_prefix_of_corrupt_segment_never_reaches_consumer(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      name, _, _ = recording(root, 18, corrupt=True)
      route = next(r for r in route_inventory(root)['routes'] if r['routeId'] == name)
      consumed = []
      with self.assertRaises(RouteInputError):
        walk_completed_route(root, route, permitted=lambda: True, consume=lambda *args: consumed.append(args))
      self.assertEqual(consumed, [], 'A corrupt segment cannot contribute even a well-formed prefix')

  def test_unclosed_recording_never_becomes_a_completed_assessment(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      bad, _, _ = recording(root, 18, closed=False)
      good, _, _ = recording(root, 17)
      params, worker = self.setup_worker(directory, bad)
      with self.assertRaises(RouteInputError) as caught:
        worker.run_once()
      failed = worker.record_failure(caught.exception)
      self.assertTrue(failed['route_skipped'])
      self.assertNotIn('assessment_sha256', failed)
      self.assertEqual(worker.run_once()['completed_route'], good)
      self.assertIsNone(params.get(KEY))

  def test_transient_decoder_cancellation_is_not_quarantined_after_permission_recovers(self):
    from openpilot.starpilot.flm.log_decode import LogDecodeCancelled

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      name, _, _ = recording(root, 18)
      _, worker = self.setup_worker(directory, name)
      with patch('openpilot.starpilot.flm.offroad_route.decode_full_rlog_segment', side_effect=LogDecodeCancelled('declared transient loss')):
        with self.assertRaises(LogDecodeCancelled) as caught:
          worker.run_once()
      self.assertIsNone(worker.record_failure(caught.exception))
      self.assertEqual(worker.cache.checkpoints(), [])
      self.assertEqual(worker.run_once()['completed_route'], name)

  def test_authority_callback_exception_is_not_mislabeled_as_corrupt_recording(self):
    from openpilot.starpilot.flm.offroad_route import RouteInputCancelled, route_reader

    @route_reader
    def declared_read(root, route, *, permitted):
      permitted()

    with self.assertRaises(RouteInputCancelled):
      declared_read('.', {'routeId': '00000012--aaaaaaaaaa'},
                    permitted=lambda: (_ for _ in ()).throw(OSError('declared authority read failure')))

  def test_bad_late_history_window_discards_its_entire_drive_but_not_other_drives(self):
    from openpilot.starpilot.flm.offroad_history import EvidenceBank, combine

    with tempfile.TemporaryDirectory() as directory:
      bank = EvidenceBank(Path(directory) / 'cache')
      one, inventory = fixtures.TestCrossDriveEvidence.inputs(1)
      bank.retain(one, inventory, 'same', permitted=lambda: True)
      two, inventory = fixtures.TestCrossDriveEvidence.inputs(2)
      history = bank.retain(two, inventory, 'same', permitted=lambda: True)
      history[0]['windows'][1]['points'][-1][6] += .1
      current, inventory = fixtures.TestCrossDriveEvidence.inputs(3)
      skipped = []
      with self.assertLogs('openpilot.starpilot.flm.offroad_history', level='WARNING'):
        pooled, sources, order = combine(current, inventory, history, on_skip=lambda *args: skipped.append(args))
      owners = {w.points[0].route for rows in pooled.windows.values() for w in rows}
      self.assertEqual(owners, {two.receipt.route_id, current.receipt.route_id})
      self.assertNotIn(one.receipt.route_id, sources)
      self.assertNotIn(one.receipt.route_id, order)
      self.assertEqual(skipped[0][0], one.receipt.route_id)
      before_ids = {w.identity for rows in two.windows.values() for w in rows}
      self.assertTrue(before_ids.issubset({w.identity for rows in pooled.windows.values() for w in rows}))

  def test_unreadable_derived_history_does_not_poison_valid_current_recording(self):
    from openpilot.starpilot.flm.offroad_history import EvidenceBank
    from openpilot.starpilot.flm.offroad_processor import analyze_latest

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      name, _, _ = recording(root, 18)
      route = route_inventory(root)['routes'][0]
      bank = EvidenceBank(Path(directory) / 'cache')
      path = bank.cache.directory / 'history.z'
      path.write_bytes(b'not a valid compressed history')
      result = analyze_latest(None, root, values(), permitted=lambda: True, selected_route=route,
                              evidence_bank=bank, history_identity=lambda cp: 'declared')
      self.assertEqual(result.receipt.route_id, name)
      self.assertEqual(result.counts['recorded_lateral_request_rows'], 50)
      self.assertFalse(result.groups)
      self.assertEqual(result.route_skips[0][0], 'Stored steering history')
      self.assertEqual(path.read_bytes(), b'not a valid compressed history')

  def test_malformed_stored_drive_shape_does_not_block_other_drives(self):
    from copy import deepcopy
    from openpilot.starpilot.flm.offroad_history import EvidenceBank, combine

    with tempfile.TemporaryDirectory() as directory:
      bank = EvidenceBank(Path(directory) / 'cache')
      one, inventory = fixtures.TestCrossDriveEvidence.inputs(1)
      bank.retain(one, inventory, 'same', permitted=lambda: True)
      two, inventory = fixtures.TestCrossDriveEvidence.inputs(2)
      history = bank.retain(two, inventory, 'same', permitted=lambda: True)
      current, inventory = fixtures.TestCrossDriveEvidence.inputs(3)
      for field in ('inventory', 'metadata', 'windows'):
        with self.subTest(field=field):
          broken = deepcopy(history)
          broken[0][field] = None
          skipped = []
          with self.assertLogs('openpilot.starpilot.flm.offroad_history', level='WARNING'):
            pooled, sources, _ = combine(current, inventory, broken, on_skip=lambda *args, target=skipped: target.append(args))
          owners = {w.points[0].route for rows in pooled.windows.values() for w in rows}
          self.assertEqual(owners, {two.receipt.route_id, current.receipt.route_id})
          self.assertNotIn(one.receipt.route_id, sources)
          self.assertEqual(skipped[0][0], one.receipt.route_id)

  def test_exclusion_changes_only_derived_history_and_cancellation_leaves_it_exact(self):
    from openpilot.starpilot.flm.offroad_history import EvidenceBank

    with tempfile.TemporaryDirectory() as directory:
      bank = EvidenceBank(Path(directory) / 'cache')
      one, inventory = fixtures.TestCrossDriveEvidence.inputs(1)
      bank.retain(one, inventory, 'same', permitted=lambda: True)
      two, inventory = fixtures.TestCrossDriveEvidence.inputs(2)
      bank.retain(two, inventory, 'same', permitted=lambda: True)
      path = bank.cache.directory / 'history.z'
      before = path.read_bytes()
      with self.assertRaises(ValueError):
        bank.exclude((one.receipt.route_id,), permitted=lambda: False)
      self.assertEqual(path.read_bytes(), before)
      bank.exclude((one.receipt.route_id,), permitted=lambda: True)
      self.assertEqual([r['receipt']['route_id'] for r in bank.read('same')], [two.receipt.route_id])

  def test_bad_historical_native_input_identifies_its_own_route(self):
    from openpilot.starpilot.flm.offroad_checks import _original_results

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      name, path, _ = recording(root, 17, corrupt=True)
      route = route_inventory(root)['routes'][0]
      body = path.read_bytes()
      source = SimpleNamespace(receipt=SimpleNamespace(route_id=name, source_sha256='a' * 64,
        segments=((path.parent.name, hashlib.sha256(body).hexdigest(), len(body)),)), source_inventory=route)
      assessment = SimpleNamespace(evidence_sources={name: source})
      spans = [{'identity': f'{i:064x}', 'route': name, 'segment': 0, 'start_ns': 1_000_000_000, 'end_ns': 1_500_000_000} for i in range(3)]
      with self.assertRaises(RouteInputError) as caught:
        _original_results(assessment, spans, tuple(s['identity'] for s in spans), values(), root, permitted=lambda: True)
      self.assertEqual(caught.exception.route_id, name)

  def test_failed_quality_check_is_not_reclassified_as_corrupt_input(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      name, _, _ = recording(root, 18)
      params, worker = self.setup_worker(directory, name)
      worker.assessor = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('declared quality refusal'))
      with self.assertRaisesRegex(ValueError, 'quality refusal') as caught:
        worker.run_once()
      result = worker.record_failure(caught.exception)
      self.assertFalse(result['route_skipped'])
      self.assertFalse(result['configuration_changed'])
      self.assertIsNone(params.get(KEY))

  def test_all_bad_routes_remain_unavailable_with_each_reason_reported(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      bad, _, _ = recording(root, 18, corrupt=True)
      recording(root, 17, corrupt=True)
      params, worker = self.setup_worker(directory, bad)
      for _ in range(2):
        with self.assertRaises(RouteInputError) as caught:
          worker.run_once()
        worker.record_failure(caught.exception)
      report = worker.run_once()
      self.assertEqual(report['status'], 'unavailable')
      self.assertEqual(len(report['skipped_routes']), 2)
      self.assertNotIn('completed_route', report)
      self.assertIsNone(params.get(KEY))

  def test_corrupt_prior_does_not_prevent_trying_next_independent_drive(self):
    from openpilot.starpilot.flm import offroad_prior_inputs as module
    from openpilot.starpilot.flm.offroad_analysis import cohort

    valid, _ = fixtures.TestCrossDriveEvidence.inputs(16)
    metadata = next(iter(valid.context_metadata.values()))
    assessment = SimpleNamespace(receipt=SimpleNamespace(route_id='00000012--aaaaaaaaaa', cp_sha256='a' * 64,
      completed_wall_ns=180_000_000_000, segments=(('00000012--aaaaaaaaaa--0', 'c' * 64, 1),)))
    routes = [{'routeId': f'{i:08x}--aaaaaaaaaa', 'segments': [{'files': {'rlog': True}}]} for i in (17, 16)]
    group = {'original_metadata': metadata}
    selected = {'cohort': cohort(next(iter(valid.windows.values()))[0])}
    with patch.object(module, 'route_inventory', return_value={'routes': routes}), patch.object(module, 'collect_route_inputs',
      side_effect=[RouteInputError(routes[0]['routeId'], 'declared corrupt source'), valid]) as collect:
      with self.assertLogs(module.__name__, level='WARNING'):
        result = module._prior_originals(assessment, group, selected, '.', permitted=lambda: True, stable=False)
    self.assertEqual(collect.call_count, 2)
    self.assertIsNone(result, 'Trying another input must not reduce the independent-window requirement')

  def test_corrupt_warmup_segment_is_rejected_before_freezing_a_prior(self):
    from openpilot.starpilot.flm import offroad_prior_inputs as module

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      recording(root, 18, closed=False)
      recording(root, 18, closed=False, corrupt=True, segment=1)
      recording(root, 18, segment=2)
      route = route_inventory(root)['routes'][0]
      with patch.object(module, 'selected_segments', return_value=(0, 2)), self.assertRaises(RouteInputError):
        module._selected_sources(root, route, permitted=lambda: True, consume=lambda *args: None,
                                 cp_sha256=None, validate_preroll=True)


if __name__ == '__main__':
  unittest.main()


class TestPartialDriveReview(unittest.TestCase):
  """Declared closed Cereal files; never a fabricated accepted road tune."""

  def segment(self, root, counter, number, *, ending='endOfSegment', fault=None):
    name, path, raw = recording(root, counter, segment=number)
    messages = []
    for event in log.Event.read_multiple_bytes(bz2.decompress(raw)):
      event = event.as_builder()
      event.logMonoTime += number * 2_000_000_000
      if event.which() == 'clocks':
        event.clocks.wallTimeNanos += number * 2_000_000_000
      elif event.which() == 'sentinel':
        event.sentinel.type = ending
      messages.append(event.to_bytes())
    body = b''.join(messages)
    complete = zstd.ZstdCompressor().compress(body)
    path.unlink()
    path = path.with_name('rlog.zst')
    raw = complete
    if fault == 'truncated':
      raw = complete[:-1]
    elif fault == 'empty':
      raw = b''
    elif fault == 'cereal':
      raw = zstd.ZstdCompressor().compress(b'not a Cereal record')
    path.write_bytes(raw)
    return name, path, complete

  def test_closed_sections_only_and_no_join_across_missing_corrupt_or_locked_sections(self):
    from openpilot.starpilot.flm.offroad_partial import partial_inventory, walk_partial_route, analyze_partial

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      name, _, _ = self.segment(root, 18, 0)
      self.segment(root, 18, 2)
      self.segment(root, 18, 3, fault='empty')
      self.segment(root, 18, 4, fault='cereal')
      _, locked, _ = self.segment(root, 18, 5)
      (locked.parent / 'rlog.lock').touch()
      route = partial_inventory(root, name, permitted=lambda: True)
      consumed = []
      receipt = walk_partial_route(root, route, permitted=lambda: True, consume=lambda n, events: consumed.append(n))
      self.assertEqual(consumed, [0, 2])
      self.assertEqual(receipt.decoded_segments, (0, 2))
      self.assertEqual([n for n, reason in receipt.omitted_segments], [1, 3, 4, 5])
      self.assertEqual(receipt.completed_wall_ns, 0)
      assessment = analyze_partial(root, name, values(), permitted=lambda: True)
      self.assertEqual(assessment.counts['recorded_lateral_request_rows'], 100)
      self.assertEqual(assessment.counts['evidence_drives'], 0)
      self.assertEqual(assessment.counts['original_segments_reviewed'], 2)
      self.assertIn('Partial-drive', assessment.report()['source_role'])

  def test_partial_never_saves_or_resolves_trial_and_unchanged_input_is_not_repeated(self):
    from openpilot.starpilot.flm.offroad_store import RouteStorage
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive
    from openpilot.starpilot.flm.offroad_checks import _original_results
    from openpilot.starpilot.flm.offroad_history import EvidenceBank
    from openpilot.starpilot.flm.offroad_partial import analyze_partial

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      name, _, _ = self.segment(root, 18, 0)
      self.segment(root, 18, 1, ending='endOfRoute', fault='truncated')
      params, worker = TestRouteFaultIsolation().setup_worker(directory, name)
      saved = fixtures.TestSavedSetupCapacity.row(1)
      self.assertTrue(RouteStorage(params).save(saved, expected=None, authorized=lambda: True))
      before = params.get(KEY)
      prior = worker.cache.put({'kind': 'declared-earlier-findings', 'findings': ['preserved']})
      result = worker.run_once()
      self.assertTrue(result['partial_drive'])
      self.assertFalse(result['configuration_changed'])
      self.assertEqual(result['partial_segments'], [1, 2])
      self.assertEqual(params.get(KEY), before)
      self.assertEqual(worker.cache.get(prior)['findings'], ['preserved'])
      with patch('openpilot.starpilot.flm.offroad_partial.analyze_partial', side_effect=AssertionError('Do not repeat unchanged partial input')):
        self.assertTrue(worker.run_once()['partial_drive'])
      assessment = analyze_partial(root, name, values(), permitted=lambda: True)
      self.assertFalse(assess_next_drive({}, assessment, scope())['counts_as_new_drive'])
      with self.assertRaisesRegex(ValueError, 'Partial reviews'):
        _original_results(assessment, [], [], values(), root, permitted=lambda: True)
      bank = EvidenceBank(worker.cache.directory)
      with self.assertRaisesRegex(ValueError, 'Partial reviews'):
        bank.retain(SimpleNamespace(receipt=assessment.receipt), {}, 'declared', permitted=lambda: True)
      self.assertEqual(params.get(KEY), before)

  def test_repaired_tail_is_reanalyzed_as_one_complete_drive_and_restart_keeps_partial_label(self):
    from openpilot.starpilot.flm.offroad_daemon import StatusReport

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'logs'
      name, _, _ = self.segment(root, 18, 0)
      _, damaged, complete = self.segment(root, 18, 1, ending='endOfRoute', fault='truncated')
      params, worker = TestRouteFaultIsolation().setup_worker(directory, name)
      partial = worker.run_once()
      self.assertTrue(partial['partial_drive'])
      status = StatusReport(path=Path(directory) / 'restored.json')
      status.restore(worker.cache)
      self.assertTrue(status.previous['partial_drive'])
      status.analyzing({'reason': 'analyzing_last_completed_drive'})
      status.publish()
      wire = json.loads(status.path.read_text())['current']
      self.assertTrue(wire['details']['Drive analysis is partial'])
      self.assertFalse(wire['application']['applied'])
      damaged.write_bytes(complete)
      full = worker.run_once()
      self.assertNotIn('partial_drive', full)
      self.assertEqual(full['analysis_counts']['recorded_lateral_request_rows'], 100)
      self.assertEqual(worker.cache.get(full['assessment_sha256'])['kind'], 'completed_assessment')
      self.assertIsNone(params.get(KEY))

  def test_copied_segment_is_not_counted_twice_and_all_bad_inputs_do_not_return_partial_success(self):
    from openpilot.starpilot.flm.offroad_partial import analyze_partial

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      name, first, _ = self.segment(root, 18, 0)
      _, copied, _ = self.segment(root, 18, 1)
      copied.write_bytes(first.read_bytes())
      result = analyze_partial(root, name, values(), permitted=lambda: True)
      self.assertEqual(result.receipt.decoded_segments, (0,))
      self.assertIn('already reviewed', result.receipt.omitted_segments[0][1])
      self.assertEqual(result.counts['recorded_lateral_request_rows'], 50)
      first.write_bytes(b'')
      copied.write_bytes(b'bad compressed data')
      with self.assertRaises(RouteInputError):
        analyze_partial(root, name, values(), permitted=lambda: True)

  def test_source_change_or_cancel_cannot_publish_a_partial_receipt(self):
    from openpilot.starpilot.flm.offroad_partial import partial_inventory, walk_partial_route
    from openpilot.starpilot.flm.offroad_route import RouteInputCancelled

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      name, first, _ = self.segment(root, 18, 0)
      self.segment(root, 18, 1, fault='truncated')
      inventory = partial_inventory(root, name, permitted=lambda: True)
      with self.assertRaises(RouteInputCancelled):
        walk_partial_route(root, inventory, permitted=lambda: True, consume=lambda n, events: first.write_bytes(b'changed'))
      with self.assertRaises(RouteInputCancelled):
        walk_partial_route(root, inventory, permitted=lambda: False, consume=lambda *args: None)


class TestUnidentifiedVehicleReview(unittest.TestCase):
  """Declared Sonata recordings; no historical identity or accepted tune is invented."""

  @staticmethod
  def setup(directory, *, vin='00000000000000000', counter=18):
    import math
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.lateral.learning_preferences import replace_preference
    from openpilot.starpilot.lateral.controller_selection import ControllerMode, replace_mode

    root = Path(directory)
    params = Params(str(root / 'params'))
    cp = interfaces[CAR.HYUNDAI_SONATA_HYBRID].get_non_essential_params(CAR.HYUNDAI_SONATA_HYBRID)
    cp.carVin = vin
    params.put_bool('OpenpilotEnabledToggle', True, block=True)
    params.put('SteeringLearningPreferences', replace_preference(None, cp, True), block=True)
    params.put('LateralControllerSelection', json.loads(replace_mode(None, cp, ControllerMode.STARPILOT)), block=True)
    route = f'{counter:08x}--aaaaaaaaaa'
    folder = root / 'logs' / (route + '--0')
    folder.mkdir(parents=True)
    messages = [log.Event.new_message(logMonoTime=1_000_000_000, valid=True, carParams=cp).to_bytes()]
    for index in range(1201):
      stamp = 1_000_000_000 + index * 10_000_000
      event = log.Event.new_message(logMonoTime=stamp, valid=True)
      state = event.init('carState')
      state.vEgo, state.canValid = 20., True
      messages.append(event.to_bytes())
      event = log.Event.new_message(logMonoTime=stamp, valid=True)
      control = event.init('carControl')
      control.latActive, control.actuators.torque = True, .1
      messages.append(event.to_bytes())
      event = log.Event.new_message(logMonoTime=stamp, valid=True)
      torque = event.init('controlsState').lateralControlState.init('torqueState')
      torque.active = True
      torque.desiredLateralAccel = .6 + .05 * math.sin(index / 75)
      torque.actualLateralAccel = .75 * torque.desiredLateralAccel
      messages.append(event.to_bytes())
    event = log.Event.new_message(logMonoTime=13_010_000_000, valid=True)
    event.init('clocks').wallTimeNanos = 1_800_000_000_000_000_000 + counter * 1_000_000_000
    messages.append(event.to_bytes())
    event = log.Event.new_message(logMonoTime=13_010_000_000, valid=True)
    event.init('sentinel').type = 'endOfRoute'
    messages.append(event.to_bytes())
    (folder / 'rlog.bz2').write_bytes(bz2.compress(b''.join(messages)))
    params.put('CurrentRoute', route, block=True)
    params.put('RouteCount', counter + 1, block=True)
    return params, cp, root / 'logs'

  def test_actual_sonata_configuration_has_precise_identity_refusal_and_stable_review_guard(self):
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.starpilot.flm.offroad_runtime import current_scope, configure, _current_controller, VehicleIdentityUnavailable

    with OpenpilotPrefix(), tempfile.TemporaryDirectory() as directory:
      params, cp, _ = self.setup(directory)
      with self.assertRaises(VehicleIdentityUnavailable) as first:
        current_scope(cp, params)
      self.assertEqual(str(first.exception), 'vehicle_identity_unavailable')
      with self.assertRaises(VehicleIdentityUnavailable) as second:
        current_scope(cp, params)
      self.assertEqual(first.exception.review_key, second.exception.review_key)
      params.put('AutoSteeringLearningReset', '1', block=True)
      with self.assertRaises(VehicleIdentityUnavailable) as changed:
        current_scope(cp, params)
      self.assertNotEqual(first.exception.review_key, changed.exception.review_key)
      ci, controller = _current_controller(cp, params)
      owner, status = configure(cp, ci, controller, params, manual_status='not_configured')
      self.assertIsNone(owner)
      self.assertEqual(status, 'configuration_unavailable')
      cp.carVin = 'KMHL341JBLU000001'
      self.assertIsNotNone(current_scope(cp, params))
      params.put_bool('OpenpilotEnabledToggle', False, block=True)
      with self.assertRaisesRegex(ValueError, 'Main controls disabled'):
        current_scope(cp, params)

  def test_original_unknown_vin_route_is_reviewed_without_pooling_fitting_or_touching_saved_profiles(self):
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.starpilot.flm.offroad_store import RouteStorage
    from openpilot.starpilot.flm.offroad_history import EvidenceBank

    with OpenpilotPrefix(), tempfile.TemporaryDirectory() as directory:
      params, _, root = self.setup(directory)
      cache = Path(directory) / 'cache'
      saved = fixtures.TestSavedSetupCapacity.row(1)
      self.assertTrue(RouteStorage(params).save(saved, expected=None, authorized=lambda: True))
      original = params.get(KEY)
      with patch.object(EvidenceBank, 'retain', side_effect=AssertionError('Unidentified cars must never be pooled')):
        worker = OffroadWorker(params, root, cache, permitted=lambda: True,
                               checker=lambda *a, **k: self.fail('An unidentified car must never enter automatic qualification'))
        result = worker.run_once()
      self.assertEqual(result['reason'], 'vehicle_identity_unavailable')
      self.assertEqual(result['analysis_counts']['recorded_tracking'], 1201)
      self.assertGreater(result['analysis_counts']['diagnostic_whole_windows'], 0)
      self.assertTrue(result['recorded_tracking']['points'])
      self.assertGreater(result['recommendation_count'], 0)
      self.assertFalse(result['configuration_changed'])
      self.assertFalse(result['actual_application_proven'])
      self.assertEqual(result['skipped_routes'], [])
      self.assertEqual(params.get(KEY), original)
      doc = worker.cache.get(result['assessment_sha256'])
      self.assertEqual(doc['kind'], 'identity_review')
      self.assertNotIn('current_scope', doc['assessment'])
      with patch.object(worker, 'assessor', side_effect=AssertionError('An unchanged recording must reuse its checkpoint')):
        self.assertEqual(worker.run_once()['assessment_sha256'], result['assessment_sha256'])
      self.assertEqual(params.get(KEY), original)
      # A second physical car could have exactly the same unknown-VIN CP.
      # The new route must still be reviewed alone, not counted as confirmation.
      self.setup(directory, counter=19)
      with patch.object(EvidenceBank, 'retain', side_effect=AssertionError('Same-model unidentified drives cannot be pooled')):
        second = worker.run_once()
      self.assertEqual(second['completed_route'], '00000013--aaaaaaaaaa')
      self.assertEqual(second['analysis_counts']['recorded_tracking'], 1201)
      self.assertEqual(second['analysis_counts'].get('evidence_drives', 0), 0)
      self.assertEqual(worker.cache.get(result['assessment_sha256']), doc)
      self.assertEqual(params.get(KEY), original)

  def test_invalid_cached_vin_is_queried_again_instead_of_reusing_an_empty_identity(self):
    from opendbc.car import car_helpers
    from opendbc.car.hyundai.values import CAR
    fresh_vin = 'KMHL341JBLU000001'
    cases = (('', 1, {}), ('invalid', 1, {}), ('00000000000000000', 1, {}), (fresh_vin, 0, {}),
             ('', 0, {'REPLAY': '1'}), ('invalid', 0, {'SKIP_FW_QUERY': '1'}))
    for cached_vin, expected_queries, environment in cases:
      with self.subTest(vin=cached_vin, environment=environment), patch.dict(os.environ, environment, clear=True):
        cp = car_helpers.interfaces[CAR.HYUNDAI_SONATA_HYBRID].get_non_essential_params(CAR.HYUNDAI_SONATA_HYBRID)
        cp.carVin = cached_vin
        cp.carFw = [{'ecu': 'engine', 'fwVersion': b'declared-version', 'address': 0x7e0}]
        with (patch.object(car_helpers, 'normal_bus_query_brand', return_value=None),
              patch.object(car_helpers, 'honda_normal_bus_topology', return_value=False),
              patch.object(car_helpers, 'chrysler_normal_bus_topology', return_value=False),
              patch.object(car_helpers, 'get_vin', return_value=(0x7e8, 0, fresh_vin)) as query,
              patch.object(car_helpers, 'get_present_ecus', return_value=set()),
              patch.object(car_helpers, 'get_fw_versions_ordered', return_value=[]),
              patch.object(car_helpers, 'match_fw_to_car', return_value=(True, {CAR.HYUNDAI_SONATA_HYBRID})),
              patch.object(car_helpers, 'can_fingerprint', return_value=(CAR.HYUNDAI_SONATA_HYBRID, {})),
              patch.object(car_helpers, 'automatic_bolt_candidate', side_effect=lambda candidate, *a: candidate)):
          result = car_helpers.fingerprint(lambda wait_for_one=False: [], lambda *a: None, lambda *a: None, cp)
        self.assertEqual(query.call_count, expected_queries)
        self.assertEqual(result[2], fresh_vin if expected_queries or cached_vin == fresh_vin else '00000000000000000')
