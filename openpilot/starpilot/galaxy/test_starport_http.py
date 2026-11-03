from typing import NotRequired, TypedDict
from unittest.mock import Mock
import http.client
import json
import threading

import pytest

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.starport_setup import StarPortSetup
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.system.starport.source_verifier import StarPortSourceVerifier
from openpilot.starpilot.bluetooth.owner import BluetoothUnavailable
from openpilot.starpilot.system.starport import apkpure


@pytest.fixture(autouse=True)
def offline_download(monkeypatch):
  monkeypatch.setattr(apkpure, 'start_background_refresh', lambda: None)
  monkeypatch.setattr(apkpure, '_cache', None)


class ApiState(TypedDict):
  parked: bool
  identity_installed: bool
  bluetooth_enabled: bool
  enabled: bool
  install_ready: bool
  enable_events: list[str]
  bluetooth_failure: NotRequired[str]
  setup_owner: NotRequired[StarPortSetup]


class FakeImport:
  def __init__(self, root):
    self.work_dir = root
    self.calls = []
    self.running = False
  def busy(self): return self.running
  def status(self): return {'state': 'running' if self.running else 'idle'}
  def start(self, *, path, enabled, app_version=None):
    self.calls.append((path.read_bytes(), enabled))
    self.running = True


@pytest.fixture
def api(tmp_path):
  access = GalaxyAccessOwner(tmp_path / 'access')
  assert access.configure('password123', lambda: True)
  state: ApiState = {'parked': True, 'identity_installed': True, 'bluetooth_enabled': True, 'enabled': True,
                     'install_ready': True, 'enable_events': []}
  def set_enabled(value):
    state['enable_events'].append('starport')
    state['enabled'] = value
  def enable_bluetooth(_session):
    state['enable_events'].append('bluetooth')
    if state.get('bluetooth_failure') == 'unavailable':
      raise BluetoothUnavailable('Radio unavailable')
    if state.get('bluetooth_failure') != 'unverified':
      state['bluetooth_enabled'] = True
  job = FakeImport(tmp_path / 'imports')
  setup = StarPortSetup(parked=lambda: state['parked'], enabled=lambda: state['enabled'], session_valid=lambda _identity: True,
                           import_job=Mock(wraps=job, work_dir=job.work_dir), identity_status=lambda: {'installed': state['identity_installed'],
                                                                     'message': 'Package status'},
                           bluetooth_enabled=lambda: state['bluetooth_enabled'], install_ready=lambda: state['install_ready'],
                           service_ready=lambda: True, set_enabled=set_enabled, enable_bluetooth=enable_bluetooth)
  state['setup_owner'] = setup
  class FakeClient:
    def __init__(self):
      self.calls = []
      self.verifier = None
      self.receiver = ''
      self.running = False
      self.auto_connect = False
      self.connection = 'wireless'
      self.car_type = 'builtin'
      self.profile = ''
      self.available = True
      self.stop_error = False
      self.stop_completes = True
    def call(self, command, **kwargs):
      assert command in ('status', 'stop') or self.verifier.valid(kwargs['source'])
      self.calls.append((command, dict(kwargs)))
      if command == 'status':
        return {'status': {'receiver_address': self.receiver, 'receiver_name': 'My car', 'state': 'streaming' if self.running else 'idle',
                           'running': self.running, 'auto_connect': self.auto_connect, 'pairing_ready': False,
                           'connection': self.connection, 'car_type': self.car_type}}
      if command == 'devices':
        return {'devices': [{'address': 'AA:BB:CC:DD:EE:FF', 'name': 'Saved car', 'paired': True, 'starport': True}]}
      if command == 'select_receiver':
        self.receiver = kwargs['address']
      if command == 'set_auto_connect':
        self.auto_connect = kwargs['enabled']
      if command == 'set_connection':
        self.connection = kwargs['connection']
      if command == 'set_car_type':
        self.car_type = kwargs['car_type']
      if command == 'set_car_profile':
        self.profile = kwargs['profile']
      if command == 'start':
        self.running = True
      if command == 'stop':
        if self.stop_error:
          raise RuntimeError('Disconnect failed')
        if self.stop_completes:
          self.running = False
      return {'pairing': {'active': True, 'receiver': {'address': 'AA:BB:CC:DD:EE:FF', 'name': 'Car'},
                          'prompt': {'id': 'a' * 32, 'kind': 'confirmation', 'value': '123456'},
                          'approved': False}} if command == 'pairing_status' else {}
  client = FakeClient()
  server = make_server(port=0, owner=access, parked=lambda: state['parked'], starport_setup=setup,
                       starport_client=client)
  client.verifier = StarPortSourceVerifier(port=server.server_port)
  worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
  worker.start()
  def request(method, path, body=None, *, cookie='', content_type='application/octet-stream'):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    headers = {'Cookie': cookie}
    if method != 'GET':
      headers['Origin'] = f'http://127.0.0.1:{server.server_port}'
    if body is not None:
      headers['Content-Type'] = content_type
    try:
      conn.request(method, path, body=body, headers=headers)
      response = conn.getresponse()
      return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
      conn.close()
  yield request, job, state, client
  server.shutdown()
  worker.join(timeout=2)
  server.server_close()


