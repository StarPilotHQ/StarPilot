"""Classic HKG brake withdrawal keeps only independently granted lateral intent."""
from dataclasses import replace

import pytest

from opendbc.car import structs
from opendbc.car.hyundai.classic_long_aol import aol_word, qualified
from opendbc.car.hyundai.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.aol.intent import AolProcessFaultContext, AolSettings
from openpilot.starpilot.aol.runtime import decide_axes
from openpilot.starpilot.aol.tests.test_classic_long_profiles import params
from openpilot.starpilot.aol.tests.test_ccnc_full_stop_intent import full_sample
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.vehicle import create_intent, policy_for
from openpilot.starpilot.aol.wire import IntentState, decode_intent, encode_intent
from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, change, native_change

IDENTITIES = (CAR.HYUNDAI_PALISADE, CAR.HYUNDAI_SONATA, CAR.HYUNDAI_SONATA_HYBRID,
              CAR.HYUNDAI_ELANTRA_2021, CAR.HYUNDAI_KONA_EV, 'palisade_hdai', 'palisade_hdaii')


def profile(identity):
  if identity in ('palisade_hdai', 'palisade_hdaii'):
    from opendbc.car.hyundai.tests.test_palisade_2023 import params as mixed_params
    from opendbc.car.hyundai.blended_stock_aol import qualified_alpha
    cp = mixed_params('hdaii' if identity == 'palisade_hdaii' else 'hdai')
    cp.openpilotLongitudinalControl, cp.pcmCruise = True, False
    cp.alphaLongitudinalAvailable = True
    cp.safetyConfigs[0].safetyParam = 0x2014 if identity == 'palisade_hdaii' else 0x2004
    cp.alternativeExperience = 32
    assert qualified_alpha(cp, marked_only=True)
    return cp
  cp = params(identity, lda=True)
  cp.safetyConfigs[0].safetyParam = aol_word(cp)
  cp.alternativeExperience = 32
  assert qualified(cp, marked_only=True)
  return cp


def armed_card(identity, *, pause_speed=0.):
  cp = profile(identity)
  card = Car.__new__(Car)
  card.CP = cp
  card.aol_transport_feedback = TransportPauseFeedback(cp)
  card.aol_process_fault_context = AolProcessFaultContext()
  card.slc_producer_session = PRODUCER
  settings = AolSettings(True, pause_speed, 0, 0, (0, 0, 0), (0, 0, 0))
  card.aol_settings = settings
  card.aol_card_intent = create_intent(cp, settings, policy_for(cp))
  cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=18., vEgoRaw=18.)
  cs.cruiseState.available = cs.cruiseState.enabled = True
  card.aol_card_intent.update(cs, now_ns=NOW - 20_000_000)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  card.aol_card_intent.update(cs, now_ns=NOW - 10_000_000)
  cs.buttonEvents = []
  assert card.aol_card_intent.allowed_latch
  card.sm, _ = full_sample(cp, NOW, 1, allowed=True)
  assert not card.aol_disarming_fault(cs, NOW, NOW)
  return card, cs


def withdrawal(card, cs, now, *, combined=True, requested=True, controls=False):
  card.sm, native = full_sample(card.CP, now, (now - NOW) // 10_000_000 + 1,
                               allowed=True, long_active=combined)
  change(card.sm, 'pandaStates', lambda pandas: setattr(pandas[0], 'controlsAllowed', controls))
  native = native_change(card.sm, native, requestedLongitudinal=requested, longitudinalAllowed=False,
                         pandaInventory=(replace(native.pandaInventory[0], controlsAllowed=controls),))
  if cs.brakePressed:
    event = card.sm.messages['onroadEvents'].as_builder()
    event.init('onroadEvents', 1)
    event.onroadEvents[0].name = log.OnroadEvent.EventName.pedalPressed
    event.onroadEvents[0].userDisable = True
    card.sm.messages['onroadEvents'] = event.as_reader()
  return native


