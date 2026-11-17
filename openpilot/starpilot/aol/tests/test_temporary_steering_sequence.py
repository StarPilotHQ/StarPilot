"""Temporary EPS retains intent through fresh proof and bounded event joins."""
from contextlib import ExitStack
from dataclasses import replace

import pytest

from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from opendbc.car.structs import car


def setup_case():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  return case


def baseline(case):
  sd, controls, drive, card, receive = case._ioniq_availability_callers(full_op=False)
  for tick in range(2, 152):
    axis, _, command = drive(tick, gas=tick >= 151, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                   event=sd.pm.messages['onroadEvents'])
  return sd, controls, drive, card, receive


def leases(feedback):
  return (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns,
          feedback.healthy_feedback_ns, feedback.healthy_native_ns)


@pytest.mark.parametrize('new_axis', [False, True], ids=['already_received_active', 'fresh_active_first_received_with_eps'])
@pytest.mark.parametrize('silent', [False, True])
def test_first_temporary_eps_keeps_intent_without_renewing_prior_leases(new_axis, silent):
  case = setup_case()
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = baseline(case)
    feedback = card.aol_transport_feedback
    original = leases(feedback)
    old_signature = feedback.signature
    axis, _, healthy_command = drive(152, gas=True, native_request_long=False)
    proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
    assert axis.lateralActive and healthy_command.latActive
    assert axis.sequence > old_signature[1]
    if not new_axis:
      assert receive(11_520_000_000, axis=proof, event=event)
      original = leases(feedback)
    temporary = fixture.car_state()
    temporary.gasPressed = temporary.steerFaultTemporary = True
    temporary.standstill = silent
    temporary.vEgo = 3.323399305343628
    temporary.steeringAngleDeg = -305.79998779296875
    # Card receives the preceding healthy axis with its first EPS sample,
    # before SelfdriveD publishes the physical withdrawal event.
    assert receive(11_529_500_000, axis=proof, event=event, state=temporary)
    assert card.aol_card_intent.allowed_latch and not feedback.failed
    assert leases(feedback) == original
    assert feedback.signature[4] == proof.aolAxisState.validUntilMonoTime
    for tick in range(153, 159):
      active_native = tick < 155
      withdrawn, published_state, command = drive(tick, gas=True, observed_state=temporary,
                                    allowed=active_native, native_request_lat=active_native,
                                    native_request_long=False)
      assert not withdrawn.lateralActive and not withdrawn.longitudinalActive
      assert not command.latActive and not command.longActive and not sd.enabled
      assert published_state.alertType == 'steerTempUnavailableSilent/warning'
      assert str(published_state.alertSound) == 'prompt'
      assert not published_state.enabled and not published_state.active
      assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'], state=temporary)
      assert card.aol_card_intent.allowed_latch and not feedback.failed
      assert leases(feedback) == original
    recovered, _, command = drive(159, gas=True, native_request_lat=True, native_request_long=False)
    assert recovered.lateralActive and command.latActive
    assert not recovered.longitudinalActive and not command.longActive and not sd.enabled
    assert receive(11_590_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('fault', ['permanent_eps', 'invalid_can', 'can_timeout', 'invalid_native',
  'producer', 'epoch', 'source_age', 'old_output_expired', 'cancel', 'settings_disabled', 'hard_event', 'mixed_soft',
  'source_reordered', 'stamp_reordered', 'session', 'fault_session'])
def test_fresh_active_eps_never_masks_hard_fault_or_bad_original_provenance(fault):
  case = setup_case()
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = baseline(case)
    drive(152, gas=True, native_request_long=False)
    proof = sd.pm.messages['aolAxisState'].as_builder()
    event = sd.pm.messages['onroadEvents']
    temporary = fixture.car_state()
    temporary.gasPressed = temporary.steerFaultTemporary = True
    if fault == 'permanent_eps':
      temporary.steerFaultPermanent = True
    elif fault == 'invalid_can':
      temporary.canValid = False
    elif fault == 'can_timeout':
      temporary.canTimeout = True
    elif fault == 'invalid_native':
      sd.sm.valid['aolSafetyWire'] = False
    elif fault == 'producer':
      proof.aolAxisState.intentProducerSessionId = 'other-card'
    elif fault == 'epoch':
      proof.aolAxisState.intentIntegrityEpoch += 1
    elif fault == 'source_age':
      proof.aolAxisState.sourceCarStateMonoTime -= 30_000_001
    elif fault == 'source_reordered':
      proof.aolAxisState.sourceCarStateMonoTime = card.aol_transport_feedback.signature[8] - 1
    elif fault == 'stamp_reordered':
      stamp = card.aol_transport_feedback.signature[2] - 1
      proof.logMonoTime = stamp
      proof.aolAxisState.observedMonoTime = stamp
      proof.aolAxisState.validUntilMonoTime = stamp + 30_000_000
      proof.aolAxisState.sourceCarStateMonoTime = stamp - 1
    elif fault == 'session':
      proof.aolAxisState.sessionId = proof.aolAxisState.faultSessionId = 'other-selfdrive'
    elif fault == 'fault_session':
      proof.aolAxisState.faultSessionId = 'other-selfdrive'
    elif fault == 'cancel':
      card.aol_card_intent.settings = replace(card.aol_card_intent.settings, cancel_actions=(9, 0, 0))
      temporary.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.cancel, pressed=True)]
    elif fault == 'settings_disabled':
      card.aol_card_intent.settings = replace(card.aol_card_intent.settings, enabled=False)
    elif fault in ('hard_event', 'mixed_soft'):
      events = Events()
      events.add(log.OnroadEvent.EventName.controlsMismatch if fault == 'hard_event' else log.OnroadEvent.EventName.commIssue)
      message = messaging.new_message('onroadEvents', len(events), valid=True, logMonoTime=event.logMonoTime)
      message.onroadEvents = events.to_msg()
      event = message.as_reader()
    now = 11_540_000_001 if fault == 'old_output_expired' else 11_529_500_000
    assert not receive(now, axis=proof.as_reader(), event=event, state=temporary)
    assert not card.aol_card_intent.allowed_latch


