import ast
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[5]


def test_starport_key_and_build_registration_are_explicit():
  keys = (ROOT / 'openpilot/common/params_keys.h').read_text()
  assert '{"StarPortEnabled", {PERSISTENT | DONT_LOG, BOOL, "0"}}' in keys
  assert '{"BluetoothEnabled", {PERSISTENT, BOOL}}' in keys
  assert "openpilot/starpilot/system/starport/SConscript" in (ROOT / 'SConstruct').read_text()


def test_manager_keeps_missing_or_false_feature_off():
  source = (ROOT / 'openpilot/system/manager/process_config.py').read_text()
  tree = ast.parse(source)
  predicate = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'starport_enabled')
  scope: dict = {'COMMA_HARDWARE': True, 'platform': SimpleNamespace(system=lambda: 'Linux'),
           'Params': object, 'car': SimpleNamespace(CarParams=object)}
  exec(compile(ast.Module(body=[predicate], type_ignores=[]), '<manager admission>', 'exec'), scope)
  class Params:
    def __init__(self, enabled): self.enabled = enabled
    def get_bool(self, key):
      assert key == 'StarPortEnabled'
      return self.enabled
  admit = scope['starport_enabled']
  assert not admit(False, Params(False), None)
  assert admit(False, Params(True), None)
  scope['COMMA_HARDWARE'] = False
  assert not admit(False, Params(True), None)
  scope['COMMA_HARDWARE'] = True
  scope['platform'] = SimpleNamespace(system=lambda: 'Darwin')
  assert not admit(False, Params(True), None)
  process = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and
                 isinstance(node.func, ast.Name) and node.func.id == 'PythonProcess' and len(node.args) >= 3 and
                 isinstance(node.args[0], ast.Constant) and node.args[0].value == 'starportd')
  assert isinstance(process.args[1], ast.Constant) and process.args[1].value == 'openpilot.starpilot.system.starport.daemon'
  assert isinstance(process.args[2], ast.Name) and process.args[2].id == 'starport_enabled'


def test_typed_preference_controls_actual_manager_admission(tmp_path, monkeypatch):
  from openpilot.common.params import Params
  from openpilot.system.manager import process_config

  params = Params(str(tmp_path))
  monkeypatch.setattr(process_config, 'COMMA_HARDWARE', True)
  monkeypatch.setattr(process_config.platform, 'system', lambda: 'Linux')
  process = process_config.managed_processes['starportd']

  assert params.get_default_value('StarPortEnabled') is False
  assert params.get('StarPortEnabled') is None
  assert not process.should_run(False, params, None)
  params.put_bool('StarPortEnabled', False, block=True)
  assert not process.should_run(True, params, None)
  params.put_bool('StarPortEnabled', True, block=True)
  assert process.should_run(False, params, None)
  params.remove('StarPortEnabled')
  assert not process.should_run(False, params, None)
  assert process.proc is None
