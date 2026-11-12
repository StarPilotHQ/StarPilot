from concurrent.futures import Future
import ctypes
from concurrent.futures.process import BrokenProcessPool
import importlib.util
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from opendbc.safety.tests.libsafety import libsafety_py

if importlib.util.find_spec("tree_sitter") and importlib.util.find_spec("tree_sitter_c"):
  from opendbc.safety.tests import mutation
else:
  mutation = None


class _SkippedClassFixture(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    raise unittest.SkipTest("fixture class unavailable")

  def test_one(self):
    pass

  def test_two(self):
    pass


class _PassingFixture(unittest.TestCase):
  def test_one(self):
    pass


@unittest.skipIf(mutation is None, "mutation parser dependencies unavailable; full mutation gate requires them")
class TestMutationInfrastructureAccounting(unittest.TestCase):
  @staticmethod
  def site():
    return mutation.MutationSite(1, 0, 1, 0, 1, 1, "==", "!=", "comparison", Path("safety.h"), 1)

  def test_static_assert_candidates_remain_accounted_without_runtime_instrumentation(self):
    source = mutation.SAFETY_DIR / "helpers.h"
    code = '\n'.join((f'#line 1 "{source}"',
                       '_Static_assert(sizeof(int) == 4, "global word size");',
                       'int permit(int value) {',
                       '  _Static_assert(sizeof(int) == 4, "local word size");',
                       '  return value <= 3;', '}', ''))
    with TemporaryDirectory() as directory:
      root = Path(directory)
      original = root / "input.c"
      original.write_text(code)
      sites, _counts, pruned, preprocessed = mutation.enumerate_sites(original, root / "input.i")
      static_sites = {site.site_id for site in sites if site.origin_line in (1, 3)}
      self.assertTrue(static_sites)
      self.assertEqual(pruned, static_sites)
      self.assertEqual({site.mutator for site in sites if site.site_id in pruned}, {"comparison", "boundary"})
      self.assertEqual({site.site_id for site in sites}, set(range(len(sites))))
      executable = [site for site in sites if site.site_id not in pruned]
      self.assertTrue(executable)
      library = root / "constant-context.so"
      mutation.compile_mutated_library(preprocessed, executable, library)
      native = ctypes.CDLL(str(library))
      native.permit.argtypes, native.permit.restype = [ctypes.c_int], ctypes.c_int
      native.mutation_set_active_mutant.argtypes = [ctypes.c_int]
      native.mutation_set_active_mutant(-1)
      self.assertEqual((native.permit(3), native.permit(4)), (1, 0))
      comparison = next(site for site in executable if site.mutator == "comparison")
      native.mutation_set_active_mutant(comparison.site_id)
      self.assertEqual((native.permit(3), native.permit(4)), (0, 1))

  def test_mutation_targets_production_safety_not_its_test_harness(self):
    production = mutation.SAFETY_DIR / "helpers.h"
    fixture = mutation.SAFETY_TESTS_DIR / "libsafety/safety.c"
    code = '\n'.join((f'#line 1 "{production}"',
                       'int allowed(int value) { return value > 0; }',
                       f'#line 1 "{fixture}"',
                       'int fixture(int value) { return value > 0; }', ''))
    with TemporaryDirectory() as directory:
      source = Path(directory) / "input.c"
      source.write_text(code)
      sites, _counts, _pruned, _source = mutation.enumerate_sites(source, Path(directory) / "input.i")
    self.assertTrue(sites)
    self.assertEqual({site.origin_file for site in sites}, {production})
    self.assertEqual({site.mutator for site in sites}, {"comparison", "boundary"})

  def test_pytest_fixture_keeps_mutant_debug_and_actual_release(self):
    source = """import pytest
from opendbc.car import structs
from opendbc.safety.tests.libsafety import libsafety_py
@pytest.fixture(params=(False, True), ids=('DEBUG', 'RELEASE'))
def native_variant(request):
  previous = libsafety_py.libpath
  libsafety_py.load(libsafety_py._build_libsafety(release=request.param))
  try:
    yield request.param
  finally:
    libsafety_py.load(previous)
def test_native_variant(native_variant):
  if native_variant:
    assert libsafety_py.libsafety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0
    with pytest.raises(AttributeError):
      libsafety_py.libsafety.mutation_get_active_mutant()
  else:
    assert libsafety_py.libsafety.mutation_get_active_mutant() == 73
"""
    release = libsafety_py._build_libsafety(release=True)
    with TemporaryDirectory() as directory, patch.dict(libsafety_py.__dict__):
      root = Path(directory)
      debug = root / "instrumented.so"
      mutation.compile_mutated_library("int fixture_marker(void) { return 0; }", [], debug)
      libsafety_py.load(debug)
      libsafety_py.libsafety.mutation_set_active_mutant(73)
      target = root / "test_native_variant.py"
      target.write_text(source)
      builds = []

      def build_variant(release=False):
        self.assertTrue(release, "active Debug library must not be rebuilt")
        builds.append(release)
        return release_library

      release_library = release
      with patch.object(libsafety_py, "_build_libsafety", side_effect=build_variant):
        result = mutation._run_pytest(["pytest:" + str(target)])
      self.assertEqual((result.ran, result.skipped, result.failures, result.errors), (2, 0, (), ()), result.output_tail)
      self.assertEqual(builds, [True])
      self.assertEqual(libsafety_py.libsafety.mutation_get_active_mutant(), 73)

  def test_worker_crash_is_infrastructure_error(self):
    site = self.site()
    future = Future()
    future.set_exception(BrokenProcessPool("worker exited"))
    result = mutation.completed_mutant_result(future, site)
    self.assertEqual(result.outcome, "infra_error")
    self.assertIn("BrokenProcessPool", result.details)

  def test_failed_safety_assertion_remains_killed(self):
    site = self.site()
    future = Future()
    future.set_result(mutation.MutantResult(site, "killed", 0.1, "test assertion"))
    self.assertEqual(mutation.completed_mutant_result(future, site).outcome, "killed")

  def test_full_baseline_failure_aborts_before_mutant_targets(self):
    catalog = {"test_defaults.py": ["test_defaults.TestNoOutput.test_tx_hook"]}
    with patch.object(mutation, "run_unittest", return_value=mutation.TestRun(1, 0, 0, (catalog["test_defaults.py"][0],), ())) as baseline, \
         patch.object(mutation, "build_priority_tests") as targets:
      with self.assertRaisesRegex(RuntimeError, "unmutated full safety baseline failed"):
        mutation.require_full_baseline(catalog, [self.site()], Path("libsafety.so"), False)
    baseline.assert_called_once_with(catalog["test_defaults.py"], Path("libsafety.so"), mutant_id=-1, verbose=False)
    targets.assert_not_called()

  def test_empty_target_is_not_treated_as_surviving_mutant(self):
    catalog = {"test_defaults.py": ["test_defaults.TestNoOutput.test_tx_hook"]}
    with patch.object(mutation, "run_unittest", return_value=mutation.TestRun(1, 0, 0, (), ())), \
      patch.object(mutation, "build_priority_tests", return_value=[]):
      with self.assertRaisesRegex(RuntimeError, "mutants lack test targets"):
        mutation.require_full_baseline(catalog, [self.site()], Path("libsafety.so"), False)

  def test_unittest_error_does_not_count_as_kill(self):
    with patch.object(mutation, "_run_isolated_unittest", return_value=mutation.TestRun(1, 0, 0, (), ("fixture.ImportError",))):
      result = mutation.eval_mutant(self.site(), ["test_defaults.TestNoOutput.test_tx_hook"], Path("libsafety.so"), False, 60)
    self.assertEqual(result.outcome, "infra_error")

  def test_isolated_worker_timeout_is_infrastructure_error(self):
    with patch.object(mutation, "_run_isolated_unittest", side_effect=RuntimeError("isolated mutant timed out")):
      result = mutation.eval_mutant(self.site(), ["test_defaults.TestNoOutput.test_tx_hook"], Path("libsafety.so"), False, 60)
    self.assertEqual(result.outcome, "infra_error")
    self.assertIn("timed out", result.details)

  def test_partial_or_all_skipped_baseline_aborts(self):
    catalog = {"test_defaults.py": ["test.one", "test.two"]}
    for run in (mutation.TestRun(1, 0, 0, (), ()), mutation.TestRun(2, 2, 0, (), ())):
      with self.subTest(run=run), patch.object(mutation, "run_unittest", return_value=run):
        with self.assertRaisesRegex(RuntimeError, "unmutated full safety baseline failed"):
          mutation.require_full_baseline(catalog, [self.site()], Path("libsafety.so"), False)

  def test_class_level_skip_suppresses_exact_method_ids(self):
    targets = [f"{__name__}._SkippedClassFixture.test_one", f"{__name__}._SkippedClassFixture.test_two",
               f"{__name__}._SkippedClassFixture.test_two",
               f"{__name__}._PassingFixture.test_one"]
    safety = MagicMock()
    with patch.object(libsafety_py, "load"), patch.dict(libsafety_py.__dict__, {"libsafety": safety}):
      result = mutation.run_unittest(targets, Path("unused.so"), -1, False)
    safety.mutation_set_active_mutant.assert_called_once_with(-1)
    self.assertEqual((result.ran, result.skipped, result.suppressed, result.failures, result.errors), (1, 0, 3, (), ()))

  def test_failfast_does_not_load_later_test_modules(self):
    class Failing(unittest.TestCase):
      def runTest(self):
        self.fail('declared mutation witness')
    safety = MagicMock()
    with (patch.object(libsafety_py, 'load'), patch.dict(libsafety_py.__dict__, {'libsafety': safety}),
          patch.object(unittest.TestLoader, 'loadTestsFromName', side_effect=[unittest.TestSuite([Failing()]),
                                                                           AssertionError('must not import later')]) as load):
      result = mutation.run_unittest(['first', 'later'], Path('unused.so'), 0, False)
    self.assertEqual(load.call_count, 1)
    self.assertEqual(result.ran, 1)
    self.assertTrue(result.failures)
    self.assertFalse(result.errors)

  def test_successful_baseline_loads_every_target(self):
    safety = MagicMock()
    with (patch.object(libsafety_py, 'load'), patch.dict(libsafety_py.__dict__, {'libsafety': safety}),
          patch.object(unittest.TestLoader, 'loadTestsFromName',
                       side_effect=lambda name: unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])) as load):
      result = mutation.run_unittest(['first', 'later'], Path('unused.so'), -1, False)
    self.assertEqual([call.args[0] for call in load.call_args_list], ['first', 'later'])
    self.assertEqual((result.ran, result.failures, result.errors), (2, (), ()))

  def test_callback_child_infrastructure_is_not_mutation_kill_credit(self):
    from opendbc.safety.tests import test_body
    case = test_body.TestBody('test_rx_callback_finishes')
    case.safety = MagicMock()
    ready = 'NATIVE_BODY_CALLBACK_READY'
    for code, output in ((-11, ready), (2, ready), (0, ''), (1, '')):
      with self.subTest(code=code, output=output), patch.dict(libsafety_py.__dict__, {'libpath': 'declared.so'}), \
           patch.object(test_body.subprocess, 'run', return_value=MagicMock(returncode=code, stdout=output, stderr='')):
        with self.assertRaises(RuntimeError):
          case.test_rx_callback_finishes()
    for output, error in ((b'', RuntimeError), (ready.encode(), AssertionError)):
      with self.subTest(output=output), patch.dict(libsafety_py.__dict__, {'libpath': 'declared.so'}), \
           patch.object(test_body.subprocess, 'run', side_effect=test_body.subprocess.TimeoutExpired('declared', 10, output=output)):
        with self.assertRaises(error):
          case.test_rx_callback_finishes()

  def test_family_targets_include_new_ioniq_and_carnival_suites(self):
    catalog = {"test_hyundai.py": ["base"], "test_hyundai_canfd.py": ["canfd"],
               "test_hyundai_ioniq6_long.py": ["ioniq_long"], "test_hyundai_canfd_carnival.py": ["carnival"],
               "test_honda_aol_family.py": ["honda_aol"]}
    hyundai = mutation.MutationSite(1, 0, 1, 0, 1, 1, "==", "!=", "comparison",
                                    mutation.ROOT / "opendbc/safety/modes/hyundai_canfd.h", 1)
    targets = mutation.build_priority_tests(hyundai, catalog, [])
    self.assertEqual(set(targets), {"base", "canfd", "ioniq_long", "carnival"})

  def test_core_priority_preserves_duplicate_method_implementations(self):
    catalog = {"test_one.py": ["A.test_rx", "B.test_rx", "A.test_tx"],
               "test_two.py": ["C.test_rx", "D.test_tx"]}
    self.assertEqual(Counter(mutation._build_core_tests(catalog)), Counter(test for ids in catalog.values() for test in ids))

  def test_isolated_signal_keeps_bounded_full_stderr_and_exit_code(self):
    child = MagicMock()
    child.returncode = -11
    child.communicate.return_value = ("partial child output", "first warning\n" + "native backtrace\n" * 100)
    with patch.object(mutation.subprocess, "Popen", return_value=child) as launch:
      with self.assertRaises(mutation.IsolatedRunError) as raised:
        mutation._run_isolated_unittest(1, ["target.test"], Path("libsafety.so"), 60)
    self.assertEqual(launch.call_args.kwargs["env"]["PWD"], str(mutation.ROOT))
    self.assertEqual(raised.exception.kind, "signal")
    self.assertEqual(raised.exception.exit_code, -11)
    self.assertIn("native backtrace\n", raised.exception.stderr_tail)
    self.assertEqual(raised.exception.stdout_tail, "partial child output")

    with patch.object(mutation, "_run_isolated_unittest", side_effect=raised.exception):
      result = mutation.eval_mutant(self.site(), ["target.test"], Path("libsafety.so"), False, 60)
    self.assertEqual(result.outcome, "infra_error")
    self.assertEqual((result.failure_kind, result.exit_code), ("signal", -11))
    self.assertIn("native backtrace\n", result.stderr_tail)

  def test_machine_artifact_preserves_source_targets_and_diagnostics(self):
    source = mutation.ROOT / "opendbc/safety/safety.h"
    site = mutation.MutationSite(7, 0, 1, 0, 1, 1, "++", "--", "increment", source, 36)
    pruned = mutation.MutationSite(8, 0, 1, 0, 1, 1, "0", "1", "boundary", source, 37)
    result = mutation.MutantResult(site, "infra_error", 0.1, "isolated mutant exit=-11",
                                   failure_kind="signal", exit_code=-11,
                                   stderr_tail="warning\nfull native backtrace\n", stdout_tail="partial")
    with TemporaryDirectory() as directory:
      path = Path(directory) / "nested" / "results.json"
      mutation.write_results_json(path, discovered_sites=[site, pruned], pruned_ids={8}, results=[result],
                                  site_targets={7: ["one.test", "two.test"]},
                                  preprocessed_source="frozen preprocessed C", baseline_sec=2.5)
      artifact = json.loads(path.read_text())
    self.assertEqual((artifact["discovered"], len(artifact["pruned_build_incompatible"])), (2, 1))
    self.assertEqual(artifact["target_sets"][artifact["results"][0]["selected_test_set"]], ["one.test", "two.test"])
    self.assertEqual(artifact["results"][0]["source"], "opendbc/safety/safety.h")
    self.assertEqual(artifact["results"][0]["exit_code"], -11)
    self.assertEqual(artifact["results"][0]["stderr_tail"], "warning\nfull native backtrace\n")
    self.assertEqual(artifact["results"][0]["outcome"], "infra_error")

    with TemporaryDirectory() as directory:
      with self.assertRaisesRegex(RuntimeError, "one outcome for every executed site"):
        mutation.write_results_json(Path(directory) / "incomplete.json", discovered_sites=[site],
                                    pruned_ids=set(), results=[], site_targets={},
                                    preprocessed_source="frozen preprocessed C", baseline_sec=2.5)

