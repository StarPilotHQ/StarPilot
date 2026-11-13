"""Subaru EPS refusal preserves established intent, never steering permission."""
from dataclasses import replace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.stock_aol import qualified
from opendbc.car.subaru.values import CAR
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.vehicle import policy_for
from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, change, native_change, sample


def baseline():
  with OpenpilotPrefix():
    cp = CarInterface.get_params(CAR.SUBARU_IMPREZA_2020, gen_empty_fingerprint(), [], False, False, False)
  policy = policy_for(cp)
  cp.alternativeExperience |= policy.alternative_experience_addition
  cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
  assert qualified(cp, marked_only=True)
  state = structs.CarState(canValid=True, gearShifter='drive', vEgo=15.)
  state.cruiseState.available = True
  feedback = TransportPauseFeedback(cp)
  sm, _ = sample(cp, NOW, NOW, 1)
  assert not feedback.observe(sm, cp, state, NOW, latched=True, intent_producer_session=PRODUCER)
  assert feedback.lateral_only_baseline
  return cp, state, feedback


def leases(feedback):
  return (feedback.healthy_feedback_ns, feedback.healthy_native_ns, feedback.lateral_baseline_ns,
          feedback.lateral_source_ns, feedback.lateral_native_ns)


@pytest.mark.parametrize('gas', (False, True))
@pytest.mark.parametrize('withdrawn', (False, True))
def test_native_eps_denial_retains_intent_without_renewing_permission(gas, withdrawn):
  cp, state, feedback = baseline()
  before = leases(feedback)
  state.steerFaultTemporary, state.gasPressed = True, gas
  now = NOW + 10_000_000
  sm, native = sample(cp, now, now, 2, active=not withdrawn, allowed=False,
                      event_stamp=now if withdrawn else NOW)
  if withdrawn:
    event = sm.messages['onroadEvents'].as_builder()
    event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
                         {'name': 'steerTempUnavailableSilent', 'warning': True}]
    sm.messages['onroadEvents'] = event.as_reader()
  assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
  assert not feedback.failed
  assert leases(feedback) == before
  assert native.requestedLateral and not native.lateralAllowed and not native.longitudinalAllowed
  # The EPS restriction may end only with fresh, independently allowed feedback.
  state.steerFaultTemporary = False
  recovered, native = sample(cp, now + 10_000_000, now + 10_000_000, 3, event_stamp=now + 10_000_000)
  assert not feedback.observe(recovered, cp, state, now + 10_000_000, latched=True, intent_producer_session=PRODUCER)
  assert not feedback.failed and native.lateralAllowed


@pytest.mark.parametrize('fault', ('no_eps', 'permanent_eps', 'invalid_can', 'timeout', 'main_off', 'cancel',
  'door', 'sensors', 'disengage', 'reverse', 'acc_fault', 'native_invalid', 'native_fault', 'native_heartbeat',
  'wrong_session', 'wrong_producer', 'wrong_epoch', 'expired', 'warning_stale', 'native_stale',
  'native_missing', 'request_off', 'long_request', 'hard_event', 'other_user'))
@pytest.mark.parametrize('brake', (False, True))
def test_eps_retention_does_not_mask_independent_vetoes(fault, brake):
  cp, state, feedback = baseline()
  state.steerFaultTemporary = True
  state.brakePressed = brake
  now = NOW + 10_000_000
  sm, native = sample(cp, now, now, 2, allowed=False)
  if brake:
    event = sm.messages['onroadEvents'].as_builder()
    event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
                         {'name': 'pedalPressed', 'userDisable': True}]
    sm.messages['onroadEvents'] = event.as_reader()
  physical = {'no_eps': ('steerFaultTemporary', False), 'permanent_eps': ('steerFaultPermanent', True),
    'invalid_can': ('canValid', False), 'timeout': ('canTimeout', True), 'door': ('doorOpen', True),
    'sensors': ('vehicleSensorsInvalid', True), 'disengage': ('steeringDisengage', True), 'acc_fault': ('accFaulted', True)}
  if fault in physical:
    setattr(state, *physical[fault])
  elif fault == 'main_off':
    state.cruiseState.available = False
  elif fault == 'cancel':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  elif fault == 'reverse':
    state.gearShifter = 'reverse'
  elif fault == 'warning_stale':
    sm.logMonoTime['onroadEvents'] = NOW - 2_000_000_000
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'faultEventMonoTime', NOW - 2_000_000_000))
  elif fault == 'native_stale':
    native_change(sm, native, validUntilMonoTime=now - 1)
  elif fault == 'native_missing':
    sm.seen['aolSafetyWire'] = False
  elif fault == 'request_off':
    native_change(sm, native, requestedLateral=False)
  elif fault == 'native_invalid':
    sm.valid['aolSafetyWire'] = False
  elif fault in ('native_fault', 'native_heartbeat'):
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0],
      **({'faults': 1} if fault == 'native_fault' else {'heartbeatLost': True})),))
  elif fault == 'wrong_session':
    native_change(sm, native, axisSessionId='different')
  elif fault == 'wrong_epoch':
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'intentIntegrityEpoch', 2))
  elif fault == 'expired':
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'validUntilMonoTime', now - 1))
  elif fault == 'long_request':
    native_change(sm, native, requestedLongitudinal=True)
  elif fault in ('hard_event', 'other_user'):
    change(sm, 'onroadEvents', lambda events: setattr(events[0],
      'immediateDisable' if fault == 'hard_event' else 'name', True if fault == 'hard_event' else 'buttonCancel'))
  assert feedback.observe(sm, cp, state, now, latched=True,
                          intent_producer_session='different' if fault == 'wrong_producer' else PRODUCER)


