"""Actual reader boundaries and retained reports; no actuator or road claims."""
import json
from pathlib import Path
import tempfile
import unittest

from openpilot.starpilot.flm.offroad_daemon import StatusReport
from openpilot.starpilot.flm.offroad_route import RouteInputError, route_inventory, walk_completed_route
from openpilot.starpilot.flm.tests.test_offroad_fault_isolation import recording


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
