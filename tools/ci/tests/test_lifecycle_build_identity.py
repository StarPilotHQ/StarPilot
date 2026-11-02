import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

GIT = ['git', '--no-optional-locks', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false']

spec = importlib.util.spec_from_file_location('private_build_identity', Path(__file__).parents[1] / 'lifecycle_shards.py')
assert spec is not None and spec.loader is not None
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class BuildIdentityTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.root = Path(self.directory.name)
    self.inventory_bytes = Path(__file__).parents[1].joinpath('lifecycle_build_outputs.json').read_bytes()
    self.inventory = json.loads(self.inventory_bytes)
    inventory_path = self.root / 'tools/ci/lifecycle_build_outputs.json'
    inventory_path.parent.mkdir(parents=True)
    inventory_path.write_bytes(self.inventory_bytes)
    for name in self.inventory:
      path = self.root / name
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_bytes(b'build output')
    for name in {'SConstruct', *[row['recipe'] for row in self.inventory.values()], 'opendbc_repo/native_input.h'}:
      path = self.root / name
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text('source input\n')
    subprocess.run([*GIT, 'init', '-q'], cwd=self.root, check=True)
    subprocess.run([*GIT, 'add', '.'], cwd=self.root, check=True)
    subprocess.run([*GIT, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Fixture'], cwd=self.root, check=True)

  def test_only_named_outputs_can_differ_with_identical_inputs(self):
    first = helper.build_identity(self.root)
    (self.root / 'msgq_repo/msgq/_ipc.so').write_bytes(b'independent genuine build')
    second = helper.build_identity(self.root)
    self.assertTrue(helper.same_build_inputs(first, second))
    self.assertNotEqual(first['declared_outputs'], second['declared_outputs'])
    self.assertNotEqual(first, second)  # The per-job before/after guard still rejects an in-test mutation.

  def test_source_native_input_and_recipe_changes_are_denied(self):
    first = helper.build_identity(self.root)
    for name in ('opendbc_repo/native_input.h', 'SConstruct'):
      path = self.root / name
      original = path.read_bytes()
      path.write_bytes(b'changed source')
      self.assertFalse(helper.same_build_inputs(first, helper.build_identity(self.root)))
      path.write_bytes(original)

  def test_undeclared_input_path_is_pinned(self):
    first = helper.build_identity(self.root)
    path = self.root / 'opendbc_repo/undeclared.so'
    path.write_bytes(b'unregistered artifact')
    self.assertFalse(helper.same_build_inputs(first, helper.build_identity(self.root)))

  def test_logger_metadata_test_is_an_exact_optional_build_output(self):
    name = 'openpilot/system/loggerd/tests/test_model_metadata'
    recipe = 'openpilot/system/loggerd/SConscript'
    self.assertEqual(self.inventory.get(name), {'recipe': recipe, 'required_tracked': False})
    output = self.root / name
    source = self.root / (name + '.cc')
    source.write_text('native test source\n')
    subprocess.run([*GIT, 'rm', '--cached', name], cwd=self.root, check=True, capture_output=True)
    subprocess.run([*GIT, 'add', str(source.relative_to(self.root))], cwd=self.root, check=True)
    subprocess.run([*GIT, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Untracked native test output'],
                   cwd=self.root, check=True)
    output.chmod(0o755)
    built = helper.build_identity(self.root)
    self.assertNotIn(name, built['inputs']['untracked'])
    self.assertEqual(built['declared_outputs'][name], helper.file_record(output))
    self.assertIn(name + '.cc', built['inputs']['tracked_inputs'])
    self.assertIn(recipe, built['inputs']['recipes'])
    output.unlink()
    checkout = helper.build_identity(self.root)
    self.assertIsNone(checkout['declared_outputs'][name])
    self.assertTrue(helper.same_build_inputs(built, checkout))
    output.write_bytes(b'independently rebuilt native test')
    rebuilt = helper.build_identity(self.root)
    self.assertTrue(helper.same_build_inputs(built, rebuilt))
    self.assertNotEqual(built['declared_outputs'][name]['sha256'], rebuilt['declared_outputs'][name]['sha256'])
    self.assertNotEqual(built, rebuilt)  # Per-job before/after still fails closed.
    for path in (source, self.root / recipe):
      original = path.read_bytes()
      path.write_bytes(b'changed input')
      self.assertFalse(helper.same_build_inputs(built, helper.build_identity(self.root)))
      path.write_bytes(original)
    output.with_name(output.name + '_other').write_bytes(b'undeclared neighboring output')
    self.assertFalse(helper.same_build_inputs(built, helper.build_identity(self.root)))
    forged = copy.deepcopy(built)
    del forged['declared_outputs'][name]
    with self.assertRaises(ValueError):
      helper.same_build_inputs(built, forged)

  def test_pandad_exact_outputs_preserve_checkout_build_identity(self):
    recipe = 'openpilot/selfdrive/pandad/SConscript'
    objects = [f'openpilot/selfdrive/pandad/{name}.o' for name in ('main', 'panda', 'panda_safety', 'panda_usb', 'pandad', 'spi')]
    executable = 'openpilot/selfdrive/pandad/tests/test_panda_debug'
    for name in [*objects, executable]:
      self.assertEqual(self.inventory.get(name), {'recipe': recipe, 'required_tracked': name != executable})
    source = self.root / (executable + '.cc')
    source.write_text('native test source\n')
    subprocess.run([*GIT, 'rm', '--cached', executable], cwd=self.root, check=True, capture_output=True)
    subprocess.run([*GIT, 'add', executable + '.cc'], cwd=self.root, check=True)
    subprocess.run([*GIT, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Pandad source fixture'],
                   cwd=self.root, check=True)
    (self.root / executable).unlink()
    checkout = helper.build_identity(self.root)
    for name in [*objects, executable]:
      (self.root / name).write_bytes(b'independent host build')
    (self.root / executable).chmod(0o755)
    built = helper.build_identity(self.root)
    self.assertTrue(helper.same_build_inputs(checkout, built))
    self.assertNotEqual(checkout, built)  # In-job output mutation still fails the before/after guard.
    self.assertIn(executable + '.cc', built['inputs']['tracked_inputs'])
    self.assertIn(recipe, built['inputs']['recipes'])
    for name in [*objects, executable]:
      self.assertEqual(built['declared_outputs'][name], helper.file_record(self.root / name))
      forged = copy.deepcopy(built)
      del forged['declared_outputs'][name]
      with self.assertRaises(ValueError):
        helper.same_build_inputs(checkout, forged)
    for path in (source, self.root / recipe):
      original = path.read_bytes()
      path.write_bytes(b'changed input')
      self.assertFalse(helper.same_build_inputs(built, helper.build_identity(self.root)))
      path.write_bytes(original)
    neighbor = self.root / 'openpilot/selfdrive/pandad/undeclared.o'
    neighbor.write_bytes(b'undeclared object')
    self.assertFalse(helper.same_build_inputs(built, helper.build_identity(self.root)))
    neighbor.unlink()
    for name in objects:
      path = self.root / name
      original = path.read_bytes()
      path.unlink()
      with self.assertRaises(ValueError):
        helper.build_identity(self.root)
      path.write_bytes(original)

  def test_missing_declared_artifact_is_denied(self):
    (self.root / 'msgq_repo/msgq/_ipc.so').unlink()
    with self.assertRaises(ValueError):
      helper.build_identity(self.root)

  def test_nonoutput_symlink_target_is_pinned(self):
    path = self.root / 'opendbc_repo/input-link.h'
    path.symlink_to('native_input.h')
    subprocess.run([*GIT, 'add', '.'], cwd=self.root, check=True)
    subprocess.run([*GIT, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Symlink'],
                   cwd=self.root, check=True)
    first = helper.build_identity(self.root)
    path.unlink()
    path.symlink_to('other_source.h')
    self.assertFalse(helper.same_build_inputs(first, helper.build_identity(self.root)))

  def test_forged_or_stripped_provenance_is_denied(self):
    first = helper.build_identity(self.root)
    for field in ('inputs', 'declared_outputs'):
      forged = copy.deepcopy(first)
      del forged[field]
      with self.assertRaises(ValueError):
        helper.same_build_inputs(first, forged)
    forged = copy.deepcopy(first)
    forged['inputs']['output_inventory_sha256'] = 'f' * 64
    with self.assertRaises(ValueError):
      helper.same_build_inputs(first, forged)


if __name__ == '__main__':
  unittest.main()
