"""spotifyd: owns the private audio server, the Soloist player and its API connection.

Started by the manager only while Spotify is enabled. Music is never driving-critical: every failure
here stays inside this process group and shows up as a status, never as a driving alert.
"""
from __future__ import annotations

import collections
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from openpilot.starpilot.soloist import backend, bluealsa_runtime, bugsink_report, credentials, package, preferences, phone_audio
from openpilot.starpilot.soloist import now_playing as now_playing_module
from openpilot.starpilot.soloist.client import PlayerClient
from openpilot.starpilot.soloist.paths import CONTROL_SOCKET, Layout, private_dir

A2DP_SINK = '0000110b-0000-1000-8000-00805f9b34fb'
EXIT_EXPIRED = 10
STOP_SECONDS = 5.0
OUTPUT_RETRY_SECONDS = 20.0
PAIRING_PROFILE_SECONDS = 180.0  # the pairing window plus time for the phone to save the comma's services
MAX_FAST_FAILURES = 5
UNRESPONSIVE_SECONDS = 120.0
PLAYING_GRACE_SECONDS = 600.0
LOG_LINES = 200
LOGIN_RETRY_MAX_SECONDS = 300.0
LOGIN_REJECTED_AFTER = 3


def network_ready() -> bool:
  """Wait for an IPv4 or IPv6 default route; this does not prove internet access."""
  for filename, destination, flags, interface in (('route', 1, 3, 0), ('ipv6_route', 0, 8, 9)):
    try:
      with open(f'/proc/net/{filename}') as routes:
        for line in routes:
          fields = line.split()
          if len(fields) <= max(destination, flags, interface):
            continue
          if fields[destination] != ('00000000' if filename == 'route' else '0' * 32):
            continue
          if filename == 'ipv6_route' and fields[1] != '00':
            continue
          route_flags = int(fields[flags], 16)
          if fields[interface] != 'lo' and route_flags & 1 and not route_flags & 0x200:
            return True
    except (OSError, ValueError):
      continue
  return False


EXPIRES_IN = re.compile(r'client expires in (\d+) days?')
# Soloist logs this when restoring the saved account fails (e.g. at boot, before the network really works).
# It never retries by itself; first-time pairing has no saved account, so it never logs this.
LOGIN_FAILED = re.compile(r'\blogin failed\b')


def _kill_group(process: subprocess.Popen | None) -> None:
  if process is None or process.poll() is not None:
    return
  try:
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=STOP_SECONDS)
  except (ProcessLookupError, PermissionError):
    pass
  except subprocess.TimeoutExpired:
    try:
      os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
      pass
    process.wait(timeout=STOP_SECONDS)


def reap_orphans(layout: Layout, proc: Path = Path('/proc')) -> int:
  """Kill leftover players/audio servers from a supervisor that was killed before it could stop them."""
  root = os.path.realpath(layout.root) + os.sep
  killed = 0
  for entry in proc.glob('[0-9]*'):
    try:
      exe = os.path.realpath(entry / 'exe')
    except OSError:
      continue
    if exe.startswith(root):
      try:
        os.kill(int(entry.name), signal.SIGKILL)
        killed += 1
      except (ProcessLookupError, PermissionError, ValueError):
        pass
  return killed


class BluetoothOutput:
  """Readiness of the one selected speaker. Connects only its A2DP profile, never pairs or switches devices."""

  def __init__(self):
    self.client = None

  def ready(self, address: str) -> bool:
    try:
      from openpilot.starpilot.bluetooth.owner import BlueZ, DEVICE
      if self.client is None:
        self.client = BlueZ()
      for path, interfaces in self.client._objects().items():
        props = interfaces.get(DEVICE)
        if props and str(props.get('Address', '')).upper() == address:
          if props.get('Connected') is True:
            return True
          if props.get('Paired') is True:
            self.client._call(path, DEVICE, 'ConnectProfile', 's', (A2DP_SINK,), timeout=15.0)
          return False
    except Exception:
      self._reset()
    return False

  def _reset(self) -> None:
    client, self.client = self.client, None
    if client is not None:
      try:
        client.close()
      except Exception:
        pass


