import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.drive_helpers import get_curvature_from_plan, smooth_value
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.starpilot.car.hyundai.model_smoothing import seconds
from openpilot.starpilot.car.subaru.smoothing import filter_action


def setup(alpha=True, alternate=False, offset=0):
  fp = gen_empty_fingerprint()
  fp[offset].update({0x1AA if alternate else 0x1CF: 16 if alternate else 8,
                    0x100: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x125: 16})
  fp[offset + 2].update({0x12A: 16, 0x1A0: 32, 0x161: 16, 0x162: 32})
  return CarInterface.get_params(CAR.HYUNDAI_KONA_2ND_GEN, fp, [], alpha, False, False)


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('aol', (False, True))
def test_factory_stock_and_alpha_use_identical_timing_without_mutating_cp(alpha, alternate, offset, aol):
  cp = setup(alpha, alternate, offset)
  assert cp.openpilotLongitudinalControl == alpha and cp.pcmCruise != alpha
  if aol:
    cp.safetyConfigs[-1].safetyParam |= 0x800
    cp.alternativeExperience = 32
  before = cp.to_dict()
  assert seconds(cp) == .1
  assert cp.to_dict() == before


@pytest.mark.parametrize('veto', ('brand', 'ev', 'hev', 'passive', 'dashcamOnly', 'notCar', 'angle', 'hda2', 'nonScc',
                                  'noCcnc', 'noCamera', 'delay', 'nanDelay', 'controller', 'tuning', 'native', 'word', 'multiple'))
def test_other_configurations_are_unchanged(veto):
  cp = setup()
  if veto in ('passive', 'dashcamOnly', 'notCar'):
    setattr(cp, veto, True)
  elif veto == 'brand':
    cp.brand = 'subaru'
  elif veto in ('ev', 'hev'):
    cp.carFingerprint = CAR.HYUNDAI_KONA_EV_2ND_GEN if veto == 'ev' else CAR.HYUNDAI_KONA_HEV_2ND_GEN
  elif veto in ('angle', 'hda2', 'nonScc', 'noCcnc', 'noCamera'):
    flag = {'angle': HyundaiFlags.CANFD_ANGLE_STEERING, 'hda2': HyundaiFlags.CANFD_LKA_STEER_MSG,
            'nonScc': HyundaiFlags.NON_SCC, 'noCcnc': HyundaiFlags.CCNC, 'noCamera': HyundaiFlags.CANFD_CAMERA_SCC}[veto]
    cp.flags = cp.flags & ~int(flag) if veto.startswith('noC') else cp.flags | int(flag)
  elif veto in ('delay', 'nanDelay'):
    cp.steerActuatorDelay = .2 if veto == 'delay' else float('nan')
  elif veto == 'controller':
    cp.steerControlType = structs.CarParams.SteerControlType.angle
  elif veto == 'tuning':
    cp.lateralTuning.init('pid')
  elif veto == 'native':
    cp.safetyConfigs[-1].safetyModel = structs.CarParams.SafetyModel.hyundai
  elif veto == 'word':
    cp.safetyConfigs[-1].safetyParam |= 0x100
  else:
    cp.safetyConfigs = [{'safetyModel': 'hyundaiCanfd', 'safetyParam': 0x40C}] * 2
  assert seconds(cp) == 0.


@pytest.mark.parametrize('speed', (0., .3, .4, 5., 18., 35.))
def test_filter_and_lookahead_match_stable_without_changing_longitudinal(speed):
  tau = seconds(setup())
  times = np.array(ModelConstants.T_IDXS)
  previous = log.ModelDataV2.Action(desiredCurvature=.001)
  expected = .001
  for tick in range(100):
    yaw = .005 * np.sin(times + tick * .05)
    rate = .005 * np.cos(times + tick * .05)
    action_t = .3 + .075 + tau
    raw = get_curvature_from_plan(yaw, rate, times, speed, action_t)
    action = log.ModelDataV2.Action(desiredCurvature=float(raw) if speed > .3 else expected,
                                   desiredAcceleration=-.25, shouldStop=True)
    filter_action(action, previous, speed, tau)
    stable_raw = 2 * np.interp(action_t, times, yaw) / (max(1., speed) * action_t) - rate[0] / max(1., speed)
    expected = float(smooth_value(stable_raw, expected, .1)) if speed > .3 else expected
    assert action.desiredCurvature == pytest.approx(expected, abs=1e-8)
    assert action.desiredAcceleration == -.25 and action.shouldStop
    previous = action


