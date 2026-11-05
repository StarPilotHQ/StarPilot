import hashlib
import json
import os
import stat
import struct
from pathlib import Path
from unittest.mock import Mock

import pytest

from openpilot.starpilot.galaxy.hotspot import DEFAULT, HotspotSettings, atomic_json, hotspot_name, validate_config
from openpilot.starpilot.galaxy import hotspot_worker
from openpilot.starpilot.galaxy.hotspot_netlink import WirelessInterfaces, attribute, attributes
from openpilot.starpilot.galaxy.hotspot_worker import (HOLD, STANDALONE_SETTLE_S, HotspotBackend, HotspotController, choose_channel,
                                                       hostapd_config, nm_activating, overlapping_routes, radio_channel)

CONFIG = {'enabled': True, 'ssid': 'TheGalaxy-6b86', 'password': '1234567890123456'}
DONGLE = 'b0c4a280b2f96b86'


def backend_mock(ready=True, ap_channel=None):
  backend = Mock()
  backend.alive.return_value = True
  backend.ap_status.return_value = {'ready': ready, 'channel': ap_channel}
  backend.activating.return_value = False
  backend.connected = set()
  return backend


@pytest.mark.parametrize('change', [{'enabled': 1}, {'ssid': 'bad\nssid'}, {'password': 'short'},
                                  {'password': 'has spaces in it'}, {'extra': True}, {'password': 'é' * 12}])
def test_strict_settings(change):
  with pytest.raises(ValueError):
    validate_config({**CONFIG, **change})


def test_settings_revision_permissions_and_credentials(tmp_path):
  settings = HotspotSettings(tmp_path, tmp_path / 'status', clock=lambda: 100, dongle_id=lambda: DONGLE)
  assert settings.read() == {**DEFAULT, 'ssid': 'TheGalaxy-6b86'}
  initial = settings.snapshot(True)
  payload = {'revision': initial['revision'], 'config': {**initial['config'], 'enabled': True}}
  with pytest.raises(PermissionError):
    settings.save(payload, lambda: False)
  assert not settings.path.exists()
  saved = settings.save(payload, lambda: True)
  assert len(saved['config']['password']) == 24
  assert stat.S_IMODE(settings.path.stat().st_mode) == 0o600
  with pytest.raises(FileExistsError):
    settings.save(payload, lambda: True)
  unchanged = settings.save({'revision': saved['revision'], 'config': {**saved['config'], 'password': ''}}, lambda: True)
  assert unchanged['config'] == saved['config']
  runtime = {'revision': saved['revision'], 'updated': 99, 'active': True, 'state': 'active', 'reason': '', 'channel': 149}
  atomic_json(settings.status_path, runtime)
  assert settings.snapshot(False)['active']
  assert not settings.snapshot(False)['editable']
  for updates in ({'updated': 80}, {'updated': 101}, {'revision': 'wrong'}):
    atomic_json(settings.status_path, {**runtime, **updates})
    assert settings.snapshot(True)['state'] == 'unavailable'


def test_settings_reject_symlink_and_oversize(tmp_path):
  settings = HotspotSettings(tmp_path, tmp_path / 'status', dongle_id=lambda: DONGLE)
  target = tmp_path / 'elsewhere'
  target.write_text(json.dumps(CONFIG))
  settings.path.symlink_to(target)
  with pytest.raises(OSError):
    settings.read()
  settings.path.unlink()
  settings.path.write_text(' ' * 4097)
  with pytest.raises(ValueError):
    settings.read()


