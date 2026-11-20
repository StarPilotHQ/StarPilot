import http.client
import json
from pathlib import Path
import threading
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server
from opendbc.car.structs import car
from copy import deepcopy
from dataclasses import replace
import unittest

from openpilot.starpilot.galaxy.toggle_backup import ToggleBackup
from openpilot.starpilot.galaxy.onroad_layout import OnroadLayoutOwner
from openpilot.starpilot.galaxy.settings import SettingsChanged
from openpilot.starpilot.galaxy.tests import test_settings as fixtures


class ToggleBackupTest(unittest.TestCase):
  def setUp(self):
    self.fixture = fixtures.SettingsGatewayTest()
    self.fixture.setUp()
    self.addCleanup(self.fixture.doCleanups)
    cp = car.CarParams.new_message(carFingerprint='TOYOTA_COROLLA_TSS2', brand='toyota',
      openpilotLongitudinalControl=True, pcmCruise=False, notCar=False, dashcamOnly=False, passive=False,
      steerControlType=car.CarParams.SteerControlType.torque,
      transmissionType=car.CarParams.TransmissionType.automatic)
    self.fixture.context.value = replace(self.fixture.context.value, cp=cp, cp_raw=cp.to_bytes())
    self.gateway = self.fixture.gateway
    self.layout = OnroadLayoutOwner(self.fixture.params, lambda: self.fixture.context.value.parked)
    self.owner = ToggleBackup(self.gateway, self.layout)
    self.identity = self.fixture.session, self.fixture.generation

  def restore(self, backup, authorized=lambda: True):
    return self.owner.restore(backup, self.identity, authorized=authorized)

  def test_export_and_roundtrip_preserve_settings_and_exclude_secrets(self):
    self.fixture.params.put('GithubSshKeys', 'private', block=True)
    backup = self.owner.export()
    self.assertGreater(len(backup['settings']), 20)
    self.assertIsNotNone(backup['layout'])
    self.assertNotIn('GithubSshKeys', [entry['key'] for entry in backup['settings']])
    result = self.restore(backup)
    self.assertTrue(result['complete'])
    self.assertEqual(result['restored'], 0)
    self.assertGreater(result['unchanged'], 20)
    self.assertEqual(self.fixture.params.get('GithubSshKeys'), 'private')

  def test_portable_layout_excludes_rotation_and_restore_disables_it(self):
    snapshot = self.layout.snapshot()
    wheel = snapshot['document']['layouts']['large']['steering_wheel']
    wheel.update(x=1300, y=100, rotate=True)
    saved = self.layout.save({key: snapshot[key] for key in ('document', 'revision')}, session_valid=lambda: True)
    self.assertTrue(saved['document']['layouts']['large']['steering_wheel']['rotate'])
    backup = self.owner.export()
    self.assertNotIn('rotate', backup['layout']['layouts']['large']['steering_wheel'])
    self.assertTrue(self.restore(backup)['complete'])
    restored = self.layout.snapshot()['document']['layouts']['large']['steering_wheel']
    self.assertFalse(restored.get('rotate', False))
    self.assertEqual((restored['x'], restored['y']), (1300, 100))

  def test_restore_uses_existing_owner_and_validates_before_writes(self):
    self.fixture.params.put_bool('AlwaysAllowUploads', False, block=True)
    backup = self.owner.export()
    saved = next(entry for entry in backup['settings'] if entry['key'] == 'AlwaysAllowUploads')
    self.assertEqual(saved['value'], 'Off')
    self.fixture.params.put_bool('AlwaysAllowUploads', True, block=True)
    invalid = deepcopy(backup)
    next(entry for entry in invalid['settings'] if entry['key'] == 'AlwaysAllowUploads')['value'] = 'invalid'
    with self.assertRaises(SettingsChanged):
      self.restore(invalid)
    self.assertTrue(self.fixture.params.get_bool('AlwaysAllowUploads'))
    result = self.restore(backup)
    self.assertTrue(result['complete'])
    self.assertEqual(result['restored'], 1)
    self.assertFalse(self.fixture.params.get_bool('AlwaysAllowUploads'))

  def test_parked_session_units_and_duplicate_guards(self):
    backup = self.owner.export()
    with self.assertRaises(SettingsChanged):
      self.restore(backup, authorized=lambda: False)
    self.fixture.context.value = replace(self.fixture.context.value, parked=False)
    with self.assertRaises(SettingsChanged):
      self.restore(backup)
    self.fixture.context.value = replace(self.fixture.context.value, parked=True, metric=True)
    with self.assertRaises(ValueError):
      self.restore(backup)
    self.fixture.context.value = replace(self.fixture.context.value, metric=False)
    backup['settings'].append(backup['settings'][0])
    with self.assertRaises(ValueError):
      self.restore(backup)

  def test_unknown_operational_keys_are_skipped_and_layout_is_validated(self):
    backup = self.owner.export()
    backup['settings'].append({'page': 'data', 'key': 'DoReboot', 'value': 'On'})
    result = self.restore(backup)
    self.assertTrue(result['complete'])
    self.assertIn('DoReboot', result['skipped'])
    self.assertFalse(self.fixture.params.get_bool('DoReboot'))
    backup['layout'] = {'version': 999}
    with self.assertRaises(ValueError):
      self.restore(backup)

  def test_authenticated_http_export_restore_and_origin_guard(self):
    access = GalaxyAccessOwner(Path(self.fixture.params.get_param_path('Version')).parent.parent / 'access')
    self.assertTrue(access.configure('password123', lambda: True))
    server = make_server(port=0, owner=access, settings=self.gateway, layouts=self.layout)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    worker.start()
    def stop():
      server.shutdown()
      worker.join(timeout=2)
      server.server_close()
    self.addCleanup(stop)
    def request(path, payload=None, cookie='', origin=None):
      connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
      headers = {'Cookie': cookie}
      if payload is not None:
        headers.update({'Content-Type':'application/json', 'Origin':origin or f'http://127.0.0.1:{server.server_port}'})
      try:
        connection.request('POST' if payload is not None else 'GET', path,
          body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read()), dict(response.getheaders())
      finally:
        connection.close()
    self.assertEqual(request('/api/settings/backup')[0], 401)
    status, _, headers = request('/api/auth/login', {'password':'password123'})
    self.assertEqual(status, 200)
    cookie = headers['Set-Cookie'].split(';',1)[0]
    status, backup, _ = request('/api/settings/backup', cookie=cookie)
    self.assertEqual(status, 200)
    self.assertEqual(backup['format'], 'galaxy-toggles')
    self.assertEqual(request('/api/settings/restore', backup, cookie, 'http://foreign.test')[0], 403)
    status, result, _ = request('/api/settings/restore', backup, cookie)
    self.assertEqual(status, 200)
    self.assertTrue(result['complete'])
    self.fixture.context.value = replace(self.fixture.context.value, parked=False)
    self.assertEqual(request('/api/settings/restore', backup, cookie)[0], 409)
