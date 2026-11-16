"""Turbocharger: a spool whistle that follows boost, and a blow-off valve release when the throttle closes on boost.

Boost builds with load once the engine is spinning fast enough, with lag. Lifting while boosted fires the blow-off valve,
which vents the boost. The valve sounds are built once at start, so firing one only mixes a sample.
"""
from __future__ import annotations

import numpy as np

from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE

SPOOL_SHARE = (0.15, 0.4)         # share of redline where boost starts and where it can be full
SPOOL_SECONDS = 0.8               # lag while boost builds
BLEED_SECONDS = 0.35              # boost falling on a part lift that does not open the valve
VENT_SECONDS = 0.08               # boost falling through the open valve
LOAD_CURVE = 0.5                  # an EV pulls on little pedal, so modest load still makes boost
LIFT_FROM = 0.3                   # load the throttle must come down from
CLOSED = 0.1                      # at or below this load the throttle counts as closed
CLOSED_SECONDS = 0.1              # longer than a rev-limiter cut, which must not open the valve
BOV_FROM = 0.3                    # boost needed for the valve to open
BOV_LEVEL = 1.0
BOV_SECONDS = (0.6, 1.3)          # release length from just enough boost to full boost
# The release follows a recording the user picked: a low whump as the valve opens, a mid-range rush, then from
# 0.45 s a stutter about 11.5 times a second (75 ms between pulses, slowing to 92 ms, 6-8 dB deep) as it settles low.
FLUTTER_START = 0.45
FLUTTER_PERIOD = (0.08, 0.096)
FLUTTER_DEPTH_DB = 7.0
WHISTLE_HZ = (1400.0, 3200.0)     # whistle pitch from no boost to full boost
WHISTLE_LEVEL = 0.07
WHOOSH_LEVEL = 0.06
VARIANTS = 6


def _band(freq: np.ndarray, centre: float, octaves: float) -> np.ndarray:
  return np.exp(-0.5 * (np.log2(np.maximum(freq, 1.0) / centre) / octaves) ** 2)


def _ramp(t: float, points: tuple[tuple[float, float], ...]) -> float:
  return float(np.interp(t, [p[0] for p in points], [p[1] for p in points]))


def _flutter(rng: np.random.Generator, t: np.ndarray) -> np.ndarray:
  """Gain that pulses as the valve chatters: unity on each pulse, FLUTTER_DEPTH_DB down between them."""
  floor = 10 ** (-FLUTTER_DEPTH_DB / 20)
  gain = np.ones(len(t))
  at, pulses = FLUTTER_START, []
  while at < t[-1]:
    share = min((at - FLUTTER_START) / (t[-1] - FLUTTER_START), 1.0)
    period = (FLUTTER_PERIOD[0] + (FLUTTER_PERIOD[1] - FLUTTER_PERIOD[0]) * share) * rng.uniform(0.92, 1.08)
    pulses.append((at, period))
    at += period
  inside = t >= FLUTTER_START
  gain[inside] = floor
  for start, period in pulses:
    width = 0.6 * period
    sel = (t >= start) & (t < start + width)
    gain[sel] = floor + (1 - floor) * np.sin(np.pi * (t[sel] - start) / width) ** 2
  onset = np.clip((t - FLUTTER_START + 0.05) / 0.05, 0.0, 1.0)
  return 1.0 - onset * (1.0 - gain)


def _release(rng: np.random.Generator, seconds: float, sample_rate: int) -> np.ndarray:
  """Noise shaped frame by frame: a low whump as the valve opens, a mid rush, then a chattering low tail."""
  frame, hop = 1024, 256
  length = int(seconds * sample_rate) + frame
  noise = rng.standard_normal(length)
  window = np.hanning(frame)
  freq = np.fft.rfftfreq(frame, 1 / sample_rate)
  low = _band(freq, rng.uniform(320, 400), 0.6)
  high = _band(freq, 5000.0, 0.8)
  mid_start, mid_end = rng.uniform(1200, 1500), rng.uniform(750, 900)
  out = np.zeros(length)
  norm = np.zeros(length)
  for at in range(0, length - frame, hop):
    t = at / sample_rate
    mid = _band(freq, mid_start * (mid_end / mid_start) ** (t / seconds), 0.9)
    low_weight = _ramp(t, ((0.0, 1.4), (0.12, 1.3), (0.3, 0.4), (0.6, 0.6), (0.95, 1.0)))
    mid_weight = _ramp(t, ((0.0, 0.1), (0.15, 0.7), (0.24, 1.0), (0.6, 1.0), (1.0, 0.35)))
    gain = low_weight * low + mid_weight * mid + 0.2 * mid_weight * high
    out[at:at + frame] += np.fft.irfft(np.fft.rfft(noise[at:at + frame] * window) * gain, frame) * window
    norm[at:at + frame] += window ** 2
  out = out[:length - frame] / np.maximum(norm[:length - frame], 1e-3)
  t = np.arange(len(out)) / sample_rate
  decay = np.where(t < 0.9 * seconds / BOV_SECONDS[1], 1.0, np.exp(-(t - 0.9 * seconds / BOV_SECONDS[1]) / 0.18))
  envelope = np.minimum(t / 0.003, 1.0) * decay * _flutter(rng, t)
  envelope[-256:] *= np.linspace(1.0, 0.0, 256)
  out = out * envelope
  return (out / np.max(np.abs(out))).astype(np.float32)


