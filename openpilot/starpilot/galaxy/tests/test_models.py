"""Read-only Model page through actual authenticated loopback HTTP."""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.models.manager import ModelManager, preferences
from openpilot.starpilot.models.jetlink_settings import JetlinkSettings


PAYLOAD = {"schemaVersion": 1, "catalog": [{"id": "bundled-current", "name": "Bundled driving model",
                                             "selectable": True}], "requestedId": "bundled-current",
           "loadedId": None, "variant": None, "health": "unavailable", "fallbackReason": None,
           "artifactSha256": None, "pendingNextStart": False}


class ModelHttpTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.owner = GalaxyAccessOwner(Path(self.temp.name) / "access")
    self.calls = 0
    self.entered = threading.Event()
    self.release = threading.Event()

    class Source:
      def json(inner):
        self.calls += 1
        if self.calls == 2:
          self.entered.set()
          self.release.wait(timeout=2)
        return PAYLOAD

    self.server = make_server(port=0, owner=self.owner, models=Source())
    self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    self.thread.start()
    self.addCleanup(self.stop)

  def stop(self):
    self.release.set()
    self.server.shutdown()
    self.thread.join(timeout=2)
    self.server.server_close()

  def request(self, path, method="GET", body=None, headers=None):
    # Exercise the password-backed forwarded path.
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
    try:
      connection.request(method, path, body=body, headers={'Forwarded': 'for=203.0.113.8', **(headers or {})})
      response = connection.getresponse()
      return response.status, response.read(), dict(response.getheaders())
    finally:
      connection.close()

  def post(self, path, payload, cookie=""):
    headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.server.server_port}"}
    if cookie:
      headers["Cookie"] = cookie
    return self.request(path, "POST", json.dumps(payload), headers)

  def test_auth_and_session_revocation_during_sampling(self):
    self.assertEqual(self.request("/api/models/status")[0], 503)
    self.assertEqual(self.calls, 0)
    self.assertTrue(self.owner.configure("password123", lambda: True))
    self.assertEqual(self.request("/api/models/status")[0], 401)
    self.assertEqual(self.calls, 0)
    status, _, headers = self.post("/api/auth/login", {"password": "password123"})
    self.assertEqual(status, 200)
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    status, body, _ = self.request("/api/models/status", headers={"Cookie": cookie})
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(body), PAYLOAD)

    result = []
    pending = threading.Thread(target=lambda: result.append(self.request("/api/models/status", headers={"Cookie": cookie})[0]))
    pending.start()
    self.assertTrue(self.entered.wait(timeout=2))
    self.assertEqual(self.post("/api/auth/logout", {}, cookie)[0], 200)
    self.release.set()
    pending.join(timeout=2)
    self.assertEqual(result, [401])
    self.assertEqual(self.request("/api/models/status", headers={"Cookie": cookie})[0], 401)


class LocalModelManagerHttpTest(unittest.TestCase):
  def test_local_actions_need_no_password_and_recheck_parked_state(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      parked = [True]
      class LinkSettings(JetlinkSettings):
        mode = "off"

        def snapshot(self):
          return {"mode": self.mode, "supported": True, "chestnut": False}

        def pick(self):
          return None

        def configure(self, payload):
          self.mode = payload["mode"]
          return {"message": "Saved"}

      link = LinkSettings()
      def offline(url, timeout):
        raise OSError('HTTP fixture disables network')
      manager = ModelManager(root=root / 'models', parked=lambda: parked[0], gpu_present=lambda: False, jetlink=link, opener=offline)
      server = make_server(port=0, owner=GalaxyAccessOwner(root / 'access'), model_manager=manager)
      thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
      thread.start()
      cookie = ['']

      def request(path, payload=None, forwarded=False):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        headers = {'Origin': f'http://127.0.0.1:{server.server_port}', 'Content-Type': 'application/json'}
        if cookie[0]:
          headers['Cookie'] = cookie[0]
        if forwarded:
          headers['Forwarded'] = 'for=203.0.113.8'
        try:
          connection.request('GET' if payload is None else 'POST', path,
                             body=None if payload is None else json.dumps(payload), headers=headers)
          response = connection.getresponse()
          if response.getheader('Set-Cookie'):
            cookie[0] = response.getheader('Set-Cookie').split(';', 1)[0]
          return response.status, json.loads(response.read())
        finally:
          connection.close()

      try:
        self.assertEqual(request('/api/auth/session')[0], 200)
        status, snapshot = request('/api/models/manager')
        self.assertEqual(status, 200)
        self.assertFalse(snapshot['randomizer'])
        self.assertEqual(request('/api/models/jetlink', {'mode': 'usb'})[0], 200)
        self.assertEqual(link.mode, 'usb')
        status, snapshot = request('/api/models/manager')
        self.assertEqual(status, 200)
        self.assertTrue(snapshot['jetlinkBig'])
        self.assertEqual(snapshot['activeBigModel'], '')
        status, laboratory = request('/api/models/laboratory')
        self.assertEqual(status, 200)
        self.assertTrue(laboratory['runtimeSupported'])
        self.assertFalse(laboratory['runtime']['active'])
        self.assertEqual(laboratory['summary']['published'], 91)
        pair = {'enabled': False, 'lateralModel': 'gwm8223', 'longitudinalModel': 'sc23'}
        self.assertEqual(request('/api/models/laboratory', pair)[0], 200)
        self.assertEqual(request('/api/models/laboratory', {**pair, 'enabled': True})[0], 409)
        self.assertEqual(request('/api/models/laboratory/download', {'model': 'gwm8223'})[0], 200)
        manager.worker.join(3)
        self.assertFalse(manager.worker.is_alive())
        self.assertEqual(manager.snapshot()['progress'], 'HTTP fixture disables network')
        self.assertEqual(request('/api/models/laboratory/delete', {'model': 'gwm8223'})[0], 200)
        self.assertEqual(request('/api/models/preferences', {'randomizer': True, 'blacklistedModels': ['gwm8223']})[0], 200)
        self.assertEqual(preferences(manager.root)['blacklistedModels'], ['gwm8223'])
        self.assertEqual(request('/api/models/active', {'profile': 'small', 'model': 'bundled-current'})[0], 409)
        parked[0] = False
        self.assertEqual(request('/api/models/jetlink', {'mode': 'off'})[0], 409)
        self.assertEqual(link.mode, 'usb')
        self.assertEqual(request('/api/models/laboratory', pair)[0], 409)
        self.assertEqual(request('/api/models/laboratory/delete', {'model': 'gwm8223'})[0], 409)
        self.assertEqual(request('/api/models/preferences', {'randomizer': False})[0], 409)
        self.assertTrue(preferences(manager.root)['randomizer'])
        self.assertEqual(request('/api/models/preferences', {'userFavorites': ['pop223']})[0], 200)
        self.assertEqual(request('/api/models/jetlink', {'mode': 'off'}, forwarded=True)[0], 503)
        self.assertEqual(link.mode, 'usb')
        self.assertEqual(request('/api/models/manager', forwarded=True)[0], 503)
        self.assertEqual(request('/api/models/laboratory', forwarded=True)[0], 503)
      finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


if __name__ == "__main__":
  unittest.main()
