#!/usr/bin/env python3
"""Build a loop bank from a simulated engine. Two sources are supported:

enginesound (https://github.com/DasEtwas/enginesound, MIT), from one of its presets:
  python make_bank.py --enginesound ~/enginesound/target/release/enginesound --config example1.esc \\
      --rpm 1000 8500 --out banks/v10

engine-sim (https://github.com/ange-yaghi/engine-sim, MIT), through the esrec headless recorder (tools/esrec), with
the engine held at each rpm on engine-sim's dynamometer at full and closed throttle:
  python make_bank.py --esrec ~/engine-sim-build/esrec/build/esrec --engine-sim ~/engine-sim-build/engine-sim \\
      --engine engines/atg-video-2/03_2jz.mr --rpm 900 6800 --out banks/2jz

Each loop is a whole number of engine cycles whose end is crossfaded into its start, so it repeats seamlessly.
enginesound has no throttle, so its "off" (overrun) layer is the same preset with weakened combustion.
Each loop is levelled to a loudness that rises with rpm, with overrun below the loaded layer, then the whole bank
is scaled to one peak.
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
ESREC_WARMUP_SECONDS = 1.5
ESREC_RECORD_SECONDS = 2.5
CROSSFADE_CYCLES = 2
PEAK = 0.89  # -1 dBFS
IDLE_LEVEL = 0.5     # loudness at the lowest rpm relative to the highest


def read_wav(path: Path) -> tuple[np.ndarray, int]:
  data = path.read_bytes()
  offset = 12
  fmt = None
  while offset + 8 <= len(data):
    tag, size = data[offset:offset + 4], int.from_bytes(data[offset + 4:offset + 8], 'little')
    body = data[offset + 8:offset + 8 + size]
    if tag == b'fmt ':
      fmt = (int.from_bytes(body[2:4], 'little'), int.from_bytes(body[4:8], 'little'), int.from_bytes(body[14:16], 'little'))
    elif tag == b'data':
      if fmt is not None and fmt[0] == 1 and fmt[2] == 32:
        return np.frombuffer(body, dtype='<f4').astype(np.float64), fmt[1]
      if fmt is not None and fmt[0] == 1 and fmt[2] == 16:
        return np.frombuffer(body, dtype='<i2').astype(np.float64) / 32768.0, fmt[1]
      raise ValueError(f'{path}: expected mono 16-bit or 32-bit float, got {fmt}')
    offset += 8 + size + (size & 1)
  raise ValueError(f'{path}: no data chunk')


def resample(audio: np.ndarray, rate: int) -> np.ndarray:
  if rate == SAMPLE_RATE:
    return audio
  length = round(len(audio) * SAMPLE_RATE / rate)
  spectrum = np.fft.rfft(audio)
  out = np.zeros(length // 2 + 1, dtype=complex)
  keep = min(len(spectrum), len(out))
  out[:keep] = spectrum[:keep]
  return np.fft.irfft(out, n=length) * length / len(audio)


def seamless(audio: np.ndarray, rpm: float) -> np.ndarray:
  """A whole number of cycles from the steady middle of a recording, its following cycles crossfaded into its start."""
  cycle = 120.0 / rpm * SAMPLE_RATE
  cycles = max(2, round(LOOP_SECONDS * SAMPLE_RATE / cycle))
  length = round(cycles * cycle)
  fade = round(CROSSFADE_CYCLES * cycle)
  start = (len(audio) - length - fade) // 2
  if start < 0:
    raise ValueError(f'{rpm:.0f} rpm: recording too short for a {length + fade}-sample loop')
  segment = audio[start:start + length + fade]
  ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(fade) / fade)
  loop = segment[:length].copy()
  loop[:fade] = segment[:fade] * np.sqrt(ramp) + segment[length:] * np.sqrt(1 - ramp)
  return loop


def overrun(config: str, ignition: float, intake: float) -> str:
  config = re.sub(r'(ignition_factor:\s*)([-0-9.e]+)', lambda m: f'{m.group(1)}{float(m.group(2)) * ignition}', config)
  return re.sub(r'(intake_volume:\s*)([-0-9.e]+)', lambda m: f'{m.group(1)}{float(m.group(2)) * intake}', config)


def record_enginesound(binary: Path, config: Path, rpm: float, out: Path) -> np.ndarray:
  cycle = 120.0 / rpm
  cycles = max(2, round(LOOP_SECONDS / cycle))
  crossfade = 2 * cycle
  subprocess.run([str(binary), '--headless', '-c', str(config), '-r', f'{rpm}', '-l', f'{cycles * cycle + crossfade / 2}',
                  '-f', f'{crossfade}', '-w', f'{WARMUP_SECONDS}', '-q', str(SAMPLE_RATE), '-o', str(out)],
                 check=True, capture_output=True, timeout=120)
  audio, rate = read_wav(out)
  return resample(audio, rate)


def record_engine_sim(recorder: Path, root: Path, engine: str, node: str | None, points,
                      work: Path) -> dict[tuple[str, int], np.ndarray]:
  """One recorder run per engine: compiling the script dominates start-up, so every rpm and throttle shares it."""
  script = root / 'assets' / f'esrec_{work.name}.mr'
  entry = f'set_engine({node}())' if node else 'main()'
  script.write_text(f'import "engine_sim.mr"\nimport "themes/default.mr"\nimport "{engine}"\n\nuse_default_theme()\n{entry}\n')
  run_dir = root / 'run'
  run_dir.mkdir(exist_ok=True)
  specs = [f'{int(rpm)}:{1.0 if layer == "on" else 0.0}:{layer}_{int(rpm)}' for layer in ('on', 'off') for rpm in points]
  try:
    result = subprocess.run([str(recorder), f'../assets/{script.name}', str(work), str(ESREC_WARMUP_SECONDS),
                             str(ESREC_RECORD_SECONDS), *specs], cwd=run_dir, capture_output=True, text=True, timeout=3600)
  finally:
    script.unlink(missing_ok=True)
  print(result.stdout.strip())
  if result.returncode:
    raise SystemExit(f'esrec failed: {result.stderr.strip()} (see {run_dir / "error_log.log"})')
  loops = {}
  for layer in ('on', 'off'):
    for rpm in points:
      audio, rate = read_wav(work / f'{layer}_{int(rpm)}.wav')
      loops[(layer, int(rpm))] = seamless(resample(audio, rate), float(rpm))
  return loops


def write_pcm(path: Path, samples: np.ndarray) -> None:
  with wave.open(str(path), 'wb') as target:
    target.setnchannels(1)
    target.setsampwidth(2)
    target.setframerate(SAMPLE_RATE)
    target.writeframes(np.clip(np.round(samples * 32767), -32768, 32767).astype('<i2').tobytes())


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  source = parser.add_mutually_exclusive_group(required=True)
  source.add_argument('--enginesound', type=Path, help='enginesound binary')
  source.add_argument('--esrec', type=Path, help='esrec binary')
  parser.add_argument('--config', type=Path, help='enginesound preset (.esc)')
  parser.add_argument('--engine-sim', type=Path, help='engine-sim checkout (holds assets/ and es/)')
  parser.add_argument('--engine', help='engine script, relative to engine-sim/assets')
  parser.add_argument('--engine-node', help="engine node to use when the script has no main (engine-sim's default car)")
  parser.add_argument('--rpm', type=float, nargs=2, metavar=('LOW', 'HIGH'), required=True)
  parser.add_argument('--step', type=float, default=500.0)
  parser.add_argument('--off-ignition', type=float, default=0.3, help='enginesound: combustion strength on overrun')
  parser.add_argument('--off-intake', type=float, default=0.5, help='enginesound: intake loudness on overrun')
  parser.add_argument('--off-level', type=float, default=0.6, help='overrun loudness relative to the loaded layer')
  parser.add_argument('--out', type=Path, required=True)
  args = parser.parse_args()
  if args.enginesound and not args.config:
    parser.error('--enginesound needs --config')
  if args.esrec and not (args.engine_sim and args.engine):
    parser.error('--esrec needs --engine-sim and --engine')

  points = np.arange(args.rpm[0], args.rpm[1] + args.step / 2, args.step)
  args.out.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory() as work:
    work = Path(work)
    if args.esrec:
      loops = record_engine_sim(args.esrec, args.engine_sim.resolve(), args.engine, args.engine_node, points, work)
      description = f'engine-sim {args.engine}'
    else:
      loops = {}
      configs = {'on': args.config, 'off': work / 'off.esc'}
      configs['off'].write_text(overrun(args.config.read_text(), args.off_ignition, args.off_intake))
      for layer, config in configs.items():
        for rpm in points:
          loops[(layer, int(rpm))] = record_enginesound(args.enginesound, config, float(rpm), work / f'{layer}_{int(rpm)}.wav')
      description = f'enginesound preset {args.config.name}'

  centred = {}
  for (layer, rpm), audio in loops.items():
    audio = audio - audio.mean()
    rise = (rpm - points[0]) / max(points[-1] - points[0], 1.0)
    level = (IDLE_LEVEL + (1 - IDLE_LEVEL) * rise) * (args.off_level if layer == 'off' else 1.0)
    centred[(layer, rpm)] = audio * level / max(np.sqrt(np.mean(audio ** 2)), 1e-9)
  scale = PEAK / max(np.abs(audio).max() for audio in centred.values())
  manifest = {'version': 1, 'source': description, 'layers': {'on': [], 'off': []}}
  for (layer, rpm), audio in sorted(centred.items()):
    name = f'{layer}_{rpm}.wav'
    write_pcm(args.out / name, audio * scale)
    manifest['layers'][layer].append({'rpm': rpm, 'file': name})
  (args.out / 'bank.json').write_text(json.dumps(manifest, indent=2) + '\n')
  print(f'{args.out}: {len(centred)} loops, scale {scale:.3g}')


if __name__ == '__main__':
  main()
