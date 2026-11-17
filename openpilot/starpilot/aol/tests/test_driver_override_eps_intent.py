from contextlib import ExitStack

import pytest

from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


@pytest.mark.parametrize('full_op', [False, True])
@pytest.mark.parametrize('steering', [False, True])
def test_temporary_eps_during_driver_override_keeps_existing_latch(full_op, steering):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=full_op)
    temporary = fixture.car_state()
    temporary.steerFaultTemporary = True
    temporary.steeringAngleDeg = 100.
    for tick in range(2, 8):
      axis, _, command = drive(tick, observed_state=temporary, steering=steering)
      assert not command.latActive and not axis.lateralActive
      assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'], state=temporary), {
                       'marker': 'TEMP_OVERRIDE_CLEARED_AOL', 'axis': axis.to_dict(),
                       'reason': card.aol_card_intent.latch_clear_reason,
                       'events': sd.pm.messages['onroadEvents'].to_dict(),
                       'feedback': {k: getattr(card.aol_transport_feedback, k) for k in
                         ('failed', 'pending_event', 'lateral_baseline_ns', 'lateral_inventory', 'signature', 'temporary_eps_event_ns')}}
      assert card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('steering', [False, True])
def test_g70_stock_temporary_eps_keeps_existing_latch(steering):
  from opendbc.car.hyundai.values import CAR
  from opendbc.car.hyundai.classic_scc_aol import qualified
  from openpilot.starpilot.aol.tests.test_classic_scc_profiles import params

  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    from openpilot.common.params import Params
    Params().put('LKASButtonControl', 9, block=True)
    cp = params(CAR.GENESIS_G70, 0x391)
    assert qualified(cp)
    sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=False, cp=cp)
    temporary = fixture.car_state()
    temporary.steerFaultTemporary = True
    temporary.steeringAngleDeg = 100.
    for tick in range(2, 8):
      axis, _, command = drive(tick, observed_state=temporary, steering=steering)
      assert not command.latActive and not axis.lateralActive
      assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'], state=temporary), {
                       'marker': 'TEMP_OVERRIDE_CLEARED_AOL', 'axis': axis.to_dict(),
                       'reason': card.aol_card_intent.latch_clear_reason,
                       'events': sd.pm.messages['onroadEvents'].to_dict(),
                       'feedback': {k: getattr(card.aol_transport_feedback, k) for k in
                         ('failed', 'pending_event', 'lateral_baseline_ns', 'lateral_inventory', 'signature', 'temporary_eps_event_ns')}}
      assert card.aol_card_intent.allowed_latch
    recovered, _, command = drive(8)
    assert recovered.lateralActive and command.latActive
    assert not command.longActive and not sd.enabled
    assert receive(10_080_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])


@pytest.mark.parametrize('configuration', ['aol', 'combined', 'g70'])
@pytest.mark.parametrize('order', ['proof_first', 'event_first', 'together'])
def test_driver_override_eps_entry_warning_and_recovery_delivery_pairs(configuration, order):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    if configuration == 'g70':
      from openpilot.common.params import Params
      from openpilot.starpilot.aol.tests.test_classic_scc_profiles import params
      from opendbc.car.hyundai.values import CAR
      Params().put('LKASButtonControl', 9, block=True)
      cp = params(CAR.GENESIS_G70, 0x391)
      sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=False, cp=cp)
    else:
      sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=configuration == 'combined')
    temporary = fixture.car_state()
    temporary.steerFaultTemporary = True
    for tick, steering, fault in ((2, True, True), (3, False, True), (4, False, False)):
      temporary.steerFaultTemporary = fault
      axis, _, command = drive(tick, observed_state=temporary, steering=steering)
      assert bool(axis.lateralActive and command.latActive) == (not fault)
      proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
      frames = [{'axis': proof}, {'event': event}] if order == 'proof_first' else (
        [{'event': event}, {'axis': proof}] if order == 'event_first' else [{'axis': proof, 'event': event}])
      for index, frame in enumerate(frames):
        assert receive(10_000_000_000 + tick * 10_000_000 + index * 1_000_000, state=temporary,
                       **frame), ('TEMP_OVERRIDE_PAIR_CLEARED_AOL', tick, order)
      assert card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('fault', ['unexpected_long', 'invalid_native', 'permanent_eps', 'can_invalid', 'hard_event'])
def test_g70_override_eps_cannot_hide_other_faults_or_long_authority(fault):
  from openpilot.cereal import log
  from openpilot.common.params import Params
  from openpilot.starpilot.aol.tests.test_classic_scc_profiles import params
  from opendbc.car.hyundai.values import CAR

  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    Params().put('LKASButtonControl', 9, block=True)
    sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=False, cp=params(CAR.GENESIS_G70, 0x391))
    temporary = fixture.car_state()
    temporary.steerFaultTemporary = True
    temporary.steerFaultPermanent = fault == 'permanent_eps'
    temporary.canValid = fault != 'can_invalid'
    drive(2, observed_state=temporary, steering=True, native_valid=fault != 'invalid_native',
          native_request_long=fault == 'unexpected_long',
          critical_event=log.OnroadEvent.EventName.canError if fault == 'hard_event' else None)
    assert not receive(10_020_000_000, state=temporary, axis=sd.pm.messages['aolAxisState'],
                       event=sd.pm.messages['onroadEvents'])
    assert not card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('full_op', [False, True])
@pytest.mark.parametrize('order', ['proof_first', 'event_first'])
def test_override_eps_missing_pair_cannot_renew_original_deadline(full_op, order):
  from openpilot.cereal import messaging

  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=full_op)
    feedback = card.aol_transport_feedback
    original = (feedback.lateral_baseline_ns, feedback.lateral_source_ns)
    temporary = fixture.car_state()
    temporary.steerFaultTemporary = True
    drive(2, observed_state=temporary, steering=True)
    proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
    half = {'axis': proof} if order == 'proof_first' else {'event': event}
    assert receive(10_020_000_000, state=temporary, **half)
    original_pending = feedback.pending_event

    def fresh_half(stamp):
      newer = messaging.new_message('aolAxisState', valid=True, logMonoTime=stamp)
      newer.aolAxisState = card.sm['aolAxisState']
      newer.aolAxisState.sequence += 1
      newer.aolAxisState.observedMonoTime = newer.aolAxisState.sourceCarStateMonoTime = stamp
      newer.aolAxisState.validUntilMonoTime = stamp + 30_000_000
      return {**half, 'axis': newer}

    assert receive(10_030_000_000, state=temporary, **fresh_half(10_030_000_000))
    assert feedback.pending_event == original_pending
    assert (feedback.lateral_baseline_ns, feedback.lateral_source_ns) == original
    assert not receive(10_050_000_001, state=temporary, **fresh_half(10_050_000_001))
    assert not card.aol_card_intent.allowed_latch
