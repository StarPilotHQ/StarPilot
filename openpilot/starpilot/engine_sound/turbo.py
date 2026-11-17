"""Turbocharger: a spool whistle that follows boost, and a blow-off valve release when the throttle closes on boost.

Boost builds with load once the engine is spinning fast enough, with lag. Lifting while boosted fires the blow-off valve,
which vents the boost. The valve sound is a recording loaded once at start, so firing one only mixes a sample.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE

BLOWOFF = Path(__file__).with_name('banks') / 'turbo' / 'blowoff.wav'

SPOOL_SHARE = (0.15, 0.4)         # share of redline where boost starts and where it can be full
SPOOL_SECONDS = 1.1               # lag while boost builds
BLEED_SECONDS = 0.35              # boost falling on a part lift that does not open the valve
VENT_SECONDS = 0.35               # boost falling through the open valve; the whistle fades into the release
PEDAL_DEADZONE = 0.1              # load below this makes no boost at all
LOAD_CURVE = 0.5                  # an EV pulls on little pedal, so modest load past the dead zone still makes boost
LIFT_FROM = 0.3                   # load the throttle must come down from
CLOSED = 0.1                      # at or below this load the throttle counts as closed
CLOSED_SECONDS = 0.1              # longer than a rev-limiter cut, which must not open the valve
BOV_FROM = 0.3                    # boost needed for the valve to open
BOV_LEVEL = 0.267                 # A-weighted, about 2 dB over the coasting engine before it dips
BOV_SECONDS = 0.8                 # just enough boost plays this much of the recording; full boost plays all of it
BOV_SPEEDS = (0.97, 1.0, 1.03)    # each release plays one of these, so repeats are not identical
# The release blends in rather than arriving as a separate clip: it fades in, sits a little darker, and the engine dips
# under it while it plays.
BOV_ATTACK_SECONDS = 0.08         # fade-in at the start of a release
BOV_HIGH_CUT_DB = -6.0            # level change above 3 kHz, so the release sits inside the engine's tone
ENGINE_DUCK_DB = 5.0              # the engine dips by this much while a release plays
DUCK_SECONDS = (0.06, 0.4)        # how fast the engine dips and comes back
# A spool whistle is air, not a pure tone: mostly a band of noise around the pitch, with a little tone in it.
WHISTLE_HZ = (1400.0, 3200.0)     # whistle pitch from no boost to full boost
WHISTLE_CURVE = 3.0               # faint until boost is well built
WHISTLE_TONE = 0.15               # share of the whistle that is a plain tone
WHISTLE_WIDTH_HZ = 250.0          # width of the noise band around the pitch
WHISTLE_LEVEL = 0.045             # with the intake rush, about 8 dB under the engine on boost, A-weighted
WHOOSH_LEVEL = 0.038


def load_releases(path: Path = BLOWOFF, sample_rate: int = SAMPLE_RATE) -> list[np.ndarray]:
  """The recorded release at each of BOV_SPEEDS; none if the recording is missing or unreadable."""
  try:
    with wave.open(str(path)) as source:
      if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != sample_rate:
        return []
      audio = np.frombuffer(source.readframes(source.getnframes()), '<i2').astype(np.float32) / 32768.0
  except (OSError, EOFError, wave.Error):
    return []
  if not len(audio):
    return []
  if BOV_HIGH_CUT_DB:
    freq = np.fft.rfftfreq(len(audio), 1 / sample_rate)
    shelf = BOV_HIGH_CUT_DB * np.clip(np.log2(np.maximum(freq, 1.0) / 2000.0), 0.0, 1.0)
    audio = np.fft.irfft(np.fft.rfft(audio) * 10 ** (shelf / 20), len(audio)).astype(np.float32)
  index = np.arange(len(audio))
  return [np.interp(np.arange(0, len(audio) - 1, speed), index, audio).astype(np.float32) for speed in BOV_SPEEDS]


def _low_noise(sample_rate: int, width_hz: float, seconds: float, seed: int) -> np.ndarray:
  """Noise only below width_hz; multiplied onto a carrier it becomes a band that wide around the carrier."""
  noise = np.random.default_rng(seed).standard_normal(int(seconds * sample_rate))
  freq = np.fft.rfftfreq(len(noise), 1 / sample_rate)
  low = np.fft.irfft(np.fft.rfft(noise) * (freq < width_hz), len(noise))
  return (low / np.sqrt(np.mean(low ** 2))).astype(np.float32)


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
    self.releases = load_releases(sample_rate=sample_rate)
    self.longest = max((len(release) for release in self.releases), default=0)
    self.noise = _whoosh(sample_rate)
    self.band = (_low_noise(sample_rate, WHISTLE_WIDTH_HZ / 2, 2.0, 11), _low_noise(sample_rate, WHISTLE_WIDTH_HZ / 2, 2.0, 12))
    self.noise_at = 0
    self.rng = rng
    self.tail = np.zeros(0, dtype=np.float32)
    self.boost = 0.0
    self.load = 0.0
    self.armed = False
    self.closed = 0.0
    self.venting = False
    self.phase = 0.0
    self.playing = 0              # samples of the current release still to come
    self.duck = 1.0
    self.engine_gain = np.ones(1, dtype=np.float32)

  def _duck(self, frames: int) -> None:
    """Per-sample gain for the engine under this block: down by ENGINE_DUCK_DB while a release plays."""
    target = 10 ** (-ENGINE_DUCK_DB / 20) if self.playing > 0 else 1.0
    seconds = DUCK_SECONDS[0] if target < self.duck else DUCK_SECONDS[1]
    start = self.duck
    self.duck = target + (self.duck - target) * float(np.exp(-frames / self.sample_rate / seconds))
    self.engine_gain = (start + (self.duck - start) * np.arange(1, frames + 1) / frames).astype(np.float32)
    self.playing = max(self.playing - frames, 0)

  def _target(self, rpm: float, load: float) -> float:
    spool = min(max((rpm - self.spool[0]) / (self.spool[1] - self.spool[0]), 0.0), 1.0)
    pedal = max(load - PEDAL_DEADZONE, 0.0) / (1.0 - PEDAL_DEADZONE)
    return spool * pedal ** LOAD_CURVE

  def _whistle(self, boost: np.ndarray, index: np.ndarray) -> np.ndarray:
    hz = WHISTLE_HZ[0] + (WHISTLE_HZ[1] - WHISTLE_HZ[0]) * boost
    phase = self.phase + np.cumsum(hz) / self.sample_rate
    self.phase = float(phase[-1] % 1.0)
    carrier = 2 * np.pi * phase
    air = (self.band[0][index] * np.cos(carrier) + self.band[1][index] * np.sin(carrier)) / np.sqrt(2)
    out = WHISTLE_TONE * np.sin(carrier) + (1 - WHISTLE_TONE) * air
    return (out * boost ** WHISTLE_CURVE).astype(np.float32)

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
      index = (self.noise_at + np.arange(frames)) % len(self.noise)
      self.noise_at = int((self.noise_at + frames) % len(self.noise))
      out[:frames] += level * (WHISTLE_LEVEL * self._whistle(ramp, index % len(self.band[0])) +
                               WHOOSH_LEVEL * ramp ** 1.5 * self.noise[index])
      if release is not None and self.releases:
        sample = self.releases[self.rng.integers(len(self.releases))]
        share = min((release - BOV_FROM) / (1.0 - BOV_FROM), 1.0)
        size = min(len(sample), int((BOV_SECONDS + (len(sample) / self.sample_rate - BOV_SECONDS) * share) * self.sample_rate))
        fade = np.ones(size, dtype=np.float32)
        fade[-min(size, 4800):] = np.linspace(1.0, 0.0, min(size, 4800))
        if BOV_ATTACK_SECONDS > 0:
          attack = min(size, int(BOV_ATTACK_SECONDS * self.sample_rate))
          fade[:attack] *= np.linspace(0.0, 1.0, attack)
        out[:size] += level * BOV_LEVEL * (0.6 + 0.4 * share) * sample[:size] * fade
        self.playing = size
    self._duck(frames)
    self.tail = out[frames:frames + self.longest].copy()
    if not self.tail.any():
      self.tail = self.tail[:0]
    return out[:frames]
