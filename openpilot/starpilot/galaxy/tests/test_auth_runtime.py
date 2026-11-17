"""Actual loopback HTTP auth with disposable credentials and injected time."""

import http.client
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest import mock

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.http_server import ReverseProxy
from openpilot.starpilot.galaxy.server import _LocalHTTPServer, allowed_authority, direct_local_connection, make_server


class AuthRuntimeTest(unittest.TestCase):
  def test_weather_requires_session_and_rechecks_revocation(self):
    sample = {'status': 'current', 'temperatureC': 12, 'observedAt': 10000}
    source = mock.Mock()
    source.sample.return_value = sample
    with mock.patch('openpilot.starpilot.galaxy.weather.WeatherSource', return_value=source) as factory:
      self.assertEqual(self.request('/api/weather')[0], 503)
      factory.assert_not_called()
      self.assertTrue(self.owner.configure('password123', lambda: True))
      _, _, cookie = self.login()
      cookie = cookie.split(';', 1)[0]
      self.assertEqual(self.request('/api/weather', headers={'Cookie': cookie})[0], 200)
      source.sample.side_effect = lambda: (self.owner.replace_for_pairing('newpassword123', lambda: True), sample)[1]
      self.assertEqual(self.request('/api/weather', headers={'Cookie': cookie})[0], 401)
  def test_reverse_proxy_requires_password_even_without_forwarding_headers(self):
    self.stop()
    self.server = make_server(port=0, owner=self.owner, monitor=self.monitor,
                              reverse_proxy=ReverseProxy('127.0.0.1', 'https://galaxy.example'))
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    self.worker.start()
    headers = {'Host': 'galaxy.example', 'Origin': 'https://galaxy.example'}
    self.assertTrue(self.owner.configure('password123', lambda: True))
    self.assertEqual(self.request('/api/system/monitor', headers=headers, local=True)[0], 401)
    self.assertEqual(self.request('/api/auth/session', headers={'Host': 'evil.example'}, local=True)[0], 403)
    self.assertEqual(self.post('/api/auth/login', {'password': 'password123'},
                               headers={**headers, 'Origin': 'https://evil.example'}, local=True)[0], 403)
    code, _, response_headers = self.post('/api/auth/login', {'password': 'password123'}, headers=headers, local=True)
    self.assertEqual(code, 200)
    cookie = response_headers['Set-Cookie'].split(';', 1)[0]
    self.assertEqual(self.request('/api/system/monitor', headers={**headers, 'Cookie': cookie}, local=True)[0], 200)

  def test_socket_destination_authority_rejects_dns_rebinding_and_mismatch(self):
    port = 8082
    self.assertTrue(allowed_authority('127.0.0.1:8082', '127.0.0.1', port))
    self.assertTrue(allowed_authority('localhost:8082', '127.0.0.1', port))
    self.assertTrue(allowed_authority('192.168.4.21:8082', '192.168.4.21', port))
    self.assertTrue(allowed_authority('100.64.1.5:8082', '100.64.1.5', port))
    self.assertFalse(allowed_authority('device.example:8082', '192.168.4.21', port))
    self.assertFalse(allowed_authority('192.168.4.22:8082', '192.168.4.21', port))
    self.assertFalse(allowed_authority('localhost:8082', '192.168.4.21', port))
    self.assertFalse(allowed_authority('0.0.0.0:8082', '0.0.0.0', port))
    self.assertFalse(allowed_authority('192.168.4.21:9999', '192.168.4.21', port))

  def test_wildcard_bound_server_retains_password_and_origin_guards(self):
    self.stop()
    self.server = make_server(port=0, host='0.0.0.0', owner=self.owner, monitor=self.monitor,
                              clock=lambda: self.now)
    self.assertEqual(self.server.server_address[0], '0.0.0.0')
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    self.worker.start()
    self.assertEqual(self.request('/api/system/monitor')[0], 503)
    self.assertTrue(self.owner.configure('password123', lambda: True))
    self.assertEqual(self.request('/api/system/monitor')[0], 401)
    self.assertEqual(self.request('/api/auth/session', headers={'Host': 'device.example:8082'})[0], 403)
    status, _, cookie = self.login()
    self.assertEqual(status, 200)
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': cookie.split(';', 1)[0]})[0], 200)
    self.assertEqual(self.post('/api/auth/logout', {}, headers={'Origin': 'http://other.example',
                                                                'Cookie': cookie.split(';', 1)[0]})[0], 403)

  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.owner = GalaxyAccessOwner(Path(self.directory.name) / 'access')
    self.now = 100.0
    self.samples = 0

    class Monitor:
      def sample(inner):
        self.samples += 1
        return {'mode': 'local-runtime', 'schemaVersion': 1}

    self.monitor = Monitor()
    self.start()

  def start(self):
    self.server = make_server(port=0, owner=self.owner, monitor=self.monitor, clock=lambda: self.now)
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    self.worker.start()
    self.addCleanup(self.stop)

  def stop(self):
    if self.server is not None:
      self.server.shutdown()
      self.worker.join(timeout=2)
      self.server.server_close()
      self.server = None

  def request(self, path, *, method='GET', body=None, headers=None, local=False):
    connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
    try:
      request_headers = {} if local else {'Forwarded': 'for=203.0.113.8'}
      request_headers.update(headers or {})
      connection.request(method, path, body=body, headers=request_headers)
      response = connection.getresponse()
      return response.status, response.read(), dict(response.getheaders())
    finally:
      connection.close()

  def post(self, path, payload, *, headers=None, local=False):
    merged = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{self.server.server_port}'}
    merged.update(headers or {})
    return self.request(path, method='POST', body=json.dumps(payload), headers=merged, local=local)

  def login(self, password='password123'):
    status, body, headers = self.post('/api/auth/login', {'password': password})
    return status, body, headers.get('Set-Cookie', '')

  def test_static_assets_revalidate_and_compress_without_caching_private_api(self):
    import gzip
    path = '/vendor/vue/vue.esm-browser.js'
    status, plain, headers = self.request(path)
    self.assertEqual(status, 200)
    self.assertEqual(headers['Cache-Control'], 'private, max-age=0, must-revalidate')
    status, body, unchanged = self.request(path, headers={'If-None-Match': headers['ETag']})
    self.assertEqual((status, body), (304, b''))
    self.assertEqual(unchanged['ETag'], headers['ETag'])
    status, packed, compressed = self.request(path, headers={'Accept-Encoding': 'gzip'})
    self.assertEqual(status, 200)
    self.assertEqual(gzip.decompress(packed), plain)
    self.assertLess(len(packed), len(plain) / 2)
    self.assertNotEqual(compressed['ETag'], headers['ETag'])
    self.assertEqual(compressed['Vary'], 'Accept-Encoding')
    self.assertEqual(self.request('/api/auth/session')[2]['Cache-Control'], 'no-store')
    self.assertEqual(self.request(path, headers={'Accept-Encoding': 'gzip;q=0'})[1], plain)

  def test_passwordless_boundary_requires_local_peer_destination_and_no_proxy_headers(self):
    for peer, destination in (('127.0.0.1', '127.0.0.1'), ('192.168.3.2', '192.168.50.20'),
                              ('10.1.2.3', '10.1.2.4'), ('172.16.0.2', '172.31.0.3'),
                              ('169.254.1.2', '169.254.1.3')):
      with self.subTest(peer=peer, destination=destination):
        self.assertTrue(direct_local_connection(peer, destination, {}))
    for peer, destination in (('8.8.8.8', '192.168.50.20'), ('192.168.3.2', '8.8.8.8'),
                              ('100.64.1.2', '100.64.1.3'), ('172.32.0.2', '172.16.0.3'),
                              ('invalid', '127.0.0.1'), ('::1', '127.0.0.1'), ('127.0.0.1', '0.0.0.0')):
      with self.subTest(peer=peer, destination=destination):
        self.assertFalse(direct_local_connection(peer, destination, {}))
    for header in ('Forwarded', 'Via', 'X-Forwarded-For', 'X-Forwarded-Host', 'X-Real-IP',
                   'X-Client-IP', 'X-Original-Forwarded-For', 'CF-Connecting-IP', 'True-Client-IP'):
      with self.subTest(header=header):
        self.assertFalse(direct_local_connection('127.0.0.1', '127.0.0.1', {header: ''}))

  def test_local_bootstrap_never_requires_or_reads_password(self):
    with mock.patch.object(self.owner, 'status', side_effect=AssertionError('Local access read credentials')), \
         mock.patch.object(self.owner, 'current_generation', side_effect=AssertionError('Local access read credentials')):
      status, body, headers = self.request('/api/auth/session', local=True)
      self.assertEqual(status, 200)
      self.assertEqual(json.loads(body), {'authenticated': True, 'state': 'configured', 'localAccess': True})
      cookie = headers['Set-Cookie'].split(';', 1)[0]
      self.assertIn('HttpOnly', headers['Set-Cookie'])
      self.assertIn('SameSite=Strict', headers['Set-Cookie'])
      self.assertEqual(self.request('/api/system/monitor', local=True, headers={'Cookie': cookie})[0], 200)
      reused = self.request('/api/auth/session', local=True, headers={'Cookie': cookie})[2]['Set-Cookie']
      self.assertEqual(reused.split(';', 1)[0], cookie)
      self.assertEqual(self.post('/api/auth/logout', {}, local=True, headers={'Cookie': cookie})[0], 200)
    self.assertFalse(self.owner.root.exists())
    self.assertEqual(self.request('/api/system/monitor', local=True, headers={'Cookie': cookie})[0], 401)

  def test_local_sessions_expire_restart_and_cannot_authenticate_forwarded_access(self):
    cookie = self.request('/api/auth/session', local=True)[2]['Set-Cookie'].split(';', 1)[0]
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': cookie})[0], 503)
    self.assertTrue(self.owner.configure('password123', lambda: True))
    self.assertEqual(self.request('/api/system/monitor', local=True, headers={'Cookie': cookie})[0], 200)
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': cookie})[0], 401)
    self.assertEqual(json.loads(self.request('/api/auth/session', headers={'Cookie': cookie})[1]),
                     {'authenticated': False, 'state': 'configured', 'localAccess': False})
    self.now += 1800
    self.assertEqual(self.request('/api/system/monitor', local=True, headers={'Cookie': cookie})[0], 401)
    refreshed = self.request('/api/auth/session', local=True, headers={'Cookie': cookie})[2]['Set-Cookie'].split(';', 1)[0]
    self.assertNotEqual(refreshed, cookie)
    self.assertTrue(self.owner.remove(lambda: True))
    self.assertEqual(self.request('/api/system/monitor', local=True, headers={'Cookie': refreshed})[0], 200)
    self.stop()
    self.start()
    self.assertEqual(self.request('/api/system/monitor', local=True, headers={'Cookie': refreshed})[0], 401)

  def test_local_bootstrap_does_not_reset_remote_login_throttle(self):
    self.assertTrue(self.owner.configure('password123', lambda: True))
    for _ in range(5):
      self.assertEqual(self.login('wrong-password')[0], 401)
    cookie = self.request('/api/auth/session', local=True)[2]['Set-Cookie'].split(';', 1)[0]
    self.assertEqual(self.request('/api/system/monitor', local=True, headers={'Cookie': cookie})[0], 200)
    self.assertEqual(self.login()[0], 429)

  def test_local_session_keeps_host_origin_and_source_guards_for_driving_preferences(self):
    from openpilot.common.params import Params
    from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway
    from openpilot.starpilot.ui.appearance_preferences import read_visibility

    class Context:
      value = AuthorityContext(True, None, None)

      def sample(inner):
        return inner.value

    context = Context()
    params = Params(str(Path(self.directory.name) / 'params'))
    gateway = SettingsGateway(params, context)
    self.stop()
    self.server = make_server(port=0, owner=self.owner, monitor=self.monitor, settings=gateway, clock=lambda: self.now)
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    self.worker.start()
    cookie = self.request('/api/auth/session', local=True)[2]['Set-Cookie'].split(';', 1)[0]
    headers = {'Cookie': cookie}
    self.assertEqual(self.request('/api/auth/session', local=True, headers={'Host': 'device.example'})[0], 403)
    for change in ('saved', 'parked', 'none'):
      with self.subTest(change=change):
        Path(params.get_param_path('SignalMetrics')).unlink(missing_ok=True)
        context.value = AuthorityContext(True, None, None)
        page = json.loads(self.request('/api/settings/pages/appearance', local=True, headers=headers)[1])
        index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'C4 amber signal border')
        payload = {'view': page['view'], 'row': index, 'value': 'On'}
        self.assertEqual(self.post('/api/settings/preview', payload, local=True,
                                   headers=headers | {'Origin': 'http://other.example'})[0], 403)
        status, body, _ = self.post('/api/settings/preview', payload, local=True, headers=headers)
        self.assertEqual(status, 200)
        intent = json.loads(body)['intent']
        if change == 'saved':
          Path(params.get_param_path('SignalMetrics')).write_bytes(b'0')
        elif change == 'parked':
          context.value = AuthorityContext(False, None, None)
        status, _, _ = self.post('/api/settings/confirm', {'intent': intent, 'confirmed': True}, local=True, headers=headers)
        self.assertEqual(status, 409 if change == 'saved' else 200)
        self.assertIs(read_visibility(params, 'SignalMetrics').value, change != 'saved')

  def test_setup_login_monitor_logout_and_no_secret_exposure(self):
    self.assertEqual(json.loads(self.request('/api/auth/session')[1])['state'], 'setup_required')
    self.assertEqual(self.request('/api/system/monitor')[0], 503)
    self.assertEqual(self.samples, 0)
    self.assertTrue(self.owner.configure('password123', lambda: True))
    self.assertEqual(self.request('/api/system/monitor')[0], 401)
    self.assertEqual(self.samples, 0)
    self.assertEqual(self.login('bad-password')[0], 401)
    status, body, cookie = self.login()
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(body), {'authenticated': True})
    self.assertIn('HttpOnly', cookie)
    self.assertIn('SameSite=Strict', cookie)
    self.assertIn('Max-Age=1800', cookie)
    self.assertNotIn('password123', cookie)
    token = cookie.split(';', 1)[0]
    self.assertTrue(json.loads(self.request('/api/auth/session', headers={'Cookie': token})[1])['authenticated'])
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': token})[0], 200)
    self.assertEqual(self.samples, 1)
    logout_status, _, logout_headers = self.post('/api/auth/logout', {}, headers={'Cookie': token})
    self.assertEqual(logout_status, 200)
    self.assertIn('Max-Age=0', logout_headers['Set-Cookie'])
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': token})[0], 401)
    self.assertEqual(self.samples, 1)

  def test_idle_connection_and_incomplete_body_do_not_block_other_requests(self):
    for pending in (b'', ('POST /api/auth/login HTTP/1.1\r\n' +
                          f'Host: 127.0.0.1:{self.server.server_port}\r\n' +
                          f'Origin: http://127.0.0.1:{self.server.server_port}\r\n' +
                          'Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{').encode()):
      with self.subTest(pending=bool(pending)), socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2) as held:
        if pending:
          held.sendall(pending)
        self.assertEqual(self.request('/api/auth/session')[0], 200)
        self.assertEqual(self.request('/api/system/monitor')[0], 503)
        self.assertEqual(self.samples, 0)

  def test_idle_connections_expire_and_capacity_recovers(self):
    # Observe accepted handlers so this exercises the socket limit, not backlog timing.
    condition = threading.Condition()
    accepted = 0
    finished = 0
    setup = self.server.RequestHandlerClass.setup
    finish = self.server.RequestHandlerClass.finish

    def observed_setup(handler):
      nonlocal accepted
      setup(handler)
      with condition:
        accepted += 1
        condition.notify_all()

    def observed_finish(handler):
      nonlocal finished
      try:
        finish(handler)
      finally:
        with condition:
          finished += 1
          condition.notify_all()

    held = []
    with mock.patch.object(self.server.RequestHandlerClass, 'setup', observed_setup), \
         mock.patch.object(self.server.RequestHandlerClass, 'finish', observed_finish), \
         mock.patch.object(self.server, 'loopback', return_value=False):
      try:
        for _ in range(self.server.MAX_CONNECTIONS):
          held.append(socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2))
        with condition:
          self.assertTrue(condition.wait_for(lambda: accepted == self.server.MAX_CONNECTIONS, timeout=2))
        admission_started = threading.Event()
        process_request = self.server.process_request

        def waiting_request(request, address):
          admission_started.set()
          process_request(request, address)

        with mock.patch.object(self.server, 'process_request', side_effect=waiting_request), ThreadPoolExecutor(max_workers=1) as executor:
          opening = executor.submit(self.request, '/js/boot.js')
          self.assertTrue(admission_started.wait(timeout=2))
          with self.assertRaises(FutureTimeoutError):
            opening.result(timeout=0.05)
          held.pop().close()
          status, body, _ = opening.result(timeout=2)
          self.assertEqual(status, 200)
          self.assertIn(b'import("./app.js")', body)
      finally:
        for connection in held:
          connection.close()
      with condition:
        self.assertTrue(condition.wait_for(lambda: finished >= self.server.MAX_CONNECTIONS, timeout=2))
    self.assertEqual(self.request('/api/auth/session')[0], 200)
    with mock.patch.object(_LocalHTTPServer, 'REQUEST_TIMEOUT', 0.05):
      with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2) as idle:
        self.assertEqual(idle.recv(1), b'')
    self.assertEqual(self.request('/api/auth/session')[0], 200)

  def test_concurrent_login_attempts_share_the_failure_budget(self):
    self.assertTrue(self.owner.configure('password123', lambda: True))
    with mock.patch.object(self.owner, 'authenticate_generation', wraps=self.owner.authenticate_generation) as verifier:
      with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(lambda _: self.login('wrong-password')[0], range(6)))
      self.assertEqual(sorted(statuses), [401] * 5 + [429])
      self.assertEqual(verifier.call_count, 5)
    self.assertEqual(self.login()[0], 429)
    self.now += 30
    self.assertEqual(self.login()[0], 200)

  def test_parallel_monitor_requests_serialize_shared_sample_history(self):
    self.assertTrue(self.owner.configure('password123', lambda: True))
    cookie = self.login()[2].split(';', 1)[0]
    active = 0
    maximum = 0
    count_lock = threading.Lock()

    def sample():
      nonlocal active, maximum
      with count_lock:
        active += 1
        maximum = max(maximum, active)
      threading.Event().wait(0.02)
      with count_lock:
        active -= 1
      return {'mode': 'local-runtime', 'schemaVersion': 1}

    with mock.patch.object(self.monitor, 'sample', side_effect=sample):
      with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: self.request('/api/system/monitor', headers={'Cookie': cookie})[0], range(4)))
    self.assertEqual(statuses, [200] * 4)
    self.assertEqual(maximum, 1)

  def test_expiry_restart_and_credential_change_revoke_session(self):
    self.assertTrue(self.owner.configure('password123', lambda: True))
    cookie = self.login()[2].split(';', 1)[0]
    self.now += 1800
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': cookie})[0], 401)
    cookie = self.login()[2].split(';', 1)[0]
    self.assertTrue(self.owner.remove(lambda: True))
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': cookie})[0], 503)
    self.assertTrue(self.owner.configure('newpassword123', lambda: True))
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': cookie})[0], 401)
    fresh = self.login('newpassword123')[2].split(';', 1)[0]
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': fresh})[0], 200)
    self.stop()
    self.start()
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': fresh})[0], 401)

  def test_corrupt_credential_record_invalidates_session(self):
    self.assertTrue(self.owner.configure('password123', lambda: True))
    cookie = self.login()[2].split(';', 1)[0]
    record = self.owner.root / self.owner.FILE
    record.write_text('{}')
    self.assertEqual(json.loads(self.request('/api/auth/session', headers={'Cookie': cookie})[1])['state'], 'unavailable')
    self.assertEqual(self.request('/api/system/monitor', headers={'Cookie': cookie})[0], 503)
    self.assertEqual(self.login()[0], 503)
    self.assertEqual(self.samples, 0)

  def test_host_origin_json_size_methods_and_login_throttle(self):
    self.assertTrue(self.owner.configure('password123', lambda: True))
    self.assertEqual(self.request('/api/system/monitor', headers={'Host': 'other.example'})[0], 403)
    self.assertEqual(self.post('/api/auth/login', {'password': 'password123'}, headers={'Origin': 'https://other.example'})[0], 403)
    self.assertEqual(self.request('/api/auth/login', method='POST', body='{}', headers={'Content-Type': 'application/json'})[0], 403)
    self.assertEqual(self.post('/api/auth/login', {'password': 'password123'}, headers={'Content-Type': 'text/plain'})[0], 415)
    self.assertEqual(self.post('/api/auth/login', {'password': 'x' * 5000})[0], 413)
    self.assertEqual(self.request('/api/auth/login', method='OPTIONS')[0], 403)
    self.assertEqual(self.post('/api/vehicle', {})[0], 405)
    self.assertEqual(self.request('/api/vehicle')[0], 401)
    self.assertEqual(self.request('/%2e%2e/server.py')[0], 404)
    for _ in range(5):
      self.assertEqual(self.login('wrong-password')[0], 401)
    self.assertEqual(self.login()[0], 429)
    self.now += 30
    self.assertEqual(self.login()[0], 200)
    self.assertEqual(self.samples, 0)


if __name__ == '__main__':
  unittest.main()
