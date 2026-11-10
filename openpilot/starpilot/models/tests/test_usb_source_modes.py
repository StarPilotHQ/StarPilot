"""Unsupported source admission fails before device or submission access."""
import ast
from pathlib import Path

import pytest


def test_usb_guard_rejects_explicit_fresh_qcom_source_before_access():
  source = Path(__file__).resolve().parents[1] / 'runtime_bridge/usb.py'
  tree = ast.parse(source.read_text())
  owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'USBProtection')
  method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == 'protect_call')
  namespace = {}
  exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
  with pytest.raises(ValueError, match='not qualified'):
    namespace['protect_call'](object(), object(), fresh_source=True)


@pytest.mark.parametrize(('fresh_source', 'qualified'), [(False, True), (True, True), (True, False)])
def test_dom_guard_preserves_qualified_qcom_dispatch(fresh_source, qualified):
  from types import SimpleNamespace

  calls = []
  class Protection:
    def protect_call(self, call, *, fresh_source=False):
      calls.append((call, fresh_source))
      return call

  class HCQInfo:
    device = ('QCOM',)

  sink = object()
  namespace = {}
  namespace.update(Protection=Protection, HCQInfo=HCQInfo, Ops=SimpleNamespace(SINK=sink))
  root = Path(__file__).resolve().parents[1] / 'runtime_bridge'
  for filename, classname in [('usb.py', 'USBProtection'), ('dom_usb.py', 'DomUSBProtection')]:
    source = root / filename
    tree = ast.parse(source.read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == classname)
    exec(compile(ast.Module(body=[owner], type_ignores=[]), str(source), 'exec'), namespace)

  guard_type = namespace['DomUSBProtection']
  assert isinstance(guard_type, type)
  guard = guard_type.__new__(guard_type)
  call = SimpleNamespace(arg=SimpleNamespace(aux=HCQInfo() if qualified else object()), body=SimpleNamespace(op=sink))
  if qualified:
    assert guard.protect_call(call, fresh_source=fresh_source) is call
    assert calls == [(call, True)]
  else:
    with pytest.raises(ValueError, match='not qualified'):
      guard.protect_call(call, fresh_source=True)
    assert calls == []


def test_runtime_policy_matches_packaged_guard_sources():
  from openpilot.starpilot.models.runtime_bridge import runtime
  runtime._verify_policy()


def test_runtime_policy_still_rejects_each_modified_guard(tmp_path, monkeypatch):
  import json
  import shutil
  from openpilot.starpilot.models.runtime_bridge import runtime

  root = Path(runtime.__file__).parent
  policy = json.loads((root / 'guard-policy.json').read_text())
  for name in ['guard-policy.json', *policy['files']]:
    shutil.copyfile(root / name, tmp_path / name)
  monkeypatch.setattr(runtime, '__file__', str(tmp_path / 'runtime.py'))
  runtime._verify_policy()
  for name in ['guard-policy.json', *policy['files']]:
    path = tmp_path / name
    original = path.read_bytes()
    path.write_bytes(original + b'\n')
    with pytest.raises(RuntimeError, match='changed'):
      runtime._verify_policy()
    path.write_bytes(original)
  runtime._verify_policy()
