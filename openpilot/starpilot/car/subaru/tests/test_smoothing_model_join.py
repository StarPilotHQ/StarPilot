"""Execute the production common action join with each installed action owner."""
import ast
from pathlib import Path
from types import SimpleNamespace
import math
import numpy as np
import pytest
from openpilot.cereal import log
from openpilot.selfdrive.modeld import modeld
from openpilot.starpilot.car.subaru.smoothing import filter_action
from openpilot.starpilot.models.runner import CatalogModelState
from openpilot.starpilot.models.laboratory_runner import LaboratoryPair


def common_join():
  tree = ast.parse(Path(modeld.__file__).read_text())
  matches = []
  for node in ast.walk(tree):
    body = getattr(node, 'body', None)
    if not isinstance(body, list):
      continue
    for index, statement in enumerate(body):
      if (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call) and
          ast.unparse(statement.value.func) == 'filter_action'):
        assert index > 0
        statements = body[index - 1:index + 2]
        assert len(statements) == 3
        assert sum(isinstance(call, ast.Call) and ast.unparse(call.func) == 'model.get_action_from_model'
                   for call in ast.walk(statements[0])) == 1
        assert ast.unparse(statements[1]) == 'filter_action(action, prev_action, v_ego, subaru_tau)'
        assert ast.unparse(statements[2]) == 'prev_action = action'
        matches.append(ast.Module(body=statements, type_ignores=[]))
  assert len(matches) == 1
  return compile(matches[0], modeld.__file__, 'exec')


@pytest.mark.parametrize('owner', ['standard', 'catalog', 'jetlink', 'laboratory'])
@pytest.mark.parametrize('tau', [0., .4])
def test_actual_common_join_once_and_acceleration_unchanged(owner, tau, monkeypatch):
  if owner in ('standard', 'laboratory'):
    function = modeld.ModelState.get_action_from_model
  elif owner == 'catalog':
    catalog = CatalogModelState.__new__(CatalogModelState)
    catalog.behavior_version = 'v15'
    function = catalog.get_action_from_model
  else:
    monkeypatch.syspath_prepend(str(Path(modeld.__file__).resolve().parents[3] / 'jetlink_repo'))
    from openpilot.starpilot.models.jetlink_adapter import _adapter_class
    function = _adapter_class()().model_face().get_action_from_model
  previous = log.ModelDataV2.Action(desiredCurvature=.01, desiredAcceleration=-.2)
  outputs = {'action': np.array([[.08, .5]])}
  baseline = function(outputs, previous, .2, .2, 2.)
  model = (LaboratoryPair.__new__(LaboratoryPair) if owner == 'laboratory' else
           SimpleNamespace(get_action_from_model=function))
  paired_action = function(outputs, previous, .2, .2, 2.) if owner == 'laboratory' else None
  scope = {'model': model, 'LaboratoryPair': LaboratoryPair, 'laboratory_action': paired_action, 'model_output': outputs, 'prev_action': previous,
           'lat_action_t': .2, 'long_action_t': .2, 'v_ego': 2., 'subaru_tau': tau, 'filter_action': filter_action}
  exec(common_join(), scope)
  action = scope['action']
  alpha = 1. - math.exp(-.05 / tau) if tau > 0. else 1.
  expected = alpha * baseline.desiredCurvature + (1. - alpha) * previous.desiredCurvature
  assert action.desiredCurvature == pytest.approx(expected)
  assert action.desiredAcceleration == baseline.desiredAcceleration and action.shouldStop == baseline.shouldStop
  assert scope['prev_action'] is action
