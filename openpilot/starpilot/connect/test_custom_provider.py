"""Custom cloud provider validation, isolation, and HTTP configuration tests."""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from openpilot.starpilot.connect import provider as p
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.drive_history import recording_details
from openpilot.starpilot.galaxy.server import make_server
from openpilot.tools.lib import api, auth, auth_config


class ParamsFiles:
  def __init__(self, root: Path):
    self.root = root
    root.mkdir()

  def get_param_path(self, key: str) -> str:
    return str(self.root / key)

  def check_key(self, key: str) -> None:
    assert key in (*p.STATE_KEYS, *p.TRANSIENT_KEYS, 'ConnectProvider')

  def put(self, key: str, value, block: bool = True) -> None:
    self.check_key(key)
    (self.root / key).write_bytes(value.encode() if isinstance(value, str) else value)

  def remove(self, key: str) -> None:
    (self.root / key).unlink(missing_ok=True)

  def get(self, key: str):
    path = self.root / key
    return path.read_text() if path.exists() else None


class TestCustomUrlValidation(unittest.TestCase):
  def test_build_custom_config_derives_endpoints_and_normalizes(self):
    config = p.build_custom_config('https://Cloud.Example.com:8443/prefix/')
    self.assertEqual(config, {
      'api': 'https://cloud.example.com:8443/prefix',
      'athena': 'wss://cloud.example.com:8443/prefix',
      'web': 'https://cloud.example.com:8443/prefix',
    })

  def test_build_custom_config_accepts_explicit_overrides(self):
    config = p.build_custom_config({
      'url': 'api.custom.test',
      'athena': 'wss://ws.custom.test',
      'web': 'https://app.custom.test/',
    })
    self.assertEqual(config, {
      'api': 'https://api.custom.test',
      'athena': 'wss://ws.custom.test',
      'web': 'https://app.custom.test',
    })

  def test_rejects_insecure_or_malformed_urls(self):
    for bad in ('', '   ', 'http://insecure.test', 'https://user@host.test', 'https://host.test?q=1',
                'https://host.test#frag', 'https://host.test/with space', 'ws://host.test'):
      with self.assertRaises(ValueError):
        p.build_custom_config(bad)
    with self.assertRaises(ValueError):
      p.build_custom_config({'url': 'https://api.test', 'athena': 'https://not-wss.test'})
    with self.assertRaises(ValueError):
      p.build_custom_config({'url': 'https://api.test', 'extra': '1'})