@pytest.mark.parametrize('gas', (False, True))
def test_actual_selfdrive_and_controls_keep_eps_output_prohibited(gas):
  from contextlib import ExitStack
  from openpilot.cereal import log
  from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture

  cp, _, _ = baseline()
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, step = case._authority_callers(full_op=False, cp=cp, genuine_events=True)
    temporary = fixture.car_state()
    temporary.steerFaultTemporary, temporary.gasPressed = True, gas
    for tick in range(2, 8):
      axis, _, command = step(tick, observed_state=temporary, allowed=False,
                              native_request_lat=True, native_request_long=False)
      assert not axis.lateralActive and not command.latActive and not command.longActive
      assert any(name in sd.events.names for name in (log.OnroadEvent.EventName.steerTempUnavailable,
                                                     log.OnroadEvent.EventName.steerTempUnavailableSilent))
    axis, _, command = step(8, native_request_lat=True, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive


def test_active_join_cannot_renew_original_permission_deadline_without_warning():
  cp, state, feedback = baseline()
  state.steerFaultTemporary = True
  before = leases(feedback)
  for sequence, elapsed in enumerate((10_000_000, 20_000_000, 30_000_001), 2):
    now = NOW + elapsed
    sm, _ = sample(cp, now, now, sequence, allowed=False)
    rejected = feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
    assert rejected == (elapsed > 30_000_000)
    assert leases(feedback) == before


@pytest.mark.parametrize('gas', (False, True))
def test_matched_warning_recovery_waits_for_fresh_native_ack(gas):
  cp, state, feedback = baseline()
  before = leases(feedback)
  state.steerFaultTemporary, state.gasPressed = True, gas
  stamp = NOW + 10_000_000
  for sequence, elapsed in enumerate((10_000_000, 20_000_000, 40_000_000), 2):
    now = NOW + elapsed
    sm, native = sample(cp, now, now, sequence, active=False, requested=sequence == 2, allowed=False, event_stamp=stamp)
    event = sm.messages['onroadEvents'].as_builder()
    event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
                         {'name': 'steerTempUnavailableSilent', 'warning': True}]
    sm.messages['onroadEvents'] = event.as_reader()
    if sequence == 4:
      state.steerFaultTemporary = False
    assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
    assert not native.lateralAllowed and not native.longitudinalAllowed
    if state.steerFaultTemporary:
      assert leases(feedback) == before
  assert not feedback.failed


@pytest.mark.parametrize('gap', (30_000_001, 60_000_000_000))
def test_first_withdrawn_warning_cannot_revive_an_expired_baseline(gap):
  cp, state, feedback = baseline()
  state.steerFaultTemporary = True
  now = NOW + gap
  sm, _ = sample(cp, now, now, 2, active=False, allowed=False, event_stamp=now)
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
                       {'name': 'steerTempUnavailableSilent', 'warning': True}]
  sm.messages['onroadEvents'] = event.as_reader()
  assert feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)


