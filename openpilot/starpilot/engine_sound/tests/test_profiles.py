import pytest

from openpilot.starpilot.engine_sound.model import EngineModel
from openpilot.starpilot.engine_sound.profiles import PROFILES, RACE_GEARS, RACE_REDLINE

DT = 0.01
SUPER_GT_RECORDED_FROM = 3250.0


@pytest.mark.parametrize('name', [name for name in PROFILES if name != 'nissan_super_gt'])
def test_default_gearing_and_shift_points(name):
  p = PROFILES[name]
  assert p.upshift_rpm == pytest.approx((0.38 * p.redline_rpm, 0.96 * p.redline_rpm))
  assert p.downshift_rpm == pytest.approx((0.2 * p.redline_rpm, 0.6 * p.redline_rpm))
  assert p.gears == pytest.approx(tuple(ratio * p.redline_rpm / RACE_REDLINE for ratio in RACE_GEARS))


def test_super_gt_is_geared_short_and_holds_revs():
  p = PROFILES['nissan_super_gt']
  assert p.gears[0] == pytest.approx(RACE_GEARS[0] * p.redline_rpm / RACE_REDLINE * 1.2)
  assert p.upshift_rpm[0] == pytest.approx(0.62 * p.redline_rpm)
  assert p.downshift_rpm == pytest.approx((0.45 * p.redline_rpm, 0.7 * p.redline_rpm))


def test_super_gt_light_pedal_stays_in_the_recorded_range_and_clear_of_redline():
  p = PROFILES['nissan_super_gt']
  model = EngineModel(p)
  speed, revs = 0.0, []
  while speed < 30.0:
    speed += 1.2 * DT
    revs.append(model.update(DT, 0.25, speed, 1.2, True)[0])
  cruising = [rpm for rpm, v in zip(revs, (DT * 1.2 * i for i in range(len(revs))), strict=True) if v > 8.0]
  assert min(cruising) > SUPER_GT_RECORDED_FROM
  assert max(revs) < 0.75 * p.redline_rpm
  assert model.gear >= 3
