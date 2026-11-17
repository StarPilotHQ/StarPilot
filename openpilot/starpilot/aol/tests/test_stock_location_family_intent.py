from contextlib import ExitStack
from unittest import mock

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests.stock_lateral_test_helpers import car_state, hkg_family_case

def stock_family_callers(cleanup, family):
  cleanup.enter_context(OpenpilotPrefix())
  saved = Params()
  saved.put('LKASButtonControl', 9, block=True)
  saved.put('MainCruiseButtonControl', 0, block=True)
  for key in ('CancelButtonControl', 'LongCancelButtonControl', 'VeryLongCancelButtonControl'):
    saved.put(key, 0, block=True)
  case = hkg_family_case('hkg_canfd', False)
  cleanup.callback(case.doCleanups)
  lka = family in ('canfd_lka', 'canfd_lka_alt')
  fp = gen_empty_fingerprint()
  fp[1 if lka else 0].update({0x1CF: 8, 0x130: 32, 0x35: 32, 0x100: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1A0: 32})
  if lka:
    fp[2][0x110 if family == 'canfd_lka_alt' else 0x50] = 32 if family == 'canfd_lka_alt' else 16
  if family == 'canfd_lka_alt':
    fp[2][0x362] = 32
  identity = (CAR.KIA_NIRO_EV_2ND_GEN if family == 'canfd_camera' else CAR.HYUNDAI_IONIQ_5_N if family == 'canfd_ccnc_camera' else
              CAR.KIA_EV6 if lka else CAR.GENESIS_GV70_1ST_GEN)
  cp = CarInterface.get_params(identity, fp, [], False, False, False)
  case.family_cp = cp.as_reader()
  sd, _, drive, card, receive = case.family_callers(False)
  state = car_state()
  state.vEgo = state.vEgoRaw = 6.98
  state.steeringAngleDeg = 65.9
  return sd, drive, card, receive, state


