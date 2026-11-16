"""Overrun exhaust pops: burble, pops and crackle after a lift from load at speed, and on downshift blips.

Each pop is a one-shot from a library built once at start; firing only mixes samples, so the cost per block is small.
"""
from __future__ import annotations

import numpy as np

from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE

LIBRARY_SIZE = 12                 # per kind
LIFT_FROM = 0.2                   # load a lift must come down from
OVERRUN = 0.08                    # at or below this load the engine is on overrun
RESUME = 0.15                     # back on the throttle ends the pops
MIN_RPM_SHARE = 0.2               # share of redline below which nothing pops; coast-down downshifts land just above it
WINDOW_SECONDS = (0.7, 2.4)       # pop window after a lift, short at low revs and long from high revs and full load
BLIP_RPM_PER_SECOND = 1500.0      # rpm climbing this fast with no load is a downshift blip
BLIP_EXTEND_SECONDS = 0.6
# Timing from a recording of a real M3 on overrun (10-15 short thumps a second, almost nothing above 1 kHz);
# pitched about a third lower and played louder than recorded, which carries better on car speakers.
RATE = 14.0                       # pops per second at the start of a strong window
KINDS = ('burble', 'pop', 'crackle')
KIND_SHARES = (0.45, 0.35, 0.2)
LEVEL = 1.0
TOP_HZ = 1000.0


def _resonator(excitation: np.ndarray, frequency: float, q: float, sample_rate: int) -> np.ndarray:
  """Two-pole band-pass applied in the frequency domain; padding keeps the decay from wrapping."""
  size = 1 << int(np.ceil(np.log2(len(excitation) * 2)))
  w = 2 * np.pi * np.fft.rfftfreq(size, 1 / sample_rate) / sample_rate
  w0 = 2 * np.pi * frequency / sample_rate
  alpha = np.sin(w0) / (2 * q)
  z = np.exp(-1j * w)
  response = (alpha - alpha * z ** 2) / ((1 + alpha) - 2 * np.cos(w0) * z + (1 - alpha) * z ** 2)
  return np.fft.irfft(np.fft.rfft(excitation, size) * response, size)[:len(excitation)]


def _low_pass(sample: np.ndarray, sample_rate: int) -> np.ndarray:
  size = 1 << int(np.ceil(np.log2(len(sample) * 2)))
  freq = np.fft.rfftfreq(size, 1 / sample_rate)
  return np.fft.irfft(np.fft.rfft(sample, size) / np.sqrt(1 + (freq / TOP_HZ) ** 8), size)[:len(sample)]


def _normalised(sample: np.ndarray, sample_rate: int) -> np.ndarray:
  sample = _low_pass(sample, sample_rate)
  fade = np.ones(len(sample))
  fade[-64:] = np.linspace(1.0, 0.0, 64)
  sample = sample * fade
  return (sample / np.max(np.abs(sample))).astype(np.float32)


def _thump(rng: np.random.Generator, sample_rate: int, centre: tuple[float, float], decay: tuple[float, float]) -> np.ndarray:
  length = int(0.08 * sample_rate)
  t = np.arange(length) / sample_rate
  burst = rng.standard_normal(length) * np.exp(-t / rng.uniform(*decay))
  return _resonator(burst, rng.uniform(*centre), rng.uniform(1.5, 3.0), sample_rate)


def _crackle(rng: np.random.Generator, sample_rate: int) -> np.ndarray:
  """A quick run of small pops, 25-60 ms apart as in the recording's densest stretches."""
  length = int(0.2 * sample_rate)
  out = np.zeros(length)
  at = 0
  for _ in range(rng.integers(2, 5)):
    pop = _thump(rng, sample_rate, (220.0, 450.0), (0.003, 0.006)) * rng.uniform(0.4, 1.0)
    if at + len(pop) > length:
      break
    out[at:at + len(pop)] += pop
    at += int(rng.uniform(0.025, 0.06) * sample_rate)
  return _normalised(out, sample_rate)