def test_galaxy_upload_requires_session_and_exact_binary_body(api):
  request, job, state, _ = api
  assert request('GET', '/api/starport/setup')[0] == 401
  assert request('POST', '/api/starport/upload', b'fake-apk')[0] == 401
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                              content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  code, status, _ = request('GET', '/api/starport/setup', cookie=cookie)
  assert code == 200 and status['wiredAvailable'] is True
  assert status['download'] is None
  assert request('POST', '/api/starport/upload', b'fake-apk', cookie=cookie,
                 content_type='application/json')[0] == 415
  assert request('POST', '/api/starport/upload', b'fake-apk', cookie=cookie)[0] == 202
  assert job.calls[0][0] == b'fake-apk'
  state['parked'] = False
  assert job.calls[0][1]() is True
  assert request('POST', '/api/starport/upload', b'other', cookie=cookie)[0] == 409


@pytest.mark.parametrize('parked', [True, False])
@pytest.mark.parametrize('enabled', [True, False])
def test_galaxy_upload_works_in_either_road_state_without_enabling(api, parked, enabled):
  request, job, state, _ = api
  state.update(parked=parked, enabled=enabled)
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                             content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  code, uploaded, _ = request('POST', '/api/starport/upload', b'fake-apk', cookie=cookie)
  assert code == 202 and uploaded['import']['state'] == 'running'
  assert uploaded['parked'] is parked and uploaded['enabled'] is enabled
  assert job.calls[0][0] == b'fake-apk' and job.calls[0][1]()
  assert state['enabled'] is enabled


def test_galaxy_rejects_upload_after_logout(api):
  request, job, _, _ = api
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                              content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  assert request('POST', '/api/auth/logout', '{}', cookie=cookie, content_type='application/json')[0] == 200
  assert request('POST', '/api/starport/upload', b'fake-apk', cookie=cookie)[0] == 401
  assert job.calls == []


@pytest.mark.parametrize('parked', [True, False])
def test_enable_in_either_road_state_requires_session_and_hardware(api, parked):
  request, _, state, _ = api
  state.update(parked=parked, enabled=False)
  def body(enabled):
    return json.dumps({'enabled': enabled})
  assert request('POST', '/api/starport/enable', body(True), content_type='application/json')[0] == 401
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                              content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  assert request('POST', '/api/starport/enable', '{"enabled":1}', cookie=cookie,
                 content_type='application/json')[0] == 400
  code, result, _ = request('POST', '/api/starport/enable', body(True), cookie=cookie, content_type='application/json')
  assert code == 200 and result['enabled'] is True and result['parked'] is parked
  assert state['enabled'] is True
  state['install_ready'] = False
  assert request('POST', '/api/starport/enable', body(True), cookie=cookie,
                 content_type='application/json')[0] == 409
  assert request('POST', '/api/starport/enable', body(False), cookie=cookie,
                 content_type='application/json')[1]['enabled'] is False
  state['install_ready'] = True
  assert request('POST', '/api/starport/enable', body(True), cookie=cookie,
                 content_type='application/json')[1]['enabled'] is True
  assert request('POST', '/api/auth/logout', '{}', cookie=cookie, content_type='application/json')[0] == 200
  assert request('POST', '/api/starport/enable', body(False), cookie=cookie, content_type='application/json')[0] == 401
  assert state['enabled'] is True


