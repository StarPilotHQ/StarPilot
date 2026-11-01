from contextlib import ExitStack
from dataclasses import replace
from unittest import mock

import pytest

from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.runtime import current_axis
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests import test_feedback_intent_boundary as boundary
from openpilot.starpilot.aol.wire import decode_safety, encode_safety


SOURCE = 10_020_000_000
PUBLISHED = SOURCE + 4_368_711
NOW = PUBLISHED + 31_785_148


def native_change(sd, now, **changes):
  native = decode_safety(sd.sm['aolSafetyWire'])
  message = messaging.new_message('aolSafetyWire', 0, valid=changes.pop('valid', True),
                                  logMonoTime=changes.get('observedMonoTime', sd.sm.logMonoTime['aolSafetyWire']))
  message.aolSafetyWire = encode_safety(replace(native, **changes))
  sd.sm.update_msgs(now / 1e9, [message.as_reader()])


def assert_expired(controls, proof, now):
  controls.sm.update_msgs(now / 1e9, [proof])
  assert current_axis(controls.sm, now_ns=now) is None
  with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
    command, _ = controls.state_control()
  assert not command.latActive and not command.longActive


@pytest.mark.parametrize('accepted_before_expiry', [False, True])
@pytest.mark.parametrize('cancel', [False, True])
def test_new_combined_output_expiry_retains_intent_without_grant(accepted_before_expiry, cancel):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as clean:
    clean.callback(case.doCleanups)
    with OpenpilotPrefix():
      sd, controls, step, card, receive = case._ioniq_availability_callers()
      step(2, offset_ns=PUBLISHED - SOURCE)
      proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
      if accepted_before_expiry:
        assert receive(PUBLISHED, axis=proof, event=event)
      feedback = card.aol_transport_feedback
      original = (feedback.lateral_source_ns, feedback.lateral_baseline_ns, feedback.lateral_native_ns,
                  feedback.healthy_feedback_ns, feedback.healthy_native_ns)
      state = fixture.car_state()
      if cancel:
        state.buttonEvents = [fixture.car.CarState.ButtonEvent(type='cancel', pressed=True)]
      assert receive(NOW, axis=proof, event=event, state=state)
      assert original == (feedback.lateral_source_ns, feedback.lateral_baseline_ns, feedback.lateral_native_ns,
                          feedback.healthy_feedback_ns, feedback.healthy_native_ns)
      assert not feedback.qualified and not feedback.retention_qualified and not feedback.failed
      assert_expired(controls, proof, NOW)


