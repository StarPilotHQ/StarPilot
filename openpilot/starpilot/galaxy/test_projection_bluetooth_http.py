from unittest.mock import Mock
"""Real Galaxy HTTP/auth and file-owner regressions; radio/AA IPC replaced by fakes."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.projection_layout import ProjectionLayoutOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.system.starport.display_profile import record_screen
from openpilot.starpilot.system.starport.projection_layout import ProjectionLayoutSource
from openpilot.starpilot.ui.onroad_customization import default_document

CAR = '02:00:00:00:00:01'
SCREEN = {'version': 1, 'width': 1280, 'height': 720, 'margin_width': 0,
          'margin_height': 240, 'fps': 60, 'config_index': 0}

class Params:
  def __init__(self, root): self.root, self.enabled = root, True
  def get(self, key):
    assert key == 'JetlinkMode'
    return 0
  def get_param_path(self, key): return str(self.root / key)
  def get_bool(self, key):
    assert key == 'StarPortEnabled'
    return self.enabled

class Bluetooth:
  def __init__(self): self.calls = []
  def snapshot(self, **kwargs):
    return {'version': 1, 'available': True, 'parked': True, 'powered': True,
            'discovering': False, 'devices': [], 'errorCode': None, 'pairing': None}
  def request(self, operation, **kwargs):
    self.calls.append((operation, kwargs))
    return self.snapshot()
  def cancel_session(self, identity): pass

class AA:
  def __init__(self):
    self.calls = []
    self.result: dict = {'handled': False}
    self.error = None
    self.http = None
  def call(self, command, **kwargs):
    self.calls.append((command, kwargs))
    assert command in ('bluetooth_action', 'bluetooth_power')
    assert set(kwargs) == ({'source', 'operation', 'address'} if command == 'bluetooth_action' else {'source', 'enabled'})
    assert self.http is not None
    code, data, _ = self.http('/api/starport/source/' + kwargs['source'])
    assert code == 200 and data == {'valid': True}, 'Real minted source proof was not live'
    if self.error:
      raise self.error
    return copy.deepcopy(self.result)

class ProjectionBluetoothHttpTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.root = Path(temporary.name)
    self.params = Params(self.root)
    self.native = self.root / 'OnroadCustomizations'
    self.native.write_text(json.dumps(default_document()))
    self.native_before = self.native.read_bytes()
    self.screen = self.root / 'screen.json'
    record_screen(SCREEN, self.screen)
    self.layout_source = ProjectionLayoutSource(self.root / 'projection/document.json')
    self.parked = True
    self.layout = ProjectionLayoutOwner(self.params, lambda: self.parked, self.layout_source, self.screen)
    self.access = GalaxyAccessOwner(self.root / 'access')
    self.assertTrue(self.access.configure('password123', lambda: True))
    self.bluetooth, self.starport = Bluetooth(), AA()
    self.native_layout = SimpleNamespace(snapshot=lambda: {'document': default_document(), 'editable': True, 'revision': 'native'})
    self.server = make_server(port=0, owner=self.access, projection_layout=self.layout, layouts=self.native_layout,
      starport_setup=SimpleNamespace(enabled=lambda: self.params.enabled),
      starport_client=self.starport, bluetooth=self.bluetooth)
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    self.worker.start()
    self.addCleanup(self.stop)
    self.starport.http = self.http
    code, _, headers = self.http('/api/auth/login', {'password': 'password123'})
    self.assertEqual(code, 200)
    self.cookie = headers['Set-Cookie'].split(';', 1)[0]

  def stop(self):
    self.server.shutdown()
    self.worker.join(timeout=2)
    self.server.server_close()

  def http(self, path, payload=None, cookie=''):
    connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
    headers = {'Cookie': cookie}
    if payload is not None:
      headers.update({'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{self.server.server_port}'})
    try:
      connection.request('POST' if payload is not None else 'GET', path,
        body=json.dumps(payload) if payload is not None else None, headers=headers)
      response = connection.getresponse()
      return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
      connection.close()

  def layout_payload(self):
    code, value, _ = self.http('/api/starport/layout', cookie=self.cookie)
    self.assertEqual(code, 200)
    return {'revision': value['revision'], 'document': copy.deepcopy(value['document'])}

  def action(self, operation='connect', cookie=None):
    return self.http('/api/bluetooth/action', {'operation': operation, 'address': CAR},
                     self.cookie if cookie is None else cookie)

  def power(self, enabled=False):
    return self.http('/api/bluetooth/action', {'operation': 'power', 'enabled': enabled}, self.cookie)

  def test_power_goes_to_the_daemon_holding_the_adapter_and_falls_back_without_it(self):
    snapshot = self.bluetooth.snapshot()
    snapshot['powered'] = False
    self.starport.result = {'handled': True, 'bluetooth': snapshot}
    code, result, _ = self.power(False)
    self.assertEqual((code, result), (200, snapshot))
    self.assertEqual(self.bluetooth.calls, [])
    command, arguments = self.starport.calls[-1]
    self.assertEqual((command, arguments['enabled']), ('bluetooth_power', False))
    self.assertEqual(self.http('/api/starport/source/' + arguments['source'])[0], 410)
    self.starport.result = {'handled': False}
    self.assertEqual(self.power(False)[0], 200)
    self.assertEqual(self.bluetooth.calls[-1][0], 'power')
    self.starport.error = OSError('daemon is not running')
    self.assertEqual(self.power(True)[0], 200)
    self.assertEqual([call[0] for call in self.bluetooth.calls], ['power', 'power'])
    self.params.enabled = False
    calls = len(self.starport.calls)
    self.assertEqual(self.power(False)[0], 200)
    self.assertEqual(len(self.starport.calls), calls, 'Android Auto off: the daemon is never contacted')

  def test_power_refusals_from_the_daemon_never_fall_back(self):
    for error, code in [('projection_active', 409), ('busy', 409), ('park_required', 409), ('service_unavailable', 503)]:
      self.starport.result = {'handled': True, 'error_code': error}
      actual, result, _ = self.power(False)
      self.assertEqual((actual, result['code']), (code, error))
      self.assertEqual(self.bluetooth.calls, [])

  def test_layout_requires_actual_auth_then_saves_only_projection_document(self):
    self.assertEqual(self.http('/api/starport/layout')[0], 401)
    payload = self.layout_payload()
    payload['document']['widgets']['current_speed']['x'] += 10
    self.assertEqual(self.http('/api/starport/layout', payload)[0], 401)
    code, result, _ = self.http('/api/starport/layout', payload, self.cookie)
    self.assertEqual(code, 200)
    self.assertTrue(result['valid'])
    self.assertEqual(json.loads(self.layout_source.path.read_bytes()), payload['document'])
    self.assertEqual(self.native.read_bytes(), self.native_before)

  def test_layout_stale_screen_revision_and_unpark_do_not_replace_saved_document(self):
    payload = self.layout_payload()
    self.assertEqual(self.http('/api/starport/layout', payload, self.cookie)[0], 200)
    before = self.layout_source.path.read_bytes()
    stale = self.layout_payload()
    record_screen(SCREEN | {'config_index': 1}, self.screen)
    self.assertEqual(self.http('/api/starport/layout', stale, self.cookie)[0], 409)
    fresh = self.layout_payload()
    self.parked = False
    self.assertEqual(self.http('/api/starport/layout', fresh, self.cookie)[0], 409)
    self.assertEqual(self.layout_source.path.read_bytes(), before)
    self.assertEqual(self.native.read_bytes(), self.native_before)

  def test_logged_out_session_cannot_save_layout_or_delegate_bluetooth(self):
    payload = self.layout_payload()
    self.assertEqual(self.http('/api/auth/logout', {}, self.cookie)[0], 200)
    self.assertEqual(self.http('/api/starport/layout', payload, self.cookie)[0], 401)
    self.assertEqual(self.action()[0], 401)
    self.assertEqual(self.starport.calls, [])
    self.assertEqual(self.bluetooth.calls, [])
    self.assertFalse(self.layout_source.path.exists())
    self.assertEqual(self.native.read_bytes(), self.native_before)

  def test_active_starport_handled_snapshot_never_calls_generic_controller(self):
    snapshot = self.bluetooth.snapshot()
    snapshot['devices'] = [{'address': CAR, 'name': 'IONIQ 6',
      'paired': True, 'trusted': True, 'connected': True}]
    self.starport.result = {'handled': True, 'bluetooth': snapshot}
    code, result, _ = self.action()
    self.assertEqual(code, 200)
    self.assertEqual(result, snapshot)
    self.assertEqual(self.bluetooth.calls, [])
    command, arguments = self.starport.calls[0]
    self.assertEqual((command, arguments['operation'], arguments['address']), ('bluetooth_action', 'connect', CAR))
    self.assertEqual(self.http('/api/starport/source/' + arguments['source'])[0], 410)

  def test_no_starport_lease_falls_back_once_and_disabled_starport_does_not_contact_daemon(self):
    self.assertEqual(self.action()[0], 200)
    self.assertEqual(len(self.starport.calls), 1)
    self.assertEqual(len(self.bluetooth.calls), 1)
    self.params.enabled = False
    self.assertEqual(self.action('disconnect')[0], 200)
    self.assertEqual(len(self.starport.calls), 1)
    self.assertEqual(len(self.bluetooth.calls), 2)
    self.assertEqual(self.bluetooth.calls[-1][0], 'disconnect')

  def test_forget_is_delegated_to_a_running_projection_and_falls_back_without_the_daemon(self):
    snapshot = self.bluetooth.snapshot()
    self.starport.result = {'handled': True, 'bluetooth': snapshot}
    code, result, _ = self.action('forget')
    self.assertEqual((code, result), (200, snapshot))
    self.assertEqual(self.starport.calls[0][1]['operation'], 'forget')
    self.assertEqual(self.bluetooth.calls, [])
    self.starport.result = {'handled': False}
    self.assertEqual(self.action('forget')[0], 200)
    self.assertEqual(self.bluetooth.calls[-1][0], 'forget')
    self.starport.error = OSError('daemon is not running')
    self.assertEqual(self.action('forget')[0], 200)
    self.assertEqual([call[0] for call in self.bluetooth.calls], ['forget', 'forget'])
    self.assertEqual(self.action('connect')[0], 503)

  def test_owner_busy_and_error_codes_never_fall_back(self):
    for error, code in [('busy', 409), ('park_required', 409), ('changed', 409), ('unreachable', 409), ('service_unavailable', 503)]:
      self.starport.result = {'handled': True, 'error_code': error}
      actual, result, _ = self.action()
      self.assertEqual(actual, code)
      self.assertEqual(result['code'], error)
      self.assertEqual(self.bluetooth.calls, [])

  def test_unknown_or_malformed_delegated_error_fails_closed_without_fallback(self):
    for error in ('unexpected_internal_error', '', None, 4, {'private': 'detail'}):
      self.starport.result = {'handled': True, 'error_code': error}
      code, result, _ = self.action()
      self.assertEqual(code, 503)
      self.assertEqual(result['code'], 'service_unavailable')
      self.assertNotIn('unexpected_internal_error', json.dumps(result))
      self.assertEqual(self.bluetooth.calls, [])

  def test_native_layout_optional_projection_failure_preserves_native_snapshot(self):
    self.assertEqual(self.http('/api/ui/layout')[0], 401)
    for error in (OSError('saved screen unavailable'), ValueError('invalid screen'), RuntimeError('projection owner unavailable')):
      def unavailable(error=error):
        raise error
      self.layout.snapshot = Mock(side_effect=unavailable)
      code, result, _ = self.http('/api/ui/layout', cookie=self.cookie)
      self.assertEqual(code, 200)
      self.assertFalse(result['projectionAvailable'])
      self.assertEqual(result['document'], default_document())
      self.assertEqual(result['revision'], 'native')
      self.assertEqual(self.native.read_bytes(), self.native_before)
      self.assertEqual(self.starport.calls, [])

  def test_daemon_transport_failure_and_malformed_reply_never_fall_back(self):
    self.starport.error = TimeoutError('private IPC failure')
    code, result, _ = self.action()
    self.assertEqual(code, 503)
    self.assertEqual(result['code'], 'service_unavailable')
    self.assertNotIn('private', json.dumps(result))
    self.assertEqual(self.bluetooth.calls, [])
    self.starport.error = None
    for malformed in ({}, {'handled': 'yes'}, {'handled': True}, {'handled': True, 'bluetooth': []}):
      self.starport.result = malformed
      self.assertEqual(self.action()[0], 503)
      self.assertEqual(self.bluetooth.calls, [])

if __name__ == '__main__':
  unittest.main()