def _whoosh(sample_rate: int, seconds: float = 2.0, seed: int = 9) -> np.ndarray:
  noise = np.random.default_rng(seed).standard_normal(int(seconds * sample_rate))
  freq = np.fft.rfftfreq(len(noise), 1 / sample_rate)
  shaped = np.fft.irfft(np.fft.rfft(noise) * np.exp(-0.5 * (np.log2(np.maximum(freq, 1.0) / 1300.0) / 0.8) ** 2), len(noise))
  return (shaped / np.max(np.abs(shaped))).astype(np.float32)


class Turbo:
  def __init__(self, intensity: float, idle_rpm: float, redline_rpm: float, sample_rate: int = SAMPLE_RATE, seed: int = 7):
    self.intensity = intensity
    self.spool = (SPOOL_SHARE[0] * redline_rpm, SPOOL_SHARE[1] * redline_rpm)
    self.sample_rate = sample_rate
    rng = np.random.default_rng(seed)
    self.releases = [_release(rng, BOV_SECONDS[1], sample_rate) for _ in range(VARIANTS)]
    self.longest = max(len(release) for release in self.releases)
    self.noise = _whoosh(sample_rate)
    self.noise_at = 0
    self.rng = rng
    self.tail = np.zeros(0, dtype=np.float32)
    self.boost = 0.0
    self.load = 0.0
    self.armed = False
    self.closed = 0.0
    self.venting = False
    self.phase = 0.0
    self.whistle_hz = WHISTLE_HZ[0]

  def _target(self, rpm: float, load: float) -> float:
    spool = min(max((rpm - self.spool[0]) / (self.spool[1] - self.spool[0]), 0.0), 1.0)
    return spool * max(load, 0.0) ** LOAD_CURVE

  def render(self, rpm: float, load: float, frames: int, volume: float) -> np.ndarray:
    dt = frames / self.sample_rate
    self.armed = self.armed or load > LIFT_FROM
    self.closed = self.closed + dt if load <= CLOSED else 0.0
    release = None
    if self.closed >= CLOSED_SECONDS - 1e-9 and self.armed:
      self.armed = False
      if self.boost >= BOV_FROM:
        release = self.boost
        self.venting = True
    if load > CLOSED:
      self.venting = False
    target = self._target(rpm, load)
    seconds = SPOOL_SECONDS if target > self.boost else VENT_SECONDS if self.venting else BLEED_SECONDS
    start = self.boost
    self.boost = target + (self.boost - target) * np.exp(-dt / seconds)
    self.load = load

    out = np.zeros(frames + self.longest, dtype=np.float32)
    out[:len(self.tail)] += self.tail
    level = self.intensity * volume
    if level > 0.0:
      ramp = (start + (self.boost - start) * np.arange(1, frames + 1) / frames).astype(np.float32)
      hz = WHISTLE_HZ[0] + (WHISTLE_HZ[1] - WHISTLE_HZ[0]) * ramp
      phase = self.phase + np.cumsum(hz) / self.sample_rate
      self.phase = float(phase[-1] % 1.0)
      index = (self.noise_at + np.arange(frames)) % len(self.noise)
      self.noise_at = int((self.noise_at + frames) % len(self.noise))
      out[:frames] += level * (WHISTLE_LEVEL * ramp ** 1.5 * np.sin(2 * np.pi * phase).astype(np.float32) +
                               WHOOSH_LEVEL * ramp * self.noise[index])
      if release is not None:
        sample = self.releases[self.rng.integers(VARIANTS)]
        share = min((release - BOV_FROM) / (1.0 - BOV_FROM), 1.0)
        size = int((BOV_SECONDS[0] + (BOV_SECONDS[1] - BOV_SECONDS[0]) * share) / BOV_SECONDS[1] * len(sample))
        fade = np.ones(size, dtype=np.float32)
        fade[-min(size, 2048):] = np.linspace(1.0, 0.0, min(size, 2048))
        out[:size] += level * BOV_LEVEL * (0.6 + 0.4 * share) * sample[:size] * fade
    self.tail = out[frames:frames + self.longest].copy()
    if not self.tail.any():
      self.tail = self.tail[:0]
    return out[:frames]
