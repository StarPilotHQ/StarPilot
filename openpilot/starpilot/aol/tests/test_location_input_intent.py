from contextlib import ExitStack
from unittest import mock

import pytest

from opendbc.car import structs
from opendbc.car.hyundai.canfd_angle_aol import MODELS
from opendbc.car.hyundai.values import CAR
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


@pytest.mark.parametrize('identity', sorted(MODELS))
@pytest.mark.parametrize('gas', [False, True])
@pytest.mark.parametrize('event_first', [False, True])
def test_stock_pcm_location_input_pause_retains_intent_without_output(identity, gas, event_first):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=False, stock_angle=identity)
    state = fixture.car_state()
    state.vEgo = state.vEgoRaw = 6.98
    state.steeringAngleDeg = 65.9
    actual_update = sd.sm.update_msgs
    unavailable = [False]
    delivered = []

    def update(timestamp, messages):
      selected = []
      for message in messages:
        if message.which() == 'deviceMotion':
          delivered.append((int(message.logMonoTime), unavailable[0]))
          message = message.as_builder()
          message.deviceMotion.inputsOK = not unavailable[0]
          message = message.as_reader()
        selected.append(message)
      return actual_update(timestamp, selected)

    with mock.patch.object(sd.sm, 'update_msgs', side_effect=update):
      for tick in range(2, 5):
        axis, _, command = drive(tick, observed_state=state, gas=gas, panda_controls_allowed=False)
        assert axis.lateralActive and command.latActive
        assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                       axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      baseline = (card.aol_transport_feedback.lateral_baseline_ns,
                  card.aol_transport_feedback.lateral_native_ns)
      prior_axis = sd.pm.messages['aolAxisState']
      unavailable[0] = True
      axis, _, command = drive(5, observed_state=state, gas=gas, panda_controls_allowed=False)
      assert fixture.log.OnroadEvent.EventName.locationdTemporaryError in sd.events.names
      assert fixture.log.OnroadEvent.EventName.pcmDisable in sd.events.names
      assert not axis.lateralActive and not axis.longitudinalActive
      assert not command.latActive and not command.longActive
      if event_first:
        assert receive(10_050_000_000, state=state, axis=prior_axis, event=sd.pm.messages['onroadEvents'])
      assert receive(10_050_000_001, state=state, axis=sd.pm.messages['aolAxisState'],
                         event=sd.pm.messages['onroadEvents'])
      assert card.aol_card_intent.allowed_latch
      assert baseline == (card.aol_transport_feedback.lateral_baseline_ns,
                          card.aol_transport_feedback.lateral_native_ns)
      for tick in range(6, 10):
        axis, _, command = drive(tick, observed_state=state, gas=gas, panda_controls_allowed=False,
                                 allowed=False, native_request_lat=False, native_request_long=False)
        assert not axis.lateralActive and not axis.longitudinalActive
        assert not command.latActive and not command.longActive
        assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                       axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        assert baseline == (card.aol_transport_feedback.lateral_baseline_ns,
                            card.aol_transport_feedback.lateral_native_ns)
      unavailable[0] = False
      for tick in range(10, 12):
        axis, _, command = drive(tick, observed_state=state, gas=gas, panda_controls_allowed=False)
        assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                       axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        assert not axis.longitudinalActive and not command.longActive and not sd.enabled
      assert delivered == [(10_050_000_000, True), (10_100_000_000, False)]
      assert axis.lateralActive and command.latActive


@pytest.mark.parametrize('identity', [CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9])
@pytest.mark.parametrize('fault', ['native_denial', 'native_invalid', 'panda_rx', 'relay', 'permanent_eps',
  'invalid_can', 'can_timeout', 'door', 'seatbelt', 'brake', 'regen', 'physical_cancel', 'reverse', 'unknown_gear',
  'extra_user', 'hard_event', 'epoch', 'session', 'expired', 'future_source', 'no_baseline', 'configured_cancel'])
def test_stock_location_input_retention_refuses_independent_faults(identity, fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=False, stock_angle=identity)
    state = fixture.car_state()
    state.vEgo = state.vEgoRaw = 6.98
    axis, _, command = drive(2, observed_state=state, gas=True,
      critical_event=fixture.log.OnroadEvent.EventName.locationdTemporaryError, native_request_long=False,
      allowed=fault != 'native_denial', native_valid=fault != 'native_invalid',
      panda_fault=fault == 'panda_rx', relay_fault=fault == 'relay')
    assert not axis.lateralActive and not axis.longitudinalActive
    assert not command.latActive and not command.longActive
    # Supply the stock PCM USER envelope alongside the injected host fault.
    proof = sd.pm.messages['aolAxisState'].as_builder()
    event = sd.pm.messages['onroadEvents'].as_builder()
    event.onroadEvents = [x.to_dict() for x in event.onroadEvents] + [{'name': 'pcmDisable', 'userDisable': True}]
    state.steerFaultPermanent = fault == 'permanent_eps'
    state.canValid = fault != 'invalid_can'
    state.canTimeout = fault == 'can_timeout'
    state.doorOpen = fault == 'door'
    state.seatbeltUnlatched = fault == 'seatbelt'
    state.brakePressed = fault == 'brake'
    state.regenBraking = fault == 'regen'
    if fault == 'configured_cancel':
      from dataclasses import replace
      card.aol_card_intent.settings = replace(card.aol_card_intent.settings, cancel_actions=(9, 0, 0))
    if fault in ('physical_cancel', 'configured_cancel'):
      state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
    if fault in ('reverse', 'unknown_gear'):
      state.gearShifter = 'reverse' if fault == 'reverse' else 'unknown'
    if fault == 'extra_user':
      event.onroadEvents = [x.to_dict() for x in event.onroadEvents] + [{'name': 'buttonCancel', 'userDisable': True}]
    if fault == 'hard_event':
      event.onroadEvents[0].immediateDisable = True
    if fault == 'epoch':
      proof.aolAxisState.intentIntegrityEpoch += 1
    if fault == 'session':
      proof.aolAxisState.sessionId = proof.aolAxisState.faultSessionId = 'wrong-owner'
    if fault == 'expired':
      proof.aolAxisState.validUntilMonoTime = 10_019_999_999
    if fault == 'future_source':
      proof.aolAxisState.sourceCarStateMonoTime = 10_030_000_000
    if fault == 'no_baseline':
      # Remove the established baseline without erasing its session/history.
      card.aol_transport_feedback.lateral_baseline_ns = 0
    # A physical pause is independent of the location SOFT warning. Current
    # native proof and zero host commands remain mandatory; unassigned CANCEL
    # preserves intent, whereas the explicitly configured CANCEL still clears.
    baseline = (card.aol_transport_feedback.lateral_baseline_ns, card.aol_transport_feedback.lateral_native_ns)
    retained = receive(10_020_000_000, state=state, axis=proof.as_reader(), event=event.as_reader())
    expected = fault in ('seatbelt', 'brake', 'regen', 'physical_cancel')
    assert retained == expected, fault
    assert card.aol_card_intent.allowed_latch == expected
    assert not axis.lateralActive and not axis.longitudinalActive
    assert not command.latActive and not command.longActive
    if expected:
      assert baseline == (card.aol_transport_feedback.lateral_baseline_ns, card.aol_transport_feedback.lateral_native_ns)

