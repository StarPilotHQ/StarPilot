"""EV6 brake receipt ordering retains only native-granted lateral permission."""
from contextlib import ExitStack
from dataclasses import replace

import pytest

from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.aol.tests.test_classic_long_profiles import params as classic_params
from openpilot.starpilot.aol.runtime import current_native
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.wire import encode_safety
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_ev6_physical_authorization import ev6_params

BASE = 10_000_000_000


def wire(sd, now, *, health_controls=True, lateral=True):
  prior = current_native(sd.sm, sd.CP, now_ns=now - 10_000_000, axis_session_id=sd.aol_session_id)
  assert prior is not None
  state = replace(prior, observedMonoTime=now, validUntilMonoTime=now + 200_000_000,
                  lateralAllowed=lateral, longitudinalAllowed=False, sourcePandaStatesMonoTime=now,
                  pandaInventory=tuple(replace(slot, controlsAllowed=health_controls) for slot in prior.pandaInventory))
  message = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
  message.aolSafetyWire = encode_safety(state)
  return message


def callers(case, cp=None):
  return case._authority_callers(True, cp=ev6_params() if cp is None else cp, genuine_events=True)


def test_ev6_early_brake_mask_is_not_an_immediate_fault():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = callers(case)
    for tick in range(10):
      axis, state, command = drive(tick)
      assert state.enabled and axis.lateralActive and axis.longitudinalActive
      assert command.latActive and command.longActive
    now = BASE + 100_000_000
    # Native health is read before its mask; latest PandaStates may already
    # report the brake, while parsed carState reaches selfdrive one tick later.
    native = wire(sd, now)
    axis, state, command = drive(10, native_request_lat=True, native_request_long=True,
      long_allowed=False, panda_controls_allowed=False, safety_message=native)
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'EV6_EARLY_NATIVE_LONG_WITHDRAWAL_MISCLASSIFIED'
    assert state.enabled and axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert sd.mismatch_counter > 0
    axis, state, command = drive(11, brake=True, native_request_lat=True, native_request_long=True,
      long_allowed=False, panda_controls_allowed=False)
    assert not state.enabled and not state.active
    assert axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names


