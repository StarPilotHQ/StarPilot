"""Discover this socket's LAN DNS identity without trusting a request's Host."""

from ipaddress import IPv4Address, ip_address, ip_network
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time


LAN_NETWORKS = tuple(ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16'))


def lan_address(value):
  try:
    address = ip_address(value)
    return isinstance(address, IPv4Address) and any(address in network for network in LAN_NETWORKS)
  except ValueError:
    return False


def dns_name(value):
  """Canonical DNS names only, never URL authorities or numeric addresses."""
  if not isinstance(value, str) or not value.isascii():
    return None
  name = value.lower().removesuffix('.')
  if not 1 <= len(name) <= 253 or name == 'localhost' or name.endswith('.localhost'):
    return None
  labels = name.split('.')
  if all(label.isdecimal() for label in labels) or not all(
      re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
    return None
  return name


def search_domains(text):
  domains = []
  for line in text.splitlines():
    fields = line.split('#', 1)[0].split(';', 1)[0].split()
    if fields and fields[0] in ('domain', 'search'):
      for value in fields[1:7]:
        name = dns_name(value)
        if name is not None and name not in domains:
          domains.append(name)
  return domains[:6]


def discover_names(destination, *, hostname=None, domains=(), reverse=socket.gethostbyaddr, forward=socket.getaddrinfo, publish=None):
  """Trust local OS/DHCP identity and destination PTR, then confirm exact IPv4 A answers.

  A request-selected name is deliberately not an input. Resolving an arbitrary
  Host to this private address would otherwise admit a DNS-rebinding origin.
  The LAN's OS/DHCP/DNS administrator remains trusted, like other LAN peers
  that already have passwordless literal-IP access. This is not LAN-user auth.
  """
  if not lan_address(destination):
    return []
  candidates = []

  def add(value):
    name = dns_name(value)
    if name is not None and name not in candidates and len(candidates) < 16:
      candidates.append(name)

  local_name = dns_name(hostname if hostname is not None else socket.gethostname())
  if local_name is not None:
    add(local_name)
    if '.' not in local_name:
      for domain in domains[:6]:
        if dns_name(domain) is not None:
          add(f'{local_name}.{domain}')
  confirmed = []
  checked = set()

  def confirm():
    for name in candidates:
      if name in checked:
        continue
      checked.add(name)
      try:
        answers = forward(name + ('.' if '.' in name else ''), None, family=socket.AF_INET, type=socket.SOCK_STREAM)
        addresses = {row[4][0] for row in answers[:32]}
        if len(answers) <= 32 and addresses == {destination}:
          confirmed.append(name)
          if publish is not None:
            publish(confirmed)
      except (OSError, ValueError, TypeError, IndexError):
        continue

  # Publish independently confirmed OS/DHCP names before optional PTR work.
  # A slow/missing reverse zone must not discard an already confirmed name.
  confirm()
  try:
    name, aliases, _ = reverse(destination)
    add(name)
    for alias in aliases[:16]:
      add(alias)
  except (OSError, ValueError):
    pass
  confirm()
  return confirmed


class LocalHostnames:
  """Bound DNS time/processes and cache by actual socket destination, not Host."""

  TTL = 5.0
  TIMEOUT = 1.0
  MAX_ADDRESSES = 16

  def __init__(self, *, run=subprocess.run, clock=time.monotonic):
    self.run, self.clock = run, clock
    self.lock = threading.Lock()
    self.cache = {}

  def names(self, destination):
    if not lan_address(destination) or not self.lock.acquire(timeout=.1):
      return ()
    try:
      now = self.clock()
      cached = self.cache.get(destination)
      if cached is not None and cached[0] > now:
        return cached[1]
      names, stdout = (), b''
      try:
        # libc DNS calls have no reliable per-call deadline. A disposable child
        # bounds even a stuck resolver and cannot exhaust the HTTP thread pool.
        result = self.run([sys.executable, str(Path(__file__).resolve()), destination],
                          capture_output=True, timeout=self.TIMEOUT, check=True)
        stdout = result.stdout
      except subprocess.TimeoutExpired as error:
        # The child may already have published fully verified OS/DHCP names.
        stdout = error.stdout or b''
      except (OSError, subprocess.SubprocessError):
        pass
      try:
        if len(stdout) > 8192:
          raise ValueError('DNS identity response too large')
        values = []
        for line in stdout.splitlines():
          batch = json.loads(line)
          if type(batch) is not list or any(not isinstance(value, str) or dns_name(value) != value for value in batch):
            raise ValueError('Invalid DNS identity response')
          values.extend(value for value in batch if value not in values)
        if len(values) > 16:
          raise ValueError('Too many DNS identities')
        names = tuple(values)
      except (ValueError, TypeError, IndexError):
        pass
      self.cache = {key: value for key, value in self.cache.items() if value[0] > now}
      if len(self.cache) >= self.MAX_ADDRESSES:
        self.cache.pop(next(iter(self.cache)))
      self.cache[destination] = (self.clock() + self.TTL, names)
      return names
    finally:
      self.lock.release()


def main():
  domains = []
  try:
    with Path('/etc/resolv.conf').open() as stream:
      domains = search_domains(stream.read(65536))
  except OSError:
    pass
  # Each name is a complete bounded record, so a later stalled lookup does not
  # discard verified progress and total output stays below 8 KiB.
  discover_names(sys.argv[1], domains=domains, publish=lambda names: print(json.dumps([names[-1]]), flush=True))


if __name__ == '__main__':
  main()
