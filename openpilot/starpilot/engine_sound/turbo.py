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
BOV_LEVEL = 0.55                  # A-weighted, about 5 dB over the coasting engine and just under it at full throttle
BOV_SECONDS = (0.6, 1.3)          # release length from just enough boost to full boost
# The release follows a recording the user picked. Its spectrum, measured in four stages (the opening whump, the rush,
# the stutter, the tail) as dB per Hz in third-octave bands, shapes the noise; from 0.45 s it stutters about 11.5 times
# a second (80 ms between pulses, slowing to 96 ms, 7 dB deep).
SHAPE_HZ = (63.0, 100.0, 160.0, 250.0, 400.0, 630.0, 1000.0, 1600.0, 2500.0, 4000.0, 6300.0, 10000.0)
SHAPE_SECONDS = (0.075, 0.3, 0.725, 1.15)
SHAPE_DB = (
  (-23.0, -22.3, -12.0, -4.2, -17.7, -21.4, -29.6, -25.8, -31.3, -34.0, -24.9, -35.8),
  (-29.5, -22.9, -13.2, -5.8, -7.1, -14.9, -17.2, -16.4, -19.2, -24.3, -19.0, -25.3),
  (-25.6, -17.7, -14.2, 0.0, -3.3, -10.7, -15.9, -17.3, -20.8, -22.9, -20.0, -25.3),
  (-22.5, -21.7, -14.8, -14.7, -8.5, -26.7, -29.5, -29.4, -30.6, -33.5, -30.0, -40.1),
)
LOW_CUT_HZ = 90.0                 # below this the recording carries the engine, not the valve
FLUTTER_START = 0.45
FLUTTER_PERIOD = (0.08, 0.096)
FLUTTER_DEPTH_DB = 7.0
WHISTLE_HZ = (1400.0, 3200.0)     # whistle pitch from no boost to full boost
WHISTLE_LEVEL = 0.045             # with the intake rush, about 8 dB under the engine on boost, A-weighted
WHOOSH_LEVEL = 0.038
VARIANTS = 6


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
  """Noise given the recording's spectrum stage by stage, then its stutter and decay."""
  frame, hop = 2048, 512
  length = int(seconds * sample_rate) + frame
  noise = rng.standard_normal(length)
  window = np.hanning(frame)
  freq = np.fft.rfftfreq(frame, 1 / sample_rate)
  log_freq = np.log2(np.maximum(freq, 20.0))
  low_cut = np.where(freq < LOW_CUT_HZ, -12.0 * np.log2(LOW_CUT_HZ / np.maximum(freq, 20.0)), 0.0)
  shape = np.array(SHAPE_DB)
  out = np.zeros(length)
  norm = np.zeros(length)
  for at in range(0, length - frame, hop):
    t = (at + frame / 2) / sample_rate
    stage = [np.interp(t, SHAPE_SECONDS, shape[:, band]) for band in range(len(SHAPE_HZ))]
    gain = 10 ** ((np.interp(log_freq, np.log2(SHAPE_HZ), stage) + low_cut) / 20)
    out[at:at + frame] += np.fft.irfft(np.fft.rfft(noise[at:at + frame] * window) * gain, frame) * window
    norm[at:at + frame] += window ** 2
  out = out[:length - frame] / np.maximum(norm[:length - frame], 1e-3)
  t = np.arange(len(out)) / sample_rate
  decay = np.where(t < 0.9 * seconds / BOV_SECONDS[1], 1.0, np.exp(-(t - 0.9 * seconds / BOV_SECONDS[1]) / 0.18))
  envelope = np.minimum(t / 0.003, 1.0) * decay * _flutter(rng, t)
  envelope[-512:] *= np.linspace(1.0, 0.0, 512)
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
