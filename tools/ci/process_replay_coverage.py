"""Account for replay inputs separately from test execution and vehicle qualification."""

import json
from pathlib import Path


REQUIRED_REPLAY_LABELS = frozenset({
  "HYUNDAI", "HYUNDAI2", "TOYOTA", "TOYOTA3", "HONDA", "HONDA2", "CHRYSLER", "RAM",
  "SUBARU", "GM", "NISSAN", "VOLKSWAGEN", "MAZDA", "FORD", "RIVIAN", "TESLA",
})
VOLVO_UNRECORDED_PLATFORMS = frozenset({
  "VOLVO_V40", "VOLVO_XC40_RECHARGE", "VOLVO_S60_RECHARGE", "POLESTAR_2", "VOLVO_V60",
})
VOLVO_REQUIRED_TESTS = {
  "interfaces": ("opendbc_repo/opendbc/car/volvo/tests/test_c1.py",
                 "opendbc_repo/opendbc/car/volvo/tests/test_cma.py",
                 "opendbc_repo/opendbc/car/volvo/tests/test_eucd.py"),
  "safety-debug": ("opendbc_repo/opendbc/safety/tests/test_volvo_c1.py",
                   "opendbc_repo/opendbc/safety/tests/test_volvo_cma.py",
                   "opendbc_repo/opendbc/safety/tests/test_volvo_eucd.py"),
  "safety-release": ("opendbc_repo/opendbc/safety/tests/test_volvo_c1.py",
                     "opendbc_repo/opendbc/safety/tests/test_volvo_cma.py",
                     "opendbc_repo/opendbc/safety/tests/test_volvo_eucd.py"),
}


def replay_coverage(active_platforms, labels, suite_targets, *, full_test):
  labels = set(labels)
  recorded_brands = {label.lower() for label in labels}
  errors = []
  gaps = []
  missing = set(active_platforms) - recorded_brands - {"body"}
  if full_test:
    absent_labels = REQUIRED_REPLAY_LABELS - labels
    if absent_labels:
      errors.append(f"Required replay inputs missing: {sorted(absent_labels)}")
    if set(active_platforms.get("volvo", ())) != VOLVO_UNRECORDED_PLATFORMS:
      errors.append("Volvo recording classification requires exactly the five known active platforms; review changed scope")
    if "volvo" not in missing:
      errors.append("Volvo recording classification is stale: Volvo now has a recording or is no longer active")
    for suite, paths in VOLVO_REQUIRED_TESTS.items():
      for path in paths:
        if path not in suite_targets.get(suite, ()):
          errors.append(f"Required Volvo source/native suite no longer selected: {suite}: {path}")
    unaccounted = missing - {"volvo"}
    if unaccounted:
      errors.append(f"Active brands missing unaccounted replay inputs: {sorted(unaccounted)}")
  if "volvo" in missing:
    for platform in sorted(set(active_platforms.get("volvo", ())) & VOLVO_UNRECORDED_PLATFORMS):
      path_index = {"VOLVO_V40": 0, "VOLVO_V60": 2}.get(platform, 1)
      gaps.append({"brand": "volvo", "platforms": [platform], "status": "unrecorded",
                   "reason": f"No {platform} recording is available in the fixture catalog.",
                   "required_source_native_suites": {suite: paths[path_index] for suite, paths in VOLVO_REQUIRED_TESTS.items()}})
  return {"schema_version": 1, "scope": "process_replay_input_inventory", "full_test": full_test,
          "recorded_input_labels": sorted(labels), "active_platforms": active_platforms,
          "unrecorded_gaps": gaps, "errors": errors,
          "evidence_limit": "Input accounting only. Required source/native suites have independent execution results. This does not qualify the fleet."}


def write_coverage(report, destination, summary=None):
  destination = Path(destination)
  destination.parent.mkdir(parents=True, exist_ok=True)
  destination.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
  if summary:
    with Path(summary).open("a") as output:
      output.write("\n### Process replay input coverage\n\n")
      output.write(f"Full input inventory: {report['full_test']}; recorded inputs: {len(report['recorded_input_labels'])}.\n\n")
      for gap in report["unrecorded_gaps"]:
        output.write(f"- {gap['brand']} / {', '.join(gap['platforms'])}: **unrecorded**. {gap['reason']}\n")
        for suite, path in gap["required_source_native_suites"].items():
          output.write(f"  Required independent source/native check: {suite}: `{path}`.\n")
      for error in report["errors"]:
        output.write(f"- Coverage error: {error}\n")
      output.write(f"\n{report['evidence_limit']}\n")
