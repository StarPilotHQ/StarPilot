import http.client
import json
from pathlib import Path
import sys
import threading
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.companion import CompanionRegistry
from openpilot.starpilot.galaxy.server import make_server


@pytest.fixture
def companion_http(tmp_path):
  registry = CompanionRegistry(tmp_path / 'settings', tmp_path / 'lease', tmp_path / 'status')
  access = GalaxyAccessOwner(tmp_path / 'access')
  access.configure('password123', lambda: True)
  aa = Mock()
  aa.enabled.return_value = False
  parked = Mock(return_value=True)
  server = make_server(port=0, owner=access, companion=registry, parked=parked, starport_setup=aa)
  server.test_parked = parked
  worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
  worker.start()

  def request(path, payload=None, cookie='', origin=None, method=None):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    headers = {'Cookie': cookie}
    if payload is not None:
      headers.update({'Content-Type': 'application/json', 'Origin': origin or f'http://127.0.0.1:{server.server_port}'})
    try:
      connection.request(method or ('POST' if payload is not None else 'GET'), path,
                         json.dumps(payload) if payload is not None else None, headers)
      response = connection.getresponse()
      return response.status, response.read(), dict(response.getheaders())
    finally:
      connection.close()
  try:
    yield request, registry, server
  finally:
    server.shutdown()
    worker.join(timeout=2)
    server.server_close()


def test_auth_and_local_mutations(companion_http):
  request, registry, _ = companion_http
  assert request('/api/companion/status')[0] == 401
  assert request('/api/companion/settings', {'enabled': True})[0] == 401
  with patch('openpilot.starpilot.galaxy.server.direct_local_connection', return_value=True):
    status, _, headers = request('/api/auth/session')
    assert status == 200
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    assert request('/api/companion/settings', {'enabled': True}, cookie, 'https://evil.test')[0] == 403
    assert not registry.enabled()
    assert request('/api/companion/settings', {'enabled': True}, cookie)[0] == 200
    assert registry.enabled()
    assert request('/api/companion/settings', {'enabled': 1}, cookie)[0] == 400
    # A signed-in LAN client still cannot register a SOCKS endpoint outside galaxy0.
    payload = {'port': 1080, 'user': 'a' * 32, 'pass': 'b' * 32, 'version': '1.0.0', 'protocol': 1}
    assert request('/api/companion/uplink', payload, cookie)[0] == 400
    assert registry.lease() is None
    status, body, _ = request('/api/companion/status', cookie=cookie)
    assert status == 200 and json.loads(body)['enabled']
    assert b'pass' not in body
    assert request('/api/companion/qr.svg', cookie=cookie)[0] == 404


def test_apk_direct_download_without_cookie_and_head(companion_http, tmp_path):
  request, _, server = companion_http
  apk = tmp_path / 'sample.apk'
  apk.write_bytes(b'APK' * 30000)
  # Path is captured during server construction; patch its file operation.
  from openpilot.starpilot.galaxy.companion import APK_PATH
  real_open = type(APK_PATH).open

  def open_apk(path, *args, **kwargs):
    return real_open(apk if path == APK_PATH else path, *args, **kwargs)

  with patch('openpilot.starpilot.galaxy.server.direct_local_connection', return_value=True), patch('pathlib.Path.open', open_apk):
    code, body, headers = request('/app.apk')
    assert code == 200 and body == apk.read_bytes()
    assert headers['Content-Type'] == 'application/vnd.android.package-archive'
    code, body, headers = request('/api/companion/app.apk', method='HEAD')
    assert code == 200 and body == b'' and headers['Content-Length'] == '90000'
    server.remote_transport = True
    assert request('/app.apk')[0] == 403


def phone_bridge_proxy(port):
  # The phone bridge imports its siblings directly, exactly as it runs on comma.
  bridge = str(Path(__file__).resolve().parents[2] / 'galaxy_bluetooth/bridge')
  if bridge not in sys.path:
    sys.path.insert(0, bridge)
  from proxy import GalaxyProxy
  return GalaxyProxy(port)


def test_phone_bridge_against_current_galaxy(companion_http):
  _, registry, server = companion_http
  proxy = phone_bridge_proxy(server.server_port)
  request = {'id': 'test', 'method': 'GET', 'path': '/api/companion/status'}
  assert proxy.handle(request)['status'] == 200
  token = proxy.cookie
  assert token.startswith('galaxy_session=')
  import base64
  request.update(method='POST', path='/api/companion/settings', headers={'Content-Type': 'application/json'},
                 body=base64.b64encode(b'{"enabled":true}').decode())
  assert proxy.handle(request)['status'] == 200
  assert registry.enabled()
  assert proxy.cookie == token
  for path in ('/api/companion/uplink', '/api/companion/app.apk'):
    request['path'] = path
    assert proxy.handle(request)['status'] == 501


def test_bluetooth_runtime_tracks_upstream_restart_and_blocks_replay(companion_http):
  import base64
  request, _, server = companion_http
  proxy = phone_bridge_proxy(server.server_port)
  for instance in ('before-restart', 'after-restart'):
    with patch('openpilot.starpilot.galaxy.server.INSTANCE', instance):
      result = proxy.handle({'id': 'runtime', 'method': 'GET', 'path': '/api/companion/runtime'})
      assert result['status'] == 200
      runtime = json.loads(base64.b64decode(result['body']))
      assert runtime == json.loads(request('/data/runtime.json')[1])
      assert runtime['instance'] == instance
  for path in ('/api/replay/start', '/api/replay/media?sequence=1'):
    assert proxy.handle({'id': 'replay', 'method': 'POST', 'path': path})['status'] == 501


