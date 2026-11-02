"""Expired MAIN intent cannot imply previously active steering."""
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import pytest

from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
from opendbc.car.gm.values import CAR
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE


def configuration(name):
  if name == 'ascm':
    return ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=True, sascm=True, radar=True)
  if name == 'sdgm':
    return sdgm_params(alpha=True, brake_c9=True)
  return params(getattr(CAR, name), pedal=True, camera=True)


CONFIGS = ['ascm', 'sdgm', 'CHEVROLET_BOLT_CC_2017', 'CHEVROLET_BOLT_CC_2018_2021',
           'CHEVROLET_BOLT_CC_2022_2023', 'CHEVROLET_BOLT_ACC_2022_2023_PEDAL']


@pytest.mark.parametrize('name', CONFIGS)
@pytest.mark.parametrize('fault', ['none', 'prior_active', 'enabled', 'native_missing', 'native_granted',
                                 'can_invalid', 'eps', 'relay', 'authority_lost', 'intent_expired'])
def test_initialized_idle_main_expiry_keeps_commands_off_and_real_faults(name, fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, controls, step = case._authority_callers(False, cp=configuration(name), genuine_events=True)
    cs = fixture.car_state(brake=True)
    cs.vEgo = cs.vEgoRaw = -1.9
    cs.gearShifter = fixture.car.CarState.GearShifter.low
    axis, _, command = step(0, observed_state=cs, steering=True, native_request_lat=False,
                            native_request_long=False, allowed=False, long_allowed=False)
    assert sd.initialized and not getattr(sd, 'aol_ever_active', False)
    assert not axis.lateralActive and not axis.longitudinalActive
    assert not command.latActive and not command.longActive
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    if fault == 'prior_active':
      sd.aol_ever_active = True
    elif fault == 'enabled':
      sd.enabled = sd.active = True
    elif fault == 'can_invalid':
      cs.canValid = False
    elif fault == 'eps':
      cs.steerFaultPermanent = True
    elif fault == 'relay':
      sd.events.add(log.OnroadEvent.EventName.relayMalfunction)
    elif fault == 'authority_lost':
      sd.aol_authority_lost = True
    now = BASE + (201_000_000 if fault == 'intent_expired' else 40_000_000)
    native = sd.aol_decision_native
    if fault == 'native_missing':
      native = None
    elif fault == 'native_granted':
      native = replace(native, requestedLateral=True, lateralAllowed=True)
    with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now), \
         patch('openpilot.starpilot.aol.transport_pause.current_native', return_value=native):
      sd._expire_axis_source(cs, now)
    assert sd.aol_car_state_log_ns == 0 and not sd.conditional_car_state_valid
    assert not sd.aol_axis_decision.lateral_active and not sd.aol_axis_decision.longitudinal_active
    assert not sd.enabled and not sd.active
    mismatch = log.OnroadEvent.EventName.controlsMismatch in sd.events.names
    assert mismatch == (fault != 'none')
    assert sd.aol_authority_lost == (fault != 'none')
    if fault == 'none':
      assert sd.aol_fault_reason == 'availabilityPause'
      assert sd.aol_publication_retained_intent.allowedLatch
      # Only a new original input plus the normal native handshake can engage.
      recovered, _, command = step(5, observed_state=cs, steering=True, native_request_lat=False,
                                   native_request_long=False, allowed=False, long_allowed=False)
      assert not recovered.lateralActive and not recovered.longitudinalActive
      assert not command.latActive and not command.longActive


@pytest.mark.parametrize('name', CONFIGS)
def test_idle_expiry_during_publication_never_sounds_takeover(name):
  from openpilot.starpilot.aol.tests.test_input_deadline_continuity import intent
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, _, step = case._authority_callers(False, cp=configuration(name), genuine_events=True)
    cs = fixture.car_state(brake=True)
    cs.vEgo = cs.vEgoRaw = -1.9
    cs.gearShifter = fixture.car.CarState.GearShifter.low
    now = [BASE + 29_000_000]
    def finish_environment(_cs):
      now[0] = BASE + 40_000_000
    with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', side_effect=lambda: now[0]), \
         patch.object(sd, 'update_conditional_mode', side_effect=finish_environment):
      axis, state, command = step(2, observed_state=cs, steering=True, state_stamp=BASE,
        intent_message=intent(BASE, 3), native_request_lat=False, native_request_long=False,
        allowed=False, long_allowed=False)
    assert sd.initialized and not getattr(sd, 'aol_ever_active', False)
    assert not axis.lateralActive and not axis.longitudinalActive
    assert not command.latActive and not command.longActive and not state.enabled
    assert not sd.aol_authority_lost
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    assert str(state.alertType) != 'controlsMismatch/immediateDisable'
