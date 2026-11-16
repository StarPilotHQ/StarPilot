from dataclasses import replace

import numpy as np

from openpilot.starpilot.engine_sound.loops import LoopSynth, load_bank
from openpilot.starpilot.engine_sound.pops import Pops, library
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
  plain = LoopSynth(PROFILES['bmw_m52'], bank)
  assert plain.effects == []
  popping = LoopSynth(replace(PROFILES['bmw_m52'], pops=1.0), bank)
  steps = [(5000.0, 1.0)] * 50 + [(5000.0 - 20 * i, 0.0) for i in range(150)]
  quiet = np.concatenate([plain.render(rpm, load, FRAMES, 1.0) for rpm, load in steps])
  loud = np.concatenate([popping.render(rpm, load, FRAMES, 1.0) for rpm, load in steps])
  assert np.array_equal(quiet[:50 * FRAMES], loud[:50 * FRAMES])
  assert np.abs(loud - quiet).max() > 0.05 and np.abs(loud).max() <= 1.0


def test_the_m3_pops_and_the_m52_does_not():
  assert PROFILES['bmw_m3'].pops > 0 and PROFILES['bmw_m3'].loops == 'bmw_m52'
  assert PROFILES['bmw_m52'].pops == 0
