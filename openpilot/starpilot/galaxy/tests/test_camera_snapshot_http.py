import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.camera_snapshot import SnapshotDenied
from openpilot.starpilot.galaxy.server import make_server


class CameraSnapshotHTTPTest(unittest.TestCase):
  def test_origin_session_parked_and_after_capture_revocation(self):
    with tempfile.TemporaryDirectory() as directory:
      access = GalaxyAccessOwner(Path(directory).resolve() / 'access')
      access.configure('password123', lambda: True)
      parked = True
      calls = []
      revoke = False
      class Reader:
        def capture(self, camera, *, permitted, **_):
          nonlocal parked
          if not permitted():
            raise SnapshotDenied
          calls.append(camera)
          if revoke == "parked":
            parked = False
          elif revoke == "logout":
            self_status, _, _ = request('/api/auth/logout', cookie=cookie, payload={})
            if self_status != 200:
              raise AssertionError("logout failed")
          return b'\xff\xd8\xff\xd9'
      server = make_server(port=0, owner=access, parked=lambda: parked, camera_snapshot=Reader())
      worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
      worker.start()
      def request(path, *, cookie=None, origin=None, payload=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        headers = {'Forwarded': 'for=203.0.113.8', 'Content-Type': 'application/json',
                   'Origin': origin or f'http://127.0.0.1:{server.server_port}'}
        if cookie:
          headers['Cookie'] = cookie
        try:
          connection.request('POST', path, json.dumps({'camera': 'cabin'} if payload is None else payload), headers)
          response = connection.getresponse()
          return response.status, response.read(), dict(response.getheaders())
        finally:
          connection.close()
      try:
        route = '/api/cameras/snapshot'
        self.assertEqual(request(route)[0], 401)
        status, _, headers = request('/api/auth/login', payload={'password': 'password123'})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.assertEqual(request(route, cookie=cookie, origin='https://elsewhere.invalid')[0], 403)
        parked = False
        self.assertEqual(request(route, cookie=cookie)[0], 409)
        self.assertEqual(calls, [])
        parked = True
        status, body, headers = request(route, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/jpeg')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(body, b'\xff\xd8\xff\xd9')
        revoke = "parked"
        status, body, _ = request(route, cookie=cookie)
        self.assertEqual(status, 409)
        self.assertNotIn(b'\xff\xd8', body)
        parked = True
        revoke = "logout"
        status, body, _ = request(route, cookie=cookie)
        self.assertEqual(status, 401)
        self.assertNotIn(b'\xff\xd8', body)
      finally:
        server.shutdown()
        server.server_close()
        worker.join(2)
