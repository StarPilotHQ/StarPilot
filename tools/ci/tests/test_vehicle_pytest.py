import tempfile
import textwrap
import os
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
import unittest

from tools.ci.vehicle_pytest import Recorder, collect, run, run_parallel, split_targets, test_id as normalize_test_id


class TestVehiclePytest(unittest.TestCase):
  def test_function_and_mixed_files_selected_once(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      fixtures = {"test_class.py": "class TestOrdinary(unittest.TestCase):\n  def test_one(self): pass\n",
                  "test_function.py": "def test_one(): pass\n",
                  "test_mixed.py": "class TestOrdinary(unittest.TestCase):\n  def test_one(self): pass\ndef test_two(): pass\n",
                  "test_plain.py": "class TestPlain:\n  def test_one(self): pass\n"}
      for name, source in fixtures.items():
        (root / name).write_text(source)
      unit, functions = split_targets(root, [*fixtures, "test_mixed.py"])
      self.assertEqual(unit, ["test_class.py"])
      self.assertEqual(functions, ["test_function.py", "test_mixed.py", "test_plain.py"])

  def test_unittest_class_with_pytest_fixture_uses_fixture_runner(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      path = root / 'test_fixture_vehicle.py'
      path.write_text(textwrap.dedent("""\
        import unittest, pytest
        @pytest.fixture
        def ready(request):
          request.cls.ready = True
        @pytest.mark.usefixtures('ready')
        class TestVehicle(unittest.TestCase):
          def test_receipt(self):
            assert self.ready
      """))
      units, fixtures = split_targets(root, [path.name])
      self.assertEqual(units, [])
      self.assertEqual(fixtures, [path.name])
      previous = Path.cwd()
      try:
        os.chdir(root)
        nodeids, errors = collect([path.name])
        self.assertFalse(errors)
        records, errors = run(nodeids)
        self.assertFalse(errors)
        self.assertEqual([row['status'] for row in records], ['passed'])
      finally:
        os.chdir(previous)

  def test_parameterized_id_retains_case_identity(self):
    self.assertEqual(normalize_test_id("opendbc_repo/opendbc/car/honda/tests/test_stock.py::test_cp[HONDA-A]"),
                     "opendbc_repo.opendbc.car.honda.tests.test_stock.test_cp[HONDA-A]")

  def test_records_all_outcomes_and_fixture_failure(self):
    for expected, outcome, phase, xfail, detail in (
      ("passed", "passed", "call", None, None), ("failed", "failed", "call", None, "assertion"),
      ("error", "failed", "setup", None, "fixture"), ("skipped", "skipped", "setup", None, "skip"),
      ("xfailed", "skipped", "call", "known", "xfail"), ("xpassed", "passed", "call", "known", None),
      ("xpassed", "failed", "call", None, "[XPASS(strict)] known")):
      with self.subTest(expected=expected):
        recorder = Recorder()
        report = SimpleNamespace(nodeid="test_example.py::test_one", duration=0.25, when=phase,
                                 failed=outcome == "failed", skipped=outcome == "skipped", longrepr=detail,
                                 sections=[("Captured stdout call", "output"), ("Captured stderr call", "error")])
        if xfail:
          report.wasxfail = xfail
        recorder.pytest_runtest_logreport(report)
        record = recorder.records["test_example.test_one"]
        self.assertEqual(record["status"], expected)
        self.assertEqual(record["time"], 0.25)
        self.assertEqual(record["stdout"], "output")
        self.assertEqual(record["stderr"], "error")
        recorder.pytest_runtest_logreport(SimpleNamespace(nodeid=report.nodeid, duration=0.1, when="teardown",
          failed=False, skipped=False, longrepr=None, sections=[]))
        self.assertEqual(record["status"], expected)

  def test_imported_classes_deselected_but_inherited_methods_remain(self):
    module = SimpleNamespace(__name__="local")
    local = SimpleNamespace(module=module, cls=SimpleNamespace(__module__="local"))
    imported = SimpleNamespace(module=module, cls=SimpleNamespace(__module__="other"))
    deselected = []
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)))
    items = [local, imported]
    Recorder().pytest_collection_modifyitems(None, config, items)
    self.assertEqual(items, [local])
    self.assertEqual(deselected, [imported])

  def test_teardown_error_overrides_pass(self):
    recorder = Recorder()
    for phase, failed in (("call", False), ("teardown", True)):
      recorder.pytest_runtest_logreport(SimpleNamespace(nodeid="test_file.py::test_case", duration=0, when=phase,
        failed=failed, skipped=False, longrepr="cleanup" if failed else None, sections=[]))
    self.assertEqual(recorder.records["test_file.test_case"]["status"], "error")

  def test_real_mixed_collection_and_execution_stays_serial(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      (root / "pytest.ini").write_text("[pytest]\naddopts = -n 4\n")
      (root / "test_mixed_vehicle_fixture.py").write_text(textwrap.dedent(f"""\
        import os, unittest, pytest
        EXPECTED_PID = {os.getpid()}
        class TestUnit(unittest.TestCase):
          def test_once(self):
            assert os.getpid() == EXPECTED_PID
            print('unit-once')
        @pytest.mark.parametrize('value', [1, 2])
        def test_parameter(value):
          assert os.getpid() == EXPECTED_PID
        @pytest.mark.skip(reason='fixture skip')
        def test_skip(): pass
        @pytest.mark.xfail(reason='known fixture')
        def test_xfail(): assert False
        @pytest.fixture
        def broken(): raise RuntimeError('fixture failed')
        def test_error(broken): pass
        def test_failure(): assert False
      """))
      previous = Path.cwd()
      try:
        os.chdir(root)
        with patch.dict(os.environ, {"PYTEST_ADDOPTS": "-n 8", "PYTEST_PLUGINS": "nonexistent_vehicle_plugin"}):
          unit, functions = split_targets(root, ["test_mixed_vehicle_fixture.py"] * 2)
          self.assertEqual(unit, [])
          self.assertEqual(functions, ["test_mixed_vehicle_fixture.py"])
          nodeids, errors = collect(functions)
          self.assertEqual(errors, [])
          self.assertEqual(len(nodeids), 7)
          records, errors = run(nodeids)
          self.assertEqual(errors, [])
          self.assertEqual(os.environ["PYTEST_ADDOPTS"], "-n 8")
          self.assertEqual(os.environ["PYTEST_PLUGINS"], "nonexistent_vehicle_plugin")
          self.assertEqual(len(records), 7)
          self.assertEqual(len({record["id"] for record in records}), 7)
          self.assertCountEqual([record["status"] for record in records],
                                ["passed", "passed", "passed", "skipped", "xfailed", "error", "failed"])
          unit_record = next(record for record in records if record["id"].endswith("TestUnit.test_once"))
          self.assertEqual(unit_record["stdout"].count("unit-once"), 1)
          self.assertTrue(any("fixture failed" in record["detail"] for record in records))
      finally:
        os.chdir(previous)

  def test_worker_plugin_configuration_fails_closed(self):
    import pytest
    for config in (SimpleNamespace(workerinput={}, option=SimpleNamespace()),
                   SimpleNamespace(option=SimpleNamespace(numprocesses=2)),
                   SimpleNamespace(option=SimpleNamespace(dist="load"))):
      with self.subTest(config=config), self.assertRaises(pytest.UsageError):
        Recorder().pytest_configure(config)

  def test_parallel_processes_keep_every_module_outcome(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      for name in ("a", "b", "c"):
        (root / f"test_{name}_fixture.py").write_text(f"import pytest\n@pytest.mark.parametrize('value', [1, 2])\ndef test_{name}(value): assert value\n")
      (root / "test_d_fixture.py").write_text("def test_d(): assert False\n")
      previous = Path.cwd()
      try:
        os.chdir(root)
        with patch.dict(os.environ, {"PYTHONPATH": str(Path(__file__).resolve().parents[3])}):
          nodeids, errors = collect(sorted(str(path.name) for path in root.glob("test_*.py")))
          self.assertEqual(errors, [])
          serial, serial_errors = run(nodeids)
          parallel, parallel_errors = run_parallel(nodeids, 3)
      finally:
        os.chdir(previous)
    self.assertEqual(parallel_errors, serial_errors)
    self.assertEqual({row["id"]: row["status"] for row in parallel}, {row["id"]: row["status"] for row in serial})
    self.assertEqual(len(parallel), 7)
