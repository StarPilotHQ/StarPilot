import base64
import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.remote import RemotePairing
from openpilot.starpilot.galaxy.server import make_remote_server, make_server
from openpilot.starpilot.galaxy.software_operations import SoftwareOperations
from openpilot.starpilot.galaxy.software_status import SoftwareStatus
from openpilot.starpilot.galaxy.tests.test_software_operations import FakeProcess
from openpilot.starpilot.software.auto_install import InstallSettings


class SoftwareHttpTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    installed = root / 'installed'
    installed.mkdir()
    subprocess.run(['git', '-C', str(installed), '-c', 'init.defaultBranch=main', 'init', '-q'], check=True)
    subprocess.run(['git', '-C', str(installed), '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-qm', 'Software history test'], check=True)
    commit = subprocess.check_output(['git', '-C', str(installed), 'rev-parse', 'HEAD'], text=True).strip()
    self.params = Params(str(root / 'params'))
    for key, value in {'Version': 'test', 'GitBranch': 'main', 'GitCommit': commit,
                       'UpdaterState': 'idle', 'UpdaterTargetBranch': 'main', 'UpdaterAvailableBranches': 'main'}.items():
      self.params.put(key, value, block=True)
    self.params.put_bool('IsOffroad', True, block=True)
    self.params.put('UpdaterCurrentReleaseNotes', b'<h1>Release notes</h1><li>Useful changes</li>', block=True)
    self.process = FakeProcess()
    self.parked = True
    self.operations = SoftwareOperations(self.params, parked=lambda: self.parked, process=self.process,
                                         installed=installed, finalized=root / 'finalized', metadata=root / 'metadata.git',
                                         install_settings=InstallSettings(root / 'install-settings'))
    self.access = GalaxyAccessOwner(root / 'access')
    self.pairing = RemotePairing(root / 'pairing')
    self.local = make_server(port=0, owner=self.access, remote_pairing=self.pairing,
                             software=SoftwareStatus(self.params), software_operations=self.operations)
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

  def test_local_and_remote_history_and_immediate_preference_roundtrip(self):
    for remote in (False, True):
      with self.subTest(remote=remote):
        cookie = self.remote_cookie if remote else self.local_cookie
        status, light, _ = self.request('/api/software/status?history=0', remote=remote, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn('history', light['operations'])
        status, value, _ = self.request('/api/software/status', remote=remote, cookie=cookie)
        self.assertEqual(light['operations'], {key: entry for key, entry in value['operations'].items() if key != 'history'})
        self.assertEqual(status, 200)
        view = value['operations']
        self.assertEqual(view['history']['installed'][0]['subject'], 'Software history test')
        self.assertEqual(view['history']['currentReleaseNotes'], 'Release notes\n\nUseful changes')
        expected = view['automaticDownloads']
        payload = {'action': 'preferences', 'automaticDownloads': not expected, 'expectedAutomaticDownloads': expected}
        status, saved, _ = self.request('/api/software/action', payload=payload, remote=remote, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIs(saved['operations']['automaticDownloads'], not expected)
        self.assertIsNone(saved['operations']['request'])
        self.assertEqual(self.request('/api/software/action', payload=payload, remote=remote, cookie=cookie)[0], 409)
    self.assertEqual(self.process.sent, [])

  def test_local_and_remote_auto_install_schedule_roundtrip_requires_offroad(self):
    for remote in (False, True):
      cookie = self.remote_cookie if remote else self.local_cookie
      status, value, _ = self.request('/api/software/status?history=0', remote=remote, cookie=cookie)
      self.assertEqual(status, 200)
      payload = {'action': 'install-preferences', 'automaticInstalls': True, 'restartSchedule': '30 2 * * 1-5',
                 'expectedRevision': value['operations']['installPreferenceRevision']}
      self.parked = False
      self.assertEqual(self.request('/api/software/action', payload=payload, remote=remote, cookie=cookie)[0], 409)
      self.parked = True
      status, saved, _ = self.request('/api/software/action', payload=payload, remote=remote, cookie=cookie)
      self.assertEqual(status, 200)
      self.assertTrue(saved['operations']['automaticInstalls'])
      self.assertEqual(saved['operations']['restartSchedule'], '30 2 * * 1-5')
      self.assertEqual(self.request('/api/software/action', payload=payload, remote=remote, cookie=cookie)[0], 409)
    self.assertEqual(self.process.sent, [])
    self.assertFalse(self.params.get_bool('DoReboot'))

  def test_remote_auth_origin_and_parked_rules_cannot_be_bypassed(self):
    payload = {'action': 'preferences', 'automaticDownloads': False, 'expectedAutomaticDownloads': True}
    self.assertEqual(self.request('/api/software/status', remote=True)[0], 401)
    self.assertEqual(self.request('/api/software/status', remote=True, cookie=self.local_cookie)[0], 401)
    self.assertEqual(self.request('/api/software/action', payload=payload, remote=True,
                                  cookie=self.remote_cookie, origin='https://wrong.example')[0], 403)
    self.parked = False
    self.assertEqual(self.request('/api/software/action', payload=payload, remote=True, cookie=self.remote_cookie)[0], 409)
    self.assertFalse(Path(self.params.get_param_path('UpdaterAutomaticDownloads')).exists())
    self.assertEqual(self.process.sent, [])
