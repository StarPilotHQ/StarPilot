"""Advertise galaxy.local through avahi's D-Bus API, answering with each interface's own address."""

import fcntl
from ipaddress import IPv6Address
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import time

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection
from jeepney.low_level import HeaderFields, MessageType

from openpilot.starpilot.galaxy.hotspot import INTERFACE as HOTSPOT_INTERFACE
from openpilot.starpilot.galaxy.local_hostnames import lan_address

NAMES = ('galaxy.local',) + tuple(f'galaxy-{n}.local' for n in range(2, 10))
INTERFACES = ('wlan0', 'eth0', HOTSPOT_INTERFACE)
NAME_FILE = Path('/data/galaxy/mdns_name')
IF_INET6 = Path('/proc/net/if_inet6')
SIOCGIFADDR = 0x8915
CHAIN = 'GALAXY_HTTP'
MAX_READ_BYTES = 65536
REFRESH_S = 10.0
ESTABLISH_S = 5.0
RETRY_S = 300.0
CALL_S = 5.0
RESOLVE_S = 1.5

AVAHI = 'org.freedesktop.Avahi'
SERVER = 'org.freedesktop.Avahi.Server'
ENTRY_GROUP = 'org.freedesktop.Avahi.EntryGroup'
PROTO_INET, PROTO_INET6 = 0, 1
PUBLISH_NO_REVERSE = 16
ESTABLISHED = 2

_published = None


def published_record():
  return _published


def _ifaddr(sock, name):
  return socket.inet_ntoa(fcntl.ioctl(sock.fileno(), SIOCGIFADDR, struct.pack('256s', name.encode()))[20:24])


def interface_addresses(lookup=_ifaddr):
  """Primary IPv4 of each candidate interface, read by ioctl."""
  addresses = {}
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
      for name in INTERFACES:
        try:
          addresses[name] = lookup(sock, name)
        except OSError:
          continue  # interface missing or has no IPv4 address
  except OSError:
    return {}
  return addresses



def _read_text(path, limit=MAX_READ_BYTES):
  fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
  with os.fdopen(fd, 'rb') as handle:
    data = handle.read(limit + 1)
  if len(data) > limit:
    raise ValueError(f'{path} exceeds {limit} bytes')
  return data.decode()



def own_address(address):
  """True for this device's current wlan0/eth0/galaxy0 LAN address."""
  return lan_address(address) and address in interface_addresses().values()


class Redirect:
  """Redirect port 80 on this device's own LAN addresses to Galaxy; one -d rule each, never forwarded traffic."""

  def __init__(self, port, run=subprocess.run, which=shutil.which):
    self.port, self.run = port, run
    self.firewall = which('iptables-legacy') or which('iptables')
    self.ready, self.applied = False, None

  def ipt(self, *args, check=True):
    if self.firewall is None:
      raise OSError('iptables missing')
    return self.run(['sudo', '-n', self.firewall, '-w', '2', '-t', 'nat', *args], capture_output=True, timeout=5, check=check)

  def sync(self, addresses):
    wanted = tuple(sorted(a for a in addresses if lan_address(a)))
    if self.firewall is None or wanted == self.applied:
      return
    try:
      if not self.ready:
        self.ipt('-N', CHAIN, check=False)  # already exists after a restart
        jump = ('PREROUTING', '-p', 'tcp', '--dport', '80', '-j', CHAIN)
        if self.ipt('-C', *jump, check=False).returncode != 0:
          self.ipt('-I', *jump)
        self.ready = True
      self.ipt('-F', CHAIN)
      for address in wanted:
        self.ipt('-A', CHAIN, '-d', address, '-p', 'tcp', '-j', 'REDIRECT', '--to-ports', str(self.port))
      self.applied = wanted
    except (OSError, subprocess.SubprocessError) as error:
      self.applied = wanted  # retried on the next address change, not every poll
      _log(f'port 80 redirect failed ({error}); http://galaxy.local needs :{self.port}')

  def close(self):
    if not self.ready:
      return
    try:
      self.ipt('-D', 'PREROUTING', '-p', 'tcp', '--dport', '80', '-j', CHAIN, check=False)
      self.ipt('-F', CHAIN, check=False)
      self.ipt('-X', CHAIN, check=False)
    except (OSError, subprocess.SubprocessError) as error:
      _log(f'port 80 redirect cleanup failed ({error})')
    self.ready, self.applied = False, None


