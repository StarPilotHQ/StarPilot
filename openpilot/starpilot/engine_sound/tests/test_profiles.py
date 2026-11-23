from dataclasses import replace

import numpy as np
import pytest

from openpilot.starpilot.engine_sound.loops import BUNDLED_ROOT, LoopSynth, load_bank
from openpilot.starpilot.engine_sound.model import EngineModel
from openpilot.starpilot.engine_sound.profiles import PROFILES, RACE_GEARS, RACE_REDLINE
from openpilot.starpilot.engine_sound.synth import Synth

DT = 0.01
SHORT_GEARED_CRUISE_FLOOR = 3250.0


SHORT_GEARED = ('ioniq_5n', 'nissan_super_gt', 'nissan_super_gt2')


@pytest.mark.parametrize('name', [name for name in PROFILES if name not in SHORT_GEARED])
def test_default_gearing_and_shift_points(name):
  p = PROFILES[name]
  assert p.upshift_rpm == pytest.approx((0.38 * p.redline_rpm, 0.96 * p.redline_rpm))
  assert p.downshift_rpm == pytest.approx((0.2 * p.redline_rpm, 0.6 * p.redline_rpm))
  assert p.gears == pytest.approx(tuple(ratio * p.redline_rpm / RACE_REDLINE for ratio in RACE_GEARS))


@pytest.mark.parametrize('name', SHORT_GEARED)
def test_short_geared_holds_revs(name):
  p = PROFILES[name]
  assert p.gears[0] == pytest.approx(RACE_GEARS[0] * p.redline_rpm / RACE_REDLINE * 1.2)
  assert p.upshift_rpm[0] == pytest.approx(0.62 * p.redline_rpm)
  assert p.downshift_rpm == pytest.approx((0.45 * p.redline_rpm, 0.7 * p.redline_rpm))


def test_super_gt2_plays_its_own_lower_bank():
  assert PROFILES['nissan_super_gt'].loops == 'nissan_super_gt' and PROFILES['nissan_super_gt2'].loops == 'nissan_super_gt2'
  assert (PROFILES['nissan_super_gt'].label, PROFILES['nissan_super_gt2'].label) == ('Nissan Super GT1', 'Nissan Super GT2')


@pytest.mark.parametrize('voice', ('loops', 'synth'))
def test_trim_scales_the_whole_voice(voice):
  base = replace(PROFILES['supra_turbo'], trim_db=0.0)
  def level(trim_db):
    profile = replace(base, trim_db=trim_db)
    synth = LoopSynth(profile, load_bank(BUNDLED_ROOT / profile.loops)) if voice == 'loops' else Synth(profile)
    out = np.concatenate([synth.render(2000.0, 0.3, 480, 0.05) for _ in range(50)])[4800:]
    return float(np.sqrt(np.mean(out ** 2)))
  assert level(6.0) / level(0.0) == pytest.approx(10 ** (6 / 20), rel=0.02)


@pytest.mark.parametrize('name', SHORT_GEARED)
def test_short_geared_light_pedal_stays_in_the_recorded_range_and_clear_of_redline(name):
  p = PROFILES[name]
  model = EngineModel(p)
  speed, revs = 0.0, []
  while speed < 30.0:
    speed += 1.2 * DT
    revs.append(model.update(DT, 0.25, speed, 1.2, True)[0])
  cruising = [rpm for rpm, v in zip(revs, (DT * 1.2 * i for i in range(len(revs))), strict=True) if v > 8.0]
  assert min(cruising) > SHORT_GEARED_CRUISE_FLOOR
  assert max(revs) < 0.75 * p.redline_rpm
  assert model.gear >= 3