def test_fixed_name_migration_password_and_restart(tmp_path):
  settings = HotspotSettings(tmp_path, dongle_id=lambda: DONGLE)
  atomic_json(settings.path, {**CONFIG, 'ssid': 'Old custom name'})
  assert settings.read() == CONFIG
  current = settings.snapshot(True)
  with pytest.raises(ValueError, match='fixed'):
    settings.save({'revision': current['revision'], 'config': {**CONFIG, 'ssid': 'Other name'}}, lambda: True)
  custom = {**CONFIG, 'password': 'my-new-password-123'}
  settings.save({'revision': current['revision'], 'config': custom}, lambda: True)
  restarted = HotspotSettings(tmp_path, dongle_id=lambda: DONGLE)
  assert restarted.read() == custom
  assert hotspot_name(DONGLE.upper()) == 'TheGalaxy-6b86'


@pytest.mark.parametrize('dongle', [None, '', 'UnregisteredDevice', '1234', 'x' * 16, b'0123456789abcdef'])
def test_unregistered_name_is_not_invented(dongle):
  with pytest.raises(ValueError, match='dongle ID'):
    hotspot_name(dongle)


@pytest.mark.parametrize('status,expected', [('wpa_state=COMPLETED\nfreq=5745', ('a', 149)),
  ('wpa_state=COMPLETED\nfreq=2412', ('g', 1)), ('wpa_state=DISCONNECTED\nfreq=5745', ('g', 6)),
  ('wpa_state=INACTIVE', ('g', 6)), ('wpa_state=SCANNING', ('g', 6)),
  ('wpa_state=ASSOCIATING', HOLD), ('wpa_state=4WAY_HANDSHAKE\nfreq=5745', HOLD),
  ('wpa_state=INTERFACE_DISABLED', None), ('FAIL', None),
  ('wpa_state=COMPLETED\nfreq=6000', None)])
def test_channel(status, expected):
  assert radio_channel(status) == expected


def test_automatic_standalone_connected_and_back():
  now = [100.]
  backend = backend_mock()
  controller = HotspotController(backend, clock=lambda: now[0])
  for status, expected in [('DISCONNECTED', ('g', 6)), ('COMPLETED\nfreq=5745', ('a', 149)), ('DISCONNECTED', ('g', 6))]:
    backend.channel.return_value = radio_channel('wpa_state=' + status)
    assert controller.tick(CONFIG)['state'] == 'starting'
    backend.start.assert_called_with(CONFIG, expected)
    assert controller.tick(CONFIG)['active']
    now[0] += STANDALONE_SETTLE_S + 1
  assert backend.start.call_count == 3
  assert controller.tick({**CONFIG, 'enabled': False})['state'] == 'disabled'


@pytest.mark.parametrize('results,expected', [
  ([('a', 149)], ('a', 149)), ([('g', 6)], ('g', 6)), ([None], None), ([HOLD, ('a', 149)], HOLD),
  ([('a', 149), HOLD], HOLD), ([('g', 1), ('a', 149)], ('g', 1)), ([('g', 6), ('a', 149)], ('a', 149)),
  ([None, ('a', 149)], ('a', 149)), ([('g', 6), None], ('g', 6))])
def test_choose_channel(results, expected):
  assert choose_channel(results) == expected


@pytest.mark.parametrize('state,expected', [('30 (disconnected)', False), ('50 (connecting (configuring))', True),
  ('60 (connecting (need authentication))', True), ('100 (connected)', False), ('', False), ('junk', False)])
def test_nm_activating(state, expected):
  assert nm_activating(state) == expected


