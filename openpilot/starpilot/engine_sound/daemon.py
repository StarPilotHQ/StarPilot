"""enginesoundd: synthesized engine sound for the car speakers, through the private music server.

Never driving-critical. The comma's own speaker and its alerts are not touched; the engine goes quiet
while an alert is sounding so it cannot mask one.
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

from openpilot.starpilot.engine_sound import settings
from openpilot.starpilot.engine_sound.loops import BankError, LoopSynth, find_bank, load_bank
from openpilot.starpilot.engine_sound.model import EngineModel
from openpilot.starpilot.engine_sound.profiles import get
from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE, Synth, to_pcm

BLOCK_SECONDS = 0.01
FRAMES = int(SAMPLE_RATE * BLOCK_SECONDS)
BLOCK_BYTES = FRAMES * 2
# Mono s16: 100 ms maximum, 40 ms target, start immediately, 10 ms requests.
BUFFER = (BLOCK_BYTES * 10, BLOCK_BYTES * 4, 0, BLOCK_BYTES)
FADE_SECONDS = 0.08
SETTINGS_SECONDS = 1.0
RECONNECT_SECONDS = 2.0
EXEC_MARKER = 'STARPILOT_ENGINE_SOUND_LIBRARIES'
PARKED_SPEED = 0.3
PEDAL_TAKEOVER = 0.02
TRIGGER_BLOCKS = 10
AUDIO_CORES = (0, 1, 2, 3, 4, 5)    # 6 is the camera's and 7 the driving model's
PREVIEW_CORES = (0, 1, 2, 3)


def background(cores, nice: int) -> None:
  """Plain scheduling, away from the camera and model cores, whatever the launching process was pinned to."""
  try:
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    os.sched_setaffinity(0, cores)
    os.nice(nice)
  except (AttributeError, OSError):
    pass


class Engine:
  """Car state in, PCM blocks out. No I/O, so it runs the same in tests and the offline renderer."""

  def __init__(self, profile_name: str, bank_root: Path | None = None):
    self.volume = 0.0
    self.bank_root = bank_root
    self.select(profile_name)

  def select(self, profile_name: str) -> None:
    self.profile = get(profile_name)
    self.model = EngineModel(self.profile)
    self.synth = voice(self.profile, self.bank_root)

  def block(self, pedal: float, speed: float, accel: float, in_gear: bool, volume: float) -> bytes:
    self.volume = volume + (self.volume - volume) * math.exp(-BLOCK_SECONDS / FADE_SECONDS)
    rpm, load = self.model.update(BLOCK_SECONDS, pedal, speed, accel, in_gear)
    return to_pcm(self.synth.render(rpm, load, FRAMES, self.volume))


class Rev:
  """Plays the preview rev script in place of the live pedal, only while parked and only until the driver touches the pedal."""

  def __init__(self):
    self.steps: list[tuple[float, float, float, bool]] = []
    self.index = 0

  @staticmethod
  def parked(pedal: float, speed: float, in_gear: bool) -> bool:
    return not in_gear and abs(speed) < PARKED_SPEED and pedal < PEDAL_TAKEOVER

  def start(self, steps, pedal: float, speed: float, in_gear: bool) -> bool:
    if not self.parked(pedal, speed, in_gear):
      return False
    self.steps, self.index = list(steps), 0
    return True

  def step(self, pedal: float, speed: float, accel: float, in_gear: bool) -> tuple[float, float, float, bool]:
    live = (pedal, speed, accel, in_gear)
    if self.index >= len(self.steps):
      return live
    if not self.parked(pedal, speed, in_gear):
      self.steps = []
      return live
    self.index += 1
    return self.steps[self.index - 1]


def trigger_stamp(path: Path) -> int:
  try:
    return path.stat().st_mtime_ns
  except OSError:
    return 0


def voice(profile, bank_root: Path | None = None):
  directory = find_bank(profile.loops, bank_root)
  if directory is not None:
    try:
      return LoopSynth(profile, load_bank(directory))
    except BankError:
      pass
  return Synth(profile)


def audible(sm, no_alert) -> bool:
  state = sm['carState']
  return bool(sm.alive['carState'] and state.canValid and not state.canTimeout and sm['selfdriveState'].alertSound == no_alert)


def with_bundled_libraries() -> None:
  """libpulse is not on AGNOS; the loader only honours LD_LIBRARY_PATH set before the process starts."""
  from openpilot.starpilot.soloist import backend
  if os.environ.get(EXEC_MARKER):
    return
  env = {**os.environ, EXEC_MARKER: '1',
         'LD_LIBRARY_PATH': backend.library_path(inherited=os.environ.get('LD_LIBRARY_PATH', '')),
         'PYTHONPATH': os.pathsep.join(filter(None, (str(Path(__file__).resolve().parents[3]), os.environ.get('PYTHONPATH', ''))))}
  os.execve(sys.executable, [sys.executable, '-m', 'openpilot.starpilot.engine_sound.daemon'], env)


def main() -> None:
  with_bundled_libraries()
  from openpilot.cereal import messaging
  from opendbc.car.structs import car
  from openpilot.common.params import Params
  from openpilot.common.swaglog import cloudlog
  from openpilot.starpilot.soloist import backend
  from openpilot.starpilot.soloist.paths import Layout
  from openpilot.starpilot.soloist.pulse_client import PulseWriter
  from openpilot.starpilot.engine_sound.preview import rev_script, trigger_path

  background(AUDIO_CORES, 5)
  params = Params()
  sm = messaging.SubMaster(['carState', 'starpilotCarState', 'selfdriveState'])
  free_gears = (car.CarState.GearShifter.park, car.CarState.GearShifter.neutral)
  no_alert = car.CarControl.HUDControl.AudibleAlert.none
  socket_path = backend.runtime_paths(Layout.default())['socket']

  name, level = settings.profile_name(params), settings.volume(params)
  engine = Engine(name)
  rev = Rev()
  trigger = trigger_path()
  triggered = trigger_stamp(trigger)
  writer = None
  settings_at = 0.0
  blocks = 0
  while True:
    if writer is None:
      try:
        if not socket_path.exists():
          raise RuntimeError('music server is not running')
        # No sink name: the server has exactly one sink, and its name changes while the speaker is away.
        writer = PulseWriter(f'unix:{socket_path}', client='StarPilot engine', stream='Engine sound', sink=None,
                             channels=1, buffer=BUFFER)
        cloudlog.info('enginesoundd connected')
      except (OSError, RuntimeError) as error:
        cloudlog.info(f'enginesoundd waiting for audio output: {error}')
        time.sleep(RECONNECT_SECONDS)
        continue

    sm.update(0)
    state = sm['carState']
    live = (sm['starpilotCarState'].acceleratorPedal, state.vEgo, state.aEgo, state.gearShifter not in free_gears)

    requested = False
    now = time.monotonic()
    if now >= settings_at:
      settings_at = now + SETTINGS_SECONDS
      selected, new_level = settings.profile_name(params), settings.volume(params)
      requested = selected != name or new_level != level
      level = new_level
      if selected != name:
        name = selected
        engine.select(name)
    blocks += 1
    if blocks % TRIGGER_BLOCKS == 0 and (stamp := trigger_stamp(trigger)) != triggered:
      triggered, requested = stamp, True
    if requested:
      rev.start(rev_script(), live[0], live[1], live[3])

    block = engine.block(*rev.step(*live), level if audible(sm, no_alert) else 0.0)
    try:
      writer.write(block)
    except RuntimeError as error:
      cloudlog.info(f'enginesoundd lost audio output: {error}')
      try:
        writer.close()
      except Exception:
        pass
      writer = None


if __name__ == '__main__':
  main()
