from types import SimpleNamespace
from pathlib import Path
from dataclasses import replace
import pytest
from opendbc.car.tesla.values import CAR
from opendbc.car.tesla.tests.test_screen_button import screen_params
from opendbc.car.tesla.screen_button import apply_screen_button, screen_qualified, screen_tap_supported
from openpilot.common.params import Params
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
from openpilot.starpilot.car.tesla.coop_preferences import prepare
from openpilot.starpilot.car.tesla.coop_saturation import is_angle_steering_limited
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


@pytest.mark.parametrize('long', (False, True))
@pytest.mark.parametrize('brake', (False, True))
def test_screen_and_cooperative_selection_compose(long, brake):
  cp = screen_params(long=long)
  prepare(cp, True)
  apply_screen_button(cp, True, brake)
  assert cp.safetyConfigs[0].safetyParam == 768 + int(long) + 1024 * brake
  assert screen_qualified(cp)
  apply_screen_button(cp, False, brake)
  assert cp.safetyConfigs[0].safetyParam == 256 + int(long)
  assert screen_tap_supported(cp)
  prepare(cp, False)
  assert cp.safetyConfigs[0].safetyParam == int(long)


@pytest.mark.parametrize('enabled,safe,value,expected', ((True,None,b'1',True),(False,None,b'1',False),
  (True,b'1',b'1',False),(True,None,b'bad',False),(True,None,None,False)))
def test_startup_snapshot(tmp_path, enabled, safe, value, expected):
  saved = Params(str(tmp_path))
  for key, raw in (('TeslaCoopSteering',value),('SafeMode',safe)):
    if raw is not None:
      if raw == b'bad':
        Path(saved.get_param_path(key)).write_bytes(raw)
      else:
        saved.put_bool(key,raw == b'1',block=True)
  snapshot = VehicleStartupPreferences.read(saved, enabled=enabled)
  assert snapshot.tesla_cooperative is expected
  cp=screen_params()
  snapshot.prepare(cp)
  assert bool(cp.safetyConfigs[0].safetyParam & 256) is expected


def test_ui_setting_is_parked_bound_and_requires_startup(tmp_path):
  saved=Params(str(tmp_path))
  cp=screen_params()
  parked=True
  owner=FeatureSettingsOwner(saved,lambda group:parked and group=='parked_preferences',vehicle_fingerprint=lambda:cp.carFingerprint,vehicle_params=lambda:cp)
  row=owner.tesla_coop.rows(True)[0]
  assert row.label=='Tesla Co-op Steering' and row.value=='Off'
  request=FeatureSettingsRequest(row.key,row.source,'On',confirmation=True,vehicle_fingerprint=cp.carFingerprint,capability=row.capability)
  assert owner.apply(request) and saved.get_bool('TeslaCoopSteering')
  assert cp.safetyConfigs[0].safetyParam==0
  parked=False
  assert not owner.apply(replace(request,expected=b'1',value='Off'))
  cp.carFingerprint=CAR.TESLA_MODEL_Y
  assert not owner.tesla_coop.rows(True)


def test_offset_is_not_saturation_and_invalid_evidence_falls_back():
  cp=screen_params()
  prepare(cp,True)
  output=SimpleNamespace(actuatorsOutput=SimpleNamespace(steeringAngleDeg=4.))
  info=SimpleNamespace(valid=True,monoTime=100,modelLimitErrorDeg=0.,resumeLimitErrorDeg=0.,cooperativeLimitErrorDeg=0.,combinedLimitErrorDeg=0.,cooperativeOffsetDeg=4.)
  companion=SimpleNamespace(steeringLimitInfo=info)
  assert not is_angle_steering_limited(cp,0.,output,companion,True,100)
  assert is_angle_steering_limited(cp,0.,output,companion,False,100)
  assert is_angle_steering_limited(cp,0.,output,companion,True,100_000_101)
  info.combinedLimitErrorDeg=3.
  assert is_angle_steering_limited(cp,0.,output,companion,True,100)
  info.combinedLimitErrorDeg=float('nan')
  assert is_angle_steering_limited(cp,0.,output,companion,True,100)
