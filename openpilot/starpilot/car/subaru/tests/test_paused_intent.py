"""Inactive intent cannot renew a source, an output deadline, or native authority."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from openpilot.cereal import messaging
from openpilot.starpilot.aol.runtime import AxisDecision
from openpilot.starpilot.aol.wire import IntentState
from openpilot.starpilot.aol.tests.test_subaru_temporary_eps import stopped_feedback, leases
from openpilot.starpilot.tests.test_ford_reverse_intent import PRODUCER, SESSION, change, native_change, sample
from openpilot.starpilot.car.subaru.paused_intent import (
  record_stopped_intent, retain_stopped_source, retain_stopped_source_feedback,
)


def source_case():
  cp, state, feedback, sm, native, stamp = stopped_feedback()
  for name in ('modelV2', 'extrinsicsCalibration', 'driverMonitoringState'):
    event = messaging.new_message(name, valid=True, logMonoTime=stamp)
    if name == 'extrinsicsCalibration':
      event.extrinsicsCalibration.calStatus = 'calibrated'
    sm.messages[name] = event.as_reader()
    sm.logMonoTime[name] = stamp
    sm.seen[name] = sm.valid[name] = sm.alive[name] = True
  sm.all_checks = lambda names: all(sm.seen[n] and sm.valid[n] and sm.alive[n] for n in names)
  intent = IntentState(PRODUCER, 2, stamp, stamp, stamp + 200_000_000, True, False, False, True, True)
  companion = SimpleNamespace(session=PRODUCER, integrity_epoch=0, last_drain_ok=True,
    latest=(stamp, True, intent), raw_projections={stamp: (intent, 0, PRODUCER)})
  sd = SimpleNamespace(CP=cp, sm=sm, aol_session_id=SESSION, aol_ever_active=True,
    aol_authority_lost=False, enabled=False, active=False, aol_fault_reason='none', aol_last_intent=intent,
    aol_intent_companion=companion, aol_axis_decision=AxisDecision(native_acknowledged=True),
    events=SimpleNamespace(to_msg=lambda: sm['onroadEvents'], contains=lambda _kind: False))
  record_stopped_intent(sd, state, sm.messages['aolAxisState'], stamp)
  assert sd.aol_stopped_intent_proof is not None
  return sd, state, intent, stamp, feedback, native


def test_stopped_source_retains_original_intent_only_and_never_reanchors():
  sd, state, intent, stamp, feedback, _ = source_case()
  proof = sd.aol_stopped_intent_proof
  before = leases(feedback), feedback.signature, intent.validUntilMonoTime, sd.aol_axis_decision
  for age in (30_000_001, 35_000_000, 100_000_000, 190_000_000):
    assert retain_stopped_source(sd, state, intent, stamp, stamp + age)
    assert sd.aol_stopped_intent_proof is proof
    assert (leases(feedback), feedback.signature, intent.validUntilMonoTime, sd.aol_axis_decision) == before
  assert not retain_stopped_source(sd, state, intent, stamp, stamp + 200_000_001)
  assert not retain_stopped_source(sd, state, intent, stamp, stamp + 30_000_000)


@pytest.mark.parametrize('fault', ('moving', 'wheel_speed', 'main', 'acc', 'gas', 'regen', 'can', 'timeout',
  'eps', 'permanent', 'door', 'belt', 'sensors', 'driver_off', 'cancel', 'reverse', 'epoch', 'producer',
  'integrity', 'drain', 'future_pair', 'queue', 'critical', 'active_lat', 'desired_lat', 'active_long',
  'desired_long', 'enabled', 'authority_lost', 'new_state', 'off', 'settings', 'pause', 'raw_epoch',
  'model', 'calibration', 'dm', 'native_fault', 'native_stale', 'native_request', 'native_allow',
  'native_long', 'native_inventory', 'native_session', 'hard_event', 'user_off', 'no_history'))
def test_stopped_source_independent_vetoes_remain_authoritative(fault):
  sd, state, intent, stamp, _, native = source_case()
  physical = {'moving': ('standstill', False), 'wheel_speed': ('vEgoRaw', 0.1), 'gas': ('gasPressed', True),
    'regen': ('regenBraking', True), 'can': ('canValid', False), 'timeout': ('canTimeout', True),
    'eps': ('steerFaultTemporary', True), 'permanent': ('steerFaultPermanent', True), 'door': ('doorOpen', True),
    'belt': ('seatbeltUnlatched', True), 'sensors': ('vehicleSensorsInvalid', True), 'driver_off': ('steeringDisengage', True)}
  if fault in physical:
    setattr(state, *physical[fault])
  elif fault in ('main', 'acc'):
    setattr(state.cruiseState, 'available' if fault == 'main' else 'enabled', fault == 'acc')
  elif fault == 'cancel':
    state.buttonEvents = [{'type': 'cancel', 'pressed': True}]
  elif fault == 'reverse':
    state.gearShifter = 'reverse'
  elif fault in ('epoch', 'producer', 'integrity'):
    setattr(sd.aol_intent_companion, {'epoch': 'integrity_epoch', 'producer': 'session', 'integrity': 'last_drain_ok'}[fault],
      {'epoch': 1, 'producer': 'other', 'integrity': False}[fault])
  elif fault in ('drain', 'future_pair', 'queue'):
    setattr(sd, {'drain': 'aol_step_intent_drain_ok', 'future_pair': 'aol_selected_pair_future',
                 'queue': 'car_state_queue_invalid'}[fault], fault != 'drain')
  elif fault == 'critical':
    sd.aol_fault_reason = 'critical'
  elif fault in ('active_lat', 'desired_lat', 'active_long', 'desired_long'):
    field = {'active_lat': 'lateral_active', 'desired_lat': 'desired_lateral',
             'active_long': 'longitudinal_active', 'desired_long': 'desired_longitudinal'}[fault]
    sd.aol_axis_decision = replace(sd.aol_axis_decision, **{field: True})
  elif fault in ('enabled', 'authority_lost'):
    setattr(sd, 'enabled' if fault == 'enabled' else 'aol_authority_lost', True)
  elif fault == 'new_state':
    state = state.as_reader().as_builder()
  elif fault in ('off', 'settings', 'pause'):
    raw = replace(intent, **{'off': {'allowedLatch': False}, 'settings': {'settingsQualified': False},
                            'pause': {'pauseLateral': True}}[fault])
    sd.aol_intent_companion.latest = (stamp, True, raw)
    sd.aol_intent_companion.raw_projections[stamp] = (raw, 0, PRODUCER)
  elif fault == 'raw_epoch':
    sd.aol_intent_companion.raw_projections[stamp] = (intent, 1, PRODUCER)
  elif fault == 'model':
    sd.sm.valid['modelV2'] = False
  elif fault == 'calibration':
    change(sd.sm, 'extrinsicsCalibration', lambda c: setattr(c, 'calStatus', 'uncalibrated'))
  elif fault == 'dm':
    change(sd.sm, 'driverMonitoringState', lambda d: setattr(d, 'alwaysOnLockout', True))
  elif fault == 'native_fault':
    native_change(sd.sm, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  elif fault == 'native_stale':
    native_change(sd.sm, native, validUntilMonoTime=stamp)
  elif fault in ('native_request', 'native_allow', 'native_long'):
    native_change(sd.sm, native, **{{'native_request': 'requestedLateral', 'native_allow': 'lateralAllowed',
                                   'native_long': 'requestedLongitudinal'}[fault]: True})
  elif fault == 'native_inventory':
    native_change(sd.sm, native, pandaSerial='other', pandaInventory=(replace(native.pandaInventory[0], hardwareSerial='other'),))
  elif fault == 'native_session':
    native_change(sd.sm, native, axisSessionId='other')
  elif fault in ('hard_event', 'user_off'):
    change(sd.sm, 'onroadEvents', lambda e: setattr(e[0], 'immediateDisable' if fault == 'hard_event' else 'name',
      True if fault == 'hard_event' else 'buttonCancel'))
  elif fault == 'no_history':
    sd.aol_stopped_intent_proof = None
  else:
    raise AssertionError(fault)
  assert not retain_stopped_source(sd, state, intent, stamp, stamp + 35_000_000)


def test_stopped_source_zero_feedback_keeps_fixed_original_source_and_native_deadlines():
  cp, state, feedback, _, _, stamp = stopped_feedback()
  before = leases(feedback), feedback.signature, feedback.sequence
  now = stamp + 35_000_000
  sm, native = sample(cp, now, now, 3, active=False, requested=False, allowed=False, event_stamp=now)
  change(sm, 'aolAxisState', lambda a: (setattr(a, 'sourceCarStateMonoTime', 0),
    setattr(a, 'nativeAcknowledged', False), setattr(a, 'faultReason', 'availabilityPause'), setattr(a, 'intentIntegrityEpoch', 0)))
  change(sm, 'onroadEvents', lambda e: (setattr(e[0], 'name', 'commIssue'), setattr(e[0], 'userDisable', False), setattr(e[0], 'softDisable', True)))
  assert retain_stopped_source_feedback(feedback, sm, cp, state, sm['aolAxisState'], now, now,
    latched=True, producer=PRODUCER)
  assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
  assert (leases(feedback), feedback.signature, feedback.sequence) == before
  late = stamp + 200_000_001
  assert not retain_stopped_source_feedback(feedback, sm, cp, state, sm['aolAxisState'], now, late,
    latched=True, producer=PRODUCER)
  assert not native.requestedLateral and not native.lateralAllowed


def paused_case():
  cp, state, feedback, sm, native, stamp = stopped_feedback()
  state.steerFaultTemporary = True
  return cp, state, feedback, sm, native, stamp


def test_paused_restriction_keeps_only_intent_without_renewing_healthy_baselines():
  from openpilot.starpilot.car.subaru.paused_intent import retain_paused_restriction
  cp, state, feedback, sm, _, stamp = paused_case()
  before = leases(feedback), feedback.healthy_feedback_ns, feedback.healthy_native_ns
  assert retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], stamp, stamp,
    latched=True, producer=PRODUCER)
  assert not feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  assert (leases(feedback), feedback.healthy_feedback_ns, feedback.healthy_native_ns) == before
  assert not retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], stamp, stamp + 30_000_001,
    latched=True, producer=PRODUCER)


@pytest.mark.parametrize('fault', ('permanent', 'can', 'timeout', 'main', 'acc', 'cancel', 'reverse', 'gas',
  'regen', 'door', 'belt', 'sensors', 'driver_off', 'long', 'lat', 'ack', 'epoch', 'producer', 'critical',
  'native_request', 'native_allow', 'native_long', 'native_fault', 'native_stale', 'native_inventory',
  'hard_event', 'user_off', 'soft_event', 'failed', 'no_history', 'off', 'other_car'))
def test_paused_restriction_preserves_independent_fault_user_and_authority_vetoes(fault):
  from openpilot.starpilot.car.subaru.paused_intent import retain_paused_restriction
  cp, state, feedback, sm, native, stamp = paused_case()
  physical = {'permanent': ('steerFaultPermanent', True), 'can': ('canValid', False), 'timeout': ('canTimeout', True),
    'gas': ('gasPressed', True), 'regen': ('regenBraking', True), 'door': ('doorOpen', True),
    'belt': ('seatbeltUnlatched', True), 'sensors': ('vehicleSensorsInvalid', True), 'driver_off': ('steeringDisengage', True)}
  if fault in physical:
    setattr(state, *physical[fault])
  elif fault in ('main', 'acc'):
    setattr(state.cruiseState, 'available' if fault == 'main' else 'enabled', fault == 'acc')
  elif fault == 'cancel':
    state.buttonEvents = [{'type': 'cancel', 'pressed': True}]
  elif fault == 'reverse':
    state.gearShifter = 'reverse'
  elif fault in ('long', 'lat', 'ack', 'epoch', 'producer', 'critical'):
    field, value = {'long': ('desiredLongitudinal', True), 'lat': ('lateralActive', True), 'ack': ('nativeAcknowledged', False),
      'epoch': ('intentIntegrityEpoch', 1), 'producer': ('intentProducerSessionId', 'other'), 'critical': ('faultReason', 'critical')}[fault]
    change(sm, 'aolAxisState', lambda a: setattr(a, field, value))
  elif fault in ('native_request', 'native_allow', 'native_long'):
    native_change(sm, native, **{{'native_request': 'requestedLateral', 'native_allow': 'lateralAllowed',
      'native_long': 'longitudinalAllowed'}[fault]: True})
  elif fault == 'native_fault':
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  elif fault == 'native_stale':
    native_change(sm, native, validUntilMonoTime=stamp - 1)
  elif fault == 'native_inventory':
    native_change(sm, native, pandaSerial='other', pandaInventory=(replace(native.pandaInventory[0], hardwareSerial='other'),))
  elif fault in ('hard_event', 'user_off', 'soft_event'):
    change(sm, 'onroadEvents', lambda e: (setattr(e[0], 'name', 'controlsMismatch' if fault == 'hard_event' else
      'buttonCancel' if fault == 'user_off' else 'commIssue'), setattr(e[0],
      'immediateDisable' if fault == 'hard_event' else 'userDisable' if fault == 'user_off' else 'softDisable', True)))
  elif fault == 'failed':
    feedback.failed = True
  elif fault == 'no_history':
    feedback.healthy_feedback_ns = 0
  elif fault == 'other_car':
    cp.carFingerprint = 'SUBARU_OUTBACK'
  elif fault != 'off':
    raise AssertionError(fault)
  assert not retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], stamp, stamp,
    latched=fault != 'off', producer=PRODUCER)


def recovery_case(order='event-first'):
  cp, state, feedback, _, _, stamp = stopped_feedback()
  feedback.temporary_eps_event_ns = stamp
  feedback.pending_event = stamp + 10_000_000
  state.gasPressed = True
  now = stamp + 15_000_000
  event_stamp = stamp if order == 'axis-first' else feedback.pending_event
  axis_event = stamp if order == 'event-first' else feedback.pending_event
  sm, native = sample(cp, now, now, 3, active=False, requested=False, allowed=False, event_stamp=event_stamp)
  change(sm, 'aolAxisState', lambda a: (setattr(a, 'faultEventMonoTime', axis_event), setattr(a, 'intentIntegrityEpoch', 0)))
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
    {'name': 'steerTempUnavailableSilent', 'warning': True}, {'name': 'gasPressedOverride', 'overrideLongitudinal': True}]
  sm.messages['onroadEvents'] = event.as_reader()
  return cp, state, feedback, sm, native, now


@pytest.mark.parametrize('order', ('event-first', 'axis-first', 'matched'))
def test_cleared_subaru_restriction_finishes_pending_join_without_renewing_authority(order):
  from openpilot.starpilot.car.subaru.paused_intent import retain_pending_restriction_recovery
  cp, state, feedback, sm, _, now = recovery_case(order)
  before = leases(feedback), feedback.signature, feedback.healthy_feedback_ns, feedback.healthy_native_ns
  stamp = sm.logMonoTime['aolAxisState']
  assert retain_pending_restriction_recovery(feedback, sm, cp, state, sm['aolAxisState'], stamp, now,
    latched=True, producer=PRODUCER)
  assert feedback.pending_event == feedback.temporary_eps_event_ns == 0
  assert (leases(feedback), feedback.signature, feedback.healthy_feedback_ns, feedback.healthy_native_ns) == before
  assert not sm['aolAxisState'].lateralActive and not sm['aolAxisState'].desiredLongitudinal


@pytest.mark.parametrize('fault', ('permanent', 'temporary', 'can', 'timeout', 'main', 'acc', 'cancel', 'reverse',
  'regen', 'door', 'belt', 'sensors', 'driver_off', 'long', 'lat', 'ack', 'epoch', 'producer', 'session', 'critical',
  'native_request', 'native_allow', 'native_long', 'native_fault', 'native_stale', 'native_inventory',
  'hard_event', 'user_off', 'soft_event', 'failed', 'no_history', 'off', 'new_event', 'no_warning',
  'no_pending', 'no_restriction', 'stale_source', 'future_source'))
def test_pending_subaru_recovery_does_not_bypass_independent_vetoes(fault):
  from openpilot.starpilot.car.subaru.paused_intent import retain_pending_restriction_recovery
  cp, state, feedback, sm, native, now = recovery_case()
  stamp = sm.logMonoTime['aolAxisState']
  physical = {'permanent': ('steerFaultPermanent', True), 'temporary': ('steerFaultTemporary', True),
    'can': ('canValid', False), 'timeout': ('canTimeout', True), 'regen': ('regenBraking', True), 'door': ('doorOpen', True),
    'belt': ('seatbeltUnlatched', True), 'sensors': ('vehicleSensorsInvalid', True), 'driver_off': ('steeringDisengage', True)}
  if fault in physical:
    setattr(state, *physical[fault])
  elif fault in ('main', 'acc'):
    setattr(state.cruiseState, 'available' if fault == 'main' else 'enabled', fault == 'acc')
  elif fault == 'cancel':
    state.buttonEvents = [{'type': 'cancel', 'pressed': True}]
  elif fault == 'reverse':
    state.gearShifter = 'reverse'
  elif fault in ('long', 'lat', 'ack', 'epoch', 'producer', 'session', 'critical', 'stale_source', 'future_source'):
    field, value = {'long': ('desiredLongitudinal', True), 'lat': ('lateralActive', True), 'ack': ('nativeAcknowledged', False),
      'epoch': ('intentIntegrityEpoch', 1), 'producer': ('intentProducerSessionId', 'other'), 'session': ('faultSessionId', 'other'),
      'critical': ('faultReason', 'critical'), 'stale_source': ('sourceCarStateMonoTime', now - 30_000_001),
      'future_source': ('sourceCarStateMonoTime', now + 1)}[fault]
    change(sm, 'aolAxisState', lambda a: setattr(a, field, value))
  elif fault in ('native_request', 'native_allow', 'native_long'):
    native_change(sm, native, **{{'native_request': 'requestedLateral', 'native_allow': 'lateralAllowed',
      'native_long': 'longitudinalAllowed'}[fault]: True})
  elif fault == 'native_fault':
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  elif fault == 'native_stale':
    native_change(sm, native, validUntilMonoTime=now - 1)
  elif fault == 'native_inventory':
    native_change(sm, native, pandaSerial='other', pandaInventory=(replace(native.pandaInventory[0], hardwareSerial='other'),))
  elif fault in ('hard_event', 'user_off', 'soft_event'):
    change(sm, 'onroadEvents', lambda e: (setattr(e[0], 'name', 'controlsMismatch' if fault == 'hard_event' else
      'buttonCancel' if fault == 'user_off' else 'commIssue'), setattr(e[0],
      'immediateDisable' if fault == 'hard_event' else 'userDisable' if fault == 'user_off' else 'softDisable', True)))
  elif fault == 'failed':
    feedback.failed = True
  elif fault == 'no_history':
    feedback.healthy_feedback_ns = 0
  elif fault == 'new_event':
    sm.logMonoTime['onroadEvents'] += 1
  elif fault == 'no_warning':
    change(sm, 'onroadEvents', lambda e: setattr(e[1], 'name', 'steerOverride'))
  elif fault == 'no_pending':
    feedback.pending_event = 0
  elif fault == 'no_restriction':
    feedback.temporary_eps_event_ns = 0
  elif fault != 'off':
    raise AssertionError(fault)
  assert not retain_pending_restriction_recovery(feedback, sm, cp, state, sm['aolAxisState'], stamp, now,
    latched=fault != 'off', producer=PRODUCER)


@pytest.mark.parametrize('axis_first', (False, True))
@pytest.mark.parametrize('native_granted', (False, True))
def test_paused_subaru_event_join_tracks_reason_without_reanchoring_deadlines(axis_first, native_granted):
  from openpilot.starpilot.car.subaru.paused_intent import retain_paused_restriction
  cp, state, feedback, sm, native, stamp = paused_case()
  if native_granted:
    native_change(sm, native, requestedLateral=True, lateralAllowed=True)
  before = leases(feedback), feedback.signature
  assert retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], stamp, stamp,
    latched=True, producer=PRODUCER)
  assert feedback.temporary_eps_event_ns == stamp
  for age in (10_000_000, 12_000_000):
    if axis_first:
      fresh, fresh_native = sample(cp, stamp + age, stamp + age, 3, active=False,
        requested=native_granted, allowed=native_granted, event_stamp=stamp)
      change(fresh, 'aolAxisState', lambda a, _age=age: (setattr(a, 'faultEventMonoTime', stamp + _age), setattr(a, 'intentIntegrityEpoch', 0)))
      sm, native = fresh, fresh_native
    else:
      event = messaging.new_message('onroadEvents', 2, valid=True, logMonoTime=stamp + age)
      event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True}, {'name': 'steerTempUnavailableSilent', 'warning': True}]
      sm.messages['onroadEvents'] = event.as_reader()
      sm.logMonoTime['onroadEvents'] = stamp + age
    assert retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], sm.logMonoTime['aolAxisState'], stamp + age + 1_000_000,
      latched=True, producer=PRODUCER)
    assert feedback.pending_event == stamp + 10_000_000
    assert (leases(feedback), feedback.signature) == before
  now = stamp + 20_000_000
  matched, _ = sample(cp, now, now, 4, active=False, requested=native_granted, allowed=native_granted, event_stamp=now)
  change(matched, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', 0))
  assert retain_paused_restriction(feedback, matched, cp, state, matched['aolAxisState'], now, now,
    latched=True, producer=PRODUCER)
  assert feedback.pending_event == 0 and feedback.temporary_eps_event_ns == now
  assert (leases(feedback), feedback.signature) == before


def test_paused_subaru_brake_release_joins_only_its_accepted_eps_event():
  from openpilot.starpilot.car.subaru.paused_intent import retain_paused_restriction
  cp, state, feedback, sm, _, stamp = paused_case()
  assert retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], stamp, stamp,
    latched=True, producer=PRODUCER)
  assert feedback.subaru_eps_brake_event_ns == stamp
  state.brakePressed = False
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True}, {'name': 'pedalPressed', 'userDisable': True},
    {'name': 'steerTempUnavailableSilent', 'warning': True}]
  sm.messages['onroadEvents'] = event.as_reader()
  assert retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], stamp, stamp,
    latched=True, producer=PRODUCER)
  feedback.subaru_eps_brake_event_ns = 0
  assert not retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], stamp, stamp,
    latched=True, producer=PRODUCER)


def test_recovery_brake_release_preserves_only_a_known_pending_eps_brake():
  from openpilot.starpilot.car.subaru.paused_intent import retain_pending_restriction_recovery
  cp, state, feedback, sm, _, now = recovery_case()
  state.brakePressed = False
  feedback.subaru_eps_brake_event_ns = feedback.temporary_eps_event_ns
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True}, {'name': 'steerTempUnavailableSilent', 'warning': True},
    {'name': 'gasPressedOverride', 'overrideLongitudinal': True}, {'name': 'pedalPressed', 'userDisable': True}]
  sm.messages['onroadEvents'] = event.as_reader()
  before = leases(feedback)
  assert retain_pending_restriction_recovery(feedback, sm, cp, state, sm['aolAxisState'], sm.logMonoTime['aolAxisState'], now,
    latched=True, producer=PRODUCER)
  assert leases(feedback) == before


@pytest.mark.parametrize('previous_role', ('off', 'waiting', 'active'))
@pytest.mark.parametrize('current_role', ('off', 'waiting', 'active'))
@pytest.mark.parametrize('native_granted', (False, True))
@pytest.mark.parametrize('ever_active', (False, True))
@pytest.mark.parametrize('steering_pressed', (False, True))
@pytest.mark.parametrize('brake_pressed', (False, True))
def test_subaru_temporary_intent_depends_on_accepted_proof_not_actuation_phase(
    previous_role, current_role, native_granted, ever_active, steering_pressed, brake_pressed):
  from openpilot.starpilot.car.subaru.paused_intent import retain_paused_restriction
  cp, state, feedback, sm, native, stamp = paused_case()
  state.steeringPressed, state.brakePressed = steering_pressed, brake_pressed
  feedback.ever_lateral_active = ever_active
  previous = list(feedback.signature)
  previous[9], previous[11] = previous_role == 'active', previous_role != 'off'
  feedback.signature = tuple(previous)
  change(sm, 'aolAxisState', lambda a: (setattr(a, 'lateralActive', current_role == 'active'),
    setattr(a, 'desiredLateral', current_role != 'off'), setattr(a, 'mode', 'lateralOnly' if current_role == 'active' else 'off')))
  native_change(sm, native, requestedLateral=native_granted, lateralAllowed=native_granted)
  before = leases(feedback), feedback.signature, sm['aolAxisState'].to_dict()
  retained = retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], stamp, stamp,
    latched=True, producer=PRODUCER)
  assert retained == (current_role != 'active' or native_granted)
  assert (leases(feedback), feedback.signature, sm['aolAxisState'].to_dict()) == before


@pytest.mark.parametrize('brake_pressed', (False, True))
def test_stopped_source_proof_depends_on_zero_wheel_speed_not_brake(brake_pressed):
  sd, state, intent, stamp, feedback, _ = source_case()
  state.brakePressed = brake_pressed
  record_stopped_intent(sd, state, sd.sm.messages['aolAxisState'], stamp)
  proof = sd.aol_stopped_intent_proof
  assert proof is not None
  before = leases(feedback), feedback.signature, intent.validUntilMonoTime, sd.aol_axis_decision
  for age in (30_000_001, 35_000_000, 100_000_000, 190_000_000):
    assert retain_stopped_source(sd, state, intent, stamp, stamp + age)
    assert sd.aol_stopped_intent_proof is proof
    assert (leases(feedback), feedback.signature, intent.validUntilMonoTime, sd.aol_axis_decision) == before
  assert not retain_stopped_source(sd, state, intent, stamp, stamp + 200_000_001)
