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


@pytest.mark.parametrize('fault', ('moving', 'brake', 'main', 'acc', 'gas', 'regen', 'can', 'timeout',
  'eps', 'permanent', 'door', 'belt', 'sensors', 'driver_off', 'cancel', 'reverse', 'epoch', 'producer',
  'integrity', 'drain', 'future_pair', 'queue', 'critical', 'active_lat', 'desired_lat', 'active_long',
  'desired_long', 'enabled', 'authority_lost', 'new_state', 'off', 'settings', 'pause', 'raw_epoch',
  'model', 'calibration', 'dm', 'native_fault', 'native_stale', 'native_request', 'native_allow',
  'native_long', 'native_inventory', 'native_session', 'hard_event', 'user_off', 'no_history'))
def test_stopped_source_independent_vetoes_remain_authoritative(fault):
  sd, state, intent, stamp, _, native = source_case()
  physical = {'moving': ('standstill', False), 'brake': ('brakePressed', False), 'gas': ('gasPressed', True),
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
    feedback.ever_lateral_active = False
  elif fault == 'other_car':
    cp.carFingerprint = 'SUBARU_OUTBACK'
  elif fault != 'off':
    raise AssertionError(fault)
  assert not retain_paused_restriction(feedback, sm, cp, state, sm['aolAxisState'], stamp, stamp,
    latched=fault != 'off', producer=PRODUCER)