def test_card_feedback_accepts_only_existing_lateral_grant_during_early_long_drop():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = callers(case)
    feedback = TransportPauseFeedback(sd.CP)
    sm = messaging.SubMaster(['aolAxisState', 'onroadEvents', 'aolSafetyWire', 'pandaStates'])
    for tick in range(10):
      drive(tick)
      now = BASE + tick * 10_000_000
      frames = [sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']]
      for name in ('aolSafetyWire', 'pandaStates'):
        event = messaging.new_message(name, len(sd.sm[name]), valid=sd.sm.valid[name], logMonoTime=sd.sm.logMonoTime[name])
        setattr(event, name, sd.sm[name])
        frames.append(event.as_reader())
      sm.update_msgs(now / 1e9, frames)
      assert not feedback.observe(sm, sd.CP, sd.CS_prev, now, latched=True, intent_producer_session='authority-card')
    now = BASE + 100_000_000
    sm.update_msgs(now / 1e9, [wire(sd, now).as_reader()])
    assert not feedback.observe(sm, sd.CP, sd.CS_prev, now, latched=True,
      intent_producer_session='authority-card'), 'EV6_CARD_EARLY_LONG_WITHDRAWAL_CLEARED_INTENT'
    assert not feedback.failed


@pytest.mark.parametrize('identity', (None, CAR.HYUNDAI_ELANTRA_2021))
@pytest.mark.parametrize('steering', (False, True))
@pytest.mark.parametrize('fault', ('lateral_denied', 'invalid', 'rx', 'heartbeat', 'inventory', 'session', 'expired'))
def test_early_long_classification_never_retains_denied_or_invalid_lateral(fault, steering, identity):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    cp = ev6_params() if identity is None else classic_params(identity, lda=True)
    sd, _, drive = callers(case, cp)
    for tick in range(2):
      axis, _, command = drive(tick, steering=steering)
      assert axis.lateralActive and command.latActive and command.longActive
    now = BASE + 20_000_000
    event = wire(sd, now, lateral=fault != 'lateral_denied')
    from openpilot.starpilot.aol.wire import decode_safety
    native = decode_safety(event.aolSafetyWire)
    assert native is not None
    if fault == 'invalid':
      event.valid = False
    elif fault in ('rx', 'heartbeat', 'inventory'):
      field, value = {'rx': ('safetyRxChecksInvalid', True), 'heartbeat': ('heartbeatLost', True),
                      'inventory': ('safetyParam', 0)}[fault]
      native = replace(native, pandaInventory=(replace(native.pandaInventory[0], **{field: value}),))
    elif fault == 'session':
      native = replace(native, axisSessionId='foreign')
    elif fault == 'expired':
      native = replace(native, observedMonoTime=now - 200_000_001, validUntilMonoTime=now - 1)
      event.logMonoTime = native.observedMonoTime
    event.aolSafetyWire = encode_safety(native)
    axis, state, command = drive(2, steering=steering, native_request_lat=True, native_request_long=True,
      long_allowed=False, panda_controls_allowed=False, safety_message=event)
    assert not axis.lateralActive and not command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert not state.enabled
    assert log.OnroadEvent.EventName.controlsMismatch in sd.events.names


def test_ev6_persistent_controls_denial_keeps_original_mismatch_counter():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = callers(case)
    for tick in range(10):
      drive(tick)
    for tick in range(10, 214):
      axis, state, command = drive(tick, native_request_lat=True, native_request_long=True,
        long_allowed=False, panda_controls_allowed=False)
      assert not axis.longitudinalActive and not command.longActive
      if log.OnroadEvent.EventName.controlsMismatch in sd.events.names:
        break
      assert axis.lateralActive and command.latActive and state.enabled
    assert sd.mismatch_counter >= 200
    assert not state.enabled and not axis.lateralActive and not command.latActive


@pytest.mark.parametrize('change', ('none', 'unmarked', 'wrong_word', 'stock', 'passive', 'wrong_identity'))
def test_exact_torque_long_profile_boundary(change):
  from openpilot.starpilot.car.hyundai.aol import early_native_long_withdrawal_supported
  cp = ev6_params()
  cp.safetyConfigs[0].safetyParam = 0x815
  cp.alternativeExperience = 32
  if change == 'unmarked':
    cp.alternativeExperience = 0
  elif change == 'wrong_word':
    cp.safetyConfigs[0].safetyParam = 0x8815
  elif change == 'stock':
    cp.openpilotLongitudinalControl, cp.pcmCruise = False, True
  elif change == 'passive':
    cp.passive = True
  elif change == 'wrong_identity':
    cp.carFingerprint = 'KIA_EV9'
  assert early_native_long_withdrawal_supported(cp) == (change == 'none')


@pytest.mark.parametrize('identity,health_controls', [
  (identity, health) for identity in (CAR.HYUNDAI_ELANTRA_2021, CAR.HYUNDAI_SONATA, CAR.HYUNDAI_IONIQ, CAR.HYUNDAI_KONA_EV, None)
  for health in (False, True)
] + [(CAR.KIA_EV9, False), (CAR.HYUNDAI_IONIQ_5_PE, False)])
@pytest.mark.parametrize('steering', (False, True))
def test_fresh_native_long_withdrawal_preserves_lateral(identity, steering, health_controls):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    if identity in (CAR.KIA_EV9, CAR.HYUNDAI_IONIQ_5_PE):
      from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params as angle_params
      from opendbc.car.hyundai.ev9_longitudinal import candidate as ev9_candidate
      from opendbc.car.hyundai.ioniq5pe_long_profile import candidate as pe_candidate
      candidate = ev9_candidate if identity == CAR.KIA_EV9 else pe_candidate
      cp = candidate(angle_params(candidate=identity), enabled=True, is_release=False)
      assert cp is not None
    else:
      cp = ev6_params() if identity is None else classic_params(identity, lda=True)
    sd, _, drive = case._authority_callers(True, cp=cp, genuine_events=True)
    for tick in range(10):
      axis, state, command = drive(tick, steering=steering)
      assert state.enabled and axis.lateralActive and axis.longitudinalActive
      assert command.latActive and command.longActive
    now = BASE + 100_000_000
    native = wire(sd, now, health_controls=health_controls)
    axis, state, command = drive(10, steering=steering, native_request_lat=True, native_request_long=True,
      long_allowed=False, panda_controls_allowed=False, safety_message=native)
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'EARLY_LONG_WITHDRAWAL_WITH_STEERING_MISCLASSIFIED'
    assert state.enabled and axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert sd.mismatch_counter > 0
    axis, state, command = drive(11, brake=True, steering=steering, native_request_lat=True, native_request_long=True,
      long_allowed=False, panda_controls_allowed=False)
    assert not state.enabled and not state.active
    assert axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names


def test_actual_classic_card_keeps_latch_through_steering_brake_and_cancel(monkeypatch):
  from openpilot.common.params import Params
  from openpilot.selfdrive.car import card as card_module
  from openpilot.starpilot.aol.tests.classic_startup_test_helpers import prepared_classic_card
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    saved = Params()
    saved.put('LKASButtonControl', 9, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    def prepare_card(CI, RI, startup_owner=None):
      from opendbc.car.hyundai.classic_long_aol import ordinary_word
      cp = CI.CP.as_reader().as_builder()
      cp.alternativeExperience = 0
      cp.safetyConfigs[0].safetyParam = ordinary_word(cp)
      return prepared_classic_card(cp)
    monkeypatch.setattr(card_module, 'Car', prepare_card)
    sd, _, drive, card, receive = case._ioniq_availability_callers(
      full_op=True, cp=classic_params(CAR.HYUNDAI_ELANTRA_2021, lda=True))
    cleanup.callback(card.vehicle_startup.close)
    sd.aol_settings = card.aol_card_intent.settings
    for tick in range(2, 10):
      axis, _, _ = drive(tick, steering=True)
      assert receive(BASE + tick * 10_000_000, state=sd.CS_prev.as_builder(),
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    now = BASE + 100_000_000
    axis, _, command = drive(10, steering=True, native_request_long=True, long_allowed=False,
      panda_controls_allowed=False, safety_message=wire(sd, now))
    assert axis.lateralActive and command.latActive and not command.longActive
    assert receive(now, state=sd.CS_prev.as_builder(), axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    for tick, brake in ((11, True), (12, False)):
      axis, state, command = drive(tick, steering=True, brake=brake, native_request_long=False,
        long_allowed=False, panda_controls_allowed=False)
      assert not state.enabled and axis.lateralActive and command.latActive and not command.longActive
      assert receive(BASE + tick * 10_000_000, state=sd.CS_prev.as_builder(),
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    cs = fixture.car_state()
    cs.buttonEvents = [{'type': 'cancel', 'pressed': True}]
    axis, state, command = drive(13, observed_state=cs, native_request_long=False,
      long_allowed=False, panda_controls_allowed=False)
    assert not state.enabled and axis.lateralActive and command.latActive and not command.longActive
    assert receive(BASE + 130_000_000, state=sd.CS_prev.as_builder(),
      axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert card.aol_card_intent.allowed_latch
