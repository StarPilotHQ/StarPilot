"""Hosted collection uses the real display-free Raylib backend, like unit tests."""

import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from tools.ci.vehicle_pytest import serial_environment


ROOT = Path(__file__).resolve().parents[3]
RUNNERS = (
  ('unit_tests', 'Run unit tests'),
  ('custom_pytest', 'Test every assigned custom pytest node serially'),
  ('aol_lifecycles', 'Gate every assigned lifecycle module serially'),
  ('safety_release_contracts', 'Run every assigned RELEASE target serially'),
)


class TestHeadlessCiEnvironment(unittest.TestCase):
  def runner_step(self, workflow, job_name, step_name):
    jobs = dict(re.findall(r'^  ([a-z_]+):\n(.*?)(?=^  [a-z_]+:|\Z)', workflow, re.M | re.S))
    steps = dict(re.findall(r'^    - name: ([^\n]+)\n(.*?)(?=^    - |\Z)', jobs[job_name], re.M | re.S))
    return steps[step_name]

  def assert_headless_step(self, step):
    self.assertRegex(step, r'(?m)^      env:\n(?:        .*\n)*?        RAYLIB_BACKEND: headless\n')

  def test_hosted_collection_selects_native_headless_backend(self):
    workflow = (ROOT / '.github/workflows/tests.yaml').read_text()
    for job_name, step_name in RUNNERS:
      with self.subTest(job=job_name):
        self.assert_headless_step(self.runner_step(workflow, job_name, step_name))

  def test_missing_or_desktop_backend_is_rejected(self):
    workflow = (ROOT / '.github/workflows/tests.yaml').read_text()
    for job_name, step_name in RUNNERS:
      step = self.runner_step(workflow, job_name, step_name)
      for replacement in ('', '        RAYLIB_BACKEND: desktop\n'):
        with self.subTest(job=job_name, replacement=replacement), self.assertRaises(AssertionError):
          self.assert_headless_step(step.replace('        RAYLIB_BACKEND: headless\n', replacement))

  def test_serial_vehicle_pytest_preserves_backend(self):
    with patch.dict(os.environ, {'RAYLIB_BACKEND': 'headless'}):
      with serial_environment():
        self.assertEqual(os.environ['RAYLIB_BACKEND'], 'headless')
      self.assertEqual(os.environ['RAYLIB_BACKEND'], 'headless')


if __name__ == '__main__':
  unittest.main()
