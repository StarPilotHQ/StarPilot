"""Phone uplink protocol and local-only lease. Safe to import from data guards."""
from __future__ import annotations

import ipaddress
import math
import os
from pathlib import Path
import re
import threading
import time

from openpilot.starpilot.galaxy.hotspot import ADDRESS, NETWORK, atomic_json, bounded_json

COMPANION_PROTOCOL = 1
LEASE_SECONDS = 15
LEASE_PATH = Path('/dev/shm/starpilot-companion.json')
STATUS_PATH = Path('/run/starpilot-companion/status.json')
APK_PATH = Path(__file__).parent / 'bin/starpilot-companion.apk'
UPLOAD_DENY_HOSTS = ['commadataci.blob.core.windows.net', 'commadata2.blob.core.windows.net',
                     'upload.comma.ai', 'logs.comma.ai']


def fresh(value, now):
  updated = value.get('updated')
  return type(updated) in (int, float) and math.isfinite(updated) and 0 <= now - updated < LEASE_SECONDS


_status_cache: dict = {}


def phone_uplink_active(path=STATUS_PATH, clock=time.monotonic) -> bool:
  # Guards call this per upload chunk; re-parse only when the file is replaced (atomic_json renames).
  try:
    st = os.lstat(path)
    key = (st.st_ino, st.st_mtime_ns, st.st_size)
    cached = _status_cache.get(str(path))
    if cached is None or cached[0] != key:
      state = bounded_json(path)
      cached = (key, state.get('active') is True, state)
      _status_cache[str(path)] = cached
    return cached[1] and fresh(cached[2], clock())
  except (OSError, ValueError, TypeError, AttributeError):
    return False


def validate_lease(value):
  if type(value) is not dict:
    raise ValueError('Invalid companion heartbeat')
  try:
    peer = ipaddress.ip_address(value['peer'])
    subnet = ipaddress.ip_network(NETWORK)
    if peer not in subnet or peer in (subnet.network_address, subnet.broadcast_address, ipaddress.ip_address(ADDRESS)):
      raise ValueError('A Galaxy hotspot peer is required')
    if type(value['port']) is not int or not 1024 <= value['port'] <= 65535:
      raise ValueError('Invalid SOCKS port')
    if type(value['protocol']) is not int or value['protocol'] != COMPANION_PROTOCOL:
      raise ValueError('Update the companion app')
    for key in ('user', 'pass'):
      if type(value[key]) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', value[key]):
        raise ValueError('Invalid SOCKS credentials')
    if type(value['version']) is not str or not re.fullmatch(r'[0-9A-Za-z._-]{1,40}', value['version']):
      raise ValueError('Invalid app version')
    if type(value.get('bytes', 0)) is not int or not 0 <= value.get('bytes', 0) <= 2**63 - 1:
      raise ValueError('Invalid byte counter')
  except (KeyError, TypeError) as error:
    raise ValueError('Invalid companion heartbeat') from error
  return {key: value[key] for key in ('peer', 'port', 'protocol', 'user', 'pass', 'version')} | {'bytes': value.get('bytes', 0)}


class CompanionRegistry:
  def __init__(self, root=None, lease_path=LEASE_PATH, status_path=STATUS_PATH, clock=time.monotonic):
    if root is None:
      from openpilot.starpilot.storage import starpilot_storage_root
      root = starpilot_storage_root() / 'companion'
    self.path = Path(root) / 'settings.json'
    self.lease_path, self.status_path, self.clock = lease_path, status_path, clock
    self.lock = threading.Lock()

  def enabled(self):
    try:
      return bounded_json(self.path).get('enabled') is True
    except FileNotFoundError:
      return False

  def save(self, payload):
    if type(payload) is not dict or set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
      raise ValueError('Expected enabled boolean')
    with self.lock:
      atomic_json(self.path, payload)
      if not payload['enabled']:
        self.lease_path.unlink(missing_ok=True)
    return self.snapshot()

  def heartbeat(self, payload, peer, remote=False):
    if remote:
      raise PermissionError('Phone uplink is only available on the Galaxy hotspot')
    if type(payload) is not dict or set(payload) - {'port', 'protocol', 'user', 'pass', 'version', 'bytes'}:
      raise ValueError('Invalid heartbeat fields')
    lease = validate_lease({**payload, 'peer': peer})
    with self.lock:
      previous = self.lease()
      # The same hotspot peer may rotate credentials (app restart); only a different phone is refused.
      if previous and previous['peer'] != peer:
        raise FileExistsError('Another phone is sharing data')
      # The phone's own opt-in (with its disclosure) is consent: a hotspot heartbeat turns sharing on.
      if not self.enabled():
        atomic_json(self.path, {'enabled': True})
      enabled = True
      atomic_json(self.lease_path, {**lease, 'updated': self.clock()})
    return {'ok': True, 'enabled': enabled, 'active': phone_uplink_active(self.status_path, self.clock),
            'protocol': COMPANION_PROTOCOL, 'uploadDenyHosts': UPLOAD_DENY_HOSTS}

  def lease(self):
    try:
      value = bounded_json(self.lease_path)
      return {**validate_lease(value), 'updated': value['updated']} if fresh(value, self.clock()) else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
      return None

  def snapshot(self):
    lease = self.lease()
    try:
      shipped = APK_PATH.with_suffix('.apk.version').read_text().strip()
    except OSError:
      shipped = ''
    active = phone_uplink_active(self.status_path, self.clock)
    reason = ''
    try:
      runtime = bounded_json(self.status_path)
      if fresh(runtime, self.clock()) and runtime.get('state') == 'error':
        reason = str(runtime.get('reason', 'Phone uplink unavailable'))[:256]
    except (OSError, ValueError, TypeError, AttributeError):
      pass
    return {'protocol': COMPANION_PROTOCOL, 'enabled': self.enabled(), 'connected': lease is not None,
            'active': active, 'state': 'active' if active else ('error' if reason else 'waiting') if self.enabled() else 'disabled',
            'reason': reason,
            'bytes': lease['bytes'] if lease else 0, 'version': lease['version'] if lease else None,
            'appAvailable': APK_PATH.is_file(), 'shippedVersion': shipped,
            'updateAvailable': bool(lease and shipped and lease['version'] != shipped)}
