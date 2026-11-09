from pathlib import Path

import pytest

from openpilot.starpilot.system.starport import head_unit_fixes as hf
from openpilot.starpilot.system.starport.quirk_state import QuirkState, SessionOutcome

RULE = hf.RULES[0]
BOLT = dict(RULE.match)
CFG_ON = {"head_unit_experiments": True, "head_unit_experiment_rules": [RULE.id]}


def pick(config, labels=BOLT, transport="wireless", q=None, key="bt:AA", car_type="builtin", profile="default"):
  return hf.select(config, labels, transport, q or QuirkState(Path("/nonexistent/q.json")),
                   key, car_type=car_type, profile=profile)


def test_shipped_rules_validate():
  assert hf.validate_rules() == []


@pytest.mark.parametrize("change", [{"head_unit_software_version": "W28E-*"}, {"car_year": ""}, {"extra": "x"}])
def test_validate_rejects_wide_rules(change):
  bad = hf.Rule(**{**RULE.__dict__, "match": {**RULE.match, **change}})
  assert hf.validate_rules((bad,))


def test_validate_rejects_missing_evidence():
  bad = hf.Rule(**{**RULE.__dict__, "evidence": hf.Evidence("", (), None)})
  assert hf.validate_rules((bad,))


@pytest.mark.parametrize("build", ["W46E-V164.3.1-MIHS21A-127", "W48E-V167.3.1-MIHS21A-131", "W59E-V176.5.2-MIHS21A-193", "<ip>", ""])
def test_other_builds_do_not_match(build):
  labels = {**BOLT, "head_unit_software_version": build}
  assert not hf.matches(RULE, labels)
  assert pick(CFG_ON, labels) is None


@pytest.mark.parametrize("field,value", [("car_year", "2020"), ("car_year", "2023"), ("head_unit_model", "MyLink"), ("car_make", "Buick")])
def test_other_cars_do_not_match(field, value):
  assert pick(CFG_ON, {**BOLT, field: value}) is None


def test_near_miss_only_for_same_model_other_build():
  assert hf.near_miss(RULE, {**BOLT, "head_unit_software_version": "W46E"})
  assert not hf.near_miss(RULE, BOLT)
  assert not hf.near_miss(RULE, {**BOLT, "car_year": "2020", "head_unit_model": "MyLink"})


def test_head_unit_labels_skip_redacted():
  assert hf.head_unit_labels({"2": ["Chevrolet"], "9": ["redacted"], "10": [""]}) == {"car_make": "Chevrolet"}


def test_match_is_case_insensitive_exact():
  assert hf.matches(RULE, {k: v.upper() for k, v in BOLT.items()})


def test_experimental_rule_needs_both_opt_ins_and_the_rule_toggle():
  assert pick({}) is None
  assert pick({"head_unit_experiments": True}) is None
  assert pick({"head_unit_experiments": False, "head_unit_experiment_rules": [RULE.id]}) is None
  sel = pick(CFG_ON)
  assert sel and sel.level == 1 and sel.actions == (hf.OPEN_BLUETOOTH,)


def test_experiments_off_beats_populated_state(tmp_path):
  q = QuirkState(tmp_path / "q.json")
  q.begin("bt:AA", BOLT, RULE.id)
  assert pick(CFG_ON, q=q) is not None
  assert pick({**CFG_ON, "head_unit_experiments": False}, q=q) is None


@pytest.mark.parametrize("kwargs", [{"car_type": "adapter"}, {"profile": "aawireless"}, {"profile": "unofficial"}, {"key": ""},
                                    {"transport": "app"}])
def test_gates_block_selection(kwargs):
  assert pick(CFG_ON, **kwargs) is None


def test_wired_selection_with_key():
  assert pick(CFG_ON, transport="wired", key="usb:x") is not None


def test_reverted_trial_stays_off(tmp_path):
  q = QuirkState(tmp_path / "q.json")
  q.begin("bt:AA", BOLT, RULE.id)["state"] = "off"
  assert pick(CFG_ON, q=q) is None


def test_suggestion_conditions(tmp_path):
  q = QuirkState(tmp_path / "q.json")
  cfg = {}
  early = SessionOutcome(video_seen=True, streamed_s=20, ended_by_car=True)
  assert hf.suggestion(cfg, BOLT, "wireless", q, "bt:AA", car_type="builtin", profile="default") is None
  q.record("bt:AA", BOLT, early, now=1)
  assert hf.suggestion(cfg, BOLT, "wireless", q, "bt:AA", car_type="builtin", profile="default") is None
  q.record("bt:AA", BOLT, early, now=2)
  s = hf.suggestion(cfg, BOLT, "wireless", q, "bt:AA", car_type="builtin", profile="default")
  assert s and s["rule"] == RULE.id
  assert hf.suggestion({"head_unit_experiments": True}, BOLT, "wireless", q, "bt:AA", car_type="builtin", profile="default") is not None
  assert hf.suggestion(cfg, BOLT, "wireless", q, "bt:AA", car_type="adapter", profile="default") is None
  assert hf.suggestion(cfg, BOLT, "wireless", q, "bt:AA", car_type="builtin", profile="aawireless") is None
  assert hf.suggestion(CFG_ON, BOLT, "wireless", q, "bt:AA", car_type="builtin", profile="default") is None
  other = {**BOLT, "head_unit_software_version": "W46E"}
  assert hf.suggestion(cfg, other, "wireless", q, "bt:AA", car_type="builtin", profile="default") is None
  q.dismiss("bt:AA", RULE.id)
  assert hf.suggestion(cfg, BOLT, "wireless", q, "bt:AA", car_type="builtin", profile="default") is None