def test_continuous_warning_keeps_intent_but_gap_or_stale_source_cannot_renew_it():
  for stale_source in (False, True):
    cp, state, feedback = baseline()
    state.steerFaultTemporary = True
    before = leases(feedback)
    for sequence in range(2, 102):
      now = NOW + (sequence - 1) * 10_000_000
      sm, native = sample(cp, now, now, sequence, active=False, allowed=False, event_stamp=now)
      event = sm.messages['onroadEvents'].as_builder()
      event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
                           {'name': 'steerTempUnavailableSilent', 'warning': True}]
      sm.messages['onroadEvents'] = event.as_reader()
      assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
      assert not native.lateralAllowed and leases(feedback) == before
    now += 10_000_000 if stale_source else 60_000_000_000
    sm, _ = sample(cp, now, now, 102, active=False, allowed=False, event_stamp=now)
    event = sm.messages['onroadEvents'].as_builder()
    event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
                         {'name': 'steerTempUnavailableSilent', 'warning': True}]
    sm.messages['onroadEvents'] = event.as_reader()
    if stale_source:
      change(sm, 'aolAxisState', lambda axis: setattr(axis, 'sourceCarStateMonoTime', NOW))
    assert feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)


@pytest.mark.parametrize('axis_first', (False, True))
def test_brake_warning_event_change_preserves_existing_eps_intent(axis_first):
  cp, state, feedback = baseline()
  state.steerFaultTemporary = state.brakePressed = True
  before = leases(feedback)
  warning = NOW + 10_000_000
  for sequence in range(2, 10):
    now = NOW + (sequence - 1) * 10_000_000
    event_stamp = warning if sequence < 6 else NOW + 50_000_000
    axis_stamp = warning if sequence < 6 or sequence == 6 and not axis_first else NOW + 50_000_000
    sm, native = sample(cp, now, now, sequence, active=False, requested=sequence == 2, allowed=False, event_stamp=event_stamp)
    change(sm, 'aolAxisState', lambda axis, stamp=axis_stamp: setattr(axis, 'faultEventMonoTime', stamp))
    event = sm.messages['onroadEvents'].as_builder()
    event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
                         {'name': 'pedalPressed', 'userDisable': True},
                         {'name': 'steerTempUnavailableSilent', 'warning': True}]
    if sequence == 6 and axis_first:
      event.logMonoTime = warning
      sm.logMonoTime['onroadEvents'] = warning
    sm.messages['onroadEvents'] = event.as_reader()
    assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
    assert not feedback.failed and leases(feedback) == before
    assert not native.lateralAllowed and not native.longitudinalAllowed


def braked_main_sample(cp, now, sequence):
  sm, native = sample(cp, now, now, sequence, active=False, allowed=False, event_stamp=now)
  change(sm, 'aolAxisState', lambda a: setattr(a, 'desiredLateral', True))
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
                       {'name': 'pedalPressed', 'userDisable': True, 'noEntry': True},
                       {'name': 'steerOverride', 'overrideLateral': True}]
  sm.messages['onroadEvents'] = event.as_reader()
  return sm, native


def cold_main_baseline():
  cp, state, _ = baseline()
  feedback = TransportPauseFeedback(cp)
  sm, _ = sample(cp, NOW, NOW, 1, active=False, requested=False, allowed=False)
  assert not feedback.observe(sm, cp, state, NOW, latched=False, intent_producer_session=PRODUCER)
  state.brakePressed = state.steeringPressed = True
  return cp, state, feedback


