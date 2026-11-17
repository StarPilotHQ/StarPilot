"""Exercise hostname admission through the real HTTP/session handler."""

import http.client
import json
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.starpilot.galaxy.http_server import ReverseProxy
from openpilot.starpilot.galaxy.server import make_remote_server, make_server
from openpilot.starpilot.galaxy.tests import test_auth_runtime


class HostnameRuntimeTest(unittest.TestCase):
  def setUp(self):
    self.runtime = test_auth_runtime.AuthRuntimeTest()
    # These HTTP/session tests have no vehicle message source or parked writes.
    with patch.object(test_auth_runtime, 'make_server', lambda **args: make_server(parked=lambda: False, **args)):
      self.runtime.setUp()
    self.addCleanup(self.runtime.doCleanups)
    self.runtime.stop()
    source = SimpleNamespace(names=lambda _: ('comma.home.example',))
    self.runtime.server = make_server(port=0, owner=self.runtime.owner, monitor=self.runtime.monitor,
                                      local_hostnames=source, clock=lambda: self.runtime.now, parked=lambda: False)
    self.runtime.worker = threading.Thread(target=self.runtime.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    self.runtime.worker.start()
    self.host = f'comma.home.example:{self.runtime.server.server_port}'

  def test_hostname_bootstrap_and_same_origin_logout_without_password(self):
    status, body, headers = self.runtime.request('/api/auth/session', local=True, headers={'Host': self.host})
    self.assertEqual(status, 200)
    self.assertTrue(json.loads(body)['localAccess'])
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    self.assertFalse(self.runtime.owner.root.exists())
    headers = {'Host': self.host, 'Cookie': cookie}
    self.assertEqual(self.runtime.request('/api/system/monitor', local=True, headers=headers)[0], 200)
    for origin in ('http://evil.example', 'null', 'http://comma.home.example', 'https://' + self.host):
      self.assertEqual(self.runtime.post('/api/auth/logout', {}, local=True, headers=headers | {'Origin': origin})[0], 403)
    self.assertEqual(self.runtime.request('/api/auth/logout', method='POST', body='{}', local=True,
                                          headers=headers | {'Content-Type': 'application/json'})[0], 403)
    self.assertEqual(self.runtime.post('/api/auth/logout', {}, local=True, headers=headers | {'Origin': 'http://' + self.host})[0], 200)

  def test_rebinding_matching_origin_and_forwarded_local_cookie_are_denied(self):
    cookie = self.runtime.request('/api/auth/session', local=True, headers={'Host': self.host})[2]['Set-Cookie'].split(';', 1)[0]
    for host in ('evil.example', 'comma.evil.example', 'anything.home.example'):
      authority = f'{host}:{self.runtime.server.server_port}'
      headers = {'Host': authority, 'Origin': 'http://' + authority, 'Cookie': cookie}
      self.assertEqual(self.runtime.request('/api/auth/session', local=True, headers=headers)[0], 403)
      self.assertEqual(self.runtime.post('/api/auth/logout', {}, local=True, headers=headers)[0], 403)
    headers = {'Host': self.host, 'Cookie': cookie}
    self.assertEqual(self.runtime.request('/api/system/monitor', headers=headers)[0], 503)
    self.assertTrue(self.runtime.owner.configure('password123', lambda: True))
    self.assertEqual(self.runtime.request('/api/system/monitor', headers=headers)[0], 401)

  def test_portless_manual_curl_is_only_inert_landing_read(self):
    host = 'comma.home.example'
    for method in ('GET', 'HEAD'):
      status, body, headers = self.runtime.request('/', local=True, method=method, headers={'Host': host})
      self.assertEqual(status, 200)
      self.assertNotIn('Set-Cookie', headers)
      if method == 'GET':
        self.assertIn(b'<!doctype html>', body.lower())
    for path in ('/api/auth/session', '/api/system/monitor', '/api/galaxy/pair', '/?api=1', '/js/app.js'):
      self.assertEqual(self.runtime.request(path, local=True, headers={'Host': host})[0], 403)
    self.assertEqual(self.runtime.request('/', local=True, headers={'Host': host, 'Origin': 'http://' + host})[0], 403)
    self.assertEqual(self.runtime.post('/api/auth/logout', {}, local=True, headers={'Host': host, 'Origin': 'http://' + host})[0], 403)

  def test_arbitrary_a_only_alias_only_redirects_landing_to_socket_address(self):
    port = self.runtime.server.server_port
    target = f'http://127.0.0.1:{port}/'
    for host in ('custom.home.example', f'custom.home.example:{port}', f'evil.example:{port}'):
      for method in ('GET', 'HEAD'):
        status, body, headers = self.runtime.request('/', local=True, method=method, headers={'Host': host})
        self.assertEqual((status, body), (302, b''))
        self.assertEqual(headers['Location'], target)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Set-Cookie', headers)
      for path in ('/api/auth/session', '/api/system/monitor', '/api/galaxy/pair', '/?x=1', '/js/app.js'):
        self.assertEqual(self.runtime.request(path, local=True, headers={'Host': host})[0], 403)
      self.assertEqual(self.runtime.request('/', local=True, headers={'Host': host, 'Origin': 'http://' + host})[0], 403)
      self.assertEqual(self.runtime.post('/', {}, local=True, headers={'Host': host, 'Origin': 'http://' + host})[0], 403)
      self.assertEqual(self.runtime.request('/', headers={'Host': host})[0], 403)
    for host in ('evil.example:9999', 'user@evil.example', 'evil.example/path', '10.0.2.244', 'localhost'):
      self.assertEqual(self.runtime.request('/', local=True, headers={'Host': host})[0], 403)
    # Following the redirect enters the existing literal-IP origin/session path.
    status, body, headers = self.runtime.request('/', local=True)
    self.assertEqual(status, 200)
    self.assertIn(b'<!doctype html>', body.lower())
    status, body, headers = self.runtime.request('/api/auth/session', local=True)
    self.assertEqual(status, 200)
    self.assertTrue(json.loads(body)['localAccess'])
    self.assertIn('Set-Cookie', headers)
    self.assertFalse(self.runtime.owner.root.exists())

  def test_duplicate_host_or_origin_is_not_an_allowlist_bypass(self):
    for extra in ('Host: evil.example\r\n', f'Origin: http://evil.example\r\nOrigin: http://{self.host}\r\n'):
      with socket.create_connection(('127.0.0.1', self.runtime.server.server_port), timeout=2) as connection:
        connection.sendall(f'GET /api/auth/session HTTP/1.1\r\nHost: {self.host}\r\n{extra}Connection: close\r\n\r\n'.encode())
        response = http.client.HTTPResponse(connection)
        response.begin()
        self.assertEqual(response.status, 403)
        self.assertIsNone(response.getheader('Set-Cookie'))
        response.read()

  def test_configured_proxy_peer_cannot_use_alias_redirect(self):
    self.runtime.stop()
    self.runtime.server = make_server(port=0, owner=self.runtime.owner, monitor=self.runtime.monitor,
                                      reverse_proxy=ReverseProxy('127.0.0.1', 'https://galaxy.example'), parked=lambda: False)
    self.runtime.worker = threading.Thread(target=self.runtime.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    self.runtime.worker.start()
    status, _, headers = self.runtime.request('/', local=True, headers={'Host': 'custom.home.example'})
    self.assertEqual(status, 403)
    self.assertNotIn('Location', headers)
    self.assertNotIn('Set-Cookie', headers)

  def test_remote_transport_never_uses_local_hostname_exception(self):
    remote = make_remote_server(self.runtime.server, port=0)
    thread = threading.Thread(target=remote.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
      connection = http.client.HTTPConnection('127.0.0.1', remote.server_port, timeout=2)
      try:
        for path in ('/', '/api/auth/session'):
          connection.request('GET', path, headers={'Host': f'comma.home.example:{remote.server_port}'})
          response = connection.getresponse()
          self.assertEqual(response.status, 403)
          self.assertIsNone(response.getheader('Set-Cookie'))
          self.assertIsNone(response.getheader('Location'))
          response.read()
      finally:
        connection.close()
    finally:
      remote.shutdown()
      thread.join(2)
      remote.server_close(close_sources=False)


if __name__ == '__main__':
  unittest.main()
