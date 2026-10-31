from contextlib import ExitStack, chdir, redirect_stdout
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

from tools import test_runner
from tools.ci import run_vehicle_tests
from tools.ci.run_vehicle_tests import audit_interface_modules, interface_coverage, interface_execution_errors, targets_for
from tools.ci.record_firmware import compile_commands


class TestVehicleCollection(unittest.TestCase):

  def test_gv70_two_panda_and_stop_parity_callers_are_required(self):
    required = (
      'openpilot/starpilot/aol/tests/test_hkg_cancel_joined.py',
      'openpilot/starpilot/aol/tests/test_gv70_two_panda_long.py',
      'openpilot/starpilot/longitudinal/tests/test_gv70_stopping_parity.py',
    )
    targets = targets_for('aol-lifecycle')
    self.assertLessEqual(set(required), set(targets))
    for target in targets:
      self.assertTrue(target.endswith('.py'), target)
      self.assertNotIn('::', target)


  def test_hkg_gv60_and_inherited_profile_guards_are_required(self):
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_hyundai_gv60_long.py::TestGV60LongDebug', targets_for('safety-debug'))
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_hyundai_blended_alpha_inherited.py', targets_for('safety-debug'))
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_hyundai_gv60_long.py::TestGV60LongReleaseDenied', targets_for('safety-release'))

  def test_stock_angle_recovery_callers_are_required_by_vehicle_ci(self):
    for path in ('opendbc_repo/opendbc/safety/tests/test_hyundai_angle_tx_recovery.py',
                 'openpilot/starpilot/aol/tests/test_stock_angle_physical_recovery.py'):
      self.assertIn(path, targets_for('aol-lifecycle'))
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_hyundai_angle_tx_recovery.py', targets_for('safety-debug'))

  def test_hkg_mode_parity_callers_are_required(self):
    path = 'opendbc_repo/opendbc/safety/tests/test_hkg_mode_parity.py'
    self.assertIn(path, targets_for('aol-lifecycle'))
    self.assertIn(path, targets_for('safety-debug'))

  def test_pe_real_stop_caller_is_required_by_vehicle_ci(self):
    path = 'opendbc_repo/opendbc/safety/tests/test_pe_stopped_callers.py'
    self.assertIn(path, targets_for('aol-lifecycle'))
    self.assertIn(path, targets_for('safety-debug'))
  def test_pe_combined_native_callers_and_refusals_keep_debug_release_separate(self):
    for target in (
      'opendbc_repo/opendbc/safety/tests/test_pe_combined_callers.py',
      'opendbc_repo/opendbc/safety/tests/test_pe_set_release_callers.py',
      'openpilot/starpilot/aol/tests/test_pe_combined_profile_contract.py',
    ):
      self.assertIn(target, targets_for('aol-lifecycle'))
    for target in (
      'opendbc_repo/opendbc/safety/tests/test_pe_combined_aol.py::TestPECombinedReleaseDenied',
      'openpilot/starpilot/aol/tests/test_pe_combined_profile_contract.py',
    ):
      self.assertIn(target, targets_for('safety-release'))
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_pe_combined_aol.py::TestPECombinedDebug', targets_for('safety-debug'))
    self.assertNotIn('opendbc_repo/opendbc/safety/tests/test_pe_combined_aol.py::TestPECombinedDebug', targets_for('safety-release'))

  def test_palisade_status_ownership_runs_in_debug_release_and_lifecycle(self):
    target = 'opendbc_repo/opendbc/safety/tests/test_palisade_stock_aol_status.py'
    for suite in ('safety-debug', 'safety-release', 'aol-lifecycle'):
      self.assertIn(target, targets_for(suite))
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_hyundai_blended_alpha.py::TestHyundaiBlendedAlphaAol',
                  targets_for('safety-release'))

  def test_ev9_joined_main_cancel_off_and_vetoes_run_in_lifecycle_ci(self):
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_ev9_set_release_callers.py', targets_for('safety-debug'))
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_ev9_set_release_callers.py', targets_for('aol-lifecycle'))
    self.assertLessEqual({
      'opendbc_repo/opendbc/safety/tests/test_ev9_combined_callers.py',
      'openpilot/starpilot/aol/tests/test_ev9_main_withdrawal_guards.py',
      'openpilot/starpilot/aol/tests/test_native_inventory_control_cadence.py',
    }, set(targets_for('aol-lifecycle')))
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_ev9_combined_callers.py', targets_for('safety-debug'))
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_ev9_combined_aol.py::TestEV9CombinedReleaseDenied', targets_for('safety-release'))
    self.assertIn('openpilot/starpilot/aol/tests/test_ev9_main_withdrawal_guards.py', targets_for('safety-release'))


  def test_classic_initialization_preserves_debug_acceptance_and_release_denial(self):
    path = 'openpilot/starpilot/aol/tests/test_classic_startup_neutral_replacement.py'
    release = 'tools/ci/test_classic_startup_release.py'
    self.assertIn(path, targets_for('safety-debug'))
    self.assertIn(release, targets_for('safety-release'))
    self.assertIn(path + '::test_classic_initializing_never_bypasses_current_health', targets_for('safety-release'))
    self.assertNotIn(path, targets_for('safety-release'))
    self.assertNotIn(release, targets_for('safety-debug'))

  def test_independent_lateral_soft_takeover_runs_in_release_ci(self):
    self.assertIn('openpilot/starpilot/aol/tests/test_independent_lateral_soft_takeover.py', targets_for('safety-release'))

  def test_expired_axis_gaps_keep_real_cpp_and_vetoes_in_release_ci(self):
    expected = {
      'openpilot/starpilot/aol/tests/test_sonata_temporary_withdrawals.py',
      'openpilot/starpilot/aol/tests/test_expired_axis_zero_request.py',
    }
    self.assertLessEqual(expected, set(targets_for('safety-release')))

  def test_ev6_source_deadline_refusals_run_in_lifecycle_and_release_ci(self):
    target = 'openpilot/starpilot/aol/tests/test_ev6_source_deadline.py'
    self.assertIn(target, targets_for('aol-lifecycle'))
    self.assertIn(target, targets_for('safety-release'))

  def test_ev6_release_ci_exercises_real_alpha_startup_and_physical_owner(self):
    expected = {
      'openpilot/starpilot/tests/test_ev6_startup.py',
      'openpilot/starpilot/aol/tests/test_ev6_stock_parity.py',
      'openpilot/starpilot/aol/tests/test_ev6_physical_authorization.py',
    }
    self.assertLessEqual(expected, set(targets_for('safety-release')))

  def test_classic_mapped_buttons_and_late_dash_run_in_release_ci(self):
    expected = {
      'openpilot/starpilot/aol/tests/test_classic_scc_mapped_buttons.py',
      'openpilot/starpilot/aol/tests/test_sonata_mapped_button_callers.py',
      'opendbc_repo/opendbc/safety/tests/test_classic_scc_main_ingress.py',
      'opendbc_repo/opendbc/car/hyundai/tests/test_classic_dash_late_configuration.py',
    }
    self.assertLessEqual(expected, set(targets_for('safety-release')))
    self.assertIn('opendbc_repo/opendbc/safety/tests/test_classic_scc_main_ingress.py', targets_for('safety-debug'))

  def test_source_snapshot_catches_uncommitted_and_untracked_edits(self):
    with tempfile.TemporaryDirectory() as directory, patch.object(run_vehicle_tests, 'ROOT', Path(directory)):
      root = Path(directory)
      source = root / 'opendbc_repo/car.py'
      source.parent.mkdir()
      source.write_text('first\n')

      def git(*args):
        subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)

      git('init', '-q')
      git('add', 'opendbc_repo/car.py')
      git('-c', 'user.name=CI Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
      original = run_vehicle_tests.source_input_snapshot()
      source.write_text('second\n')
      modified = run_vehicle_tests.source_input_snapshot()
      self.assertEqual(original['revision'], modified['revision'])
      self.assertNotEqual(original['tracked_patch_sha256'], modified['tracked_patch_sha256'])
      source.write_text('first\n')
      self.assertEqual(run_vehicle_tests.source_input_snapshot(), original)
      added = source.with_name('new_test.py')
      added.write_text('first\n')
      untracked = run_vehicle_tests.source_input_snapshot()
      added.write_text('second\n')
      self.assertNotEqual(untracked['untracked_sha256'], run_vehicle_tests.source_input_snapshot()['untracked_sha256'])

  def test_passed_tests_with_changed_source_fail_the_gate_and_keep_outcomes(self):
    test_id = 'fixture.TestCarInterfaces.test_car_interfaces_A'
    test = unittest.FunctionTestCase(lambda: None)
    with tempfile.TemporaryDirectory() as directory, \
         patch.object(test, 'id', return_value=test_id), \
         patch.object(run_vehicle_tests, 'source_provenance', side_effect=[{'inputs': value, 'build_identity': {'inputs': {'revision': 'fixture'}}}
                                                                          for value in ('before', 'after')]), \
         patch.object(run_vehicle_tests, 'vehicle_inventory', return_value={'platform_ids': ['A'], 'platform_count': 1}), \
         patch.object(run_vehicle_tests, 'native_provenance', return_value={}), \
         patch.object(run_vehicle_tests, 'audit_interface_modules', return_value=([], [])), \
         patch.object(run_vehicle_tests.vehicle_pytest, "split_targets", return_value=(["fixture.py"], [])), \
         patch.object(run_vehicle_tests.vehicle_pytest, "collect", return_value=([], [])), \
         patch.object(test_runner, 'collect', return_value=([test], [])), \
         patch.object(test_runner, 'run_batch', return_value=[test_runner.make_record(test_id)]), redirect_stdout(io.StringIO()):
      self.assertEqual(run_vehicle_tests.run('interfaces', directory), 1)
      result = json.loads((Path(directory) / 'results.json').read_text())
      coverage = json.loads((Path(directory) / 'coverage.json').read_text())
    self.assertEqual(result['results'][0]['status'], 'passed')
    self.assertEqual(result['status'], 'failed')
    self.assertIn('source inputs changed', result['gate_errors'][0])
    self.assertEqual(coverage['source_after']['inputs'], 'after')

  def test_exact_platform_collection(self):
    ids = [f"fixture.TestCarInterfaces.test_car_interfaces_{platform}" for platform in ("A", "B")]
    self.assertEqual(interface_coverage(ids, {"A", "B"}), dict(zip(("A", "B"), ids, strict=True)))

  def test_missing_extra_duplicate_and_abstract_cannot_satisfy_platforms(self):
    good = "fixture.TestCarInterfaces.test_car_interfaces_A"
    for ids in ([], [good, good], [good, good.replace("_A", "_B")], ["fixture.TestCarModelBase.test_car_params"]):
      with self.subTest(ids=ids), self.assertRaises(ValueError):
        interface_coverage(ids, {"A"})

  def test_concrete_platforms_require_passed_outcomes(self):
    mapping = {"A": "fixture.A", "B": "fixture.B"}
    for status in ("skipped", "xfailed", "failed", "error"):
      with self.subTest(status=status):
        records = [test_runner.make_record("fixture.A"), test_runner.make_record("fixture.B", status)]
        self.assertEqual(len(interface_execution_errors(mapping, records)), 1)
    self.assertEqual(len(interface_execution_errors(mapping, [])), 2)
    self.assertEqual(interface_execution_errors(mapping, [test_runner.make_record(test_id) for test_id in mapping.values()]), [])

  def test_class_skip_preserved_but_cannot_pass_interface_gate(self):
    module = ModuleType("vehicle_skip_fixture")

    @classmethod
    def set_up_class(cls):
      raise unittest.SkipTest("fixture class unavailable")

    module.TestCarInterfaces = type("TestCarInterfaces", (unittest.TestCase,), {
      "__module__": module.__name__, "setUpClass": set_up_class, "test_car_interfaces_A": lambda self: self.fail("unreachable")})
    tests = list(test_runner.flatten(unittest.defaultTestLoader.loadTestsFromModule(module)))
    with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {module.__name__: module}), \
         patch.object(run_vehicle_tests, "source_provenance", return_value={"build_identity": {"inputs": {"revision": "fixture"}}}), \
         patch.object(run_vehicle_tests, "vehicle_inventory", return_value={"platform_ids": ["A"], "platform_count": 1}), \
         patch.object(run_vehicle_tests, "native_provenance", return_value={}), \
         patch.object(run_vehicle_tests, "audit_interface_modules", return_value=([], [])), \
         patch.object(run_vehicle_tests.vehicle_pytest, "split_targets", return_value=(["fixture.py"], [])), \
         patch.object(run_vehicle_tests.vehicle_pytest, "collect", return_value=([], [])), \
         patch.object(test_runner, "collect", return_value=(tests, [])), redirect_stdout(io.StringIO()):
      self.assertEqual(run_vehicle_tests.run("interfaces", directory), 1)
      result = json.loads((Path(directory) / "results.json").read_text())
      coverage = json.loads((Path(directory) / "coverage.json").read_text())
    self.assertEqual(result["results"][0]["status"], "skipped")
    self.assertIn("fixture class unavailable", result["results"][0]["detail"])
    self.assertEqual(result["status"], "failed")
    self.assertEqual(len(result["gate_errors"]), 1)
    self.assertEqual(coverage["interface_results"]["A"]["status"], "skipped")

  def test_initialization_and_collection_failures_leave_error_artifacts(self):
    for failing in ("source_provenance", "vehicle_inventory", "native_provenance", "collect"):
      for collect_only in (False, True):
        with self.subTest(failing=failing, collect_only=collect_only), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
          output = Path(directory)

          def fail(output=output):
            self.assertTrue((output / "plan.json").is_file())
            raise ImportError("missing native fixture dependency")

          for name in ("source_provenance", "vehicle_inventory", "native_provenance"):
            value = {"platform_ids": ["A"], "platform_count": 1} if name == "vehicle_inventory" else {}
            if name == "source_provenance":
              value = {"build_identity": {"inputs": {"revision": "fixture"}}}
            stack.enter_context(patch.object(run_vehicle_tests, name, side_effect=fail if name == failing else None, return_value=value))
          stack.enter_context(patch.object(run_vehicle_tests.vehicle_pytest, "split_targets", return_value=(["fixture.py"], [])))
          stack.enter_context(patch.object(run_vehicle_tests.vehicle_pytest, "collect", return_value=([], [])))
          if failing == "collect":
            stack.enter_context(patch.object(test_runner, "collect", side_effect=ImportError("fixture collection failed")))
          stack.enter_context(redirect_stdout(io.StringIO()))
          self.assertEqual(run_vehicle_tests.run("interfaces", output, collect_only), 1)
          for name in ("plan.json", "results.json", "coverage.json"):
            artifact = json.loads((output / name).read_text())
            self.assertEqual(artifact["status"], "error", name)
            self.assertEqual(artifact["exit_code"], 1, name)
            self.assertTrue(artifact["collection_errors"], name)
            self.assertEqual(artifact["error_phase"], "collection" if failing == "collect" else "initialization", name)

  def test_native_safety_targets_are_top_level_and_complete(self):
    root = Path(__file__).resolve().parents[3] / "opendbc_repo/opendbc/safety/tests"
    expected = {path.name for path in root.glob("test_*.py")} | {"test_classic_startup_neutral_replacement.py"}
    self.assertEqual({Path(target.split("::", 1)[0]).name for target in targets_for("safety-debug")}, expected)
    self.assertTrue(all(Path(target.split("::", 1)[0]).parent.name == "tests" for target in targets_for("safety-debug")))

  def test_pe_native_variants_select_healthy_acceptance_and_denial_classes_separately(self):
    path = "opendbc_repo/opendbc/safety/tests/test_ioniq5pe_paired_native.py"
    debug, release = targets_for("safety-debug"), targets_for("safety-release")
    self.assertIn(path + "::TestPELongDebug", debug)
    self.assertNotIn(path + "::TestPELongReleaseDenied", debug)
    self.assertIn(path + "::TestPELongReleaseDenied", release)
    self.assertNotIn(path + "::TestPELongDebug", release)
    self.assertNotIn(path, debug)

  def test_mixed_native_variants_have_explicit_nonoverlapping_selection(self):
    import ast
    root = Path(__file__).resolve().parents[3]
    directory = root / "opendbc_repo/opendbc/safety/tests"
    debug, release = targets_for("safety-debug"), targets_for("safety-release")
    mixed = []
    for path in sorted(directory.glob("test_*.py")):
      classes = [node.name for node in ast.parse(path.read_text()).body
                 if isinstance(node, ast.ClassDef) and node.name.startswith("Test")]
      if not (any("Debug" in name for name in classes) and any("Release" in name for name in classes)):
        continue
      relative = str(path.relative_to(root))
      mixed.append(relative)
      self.assertIn(relative, run_vehicle_tests.DEBUG_SAFETY_CLASS_TARGETS)
      self.assertNotIn(relative, debug)
      self.assertNotIn(relative, release)
      for name in classes:
        target = relative + "::" + name
        if "Release" in name:
          self.assertNotIn(target, debug)
          self.assertIn(target, release)
        elif "Debug" in name:
          self.assertIn(target, debug)
          self.assertNotIn(target, release)
        else:
          self.assertIn(target, debug)
          self.assertIn(target, release)
    self.assertIn("opendbc_repo/opendbc/safety/tests/test_hyundai_ev9_long.py", mixed)

  def test_interfaces_do_not_mislabel_historical_route_test_base(self):
    root = Path(__file__).resolve().parents[3]
    car = root / "opendbc_repo/opendbc/car"
    expected = {str(path.relative_to(root)) for path in car.rglob("test_*.py") if path != car / "tests/test_models.py"}
    self.assertEqual(set(targets_for("interfaces")), expected)
    self.assertIn("opendbc_repo/opendbc/car/tesla/tests/test_tesla.py", expected)
    self.assertIn("opendbc_repo/opendbc/car/tests/test_car_interfaces.py", expected)

  def test_recursive_collection_excludes_only_the_known_route_module(self):
    with tempfile.TemporaryDirectory() as directory, patch.object(run_vehicle_tests, "ROOT", Path(directory)):
      for name in ("tests/test_models.py", "brand/tests/test_models.py", "brand/nested/test_feature.py"):
        path = Path(directory) / "opendbc_repo/opendbc/car" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
      self.assertEqual(targets_for("interfaces"), [
        "opendbc_repo/opendbc/car/brand/nested/test_feature.py", "opendbc_repo/opendbc/car/brand/tests/test_models.py"])

  def test_module_audit_accepts_collected_generated_methods(self):
    with tempfile.TemporaryDirectory() as directory, patch.object(run_vehicle_tests, "ROOT", Path(directory)):
      path = Path(directory) / "test_fixture.py"
      path.write_text("class TestCar:\n  def test_parameters(self): pass\n")
      modules, errors = audit_interface_modules([path.name], ["test_fixture.TestCar.test_parameters_A", "test_fixture.TestCar.test_parameters_B"])
    self.assertEqual(errors, [])
    self.assertEqual(modules[0]["collected_count"], 2)
    self.assertEqual(modules[0]["uncollected_test_methods"], [])

  def test_module_audit_checks_every_real_parameterized_class_and_method(self):
    from tools.ci import vehicle_pytest

    with tempfile.TemporaryDirectory() as directory, patch.object(run_vehicle_tests, "ROOT", Path(directory)):
      path = Path(directory) / "test_parameterized_audit_fixture.py"
      path.write_text("""import unittest
from opendbc.testing import parameterized_class
@parameterized_class('variant', [('A',), ('B',)])
class TestCar(unittest.TestCase):
  def test_first(self): pass
  def test_second(self): pass
""")
      try:
        with chdir(directory), redirect_stdout(io.StringIO()):
          nodeids, collection_errors = vehicle_pytest.collect([path.name])
        self.assertEqual(collection_errors, [])
        ids = [vehicle_pytest.test_id(nodeid).split("test_parameterized_audit_fixture.", 1)[1] for nodeid in nodeids]
        ids = ["test_parameterized_audit_fixture." + identity for identity in ids]
        self.assertEqual(len(ids), 4)
        # A similar name is not evidence that a class came from this template.
        imported = sys.modules[path.stem]
        imported.TestCar_Unrelated = type("TestCar_Unrelated", (unittest.TestCase,),
                                         {"__module__": imported.__name__, "__test__": True})
        _, errors = audit_interface_modules([path.name], ids)
        self.assertEqual(errors, [])
        missing = "test_parameterized_audit_fixture.TestCar_B.test_second"
        modules, errors = audit_interface_modules([path.name], [identity for identity in ids if identity != missing])
        self.assertEqual(modules[0]["uncollected_test_methods"], ["TestCar_B.test_second"])
        self.assertTrue(errors)
        modules, errors = audit_interface_modules([path.name], [identity for identity in ids if ".TestCar_B." not in identity])
        self.assertEqual(modules[0]["uncollected_test_methods"], ["TestCar_B.test_first", "TestCar_B.test_second"])
        self.assertTrue(errors)
      finally:
        sys.modules.pop(path.stem, None)

  def test_module_audit_rejects_unrelated_class_prefix(self):
    with tempfile.TemporaryDirectory() as directory, patch.object(run_vehicle_tests, "ROOT", Path(directory)):
      path = Path(directory) / "test_fixture.py"
      path.write_text("class TestCar:\n  def test_method(self): pass\n")
      modules, errors = audit_interface_modules([path.name], ["test_fixture.TestCar_A.test_method"])
      self.assertEqual(modules[0]["uncollected_test_methods"], ["TestCar.test_method"])
      self.assertTrue(errors)

  def test_module_audit_rejects_uncollected_pytest_functions_and_methods(self):
    with tempfile.TemporaryDirectory() as directory, patch.object(run_vehicle_tests, "ROOT", Path(directory)):
      path = Path(directory) / "test_fixture.py"
      path.write_text("def test_function(): pass\nclass TestPytest:\n  def test_method(self): pass\n")
      modules, errors = audit_interface_modules([path.name], [])
      self.assertEqual(len(errors), 3)
      self.assertEqual(modules[0]["module_level_test_functions"], ["test_function"])
      self.assertEqual(modules[0]["uncollected_test_methods"], ["TestPytest.test_method"])
      # A supported class elsewhere in the same module cannot hide these omissions.
      _, errors = audit_interface_modules([path.name], ["test_fixture.TestUnit.test_present"])
      self.assertEqual(len(errors), 2)

  def test_runner_reloads_dynamic_class_ids_in_serial_execution(self):
    module = ModuleType("vehicle_collection_fixture")

    def test_generated(self):
      self.assertTrue(True)

    module.Generated = type("Generated", (unittest.TestCase,), {"__module__": module.__name__, "test_generated": test_generated})
    with patch.dict(sys.modules, {module.__name__: module}):
      selected = [test.id() for test in test_runner.flatten(unittest.defaultTestLoader.loadTestsFromModule(module))]
      records = test_runner.run_batch(selected, capture_output=True)
    self.assertEqual([record["id"] for record in records], selected)
    self.assertEqual([record["status"] for record in records], ["passed"])

  def test_firmware_requires_actual_matching_compile_flags(self):
    release = "arm-none-eabi-gcc -o panda/board/obj/panda_h7/main.o -c panda/board/main.c"
    debug = release + " -DALLOW_DEBUG"
    self.assertEqual(compile_commands(release, "release"), [release.split()])
    self.assertEqual(compile_commands(debug, "debug"), [debug.split()])
    for log, variant in (("scons: up to date", "release"), (release, "debug"), (debug, "release")):
      with self.subTest(log=log, variant=variant), self.assertRaises(ValueError):
        compile_commands(log, variant)


if __name__ == "__main__":
  unittest.main()