class Supervisor:
  def __init__(self, layout: Layout | None = None, *, output: BluetoothOutput | None = None,
               spawn: Callable[..., subprocess.Popen] = subprocess.Popen, clock: Callable[[], float] = time.monotonic,
               make_client: Callable[[Path], PlayerClient] | None = None,
               network: Callable[[], bool] = network_ready):
    self.layout = layout or Layout.default()
    self.output = output or BluetoothOutput()
    self.spawn, self.clock, self.network = spawn, clock, network
    self.make_client = make_client or (lambda data: PlayerClient(data))
    self.server: subprocess.Popen | None = None
    self.player: subprocess.Popen | None = None
    self.client: PlayerClient | None = None
    self.launched: tuple | None = None
    self.release_target: str | None = None
    self.lock = threading.Lock()
    # Separate so child output never waits on a tick that is waiting for that child.
    self.log_lock = threading.Lock()
    self.log: collections.deque[str] = collections.deque(maxlen=LOG_LINES)
    self.state, self.message = 'starting', ''
    self.fast_failures, self.retry_at, self.started_at = 0, 0.0, 0.0
    self.expired_target: str | None = None
    self.days_left: int | None = None
    self.secret = ''
    self.phone = phone_audio.PhoneInput()
    self.phone_prefs = None
    self.phone_track = None
    self.phone_profile = False
    self.pairing_profile_until = 0.0
    self.output_available = True
    self.output_check_at = 0.0
    self.output_lock = threading.Lock()
    self.output_seen: tuple[int, bool] | None = None
    self.output_generation = 0
    self.output_checking = False
    self.output_flips = 0
    self.waiting_for = ''
    self.run_async: Callable[[Callable[[], None]], None] = lambda fn: threading.Thread(
      target=fn, name='spotify_output_check', daemon=True).start()
    self.unresponsive_since: float | None = None
    self.authenticated_once = False
    self.login_failed = False
    self.login_failures = 0
    self.answered = False
    self.last_playing = False

  # ---- status ---------------------------------------------------------------------------------------
  def _set(self, state: str, message: str = '') -> None:
    if (state, message) != (self.state, self.message):
      self.state, self.message = state, message
      self.publish()

  def status(self) -> dict:
    player = self.client.snapshot() if self.client is not None else None
    state = self.state
    if state == 'running' and player is not None:
      state = ('connecting' if not player['connected'] else 'waiting' if not player['loggedIn'] else
               'playing' if player['status'] == 'playing' and player['active'] else 'ready')
    now_playing = (now_playing_module.from_phone(self.phone_track) if self.phone_prefs is not None and self.player is not None
                   else now_playing_module.from_spotify(player))
    with self.log_lock:
      log = list(self.log)[-20:]
    return {'state': state, 'message': self.message, 'daysLeft': self.days_left, 'player': player,
            'nowPlaying': now_playing, 'outputWaiting': self.waiting_for if self.player is not None else '', 'log': log}

  def publish(self) -> None:
    try:
      private_dir(self.layout.runtime)
      temporary = self.layout.status.with_suffix('.tmp')
      temporary.write_text(json.dumps(self.status()))
      os.replace(temporary, self.layout.status)
    except OSError:
      pass

  # ---- children -------------------------------------------------------------------------------------
  def _record(self, stream, label: str) -> None:
    for raw in iter(stream.readline, b''):
      line = raw.decode('utf-8', 'replace').rstrip()
      line = re.sub(r'\x1b\[[0-9;]*m', '', line)
      if self.secret:
        line = line.replace(self.secret, '[api key]')
      with self.log_lock:
        self.log.append(f'{label}: {line[:300]}')
      match = EXPIRES_IN.search(line)
      if match:
        self.days_left = int(match.group(1))
      if label == 'soloist' and LOGIN_FAILED.search(line):
        self.login_failed = True

  def _start(self, launch: backend.Launch, label: str) -> subprocess.Popen:
    process = self.spawn(launch.argv, env=launch.env, cwd=str(self.layout.runtime), stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    threading.Thread(target=self._record, args=(process.stdout, label), name=f'spotify_{label}_log', daemon=True).start()
    return process

  def stop_children(self, *, release_phone: bool = True) -> None:
    self.unresponsive_since = None
    self.login_failed = False
    self.output_generation += 1
    self.output_seen, self.output_flips, self.waiting_for = None, 0, ''
    if self.client is not None:
      self.client.stop()
      self.client = None
    _kill_group(self.player)
    _kill_group(self.server)
    self.player = self.server = None
    self.launched = None
    if release_phone:
      self.phone.close()
      self.phone_prefs = None
      if self.clock() >= self.pairing_profile_until:
        self._release_profile()

  def _output_ready(self, address: str) -> bool:
    with self.output_lock:
      return self.output.ready(address)

  def _output_changed(self, now: float, address: str) -> bool:
    seen, self.output_seen = self.output_seen, None
    if seen is not None and seen[0] == self.output_generation:
      self.output_flips = self.output_flips + 1 if seen[1] != self.output_available else 0
      if self.output_flips >= 2:
        return True
    if now >= self.output_check_at and not self.output_checking:
      self.output_check_at = now + OUTPUT_RETRY_SECONDS
      self.output_checking = True
      generation = self.output_generation

      def check() -> None:
        try:
          self.output_seen = (generation, self._output_ready(address))
        finally:
          self.output_checking = False
      self.run_async(check)
    return False

  def _switch_output(self, prefs: preferences.Preferences) -> bool:
    """Move audio to or from the speaker inside the running server, so Soloist keeps its session."""
    speaker = not self.output_available
    if not backend.can_switch():
      return False
    try:
      backend.switch_output(self.layout, speaker)
    except (OSError, subprocess.SubprocessError) as error:
      with self.log_lock:
        self.log.append(f'audio: could not switch output: {error}')
      return False
    self.output_available, self.output_flips = speaker, 0
    self.waiting_for = '' if speaker else prefs.output_name or 'your Bluetooth speaker'
    self.publish()
    return True

  def _release_profile(self) -> None:
    if self.phone_profile:
      phone_audio.profile('release')
      self.phone_profile = False

  def tick_phone(self, prefs: preferences.Preferences) -> None:
    now = self.clock()
    if prefs != self.phone_prefs:
      self.stop_children()
      self.phone_prefs = prefs
      self.retry_at = 0
    if not prefs.phone_address:
      self._set('no_phone', 'Pair your phone in the companion app or on the Bluetooth page, then choose it as the music source.')
      return
    if prefs.output == 'bluetooth' and not prefs.output_address:
      self._set('no_output', 'Choose where music plays.')
      return
    if now < self.retry_at:
      return
    try:
      if not backend.bundle_ready():
        raise RuntimeError('This build is missing the music audio component.')
      if not self.phone.powered():
        self.stop_children(release_phone=False)
        self._set('waiting_bluetooth', 'Turn on Bluetooth from the Bluetooth page to receive phone music.')
        self.retry_at = now + 5
        return
      if not self.phone_profile:
        phone_audio.profile('acquire')
        self.phone_profile = True
      if not self.phone.ready(prefs.phone_address, now):
        self.stop_children(release_phone=False)
        self._set('waiting_phone', f'Waiting for {prefs.phone_name or "your phone"}. Select the comma as its Bluetooth audio output.')
        self.retry_at = now + 5
        return
      if prefs.output == 'bluetooth' and not self._output_ready(prefs.output_address):
        self.stop_children(release_phone=False)
        self._set('waiting_output', f'Waiting for {prefs.output_name or "your speaker"} to connect.')
        self.retry_at = now + OUTPUT_RETRY_SECONDS
        return
      if self.player is not None:
        if self.player.poll() is None and self.server is not None and self.server.poll() is None:
          self.phone_track = self.phone.track(prefs.phone_address)
          return
        raise RuntimeError('Phone audio disconnected. Reconnecting…')
      self.server = self._start(backend.server_launch(self.layout, prefs), 'audio')
      socket_path = backend.runtime_paths(self.layout)['socket']
      deadline = time.monotonic() + 5
      while not socket_path.exists() and self.server.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
      if not socket_path.exists():
        raise RuntimeError('Audio output could not open')
      env = {**os.environ, 'PULSE_SERVER': f'unix:{socket_path}', 'STARPILOT_AUDIO_PARENT_PID': str(os.getpid()),
             'STARPILOT_SOLOIST_CONFIG': str(self.layout.config)}
      argv = [sys.executable, '-m', 'openpilot.starpilot.soloist.phone_audio', prefs.phone_address,
              env['PULSE_SERVER'], str(self.layout.runtime / 'phone-alsa.conf')]
      # Resolve this checkout explicitly; the worker runs in the private runtime directory.
      env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3])
      self.player = self._start(backend.Launch(argv, env), 'phone')
      self._set('phone_ready', 'Phone audio connected. Play music on your phone; use the phone and car volume controls.')
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
      self.stop_children(release_phone=False)
      self.phone.close()
      self.retry_at = now + OUTPUT_RETRY_SECONDS
      self._set('phone_retrying', str(error))

  # ---- policy ---------------------------------------------------------------------------------------
  def desired(self) -> tuple[str, str, tuple | None]:
    """Return (state, message, launch key). The key changes whenever the player must restart."""
    release = package.release_status(self.layout)
    if not release['installed']:
      return 'missing_package', 'Upload Spotify Soloist to finish setup.', None
    target = os.path.realpath(self.layout.current)
    if release.get('expired') or self.expired_target == target:
      return 'expired', 'This Soloist build expired. Download the newest one from Spotify and upload it.', None
    key = credentials.load(self.layout)
    if key is None:
      return 'missing_key', 'Add your Soloist API key.', None
    prefs = preferences.load(self.layout)
    if prefs.output == 'bluetooth' and not prefs.output_address:
      return 'no_output', 'Choose where music plays.', None
    if not backend.bundle_ready():
      return 'audio_unavailable', 'This build is missing the Spotify audio component.', None
    return 'running', '', (target, key, prefs)

  def tick(self) -> None:
    prefs = preferences.load(self.layout)
    if prefs.source == 'phone':
      self.tick_phone(prefs)
      return
    if self.phone_prefs is not None:
      self.stop_children()
      self.retry_at = 0
    elif self.phone_profile and (prefs.output != 'bluetooth' or self.launched is None) and self.clock() >= self.pairing_profile_until:
      self._release_profile()
    state, message, launch_key = self.desired()
    now = self.clock()
    if launch_key != self.launched:
      self.stop_children()
    if launch_key is None:
      self._set(state, message)
      return
    target, key, prefs = launch_key
    if self.release_target != target:
      self.release_target, self.fast_failures, self.retry_at = target, 0, 0.0
    if self.player is not None:
      if prefs.output == 'bluetooth' and self._output_changed(now, prefs.output_address):
        if not self._switch_output(prefs):
          self.stop_children()
          self.retry_at = 0.0
          return
      self._watch(now, target)
      return
    if now < self.retry_at:
      return
    if not self.network():
      self._set('waiting_network', 'Waiting for the comma’s network…')
      return
    if prefs.output == 'bluetooth' and not self.phone_profile:
      try:
        phone_audio.profile('acquire')
        self.phone_profile = True
      except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        self.retry_at = now + OUTPUT_RETRY_SECONDS
        self._set('audio_unavailable', str(error))
        return
    self.output_available = prefs.output != 'bluetooth' or self._output_ready(prefs.output_address)
    self.output_check_at = now + OUTPUT_RETRY_SECONDS
    self.waiting_for = '' if self.output_available else prefs.output_name or 'your Bluetooth speaker'
    # A private null sink keeps Connect available until the speaker returns; never the warning speaker.
    self.secret = key
    self._set('starting', 'Starting Spotify…')
    try:
      self.server = self._start(backend.server_launch(self.layout, prefs, silent=not self.output_available), 'audio')
      deadline = now + 5.0
      socket_path = backend.runtime_paths(self.layout)['socket']
      while not socket_path.exists() and self.server.poll() is None and self.clock() < deadline:
        time.sleep(0.1)
      if not socket_path.exists():
        raise RuntimeError('audio output could not open')
      self.player = self._start(backend.player_launch(self.layout, prefs, key), 'soloist')
    except (OSError, RuntimeError, backend.BackendUnavailable) as error:
      self.stop_children()
      self._fail(now, f'Could not start Spotify: {error}')
      return
    self.launched, self.started_at, self.answered, self.last_playing = launch_key, now, False, False
    self.client = self.make_client(self.layout.state)
    self.client.on_change = self.publish
    self.client.start()
    self._set('running')

  def _watch(self, now: float, target: str) -> None:
    code = self.player.poll() if self.player is not None else None
    server_dead = self.server is not None and self.server.poll() is not None
    if code is None and not server_dead:
      snapshot = self.client.snapshot() if self.client is not None else {}
      connected = bool(snapshot.get('connected'))
      self.answered = self.answered or connected
      if snapshot.get('loggedIn'):
        self.authenticated_once = True
        self.login_failures = 0
        self.last_playing = bool(snapshot.get('active') and snapshot.get('status') in ('playing', 'buffering'))
      elif self.login_failed:
        self._retry_login(now)
        return
      lost_login = connected and self.authenticated_once and not snapshot.get('loggedIn')
      # Logout clears the playback state, so cached music from before a coverage gap gets longer.
      limit = PLAYING_GRACE_SECONDS if lost_login and self.last_playing else UNRESPONSIVE_SECONDS
      if self.client is not None and (not connected or lost_login):
        if self.unresponsive_since is None:
          self.unresponsive_since = now
        elif now - self.unresponsive_since >= limit:
          self.stop_children()
          if not self.answered:
            self._fail(now, 'Spotify didn’t respond. Retrying…')
            return
          self.retry_at = now + 10
          self._set('retrying', 'Reconnecting Spotify with your saved account…')
          return
      else:
        self.unresponsive_since = None
      if self.answered and now - self.started_at > 60:
        self.fast_failures = 0
      return
    self.stop_children()
    if code == EXIT_EXPIRED:
      self.expired_target = target
      self._set('expired', 'This Soloist build expired. Download the newest one from Spotify and upload it.')
      return
    detail = 'audio output closed' if server_dead and code is None else f'player exited ({code})'
    self._fail(now, f'Spotify stopped: {detail}. Retrying…')

  def _retry_login(self, now: float) -> None:
    self.stop_children()
    self.login_failures += 1
    delay = min(LOGIN_RETRY_MAX_SECONDS, 5.0 * 2 ** (self.login_failures - 1)) * random.uniform(0.8, 1.2)
    self.retry_at = now + delay
    rejected = self.login_failures >= LOGIN_REJECTED_AFTER
    self._set('retrying', 'Spotify keeps refusing the sign-in. Check your API key or network connection. Trying again…' if rejected
              else 'Couldn’t sign in to Spotify yet. Trying again…')

  def _fail(self, now: float, message: str) -> None:
    self.fast_failures += 1
    if self.fast_failures >= MAX_FAST_FAILURES and package.rollback(self.layout):
      self.fast_failures = 0
      message = 'The new Soloist build would not start; went back to the previous one.'
    elif self.fast_failures >= MAX_FAST_FAILURES:
      self.retry_at = now + 300
      self._set('failed', message.replace('Retrying…', 'Will retry in a few minutes.'))
      return
    self.retry_at = now + min(60.0, 2.0 ** self.fast_failures) * random.uniform(0.8, 1.2)
    self._set('retrying', message)

  def _flush_phone_audio(self, sent: str | None) -> None:
    """Drop the audio already queued for the car so pause and skip take effect now, not a buffer later."""
    if sent in phone_audio.FLUSH_ACTIONS and self.player is not None and self.player.poll() is None:
      try:
        os.kill(self.player.pid, phone_audio.FLUSH_ACTIONS[sent][0])
      except (ProcessLookupError, PermissionError):
        pass

  # ---- control --------------------------------------------------------------------------------------
  def handle(self, request: dict) -> dict:
    command = request.get('command')
    if command == 'status':
      return {'ok': True, 'status': self.status()}
    if command == 'control':
      phone_prefs = self.phone_prefs
      if phone_prefs is not None:
        action = request.get('action')
        if action not in (*phone_audio.PLAYER_METHODS, 'play_pause') or request.get('args', {}) != {}:
          return {'ok': False, 'error': 'Use your phone for playback and volume controls'}
        try:
          sent = self.phone.control(phone_prefs.phone_address, action)
          self._flush_phone_audio(sent)
          return {'ok': True}
        except (OSError, RuntimeError):
          return {'ok': False, 'error': 'Phone playback control is unavailable'}
      client = self.client
      if client is None:
        return {'ok': False, 'error': 'Spotify isn’t running'}
      args = request.get('args', {})
      if not isinstance(args, dict):
        return {'ok': False, 'error': 'Invalid control'}
      action = str(request.get('action'))
      if action == 'play_pause':
        action = 'pause' if client.snapshot().get('status') == 'playing' else 'play'
      client.send(action, args)
      return {'ok': True}
    if command == 'prepare_phone_pairing':
      # A phone only offers the comma as an audio output if the receiving profile is advertised when it pairs.
      with self.lock:
        try:
          if not self.phone_profile:
            phone_audio.profile('acquire')
            self.phone_profile = True
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
          return {'ok': False, 'error': str(error)}
        self.pairing_profile_until = self.clock() + PAIRING_PROFILE_SECONDS
      return {'ok': True}
    if command == 'reset_session':
      with self.lock:
        self.stop_children()
        self.authenticated_once = False
        self.login_failures = 0
        shutil.rmtree(self.layout.state, ignore_errors=True)
        shutil.rmtree(self.layout.cache, ignore_errors=True)
      return {'ok': True}
    if command == 'clear_cache':
      with self.lock:
        self.stop_children()
        shutil.rmtree(self.layout.cache, ignore_errors=True)
      return {'ok': True}
    return {'ok': False, 'error': 'Unknown command'}


