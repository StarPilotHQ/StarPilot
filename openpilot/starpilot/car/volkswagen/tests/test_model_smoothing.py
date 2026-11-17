from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest

from opendbc.car import structs
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.drive_helpers import get_curvature_from_plan, smooth_value
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.starpilot.car.subaru.smoothing import filter_action
from openpilot.starpilot.car.volkswagen.model_smoothing import ControlSmoothing, POPV2_SHA256, seconds
from openpilot.starpilot.models.status import ModelLoad, ModelVariant


def setup(gearbox='manual', alpha=True, offset=0):
  fp = {b: {} for b in range(8)}
  fp[offset + 1][0xFD] = 8
  if gearbox != 'manual':
    fp[offset][0xAD if gearbox == 'automatic' else 0x187] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, fp, [], alpha, False, False)
  load = ModelLoad(42, 123, 1_000_000_000, 'pop223', ModelVariant.SMALL, POPV2_SHA256)
  return cp, load


@pytest.mark.parametrize('gearbox', ('manual', 'automatic', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
def test_factory_mqb_popv2_has_the_same_matched_target_and_delay(gearbox, alpha, offset):
  cp, load = setup(gearbox, alpha, offset)
  assert seconds(cp, load) == (.1 if gearbox == 'manual' else 0.)


@pytest.mark.parametrize('veto', ('model', 'artifact', 'variant', 'fallback', 'missing', 'brand', 'passive', 'dashcam',
                                  'notCar', 'pq', 'mlb', 'meb', 'controller', 'gain', 'native', 'multiple'))
def test_other_configurations_and_unqualified_model_are_unchanged(veto):
  cp, load = setup()
  if veto in ('model', 'artifact', 'variant', 'fallback'):
    key, value = {'model': ('model_id', 'rdf43'), 'artifact': ('artifact_sha256', '0' * 64),
                  'variant': ('variant', ModelVariant.CHESTNUT), 'fallback': ('fallback_reason', 'selected-load-failed')}[veto]
    load = replace(load, **{key: value})
  elif veto == 'missing':
    load = None
  elif veto in ('brand', 'passive', 'dashcam', 'notCar', 'pq', 'mlb', 'meb'):
    key, value = {'brand': ('brand', 'hyundai'), 'passive': ('passive', True), 'dashcam': ('dashcamOnly', True),
                  'notCar': ('notCar', True), 'pq': ('flags', 2), 'mlb': ('flags', 8), 'meb': ('flags', 16)}[veto]
    setattr(cp, key, value)
  elif veto == 'controller':
    cp.lateralTuning.init('torque')
  elif veto == 'gain':
    cp.lateralTuning.pid.kpV = [.5]
  elif veto == 'native':
    cp.safetyConfigs[-1].safetyModel = structs.CarParams.SafetyModel.volkswagenPq
  else:
    cp.safetyConfigs = [{'safetyModel': 'volkswagen'}, {'safetyModel': 'volkswagen'}]
  assert seconds(cp, load) == 0.


def test_control_timing_uses_loaded_identity_without_per_frame_file_io():
  cp, load = setup()
  owner = ControlSmoothing(cp)
  with patch('openpilot.starpilot.car.volkswagen.model_smoothing.read_receipt', return_value=load) as read, \
       patch('openpilot.starpilot.car.volkswagen.model_smoothing.process_start_ticks', return_value=123):
    assert owner.seconds(2_000_000_000, 2_000_000_000) == .1
    for step in range(1, 100):
      now = 2_000_000_000 + step * 10_000_000
      assert owner.seconds(now, now) == .1
    assert read.call_count == 1
    assert owner.seconds(0, 2_990_000_000) == 0.
  with patch('openpilot.starpilot.car.volkswagen.model_smoothing.read_receipt', return_value=load), \
       patch('openpilot.starpilot.car.volkswagen.model_smoothing.process_start_ticks', return_value=124):
    assert owner.seconds(3_000_000_000, 3_000_000_000) == 0.


@pytest.mark.parametrize('speed', (0., .3, .4, 5., 18., 35.))
def test_target_sequence_matches_stable_projection_and_smoothing(speed):
  cp, load = setup()
  tau = seconds(cp, load)
  times = np.array(ModelConstants.T_IDXS)
  candidate = log.ModelDataV2.Action(desiredCurvature=.001)
  stable = .001
  for tick in range(100):
    yaw = .005 * np.sin(times + tick * .05)
    rate = .005 * np.cos(times + tick * .05)
    action_t = .2893982231616974 + .075 + tau
    raw = get_curvature_from_plan(yaw, rate, times, speed, action_t)
    candidate.desiredAcceleration = -.25
    candidate.shouldStop = True
    candidate.desiredCurvature = float(raw) if speed > .3 else candidate.desiredCurvature
    previous = log.ModelDataV2.Action(desiredCurvature=stable)
    filter_action(candidate, previous, speed, tau)
    expected_raw = 2 * np.interp(action_t, times, yaw) / (max(1., speed) * action_t) - rate[0] / max(1., speed)
    stable = float(smooth_value(expected_raw, stable, .1)) if speed > .3 else stable
    assert candidate.desiredCurvature == pytest.approx(stable, abs=1e-8)
    assert candidate.desiredAcceleration == -.25 and candidate.shouldStop


def test_model_join_applies_matching_input_delay_and_exactly_one_filter():
  import ast
  import importlib.util
  from pathlib import Path
  from types import SimpleNamespace

  spec = importlib.util.find_spec('openpilot.selfdrive.modeld.modeld')
  assert spec is not None and spec.origin is not None
  tree = ast.parse(Path(spec.origin).read_text())
  cp, load = setup()
  previous = log.ModelDataV2.Action(desiredCurvature=0.)
  action = log.ModelDataV2.Action(desiredCurvature=.001, desiredAcceleration=-.25, shouldStop=True)
  env = {'CP': cp, 'receipt_owner': SimpleNamespace(load=load), 'v_ego': 18., 'subaru_smoothing': 0.,
         'subaru_smooth_seconds': lambda speed, maximum: 0., 'volkswagen_smoothing': seconds,
         'LAT_SMOOTH_SECONDS': 0., 'sm': {'lateralDelay': SimpleNamespace(lateralDelay=.2893982231616974)},
         'action': action, 'prev_action': previous, 'filter_action': filter_action}
  wanted = {'subaru_tau', 'volkswagen_tau', 'lat_delay'}
  nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and
           any(isinstance(t, ast.Name) and t.id in wanted for t in n.targets)]
  filters = [n for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and
             isinstance(n.value.func, ast.Name) and n.value.func.id == 'filter_action']
  for node in sorted(nodes + filters, key=lambda n: n.lineno):
    exec(compile(ast.Module(body=[node], type_ignores=[]), spec.origin, 'exec'), env)
  assert env['lat_delay'] == pytest.approx(.3893982231616974)
  assert action.desiredCurvature == pytest.approx(float(smooth_value(.001, 0., .1)), abs=1e-9)
  assert action.desiredAcceleration == -.25 and action.shouldStop
