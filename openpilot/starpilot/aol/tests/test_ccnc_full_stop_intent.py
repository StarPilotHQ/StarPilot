"""A full-engagement native stop pause cannot silently consume armed AOL intent."""
from dataclasses import replace

import pytest
from opendbc.car import structs
from opendbc.car.hyundai.ev9_longitudinal import candidate as ev9_candidate
from opendbc.car.hyundai.ioniq5pe_long_profile import candidate as pe_candidate
from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params
from opendbc.car.hyundai.values import CAR
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, change, native_change, sample


IDENTITIES = (CAR.KIA_EV9, CAR.HYUNDAI_IONIQ_5_PE)


def profile(identity):
  cp = (ev9_candidate if identity == CAR.KIA_EV9 else pe_candidate)(params(candidate=identity), enabled=True, is_release=False)
  cp.alternativeExperience = 32
  return cp


def full_sample(cp, now, sequence, *, allowed, long_active=True):
  sm, native = sample(cp, now, now, sequence, active=allowed, allowed=allowed, event_stamp=now)
  change(sm, 'aolAxisState', lambda axis: setattr(axis, 'desiredLateral', True))
  for name, value in (('desiredLongitudinal', long_active), ('longitudinalActive', long_active), ('mode', 'combined' if long_active else 'lateralOnly')):
    change(sm, 'aolAxisState', lambda axis, name=name, value=value: setattr(axis, name, value))
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = []
  sm.messages['onroadEvents'] = event.as_reader()
  change(sm, 'pandaStates', lambda pandas: setattr(pandas[0], 'controlsAllowed', long_active))
  native = native_change(sm, native, requestedLongitudinal=long_active, longitudinalAllowed=long_active,
                         pandaInventory=(replace(native.pandaInventory[0], controlsAllowed=long_active),))
  return sm, native


@pytest.mark.parametrize('identity', IDENTITIES)
@pytest.mark.parametrize('long_active', [False, True])
def test_restricted_full_stop_keeps_only_intent(identity, long_active):
  cp = profile(identity)
  cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=1., vEgoRaw=1.)
  cs.cruiseState.available = cs.cruiseState.enabled = True
  feedback = TransportPauseFeedback(cp)
  sm, _ = full_sample(cp, NOW, 1, allowed=True)
  assert not feedback.observe(sm, cp, cs, NOW, latched=True, intent_producer_session=PRODUCER)
  assert feedback.lateral_inventory and not feedback.lateral_only_baseline
  before = (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns,
            feedback.healthy_feedback_ns, feedback.healthy_native_ns)
  cs.vEgo = cs.vEgoRaw = .08
  cs.standstill = True
  cs.brakePressed = not long_active
  for tick in range(1, 35):
    now = NOW + tick * 10_000_000
    sm, native = full_sample(cp, now, tick + 1, allowed=False, long_active=long_active)
    original = native
    assert not feedback.observe(sm, cp, cs, now, latched=True, intent_producer_session=PRODUCER)
    assert not feedback.failed
    assert not native.lateralAllowed and native.longitudinalAllowed == long_active
    assert native == original
    assert before == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns,
                      feedback.healthy_feedback_ns, feedback.healthy_native_ns)
  cs.standstill = False
  cs.brakePressed = False
  cs.vEgo = cs.vEgoRaw = 1.
  sm, native = full_sample(cp, NOW + 350_000_000, 36, allowed=True)
  assert not feedback.observe(sm, cp, cs, NOW + 350_000_000, latched=True, intent_producer_session=PRODUCER)
  assert native.lateralAllowed and not feedback.failed


@pytest.mark.parametrize('identity', IDENTITIES)
@pytest.mark.parametrize('fault', ['rolling', 'can', 'timeout', 'eps', 'acc', 'steering', 'epoch', 'producer',
  'session', 'source', 'native_long', 'native_request', 'native_fault', 'panda_fault', 'event', 'baseline', 'inventory'])
def test_full_stop_cannot_mask_faults(identity, fault):
  cp = profile(identity)
  cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=1., vEgoRaw=1.)
  cs.cruiseState.available = cs.cruiseState.enabled = True
  feedback = TransportPauseFeedback(cp)
  sm, _ = full_sample(cp, NOW, 1, allowed=True)
  assert not feedback.observe(sm, cp, cs, NOW, latched=True, intent_producer_session=PRODUCER)
  now = NOW + 10_000_000
  cs.vEgo = cs.vEgoRaw = .08
  cs.standstill = True
  sm, native = full_sample(cp, now, 2, allowed=False)
  producer = PRODUCER
  if fault == 'rolling':
    cs.standstill = False
    cs.vEgo = cs.vEgoRaw = 1.
  if fault in ('can', 'timeout', 'eps', 'acc', 'steering'):
    field, value = {'can': ('canValid', False), 'timeout': ('canTimeout', True), 'eps': ('steerFaultPermanent', True),
                    'acc': ('accFaulted', True), 'steering': ('steeringDisengage', True)}[fault]
    setattr(cs, field, value)
  if fault in ('epoch', 'session', 'source'):
    field, value = {'epoch': ('intentIntegrityEpoch', 99), 'session': ('sessionId', 'foreign'),
                    'source': ('sourceCarStateMonoTime', now - 30_000_001)}[fault]
    change(sm, 'aolAxisState', lambda axis: setattr(axis, field, value))
  if fault == 'producer':
    producer = 'foreign'
  if fault in ('native_long', 'native_request'):
    native_change(sm, native, **({'longitudinalAllowed': False} if fault == 'native_long' else {'requestedLongitudinal': False}))
  if fault == 'native_fault':
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  if fault == 'panda_fault':
    change(sm, 'pandaStates', lambda pandas: setattr(pandas[0], 'faults', ['relayMalfunction']))
  if fault == 'event':
    event = sm.messages['onroadEvents'].as_builder()
    event.init('onroadEvents', 1)
    event.onroadEvents[0].name = log.OnroadEvent.EventName.controlsMismatch
    event.onroadEvents[0].immediateDisable = True
    sm.messages['onroadEvents'] = event.as_reader()
  if fault == 'baseline':
    feedback.lateral_baseline_ns = 0
  if fault == 'inventory':
    feedback.lateral_inventory = None
  assert not feedback._angle_stop_intent_current(sm, cp, cs, sm['aolAxisState'], now, now,
    latched=True, producer=producer)


