#!/usr/bin/env python3
"""Build a loop bank from a recording of a real engine.

  python bank_from_recording.py hellcat.wav --cylinders 8 --anchor 14 700 \\
      --on 25-94 --off 94-119 --idle 10.5-17.5 --out banks/hellcat

rpm is tracked through the recording from the anchor (a time and its known rpm, usually at idle), following the
engine's harmonics without jumps, so the anchor fixes the scale everywhere it can be followed continuously.
Each bank rpm takes the longest stretch near it from the --on (loaded, rpm not falling) or --off (overrun, rpm not
rising) ranges; the stretch is resampled against the engine's own cycle so it plays at one steady rpm, then made
into a seamless loop. --idle ranges are steady idle and feed both layers.
Check the track with --track-image before trusting a bank: a wrong anchor puts every loop an octave out.
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000
HOP = SAMPLE_RATE // 20
FFT = 1 << 14
STEP = 1.008                      # candidate rpm spacing as a ratio
MAX_STEP = 0.1                    # largest rpm change between 50 ms frames, as a log ratio
NEAR = 0.07                       # stretch may wander this share either side of the bank rpm; replay evens out the pitch
MIN_CYCLES = 4                    # fewer distinct engine cycles than this cannot hide a repeat
LOOP_SECONDS = 1.5
CROSSFADE_CYCLES = 2
JOIN_SECONDS = 0.003              # crossfade between shuffled cycles
PEAK = 0.89
IDLE_LEVEL = 0.5
OFF_LEVEL = 0.6


def read_recording(path: Path) -> np.ndarray:
  with wave.open(str(path)) as source:
    rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
    raw = source.readframes(source.getnframes())
  if width != 2:
    raise SystemExit(f'{path}: expected 16-bit PCM')
  audio = np.frombuffer(raw, '<i2').astype(np.float64).reshape(-1, channels).mean(axis=1) / 32768.0
  if rate != SAMPLE_RATE:
    length = round(len(audio) * SAMPLE_RATE / rate)
    spectrum, out = np.fft.rfft(audio), np.zeros(length // 2 + 1, dtype=complex)
    keep = min(len(spectrum), len(out))
    out[:keep] = spectrum[:keep]
    audio = np.fft.irfft(out, n=length) * length / len(audio)
  return audio


def track(audio: np.ndarray, cylinders: int, anchors: list[tuple[float, float]], half_orders: bool) -> tuple[np.ndarray, np.ndarray]:
  """(times, rpm) every 50 ms by dynamic programming over engine-order salience, pinned at each anchor."""
  freq = np.fft.rfftfreq(FFT, 1 / SAMPLE_RATE)
  candidates = 400.0 * STEP ** np.arange(int(np.log(8000 / 400) / np.log(STEP)) + 1)
  # Orders are multiples of rpm/120. Some engines carry only the whole engine orders (even ones) under load, and
  # following the odd ones there would track half the true rpm; others carry every half order. The firing order leads.
  orders = np.arange(1, 4 * cylinders + 1) if half_orders else np.arange(2, 4 * cylinders + 1, 2)
  weights = np.where(orders % cylinders == 0, 3.0, 1.0)
  rows = []
  for start in range(0, len(audio) - FFT, HOP):
    level = np.log(np.abs(np.fft.rfft(audio[start:start + FFT] * np.hanning(FFT))) + 1e-9)
    base = np.median(np.lib.stride_tricks.sliding_window_view(np.pad(level, 10, mode='edge'), 21), axis=1)
    white = np.maximum(level - base, 0.0)
    rows.append(sum(w * np.interp(candidates * order / 120, freq, white) for order, w in zip(orders, weights, strict=True)) / weights.sum())
  salience = np.array(rows)
  times = np.arange(len(salience)) * HOP / SAMPLE_RATE + FFT / 2 / SAMPLE_RATE
  for seconds, anchor_rpm in anchors:
    pinned = int(np.argmin(np.abs(times - seconds)))
    salience[pinned] = np.where(np.abs(np.log(candidates / anchor_rpm)) < 0.03, salience[pinned].max() + 100.0, -1e9)
  distance = np.abs(np.subtract.outer(np.arange(len(candidates)), np.arange(len(candidates)))) * np.log(STEP)
  penalty = np.where(distance > MAX_STEP, 1e9, 6.0 * distance)
  score, back = salience[0].copy(), np.zeros(salience.shape, dtype=np.int32)
  for t in range(1, len(salience)):
    total = score[:, None] - penalty
    back[t] = np.argmax(total, axis=0)
    score = total[back[t], np.arange(len(candidates))] + salience[t]
  path = np.zeros(len(salience), dtype=np.int32)
  path[-1] = int(np.argmax(score))
  for t in range(len(salience) - 1, 0, -1):
    path[t - 1] = back[t, path[t]]
  return times, candidates[path]


def ranges(text: list[str]) -> list[tuple[float, float]]:
  return [tuple(float(value) for value in item.split('-')) for item in text]


def usable(times: np.ndarray, rpm: np.ndarray, spans, direction: int) -> np.ndarray:
  """Frames inside the spans whose rpm is not moving against the layer (no falling under load, no rising on overrun)."""
  inside = np.zeros(len(times), dtype=bool)
  for start, end in spans:
    inside |= (times >= start) & (times <= end)
  slope = np.gradient(rpm, times)
  return inside & (direction * slope >= -150.0) if direction else inside


def stretch(times: np.ndarray, rpm: np.ndarray, mask: np.ndarray, target: float) -> tuple[float, float] | None:
  near = mask & (np.abs(np.log(rpm / target)) < NEAR)
  best, run_start = None, None
  for index, flag in enumerate(np.append(near, False)):
    if flag and run_start is None:
      run_start = index
    elif not flag and run_start is not None:
      span = (times[run_start], times[index - 1])
      if best is None or span[1] - span[0] > best[1] - best[0]:
        best = span
      run_start = None
  return best


def steady(audio: np.ndarray, times: np.ndarray, rpm: np.ndarray, span: tuple[float, float], target: float) -> np.ndarray | None:
  """The span replayed against the engine's own cycle so it runs at exactly the target rpm, as a seamless loop."""
  sample_times = np.arange(int(span[0] * SAMPLE_RATE), int(span[1] * SAMPLE_RATE)) / SAMPLE_RATE
  if len(sample_times) < 2:
    return None
  cycles =np.cumsum(np.interp(sample_times, times, rpm)) / 120.0 / SAMPLE_RATE
  per_sample = target / 120.0 / SAMPLE_RATE
  out_cycles = np.arange(cycles[0], cycles[-1], per_sample)
  source = np.interp(out_cycles, cycles, sample_times) * SAMPLE_RATE
  whole = source.astype(np.int64)
  fraction = source - whole
  steady_audio = audio[whole] * (1 - fraction) + audio[np.minimum(whole + 1, len(audio) - 1)] * fraction
  cycle = 120.0 / target * SAMPLE_RATE
  fade = round(CROSSFADE_CYCLES * cycle)
  count = int(min(LOOP_SECONDS * SAMPLE_RATE, len(steady_audio) - fade) // cycle)
  if count < LOOP_SECONDS * SAMPLE_RATE // cycle:
    return shuffled(steady_audio, cycle, target)
  length = round(count * cycle)
  start = (len(steady_audio) - length - fade) // 2
  segment = steady_audio[start:start + length + fade]
  ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(fade) / fade)
  loop = segment[:length].copy()
  loop[:fade] = segment[:fade] * np.sqrt(ramp) + segment[length:] * np.sqrt(1 - ramp)
  return loop - loop.mean()


