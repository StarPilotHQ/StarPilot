import ast
from pathlib import Path

import pytest

from openpilot.common.params import Params


@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("record_locked", [False, True])
def test_fleet_dm_lock_is_independent_of_recording(tmp_path, locked, record_locked):
  params = Params(str(tmp_path))
  params.put_bool("AlwaysOnDM", False, block=True)
  params.put_bool("AlwaysOnDMLock", locked, block=True)
  params.put_bool("RecordFrontLock", record_locked, block=True)
  path = Path(__file__).resolve().parents[1] / "manager.py"
  function = next(node for node in ast.parse(path.read_text()).body
                  if isinstance(node, ast.FunctionDef) and node.name == "manager_init")
  branch = next(node for node in function.body if isinstance(node, ast.If)
                and ast.unparse(node.test) == "params.get_bool('AlwaysOnDMLock')")
  exec(compile(ast.Module(body=[branch], type_ignores=[]), str(path), "exec"), {"params": params})
  assert params.get_bool("AlwaysOnDM") is locked
  assert params.get_bool("RecordFrontLock") is record_locked
