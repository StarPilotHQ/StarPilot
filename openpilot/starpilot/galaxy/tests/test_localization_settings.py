from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.localization_settings import LocalizationSettingsOwner
from openpilot.starpilot.galaxy.settings import AuthorityContext, ContextSource, SettingsChanged, SettingsGateway
from openpilot.starpilot.speed_limits import offset_document as od
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.slc_offset_feature import DOCUMENT_KEY


def test_real_gateway_units_and_wheel_side_without_a_vehicle(tmp_path):
  params = Params(str(tmp_path))
  context = SimpleNamespace(value=AuthorityContext(True, None, None))
  context.sample = lambda: context.value
  gateway = SettingsGateway(params, cast(ContextSource, context))
  token, generation = "localization-session", b"generation"

  def preview(index, value):
    page = gateway.page("localization", token, generation)
    assert page["title"] == "Localization" and not page["vehicleIdentified"]
    assert [row["label"] for row in page["rows"]] == ["Comma speed units", "Galaxy speed units", "Galaxy distance units",
                                                      "Right-Hand Drive", "Language"]
    return gateway.preview(page["view"], index, 0, token, generation, value=value)["intent"]

  assert gateway.confirm(preview(1, "Metric"), token, generation)
  assert params.get("GalaxySpeedUnits") == 1 and not params.get_bool("IsMetric"), "Galaxy units are Galaxy's own choice"
  assert gateway.confirm(preview(0, "Metric"), token, generation)
  assert params.get_bool("IsMetric"), "Comma speed units is the comma's own setting"
  assert not Path(params.get_param_path(DOCUMENT_KEY)).exists()
  assert gateway.confirm(preview(3, "On"), token, generation)
  assert params.get_bool("IsRhdDetected") and params.get_bool("OnroadCycleRequested")
  pending = preview(3, "Off")
  context.value = replace(context.value, parked=False)
  with pytest.raises(SettingsChanged):
    gateway.confirm(pending, token, generation)
  assert params.get_bool("IsRhdDetected")


def test_localization_preserves_current_offsets(tmp_path):
  params = Params(str(tmp_path))
  params.put_bool("IsMetric", False, block=True)
  document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (5 * od.MPH_TO_MPS, 0, 0, 0, 0, 0, 0))
  params.put(DOCUMENT_KEY, od.to_value(document), block=True)
  params.put_bool("SpeedLimitController", False, block=True)
  owner = LocalizationSettingsOwner(params, lambda: True, lambda: True)
  assert owner.apply(FeatureSettingsRequest("IsMetric", b"0", "Metric"))
  assert od.decode(Path(params.get_param_path(DOCUMENT_KEY)).read_bytes()) == document
  assert owner.apply(FeatureSettingsRequest("IsMetric", b"1", "Imperial"))
  assert od.decode(Path(params.get_param_path(DOCUMENT_KEY)).read_bytes()) == document


@pytest.mark.parametrize("condition", ["onroad", "preview", "unauthorized", "stale", "invalid"])
def test_wheel_side_cannot_change_during_a_drive_or_stale_request(tmp_path, condition):
  params = Params(str(tmp_path))
  params.put_bool("IsRhdDetected", False, block=True)
  params.put_bool("IsDriverViewEnabled", condition == "preview", block=True)
  owner = LocalizationSettingsOwner(params, lambda: condition != "unauthorized", lambda: condition != "onroad")
  request = FeatureSettingsRequest("IsRhdDetected", b"1" if condition == "stale" else b"0",
                                   "invalid" if condition == "invalid" else "On")
  assert not owner.apply(request)
  assert not params.get_bool("IsRhdDetected")
  assert not params.get_bool("OnroadCycleRequested")


def test_settings_session_rechecked_before_localization_commit(tmp_path):
  params = Params(str(tmp_path))
  context = SimpleNamespace(sample=lambda: AuthorityContext(True, None, None))
  gateway = SettingsGateway(params, cast(ContextSource, context))
  page = gateway.page("localization", "session", b"generation")
  pending = gateway.preview(page["view"], 3, 0, "session", b"generation", value="On")["intent"]
  with pytest.raises(SettingsChanged):
    gateway.confirm(pending, "session", b"generation", session_valid=lambda: False)
  assert not params.get_bool("IsRhdDetected")


def test_units_without_offsets_work_onroad_but_wheel_side_waits(tmp_path):
  params = Params(str(tmp_path))
  owner = LocalizationSettingsOwner(params, lambda: True, lambda: False)
  rows = owner.snapshot().rows
  assert rows[0].available and rows[0].key == "IsMetric" and not rows[3].available
  assert owner.apply(FeatureSettingsRequest("IsMetric", None, "Metric"))
  assert params.get_bool("IsMetric")
  assert not Path(params.get_param_path(DOCUMENT_KEY)).exists()


def test_galaxy_units_do_not_require_legacy_offset_migration(tmp_path):
  params = Params(str(tmp_path))
  Path(params.get_param_path("Offset1")).write_bytes(b"5.0")
  owner = LocalizationSettingsOwner(params, lambda: True, lambda: False)
  row = owner.snapshot().rows[1]
  assert row.available and row.key == "GalaxySpeedUnits"
  assert owner.apply(FeatureSettingsRequest("GalaxySpeedUnits", None, "Metric"))
  assert not owner.apply(FeatureSettingsRequest("IsMetric", None, "On")), "the old On/Off values are not accepted"
  assert not params.get_bool("IsMetric")


