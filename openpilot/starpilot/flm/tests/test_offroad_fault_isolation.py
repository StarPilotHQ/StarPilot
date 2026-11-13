"""Declared corrupt-input fixtures; no road improvement or accepted tune is mocked."""

import bz2
import hashlib
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
      completed_wall_ns=180_000_000_000))
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
