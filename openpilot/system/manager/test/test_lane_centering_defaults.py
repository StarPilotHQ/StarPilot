import ast
import os
from pathlib import Path
import tempfile
import unittest

from openpilot.common.params import Params


class TestLaneCenteringDefaults(unittest.TestCase):
  def test_typed_enrollment_preserves_existing_choices(self):
    source = Path(__file__).resolve().parents[1] / "manager.py"
    tree = ast.parse(source.read_text())
    loop = next(node for node in ast.walk(tree) if isinstance(node, ast.For) and
                ast.unparse(node.iter) == "params.all_keys()")
    code = compile(ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[])), str(source), "exec")
    for upgrade in (False, True):
      for saved in (None, b"0", b"1", b"malformed"):
        with self.subTest(upgrade=upgrade, saved=saved), tempfile.TemporaryDirectory() as root:
          params = Params(root)
          if upgrade:
            params.put("Version", "7.0", block=True)
          path = Path(params.get_param_path("LaneCentering"))
          if saved is not None:
            path.write_bytes(saved)
          exec(code, {"params": params, "os": os})
          self.assertEqual(path.read_bytes(), saved if saved is not None else b"0" if upgrade else b"1")