@pytest.mark.parametrize('parked', [True, False])
def test_confirmed_enable_powers_bluetooth_first_in_either_road_state(api, parked):
  request, _, state, _ = api
  state.update(parked=parked, enabled=False, bluetooth_enabled=False)
  confirmed = json.dumps({'enabled': True, 'enableBluetooth': True})
  assert request('POST', '/api/starport/enable', confirmed, content_type='application/json')[0] == 401
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}), content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  assert request('POST', '/api/starport/enable', '{"enabled":true}', cookie=cookie, content_type='application/json')[0] == 409
  for payload in ({'enabled': True, 'enableBluetooth': 1}, {'enabled': False, 'enableBluetooth': True}):
    assert request('POST', '/api/starport/enable', json.dumps(payload), cookie=cookie, content_type='application/json')[0] == 400
  assert state['enable_events'] == [] and not state['enabled'] and not state['bluetooth_enabled']
  state['install_ready'] = False
  assert request('POST', '/api/starport/enable', confirmed, cookie=cookie, content_type='application/json')[0] == 409
  assert state['enable_events'] == []
  state['install_ready'] = True
  code, result, _ = request('POST', '/api/starport/enable', confirmed, cookie=cookie, content_type='application/json')
  assert code == 200 and result['enabled'] and result['bluetoothEnabled']
  assert result['parked'] is parked and state['enable_events'] == ['bluetooth', 'starport']


@pytest.mark.parametrize('failure', ['unavailable', 'unverified'])
def test_confirmed_enable_does_not_turn_on_starport_when_bluetooth_fails(api, failure):
  request, _, state, _ = api
  state.update(parked=False, enabled=False, bluetooth_enabled=False, bluetooth_failure=failure)
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}), content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  code, result, _ = request('POST', '/api/starport/enable', '{"enabled":true,"enableBluetooth":true}',
                           cookie=cookie, content_type='application/json')
  assert code == (503 if failure == 'unavailable' else 409)
  assert 'Bluetooth' in result['error']
  assert state['enable_events'] == ['bluetooth'] and not state['enabled']


@pytest.mark.parametrize('parked', [True, False])
@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('connection', ['connected', 'idle', 'unavailable'])
def test_identity_removal_disconnects_first_in_either_road_state(api, monkeypatch, parked, enabled, connection):
  from openpilot.starpilot.system.starport import apk_identity
  request, job, state, client = api
  state.update(parked=parked, enabled=enabled)
  client.running = connection == 'connected'
  client.available = connection != 'unavailable'
  removed = []
  def remove():
    assert not client.running
    commands = [command for command, _kwargs in client.calls]
    assert commands == {'connected': ['status', 'stop', 'status'], 'idle': ['status'], 'unavailable': []}[connection]
    removed.append(True)
  monkeypatch.setattr(apk_identity, 'remove_identity', remove)
  assert request('DELETE', '/api/starport/identity')[0] == 401
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                              content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  job.running = True
  assert request('DELETE', '/api/starport/identity', cookie=cookie)[0] == 409
  assert removed == [] and client.calls == []
  job.running = False
  assert request('DELETE', '/api/starport/identity', cookie=cookie)[0] == 200
  assert removed == [True]
  assert state['enabled'] is enabled
  assert request('POST', '/api/auth/logout', '{}', cookie=cookie, content_type='application/json')[0] == 200
  assert request('DELETE', '/api/starport/identity', cookie=cookie)[0] == 401
  assert removed == [True]


@pytest.mark.parametrize('failure', ['stop_error', 'still_running', 'status_error', 'invalid_status'])
def test_identity_removal_preserves_package_when_disconnect_cannot_be_confirmed(api, monkeypatch, failure):
  from openpilot.starpilot.system.starport import apk_identity
  request, _, state, client = api
  state.update(parked=False, enabled=False)
  client.running = True
  client.stop_error = failure == 'stop_error'
  client.stop_completes = failure != 'still_running'
  original_call = client.call
  def call(command, **kwargs):
    if command == 'status' and failure == 'status_error':
      raise RuntimeError('Status unavailable')
    if command == 'status' and failure == 'invalid_status':
      return {'status': {}}
    return original_call(command, **kwargs)
  monkeypatch.setattr(client, 'call', call)
  remove = Mock()
  monkeypatch.setattr(apk_identity, 'remove_identity', remove)
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                             content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  code, result, _ = request('DELETE', '/api/starport/identity', cookie=cookie)
  assert code == (409 if failure == 'still_running' else 503)
  assert 'disconnect' in result['error']
  remove.assert_not_called()


