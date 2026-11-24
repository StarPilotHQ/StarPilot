from dataclasses import replace

import numpy as np
import pytest

from openpilot.starpilot.engine_sound.loops import LoopSynth, load_bank
from openpilot.starpilot.engine_sound.pops import GRAIN_SECONDS, Pops, library, load_grains
from openpilot.starpilot.engine_sound.profiles import PROFILES
from openpilot.starpilot.engine_sound.tests.test_loops import make_bank

FRAMES = 480


def run(pops, steps):
  return np.concatenate([pops.render(rpm, load, FRAMES, 1.0) for rpm, load in steps])


def test_library_is_fixed_and_normalised():
  first, second = library(), library()
  for kind, samples in first.items():
    for a, b in zip(samples, second[kind], strict=True):
      assert np.array_equal(a, b)
      assert np.abs(a).max() == np.float32(1.0) and a[-1] == 0.0


def test_lifting_at_speed_pops_and_getting_back_on_the_throttle_stops_them():
  pops = Pops(1.0, 700.0, 7000.0)
  assert not run(pops, [(5000.0, 0.9)] * 100).any()
  after_lift = run(pops, [(5000.0 - 20 * i, 0.0) for i in range(150)])
  assert np.abs(after_lift).max() > 0.1
  run(pops, [(4000.0, 0.6)] * 30)
  assert pops.window == 0.0
  assert not run(pops, [(4000.0, 0.6)] * 50).any()


def test_no_pops_at_low_revs_or_when_coasting_without_a_lift():
  pops = Pops(1.0, 700.0, 7000.0)
  assert not run(pops, [(1000.0, 0.9)] * 50 + [(1000.0, 0.0)] * 200).any()
  pops = Pops(1.0, 700.0, 7000.0)
  assert not run(pops, [(4000.0, 0.0)] * 300).any()


def test_a_downshift_blip_on_overrun_pops_without_a_lift():
  pops = Pops(1.0, 700.0, 7000.0)
  run(pops, [(1600.0, 0.0)] * 50)
  assert np.abs(run(pops, [(1600.0 + 60 * i, 0.0) for i in range(10)] + [(2200.0, 0.0)] * 60)).max() > 0.1


def test_a_pop_rings_on_into_the_next_block():
  pops = Pops(1.0, 700.0, 7000.0, seed=1)
  run(pops, [(6000.0, 1.0)] * 50)
  for _ in range(200):
    pops.render(6000.0, 0.0, FRAMES, 1.0)
    if pops.tail.any():
      break
  carried = pops.tail.copy()
  assert carried.any()
  assert np.array_equal(pops.render(6000.0, 1.0, FRAMES, 1.0), carried[:FRAMES])


def test_quiet_at_zero_volume_and_profiles_without_pops_are_unchanged(tmp_path):
  pops = Pops(1.0, 700.0, 7000.0)
  assert not np.concatenate([pops.render(6000.0, load, FRAMES, 0.0) for load in [1.0] * 50 + [0.0] * 100]).any()
  bank = load_bank(make_bank(tmp_path))
  plain = LoopSynth(PROFILES['ferrari_f136'], bank)
  assert plain.effects == []
  popping = LoopSynth(replace(PROFILES['ferrari_f136'], pops=1.0), bank)
  steps = [(5000.0, 1.0)] * 50 + [(5000.0 - 20 * i, 0.0) for i in range(150)]
  quiet = np.concatenate([plain.render(rpm, load, FRAMES, 1.0) for rpm, load in steps])
  loud = np.concatenate([popping.render(rpm, load, FRAMES, 1.0) for rpm, load in steps])
  assert np.array_equal(quiet[:50 * FRAMES], loud[:50 * FRAMES])
  assert np.abs(loud - quiet).max() > 0.05 and np.abs(loud).max() <= 1.0


