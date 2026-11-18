from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BuildTargetsTest(unittest.TestCase):
  def test_build_targets_reject_bridge_and_invalid_refs(self):
    for workflow in ('compile_starpilot.yaml', 'compile_bluescreens.yaml'):
      source = (ROOT / '.github/workflows' / workflow).read_text()
      guard = source.split('          branch="${TARGET_REF#refs/heads/}"', 1)[1].split('\n      - ', 1)[0]
      script = 'set -eu\nbranch="${TARGET_REF#refs/heads/}"\n' + '\n'.join(line[10:] if line.startswith('          ') else line for line in guard.splitlines())
      with tempfile.TemporaryDirectory() as temporary:
        for target in ('stable', 'testing', 'bluescreensonly2', 'refs/heads/stable'):
          with self.subTest(workflow=workflow, target=target):
            result = subprocess.run(['bash', '-c', script], cwd=temporary, env={**os.environ, 'TARGET_REF': target}, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        for target in ('Dom', 'StarPilot', 'refs/heads/Dom', 'refs/heads/StarPilot', '-bad', 'feature\nBUILD_BRANCH=Dom', 'HEAD', 'refs/heads/../../Dom'):
          with self.subTest(workflow=workflow, target=target):
            result = subprocess.run(['bash', '-c', script], cwd=temporary, env={**os.environ, 'TARGET_REF': target}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)

  def test_default_stable_and_source_only_fleet_gate(self):
    compile_workflow = (ROOT / '.github/workflows/compile_starpilot.yaml').read_text()
    self.assertIn('default: "stable"', compile_workflow)
    self.assertIn('git push origin "HEAD:refs/heads/${BUILD_BRANCH}"', compile_workflow)
    self.assertNotIn('--force', compile_workflow)
    fleet = (ROOT / '.github/workflows/fleet_safety.yaml').read_text()
    self.assertIn('python -m selfdrive.car.tests.fleet_safety --all --release', fleet)
    self.assertNotIn('openpilot/selfdrive/', fleet)


if __name__ == '__main__':
  unittest.main()