def test_join_leaves_running_ap_alone():
  now = [100.]
  backend = backend_mock(ap_channel=1)
  controller = HotspotController(backend, clock=lambda: now[0])
  backend.channel.return_value = ('g', 1)
  controller.tick(CONFIG)
  assert controller.tick(CONFIG)['active']
  backend.start.reset_mock()
  backend.stop.reset_mock()
  # Android Auto drops home Wi-Fi, scans, associates and joins the car on channel 149.
  for channel in (('g', 6), HOLD, ('g', 6), HOLD):
    backend.channel.return_value = channel
    now[0] += 2
    assert controller.tick(CONFIG)['active']
  backend.stop.assert_not_called()
  backend.start.assert_not_called()
  backend.ap_status.return_value = {'ready': True, 'channel': 149}
  backend.channel.return_value = ('a', 149)
  assert controller.tick(CONFIG)['channel'] == 149
  backend.stop.assert_not_called()
  backend.start.assert_not_called()
  # Only a station gone for good moves the AP to its standalone channel.
  backend.channel.return_value = ('g', 6)
  now[0] += STANDALONE_SETTLE_S - 1
  assert controller.tick(CONFIG)['active']
  backend.start.assert_not_called()
  now[0] += 2
  assert controller.tick(CONFIG)['state'] == 'starting'
  backend.start.assert_called_once_with(CONFIG, ('g', 6))


def test_hold_does_not_start_ap():
  backend = backend_mock()
  backend.channel.return_value = HOLD
  controller = HotspotController(backend, clock=lambda: 100)
  assert controller.tick(CONFIG)['state'] == 'waiting'
  backend.start.assert_not_called()
  backend.stop.assert_not_called()


def test_backend_channel_reports_idle_stations_and_asks_networkmanager_only_for_them(tmp_path):
  def command(args, **kwargs):
    if args[0] == 'nmcli':
      return Mock(returncode=0, stdout='50 (connecting (configuring))\n')
    return Mock(returncode=0, stdout='wpa_state=DISCONNECTED\n')
  run = Mock(side_effect=command)
  backend = HotspotBackend(tmp_path, run=run, interfaces=Mock())
  assert backend.channel() == ('g', 6) and backend.idle == ['wlan0']
  assert all(call.args[0][0] != 'nmcli' for call in run.call_args_list)
  assert backend.activating()
  run.side_effect = lambda args, **kwargs: Mock(returncode=0, stdout='wpa_state=COMPLETED\nfreq=5745')
  assert backend.channel() == ('a', 149) and backend.connected == {149}
  assert not backend.activating()
  assert all(call.args[0][-1] in ('status', 'wlan0') for call in run.call_args_list)


def test_backend_skips_car_station_without_status(tmp_path, monkeypatch):
  monkeypatch.setattr(Path, 'exists', lambda self: True)
  def command(args, **kwargs):
    if args[2] == 'aa0':
      assert kwargs['check'] is False
      return Mock(returncode=255, stdout='Failed to connect to non-global ctrl_ifname: aa0')
    return Mock(returncode=0, stdout='wpa_state=COMPLETED\nfreq=2412')
  backend = HotspotBackend(tmp_path, run=Mock(side_effect=command), interfaces=Mock())
  assert backend.channel() == ('g', 1)


def test_networkmanager_join_holds_the_running_ap():
  now = [100.]
  backend = backend_mock(ap_channel=1)
  controller = HotspotController(backend, clock=lambda: now[0])
  backend.channel.return_value = ('g', 1)
  controller.tick(CONFIG)
  backend.start.reset_mock()
  backend.channel.return_value = ('g', 6)
  now[0] += STANDALONE_SETTLE_S + 1
  backend.activating.return_value = True
  assert controller.tick(CONFIG)['active']
  backend.start.assert_not_called()
  backend.activating.reset_mock()
  backend.channel.return_value = ('g', 1)
  backend.activating.return_value = False
  controller.tick(CONFIG)
  backend.activating.assert_not_called()  # nothing to change, so nothing to ask


def test_ap_moved_by_the_driver_to_another_station_stays_there():
  backend = backend_mock(ap_channel=36)
  controller = HotspotController(backend, clock=lambda: 100)
  backend.channel.return_value = ('a', 36)
  controller.tick(CONFIG)
  backend.start.reset_mock()
  backend.stop.reset_mock()
  backend.connected = {36, 149}
  backend.ap_status.return_value = {'ready': True, 'channel': 149}
  for _ in range(3):
    assert controller.tick(CONFIG) == {'active': True, 'state': 'active', 'reason': '', 'channel': 149}
  backend.start.assert_not_called()
  backend.stop.assert_not_called()


