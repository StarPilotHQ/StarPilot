"""Support export remains useful when learning fails, without exporting private logs."""

import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.flm_diagnostics import download
from openpilot.starpilot.galaxy.flm_training_results import FlmTrainingResults
from openpilot.starpilot.galaxy.server import make_server


class TestDiagnostics(unittest.TestCase):
  def test_partial_review_coverage_is_exported_without_claiming_a_saved_tune(self):
    report = {'current': {'status': 'collecting', 'reason': 'partial_drive_provisional_only', 'details': {
      'Drive analysis is partial': True, 'Partial drive segments': [9, 10],
      'Omitted recorded segments': [[9, 'incomplete or trailing zstd frame']],
    }}}
    _, body = download(report, None)
    result = json.loads(body)
    self.assertTrue(result['current']['analysis']['Drive analysis is partial'])
    self.assertEqual(result['current']['analysis']['Partial drive segments'], [9, 10])
    self.assertEqual(result['current']['analysis']['Omitted recorded segments'], [[9, 'incomplete or trailing zstd frame']])
    self.assertEqual(result['saved_profiles'], [])

  def test_failed_reviews_skips_counts_and_version_survive_without_private_payloads(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      params = SimpleNamespace(get_param_path=lambda key: str(root / key))
      (root / 'GitCommit').write_text('a' * 40)
      (root / 'CarParams').write_text('private-vin')
      (root / 'AccessToken').write_text('private-token')
      cache = AssessmentCache(directory_for_params(params))
      cache.checkpoint({'input_fingerprint': 'b' * 64, 'result': {'status': 'unavailable', 'reason': 'Assessment cache full',
        'skipped_routes': [['00000049--1b13f9d9d3', 'incomplete or trailing zstd frame']],
        'analysis_counts': {'recorded_tracking': 83433}}})
      name, body = download({'current': {'status': 'unavailable', 'reason': 'Assessment cache full', 'details': {
        'CarParams': 'private-vin', 'Recorded steering observations': 83433}}, 'history': [
          {'status': 'paused', 'reason': 'password=private-secret https://example.invalid/private-token'}]}, params)
      result = json.loads(body)
      self.assertEqual(name, 'starpilot-flm-logs.json')
      self.assertEqual(result['GitCommit'], 'a' * 40)
      self.assertEqual(result['current']['reason'], 'Assessment cache full')
      self.assertEqual(result['checkpoints'][0]['skipped_drives'][0]['reason'], 'incomplete or trailing zstd frame')
      self.assertEqual(result['current']['analysis']['Recorded steering observations'], 83433)
      self.assertNotIn(b'private-vin', body)
      self.assertNotIn(b'private-token', body)
      self.assertNotIn(b'private-secret', body)

  def test_export_does_not_create_an_absent_cache_or_restore_a_tune(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary) / 'params'
      root.mkdir()
      params = SimpleNamespace(get_param_path=lambda key: str(root / key))
      before = list(root.parent.iterdir())
      _, body = download({}, params)
      self.assertEqual(json.loads(body)['storage']['status'], 'not_created')
      self.assertEqual(list(root.parent.iterdir()), before)
      self.assertEqual(list(root.iterdir()), [])

  def test_authenticated_attachment_and_query_refusal(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      owner = GalaxyAccessOwner(root / 'access')
      self.assertTrue(owner.configure('password123', lambda: True))
      source = FlmTrainingResults(root / 'absent-report')
      server = make_server(port=0, owner=owner, training_results=source, parked=lambda: True)
      thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
      thread.start()

      def request(path, cookie=None, *, method='GET', body=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
          headers = {'Forwarded': 'for=203.0.113.8', 'Origin': f'http://127.0.0.1:{server.server_port}', 'Content-Type': 'application/json'}
          if cookie:
            headers['Cookie'] = cookie
          connection.request(method, path, body, headers)
          response = connection.getresponse()
          return response.status, dict(response.getheaders()), response.read()
        finally:
          connection.close()

      try:
        with patch.object(source, 'download', wraps=source.download) as reader:
          self.assertEqual(request('/api/flm/logs/download')[0], 401)
          reader.assert_not_called()
          status, headers, _ = request('/api/auth/login', method='POST', body=json.dumps({'password': 'password123'}))
          self.assertEqual(status, 200)
          cookie = headers['Set-Cookie'].split(';')[0]
          self.assertEqual(request('/api/flm/logs/download?path=secret', cookie)[0], 400)
          status, headers, body = request('/api/flm/logs/download', cookie)
          self.assertEqual(status, 200)
          self.assertIn('starpilot-flm-logs.json', headers['Content-Disposition'])
          self.assertEqual(json.loads(body)['current']['reason'], 'learner_report_unavailable')
      finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class TestIdentityAndDashboardDiagnostics(unittest.TestCase):
  def test_identity_refusal_explains_review_without_claiming_automatic_learning(self):
    report = {'current': {'status': 'collecting', 'reason': 'vehicle_identity_unavailable', 'details': {
      'Recorded vehicle identity': 'unavailable', 'Recorded steering observations': 1201}}}
    result = json.loads(download(report, None)[1])
    self.assertEqual(result['current']['analysis']['Recorded vehicle identity'], 'unavailable')
    self.assertIn('no saved tune was loaded', result['current']['explanation'])
    self.assertEqual(result['saved_profiles'], [])

  def test_dashboard_export_shows_existing_failures_and_permission_without_route_or_private_data(self):
    from openpilot.starpilot.galaxy.drive_stats import DriveStatsOwner
    from openpilot.starpilot.galaxy.flm_diagnostics import with_dashboard_diagnostics
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      owner = DriveStatsOwner(root=root, store=root / 'stats.json', permitted=lambda: False)
      try:
        owner._scanned = True
        owner._failures = {'private-route-location': {'reason': 'End of route was not recorded'},
                           'secret-route': {'reason': 'private-secret path /data/secret'}}
        authority = SimpleNamespace(status=lambda: {'allowed': False, 'reason': 'Vehicle status unavailable'}, effective=lambda: None)
        body = with_dashboard_diagnostics(download({}, None)[1], owner, authority)
        data = json.loads(body)['dashboard_statistics']
        self.assertEqual(data['failed_routes'], 2)
        self.assertFalse(data['analysis_permitted'])
        self.assertFalse(data['vehicle_permission']['allowed'])
        self.assertEqual(data['failure_reasons'], {'End of route was not recorded': 1, 'route_analysis_error': 1})
        self.assertNotIn(b'private-route', body)
        self.assertNotIn(b'private-secret', body)
        self.assertIsNone(owner._scheduler)
        self.assertFalse(owner.store.exists())
        self.assertEqual(json.loads(with_dashboard_diagnostics(download({}, None)[1], None))['dashboard_statistics']['status'], 'not_started')
      finally:
        owner.close()