def test_comma_and_galaxy_units_rows(tmp_path):
  params = Params(str(tmp_path))
  params.put_bool("IsMetric", False, block=True)
  owner = LocalizationSettingsOwner(params, lambda: True, lambda: True)
  rows = owner.snapshot().rows
  assert [(r.key, r.label, r.value, r.choices, r.default_value) for r in rows[:3]] == [
    ("IsMetric", "Comma speed units", "Imperial", ("Metric", "Imperial"), "Imperial"),
    ("GalaxySpeedUnits", "Galaxy speed units", "Same as comma", ("Same as comma", "Metric", "Imperial"), "Same as comma"),
    ("GalaxySmallDistanceUnits", "Galaxy distance units", "Same as comma", ("Same as comma", "Metric", "Imperial"), "Same as comma")]
  assert owner.apply(FeatureSettingsRequest("GalaxySmallDistanceUnits", None, "Imperial"))
  assert params.get("GalaxySmallDistanceUnits") == 2


def _width_row(metric):
  from openpilot.starpilot.ui.feature_settings_state import FeatureRow
  return FeatureRow("lane_change:width", "Minimum adjacent lane width", "5", b"x", step=0.1, minimum=0.0, maximum=15.0, unit="ft",
                    available=True, default_value="0")


@pytest.mark.parametrize("comma_metric,galaxy_distance,unit", [
  (False, None, "ft"),     # Same as comma follows the comma's (imperial) speed units
  (True, None, "m"),       # metric comma: distances follow
  (False, b"1", "m"),      # Galaxy's own choice wins
  (True, b"2", "ft"),
])
def test_galaxy_distance_same_as_comma_follows_comma_units(tmp_path, comma_metric, galaxy_distance, unit):
  from openpilot.starpilot.galaxy import slider_units
  params = Params(str(tmp_path))
  params.put_bool("IsMetric", comma_metric, block=True)
  if galaxy_distance is not None:
    Path(params.get_param_path("GalaxySmallDistanceUnits")).write_bytes(galaxy_distance)
  shown = slider_units.project(params, _width_row(comma_metric))
  assert shown.unit == unit
  assert float(shown.value) == pytest.approx(5 if unit == "ft" else 1.5), "5 ft shows as a clean 1.5 m (0.05 m steps)"
  if galaxy_distance is None:
    assert ("IsMetric", b"1" if comma_metric else b"0") in shown.dependencies, "a comma units change refreshes the view"


def test_retired_offsets_do_not_require_parking_or_migration_to_change_units(tmp_path):
  params = Params(str(tmp_path))
  Path(params.get_param_path("Offset1")).write_bytes(b"5.0")
  owner = LocalizationSettingsOwner(params, lambda: True, lambda: False)
  row = owner.snapshot().rows[0]
  assert row.available
  assert owner.apply(FeatureSettingsRequest("IsMetric", None, "Metric"))
  assert params.get_bool("IsMetric")
  assert not Path(params.get_param_path(DOCUMENT_KEY)).exists()
  assert Path(params.get_param_path("Offset1")).read_bytes() == b"5.0"


@pytest.mark.parametrize("name,code", [("English", "en"), ("日本語", "ja"), ("한국어", "ko"),
                                       ("Español", "es"), ("Français", "fr"), ("Deutsch", "de"), ("Tiếng Việt", "vi")])
def test_language_uses_existing_persistent_setting(tmp_path, name, code):
  params = Params(str(tmp_path))
  owner = LocalizationSettingsOwner(params, lambda: True, lambda: True)
  row = owner.snapshot().rows[4]
  assert row.value == "English" and name in row.choices
  assert owner.apply(FeatureSettingsRequest(row.key, row.source, name))
  assert params.get("LanguageSetting") == code
  assert owner.snapshot().rows[4].value == name
  assert not owner.apply(FeatureSettingsRequest(row.key, b"stale", "English"))
  assert params.get("LanguageSetting") == code


@pytest.mark.parametrize("authorized,parked,value", [(False, True, "日本語"), (True, False, "日本語"),
                                                      (True, True, "unsupported")])
def test_language_rechecks_authority_and_parking(tmp_path, authorized, parked, value):
  params = Params(str(tmp_path))
  owner = LocalizationSettingsOwner(params, lambda: authorized, lambda: parked)
  assert not owner.apply(FeatureSettingsRequest("LanguageSetting", None, value))
  assert not Path(params.get_param_path("LanguageSetting")).exists()


def test_language_dropdown_through_gateway_rechecks_session(tmp_path):
  params = Params(str(tmp_path))
  context = SimpleNamespace(sample=lambda: AuthorityContext(True, None, None))
  gateway = SettingsGateway(params, cast(ContextSource, context))
  token, generation = "language-session", b"generation"
  page = gateway.page("localization", token, generation)
  assert page["rows"][4]["choices"] == list(LocalizationSettingsOwner(params, lambda: True, lambda: True).snapshot().rows[4].choices)
  pending = gateway.preview(page["view"], 4, 0, token, generation, value="日本語")["intent"]
  with pytest.raises(SettingsChanged):
    gateway.confirm(pending, token, generation, session_valid=lambda: False)
  assert not Path(params.get_param_path("LanguageSetting")).exists()
  page = gateway.page("localization", token, generation)
  pending = gateway.preview(page["view"], 4, 0, token, generation, value="日本語")["intent"]
  assert gateway.confirm(pending, token, generation)
  assert params.get("LanguageSetting") == "ja"
