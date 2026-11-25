"""Actual LAN addresses for direct Galaxy access, independent of the tunnel."""

from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from datetime import UTC, datetime
from ipaddress import IPv4Address, ip_network
import json
import re
import subprocess
import threading
import time
from zipfile import ZIP_DEFLATED, ZipFile


LAN = tuple(ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16'))


# Virtual links can carry RFC1918 addresses too; they are not a local Wi-Fi/Ethernet address.
TUNNEL_KINDS = frozenset(('tun', 'wireguard', 'gre', 'gretap', 'ipip', 'sit', 'vti', 'vti6', 'vxlan', 'geneve'))
TUNNEL_PREFIXES = ('tun', 'tap', 'wg', 'tailscale', 'zt', 'docker', 'veth', 'br-', 'p2p', 'usb', 'rndis', 'ppp', 'wwan', 'rmnet')


class LocalAccess:
  def __init__(self, port=8082, *, run=subprocess.run, clock=time.monotonic):
    self.port, self.run, self.clock = port, run, clock
    self.lock = threading.Lock()
    self.cached, self.expiry = None, 0.0

  def snapshot(self):
    with self.lock:
      if self.cached is not None and self.clock() < self.expiry:
        return self.cached
      addresses = []
      reason = 'Connect to the Galaxy hotspot, or put the comma and phone on the same Wi-Fi or Ethernet network.'
      try:
        result = self.run(['ip', '-j', '-4', 'address', 'show', 'up'], capture_output=True, timeout=1, check=True)
        if len(result.stdout) > 65536:
          raise ValueError('Network inventory too large')
        interfaces = json.loads(result.stdout)
        if not isinstance(interfaces, list):
          raise ValueError('Invalid network inventory')
        for interface in interfaces[:64]:
          if len(addresses) >= 32:
            break
          if type(interface) is not dict:
            continue
          name = interface.get('ifname', '')
          flags = interface.get('flags', [])
          info_rows = interface.get('addr_info', [])
          if (not isinstance(name, str) or not name or type(flags) is not list or
              'UP' not in flags or 'LOOPBACK' in flags or type(info_rows) is not list or
              name.lower().startswith(TUNNEL_PREFIXES) or
              interface.get('operstate') in ('DOWN', 'LOWERLAYERDOWN', 'NOTPRESENT') or
              isinstance(interface.get('linkinfo'), dict) and
              interface['linkinfo'].get('info_kind') in TUNNEL_KINDS):
            continue
          for info in info_rows[:16]:
            if len(addresses) >= 32:
              break
            if type(info) is not dict:
              continue
            if (info.get('family') != 'inet' or info.get('scope') != 'global' or
                info.get('valid_life_time') == 0 or info.get('preferred_life_time') == 0 or
                info.get('tentative') or info.get('dadfailed')):
              continue
            address = IPv4Address(info['local'])
            if not any(address in network for network in LAN):
              continue
            url = f'http://{address}:{self.port}/'
            if any(row['url'] == url for row in addresses):
              continue
            addresses.append({'interface': name[:64], 'label': f'{address} ({name[:64]})', 'url': url})
        addresses.sort(key=lambda row: (row['interface'], row['url']))
      except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        reason = 'The comma could not read its network addresses. Check its Network settings and refresh.'
        addresses = []
      self.cached = {'available': bool(addresses), 'addresses': addresses,
                     'reason': '' if addresses else reason}
      self.expiry = self.clock() + 5.0
      return self.cached


DISCOVERY_UNIT = 'galaxy-lan-discovery.service'
DISCOVERY_MESSAGE = re.compile(''.join((
  r'(Galaxy Wi-Fi discovery available on [0-9]{1,3} local interface\(s\)|',
  r'Wi-Fi discovery unavailable: [A-Za-z][A-Za-z0-9_]{0,63}|LAN publisher cleanup failed)\Z',
)))


BLE_UNIT = 'galaxy-ble-fork.service'
BLE_MESSAGE = re.compile(''.join((
  r'(Pairing credentials changed; [0-9]{1,4} phone\(s\) paired|Phone claimed pairing window|',
  r'Galaxy BLE handshake challenge requested; active sessions=[0-9]{1,4}|',
  r'Rejected invalid or unauthenticated BLE message|Notification transfer interrupted \([A-Za-z0-9_]{1,64}\); reconnect to check request outcome|',
  r'BLE advertisement released by BlueZ; bridge will re-register after restart|Registering Galaxy BLE advertisement|',
  r'Starting Galaxy bridge pid=[0-9]{1,10} source=[0-9a-f]{12}|',
  r'Galaxy BLE bridge ready on /org/bluez/hci[0-9]{1,3}; forwarding to localhost:[0-9]{1,5}|',
  r'Stopping Galaxy bridge; clearing ready status and Bluetooth sessions)\Z',
)))
INTERFACE_NAME = re.compile(r'[A-Za-z0-9_.:-]{1,32}\Z')


def _network_inventory(run):
  lines = []
  try:
    result = run(['ip', '-j', '-4', 'address', 'show'], capture_output=True, timeout=1, check=True)
    rows = json.loads(result.stdout) if len(result.stdout) <= 65536 else None
    if not isinstance(rows, list):
      raise ValueError('Invalid network inventory')
    for row in rows[:64]:
      if not isinstance(row, dict):
        continue
      name = row.get('ifname')
      if not isinstance(name, str) or not INTERFACE_NAME.fullmatch(name):
        continue
      state = row.get('operstate')
      if state not in ('UNKNOWN', 'NOTPRESENT', 'DOWN', 'LOWERLAYERDOWN', 'TESTING', 'DORMANT', 'UP'):
        state = 'unknown'
      flags = row.get('flags', [])
      if not isinstance(flags, list):
        flags = []
      flags = ','.join(flag for flag in ('UP', 'LOWER_UP', 'RUNNING', 'MULTICAST', 'LOOPBACK') if flag in flags)
      addresses = []
      info_rows = row.get('addr_info', [])
      for info in info_rows[:16] if isinstance(info_rows, list) else []:
        if not isinstance(info, dict):
          continue
        try:
          address, prefix = IPv4Address(info.get('local')), info.get('prefixlen')
        except ValueError:
          continue
        if any(address in network for network in LAN) and type(prefix) is int and 0 <= prefix <= 32:
          addresses.append(f'{address}/{prefix}')
      lines.append(f'{name}: state={state}; flags={flags or "none"}; private IPv4={", ".join(addresses) or "none"}')
  except (OSError, subprocess.SubprocessError, ValueError):
    lines.append('Network interface inventory unavailable')
  try:
    result = run(['ip', '-j', '-4', 'route', 'show', 'default'], capture_output=True, timeout=1, check=True)
    rows = json.loads(result.stdout) if len(result.stdout) <= 65536 else None
    if not isinstance(rows, list):
      raise ValueError('Invalid route inventory')
    interfaces = [row['dev'] for row in rows[:16] if isinstance(row, dict)
                  and isinstance(row.get('dev'), str) and INTERFACE_NAME.fullmatch(row['dev'])]
    lines.append('IPv4 default route interfaces: ' + (', '.join(interfaces) or 'none'))
  except (OSError, subprocess.SubprocessError, ValueError):
    lines.append('IPv4 default route inventory unavailable')
  try:
    result = run(['ss', '-H', '-lnt', 'sport = :8082'], capture_output=True, timeout=1, check=True)
    bindings = set()
    for line in result.stdout.decode('ascii', errors='replace').splitlines()[:32]:
      fields = line.split()
      if len(fields) < 5 or fields[0] != 'LISTEN' or not fields[3].endswith(':8082'):
        continue
      host = fields[3][:-5]
      if host in ('*', '0.0.0.0', '[::]'):
        bindings.add('all interfaces')
      elif host.startswith('127.') or host == '[::1]':
        bindings.add('loopback')
      else:
        bindings.add('specific interface')
    lines.append('TCP port 8082 listening: ' + (', '.join(sorted(bindings)) or 'no'))
  except (OSError, subprocess.SubprocessError):
    lines.append('TCP port 8082 listener state unavailable')
  return lines


def _service_capture(run, unit, pattern, label):
  state = 'unavailable'
  details = []
  try:
    result = run(['systemctl', 'show', unit, '--property=ActiveState,SubState,Result,NRestarts,ExecMainStatus'],
                 capture_output=True, timeout=1, check=True)
    for line in result.stdout.decode('ascii', errors='replace').splitlines()[:12]:
      if re.fullmatch(r'(?:ActiveState|SubState|Result)=[a-z][a-z0-9-]{0,39}|(?:NRestarts|ExecMainStatus)=[0-9]{1,10}', line):
        if line.startswith('ActiveState='):
          state = line.split('=', 1)[1]
        else:
          details.append(line)
  except (OSError, subprocess.SubprocessError):
    pass
  lines = [f'{label}: {state}', *details, 'Recent service events (last 2 hours, up to 120 journal records read)']
  try:
    result = run(['journalctl', '--no-pager', '--quiet', '-u', unit, '--since', '-2h',
                  '-n', '120', '-o', 'json', '--output-fields=MESSAGE'],
                 capture_output=True, timeout=2, check=True)
    events = []
    for raw in result.stdout.splitlines()[-120:]:
      if len(raw) > 8192:
        continue
      try:
        entry = json.loads(raw)
        message = entry.get('MESSAGE') if isinstance(entry, dict) else None
        if isinstance(message, str) and pattern.fullmatch(message):
          stamp = entry.get('__REALTIME_TIMESTAMP', '')
          if isinstance(stamp, str) and re.fullmatch(r'[0-9]{1,16}', stamp):
            timestamp = datetime.fromtimestamp(int(stamp) / 1e6, UTC).isoformat(timespec='seconds')
            message = f'{timestamp} {message}'
          events.append(message)
      except (ValueError, OSError, OverflowError):
        continue
    lines.extend(events or ['No matching events'])
  except (OSError, subprocess.SubprocessError):
    lines.append('Advertiser journal unavailable' if unit == DISCOVERY_UNIT else 'BLE journal unavailable')
  return lines


def connection_capture(addresses, *, configuration_allowed=None, run=subprocess.run):
  lines = ['StarPilot connection capture', 'Capture schema: 2',
           f'Captured at (UTC): {datetime.now(UTC).isoformat(timespec="seconds")}',
           'Galaxy endpoint: HTTP port 8082', 'Bonjour service: _sp-galaxy._tcp.local.']
  try:
    with open('/proc/uptime') as stream:
      lines.append(f'Comma uptime: {float(stream.read(128).split()[0]):.0f} seconds')
  except (OSError, ValueError, IndexError):
    lines.append('Comma uptime: unavailable')
  if configuration_allowed is not None:
    try:
      lines.append(f'Parked configuration allowed: {bool(configuration_allowed())}')
    except Exception:
      lines.append('Parked configuration allowed: unavailable')
  lines.extend(['', 'Advertisable local addresses'])
  lines.extend(f'{row["interface"]}: {row["url"]}' for row in addresses['addresses'])
  if not addresses['available']:
    lines.append(addresses['reason'] or 'No eligible local address')
  with ThreadPoolExecutor(max_workers=3) as pool:
    network = pool.submit(_network_inventory, run)
    discovery = pool.submit(_service_capture, run, DISCOVERY_UNIT, DISCOVERY_MESSAGE, 'Bonjour advertiser')
    bluetooth = pool.submit(_service_capture, run, BLE_UNIT, BLE_MESSAGE, 'Bluetooth bridge')
    lines.extend(['', 'Network interfaces', *network.result(), '', *discovery.result(), '', *bluetooth.result()])
  report = '\n'.join(lines) + '\n'
  output = BytesIO()
  with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
    archive.writestr('connection-logs.txt', report)
  # Keep the original text field for browsers still running the previous page.
  return {'schemaVersion': 1, 'text': report[:16384], 'zipBase64': b64encode(output.getvalue()).decode('ascii')}