def serve(supervisor: Supervisor, path: str = CONTROL_SOCKET, stop: threading.Event | None = None) -> None:
  try:
    os.unlink(path)
  except FileNotFoundError:
    pass
  server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
  server.bind(path)
  os.chmod(path, 0o600)
  server.listen(4)
  server.settimeout(0.5)
  while stop is None or not stop.is_set():
    try:
      connection, _ = server.accept()
    except TimeoutError:
      continue
    with connection:
      connection.settimeout(2.0)
      try:
        data = bytearray()
        while not data.endswith(b'\n') and len(data) < 8192:
          chunk = connection.recv(4096)
          if not chunk:
            break
          data.extend(chunk)
        request = json.loads(data)
        result = supervisor.handle(request) if isinstance(request, dict) else {'ok': False, 'error': 'Invalid request'}
      except (ValueError, OSError, RuntimeError) as error:
        result = {'ok': False, 'error': str(error) or 'Invalid request'}
      try:
        connection.sendall(json.dumps(result).encode() + b'\n')
      except OSError:
        pass


def parked() -> bool:
  try:
    from openpilot.common.params import Params
    return Params().get_bool('IsOffroad')
  except Exception:
    return False


def main() -> None:
  os.nice(10)  # music yields to everything driving-related; children inherit this
  supervisor = Supervisor()
  reap_orphans(supervisor.layout)
  legacy_profile = Path('/run/systemd/system/bluealsa.service.d/90-starpilot-phone.conf')
  if legacy_profile.exists() or bluealsa_runtime.paths(legacy_profile)[0].exists():
    phone_audio.profile('release')
  stop = threading.Event()

  def shutdown(*_):
    stop.set()
  signal.signal(signal.SIGTERM, shutdown)
  signal.signal(signal.SIGINT, shutdown)
  threading.Thread(target=serve, args=(supervisor, CONTROL_SOCKET, stop), name='spotify_control', daemon=True).start()
  reporter = bugsink_report.Reporter(parked=parked, output=lambda: preferences.load(supervisor.layout).output).start()
  try:
    while not stop.is_set():
      with supervisor.lock:
        try:
          supervisor.tick()
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
          supervisor._set('failed', str(error))
      supervisor.publish()
      stop.wait(1.0)
  finally:
    reporter.close()
    try:
      supervisor.pairing_profile_until = 0.0
      supervisor.stop_children()
    finally:
      try:
        os.unlink(CONTROL_SOCKET)
      except FileNotFoundError:
        pass


if __name__ == '__main__':
  main()