def test_web_manifest_lists_verifiable_files_the_apps_accept(companion_http):
  import hashlib
  from openpilot.starpilot.galaxy.server import WEB
  request, _, _ = companion_http
  status, body, _ = request('/api/companion/web-manifest')
  assert status == 200
  manifest = json.loads(body)
  assert manifest['schema'] == 1
  files = manifest['files']
  paths = [entry['path'] for entry in files]
  assert paths == sorted(paths) and len(set(paths)) == len(paths)
  assert 'data/runtime.json' not in paths and not any(p.endswith('.sample.json') for p in paths)
  for entry in files:
    data = (WEB / entry['path']).read_bytes()
    assert entry['size'] == len(data) and entry['sha256'] == hashlib.sha256(data).hexdigest()
    assert request('/' + entry['path'])[1] == data
  # The companion apps reject a download that breaks these rules, so catch a new root file or odd name here.
  root_files = {'index.html', 'manifest.webmanifest', 'sentry-push-worker.js'}
  root_dirs = ('assets/', 'css/', 'js/', 'vendor/', 'data/')
  allowed = set('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-./_')
  assert 1 <= len(files) <= 512 and sum(entry['size'] for entry in files) <= 32 * 1024 * 1024
  for entry in files:
    assert set(entry['path']) <= allowed and entry['size'] <= 4 * 1024 * 1024
    assert entry['path'] in root_files or entry['path'].startswith(root_dirs), entry['path']
  assert {'index.html', 'js/boot.js', 'js/app.js', 'data/catalog.json'} <= set(paths)


def test_phone_location_needs_local_session(tmp_path):
  access = GalaxyAccessOwner(tmp_path / 'access')
  access.configure('password123', lambda: True)
  phone = Mock()
  phone.publish.side_effect = lambda payload: {'ok': True} if payload.get('ageMs', 0) <= 2000 else (_ for _ in ()).throw(ValueError('Location is too old'))
  aa = Mock()
  aa.enabled.return_value = False
  server = make_server(port=0, owner=access, companion=CompanionRegistry(tmp_path / 's', tmp_path / 'l', tmp_path / 't'),
                       parked=Mock(return_value=True), starport_setup=aa, phone_location=phone)
  worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
  worker.start()
  origin = f'http://127.0.0.1:{server.server_port}'

  def post(payload, cookie=''):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
      connection.request('POST', '/api/companion/location', json.dumps(payload),
                         {'Cookie': cookie, 'Content-Type': 'application/json', 'Origin': origin})
      response = connection.getresponse()
      return response.status, json.loads(response.read())
    finally:
      connection.close()
  try:
    fix = {'latitude': 40., 'longitude': -110., 'horizontalAccuracy': 5., 'ageMs': 200}
    assert post(fix)[0] == 401
    with patch('openpilot.starpilot.galaxy.server.direct_local_connection', return_value=True):
      connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
      connection.request('GET', '/api/auth/session')
      cookie = connection.getresponse().getheader('Set-Cookie').split(';', 1)[0]
      connection.close()
      assert post(fix, cookie) == (200, {'ok': True})
      assert post({**fix, 'ageMs': 5000}, cookie) == (400, {'error': 'Location is too old'})
    server.remote_transport = True
    assert post(fix, cookie)[0] == 403
    assert phone.publish.call_count == 2
  finally:
    server.shutdown()
    worker.join(timeout=2)
    server.server_close()


def test_bluetooth_ready_needs_a_session_and_reports_what_blocks_pairing(companion_http):
  request, _, _ = companion_http
  assert request('/api/companion/bluetooth-ready')[0] == 401
  with patch('openpilot.starpilot.galaxy.server.direct_local_connection', return_value=True):
    cookie = request('/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    status, body, _ = request('/api/companion/bluetooth-ready', cookie=cookie)
  assert status == 200
  answer = json.loads(body)
  assert answer['schema'] == 1 and isinstance(answer['ready'], bool)
  assert answer['code'] in {'ready', 'starting', 'park_required', 'bluetooth_off', 'bluetooth_unavailable', 'bridge_starting'}
  assert answer['message']


def test_phone_location_timing_requires_direct_local_session(companion_http):
  request, _, server = companion_http
  assert request('/api/companion/location')[0] == 401
  with patch('openpilot.starpilot.galaxy.server.direct_local_connection', return_value=True):
    cookie = request('/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    status, body, headers = request('/api/companion/location', cookie=cookie)
    value = json.loads(body)
    assert status == 200 and value['ok'] and type(value['serverMonoTimeNs']) is int
    assert 'no-store' in headers['Cache-Control']
  with patch('openpilot.starpilot.galaxy.server.direct_local_connection', return_value=False):
    assert request('/api/companion/location', cookie=cookie)[0] in (401, 403)
  server.remote_transport = True
  with patch('openpilot.starpilot.galaxy.server.direct_local_connection', return_value=True):
    assert request('/api/companion/location', cookie=cookie)[0] == 403