@pytest.mark.parametrize('epoch', (0, 1))
def test_braked_main_retains_only_intent_until_fresh_native_grant(epoch):
  cp, state, feedback = cold_main_baseline()
  before = leases(feedback)
  arm_ns = NOW + 10_000_000
  for tick in range(1, 101):
    now = NOW + tick * 10_000_000
    sm, native = braked_main_sample(cp, now, tick + 1)
    change(sm, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', epoch))
    assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER, main_arm_ns=arm_ns)
    assert not feedback.failed and not native.lateralAllowed and not feedback.ever_lateral_active
    assert leases(feedback) == before
  state.brakePressed = state.steeringPressed = False
  now += 10_000_000
  sm, _ = braked_main_sample(cp, now, 102)
  change(sm, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', epoch))
  assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER, main_arm_ns=arm_ns)
  recovered, native = sample(cp, now + 10_000_000, now + 10_000_000, 103, event_stamp=now + 10_000_000)
  change(recovered, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', epoch))
  assert not feedback.observe(recovered, cp, state, now + 10_000_000, latched=True, intent_producer_session=PRODUCER, main_arm_ns=arm_ns)
  assert feedback.ever_lateral_active and native.lateralAllowed


@pytest.mark.parametrize('veto', ('permanent', 'temporary', 'invalid_can', 'timeout', 'acc', 'door', 'seatbelt',
  'sensors', 'steering', 'gas', 'regen', 'main_off', 'reverse', 'cancel', 'event_fault', 'initializing',
  'wrong_producer', 'axis_fault', 'expired_axis', 'bad_native', 'missing_native', 'native_heartbeat', 'native_fault'))
@pytest.mark.parametrize('prior_active', (False, True))
def test_pending_braked_main_preserves_independent_vetoes(veto, prior_active):
  cp, state, feedback = baseline() if prior_active else cold_main_baseline()
  state.brakePressed = True
  now = NOW + 10_000_000
  sm, native = braked_main_sample(cp, now, 2)
  fields = {'permanent': 'steerFaultPermanent', 'temporary': 'steerFaultTemporary', 'invalid_can': 'canValid',
            'timeout': 'canTimeout', 'acc': 'accFaulted', 'door': 'doorOpen', 'seatbelt': 'seatbeltUnlatched',
            'sensors': 'vehicleSensorsInvalid', 'steering': 'steeringDisengage', 'gas': 'gasPressed', 'regen': 'regenBraking'}
  if veto in fields:
    setattr(state, fields[veto], veto != 'invalid_can')
  elif veto == 'main_off':
    state.cruiseState.available = False
  elif veto == 'reverse':
    state.gearShifter = 'reverse'
  elif veto == 'cancel':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  elif veto in ('event_fault', 'initializing'):
    change(sm, 'onroadEvents', lambda e: setattr(e[0], 'immediateDisable' if veto == 'event_fault' else 'noEntry', True))
  elif veto == 'axis_fault':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'faultReason', 'critical'))
  elif veto == 'expired_axis':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'validUntilMonoTime', now - 1))
  elif veto == 'bad_native':
    sm.valid['aolSafetyWire'] = False
  elif veto == 'missing_native':
    sm.seen['aolSafetyWire'] = False
  elif veto in ('native_heartbeat', 'native_fault'):
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0],
      **({'heartbeatLost': True} if veto == 'native_heartbeat' else {'faults': 1})),))
  assert feedback.observe(sm, cp, state, now, latched=True, main_arm_ns=now,
                          intent_producer_session='wrong' if veto == 'wrong_producer' else PRODUCER)


def test_eps_brake_release_joins_only_the_previously_verified_pedal_event():
  cp, state, feedback = baseline()
  state.steerFaultTemporary = state.brakePressed = state.steeringPressed = True
  now = NOW + 10_000_000
  sm, _ = braked_main_sample(cp, now, 2)
  change(sm, 'aolAxisState', lambda a: setattr(a, 'desiredLateral', False))
  before = leases(feedback)
  assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
  state.brakePressed = False
  newer, _ = braked_main_sample(cp, now + 10_000_000, 3)
  change(newer, 'aolAxisState', lambda a: setattr(a, 'desiredLateral', False))
  old_event = sm.messages['onroadEvents']
  newer.messages['onroadEvents'] = old_event
  newer.logMonoTime['onroadEvents'] = now
  change(newer, 'aolAxisState', lambda a: setattr(a, 'faultEventMonoTime', now))
  assert not feedback.observe(newer, cp, state, now + 10_000_000, latched=True, intent_producer_session=PRODUCER)
  assert leases(feedback) == before
  # A different pedal event has no receipt-bound physical brake proof.
  different, _ = braked_main_sample(cp, now + 20_000_000, 4)
  change(different, 'aolAxisState', lambda a: setattr(a, 'desiredLateral', False))
  assert feedback.observe(different, cp, state, now + 20_000_000, latched=True, intent_producer_session=PRODUCER)


def test_pending_braked_main_epoch_and_release_timeout_are_not_renewed():
  for changed in ('epoch', 'release_timeout'):
    cp, state, feedback = cold_main_baseline()
    arm = NOW + 10_000_000
    sm, _ = braked_main_sample(cp, arm, 2)
    assert not feedback.observe(sm, cp, state, arm, latched=True, intent_producer_session=PRODUCER, main_arm_ns=arm)
    state.brakePressed = False
    now = arm + (200_000_001 if changed == 'release_timeout' else 10_000_000)
    sm, _ = braked_main_sample(cp, now, 3)
    if changed == 'epoch':
      change(sm, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', 2))
    assert feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER, main_arm_ns=arm)



