"""Explicit reverse-proxy admission without trusting forwarded client identity."""

import unittest
from tempfile import TemporaryDirectory

from openpilot.starpilot.galaxy.http_server import ReverseProxy, allowed_authority
from openpilot.starpilot.galaxy.reverse_proxy import ReverseProxySettings


class ReverseProxyTest(unittest.TestCase):
  def test_opt_in_and_invalid_configuration(self):
    for peer, origin in (('', 'https://galaxy.example'), ('proxy.example', 'https://galaxy.example'),
                         ('192.168.1.2', ''), ('192.168.1.2', 'http://galaxy.example'),
                         ('192.168.1.2', 'https://galaxy.example/'),
                         ('192.168.1.2', 'https://user@galaxy.example'),
                         ('192.168.1.2', 'https://galaxy.example?query=1')):
      with self.subTest(peer=peer, origin=origin), self.assertRaises(ValueError):
        ReverseProxy.validated(peer, origin)

  def test_persistence_disable_stale_revision_and_authority(self):
    with TemporaryDirectory() as root:
      owner = ReverseProxySettings(root)
      self.assertIsNone(owner.current)
      payload = {**owner.snapshot(), 'enabled': True, 'address': '192.168.1.2',
                 'origin': 'https://galaxy.example', 'confirmed': True}
      with self.assertRaises(PermissionError):
        owner.save(payload, lambda: False)
      saved = owner.save(payload, lambda: True)
      self.assertEqual(ReverseProxySettings(root).current.origin, payload['origin'])
      with self.assertRaises(ValueError):
        owner.save(payload, lambda: True)
      calls = iter((True, False))
      with self.assertRaises(PermissionError):
        owner.save({**saved, 'enabled': False, 'confirmed': True}, lambda: next(calls))
      self.assertIsNotNone(owner.current)
      owner.save({**saved, 'enabled': False, 'confirmed': True}, lambda: True)
      self.assertIsNone(ReverseProxySettings(root).current)

  def test_exact_peer_host_and_origin(self):
    proxy = ReverseProxy('192.168.1.2', 'https://galaxy.example:8443')
    self.assertTrue(proxy.allows('192.168.1.2', 'galaxy.example:8443', None))
    self.assertTrue(proxy.allows('192.168.1.2', 'galaxy.example:8443', proxy.origin, mutation=True))
    for peer, host, origin in (
      ('192.168.1.3', 'galaxy.example:8443', proxy.origin),
      (proxy.peer, 'evil.example', proxy.origin),
      (proxy.peer, 'galaxy.example:8443', 'https://evil.example'),
      (proxy.peer, 'galaxy.example:8443', 'http://galaxy.example:8443'),
    ):
      self.assertFalse(proxy.allows(peer, host, origin))
    self.assertFalse(proxy.allows(proxy.peer, 'galaxy.example:8443', None, mutation=True))
    self.assertTrue(allowed_authority('192.168.1.10:8082', '192.168.1.10', 8082))
    self.assertFalse(allowed_authority('evil.example:8082', '192.168.1.10', 8082))