def test_dfs_channel_that_will_not_start_falls_back_to_standalone():
  now = [100.]
  backend = backend_mock(ready=False)
  controller = HotspotController(backend, clock=lambda: now[0])
  backend.channel.return_value = ('a', 100)
  assert controller.tick(CONFIG)['state'] == 'starting'
  now[0] += 16
  assert controller.tick(CONFIG)['state'] == 'error'
  now[0] += 16
  backend.ap_status.return_value = {'ready': True, 'channel': 6}
  assert controller.tick(CONFIG)['state'] == 'starting'
  backend.start.assert_called_with(CONFIG, ('g', 6))


def test_backend_ap_status(tmp_path):
  run = Mock(return_value=Mock(returncode=0, stdout='state=ENABLED\nchannel=149\n'))
  backend = HotspotBackend(tmp_path, run=run, interfaces=Mock())
  assert backend.ap_status() == {'ready': True, 'channel': 149}
  assert run.call_count == 1
  run.return_value = Mock(returncode=255, stdout='')
  assert backend.ap_status() == {'ready': False, 'channel': None}


def test_routes_and_config():
  assert not overlapping_routes([{'dst': 'default'}, {'dst': '192.168.180.0/24'}, {'dst': '172.31.254.0/24', 'dev': 'galaxy0'}])
  assert overlapping_routes([{'dst': '172.31.0.0/16', 'dev': 'wlan0'}])
  assert overlapping_routes([{'dst': '172.31.254.1', 'dev': 'eth0'}])
  config = hostapd_config(CONFIG, ('a', 149))
  assert 'interface=galaxy0\n' in config and 'channel=149\n' in config and 'wpa=2\n' in config
  assert 'country_code' not in config
  with pytest.raises(ValueError):
    hostapd_config({**CONFIG, 'ssid': 'Galaxy\ninterface=wlan0'}, ('a', 149))


def test_controller_lifecycle_and_channel_recovery():
  backend = backend_mock()
  backend.channel.return_value = ('a', 149)
  now = [100.]
  controller = HotspotController(backend, clock=lambda: now[0])
  assert controller.tick(CONFIG)['state'] == 'starting'
  assert controller.tick(CONFIG)['active']
  backend.start.assert_called_once_with(CONFIG, ('a', 149))
  backend.channel.return_value = ('a', 36)
  assert controller.tick(CONFIG)['state'] == 'error'  # rate limit restarts
  now[0] += 16
  assert controller.tick(CONFIG)['state'] == 'starting'
  backend.start.assert_called_with(CONFIG, ('a', 36))
  backend.channel.return_value = None
  assert controller.tick(CONFIG)['state'] == 'waiting'
  assert controller.applied is None
  assert controller.tick({**CONFIG, 'enabled': False})['state'] == 'disabled'
  backend.stop.assert_called()


def test_controller_readiness_and_failed_child():
  backend = backend_mock(ready=False)
  backend.channel.return_value = ('a', 149)
  now = [0]
  controller = HotspotController(backend, clock=lambda: now[0])
  assert controller.tick(CONFIG)['state'] == 'starting'
  assert controller.tick(CONFIG)['state'] == 'starting'
  now[0] = 16
  assert controller.tick(CONFIG)['state'] == 'error'
  assert controller.applied is None
  now[0] = 32
  backend.start.side_effect = OSError('failed')
  assert controller.tick(CONFIG)['state'] == 'error'
  backend.stop.assert_called()


