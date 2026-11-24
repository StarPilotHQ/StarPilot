"""Pure bounded network/launcher readers; never imports native vehicle modules."""

import ast
from base64 import b64decode
from io import BytesIO
from zipfile import ZipFile
from pathlib import Path
import threading
import json
import subprocess
from types import SimpleNamespace
import unittest

from openpilot.starpilot.galaxy.local_access import LocalAccess, connection_capture
from openpilot.starpilot.galaxy.tmux_live import TmuxLive, MAX_BYTES


def interface(name='wlan0', local='192.168.50.21', flags=None):
  return {'ifname': name, 'flags': ['UP'] if flags is None else flags, 'addr_info': [{'family': 'inet', 'scope': 'global', 'local': local}]}


class TestNetworkDiagnostics(unittest.TestCase):
  def addresses(self, inventory):
    calls = []

    def run(args, **kwargs):
      calls.append((args, kwargs))
      return SimpleNamespace(stdout=json.dumps(inventory).encode())

    return LocalAccess(run=run), calls

  def console(self, raw, panes=b'%0\t0\t0\n'):
    calls = []

    def run(args, **kwargs):
      calls.append((args, kwargs))
      if args[1] == 'list-panes':
        return SimpleNamespace(stdout=panes)
      kwargs['stdout'].write(raw)
      return SimpleNamespace()

    return TmuxLive(run=run), calls

  def test_active_lan_addresses_deduplicate_and_exclude_loopback_public(self):
    source, calls = self.addresses(
      [interface(), interface('eth0'), interface('lo', '127.0.0.1', ['UP', 'LOOPBACK']), interface('vpn', '100.64.0.1'), interface('down', flags=[])]
    )
    result = source.snapshot()
    self.assertTrue(result['available'])
    self.assertEqual(len(result['addresses']), 1)
    self.assertEqual(result['addresses'][0]['url'], 'http://192.168.50.21:8082/')
    self.assertEqual(calls[0][0], ['ip', '-j', '-4', 'address', 'show', 'up'])
    self.assertEqual(calls[0][1]['timeout'], 1)
    source.snapshot()
    self.assertEqual(len(calls), 1)

  def test_network_result_caps_at_frontend_contract(self):
    source, _ = self.addresses([interface(f'eth{i}', f'10.0.0.{i + 1}') for i in range(64)])
    result = source.snapshot()
    self.assertEqual(len(result['addresses']), 32)

  def test_malformed_interfaces_skip_without_inventing_addresses(self):
    source, _ = self.addresses([None, 'bad', {'ifname': 'x', 'flags': 'UP', 'addr_info': []}, {'ifname': 'x', 'flags': ['UP'], 'addr_info': {}}, interface()])
    self.assertEqual(len(source.snapshot()['addresses']), 1)
    source, _ = self.addresses({'bad': 'inventory'})
    self.assertFalse(source.snapshot()['available'])

  def test_large_inventory_and_reader_errors_fail_closed(self):
    for run in [lambda *_a, **_k: SimpleNamespace(stdout=b'x' * 65537), lambda *_a, **_k: (_ for _ in ()).throw(subprocess.TimeoutExpired('ip', 1))]:
      self.assertFalse(LocalAccess(run=run).snapshot()['available'])

  def test_connection_capture_includes_only_known_advertiser_events(self):
    source, _ = self.addresses([interface()])
    commands = []

    def run(args, **_kwargs):
      commands.append(args)
      if args[0] == 'systemctl':
        return SimpleNamespace(stdout=b'ActiveState=active\nSubState=running\nNRestarts=3\n')
      if args[0] == 'ip':
        state = [{'dev': 'wlan0', 'gateway': '192.168.50.1'}] if 'route' in args else [
          {'ifname': 'wlan0', 'operstate': 'UP', 'address': 'de:ad:be:ef:00:01',
           'addr_info': [{'local': '192.168.50.21', 'prefixlen': 24}, {'local': '8.8.8.8', 'prefixlen': 32}]}]
        return SimpleNamespace(stdout=json.dumps(state).encode())
      if args[0] == 'ss':
        return SimpleNamespace(stdout=b'LISTEN 0 20 0.0.0.0:8082 0.0.0.0:*')
      if 'galaxy-ble-fork.service' in args:
        return SimpleNamespace(stdout=b'{"MESSAGE":"Phone claimed pairing window"}')
      rows = [{'MESSAGE': 'Galaxy Wi-Fi discovery available on 1 local interface(s)'},
              {'MESSAGE': 'Wi-Fi discovery unavailable: TimeoutExpired'},
              {'MESSAGE': 'Wi-Fi discovery unavailable: cookie=private'},
              {'MESSAGE': 'Tunnel password: private'}]
      return SimpleNamespace(stdout='\n'.join(map(json.dumps, rows)).encode())

    capture = connection_capture(source.snapshot(), run=run)
    self.assertEqual(capture['schemaVersion'], 1)
    self.assertIn('http://192.168.50.21:8082/', capture['text'])
    self.assertIn('Bonjour advertiser: active', capture['text'])
    self.assertIn('Galaxy Wi-Fi discovery available on 1 local interface(s)', capture['text'])
    self.assertIn('Wi-Fi discovery unavailable: TimeoutExpired', capture['text'])
    self.assertNotIn('Tunnel password:', capture['text'])
    self.assertTrue(any('galaxy-lan-discovery.service' in args for args in commands))
    with ZipFile(BytesIO(b64decode(capture['zipBase64']))) as archive:
      self.assertEqual(archive.namelist(), ['connection-logs.txt'])
      report = archive.read('connection-logs.txt').decode()
    self.assertIn('192.168.50.21/24', report)
    self.assertIn('IPv4 default route interfaces: wlan0', report)
    self.assertIn('TCP port 8082 listening: all interfaces', report)
    self.assertIn('NRestarts=3', report)
    self.assertIn('Phone claimed pairing window', report)
    for private_value in ('de:ad:be:ef:00:01', '192.168.50.1', '8.8.8.8', 'cookie=private'):
      self.assertNotIn(private_value, report)

    def missing(*_args, **_kwargs):
      raise FileNotFoundError()

    partial = connection_capture(source.snapshot(), run=missing)['text']
    self.assertIn('Bonjour advertiser: unavailable', partial)
    self.assertIn('Advertiser journal unavailable', partial)
    self.assertIn('192.168.50.21', partial)

  def test_console_uses_exact_readonly_launcher_pane_and_cache(self):
    source, calls = self.console(b'first\nsecond\n')
    result = source.snapshot()
    self.assertTrue(result['available'])
    self.assertEqual(result['text'], 'first\nsecond')
    self.assertEqual(result['pane'], 'comma:0.0')
    self.assertEqual(calls[1][0], ['tmux', 'capture-pane', '-p', '-t', '%0', '-S', '-300', '-E', '-'])
    source.snapshot()
    self.assertEqual(len(calls), 2)

  def test_console_bounds_utf8_replacement_bytes_and_line_count(self):
    for raw in [b'\xff' * MAX_BYTES, b'\xc3' + b'a' * MAX_BYTES, b'line\n' * 500]:
      source, _ = self.console(raw)
      result = source.snapshot()
      self.assertTrue(result['available'])
      self.assertTrue(result['truncated'])
      self.assertLessEqual(len(result['text'].encode()), MAX_BYTES)
      self.assertLessEqual(len(result['text'].splitlines()), 300)

  def test_dead_or_malformed_pane_does_not_capture(self):
    for panes in [b'%0\t0\t1\n', b'bad\t0\t0\n', b'%1\t1\t0\n']:
      source, calls = self.console(b'', panes)
      self.assertFalse(source.snapshot()['available'])
      self.assertEqual(len(calls), 1)

  def test_settings_diagnostics_projects_only_existing_allowlisted_rows(self):
    path = Path(__file__).parents[1] / 'settings.py'
    tree = ast.parse(path.read_text())
    method = next(
      node for cls in tree.body if isinstance(cls, ast.ClassDef) for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'diagnostics'
    )
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    pages = []
    ctx = SimpleNamespace(cp=SimpleNamespace(carFingerprint='car', brand='brand', openpilotLongitudinalControl=True, steerControlType='torque'), metric=True)

    def state(page, received):
      self.assertIs(received, ctx)
      pages.append(page)
      return SimpleNamespace(title=page, rows=[SimpleNamespace(label='Setting', value='Shown', source=b'not-public')])

    owner = SimpleNamespace(lock=threading.Lock(), context=SimpleNamespace(sample=lambda: ctx), _state=state)
    result = namespace['diagnostics'](owner)
    self.assertEqual(pages, ['torque', 'torque/developer', 'lane', 'lane_change', 'aol', 'conditional', 'profiles', 'slc', 'curve'])
    self.assertEqual(result['sections'][0]['rows'], [{'label': 'Setting', 'value': 'Shown'}])
    self.assertTrue(result['vehicle']['available'])
    self.assertNotIn('not-public', json.dumps(result))
