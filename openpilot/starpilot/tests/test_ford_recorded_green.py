import lzma
import json
from pathlib import Path

import pytest
from openpilot.starpilot.longitudinal.tests import test_cem_green_release as green
from openpilot.starpilot.tests.ford_lifecycle_helpers import FORD_FACTORIES, ford_joined as ford_joined
from openpilot.starpilot.tests.test_ford_recorded_departure import _motion_payloads
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner


@pytest.mark.parametrize('geometry', ['recorded', 'shifted-origin', 'late-backtrack'])
@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [True])
def test_captured_green_forecast_releases_existing_model_hold(ford_joined, monkeypatch, geometry):
  samples = json.loads(lzma.decompress((Path(__file__).parent / 'testdata/ford_mache_green_origin.json.xz').read_bytes()))['frames']
  case = green.CemGreenCallerTests(methodName='runTest')
  case.setUp()
  case.cp = ford_joined.host.CP
  case.planner = LongitudinalPlanner(case.cp, init_v=4.27)
  try:
    case.prime()
    original = green.serialized_scene
    current = [None]
    monkeypatch.setattr(green, 'serialized_scene', lambda **kwargs: _motion_payloads(original(**kwargs), current[0]))
    plans = []
    for sample in samples:
      if geometry == 'shifted-origin':
        sample['model']['position']['x'][0] = .001
      elif geometry == 'late-backtrack':
        sample['model']['position']['x'][-2] = sample['model']['position']['x'][-3] - .1
      current[0] = sample
      _, _, held, plan = case.cycle(stopped=True, position=sample['model']['position']['x'],
        velocity=sample['model']['velocity']['x'], should_stop=sample['model']['action']['shouldStop'],
        desired_accel=sample['model']['action']['desiredAcceleration'], lead=False)
      plans.append((held, plan.shouldStop, plan.aTarget))
    if geometry == 'recorded':
      assert any(not held and not stop and target > 0 for held, stop, target in plans[-4:]), plans
    else:
      assert all(held and stop and target <= 0 for held, stop, target in plans), plans
  finally:
    case.doCleanups()