def test_pairing_source_is_minted_only_for_authenticated_parked_session_and_revoked_on_logout(api):
  request, _, state, client = api
  assert request('POST', '/api/starport/pairing', '{}', content_type='application/json')[0] == 401
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                              content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  state['parked'] = False
  assert request('POST', '/api/starport/pairing', '{}', cookie=cookie, content_type='application/json')[0] == 403
  state['parked'] = True
  state['identity_installed'] = False
  assert request('POST', '/api/starport/pairing', '{}', cookie=cookie, content_type='application/json')[0] == 200
  state['identity_installed'] = True
  state['bluetooth_enabled'] = False
  assert request('POST', '/api/starport/pairing', '{}', cookie=cookie, content_type='application/json')[0] == 409
  state['bluetooth_enabled'] = True
  code, result, _ = request('POST', '/api/starport/pairing', '{}', cookie=cookie, content_type='application/json')
  assert code == 200 and result == {'pairing': True, 'seconds': 180}
  source = next(kwargs['source'] for command, kwargs in client.calls if command == 'prepare_pairing')
  assert source not in json.dumps(result) and client.verifier.valid(source)
  assert request('POST', '/api/auth/logout', '{}', cookie=cookie, content_type='application/json')[0] == 200
  assert not client.verifier.valid(source)


def test_incoming_prompt_requires_same_session_park_and_current_source(api):
  request, _, state, client = api
  assert request('GET', '/api/starport/pairing/status')[0] == 401
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                              content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  assert request('GET', '/api/starport/pairing/status', cookie=cookie)[1]['pairing']['active'] is False
  assert request('POST', '/api/starport/pairing', '{}', cookie=cookie,
                 content_type='application/json')[0] == 200
  code, status, _ = request('GET', '/api/starport/pairing/status', cookie=cookie)
  assert code == 200 and status['pairing']['prompt']['kind'] == 'confirmation'
  source = next(kwargs['source'] for command, kwargs in client.calls if command == 'prepare_pairing')
  assert source not in json.dumps(status)
  assert request('POST', '/api/starport/pairing/response',
                 json.dumps({'prompt_id': 'bad', 'accepted': True, 'value': ''}), cookie=cookie,
                 content_type='application/json')[0] == 400
  assert [command for command, _kwargs in client.calls if command != 'status'] == ['prepare_pairing', 'pairing_status']
  payload = {'prompt_id': 'a' * 32, 'accepted': True, 'value': ''}
  assert request('POST', '/api/starport/pairing/response', json.dumps(payload), cookie=cookie,
                 content_type='application/json')[0] == 200
  assert client.calls[-1][0] == 'pairing_response'
  client.receiver = 'AA:BB:CC:DD:EE:FF'
  code, selected, _ = request('GET', '/api/starport/pairing/status', cookie=cookie)
  assert code == 200 and selected['selectedReceiver'] == {'address': client.receiver, 'name': 'My car'}
  state['parked'] = False
  assert request('POST', '/api/starport/pairing/response', json.dumps(payload), cookie=cookie,
                 content_type='application/json')[0] == 403
  state['parked'] = True
  assert request('POST', '/api/starport/pairing/cancel', '{}', cookie=cookie,
                 content_type='application/json')[0] == 200
  assert not client.verifier.valid(source)
  assert request('POST', '/api/starport/pairing/response', json.dumps(payload), cookie=cookie,
                 content_type='application/json')[0] == 409


