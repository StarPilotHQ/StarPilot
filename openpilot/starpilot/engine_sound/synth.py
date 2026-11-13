"""Wavetable renderer for 'synth' profiles: phase-continuous across blocks, mono float output in -1..1."""
from __future__ import annotations

import numpy as np

from openpilot.starpilot.engine_sound.profiles import Profile

SAMPLE_RATE = 48000
TABLE_SIZE = 4096
NOISE_SIZE = SAMPLE_RATE * 2


def _table(partials, column: int, rng: np.random.Generator) -> np.ndarray:
  phase = np.arange(TABLE_SIZE) / TABLE_SIZE
  table = np.zeros(TABLE_SIZE)
  for partial in partials:
    table += partial[column] * np.sin(2 * np.pi * (partial[0] * phase + rng.random()))
  peak = np.max(np.abs(table))
  return (table / peak if peak > 0 else table).astype(np.float32)


class Synth:
  def __init__(self, profile: Profile, sample_rate: int = SAMPLE_RATE):
    self.profile = profile
    self.sample_rate = sample_rate
    self.idle_table = _table(profile.partials, 1, np.random.default_rng(1))
    self.load_table = _table(profile.partials, 2, np.random.default_rng(1))
    noise = np.random.default_rng(2).standard_normal(NOISE_SIZE)
    # One-pole low-pass (as a short FIR) keeps the hiss out of the top octave.
    smooth = np.convolve(noise, 0.25 * 0.75 ** np.arange(48), mode='same')
    self.noise = (smooth / np.max(np.abs(smooth))).astype(np.float32)
    self.noise_at = 0
    self.phase = 0.0
    self.detuned_phase = 0.0
    self.tremolo_phase = 0.0
    self.frequency = profile.idle_rpm * profile.pitch
    self.load = 0.0
    self.gain = 0.0

  def _lookup(self, table: np.ndarray, phase: np.ndarray) -> np.ndarray:
    position = (phase % 1.0) * TABLE_SIZE
    index = position.astype(np.int32)
    fraction = (position - index).astype(np.float32)
    return table[index % TABLE_SIZE] * (1 - fraction) + table[(index + 1) % TABLE_SIZE] * fraction

  def _voice(self, phase: np.ndarray, load: np.ndarray) -> np.ndarray:
    return self._lookup(self.idle_table, phase) * (1 - load) + self._lookup(self.load_table, phase) * load

  def render(self, rpm: float, load: float, frames: int, volume: float = 1.0) -> np.ndarray:
    p = self.profile
    load = min(max(load, 0.0), 1.0)
    ramp = np.arange(1, frames + 1, dtype=np.float64) / frames
    frequency = self.frequency + (rpm * p.pitch - self.frequency) * ramp
    load_ramp = (self.load + (load - self.load) * ramp).astype(np.float32)
    target_gain = volume * (p.gain[0] + (p.gain[1] - p.gain[0]) * load)
    gain = (self.gain + (target_gain - self.gain) * ramp).astype(np.float32)

    phase = self.phase + np.cumsum(frequency) / self.sample_rate
    out = self._voice(phase, load_ramp)
    if p.detune:
      detuned = self.detuned_phase + np.cumsum(frequency * (1 + p.detune)) / self.sample_rate
      out = 0.5 * (out + self._voice(detuned, load_ramp))
      self.detuned_phase = float(detuned[-1] % 1.0)
    if p.whine_level:
      out = out + p.whine_level * load_ramp * np.sin(2 * np.pi * p.whine_ratio * phase).astype(np.float32)

    level = p.noise[0] + (p.noise[1] - p.noise[0]) * load_ramp
    if level.any():
      index = (self.noise_at + np.arange(frames)) % NOISE_SIZE
      noise = self.noise[index]
      self.noise_at = int((self.noise_at + frames) % NOISE_SIZE)
      if p.noise_pulse:
        pulse = np.maximum(np.sin(2 * np.pi * p.pulse_partial * phase), 0.0).astype(np.float32) ** 6
        noise = noise * (1 - p.noise_pulse + p.noise_pulse * 4.0 * pulse)
      out = out + level * noise

    if p.tremolo[1]:
      tremolo = self.tremolo_phase + np.arange(1, frames + 1) * (p.tremolo[0] / self.sample_rate)
      out = out * (1 - p.tremolo[1] * (0.5 + 0.5 * np.sin(2 * np.pi * tremolo))).astype(np.float32)
      self.tremolo_phase = float(tremolo[-1] % 1.0)

    self.phase = float(phase[-1] % 1.0)
    self.frequency, self.load, self.gain = float(frequency[-1]), load, target_gain
    return np.tanh(0.8 * out * gain).astype(np.float32)


def to_pcm(samples: np.ndarray) -> bytes:
  return (samples * 32767.0).astype('<i2').tobytes()
