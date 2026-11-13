"""Capture one paired phone through BlueALSA and feed the private music server.

The worker's arecord child shares its process group, so supervisor shutdown kills
both even if the output is blocked. ALSA plug converts to 48 kHz stereo; Pulse's
bounded playback queue keeps backpressure from growing an unbounded Python queue.
No system default audio device is opened.
"""
from __future__ import annotations

import ctypes as C
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from openpilot.starpilot.soloist import bluealsa_runtime
from openpilot.starpilot.soloist import preferences
from openpilot.starpilot.soloist.paths import Layout
from openpilot.starpilot.soloist.preferences import ADDRESS
from openpilot.starpilot.soloist.pulse_client import PulseWriter

A2DP_SOURCE = '0000110a-0000-1000-8000-00805f9b34fb'
MEDIA_PLAYER = 'org.bluez.MediaPlayer1'
MEDIA_TRANSPORT = 'org.bluez.MediaTransport1'
PLAYER_METHODS = {'play': 'Play', 'pause': 'Pause', 'next': 'Next', 'previous': 'Previous'}
# Flush queued audio and mute incoming samples until the phone handles pause or skip.
FLUSH_ACTIONS = {'pause': (signal.SIGUSR1, 'pause'), 'next': (signal.SIGUSR2, 'skip'), 'previous': (signal.SIGUSR2, 'skip')}


def mute_seconds(kind: str, path: Path | None = None) -> float:
  default = getattr(preferences.Preferences(), f'{kind}_mute')
  try:
    config = path or Path(os.environ.get('STARPILOT_SOLOIST_CONFIG') or Layout.default().config)
    value = json.loads(config.read_text())[f'{kind}_mute']
    if not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= preferences.MUTE_MAX:
      return float(value)
  except (OSError, ValueError, KeyError, TypeError):
    pass
  return default


def profile(action: str) -> None:
  if action not in ('acquire', 'release'):
    raise ValueError('Unknown profile action')
  command = ['/usr/bin/python3', str(Path(__file__).with_name('phone_profile.py')), action]
  if os.geteuid() != 0:
    command = ['/usr/bin/sudo', '-n', *command]
  result = subprocess.run(command, capture_output=True, text=True, timeout=60)
  if result.returncode:
    raise RuntimeError('Could not configure phone audio: ' + result.stderr.strip()[-500:])


class PhoneInput:
  def __init__(self):
    self.client = None
    self.next_connect = 0.0

  def _objects(self):
    from openpilot.starpilot.bluetooth.owner import BlueZ
    try:
      if self.client is None:
        self.client = BlueZ()
      return self.client._objects()
    except Exception as error:
      self.close()
      raise RuntimeError('Bluetooth is unavailable. Turn it on from the Bluetooth page.') from error

  def _call(self, *args, **kwargs):
    try:
      return self.client._call(*args, **kwargs)
    except Exception as error:
      raise RuntimeError(str(error)) from error

  def powered(self) -> bool:
    from openpilot.starpilot.bluetooth.owner import ADAPTER
    return any(interfaces.get(ADAPTER, {}).get('Powered') is True for interfaces in self._objects().values())

  def ready(self, address: str, now: float) -> bool:
    from openpilot.starpilot.bluetooth.owner import DEVICE
    objects = self._objects()
    for path, interfaces in objects.items():
      props = interfaces.get(DEVICE, {})
      if str(props.get('Address', '')).upper() != address:
        continue
      if not props.get('Paired') or props.get('Blocked'):
        return False
      if A2DP_SOURCE not in props.get('UUIDs', []):
        raise RuntimeError('The selected device does not offer Bluetooth music. Choose your phone.')
      # A streaming transport already proves the profile is connected. Do not
      # send periodic ConnectProfile requests into an established audio stream.
      if props.get('Connected') and any(
        transport.get('Device') == path and transport.get('State') in ('pending', 'active')
        for item in objects.values() if (transport := item.get(MEDIA_TRANSPORT)) is not None
      ):
        return True
      # Connected alone does not mean A2DP is connected (e.g. an existing HFP link).
      if now >= self.next_connect:
        self.next_connect = now + 20
        try:
          self._call(path, DEVICE, 'ConnectProfile', 's', (A2DP_SOURCE,), timeout=5.0)
        except RuntimeError:
          if not props.get('Connected'):
            return False
      return bool(props.get('Connected'))
    return False

  def track(self, address: str) -> dict | None:
    """The phone's Bluetooth media player (title, artist, status), for the car screen; never raises."""
    try:
      from openpilot.starpilot.bluetooth.owner import DEVICE
      objects = self._objects()
      device = next((path for path, interfaces in objects.items()
                     if str(interfaces.get(DEVICE, {}).get('Address', '')).upper() == address), None)
      return next((interfaces[MEDIA_PLAYER] for path, interfaces in objects.items()
                   if device and path.startswith(device + '/') and MEDIA_PLAYER in interfaces), None)
    except Exception:
      return None

  def pause(self, address: str) -> None:
    self.control(address, 'pause')

  def control(self, address: str, action: str) -> str | None:
    """Send a playback command to the phone; returns the command sent (play_pause resolves to play or pause)."""
    if action not in (*PLAYER_METHODS, 'play_pause'):
      raise ValueError('Unsupported phone control')
    if self.client is None:
      return None
    from openpilot.starpilot.bluetooth.owner import DEVICE
    objects = self._objects()
    device = next((path for path, interfaces in objects.items()
                   if str(interfaces.get(DEVICE, {}).get('Address', '')).upper() == address), None)
    if device:
      for path, interfaces in objects.items():
        if path.startswith(device + '/') and MEDIA_PLAYER in interfaces:
          if action == 'play_pause':
            action = 'pause' if str(interfaces[MEDIA_PLAYER].get('Status', '')).lower() == 'playing' else 'play'
          self._call(path, MEDIA_PLAYER, PLAYER_METHODS[action], timeout=2.0)
          return action
    return None

  def close(self):
    if self.client is not None:
      try:
        self.client.close()
      except Exception:
        pass
      self.client = None
    self.next_connect = 0.0