@pytest.mark.parametrize('family', ['canfd_radar', 'canfd_lka', 'canfd_lka_alt', 'canfd_camera', 'canfd_ccnc_camera'])
@pytest.mark.parametrize('event_first', [False, True])
def test_existing_stock_hkg_location_and_nominal_pcm_off_keep_established_intent(family, event_first):
  with ExitStack() as cleanup:
    sd, drive, card, receive, state = stock_family_callers(cleanup, family)
    original = sd.sm.update_msgs
    unavailable = [False]

    def update(timestamp, messages):
      selected = []
      for message in messages:
        if message.which() == 'deviceMotion':
          message = message.as_builder()
          message.deviceMotion.inputsOK = not unavailable[0]
          message = message.as_reader()
        selected.append(message)
      return original(timestamp, selected)

    with mock.patch.object(sd.sm, 'update_msgs', side_effect=update):
      for tick in range(2, 5):
        axis, _, command = drive(tick, observed_state=state, panda_controls_allowed=False, native_request_long=False)
        assert axis.lateralActive and command.latActive and not axis.longitudinalActive and not command.longActive
        assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                       axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      baseline = (card.aol_transport_feedback.lateral_baseline_ns, card.aol_transport_feedback.lateral_native_ns)
      prior = sd.pm.messages['aolAxisState']
      unavailable[0] = True
      axis, _, command = drive(5, observed_state=state, panda_controls_allowed=False, native_request_long=False)
      assert log.OnroadEvent.EventName.locationdTemporaryError in sd.events.names
      assert log.OnroadEvent.EventName.pcmDisable in sd.events.names
      assert not axis.lateralActive and not axis.longitudinalActive and not command.latActive and not command.longActive
      if event_first:
        assert receive(10_050_000_000, state=state, axis=prior, event=sd.pm.messages['onroadEvents'])
      assert receive(10_050_000_001, state=state, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      assert card.aol_card_intent.allowed_latch
      assert baseline == (card.aol_transport_feedback.lateral_baseline_ns, card.aol_transport_feedback.lateral_native_ns)
      for tick in range(6, 10):
        axis, _, command = drive(tick, observed_state=state, panda_controls_allowed=False, native_request_long=False,
                                 allowed=False, native_request_lat=False)
        assert not axis.lateralActive and not axis.longitudinalActive and not command.latActive and not command.longActive
        assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                       axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        assert baseline == (card.aol_transport_feedback.lateral_baseline_ns, card.aol_transport_feedback.lateral_native_ns)
      unavailable[0] = False
      for tick in range(10, 12):
        axis, _, command = drive(tick, observed_state=state, panda_controls_allowed=False, native_request_long=False)
        assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                       axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        assert not axis.longitudinalActive and not command.longActive and not sd.enabled
      assert axis.lateralActive and command.latActive


@pytest.mark.parametrize('family', ['canfd_radar', 'canfd_lka', 'canfd_lka_alt', 'canfd_camera', 'canfd_ccnc_camera'])
@pytest.mark.parametrize('fault', ['native_denial', 'native_invalid', 'panda_rx', 'relay', 'permanent_eps',
  'invalid_can', 'can_timeout', 'door', 'seatbelt', 'brake', 'regen', 'gas', 'physical_cancel', 'reverse', 'unknown_gear',
  'extra_user', 'hard_event', 'epoch', 'session', 'expired', 'future_source', 'no_baseline', 'configured_cancel'])
def test_stock_location_never_retains_intent_through_independent_fault(family, fault):
  from opendbc.car import structs

  with ExitStack() as cleanup:
    sd, drive, card, receive, state = stock_family_callers(cleanup, family)
    for tick in range(2, 5):
      axis, _, command = drive(tick, observed_state=state, panda_controls_allowed=False, native_request_long=False)
      assert axis.lateralActive and command.latActive
      assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                     axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert card.aol_transport_feedback.lateral_baseline_ns
    original = sd.sm.update_msgs

    def update(timestamp, messages):
      selected = []
      for message in messages:
        if message.which() == 'deviceMotion':
          message = message.as_builder()
          message.deviceMotion.inputsOK = False
          message = message.as_reader()
        selected.append(message)
      return original(timestamp, selected)

    with mock.patch.object(sd.sm, 'update_msgs', side_effect=update):
      axis, _, command = drive(5, observed_state=state, panda_controls_allowed=False, native_request_long=False,
        allowed=fault != 'native_denial', native_valid=fault != 'native_invalid',
        panda_fault=fault == 'panda_rx', relay_fault=fault == 'relay')
    assert log.OnroadEvent.EventName.locationdTemporaryError in sd.events.names
    assert log.OnroadEvent.EventName.pcmDisable in sd.events.names
    assert not axis.lateralActive and not axis.longitudinalActive and not command.latActive and not command.longActive
    proof = sd.pm.messages['aolAxisState'].as_builder()
    event = sd.pm.messages['onroadEvents'].as_builder()
    state.steerFaultPermanent = fault == 'permanent_eps'
    state.canValid = fault != 'invalid_can'
    state.canTimeout = fault == 'can_timeout'
    state.doorOpen = fault == 'door'
    state.seatbeltUnlatched = fault == 'seatbelt'
    state.brakePressed = fault == 'brake'
    state.regenBraking = fault == 'regen'
    state.gasPressed = fault == 'gas'
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
      proof.aolAxisState.validUntilMonoTime = 10_049_999_999
    if fault == 'future_source':
      proof.aolAxisState.sourceCarStateMonoTime = 10_060_000_000
    if fault == 'no_baseline':
      card.aol_transport_feedback.lateral_baseline_ns = 0
    # A physical pause is independent of the location SOFT warning. Current
    # native proof and zero host commands remain mandatory; unassigned CANCEL
    # preserves intent, whereas the explicitly configured CANCEL still clears.
    baseline = (card.aol_transport_feedback.lateral_baseline_ns, card.aol_transport_feedback.lateral_native_ns)
    retained = receive(10_050_000_001, state=state, axis=proof.as_reader(), event=event.as_reader())
    expected = fault in ('seatbelt', 'brake', 'regen', 'gas', 'physical_cancel')
    assert retained == expected, fault
    assert card.aol_card_intent.allowed_latch == expected
    assert not axis.lateralActive and not axis.longitudinalActive
    assert not command.latActive and not command.longActive
    if expected:
      assert baseline == (card.aol_transport_feedback.lateral_baseline_ns, card.aol_transport_feedback.lateral_native_ns)



@pytest.mark.parametrize('family', ['canfd_radar', 'canfd_lka', 'canfd_lka_alt', 'canfd_camera', 'canfd_ccnc_camera'])
def test_healthy_stock_aol_skips_fault_recovery_profile_validation(family):
  with ExitStack() as cleanup:
    sd, drive, card, receive, state = stock_family_callers(cleanup, family)
    for tick in range(2, 5):
      axis, _, command = drive(tick, observed_state=state, panda_controls_allowed=False, native_request_long=False)
      assert axis.lateralActive and command.latActive
      assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                     axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    feedback = card.aol_transport_feedback
    axis = card.sm['aolAxisState']
    with mock.patch('openpilot.starpilot.aol.transport_pause.stock_lateral_user_event') as user_profile, \
         mock.patch('opendbc.car.hyundai.canfd_stock_aol.qualified') as canfd_profile, \
         mock.patch('opendbc.car.hyundai.canfd_angle_aol.qualified') as angle_profile, \
         mock.patch('opendbc.car.hyundai.classic_scc_aol.qualified') as classic_profile:
      assert not feedback._temporary_host_intent_current(card.sm, card.CP, state, axis,
        int(card.sm.logMonoTime['aolAxisState']), 10_040_000_001, latched=True,
        producer=card.slc_producer_session)
      for profile in (user_profile, canfd_profile, angle_profile, classic_profile):
        profile.assert_not_called()
    assert card.aol_card_intent.allowed_latch
