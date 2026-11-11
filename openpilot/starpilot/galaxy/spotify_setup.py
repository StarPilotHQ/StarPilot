"""Galaxy setup owner for Spotify Soloist, modeled on the Android Auto package flow.

The HTTP server must authenticate the caller before invoking this owner. The API key is write-only:
no method here returns it. Configuration that changes the running player (install, output, account)
uses the shared connectivity authority (Park or offroad); playback controls work in any road state.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from openpilot.starpilot.soloist import backend, credentials, package, preferences
from openpilot.starpilot.soloist.phone_audio import A2DP_SOURCE
from openpilot.starpilot.soloist.paths import CONTROL_SOCKET, Layout, private_dir

LINKS = {
  'download': package.DOWNLOAD_PAGE,
  'key': 'https://developer.spotify.com/dashboard/soloist',
  'help': 'https://developer.spotify.com/documentation/soloist/tutorials/getting-started',
}


class SetupRejected(RuntimeError):
  pass


class SpotifyClient:
  def __init__(self, path: str = CONTROL_SOCKET, timeout: float = 3.0):
    self.path, self.timeout = path, timeout

  @property
  def available(self) -> bool:
    return os.path.exists(self.path)

  def call(self, command: str, **payload) -> dict:
    if not self.available:
      raise RuntimeError('Spotify isn’t running')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
      sock.settimeout(self.timeout)
      sock.connect(self.path)
      sock.sendall(json.dumps({'command': command, **payload}).encode() + b'\n')
      response = bytearray()
      while not response.endswith(b'\n') and len(response) < 1024 * 1024:
        chunk = sock.recv(65536)
        if not chunk:
          break
        response.extend(chunk)
    result = json.loads(response) if response else {}
    if not result.get('ok'):
      raise RuntimeError(str(result.get('error') or 'Spotify did not respond'))
    return result


class SpotifySetup:
  def __init__(self, *, parked: Callable[[], bool], session_valid: Callable[[tuple], bool],
               layout: Layout | None = None, client: SpotifyClient | None = None,
               import_job: package.ImportJob | None = None,
               paired_devices: Callable[[tuple], list[dict]] = lambda session: [],
               clock: Callable[[], float] = time.monotonic):
    self.parked, self.session_valid = parked, session_valid
    self.layout = layout or Layout.default()
    self.client = client or SpotifyClient()
    self.job = import_job or package.ImportJob(self.layout)
    self.paired_devices, self.clock = paired_devices, clock
    self._upload_lock = threading.Lock()

  def _require_session(self, session: tuple) -> None:
    if not session or not self.session_valid(session):
      raise SetupRejected('Galaxy session expired; sign in again')

  def _require_parked(self, what: str) -> None:
    if not self.parked():
      raise SetupRejected(f'Put the car in Park or use offroad mode to {what}')

  def _service(self) -> dict | None:
    try:
      return self.client.call('status')['status'] if self.client.available else None
    except (OSError, RuntimeError, ValueError, KeyError):
      return None

  def status(self, session: tuple) -> dict:
    self._require_session(session)
    prefs = preferences.load(self.layout)
    try:
      devices = [{'address': d['address'], 'name': d['name'], 'connected': d.get('connected') is True,
                  'audioSink': d.get('audioSink') is True}
                 for d in self.paired_devices(session) if d.get('paired')]
    except Exception:
      devices = []
    return {
      'enabled': self.layout.enabled_flag.exists(), 'parked': self.parked(),
      'release': package.release_status(self.layout), 'keyPresent': credentials.present(self.layout),
      'audioReady': backend.bundle_ready(),
      'preferences': prefs.public(), 'devices': devices, 'service': self._service(),
      'import': self.job.status(), 'maxUploadBytes': package.MAX_UPLOAD_BYTES, 'archiveName': package.ARCHIVE_NAME,
      'cacheChoices': list(preferences.CACHE_CHOICES), 'links': LINKS,
      'clock': int(time.time() * 1000),  # noqa: TID251 lets the browser rebase epoch-ms play positions
    }

  def enable(self, session: tuple, value: bool) -> dict:
    self._require_session(session)
    if type(value) is not bool:
      raise SetupRejected('Invalid Spotify setting')
    preferences.set_enabled(value, self.layout)
    return self.status(session)

  def upload(self, session: tuple, source: BinaryIO, length: int) -> dict:
    self._require_session(session)
    self._require_parked('install Spotify')
    if type(length) is not int or not 0 < length <= package.MAX_UPLOAD_BYTES:
      raise SetupRejected('Invalid Soloist download size')
    if not self._upload_lock.acquire(blocking=False):
      raise SetupRejected('Another Soloist upload is running')
    path: Path | None = None
    started = False
    try:
      if self.job.busy():
        raise SetupRejected('A Soloist install is running')
      work_dir = private_dir(self.job.work_dir)
      for stale in work_dir.glob('galaxy-upload-*'):
        stale.unlink(missing_ok=True)
      fd, name = tempfile.mkstemp(prefix='galaxy-upload-', suffix='.tar.gz', dir=work_dir)
      path = Path(name)
      with os.fdopen(fd, 'wb') as out:
        remaining = length
        while remaining:
          self._require_session(session)
          chunk = source.read(min(1024 * 1024, remaining))
          if not chunk:
            raise SetupRejected('Soloist upload ended early')
          out.write(chunk)
          remaining -= len(chunk)
        out.flush()
        os.fsync(out.fileno())
      self._require_session(session)
      self.job.start(path=path, enabled=lambda: self.session_valid(session), may_activate=self.parked)
      started = True
      return self.status(session)
    finally:
      if path is not None and not started:
        path.unlink(missing_ok=True)
      self._upload_lock.release()

  def set_key(self, session: tuple, key: object) -> dict:
    self._require_session(session)
    try:
      credentials.store(key, self.layout)
    except credentials.InvalidKey as error:
      raise SetupRejected(str(error)) from error
    return self.status(session)

  def save_preferences(self, session: tuple, payload: dict) -> dict:
    self._require_session(session)
    current = preferences.load(self.layout)
    fields = {'deviceName': 'device_name', 'cacheMb': 'cache_mb', 'initialVolume': 'initial_volume',
              'output': 'output', 'outputAddress': 'output_address', 'source': 'source', 'phoneAddress': 'phone_address',
              'pauseMute': 'pause_mute', 'skipMute': 'skip_mute'}
    if not isinstance(payload, dict) or not payload or set(payload) - set(fields):
      raise SetupRejected('Invalid Spotify settings')
    values = {**current.__dict__, **{fields[key]: value for key, value in payload.items()}}
    changes_output = values['output'] != current.output or values['output_address'] != current.output_address
    # Flipping between Spotify and the already chosen phone is a playback control; picking a different phone is setup.
    if values['phone_address'] != current.phone_address:
      self._require_parked('change the phone')
    if values['source'] != current.source or values['phone_address'] != current.phone_address:
      if values['phone_address']:
        match = next((d for d in self.status(session)['devices'] if d['address'] == str(values['phone_address']).upper()), None)
        if match is None:
          raise SetupRejected('Pair your phone on the Bluetooth page first')
        values['phone_name'] = match['name']
      else:
        values['phone_name'] = ''
    if changes_output:
      self._require_parked('change where music plays')
      if values['output'] == 'bluetooth' and values['output_address']:
        match = next((d for d in self.status(session)['devices'] if d['address'] == str(values['output_address']).upper()), None)
        if match is None:
          raise SetupRejected('Pair that speaker or car on the Bluetooth page first')
        values['output_name'] = match['name']
      else:
        values['output_name'] = ''
    try:
      preferences.save(preferences.validated(values), self.layout)
    except (ValueError, TypeError) as error:
      raise SetupRejected(str(error)) from error
    return self.status(session)

  def prepare_phone_pairing(self) -> None:
    """Advertise the music receiver before a phone pairs, so the phone offers the comma as an audio output."""
    if not (self.layout.enabled_flag.exists() and self.client.available):
      return
    try:
      self.client.call('prepare_phone_pairing')
    except (OSError, RuntimeError, ValueError):
      pass

  def adopt_phone(self, address: str, name: str, uuids: object) -> bool:
    """Offer a newly paired phone as the music source without overriding anything the owner chose."""
    if not isinstance(uuids, (list, tuple)) or A2DP_SOURCE not in {str(value).lower() for value in uuids}:
      return False
    current = preferences.load(self.layout)
    if current.phone_address:
      return False
    values = {**current.__dict__, 'phone_address': str(address).upper(), 'phone_name': str(name)}
    if not package.release_status(self.layout).get('installed'):
      values['source'] = 'phone'
    try:
      preferences.save(preferences.validated(values), self.layout)
    except (ValueError, TypeError):
      return False
    return True

  def control(self, session: tuple, action: object, args: object) -> dict:
    self._require_session(session)
    if not isinstance(action, str) or not isinstance(args, dict):
      raise SetupRejected('Invalid Spotify control')
    try:
      self.client.call('control', action=action, args=args)
    except (OSError, ValueError) as error:
      raise SetupRejected('Spotify isn’t responding') from error
    except RuntimeError as error:
      raise SetupRejected(str(error)) from error
    return {'ok': True}

  def maintenance(self, session: tuple, action: str) -> dict:
    self._require_session(session)
    if action not in ('reset_session', 'clear_cache', 'remove_key'):
      raise SetupRejected('Invalid Spotify action')
    if action == 'remove_key':
      credentials.remove(self.layout)
      return self.status(session)
    self._require_parked('change your Spotify account' if action == 'reset_session' else 'clear the cache')
    if self.client.available:
      try:
        self.client.call(action)
      except (OSError, RuntimeError, ValueError) as error:
        raise SetupRejected('Spotify isn’t responding') from error
    else:
      shutil.rmtree(self.layout.cache, ignore_errors=True)
      if action == 'reset_session':
        shutil.rmtree(self.layout.state, ignore_errors=True)
    return self.status(session)

  def remove(self, session: tuple, *, wait: float = 12.0) -> dict:
    """Disable, wait for the manager to stop the player, then delete only Soloist-owned files."""
    self._require_session(session)
    with self._upload_lock:
      if self.job.busy():
        raise SetupRejected('Wait for the Soloist install to finish')
      preferences.set_enabled(False, self.layout)
      deadline = self.clock() + wait
      while self.client.available and self.clock() < deadline:
        time.sleep(0.25)
      if self.client.available:
        raise SetupRejected('Spotify is still stopping; try again in a moment')
      from openpilot.starpilot.soloist.supervisor import reap_orphans
      reap_orphans(self.layout)
      shutil.rmtree(self.layout.root, ignore_errors=True)
    return self.status(session)
