"""Persistent, opt-in Galaxy reverse-proxy admission configuration."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from openpilot.starpilot.galaxy.http_server import ReverseProxy


class ReverseProxySettings:
  def __init__(self, root):
    self.path = Path(root) / 'reverse-proxy-v1.json'
    self.lock = threading.Lock()
    self.current = None
    try:
      record = self._read()
      if record['enabled']:
        self.current = ReverseProxy.validated(record['address'], record['origin'])
    except (OSError, ValueError, TypeError, KeyError):
      pass  # Invalid saved configuration must never grant proxy access.

  def _read(self):
    try:
      with self.path.open('rb') as stream:
        raw = stream.read(4097)
    except FileNotFoundError:
      return {'enabled': False, 'address': '', 'origin': ''}
    if len(raw) > 4096:
      raise ValueError('Proxy configuration is too large')
    record = json.loads(raw)
    if (type(record) is not dict or set(record) != {'enabled', 'address', 'origin'} or
        type(record['enabled']) is not bool or type(record['address']) is not str or type(record['origin']) is not str):
      raise ValueError('Invalid proxy configuration')
    return record

  def snapshot(self):
    record = self._read()
    return {**record, 'revision': hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()}

  def save(self, payload, authorized):
    if (type(payload) is not dict or set(payload) != {'enabled', 'address', 'origin', 'revision', 'confirmed'} or
        payload['confirmed'] is not True or type(payload['enabled']) is not bool or
        type(payload['address']) is not str or type(payload['origin']) is not str or
        len(payload['address']) > 64 or len(payload['origin']) > 256):
      raise ValueError('Invalid reverse proxy settings')
    proxy = ReverseProxy.validated(payload['address'], payload['origin']) if payload['enabled'] else None
    record = {key: payload[key] for key in ('enabled', 'address', 'origin')}
    with self.lock:
      if not authorized():
        raise PermissionError('Park the vehicle and sign in before changing proxy settings')
      if self.snapshot()['revision'] != payload['revision']:
        raise ValueError('Proxy settings changed; refresh before saving')
      self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
      temporary = None
      try:
        with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix='.proxy-', delete=False) as stream:
          temporary = stream.name
          stream.write(json.dumps(record).encode())
          stream.flush()
          os.fsync(stream.fileno())
        if not authorized():
          raise PermissionError('Proxy settings are no longer available')
        os.replace(temporary, self.path)
        temporary = None
        self.current = proxy
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
          os.fsync(directory)
        finally:
          os.close(directory)
      finally:
        if temporary is not None:
          os.unlink(temporary)
      return self.snapshot()