@pytest.mark.parametrize('fault', [
  'native_lat_denied', 'native_long_denied', 'native_lat_withdrawn', 'native_long_withdrawn',
  'native_invalid', 'native_expired', 'native_future', 'native_regressed', 'native_session', 'native_inventory',
  'native_panda_fault', 'panda_configuration', 'producer', 'epoch', 'session', 'fault_session',
  'sequence_equal', 'source_equal', 'source_regressed', 'source_zero', 'source_future', 'source_stale_at_publication',
  'stamp_equal', 'lease_short', 'lease_long', 'no_ack', 'not_qualified', 'not_combined',
  'invalid_envelope', 'unseen_envelope', 'dead_envelope', 'prior_not_combined', 'pending_join', 'already_failed',
  'soft_event', 'immediate_event', 'can_invalid', 'can_timeout', 'temporary_eps', 'permanent_eps',
  'physical_cancel', 'settings_disabled', 'already_unlatched', 'unqualified_cp',
])
def test_new_expired_combined_never_hides_restriction(fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as clean:
    clean.callback(case.doCleanups)
    with OpenpilotPrefix():
      sd, controls, step, card, receive = case._ioniq_availability_callers()
      step(2, offset_ns=PUBLISHED - SOURCE)
      proof = sd.pm.messages['aolAxisState'].as_builder()
      axis = proof.aolAxisState
      event = sd.pm.messages['onroadEvents']
      feedback = card.aol_transport_feedback
      state = fixture.car_state()
      changes = {
        'native_lat_denied': {'lateralAllowed': False}, 'native_long_denied': {'longitudinalAllowed': False},
        'native_lat_withdrawn': {'requestedLateral': False}, 'native_long_withdrawn': {'requestedLongitudinal': False},
        'native_invalid': {'valid': False},
        'native_expired': {'observedMonoTime': NOW - 200_000_001, 'validUntilMonoTime': NOW - 1},
        'native_future': {'observedMonoTime': NOW + 1, 'validUntilMonoTime': NOW + 200_000_001},
        'native_regressed': {'observedMonoTime': feedback.lateral_native_ns - 1,
                             'validUntilMonoTime': feedback.lateral_native_ns + 199_999_999},
        'native_session': {'axisSessionId': 'other'},
      }
      if fault in changes:
        native_change(sd, NOW, **changes[fault])
      if fault in ('native_inventory', 'native_panda_fault'):
        native = decode_safety(sd.sm['aolSafetyWire'])
        slot = replace(native.pandaInventory[0], **({'hardwareSerial': 'other'} if fault == 'native_inventory' else {'faults': 1}))
        native_change(sd, NOW, pandaInventory=(slot,))
      if fault == 'panda_configuration':
        pandas = messaging.new_message('pandaStates', len(sd.sm['pandaStates']), valid=True,
                                        logMonoTime=sd.sm.logMonoTime['pandaStates'])
        pandas.pandaStates = sd.sm['pandaStates']
        pandas.pandaStates[0].safetyParam ^= 1
        sd.sm.update_msgs(NOW / 1e9, [pandas.as_reader()])
      if fault == 'producer':
        axis.intentProducerSessionId = 'other'
      if fault == 'epoch':
        axis.intentIntegrityEpoch += 1
      if fault == 'session':
        axis.sessionId = 'other'
      if fault == 'fault_session':
        axis.faultSessionId = 'other'
      if fault == 'sequence_equal':
        axis.sequence = feedback.sequence
      if fault == 'source_equal':
        axis.sourceCarStateMonoTime = feedback.signature[8]
      if fault == 'source_regressed':
        axis.sourceCarStateMonoTime = feedback.signature[8] - 1
      if fault == 'source_zero':
        axis.sourceCarStateMonoTime = 0
      if fault == 'source_future':
        axis.sourceCarStateMonoTime = PUBLISHED + 1
      if fault == 'source_stale_at_publication':
        axis.sourceCarStateMonoTime = PUBLISHED - 30_000_001
      if fault == 'stamp_equal':
        proof.logMonoTime = axis.observedMonoTime = feedback.signature[2]
        axis.validUntilMonoTime = proof.logMonoTime + 30_000_000
      if fault == 'lease_short':
        axis.validUntilMonoTime -= 1
      if fault == 'lease_long':
        axis.validUntilMonoTime += 1
      if fault == 'no_ack':
        axis.nativeAcknowledged = False
      if fault == 'not_qualified':
        axis.qualified = False
      if fault == 'not_combined':
        axis.longitudinalActive = False
      if fault == 'invalid_envelope':
        proof.valid = False
      if fault in ('unseen_envelope', 'dead_envelope'):
        original_update = card.sm.update_msgs
        def update(*args, **kwargs):
          result = original_update(*args, **kwargs)
          (card.sm.seen if fault == 'unseen_envelope' else card.sm.alive)['aolAxisState'] = False
          return result
        clean.enter_context(mock.patch.object(card.sm, 'update_msgs', side_effect=update))
      if fault == 'prior_not_combined':
        previous = list(feedback.signature)
        previous[10] = previous[12] = False
        feedback.signature = tuple(previous)
      if fault == 'pending_join':
        feedback.retention_started_ns = NOW - 30_000_001
      if fault == 'already_failed':
        feedback.failed = True
      if fault in ('soft_event', 'immediate_event'):
        name = log.OnroadEvent.EventName.commIssue if fault == 'soft_event' else log.OnroadEvent.EventName.steerUnavailable
        event = boundary.TestFeedbackIntentBoundary.event_at(NOW, name)
      if fault == 'can_invalid':
        state.canValid = False
      if fault == 'can_timeout':
        state.canTimeout = True
      if fault == 'temporary_eps':
        state.steerFaultTemporary = True
      if fault == 'permanent_eps':
        state.steerFaultPermanent = True
      if fault == 'physical_cancel':
        # The fault requests lateral withdrawal; default CANCEL only stops cruise.
        card.aol_card_intent.settings = replace(card.aol_card_intent.settings, cancel_actions=(9, 0, 0))
        state.buttonEvents = [fixture.car.CarState.ButtonEvent(type=fixture.car.CarState.ButtonEvent.Type.cancel, pressed=True)]
      if fault == 'settings_disabled':
        card.aol_card_intent.settings = replace(card.aol_card_intent.settings, enabled=False)
      if fault == 'already_unlatched':
        card.aol_card_intent.allowed_latch = False
      if fault == 'unqualified_cp':
        changed = card.CP.as_reader().as_builder()
        changed.radarUnavailable = True
        card.CP = changed
      assert_expired(controls, proof.as_reader(), NOW)
      assert not receive(NOW, axis=proof.as_reader(), event=event, state=state), fault
      assert not card.aol_card_intent.allowed_latch
      # No new physical arming edge follows. Healthy feedback alone cannot rearm.
      assert not receive(NOW + 1, axis=proof.as_reader(), event=sd.pm.messages['onroadEvents'])


@pytest.mark.parametrize('publication_age_ns', [30_000_000, 30_000_001])
def test_new_combined_source_must_have_been_fresh_when_published(publication_age_ns):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as clean:
    clean.callback(case.doCleanups)
    with OpenpilotPrefix():
      sd, controls, step, card, receive = case._ioniq_availability_callers()
      step(2, offset_ns=PUBLISHED - SOURCE)
      proof = sd.pm.messages['aolAxisState'].as_builder()
      proof.logMonoTime = proof.aolAxisState.observedMonoTime = SOURCE + 30_000_000
      proof.aolAxisState.sourceCarStateMonoTime = proof.logMonoTime - publication_age_ns
      proof.aolAxisState.validUntilMonoTime = proof.logMonoTime + 30_000_000
      now = proof.aolAxisState.validUntilMonoTime + 1
      assert_expired(controls, proof.as_reader(), now)
      assert receive(now, axis=proof.as_reader(), event=sd.pm.messages['onroadEvents']) == (publication_age_ns == 30_000_000)


def test_successive_expired_combined_proofs_do_not_renew_original_retention_deadline():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as clean:
    clean.callback(case.doCleanups)
    with OpenpilotPrefix():
      sd, controls, step, card, receive = case._ioniq_availability_callers()
      step(2, offset_ns=PUBLISHED - SOURCE)
      proof = sd.pm.messages['aolAxisState'].as_builder()
      feedback = card.aol_transport_feedback
      origin = (feedback.lateral_source_ns, feedback.lateral_baseline_ns, feedback.lateral_native_ns,
                feedback.healthy_feedback_ns, feedback.healthy_native_ns)
      deadline = origin[0] + 200_000_000
      retention_start = None
      for now in (NOW, origin[0] + 100_000_000, deadline, deadline + 1):
        proof.aolAxisState.sequence += 1
        proof.logMonoTime = proof.aolAxisState.observedMonoTime = now - 30_000_001
        proof.aolAxisState.sourceCarStateMonoTime = proof.logMonoTime - 4_000_000
        proof.aolAxisState.validUntilMonoTime = now - 1
        native_change(sd, now, observedMonoTime=now - 1, validUntilMonoTime=now + 199_999_999,
                      sourcePandaStatesMonoTime=now - 1)
        event = boundary.TestFeedbackIntentBoundary.event_at(now)
        assert_expired(controls, proof.as_reader(), now)
        assert receive(now, axis=proof.as_reader(), event=event) == (now <= deadline)
        assert origin == (feedback.lateral_source_ns, feedback.lateral_baseline_ns, feedback.lateral_native_ns,
                          feedback.healthy_feedback_ns, feedback.healthy_native_ns)
        if retention_start is None:
          retention_start = feedback.source_retention_started_ns
        assert feedback.source_retention_started_ns == retention_start
        if now < deadline:
          # Duplicate delivery cannot refresh any original timer either.
          assert receive(now + 1, axis=proof.as_reader(), event=event)
          assert feedback.source_retention_started_ns == retention_start
      assert feedback.failed and not card.aol_card_intent.allowed_latch


def test_expired_combined_retention_does_not_restore_long_after_actual_disable():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as clean:
    clean.callback(case.doCleanups)
    with OpenpilotPrefix():
      sd, _, step, card, receive = case._ioniq_availability_callers()
      step(2, offset_ns=PUBLISHED - SOURCE)
      assert receive(NOW, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      original_events = sd.update_events
      _, state, command = step(6, critical_event=log.OnroadEvent.EventName.steerUnavailable)
      assert not state.enabled and not command.latActive and not command.longActive
      assert not receive(10_060_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      sd.update_events = original_events
      _, state, command = step(7, latched=False, native_request_long=False)
      assert not state.enabled and not command.longActive
      assert not card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('regression', ['source', 'publication', 'same_sequence'])
def test_new_expired_combined_compares_against_last_retained_proof(regression):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as clean:
    clean.callback(case.doCleanups)
    with OpenpilotPrefix():
      sd, controls, step, card, receive = case._ioniq_availability_callers()
      step(2, offset_ns=PUBLISHED - SOURCE)
      first = sd.pm.messages['aolAxisState']
      assert receive(NOW, axis=first, event=sd.pm.messages['onroadEvents'])
      second = first.as_builder()
      second.aolAxisState.sequence += 1
      second.logMonoTime = second.aolAxisState.observedMonoTime = PUBLISHED + 10_000_000
      second.aolAxisState.sourceCarStateMonoTime = SOURCE + 1_000_000
      if regression == 'source':
        second.aolAxisState.sourceCarStateMonoTime = SOURCE - 1  # Still newer than the original baseline.
      elif regression == 'publication':
        second.logMonoTime = second.aolAxisState.observedMonoTime = PUBLISHED - 1
      else:
        second.aolAxisState.sequence = first.aolAxisState.sequence
      second.aolAxisState.validUntilMonoTime = second.logMonoTime + 30_000_000
      now = NOW + 10_000_000
      assert_expired(controls, second.as_reader(), now)
      assert not receive(now, axis=second.as_reader(), event=sd.pm.messages['onroadEvents'])
      assert card.aol_transport_feedback.failed and not card.aol_card_intent.allowed_latch


def test_real_native_disarm_is_not_undone_by_fresh_combined_feedback():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as clean:
    clean.callback(case.doCleanups)
    with OpenpilotPrefix():
      sd, _, step, card, receive = case._ioniq_availability_callers()
      step(2, offset_ns=PUBLISHED - SOURCE)
      proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
      assert receive(NOW, axis=proof, event=event)
      native_change(sd, NOW + 1, lateralAllowed=False)
      assert not receive(NOW + 1, axis=proof, event=event)
      assert not card.aol_card_intent.allowed_latch
      # Ordinary control was not disabled by this Card-only observation.
      # Its subsequent genuinely fresh proof can reset feedback history, but
      # must not invent a new physical AOL arming edge.
      _, state, command = step(6, latched=False)
      assert state.enabled and command.latActive and command.longActive
      assert not receive(10_060_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      assert not card.aol_transport_feedback.failed
      assert not card.aol_card_intent.allowed_latch