def axes(card, cs, native, now):
  owner = card.aol_card_intent
  allowed, pause_lat, pause_long = owner.output(cs)
  intent = decode_intent(encode_intent(IntentState(PRODUCER, 1, now, now, now + 200_000_000,
    allowed, pause_lat, pause_long, True, owner.allowed_latch)))
  return decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent,
    native=native, car_state=cs, initialized=True, model_ready=True, no_entry=False,
    immediate_disable=False, dm_lockout=False, pause_brake_mps=owner.settings.pause_brake_mps)


@pytest.mark.parametrize('identity', IDENTITIES)
@pytest.mark.parametrize('parsed_first', [False, True])
@pytest.mark.parametrize('controls', [False, True])
def test_actual_card_brake_join_retains_latch_and_only_native_lateral(identity, parsed_first, controls):
  card, cs = armed_card(identity)
  before, settings = card.CP.to_dict(), card.aol_card_intent.settings
  cs.brakePressed = parsed_first
  for tick, combined, requested in ((1, True, True), (2, False, True), (3, False, False)):
    now = NOW + tick * 10_000_000
    if tick > 1:
      cs.brakePressed = True
      cs.cruiseState.enabled = False
    native = withdrawal(card, cs, now, combined=combined, requested=requested, controls=controls)
    fault = card.aol_disarming_fault(cs, now, now)
    card.aol_card_intent.update(cs, fault_active=fault, now_ns=now)
    assert not fault and card.aol_card_intent.allowed_latch
    result = axes(card, cs, native, now)
    assert result.lateral_active and not result.longitudinal_active
    assert native.lateralAllowed and not native.longitudinalAllowed
  cs.brakePressed = False
  now = NOW + 40_000_000
  native = withdrawal(card, cs, now, combined=False, requested=False, controls=False)
  assert not card.aol_disarming_fault(cs, now, now)
  result = axes(card, cs, native, now)
  assert result.lateral_active and not result.longitudinal_active
  assert card.CP.to_dict() == before and card.aol_card_intent.settings == settings


@pytest.mark.parametrize('fault', ['lat_denied', 'rx', 'heartbeat', 'native_fault', 'panda_fault', 'session',
  'producer', 'epoch', 'source', 'expired_native', 'invalid_wire', 'can', 'eps', 'acc', 'critical', 'no_brake_event'])
@pytest.mark.parametrize('identity', (CAR.HYUNDAI_PALISADE, 'palisade_hdai', 'palisade_hdaii'))
def test_brake_join_cannot_retain_faulted_or_unproven_intent(fault, identity):
  card, cs = armed_card(identity)
  now = NOW + 10_000_000
  cs.brakePressed = True
  native = withdrawal(card, cs, now)
  if fault in ('lat_denied', 'session', 'expired_native'):
    changes = {'lat_denied': {'lateralAllowed': False}, 'session': {'axisSessionId': 'foreign'},
               'expired_native': {'observedMonoTime': now - 200_000_001, 'validUntilMonoTime': now - 1}}
    native_change(card.sm, native, **changes[fault])
  elif fault in ('rx', 'heartbeat', 'native_fault'):
    field, value = {'rx': ('safetyRxChecksInvalid', True), 'heartbeat': ('heartbeatLost', True),
                    'native_fault': ('faults', 1)}[fault]
    native_change(card.sm, native, pandaInventory=(replace(native.pandaInventory[0], **{field: value}),))
  elif fault == 'panda_fault':
    change(card.sm, 'pandaStates', lambda pandas: setattr(pandas[0], 'faults', ['relayMalfunction']))
  elif fault in ('producer', 'epoch', 'source'):
    field, value = {'producer': ('intentProducerSessionId', 'foreign'), 'epoch': ('intentIntegrityEpoch', 99),
                    'source': ('sourceCarStateMonoTime', now - 30_000_001)}[fault]
    change(card.sm, 'aolAxisState', lambda axis: setattr(axis, field, value))
  elif fault == 'invalid_wire':
    card.sm.valid['aolSafetyWire'] = False
  elif fault in ('can', 'eps', 'acc'):
    field, value = {'can': ('canValid', False), 'eps': ('steerFaultPermanent', True), 'acc': ('accFaulted', True)}[fault]
    setattr(cs, field, value)
  elif fault == 'critical':
    event = card.sm.messages['onroadEvents'].as_builder()
    event.onroadEvents[0].name = log.OnroadEvent.EventName.controlsMismatch
    event.onroadEvents[0].immediateDisable = True
    card.sm.messages['onroadEvents'] = event.as_reader()
  elif fault == 'no_brake_event':
    cs.brakePressed = False
  failed = card.aol_disarming_fault(cs, now, now)
  card.aol_card_intent.update(cs, fault_active=failed, now_ns=now)
  assert failed and not card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('identity', (CAR.HYUNDAI_PALISADE, 'palisade_hdai', 'palisade_hdaii'))
