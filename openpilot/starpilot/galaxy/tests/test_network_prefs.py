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
  assert snapshot['prefer'] == 'wifi' and snapshot['cellular']['enabled'] and not snapshot['pauseHotspot']


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


@pytest.mark.parametrize('order', [[HOME, []], [HOME, 1], [HOME, False]])
def test_order_rejects_non_string_ids_without_modifying_profiles(tmp_path, order):
  calls = []
  made, _ = settings(tmp_path, calls)
  with pytest.raises(FileExistsError):
    made.apply({'action': 'order', 'uuids': order}, lambda: True)
  assert changes(calls) == []


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
    made.apply({'action': 'pauseHotspot', 'value': True}, lambda: False)
  assert changes(calls) == [] and network_prefs.read_prefs(tmp_path)['pauseHotspot'] is False


def test_scan_join_connect_and_forget(tmp_path):
  calls = []

  def run(argv, **_kwargs):
    calls.append(argv[1:])
    if 'wifi' in argv and 'list' in argv:
      return SimpleNamespace(returncode=0, stdout='Home:80:WPA2\nGuest\\: Wi-Fi:72:\nOffice:65:WPA2\nCorp:50:WPA2 802.1X\nTheGalaxy-1234:90:WPA2\n', stderr='')
    if 'connect' in argv or 'up' in argv or 'delete' in argv or 'modify' in argv or 'reapply' in argv:
      return SimpleNamespace(returncode=0, stdout='', stderr='')
    return SimpleNamespace(returncode=0, stdout=details('-1') if 'connection.uuid' in ' '.join(argv) else LISTING, stderr='')

  params = Mock()
  params.get_bool.return_value = False
  made = NetworkSettings(tmp_path, NetworkManagerCli(run), params, link=lambda: {'link': 'wifi'})
  scanned = made.apply({'action': 'scan'}, lambda: True)
  assert scanned['available'] == [
    {'ssid': 'Guest: Wi-Fi', 'signal': 72, 'security': 'open'},
    {'ssid': 'Office', 'signal': 65, 'security': 'wpa'},
    {'ssid': 'Corp', 'signal': 50, 'security': 'unsupported'},
  ]
  assert ['-t', '-f', 'SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list', '--rescan', 'yes'] in calls

  made.apply({'action': 'join', 'ssid': 'Office', 'password': 'correct-password', 'hidden': False}, lambda: True)
  assert ['--wait', '15', 'device', 'wifi', 'connect', 'Office', 'ifname', 'wlan0', 'password', 'correct-password'] in calls

  made.apply({'action': 'connect', 'uuid': PHONE}, lambda: True)
  assert ['--wait', '15', 'connection', 'up', 'uuid', PHONE, 'ifname', 'wlan0'] in calls

  made.apply({'action': 'forget', 'uuid': PHONE}, lambda: True)
  assert ['connection', 'delete', 'uuid', PHONE] in calls

  with pytest.raises(ValueError):
    made.apply({'action': 'join', 'ssid': 'Office', 'password': 'short'}, lambda: True)


def test_join_pauses_the_hotspot_and_cleans_up_a_failed_profile(tmp_path, monkeypatch):
  network_prefs.atomic_json(network_prefs.prefs_path(tmp_path), {**network_prefs.DEFAULT, 'pauseHotspot': True})
  calls, events = [], []
  iface = tmp_path / 'galaxy0'
  iface.touch()
  monkeypatch.setattr(network_prefs.hotspot, 'request_pause', lambda seconds: (events.append('pause'), iface.unlink()))
  monkeypatch.setattr(network_prefs.hotspot, 'clear_pause', lambda: events.append('resume'))
  new, tried = 'dddddddd-dddd-dddd-dddd-dddddddddddd', []

  def run(argv, **_kwargs):
    calls.append(argv[1:])
    if 'connect' in argv:
      events.append('join')
      tried.append(True)
      return SimpleNamespace(returncode=4, stdout='', stderr='Secrets were required')
    if 'wifi' in argv or 'delete' in argv:
      return SimpleNamespace(returncode=0, stdout='', stderr='')
    extra = f'\n\nconnection.uuid:{new}\n802-11-wireless.ssid:Office\n802-11-wireless.mode:infrastructure\n' if tried else ''
    if 'connection.uuid' in ' '.join(argv):
      return SimpleNamespace(returncode=0, stdout=details() + extra, stderr='')
    return SimpleNamespace(returncode=0, stdout=LISTING + (f'{new}:802-11-wireless:yes:0:no:Office\n' if tried else ''), stderr='')

  made = NetworkSettings(tmp_path, NetworkManagerCli(run), Mock(), link=lambda: {'link': 'wifi'}, hotspot_iface=iface, sleep=lambda _s: None)
  with pytest.raises(ValueError):
    made.apply({'action': 'join', 'ssid': 'Office', 'password': 'wrong-password'}, lambda: True)
  assert events == ['pause', 'join', 'resume']
  assert ['connection', 'delete', 'uuid', new] in calls