def test_the_m3s_pop_on_their_own_banks():
  assert (PROFILES['bmw_m3'].loops, PROFILES['bmw_m3_deep'].loops) == ('bmw_m3', 'bmw_m3_deep')
  assert PROFILES['bmw_m3'].pops > 0 and PROFILES['bmw_m3_deep'].pops > 0


def test_burbling_profiles():
  assert [name for name, profile in PROFILES.items() if profile.burble] == ['bmw_m3', 'bmw_m3_deep', 'jaguar_ftype']
  assert all(PROFILES[name].pops > 0 for name in ('bmw_m3', 'bmw_m3_deep', 'jaguar_ftype'))
  assert [name for name, profile in PROFILES.items() if profile.pop_pitch != 1.0] == ['bmw_m3_deep']


def test_a_lower_pop_pitch_plays_every_pop_slower():
  plain, deep = Pops(1.0, 700.0, 7000.0, burble=True), Pops(1.0, 700.0, 7000.0, burble=True, pitch=0.8)
  assert len(deep.grains) == len(plain.grains)
  assert len(deep.grains[0]) == pytest.approx(len(plain.grains[0]) / 0.8, abs=2)
  assert len(deep.samples['pop'][0]) == pytest.approx(len(plain.samples['pop'][0]) / 0.8, abs=2)
  assert deep.longest > plain.longest


def test_recorded_grains_load_and_a_missing_recording_falls_back_to_plain_pops(tmp_path):
  grains = load_grains()
  assert len(grains) == 62 and all(len(g) == int(GRAIN_SECONDS * 48000) for g in grains)
  assert load_grains(tmp_path / 'missing.wav') == []
  assert not Pops(1.0, 721.0, 6500.0, sample_rate=44100, burble=True).burble


def test_a_low_rev_lift_burbles_then_rumbles_and_a_high_rev_lift_still_crackles():
  pops = Pops(1.0, 721.0, 6500.0, burble=True)
  run(pops, [(2300.0, 0.3)] * 100)
  burst = run(pops, [(2300.0 - 2 * i, 0.0) for i in range(110)])
  assert pops.window == 0.0 and np.abs(burst).max() > 0.05
  run(pops, [(2000.0, 0.0)] * 15)
  assert pops.burst == 0.0 and pops.rumble > 0.0
  rumble = run(pops, [(2000.0, 0.0)] * 50)
  assert 0.003 < np.sqrt(np.mean(rumble[4 * FRAMES:] ** 2)) < 0.03
  assert not run(pops, [(2000.0, 0.0)] * 200)[-50 * FRAMES:].any()

  high = Pops(1.0, 721.0, 6500.0, burble=True)
  run(high, [(5500.0, 0.9)] * 100)
  run(high, [(5500.0, 0.0)])
  assert high.window > 0.0 and high.burst == 0.0


def test_downshift_bursts_are_occasional_never_back_to_back_and_shrink():
  first, lengths = 0, {1: set(), 2: set(), 3: set()}
  for seed in range(300):
    pops = Pops(1.0, 721.0, 6500.0, seed=seed, burble=True)
    fired = []
    for _ in range(8):
      pops.burst = 0.0
      before = pops.downshift_bursts
      pops._downshift()
      fired.append(pops.downshift_bursts > before)
      if fired[-1]:
        lengths[pops.downshift_bursts].add(round(pops.burst, 4))
    assert not any(a and b for a, b in zip(fired, fired[1:], strict=False)) and sum(fired) <= 3
    first += fired[0]
  assert 0.2 < first / 300 < 0.4
  assert lengths[1] == {0.35} and lengths[2] == {0.21} and lengths[3] == {0.126}


def test_a_downshift_blip_in_burble_mode_never_fires_a_single_pop():
  for seed in range(20):
    pops = Pops(1.0, 721.0, 6500.0, seed=seed, burble=True)
    run(pops, [(1400.0, 0.0)] * 50)
    blip = run(pops, [(1400.0 + 60 * i, 0.0) for i in range(8)] + [(1880.0, 0.0)] * 60)
    assert blip.any() == (pops.downshift_bursts == 1)