def library(sample_rate: int = SAMPLE_RATE, seed: int = 3) -> dict[str, list[np.ndarray]]:
  rng = np.random.default_rng(seed)
  return {'burble': [_normalised(_thump(rng, sample_rate, (120.0, 210.0), (0.008, 0.016)), sample_rate) for _ in range(LIBRARY_SIZE)],
          'pop': [_normalised(_thump(rng, sample_rate, (190.0, 320.0), (0.004, 0.009)), sample_rate) for _ in range(LIBRARY_SIZE)],
          'crackle': [_crackle(rng, sample_rate) for _ in range(LIBRARY_SIZE)]}


class Pops:
  def __init__(self, intensity: float, idle_rpm: float, redline_rpm: float, sample_rate: int = SAMPLE_RATE, seed: int = 5):
    self.intensity = intensity
    self.min_rpm = max(idle_rpm, MIN_RPM_SHARE * redline_rpm)
    self.redline_rpm = redline_rpm
    self.sample_rate = sample_rate
    self.samples = library(sample_rate)
    self.longest = max(len(sample) for samples in self.samples.values() for sample in samples)
    self.tail = np.zeros(0, dtype=np.float32)
    self.rng = np.random.default_rng(seed)
    self.rpm: float | None = None
    self.load = 0.0
    self.peak_load = 0.0
    self.armed = False         # loaded since the last lift
    self.window = 0.0         # seconds of pops left
    self.length = 1.0          # length of the current window
    self.strength = 0.0

  def _rpm_share(self, rpm: float) -> float:
    return min(max((rpm - self.min_rpm) / (self.redline_rpm - self.min_rpm), 0.0), 1.0)

  def _blip(self, rpm: float, load: float, dt: float) -> bool:
    return self.rpm is not None and load <= OVERRUN and (rpm - self.rpm) / dt >= BLIP_RPM_PER_SECOND and rpm >= self.min_rpm

  def _update(self, rpm: float, load: float, dt: float, blip: bool) -> None:
    self.peak_load = max(load, self.peak_load * np.exp(-dt / 1.5))
    self.armed = self.armed or load > LIFT_FROM
    if load >= RESUME or rpm < 0.8 * self.min_rpm:
      self.window = 0.0
    elif load <= OVERRUN and self.armed and rpm >= self.min_rpm:
      self.armed = False
      share = self._rpm_share(rpm) * self.peak_load
      self.length = self.window = WINDOW_SECONDS[0] + (WINDOW_SECONDS[1] - WINDOW_SECONDS[0]) * share
      self.strength = 0.4 + 0.6 * share
    elif blip:
      self.window = max(self.window, BLIP_EXTEND_SECONDS)
      self.length = max(self.length, self.window)
      self.strength = max(self.strength, 0.7)
    else:
      self.window = max(self.window - dt, 0.0)
    self.rpm, self.load = rpm, load

  def render(self, rpm: float, load: float, frames: int, volume: float) -> np.ndarray:
    dt = frames / self.sample_rate
    blip = self._blip(rpm, load, dt)
    self._update(rpm, load, dt, blip)
    out = np.zeros(frames + self.longest, dtype=np.float32)
    out[:len(self.tail)] += self.tail
    if self.window > 0.0 and self.intensity > 0.0 and volume > 0.0:
      envelope = self.strength * (self.window / self.length) ** 0.7
      expected = RATE * envelope * (0.6 + 0.4 * self._rpm_share(rpm)) * dt + (0.8 if blip else 0.0)
      for _ in range(self.rng.poisson(expected)):
        kind = 'pop' if blip else KINDS[self.rng.choice(len(KINDS), p=KIND_SHARES)]
        sample = self.samples[kind][self.rng.integers(LIBRARY_SIZE)]
        start = int(self.rng.integers(frames))
        level = LEVEL * self.intensity * volume * envelope ** 0.3 * self.rng.uniform(0.5, 1.0)
        out[start:start + len(sample)] += level * sample
        blip = False
    self.tail = out[frames:frames + self.longest].copy()
    if not self.tail.any():
      self.tail = self.tail[:0]
    return out[:frames]