def test_existing_paired_car_controls_are_local_authenticated_and_source_bound(api):
  request, _, state, client = api
  assert request('GET', '/api/starport/receivers')[0] == 401
  assert request('POST', '/api/starport/control', json.dumps({'action': 'start'}),
                 content_type='application/json')[0] == 401
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                              content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  code, available, _ = request('GET', '/api/starport/receivers', cookie=cookie)
  assert code == 200 and available['receivers'] == [{'address': 'AA:BB:CC:DD:EE:FF', 'name': 'Saved car'}]
  source = next(kwargs['source'] for command, kwargs in client.calls if command == 'devices')
  assert source not in json.dumps(available) and not client.verifier.valid(source)
  assert request('POST', '/api/starport/control', json.dumps({'action': 'select_receiver', 'address': 'bad'}),
                 cookie=cookie, content_type='application/json')[0] == 400
  assert request('POST', '/api/starport/control', json.dumps({'action': 'start'}),
                 cookie=cookie, content_type='application/json')[0] == 409
  code, result, _ = request('POST', '/api/starport/control',
                            json.dumps({'action': 'select_receiver', 'address': 'AA:BB:CC:DD:EE:FF'}),
                            cookie=cookie, content_type='application/json')
  assert code == 200 and result['status']['receiver_address'] == 'AA:BB:CC:DD:EE:FF'
  state['identity_installed'] = False
  assert request('POST', '/api/starport/control', json.dumps({'action': 'start'}),
                 cookie=cookie, content_type='application/json')[0] == 409
  assert not client.running
  state['identity_installed'] = True
  state['parked'] = False
  assert request('POST', '/api/starport/control', json.dumps({'action': 'auto_connect', 'enabled': True}),
                 cookie=cookie, content_type='application/json')[0] == 403
  assert request('POST', '/api/starport/control', json.dumps({'action': 'start'}),
                 cookie=cookie, content_type='application/json')[0] == 200
  assert client.running
  assert request('POST', '/api/starport/control', json.dumps({'action': 'stop'}),
                 cookie=cookie, content_type='application/json')[0] == 200
  assert not client.running
  state['parked'] = True
  assert request('POST', '/api/starport/control', json.dumps({'action': 'auto_connect', 'enabled': True}),
                 cookie=cookie, content_type='application/json')[0] == 200
  assert client.auto_connect
  assert request('POST', '/api/auth/logout', '{}', cookie=cookie, content_type='application/json')[0] == 200
  assert request('POST', '/api/starport/control', json.dumps({'action': 'start'}),
                 cookie=cookie, content_type='application/json')[0] == 401


@pytest.mark.parametrize('connection', ['wired', 'headunitpad', 'openheadunit'])
def test_wired_connection_requires_park_but_not_a_paired_receiver(api, connection):
  request, _, state, client = api
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                              content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  payload = json.dumps({'action': 'connection', 'connection': connection})
  state['parked'] = False
  assert request('POST', '/api/starport/control', payload, cookie=cookie, content_type='application/json')[0] == 403
  state['parked'] = True
  code, result, _ = request('POST', '/api/starport/control', payload, cookie=cookie, content_type='application/json')
  assert code == 200 and result['status']['connection'] == connection
  assert request('POST', '/api/starport/control', json.dumps({'action': 'start'}), cookie=cookie,
                 content_type='application/json')[0] == 200
  assert client.running

  client.running = False
  state['identity_installed'] = False
  code, result, _ = request('POST', '/api/starport/control', json.dumps({'action': 'start'}), cookie=cookie,
                            content_type='application/json')
  assert code == 409 and result['error'] == 'Verify your Android Auto package first'


def test_setup_type_and_car_profile_need_park_and_valid_values(api):
  request, _, state, client = api
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                             content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]

  def post(body):
    return request('POST', '/api/starport/control', json.dumps(body), cookie=cookie, content_type='application/json')

  state['parked'] = False
  assert post({'action': 'car_type', 'carType': 'adapter'})[0] == 403
  assert post({'action': 'car_profile', 'profile': 'default'})[0] == 403
  state['parked'] = True
  code, result, _ = post({'action': 'car_type', 'carType': 'adapter'})
  assert code == 200 and result['status']['car_type'] == 'adapter'
  assert post({'action': 'car_type', 'carType': 'hovercraft'})[0] == 400
  assert post({'action': 'car_type', 'carType': 'usb', 'extra': 1})[0] == 400
  assert post({'action': 'car_profile', 'profile': 'default'})[0] == 200 and client.profile == 'default'
  assert post({'action': 'car_profile', 'profile': ''})[0] == 200 and client.profile == ''
  assert post({'action': 'car_profile', 'profile': 'not-a-profile'})[0] == 400


def test_local_enable_then_upload_bundle_preserves_binary_bytes(api):
  import io
  import zipfile

  request, job, state, _ = api
  state['enabled'] = False
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                             content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  code, before, _ = request('GET', '/api/starport/setup', cookie=cookie)
  assert code == 200 and not before['enabled'] and before['installReady']
  code, enabled, _ = request('POST', '/api/starport/enable', json.dumps({'enabled': True}),
                            cookie=cookie, content_type='application/json')
  assert code == 200 and enabled['enabled']
  archive = io.BytesIO()
  with zipfile.ZipFile(archive, 'w') as bundle:
    bundle.writestr('base.apk', b'package-content')
  payload = archive.getvalue()
  code, uploaded, _ = request('POST', '/api/starport/upload', payload, cookie=cookie)
  assert code == 202 and uploaded['import']['state'] == 'running'
  assert job.calls[0][0] == payload
  assert job.calls[0][1]() is True
  code, status, _ = request('GET', '/api/starport/setup', cookie=cookie)
  assert code == 200 and status['import']['state'] == 'running'


