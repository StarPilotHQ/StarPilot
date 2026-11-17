import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.drive_helpers import smooth_value
from openpilot.selfdrive.modeld.constants import LAT_SMOOTH_SECONDS


def action_function():
  path = Path(__file__).resolve().parents[1] / "modeld.py"
  function = next(node for node in ast.parse(path.read_text()).body
                  if isinstance(node, ast.FunctionDef) and node.name == "get_action_from_model")
  namespace = {"np": np, "log": log, "smooth_value": smooth_value,
               "LONG_SMOOTH_SECONDS": 0.3, "LAT_SMOOTH_SECONDS": LAT_SMOOTH_SECONDS,
               "MIN_LAT_CONTROL_SPEED": 0.3, "should_stop": lambda speed, accel: False}
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
  return namespace["get_action_from_model"]


@pytest.mark.parametrize("speed", [0.0, 0.3, 0.31, 1.0, 2.0, 2.5, 5.0, 30.0])
def test_action_curvature_low_speed_threshold(speed):
  previous = log.ModelDataV2.Action(desiredCurvature=0.01, desiredAcceleration=0.1)
  action = action_function()({"action": np.array([[0.25, 0.5]])}, previous, 0.2, 0.2, speed)
  expected = smooth_value(0.25 / max(2.5, speed) ** 2, 0.01, LAT_SMOOTH_SECONDS) if speed > 0.3 else 0.01
  assert action.desiredCurvature == pytest.approx(expected)
  assert action.desiredAcceleration == pytest.approx(smooth_value(0.5, 0.1, 0.3))
  assert not action.shouldStop


def test_legacy_plan_action_is_unchanged():
  function = action_function()
  calls = []
  def curvature(*args):
    calls.append(args)
    return 0.02
  function.__globals__.update(Plan=SimpleNamespace(VELOCITY=0, ACCELERATION=1, T_FROM_CURRENT_EULER=2, ORIENTATION_RATE=3),
                             ModelConstants=SimpleNamespace(T_IDXS=np.array([0.0, 1.0])),
                             get_accel_from_plan=lambda *args, **kwargs: 0.4,
                             get_curvature_from_plan=curvature)
  previous = log.ModelDataV2.Action(desiredCurvature=0.01, desiredAcceleration=0.1)
  action = function({"plan": np.zeros((1, 2, 4, 3))}, previous, 0.2, 0.2, 1.0)
  assert len(calls) == 1
  assert action.desiredCurvature == pytest.approx(smooth_value(0.02, 0.01, LAT_SMOOTH_SECONDS))
  assert action.desiredAcceleration == pytest.approx(smooth_value(0.4, 0.1, 0.3))