def link_local(interface, path=IF_INET6):
  """The interface's fe80:: address, so AAAA lookups answer at once."""
  try:
    for line in _read_text(path).splitlines():
      fields = line.split()
      # address ifindex prefixlen scope flags name; scope 0x20 = link
      if len(fields) == 6 and fields[5] == interface and fields[3] == '20':
        return str(IPv6Address(bytes.fromhex(fields[0])))
  except (OSError, ValueError):
    pass
  return None



def records(found, ipv6=link_local, index=socket.if_nametoindex):
  """(interface index, protocol, address) for each LAN interface."""
  out = []
  for name in INTERFACES:
    address = found.get(name)
    if address is None or not lan_address(address):
      continue
    try:
      number = index(name)
    except OSError:
      continue
    out.append((number, PROTO_INET, address))
    address6 = ipv6(name)
    if address6 is not None:
      out.append((number, PROTO_INET6, address6))
  return tuple(out)


class AvahiError(Exception):
  def __init__(self, name, message=''):
    super().__init__(f'{name}: {message}' if message else name)
    self.name = name

  @property
  def collision(self):
    return self.name == 'org.freedesktop.Avahi.CollisionError'


class Avahi:
  """Entry groups belong to this D-Bus connection, so avahi withdraws them if Galaxy dies."""

  def __init__(self, connect=lambda: open_dbus_connection(bus='SYSTEM'), stopped=lambda: False):
    self.connect, self.stopped, self.connection = connect, stopped, None

  def call(self, path, interface, member, signature=None, body=()):
    if self.connection is None:
      self.connection = self.connect()
    address = DBusAddress(path, bus_name=AVAHI, interface=interface)
    message = new_method_call(address, member, signature, body) if signature else new_method_call(address, member)
    try:
      reply = self.connection.send_and_get_reply(message, timeout=CALL_S)
    except OSError:
      self.close()
      raise
    if reply.header.message_type == MessageType.error:
      raise AvahiError(str(reply.header.fields.get(HeaderFields.error_name, member)), str(reply.body[0]) if reply.body else '')
    return reply.body

  def first(self, path, interface, member, kind):
    body = self.call(path, interface, member)
    if len(body) != 1 or not isinstance(body[0], kind):
      raise AvahiError(f'{member} returned {body!r}')
    return body[0]

  def state(self, group):
    return self.first(group, ENTRY_GROUP, 'GetState', int)

  def claim(self, name, entries, timeout=ESTABLISH_S):
    """The committed group once avahi has probed the name, or None if another device holds it."""
    group = self.first('/', SERVER, 'EntryGroupNew', str)
    try:
      for number, protocol, address in entries:
        self.call(group, ENTRY_GROUP, 'AddAddress', 'iiuss', (number, protocol, PUBLISH_NO_REVERSE, name, address))
      self.call(group, ENTRY_GROUP, 'Commit')
      deadline = time.monotonic() + timeout
      while (state := self.state(group)) < ESTABLISHED and time.monotonic() < deadline and not self.stopped():
        time.sleep(0.1)
      if state == ESTABLISHED:
        return group
    except AvahiError as error:
      if not error.collision:
        self.free(group)
        raise
    self.free(group)
    return None

  def free(self, group):
    try:
      self.call(group, ENTRY_GROUP, 'Free')
    except (OSError, AvahiError):
      pass  # already withdrawn, or the connection is gone and avahi dropped it

  def close(self):
    if self.connection is not None:
      try:
        self.connection.close()
      except OSError:
        pass
      self.connection = None


