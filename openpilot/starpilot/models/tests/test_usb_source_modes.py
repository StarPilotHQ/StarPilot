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
