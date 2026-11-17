"""The saved donor option reaches the real settings and controller owners."""
from dataclasses import replace

import pytest
from opendbc.car.toyota.interface import CarInterface
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.car.toyota.tests.test_gas_override_cancel import setup, acc_frames, PROFILES
from openpilot.starpilot.car.toyota.tests.test_prius_sdsu_filter import factory
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


def eligible(cp):
  from opendbc.car.toyota.sng import eligible as check
  return check(cp)


def owner_for(params, cp, parked=True):
  return FeatureSettingsOwner(params, lambda _: parked, vehicle_params=lambda: cp,
                              vehicle_fingerprint=lambda: cp.carFingerprint)


def rows(owner, parked=True):
  return owner.snapshot('vehicle', parked=parked, system_long=True, lateral_context=True, metric=False).rows


def test_saved_sng_option_is_exposed():
  with OpenpilotPrefix():
    params = Params()
    assert any(row.key == 'SNGHack' for row in rows(owner_for(params, factory()))), 'TOYOTA_SNG_OPTION_MISSING'


def test_saved_ui_choice_binds_next_controller_without_changing_cp():
  with OpenpilotPrefix():
    params, cp = Params(), factory()
    before = cp.to_dict()
    owner = owner_for(params, cp)
    row = next(row for row in rows(owner) if row.key == 'SNGHack')
    assert row.available and row.value == 'Off'
    request = row_change(row)
    assert request is not None
    request = replace(request, confirmation=True)
    from openpilot.starpilot.ui.vehicle_bool import VEHICLE_BOOL_KEYS, confirmation_question
    assert request.key in VEHICLE_BOOL_KEYS and 'Toyota stop-and-go' in confirmation_question(request)
    assert owner.apply(request) and params.get_bool('SNGHack')
    assert not owner.apply(request)
    ci = CarInterface(cp)
    prefs = VehicleStartupPreferences.read(params, enabled=True)
    prefs.configure_controller(ci)
    assert ci.CC.sng_hack and cp.to_dict() == before
    params.put_bool('SNGHack', False, block=True)
    assert ci.CC.sng_hack
    VehicleStartupPreferences.read(params, enabled=True).configure_controller(ci)
    assert not ci.CC.sng_hack


@pytest.mark.parametrize('veto', ('stock', 'automatic', 'pedal', 'passive', 'dashcam', 'notcar', 'native'))
def test_donor_eligibility_and_native_identity_are_preserved(veto):
  cp = factory()
  assert eligible(cp)
  if veto == 'stock':
    cp.openpilotLongitudinalControl = False
  elif veto == 'pedal':
    cp.deprecated.enableGasInterceptor = True
  elif veto == 'native':
    cp.safetyConfigs[0].safetyModel = 'noOutput'
  else:
    setattr(cp, {'automatic': 'autoResumeSng', 'passive': 'passive', 'dashcam': 'dashcamOnly', 'notcar': 'notCar'}[veto], True)
  assert not eligible(cp)
  with OpenpilotPrefix():
    assert all(row.key != 'SNGHack' for row in rows(owner_for(Params(), cp)))


def test_onroad_and_safe_mode_cannot_enable_override():
  with OpenpilotPrefix():
    params, cp = Params(), factory()
    owner = owner_for(params, cp, parked=False)
    assert not next(row for row in rows(owner, False) if row.key == 'SNGHack').available
    params.put_bool('SNGHack', True, block=True)
    params.put_bool('SafeMode', True, block=True)
    ci = CarInterface(cp)
    VehicleStartupPreferences.read(params, enabled=True).configure_controller(ci)
    assert not ci.CC.sng_hack


@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('active', (False, True))
def test_stop_latch_override_requires_qualified_active_long(profile, active):
  cp, controller, command, state = setup(profile)
  controller.sng_hack = eligible(cp)
  state.out.gasPressed = False
  state.out.standstill = state.out.cruiseState.standstill = True
  state.out.vEgo = state.out.vEgoRaw = 0.
  state.pcm_acc_status = 8
  command.longActive = active
  command.actuators.accel = -.5
  controller.standstill_req = True
  frames = acc_frames(cp, controller, command, state)
  assert frames
  assert all(bool(frame['RELEASE_STANDSTILL']) == (active and eligible(cp)) for frame in frames)
  assert all(not frame['CANCEL_REQ'] for frame in frames)
