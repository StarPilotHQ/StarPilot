"""Keep custom pytest-function tests in the hosted shard selection and completion gate."""

import ast
from pathlib import Path
import re
import unittest

from tools.ci.run_host_tests import UI_PYTEST_FILES, pytest_files


ROOT = Path(__file__).resolve().parents[3]


class TestFeatureTestDiscovery(unittest.TestCase):
  def assert_hosted_pytest_contract(self, workflow):
    jobs = dict(re.findall(r'^  ([a-z_]+):\n(.*?)(?=^  [a-z_]+:|\Z)', workflow, re.M | re.S))
    shards = jobs['custom_pytest']
    complete = jobs['custom_pytest_complete']
    matrix = re.search(r'^        group: (\[.*\])$', shards, re.M)
    assert matrix is not None
    self.assertEqual(ast.literal_eval(matrix[1]), list(range(9)))
    self.assertIn('      fail-fast: false\n', shards)
    self.assertNotIn('continue-on-error:', shards + complete)
    self.assertIn('      run: .venv/bin/python -m tools.ci.custom_pytest_shards run --group "${{ matrix.group }}" --groups 9 '
                  + '--count 32 --output "ci-results/custom-pytest-group-${{ matrix.group }}"\n', shards)
    self.assertIn('        name: custom-pytest-group-${{ matrix.group }}\n', shards)
    self.assertIn('        path: ci-results/custom-pytest-group-${{ matrix.group }}/\n', shards)
    self.assertIn('        if-no-files-found: error\n', shards)
    self.assertIn('    needs: custom_pytest\n', complete)
    self.assertIn('    if: always()\n', complete)
    self.assertIn('        pattern: custom-pytest-*\n', complete)
    self.assertIn('        path: ci-results/custom-pytest-shards\n', complete)
    self.assertIn('      run: python3 -m tools.ci.custom_pytest_shards aggregate '
                  + '--directory ci-results/custom-pytest-shards --count 32 --output ci-results/custom-pytest-complete.json\n', complete)

  def test_hosted_pytest_selection_covers_all_custom_function_tests(self):
    workflow = (ROOT / '.github/workflows/tests.yaml').read_text()
    self.assert_hosted_pytest_contract(workflow)
    selected = pytest_files()
    self.assertIn('openpilot/system/tests/test_sentry.py', selected)
    self.assertIn('openpilot/system/tests/test_sentry.py', UI_PYTEST_FILES)
    self.assertTrue(selected)
    for package in ('flm', 'spot_monitor', 'sentry_mode', 'galaxy', 'ui'):
      self.assertTrue(any(path.startswith(f'openpilot/starpilot/{package}/') for path in selected), package)

  def test_missing_duplicate_or_mismatched_shards_are_rejected(self):
    workflow = (ROOT / '.github/workflows/tests.yaml').read_text()
    matrix = 'group: [' + ', '.join(map(str, range(9))) + ']'
    for replacement in (list(range(8)), list(range(8)) + [7], list(range(10))):
      with self.subTest(matrix=replacement), self.assertRaises(AssertionError):
        self.assert_hosted_pytest_contract(workflow.replace(matrix, 'group: ' + str(replacement)))
    with self.assertRaises(AssertionError):
      self.assert_hosted_pytest_contract(workflow.replace('--groups 9', '--groups 8'))
    with self.assertRaises(AssertionError):
      self.assert_hosted_pytest_contract(workflow.replace('--count 32', '--count 31'))

  def test_completion_and_full_runner_cannot_be_bypassed(self):
    workflow = (ROOT / '.github/workflows/tests.yaml').read_text()
    for old, new in (
      ('fail-fast: false', 'fail-fast: true'),
      ('needs: custom_pytest', 'needs: unit_tests'),
      ('    if: always()\n', '    if: success()\n'),
      ('pattern: custom-pytest-*', 'pattern: custom-pytest-0'),
      ('if-no-files-found: error', 'if-no-files-found: ignore'),
      ('--count 32 --output', '--count 32 -k selected --output'),
      ('    needs: custom_pytest', '    continue-on-error: true\n    needs: custom_pytest'),
    ):
      with self.subTest(change=old), self.assertRaises(AssertionError):
        self.assert_hosted_pytest_contract(workflow.replace(old, new))


if __name__ == '__main__':
  unittest.main()