def test_join_refuses_when_the_hotspot_stays_up(tmp_path, monkeypatch):
  network_prefs.atomic_json(network_prefs.prefs_path(tmp_path), {**network_prefs.DEFAULT, 'pauseHotspot': True})
  iface, calls, events = tmp_path / 'galaxy0', [], []
  iface.touch()
  monkeypatch.setattr(network_prefs.hotspot, 'request_pause', lambda seconds: events.append('pause'))
  monkeypatch.setattr(network_prefs.hotspot, 'clear_pause', lambda: events.append('resume'))
  ticks = iter(range(0, 100, 5))
  made = NetworkSettings(tmp_path, NetworkManagerCli(fake_run(calls)), Mock(), link=lambda: {'link': 'wifi'},
                         clock=lambda: next(ticks), hotspot_iface=iface, sleep=lambda _s: None)
  with pytest.raises(ValueError):
    made.apply({'action': 'connect', 'uuid': PHONE}, lambda: True)
  assert events == ['pause', 'resume'] and not any('up' in call for call in calls)


def test_a_hotspot_client_gets_an_answer_before_the_join_runs(tmp_path, monkeypatch):
  calls, jobs, events = [], [], []
  monkeypatch.setattr(network_prefs.hotspot, 'request_pause', lambda seconds: events.append('pause'))
  monkeypatch.setattr(network_prefs.hotspot, 'clear_pause', lambda: events.append('resume'))
  made = NetworkSettings(tmp_path, NetworkManagerCli(fake_run(calls)), Mock(), link=lambda: {'link': 'wifi'},
                         hotspot_iface=tmp_path / 'galaxy0', sleep=lambda _s: None, spawn=jobs.append)
  accepted = made.apply({'action': 'connect', 'uuid': PHONE}, lambda: True, background=True)
  assert accepted['lastJoin'] == {'ssid': 'Phone: 5G', 'state': 'joining', 'error': ''}
  assert not any('up' in call for call in calls)
  with pytest.raises(FileExistsError):
    made.apply({'action': 'forget', 'uuid': PHONE}, lambda: True)
  jobs.pop()()
  assert ['--wait', '15', 'connection', 'up', 'uuid', PHONE, 'ifname', 'wlan0'] in calls
  assert made.snapshot(True)['lastJoin'] == {'ssid': 'Phone: 5G', 'state': 'joined', 'error': ''}


def test_a_failed_background_join_keeps_the_reason(tmp_path):
  jobs = []

  def run(argv, **kwargs):
    if 'connect' in argv:
      return SimpleNamespace(returncode=4, stdout='', stderr='Secrets were required')
    return fake_run([])(argv, **kwargs)

  made = NetworkSettings(tmp_path, NetworkManagerCli(run), Mock(), link=lambda: {'link': 'wifi'},
                         hotspot_iface=tmp_path / 'galaxy0', sleep=lambda _s: None, spawn=jobs.append)
  made.apply({'action': 'join', 'ssid': 'Office', 'password': 'wrong-password'}, lambda: True, background=True)
  jobs.pop()()
  last = made.snapshot(True)['lastJoin']
  assert last['state'] == 'failed' and 'password' in last['error']


def test_only_hotspot_addresses_count_as_hotspot_clients():
  assert network_prefs.via_hotspot('172.31.254.23')
  assert not network_prefs.via_hotspot('192.168.1.20') and not network_prefs.via_hotspot('127.0.0.1') and not network_prefs.via_hotspot('bad')


def test_join_leaves_the_hotspot_up_when_the_pause_is_off(tmp_path, monkeypatch):
  iface, calls, events = tmp_path / 'galaxy0', [], []
  iface.touch()
  monkeypatch.setattr(network_prefs.hotspot, 'request_pause', lambda seconds: events.append('pause'))
  made = NetworkSettings(tmp_path, NetworkManagerCli(fake_run(calls)), Mock(), link=lambda: {'link': 'wifi'},
                         hotspot_iface=iface, sleep=lambda _s: None)
  made.apply({'action': 'connect', 'uuid': PHONE}, lambda: True)
  assert events == [] and ['--wait', '15', 'connection', 'up', 'uuid', PHONE, 'ifname', 'wlan0'] in calls
