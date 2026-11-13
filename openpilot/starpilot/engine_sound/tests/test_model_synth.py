import numpy as np
import pytest

from openpilot.starpilot.engine_sound.model import EngineModel
from openpilot.starpilot.engine_sound.profiles import DEFAULT_PROFILE, PROFILES, get
from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE, Synth, to_pcm

DT = 0.01


def run(model, seconds, pedal, speed=0.0, accel=0.0, in_gear=False):
  out = [model.update(DT, pedal, speed, accel, in_gear) for _ in range(int(seconds / DT))]
  return out[-1]


@pytest.mark.parametrize('name', sorted(PROFILES))
def test_free_rev_follows_pedal_and_returns_to_idle(name):
  profile = PROFILES[name]
  model = EngineModel(profile)
  assert run(model, 2.0, 0.0)[0] == pytest.approx(profile.idle_rpm, abs=1)
  half = run(model, 4.0, 0.5)[0]
  assert profile.idle_rpm < half < profile.redline_rpm
  full = max(model.update(DT, 1.0, 0.0, 0.0, False)[0] for _ in range(400))
  assert full > half and full <= profile.redline_rpm + 1
  assert run(model, 8.0, 0.0)[0] == pytest.approx(profile.idle_rpm, abs=5)


def test_limiter_bounces_instead_of_sitting_at_redline():
  profile = PROFILES['race_car']
  model = EngineModel(profile)
  run(model, 3.0, 1.0)
  trace = [model.update(DT, 1.0, 0.0, 0.0, False)[0] for _ in range(200)]
  assert max(trace) - min(trace) > 150


@pytest.mark.parametrize('name', [name for name, profile in PROFILES.items() if profile.gears])
def test_acceleration_climbs_through_every_gear_within_rev_range(name):
  profile = PROFILES[name]
  model = EngineModel(profile)
  gears, speed = set(), 0.0
  while speed < 60.0:
    speed += 3.0 * DT
    rpm, _ = model.update(DT, 1.0, speed, 3.0, True)
    gears.add(model.gear)
    assert profile.idle_rpm - 1 <= rpm <= profile.redline_rpm + 1
  assert gears == set(range(len(profile.gears)))


@pytest.mark.parametrize('name', [name for name, profile in PROFILES.items() if profile.gears])
def test_steady_cruise_does_not_hunt_between_gears(name):
  model = EngineModel(PROFILES[name])
  for speed in (5.0, 12.0, 20.0, 30.0):
    run(model, 3.0, 0.15, speed, 0.0, True)
    settled = model.gear
    shifts = 0
    for _ in range(500):
      model.update(DT, 0.15, speed, 0.0, True)
      shifts += model.gear != settled
    assert shifts == 0


def test_kickdown_downshifts():
  model = EngineModel(PROFILES['race_car'])
  run(model, 5.0, 0.1, 25.0, 0.0, True)
  cruising = model.gear
  run(model, 2.0, 1.0, 25.0, 2.0, True)
  assert model.gear < cruising


def test_openpilot_acceleration_counts_as_load_with_pedal_at_rest():
  model = EngineModel(PROFILES['race_car'])
  assert run(model, 1.0, 0.0, 15.0, 2.0, True)[1] > 0.5
  assert run(model, 1.0, 0.0, 15.0, -1.0, True)[1] < 0.05


def test_stopped_in_drive_idles_and_pedal_slips_above_idle():
  profile = PROFILES['diesel']
  model = EngineModel(profile)
  assert run(model, 2.0, 0.0, in_gear=True)[0] == pytest.approx(profile.idle_rpm, abs=1)
  assert run(model, 2.0, 1.0, in_gear=True)[0] == pytest.approx(profile.idle_rpm + profile.launch_slip_rpm, abs=5)


def test_unknown_profile_falls_back_to_default():
  assert get('nope').name == DEFAULT_PROFILE


@pytest.mark.parametrize('name', sorted(PROFILES))
def test_render_is_bounded_and_continuous_across_blocks(name):
  profile = PROFILES[name]
  synth = Synth(profile)
  blocks = [synth.render(profile.idle_rpm + i * 20, min(i / 100, 1.0), 480) for i in range(200)]
  audio = np.concatenate(blocks)
  assert audio.dtype == np.float32 and len(audio) == 200 * 480
  assert np.max(np.abs(audio)) <= 1.0
  assert np.sqrt(np.mean(audio[-SAMPLE_RATE // 2:] ** 2)) > 0.02
  inside = np.abs(np.diff(audio))
  boundaries = inside[479::480]
  assert boundaries.max() <= inside.max()
  assert len(to_pcm(blocks[0])) == 960


@pytest.mark.parametrize('name', sorted(PROFILES))
def test_zero_volume_is_silent_and_volume_changes_ramp(name):
  synth = Synth(PROFILES[name])
  assert not synth.render(2000, 0.5, 480, volume=0.0).any()
  rising = synth.render(2000, 0.5, 480, volume=1.0)
  assert abs(rising[0]) < 0.01