@pytest.mark.parametrize('identity', IDENTITIES)
def test_actual_card_disarming_preserves_intent_then_brake_fallback(identity):
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.aol.intent import AolProcessFaultContext, read_settings
  from openpilot.starpilot.aol.runtime import decide_axes
  from openpilot.starpilot.aol.wire import IntentState, encode_intent, decode_intent
  from openpilot.starpilot.aol.vehicle import create_intent, native_latch_rejected, policy_for

  with OpenpilotPrefix():
    saved = Params()
    saved.put_bool('AlwaysOnLateral', True, block=True)
    saved.put('LKASButtonControl', 0, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    saved.put('AolBrakePauseSpeedMps', 0.0, block=True)
    settings = read_settings(saved)
    cp = profile(identity)
    card = Car.__new__(Car)
    card.CP = cp
    def intent_at(now):
      allowed, pause_lat, pause_long = card.aol_card_intent.output(cs)
      return decode_intent(encode_intent(IntentState(PRODUCER, 1, now, now, now + 200_000_000,
        allowed, pause_lat, pause_long, True, card.aol_card_intent.allowed_latch)))
    card.aol_transport_feedback = TransportPauseFeedback(cp)
    card.aol_process_fault_context = AolProcessFaultContext()
    card.slc_producer_session = PRODUCER
    card.aol_settings = settings
    card.aol_card_intent = create_intent(cp, settings, policy_for(cp))
    cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=12., vEgoRaw=12.)
    cs.cruiseState.available = cs.cruiseState.enabled = True
    card.aol_card_intent.update(cs, now_ns=NOW - 20_000_000)
    cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
    card.aol_card_intent.update(cs, now_ns=NOW - 10_000_000)
    cs.buttonEvents = []
    assert card.aol_card_intent.allowed_latch
    card.sm, _ = full_sample(cp, NOW, 1, allowed=True)
    assert not card.aol_disarming_fault(cs, NOW, NOW)
    cs.vEgo = cs.vEgoRaw = .08
    cs.standstill = True
    for tick in range(1, 35):
      now = NOW + tick * 10_000_000
      card.sm, native = full_sample(cp, now, tick + 1, allowed=False)
      fault = card.aol_disarming_fault(cs, now, now)
      assert not native_latch_rejected(cp, native, state=cs)
      card.aol_card_intent.update(cs, fault_active=fault, now_ns=now)
      assert card.aol_card_intent.allowed_latch
      axes = decide_axes(standard_lateral=True, standard_longitudinal=True, intent=intent_at(now),
        native=native, car_state=cs, initialized=True, model_ready=True, no_entry=False,
        immediate_disable=False, dm_lockout=False, pause_brake_mps=0.0)
      assert not axes.lateral_active
    now = NOW + 350_000_000
    cs.standstill = False
    cs.vEgo = cs.vEgoRaw = 12.
    card.sm, _ = full_sample(cp, now, 36, allowed=True)
    assert not card.aol_disarming_fault(cs, now, now)
    cs.brakePressed = True
    now += 10_000_000
    card.sm, native = full_sample(cp, now, 37, allowed=True)
    for name, value in (('desiredLongitudinal', False), ('longitudinalActive', False), ('mode', 'lateralOnly')):
      change(card.sm, 'aolAxisState', lambda axis, name=name, value=value: setattr(axis, name, value))
    change(card.sm, 'pandaStates', lambda pandas: setattr(pandas[0], 'controlsAllowed', False))
    native = native_change(card.sm, native, requestedLongitudinal=False, longitudinalAllowed=False,
                           pandaInventory=(replace(native.pandaInventory[0], controlsAllowed=False),))
    event = card.sm.messages['onroadEvents'].as_builder()
    event.init('onroadEvents', 1)
    event.onroadEvents[0].name = log.OnroadEvent.EventName.pedalPressed
    event.onroadEvents[0].userDisable = True
    card.sm.messages['onroadEvents'] = event.as_reader()
    fault = card.aol_disarming_fault(cs, now, now)
    card.aol_card_intent.update(cs, fault_active=fault, now_ns=now)
    assert card.aol_card_intent.allowed_latch
    axes = decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent_at(now),
      native=native, car_state=cs, initialized=True, model_ready=True, no_entry=False,
      immediate_disable=False, dm_lockout=False, pause_brake_mps=0.0)
    assert axes.lateral_active and not axes.longitudinal_active
