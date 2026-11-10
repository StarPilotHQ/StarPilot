"""Real loopback requests: cookie isolation, safe retry and BLE restrictions."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor'))
from proxy import GalaxyProxy, validate_target


class Fixture(BaseHTTPRequestHandler):
  def log_message(self, format: str, *args: object) -> None:  # noqa: A002  # stdlib override
    pass

  def do_GET(self):
    if self.path == '/api/auth/session':
      self.server.auths += 1
      self.server.cookies.add('galaxy_session=' + str(self.server.auths))
      return self.reply(200, {'authenticated': True, 'localAccess': True},
                        {'Set-Cookie': 'galaxy_session=' + str(self.server.auths) + '; HttpOnly; Path=/'})
    self.check()

  def do_POST(self): self.check()

  def check(self):
    cookie = self.headers.get('Cookie')
    if cookie not in self.server.cookies:
      return self.reply(401, {'error': 'Refresh the local Galaxy session'})
    if self.command == 'POST':
      if self.headers.get('Origin') != 'http://127.0.0.1:' + str(self.server.server_port):
        return self.reply(403, {'error': 'bad origin'})
      if self.headers.get('Content-Type') != 'application/json':
        return self.reply(400, {'error': 'bad content type'})
      self.server.writes += 1
      self.rfile.read(int(self.headers.get('Content-Length', '0')))
    self.server.seen.append((self.path, dict(self.headers)))
    return self.reply(200, {'ok': True})

  def reply(self, status, data, headers=None):
    raw = json.dumps(data).encode()
    self.send_response(status)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Content-Length', str(len(raw)))
    for key, value in (headers or {}).items():
      self.send_header(key, value)
    self.end_headers()
    self.wfile.write(raw)


class ProxyTest(unittest.TestCase):
  def setUp(self):
    self.server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    self.server.cookies, self.server.auths, self.server.writes, self.server.seen = set(), 0, 0, []
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    self.worker.start()
    self.proxy = GalaxyProxy(self.server.server_port)
    self.addCleanup(self.stop)

  def stop(self):
    self.server.shutdown()
    self.worker.join()
    self.server.server_close()

  def request(self, path='/api/settings/pages/hub', method='GET', body='', headers=None):
    return self.proxy.handle({'id': 'test', 'path': path, 'method': method,
                              'body': base64.b64encode(body.encode()).decode(), 'headers': headers or {}})

  def test_origin_cookie_forgery_and_mutation_headers(self):
    result = self.request('/api/settings/preview', 'POST', '{}',
                          {'Cookie': 'forged', 'Origin': 'https://evil.example', 'Content-Type': 'text/plain', 'Forwarded': 'for=evil'})
    self.assertEqual(result['status'], 200)
    self.assertEqual(self.server.writes, 1)
    self.assertEqual(self.server.auths, 1)
    self.assertNotIn('set-cookie', result['headers'])
    self.assertNotIn('Forwarded', self.server.seen[-1][1])

  def test_only_explicit_expiry_refreshes_once(self):
    self.assertEqual(self.request()['status'], 200)
    self.server.cookies.clear()
    self.assertEqual(self.request('/api/settings/confirm', 'POST', '{"intent":"x"}')['status'], 200)
    self.assertEqual(self.server.auths, 2)
    self.assertEqual(self.server.writes, 1)
    self.assertEqual(self.request()['status'], 200)
    self.assertEqual(self.server.auths, 2)

  def test_network_failure_does_not_replay_mutation(self):
    self.request()
    with patch.object(self.proxy, 'send', side_effect=TimeoutError) as send:
      self.assertEqual(self.request('/api/settings/confirm', 'POST', '{}')['status'], 502)
      self.assertEqual(send.call_count, 1)

  def test_wifi_features_never_reach_server(self):
    for path in ('/api/recordings', '/api/replay/status', '/api/cameras/status', '/api/maps/tiles/a',
                 '/api/phone-internet/start', '/api/starport/upload', '/api/starport/apk/latest', '/api/auth/login', '/api/auth/logout',
                 '/api/navigation/map/tiles/1/2/3', '/api/companion/uplink', '/api/companion/app.apk', '/api/spotify/upload'):
      self.assertEqual(self.request(path)['status'], 501, path)
    self.assertEqual(self.server.auths, 0)

  def test_phone_gps_is_carried_over_bluetooth(self):
    self.request()
    self.assertEqual(self.request('/api/companion/location', 'POST', '{}')['status'], 200)
    self.assertEqual(self.server.seen[-1][0], '/api/companion/location')

  def test_runtime_identity_and_isolated_handshake_cookies(self):
    self.assertEqual(self.request('/data/runtime.json')['status'], 200)
    second = self.proxy.new_session()
    result = second.handle({'id': 'b', 'path': '/api/device/state'})
    self.assertEqual(result['status'], 200)
    self.assertNotEqual(self.proxy.cookie, second.cookie)
    self.assertEqual(self.server.auths, 2)

  def test_navigation_uses_authenticated_bluetooth_post(self):
    result = self.request('/api/companion/navigation', 'POST', '{"active":false}')
    self.assertEqual(result['status'], 200)
    path, headers = self.server.seen[-1]
    self.assertEqual(path, '/api/companion/navigation')
    self.assertEqual(headers['Content-Type'], 'application/json')
    self.assertEqual(headers['Origin'], self.proxy.base)

  def test_rejects_path_traversal(self):
    for path in ('https://evil/api/x', '//evil/api/x', '/api/../x', '/api/%252e%252e/x', '/api/%2e%2e/x', '/index.html',
                 '/data/catalog.json', '/api/x#secret', '/api/%0a'):
      with self.assertRaises(ValueError, msg=path):
        validate_target(path)


if __name__ == '__main__':
  unittest.main()