def shuffled(steady_audio: np.ndarray, cycle: float, target: float, seed: int = 0) -> np.ndarray | None:
  """A full-length loop from too short a stretch: its engine cycles in a shuffled order, so no short pattern repeats.

  Each cycle is joined to the next on a cycle boundary with a short crossfade; the last join wraps to the first cycle.
  """
  join = round(JOIN_SECONDS * SAMPLE_RATE)
  size = round(cycle)
  grains = [steady_audio[round(i * cycle):round(i * cycle) + size + join] for i in range(int(len(steady_audio) // cycle))]
  grains = [grain for grain in grains if len(grain) == size + join]
  if len(grains) < MIN_CYCLES:
    return None
  rng = np.random.default_rng(seed + int(target))
  order: list[int] = []
  while len(order) * cycle < LOOP_SECONDS * SAMPLE_RATE:
    for index in rng.permutation(len(grains)):
      if not order or index != order[-1]:
        order.append(int(index))
  if order[-1] == order[0]:
    order.pop()
  ramp = np.sin(0.5 * np.pi * np.arange(join) / join) ** 2
  loop = np.concatenate([grains[index][:size] for index in order])
  for position, index in enumerate(order):
    tail = grains[index][size:]
    at = (position + 1) * size % len(loop)
    loop[at:at + join] = loop[at:at + join] * ramp + tail * (1 - ramp)
  return loop - loop.mean()


def track_image(path: Path, audio: np.ndarray, times: np.ndarray, rpm: np.ndarray, cylinders: int) -> None:
  from PIL import Image, ImageDraw
  columns = [20 * np.log10(np.abs(np.fft.rfft(audio[i:i + 8192] * np.hanning(8192))) + 1e-9)
             for i in range(0, len(audio) - 8192, SAMPLE_RATE // 20)]
  top = 800.0
  image = np.array(columns).T[:int(top / (SAMPLE_RATE / 8192))][::-1]
  image = np.clip((image - (image.max() - 60)) / 60, 0, 1)
  picture = Image.fromarray((image * 200).astype(np.uint8)).convert('RGB').resize((1600, 400))
  draw = ImageDraw.Draw(picture)
  seconds = len(audio) / SAMPLE_RATE
  draw.line([(t / seconds * 1600, 400 - r * cylinders / 120 / top * 400) for t, r in zip(times, rpm, strict=True)], fill=(255, 60, 60))
  for second in range(0, int(seconds) + 1, 5):
    draw.text((second / seconds * 1600 + 2, 2), f'{second}s', fill=(255, 255, 255))
  picture.save(path)


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('recording', type=Path)
  parser.add_argument('--cylinders', type=int, required=True)
  parser.add_argument('--anchor', type=float, nargs=2, metavar=('SECONDS', 'RPM'), action='append', required=True,
                      help='a time and its known rpm; repeat for each take in a spliced recording')
  parser.add_argument('--on', nargs='+', default=[], metavar='START-END')
  parser.add_argument('--off', nargs='+', default=[], metavar='START-END')
  parser.add_argument('--idle', nargs='+', default=[], metavar='START-END')
  parser.add_argument('--half-orders', action='store_true',
                      help='track with every half order; use when the loaded spectrum has lines every rpm/120 Hz')
  parser.add_argument('--step', type=float, default=250.0)
  parser.add_argument('--out', type=Path, required=True)
  parser.add_argument('--track-image', type=Path)
  args = parser.parse_args()

  audio = read_recording(args.recording)
  times, rpm = track(audio, args.cylinders, [tuple(anchor) for anchor in args.anchor], args.half_orders)
  if args.track_image:
    track_image(args.track_image, audio, times, rpm, args.cylinders)
  masks = {'on': usable(times, rpm, ranges(args.on), 1), 'off': usable(times, rpm, ranges(args.off), -1)}
  idle = usable(times, rpm, ranges(args.idle), 0)
  low = min(rpm[idle | masks['on'] | masks['off']])
  high = max(rpm[masks['on'] | masks['off']])
  points = np.arange(np.ceil(low / args.step) * args.step, high + 1, args.step)
  if idle.any():
    idle_rpm = round(float(np.median(rpm[idle])))
    points = np.unique(np.append(points[points >= 0.9 * idle_rpm], idle_rpm))
  loops = {}
  for layer, mask in masks.items():
    if not mask.any():
      continue
    for target in points:
      span = stretch(times, rpm, mask | idle, target)
      loop = steady(audio, times, rpm, span, target) if span else None
      if loop is not None:
        loops[(layer, int(target))] = loop
  if not any(layer == 'on' for layer, _ in loops):
    raise SystemExit('no loaded stretch long enough for a loop')

  on_points = sorted(rpm for layer, rpm in loops if layer == 'on')
  span = max(on_points[-1] - on_points[0], 1)
  levelled = {}
  for (layer, target), loop in loops.items():
    level = (IDLE_LEVEL + (1 - IDLE_LEVEL) * (target - on_points[0]) / span) * (OFF_LEVEL if layer == 'off' else 1.0)
    levelled[(layer, target)] = loop * level / max(np.sqrt(np.mean(loop ** 2)), 1e-9)
  scale = PEAK / max(np.abs(loop).max() for loop in levelled.values())
  args.out.mkdir(parents=True, exist_ok=True)
  manifest = {'version': 1, 'source': f'recording {args.recording.name}', 'layers': {}}
  for (layer, target), loop in sorted(levelled.items()):
    name = f'{layer}_{target}.wav'
    with wave.open(str(args.out / name), 'wb') as out:
      out.setnchannels(1)
      out.setsampwidth(2)
      out.setframerate(SAMPLE_RATE)
      out.writeframes(np.clip(np.round(loop * scale * 32767), -32768, 32767).astype('<i2').tobytes())
    manifest['layers'].setdefault(layer, []).append({'rpm': target, 'file': name})
  (args.out / 'bank.json').write_text(json.dumps(manifest, indent=2) + '\n')
  for layer, entries in manifest['layers'].items():
    print(f'{layer}: {len(entries)} loops, {entries[0]["rpm"]}-{entries[-1]["rpm"]} rpm')


if __name__ == '__main__':
  main()