class TestCustomProviderLifecycle(unittest.TestCase):
  def test_reboot_switch_identity_isolation_and_endpoint_change(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / 'cloud'
      params = ParamsFiles(Path(directory) / 'params')
      params.put('DongleId', '0123456789abcdef')

      initial = p.status(root)
      self.assertEqual([item['id'] for item in initial['providers']], ['comma', 'konik', 'custom'])

      with self.assertRaises(ValueError):
        p.select_provider('custom', initial['revision'], lambda: True, root=root, boot=lambda: 'b1')

      pending = p.select_provider('custom', initial['revision'], lambda: True,
                                  custom={'url': 'https://one.example.test'}, root=root, boot=lambda: 'b1')
      self.assertEqual(pending['active'], 'comma')
      self.assertEqual(pending['selected'], 'custom')
      self.assertTrue(pending['restartRequired'])
      self.assertEqual(pending['custom']['api'], 'https://one.example.test')

      p.activate_at_boot(params, root=root, boot=lambda: 'b1')
      self.assertEqual(params.get('DongleId'), '0123456789abcdef')

      p.activate_at_boot(params, root=root, boot=lambda: 'b2')
      self.assertIsNone(params.get('DongleId'))
      self.assertEqual(params.get('ConnectProvider'), 'custom')
      params.put('DongleId', 'aaaaaaaaaaaaaaaa')

      second = p.status(root)
      self.assertEqual(second['active'], 'custom')
      self.assertFalse(second['restartRequired'])

      # Changing custom URL while custom is active defers new endpoints & clears identity on next reboot.
      edited = p.select_provider('custom', second['revision'], lambda: True,
                                 custom={'url': 'https://two.example.test'}, root=root, boot=lambda: 'b2')
      self.assertTrue(edited['restartRequired'])
      self.assertEqual(edited['custom']['api'], 'https://two.example.test')
      self.assertEqual(p.configuration(root)['custom']['api'], 'https://one.example.test')

      p.activate_at_boot(params, root=root, boot=lambda: 'b3')
      self.assertIsNone(params.get('DongleId'))
      self.assertEqual(p.status(root)['custom']['api'], 'https://two.example.test')

      # Switching back to comma restores its exact saved DongleId.
      rev = p.status(root)['revision']
      p.select_provider('comma', rev, lambda: True, root=root, boot=lambda: 'b3')
      p.activate_at_boot(params, root=root, boot=lambda: 'b4')
      self.assertEqual(params.get('DongleId'), '0123456789abcdef')
      self.assertEqual(params.get('ConnectProvider'), 'comma')

  def test_custom_key_pair_and_tools_isolation(self):
    custom = p.custom_provider({
      'api': 'https://custom-api.test',
      'athena': 'wss://custom-athena.test',
      'web': 'https://custom-web.test',
    })
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      with patch.object(p, 'root_path', return_value=root / 'cloud'):
        konik_pair = p.provider_key_pair('konik')
        custom_pair = p.provider_key_pair('custom')
        self.assertEqual(p.provider_key_pair('custom'), custom_pair)
        self.assertNotEqual(custom_pair[1], konik_pair[1])

      with patch.object(auth_config.Paths, 'config_root', return_value=str(root / 'cfg')):
        with patch.object(auth_config, 'active_provider', return_value=custom):
          self.assertTrue(auth_config.auth_path().endswith('auth-custom.json'))
          auth_config.set_token('custom-secret')
          self.assertEqual(auth_config.get_token(), 'custom-secret')
        with patch.object(auth_config, 'active_provider', return_value=p.PROVIDERS['konik']):
          self.assertIsNone(auth_config.get_token())

      with patch.object(auth, 'active_provider', return_value=custom):
        self.assertIn('https://custom-web.test', auth.login('github')['error'])

      with patch.object(api, 'active_provider', return_value=custom):
        client = api.CommaApi('custom-secret')
        self.assertEqual(client.api_host, 'https://custom-api.test')

      with patch.object(p, 'configuration', return_value={
        'active': 'custom',
        'selected': 'custom',
        'requestedBoot': '',
        'custom': {'api': custom.api, 'athena': custom.athena, 'web': custom.web},
      }):
        details = recording_details({'routes': [{'routeId': '2026-10-01--12-00-00', 'provider': 'custom'}]}, {}, None,
                                    device_ids={'custom': '0123456789abcdef'})
        self.assertEqual(details['routes'][0]['connectUrl'], 'https://custom-web.test/0123456789abcdef/2026-10-01--12-00-00')


class TestCustomProviderHTTP(unittest.TestCase):
  def test_http_custom_provider_selection(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      access = GalaxyAccessOwner(root / 'access')
      access.configure('password123', lambda: True)
      cloud_root = root / 'cloud'

      class Cloud:
        def status(self):
          return p.status(cloud_root)

        def select(self, name, revision, authorized, *, custom=None):
          return p.select_provider(name, revision, authorized, custom=custom, root=cloud_root, boot=lambda: 'test-boot')

      server = make_server(port=0, owner=access, parked=lambda: True, cloud_offroad=lambda: True, cloud_provider=Cloud())
      worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
      worker.start()

      def request(path, payload=None, cookie=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        headers = {'Forwarded': 'for=203.0.113.8', 'Origin': f'http://127.0.0.1:{server.server_port}',
                   'Content-Type': 'application/json'}
        if cookie:
          headers['Cookie'] = cookie
        try:
          connection.request('GET' if payload is None else 'POST', path, None if payload is None else json.dumps(payload), headers)
          response = connection.getresponse()
          return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
          connection.close()

      try:
        code, _, headers = request('/api/auth/login', {'password': 'password123'})
        self.assertEqual(code, 200)
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        code, state, _ = request('/api/connect/provider', cookie=cookie)
        self.assertEqual(code, 200)

        code, _, _ = request('/api/connect/provider', {
          'provider': 'comma',
          'custom': {'url': 'https://cloud.example.test'},
          'revision': state['revision'],
          'confirmed': True,
        }, cookie)
        self.assertEqual(code, 409)

        code, updated, _ = request('/api/connect/provider', {
          'provider': 'custom',
          'custom': {'url': 'https://cloud.example.test:8443'},
          'revision': state['revision'],
          'confirmed': True,
        }, cookie)
        self.assertEqual(code, 200)
        self.assertEqual(updated['selected'], 'custom')
        self.assertEqual(updated['custom']['api'], 'https://cloud.example.test:8443')
        self.assertEqual(updated['custom']['athena'], 'wss://cloud.example.test:8443')
      finally:
        server.shutdown()
        server.server_close()
        worker.join(2)