def test_two_fresh_active_eps_receipts_cannot_chain_past_original_source_lease():
  case = setup_case()
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = baseline(case)
    feedback = card.aol_transport_feedback
    original = leases(feedback)
    temporary = fixture.car_state()
    temporary.gasPressed = temporary.steerFaultTemporary = True
    for tick in (152, 153):
      # SD has not yet received EPS; each real publication uses a fresh
      # healthy source. Card must keep only intent inside its fixed baseline.
      axis, _, command = drive(tick, gas=True, native_request_long=False)
      assert axis.lateralActive and command.latActive
      now = 10_000_000_000 + tick * 10_000_000 + 9_500_000
      assert receive(now, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=temporary)
      assert leases(feedback) == original and not feedback.failed
    axis, _, _ = drive(154, gas=True, native_request_long=False)
    assert axis.lateralActive
    now = 11_540_000_001
    assert now - original[1] > 30_000_000
    assert now <= sd.pm.messages['aolAxisState'].aolAxisState.validUntilMonoTime
    assert not receive(now, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=temporary)
    assert not card.aol_card_intent.allowed_latch
    assert leases(feedback) == original


@pytest.mark.parametrize('order', ['proof_first', 'event_first', 'same_cycle'])
def test_actual_eps_withdrawal_delivery_orders_keep_only_existing_intent(order):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = baseline(case)
    feedback = card.aol_transport_feedback
    original = leases(feedback)
    drive(152, gas=True, native_request_long=False)
    temporary = fixture.car_state()
    temporary.gasPressed = temporary.steerFaultTemporary = True
    assert receive(11_529_500_000, axis=sd.pm.messages['aolAxisState'],
                   event=sd.pm.messages['onroadEvents'], state=temporary)
    assert leases(feedback) == original and not feedback.failed
    withdrawn, published_state, command = drive(153, gas=True, observed_state=temporary,
                                  native_request_lat=True, native_request_long=False)
    assert not withdrawn.lateralActive and not withdrawn.longitudinalActive
    assert not command.latActive and not command.longActive and not sd.enabled
    assert published_state.alertType == 'steerTempUnavailableSilent/warning'
    assert str(published_state.alertSound) == 'prompt'
    assert not published_state.enabled and not published_state.active
    proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
    assert proof.aolAxisState.faultEventMonoTime == event.logMonoTime
    deliveries = [{'axis': proof}, {'event': event}] if order == 'proof_first' else \
                 [{'event': event}, {'axis': proof}] if order == 'event_first' else [{'axis': proof, 'event': event}]
    for index, delivery in enumerate(deliveries):
      assert receive(11_530_000_000 + index * 100_000, state=temporary, **delivery)
      assert card.aol_card_intent.allowed_latch and not feedback.failed
      assert leases(feedback) == original
    recovered, _, recovered_command = drive(154, gas=True, native_request_lat=True, native_request_long=False)
    assert recovered.lateralActive and recovered_command.latActive
    assert not recovered.longitudinalActive and not recovered_command.longActive and not sd.enabled
    assert receive(11_540_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert card.aol_card_intent.allowed_latch and not feedback.failed


@pytest.mark.parametrize('fault', ['missing_event', 'late_event', 'duplicate_timeout', 'higher_sequence_timeout',
  'changed_occurrence', 'hard_event', 'mixed_soft', 'user_event', 'invalid_native', 'native_inventory',
  'producer', 'epoch', 'permanent_eps', 'can_timeout', 'cancel', 'settings_disabled', 'stale_source'])
def test_pending_eps_join_cannot_extend_or_hide_faults(fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = baseline(case)
    feedback = card.aol_transport_feedback
    original = leases(feedback)
    drive(152, gas=True, native_request_long=False)
    temporary = fixture.car_state()
    temporary.gasPressed = temporary.steerFaultTemporary = True
    assert receive(11_529_500_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=temporary)
    axis, _, command = drive(153, gas=True, observed_state=temporary,
                             native_request_lat=True, native_request_long=False)
    assert not axis.lateralActive and not axis.longitudinalActive
    assert not command.latActive and not command.longActive
    proof = sd.pm.messages['aolAxisState']
    event = sd.pm.messages['onroadEvents']
    anchor = proof.aolAxisState.faultEventMonoTime
    assert receive(11_530_000_000, axis=proof, state=temporary)
    assert feedback.pending_event == anchor and feedback.awaiting and not feedback.failed
    assert leases(feedback) == original
    now = 11_530_100_000
    delivery = {}
    if fault in ('missing_event', 'late_event', 'duplicate_timeout', 'higher_sequence_timeout'):
      if fault == 'duplicate_timeout':
        assert receive(11_550_000_000, axis=proof, state=temporary)
        assert feedback.pending_event == anchor
      elif fault == 'higher_sequence_timeout':
        for tick in (154, 155):
          axis, _, command = drive(tick, gas=True, observed_state=temporary, allowed=False,
                                    native_request_lat=False, native_request_long=False)
          assert not command.latActive and not command.longActive
          assert sd.pm.messages['aolAxisState'].aolAxisState.faultEventMonoTime == anchor
          assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'], state=temporary)
          assert feedback.pending_event == anchor
          assert leases(feedback) == original
      axis, _, command = drive(156, gas=True, observed_state=temporary, allowed=False,
                                native_request_lat=False, native_request_long=False)
      proof = sd.pm.messages['aolAxisState']
      assert not axis.lateralActive and not command.latActive and not command.longActive
      assert proof.aolAxisState.faultEventMonoTime == anchor
      now = anchor + 30_000_001
      assert now <= proof.aolAxisState.validUntilMonoTime
      delivery = {'axis': proof}
      if fault == 'late_event':
        delivery['event'] = event
    elif fault == 'changed_occurrence':
      changed = proof.as_builder()
      changed.aolAxisState.faultEventMonoTime += 1
      delivery = {'axis': changed.as_reader()}
    elif fault in ('hard_event', 'mixed_soft', 'user_event'):
      events = Events()
      events.add(log.OnroadEvent.EventName.steerTempUnavailable)
      events.add({'hard_event': log.OnroadEvent.EventName.controlsMismatch,
                  'mixed_soft': log.OnroadEvent.EventName.commIssue,
                  'user_event': log.OnroadEvent.EventName.buttonCancel}[fault])
      message = messaging.new_message('onroadEvents', len(events), valid=True, logMonoTime=anchor)
      message.onroadEvents = events.to_msg()
      delivery = {'event': message.as_reader()}
    elif fault == 'invalid_native':
      sd.sm.valid['aolSafetyWire'] = False
    elif fault == 'native_inventory':
      message = messaging.new_message('pandaStates', len(sd.sm['pandaStates']), valid=True,
                                      logMonoTime=sd.sm.logMonoTime['pandaStates'])
      message.pandaStates = sd.sm['pandaStates']
      message.pandaStates[-1].safetyParam ^= 1
      sd.sm.update_msgs(now / 1e9, [message.as_reader()])
    elif fault in ('producer', 'epoch', 'stale_source'):
      changed = proof.as_builder()
      if fault == 'producer':
        changed.aolAxisState.intentProducerSessionId = 'other-card'
      elif fault == 'epoch':
        changed.aolAxisState.intentIntegrityEpoch += 1
      else:
        changed.aolAxisState.sourceCarStateMonoTime -= 30_000_001
      delivery = {'axis': changed.as_reader()}
    elif fault == 'permanent_eps':
      temporary.steerFaultPermanent = True
    elif fault == 'can_timeout':
      temporary.canTimeout = True
    elif fault == 'cancel':
      card.aol_card_intent.settings = replace(card.aol_card_intent.settings, cancel_actions=(9, 0, 0))
      temporary.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.cancel, pressed=True)]
    elif fault == 'settings_disabled':
      card.aol_card_intent.settings = replace(card.aol_card_intent.settings, enabled=False)
    assert not receive(now, state=temporary, **delivery)
    assert not card.aol_card_intent.allowed_latch
    assert leases(feedback) == original


