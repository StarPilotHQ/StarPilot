"""RAV4 route1d: held brake plus source timeout must withdraw output, not intent."""
from contextlib import ExitStack

import pytest
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE, intent
from openpilot.starpilot.aol.tests.test_toyota_parking_brake_deadline import refresh_saved_settings


def rolling(brake=True, gas=False, gear='drive'):
  state = fixture.car_state(brake=brake)
  state.gasPressed = gas
  state.gearShifter = gear
  state.vEgo = state.vEgoRaw = 17.76392555237 if gas else .63179683685
  state.standstill = False
  state.cruiseState.available = True
  state.cruiseState.enabled = False
  return state


@pytest.mark.parametrize('gear', ('drive', 'sport', 'brake'))
@pytest.mark.parametrize('pedal', ('none', 'brake', 'gas'))
@pytest.mark.parametrize('bsm', (False, True))
def test_rav4_held_brake_source_gap_preserves_armed_intent(pedal, bsm, gear):
  brake, gas = pedal == 'brake', pedal == 'gas'
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    fingerprint = {0: {0x3F6: 8} if bsm else {}, 1: {}, 2: {}}
    cp = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2, fingerprint, [], False, False, False)
    assert int(cp.flags) == (1050 if bsm else 1048) and cp.safetyConfigs[0].safetyParam == 73
    sd, controls, drive = case._authority_callers(False, cp=cp, genuine_events=True)
    refresh_saved_settings(sd)
    for tick in (0, 1):
      axis, _, command = drive(tick, observed_state=rolling(brake, gas, gear), brake=brake, gas=gas, native_request_long=False)
      assert axis.lateralActive and command.latActive and not command.longActive
    prior, _, command = drive(2, offset_ns=5_209_427, state_stamp=BASE,
      intent_message=intent(BASE, 3), observed_state=rolling(brake, gas, gear), brake=brake, gas=gas, native_request_long=False)
    assert prior.lateralActive and command.latActive
    # Recorded gap: source32.074ms old, prior command still3.135ms live.
    axis, host, command = drive(5, offset_ns=2_073_802, timeout=True,
      state_stamp=BASE, intent_message=intent(BASE, 3), brake=brake, gas=gas, native_request_long=False)
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'RAV4_HELD_BRAKE_SOURCE_GAP'
    assert str(axis.faultReason) == 'availabilityPause' and axis.sourceCarStateMonoTime == 0
    assert not axis.lateralActive and not command.latActive and not command.longActive and not host.enabled
    assert axis.desiredLateral and axis.nativeAcknowledged and not sd.aol_authority_lost
    source = BASE + 28_454_011
    axis, host, command = drive(6, state_stamp=source, intent_message=intent(source, 4),
      observed_state=rolling(brake, gas, gear), brake=brake, gas=gas, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive and not host.enabled
    assert not sd.aol_authority_lost


@pytest.mark.parametrize('gas', (False, True))
def test_rav4_pedal_source_pause_reaches_actual_card_without_disarming(gas):
  brake = not gas
  from openpilot.starpilot.aol.tests.stock_lateral_test_helpers import callers
  with ExitStack() as cleanup:
    sd, _, drive, card, receive = callers(cleanup, 'toyota', True)
    refresh_saved_settings(sd)
    prior, _, command = drive(2, offset_ns=5_209_427, state_stamp=BASE,
      intent_message=intent(BASE, 3), observed_state=rolling(brake, gas), brake=brake, gas=gas, native_request_long=False)
    assert prior.lateralActive and command.latActive
    assert receive(BASE + 5_209_427, state=rolling(brake, gas))
    axis, host, command = drive(5, offset_ns=2_073_802, timeout=True,
      state_stamp=BASE, intent_message=intent(BASE, 3), brake=brake, gas=gas, native_request_long=False)
    assert str(axis.faultReason) == 'availabilityPause' and axis.sourceCarStateMonoTime == 0
    assert not command.latActive and not command.longActive and not host.enabled
    assert receive(BASE + 32_073_802, state=rolling(brake, gas)), 'RAV4_CARD_BRAKE_SOURCE_PAUSE'
    assert card.aol_card_intent.allowed_latch
    source = BASE + 28_454_011
    axis, host, command = drive(6, state_stamp=source, intent_message=intent(source, 4),
      observed_state=rolling(brake, gas), brake=brake, gas=gas, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive and not host.enabled
    assert receive(BASE + 40_000_000, state=rolling(brake, gas))


@pytest.mark.parametrize('fault', ('native_denied', 'native_invalid', 'rx_fault', 'relay_fault',
                                  'latch_withdrawn', 'missing_anchor', 'pause_setting', 'dm_lockout'))
def test_rav4_brake_source_gap_does_not_waive_restrictions(fault):
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2, {0: {}, 1: {}, 2: {}}, [], False, False, False)
    sd, _, drive = case._authority_callers(False, cp=cp, genuine_events=True)
    refresh_saved_settings(sd)
    for tick in (0, 1):
      axis, _, command = drive(tick, observed_state=rolling(), brake=True, native_request_long=False)
      assert axis.lateralActive and command.latActive
    prior, _, command = drive(2, offset_ns=5_209_427, state_stamp=BASE,
      intent_message=intent(BASE, 3), observed_state=rolling(), brake=True, native_request_long=False)
    assert prior.lateralActive and command.latActive
    if fault == 'missing_anchor':
      sd.aol_ordinary_source_anchor = None
    elif fault == 'pause_setting':
      sd.params.put('PauseAOLOnBrake', 30., block=True)
      refresh_saved_settings(sd)
    elif fault == 'dm_lockout':
      sd.aol_dm_lateral_inhibit = True
    axis, host, command = drive(5, offset_ns=2_073_802, timeout=True,
      state_stamp=BASE, intent_message=intent(BASE, 3, latched=fault != 'latch_withdrawn'),
      brake=True, native_request_long=False, allowed=fault != 'native_denied',
      native_valid=fault != 'native_invalid', panda_fault=fault == 'rx_fault',
      relay_fault=fault == 'relay_fault')
    assert not axis.lateralActive and not command.latActive
    assert not axis.longitudinalActive and not command.longActive and not host.enabled
    if fault == 'latch_withdrawn':
      assert not axis.desiredLateral and sd.aol_last_intent is None
    else:
      assert str(axis.faultReason) != 'availabilityPause'


@pytest.mark.parametrize('steering', (False, True))
def test_corolla_gas_override_source_gap_preserves_lateral_intent(steering):
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = CarInterface.get_params(CAR.TOYOTA_COROLLA_TSS2, {0: {}, 1: {}, 2: {}}, [], False, False, False)
    sd, _, drive = case._authority_callers(True, cp=cp, genuine_events=True)
    refresh_saved_settings(sd)
    def state():
      cs = fixture.car_state()
      cs.vEgo = cs.vEgoRaw = 30.
      cs.cruiseState.enabled = True
      return cs
    for tick in (0, 1, 2):
      axis, _, command = drive(tick, observed_state=state(), gas=True, steering=steering,
        offset_ns=5_328_073 if tick == 2 else 0, state_stamp=BASE if tick == 2 else None,
        intent_message=intent(BASE, 3) if tick == 2 else None,
        long_allowed=False, native_request_long=True)
      assert axis.lateralActive and command.latActive and not axis.longitudinalActive and not command.longActive
    # Recorded source31.321875ms old, previous output still4.006198ms live.
    axis, host, command = drive(5, offset_ns=1_321_875, timeout=True,
      state_stamp=BASE, intent_message=intent(BASE, 3), gas=True, steering=steering,
      long_allowed=False, native_request_long=True)
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'COROLLA_GAS_SOURCE_GAP'
    assert str(axis.faultReason) == 'availabilityPause' and axis.sourceCarStateMonoTime == 0
    assert axis.desiredLateral and not axis.desiredLongitudinal and not sd.aol_authority_lost
    assert not command.latActive and not command.longActive and host.enabled
    assert str(host.state) == 'softDisabling' and log.OnroadEvent.EventName.commIssue in sd.events.names
    source = BASE + 27_305_261
    axis, _, command = drive(6, state_stamp=source, intent_message=intent(source, 4), observed_state=state(),
      gas=True, steering=steering, long_allowed=False, native_request_long=True)
    assert axis.lateralActive and command.latActive and not command.longActive and not sd.aol_authority_lost


def test_corolla_gas_source_pause_reaches_actual_card_without_latch_loss():
  from openpilot.starpilot.aol.tests.stock_lateral_test_helpers import callers
  with ExitStack() as cleanup:
    sd, _, drive, card, receive = callers(cleanup, 'toyota', False)
    refresh_saved_settings(sd)
    def state():
      cs = fixture.car_state()
      cs.vEgo = cs.vEgoRaw = 30.
      cs.cruiseState.enabled = True
      cs.gasPressed = True
      cs.steeringPressed = True
      return cs
    axis, _, command = drive(2, observed_state=state(), gas=True, steering=True,
      offset_ns=5_328_073, state_stamp=BASE, intent_message=intent(BASE, 3),
      native_request_long=True, long_allowed=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    assert receive(BASE + 5_328_073, state=state()) and card.aol_card_intent.allowed_latch
    axis, _, command = drive(5, offset_ns=1_321_875, timeout=True, state_stamp=BASE,
      intent_message=intent(BASE, 3), gas=True, steering=True, native_request_long=True, long_allowed=False)
    assert str(axis.faultReason) == 'availabilityPause'
    assert not command.latActive and not command.longActive
    assert receive(BASE + 31_321_875, state=state()), 'COROLLA_CARD_GAS_SOURCE_PAUSE'
    assert card.aol_card_intent.allowed_latch
    source = BASE + 27_305_261
    axis, _, command = drive(6, state_stamp=source, intent_message=intent(source, 4),
      observed_state=state(), gas=True, steering=True, native_request_long=True, long_allowed=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    assert receive(BASE + 40_000_000, state=state()) and card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('fault', ('native_denied', 'native_invalid', 'rx_fault', 'relay_fault',
                                  'latch_withdrawn', 'missing_anchor', 'epoch_changed', 'dm_lockout'))
def test_corolla_gas_source_gap_preserves_every_native_and_intent_veto(fault):
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = CarInterface.get_params(CAR.TOYOTA_COROLLA_TSS2, {0: {}, 1: {}, 2: {}}, [], False, False, False)
    sd, _, drive = case._authority_callers(True, cp=cp, genuine_events=True)
    refresh_saved_settings(sd)
    cs = fixture.car_state()
    cs.vEgo = cs.vEgoRaw = 30.
    cs.cruiseState.enabled = True
    for tick in (0, 1, 2):
      axis, _, command = drive(tick, observed_state=cs, gas=True, steering=True,
        offset_ns=5_328_073 if tick == 2 else 0, state_stamp=BASE if tick == 2 else None,
        intent_message=intent(BASE, 3) if tick == 2 else None,
        long_allowed=False, native_request_long=True)
      assert axis.lateralActive and command.latActive and not command.longActive
    if fault == 'missing_anchor':
      sd.aol_ordinary_source_anchor = None
    elif fault == 'epoch_changed':
      sd.aol_intent_companion.integrity_epoch += 1
    elif fault == 'dm_lockout':
      sd.aol_dm_lateral_inhibit = True
    axis, _, command = drive(5, offset_ns=1_321_875, timeout=True,
      state_stamp=BASE, intent_message=intent(BASE, 3, latched=fault != 'latch_withdrawn'),
      gas=True, steering=True, long_allowed=False, native_request_long=True,
      allowed=fault != 'native_denied', native_valid=fault != 'native_invalid',
      panda_fault=fault == 'rx_fault', relay_fault=fault == 'relay_fault')
    assert not axis.lateralActive and not axis.longitudinalActive
    assert not command.latActive and not command.longActive
    assert str(axis.faultReason) != 'availabilityPause'
