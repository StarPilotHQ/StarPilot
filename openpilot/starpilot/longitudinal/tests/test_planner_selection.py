"""Real saved-source/CAS selection; upstream algorithm imports qualify separately."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from openpilot.starpilot.longitudinal.planner_selection import KEY, read_selection, run_selected, save_selection


class Params:
  def __init__(self, root):
    self.root = Path(root)
    (self.root / 'd').mkdir()

  def get_param_path(self, key):
    return str(self.root / 'd' / key)


class TestPlannerSelection(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.path = Path(self.params.get_param_path(KEY))

  def test_default_on_exact_off_and_invalid_sources(self):
    self.assertEqual((read_selection(self.params).starpilot, read_selection(self.params).valid), (True, True))
    for raw, starpilot, valid in ((b'0', False, True), (b'1', True, True), (b'0\n', True, False),
                                 (b'', True, False), (b'garbage', True, False), (b'0' * 9, True, False)):
      self.path.write_bytes(raw)
      value = read_selection(self.params)
      self.assertEqual((value.starpilot, value.valid), (starpilot, valid))
    self.path.unlink()
    self.path.symlink_to(self.params.root / 'missing')
    self.assertTrue(read_selection(self.params).starpilot)
    self.assertFalse(read_selection(self.params).valid)

  def test_off_calls_only_separate_upstream_entrypoint(self):
    self.path.write_bytes(b'0')
    calls = []
    def importer(name):
      calls.append(name)
      return SimpleNamespace(main=lambda: calls.append('upstream_loop'))
    run_selected(self.params, lambda: self.fail('StarPilot host executed while Off'), importer=importer)
    self.assertEqual(calls, ['openpilot.starpilot.longitudinal.upstream.plannerd', 'upstream_loop'])

  def test_on_never_imports_upstream_and_latches_during_loop(self):
    def starpilot():
      self.assertTrue(save_selection(self.params, 'Off', None, authorized=lambda: True))
      # Existing selected process is not swapped by a preference write.
      return 'same_starpilot_session'
    self.assertEqual(run_selected(self.params, starpilot, importer=lambda _: self.fail('Upstream imported while On')),
                     'same_starpilot_session')
    self.assertFalse(read_selection(self.params).starpilot)

  def test_persistent_selection_cas_and_authority(self):
    self.assertFalse(save_selection(self.params, 'Off', None, authorized=lambda: False))
    self.assertFalse(self.path.exists())
    self.assertTrue(save_selection(self.params, 'Off', None, authorized=lambda: True))
    self.assertEqual(self.path.read_bytes(), b'0')
    self.assertFalse(save_selection(self.params, 'On', None, authorized=lambda: True))
    self.assertFalse(save_selection(self.params, 'invalid', b'0', authorized=lambda: True))
    self.assertTrue(save_selection(self.params, 'On', b'0', authorized=lambda: True))
    self.assertTrue(read_selection(self.params).starpilot)

  def test_authority_loss_during_commit_preserves_source(self):
    self.path.write_bytes(b'1')
    calls = 0
    def authorized():
      nonlocal calls
      calls += 1
      return calls < 3
    self.assertFalse(save_selection(self.params, 'Off', b'1', authorized=authorized))
    self.assertEqual(self.path.read_bytes(), b'1')

  def test_pinned_upstream_files_and_isolated_mpc_imports(self):
    upstream = Path(__file__).parents[1] / 'upstream'
    manifest = json.loads((upstream / 'provenance.json').read_bytes())
    self.assertEqual(manifest['upstream_commit'], '521db4c825d37eb5f29acf955daa88da003e4433')
    for filename, entry in manifest['files'].items():
      self.assertEqual(hashlib.sha256((upstream / filename).read_bytes()).hexdigest(), entry['adapted_sha256'])
    planner = ast.parse((upstream / 'planner.py').read_text())
    imports = [node.module for node in planner.body if isinstance(node, ast.ImportFrom)]
    self.assertIn('openpilot.starpilot.longitudinal.upstream.mpc', imports)
    self.assertNotIn('openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc', imports)
    self.assertEqual({value for value in imports if value and value.startswith('openpilot.starpilot.')}, {
      'openpilot.starpilot.longitudinal.upstream.mpc', 'openpilot.starpilot.longitudinal.cruise_ceiling',
    })


if __name__ == '__main__':
  unittest.main()