def test_backend_failure_rolls_back_only_owned_interface(tmp_path):
  run = Mock(return_value=Mock(stdout='[]'))
  interfaces = Mock()
  backend = HotspotBackend(tmp_path, run=run, interfaces=interfaces, udev_rule=tmp_path / 'udev/85.rules',
                           supplicant_dir=tmp_path / 'wpa')
  backend.firewall = '/sbin/iptables-legacy'
  def command(args, **kwargs):
    if args[:3] == ['ip', 'address', 'add']:
      raise OSError('failed')
    return Mock(stdout='[]')
  run.side_effect = command
  with pytest.raises(OSError):
    backend.start(CONFIG, ('a', 149))
  interfaces.return_value.create.assert_called_once_with('galaxy0')
  interfaces.return_value.delete.assert_called_once_with('galaxy0')
  commands = [call.args[0] for call in run.call_args_list]
  assert ['nmcli', 'device', 'set', 'galaxy0', 'managed', 'no'] in commands
  assert all('wlan0' not in command and 'p2p0' not in command for command in commands)
  assert not backend.owned


def test_missing_hostapd_never_creates_the_interface(tmp_path, monkeypatch):
  monkeypatch.setattr(hotspot_worker, 'HOSTAPD', tmp_path / 'missing')
  interfaces = Mock()
  backend = HotspotBackend(tmp_path, run=Mock(return_value=Mock(stdout='[]')), interfaces=interfaces)
  backend.firewall = '/sbin/iptables-legacy'
  with pytest.raises(RuntimeError, match='hostapd'):
    backend.start(CONFIG, ('a', 149))
  interfaces.assert_not_called()


def test_bundled_hostapd_matches_its_checksum():
  bundled = Path(hotspot_worker.__file__).parent / 'bin'
  for line in (bundled / 'hostapd.sha256').read_text().splitlines():
    digest, name = line.split()
    path = bundled / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest and os.access(path, os.X_OK)
  assert {hotspot_worker.HOSTAPD.name, hotspot_worker.HOSTAPD_CLI.name} <= {line.split()[1] for line in
                                                                         (bundled / 'hostapd.sha256').read_text().splitlines()}


def test_repeated_failures_back_off_and_success_resets():
  backend = backend_mock()
  backend.channel.return_value = ('a', 149)
  backend.start.side_effect = RuntimeError('hostapd is missing')
  now = [0.]
  controller = HotspotController(backend, clock=lambda: now[0])
  starts = []
  for _ in range(400):
    calls = backend.start.call_count
    controller.tick(CONFIG)
    if backend.start.call_count > calls:
      starts.append(now[0])
    now[0] += 2
  gaps = [later - earlier for earlier, later in zip(starts, starts[1:], strict=False)]
  assert gaps[:5] == [30, 60, 120, 240, 300] and max(gaps) == 300
  backend.start.side_effect = None
  now[0] += 300
  assert controller.tick(CONFIG)['state'] == 'starting'
  assert controller.tick(CONFIG)['active'] and controller.failures == 0 and controller.backoff() == 15
  controller.failures = 10000
  assert controller.backoff() == hotspot_worker.MAX_RETRY_S


def test_ap_that_keeps_exiting_backs_off():
  backend = backend_mock()
  backend.channel.return_value = ('a', 149)
  backend.alive.return_value = False  # hostapd exits right after each start
  now = [0.]
  controller = HotspotController(backend, clock=lambda: now[0])
  starts = []
  for _ in range(200):
    calls = backend.start.call_count
    controller.tick(CONFIG)
    if backend.start.call_count > calls:
      starts.append(now[0])
    now[0] += 2
  gaps = [later - earlier for earlier, later in zip(starts, starts[1:], strict=False)]
  assert gaps[0] >= 30 and gaps[0] < gaps[1] < gaps[2] <= 300 and controller.failures >= 3