@unittest.skipUnless(mutation is not None, "tree-sitter mutation dependencies required")
class TestPytestMutationCatalog(unittest.TestCase):
  def test_actual_pytest_parametrization_skips_and_error_classification(self):
    with TemporaryDirectory() as directory:
      path = Path(directory) / 'test_mutation_runner_fixture.py'
      path.write_text('''import pytest
@pytest.mark.parametrize('value', [1, 2])
def test_pass(value):
  assert value > 0
@pytest.mark.skip(reason='declared unavailable fixture')
def test_skip():
  pass
def test_failure():
  assert False
def test_error():
  raise ValueError('infrastructure witness')
def test_missing_fixture(unavailable_fixture):
  pass
''')
      prefix = 'pytest:' + str(path)
      success = mutation._run_pytest([prefix + '::test_pass', prefix + '::test_skip'])
      self.assertEqual((success.ran, success.skipped, success.suppressed), (3, 1, 0))
      self.assertFalse(success.failures or success.errors)
      failure = mutation._run_pytest([prefix + '::test_failure'])
      self.assertEqual(failure.failures, (prefix + '::test_failure',))
      self.assertFalse(failure.errors)
      for name in ('test_error', 'test_missing_fixture'):
        result = mutation._run_pytest([prefix + '::' + name])
        self.assertEqual(result.errors, (prefix + '::' + name,))
        self.assertFalse(result.failures)

  def test_actual_collection_retains_unittest_and_expands_pytest_parameters(self):
    with TemporaryDirectory() as directory:
      root = Path(directory)
      path = root / 'test_mutation_mixed_fixture.py'
      path.write_text('''import unittest, pytest
class TestLegacy(unittest.TestCase):
  def test_legacy(self):
    pass
@pytest.mark.parametrize('value', [1, 2])
def test_function(value):
  assert value > 0
''')
      legacy = unittest.FunctionTestCase(lambda: None)
      suite = unittest.TestSuite([unittest.TestSuite([legacy])])
      with patch.object(mutation, 'ROOT', root), patch.object(mutation, 'SAFETY_TESTS_DIR', root), \
           patch.object(mutation, 'BLUEPILOT_SAFETY_DIR', root / 'absent'), \
           patch.object(unittest.TestLoader, 'loadTestsFromName', return_value=suite):
        catalog = mutation._discover_test_catalog()
      self.assertEqual(catalog[path.name], [legacy.id(), 'pytest:' + str(path) + '::test_function[1]',
                                           'pytest:' + str(path) + '::test_function[2]'])


if __name__ == "__main__":
  unittest.main()
