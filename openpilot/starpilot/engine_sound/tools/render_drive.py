#!/usr/bin/env python3
"""Render engine sound for a recorded drive (or a parked rev demo) to WAV files, one per profile.

  python render_drive.py <dir of uncompressed rlogs> --out <dir> [--start 0 --seconds 120]
  python render_drive.py --demo --out <dir>

Logs recorded before acceleratorPedal existed fall back to raw ACCELERATOR 0x35 byte 5 on any bus.
"""
from __future__ import annotations

import argparse
import re
import sys
import wave
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from openpilot.starpilot.engine_sound.model import EngineModel  # noqa: E402
from openpilot.starpilot.engine_sound.profiles import PROFILES  # noqa: E402
from openpilot.starpilot.engine_sound.daemon import voice  # noqa: E402
from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE, to_pcm  # noqa: E402

DT = 0.01
FRAMES = int(SAMPLE_RATE * DT)
ACCELERATOR_ADDRESS, PEDAL_BYTE, PEDAL_FULL = 0x35, 5, 254.0
FREE_GEARS = ('park', 'neutral')


def load_schema():
  import capnp
  capnp.remove_import_hook()
  return capnp.load(str(REPO / 'openpilot' / 'cereal' / 'log.capnp'),
                    imports=[str(REPO / 'opendbc_repo' / 'opendbc' / 'car'), str(REPO / 'openpilot' / 'cereal')])


def segments(path: Path) -> list[Path]:
  files = [f for f in path.iterdir() if f.is_file() and 'rlog' in f.name and not f.name.endswith(('.zst', '.bz2'))]
  return sorted(files, key=lambda f: int(match.group(1)) if (match := re.search(r'--(\d+)--rlog', f.name)) else 0)


def drive_samples(path: Path, start: float, seconds: float):
  """Yield (pedal, speed, accel, in_gear) once per carState."""
  log = load_schema()
  raw_pedal, published, t0 = 0.0, None, None
  for segment in segments(path):
    with open(segment, 'rb') as handle:
      for event in log.Event.read_multiple(handle, traversal_limit_in_words=2 ** 62):
        which = event.which()
        if which == 'can':
          for frame in event.can:
            if frame.address == ACCELERATOR_ADDRESS and len(frame.dat) > PEDAL_BYTE:
              raw_pedal = frame.dat[PEDAL_BYTE] / PEDAL_FULL
        elif which == 'starpilotCarState':
          published = getattr(event.starpilotCarState, 'acceleratorPedal', None)
        elif which == 'carState':
          t0 = event.logMonoTime if t0 is None else t0
          elapsed = (event.logMonoTime - t0) * 1e-9
          if elapsed < start:
            continue
          if elapsed > start + seconds:
            return
          state = event.carState
          pedal = published if published else raw_pedal
          yield min(pedal, 1.0), state.vEgo, state.aEgo, str(state.gearShifter) not in FREE_GEARS


def demo_samples():
  """Parked: blips, a slow sweep to the limiter, then a pull through the gears and a lift."""
  def hold(pedal, seconds, speed=0.0, accel=0.0, in_gear=False):
    return [(pedal, speed, accel, in_gear)] * int(seconds / DT)
  out = hold(0.0, 1.5)
  for _ in range(3):
    out += hold(0.8, 0.25) + hold(0.0, 0.7)
  out += [(i / 400, 0.0, 0.0, False) for i in range(400)] + hold(1.0, 1.5) + hold(0.0, 2.0)
  speed = 0.0
  for step in range(1800):
    pedal = 0.9 if step < 1200 else 0.0
    accel = 3.2 * pedal - 0.02 * speed - (0.6 if pedal == 0 else 0)
    speed = max(speed + accel * DT, 0.0)
    out.append((pedal, speed, accel, True))
  return out


def render(samples, profile, volume: float, banks: Path | None = None) -> bytes:
  model, synth = EngineModel(profile), voice(profile, banks)
  chunks = []
  for pedal, speed, accel, in_gear in samples:
    rpm, load = model.update(DT, pedal, speed, accel, in_gear)
    chunks.append(to_pcm(synth.render(rpm, load, FRAMES, volume)))
  return b''.join(chunks)


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('route', nargs='?', type=Path)
  parser.add_argument('--demo', action='store_true')
  parser.add_argument('--out', type=Path, required=True)
  parser.add_argument('--start', type=float, default=0.0)
  parser.add_argument('--seconds', type=float, default=120.0)
  parser.add_argument('--volume', type=float, default=0.8)
  parser.add_argument('--profile', choices=sorted(PROFILES), action='append')
  parser.add_argument('--banks', type=Path, help='loop bank root (default: the device location)')
  parser.add_argument('--bank', help="audition this bank with each chosen profile instead of the profile's own")
  args = parser.parse_args()
  if not args.demo and args.route is None:
    parser.error('give a route directory or --demo')

  samples = demo_samples() if args.demo else list(drive_samples(args.route, args.start, args.seconds))
  if not samples:
    raise SystemExit('No carState in that range')
  args.out.mkdir(parents=True, exist_ok=True)
  for name in args.profile or sorted(PROFILES):
    profile = replace(PROFILES[name], loops=args.bank) if args.bank else PROFILES[name]
    target = args.out / f'{"demo" if args.demo else "drive"}_{name}{"_" + args.bank if args.bank else ""}.wav'
    with wave.open(str(target), 'wb') as out:
      out.setnchannels(1)
      out.setsampwidth(2)
      out.setframerate(SAMPLE_RATE)
      out.writeframes(render(samples, profile, args.volume, args.banks))
    print(f'{target}  ({len(samples) * DT:.0f} s)')


if __name__ == '__main__':
  main()
