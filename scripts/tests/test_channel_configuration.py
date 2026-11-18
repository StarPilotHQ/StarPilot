import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ChannelConfigurationTest(unittest.TestCase):
  def test_old_and_new_channels_keep_ci_and_published_receipt_gate(self):
    for name in ('tests.yaml', 'safety.yaml', 'docs.yaml', 'flm-offroad.yaml', 'galaxy-phone-bridge.yaml'):
      source = (ROOT / '.github/workflows' / name).read_text().split('pull_request:', 1)[0]
      for branch in ('StarPilot', 'Dom', 'stable', 'testing', 'dev'):
        with self.subTest(workflow=name, branch=branch):
          self.assertRegex(source, rf'\b{branch}\b')
    tests = (ROOT / '.github/workflows/tests.yaml').read_text()
    guard = next(line for line in tests.splitlines() if "github.event_name == 'push' && (github.ref" in line)
    for branch in ('StarPilot', 'Dom', 'stable', 'testing', 'dev'):
      self.assertIn(f"refs/heads/{branch}'", guard)
    self.assertIn('tools/ci/check_prebuilt_receipt.py --revision HEAD', tests)

  def test_pretty_channel_names_do_not_change_release_or_tested_policy(self):
    source = ast.parse((ROOT / 'openpilot/common/version.py').read_text())
    assignments = [node for node in source.body if isinstance(node, ast.Assign) and
                   any(isinstance(target, ast.Name) and target.id in ('RELEASE_BRANCHES', 'TESTED_BRANCHES')
                       for target in node.targets)]
    values = {}
    exec(compile(ast.Module(body=assignments, type_ignores=[]), '<release-policy>', 'exec'), {}, values)
    for name in ('RELEASE_BRANCHES', 'TESTED_BRANCHES'):
      self.assertTrue(set(values[name]).isdisjoint({'stable', 'testing', 'dev', 'Dom', 'StarPilot'}))

  def test_launcher_and_updater_share_exact_legacy_pins(self):
    source = ast.parse((ROOT / 'openpilot/system/updated/updated.py').read_text())
    assignment = next(node for node in source.body if isinstance(node, ast.Assign) and
                      any(isinstance(target, ast.Name) and target.id == 'TRUSTED_LEGACY_RELEASES' for target in node.targets))
    values = {}
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), '<legacy-pins>', 'exec'), {}, values)
    pins = values['TRUSTED_LEGACY_RELEASES']
    launcher = (ROOT / 'launch_chffrplus.sh').read_text()
    declared = set(re.findall(r'TRUSTED_LEGACY_(?:STARPILOT_COMMIT|STABLE_MIGRATION)="([0-9a-f]{40})"', launcher))
    self.assertEqual(declared, set().union(*pins.values()))
    self.assertEqual(set(pins), {'StarPilot', 'stable'})
    self.assertEqual(len(pins['stable']), 1)
    self.assertLessEqual(pins['stable'], pins['StarPilot'])


if __name__ == '__main__':
  unittest.main()
