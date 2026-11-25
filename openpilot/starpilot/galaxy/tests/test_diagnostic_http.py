"""Authenticated diagnostics over the same local and remote HTTP handler."""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.log_relay_stage import LogRelayStager
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway


class DiagnosticHTTPTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.access = GalaxyAccessOwner(Path(self.temp.name))
    self.access.configure('test-password-123', lambda: True)
    self.calls = []
    self.addresses = SimpleNamespace(snapshot=lambda: self.record('address', {'available': True, 'addresses': [
      {'interface': 'wlan0', 'label': '192.168.4.2 (wlan0)', 'url': 'http://192.168.4.2:8082/'}], 'reason': ''}))
    self.console = SimpleNamespace(snapshot=lambda: self.record('console', {'available': True, 'text': 'actual console',
      'truncated': False, 'pane': 'comma:0.0', 'reason': ''}))
    self.settings = SimpleNamespace(diagnostics=lambda: self.record('settings', {'schemaVersion': 1, 'sections': []}))
    self.stage_posts = []
    self.stage_response = None
    self.stager = LogRelayStager(
      lambda **_kw: ('starport-logs.zip', b'PK\x03\x04' + b'0' * 32),
      lambda: b'PK\x03\x04' + b'1' * 32,
      lambda: b'PK\x03\x04' + b'2' * 32,
      post=self._stage_post,
      official=lambda: True,
    )
    self.server = make_server(port=0, owner=self.access, parked=lambda: True, local_access=self.addresses,
                              tmux_live=self.console, settings=self.settings, log_relay_stager=self.stager)
    self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    self.thread.start()
    self.cookie = ''

  def _stage_post(self, url, **kwargs):
    self.stage_posts.append((url, kwargs))
    return self.stage_response

  def tearDown(self):
    self.server.shutdown()
    self.thread.join(2)
    self.server.server_close()
    self.temp.cleanup()

  def record(self, name, value):
    self.calls.append(name)
    return value

  def request(self, path, *, method='GET', payload=None, remote=False, cookie=None, origin=None):
    headers = {'Origin': origin or f'http://127.0.0.1:{self.server.server_port}', 'Content-Type': 'application/json'}
    if cookie is not None or self.cookie:
      headers['Cookie'] = self.cookie if cookie is None else cookie
    if remote:
      headers['Forwarded'] = 'for=203.0.113.8'
    connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
    try:
      connection.request(method, path, None if payload is None else json.dumps(payload), headers)
      response = connection.getresponse()
      token = response.getheader('Set-Cookie')
      return response.status, json.loads(response.read()), token.split(';')[0] if token else ''
    finally:
      connection.close()

  def test_real_http_routes_authentication_and_no_mutations(self):
    for path in ('/api/local-access', '/api/local-access/capture', '/api/tmux/live', '/api/troubleshoot', '/api/logs/stage'):
      self.assertEqual(self.request(path, remote=True)[0], 401)
    self.assertEqual(self.calls, [])
    status, _, self.cookie = self.request('/api/auth/session')
    self.assertEqual(status, 200)
    self.assertEqual(self.request('/api/local-access')[1]['addresses'][0]['url'], 'http://192.168.4.2:8082/')
    status, capture, _ = self.request('/api/local-access/capture')
    self.assertEqual(status, 200)
    self.assertIn('http://192.168.4.2:8082/', capture['text'])
    self.assertEqual(self.request('/api/tmux/live')[1]['text'], 'actual console')
    self.assertEqual(self.request('/api/troubleshoot')[1]['sections'], [])
    before = len(self.calls)
    self.assertEqual(self.request('/api/tmux/live', origin='https://example.org')[0], 403)
    self.assertNotEqual(self.request('/api/tmux/live', method='POST', payload={})[0], 200)
    self.assertEqual(len(self.calls), before)

  def test_log_stage_routes_require_session_and_stage_bundle(self):
    self.assertEqual(self.request('/api/logs/stage', method='POST', payload={'kind': 'starport', 'note': 'Blank'}, remote=True)[0], 401)
    _, _, self.cookie = self.request('/api/auth/session')
    claim_url = 'https://starpilot-log-relay.nathan-g-hubbard.workers.dev/report/galaxy/claim/' + ('a' * 64)
    self.stage_response = Mock(status_code=200)
    self.stage_response.json.return_value = {'claimUrl': claim_url}
    status, idle, _ = self.request('/api/logs/stage')
    self.assertEqual(status, 200)
    self.assertEqual(idle['state'], 'idle')
    self.assertTrue(idle['available'])

    status, accepted, _ = self.request('/api/logs/stage', method='POST',
                                       payload={'kind': 'connection', 'phone': 'ios', 'note': 'Lost network'})
    self.assertEqual(status, 202)
    self.assertEqual(accepted['kind'], 'connection')

    deadline = time.monotonic() + 2
    state = accepted
    while state['state'] == 'staging' and time.monotonic() < deadline:
      time.sleep(0.01)
      _, state, _ = self.request('/api/logs/stage')
    self.assertEqual(state['state'], 'ready')
    self.assertEqual(state['claimUrl'], claim_url)
    self.assertEqual(len(self.stage_posts), 1)
    _, kwargs = self.stage_posts[0]
    self.assertFalse(kwargs['allow_redirects'])
    self.assertEqual(kwargs['headers']['X-Galaxy-Kind'], 'connection')
    self.assertEqual(kwargs['headers']['X-Galaxy-Platform'], 'ios')
    self.assertIn('Lost%20network', kwargs['headers']['X-Galaxy-Note'])

  def test_authenticated_forwarded_transport_uses_same_tools(self):
    status, _, self.cookie = self.request('/api/auth/login', method='POST', payload={'password': 'test-password-123'}, remote=True)
    self.assertEqual(status, 200)
    self.assertEqual(self.request('/api/tmux/live', remote=True)[1]['text'], 'actual console')
    self.assertEqual(self.request('/api/local-access', remote=True)[0], 200)
    self.assertEqual(self.request('/api/troubleshoot', remote=True)[0], 200)

  def test_revocation_during_console_read_discards_output(self):
    _, _, self.cookie = self.request('/api/auth/login', method='POST', payload={'password': 'test-password-123'}, remote=True)
    entered, release = threading.Event(), threading.Event()
    def wait():
      entered.set()
      release.wait(2)
      return {'available': True, 'text': 'private console'}
    self.console.snapshot = wait
    response = []
    thread = threading.Thread(target=lambda: response.append(self.request('/api/tmux/live', remote=True)))
    thread.start()
    self.assertTrue(entered.wait(2))
    self.assertEqual(self.request('/api/auth/logout', method='POST', payload={}, remote=True)[0], 200)
    release.set()
    thread.join(3)
    self.assertEqual(response[0][0], 401)
    self.assertNotIn('private console', json.dumps(response))

  def test_diagnostics_projects_existing_owners_without_raw_params(self):
    context = AuthorityContext(True, SimpleNamespace(carFingerprint='TEST_CAR', brand='test',
      openpilotLongitudinalControl=True, steerControlType='torque'), b'cp', False)
    class NoRawParams:
      def __getattr__(self, name):
        raise AssertionError(f'Raw Params access: {name}')
    gateway = SettingsGateway(NoRawParams(), Mock(sample=lambda: context))
    pages = []
    def state(page, ctx):
      pages.append(page)
      self.assertIs(ctx, context)
      return SimpleNamespace(title=page, rows=[SimpleNamespace(label='Visible preference', value='On', source=b'private')])
    gateway._state = Mock(side_effect=state)
    result = gateway.diagnostics()
    self.assertEqual(pages, ['torque', 'torque/developer', 'lane', 'lane_change', 'aol', 'conditional', 'profiles', 'slc', 'curve'])
    self.assertEqual(result['vehicle']['fingerprint'], 'TEST_CAR')
    self.assertEqual(result['sections'][0]['rows'], [{'label': 'Visible preference', 'value': 'On'}])
    self.assertNotIn('private', json.dumps(result))
    self.assertEqual(gateway.views, {})
    self.assertEqual(gateway.intents, {})


if __name__ == '__main__':
  unittest.main()