def test_saved_brake_pause_still_withdraws_steering_without_clearing_intent(identity):
  card, cs = armed_card(identity, pause_speed=30.)
  cs.brakePressed = True
  now = NOW + 10_000_000
  native = withdrawal(card, cs, now)
  fault = card.aol_disarming_fault(cs, now, now)
  card.aol_card_intent.update(cs, fault_active=fault, now_ns=now)
  assert not fault and card.aol_card_intent.allowed_latch
  result = axes(card, cs, native, now)
  assert not result.lateral_active and not result.longitudinal_active


@pytest.mark.parametrize('identity', (CAR.HYUNDAI_PALISADE, CAR.HYUNDAI_SONATA_HYBRID, 'palisade_hdai', 'palisade_hdaii'))
def test_actual_selfdrive_controls_withdraw_long_before_parsed_brake(identity):
  from contextlib import ExitStack

  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.starpilot.aol.tests.test_ioniq_availability import IoniqAvailabilityTests

  case = IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = case._authority_callers(True, cp=profile(identity), genuine_events=True)
    for tick in range(10):
      axis, state, command = drive(tick)
      assert state.enabled and axis.lateralActive and axis.longitudinalActive
      assert command.latActive and command.longActive
    axis, state, command = drive(10, native_request_lat=True, native_request_long=True,
      long_allowed=False, panda_controls_allowed=True)
    assert state.enabled and axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert sd.mismatch_counter == 0
    axis, state, command = drive(11, brake=True, native_request_lat=True, native_request_long=True,
      long_allowed=False, panda_controls_allowed=False)
    assert not state.enabled and axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names


@pytest.mark.parametrize('identity', (CAR.HYUNDAI_PALISADE, 'palisade_hdai', 'palisade_hdaii'))
def test_classic_persistent_long_denial_keeps_original_mismatch_limit(identity):
  from contextlib import ExitStack

  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.starpilot.aol.tests.test_ioniq_availability import IoniqAvailabilityTests

  case = IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = case._authority_callers(True, cp=profile(identity), genuine_events=True)
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


@pytest.mark.parametrize('identity', ['palisade_hdai', 'palisade_hdaii'])
@pytest.mark.parametrize('change', ['none', 'unmarked', 'word', 'model', 'identity', 'passive', 'dashcam', 'not_car', 'stock', 'topology'])
def test_mixed_brake_join_requires_exact_final_marked_profile(identity, change):
  from openpilot.starpilot.car.hyundai.aol import classic_brake_withdrawal, early_native_long_withdrawal_supported
  from opendbc.car.hyundai.values import HyundaiFlags

  cp = profile(identity)
  if change == 'unmarked':
    cp.alternativeExperience = 0
  elif change == 'word':
    cp.safetyConfigs[0].safetyParam ^= 0x10
  elif change == 'model':
    cp.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.hyundaiCanfd
  elif change == 'identity':
    cp.carFingerprint = CAR.HYUNDAI_PALISADE
  elif change in ('passive', 'dashcam', 'not_car'):
    setattr(cp, {'passive': 'passive', 'dashcam': 'dashcamOnly', 'not_car': 'notCar'}[change], True)
  elif change == 'stock':
    cp.openpilotLongitudinalControl, cp.pcmCruise = False, True
  elif change == 'topology':
    cp.flags = int(cp.flags) ^ int(HyundaiFlags.CANFD_LKA_STEER_MSG)
  cs = structs.CarState(brakePressed=True, canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  assert early_native_long_withdrawal_supported(cp) == (change == 'none')
  assert classic_brake_withdrawal(cp, cs) == (change == 'none')