def load_name(path=NAME_FILE):
  try:
    name = _read_text(path, 64).strip()
  except (OSError, ValueError):
    return None
  return name if name in NAMES else None


def save_name(name, path=NAME_FILE):
  try:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(name + '\n')
    os.replace(tmp, path)
  except OSError as error:
    _log(f'cannot remember {name} ({error})')


def candidates(remembered):
  return ((remembered,) if remembered else ()) + tuple(name for name in NAMES if name != remembered)


def held_elsewhere(name, own, run=subprocess.run):
  """True when another device already answers for name; probing alone can miss a responder that doesn't defend."""
  try:
    out = run(['avahi-resolve', '-4', '-n', name], capture_output=True, text=True, timeout=RESOLVE_S).stdout
  except (OSError, subprocess.SubprocessError):
    return False
  fields = out.split()
  return len(fields) == 2 and fields[0] == name and fields[1] not in own


def _claim(avahi, entries, remembered, stop, taken=held_elsewhere):
  own = {address for _, _, address in entries}
  for name in candidates(remembered):
    if stop.is_set():
      break
    if taken(name, own):
      continue
    group = avahi.claim(name, entries)
    if group is not None:
      return name, group
  return None, None


def _log(message, warning=True):
  from openpilot.common.swaglog import cloudlog
  (cloudlog.warning if warning else cloudlog.info)(f'Galaxy mDNS: {message}')


def publish(stop, *, avahi=None, addresses=interface_addresses, ipv6=link_local, index=socket.if_nametoindex, redirect=None,
            taken=held_elsewhere, name_file=NAME_FILE, refresh=REFRESH_S, retry=RETRY_S):
  """Keep the first free galaxy[-N].local name on every LAN interface until stop is set."""
  global _published
  avahi = avahi if avahi is not None else Avahi(stopped=stop.is_set)
  group, entries, retry_at, failure = None, (), 0.0, None
  remembered = load_name(name_file)

  def failed(message):
    nonlocal failure
    if message != failure:
      failure = message
      _log(f'{message}; retrying every {retry:.0f}s')

  try:
    while not stop.is_set():
      found = addresses()
      if redirect is not None:
        redirect.sync(found.values())
      wanted = records(found, ipv6, index)
      lost = False
      if group is not None:
        try:
          lost = avahi.state(group) != ESTABLISHED
        except (OSError, AvahiError):
          lost = True
        if lost and _published is not None:
          _log(f'lost {_published[0]}; reclaiming')
      if wanted != entries or lost or (group is None and wanted and time.monotonic() >= retry_at):
        if group is not None:
          avahi.free(group)
        group, _published, entries = None, None, wanted
        if wanted:
          name = None
          try:
            name, group = _claim(avahi, wanted, remembered, stop, taken)
          except (OSError, AvahiError) as error:
            failed(f'avahi unavailable ({error})')
          else:
            if name is None:
              failed('every galaxy[-N].local name is taken')
          if name is None:
            retry_at = time.monotonic() + retry
          else:
            failure = None
            _published = (name, tuple(address for _, protocol, address in wanted if protocol == PROTO_INET))
            interfaces = ', '.join(n for n in INTERFACES if found.get(n) in _published[1])
            _log(f'published {name} on {interfaces}', warning=False)
            if name != remembered:
              remembered = name
              save_name(name, name_file)
      stop.wait(refresh)
  finally:
    if group is not None:
      avahi.free(group)
    avahi.close()
    _published = None


def run(stop, port):
  redirect = Redirect(port)
  try:
    publish(stop, redirect=redirect)
  except Exception:
    from openpilot.common.swaglog import cloudlog
    cloudlog.exception('Galaxy mDNS: publisher stopped; galaxy.local is offline, the IP address still works')
  finally:
    redirect.close()
