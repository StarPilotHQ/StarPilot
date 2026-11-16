"""Closes internet connections left on a network the comma has moved off, so apps reconnect on the new one.

Linux keeps a connection open after its address is gone, and an app without keepalives then waits on it for
many minutes. Only connections that were open on the old link are closed; the car link, the Galaxy hotspot
and local addresses are never touched.
"""
from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import signal
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

LINKS = ('wlan0', 'ppp0')
STATUS_PATH = Path('/dev/shm/starpilot-netwatch.json')
CHANGE_SETTLE_S = 2.0
DOWN_SETTLE_S = 4.0
GATEWAY_WAIT_S = 10.0   # longest wait for the new gateway's hardware address before accepting a Wi-Fi change
SIOCGIFADDR = 0x8915

Connection = tuple[str, int, str, int]  # local address, local port, peer address, peer port


def address(link: str) -> str:
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
      packed = fcntl.ioctl(probe.fileno(), SIOCGIFADDR, struct.pack('256s', link.encode()[:15]))
    return socket.inet_ntoa(packed[20:24])
  except OSError:
    return ''


def _read(path: str) -> str:
  try:
    return Path(path).read_text()
  except OSError:
    return ''


def default_routes(text: str | None = None) -> list[tuple[int, str, str]]:
  """(metric, interface, gateway) for every IPv4 default route, best first."""
  routes = []
  for line in (_read('/proc/net/route') if text is None else text).splitlines()[1:]:
    fields = line.split()
    try:
      if len(fields) > 6 and fields[1] == '00000000' and int(fields[3], 16) & 1:
        routes.append((int(fields[6]), fields[0], socket.inet_ntoa(struct.pack('<L', int(fields[2], 16)))))
    except (ValueError, OSError):
      continue
  return sorted(routes)


def gateway_hardware(link: str, gateway: str, text: str | None = None) -> str:
  for line in (_read('/proc/net/arp') if text is None else text).splitlines()[1:]:
    fields = line.split()
    if len(fields) > 5 and fields[0] == gateway and fields[5] == link and fields[2] != '0x0':
      return fields[3]
  return ''


def signature(link: str) -> tuple | None:
  """What identifies the network behind a link, or None while the link carries no internet."""
  local = address(link)
  if not local:
    return None
  if link.startswith('ppp'):
    return (_read(f'/sys/class/net/{link}/ifindex').strip(), local)
  if _read(f'/sys/class/net/{link}/carrier').strip() != '1':
    return None
  gateway = next((route[2] for route in default_routes() if route[1] == link), '')
  return (local, gateway, gateway_hardware(link, gateway)) if gateway else None


def same(first: tuple | None, second: tuple | None) -> bool:
  if first is None or second is None:
    return first is second
  if len(first) == 3 and len(second) == 3 and not (first[2] and second[2]):
    return first[:2] == second[:2]
  return first == second


def local_address(mark: tuple | None) -> str:
  return '' if mark is None else mark[1] if len(mark) == 2 else mark[0]


def _endpoint(value: str) -> tuple[str, int]:
  host, _, port = value.rpartition(':')
  return host, int(port)


def parse_connections(text: str) -> set[Connection]:
  found = set()
  for line in text.splitlines():
    fields = line.split()
    try:
      found.add((*_endpoint(fields[2]), *_endpoint(fields[3])))
    except (IndexError, ValueError):
      continue
  return found


def internet(connection: Connection, local: str) -> bool:
  try:
    peer = ipaddress.ip_address(connection[2])
  except ValueError:
    return False
  return connection[0] == local and peer.version == 4 and peer.is_global


def list_connections(run=subprocess.run) -> set[Connection]:
  try:
    return parse_connections(run(['ss', '-4tnH', 'state', 'established'], capture_output=True, text=True, timeout=5).stdout)
  except (OSError, subprocess.SubprocessError):
    return set()


