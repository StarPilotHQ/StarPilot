"""Actual reader boundaries and retained reports; no actuator or road claims."""
import bz2
import json
from pathlib import Path
import tempfile
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
