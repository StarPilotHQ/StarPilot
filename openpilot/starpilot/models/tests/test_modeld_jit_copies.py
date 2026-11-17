"""The production modern copy helper must see fresh NumPy storage on every call."""
import __future__
import ast
from pathlib import Path

import numpy as np


def test_modern_copy_jit_reloads_mutated_host_inputs():
  from tinygrad.tensor import Tensor
  path = Path(__file__).resolve().parents[3] / 'selfdrive/modeld/modeld.py'
  function = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'compile_copy')
  namespace = {}
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec', flags=__future__.annotations.compiler_flag), namespace)
  source = np.arange(32, dtype=np.float32)
  tensor = Tensor(source, device='NPY').to('CPU')
  copy = namespace['compile_copy'](tensor)
  for iteration in range(5):
    source[:] = np.arange(32, dtype=np.float32) + iteration * 0.25
    np.testing.assert_array_equal(copy().numpy(), source)
