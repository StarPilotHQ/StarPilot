from dataclasses import replace
import pytest
from opendbc.car import structs
from opendbc.car.tesla.screen_button import apply_screen_button
from opendbc.car.tesla.tests.test_screen_button import screen_params
from openpilot.starpilot.aol.intent import AolSettings, ButtonType
from openpilot.starpilot.car.tesla.screen_intent import TeslaScreenCardIntent


def intent(brake=False):
  cp = screen_params()
  apply_screen_button(cp, True, brake)
  settings = AolSettings(True, 0, 9, 0, (0, 0, 0), (0, 0, 0))
  return TeslaScreenCardIntent(cp, settings)


def state(*, available=True, enabled=False, press=None, brake=False):
  cs = structs.CarState(canValid=True, gearShifter=structs.CarState.GearShifter.drive, brakePressed=brake)
  cs.cruiseState.available = available
  cs.cruiseState.enabled = enabled
  if press is not None:
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=ButtonType.lkas, pressed=press)]
  return cs


def test_cruise_baseline_screen_toggle_and_normal_engagement_reset():
  owner = intent()
  owner.update(state().as_reader(), now_ns=1)
  assert owner.allowed_latch and owner.screen_override is None
  owner.update(state(press=True).as_reader(), now_ns=2)
  assert not owner.allowed_latch and owner.pause_lateral
  owner.update(state(press=False).as_reader(), now_ns=3)
  owner.update(state(press=True).as_reader(), now_ns=4)
  assert owner.allowed_latch and not owner.pause_lateral
  owner.update(state(press=True).as_reader(), now_ns=5)
  owner.update(state(enabled=True).as_reader(), now_ns=6)
  assert owner.allowed_latch and owner.screen_override is None


@pytest.mark.parametrize('field', ['doorOpen', 'steerFaultPermanent', 'steeringDisengage', 'accFaulted'])
def test_fault_cannot_be_overridden_by_screen(field):
  owner = intent()
  cs = state(press=True)
  setattr(cs, field, True)
  owner.update(cs.as_reader(), now_ns=2)
  assert not owner.allowed_latch


@pytest.mark.parametrize('brake_disengage', [False, True])
def test_brake_choice_frozen_and_live_settings_optout(brake_disengage):
  owner = intent(brake_disengage)
  owner.update(state().as_reader(), now_ns=1)
  owner.update(state(brake=True).as_reader(), now_ns=2)
  assert owner.allowed_latch is not brake_disengage
  owner.settings = replace(owner.settings, enabled=False)
  owner.update(state(press=True).as_reader(), now_ns=3)
  assert not owner.allowed_latch


def test_expired_native_permission_and_cancel_require_new_intent():
  owner = intent()
  owner.update(state(available=False).as_reader(), now_ns=1)
  owner.update(state(available=False, press=True).as_reader(), now_ns=2)
  assert owner.allowed_latch
  owner.update(state(available=False).as_reader(), now_ns=4, native_rejection_ns=3)
  assert not owner.allowed_latch
  cs = state(available=False, press=True)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=ButtonType.cancel, pressed=True)]
  owner.update(cs.as_reader(), now_ns=5)
  assert not owner.allowed_latch


def test_actual_startup_preferences_preserve_off_and_freeze_brake(tmp_path):
  from openpilot.common.params import Params
  from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
  from openpilot.starpilot.car.tesla.aol import policy_for
  params = Params(str(tmp_path / 'params'))
  params.put_bool('AlwaysOnLateral', True, block=True)
  params.put_bool('TeslaAOLScreenTap', False, block=True)
  cp = screen_params()
  saved = VehicleStartupPreferences.read(params, enabled=True)
  saved.prepare(cp)
  assert cp.safetyConfigs[0].safetyParam == 0
  assert policy_for(cp).settings_supported and not policy_for(cp).runtime_supported
  params.put_bool('TeslaAOLScreenTap', True, block=True)
  params.put_bool('TeslaAOLDisengageOnBrake', True, block=True)
  saved = VehicleStartupPreferences.read(params, enabled=True)
  cp = screen_params()
  saved.prepare(cp)
  assert cp.safetyConfigs[0].safetyParam == 1536
  params.put_bool('TeslaAOLDisengageOnBrake', False, block=True)
  saved.finalize(cp)
  assert cp.safetyConfigs[0].safetyParam == 1536
  params.put_bool('AlwaysOnLateral', False, block=True)
  refreshed = VehicleStartupPreferences.read(params, enabled=True)
  refreshed.prepare(cp)
  assert cp.safetyConfigs[0].safetyParam == 0
  assert not cp.flags & 16
  assert policy_for(cp).settings_supported and not policy_for(cp).runtime_supported


