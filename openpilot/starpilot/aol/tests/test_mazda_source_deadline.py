"""Recorded Mazda source cutoff; real SD/Controls, synthetic clocks and transport."""
from contextlib import ExitStack
from dataclasses import replace
from unittest import mock

import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE, intent
from openpilot.starpilot.aol.wire import decode_intent, encode_intent


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
@pytest.mark.parametrize('condition', ('healthy', 'deadline', 'native_denied', 'cancel', 'brake', 'main_off',
                                      'eps_fault', 'invalid_can', 'producer', 'pause', 'repeated'))
def test_mazda_source_cutoff_preserves_only_original_command_lease(identity, condition):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
    sd, controls, drive = case._authority_callers(True, cp=cp, genuine_events=True)
    state = fixture.car_state()
    state.cruiseState.available = state.cruiseState.enabled = True
    drive(0, observed_state=state, native_request_long=False, long_allowed=False, panda_controls_allowed=True)
    old, _, command = drive(2, state_stamp=BASE, offset_ns=4_237_448,
      intent_message=intent(BASE, 3), observed_state=state, native_request_long=False, long_allowed=False, panda_controls_allowed=True)
    assert old.lateralActive and command.latActive and not command.longActive
    original = sd.pm.messages['aolAxisState'].as_builder().to_bytes()
    now = BASE + 30_453_437
    sd.aol_step_input_proof = sd.aol_published_input_proof
    sd.aol_step_deferral_checked = False
    sd.aol_step_intent_drain_count = 0
    sd.aol_step_intent_drain_ok = True
    if condition == 'deadline':
      now = old.validUntilMonoTime
    elif condition == 'native_denied':
      sd.sm.valid['aolSafetyWire'] = False
    elif condition == 'cancel':
      sd.events.add(log.OnroadEvent.EventName.buttonCancel)
    elif condition == 'brake':
      state.brakePressed = True
    elif condition == 'main_off':
      state.cruiseState.available = False
      state.cruiseState.enabled = False
      sd.events.add(log.OnroadEvent.EventName.wrongCarMode)
    elif condition == 'eps_fault':
      state.steerFaultPermanent = True
    elif condition == 'invalid_can':
      state.canValid = False
    elif condition in ('producer', 'pause'):
      message = intent(BASE + 29_000_000, 4)
      value = decode_intent(message.aolIntentWire)
      message.aolIntentWire = encode_intent(replace(value, **(
        {'producerSessionId': 'different-card'} if condition == 'producer' else {'pauseLateral': True})))
      sd.aol_intent_companion._observe(message.logMonoTime, message.valid, message.aolIntentWire)
    elif condition == 'repeated':
      sd.aol_step_deferral_checked = True
    initial = (sd.state_machine.state, sd.state_machine.soft_disable_timer, sd.enabled, sd.active)
    sd.car_state_samples = ((state.as_reader(), True),)
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now):
      deferred = sd.defer_expired_axis_publication(state, initial, now_ns=now)
    assert deferred == (condition == 'healthy')
    assert sd.pm.messages['aolAxisState'].as_builder().to_bytes() == original
    assert old.sourceCarStateMonoTime == BASE
    assert old.validUntilMonoTime == BASE + 34_237_448
    # Every consumer still withdraws at the original absolute deadline.
    with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=old.validUntilMonoTime + 1):
      expired, _ = controls.state_control()
    assert not expired.latActive and not expired.longActive
    if condition == 'healthy':
      fresh = BASE + 27_821_823
      axis, _, command = drive(6, state_stamp=fresh, offset_ns=-7_000_000,
        intent_message=intent(fresh, 4), observed_state=state, native_request_long=False, long_allowed=False, panda_controls_allowed=True)
      assert axis.sourceCarStateMonoTime == fresh and axis.lateralActive and command.latActive
      assert not command.longActive
      assert log.OnroadEvent.EventName.commIssue not in sd.events.names
      assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_mazda_actual_step_defers_warning_then_consumes_fresh_successor(identity):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
    sd, controls, drive = case._authority_callers(True, cp=cp, genuine_events=True)
    state = fixture.car_state()
    state.cruiseState.available = state.cruiseState.enabled = True
    options = {'observed_state': state, 'native_request_long': False,
               'long_allowed': False, 'panda_controls_allowed': True}
    drive(0, **options)
    old, _, command = drive(2, state_stamp=BASE, offset_ns=4_237_448,
      intent_message=intent(BASE, 3), **options)
    assert old.lateralActive and command.latActive
    original = sd.pm.messages['aolAxisState'].as_builder().to_bytes()
    now = [BASE + 29_900_000]
    actual_events = sd.update_events

    def events(cs):
      result = actual_events(cs)
      now[0] = BASE + 30_453_437
      return result

    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', side_effect=lambda: now[0]), \
         mock.patch.object(sd, 'update_events', side_effect=events):
      axis, _, command = drive(5, timeout=True, offset_ns=-100_000,
        intent_message=intent(BASE, 3), **options)
    assert sd.aol_axis_publication_deferred
    assert sd.pm.messages['aolAxisState'].as_builder().to_bytes() == original
    assert command.latActive and not command.longActive
    assert log.OnroadEvent.EventName.commIssue not in sd.events.names
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=old.validUntilMonoTime + 1):
      expired, _ = controls.state_control()
    assert not expired.latActive and not expired.longActive
    fresh = BASE + 27_821_823
    axis, _, command = drive(6, state_stamp=fresh, offset_ns=-7_000_000,
      intent_message=intent(fresh, 4), **options)
    assert axis.sourceCarStateMonoTime == fresh and axis.lateralActive and command.latActive
    assert not command.longActive
    assert log.OnroadEvent.EventName.commIssue not in sd.events.names
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