def test_initial_eps_off_cannot_reorder_the_preceding_accepted_active_source():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = baseline(case)
    drive(152, gas=True, native_request_long=False)
    temporary = fixture.car_state()
    temporary.gasPressed = temporary.steerFaultTemporary = True
    assert receive(11_529_500_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=temporary)
    previous = card.aol_transport_feedback.signature
    axis, _, command = drive(153, gas=True, observed_state=temporary,
                             native_request_lat=True, native_request_long=False)
    assert not axis.lateralActive and not command.latActive and not command.longActive
    proof = sd.pm.messages['aolAxisState'].as_builder()
    proof.aolAxisState.sourceCarStateMonoTime = previous[8] - 1
    assert proof.aolAxisState.sourceCarStateMonoTime <= proof.aolAxisState.observedMonoTime
    assert 11_530_000_000 - proof.aolAxisState.sourceCarStateMonoTime < 30_000_000
    assert not receive(11_530_000_000, axis=proof.as_reader(), state=temporary)
    assert not card.aol_card_intent.allowed_latch


def test_axis_first_eps_join_uses_last_accepted_active_proof_without_renewing_leases():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, _, drive, card, receive = baseline(case)
    feedback = card.aol_transport_feedback
    original = leases(feedback)
    drive(152, gas=True, native_request_long=False)
    temporary = fixture.car_state()
    temporary.gasPressed = temporary.steerFaultTemporary = True
    assert receive(11_529_500_000, axis=sd.pm.messages['aolAxisState'],
                   event=sd.pm.messages['onroadEvents'], state=temporary)
    active = feedback.signature
    assert leases(feedback) == original
    withdrawn, _, command = drive(153, gas=True, observed_state=temporary,
                                  native_request_lat=True, native_request_long=False)
    assert not withdrawn.lateralActive and not withdrawn.longitudinalActive
    assert not command.latActive and not command.longActive
    proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
    now = 11_541_000_000
    assert now - original[1] > 30_000_000
    assert now - active[8] <= 30_000_000 and now <= active[4]
    assert receive(now, axis=proof, state=temporary)
    assert card.aol_card_intent.allowed_latch and not feedback.failed
    assert leases(feedback) == original
    assert receive(now + 100_000, event=event, state=temporary)
    assert card.aol_card_intent.allowed_latch and leases(feedback) == original


