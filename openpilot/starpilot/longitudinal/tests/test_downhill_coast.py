from pathlib import Path
from types import SimpleNamespace
import math

import pytest

from openpilot.common.constants import CV
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling, CurveCeiling
from openpilot.starpilot.longitudinal.downhill_coast import KEY, SURPLUS_KEY, read_surplus, speed_error
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import ACTIVE, messages, snapshot
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR

SET_SPEED = 30 * CV.MPH_TO_MS
LIMIT = 35 * CV.MPH_TO_MS
NOW = 5_000_000_000
DRIVE = 1_000_000_000


def test_saved_defaults_and_invalid_values(tmp_path: Path):
  params = SimpleNamespace(get_param_path=lambda key: tmp_path / key)
  assert read_surplus(params) == 0.
  (tmp_path / KEY).write_bytes(b'1')
  for raw, expected in ((b'25.000000', 25 * CV.MPH_TO_MS), (b'0', 0.), (b'-1', 0.),
                        (b'25.1', 0.), (b'nan', 0.), (b'inf', 0.), (b'bad', 0.)):
    (tmp_path / SURPLUS_KEY).write_bytes(raw)
    assert read_surplus(params) == expected
  (tmp_path / SURPLUS_KEY).write_bytes(b'5')
  (tmp_path / 'SafeMode').write_bytes(b'1')
  assert read_surplus(params) == 0.
  (tmp_path / 'SafeMode').unlink()
  (tmp_path / KEY).write_bytes(b'0')
  assert read_surplus(params) == 0.


def test_gravity_only_allowance_and_approach_to_limit():
  def error(speed, pitch=-.05, accel=.3, limit=LIMIT):
    return speed_error(SET_SPEED, speed, accel, pitch, limit=limit, delay=.25)
  assert error(SET_SPEED - .1) is None
  assert error(SET_SPEED + 1) == 0.
  assert error(LIMIT) == pytest.approx(-.075)
  assert error(LIMIT + 1) == pytest.approx(-1.075)
  assert error(SET_SPEED + 1, pitch=.05) is None
  assert error(SET_SPEED + 1, pitch=0.) is None
  assert error(SET_SPEED + 1, pitch=math.nan) is None
  assert error(SET_SPEED + 1, limit=SET_SPEED) is None


class Frame(dict):
  def __init__(self, **kwargs):
    data, _ = messages(**kwargs)
    super().__init__(data, deviceState=SimpleNamespace(started=True, startedMonoTime=DRIVE), deviceMotion=SimpleNamespace())
    self['carState'].vCruise = SET_SPEED / CV.KPH_TO_MS
    self['carState'].vEgo = SET_SPEED + 1.
    self['carState'].canValid = True
    self['carControl'].enabled = True
    self['carControl'].longActive = True
    self['carControl'].orientationNED = [0., -.05, 0.]
    self.seen = dict.fromkeys(self, True)
    self.alive = self.seen.copy()
    self.valid = self.seen.copy()
    self.logMonoTime = dict.fromkeys(self, NOW - 20_000_000)
    self.recv_time = dict.fromkeys(self, (NOW - 20_000_000) / 1e9)


@pytest.mark.parametrize('case', ['clear', 'above-limit', 'lead', 'stop', 'force', 'slc', 'curve', 'navigation',
                                 'stale', 'flat', 'driver', 'below-set'])
def test_planner_only_relaxes_ordinary_cruise_braking(case, monkeypatch):
  cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
  frame = Frame(lead=case == 'lead', e2e=case == 'stop', force=case == 'force')
  options = {}
  if case == 'above-limit':
    frame['carState'].vEgo = LIMIT + 1.
  elif case == 'slc':
    options['cruise_ceiling'] = CruiseCeiling(SET_SPEED + .5, ACTIVE)
  elif case == 'curve':
    options['curve_ceiling'] = CurveCeiling(SET_SPEED + .5, frame.logMonoTime['modelV2'])
  elif case == 'navigation':
    from openpilot.selfdrive.controls.lib import longitudinal_planner
    # A navigation approach can be above SET but below the downhill allowance.
    monkeypatch.setattr(longitudinal_planner, 'navigation_ceiling',
                        lambda *args, **kwargs: SET_SPEED + .5 if args[3] > SET_SPEED + .75 else None)
  elif case == 'stale':
    frame.logMonoTime['deviceMotion'] = NOW - 500_000_000
  elif case == 'flat':
    frame['carControl'].orientationNED = [0., 0., 0.]
  elif case == 'driver':
    frame['carState'].gasPressed = True
  elif case == 'below-set':
    frame['carState'].vEgo = SET_SPEED - 1.
  speed = float(frame['carState'].vEgo)
  normal = LongitudinalPlanner(cp, init_v=speed, init_a=.7)
  coast = LongitudinalPlanner(cp, init_v=speed, init_a=.7, downhill_surplus_mps=5 * CV.MPH_TO_MS)
  for _ in range(30):
    normal.update(frame, now_ns=NOW, drive_id=DRIVE, **options)
    coast.update(frame, now_ns=NOW, drive_id=DRIVE, **options)
    assert coast.mpc.solution_status == normal.mpc.solution_status == 0
    if case in ('clear', 'above-limit'):
      assert coast.a_cruise <= 0.
      assert coast.output_a_target <= 0.
    else:
      assert snapshot(coast) == snapshot(normal)
  if case == 'clear':
    assert normal.a_cruise < coast.a_cruise == 0.
  elif case == 'above-limit':
    assert coast.output_a_target < 0.
