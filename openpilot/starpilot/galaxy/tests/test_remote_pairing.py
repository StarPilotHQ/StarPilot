"""Disposable socket coverage for Galaxy pairing and tunnel auth boundaries."""

import base64
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.remote import RemotePairing, RemoteTokens, gateway_cookie_valid, make_gateway_auth_server
from openpilot.starpilot.galaxy.server import make_remote_server, make_server


class RemotePairingTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    claim_path = mock.patch('openpilot.starpilot.galaxy_bluetooth.bridge.claim.CLAIM_PATH', Path(temporary.name) / 'claim.json')
    claim_path.start()
    self.addCleanup(claim_path.stop)
    root = Path(temporary.name)
    self.owner = GalaxyAccessOwner(root / 'credentials')
    self.pairing = RemotePairing(root / 'pairing')
    class Monitor:
      def sample(self):
        return {'schemaVersion': 1, 'mode': 'disposable'}
    self.local = make_server(port=0, owner=self.owner, remote_pairing=self.pairing, parked=lambda: True, monitor=Monitor())
    self.remote = make_remote_server(self.local, port=0)
    self.workers = []
    for server in (self.local, self.remote):
      worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
      worker.start()
      self.workers.append(worker)
    self.addCleanup(self.close_servers)

  def close_servers(self):
    for server, worker in zip((self.remote, self.local), reversed(self.workers), strict=True):
      server.shutdown()
      worker.join(timeout=2)
      server.server_close()

  def request(self, server, path, *, method='GET', payload=None, host=None, origin=None, cookie=None):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
    headers = {'Host': host or f'127.0.0.1:{server.server_port}'}
    if payload is not None:
      headers['Content-Type'] = 'application/json'
    if origin is not None:
      headers['Origin'] = origin
    if cookie is not None:
      headers['Cookie'] = cookie
    connection.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
    response = connection.getresponse()
    result = response.status, response.read(), dict(response.getheaders())
    connection.close()
    return result

  def test_app_key_export_requires_direct_session_or_matching_gateway_cookie(self):
    for path in ('/api/galaxy/session', '/api/galaxy/session/qr.svg'):
      self.assertEqual(self.request(self.local, path)[0], 401)
    cookie = self.request(self.local, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    code, body, headers = self.request(self.local, '/api/galaxy/session', cookie=cookie)
    self.assertEqual((code, json.loads(body)), (200, {'paired': False}))
    self.assertEqual(headers['Cache-Control'], 'no-store')
    slug = self.pairing.pair(hashlib.sha256(b'password123').hexdigest())
    record = self.pairing.read()
    assert record is not None
    token = f"{slug}%3A{record['session']}"
    for path in ('/api/galaxy/session', '/api/galaxy/session/qr.svg'):
      self.assertEqual(self.request(self.local, path, cookie=cookie)[0], 200)
      with mock.patch('openpilot.starpilot.galaxy.server.direct_local_connection', return_value=False):
        self.assertEqual(self.request(self.local, path, cookie=cookie)[0], 403)
      self.assertEqual(self.request(self.remote, path, host='galaxy.firestar.link', cookie=cookie)[0], 403)
      self.assertEqual(self.request(self.remote, path, host='galaxy.firestar.link', cookie='galaxy_session=wrong')[0], 403)
      code, body, headers = self.request(self.remote, f'/{slug}' + path, host='galaxy.firestar.link', cookie='galaxy_session=' + token)
      self.assertEqual(code, 200)
      self.assertEqual(headers['Cache-Control'], 'no-store')
      if not path.endswith('.svg'):
        self.assertEqual(json.loads(body)['sessionToken'], token)
      else:
        self.assertIn(b'<svg', body)

  def test_gateway_session_cannot_access_routes(self):
    self.assertTrue(self.owner.configure('password123', lambda: True))
    slug = self.pairing.pair(hashlib.sha256(b'password123').hexdigest())
    record = self.pairing.read()
    assert record is not None
    cookie = f"galaxy_session={slug}%3A{record['session']}"
    host = f'{slug}.devices.local'
    _, body, _ = self.request(self.remote, '/api/auth/session', host=host, cookie=cookie)
    self.assertTrue(json.loads(body)['authenticated'])
    self.assertFalse(json.loads(body)['localAccess'])
    route = '00000042--abcdef1234'
    paths = ['/api/replay/catalog', '/api/replay/status?clientId=' + '1' * 32,
             f'/api/replay/detail?routeId={route}',
             '/api/replay/media?sessionId=' + '1' * 32 + '&clientId=' + '2' * 32 + '&epoch=1&sequence=0',
             '/api/recordings/local', f'/api/recordings/segment-summary?segmentName={route}--0',
             f'/api/recordings/logs/{route}', f'/api/recordings/files/{route}--0/rlog.zst',
             f'/api/recordings/media/{route}--0']
    for camera in ('qcamera', 'fcamera', 'dcamera', 'ecamera'):
      paths.extend((f'/api/recordings/media/{route}--0/{camera}', f'/api/recordings/route/{route}/{camera}'))
    for prefix in ('', f'/{slug}'):
      for path in paths:
        for method in ('GET', 'HEAD'):
          with self.subTest(prefix=prefix, path=path, method=method):
            status, body, _ = self.request(self.remote, prefix + path, method=method, host=host, cookie=cookie)
            self.assertEqual(status, 403)
            if method == 'GET':
              self.assertEqual(json.loads(body)['code'], 'local_only')
      for path in ('/api/replay/start', '/api/replay/control', '/api/replay/stop', '/api/recordings/action', '/api/recordings/delete-videos'):
        status, body, _ = self.request(self.remote, prefix + path, method='POST',
                                       payload={'clientId': '1' * 32}, host=host, cookie=cookie, origin='https://galaxy.firestar.link')
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)['code'], 'local_only')
    self.assertIsNone(getattr(self.local, 'recording_media_source', None), 'tunnel video cannot prepare a media source')

  def test_local_pair_and_remote_uses_gateway_auth_even_over_loopback(self):
    local_origin = f'http://127.0.0.1:{self.local.server_port}'
    local_cookie = self.request(self.local, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    status, body, _ = self.request(self.local, '/api/galaxy/pair', method='POST', payload={'password': '  password123  '},
                                   origin=local_origin, cookie=local_cookie)
    self.assertEqual(status, 200)
    url = json.loads(body)['url']
    slug = url.rsplit('/', 1)[1]
    record = self.pairing.read()
    assert record is not None
    assert record is not None
    self.assertEqual(record['slug'], slug)
    self.assertEqual(record['authHash'], hashlib.sha256(b'password123').hexdigest())
    qr_status, qr_body, qr_headers = self.request(self.local, '/api/galaxy/qr.svg', cookie=local_cookie)
    self.assertEqual(qr_status, 200)
    self.assertEqual(qr_headers['Content-Type'], 'image/svg+xml')
    self.assertIn(b'<svg', qr_body)
    self.assertEqual(self.request(self.remote, '/api/auth/session', host=f'{slug}.devices.local')[0], 200)
    remote_session = json.loads(self.request(self.remote, '/api/auth/session', host=f'{slug}.devices.local')[1])
    self.assertFalse(remote_session['authenticated'])
    self.assertFalse(remote_session['localAccess'])
    self.assertEqual(self.request(self.remote, '/api/system/monitor', host=f'{slug}.devices.local', cookie=local_cookie)[0], 401)
    gateway_cookie = f"galaxy_session={slug}%3A{record['session']}"
    self.assertEqual(self.request(self.remote, f'/{slug}', host=f'{slug}.devices.local')[0], 308)
    self.assertEqual(self.request(self.remote, f'/{slug}/api/system/monitor',
                                  host=f'{slug}.devices.local', cookie=gateway_cookie)[0], 200)
    remote_origin = 'https://galaxy.firestar.link'
    remote_cookie = 'galaxy_session=' + base64.urlsafe_b64encode(json.dumps({slug: record['session'],
                         'OtherComma123456': 'd' * 64}).encode()).decode().rstrip('=')
    for cookie in (gateway_cookie, remote_cookie):
      for path in ('/api/auth/session', f'/{slug}/api/auth/session'):
        status, body, headers = self.request(self.remote, path, host=f'{slug}.devices.local', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['authenticated'])
        self.assertTrue(json.loads(body)['gatewayAccess'])
        self.assertNotIn('Set-Cookie', headers)
      self.assertEqual(self.request(self.remote, '/api/system/monitor', host=f'{slug}.devices.local', cookie=cookie)[0], 200)
    for endpoint in ('login', 'logout'):
      payload = {'password': 'password123'} if endpoint == 'login' else {}
      status, body, headers = self.request(self.remote, f'/api/auth/{endpoint}', method='POST', payload=payload,
                                           host=f'{slug}.devices.local', origin=remote_origin, cookie=remote_cookie)
      self.assertEqual(status, 409)
      self.assertEqual(json.loads(body)['code'], 'gateway_auth_required')
      self.assertNotIn('Set-Cookie', headers)
    self.assertEqual(self.request(self.remote, '/api/galaxy/unpair', method='POST', payload={},
                                  host=f'{slug}.devices.local', origin=remote_origin, cookie=remote_cookie)[0], 403)
    self.assertEqual(self.request(self.local, '/api/galaxy/unpair', method='POST', payload={},
                                  origin=local_origin, cookie=local_cookie)[0], 200)
    self.assertEqual(self.request(self.remote, '/api/system/monitor', host=f'{slug}.devices.local', cookie=remote_cookie)[0], 403)
    status, body, _ = self.request(self.local, '/api/galaxy/pair', method='POST', payload={'password': 'password123'},
                                   origin=local_origin, cookie=local_cookie)
    self.assertEqual(status, 200)
    new_slug = json.loads(body)['url'].rsplit('/', 1)[1]
    self.assertEqual(self.request(self.remote, '/api/system/monitor', host=f'{new_slug}.devices.local', cookie=remote_cookie)[0], 401)
    self.assertEqual(self.request(self.remote, '/api/system/monitor', host=f'{new_slug}.devices.local', cookie=gateway_cookie)[0], 401)

  def test_imported_pairing_without_local_verifier_authorizes_gateway_and_revokes_async_session(self):
    from openpilot.starpilot.galaxy.remote import default_remote_pairing
    legacy = self.pairing.root.parent / 'legacy'
    legacy.mkdir()
    record = {'version': 1, 'slug': 'ExistingGalaxy01', 'authHash': hashlib.sha256(b'oldpw6').hexdigest(), 'session': 'b' * 64}
    for filename, key in (('glxyauth', 'authHash'), ('glxysession', 'session'), ('glxyslug', 'slug')):
      value = record[key]
      assert isinstance(value, str)
      (legacy / filename).write_text(value)
    with mock.patch('openpilot.starpilot.galaxy.access.legacy_galaxy_root', return_value=legacy), \
         mock.patch('openpilot.starpilot.storage.galaxy_storage_root', return_value=self.pairing.root):
      imported = default_remote_pairing()
    self.assertEqual(imported.read(), record)
    self.assertIsNone(self.owner.current_generation())
    gateway = make_gateway_auth_server(imported, 'disposable-dongle', port=0)
    worker = threading.Thread(target=gateway.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    worker.start()
    try:
      connection = http.client.HTTPConnection('127.0.0.1', gateway.server_port, timeout=2)
      connection.request('POST', '/glxylogin', body=record['authHash'])
      response = connection.getresponse()
      self.assertEqual(response.status, 200)
      self.assertEqual(json.loads(response.read()), {'dongle_id': 'disposable-dongle', 'token': record['session']})
      connection.close()
    finally:
      gateway.shutdown()
      worker.join(timeout=2)
      gateway.server_close()
    host = record['slug'] + '.devices.local'
    cookie = 'galaxy_session=' + record['slug'] + '%3A' + record['session']
    status, body, headers = self.request(self.remote, '/api/auth/session', host=host, cookie=cookie)
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(body), {'authenticated': True, 'localAccess': False, 'gatewayAccess': True, 'state': 'configured'})
    self.assertNotIn('Set-Cookie', headers)
    self.assertEqual(self.request(self.remote, '/api/galaxy/device-name', method='POST', host=host, cookie=cookie,
                                  origin='https://galaxy.firestar.link', payload={'name': 'Migrated comma'})[0], 200)
    self.assertEqual(json.loads(self.request(self.remote, '/api/galaxy/device-name', host=host, cookie=cookie)[1]),
                     {'name': 'Migrated comma'})
    wrong_cookie = 'galaxy_session=' + record['slug'] + '%3A' + 'a' * 64
    self.assertEqual(self.request(self.remote, '/api/galaxy/device-name', host=host, cookie=wrong_cookie)[0], 401)
    created = []
    def bluetooth_factory(authority, session_valid, **_kwargs):
      owner = mock.Mock()
      owner.session_valid = session_valid
      owner.identities = []
      def snapshot(*, session):
        owner.identities.append(session)
        return {'available': session_valid(session)}
      owner.snapshot.side_effect = snapshot
      created.append(owner)
      return owner
    with mock.patch('openpilot.starpilot.galaxy.server.BluetoothOwner', side_effect=bluetooth_factory), \
         mock.patch('openpilot.starpilot.galaxy.settings.LiveContextSource'):
      status, body, _ = self.request(self.remote, '/api/bluetooth/status', host=host, cookie=cookie)
    self.assertEqual(status, 200)
    self.assertIs(json.loads(body)['available'], True)
    bluetooth = created[0]
    identity = bluetooth.identities[0]
    self.assertTrue(bluetooth.session_valid(identity))
    self.assertIsNone(self.owner.current_generation())
    self.assertTrue(imported.unpair())
    self.assertFalse(bluetooth.session_valid(identity))
    self.assertEqual(self.request(self.remote, '/api/galaxy/device-name', host=host, cookie=cookie)[0], 403)
    with mock.patch('openpilot.starpilot.galaxy.access.legacy_galaxy_root', return_value=legacy), \
         mock.patch('openpilot.starpilot.storage.galaxy_storage_root', return_value=self.pairing.root):
      self.assertIsNone(default_remote_pairing().read())
    self.assertIsNone(self.owner.current_generation())

  def test_device_name_is_authenticated_persistent_and_independent_of_pairing(self):
    self.assertTrue(self.owner.configure('password123', lambda: True))
    slug = self.pairing.pair(hashlib.sha256(b'password123').hexdigest())
    assert slug is not None
    record = self.pairing.read()
    assert record is not None
    pairing_bytes = (self.pairing.root / self.pairing.FILE).read_bytes()
    cookie = 'galaxy_session=' + base64.urlsafe_b64encode(json.dumps({slug: record['session']}).encode()).decode().rstrip('=')
    host = f'{slug}.devices.local'
    path = '/api/galaxy/device-name'
    self.assertEqual(self.request(self.remote, path, host=host)[0], 401)
    self.assertEqual(self.request(self.remote, path, method='POST', host=host, cookie=cookie,
                                  payload={'name': 'Road comma'})[0], 403)
    result = self.request(self.remote, path, method='POST', host=host, cookie=cookie,
                          origin='https://galaxy.firestar.link', payload={'name': 'Road comma'})
    self.assertEqual(result[0], 200)
    self.assertEqual(json.loads(result[1]), {'name': 'Road comma'})
    self.assertEqual(json.loads(self.request(self.remote, path, host=host, cookie=cookie)[1]), {'name': 'Road comma'})
    from openpilot.starpilot.galaxy.device_name import DeviceName
    self.assertEqual(DeviceName(self.pairing.root).read(), 'Road comma')
    self.assertEqual((self.pairing.root / self.pairing.FILE).read_bytes(), pairing_bytes)
    for payload in ({'name': 'x' * 41}, {'name': 'bad\nname'}, {'name': []}, {'key': 'OtherParam', 'value': 'bad'}):
      self.assertEqual(self.request(self.remote, path, method='POST', host=host, cookie=cookie,
                                    origin='https://galaxy.firestar.link', payload=payload)[0], 400)
    other_cookie = 'galaxy_session=' + base64.urlsafe_b64encode(json.dumps({'OtherComma123456': record['session']}).encode()).decode().rstrip('=')
    self.assertEqual(self.request(self.remote, path, method='POST', host=host, cookie=other_cookie,
                                  origin='https://galaxy.firestar.link', payload={'name': 'Wrong comma'})[0], 401)
    self.assertEqual(DeviceName(self.pairing.root).read(), 'Road comma')
    self.assertEqual(self.request(self.remote, '/api/params', method='PUT', host=host, cookie=cookie,
                                  origin='https://galaxy.firestar.link', payload={'key': 'GalaxyDeviceName', 'value': 'Wrong'})[0], 405)
    self.assertTrue(self.pairing.unpair())
    self.assertEqual(DeviceName(self.pairing.root).read(), 'Road comma')

  def test_gateway_cookie_checks_this_comma_token_not_header_or_other_device(self):
    record: RemoteTokens = {'slug': 'CurrentComma1234', 'session': 'a' * 64}
    def encode(value):
      return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    self.assertTrue(gateway_cookie_valid(encode({record['slug']: record['session']}), record))
    self.assertTrue(gateway_cookie_valid(f"{record['slug']}%3A{record['session']}", record))
    for value in (None, '', 'x' * 4097, 'not+base64', encode([]), encode({'OtherComma123456': record['session']}),
                  encode({record['slug']: 'b' * 64}), encode({record['slug']: 123}), encode({record['slug']: 'a' * 63}),
                  'OtherComma123456:' + record['session'], '%ff'):
      with self.subTest(value=str(value)[:20]):
        self.assertFalse(gateway_cookie_valid(value, record))

  def test_first_pairing_sets_new_remote_password_when_local_verifier_already_exists(self):
    self.assertTrue(self.owner.configure('previous-local-password', lambda: True))
    previous_generation = self.owner.current_generation()
    origin = f'http://127.0.0.1:{self.local.server_port}'
    local_cookie = self.request(self.local, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    status, body, _ = self.request(self.local, '/api/galaxy/pair', method='POST', payload={'password': 'new-remote-password'},
                                   origin=origin, cookie=local_cookie)
    self.assertEqual(status, 200)
    self.assertNotEqual(self.owner.current_generation(), previous_generation)
    self.assertFalse(self.owner.verify('previous-local-password'))
    self.assertTrue(self.owner.verify('new-remote-password'))
    slug = json.loads(body)['url'].rsplit('/', 1)[1]
    record = self.pairing.read()
    assert record is not None
    self.assertEqual(record['authHash'], hashlib.sha256(b'new-remote-password').hexdigest())
    remote_cookie = 'galaxy_session=' + base64.urlsafe_b64encode(json.dumps({slug: record['session']}).encode()).decode().rstrip('=')
    result = self.request(self.remote, '/api/auth/session', host=f'{slug}.devices.local', cookie=remote_cookie)
    self.assertTrue(json.loads(result[1])['authenticated'])
    self.assertEqual(self.request(self.local, '/api/galaxy/pair', method='POST', payload={'password': 'another-password'},
                                  origin=origin, cookie=local_cookie)[0], 409)
    self.assertTrue(self.owner.verify('new-remote-password'))

  def test_failed_pairing_persistence_restores_previous_password_state(self):
    origin = f'http://127.0.0.1:{self.local.server_port}'
    cookie = self.request(self.local, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    self.assertTrue(self.owner.configure('previous-local-password', lambda: True))
    previous_generation = self.owner.current_generation()
    with mock.patch.object(self.pairing, 'pair', return_value=None):
      status, _, _ = self.request(self.local, '/api/galaxy/pair', method='POST', payload={'password': 'new-remote-password'},
                                  origin=origin, cookie=cookie)
    self.assertEqual(status, 409)
    self.assertIsNone(self.pairing.read())
    self.assertEqual(self.owner.current_generation(), previous_generation)
    self.assertTrue(self.owner.verify('previous-local-password'))
    self.assertFalse(self.owner.verify('new-remote-password'))

  def test_failed_first_pair_leaves_no_remote_verifier(self):
    origin = f'http://127.0.0.1:{self.local.server_port}'
    cookie = self.request(self.local, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    with mock.patch.object(self.pairing, 'pair', return_value=None):
      status, _, _ = self.request(self.local, '/api/galaxy/pair', method='POST', payload={'password': 'new-remote-password'},
                                  origin=origin, cookie=cookie)
    self.assertEqual(status, 409)
    self.assertIsNone(self.owner.current_generation())
    self.assertIsNone(self.pairing.read())

  def test_pairing_storage_exception_and_late_park_loss_restore_verifier(self):
    origin = f'http://127.0.0.1:{self.local.server_port}'
    cookie = self.request(self.local, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    self.assertTrue(self.owner.configure('previous-local-password', lambda: True))
    previous_generation = self.owner.current_generation()
    with mock.patch.object(self.pairing, 'pair', side_effect=OSError('disposable storage failure')):
      status, _, _ = self.request(self.local, '/api/galaxy/pair', method='POST', payload={'password': 'new-remote-password'},
                                  origin=origin, cookie=cookie)
    self.assertEqual(status, 409)
    self.assertEqual(self.owner.current_generation(), previous_generation)

    parked = [True]
    with tempfile.TemporaryDirectory() as directory:
      owner = GalaxyAccessOwner(Path(directory) / 'access')
      pairing = RemotePairing(Path(directory) / 'pairing')
      self.assertTrue(owner.configure('previous-local-password', lambda: True))
      generation = owner.current_generation()
      server = make_server(port=0, owner=owner, remote_pairing=pairing, parked=lambda: parked[0])
      worker = threading.Thread(target=server.serve_forever, daemon=True)
      worker.start()
      try:
        local_origin = f'http://127.0.0.1:{server.server_port}'
        local_cookie = self.request(server, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
        replace = owner.replace_for_pairing
        def lose_parked(password, authority):
          result = replace(password, authority)
          parked[0] = False
          return result
        with mock.patch.object(owner, 'replace_for_pairing', side_effect=lose_parked):
          status, _, _ = self.request(server, '/api/galaxy/pair', method='POST', payload={'password': 'new-remote-password'},
                                      origin=local_origin, cookie=local_cookie)
        self.assertEqual(status, 409)
        self.assertEqual(owner.current_generation(), generation)
        self.assertIsNone(pairing.read())
      finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()

  def test_gateway_auth_protocol_and_private_storage(self):
    self.assertIsNone(self.pairing.pair('wrong'))
    self.assertTrue(self.owner.configure('password123', lambda: True))
    slug = self.pairing.pair(hashlib.sha256(b'password123').hexdigest())
    assert slug is not None
    self.assertRegex(slug, r'^[A-Za-z0-9]{16}$')
    self.assertEqual((self.pairing.root / self.pairing.FILE).stat().st_mode & 0o777, 0o600)
    self.assertEqual(self.pairing.root.stat().st_mode & 0o777, 0o700)
    server = make_gateway_auth_server(self.pairing, 'disposable-dongle', port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
      auth_hash = hashlib.sha256(b'password123').hexdigest()
      conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
      conn.request('POST', '/glxylogin', body=auth_hash)
      response = conn.getresponse()
      self.assertEqual(response.status, 200)
      token = json.loads(response.read())['token']
      conn.close()
      conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
      conn.request('POST', '/glxyverify', body=token)
      response = conn.getresponse()
      self.assertEqual(response.status, 200)
      response.read()
      conn.close()
    finally:
      server.shutdown()
      worker.join(timeout=2)
      server.server_close()

  def test_verified_legacy_pairing_keeps_existing_link_and_session(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      legacy = root / 'old'
      legacy.mkdir()
      password = 'oldpw6'
      slug = 'ExistingGalaxy01'
      session = 'b' * 64
      (legacy / 'glxyauth').write_text(hashlib.sha256(password.encode()).hexdigest())
      (legacy / 'glxysession').write_text(session)
      (legacy / 'glxyslug').write_text(slug)
      owner = GalaxyAccessOwner(root / 'new', legacy_root=legacy)
      pairing = RemotePairing(root / 'pairing')
      server = make_server(port=0, owner=owner, remote_pairing=pairing, parked=lambda: True)
      worker = threading.Thread(target=server.serve_forever, daemon=True)
      worker.start()
      try:
        origin = f'http://127.0.0.1:{server.server_port}'
        cookie = self.request(server, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
        status, body, _ = self.request(server, '/api/galaxy/pair', method='POST', payload={'password': password},
                                       origin=origin, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['url'], f'https://galaxy.firestar.link/{slug}')
        record = pairing.read()
        assert record is not None
        assert record is not None
        self.assertEqual(record['session'], session)
        self.assertTrue(owner.verify(password))
        login_status, _, _ = self.request(server, '/api/auth/login', method='POST', payload={'password': password}, origin=origin)
        self.assertEqual(login_status, 200)
      finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()

  def test_direct_local_unpair_and_repair_with_new_password_without_a_local_verifier(self):
    legacy = self.pairing.root.parent / 'legacy'
    legacy.mkdir()
    old_password = 'oldpw6'
    slug = 'ExistingGalaxy01'
    old_token = 'b' * 64
    (legacy / 'glxyauth').write_text(hashlib.sha256(old_password.encode()).hexdigest())
    (legacy / 'glxyslug').write_text(slug)
    (legacy / 'glxysession').write_text(old_token)
    self.owner.legacy_root = legacy
    self.assertTrue(self.pairing.migrate_legacy(legacy))
    self.assertIsNone(self.owner.current_generation())
    origin = f'http://127.0.0.1:{self.local.server_port}'
    cookie = self.request(self.local, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
    local = {'method': 'POST', 'origin': origin, 'cookie': cookie}
    pair = '/api/galaxy/pair'
    new_password = {'password': 'different-password'}
    self.assertEqual(self.request(self.local, pair, payload=new_password, **local)[0], 409)
    self.assertEqual(self.request(self.local, '/api/galaxy/unpair', payload={}, **local)[0], 200)
    self.assertEqual(self.request(self.local, pair, method='POST', origin=origin, payload=new_password)[0], 401)
    self.assertEqual(self.request(self.local, pair, method='POST', origin='https://wrong.example',
                                  cookie=cookie, payload=new_password)[0], 403)
    with mock.patch('openpilot.starpilot.galaxy.server.direct_local_connection', return_value=False):
      self.assertEqual(self.request(self.local, pair, payload=new_password, **local)[0], 403)
    self.assertEqual(self.request(self.local, pair, payload={'password': 'other6'}, **local)[0], 400)
    self.assertIsNone(self.owner.current_generation())
    code, body, _ = self.request(self.local, pair, payload=new_password, **local)
    self.assertEqual(code, 200)
    record = self.pairing.read()
    assert record is not None
    self.assertNotEqual(record['slug'], slug)
    self.assertNotEqual(record['session'], old_token)
    self.assertTrue(json.loads(body)['url'].endswith(record['slug']))
    self.assertTrue(self.owner.verify(new_password['password']))
    self.assertFalse(self.owner.verify(old_password))

  def test_existing_local_verifier_does_not_hide_matching_legacy_link(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      legacy = root / 'old'
      legacy.mkdir()
      owner = GalaxyAccessOwner(root / 'new', legacy_root=legacy)
      self.assertTrue(owner.configure('separate-local-password', lambda: True))
      password = 'oldpw6'
      slug = 'ExistingGalaxy01'
      session = 'b' * 64
      (legacy / 'glxyauth').write_text(hashlib.sha256(password.encode()).hexdigest())
      (legacy / 'glxysession').write_text(session)
      (legacy / 'glxyslug').write_text(slug)
      pairing = RemotePairing(root / 'pairing')
      server = make_server(port=0, owner=owner, remote_pairing=pairing, parked=lambda: True)
      worker = threading.Thread(target=server.serve_forever, daemon=True)
      worker.start()
      try:
        origin = f'http://127.0.0.1:{server.server_port}'
        cookie = self.request(server, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
        status, body, _ = self.request(server, '/api/galaxy/pair', method='POST', payload={'password': password},
                                       origin=origin, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['url'], f'https://galaxy.firestar.link/{slug}')
        record = pairing.read()
        assert record is not None
        assert record is not None
        self.assertEqual(record['session'], session)
        self.assertTrue(owner.verify(password))
      finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()

  def test_pairing_authority_closes_with_server(self):
    with mock.patch('openpilot.starpilot.galaxy.settings.LiveContextSource') as authority:
      server = make_server(port=0, owner=self.owner, remote_pairing=self.pairing)
      self.assertIs(server.pairing_authority, authority.return_value)
      authority.return_value.parked.assert_called_once_with()
      worker = threading.Thread(target=server.serve_forever, daemon=True)
      worker.start()
      cookie = self.request(server, '/api/auth/session')[2]['Set-Cookie'].split(';', 1)[0]
      self.assertEqual(self.request(server, '/api/galaxy/status', cookie=cookie)[0], 200)
      self.assertEqual(authority.return_value.parked.call_count, 2)
      server.shutdown()
      worker.join(timeout=2)
      server.server_close()
      authority.return_value.close.assert_called_once_with()


if __name__ == '__main__':
  unittest.main()
