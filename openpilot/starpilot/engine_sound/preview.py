"""Parked engine preview: revs the selected engine once.

Offroad it plays here, on the car output when the music server has one open, otherwise on the comma speaker.
Onroad the running enginesoundd owns the output, so it is asked to play the same rev script instead.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from openpilot.starpilot.engine_sound import settings
from openpilot.starpilot.engine_sound.daemon import BLOCK_SECONDS, BUFFER, PREVIEW_CORES, Engine, background
from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE

MODULE = 'openpilot.starpilot.engine_sound.preview'
ROOT = Path(__file__).resolve().parents[3]
EXIT_CAR, EXIT_REFUSED, EXIT_COMMA, EXIT_HANDED_OFF = 0, 2, 3, 4
TAKEOVER_SECONDS = 1.5
CHECK_BLOCKS = 25


def trigger_path() -> Path:
  return Path(tempfile.gettempdir()) / f'starpilot-engine-preview-{os.getuid()}'


def lock_path() -> Path:
  return Path(tempfile.gettempdir()) / f'starpilot-engine-preview-{os.getuid()}.lock'


def rev_script(dt: float = BLOCK_SECONDS) -> list[tuple[float, float, float, bool]]:
  """(pedal, speed, accel, in_gear) in Park: idle, two blips, a pull into the limiter, back to idle."""
  def hold(pedal: float, seconds: float):
    return [(pedal, 0.0, 0.0, False)] * round(seconds / dt)
  ramp = [(min(i * dt / 0.8, 1.0), 0.0, 0.0, False) for i in range(round(0.8 / dt))]
  return hold(0.0, 0.5) + hold(0.65, 0.2) + hold(0.0, 0.55) + hold(0.8, 0.25) + hold(0.0, 0.8) + ramp + hold(1.0, 0.7) + hold(0.0, 1.6)


def render(profile_name: str, volume: float) -> list[bytes]:
  engine = Engine(profile_name)
  return [engine.block(pedal, speed, accel, in_gear, volume) for pedal, speed, accel, in_gear in rev_script()]


def _car_writer():
  from openpilot.starpilot.soloist import backend, preferences
  from openpilot.starpilot.soloist.paths import Layout
  from openpilot.starpilot.soloist.pulse_client import PulseWriter
  layout = Layout.default()
  if preferences.load(layout).output == 'test':
    return None
  socket = backend.runtime_paths(layout)['socket']
  if not socket.exists():
    return None
  try:
    # Named sink on purpose: while the speaker is away the server only has the silent sink, and this fails over to the comma.
    return PulseWriter(f'unix:{socket}', client='StarPilot engine preview', stream='Engine preview', sink=backend.SINK_NAME,
                       channels=1, buffer=BUFFER)
  except (OSError, RuntimeError):
    return None


def play_car(writer, blocks: list[bytes], still_allowed) -> bool:
  try:
    for index, block in enumerate(blocks):
      if index % CHECK_BLOCKS == 0 and not still_allowed():
        return False
      writer.write(block)
    writer.drain()
    return True
  finally:
    writer.close()


def play_comma(blocks: list[bytes], still_allowed, audio=None) -> bool:
  import numpy as np
  if audio is None:
    import sounddevice as audio
    from openpilot.system import micd
    micd.patch_sounddevice(audio)
  with audio.OutputStream(channels=1, samplerate=SAMPLE_RATE, dtype='int16') as stream:
    for index, block in enumerate(blocks):
      if index % CHECK_BLOCKS == 0 and not still_allowed():
        return False
      stream.write(np.frombuffer(block, dtype='<i2'))
  return True


def _is_preview(pid: int) -> bool:
  try:
    return MODULE.encode() in Path(f'/proc/{pid}/cmdline').read_bytes()
  except OSError:
    return False


def take_over(path: Path, clock=time.monotonic, sleep=time.sleep) -> int | None:
  """Hold the preview lock, stopping an older preview first so the newest choice is the one heard."""
  import fcntl
  descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
  deadline = clock() + TAKEOVER_SECONDS
  signalled = False
  while True:
    try:
      fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
      break
    except BlockingIOError:
      if not signalled:
        signalled = True
        try:
          pid = int(os.pread(descriptor, 16, 0).decode().strip() or 0)
          if pid > 0 and pid != os.getpid() and _is_preview(pid):
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
          pass
      if clock() >= deadline:
        os.close(descriptor)
        return None
      sleep(0.05)
  os.ftruncate(descriptor, 0)
  os.pwrite(descriptor, str(os.getpid()).encode(), 0)
  return descriptor


def main() -> int:
  from openpilot.common.params import Params
  from openpilot.starpilot.galaxy.settings import LiveContextSource
  params = Params()
  context = LiveContextSource(params)
  descriptor = None
  try:
    if not context.offroad():
      if not settings.enabled(params):
        return EXIT_REFUSED
      trigger_path().touch()
      return EXIT_HANDED_OFF
    descriptor = take_over(lock_path())
    if descriptor is None:
      return EXIT_REFUSED
    blocks = render(settings.profile_name(params), settings.volume(params))
    writer = _car_writer()
    if writer is not None:
      return EXIT_CAR if play_car(writer, blocks, context.offroad) else EXIT_REFUSED
    if params.get_bool('IsDriverViewEnabled'):
      return EXIT_REFUSED
    return EXIT_COMMA if play_comma(blocks, lambda: context.offroad() and not params.get_bool('IsDriverViewEnabled')) else EXIT_REFUSED
  finally:
    context.close()
    if descriptor is not None:
      os.close(descriptor)


def environment() -> dict[str, str]:
  from openpilot.starpilot.soloist import backend
  return {**os.environ, 'LD_LIBRARY_PATH': backend.library_path(inherited=os.environ.get('LD_LIBRARY_PATH', '')),
          'PYTHONPATH': os.pathsep.join(filter(None, (str(ROOT), os.environ.get('PYTHONPATH', ''))))}


def command() -> list[str]:
  return [sys.executable, '-m', MODULE]


def demote() -> None:
  """Runs in the child before exec: the UI is pinned to the camera core, and a preview must not inherit that."""
  background(PREVIEW_CORES, 10)


def spawn_options() -> dict:
  return {'preexec_fn': demote} if hasattr(os, 'sched_setaffinity') else {}


def offroad() -> bool:
  try:
    from openpilot.common.params import Params
    return Params().get_bool('IsOffroad')
  except Exception:
    return True


def request_rev() -> bool:
  """Ask the running enginesoundd to rev. Costs one file touch, so it is safe while driving."""
  try:
    trigger_path().touch()
    return True
  except OSError:
    return False


def start(spawn=subprocess.Popen, is_offroad=offroad) -> bool:
  """Fire and forget, for setting changes. Never raises into the settings page."""
  if not is_offroad():
    return request_rev()
  try:
    process = spawn(command(), cwd=str(ROOT), env=environment(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True, **spawn_options())
  except (OSError, ValueError):
    return False
  threading.Thread(target=process.wait, name='engine_preview_reaper', daemon=True).start()
  return True


if __name__ == '__main__':
  sys.exit(main())
