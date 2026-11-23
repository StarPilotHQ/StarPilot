from dataclasses import replace

import numpy as np
import pytest

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
  run(turbo, [(4000.0, 1.0)] * 250)
  assert early < 0.25 < 0.8 < turbo.boost
  low = Turbo(1.0, 800.0, 6000.0)
  run(low, [(850.0, 1.0)] * 200)
  assert low.boost == 0.0


def test_the_first_tenth_of_pedal_makes_no_boost_and_part_boost_whistles_faintly():
  light = Turbo(1.0, 800.0, 6000.0)
  assert not run(light, [(4000.0, 0.1)] * 300).any() and light.boost == 0.0
  part, full = Turbo(1.0, 800.0, 6000.0), Turbo(1.0, 800.0, 6000.0)
  run(part, [(4000.0, 0.2)] * 400)
  run(full, [(4000.0, 1.0)] * 400)
  quiet, loud = np.abs(run(part, [(4000.0, 0.2)] * 20)).max(), np.abs(run(full, [(4000.0, 1.0)] * 20)).max()
  assert quiet < 0.3 * loud


def test_lifting_on_boost_fires_the_valve_and_vents_it():
  turbo = Turbo(1.0, 800.0, 6000.0)
  run(turbo, [(4000.0, 1.0)] * 200)
  on_boost = np.abs(run(turbo, [(4000.0, 1.0)] * 20)).max()
  release = run(turbo, [(3800.0, 0.0)] * 190)
  assert np.abs(release).max() > 2.5 * on_boost
  assert turbo.boost < 0.1
  assert np.abs(release[-FRAMES * 10:]).max() < 0.5 * np.abs(release[FRAMES * 10:FRAMES * 20]).max()


def test_the_recorded_release_loads_at_three_speeds_and_flutters():
  from openpilot.starpilot.engine_sound.turbo import BOV_SPEEDS, load_releases
  releases = load_releases()
  assert len(releases) == len(BOV_SPEEDS)
  assert len(releases[0]) > len(releases[1]) > len(releases[2]) > 2 * 48000
  release = releases[1].astype(np.float64)
  spectrum = np.fft.rfft(release)
  freq = np.fft.rfftfreq(len(release), 1 / 48000)
  spectrum[(freq < 1200) | (freq > 9000)] = 0
  high = np.fft.irfft(spectrum, len(release))
  db = 20 * np.log10(np.sqrt(np.convolve(high ** 2, np.ones(96) / 96, mode='same'))[::48] + 1e-9)
  peaks = [i for i in range(12, len(db) - 12) if db[i] == db[i - 12:i + 13].max() and db[i] > np.median(db[max(0, i - 60):i + 60]) + 3]
  assert 12.0 < len(peaks) / (len(db) / 1000) < 20.0


def test_the_release_fades_in_and_the_engine_dips_under_it_then_recovers():
  from openpilot.starpilot.engine_sound.turbo import ENGINE_DUCK_DB
  turbo = Turbo(1.0, 800.0, 6000.0)
  run(turbo, [(4000.0, 1.0)] * 200)
  assert np.all(turbo.engine_gain == 1.0)
  run(turbo, [(3800.0, 0.0)] * 10)
  first = turbo.render(3800.0, 0.0, FRAMES, 1.0)
  later = run(turbo, [(3800.0, 0.0)] * 30)
  assert np.abs(first[:48]).max() < np.abs(later).max()
  assert turbo.engine_gain[-1] == pytest.approx(10 ** (-ENGINE_DUCK_DB / 20), rel=0.05)
  run(turbo, [(3800.0, 0.0)] * 400)
  assert turbo.engine_gain[-1] == pytest.approx(1.0, abs=1e-3)


def test_a_missing_recording_means_no_release_and_no_crash(tmp_path, monkeypatch):
  from openpilot.starpilot.engine_sound import turbo as module
  assert module.load_releases(tmp_path / 'missing.wav') == []
  monkeypatch.setattr(module, 'load_releases', lambda **kwargs: [])
  quiet = Turbo(1.0, 800.0, 6000.0)
  run(quiet, [(4000.0, 1.0)] * 200)
  assert np.abs(run(quiet, [(3800.0, 0.0)] * 50)).max() < 0.1


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
  plain = LoopSynth(PROFILES['ferrari_f136'], bank)
  assert plain.effects == []
  turbo = LoopSynth(replace(PROFILES['ferrari_f136'], turbo=1.0), bank)
  steps = [(4000.0, 1.0)] * 200 + [(3800.0, 0.0)] * 50
  out = np.concatenate([turbo.render(rpm, load, FRAMES, 1.0) for rpm, load in steps])
  assert np.abs(out).max() <= 1.0


def test_the_supra_has_a_turbo():
  assert PROFILES['supra_turbo'].turbo > 0 and PROFILES['supra_turbo'].pops == 0
