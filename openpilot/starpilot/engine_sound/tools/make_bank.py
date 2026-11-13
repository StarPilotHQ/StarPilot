#!/usr/bin/env python3
"""Build a loop bank from an enginesound preset (https://github.com/DasEtwas/enginesound, MIT).

  python make_bank.py --enginesound ~/enginesound/target/release/enginesound --config example1.esc \\
      --rpm 1000 8500 --step 500 --out banks/v10

Each loop is a whole number of engine cycles, recorded with enginesound's end-to-start crossfade so it repeats
seamlessly. The "off" (overrun) layer is the same preset with weakened combustion, since enginesound has no throttle.
Each loop is levelled to a loudness that rises with rpm (enginesound's raw output often gets quieter as it revs),
with overrun below the loaded layer, then the whole bank is scaled to one peak.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000
LOOP_SECONDS = 1.5
WARMUP_SECONDS = 1.0
PEAK = 0.89  # -1 dBFS
IDLE_LEVEL = 0.5     # loudness at the lowest rpm relative to the highest


def read_float_wav(path: Path) -> np.ndarray:
  data = path.read_bytes()
  offset = 12
  fmt = None
  while offset + 8 <= len(data):
    tag, size = data[offset:offset + 4], int.from_bytes(data[offset + 4:offset + 8], 'little')
    body = data[offset + 8:offset + 8 + size]
    if tag == b'fmt ':
      fmt = (int.from_bytes(body[2:4], 'little'), int.from_bytes(body[4:8], 'little'), int.from_bytes(body[14:16], 'little'))
    elif tag == b'data':
      if fmt != (1, SAMPLE_RATE, 32):
        raise ValueError(f'{path}: expected mono 32-bit float at {SAMPLE_RATE} Hz, got {fmt}')
      return np.frombuffer(body, dtype='<f4').astype(np.float64)
    offset += 8 + size + (size & 1)
  raise ValueError(f'{path}: no data chunk')


def overrun(config: str, ignition: float, intake: float) -> str:
  config = re.sub(r'(ignition_factor:\s*)([-0-9.e]+)', lambda m: f'{m.group(1)}{float(m.group(2)) * ignition}', config)
  return re.sub(r'(intake_volume:\s*)([-0-9.e]+)', lambda m: f'{m.group(1)}{float(m.group(2)) * intake}', config)


def record(binary: Path, config: Path, rpm: float, out: Path) -> np.ndarray:
  cycle = 120.0 / rpm
  cycles = max(2, round(LOOP_SECONDS / cycle))
  crossfade = 2 * cycle
  subprocess.run([str(binary), '--headless', '-c', str(config), '-r', f'{rpm}', '-l', f'{cycles * cycle + crossfade / 2}',
                  '-f', f'{crossfade}', '-w', f'{WARMUP_SECONDS}', '-q', str(SAMPLE_RATE), '-o', str(out)],
                 check=True, capture_output=True, timeout=120)
  return read_float_wav(out)


def write_pcm(path: Path, samples: np.ndarray) -> None:
  with wave.open(str(path), 'wb') as target:
    target.setnchannels(1)
    target.setsampwidth(2)
    target.setframerate(SAMPLE_RATE)
    target.writeframes(np.clip(np.round(samples * 32767), -32768, 32767).astype('<i2').tobytes())


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('--enginesound', type=Path, required=True)
  parser.add_argument('--config', type=Path, required=True)
  parser.add_argument('--rpm', type=float, nargs=2, metavar=('LOW', 'HIGH'), required=True)
  parser.add_argument('--step', type=float, default=500.0)
  parser.add_argument('--off-ignition', type=float, default=0.3, help='combustion strength on overrun, as a fraction')
  parser.add_argument('--off-intake', type=float, default=0.5, help='intake loudness on overrun, as a fraction')
  parser.add_argument('--off-level', type=float, default=0.6, help='overrun loudness relative to the loaded layer')
  parser.add_argument('--out', type=Path, required=True)
  args = parser.parse_args()

  points = np.arange(args.rpm[0], args.rpm[1] + args.step / 2, args.step)
  args.out.mkdir(parents=True, exist_ok=True)
  source = args.config.read_text()
  loops = {}
  with tempfile.TemporaryDirectory() as work:
    work = Path(work)
    configs = {'on': args.config, 'off': work / 'off.esc'}
    configs['off'].write_text(overrun(source, args.off_ignition, args.off_intake))
    for layer, config in configs.items():
      for rpm in points:
        loops[(layer, int(rpm))] = record(args.enginesound, config, float(rpm), work / f'{layer}_{int(rpm)}.wav')
        print(f'{layer} {int(rpm)} rpm: {len(loops[(layer, int(rpm))]) / SAMPLE_RATE:.2f} s')

  centred = {}
  for (layer, rpm), audio in loops.items():
    audio = audio - audio.mean()
    rise = (rpm - points[0]) / max(points[-1] - points[0], 1.0)
    level = (IDLE_LEVEL + (1 - IDLE_LEVEL) * rise) * (args.off_level if layer == 'off' else 1.0)
    centred[(layer, rpm)] = audio * level / max(np.sqrt(np.mean(audio ** 2)), 1e-9)
  scale = PEAK / max(np.abs(audio).max() for audio in centred.values())
  manifest = {'version': 1, 'source': f'enginesound preset {args.config.name}', 'layers': {'on': [], 'off': []}}
  for (layer, rpm), audio in sorted(centred.items()):
    name = f'{layer}_{rpm}.wav'
    write_pcm(args.out / name, audio * scale)
    manifest['layers'][layer].append({'rpm': rpm, 'file': name})
  (args.out / 'bank.json').write_text(json.dumps(manifest, indent=2) + '\n')
  print(f'{args.out}: {len(centred)} loops, scale {scale:.3g}')


if __name__ == '__main__':
  main()
