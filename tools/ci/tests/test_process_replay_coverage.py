import ast
import tempfile
import unittest
from pathlib import Path

from tools.ci.process_replay_coverage import REQUIRED_REPLAY_LABELS, VOLVO_REQUIRED_TESTS, VOLVO_UNRECORDED_PLATFORMS, replay_coverage, write_coverage
from tools.ci.run_vehicle_tests import ROOT, targets_for


class TestProcessReplayCoverage(unittest.TestCase):
  def report(self, platforms=None, labels=None, targets=None):
    active = {label.lower(): [label] for label in REQUIRED_REPLAY_LABELS}
    active["volvo"] = sorted(VOLVO_UNRECORDED_PLATFORMS)
    return replay_coverage(active if platforms is None else platforms,
                           REQUIRED_REPLAY_LABELS if labels is None else labels,
                           {suite: targets_for(suite) for suite in VOLVO_REQUIRED_TESTS} if targets is None else targets,
                           full_test=True)

  def test_available_recordings_remain_required_and_volvo_is_explicitly_unrecorded(self):
    report = self.report()
    self.assertEqual(report["errors"], [])
    self.assertEqual(report["unrecorded_gaps"][0]["status"], "unrecorded")
    for label in REQUIRED_REPLAY_LABELS:
      self.assertTrue(self.report(labels=REQUIRED_REPLAY_LABELS - {label})["errors"])

  def test_actual_vehicle_suite_targets_include_required_source_and_both_native_modes(self):
    for suite, paths in VOLVO_REQUIRED_TESTS.items():
      for path in paths:
        with self.subTest(suite=suite, path=path):
          self.assertIn(path, targets_for(suite))
          self.assertTrue((ROOT / path).is_file())
    module = ast.parse((ROOT / VOLVO_REQUIRED_TESTS["interfaces"][0]).read_text())
    self.assertTrue(any(isinstance(node, ast.ClassDef) and node.name == "TestVolvoC1Host" for node in module.body))

  def test_unknown_brand_changed_platform_and_obsolete_exception_fail(self):
    active = self.report()["active_platforms"]
    for change in ({**active, "new_brand": ["NEW_CAR"]},
                   {**active, "volvo": [*active["volvo"], "VOLVO_NEW"]},
                   {brand: ids for brand, ids in active.items() if brand != "volvo"}):
      self.assertTrue(self.report(platforms=change)["errors"])
    self.assertTrue(self.report(labels=REQUIRED_REPLAY_LABELS | {"VOLVO"})["errors"])

  def test_required_alternative_suite_removal_fails(self):
    targets = {suite: targets_for(suite) for suite in VOLVO_REQUIRED_TESTS}
    for suite, paths in VOLVO_REQUIRED_TESTS.items():
      for path in paths:
        with self.subTest(suite=suite, path=path):
          removed = {**targets, suite: [target for target in targets[suite] if target != path]}
          self.assertTrue(self.report(targets=removed)["errors"])

  def test_frozen_platform_inventory_matches_current_registry_source(self):
    expected = {"VOLVO_V40", "VOLVO_XC40_RECHARGE", "VOLVO_S60_RECHARGE", "POLESTAR_2", "VOLVO_V60"}
    self.assertEqual(VOLVO_UNRECORDED_PLATFORMS, expected)
    module = ast.parse((ROOT / "opendbc_repo/opendbc/car/volvo/values.py").read_text())
    car = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "CAR")
    declared = {target.id for node in car.body if isinstance(node, ast.Assign)
                for target in node.targets if isinstance(target, ast.Name)}
    self.assertEqual(declared, expected)

  def test_each_platform_gap_names_its_independent_source_and_native_alternatives(self):
    report = self.report()
    gaps = report["unrecorded_gaps"]
    self.assertEqual(len(gaps), 5)
    self.assertEqual({gap["platforms"][0] for gap in gaps}, VOLVO_UNRECORDED_PLATFORMS)
    for gap in gaps:
      with self.subTest(platform=gap["platforms"]):
        self.assertEqual(len(gap["platforms"]), 1)
        self.assertEqual(gap["status"], "unrecorded")
        index = {"VOLVO_V40": 0, "VOLVO_V60": 2}.get(gap["platforms"][0], 1)
        self.assertEqual(gap["required_source_native_suites"],
                         {suite: paths[index] for suite, paths in VOLVO_REQUIRED_TESTS.items()})
    self.assertIn("independent execution results", report["evidence_limit"])
    self.assertIn("does not qualify the fleet", report["evidence_limit"])

  def test_missing_known_platform_requires_scope_review(self):
    active = self.report()["active_platforms"]
    for platform in VOLVO_UNRECORDED_PLATFORMS:
      with self.subTest(platform=platform):
        changed = {**active, "volvo": [item for item in active["volvo"] if item != platform]}
        self.assertTrue(self.report(platforms=changed)["errors"])

  def test_artifact_and_summary_expose_gap_without_execution_claim(self):
    with tempfile.TemporaryDirectory() as temporary:
      destination = Path(temporary) / "coverage.json"
      summary = Path(temporary) / "summary.md"
      write_coverage(self.report(), destination, summary)
      self.assertIn('"scope": "process_replay_input_inventory"', destination.read_text())
      self.assertEqual(summary.read_text().count("**unrecorded**"), 5)
      for platform in VOLVO_UNRECORDED_PLATFORMS:
        self.assertIn(platform, summary.read_text())
      for paths in VOLVO_REQUIRED_TESTS.values():
        for path in paths:
          self.assertIn(path, summary.read_text())
      self.assertIn("does not qualify the fleet", summary.read_text())
