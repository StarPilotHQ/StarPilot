"""A private PulseAudio server that carries Soloist's audio to one selected output.

AGNOS 20 ships no PulseAudio at all, and Soloist only plays through PulseAudio or PipeWire. The Ubuntu
24.04 server and client libraries (16.1+dfsg1-2ubuntu10.1) are vendored in ./pulseaudio; see its README
for provenance and licenses.

The server never becomes the system's audio: it runs with no default script, owns one sink, and is
reachable only through a socket in this feature's private run directory. The global ALSA default,
soundd and the warning speaker are untouched. Outputs:

- bluetooth: the comma's existing BlueALSA A2DP source, through a process-local ALSA configuration.
  That approach is adapted from firestar5683's RoadScore Bluetooth output
  (firestar5683/StarPilot@249eb88b3a397db847e4a595d7e23b7e85d329e5, roadscore/prototype/bluetooth_output.py).
- starport: a real-time pipe of 48 kHz stereo PCM that starportd sends on the car's media channel.
- test: a null sink, to check Spotify without a speaker.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from openpilot.starpilot.soloist import bluealsa_runtime
from openpilot.starpilot.soloist.paths import Layout, private_dir
from openpilot.starpilot.soloist.preferences import ADDRESS, Preferences

SERVER_ROOT = Path(__file__).resolve().parent / 'pulseaudio'
PCM_NAME = 'starpilot_spotify'
SINK_NAME = 'spotify'
SILENT_SINK = 'spotify_silent'
SYSTEM_ALSA_CONFIG = Path('/usr/share/alsa/alsa.conf')
# The Android Auto media channel format every head unit must offer; the pipe runs on the system clock,
# so it produces exactly real-time audio (silence when paused) whether or not anyone reads it.
PIPE_RATE, PIPE_CHANNELS, PIPE_BYTES_PER_FRAME = 48000, 2, 4


class BackendUnavailable(RuntimeError):
  pass


def bundle_ready(root: Path = SERVER_ROOT) -> bool:
  return os.access(root / 'bin' / 'pulseaudio', os.X_OK) and (root / 'modules').is_dir()


def library_path(root: Path = SERVER_ROOT, inherited: str = '') -> str:
  """AGNOS 20 ships no libpulse0, so the server, pactl, Soloist and the phone relay all load it from the bundle."""
  return ':'.join(filter(None, (str(root / 'lib'), inherited)))


def can_switch(root: Path = SERVER_ROOT) -> bool:
  return os.access(root / 'bin' / 'pactl', os.X_OK)


def alsa_config(address: str, base: Path = SYSTEM_ALSA_CONFIG) -> str:
  """Process-local ALSA config: the system configuration plus one named BlueALSA A2DP PCM."""
  if not ADDRESS.fullmatch(address):
    raise BackendUnavailable('Choose a Bluetooth speaker or car first')
  if any(character in str(base) for character in '\n\r<>"'):
    raise BackendUnavailable('Invalid system ALSA configuration path')
  return (f'<{base}>\n' + bluealsa_runtime.alsa_plugins() +
          f'\npcm.{PCM_NAME} {{\n  type bluealsa\n  device "{address}"\n  profile "a2dp"\n  volume "100+"\n  softvol "true"\n' +
          '  hint { show on description "StarPilot Spotify output" }\n}\n')


@dataclass(frozen=True)
class Launch:
  argv: list[str]
  env: dict[str, str]


def runtime_paths(layout: Layout) -> dict[str, Path]:
  run = layout.runtime
  return {'run': run, 'home': run / 'home', 'pulse': run / 'pulse', 'socket': run / 'pulse' / 'native', 'alsa': run / 'alsa.conf',
          'starport': run / 'starport.pcm', 'daemon': run / 'daemon.conf'}


def starport_pipe(layout: Layout | None = None) -> Path:
  return runtime_paths(layout or Layout.default())['starport']


def _sink(output: str, paths: dict[str, Path]) -> tuple[str, str]:
  if output == 'bluetooth':
    return 'module-alsa-sink', f'device={PCM_NAME} sink_name={SINK_NAME} channels=2 tsched=0'
  if output == 'starport':
    return 'module-pipe-sink', (f'file={paths["starport"]} sink_name={SINK_NAME} format=s16le rate={PIPE_RATE} ' +
                                f'channels={PIPE_CHANNELS} use_system_clock_for_timing=yes')
  return 'module-null-sink', f'sink_name={SILENT_SINK if output == "silent" else SINK_NAME}'


def server_launch(layout: Layout, prefs: Preferences, *, silent: bool = False, alsa_base: Path = SYSTEM_ALSA_CONFIG,
                  root: Path = SERVER_ROOT) -> Launch:
  paths = runtime_paths(layout)
  for key in ('run', 'home', 'pulse'):
    private_dir(paths[key])
  paths['socket'].unlink(missing_ok=True)
  env = {
    'HOME': str(paths['home']), 'XDG_RUNTIME_DIR': str(paths['run']), 'XDG_CONFIG_HOME': str(paths['home']),
    'PULSE_RUNTIME_PATH': str(paths['pulse']), 'PULSE_STATE_PATH': str(paths['pulse']),
    'LD_LIBRARY_PATH': library_path(root), 'PATH': '/usr/bin:/bin', 'PULSE_CONFIG': str(paths['daemon']),
  }
  paths['daemon'].write_text('nice-level = -5\n')
  if prefs.output == 'bluetooth':
    # Written even when starting silent, so the speaker can be added to the running server later.
    paths['alsa'].write_text(alsa_config(prefs.output_address, alsa_base))
    os.chmod(paths['alsa'], 0o600)
    env['ALSA_CONFIG_PATH'] = str(paths['alsa'])
  elif prefs.output == 'starport':
    paths['starport'].unlink(missing_ok=True)
  sink = ' '.join(_sink('silent' if silent else prefs.output, paths))
  argv = [str(root / 'bin' / 'pulseaudio'), '--daemonize=no', '--system=no', '--use-pid-file=no', '-n',
          '--exit-idle-time=-1', '--realtime=no', '--high-priority=yes', '--disallow-exit', '--log-target=stderr',
          '--log-level=notice', '--resample-method=speex-float-3', f'--dl-search-path={root / "modules"}',
          '-L', f'module-native-protocol-unix socket={paths["socket"]} auth-anonymous=1', '-L', sink]
  return Launch(argv, env)


def player_launch(layout: Layout, prefs: Preferences, api_key: str, *, root: Path = SERVER_ROOT) -> Launch:
  paths = runtime_paths(layout)
  private_dir(layout.state)
  private_dir(layout.cache)
  # The key must be an argument: Soloist documents no file, fd or environment alternative.
  argv = [str(layout.current / 'soloist'), '--device-name', prefs.device_name, '--api-key', api_key,
          '--data-dir', str(layout.state), '--cache-dir', str(layout.cache), '--cache-size', str(prefs.cache_mb),
          '--initial-volume', str(prefs.initial_volume), '--ws', '127.0.0.1:0']
  env = {'HOME': str(paths['home']), 'XDG_RUNTIME_DIR': str(paths['run']), 'PATH': '/usr/bin:/bin',
         'PULSE_SERVER': f'unix:{paths["socket"]}', 'LD_LIBRARY_PATH': library_path(root)}
  return Launch(argv, env)


def _pactl(layout: Layout, root: Path, run: Callable[..., subprocess.CompletedProcess], *args: str) -> str:
  paths = runtime_paths(layout)
  env = {'HOME': str(paths['home']), 'XDG_RUNTIME_DIR': str(paths['run']), 'PATH': '/usr/bin:/bin',
         'PULSE_SERVER': f'unix:{paths["socket"]}', 'LD_LIBRARY_PATH': library_path(root)}
  return run([str(root / 'bin' / 'pactl'), *args], env=env, capture_output=True, text=True, timeout=5, check=True).stdout


def switch_output(layout: Layout, speaker: bool, *, root: Path = SERVER_ROOT,
                  run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
  """Move Soloist between the speaker and the silent sink inside the running server, without restarting it."""
  new, old = (SINK_NAME, SILENT_SINK) if speaker else (SILENT_SINK, SINK_NAME)
  module, args = _sink('bluetooth' if speaker else 'silent', runtime_paths(layout))

  def pactl(*args: str) -> str:
    return _pactl(layout, root, run, *args)
  pactl('load-module', module, *args.split())
  pactl('set-default-sink', new)
  for line in pactl('list', 'short', 'sink-inputs').splitlines():
    if line.strip():
      pactl('move-sink-input', line.split()[0], new)
  for line in pactl('list', 'short', 'modules').splitlines():
    if f'sink_name={old}' in line.split():
      pactl('unload-module', line.split()[0])
