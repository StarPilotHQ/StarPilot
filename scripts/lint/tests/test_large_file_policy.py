import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).parents[1] / 'check_added_large_files.py'
spec = importlib.util.spec_from_file_location('large_file_policy', PATH)
assert spec is not None and spec.loader is not None
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class LargeFilePolicyTest(unittest.TestCase):
  def check(self, name, size, binary=False, pointer=False):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      path = root / name
      path.parent.mkdir(parents=True, exist_ok=True)
      with path.open('wb') as output:
        output.write(b'version https://git-lfs.github.com/spec/v1\n' if pointer else b'ordinary data')
        output.truncate(size)
      info = path.stat()
      with (patch.object(policy, 'binary_files', return_value={name} if binary else set()),
            patch.object(policy.os, 'stat', return_value=info), patch('builtins.open', side_effect=lambda *_: path.open('rb'))):
        return policy.check_added_large_files([name], 120)

  def test_generated_text_and_exact_retained_fixture(self):
    self.assertEqual(self.check('openpilot/cereal/gen/cpp/custom.capnp.h', 700000), 0)
    self.assertEqual(self.check('openpilot/starpilot/lateral/tests/fixtures/oct2_turns.json', 200000), 0)
    self.assertEqual(self.check('openpilot/cereal/gen/cpp/arbitrary.h', 200000), 1)
    self.assertEqual(self.check('openpilot/starpilot/lateral/tests/fixtures/arbitrary.json', 200000), 1)

  def test_generated_translation_catalogs_keep_exact_paths_and_hard_guards(self):
    for name in (
      'openpilot/selfdrive/ui/translations/app.pot',
      'openpilot/selfdrive/ui/translations/app_de.po',
      'openpilot/selfdrive/ui/translations/app_es.po',
      'openpilot/selfdrive/ui/translations/app_fr.po',
      'openpilot/selfdrive/ui/translations/app_ja.po',
      'openpilot/selfdrive/ui/translations/app_ko.po',
      'openpilot/selfdrive/ui/translations/app_vi.po',
      'openpilot/starpilot/ui/translation_messages.json',
    ):
      self.assertEqual(self.check(name, 1000000), 0)
      self.assertEqual(self.check(name, 100 * 1024 * 1024 + 1), 1)
      self.assertEqual(self.check(name, 1000000, pointer=True), 1)
    for name in ('openpilot/selfdrive/ui/translations/arbitrary.po',
                 'openpilot/starpilot/ui/arbitrary.json'):
      self.assertEqual(self.check(name, 200000), 1)

  def test_binary_classification_does_not_bypass_hard_guards(self):
    self.assertEqual(self.check('native.so', 500000, binary=True), 0)
    self.assertEqual(self.check('native.so', 100 * 1024 * 1024 + 1, binary=True), 1)
    self.assertEqual(self.check('native.so', 500000, binary=True, pointer=True), 1)
    self.assertEqual(self.check('openpilot/cereal/gen/cpp/custom.capnp.h', 100 * 1024 * 1024 + 1), 1)
    self.assertEqual(self.check('openpilot/cereal/gen/cpp/custom.capnp.h', 500000, pointer=True), 1)


if __name__ == '__main__':
  unittest.main()
