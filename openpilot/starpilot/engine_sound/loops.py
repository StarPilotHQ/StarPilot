"""Loop renderer: seamless engine loops recorded at fixed rpm, pitched and crossfaded to the simulated rpm.

A bank is a directory (installed under BANK_ROOT or bundled in ./banks) holding bank.json and mono 16-bit WAV loops at the synth sample rate:
  {"version": 1, "layers": {"on": [{"rpm": 1000, "file": "on_1000.wav"}, ...], "off": [...]}}
"on" is played under load and "off" on overrun; a bank with only "on" uses it for both.
"""
from __future__ import annotations

import json
import os
import wave
from pathlib import Path

import numpy as np

from openpilot.starpilot.engine_sound.profiles import Profile
from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE

BANK_ROOT = Path(os.environ.get('STARPILOT_ENGINE_SOUND_ROOT', '/data/starpilot/engine_sounds'))
BUNDLED_ROOT = Path(__file__).with_name('banks')
BANK_FILE = 'bank.json'
LAYERS = ('on', 'off')
MAX_LOOP_SECONDS = 10.0
MAX_LOOPS = 64


class BankError(ValueError):
  pass


def read_loop(path: Path, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
  with wave.open(str(path), 'rb') as source:
    if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != sample_rate:
      raise BankError(f'{path.name} must be mono 16-bit {sample_rate} Hz')
    frames = source.getnframes()
    if not sample_rate // 10 <= frames <= MAX_LOOP_SECONDS * sample_rate:
      raise BankError(f'{path.name} must be 0.1–{MAX_LOOP_SECONDS:g} s long')
    return np.frombuffer(source.readframes(frames), dtype='<i2').astype(np.float32) / 32768.0


def load_bank(directory: Path, sample_rate: int = SAMPLE_RATE) -> dict[str, tuple[np.ndarray, list[np.ndarray]]]:
  """Returns {layer: (rpm points ascending, loops)}."""
  try:
    manifest = json.loads((directory / BANK_FILE).read_text())
  except (OSError, ValueError) as error:
    raise BankError(f'{directory.name}: unreadable {BANK_FILE}') from error
  layers = manifest.get('layers') if isinstance(manifest, dict) else None
  if manifest.get('version') != 1 or not isinstance(layers, dict) or 'on' not in layers or set(layers) - set(LAYERS):
    raise BankError(f'{directory.name}: needs version 1 and an "on" layer')
  bank = {}
  for layer, entries in layers.items():
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_LOOPS:
      raise BankError(f'{directory.name}: layer {layer} needs 1–{MAX_LOOPS} loops')
    loops = []
    for entry in entries:
      name = entry.get('file') if isinstance(entry, dict) else None
      rpm = entry.get('rpm') if isinstance(entry, dict) else None
      if not isinstance(name, str) or Path(name).name != name or isinstance(rpm, bool) or not isinstance(rpm, (int, float)) or rpm <= 0:
        raise BankError(f'{directory.name}: bad loop entry in {layer}')
      loops.append((float(rpm), read_loop(directory / name, sample_rate)))
    loops.sort(key=lambda item: item[0])
    points = np.array([rpm for rpm, _ in loops])
    if len(np.unique(points)) != len(points):
      raise BankError(f'{directory.name}: duplicate rpm in {layer}')
    bank[layer] = (points, [audio for _, audio in loops])
  bank.setdefault('off', bank['on'])
  return bank


def find_bank(name: str, root: Path | None = None) -> Path | None:
  """An installed bank wins over the bundled one of the same name, so a bank can be replaced without an update."""
  if not name or Path(name).name != name:
    return None
  for base in ((root,) if root is not None else (BANK_ROOT, BUNDLED_ROOT)):
    if (base / name / BANK_FILE).is_file():
      return base / name
  return None


class LoopSynth:
  def __init__(self, profile: Profile, bank: dict, sample_rate: int = SAMPLE_RATE):
    self.profile = profile
    self.bank = bank
    self.sample_rate = sample_rate
    self.positions = {layer: [0.0] * len(loops) for layer, (_, loops) in bank.items()}
    self.rpm = profile.idle_rpm
    self.load = 0.0
    self.gain = 0.0

  def _layer(self, layer: str, rpm: np.ndarray) -> np.ndarray:
    points, loops = self.bank[layer]
    middle = rpm[len(rpm) // 2]
    upper = int(np.clip(np.searchsorted(points, middle), 1, len(points) - 1)) if len(points) > 1 else 0
    lower = upper - 1 if len(points) > 1 else 0
    if lower == upper:
      return self._play(layer, lower, rpm)
    fade = np.clip((rpm - points[lower]) / (points[upper] - points[lower]), 0.0, 1.0)
    return (self._play(layer, lower, rpm) * np.cos(0.5 * np.pi * fade).astype(np.float32) +
            self._play(layer, upper, rpm) * np.sin(0.5 * np.pi * fade).astype(np.float32))

  def _play(self, layer: str, index: int, rpm: np.ndarray) -> np.ndarray:
    points, loops = self.bank[layer]
    loop = loops[index]
    position = self.positions[layer][index] + np.cumsum(rpm / points[index])
    self.positions[layer][index] = float(position[-1] % len(loop))
    position %= len(loop)
    whole = position.astype(np.int64)
    fraction = (position - whole).astype(np.float32)
    return loop[whole] * (1 - fraction) + loop[(whole + 1) % len(loop)] * fraction

  def render(self, rpm: float, load: float, frames: int, volume: float = 1.0) -> np.ndarray:
    p = self.profile
    load = min(max(load, 0.0), 1.0)
    ramp = np.arange(1, frames + 1, dtype=np.float64) / frames
    rpm_ramp = self.rpm + (rpm - self.rpm) * ramp
    load_ramp = (self.load + (load - self.load) * ramp).astype(np.float32)
    target_gain = volume * (p.gain[0] + (p.gain[1] - p.gain[0]) * load)
    gain = (self.gain + (target_gain - self.gain) * ramp).astype(np.float32)
    on = self._layer('on', rpm_ramp)
    if self.bank['off'] is self.bank['on']:
      out = on
    else:
      off = self._layer('off', rpm_ramp)
      out = on * np.sqrt(load_ramp) + off * np.sqrt(1 - load_ramp)
    self.rpm, self.load, self.gain = rpm, load, target_gain
    return np.tanh(out * gain).astype(np.float32)