def test_outgoing_car_selection_requires_current_pairing_session_and_valid_address(api):
  request, _, state, client = api
  path = '/api/starport/pairing/select'
  selected = json.dumps({'address': 'aa:bb:cc:dd:ee:ff'})
  assert request('POST', path, selected, content_type='application/json')[0] == 401
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}),
                              content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  def select(body=selected):
    return request('POST', path, body, cookie=cookie, content_type='application/json')[0]
  assert select() == 409
  assert request('POST', '/api/starport/pairing', '{}', cookie=cookie, content_type='application/json')[0] == 200
  for body in ('{}', '{"address":1}', '{"address":"bad"}', '{"address":"AA:BB:CC:DD:EE:FF","force":true}'):
    assert select(body) == 400
  assert select() == 200
  assert client.calls[-1][0] == 'pair_device'
  assert client.calls[-1][1]['address'] == 'AA:BB:CC:DD:EE:FF'
  assert client.verifier.valid(client.calls[-1][1]['source'])
  state['parked'] = False
  assert select() == 403
  state['parked'] = True
  assert request('POST', '/api/starport/pairing/cancel', '{}', cookie=cookie, content_type='application/json')[0] == 200
  assert select() == 409


def test_google_sign_in_api_is_gone(api):
  request, _, _, _ = api
  _, _, headers = request('POST', '/api/auth/login', '{"password":"password123"}', content_type='application/json')
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  payload = json.dumps({'email': 'a@example.com', 'token': 'oauth2_4/test', 'remember': False})
  for path in ('/api/starport/google-import', '/api/starport/google-forget'):
    assert request('POST', path, payload, cookie=cookie, content_type='application/json')[0] == 405


def test_on_device_browser_api_is_gone(api):
  request, _, _, _ = api
  _, _, headers = request('POST', '/api/auth/login', '{"password":"password123"}', content_type='application/json')
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  start = {'operation': 'start', 'email': 'user@example.com', 'remember': False}
  assert request('POST', '/api/starport/google-browser', json.dumps(start), cookie=cookie, content_type='application/json')[0] != 200


def test_classic_setup_switch_needs_a_session_and_park_but_not_the_service(api, tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import new_setup
  monkeypatch.setattr(new_setup, 'FLAG_PATH', tmp_path / 'new_setup')
  request, _, state, client = api
  body = json.dumps({'action': 'new_setup', 'enabled': False})
  assert request('POST', '/api/starport/control', body, content_type='application/json')[0] == 401
  code, _, headers = request('POST', '/api/auth/login', json.dumps({'password': 'password123'}), content_type='application/json')
  assert code == 200
  cookie = headers['Set-Cookie'].split(';', 1)[0]
  assert new_setup.enabled()
  state['parked'] = False
  assert request('POST', '/api/starport/control', body, cookie=cookie, content_type='application/json')[0] == 403
  assert new_setup.enabled()
  state['parked'] = True
  client.calls.clear()
  code, result, _ = request('POST', '/api/starport/control', body, cookie=cookie, content_type='application/json')
  assert (code, result) == (200, {'newSetup': False}) and not new_setup.enabled()
  assert client.calls == []   # the daemon was never asked: this is the way back when it is down
  assert request('GET', '/api/starport/setup', cookie=cookie)[1]['newSetup'] is False
  code, result, _ = request('POST', '/api/starport/control', json.dumps({'action': 'new_setup', 'enabled': True}),
                            cookie=cookie, content_type='application/json')
  assert (code, result) == (200, {'newSetup': True}) and new_setup.enabled()
  for bad in ({'action': 'new_setup'}, {'action': 'new_setup', 'enabled': 'no'}, {'action': 'new_setup', 'enabled': True, 'x': 1}):
    assert request('POST', '/api/starport/control', json.dumps(bad), cookie=cookie, content_type='application/json')[0] == 400
