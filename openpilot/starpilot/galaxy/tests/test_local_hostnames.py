"""LAN DNS admission must not turn arbitrary resolving Host names into authority."""

import json
import socket
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from openpilot.starpilot.galaxy.http_server import allowed_authority, direct_local_connection
from openpilot.starpilot.galaxy.local_hostnames import LocalHostnames, discover_names, dns_name, search_domains


DESTINATION = '10.0.2.244'


def answer(address):
  return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 0))


class LocalHostnameTest(unittest.TestCase):
  def test_ptr_identity_not_request_host_is_forward_confirmed(self):
    reverse = Mock(return_value=('comma.home.example', ['comma-alias.home.example'], [DESTINATION]))
    forward = Mock(side_effect=lambda name, *_args, **_kwargs:
                   [answer(DESTINATION if name in ('comma.home.example.', 'evil.example.') else '10.0.2.245')])
    names = discover_names(DESTINATION, hostname='other', reverse=reverse, forward=forward)
    self.assertEqual(names, ['comma.home.example'])
    reverse.assert_called_once_with(DESTINATION)
    self.assertNotIn('evil.example.', [call.args[0] for call in forward.call_args_list])
    source = SimpleNamespace(names=lambda destination: names if destination == DESTINATION else [])
    self.assertTrue(allowed_authority('comma.home.example:8082', DESTINATION, 8082, local_hostnames=source))
    for host in ('evil.example:8082', 'comma.evil.example:8082', 'anything.home.example:8082',
                 'comma-alias.home.example:8082', 'comma.home.example:9999', 'comma.home.example'):
      self.assertFalse(allowed_authority(host, DESTINATION, 8082, local_hostnames=source), host)
    self.assertFalse(allowed_authority('comma.home.example:8082', '10.0.2.245', 8082, local_hostnames=source))

  def test_a_only_os_hostname_plus_local_search_domain(self):
    reverse = Mock(side_effect=socket.herror('No PTR'))
    forward = Mock(side_effect=lambda name, *_args, **_kwargs:
                   [answer(DESTINATION)] if name == 'comma.home.example.' else [])
    self.assertEqual(discover_names(DESTINATION, hostname='comma', domains=['home.example'],
                                    reverse=reverse, forward=forward), ['comma.home.example'])
    self.assertEqual(search_domains('# search evil.example\nsearch home.example lan.example # comment\n'),
                     ['home.example', 'lan.example'])
    self.assertEqual(discover_names(DESTINATION, hostname='different', domains=['home.example'],
                                    reverse=reverse, forward=forward), [])

  def test_fail_closed_for_public_loopback_errors_and_mixed_answers(self):
    for destination in ('8.8.8.8', '127.0.0.1', '0.0.0.0', '::1', '100.64.0.1', 'invalid'):
      reverse, forward = Mock(), Mock()
      self.assertEqual(discover_names(destination, reverse=reverse, forward=forward), [])
      reverse.assert_not_called()
      forward.assert_not_called()
    for result in ([], [answer('10.0.2.245')], [answer(DESTINATION), answer('8.8.8.8')]):
      self.assertEqual(discover_names(DESTINATION, hostname='comma', reverse=Mock(side_effect=OSError()),
                                      forward=Mock(return_value=result)), [])
    self.assertEqual(discover_names(DESTINATION, hostname='comma', reverse=Mock(side_effect=OSError()),
                                    forward=Mock(side_effect=socket.gaierror())), [])

  def test_malformed_names_authorities_and_numeric_ptr_are_rejected(self):
    for name in ('', 'a..b', '-comma.example', 'comma-.example', '10.0.2.244', '123', 'localhost',
                 'comma.localhost', 'comma.example:8082', 'user@comma.example', 'comma.example/path',
                 ' comma.example', 'comma.example\n', 'a' * 64 + '.example', 'cömmä.example'):
      self.assertIsNone(dns_name(name), name)
    source = SimpleNamespace(names=lambda _: ['comma.home.example'])
    for host in ('comma.home.example:8082.evil', 'comma.home.example:08082', 'user@comma.home.example:8082',
                 'comma.home.example/path:8082', ' comma.home.example:8082'):
      self.assertFalse(allowed_authority(host, DESTINATION, 8082, local_hostnames=source), host)
    self.assertEqual(dns_name('Comma.Home.Example.'), 'comma.home.example')

  def test_bounded_lookup_and_destination_cache_expiry(self):
    now = [100.0]
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps(['comma.home.example']).encode()))
    source = LocalHostnames(run=run, clock=lambda: now[0])
    self.assertEqual(source.names(DESTINATION), ('comma.home.example',))
    self.assertEqual(source.names(DESTINATION), ('comma.home.example',))
    self.assertEqual(run.call_count, 1)
    self.assertEqual(run.call_args.args[0][-1], DESTINATION)
    self.assertEqual(run.call_args.kwargs['timeout'], 1.0)
    run.return_value.stdout = b'[]'
    self.assertEqual(source.names('10.0.2.245'), ())
    now[0] += 5.0
    self.assertEqual(source.names(DESTINATION), ())
    self.assertEqual(run.call_count, 3)
    for failure in (subprocess.TimeoutExpired('dns', 1), OSError('resolver unavailable')):
      now[0] += 5.0
      run.side_effect = failure
      self.assertEqual(source.names(DESTINATION), ())
    self.assertTrue(allowed_authority(f'{DESTINATION}:8082', DESTINATION, 8082, local_hostnames=source))

  def test_bad_child_results_and_concurrent_lookup_fail_closed(self):
    for stdout in (b'bad JSON', b'{}', b'[null]', b'["evil.example:8082"]', b'["10.0.2.244"]', b'x' * 8193):
      self.assertEqual(LocalHostnames(run=Mock(return_value=SimpleNamespace(stdout=stdout))).names(DESTINATION), ())
    source = LocalHostnames(run=Mock())
    with source.lock:
      self.assertEqual(source.names(DESTINATION), ())
    source.run.assert_not_called()

  def test_slow_ptr_keeps_names_already_independently_confirmed(self):
    published = []
    reverse = Mock(side_effect=lambda _: self.assertEqual(published, [['comma.home.example']]) or ('comma.home.example', [], []))
    names = discover_names(DESTINATION, hostname='comma.home.example', reverse=reverse,
                            forward=Mock(return_value=[answer(DESTINATION)]), publish=lambda names: published.append(list(names)))
    self.assertEqual(names, ['comma.home.example'])
    run = Mock(side_effect=subprocess.TimeoutExpired('dns', 1, output=b'["comma.home.example"]\n'))
    self.assertEqual(LocalHostnames(run=run).names(DESTINATION), ('comma.home.example',))

  def test_each_name_is_published_before_another_lookup(self):
    published = []
    def forward(name, *_args, **_kwargs):
      if name == 'comma.slow.example.':
        self.assertEqual(published, [['comma'], ['comma', 'comma.home.example']])
        raise socket.gaierror()
      return [answer(DESTINATION)]
    self.assertEqual(discover_names(DESTINATION, hostname='comma', domains=['home.example', 'slow.example'],
                                    reverse=Mock(side_effect=OSError()), forward=forward,
                                    publish=lambda names: published.append(list(names))), ['comma', 'comma.home.example'])
    stdout = b'["comma"]\n["comma.home.example"]\n'
    run = Mock(side_effect=subprocess.TimeoutExpired('dns', 1, output=stdout))
    self.assertEqual(LocalHostnames(run=run).names(DESTINATION), ('comma', 'comma.home.example'))

  def test_portless_is_explicit_and_proxy_boundary_stays_separate(self):
    source = SimpleNamespace(names=lambda _: ['comma.home.example'])
    self.assertTrue(allowed_authority('comma.home.example', DESTINATION, 8082, local_hostnames=source, allow_portless=True))
    self.assertFalse(allowed_authority('evil.example', DESTINATION, 8082, local_hostnames=source, allow_portless=True))
    for headers in ({'Forwarded': ''}, {'X-Forwarded-For': '10.0.2.1'}, {'X-Forwarded-Host': 'comma.home.example'}):
      self.assertFalse(direct_local_connection('10.0.2.1', DESTINATION, headers))
    self.assertFalse(direct_local_connection('8.8.8.8', DESTINATION, {}))


if __name__ == '__main__':
  unittest.main()
