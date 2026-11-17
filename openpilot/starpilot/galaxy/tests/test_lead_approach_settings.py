"""Galaxy global boolean preferences share saved Params and fresh authority."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot import saved_document
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged, SettingsGateway
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner


@pytest.fixture(params=[
  ("LeadApproachBuffer", "Approaching lead buffer", "Off"),
  ("FasterLeadTakeoff", "Faster Takeoffs", "Off"),
  ("ExperimentalSpeedNudge", "Experimental Speed Nudge", "On"),
  ("DisengageOnAccelerator", "Disengage on Accelerator Pedal", "Off"),
])
def setup(tmp_path, request):
  params = Params(str(tmp_path))
  cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
  assert cp.openpilotLongitudinalControl and cp.pcmCruise
  key, label, default = request.param
  current = SimpleNamespace(parked=True, cp=cp, raw=b"current-honda-cp", key=key, label=label, default=default)
  context = SimpleNamespace(sample=lambda: AuthorityContext(current.parked, current.cp, current.raw))
  return params, current, SettingsGateway(params, Mock(wraps=context), clock=lambda: 10.0)


def page_row(gateway, label):
  page = gateway.page("profiles", "session", b"generation")
  index = next(index for index, row in enumerate(page["rows"]) if row["label"] == label)
  return page, index, page["rows"][index]


def test_galaxy_get_select_apply_without_cp_or_profile_health(setup):
  params, current, gateway = setup
  current.cp = None
  current.raw = None
  Path(params.get_param_path("LongitudinalPersonalityProfiles")).write_bytes(b"{invalid")
  Path(params.get_param_path("CustomPersonalities")).write_bytes(b"invalid")
  page, index, row = page_row(gateway, current.label)
  assert row["value"] == current.default and row["defaultValue"] == current.default
  assert row["available"] and row["choices"] == ["Off", "On"]
  intent = gateway.preview(page["view"], index, 0, "session", b"generation", value="On")
  assert intent["proposed"] == "On"
  assert gateway.confirm(intent["intent"], "session", b"generation")
  assert Path(params.get_param_path(current.key)).read_bytes() == b"1"
  page, index, row = page_row(gateway, current.label)
  assert row["value"] == "On"
  intent = gateway.preview(page["view"], index, 0, "session", b"generation", value="Off")
  assert gateway.confirm(intent["intent"], "session", b"generation")
  assert Path(params.get_param_path(current.key)).read_bytes() == b"0"
  assert not params.get_bool("OnroadCycleRequested")


@pytest.mark.parametrize("field,value", [("openpilotLongitudinalControl", False), ("passive", True),
                                          ("dashcamOnly", True), ("notCar", True), ("carFingerprint", "")])
def test_galaxy_saved_preference_remains_editable_with_unqualified_cp(setup, field, value):
  params, current, gateway = setup
  setattr(current.cp, field, value)
  page, index, row = page_row(gateway, current.label)
  assert row["available"]
  intent = gateway.preview(page["view"], index, 0, "session", b"generation", value="On")
  assert gateway.confirm(intent["intent"], "session", b"generation")
  assert Path(params.get_param_path(current.key)).read_bytes() == b"1"


def test_galaxy_intent_rechecks_context_session_and_exact_saved_source(setup):
  params, current, gateway = setup
  path = Path(params.get_param_path(current.key))
  for revocation in ("context", "session", "source"):
    page, index, _ = page_row(gateway, current.label)
    intent = gateway.preview(page["view"], index, 0, "session", b"generation", value="On")
    if revocation == "context":
      current.raw = b"different-car-source"
      with pytest.raises(SettingsChanged):
        gateway.confirm(intent["intent"], "session", b"generation")
      current.raw = b"current-honda-cp"
    elif revocation == "session":
      with pytest.raises(SettingsChanged):
        gateway.confirm(intent["intent"], "session", b"generation", session_valid=lambda: False)
    else:
      path.write_bytes(b"0")
      assert not gateway.confirm(intent["intent"], "session", b"generation")
      path.unlink()
    assert not path.exists()


def test_saved_global_preference_remains_editable_onroad(setup):
  params, current, gateway = setup
  page, index, _ = page_row(gateway, current.label)
  intent = gateway.preview(page["view"], index, 0, "session", b"generation", value="On")
  current.parked = False
  assert gateway.confirm(intent["intent"], "session", b"generation")
  assert Path(params.get_param_path(current.key)).read_bytes() == b"1"


def test_native_parameter_changes_are_visible_in_galaxy(setup):
  params, current, gateway = setup
  for desired in (True, False):
    params.put_bool(current.key, desired, block=True)
    _, _, row = page_row(gateway, current.label)
    assert row["value"] == ("On" if desired else "Off")


def test_invalid_saved_preference_is_not_editable(setup):
  params, current, gateway = setup
  path = Path(params.get_param_path(current.key))
  path.write_bytes(b"invalid")
  page, index, row = page_row(gateway, current.label)
  assert not row["available"] and not row["choices"]
  with pytest.raises(SettingsChanged):
    gateway.preview(page["view"], index, 0, "session", b"generation", value="On")
  assert path.read_bytes() == b"invalid"


def test_session_rechecked_after_parameter_lock(setup):
  params, current, gateway = setup
  page, index, _ = page_row(gateway, current.label)
  intent = gateway.preview(page["view"], index, 0, "session", b"generation", value="On")
  authorized = True
  acquire = saved_document.acquire_native_lock

  def revoke(fd):
    nonlocal authorized
    acquire(fd)
    authorized = False

  with patch.object(saved_document, "acquire_native_lock", side_effect=revoke):
    assert not gateway.confirm(intent["intent"], "session", b"generation", session_valid=lambda: authorized)
  assert not Path(params.get_param_path(current.key)).exists()


@pytest.mark.parametrize("galaxy", [False, True])
def test_accelerator_row_is_only_added_to_galaxy(tmp_path, galaxy):
  params = Params(str(tmp_path))
  owner = FeatureSettingsOwner(params, lambda _: True, vehicle_fingerprint=lambda: None, galaxy=lambda: galaxy)
  rows = owner.snapshot("profiles", parked=True, system_long=False, lateral_context=False, metric=False).rows
  assert any(row.key == "DisengageOnAccelerator" for row in rows) == galaxy
