"""Shared presentation metadata must not change native edit intent or saved values."""
from dataclasses import replace
import json

from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState, SettingLevel, row_change, setting_levels


def test_existing_controls_keep_their_default_level_and_placement():
  row = FeatureRow("lane_change:speed", "Minimum speed", "42", page="conditional/cem")
  assert FeatureSettingsState().row_level(row) == SettingLevel.SIMPLE
  assert row.page == "conditional/cem"
  assert json.loads(json.dumps({"level": FeatureSettingsState().row_level(row)}))["level"] == "simple"


def test_classification_survives_owner_updates_without_affecting_edits():
  row = FeatureRow("toggle", "Toggle", "Off", b"0", ("Off", "On"), available=True,
                   vehicle_fingerprint="vehicle", dependencies=(("parent", b"1"),))
  for level in SettingLevel:
    tagged = replace(row, level=level)
    assert row_change(tagged) == row_change(row)
    assert replace(tagged, reason="Updated availability").level == level
    assert tagged.source == b"0"


def test_category_default_and_row_override_survive_replacement():
  inherited = FeatureRow("debug", "Debug", "Off")
  override = FeatureRow("ordinary", "Ordinary", "Off", level=SettingLevel.SIMPLE)
  developer = FeatureSettingsState("debug", rows=(inherited, override), level=SettingLevel.ADVANCED)
  assert developer.row_level(inherited) == SettingLevel.ADVANCED
  assert developer.row_level(override) == SettingLevel.SIMPLE
  assert replace(developer, subtitle="Updated status").row_level(inherited) == SettingLevel.ADVANCED
  assert setting_levels(developer, lambda page: None) == {SettingLevel.SIMPLE, SettingLevel.ADVANCED}


def test_navigation_levels_follow_descendants_and_ignore_empty_headings():
  advanced = FeatureSettingsState("advanced", rows=(FeatureRow("debug", "Debug", "Off"),), level=SettingLevel.ADVANCED)
  empty = FeatureSettingsState("empty", rows=(FeatureRow("heading:empty", "Empty", ""),))
  parent = FeatureSettingsState("parent", rows=(FeatureRow("", "Advanced", "", page="advanced"),
                                               FeatureRow("", "Empty", "", page="empty")))
  states = {state.page: state for state in (advanced, empty, parent)}
  assert setting_levels(parent, states.get) == {SettingLevel.ADVANCED}
  assert setting_levels(empty, states.get) == set()
  states["advanced"] = replace(advanced, rows=advanced.rows + (FeatureRow("basic", "Basic", "On", level=SettingLevel.SIMPLE),))
  assert SettingLevel.SIMPLE in setting_levels(parent, states.get)


def test_navigation_cycles_and_unavailable_pages_do_not_recurse_forever():
  state = FeatureSettingsState("cycle", rows=(FeatureRow("", "Cycle", "", page="cycle"),))
  assert setting_levels(state, lambda page: state) == set()
  assert setting_levels(state, lambda page: None) == {SettingLevel.SIMPLE}


def test_developer_is_an_ordinary_category_without_implied_classification():
  for page in ("developer", "torque/developer", "profiles/developer", "ordinary"):
    category = FeatureSettingsState(page, title="Developer Parameters", rows=(FeatureRow("debug", "Debug", "Off"),))
    assert setting_levels(category, lambda name: None) == {SettingLevel.SIMPLE}
    advanced = replace(category, rows=(replace(category.rows[0], level=SettingLevel.ADVANCED),))
    assert setting_levels(advanced, lambda name: None) == {SettingLevel.ADVANCED}
    mixed = replace(advanced, rows=advanced.rows + (FeatureRow("basic", "Basic", "Off"),))
    assert setting_levels(mixed, lambda name: None) == {SettingLevel.SIMPLE, SettingLevel.ADVANCED}
