"""Actual reader boundaries and retained reports; no actuator or road claims."""
import bz2
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from openpilot.cereal import log
from openpilot.starpilot.flm.offroad_daemon import StatusReport
from openpilot.starpilot.flm.offroad_route import RouteInputError, route_inventory, walk_completed_route
from openpilot.starpilot.flm.offroad_partial import partial_inventory, walk_partial_route
from openpilot.starpilot.flm.tests.test_offroad_fault_isolation import recording, zstd_recording


class TestAnalysisProgress(unittest.TestCase):
  def test_real_closed_recording_reports_only_consumed_sections_then_verification(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      recording(root, 8)
      route = route_inventory(root)['routes'][0]
      events = []
      receipt = walk_completed_route(root, route, permitted=lambda: True,
        consume=lambda number, rows: events.append(('consumed', number)), on_progress=lambda value: events.append(value))
      self.assertEqual(len(receipt.segments), 1)
      self.assertEqual(events, [
        {'phase': 'reading', 'completed': 0, 'total': 1}, ('consumed', 0),
        {'phase': 'reading', 'completed': 1, 'total': 1}, {'phase': 'verifying', 'completed': 1, 'total': 1}])

  def test_corruption_cannot_report_a_section_as_read_or_verified(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      recording(root, 8, corrupt=True)
      route = route_inventory(root)['routes'][0]
      events = []
      with self.assertRaises(RouteInputError):
        walk_completed_route(root, route, permitted=lambda: True,
          consume=lambda *args: self.fail('Corrupt source escaped'), on_progress=events.append)
      self.assertEqual(events, [{'phase': 'reading', 'completed': 0, 'total': 1}])

  def test_bad_zstd_tail_goes_straight_to_partial_review_and_repaired_tail_is_retried(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      name, first, raw = recording(root, 8, closed=False)
      closing = log.Event.new_message(logMonoTime=1_500_000_000, valid=True)
      closing.init('sentinel').type = 'endOfSegment'
      first.write_bytes(bz2.compress(bz2.decompress(raw) + closing.to_bytes()))
      _, tail, healthy = zstd_recording(root, 8, segment=1, fault='unfinished')
      route = route_inventory(root)['routes'][0]
      consumed = []

      with self.assertRaisesRegex(RouteInputError, 'incomplete or trailing zstd frame'):
        walk_completed_route(root, route, permitted=lambda: True, consume=lambda number, _: consumed.append(number))
      self.assertEqual(consumed, [], 'An unreadable ending must be caught before collecting the prefix')
      progress = []
      partial = walk_partial_route(root, partial_inventory(root, name, permitted=lambda: True), permitted=lambda: True,
                                   consume=lambda number, _: consumed.append(number), on_progress=progress.append)
      self.assertEqual(progress, [{'phase': 'reading', 'completed': 0, 'total': 2},
                                  {'phase': 'reading', 'completed': 1, 'total': 2},
                                  {'phase': 'verifying', 'completed': 1, 'total': 2}])
      self.assertEqual(partial.decoded_segments, (0,))
      self.assertEqual(partial.omitted_segments, ((1, 'incomplete or trailing zstd frame'),))
      self.assertEqual(partial.completed_wall_ns, 0)
      self.assertEqual(consumed, [0])

      tail.write_bytes(healthy)
      consumed.clear()
      route = route_inventory(root)['routes'][0]
      receipt = walk_completed_route(root, route, permitted=lambda: True, consume=lambda number, _: consumed.append(number))
      self.assertEqual(consumed, [0, 1])
      self.assertEqual(len(receipt.segments), 2)

  def test_optional_display_failure_does_not_turn_valid_source_into_a_corrupt_route(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      recording(root, 8)
      route = route_inventory(root)['routes'][0]
      def unavailable(value):
        raise OSError('Display unavailable')
      receipt = walk_completed_route(root, route, permitted=lambda: True, consume=lambda *args: None, on_progress=unavailable)
      self.assertEqual(len(receipt.segments), 1)

  def test_new_progress_never_uses_retained_drive_counts_or_erases_its_findings(self):
    with tempfile.TemporaryDirectory() as root:
      report = StatusReport(path=Path(root) / 'report.json', clock_ns=lambda: 10_000_000_000)
      report.previous = {'completed_route': 'earlier-drive', 'vehicle': 'declared', 'recommendations': [],
                         'analysis_counts': {'original_segments_reviewed': 99, 'original_segments_available': 99}}
      report.analyzing({'status': 'fitting', 'reason': 'analyzing_last_completed_drive',
                        'analysis_progress': {'phase': 'reading', 'completed': 2, 'total': 10}})
      report.publish()
      details = json.loads(report.path.read_text())['current']['details']
      self.assertEqual(details['Parked analysis progress'], ['reading', 2, 10])
      self.assertEqual(details['Completed drive analyzed'], 'earlier-drive')
      self.assertTrue(details['Earlier analysis shown while updating'])
      report.analyzing({'status': 'fitting', 'reason': 'loading_parked_analysis'})
      report.publish()
      self.assertEqual(json.loads(report.path.read_text())['current']['details']['Parked analysis progress'], ['preparing', None, None])
      report.analyzing({'status': 'fitting', 'reason': 'checking_recorded_steering_recommendations',
                        'analysis_counts': {'original_segments_reviewed': 10, 'original_segments_available': 10}})
      report.publish()
      self.assertEqual(json.loads(report.path.read_text())['current']['details']['Parked analysis progress'], ['checking', 10, 10])
      report.set({'status': 'collecting', 'reason': 'more_independent_eligible_tracking_required'})
      report.publish()
      self.assertNotIn('Parked analysis progress', json.loads(report.path.read_text())['current']['details'])

  def test_real_backlog_reads_each_inactive_recording_once(self):
    from openpilot.common.params import Params
    from openpilot.starpilot.flm import offroad_processor as processor
    from openpilot.starpilot.flm.offroad_worker import OffroadWorker
    from openpilot.starpilot.flm.tests.test_offroad_backend import scope

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      routes = [recording(root / 'logs', counter)[0] for counter in (18, 17, 16)]
      params = Params(str(root / 'params'))
      params.put('CurrentRoute', routes[0], block=True)
      params.put('RouteCount', 19, block=True)
      progress = []
      worker = OffroadWorker(params, root / 'logs', root / 'cache', permitted=lambda: True,
                             scope_for=lambda *_: scope(), on_progress=progress.append)
      with patch.object(processor, 'collect_route_inputs', wraps=processor.collect_route_inputs) as read:
        for name in routes:
          self.assertEqual(worker.run_once()['completed_route'], name)
        finished = worker.run_once()
        self.assertEqual(finished['completed_route'], routes[0])
        self.assertEqual([call.args[1]['routeId'] for call in read.call_args_list], routes)
      starts = [row for row in progress if row['phase'] == 'reading' and row['completed'] == 0]
      self.assertEqual([(row['recording_position'], row['recording_total']) for row in starts], [(1, 3), (2, 3), (3, 3)])
      self.assertEqual(finished['recommendations'], [])
      self.assertFalse(finished['configuration_changed'])

  def test_pending_recordings_finish_before_one_cross_drive_refresh(self):
    from types import SimpleNamespace
    from openpilot.common.params import Params
    from openpilot.starpilot.flm import offroad_worker as module
    from openpilot.starpilot.flm.offroad_history import EvidenceBank
    from openpilot.starpilot.flm.offroad_route import RouteReceipt
    from openpilot.starpilot.flm.tests.test_offroad_backend import scope

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      routes = [{'routeId': f'{counter:08x}--aaaaaaaaaa'} for counter in (18, 17, 16)]
      reads = []
      revision = [0]

      def assess(_, __, previous, *, permitted, selected_route=None, **kwargs):
        route = routes[0] if selected_route is None else selected_route
        reads.append(route)
        if route != routes[0]:
          revision[0] += 1
        cp = SimpleNamespace(carFingerprint='declared-queue-car')
        previous(cp)
        receipt = RouteReceipt(route['routeId'], 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
        return module.CompletedAssessment(receipt, (), {'active_lateral_request_rows': 500}, cp, (), route)

      with patch.object(module, 'latest_route', return_value=routes[0]), patch.object(module, 'pending_routes', return_value=routes), \
           patch.object(module, 'route_inventory', return_value={'scanIncomplete': False, 'routes': routes}):
        worker = module.OffroadWorker(Params(str(root / 'params')), root / 'logs', root / 'cache', permitted=lambda: True,
                                     assessor=assess, scope_for=lambda *_: scope())
        worker.evidence_bank = EvidenceBank(worker.cache.directory)
        with patch.object(worker.evidence_bank, 'revision', side_effect=lambda: f'{revision[0]:064x}'):
          for name in [*routes, routes[0], routes[0]]:
            self.assertEqual(worker.run_once()['completed_route'], name['routeId'])
        self.assertEqual(reads, [*routes, routes[0]])

  def test_checking_callback_stays_active_and_retains_recording_queue(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.diagnostic_state import _stage

    report = StatusReport(clock_ns=lambda: 10_000_000_000)
    observed = []
    report.diagnostics = SimpleNamespace(observe=observed.append)
    report.analyzing({'reason': 'analyzing_last_completed_drive', 'analysis_progress': {
      'phase': 'reading', 'completed': 6, 'total': 43, 'recording_position': 2, 'recording_total': 4}})
    report.analyzing({'reason': 'checking_recorded_steering_recommendations', 'completed_route': 'declared'})
    self.assertEqual(_stage(observed[-1]), 'checking_recommendations')
    self.assertEqual(report.current['analysis_progress']['recording_position'], 2)
    self.assertEqual(report.current['analysis_progress']['recording_total'], 4)
    report.set({'status': 'collecting', 'reason': 'no_consistent_adjustment_in_completed_drive', 'completed_route': 'declared'})
    self.assertEqual(_stage(observed[-1]), 'analysis_result')
    self.assertNotIn('analysis_progress', report.current)

  def test_running_read_waits_for_fresh_device_state_but_never_through_an_onroad_transition(self):
    from types import SimpleNamespace as NS
    from openpilot.starpilot.flm.offroad_daemon import wait_for_parked

    def fixture(resumed, onroad):
      now = [100.0]
      flags = {'IsOffroad': True, 'IsEngaged': False}
      data = {'deviceState': NS(started=False), 'pandaStates': [NS(ignitionLine=False, ignitionCan=False, controlsAllowed=False, faults=[])],
              'peripheralState': NS()}

      class Messages:
        seen = alive = valid = dict.fromkeys(data, True)
        recv_time = dict.fromkeys(data, 100.0) | {'deviceState': 97.0}

        def __getitem__(self, service):
          return data[service]

        def update(self, timeout):
          now[0] += timeout / 1000
          self.recv_time.update(pandaStates=now[0], peripheralState=now[0])
          if onroad and now[0] >= 100.9:
            flags['IsOffroad'] = False
          if resumed and now[0] >= 101.1:
            self.recv_time['deviceState'] = now[0]

      return now, Messages(), NS(get_bool=lambda key: flags[key])

    for recovery, resumed, onroad, expected, limit in ((False, True, False, False, 0.78), (True, True, False, True, 1.13),
                                                       (True, False, False, False, 2.03), (True, True, True, False, 0.93)):
      with self.subTest(recovery=recovery, resumed=resumed, onroad=onroad):
        now, sm, params = fixture(resumed, onroad)
        refusals = []
        self.assertEqual(wait_for_parked(sm, params, clock=lambda now=now: now[0],
                                        recover_device_state=recovery, on_refusal=lambda *result, refusals=refusals: refusals.append(result)), expected)
        self.assertLessEqual(now[0] - 100.0, limit)
        if expected:
          self.assertEqual(sm.recv_time['deviceState'], now[0])
          self.assertEqual(refusals, [])
        elif onroad:
          self.assertEqual(refusals[0][0], 'waiting_for_vehicle_offroad')
          self.assertFalse(refusals[0][2])
        else:
          self.assertEqual(refusals[0][0], 'waiting_for_fresh_deviceState')
          self.assertTrue(refusals[0][2])

    # A stalled initial poll must not consume the recovery budget. When deviceState
    # recovers first, wait for fresh Panda data rather than reverting to 750 ms.
    now, sm, params = fixture(resumed=False, onroad=False)
    original_update = sm.update

    def staggered_update(timeout):
      if timeout == 0:
        now[0] += 2.2
      original_update(timeout)
      sm.recv_time['pandaStates'] = now[0] if now[0] >= 103.8 else 100
      if now[0] >= 103.3:
        sm.recv_time['deviceState'] = now[0]

    sm.update = staggered_update
    self.assertTrue(wait_for_parked(sm, params, clock=lambda: now[0], recover_device_state=True))
    self.assertGreaterEqual(now[0], 103.8)