def capture_config(address: str) -> str:
  if not ADDRESS.fullmatch(address):
    raise ValueError('Invalid phone Bluetooth address')
  return ('</usr/share/alsa/alsa.conf>\n' + bluealsa_runtime.alsa_plugins() + 'pcm.starpilot_phone {\n type plug\n slave.pcm {\n' +
          f'  type bluealsa\n  device "{address}"\n  profile "a2dp"\n  hwcompat "silence"\n  softvol "true"\n' + ' }\n}\n')


class FlushRequest:
  """Set from signal handlers (which only store a name); the audio loop does the flush."""
  kind: str | None = None


def request_flush(kind: str):
  # A pause outranks a skip that lands in the same read.
  if FlushRequest.kind != 'pause':
    FlushRequest.kind = kind


def relay(address: str, server: str, config: Path):
  config.write_text(capture_config(address))
  os.chmod(config, 0o600)
  writer = PulseWriter(server)
  try:
    with subprocess.Popen(['/usr/bin/arecord', '-q', '-D', 'starpilot_phone', '-t', 'raw', '-f', 'S16_LE',
                           '-r', '48000', '-c', '2', '--buffer-time=200000', '--period-time=20000'],
                          env={**os.environ, 'ALSA_CONFIG_PATH': str(config)}, stdout=subprocess.PIPE) as capture:
      try:
        mute_until = 0.0
        while data := capture.stdout.read(3840):
          now = time.monotonic()
          if len(data) % 4:
            raise RuntimeError('Incomplete phone audio frame')
          if FlushRequest.kind:
            mute_until, FlushRequest.kind = now + mute_seconds(FlushRequest.kind), None
            writer.flush()
          if now < mute_until:
            continue
          writer.write(data)
      finally:
        capture.terminate()
        try:
          capture.wait(timeout=3)
        except subprocess.TimeoutExpired:
          capture.kill()
          capture.wait(timeout=3)
      if capture.returncode != 0:
        raise RuntimeError('Phone audio disconnected; reconnecting')
  finally:
    writer.close()


if __name__ == '__main__':
  # Handlers go in before anything else: an early flush signal must never hit the default (terminate) action.
  signal.signal(signal.SIGUSR1, lambda *_: request_flush('pause'))
  signal.signal(signal.SIGUSR2, lambda *_: request_flush('skip'))
  # Exit after an abrupt supervisor death too, so stale phone audio cannot overlap a new source.
  parent = int(os.environ['STARPILOT_AUDIO_PARENT_PID'])
  libc = C.CDLL(None, use_errno=True)
  if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent:
    raise SystemExit('Music supervisor stopped')

  def stopped(*_):
    raise SystemExit(0)
  signal.signal(signal.SIGTERM, stopped)
  relay(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