def test_unbuckled_screen_gesture_preserves_independent_lateral_intent():
  owner = intent()
  cs = state(available=False, press=False)
  cs.seatbeltUnlatched = True
  owner.update(cs.as_reader(), now_ns=1)
  cs.buttonEvents[0].pressed = True
  owner.update(cs.as_reader(), now_ns=2)
  assert owner.allowed_latch
  cs.buttonEvents = []
  owner.update(cs.as_reader(), now_ns=3)
  assert owner.allowed_latch
  cs.seatbeltUnlatched = False
  owner.update(cs.as_reader(), now_ns=4)
  assert owner.allowed_latch and not cs.cruiseState.enabled


@pytest.mark.parametrize('gear', ['reverse', 'neutral'])
def test_screen_existing_intent_survives_known_gear_pause_without_new_arm(gear):
  owner = intent()
  cs = state(available=False, press=False)
  owner.update(cs.as_reader(), now_ns=1)
  cs.buttonEvents[0].pressed = True
  owner.update(cs.as_reader(), now_ns=2)
  assert owner.allowed_latch
  cs.buttonEvents = []
  cs.gearShifter = gear
  owner.update(cs.as_reader(), now_ns=3, fault_active=False)
  assert owner.allowed_latch
  cs.gearShifter = 'drive'
  owner.update(cs.as_reader(), now_ns=4, fault_active=False)
  assert owner.allowed_latch
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=ButtonType.lkas, pressed=False)]
  owner.update(cs.as_reader(), now_ns=5)
  cs.buttonEvents[0].pressed = True
  owner.update(cs.as_reader(), now_ns=6)
  assert not owner.allowed_latch
  cs.gearShifter = gear
  owner.update(cs.as_reader(), now_ns=7, fault_active=False)
  cs.gearShifter = 'drive'
  owner.update(cs.as_reader(), now_ns=8, fault_active=False)
  assert not owner.allowed_latch


@pytest.mark.parametrize('withdrawal', ['screen_off', 'can_timeout', 'native_rejection', 'cancel', 'brake'])
def test_availability_cycle_cannot_undo_an_explicit_or_fault_off(withdrawal):
  owner = intent(brake=withdrawal == 'brake')
  owner.update(state(available=False).as_reader(), now_ns=10)
  owner.update(state(available=False, press=True).as_reader(), now_ns=20)
  owner.update(state(available=True, press=False).as_reader(), now_ns=30)
  assert owner.allowed_latch
  cs = state()
  kwargs = {}
  if withdrawal == 'screen_off':
    cs = state(press=True)
  elif withdrawal == 'can_timeout':
    cs.canTimeout = True
  elif withdrawal == 'native_rejection':
    kwargs['native_rejection_ns'] = 40
  elif withdrawal == 'cancel':
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=ButtonType.cancel, pressed=True)]
  else:
    cs.brakePressed = True
  owner.update(cs.as_reader(), now_ns=50, **kwargs)
  assert not owner.allowed_latch
  owner.update(state(press=False).as_reader(), now_ns=60)
  for index, available in enumerate((False, True, False, True)):
    owner.update(state(available=available).as_reader(), now_ns=70 + index)
    assert not owner.allowed_latch
  owner.update(state(press=True).as_reader(), now_ns=80)
  assert owner.allowed_latch


def test_availability_off_still_allows_a_new_ordinary_cruise_engagement():
  owner = intent()
  owner.update(state().as_reader(), now_ns=10)
  owner.update(state(press=True).as_reader(), now_ns=20)
  assert not owner.allowed_latch
  owner.update(state(available=False, press=False).as_reader(), now_ns=30)
  owner.update(state(available=True).as_reader(), now_ns=40)
  assert not owner.allowed_latch
  owner.update(state(enabled=True).as_reader(), now_ns=50)
  assert owner.allowed_latch and owner.screen_override is None
