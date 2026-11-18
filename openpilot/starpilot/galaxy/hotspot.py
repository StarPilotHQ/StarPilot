"""Saved, opt-in local Wi-Fi access. No network effects in the HTTP process."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time

INTERFACE = 'galaxy0'
ADDRESS = '172.31.254.1'
NETWORK = '172.31.254.0/24'
STATUS_PATH = Path('/tmp/starpilot-galaxy-hotspot-status.json')
DEFAULT = {'enabled': False, 'ssid': 'Galaxy', 'password': ''}
PAUSE_PATH = Path('/tmp/starpilot-galaxy-hotspot-pause.json')
MAX_PAUSE_S = 120.0


def device_dongle_id():
  from openpilot.common.params import Params
  from openpilot.starpilot.connect.provider import galaxy_device_id
  return galaxy_device_id(Params())


def hotspot_name(dongle_id):
  if type(dongle_id) is not str or not re.fullmatch(r'[a-fA-F0-9]{16}', dongle_id):
    raise ValueError('A registered comma dongle ID is required for the hotspot.')
  return 'TheGalaxy-' + dongle_id[-4:].lower()


def validate_config(value):
  if (type(value) is not dict or set(value) != set(DEFAULT) or type(value['enabled']) is not bool or
      type(value['ssid']) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,31}', value['ssid']) or
      type(value['password']) is not str or not re.fullmatch(r'[\x21-\x7e]{12,63}', value['password'])):
    raise ValueError('Use a 1–32 character network name and a 12–63 character password without spaces.')
  return dict(value)


def request_pause(seconds, path=PAUSE_PATH, clock=time.monotonic):
  """Ask the hotspot to stand down briefly, for example while the comma joins a Wi-Fi network during a drive."""
  atomic_json(path, {'until': clock() + min(float(seconds), MAX_PAUSE_S)}, mode=0o644)


def clear_pause(path=PAUSE_PATH):
  try:
    path.unlink()
  except FileNotFoundError:
    pass


def paused(path=PAUSE_PATH, clock=time.monotonic):
  try:
    until = bounded_json(path)['until']
    return type(until) in (int, float) and 0 < until - clock() <= MAX_PAUSE_S
  except (OSError, ValueError, KeyError, TypeError):
    return False


def atomic_json(path, value, mode=0o600):
  path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
  fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
  try:
    os.fchmod(fd, mode)
    with os.fdopen(fd, 'w') as handle:
      json.dump(value, handle)
      handle.flush()
      os.fsync(handle.fileno())
    os.replace(temporary, path)
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)


def bounded_json(path):
  fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
  with os.fdopen(fd, 'r') as handle:
    raw = handle.read(4097)
  if len(raw) > 4096:
    raise ValueError('Hotspot document is too large')
  return json.loads(raw)


class HotspotSettings:
  def __init__(self, root=None, status_path=STATUS_PATH, clock=time.monotonic, dongle_id=device_dongle_id):
    if root is None:
      from openpilot.starpilot.storage import galaxy_storage_root
      root = galaxy_storage_root()
    self.path = Path(root) / 'hotspot.json'
    self.status_path, self.clock = status_path, clock
    self.dongle_id = dongle_id
    self.lock = threading.Lock()

  def read(self):
    # Resolve the physical comma identity, including when another cloud provider
    # is active. Older configurable SSIDs are normalized without losing secrets.
    ssid = hotspot_name(self.dongle_id())
    try:
      return {**validate_config(bounded_json(self.path)), 'ssid': ssid}
    except FileNotFoundError:
      return {**DEFAULT, 'ssid': ssid}

  @staticmethod
  def revision(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

  def snapshot(self, parked):
    config = self.read()
    runtime = {'active': False, 'state': 'unavailable', 'reason': 'Hotspot service has not reported its status.'}
    try:
      saved = bounded_json(self.status_path)
      if (type(saved) is dict and type(saved.get('updated')) in (int, float) and
          0 <= self.clock() - saved['updated'] < 10 and
          saved.get('revision') == self.revision(config)):
        runtime = {key: saved[key] for key in ('active', 'state', 'reason', 'channel') if key in saved}
    except (OSError, ValueError, TypeError):
      pass
    return {'version': 1, 'config': config, 'revision': self.revision(config), 'editable': bool(parked),
            'url': f'http://{ADDRESS}:8082/', **runtime}

  def save(self, payload, permitted):
    if type(payload) is not dict or set(payload) != {'revision', 'config'}:
      raise ValueError('Invalid hotspot settings')
    with self.lock:
      current = self.read()
      if payload['revision'] != self.revision(current):
        raise FileExistsError('Hotspot settings changed; refresh and try again.')
      candidate = payload['config']
      if type(candidate) is dict and candidate.get('password') == '':
        candidate = {**candidate, 'password': current['password'] or secrets.token_hex(12)}
      candidate = validate_config(candidate)
      if candidate['ssid'] != current['ssid']:
        raise ValueError('The hotspot network name is fixed by the comma dongle ID.')
      if not permitted():
        raise PermissionError('Park and sign in before changing the hotspot.')
      atomic_json(self.path, candidate)
      return self.snapshot(permitted())
