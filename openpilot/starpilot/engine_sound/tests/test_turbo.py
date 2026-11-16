from dataclasses import replace

import numpy as np

from openpilot.starpilot.engine_sound.loops import LoopSynth, load_bank
from openpilot.starpilot.engine_sound.profiles import PROFILES
from openpilot.starpilot.engine_sound.tests.test_loops import make_bank
from openpilot.starpilot.engine_sound.turbo import BOV_FROM, Turbo

FRAMES = 480


def run(turbo, steps, volume=1.0):
  return np.concatenate([turbo.render(rpm, load, FRAMES, volume) for rpm, load in steps])


def test_boost_lags_the_throttle_and_needs_revs():
  turbo = Turbo(1.0, 800.0, 6000.0)
  run(turbo, [(4000.0, 1.0)] * 10)
  early = turbo.boost
  run(turbo, [(4000.0, 1.0)] * 150)
  assert early < 0.25 < 0.8 < turbo.boost
  low = Turbo(1.0, 800.0, 6000.0)
  run(low, [(850.0, 1.0)] * 200)
  assert low.boost == 0.0


def test_lifting_on_boost_fires_the_valve_and_vents_it():
  turbo = Turbo(1.0, 800.0, 6000.0)
  run(turbo, [(4000.0, 1.0)] * 200)
  on_boost = np.abs(run(turbo, [(4000.0, 1.0)] * 20)).max()
  release = run(turbo, [(3800.0, 0.0)] * 160)
  assert np.abs(release).max() > 4 * on_boost
  assert turbo.boost < 0.1
  assert np.abs(release[-FRAMES * 10:]).max() < 0.5 * np.abs(release[FRAMES * 10:FRAMES * 20]).max()


def test_the_release_stutters_about_eleven_times_a_second():
  from openpilot.starpilot.engine_sound.turbo import BOV_SECONDS, FLUTTER_START, _release
  release = _release(np.random.default_rng(0), BOV_SECONDS[1], 48000).astype(np.float64)
  envelope = np.sqrt(np.convolve(release ** 2, np.ones(192) / 192, mode='same'))[::48]
  body = envelope[int(FLUTTER_START * 1000) + 50:1100]
  spectrum = np.abs(np.fft.rfft((body - body.mean()) * np.hanning(len(body)), 1 << 14))
  freq = np.fft.rfftfreq(1 << 14, 1 / 1000)
  band = (freq > 4) & (freq < 40)
  assert 10.0 < freq[band][np.argmax(spectrum[band])] < 13.5


def test_a_rev_limiter_cut_does_not_open_the_valve():
  turbo = Turbo(1.0, 800.0, 6000.0)
  on_boost = np.abs(run(turbo, [(5800.0, 1.0)] * 200)).max()
  bouncing = run(turbo, ([(5800.0, 0.0)] * 7 + [(5800.0, 1.0)] * 5) * 10)
  assert not turbo.venting and np.abs(bouncing).max() < 1.5 * on_boost


def test_no_valve_without_boost_or_without_a_lift():
  quick = Turbo(1.0, 800.0, 6000.0)
  run(quick, [(4000.0, 1.0)] * 5)
  assert quick.boost < BOV_FROM
  assert np.abs(run(quick, [(3800.0, 0.0)] * 40)).max() < 0.05
  cruise = Turbo(1.0, 800.0, 6000.0)
  steady = run(cruise, [(3000.0, 0.6)] * 300)
  assert np.abs(steady).max() < 0.2


def test_quiet_at_zero_volume_and_engines_without_a_turbo_are_unchanged(tmp_path):
  assert not run(Turbo(1.0, 800.0, 6000.0), [(4000.0, 1.0)] * 200 + [(3800.0, 0.0)] * 50, volume=0.0).any()
  bank = load_bank(make_bank(tmp_path))
  plain = LoopSynth(PROFILES['bmw_m52'], bank)
  assert plain.effects == []
  turbo = LoopSynth(replace(PROFILES['bmw_m52'], turbo=1.0), bank)
  steps = [(4000.0, 1.0)] * 200 + [(3800.0, 0.0)] * 50
  out = np.concatenate([turbo.render(rpm, load, FRAMES, 1.0) for rpm, load in steps])
  assert np.abs(out).max() <= 1.0


def test_the_supra_has_a_turbo():
  assert PROFILES['supra_turbo'].turbo > 0 and PROFILES['supra_turbo'].pops == 0
