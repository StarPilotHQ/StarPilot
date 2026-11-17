from contextlib import ExitStack

import pytest

from opendbc.car.hyundai.values import CAR

from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


@pytest.mark.parametrize('profile', [False, CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9], ids=['ioniq6', 'ioniq5pe', 'ev9'])
@pytest.mark.parametrize('order', ['event_first', 'axis_first', 'together'])
def test_repeated_communication_warning_keeps_existing_latch(order, profile):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, step, card, receive = case._ioniq_availability_callers(full_op=not profile, stock_angle=profile)
    for tick in range(2, 65):
      axis, _, command = step(tick, car_output_valid=False, native_request_long=False)
      assert not axis.lateralActive and not command.latActive and not command.longActive
      assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'])
    assert card.aol_transport_feedback.ordinary_warning_retained
    axis, _, command = step(65, car_output_valid=False, native_request_long=False)
    proof = sd.pm.messages['aolAxisState'].as_builder()
    event = sd.pm.messages['onroadEvents'].as_builder()
    now = 10_650_000_000
    event.logMonoTime = now
    proof.aolAxisState.faultEventMonoTime = now
    frames = [{'event': event}, {'axis': proof}] if order == 'event_first' else (
      [{'axis': proof}, {'event': event}] if order == 'axis_first' else [{'axis': proof, 'event': event}])
    for index, frame in enumerate(frames):
      assert receive(now + index * 1_000_000, **frame), 'REPEATED_SOFT_WARNING_CLEARED_LATCH'
    assert not card.aol_transport_feedback.failed
    assert not command.latActive and not command.longActive and not sd.enabled
    recovered, _, command = step(66, native_request_long=False)
    assert recovered.lateralActive and command.latActive
    assert not command.longActive and not sd.enabled
    assert receive(10_660_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])


@pytest.mark.parametrize('order', ['axis_first', 'event_first'])
@pytest.mark.parametrize('repeat', [False, True])
def test_repeated_warning_missing_partner_keeps_original_deadline(order, repeat):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, step, card, receive = case._ioniq_availability_callers()
    for tick in (2, 3):
      step(tick, car_output_valid=False, native_request_long=False)
      assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'])
    step(4, car_output_valid=False, native_request_long=False)
    now = 10_040_000_000
    proof = sd.pm.messages['aolAxisState'].as_builder()
    event = sd.pm.messages['onroadEvents'].as_builder()
    proof.aolAxisState.faultEventMonoTime = event.logMonoTime = now
    half = {'axis': proof} if order == 'axis_first' else {'event': event}
    assert receive(now, **half), 'REPEATED_SOFT_WARNING_CLEARED_LATCH'
    original_deadline = card.aol_transport_feedback.pending_event
    assert original_deadline == now
    def fresh_half(stamp):
      updated = messaging.new_message('aolAxisState', valid=True, logMonoTime=stamp)
      updated.aolAxisState = card.sm['aolAxisState']
      updated.aolAxisState.sequence += 1
      updated.aolAxisState.observedMonoTime = updated.aolAxisState.sourceCarStateMonoTime = stamp
      updated.aolAxisState.validUntilMonoTime = stamp + 30_000_000
      return {**half, 'axis': updated}

    if repeat:
      assert receive(now + 10_000_000, **fresh_half(now + 10_000_000))
      assert card.aol_transport_feedback.pending_event == original_deadline
    expired = now + 30_000_001
    assert not receive(expired, **(fresh_half(expired) if repeat else half))
    assert card.aol_transport_feedback.failed and not card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('fault', ['producer', 'epoch', 'session', 'fault_session', 'stale_source',
                                   'future_source', 'invalid_native', 'can_invalid', 'permanent_eps', 'critical_event'])
def test_repeated_warning_join_preserves_denials(fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, step, card, receive = case._ioniq_availability_callers()
    for tick in (2, 3):
      step(tick, car_output_valid=False, native_request_long=False)
      assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'])
    step(4, car_output_valid=False, native_request_long=False)
    now = 10_040_000_000
    proof = sd.pm.messages['aolAxisState'].as_builder()
    proof.aolAxisState.faultEventMonoTime = now
    observed = fixture.car_state()
    frame = {'axis': proof}
    if fault == 'producer':
      proof.aolAxisState.intentProducerSessionId = 'other-card'
    elif fault == 'epoch':
      proof.aolAxisState.intentIntegrityEpoch += 1
    elif fault == 'session':
      proof.aolAxisState.sessionId = 'other-session'
    elif fault == 'fault_session':
      proof.aolAxisState.faultSessionId = 'other-session'
    elif fault == 'stale_source':
      proof.aolAxisState.sourceCarStateMonoTime = now - 30_000_001
    elif fault == 'future_source':
      proof.aolAxisState.sourceCarStateMonoTime = now + 1
    elif fault == 'invalid_native':
      sd.sm.valid['aolSafetyWire'] = False
    elif fault == 'can_invalid':
      observed.canValid = False
    elif fault == 'permanent_eps':
      observed.steerFaultPermanent = True
    else:
      event = messaging.new_message('onroadEvents', 1, valid=True, logMonoTime=now)
      event.onroadEvents[0].name = 'canError'
      event.onroadEvents[0].immediateDisable = True
      frame['event'] = event
    assert not receive(now, state=observed, **frame)
    assert not card.aol_card_intent.allowed_latch