def stopped_feedback(epoch=0):
  cp, state, feedback = baseline()
  state.standstill = state.brakePressed = True
  state.vEgo = state.vEgoRaw = 0.
  stamp = NOW + 10_000_000
  sm, native = sample(cp, stamp, stamp, 2, active=False, requested=False, allowed=False, event_stamp=stamp)
  change(sm, 'aolAxisState', lambda axis: setattr(axis, 'intentIntegrityEpoch', epoch))
  assert not feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  assert not any(feedback.signature[9:13]) and feedback.ever_lateral_active
  return cp, state, feedback, sm, native, stamp


@pytest.mark.parametrize('epoch', (0, 1))
def test_stopped_feedback_gap_retains_intent_without_renewing_leases(epoch):
  from openpilot.starpilot.aol.runtime import current_axis

  cp, state, feedback, sm, _, stamp = stopped_feedback(epoch)
  before = leases(feedback), feedback.signature, feedback.sequence
  messages = dict(sm.logMonoTime)
  for age in (33_917_864, 50_000_000, 100_000_000, 190_000_000):
    assert current_axis(sm, now_ns=stamp + age) is None
    assert not feedback.observe(sm, cp, state, stamp + age, latched=True, intent_producer_session=PRODUCER)
    assert not feedback.failed
    assert (leases(feedback), feedback.signature, feedback.sequence) == before
    assert sm.logMonoTime == messages
  # A new source and genuine native grant own recovery after movement, never the retained OFF receipt.
  state.standstill = state.brakePressed = False
  state.vEgo = state.vEgoRaw = 15.
  now = stamp + 195_000_000
  fresh, native = sample(cp, now, now, 3, event_stamp=now)
  change(fresh, 'aolAxisState', lambda axis: setattr(axis, 'intentIntegrityEpoch', epoch))
  assert not feedback.observe(fresh, cp, state, now, latched=True, intent_producer_session=PRODUCER)
  assert current_axis(fresh, now_ns=now).lateralActive and native.lateralAllowed


def test_stopped_feedback_gap_deadline_cannot_be_extended_by_new_native_receipts():
  cp, state, feedback, old, _, stamp = stopped_feedback()
  now = stamp + 200_000_001
  fresh, _ = sample(cp, now, now, 3, active=False, requested=False, allowed=False, event_stamp=now)
  fresh.messages['aolAxisState'] = old.messages['aolAxisState']
  fresh.logMonoTime['aolAxisState'] = stamp
  assert feedback.observe(fresh, cp, state, now, latched=True, intent_producer_session=PRODUCER)
  assert feedback.failed


@pytest.mark.parametrize('fault', ('moving', 'brake_released', 'main_off', 'acc_on', 'gas', 'regen', 'invalid_can',
  'timeout', 'temporary_eps', 'permanent_eps', 'sensors', 'door', 'belt', 'disengage', 'cancel', 'park',
  'wrong_producer', 'wrong_epoch', 'changed_source', 'axis_invalid', 'axis_unqualified', 'native_ack',
  'native_stale', 'native_missing', 'native_fault', 'heartbeat', 'wrong_native_session', 'wrong_inventory',
  'lateral_requested', 'lateral_allowed', 'long_requested', 'long_allowed', 'hard_event', 'soft_event',
  'other_event', 'stale_event', 'invalid_event', 'no_prior_active', 'wrong_cp'))