def close_connection(connection: Connection, run=subprocess.run) -> bool:
  local, local_port, peer, peer_port = connection
  command = ['ss', '-K', '-4tnH', 'state', 'established', 'src', local, 'sport', '=', f':{local_port}',
             'dst', peer, 'dport', '=', f':{peer_port}']
  try:
    return bool(run(command if os.geteuid() == 0 else ['sudo', '-n', *command], capture_output=True, text=True, timeout=5).stdout.strip())
  except (OSError, subprocess.SubprocessError):
    return False


@dataclass
class Link:
  stable: tuple | None = None
  candidate: tuple | None = None
  changing: bool = False
  since: float = 0.0
  started: float = 0.0
  pending: set | None = None


@dataclass
class Watcher:
  read: object = signature
  connections: object = list_connections
  close: object = close_connection
  links: dict = field(default_factory=dict)
  changes: int = 0
  closed: int = 0
  changed_at: float = 0.0

  def start(self) -> None:
    self.links = {name: Link(stable=self.read(name)) for name in LINKS}

  def tick(self, now: float) -> list[tuple[str, int]]:
    """Returns (link, connections closed) for every change accepted on this pass."""
    accepted = []
    for name, link in self.links.items():
      current = self.read(name)
      if same(current, link.stable):
        if current is not None and len(current) == 3 and current[2]:
          link.stable = current
        link.changing, link.candidate, link.pending = False, None, None
        continue
      if link.pending is None:
        old = local_address(link.stable)
        link.pending = {c for c in self.connections() if internet(c, old)} if old else set()
        link.started = now
      if not link.changing or not same(current, link.candidate):
        link.changing, link.candidate, link.since = True, current, now
        continue
      link.candidate = current
      waiting_gateway = current is not None and len(current) == 3 and not current[2] and now - link.started < GATEWAY_WAIT_S
      if now - link.since < (DOWN_SETTLE_S if current is None else CHANGE_SETTLE_S) or waiting_gateway:
        continue
      still_open = link.pending & self.connections() if link.pending else set()
      count = sum(1 for connection in sorted(still_open) if self.close(connection))
      link.stable, link.changing, link.candidate, link.pending = current, False, None, None
      self.changes += 1
      self.closed += count
      self.changed_at = time.time()  # noqa: TID251 shown to the user as a wall-clock time
      accepted.append((name, count))
    return accepted

  def status(self) -> dict:
    routes = default_routes()
    best = routes[0][1] if routes else ''
    return {'link': 'wifi' if best.startswith('wlan') else 'cellular' if best.startswith('ppp') else 'none' if not best else 'other',
            'wifi': self.links['wlan0'].stable is not None, 'cellular': self.links['ppp0'].stable is not None,
            'changes': self.changes, 'closed': self.closed, 'changedAt': self.changed_at,
            'updated': time.monotonic()}


def read_status(path: Path = STATUS_PATH, clock=time.monotonic) -> dict:
  try:
    value = json.loads(path.read_text())
    return value if type(value) is dict and 0 <= clock() - value.get('updated', -1e9) < 15 else {}
  except (OSError, ValueError, TypeError):
    return {}


def publish(status: dict, path: Path = STATUS_PATH) -> None:
  temporary = path.with_name(path.name + '.tmp')
  try:
    temporary.write_text(json.dumps(status))
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
  except OSError:
    pass


def main() -> None:
  from openpilot.common.swaglog import cloudlog
  os.nice(10)
  stop = threading.Event()
  signal.signal(signal.SIGTERM, lambda *_: stop.set())
  signal.signal(signal.SIGINT, lambda *_: stop.set())
  watcher = Watcher()
  watcher.start()
  while not stop.is_set():
    now = time.monotonic()
    for name, count in watcher.tick(now):
      cloudlog.event('netwatch_changed', link=name, closed=count, up=watcher.links[name].stable is not None)
    publish(watcher.status())
    stop.wait(1.0)


if __name__ == '__main__':
  main()
