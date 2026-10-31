"""BluePilot lateral settings: which rows a Ford offers and when, and what each change saves."""
from pathlib import Path

import pytest
from opendbc.car.car_helpers import interfaces
from opendbc.car.ford.values import CAR
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.common.params import Params
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.ford_bluepilot_feature import ENABLE, MODE, PINION, FordBluePilotFeature


class Owner:
  def __init__(self, params, cp, parked):
    self.params, self.cp, self.parked = params, cp, parked

  def vehicle_params(self):
    return self.cp

  def vehicle_fingerprint(self):
    return str(self.cp.carFingerprint)

  def authority(self, group):
    return group == "preferences" or (group == "parked_preferences" and self.parked)


def owner(tmp_path, car=CAR.FORD_F_150_MK14, *, parked=True):
  return Owner(Params(str(tmp_path)), interfaces[car].get_non_essential_params(car), parked)


def rows(o):
  return {row.key: row for row in FordBluePilotFeature(o).rows(o.parked)}


def save(o, key, raw):
  Path(o.params.get_param_path(key)).write_bytes(raw)


def saved(o, key):
  path = Path(o.params.get_param_path(key))
  return path.read_bytes() if path.exists() else None


def change(o, key, value, row=None):
  row = row or rows(o)[key]
  request = FeatureSettingsRequest(key, row.source, value, vehicle_fingerprint=o.vehicle_fingerprint(),
                                   capability=row.capability, dependencies=row.dependencies)
  return FordBluePilotFeature(o).apply(request)


def test_off_by_default_with_only_the_switch_editable(tmp_path):
  o = owner(tmp_path)
  shown = rows(o)
  assert shown[ENABLE].value == "Off" and shown[ENABLE].available
  assert not shown[MODE].available and not shown[PINION].available
  assert shown["LC_PID_gain_UI_curv"].reason == "Turn on BluePilot Lateral first"
  assert not any(key.endswith("_ang") for key in shown)


def test_turning_on_saves_the_inverse_and_unlocks_the_tuning(tmp_path):
  o = owner(tmp_path)
  assert change(o, ENABLE, "On")
  assert saved(o, ENABLE) == b"0"
  shown = rows(o)
  assert shown[MODE].available and shown[PINION].available
  assert shown["enable_lane_positioning_curv"].available
  assert not shown["custom_path_offset_curv"].available
  assert shown["custom_path_offset_curv"].reason == "Turn on Lane Positioning first"
  assert change(o, "enable_lane_positioning_curv", "On")
  assert rows(o)["custom_path_offset_curv"].available


def test_angle_mode_shows_its_own_rows(tmp_path):
  o = owner(tmp_path)
  save(o, ENABLE, b"0")
  assert change(o, MODE, "Angle")
  assert saved(o, MODE) == b"1"
  shown = rows(o)
  assert {key for key in shown if key.endswith("_ang")} == {
    "FordLowSpeedFactor_ang", "FordHighSpeedFactor_ang", "FordHighSpeedDampening_ang", "lane_change_factor_high_ang",
    "enable_lane_positioning_ang", "custom_path_offset_ang", "lane_centering_strength_ang"}
  assert not any(key.endswith("_curv") for key in shown)


def test_startup_settings_change_only_while_parked_and_tuning_any_time(tmp_path):
  o = owner(tmp_path, parked=False)
  save(o, ENABLE, b"0")
  shown = rows(o)
  assert not shown[ENABLE].available and not shown[MODE].available and not shown[PINION].available
  assert shown["lane_change_factor_high_curv"].available
  assert not change(o, ENABLE, "Off")
  assert change(o, "lane_change_factor_high_curv", "0.9")
  assert saved(o, "lane_change_factor_high_curv") == b"0.9"


def test_numbers_stay_on_the_rows_steps_and_range(tmp_path):
  o = owner(tmp_path)
  save(o, ENABLE, b"0")
  save(o, "enable_lane_positioning_curv", b"1")
  row = rows(o)["LC_PID_gain_UI_curv"]
  assert (row.minimum, row.maximum, row.step, row.value) == (0.0, 50.0, 0.5, "1.0")
  assert not change(o, "LC_PID_gain_UI_curv", "1.3")
  assert not change(o, "LC_PID_gain_UI_curv", "50.5")
  assert change(o, "LC_PID_gain_UI_curv", "1.5")
  assert saved(o, "LC_PID_gain_UI_curv") == b"1.5"


@pytest.mark.parametrize("car,pinion", ((CAR.FORD_F_150_MK14, True), (CAR.FORD_MONDEO_MK5, True), (CAR.FORD_EDGE_MK2, False)))
def test_pinion_only_on_platforms_with_a_geometry_row(tmp_path, car, pinion):
  assert (PINION in rows(owner(tmp_path, car))) == pinion


@pytest.mark.parametrize("car", (CAR.FORD_TRANSIT_MK5, TOYOTA.TOYOTA_RAV4_TSS2_2023))
def test_no_rows_without_bluepilot_lateral(tmp_path, car):
  assert rows(owner(tmp_path, car)) == {}


def test_safe_mode_keeps_bluepilot_lateral_off(tmp_path):
  o = owner(tmp_path)
  save(o, "SafeMode", b"1")
  assert not rows(o)[ENABLE].available
  assert rows(o)[ENABLE].reason == "Safe Mode must be off"
  assert not change(o, ENABLE, "On")


def test_a_request_from_before_its_dependency_changed_is_refused(tmp_path):
  o = owner(tmp_path)
  save(o, ENABLE, b"0")
  stale = rows(o)["lane_change_factor_high_curv"]
  save(o, ENABLE, b"1")
  assert not change(o, "lane_change_factor_high_curv", "0.9", row=stale)
  assert saved(o, "lane_change_factor_high_curv") is None


def test_an_invalid_saved_value_repairs_to_the_default(tmp_path):
  o = owner(tmp_path)
  save(o, ENABLE, b"0")
  save(o, "lane_change_factor_high_curv", b"7")
  row = rows(o)["lane_change_factor_high_curv"]
  assert row.value == "Invalid saved value" and row.available and row.repair_value == "0.85"
  assert change(o, "lane_change_factor_high_curv", row.repair_value)
  assert saved(o, "lane_change_factor_high_curv") == b"0.85"
