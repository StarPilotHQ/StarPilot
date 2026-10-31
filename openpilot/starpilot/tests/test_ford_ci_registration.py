"""Qualified Ford configurations remain collected by both CI lanes."""
import ast
from tools.ci import run_vehicle_tests as runner


FAMILY_TARGETS = (
  'opendbc_repo/opendbc/safety/tests/test_ford_canfd_display.py',
  'openpilot/starpilot/tests/test_ford_configuration_parity.py',
  'openpilot/starpilot/tests/test_ford_configuration_admission.py',
  'openpilot/starpilot/tests/test_ford_release_long_preferences.py',
  'openpilot/selfdrive/car/tests/test_registered_ford_discovery.py',
  'openpilot/selfdrive/car/tests/test_automatic_ford_discovery.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_configuration_parity.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_alpha_aol.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_full_engagement.py',
)


def test_both_actual_ci_selectors_collect_all_qualified_ford_configuration_contracts():
  for suite in ('aol-lifecycle', 'safety-release'):
    selected = runner.targets_for(suite)
    for path in FAMILY_TARGETS:
      assert path in selected
      assert (runner.ROOT / path).is_file()
  # RELEASE CAN FD Alpha: the stock CAN FD long word runs its complete positive contract on the release library
  release = runner.targets_for('safety-release')
  prefix = 'opendbc_repo/opendbc/safety/tests/test_ford.py::'
  assert prefix + 'TestFordCANFDLongitudinalSafety' in release
  source = ast.parse((runner.ROOT / prefix.split('::', 1)[0]).read_text())
  assert any(isinstance(node, ast.ClassDef) and node.name == 'TestFordCANFDLongitudinalSafety' for node in source.body)


def test_actual_host_collector_discovers_release_ui_guards(tmp_path):
  from tools.ci.run_host_tests import pytest_files
  name = 'openpilot/starpilot/ui/tests/test_ford_release_alpha.py'
  target = tmp_path / name
  target.parent.mkdir(parents=True)
  target.write_bytes((runner.ROOT / name).read_bytes())
  assert name in pytest_files(tmp_path)
