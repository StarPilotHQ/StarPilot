from dataclasses import replace
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest
from opendbc.car.structs import CarParams
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.common.params import Params
from openpilot.starpilot.aol.vehicle import policy_for
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change


@pytest.mark.parametrize("brand", (None, "hyundai", "ford", "toyota", "gm", "tesla", "subaru"))
def test_aol_master_is_a_saved_preference_without_runtime_authority(brand):
  with tempfile.TemporaryDirectory() as directory:
    params = Params(directory)
    params.put_bool("AlwaysOnLateral", False, block=True)
    cp = None
    if brand is not None:
      cp = CarParams.new_message(brand=brand, carFingerprint="unsupported-runtime-profile")
    owner = FeatureSettingsOwner(params, lambda group: group == "preferences",
                                 vehicle_fingerprint=lambda: cp.carFingerprint if cp is not None else None,
                                 vehicle_params=lambda: cp)
    before = policy_for(cp)
    row = next(row for row in owner.snapshot("aol", parked=True, system_long=False,
                                            lateral_context=False, metric=False).rows if row.key == "AlwaysOnLateral")
    assert row.available
    assert "compatible vehicle" not in row.reason
    request = row_change(row, 1)
    assert request is not None
    assert owner.apply(request)
    assert params.get_bool("AlwaysOnLateral")
    assert policy_for(cp) == before
    assert not before.runtime_supported
    assert not owner.apply(request)
    assert not owner.apply(replace(request, expected=b"1", dependencies=()))


def test_master_preserves_threshold_and_preference_validation():
  with tempfile.TemporaryDirectory() as directory:
    params = Params(directory)
    params.put_bool("AlwaysOnLateral", False, block=True)
    authorized = True
    owner = FeatureSettingsOwner(params, lambda group: authorized and group == "preferences",
                                 vehicle_fingerprint=lambda: None)
    def master():
      return next(row for row in owner.snapshot("aol", parked=True, system_long=False,
                                               lateral_context=False, metric=False).rows if row.key == "AlwaysOnLateral")
    request = row_change(master(), 1)
    assert request is not None
    authorized = False
    assert not owner.apply(request)
    authorized = True
    Path(params.get_param_path("AolBrakePauseSpeedMps")).write_bytes(b"nan")
    assert not master().available
    assert not owner.apply(request)
    Path(params.get_param_path("AlwaysOnLateral")).write_bytes(b"broken")
    assert not master().available
    assert not owner.apply(request)


@pytest.mark.parametrize("camera", (False, True))
@pytest.mark.parametrize("external", (False, True))
def test_ev6_hda1_stock_profile_already_has_runtime_policy(camera, external):
  cp = CarParams.new_message(brand="hyundai", carFingerprint=CAR.KIA_EV6, pcmCruise=True)
  cp.flags = int(HyundaiFlags.CANFD | HyundaiFlags.EV | (HyundaiFlags.CANFD_CAMERA_SCC if camera else 0))
  cp.steerControlType = CarParams.SteerControlType.torque
  configs = [{"safetyModel": "hyundaiCanfd", "safetyParam": 1 | (8 if camera else 0)}]
  if external:
    configs.insert(0, {"safetyModel": "noOutput", "safetyParam": 0})
  cp.safetyConfigs = configs
  assert policy_for(cp).settings_supported
  assert policy_for(cp).runtime_supported


def test_runtime_async_save_uses_global_preference_authority():
  from openpilot.starpilot.ui.runtime_app import StarShellSession
  from openpilot.starpilot.ui.shell import ShellMode
  with tempfile.TemporaryDirectory() as directory:
    params = Params(directory)
    params.put_bool("AlwaysOnLateral", False, block=True)
    session = StarShellSession.__new__(StarShellSession)
    session._mode = ShellMode.HOME
    with patch.object(session, "_feature_authority", side_effect=lambda group: group == "preferences"), \
         patch.object(session, "_unavailable", side_effect=pytest.fail):
      session.feature_owner = FeatureSettingsOwner(params, session._feature_authority, vehicle_fingerprint=lambda: None)
      row = next(row for row in session.feature_owner.snapshot("aol", parked=True, system_long=False,
                                                             lateral_context=False, metric=False).rows
                 if row.key == "AlwaysOnLateral")
      request = row_change(replace(row, available=True), 1)
      assert request is not None
      assert not session.feature_request(request)
      pending = session._aol_save
      assert pending is not None and pending.done.wait(5)
      session._poll_aol_save()
      assert params.get_bool("AlwaysOnLateral")
      assert session.notice == "AOL preference saved"
