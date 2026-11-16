"""Saved Wi-Fi order and per-network choices: only Wi-Fi profiles are ever changed."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.starpilot.galaxy import network_prefs
from openpilot.starpilot.galaxy.network_prefs import NetworkManagerCli, NetworkSettings, split_terse

HOME, PHONE, AP = (f'{digit * 8}-{digit * 4}-{digit * 4}-{digit * 4}-{digit * 12}' for digit in 'abc')
LISTING = '\n'.join([f'{HOME}:802-11-wireless:yes:0:yes:openpilot connection Home',
                     'ffffffff-1111-2222-3333-444444444444:gsm:yes:2:no:esim',
                     f'{AP}:802-11-wireless:no:0:no:Hotspot',
                     f'{PHONE}:802-11-wireless:yes:5:no:openpilot connection Phone\\: 5G', ''])


def details(metric='-1'):
  rows = [(HOME, 'Home', 'infrastructure'), (AP, 'weedle', 'ap'), (PHONE, 'Phone: 5G', 'infrastructure')]
  return '\n\n'.join(f'connection.uuid:{uuid}\n802-11-wireless.ssid:{name.replace(":", chr(92) + ":")}\n802-11-wireless.mode:{mode}\n' +
                     f'connection.metered:unknown\nipv4.route-metric:{metric}' for uuid, name, mode in rows) + '\n'


def fake_run(calls, metric='-1'):
  def run(argv, **_kwargs):
    calls.append(argv[1:])
    if 'modify' in argv or 'reapply' in argv:
      return SimpleNamespace(returncode=0, stdout='', stderr='')
    if 'wifi' in argv:
      return SimpleNamespace(returncode=0, stdout='Home:80\nHome:40\n', stderr='')
    return SimpleNamespace(returncode=0, stdout=details(metric) if 'connection.uuid' in ' '.join(argv) else LISTING, stderr='')
  return run


def settings(tmp_path, calls, metric='-1'):
  params = Mock()
  params.get_bool.return_value = False
  return NetworkSettings(tmp_path, NetworkManagerCli(fake_run(calls, metric)), params, link=lambda: {'link': 'wifi'}), params


def changes(calls):
  return [call for call in calls if 'modify' in call or 'reapply' in call]


def test_terse_lines_keep_colons_inside_names():
  assert split_terse('a:b:name\\: with\\\\colon:x', 3) == ['a', 'b', 'name: with\\colon:x']


def test_only_client_wifi_networks_are_listed_best_priority_first(tmp_path):
  made, _ = settings(tmp_path, [])
  snapshot = made.snapshot(True)
  assert [(n['name'], n['connected'], n['signal'], n['whileDriving']) for n in snapshot['networks']] == [
    ('Phone: 5G', False, None, True), ('Home', True, 80, True)]
  assert snapshot['prefer'] == 'wifi' and snapshot['cellular']['enabled'] and snapshot['pauseHotspot']


def test_order_writes_priorities_and_rejects_a_stale_list(tmp_path):
  calls = []
  made, _ = settings(tmp_path, calls)
  made.apply({'action': 'order', 'uuids': [HOME, PHONE]}, lambda: True)
  assert changes(calls) == [['connection', 'modify', HOME, 'connection.autoconnect-priority', '2'],
                            ['connection', 'modify', PHONE, 'connection.autoconnect-priority', '1']]
  with pytest.raises(FileExistsError):
    made.apply({'action': 'order', 'uuids': [HOME]}, lambda: True)
  with pytest.raises(FileExistsError):
    made.apply({'action': 'order', 'uuids': [HOME, AP]}, lambda: True)


def test_per_network_choices(tmp_path):
  calls = []
  made, _ = settings(tmp_path, calls)
  snapshot = made.apply({'action': 'network', 'uuid': PHONE, 'setting': 'whileDriving', 'value': False}, lambda: True)
  assert [n['whileDriving'] for n in snapshot['networks']] == [False, True] and changes(calls) == []
  assert network_prefs.read_prefs(tmp_path)['parkedOnly'] == [PHONE]
  made.apply({'action': 'network', 'uuid': HOME, 'setting': 'metered', 'value': 'Metered'}, lambda: True)
  made.apply({'action': 'network', 'uuid': PHONE, 'setting': 'autoJoin', 'value': False}, lambda: True)
  assert changes(calls) == [['connection', 'modify', HOME, 'connection.metered', 'yes'], ['device', 'reapply', 'wlan0'],
                            ['connection', 'modify', PHONE, 'connection.autoconnect', 'no']]
  for bad in ({'action': 'network', 'uuid': AP, 'setting': 'autoJoin', 'value': True},):
    with pytest.raises(FileExistsError):
      made.apply(bad, lambda: True)
  for bad in ({'action': 'network', 'uuid': HOME, 'setting': 'metered', 'value': 'yes'}, {'action': 'reboot'}, []):
    with pytest.raises(ValueError):
      made.apply(bad, lambda: True)


def test_preferring_cellular_raises_only_the_wifi_route_cost(tmp_path):
  calls = []
  made, _ = settings(tmp_path, calls)
  assert made.apply({'action': 'prefer', 'value': 'cellular'}, lambda: True)['prefer'] == 'cellular'
  assert changes(calls) == [['connection', 'modify', PHONE, 'ipv4.route-metric', '2000', 'ipv6.route-metric', '2000'],
                            ['connection', 'modify', HOME, 'ipv4.route-metric', '2000', 'ipv6.route-metric', '2000'],
                            ['device', 'reapply', 'wlan0']]
  assert all('esim' not in ' '.join(call) and 'gsm' not in ' '.join(call) for call in changes(calls))
  calls.clear()
  assert network_prefs.apply_preference(tmp_path, NetworkManagerCli(fake_run(calls, '2000'))) == 0 and changes(calls) == []


def test_cellular_switch_uses_the_existing_setting_and_changes_need_park(tmp_path):
  calls = []
  made, params = settings(tmp_path, calls)
  made.apply({'action': 'cellular', 'value': False}, lambda: True)
  params.put_bool.assert_called_once_with('CellularDisabled', True)
  with pytest.raises(PermissionError):
    made.apply({'action': 'pauseHotspot', 'value': False}, lambda: False)
  assert changes(calls) == [] and network_prefs.read_prefs(tmp_path)['pauseHotspot'] is True