def test_stopped_feedback_retention_preserves_independent_vetoes(fault):
  cp, state, feedback, sm, native, stamp = stopped_feedback()
  now = stamp + 33_917_864
  physical = {'moving': ('standstill', False), 'brake_released': ('brakePressed', False),
    'gas': ('gasPressed', True), 'regen': ('regenBraking', True), 'invalid_can': ('canValid', False),
    'timeout': ('canTimeout', True), 'temporary_eps': ('steerFaultTemporary', True),
    'permanent_eps': ('steerFaultPermanent', True), 'sensors': ('vehicleSensorsInvalid', True),
    'door': ('doorOpen', True), 'belt': ('seatbeltUnlatched', True), 'disengage': ('steeringDisengage', True)}
  if fault in physical:
    setattr(state, *physical[fault])
  elif fault == 'main_off':
    state.cruiseState.available = False
  elif fault == 'acc_on':
    state.cruiseState.enabled = True
  elif fault == 'cancel':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  elif fault == 'park':
    state.gearShifter = 'park'
  elif fault in ('wrong_epoch', 'changed_source', 'axis_unqualified', 'native_ack'):
    field, value = {'wrong_epoch': ('intentIntegrityEpoch', 2), 'changed_source': ('sourceCarStateMonoTime', stamp - 1),
                    'axis_unqualified': ('qualified', False), 'native_ack': ('nativeAcknowledged', False)}[fault]
    change(sm, 'aolAxisState', lambda axis: setattr(axis, field, value))
  elif fault == 'axis_invalid':
    sm.valid['aolAxisState'] = False
  elif fault == 'native_stale':
    native_change(sm, native, validUntilMonoTime=now - 1)
  elif fault == 'native_missing':
    sm.seen['aolSafetyWire'] = False
  elif fault in ('native_fault', 'heartbeat'):
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0],
      **({'faults': 1} if fault == 'native_fault' else {'heartbeatLost': True})),))
  elif fault == 'wrong_native_session':
    native_change(sm, native, axisSessionId='changed')
  elif fault == 'wrong_inventory':
    native_change(sm, native, pandaSerial='changed', pandaInventory=(replace(native.pandaInventory[0], hardwareSerial='changed'),))
  elif fault in ('lateral_requested', 'lateral_allowed', 'long_requested', 'long_allowed'):
    field = {'lateral_requested': 'requestedLateral', 'lateral_allowed': 'lateralAllowed',
             'long_requested': 'requestedLongitudinal', 'long_allowed': 'longitudinalAllowed'}[fault]
    native_change(sm, native, **{field: True})
  elif fault in ('hard_event', 'soft_event', 'other_event'):
    change(sm, 'onroadEvents', lambda events: setattr(events[0],
      'immediateDisable' if fault == 'hard_event' else 'softDisable' if fault == 'soft_event' else 'name',
      'buttonCancel' if fault == 'other_event' else True))
  elif fault == 'stale_event':
    sm.logMonoTime['onroadEvents'] = NOW - 2_000_000_000
  elif fault == 'invalid_event':
    sm.valid['onroadEvents'] = False
  elif fault == 'no_prior_active':
    feedback.ever_lateral_active = False
  elif fault == 'wrong_cp':
    cp.carFingerprint = CAR.SUBARU_OUTBACK_2023
  assert feedback.observe(sm, cp, state, now, latched=True,
                          intent_producer_session='changed' if fault == 'wrong_producer' else PRODUCER)


@pytest.mark.parametrize('epoch', (0, 1))
def test_new_main_after_prior_grant_waits_for_brake_without_renewing_leases(epoch):
  cp, state, feedback = baseline()
  assert feedback.ever_lateral_active and feedback.subaru_lateral_grant_ns == NOW
  state.brakePressed = True
  off = NOW + 10_000_000
  sm, _ = sample(cp, off, off, 2, active=False, requested=False, allowed=False, event_stamp=off)
  assert not feedback.observe(sm, cp, state, off, latched=False, intent_producer_session=PRODUCER)
  before = leases(feedback)
  arm = off + 10_000_000
  for tick in range(100):
    now = arm + tick * 10_000_000
    sm, native = braked_main_sample(cp, now, tick + 3)
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'intentIntegrityEpoch', epoch))
    assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER, main_arm_ns=arm)
    assert not feedback.failed and not native.lateralAllowed and leases(feedback) == before
    assert feedback.subaru_lateral_grant_ns == NOW
  state.brakePressed = False
  now += 10_000_000
  sm, native = sample(cp, now, now, 103, event_stamp=now)
  change(sm, 'aolAxisState', lambda axis: setattr(axis, 'intentIntegrityEpoch', epoch))
  assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER, main_arm_ns=arm)
  assert native.lateralAllowed and feedback.subaru_lateral_grant_ns == now


@pytest.mark.parametrize('arm_offset', (-10_000_000, 0))
def test_braked_main_cannot_reinterpret_granted_gesture_as_first_grant(arm_offset):
  from openpilot.starpilot.car.subaru.aol import retain_braked_main_intent

  cp, state, feedback = baseline()
  state.brakePressed = True
  now = NOW + 10_000_000
  sm, _ = braked_main_sample(cp, now, 2)
  before = leases(feedback)
  assert not retain_braked_main_intent(feedback, sm, cp, state, sm['aolAxisState'], now,
                                      arm_ns=NOW + arm_offset, producer=PRODUCER)
  assert feedback.subaru_braked_main is None and leases(feedback) == before