@pytest.mark.parametrize('profile', ('g70', 'sonata', 'ev6', 'mixed_hdai', 'mixed_hdaii'))
@pytest.mark.parametrize('fault', ('none', 'permanent_eps', 'invalid_can', 'invalid_native', 'relay_fault'))
def test_hkg_steering_override_eps_then_brake_retains_only_valid_intent(profile, fault):
  from opendbc.car.hyundai.values import CAR
  from openpilot.common.params import Params
  from openpilot.starpilot.aol.tests.test_classic_scc_profiles import params as classic_params
  from openpilot.starpilot.aol.tests.test_canfd_stock_profiles import params as canfd_params
  from opendbc.car.hyundai.tests.test_palisade_2023 import params as mixed_params

  case = setup_case()
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    Params().put('MainCruiseButtonControl', 9, block=True)
    if profile.startswith('mixed_'):
      cp = mixed_params(profile.removeprefix('mixed_'))
    elif profile == 'ev6':
      cp = canfd_params(CAR.KIA_EV6)
    else:
      cp = classic_params(CAR.GENESIS_G70 if profile == 'g70' else CAR.HYUNDAI_SONATA)
    sd, _, drive, card, receive = case._ioniq_availability_callers(False, cp=cp, initially_armed=False)
    arm = fixture.car_state()
    arm.cruiseState.available = True
    arm.buttonEvents = [car.CarState.ButtonEvent(type='mainCruise', pressed=True)]
    card.aol_card_intent.update(arm, now_ns=10_015_000_000)
    assert card.aol_card_intent.allowed_latch
    for tick in range(2, 7):
      axis, _, command = drive(tick, steering=True, native_request_long=False)
      assert axis.lateralActive and command.latActive
      assert receive(10_000_000_000 + tick * 10_000_000,
                     axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    temporary = fixture.car_state()
    temporary.steerFaultTemporary = temporary.steeringPressed = True
    if fault == 'permanent_eps':
      temporary.steerFaultPermanent = True
    elif fault == 'invalid_can':
      temporary.canValid = False
    for tick in range(7, 21):
      temporary.brakePressed = tick >= 16
      axis, state, command = drive(tick, steering=True, brake=temporary.brakePressed, observed_state=temporary,
        native_request_lat=tick == 7, allowed=tick == 7, native_request_long=False,
        native_valid=fault != 'invalid_native', relay_fault=fault == 'relay_fault')
      assert not axis.lateralActive and not command.latActive and not command.longActive
      retained = receive(10_000_000_000 + tick * 10_000_000,
                         axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=temporary)
      if fault != 'none':
        assert not retained and not card.aol_card_intent.allowed_latch
        break
      assert retained and card.aol_card_intent.allowed_latch
      assert state.alertType == 'steerTempUnavailableSilent/warning'
      assert not state.enabled and not state.active
      assert axis.validUntilMonoTime - axis.observedMonoTime == 30_000_000
    if fault == 'none':
      axis, _, command = drive(21, steering=True, brake=True, native_request_lat=True, native_request_long=False)
      assert axis.lateralActive and command.latActive and not command.longActive
      assert receive(10_210_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      assert card.aol_card_intent.allowed_latch
