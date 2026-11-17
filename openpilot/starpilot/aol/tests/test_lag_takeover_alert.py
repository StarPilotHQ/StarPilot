"""Real event selection must notify loss of independent lateral authority."""
from contextlib import ExitStack
from dataclasses import replace
from unittest import mock

import pytest
from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


@pytest.mark.parametrize('pedal', ['none', 'brake', 'low_gas', 'drive_gas', 'door'])
@pytest.mark.parametrize('timer', [0, 120])
def test_lag_notifies_actual_lat_withdrawal_without_changing_timer(pedal, timer):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = sdgm_params()
    assert cp.safetyConfigs[0].safetyParam == 0x5007
    sd, _, drive = case._authority_callers(False, cp=cp, genuine_events=True)
    axis, _, command = drive(0, native_request_long=False)
    assert axis.lateralActive and axis.nativeAcknowledged and command.latActive
    sd.state_machine.soft_disable_timer = timer
    cs = fixture.car_state(brake=pedal == 'brake')
    cs.doorOpen = pedal == 'door'
    cs.gearShifter = car.CarState.GearShifter.low if pedal == 'low_gas' else car.CarState.GearShifter.drive
    with mock.patch.object(sd.rk.avg_dt, 'get_average', return_value=.02):
      axis, state, command = drive(1, observed_state=cs, gas=pedal.endswith('gas'), native_request_long=False)
      assert not axis.lateralActive and not axis.longitudinalActive
      assert not command.latActive and not command.longActive and not state.enabled
      assert state.alertType == 'selfdrivedLagging/softDisable'
      assert state.alertText1 == 'TAKE CONTROL IMMEDIATELY'
      assert state.alertText2 == 'System Lagging'
      assert str(state.alertSound) == 'warningImmediate'
      assert sd.state_machine.soft_disable_timer == max(0, timer - 1)
      drive(2, native_request_long=False)
      assert not sd.aol_lag_takeover_alert
    axis, _, command = drive(3, allowed=False, native_request_lat=False, native_request_long=False)
    assert not axis.lateralActive and not command.latActive


@pytest.mark.parametrize('bad', ['never_active', 'unarmed', 'native_invalid', 'native_denied', 'pause',
                                'cancel', 'reverse', 'temporary_eps', 'main_off', 'brake_pause'])
def test_no_lag_takeover_without_counterfactual_authority(bad):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, _, drive = case._authority_callers(False, cp=sdgm_params(), genuine_events=True)
    drive(0, native_request_long=False, latched=bad != 'never_active')
    kwargs = {'unarmed': {'latched': False}, 'native_invalid': {'native_valid': False},
              'native_denied': {'allowed': False}, 'pause': {'pause': True},
              'never_active': {'latched': False}}.get(bad, {})
    cs = fixture.car_state(brake=bad == 'brake_pause')
    cs.cruiseState.available = bad != 'main_off'
    if bad == 'cancel':
      cs.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.cancel, pressed=True)]
    if bad == 'reverse':
      cs.gearShifter = car.CarState.GearShifter.reverse
    cs.steerFaultTemporary = bad == 'temporary_eps'
    if bad == 'brake_pause':
      from openpilot.starpilot.aol.intent import read_settings
      sd.aol_settings = replace(read_settings(sd.params), pause_brake_mps=100.)
    source = messaging.new_message('carState', valid=True, logMonoTime=10_010_000_000)
    source.carState = cs
    with mock.patch.object(sd.rk.avg_dt, 'get_average', return_value=.02):
      _, _, command = drive(1, native_request_long=False, car_state_message=source, **kwargs)
    assert not sd.aol_lag_takeover_alert and not command.latActive and not command.longActive


@pytest.mark.parametrize('case_name', ['ordinary_lag', 'ordinary_brake', 'immediate'])
def test_existing_ordinary_timer_and_immediate_priority(case_name):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, _, drive = case._authority_callers(case_name != 'immediate', cp=sdgm_params(), genuine_events=True)
    drive(0)
    with mock.patch.object(sd.rk.avg_dt, 'get_average', return_value=.02):
      _, state, _ = drive(1, brake=case_name == 'ordinary_brake', native_valid=case_name != 'immediate')
    assert not sd.aol_lag_takeover_alert
    if case_name == 'ordinary_lag':
      assert str(state.state) == 'softDisabling' and sd.state_machine.soft_disable_timer == 300
      assert str(state.alertSound) == 'warningSoft'
    elif case_name == 'ordinary_brake':
      assert state.alertType == 'pedalPressed/userDisable' and not state.enabled
    else:
      assert state.alertType == 'controlsMismatch/immediateDisable'
