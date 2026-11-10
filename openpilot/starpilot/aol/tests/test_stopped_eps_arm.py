from contextlib import ExitStack

import pytest

from opendbc.car import structs
from opendbc.car.hyundai.values import CAR
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_classic_scc_profiles import params


@pytest.mark.parametrize('identity', (CAR.GENESIS_G70, CAR.HYUNDAI_SONATA, CAR.HYUNDAI_SONATA_HYBRID))
@pytest.mark.parametrize('button', (structs.CarState.ButtonEvent.Type.mainCruise, structs.CarState.ButtonEvent.Type.lkas))
def test_fresh_stopped_eps_press_retains_intent_until_real_permission(identity, button):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    Params().put('MainCruiseButtonControl', 9, block=True)
    Params().put('LKASButtonControl', 9, block=True)
    sd, _, drive, card, receive = case._ioniq_availability_callers(
      full_op=False, initially_armed=False, cp=params(identity, 0x391), cold_start=True)
    def cycle(tick, *, fault=True, event=None, requested=False, allowed=False, moving=False, cruise=False):
      state = fixture.car_state(brake=fault)
      state.vEgo = state.vEgoRaw = 8. if moving else 0.
      state.standstill = not moving
      state.steerFaultTemporary = fault
      state.cruiseState.enabled = cruise
      if event is not None:
        state.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=event)]
      axis, _, command = drive(tick, observed_state=state, native_request_lat=requested,
        native_request_long=False, panda_controls_allowed=cruise, allowed=allowed)
      receive(10_000_000_000 + tick * 10_000_000,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=state)
      return axis, command
    cycle(0, fault=False)
    assert not card.aol_card_intent.allowed_latch
    cycle(1, event=True)
    assert card.aol_card_intent.allowed_latch
    for tick in range(2, 205):
      axis, command = cycle(tick, event=False if tick == 2 else None)
      assert card.aol_card_intent.allowed_latch, 'fresh physical EPS-wait intent was erased'
      assert not axis.lateralActive and not command.latActive and not command.longActive
    for tick in (205, 206):
      axis, command = cycle(tick, fault=False)
      assert card.aol_card_intent.allowed_latch
      assert not axis.lateralActive and not command.latActive
    axis, command = cycle(207, fault=False, requested=True, allowed=True, moving=True)
    assert axis.lateralActive and command.latActive
    assert card.aol_card_intent.allowed_latch
    axis, command = cycle(208, fault=False, requested=True, allowed=True, moving=True, cruise=True)
    assert sd.enabled and axis.lateralActive and command.latActive
    assert card.aol_card_intent.allowed_latch
    cycle(209, fault=False, event=True, requested=True, allowed=True, moving=True)
    assert not card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('failure', ('can_invalid', 'can_timeout', 'permanent_eps', 'acc_fault',
                                    'rx_checks', 'relay_fault', 'native_invalid', 'critical_event'))
def test_pending_stopped_eps_arm_still_clears_for_real_faults(failure):
  from openpilot.cereal import log
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    Params().put('MainCruiseButtonControl', 9, block=True)
    sd, _, drive, card, receive = case._ioniq_availability_callers(
      full_op=False, initially_armed=False, cp=params(CAR.GENESIS_G70, 0x391), cold_start=True)
    for tick in range(4):
      state = fixture.car_state(brake=tick > 0)
      state.vEgo = state.vEgoRaw = 0.
      state.standstill = True
      state.steerFaultTemporary = tick > 0
      if tick in (1, 2):
        state.buttonEvents = [structs.CarState.ButtonEvent(
          type=structs.CarState.ButtonEvent.Type.mainCruise, pressed=tick == 1)]
      options = {}
      if tick == 3:
        if failure == 'can_invalid':
          state.canValid = False
        elif failure == 'can_timeout':
          state.canTimeout = True
        elif failure == 'permanent_eps':
          state.steerFaultPermanent = True
        elif failure == 'acc_fault':
          state.accFaulted = True
        elif failure == 'rx_checks':
          options['panda_fault'] = True
        elif failure == 'relay_fault':
          options['relay_fault'] = True
        elif failure == 'native_invalid':
          options['native_valid'] = False
        else:
          options['critical_event'] = log.OnroadEvent.EventName.controlsMismatch
      axis, _, command = drive(tick, observed_state=state, native_request_lat=False,
        native_request_long=False, allowed=False, **options)
      receive(10_000_000_000 + tick * 10_000_000,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=state)
      assert not axis.lateralActive and not command.latActive and not command.longActive
      if tick in (1, 2):
        assert card.aol_card_intent.allowed_latch
    assert not card.aol_card_intent.allowed_latch
