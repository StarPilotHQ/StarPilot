from unittest.mock import Mock
"""The vehicle switch saves exact bytes only for current supported parked cars."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from opendbc.car.structs import car
from opendbc.car.toyota.interface import CarInterface, toyota_auto_hold_supported
from opendbc.car.toyota.values import CAR, ToyotaFlags, ToyotaSafetyFlags
from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged, SettingsGateway
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, row_change


@pytest.fixture
def setup(tmp_path):
  params = Params(str(tmp_path))
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
  assert toyota_auto_hold_supported(cp)
  current = SimpleNamespace(parked=True, cp=cp, raw=b"current-cp")
  owner = FeatureSettingsOwner(params, lambda group: current.parked and group == "vehicle",
                               vehicle_fingerprint=lambda: getattr(current.cp, "carFingerprint", None), vehicle_params=lambda: current.cp)
  context = SimpleNamespace(sample=lambda: AuthorityContext(current.parked, current.cp, current.raw))
  gateway = SettingsGateway(params, Mock(wraps=context), clock=lambda: 10)
  return params, current, owner, gateway


def row(owner, parked=True):
  state = owner.snapshot(FeaturePage.VEHICLE, parked=parked, system_long=True, lateral_context=True, metric=False)
  toggle, = (item for item in state.rows if item.key == "ToyotaAutoHold")
  return toggle


def test_default_off_saved_switch_applies_after_startup_without_native_menu_link(setup):
  params, _, owner, _ = setup
  toggle = row(owner)
  assert toggle.label == "Automatic Brake Hold"
  assert toggle.value == "Off" and toggle.source is None and toggle.available
  assert params.get("ToyotaAutoHold") is None
  assert "next startup" in toggle.reason
  request = replace(required_change(toggle), confirmation=True)
  assert owner.apply(request)
  assert Path(params.get_param_path("ToyotaAutoHold")).read_bytes() == b"1"
  assert row(owner).value == "On"
  assert not owner.apply(request)  # The original displayed bytes are now stale.
  assert owner.apply(replace(required_change(row(owner)), confirmation=True))
  assert Path(params.get_param_path("ToyotaAutoHold")).read_bytes() == b"0"
  hub = owner.snapshot(FeaturePage.HUB, parked=True, system_long=True, lateral_context=True, metric=False)
  vehicle_links = [item for item in hub.rows if item.page == FeaturePage.VEHICLE]
  assert len(vehicle_links) == 1 and vehicle_links[0].available
  assert vehicle_links[0].label == 'Vehicle Settings'


@pytest.mark.parametrize("change", [
  lambda cp: setattr(cp, "openpilotLongitudinalControl", False),
  lambda cp: setattr(cp, "passive", True),
  lambda cp: setattr(cp, "dashcamOnly", True),
  lambda cp: setattr(cp, "notCar", True),
  lambda cp: setattr(cp, "brand", "hyundai"),
  lambda cp: setattr(cp, "carFingerprint", "UNSUPPORTED TOYOTA"),
  lambda cp: setattr(cp, "safetyConfigs", [cp.safetyConfigs[0].to_dict()] * 2),
  lambda cp: setattr(cp, "flags", int(ToyotaFlags.SECOC)),
  lambda cp: setattr(cp.safetyConfigs[0], "safetyParam", int(ToyotaSafetyFlags.STOCK_LONGITUDINAL)),
  lambda cp: setattr(cp.safetyConfigs[0], "safetyParam", int(ToyotaSafetyFlags.SECOC)),
  lambda cp: setattr(cp.safetyConfigs[0], "safetyParam", cp.safetyConfigs[0].safetyParam | 0x8000),
  lambda cp: setattr(cp.safetyConfigs[0], "safetyParam", cp.safetyConfigs[0].safetyParam ^ 1),
  lambda cp: setattr(cp.safetyConfigs[0], "safetyParam", 0),
  lambda cp: setattr(cp.safetyConfigs[0], "safetyModel", car.CarParams.SafetyModel.noOutput),
])
def test_unsupported_or_changed_cp_cannot_edit_saved_switch(setup, change):
  params, current, owner, _ = setup
  request = replace(required_change(row(owner)), confirmation=True)
  change(current.cp)
  state = owner.snapshot(FeaturePage.VEHICLE, parked=True, system_long=True, lateral_context=True, metric=False)
  assert all(item.key != "ToyotaAutoHold" for item in state.rows)
  hub = owner.snapshot(FeaturePage.HUB, parked=True, system_long=True, lateral_context=True, metric=False)
  assert all(item.page != FeaturePage.VEHICLE for item in hub.rows)
  assert not owner.apply(request)
  assert params.get("ToyotaAutoHold") is None


@pytest.mark.parametrize("raw", [b"2", b"true", b"1 ", b"\xff", b"1" * 129])
def test_invalid_saved_values_remain_untouched(setup, raw):
  params, _, owner, _ = setup
  path = Path(params.get_param_path("ToyotaAutoHold"))
  path.write_bytes(raw)
  assert not row(owner).available
  assert row_change(row(owner)) is None
  assert path.read_bytes() == raw


def gateway_hold_row(page):
  index, = (i for i, item in enumerate(page["rows"]) if item["label"] == "Automatic Brake Hold")
  return index


@pytest.mark.parametrize("revocation", ["cp", "bytes", "session"])
def test_gateway_existing_guard_rejects_changed_context_or_source(setup, revocation):
  params, current, _, gateway = setup
  page = gateway.page("vehicle", "session", b"generation")
  assert page["rows"][gateway_hold_row(page)]["choices"] == ["Off", "On"]
  intent = gateway.preview(page["view"], gateway_hold_row(page), 0, "session", b"generation", value="On")
  if revocation == "cp":
    current.raw = b"changed-cp"
  elif revocation == "bytes":
    Path(params.get_param_path("ToyotaAutoHold")).write_bytes(b"0")
  if revocation in ("cp", "session"):
    with pytest.raises(SettingsChanged):
      gateway.confirm(intent["intent"], "session", b"generation", session_valid=lambda: revocation != "session")
  else:
    assert not gateway.confirm(intent["intent"], "session", b"generation")
  if revocation == "bytes":
    assert Path(params.get_param_path("ToyotaAutoHold")).read_bytes() == b"0"
  else:
    assert params.get("ToyotaAutoHold") is None


def test_gateway_direct_on_off_uses_existing_value_save_path(setup):
  params, _, _, gateway = setup
  for value in ("On", "Off"):
    page = gateway.page("vehicle", "session", b"generation")
    intent = gateway.preview(page["view"], gateway_hold_row(page), 0, "session", b"generation", value=value)
    assert "next startup" in intent["question"]
    assert gateway.confirm(intent["intent"], "session", b"generation")
    assert Path(params.get_param_path("ToyotaAutoHold")).read_bytes() == (b"1" if value == "On" else b"0")


def test_missing_or_changed_supported_vehicle_context_cannot_use_old_request(setup):
  params, current, owner, _ = setup
  request = replace(required_change(row(owner)), confirmation=True)
  current.cp.carVin = "different-vehicle"
  assert row(owner).available
  assert not owner.apply(request)
  current.cp = None
  state = owner.snapshot(FeaturePage.VEHICLE, parked=True, system_long=True, lateral_context=True, metric=False)
  assert all(item.key != "ToyotaAutoHold" for item in state.rows)
  assert not owner.apply(request)
  assert params.get("ToyotaAutoHold") is None


def required_change(row):
  request = row_change(row)
  assert request is not None
  return request


def test_gateway_saved_auto_hold_preference_remains_editable_onroad(setup):
  params, current, _, gateway = setup
  page = gateway.page("vehicle", "session", b"generation")
  intent = gateway.preview(page["view"], gateway_hold_row(page), 0, "session", b"generation", value="On")
  current.parked = False
  assert gateway.confirm(intent["intent"], "session", b"generation")
  assert Path(params.get_param_path("ToyotaAutoHold")).read_bytes() == b"1"
