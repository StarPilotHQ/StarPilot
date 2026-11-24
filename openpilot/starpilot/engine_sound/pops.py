"""Overrun exhaust pops: burble, pops and crackle after a lift from load at speed, and on downshift blips.

Each pop is a one-shot from a library built once at start; firing only mixes samples, so the cost per block is small.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE

GRAINS = Path(__file__).with_name('banks') / 'pops' / 'burble_grains.wav'
GRAIN_SECONDS = 0.052

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

# Burble mode: below the crackle range, bursts of pops recorded from a real V8 on overrun, with the recording's timing,
# then a low rumble. Single pops at low revs sound like isolated clicks on car speakers.
SLOW_SHARE = 0.25                 # lifts below this share (revs x load) burble instead of crackling
BURBLE_LIFT_SECONDS = 1.2
BURBLE_GAP_SECONDS = 0.037        # median gap between pops, lognormal spread
BURBLE_GAP_SPREAD = 0.7
BURBLE_GAP_RANGE = (0.011, 0.16)
BURBLE_FADE_FROM = 0.7            # share of a burst after which it thins out
GRAIN_LEVEL = 0.175
DOWNSHIFT_CHANCE = 0.3            # never two downshifts in a row
DOWNSHIFT_MAX = 3                 # per slow-down
DOWNSHIFT_SECONDS = 0.35
DOWNSHIFT_TAPER = 0.6             # each later burst of a slow-down is this much shorter
DOWNSHIFT_FADE = 0.75             # and this much quieter
RUMBLE_SECONDS = (1.8, 1.0)       # after a lift, after a downshift
RUMBLE_LEVEL = 0.0118
RUMBLE_BAND_HZ = (150.0, 700.0)
RUMBLE_HZ = 15.6
RUMBLE_WOBBLE_DB = 2.0


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


def load_grains(path: Path = GRAINS, sample_rate: int = SAMPLE_RATE) -> list[np.ndarray]:
  """Recorded pops of GRAIN_SECONDS each, back to back; none if the recording is missing or unreadable."""
  try:
    with wave.open(str(path)) as source:
      if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != sample_rate:
        return []
      audio = np.frombuffer(source.readframes(source.getnframes()), '<i2').astype(np.float32) / 32768.0
  except (OSError, EOFError, wave.Error):
    return []
  size = int(GRAIN_SECONDS * sample_rate)
  return [audio[i:i + size] for i in range(0, len(audio) - size + 1, size)]


def _band_noise(sample_rate: int, band: tuple[float, float], seconds: float, seed: int) -> np.ndarray:
  noise = np.random.default_rng(seed).standard_normal(int(seconds * sample_rate))
  freq = np.fft.rfftfreq(len(noise), 1 / sample_rate)
  out = np.fft.irfft(np.fft.rfft(noise) * ((freq >= band[0]) & (freq <= band[1])), len(noise))
  return (out / np.sqrt(np.mean(out ** 2))).astype(np.float32)


def _lowered(sample: np.ndarray, pitch: float) -> np.ndarray:
  if pitch == 1.0:
    return sample
  return np.interp(np.arange(0, len(sample) - 1, pitch), np.arange(len(sample)), sample).astype(np.float32)


class Pops:
  def __init__(self, intensity: float, idle_rpm: float, redline_rpm: float, sample_rate: int = SAMPLE_RATE, seed: int = 5,
               burble: bool = False, pitch: float = 1.0):
    self.intensity = intensity
    self.min_rpm = max(idle_rpm, MIN_RPM_SHARE * redline_rpm)
    self.redline_rpm = redline_rpm
    self.sample_rate = sample_rate
    self.samples = {kind: [_lowered(sample, pitch) for sample in samples] for kind, samples in library(sample_rate).items()}
    self.grains = [_lowered(grain, pitch) for grain in load_grains(sample_rate=sample_rate)] if burble else []
    self.burble = bool(self.grains)
    self.longest = max(len(sample) for samples in (*self.samples.values(), self.grains) for sample in samples)
    self.tail = np.zeros(0, dtype=np.float32)
    self.rng = np.random.default_rng(seed)
    self.rpm: float | None = None
    self.load = 0.0
    self.peak_load = 0.0
    self.armed = False         # loaded since the last lift
    self.window = 0.0         # seconds of pops left
    self.length = 1.0          # length of the current window
    self.strength = 0.0
    self.blipping = False
    self.burst = 0.0           # seconds of burble burst left
    self.burst_length = 1.0
    self.burst_level = 1.0
    self.next_grain = 0.0      # seconds into the coming block
    self.from_downshift = False
    self.downshift_bursts = 0
    self.last_downshift_burst = False
    self.rumble = 0.0          # seconds of rumble left
    self.rumble_length = 1.0
    self.rumble_after = 0.0
    self.rumble_noise = _band_noise(sample_rate, RUMBLE_BAND_HZ, 4.0, seed) if self.burble else np.zeros(1, np.float32)
    self.rumble_at = 0
    self.rumble_phase = 0.0

  def _rpm_share(self, rpm: float) -> float:
    return min(max((rpm - self.min_rpm) / (self.redline_rpm - self.min_rpm), 0.0), 1.0)

  def _blip(self, rpm: float, load: float, dt: float) -> bool:
    return self.rpm is not None and load <= OVERRUN and (rpm - self.rpm) / dt >= BLIP_RPM_PER_SECOND and rpm >= self.min_rpm

  def _start_burst(self, seconds: float, level: float, rumble_seconds: float, downshift: bool) -> None:
    if self.burst <= 0.0:
      self.next_grain = 0.0
      self.burst_length = self.burst = seconds
    elif seconds > self.burst:
      self.burst_length += seconds - self.burst
      self.burst = seconds
    self.rumble = 0.0
    self.burst_level, self.rumble_after, self.from_downshift = level, rumble_seconds, downshift

  def _downshift(self) -> None:
    if self.burst > 0.0 and not self.from_downshift:
      return
    fire = (self.downshift_bursts < DOWNSHIFT_MAX and not self.last_downshift_burst
            and self.rng.random() < DOWNSHIFT_CHANCE)
    self.last_downshift_burst = fire
    if fire:
      taper = DOWNSHIFT_TAPER ** self.downshift_bursts
      self.downshift_bursts += 1
      self._start_burst(DOWNSHIFT_SECONDS * taper, DOWNSHIFT_FADE ** self.downshift_bursts, RUMBLE_SECONDS[1] * taper, True)

  def _update(self, rpm: float, load: float, dt: float, blip: bool) -> None:
    self.peak_load = max(load, self.peak_load * np.exp(-dt / 1.5))
    self.armed = self.armed or load > LIFT_FROM
    if load >= RESUME or rpm < 0.8 * self.min_rpm:
      self.window = self.burst = self.rumble = 0.0
      self.downshift_bursts, self.last_downshift_burst = 0, False
    elif load <= OVERRUN and self.armed and rpm >= self.min_rpm:
      self.armed = False
      self.downshift_bursts, self.last_downshift_burst = 0, False
      share = self._rpm_share(rpm) * self.peak_load
      if self.burble and share < SLOW_SHARE:
        self.window = 0.0
        self._start_burst(BURBLE_LIFT_SECONDS, 1.0, RUMBLE_SECONDS[0], False)
      else:
        self.length = self.window = WINDOW_SECONDS[0] + (WINDOW_SECONDS[1] - WINDOW_SECONDS[0]) * share
        self.strength = 0.4 + 0.6 * share
    elif blip and self.burble:
      if not self.blipping:
        self._downshift()
      self.window = max(self.window - dt, 0.0)
    elif blip:
      self.window = max(self.window, BLIP_EXTEND_SECONDS)
      self.length = max(self.length, self.window)
      self.strength = max(self.strength, 0.7)
    else:
      self.window = max(self.window - dt, 0.0)
    self.blipping = blip
    self.rpm, self.load = rpm, load

  def _render_burst(self, out: np.ndarray, dt: float, gain: float) -> None:
    while self.next_grain < dt:
      progress = 1.0 - (self.burst - self.next_grain) / self.burst_length
      thin = 1.0 if progress < BURBLE_FADE_FROM else max(1.0 - (progress - BURBLE_FADE_FROM) / (1.0 - BURBLE_FADE_FROM) * 0.7, 0.3)
      grain = self.grains[self.rng.integers(len(self.grains))]
      start = int(self.next_grain * self.sample_rate)
      out[start:start + len(grain)] += GRAIN_LEVEL * self.burst_level * gain * thin * self.rng.uniform(0.7, 1.0) * grain
      gap = BURBLE_GAP_SECONDS * np.exp(BURBLE_GAP_SPREAD * self.rng.standard_normal())
      self.next_grain += float(np.clip(gap, *BURBLE_GAP_RANGE))
    self.next_grain -= dt
    self.burst -= dt
    if self.burst <= 0.0:
      self.burst = 0.0
      self.rumble_length = self.rumble = self.rumble_after

  def _render_rumble(self, out: np.ndarray, frames: int, dt: float, gain: float) -> None:
    index = (self.rumble_at + np.arange(frames)) % len(self.rumble_noise)
    self.rumble_at = int(index[-1] + 1)
    wobble = 10 ** (RUMBLE_WOBBLE_DB * np.sin(2 * np.pi * (self.rumble_phase + RUMBLE_HZ * np.arange(frames) / self.sample_rate)) / 20)
    self.rumble_phase = (self.rumble_phase + RUMBLE_HZ * dt) % 1.0
    envelope = (self.rumble / self.rumble_length) ** 1.5
    out[:frames] += RUMBLE_LEVEL * self.burst_level * gain * envelope * wobble * self.rumble_noise[index]
    self.rumble = max(self.rumble - dt, 0.0)

  def render(self, rpm: float, load: float, frames: int, volume: float) -> np.ndarray:
    dt = frames / self.sample_rate
    blip = self._blip(rpm, load, dt)
    self._update(rpm, load, dt, blip)
    blip = blip and not self.burble
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
    if self.burst > 0.0:
      self._render_burst(out, dt, self.intensity * volume)
    if self.rumble > 0.0:
      self._render_rumble(out, frames, dt, self.intensity * volume)
    self.tail = out[frames:frames + self.longest].copy()
    if not self.tail.any():
      self.tail = self.tail[:0]
    return out[:frames]
