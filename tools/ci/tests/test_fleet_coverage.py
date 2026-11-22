"""Small synthetic fixtures for the exact-ID fleet coverage algorithm."""

import hashlib
import json
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from typing import Any

from tools.ci.run_vehicle_tests import SOURCE_INPUT_PATHS
from tools.ci.fleet_coverage import ROOT, DEFAULT_MANIFEST, SCENARIOS, canonical_sha256, evaluate, sha256
from openpilot.starpilot.validation.tests.test_control_modes import fixture


class FleetCoverageTest(unittest.TestCase):
  def setUp(self):
    self.revision = "a" * 40
    self.manifest: dict[str, Any] = {"schema_version": 1, "source_revision": "b" * 40,
                     "source_inventory_sha256": "c" * 64, "upstream_additions": ["ADDITION"],
                     "platforms": [{"id": "PRESENT", "family": "test", "source_declaration": "same_declaration"},
                                   {"id": "MISSING", "family": "test", "source_declaration": "source_only"}]}
    self.ids = {p: f"car.TestCarInterfaces.test_car_interfaces_{p}" for p in ("PRESENT", "ADDITION")}
    self.records = [{"id": t, "status": "passed"} for t in self.ids.values()]
    self.results: dict[str, Any] = {"schema_version": 1, "status": "completed", "exit_code": 0,
                    "selected_test_ids": list(self.ids.values()), "results": self.records}
    self.result_hash = hashlib.sha256(json.dumps(self.results, sort_keys=True).encode()).hexdigest()
    upstream = json.loads((ROOT / "upstream-sync.json").read_text())
    self.coverage: dict[str, Any] = {"schema_version": 1, "suite": "interfaces", "status": "completed", "exit_code": 0,
                     "platform_ids": list(self.ids), "platform_count": 2, "interface_test_ids": self.ids,
                     "selected_test_ids": list(self.ids.values()), "interface_results": dict(zip(self.ids, self.records, strict=True)),
                     "result_sha256": self.result_hash,
                     "source": {"revision": self.revision, "manifest_sha256": sha256(ROOT / "upstream-sync.json"),
                                "runner_sha256": sha256(ROOT / "tools/test_runner.py"),
                                "suite_runner_sha256": sha256(ROOT / "tools/ci/run_vehicle_tests.py"),
                                "pytest_runner_sha256": sha256(ROOT / "tools/ci/vehicle_pytest.py"),
                                "dependencies": upstream["dependencies"]}}

    self.current_snapshot = {"revision": self.revision, "paths": list(SOURCE_INPUT_PATHS),
                             "tracked_patch_sha256": "d" * 64, "untracked_sha256": {}}
    self.coverage["source"]["inputs"] = copy.deepcopy(self.current_snapshot)
    self.coverage["source_after"] = copy.deepcopy(self.coverage["source"])

  def check(self, modes=None, *, dirty_relevant=None):
    return evaluate(self.manifest, self.coverage, self.results, modes,
                    expected_revision=self.revision, hashes={"results": self.result_hash}, dirty_relevant=dirty_relevant or [],
                    current_snapshot=self.current_snapshot, expected_registry=set(self.ids))

  def test_checked_in_manifest_keeps_exact_source_and_addition_counts(self):
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    self.assertEqual(346, len(manifest["platforms"]))
    self.assertEqual(7, len(manifest["upstream_additions"]))
    self.assertEqual(353, len({p["id"] for p in manifest["platforms"]} | set(manifest["upstream_additions"])))

  def test_missing_port_and_missing_modes_remain_uncovered(self):
    report = self.check()
    self.assertEqual("uncovered", report["status"])
    self.assertEqual(1, report["reported_interface_counts"]["missing_port"])
    self.assertEqual(0, report["current_interface_counts"].get("passed", 0))
    self.assertEqual(["ADDITION"], report["upstream_only"])
    self.assertEqual("not_established", report["vehicle_qualification"])
    self.assertIn("independent per-configuration mode evidence absent", report["uncovered"])

  def test_skipped_and_failed_concrete_tests_are_preserved(self):
    self.records[0]["status"] = "skipped"
    self.records[1]["status"] = "failed"
    report = self.check()
    self.assertEqual("failed", report["status"])
    self.assertEqual("skipped", next(p for p in report["platforms"] if p["id"] == "PRESENT")["interface"])
    self.assertTrue(any("ADDITION" in issue for issue in report["failures"]))

  def test_missing_addition_cannot_disappear(self):
    self.coverage["platform_ids"] = ["PRESENT"]
    self.coverage["platform_count"] = 1
    self.coverage["interface_test_ids"] = {"PRESENT": self.ids["PRESENT"]}
    report = self.check()
    self.assertIn("upstream additions differ from frozen manifest", report["uncovered"])
    self.assertTrue(any("upstream addition ADDITION" in issue for issue in report["uncovered"]))

  def test_mode_enumeration_and_synthetic_traces_do_not_clear_matrix(self):
    modes = {"schema_version": 1, "enumeration_complete": True,
             "provenance": {"configuration_inventory_sha256": canonical_sha256([{"platform": "PRESENT"}]),
                            "trace_catalog_sha256": canonical_sha256([])},
             "configurations": [{"platform": "PRESENT"}], "traces": []}
    report = self.check(modes)
    self.assertEqual("uncovered", report["status"])
    self.assertTrue(any("PRESENT: 17 mode" in issue for issue in report["uncovered"]))
    self.assertTrue(any("ADDITION: no exact configurations" in issue for issue in report["uncovered"]))

  def test_results_hash_and_revision_are_required(self):
    self.coverage["result_sha256"] = "0" * 64
    report = self.check()
    self.assertEqual("error", report["status"])
    self.assertTrue(any("results hash" in issue for issue in report["errors"]))
    self.coverage["result_sha256"] = self.result_hash
    report = evaluate(self.manifest, self.coverage, self.results, expected_revision="f" * 40,
                      hashes={"results": self.result_hash}, dirty_relevant=[], current_snapshot=self.current_snapshot, expected_registry=set(self.ids))
    self.assertIn("interface report source revision differs from requested checkout", report["uncovered"])

  def test_empty_required_manifest_and_malformed_ids_are_errors(self):
    self.manifest["platforms"] = []
    self.results["results"][0]["id"] = []
    self.coverage["interface_test_ids"]["PRESENT"] = []
    report = self.check()
    self.assertEqual("error", report["status"])
    self.assertTrue(any("at least one exact source ID" in issue for issue in report["errors"]))
    self.assertTrue(any("invalid or duplicate test IDs" in issue for issue in report["errors"]))

  def test_malformed_mapping_is_rejected_with_nonempty_obligations(self):
    self.coverage["interface_test_ids"]["PRESENT"] = []
    report = self.check()
    self.assertEqual("error", report["status"])
    self.assertEqual(0, report["current_interface_counts"].get("passed", 0))

  def test_missing_reported_source_status_is_not_current_evidence(self):
    report = self.check()
    self.assertIn("interface report working-tree status missing or malformed", report["uncovered"])

  def test_dirty_relevant_source_and_empty_native_set_cannot_be_current(self):
    self.coverage.pop("source_after")
    report = evaluate(self.manifest, self.coverage, self.results, expected_revision=self.revision,
                      hashes={"results": self.result_hash}, dirty_relevant=[" M opendbc_repo/opendbc/car/example.py"],
                      current_snapshot=self.current_snapshot, expected_registry=set(self.ids))
    self.assertEqual(0, report["current_interface_counts"].get("passed", 0))
    self.assertIn("relevant vehicle/runner source differs from committed checkout", report["uncovered"])
    self.coverage["native"] = {"can_sources": {}, "pycapnp": {"path": __file__, "sha256": sha256(__file__)}}
    report = self.check()
    self.assertIn("interface report native CAN source set incomplete or unexpected", report["uncovered"])

  def test_reported_dirty_vehicle_source_invalidates_current_pass(self):
    self.coverage.pop("source_after")
    self.coverage["source"]["status"] = " M opendbc_repo/opendbc/car/honda/values.py\n M openpilot/starpilot/ui/device.py"
    report = self.check()
    self.assertEqual(0, report["current_interface_counts"].get("passed", 0))
    self.assertEqual(1, len(report["reported_relevant_dirty_paths"]))

  def test_pytest_adapter_hash_is_required_and_current(self):
    for value in (None, "0" * 64):
      with self.subTest(value=value):
        if value is None:
          self.coverage["source"].pop("pytest_runner_sha256", None)
        else:
          self.coverage["source"]["pytest_runner_sha256"] = value
        report = self.check()
        self.assertIn("interface report pytest_runner_sha256 differs from current source", report["uncovered"])
        self.assertEqual(0, report["current_interface_counts"].get("passed", 0))

  def test_reported_dirty_pytest_adapter_invalidates_current_evidence(self):
    self.coverage.pop("source_after")
    self.coverage["source"]["status"] = " M tools/ci/vehicle_pytest.py"
    report = self.check()
    self.assertEqual(report["reported_relevant_dirty_paths"], [" M tools/ci/vehicle_pytest.py"])
    self.assertEqual(0, report["current_interface_counts"].get("passed", 0))

  def test_mode_digest_is_verified_against_arrays(self):
    modes = {"schema_version": 1, "enumeration_complete": True, "configurations": [], "traces": [],
             "provenance": {"configuration_inventory_sha256": canonical_sha256([]), "trace_catalog_sha256": canonical_sha256([])}}
    modes["configurations"].append({"platform": "PRESENT"})
    report = self.check(modes)
    self.assertTrue(any("inventory digest mismatch" in issue for issue in report["errors"]))

  def test_coherent_contract_fixture_can_pass_without_vehicle_qualification(self):
    import capnp.lib.capnp as native_capnp

    self.manifest["platforms"] = [self.manifest["platforms"][0]]
    self.manifest["upstream_additions"] = []
    self.ids = {"PRESENT": self.ids["PRESENT"]}
    self.records = [self.records[0]]
    self.results["selected_test_ids"] = list(self.ids.values())
    self.results["results"] = self.records
    self.result_hash = hashlib.sha256(json.dumps(self.results, sort_keys=True).encode()).hexdigest()
    self.coverage.update(platform_ids=["PRESENT"], platform_count=1, interface_test_ids=self.ids,
                         selected_test_ids=list(self.ids.values()), interface_results={"PRESENT": self.records[0]},
                         result_sha256=self.result_hash)
    native_path = native_capnp.__file__
    self.coverage["native"] = {"can_sources": {p: sha256(ROOT / p) for p in (
      "opendbc_repo/opendbc/can/packer.py", "opendbc_repo/opendbc/can/parser.py")},
      "pycapnp": {"path": native_path, "sha256": sha256(native_path)}}
    self.coverage["source"]["status"] = " M openpilot/starpilot/ui/device.py"
    traces = []
    for scenario in SCENARIOS:
      mode = scenario.removeprefix("mode_") if scenario.startswith("mode_") else "off"
      trace = fixture(mode=mode, kind="replay")
      trace["configuration"]["platform"] = "PRESENT"
      trace["scenario"] = scenario
      trace["case_id"] = "fixture-" + scenario
      if not scenario.startswith("mode_"):
        trace["requirements"]["required_events"] = [scenario]
        trace["frames"][1]["event"] = scenario
      traces.append(trace)
    configurations = [copy.deepcopy(traces[0]["configuration"])]
    modes = {"schema_version": 1, "enumeration_complete": True, "configurations": configurations, "traces": traces,
             "provenance": {"configuration_inventory_sha256": canonical_sha256(configurations),
                            "trace_catalog_sha256": canonical_sha256(traces)}}
    report = self.check(modes)
    self.assertEqual("pass", report["status"], report)
    self.assertEqual("not_established", report["vehicle_qualification"])
    interface_only = self.check()
    self.assertEqual("pass", interface_only["interface_gate"]["status"])
    self.assertEqual("uncovered", interface_only["status"])
    self.assertIn("independent per-configuration mode evidence absent", interface_only["uncovered"])
    for outcome in ("failed", "error", "skipped"):
      self.records[0]["status"] = outcome
      self.assertNotEqual("pass", self.check()["interface_gate"]["status"])
    self.records[0]["status"] = "passed"
    ancillary = {"id": "test.Inapplicable", "status": "skipped"}
    self.results["results"].append(ancillary)
    self.results["selected_test_ids"].append(ancillary["id"])
    self.coverage["selected_test_ids"] = list(self.results["selected_test_ids"])
    self.assertEqual("pass", self.check()["interface_gate"]["status"])
    self.assertTrue(any("test.Inapplicable" in issue for issue in self.check()["uncovered"]))
    self.results["results"].pop()
    self.results["selected_test_ids"].pop()
    self.coverage["selected_test_ids"] = list(self.results["selected_test_ids"])
    snapshot = copy.deepcopy(self.current_snapshot)
    self.current_snapshot["tracked_patch_sha256"] = "e" * 64
    self.assertNotEqual("pass", self.check()["interface_gate"]["status"])
    self.current_snapshot = snapshot
    self.manifest["platforms"].append({"id": "ABSENT", "family": "test", "source_declaration": "source_only"})
    self.assertEqual("pass", self.check()["interface_gate"]["status"])
    self.assertIn("ABSENT: interface missing_port", self.check()["uncovered"])
    self.assertEqual("uncovered", self.check()["status"])
    missing_registry = evaluate(self.manifest, self.coverage, self.results,
                                expected_revision=self.revision, hashes={"results": self.result_hash},
                                dirty_relevant=[], current_snapshot=self.current_snapshot,
                                expected_registry={"PRESENT", "REGISTERED_BUT_OMITTED"})
    self.assertNotEqual("pass", missing_registry["interface_gate"]["status"])
    self.manifest["platforms"].pop()
    self.coverage["source"]["status"] = " M opendbc_repo/opendbc/car/example.py\n M opendbc_repo/opendbc/can/parser.so"
    dirty = self.coverage["source"]["status"].splitlines()
    report = self.check(modes, dirty_relevant=dirty)
    self.assertEqual("pass", report["status"], report)
    self.assertEqual(1, report["current_interface_counts"]["passed"])
    changed = copy.deepcopy(self.current_snapshot)
    self.current_snapshot["tracked_patch_sha256"] = "e" * 64
    report = self.check(modes, dirty_relevant=dirty)
    self.assertEqual(0, report["current_interface_counts"].get("passed", 0))
    self.assertTrue(any("source inputs" in issue for issue in report["uncovered"]))
    self.current_snapshot = changed
    modes["traces"][0]["case_id"] = "changed-after-catalog-hash"
    report = self.check(modes)
    self.assertEqual("error", report["status"])
    self.assertTrue(any("trace catalog digest mismatch" in issue for issue in report["errors"]))
    self.assertEqual(0, report["current_interface_counts"].get("passed", 0))

  def test_cli_refuses_existing_output_without_changing_bytes(self):
    with tempfile.TemporaryDirectory() as directory:
      output = Path(directory) / "report.json"
      output.write_text("earlier failed evidence\n")
      run = subprocess.run([sys.executable, str(ROOT / "tools/ci/fleet_coverage.py"),
                            "--interface-coverage", "missing.json", "--interface-results", "missing.json",
                            "--output", str(output)], cwd=ROOT, capture_output=True, text=True, check=False)
      self.assertNotEqual(0, run.returncode)
      self.assertIn("refusing to replace evidence", run.stderr)
      self.assertEqual("earlier failed evidence\n", output.read_text())


if __name__ == "__main__":
  unittest.main()
