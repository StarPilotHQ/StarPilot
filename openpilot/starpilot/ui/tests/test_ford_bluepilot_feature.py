"""The compact Ford menu and its runtime lane-positioning policy share the same saved preferences."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from opendbc.car import Bus
from opendbc.car.car_helpers import interfaces
from opendbc.car.ford.values import CAR
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.common.params import Params
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, FeatureSettingsRequest, feature_parent_page, feature_parent_title
from openpilot.starpilot.ui.ford_bluepilot_feature import MODE, PINION, CONTROLLERS, BLUEPILOT_VERSION, FordBluePilotFeature


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
  feature = FordBluePilotFeature(o)
  return {row.key: row for row in (*feature.rows(o.parked), *feature.rows(o.parked, options=True)) if row.key}


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


def test_compact_menu_has_two_controllers_and_versioned_options_page(tmp_path):
  o = owner(tmp_path)
  primary, options = FordBluePilotFeature(o).rows(True)
  assert primary.key == MODE and primary.label == "Ford Controller"
  assert primary.choices == ("BluePilot Curvature", "BluePilot Angle")
  assert primary.value == primary.default_value == CONTROLLERS[0]
  assert options.label == "BluePilot Controller Options" and options.value == BLUEPILOT_VERSION
  assert options.page == FeaturePage.BLUEPILOT and options.available
  assert feature_parent_page(options.page) == FeaturePage.TORQUE
  assert feature_parent_title(options.page) == "Steering and Torque"
  assert PINION not in {row.key for row in FordBluePilotFeature(o).rows(True)}
  assert rows(o)[PINION].available


def test_retired_stock_choice_cannot_be_saved(tmp_path):
  o = owner(tmp_path)
  save(o, "disable_BP_lat_UI", b"1")
  assert rows(o)[MODE].value == "BluePilot Curvature"
  assert rows(o)["lane_change_factor_high_curv"].available
  assert not change(o, MODE, "Stock Ford")
  assert not FordBluePilotFeature(o).apply(FeatureSettingsRequest("disable_BP_lat_UI", b"1", "Stock Ford"))
  assert change(o, MODE, CONTROLLERS[1])
  assert saved(o, MODE) == b"1"


def test_no_bluepilot_lane_positioning_rows_or_requests(tmp_path):
  from openpilot.starpilot.car.ford.bluepilot import HOST_SETTINGS
  o = owner(tmp_path)
  for mode in CONTROLLERS:
    assert change(o, MODE, mode)
    shown = rows(o)
    for key in HOST_SETTINGS:
      assert key not in shown
      assert not FordBluePilotFeature(o).apply(FeatureSettingsRequest(key, None, "On"))


def test_angle_mode_shows_only_its_remaining_options(tmp_path):
  o = owner(tmp_path)
  assert change(o, MODE, CONTROLLERS[1])
  shown = rows(o)
  assert {key for key in shown if key.endswith("_ang")} == {
    "FordLowSpeedFactor_ang", "FordHighSpeedFactor_ang", "FordHighSpeedDampening_ang", "lane_change_factor_high_ang"}
  assert not any(key.endswith("_curv") for key in shown)


def test_startup_settings_change_only_while_parked_and_tuning_any_time(tmp_path):
  o = owner(tmp_path, parked=False)
  shown = rows(o)
  assert not shown[MODE].available and not shown[PINION].available
  assert shown["lane_change_factor_high_curv"].available
  assert not change(o, MODE, CONTROLLERS[1])
  assert change(o, "lane_change_factor_high_curv", "0.9")
  assert saved(o, "lane_change_factor_high_curv") == b"0.9"


def test_numbers_stay_on_the_rows_steps_and_range(tmp_path):
  o = owner(tmp_path)
  assert change(o, MODE, CONTROLLERS[1])
  row = rows(o)["FordLowSpeedFactor_ang"]
  assert (row.minimum, row.maximum, row.step, row.value) == (0.5, 1.5, 0.01, "1.0")
  assert not change(o, row.key, "1.005")
  assert not change(o, row.key, "1.51")
  assert change(o, row.key, "1.15")
  assert saved(o, row.key) == b"1.15"


@pytest.mark.parametrize("car,pinion", ((CAR.FORD_F_150_MK14, True), (CAR.FORD_MONDEO_MK5, True), (CAR.FORD_EDGE_MK2, False)))
def test_pinion_only_on_platforms_with_a_geometry_row(tmp_path, car, pinion):
  assert (PINION in rows(owner(tmp_path, car))) == pinion


@pytest.mark.parametrize("car", (CAR.FORD_TRANSIT_MK5, TOYOTA.TOYOTA_RAV4_TSS2_2023))
def test_no_rows_without_bluepilot_lateral(tmp_path, car):
  assert rows(owner(tmp_path, car)) == {}


def test_safe_mode_refuses_controller_and_tuning_changes(tmp_path):
  o = owner(tmp_path)
  save(o, "SafeMode", b"1")
  assert not rows(o)[MODE].available
  assert rows(o)[MODE].reason == "Safe Mode must be off"
  assert not change(o, MODE, CONTROLLERS[1])
  assert not change(o, "lane_change_factor_high_curv", "0.9")


def test_a_request_from_before_its_dependency_changed_is_refused(tmp_path):
  o = owner(tmp_path)
  stale = rows(o)["lane_change_factor_high_curv"]
  save(o, MODE, b"1")
  assert not change(o, "lane_change_factor_high_curv", "0.9", row=stale)
  assert saved(o, "lane_change_factor_high_curv") is None


def test_an_invalid_saved_value_repairs_to_the_default(tmp_path):
  o = owner(tmp_path)
  save(o, "lane_change_factor_high_curv", b"7")
  row = rows(o)["lane_change_factor_high_curv"]
  assert row.value == "Invalid saved value" and row.available and row.repair_value == "0.85"
  assert change(o, row.key, row.repair_value)
  assert saved(o, row.key) == b"0.85"


@pytest.mark.parametrize("angle", (False, True))
def test_real_library_gets_no_secondary_lane_positioning_even_with_legacy_saves(tmp_path, angle):
  from opendbc.can import CANPacker
  from opendbc.car.ford import fordcan
  from opendbc.car.ford.values import DBC
  from opendbc.bluepilot_lateral.hosts.starpilot.lateral import BluePilotLateral, FrameInputs
  from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import init_ford_safety_param_starpilot
  from openpilot.starpilot.car.ford.bluepilot import BluePilotInputs, HOST_SETTINGS, startup_params
  from openpilot.starpilot.lateral.lane_runtime import read_settings
  o = owner(tmp_path)
  for key, value in HOST_SETTINGS.items():
    save(o, key, b"1" if isinstance(value, bool) else b"0.5")
  save(o, MODE, b"1" if angle else b"0")
  save(o, "LaneCentering", b"1")
  save(o, "LaneCenterOffset", b"0.2")
  originals = {key: saved(o, key) for key in (*HOST_SETTINGS, "LaneCentering", "LaneCenterOffset")}
  startup = startup_params(o.params, enabled=True)
  assert startup["disable_BP_lat_UI"] == "0"
  init_ford_safety_param_starpilot(o.cp, startup)
  with patch("openpilot.starpilot.car.ford.bluepilot.messaging.SubMaster"):
    reader = BluePilotInputs(o.params)
  frame = FrameInputs(params=reader.read_settings())
  bp = BluePilotLateral(o.cp, CANPacker(DBC[o.cp.carFingerprint][Bus.pt]), fordcan.CanBus(o.cp))
  assert bp.update(frame, 0)
  assert int(bp.curv.primary_lateral_control) == int(angle)
  assert not bp.curv.enable_lane_positioning_curv and not bp.curv.enable_lane_full_mode_curv
  assert bp.curv.custom_path_offset_curv == bp.curv.LC_PID_gain_UI_curv == 0.0
  assert not bp.angle.enable_lane_positioning_ang
  assert bp.angle.custom_path_offset_ang == bp.angle.lane_centering_strength_ang == 0.0
  global_lane = read_settings(o.params)
  assert global_lane.enabled and global_lane.offset_m == 0.2
  assert {key: saved(o, key) for key in originals} == originals


@pytest.mark.parametrize("safe,enabled", ((b"1", True), (b"bad", True), (b"0", False)))
def test_retiring_stock_choice_keeps_startup_safety_gates(tmp_path, safe, enabled):
  from openpilot.starpilot.car.ford.bluepilot import startup_params
  o = owner(tmp_path)
  save(o, "SafeMode", safe)
  assert startup_params(o.params, enabled=enabled) == {"disable_BP_lat_UI": "1"}


def test_real_native_and_galaxy_owners_share_the_options_page(tmp_path):
  from openpilot.starpilot.galaxy.settings import AuthorityContext, ContextSource, SettingsGateway
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
  o = owner(tmp_path)
  native = FeatureSettingsOwner(o.params, vehicle_params=o.vehicle_params, vehicle_fingerprint=o.vehicle_fingerprint,
                                authority=o.authority)
  root = native.snapshot('torque', parked=True, system_long=False, lateral_context=True, metric=False)
  assert [r.key for r in root.rows[:2]] == [MODE, ""]
  assert root.rows[1].page == FeaturePage.BLUEPILOT
  context = SimpleNamespace(parked=True)
  source = Mock(spec=ContextSource, sample=lambda: AuthorityContext(context.parked, o.cp, o.cp.to_bytes()))
  gateway = SettingsGateway(o.params, source, clock=lambda: 10)
  try:
    page = gateway.page(FeaturePage.BLUEPILOT, 'session', b'generation')
    assert page['title'] == 'BluePilot Controller Options'
    assert any(r['label'] == 'Use Pinion Yaw Sensor' for r in page['rows'])
    assert not any('Center' in r['label'] or 'Offset' in r['label'] for r in page['rows'])
    index = next(i for i, r in enumerate(page['rows']) if r['label'] == 'Human Turn Detection')
    intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='Off')
    assert gateway.confirm(intent['intent'], 'session', b'generation')
    assert saved(o, 'enable_human_turn_detection_curv') == b'0'
  finally:
    gateway.close()
