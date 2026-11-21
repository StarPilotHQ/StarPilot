import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class TestModelArtifactGitPolicy(unittest.TestCase):
  def test_only_small_numbered_packages_are_publishable(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
      for name in ('.gitignore', '.gitattributes'):
        (root / name).write_bytes((ROOT / name).read_bytes())
      base = 'openpilot/selfdrive/modeld/models/'
      publishable = [name + suffix for name in ('dmonitoring_model_tinygrad.pkl', 'cheddar_driving_tinygrad.pkl')
                     for suffix in ('.chunk01of02', '.chunk01of100', '.chunk100of100', '.chunk1024of1024', '.chunkmanifest', '.chunksha256')]
      ignored = ['cheddar_driving_tinygrad.pkl', 'cheddar_driving_tinygrad.pkl.unchunked', 'driving_tinygrad.pkl', 'dmonitoring_model_tinygrad.pkl', 'driving_tinygrad.pkl.unchunked',
                 'driving_tinygrad.pkl.chunk01of02', 'big_driving_tinygrad.pkl', 'big_driving_tinygrad.pkl.chunk01of02',
                 'driving_tinygrad.pkl.chunkNOTESof02', 'other_driving_tinygrad.pkl.chunk01of02']
      for name, expected in [(n, False) for n in publishable] + [(n, True) for n in ignored]:
        with self.subTest(name=name):
          result = subprocess.run(['git', 'check-ignore', '--quiet', '--no-index', '--', base + name], cwd=root)
          self.assertEqual(result.returncode, 0 if expected else 1)
      result = subprocess.check_output(['git', 'check-attr', 'text', '--', base + 'driving_tinygrad.pkl.chunk01of02'], cwd=root, text=True)
      self.assertTrue(result.endswith('text: unset\n'))


if __name__ == '__main__':
  unittest.main()
