"""Tune files are portable data, never portable controller authority."""
from copy import deepcopy
from dataclasses import replace
import json
import http.client
from pathlib import Path
import threading
from typing import Any
from unittest.mock import patch
import unittest

from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.flm.learned_lifecycle import decode, encode
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
from openpilot.starpilot.flm.offroad_saved_history import SavedHistory
from openpilot.starpilot.flm.offroad_store import KEY, NAMESPACE, RouteScope, remember
from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES
from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
from openpilot.starpilot.galaxy.flm_backup import BackupRefused, FlmBackup, digest
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.galaxy.settings import SettingsChanged
from openpilot.starpilot.galaxy.tests import test_settings as fixtures


class FlmBackupTest(unittest.TestCase):
  def setUp(self):
    self.fixture = fixtures.SettingsGatewayTest()
    self.fixture.setUp()
    self.addCleanup(self.fixture.doCleanups)
    cp = CarInterface.get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    self.fixture.context.value = replace(self.fixture.context.value, cp=cp, cp_raw=cp.to_bytes())
    self.params = self.fixture.params
    self.params.put_bool('IsOffroad', True, block=True)
    self.gateway = self.fixture.gateway
    self.identity = self.fixture.session, self.fixture.generation
    self.owner = FlmBackup(self.gateway)
    self.scope = RouteScope(car_params_digest(cp), 'declared-backup-controller', 'a' * 64, 'b' * 64, '0',
                            self.owner._mode(cp), 'torque')
    zero = dict.fromkeys(ROUTE_NAMES, 0.0)
    self.row: dict[str, Any] = {'scope': self.scope.document(), 'values': dict(zero, friction_threshold_mid=.0036,
      friction_threshold_fast=.0048, friction_threshold_highway=.0048), 'previous': zero,
      'route_sha256s': ['c' * 64, 'd' * 64], 'source_start_ns': 1, 'source_end_ns': 2,
      'analysis_sha256': 'e' * 64, 'completed_wall_ns': 1794777785171179957, 'sequence': 1, 'state': 'ready_for_next_drive'}
    self.raw = remember(None, self.row)
    self.params.put(KEY, self.raw, block=True)
    self.scope_patch = patch('openpilot.starpilot.galaxy.flm_backup.current_scope', return_value=self.scope)
    self.scope_patch.start()
    self.addCleanup(self.scope_patch.stop)

  def preview(self, backup=None, mode='learned', authorized=lambda: True):
    return self.owner.preview(self.owner.export() if backup is None else backup, mode, self.identity, authorized=authorized)

  def stored(self):
    return Path(self.params.get_param_path(KEY)).read_bytes()

  def confirm(self, preview, authorized=lambda: True):
    return self.owner.confirm(preview['intent'], self.identity, authorized=authorized)

  @staticmethod
  def seal(backup):
    backup['checksum'] = digest({key: value for key, value in backup.items() if key != 'checksum'})
    return backup

  def test_export_roundtrip_is_readonly_and_never_claims_current_application(self):
    self.params.put('GithubSshKeys', 'private-key', block=True)
    backup = self.owner.export()
    self.assertEqual(self.owner._validate(backup)[self.scope.key()], self.row)
    self.assertEqual(backup['learned'][self.scope.key()]['completed_wall_ns'], str(self.row['completed_wall_ns']))
    self.assertEqual(len(backup['manual']), 30)
    self.assertNotIn('private-key', json.dumps(backup))
    self.assertNotIn('CarParams', json.dumps(backup))
    preview = self.preview(backup)
    self.assertEqual(preview['count'], 0)
    self.assertEqual(self.stored(), self.raw)
    result = self.confirm(preview)
    self.assertTrue(result['complete'])
    self.assertEqual(result['restored'], 0)
    self.assertNotIn('applied', result)
    self.assertEqual(self.stored(), self.raw)

  def test_browser_transport_preserves_exact_provenance_and_rejects_numeric_substitutions(self):
    backup = self.owner.export()
    transported = json.loads(json.dumps(backup))
    self.assertEqual(self.owner._validate(transported)[self.scope.key()], self.row)
    for field, value in [('completed_wall_ns', 1794777785171179957), ('source_start_ns', '1.5'), ('source_end_ns', '9' * 20)]:
      changed = deepcopy(backup)
      changed['learned'][self.scope.key()][field] = value
      with self.subTest(field=field), self.assertRaises(BackupRefused):
        self.preview(self.seal(changed))
    changed = deepcopy(backup)
    changed['learned'][self.scope.key()]['values']['friction_threshold_mid'] = .0036
    with self.assertRaises(BackupRefused):
      self.preview(self.seal(changed))
    self.assertEqual(self.stored(), self.raw)

  def test_locally_preserved_compatible_setup_restores_without_overwriting_other_scopes(self):
    backup = self.owner.export()
    SavedHistory(self.params).preserve(self.raw)
    other = deepcopy(self.row)
    other_scope = replace(self.scope, controller_revision='other-controller')
    other['scope'] = other_scope.document()
    before = remember(None, other)
    self.params.put(KEY, before, block=True)
    preview = self.preview(backup)
    self.assertEqual(preview['count'], 3)
    self.assertEqual(self.stored(), before, 'Preview must not write')
    result = self.confirm(preview)
    self.assertEqual(result['restored'], 3)
    restored = decode(self.stored())[NAMESPACE]
    self.assertEqual(restored[self.scope.key()], self.row)
    self.assertEqual(restored[other_scope.key()], other)
    self.assertIn(before.decode(), SavedHistory(self.params).read().values())
    snapshots = AssessmentCache(directory_for_params(self.params).with_name(directory_for_params(self.params).name + '-backups'))
    self.assertTrue(any(path.suffix == '.json' for path in snapshots.directory.iterdir()))

  def test_manual_roundtrip_uses_existing_slider_owner_and_preserves_automatic_history(self):
    page = self.gateway.page('tuning', *self.identity)
    index = next(i for i, row in enumerate(page['rows']) if row.get('coordinate') == 'highway_center_taper_max')
    change = self.gateway.preview(page['view'], index, 0, *self.identity, value=.5)
    self.assertTrue(self.gateway.confirm(change['intent'], *self.identity))
    backup = self.owner.export()
    page = self.gateway.page('tuning', *self.identity)
    change = self.gateway.preview(page['view'], index, 0, *self.identity, value=0)
    self.assertTrue(self.gateway.confirm(change['intent'], *self.identity))
    preview = self.preview(backup, 'manual')
    self.assertEqual(preview['count'], 1)
    result = self.confirm(preview)
    self.assertTrue(result['complete'])
    row = self.gateway.page('tuning', *self.identity)['rows'][index]
    self.assertEqual(float(row['value']), .5)
    self.assertEqual(self.stored(), self.raw)

  def test_wrong_vehicle_controller_units_and_corrupt_file_refuse_before_writes(self):
    backup = self.owner.export()
    for key, value in [('vehicle', 'OTHER_CAR'), ('cpSha256', 'f' * 64), ('controllerMode', 'wrong-mode'), ('metric', not backup['metric'])]:
      changed = deepcopy(backup)
      changed[key] = value
      with self.subTest(key=key), self.assertRaises(BackupRefused):
        self.preview(self.seal(changed))
    changed = deepcopy(backup)
    changed['learned'][self.scope.key()]['values']['friction_threshold_mid'] = '.004'
    with self.assertRaisesRegex(BackupRefused, 'checksum'):
      self.preview(changed)
    with self.assertRaisesRegex(BackupRefused, 'latest qualified'):
      self.preview(self.seal(changed))
    self.assertEqual(self.stored(), self.raw)

  def test_foreign_qualification_and_newer_rollback_cannot_be_reimported(self):
    backup = self.owner.export()
    self.params.put(KEY, encode(decode(None)), block=True)
    with self.assertRaisesRegex(BackupRefused, 'latest qualified'):
      self.preview(backup)
    SavedHistory(self.params).preserve(self.raw)
    rolled_back = deepcopy(self.row)
    rolled_back.update(values=dict(rolled_back['previous']), state='rolled_back')
    self.params.put(KEY, encode({'version': 3, 'profiles': {}, 'fullSurfaces': {}, NAMESPACE: {self.scope.key(): rolled_back}}), block=True)
    with self.assertRaisesRegex(BackupRefused, 'latest qualified'):
      self.preview(backup)

  def test_consumed_trial_stays_consumed_and_source_transition_refuses(self):
    backup = self.owner.export()
    cache = AssessmentCache(directory_for_params(self.params))
    self.assertTrue(cache.claim_trial(self.row['analysis_sha256'], 'a' * 32))
    with self.assertRaisesRegex(BackupRefused, 'already started'):
      self.preview(backup)
    with patch('openpilot.starpilot.galaxy.flm_backup.current_scope', return_value=replace(self.scope, controller_revision='updated-controller')):
      with self.assertRaisesRegex(BackupRefused, 'matches the current controller'):
        self.preview(backup)

  def test_full_history_is_preserved_instead_of_deleting_a_setup(self):
    backup = self.owner.export()
    SavedHistory(self.params).preserve(self.raw)
    rows = {}
    for index in range(8):
      other = deepcopy(self.row)
      scope = replace(self.scope, controller_revision='other-' + str(index))
      other['scope'] = scope.document()
      rows[scope.key()] = other
    raw = encode({'version': 3, 'profiles': {}, 'fullSurfaces': {}, NAMESPACE: rows})
    self.params.put(KEY, raw, block=True)
    with self.assertRaisesRegex(BackupRefused, 'history is full'):
      self.preview(backup)
    self.assertEqual(self.stored(), raw)

  def test_park_session_expiry_and_changed_state_refuse(self):
    backup = self.owner.export()
    with self.assertRaises(SettingsChanged):
      self.preview(backup, authorized=lambda: False)
    self.fixture.context.value = replace(self.fixture.context.value, parked=False)
    with self.assertRaises(SettingsChanged):
      self.preview(backup)
    self.fixture.context.value = replace(self.fixture.context.value, parked=True)
    preview = self.preview(backup)
    with self.assertRaises(SettingsChanged):
      self.owner.confirm(preview['intent'], ('other-session', b'other-generation'), authorized=lambda: True)
    self.params.put(KEY, encode(decode(None)), block=True)
    with self.assertRaises(BackupRefused):
      self.confirm(preview)
    clock = [0]
    self.owner = FlmBackup(self.gateway, clock=lambda: clock[0])
    self.params.put(KEY, self.raw, block=True)
    preview = self.preview(backup)
    clock[0] = 91
    with self.assertRaisesRegex(SettingsChanged, 'expired'):
      self.confirm(preview)

  def test_manual_invalid_value_or_non_tuning_key_is_rejected_before_any_write(self):
    backup = self.owner.export()
    for key, value in [('value', '999999'), ('key', 'DoReboot')]:
      changed = deepcopy(backup)
      changed['manual'][0][key] = value
      with self.subTest(key=key), self.assertRaises((SettingsChanged, BackupRefused)):
        self.preview(self.seal(changed), 'manual')
    self.assertEqual(self.stored(), self.raw)

  def test_authenticated_http_download_preview_restore_origin_and_size_guards(self):
    access = GalaxyAccessOwner(Path(self.params.get_param_path(KEY)).parent.parent / 'backup-access')
    self.assertTrue(access.configure('password123', lambda: True))
    server = make_server(port=0, owner=access, settings=self.gateway)
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
        headers.update({'Content-Type': 'application/json', 'Origin': origin or f'http://127.0.0.1:{server.server_port}'})
      try:
        connection.request('POST' if payload is not None else 'GET', path,
                           body=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.headers), json.loads(response.read())
      finally:
        connection.close()

    self.assertEqual(request('/api/flm/backup')[0], 401)
    status, headers, _ = request('/api/auth/login', {'password': 'password123'})
    self.assertEqual(status, 200)
    cookie = headers['Set-Cookie'].split(';')[0]
    status, headers, backup = request('/api/flm/backup', cookie=cookie)
    self.assertEqual(status, 200)
    self.assertIn('starpilot-flm-tune.json', headers['Content-Disposition'])
    self.assertEqual(self.owner._validate(backup)[self.scope.key()], self.row)
    self.assertEqual(backup['learned'][self.scope.key()]['completed_wall_ns'], str(self.row['completed_wall_ns']))
    self.assertEqual(request('/api/flm/backup?unexpected=1', cookie=cookie)[0], 400)
    endpoint = '/api/settings/flm-backup/preview'
    payload = {'backup': backup, 'mode': 'learned'}
    self.assertEqual(request(endpoint, payload)[0], 401)
    self.assertEqual(request(endpoint, payload, cookie, 'https://wrong.invalid')[0], 403)
    status, _, preview = request(endpoint, payload, cookie)
    self.assertEqual(status, 200)
    self.assertEqual(self.stored(), self.raw)
    status, _, result = request('/api/settings/flm-backup/confirm', {'intent': preview['intent'], 'confirmed': True}, cookie)
    self.assertEqual(status, 200)
    self.assertTrue(result['complete'])
    self.assertEqual(result['restored'], 0)
    self.assertEqual(request(endpoint, {'backup': 'x' * (67 * 1024), 'mode': 'learned'}, cookie)[0], 413)
    self.assertEqual(self.stored(), self.raw)


if __name__ == '__main__':
  unittest.main()
