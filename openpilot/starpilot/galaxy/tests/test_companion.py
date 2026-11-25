import json
import stat

import pytest

from openpilot.starpilot.galaxy.companion import CompanionRegistry, phone_uplink_active, validate_lease
from openpilot.starpilot.galaxy.companion_uplink_worker import other_internet, tunnel_config
from openpilot.starpilot.galaxy.hotspot import atomic_json

PAYLOAD = {'port': 1080, 'user': 'a' * 32, 'pass': 'b' * 32, 'version': '1.0.0', 'protocol': 1, 'bytes': 100}
PEER = '172.31.254.10'


def registry(tmp_path, now=lambda: 100):
  return CompanionRegistry(tmp_path, tmp_path / 'lease', tmp_path / 'status', clock=now)


def test_opt_in_and_stale_lease(tmp_path):
  now = [100]
  owner = registry(tmp_path, lambda: now[0])
  owner.save({'enabled': False})
  assert owner.heartbeat(PAYLOAD, PEER)['enabled']
  assert owner.enabled()
  assert stat.S_IMODE(owner.lease_path.stat().st_mode) == 0o600
  assert owner.snapshot()['connected']
  assert 'pass' not in owner.snapshot()
  now[0] = 115
  assert owner.lease() is None
  now[0] = 99
  assert owner.lease() is None


@pytest.mark.parametrize('peer', ['127.0.0.1', '192.168.1.2', '172.31.254.1', '172.31.254.0', '172.31.254.255', '::ffff:172.31.254.10'])
def test_only_hotspot_peers(tmp_path, peer):
  owner = registry(tmp_path)
  with pytest.raises(ValueError):
    owner.heartbeat(PAYLOAD, peer)
  assert not owner.enabled()


def test_remote_and_conflict_and_disable(tmp_path):
  owner = registry(tmp_path)
  with pytest.raises(PermissionError):
    owner.heartbeat(PAYLOAD, PEER, remote=True)
  assert not owner.enabled()
  owner.heartbeat(PAYLOAD, PEER)
  with pytest.raises(FileExistsError):
    owner.heartbeat(PAYLOAD, '172.31.254.11')
  owner.heartbeat({**PAYLOAD, 'user': 'c' * 32}, PEER)
  assert owner.lease()['user'] == 'c' * 32
  owner.save({'enabled': False})
  assert owner.lease() is None


@pytest.mark.parametrize('patch', [{'port': True}, {'port': 22}, {'protocol': 2}, {'user': 'a\n' * 20}, {'bytes': -1}, {'bytes': True}, {'version': None}])
def test_invalid_payload(patch):
  with pytest.raises(ValueError):
    validate_lease({**PAYLOAD, 'peer': PEER, **patch})


def test_route_guard(tmp_path):
  path = tmp_path / 'status'
  assert not phone_uplink_active(path, lambda: 100)
  for state, expected in [({'active': True, 'updated': 99}, True), ({'active': False, 'updated': 99}, False),
                          ({'active': True, 'updated': 85}, False), ({'active': True, 'updated': 101}, False),
                          ({'active': True, 'updated': float('nan')}, False)]:
    atomic_json(path, state)
    assert phone_uplink_active(path, lambda: 100) is expected


def test_tunnel_mapped_dns_and_interface_probes():
  config = json.loads(tunnel_config({**PAYLOAD, 'peer': PEER}))
  assert config['socks5']['address'] == PEER
  assert config['socks5']['udp'] == 'udp'
  assert config['mapdns']['network'] == '198.19.0.0'
  routes = [{'dst': 'default', 'dev': 'ppp0'}, {'dst': 'default', 'dev': 'spuplink0'}, {'dst': '172.31.254.0/24', 'dev': 'galaxy0'}]
  calls = []
  assert other_internet(routes, lambda interface: calls.append(interface) or True)
  assert calls == ['ppp0']
  assert not other_internet(routes, lambda _: False)


def test_backend_routing_and_cleanup_order(tmp_path, monkeypatch):
  from unittest.mock import Mock
  from openpilot.starpilot.galaxy import companion_uplink_worker as worker
  monkeypatch.setattr(worker, 'RUNTIME', tmp_path)
  monkeypatch.setattr(worker, 'STATUS_PATH', tmp_path / 'status')
  child = Mock()
  child.poll.return_value = None
  created = [False]
  calls = []
  original_exists = worker.Path.exists

  def exists(path):
    return created[0] if str(path) == '/sys/class/net/spuplink0' else original_exists(path)

  def spawn(*args, **kwargs):
    created[0] = True
    return child

  def command(args, check=True):
    calls.append((args, phone_uplink_active(tmp_path / 'status')))
    return Mock(stdout='[]', returncode=0)

  monkeypatch.setattr(worker.Path, 'exists', exists)
  monkeypatch.setattr(worker.subprocess, 'Popen', spawn)
  monkeypatch.setattr(worker, 'command', command)
  backend = worker.Backend()
  backend.start({**PAYLOAD, 'peer': PEER}, [])
  activation = next(i for i, (args, _) in enumerate(calls) if args[:4] == ['ip', 'rule', 'add', 'priority'] and '18001' in args)
  assert calls[activation][1]  # Data guard is published before route activation.
  assert (tmp_path / 'owned').exists()
  assert all('ppp0' not in args and 'wlan0' not in args for args, _ in calls)
  backend.stop()
  assert not phone_uplink_active(tmp_path / 'status')
  assert not (tmp_path / 'owned').exists()
  child.terminate.assert_called_once()
  teardown = [args for args, _ in calls[activation + 1:]]
  assert teardown.index(['resolvectl', 'revert', 'spuplink0']) < teardown.index(['ip', 'link', 'delete', 'spuplink0'])


def test_backend_refuses_route_collision(tmp_path, monkeypatch):
  from openpilot.starpilot.galaxy import companion_uplink_worker as worker
  monkeypatch.setattr(worker, 'RUNTIME', tmp_path)
  with pytest.raises(RuntimeError, match='conflicts'):
    worker.Backend().start({**PAYLOAD, 'peer': PEER}, [{'dst': '198.19.0.0/24', 'dev': 'wlan0'}])
  assert not (tmp_path / 'owned').exists()
