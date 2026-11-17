"""Cold Toyota readiness is not a previously granted steering command."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_toyota_moving_pcm_off_deadline import cp_for, moving
from openpilot.starpilot.aol.tests.test_toyota_parking_brake_deadline import refresh_saved_settings


BASE = 10_000_000_000


@pytest.mark.parametrize('profile', ('prius_long', 'prius_stock', 'camera_long', 'camera_stock'))
@pytest.mark.parametrize('gas', (False, True))
def test_cold_source_expiry_waits_without_inventing_authority(profile, gas):
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    cleanup.enter_context(patch.dict('os.environ', {'SIMULATION': '0', 'REPLAY': '0'}))
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, _, drive = case._authority_callers(False, cp=cp_for(profile), genuine_events=True)
    refresh_saved_settings(sd)
    feedback = TransportPauseFeedback(sd.CP)
    feedback_sm = messaging.SubMaster(['aolAxisState', 'onroadEvents', 'aolSafetyWire', 'pandaStates'])

    def card_feedback(now):
      samples = [sd.pm.messages[name] for name in ('aolAxisState', 'onroadEvents')]
      for name in ('aolSafetyWire', 'pandaStates'):
        sample = messaging.new_message(name, len(sd.sm[name]), valid=sd.sm.valid[name], logMonoTime=sd.sm.logMonoTime[name])
        setattr(sample, name, sd.sm[name])
        samples.append(sample.as_reader())
      feedback_sm.update_msgs(now / 1e9, samples)
      assert not feedback.observe(feedback_sm, sd.CP, moving(), now, latched=True,
                                  intent_producer_session='authority-card')

    update_events, publish = sd.update_events, sd.publish_axis_proof

    def startup_events(cs):
      sd.sm.valid['longitudinalPlan'] = False
      return update_events(cs)

    def delayed_publication(cs, **kwargs):
      with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=BASE + 31_148_802):
        return publish(cs, **kwargs)

    with patch.object(sd, 'update_events', side_effect=startup_events), \
         patch.object(sd, 'publish_axis_proof', side_effect=delayed_publication):
      axis, host, command = drive(0, observed_state=moving(), gas=gas, allowed=False,
                                  native_request_lat=False, native_request_long=False)
    assert log.OnroadEvent.EventName.commIssue in sd.events.names
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'TOYOTA_COLD_SOURCE_EXPIRY'
    assert axis.sourceCarStateMonoTime == 0 and str(axis.faultReason) == 'availabilityPause'
    assert not axis.nativeAcknowledged and not axis.desiredLateral and not axis.desiredLongitudinal
    assert not axis.lateralActive and not axis.longitudinalActive and not host.enabled
    assert not command.latActive and not command.longActive and not sd.aol_authority_lost
    assert sd.aol_publication_retained_intent.validUntilMonoTime == BASE + 200_000_000
    card_feedback(BASE + 31_148_802)
    # A fresh source is required; requesting LAT alone still cannot produce it.
    axis, host, command = drive(4, observed_state=moving(), gas=gas, allowed=False,
                                native_request_lat=False, native_request_long=False)
    assert axis.sourceCarStateMonoTime == BASE + 40_000_000
    assert not axis.lateralActive and not command.latActive and not host.enabled
    card_feedback(BASE + 40_000_000)
    axis, host, command = drive(5, observed_state=moving(), gas=gas, allowed=True,
                                native_request_lat=True, native_request_long=False)
    assert axis.lateralActive and command.latActive and not host.enabled and not command.longActive
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    card_feedback(BASE + 50_000_000)
    assert feedback.ever_lateral_active


@pytest.mark.parametrize('restriction', ('previously_active', 'native_requested', 'native_granted', 'native_invalid',
                                        'rx_fault', 'relay_fault', 'expired_source', 'can_invalid', 'critical'))
def test_cold_wait_never_waives_authority_or_health_failures(restriction):
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    cleanup.enter_context(patch.dict('os.environ', {'SIMULATION': '0', 'REPLAY': '0'}))
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, _, drive = case._authority_callers(False, cp=cp_for('prius_long'), genuine_events=True)
    refresh_saved_settings(sd)
    if restriction == 'previously_active':
      axis, _, command = drive(0, observed_state=moving(), native_request_long=False)
      assert axis.lateralActive and command.latActive
    original_events, publish = sd.update_events, sd.publish_axis_proof

    def startup_events(cs):
      sd.sm.valid['longitudinalPlan'] = False
      return original_events(cs)

    def delayed_publication(cs, **kwargs):
      delay = 201_000_000 if restriction == 'expired_source' else 31_148_802
      with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=BASE + 10_000_000 + delay):
        return publish(cs, **kwargs)

    state = moving()
    if restriction == 'can_invalid':
      state.canValid = False
    options = {'allowed': restriction == 'native_granted', 'native_request_lat': restriction == 'native_requested',
               'native_request_long': False, 'native_valid': restriction != 'native_invalid',
               'panda_fault': restriction == 'rx_fault', 'relay_fault': restriction == 'relay_fault'}
    if restriction == 'critical':
      options['critical_event'] = log.OnroadEvent.EventName.canError
    with patch.object(sd, 'update_events', side_effect=startup_events), \
         patch.object(sd, 'publish_axis_proof', side_effect=delayed_publication):
      axis, host, command = drive(1, observed_state=state, **options)
    assert log.OnroadEvent.EventName.controlsMismatch in sd.events.names
    assert not axis.lateralActive and not axis.longitudinalActive
    assert not host.enabled and not command.latActive and not command.longActive
    assert str(axis.faultReason) == 'critical'
