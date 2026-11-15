#!/usr/bin/env python3
"""Make a lower, fuller copy of a loop bank for small car speakers.

  python lower_bank.py <source bank dir> <output bank dir>

Each loop is relabelled to a higher rpm, so every rpm plays a recording from lower in the rev range, and is
equalised in the frequency domain, which keeps the loops seamless. Off-throttle loops also take some of the
on-throttle loop of the same rpm and a midrange cut, so light throttle keeps its body.
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000
PEAK = 0.92


def shelf(freq: np.ndarray, low: float, high: float, gain_db: float) -> np.ndarray:
  """gain_db below low, unity above high, a log-frequency ramp between."""
  position = np.clip((np.log(np.maximum(freq, 1.0)) - np.log(low)) / (np.log(high) - np.log(low)), 0.0, 1.0)
  return 10 ** (gain_db * (1 - position) / 20)


def equalise(audio: np.ndarray, bass_db: float, treble_db: float, mid_db: float = 0.0) -> np.ndarray:
  freq = np.fft.rfftfreq(len(audio), 1 / SAMPLE_RATE)
  gain = shelf(freq, 100.0, 300.0, bass_db)
  gain *= 10 ** (treble_db / 20) / shelf(freq, 900.0, 2500.0, treble_db)
  gain *= 10 ** (mid_db / 20) / shelf(freq, 300.0, 800.0, mid_db)
  return np.fft.irfft(np.fft.rfft(audio) * gain, len(audio))


def read(path: Path) -> np.ndarray:
  with wave.open(str(path)) as source:
    return np.frombuffer(source.readframes(source.getnframes()), '<i2').astype(np.float64) / 32768


def lower(source: Path, target: Path, pitch: float, bass_db: float, treble_db: float, off_mix: float, off_mid_db: float) -> None:
  manifest = json.loads((source / 'bank.json').read_text())
  on = {entry['rpm']: read(source / entry['file']) for entry in manifest['layers']['on']}
  loops = {}
  for layer, entries in manifest['layers'].items():
    for entry in entries:
      audio = read(source / entry['file'])
      if layer == 'off':
        audio = equalise(audio, bass_db, treble_db, off_mid_db)
        partner = on.get(entry['rpm'])
        if partner is not None and len(partner) == len(audio):
          audio = audio + off_mix * equalise(partner, bass_db, treble_db)
      else:
        audio = equalise(audio, bass_db, treble_db)
      loops[(layer, entry['rpm'])] = audio
  scale = min(1.0, PEAK / max(np.abs(audio).max() for audio in loops.values()))

  target.mkdir(parents=True, exist_ok=True)
  for layer, entries in manifest['layers'].items():
    for entry in entries:
      audio = loops[(layer, entry['rpm'])] * scale
      entry['rpm'] = round(entry['rpm'] * pitch)
      entry['file'] = f"{layer}_{entry['rpm']}.wav"
      with wave.open(str(target / entry['file']), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes((np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes())
  (target / 'bank.json').write_text(json.dumps(manifest, indent=2) + '\n')
  print(f'{target}: {len(loops)} loops, level {scale:.2f}')


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('source', type=Path)
  parser.add_argument('target', type=Path)
  parser.add_argument('--pitch', type=float, default=1.5, help='rpm label multiplier; 1.5 plays a third lower')
  parser.add_argument('--bass', type=float, default=8.0, help='dB below 100 Hz')
  parser.add_argument('--treble', type=float, default=-6.0, help='dB above 2.5 kHz')
  parser.add_argument('--off-mix', type=float, default=0.5, help='share of the on-throttle loop added to each off-throttle loop')
  parser.add_argument('--off-mid', type=float, default=-6.0, help='dB of 300 Hz-800 Hz on off-throttle loops')
  args = parser.parse_args()
  if args.target.exists():
    parser.error(f'{args.target} already exists')
  lower(args.source, args.target, args.pitch, args.bass, args.treble, args.off_mix, args.off_mid)


if __name__ == '__main__':
  main()
