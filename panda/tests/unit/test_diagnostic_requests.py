from unittest.mock import Mock

import pytest

from panda import Panda, McuType


@pytest.mark.parametrize('mcu', (McuType.F4, McuType.H7))
@pytest.mark.parametrize('version', ('DEV-source-DEBUG', 'DEV-source-DEBUG-FAULTTEST'))
@pytest.mark.parametrize('method,arguments,request,h7', (
  ('inject_fault', (2,), 0xea, False),
  ('inject_hardfault', (), 0xeb, False),
  ('inject_nmi', (), 0xec, False),
  ('inject_interrupt_fault', (2,), 0xef, True),
  ('inject_mpu_fault', (), 0xee, True),
  ('inject_stack_overflow', (), 0xf0, True),
  ('inject_self_test_failure', (2,), 0xed, True),
  ('inject_readback_fault', (2,), 0xe9, True),
))
def test_diagnostic_requests_require_dedicated_bench_firmware(mcu, version, method, arguments, request, h7):
  panda = object.__new__(Panda)
  panda._handle = Mock()
  panda.get_mcu_type = lambda: mcu
  panda.get_type = lambda: Panda.HW_TYPE_DOS if mcu == McuType.F4 else Panda.HW_TYPE_TRES
  panda.get_version = lambda: version
  if h7 and mcu == McuType.F4:
    with pytest.raises(NotImplementedError):
      getattr(panda, method)(*arguments)
    panda._handle.controlWrite.assert_not_called()
  elif not version.endswith('-FAULTTEST'):
    with pytest.raises(RuntimeError):
      getattr(panda, method)(*arguments)
    panda._handle.controlWrite.assert_not_called()
  else:
    getattr(panda, method)(*arguments)
    assert request != 0xf5
    panda._handle.controlWrite.assert_called_once()
    assert panda._handle.controlWrite.call_args.args[1] == request


def test_body_does_not_claim_fault_injection_capability():
  panda = object.__new__(Panda)
  panda._handle = Mock()
  panda.get_type = lambda: Panda.HW_TYPE_BODY
  panda.get_mcu_type = lambda: McuType.H7
  panda.get_version = lambda: 'DEV-source-DEBUG-FAULTTEST'
  with pytest.raises(NotImplementedError):
    panda.inject_fault(1)
  panda._handle.controlWrite.assert_not_called()


@pytest.mark.parametrize('release,injection,expected', (
  (False, '0', 'DEBUG'),
  (False, '1', 'DEBUG-FAULTTEST'),
  (True, '0', 'RELEASE'),
  (True, '1', None),
  (False, 'yes', None),
))
def test_build_requires_explicit_debug_fault_injection(monkeypatch, release, injection, expected):
  import ast
  from pathlib import Path
  from SCons.Script import File

  root = Path(__file__).resolve().parents[2]
  source = root / 'SConscript'
  tree = ast.parse(source.read_text())
  prefix = []
  for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == 'objcopy':
      break
    prefix.append(node)
  if release:
    monkeypatch.setenv('RELEASE', '1')
  else:
    monkeypatch.delenv('RELEASE', raising=False)
  monkeypatch.setenv('CERT', str(root / 'board/crypto/certs/debug'))
  monkeypatch.setenv('PANDA_FAULT_INJECTION', injection)
  namespace = {'File': File}
  code = compile(ast.Module(body=prefix, type_ignores=[]), str(source), 'exec')
  if expected is None:
    with pytest.raises(ValueError):
      exec(code, namespace)
  else:
    exec(code, namespace)
    assert namespace['BUILD_TYPE'] == expected
    assert ('-DPANDA_FAULT_INJECTION' in namespace['common_flags']) == expected.endswith('-FAULTTEST')
