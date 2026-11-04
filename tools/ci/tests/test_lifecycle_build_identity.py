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

  def test_panda_faults_is_an_exact_optional_output_with_pinned_inputs(self):
    name = 'openpilot/selfdrive/pandad/tests/test_panda_faults'
    recipe = 'openpilot/selfdrive/pandad/SConscript'
    self.assertEqual(self.inventory.get(name), {'recipe': recipe, 'required_tracked': False})
    actual_recipe = Path(__file__).parents[3].joinpath(recipe).read_text()
    self.assertIn("env.Program('tests/test_panda_faults', ['tests/test_panda_faults.cc']", actual_recipe)
    output = self.root / name
    source = self.root / (name + '.cc')
    source.write_text('native faults test source\n')
    subprocess.run([*GIT, 'rm', '--cached', name], cwd=self.root, check=True, capture_output=True)
    subprocess.run([*GIT, 'add', name + '.cc'], cwd=self.root, check=True)
    subprocess.run([*GIT, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Faults test source'],
                   cwd=self.root, check=True)
    output.unlink()
    checkout = helper.build_identity(self.root)
    output.write_bytes(b'independently built faults executable')
    output.chmod(0o755)
    built = helper.build_identity(self.root)
    self.assertTrue(helper.same_build_inputs(checkout, built))
    self.assertIsNone(checkout['declared_outputs'][name])
    self.assertEqual(built['declared_outputs'][name], helper.file_record(output))
    self.assertNotIn(name, built['inputs']['untracked'])
    self.assertEqual(built['inputs']['tracked_inputs'][name + '.cc'], helper.file_record(source))
    self.assertEqual(built['inputs']['recipes'][recipe], helper.file_record(self.root / recipe))
    for mutation in ('bytes', 'mode'):
      with self.subTest(output_mutation=mutation):
        if mutation == 'bytes':
          output.write_bytes(b'another independent faults build')
        else:
          output.chmod(0o644)
        rebuilt = helper.build_identity(self.root)
        self.assertTrue(helper.same_build_inputs(built, rebuilt))
        self.assertNotEqual(built, rebuilt)  # The per-job exact before/after guard must still reject this.
    for path in (source, self.root / recipe, self.root / 'opendbc_repo/native_input.h'):
      with self.subTest(input_mutation=str(path.relative_to(self.root))):
        original = path.read_bytes()
        path.write_bytes(b'changed real input')
        self.assertFalse(helper.same_build_inputs(built, helper.build_identity(self.root)))
        path.write_bytes(original)
    source.chmod(0o755)
    self.assertFalse(helper.same_build_inputs(built, helper.build_identity(self.root)))
    source.chmod(0o644)
    for name_suffix in ('_other', '.input.h'):
      neighbor = output.with_name(output.name + name_suffix)
      neighbor.write_bytes(b'undeclared neighboring input')
      self.assertFalse(helper.same_build_inputs(built, helper.build_identity(self.root)))
      neighbor.unlink()
    for metadata in ('missing', 'malformed'):
      forged = copy.deepcopy(built)
      if metadata == 'missing':
        del forged['declared_outputs'][name]
      else:
        forged['declared_outputs'][name] = {'sha256': 'bad', 'mode': 0o755}
      with self.assertRaises(ValueError):
        helper.same_build_inputs(checkout, forged)

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