def test_networkmanager_is_kept_off_galaxy0_before_it_exists(tmp_path):
  rule = tmp_path / 'udev/85-starpilot-galaxy-hotspot.rules'
  run = Mock(return_value=Mock(stdout='[]', returncode=0))
  interfaces = Mock()
  order = []
  interfaces.return_value.create.side_effect = lambda name: order.append(('create', rule.exists()))
  backend = HotspotBackend(tmp_path, run=run, interfaces=interfaces, popen=Mock(), udev_rule=rule,
                           supplicant_dir=tmp_path / 'wpa')
  backend.firewall = '/sbin/iptables-legacy'
  backend.start(CONFIG, ('a', 149))
  assert order == [('create', True)]
  assert rule.read_text() == 'SUBSYSTEM=="net", ENV{INTERFACE}=="galaxy0", ENV{NM_UNMANAGED}="1"\n'
  assert ['udevadm', 'control', '--reload'] in [call.args[0] for call in run.call_args_list]
  run.reset_mock()
  backend.keep_unmanaged()
  run.assert_not_called()  # unchanged rule: no reload


def test_hostapd_waits_for_wpa_supplicant_to_let_go(tmp_path):
  supplicant = tmp_path / 'wpa'
  supplicant.mkdir()
  (supplicant / 'galaxy0').touch()
  now = [0.]
  def sleep(seconds):
    now[0] += seconds
    if now[0] >= 1:
      (supplicant / 'galaxy0').unlink(missing_ok=True)
  backend = HotspotBackend(tmp_path, run=Mock(), interfaces=Mock(), supplicant_dir=supplicant, clock=lambda: now[0], sleep=sleep)
  backend.wait_supplicant_released()
  assert 1 <= now[0] < 1.2
  (supplicant / 'galaxy0').touch()
  def sleep_forever(seconds):
    now[0] += seconds

  backend = HotspotBackend(tmp_path, run=Mock(), interfaces=Mock(), supplicant_dir=supplicant, clock=lambda: now[0],
                           sleep=sleep_forever)
  with pytest.raises(RuntimeError, match='wpa_supplicant'):
    backend.wait_supplicant_released()


def test_backend_conflict_does_not_create(tmp_path):
  interfaces = Mock()
  backend = HotspotBackend(tmp_path, run=Mock(return_value=Mock(stdout='[{"dst":"172.31.0.0/16"}]')), interfaces=interfaces)
  backend.firewall = '/sbin/iptables-legacy'
  with pytest.raises(RuntimeError):
    backend.start(CONFIG, ('a', 149))
  interfaces.assert_not_called()


def test_netlink_encoding_and_refusal():
  assert attributes(attribute(4, b'galaxy0\0')) == {4: b'galaxy0\0'}
  with pytest.raises(ValueError):
    attributes(b'\x03\x00\x01\x00')
  wireless = WirelessInterfaces.__new__(WirelessInterfaces)
  wireless.interfaces = Mock(return_value={'wlan0': {'type': 2, 'phy': 0, 'index': 1}, 'galaxy0': {'index': 7}})
  wireless.query = Mock()
  wireless.family = 22
  with pytest.raises(RuntimeError):
    wireless.create('galaxy0')
  wireless.query.assert_not_called()
  wireless.delete('galaxy0')
  wireless.query.assert_called_once_with(22, 8, attribute(3, b'\x07\0\0\0'), ack=True)


def test_netlink_new_interface_payloads():
  wireless = WirelessInterfaces.__new__(WirelessInterfaces)
  wireless.interfaces = Mock(return_value={'wlan0': {'type': 2, 'phy': 0, 'index': 1}})
  wireless.query = Mock()
  wireless.family = 22
  wireless.create('galaxy0')
  ap = attribute(1, struct.pack('I', 0)) + attribute(4, b'galaxy0\0') + attribute(5, struct.pack('I', 3))
  wireless.query.assert_called_with(22, 7, ap, ack=True)
  wireless.create('aa0', iftype=2)
  station = attribute(1, struct.pack('I', 0)) + attribute(4, b'aa0\0') + attribute(5, struct.pack('I', 2))
  wireless.query.assert_called_with(22, 7, station, ack=True)