def source(module):
  spec = importlib.util.find_spec(module)
  assert spec is not None and spec.origin is not None
  return ast.parse(Path(spec.origin).read_text()), spec.origin


@pytest.mark.parametrize('alpha', (False, True))
def test_common_model_join_keeps_filter_and_lookahead_matched(alpha):
  tree, filename = source('openpilot.selfdrive.modeld.modeld')
  cp = setup(alpha)
  action = log.ModelDataV2.Action(desiredCurvature=.001, desiredAcceleration=-.25, shouldStop=True)
  previous = log.ModelDataV2.Action(desiredCurvature=0.)
  env = {'CP': cp, 'receipt_owner': SimpleNamespace(load=object()), 'v_ego': 18., 'subaru_smoothing': 0.,
         'subaru_smooth_seconds': lambda *args: 0., 'volkswagen_smoothing': lambda *args: 0.,
         'kona_smoothing': seconds, 'kona_tau': seconds(cp), 'LAT_SMOOTH_SECONDS': 0.,
         'sm': {'lateralDelay': SimpleNamespace(lateralDelay=.3)}, 'action': action, 'prev_action': previous,
         'filter_action': filter_action, 'frame_delay': .05, 'action_delay': .025, 'inputs': {}, 'np': np}
  wanted = {'kona_tau', 'subaru_tau', 'volkswagen_tau', 'lat_delay', 'lat_action_t'}
  nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and
           (any(isinstance(t, ast.Name) and t.id in wanted for t in n.targets) or
            any(isinstance(t, ast.Subscript) and ast.unparse(t) == "inputs['lateral_control_params']" for t in n.targets))]
  filters = [n for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and
             isinstance(n.value.func, ast.Name) and n.value.func.id == 'filter_action']
  assert len(filters) == 1
  # All model generations feed the same action join.
  main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
  loop = next(n for n in main.body if isinstance(n, ast.While) and
              any(isinstance(x, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'lat_delay' for t in x.targets)
                  for x in ast.walk(n)))
  assert all(n.lineno < loop.lineno for n in nodes if isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'kona_tau')
  for node in sorted(nodes + filters, key=lambda n: n.lineno):
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), env)
  assert env['lat_delay'] == pytest.approx(.4)
  assert env['lat_action_t'] == pytest.approx(.475)
  assert env['inputs']['lateral_control_params'][1] == pytest.approx(.4)
  assert action.desiredCurvature == pytest.approx(float(smooth_value(.001, 0., .1)), abs=1e-9)
  assert action.desiredAcceleration == -.25 and action.shouldStop


@pytest.mark.parametrize('delay', (None, .3))
def test_controls_and_lane_centering_use_matching_cached_delay(delay):
  tree, filename = source('openpilot.selfdrive.controls.controlsd')
  env = {'self': SimpleNamespace(kona_smoothing=.1, sm={'lateralDelay': SimpleNamespace(lateralDelay=.3)}),
         'CS': SimpleNamespace(vEgo=18.), 'LAT_SMOOTH_SECONDS': 0., 'subaru_smooth_seconds': lambda *args: 0.,
         'control_maximum': lambda *args: 0., 'current_delay': lambda *args: delay, 'lane_now_ns': 1}
  nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and
           any(isinstance(t, ast.Name) and t.id in ('kona_tau', 'lat_delay') for t in n.targets)]
  lane = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and
              any(isinstance(t, ast.Name) and t.id == 'lane_delay' for t in n.targets) and isinstance(n.value, ast.Call))
  guard = next((n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == 'lane_delay is not None'), None)
  for node in sorted(nodes + [lane] + ([guard] if guard is not None else []), key=lambda n: n.lineno):
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), env)
  assert env['lat_delay'] == pytest.approx(.4)
  assert env['lane_delay'] is None if delay is None else env['lane_delay'] == pytest.approx(.4)
  state_control = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'state_control')
  assert not any(isinstance(n, ast.Call) and ast.unparse(n.func) == 'kona_smoothing' for n in ast.walk(state_control))
