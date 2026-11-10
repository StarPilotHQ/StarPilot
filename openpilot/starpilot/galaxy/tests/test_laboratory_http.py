import base64
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.remote import RemotePairing
from openpilot.starpilot.galaxy.server import make_remote_server, make_server
from openpilot.starpilot.models.catalog import CATALOG_PATH
from openpilot.starpilot.models.manager import ModelManager, atomic_json


class LaboratoryHttpTest(unittest.TestCase):
  def setUp(self):
    unsupported = patch('openpilot.starpilot.models.laboratory.RUNTIME_QUALIFIED', False)
    unsupported.start()
    self.addCleanup(unsupported.stop)
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    self.manifest = json.loads(CATALOG_PATH.read_text())
    atomic_json(root / 'models' / 'catalog.json', self.manifest)
    manager = ModelManager(root=root / 'models', parked=lambda: True, gpu_present=lambda: False)
    self.addCleanup(manager.close)
    self.access = GalaxyAccessOwner(root / 'access')
    self.pairing = RemotePairing(root / 'pairing')
    self.local = make_server(port=0, owner=self.access, remote_pairing=self.pairing, model_manager=manager)
    self.remote = make_remote_server(self.local, port=0)
    self.servers = []
    for server in (self.local, self.remote):
      thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
      thread.start()
      self.servers.append((server, thread))
    self.addCleanup(self.close)
    self.local_cookie = self.request('/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    self.access.configure('password123', lambda: True)
    self.slug = self.pairing.pair(hashlib.sha256(b'password123').hexdigest())
    record = self.pairing.read()
    assert record is not None
    token = base64.urlsafe_b64encode(json.dumps({self.slug: record['session']}).encode()).decode().rstrip('=')
    self.remote_cookie = 'galaxy_session=' + token

  def close(self):
    for server, thread in reversed(self.servers):
      server.shutdown()
      thread.join(timeout=2)
      server.server_close()

  def request(self, path, *, payload=None, remote=False, cookie=None, origin=None):
    server = self.remote if remote else self.local
    host = f'{self.slug}.devices.local' if remote else f'127.0.0.1:{server.server_port}'
    headers = {'Host': host}
    if cookie is not None:
      headers['Cookie'] = cookie
    if payload is not None:
      headers.update({'Content-Type': 'application/json', 'Origin': origin or
                      ('https://galaxy.firestar.link' if remote else f'http://{host}')})
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    try:
      connection.request('POST' if payload is not None else 'GET', path,
                          json.dumps(payload) if payload is not None else None, headers)
      response = connection.getresponse()
      return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
      connection.close()

  def test_local_and_gateway_catalog_keep_pair_guards_and_auth(self):
    views = []
    for remote in (False, True):
      cookie = self.remote_cookie if remote else self.local_cookie
      status, view, _ = self.request('/api/models/laboratory', remote=remote, cookie=cookie)
      self.assertEqual(status, 200, view)
      self.assertEqual({r['value'] for r in view['models']}, {r['id'] for r in self.manifest['models']})
      self.assertFalse(view['runtimeSupported'])
      self.assertFalse(view['runtime']['active'])
      big = [row for row in view['models'] if not row['small']]
      self.assertTrue(big)
      self.assertTrue(all(row['modelLabEligible'] and row['modelLabChestnut'] for row in big))
      views.append(view)
      payload = {'enabled': True, 'lateralModel': 'gwm8223', 'longitudinalModel': 'sc23'}
      self.assertNotEqual(self.request('/api/models/laboratory', payload=payload, remote=remote, cookie=cookie)[0], 200)
      self.assertNotEqual(self.request('/api/models/laboratory', remote=remote)[0], 200)
      self.assertNotEqual(self.request('/api/models/laboratory', payload=payload, remote=remote, cookie=cookie,
                                       origin='https://untrusted.invalid')[0], 200)
    self.assertEqual(views[0], views[1])

  def test_runtime_capability_is_separate_from_connected_gpu_and_activation(self):
    with patch('openpilot.starpilot.models.laboratory.RUNTIME_QUALIFIED', True):
      status, view, _ = self.request('/api/models/laboratory', cookie=self.local_cookie)
    self.assertEqual(status, 200, view)
    self.assertTrue(view['runtimeSupported'])
    self.assertFalse(view['chestnutReady'])
    self.assertFalse(view['runtime']['active'])
