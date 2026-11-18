"""Civic cf bookmark: short EPS pulse clears before the next native receipt."""
from types import SimpleNamespace as NS
from dataclasses import replace
from opendbc.car import structs

import pytest

from openpilot.starpilot.aol.tests.test_honda_route_regressions import baseline, eps_sample, NOW, PRODUCER, change, native_change
from openpilot.starpilot.aol.runtime import decide_axes


def recovery_case():
  cp, state, feedback, _, _ = baseline()
  state.steerFaultTemporary = state.steeringPressed = True
  stamp = NOW + 10_000_000
  sm, native = eps_sample(cp, stamp, withdrawn=True)
  assert not feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  state.steerFaultTemporary = False
  return cp, state, feedback, sm, native, stamp


def observe(case, now, **kwargs):
  cp, state, feedback, sm, _, _ = case
  return feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER, **kwargs)


def output(state, native):
  return decide_axes(standard_lateral=False, standard_longitudinal=False,
    intent=NS(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False), native=native, car_state=state,
    initialized=True, model_ready=True, no_entry=False, immediate_disable=False, dm_lockout=False, pause_brake_mps=1.)


def test_clear_before_native_receipt_retains_intent_without_grant_or_lease_renewal():
  case = recovery_case()
  cp, state, feedback, sm, native, stamp = case
  leases = feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns
  assert not observe(case, stamp + 10_000_000)
  assert not feedback.failed and not output(state, native).lateral_active
  assert leases == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns)
  # Next host publication requests LAT, but cannot activate before native recovers.
  change(sm, 'aolAxisState', lambda a: setattr(a, 'desiredLateral', True))
  change(sm, 'aolAxisState', lambda a: setattr(a, 'sequence', a.sequence + 1))
  assert not observe(case, stamp + 20_000_000)
  assert not output(state, native).lateral_active
  assert leases == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns)
  # Normal fresh permission ends the join. No alternate actuator path exists.
  from openpilot.starpilot.tests.test_ford_reverse_intent import sample
  recovered, grant = sample(cp, stamp + 25_000_000, stamp + 25_000_000, 4, event_stamp=stamp)
  change(recovered, 'onroadEvents', lambda e: setattr(e[0], 'userDisable', False))
  assert not feedback.observe(recovered, cp, state, stamp + 25_000_000, latched=True, intent_producer_session=PRODUCER)
  assert output(state, grant).lateral_active


@pytest.mark.parametrize('fault', ['cancel', 'brake', 'regen', 'permanent', 'can', 'timeout', 'door', 'belt',
  'sensor', 'disengage', 'main', 'reverse', 'user_event', 'hard_event', 'wrong_session', 'wrong_producer',
  'wrong_epoch', 'wrong_profile', 'native_stale', 'native_missing', 'native_fault', 'native_rx',
  'native_heartbeat', 'native_inventory', 'native_long', 'axis_stale', 'no_eps_source', 'expired_source'])
def test_recovery_join_does_not_bypass_independent_vetoes(fault):
  case = recovery_case()
  cp, state, feedback, sm, native, stamp = case
  fields = {'brake': ('brakePressed', True), 'regen': ('regenBraking', True), 'permanent': ('steerFaultPermanent', True),
    'can': ('canValid', False), 'timeout': ('canTimeout', True), 'door': ('doorOpen', True),
    'belt': ('seatbeltUnlatched', True), 'sensor': ('vehicleSensorsInvalid', True), 'disengage': ('steeringDisengage', True)}
  if fault in fields:
    setattr(state, *fields[fault])
  elif fault == 'cancel':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  elif fault == 'main':
    state.cruiseState.available = False
  elif fault == 'reverse':
    state.gearShifter = 'reverse'
  elif fault in ('user_event', 'hard_event'):
    change(sm, 'onroadEvents', lambda e: setattr(e[0], 'userDisable' if fault == 'user_event' else 'immediateDisable', True))
  elif fault in ('wrong_session', 'wrong_producer', 'wrong_epoch'):
    field = {'wrong_session':'sessionId', 'wrong_producer':'intentProducerSessionId', 'wrong_epoch':'intentIntegrityEpoch'}[fault]
    change(sm, 'aolAxisState', lambda a: setattr(a, field, 99 if fault == 'wrong_epoch' else 'different'))
  elif fault == 'wrong_profile':
    cp.alternativeExperience = 0
  elif fault == 'native_stale':
    native_change(sm, native, validUntilMonoTime=stamp)
  elif fault == 'native_missing':
    sm.seen['aolSafetyWire'] = False
  elif fault in ('native_fault', 'native_rx', 'native_heartbeat', 'native_inventory'):
    field = {'native_fault':'faults', 'native_rx':'safetyRxChecksInvalid', 'native_heartbeat':'heartbeatLost',
             'native_inventory':'hardwareSerial'}[fault]
    value = 'different' if fault == 'native_inventory' else 1 if fault == 'native_fault' else True
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], **{field:value}),))
  elif fault == 'native_long':
    native_change(sm, native, requestedLongitudinal=True)
  elif fault == 'axis_stale':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'validUntilMonoTime', stamp))
  elif fault == 'no_eps_source':
    feedback.honda_eps_source_ns = 0
  elif fault == 'expired_source':
    feedback.honda_eps_source_ns = stamp - 201_000_000
  assert observe(case, stamp + 10_000_000)
  assert not output(state, native).lateral_active


def test_repeated_publications_cannot_move_recovery_deadline():
  case = recovery_case()
  cp, state, feedback, sm, native, stamp = case
  for tick in range(1, 22):
    now = stamp + tick * 10_000_000
    event = sm.messages['aolAxisState'].as_builder()
    event.logMonoTime = now
    axis = event.aolAxisState
    axis.sequence += 1
    axis.sourceCarStateMonoTime = axis.observedMonoTime = now
    axis.validUntilMonoTime = now + 30_000_000
    axis.desiredLateral = True
    sm.messages['aolAxisState'] = event.as_reader()
    sm.logMonoTime['aolAxisState'] = now
    native = native_change(sm, native, observedMonoTime=now, validUntilMonoTime=now+200_000_000)
    assert observe(case, now) == (tick > 20)
    assert feedback.honda_eps_source_ns == stamp
    assert not output(state, native).lateral_active


def test_recovery_rejects_higher_sequence_with_rolled_back_source():
  case = recovery_case()
  _, _, _, sm, _, stamp = case
  event = sm.messages['aolAxisState'].as_builder()
  event.logMonoTime = stamp + 10_000_000
  event.aolAxisState.sequence += 1
  event.aolAxisState.observedMonoTime = stamp + 10_000_000
  event.aolAxisState.sourceCarStateMonoTime = stamp + 10_000_000
  event.aolAxisState.validUntilMonoTime = stamp + 40_000_000
  sm.messages['aolAxisState'] = event.as_reader()
  sm.logMonoTime['aolAxisState'] = stamp + 10_000_000
  assert not observe(case, stamp + 10_000_000)
  change(sm, 'aolAxisState', lambda a: setattr(a, 'sequence', a.sequence + 1))
  change(sm, 'aolAxisState', lambda a: setattr(a, 'sourceCarStateMonoTime', stamp))
  assert observe(case, stamp + 20_000_000)
