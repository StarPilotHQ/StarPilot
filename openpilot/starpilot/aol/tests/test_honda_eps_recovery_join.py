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


_cleared_recovery_case = recovery_case


def pending_request_case(*, first=False, requested=True, new_event=False):
  if first:
    cp, state, feedback, _, _ = baseline()
    stamp = NOW + 10_000_000
    sm, native = eps_sample(cp, stamp, withdrawn=False)
    change(sm, 'aolAxisState', lambda a: setattr(a, 'lateralActive', False))
    change(sm, 'aolAxisState', lambda a: setattr(a, 'mode', 'off'))
  else:
    cp, state, feedback, sm, native, stamp = _cleared_recovery_case()
    event = sm.messages['aolAxisState'].as_builder()
    event.logMonoTime = stamp + 10_000_000
    axis = event.aolAxisState
    axis.sequence += 1
    axis.observedMonoTime = axis.sourceCarStateMonoTime = stamp + 10_000_000
    axis.validUntilMonoTime = stamp + 40_000_000
    axis.desiredLateral = True
    if new_event:
      axis.faultEventMonoTime = stamp + 10_000_000
      events = sm.messages['onroadEvents'].as_builder()
      events.logMonoTime = axis.faultEventMonoTime
      sm.messages['onroadEvents'] = events.as_reader()
      sm.logMonoTime['onroadEvents'] = int(events.logMonoTime)
    sm.messages['aolAxisState'] = event.as_reader()
    sm.logMonoTime['aolAxisState'] = int(event.logMonoTime)
    native = native_change(sm, native, requestedLateral=requested)
  state.steerFaultTemporary = state.steeringPressed = True
  return cp, state, feedback, sm, native, stamp


@pytest.mark.parametrize('first,requested,new_event', [(True, True, False),
  (False, True, False), (False, False, False), (False, False, True)])
def test_native_first_and_repeated_eps_retain_only_current_latched_intent(first, requested, new_event):
  case = pending_request_case(first=first, requested=requested, new_event=new_event)
  _, state, feedback, _, native, stamp = case
  leases = feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns
  assert not observe(case, stamp + 10_000_000)
  assert not feedback.failed
  assert not output(state, native).lateral_active
  assert leases == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns)


@pytest.mark.parametrize('first', [False, True])
@pytest.mark.parametrize('fault', ['regen', 'permanent', 'can', 'timeout', 'door', 'belt',
  'sensor', 'disengage', 'main', 'reverse', 'user_event', 'hard_event', 'wrong_session', 'wrong_producer',
  'wrong_epoch', 'wrong_profile', 'native_stale', 'native_missing', 'native_fault', 'native_rx',
  'native_heartbeat', 'native_inventory', 'native_long', 'axis_stale', 'no_eps_source', 'expired_source'])
def test_pending_eps_request_preserves_all_independent_vetoes(first, fault, monkeypatch):
  import sys
  def case_for_fault():
    case = pending_request_case(first=first, requested=first)
    if first and fault == 'expired_source':
      previous = list(case[2].signature)
      previous[4] = case[5] - 1
      case[2].signature = tuple(previous)
    if first and fault == 'no_eps_source':
      previous = list(case[2].signature)
      previous[9] = False
      case[2].signature = tuple(previous)
    return case
  monkeypatch.setattr(sys.modules[__name__], 'recovery_case', case_for_fault)
  test_recovery_join_does_not_bypass_independent_vetoes(fault)


def braked_eps_case():
  cp, state, feedback, _, _ = baseline()
  state.steerFaultTemporary = state.steeringPressed = state.brakePressed = True
  stamp = NOW + 10_000_000
  sm, native = eps_sample(cp, stamp, withdrawn=True, brake=True)
  assert not feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  return cp, state, feedback, sm, native, stamp


@pytest.mark.parametrize('cleared_eps', [False, True])
def test_brake_release_joins_only_its_previously_observed_eps_pedal_event(cleared_eps):
  case = braked_eps_case()
  _, state, feedback, _, native, stamp = case
  state.brakePressed = False
  state.steerFaultTemporary = not cleared_eps
  leases = feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns
  assert not observe(case, stamp + 10_000_000)
  assert not output(state, native).lateral_active
  assert leases == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns)


@pytest.mark.parametrize('fault', ['unobserved_brake', 'expired_source', 'new_pedal_event'])
def test_brake_release_cannot_borrow_unmatched_or_expired_eps_history(fault):
  case = braked_eps_case()
  _, state, feedback, sm, _, stamp = case
  state.brakePressed = False
  if fault == 'unobserved_brake':
    feedback.honda_eps_brake_event_ns = 0
  elif fault == 'expired_source':
    feedback.honda_eps_source_ns = stamp - 201_000_000
  else:
    event = sm.messages['onroadEvents'].as_builder()
    event.logMonoTime = stamp + 5_000_000
    sm.messages['onroadEvents'] = event.as_reader()
    sm.logMonoTime['onroadEvents'] = int(event.logMonoTime)
    change(sm, 'aolAxisState', lambda a: setattr(a, 'faultEventMonoTime', stamp + 5_000_000))
  assert observe(case, stamp + 10_000_000)


@pytest.mark.parametrize('first', [False, True])
def test_pending_eps_with_current_brake_still_denies_all_output(first):
  case = pending_request_case(first=first, requested=first)
  _, state, feedback, sm, native, stamp = case
  state.brakePressed = True
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = [*(e.to_dict()for e in event.onroadEvents),
                        {'name':'pedalPressed','userDisable':True}]
  sm.messages['onroadEvents'] = event.as_reader()
  assert not observe(case, stamp + 10_000_000)
  assert not feedback.failed and not output(state, native).lateral_active


@pytest.mark.parametrize('brake', [False, True])
@pytest.mark.parametrize('source_lag', [0, 20_000_000])
def test_fresh_active_publication_during_eps_keeps_only_intent(brake, source_lag):
  case = pending_request_case(first=True)
  _, state, feedback, sm, native, stamp = case
  state.brakePressed = brake
  previous = list(feedback.signature)
  previous[8] -= source_lag
  feedback.signature = tuple(previous)
  feedback.lateral_source_ns -= source_lag
  change(sm, 'aolAxisState', lambda a: setattr(a, 'lateralActive', True))
  change(sm, 'aolAxisState', lambda a: setattr(a, 'mode', 'lateralOnly'))
  native = native_change(sm, native, lateralAllowed=True)
  before = feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns
  assert not observe(case, stamp + 10_000_000)
  assert not output(state, native).lateral_active
  assert before == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns)


@pytest.mark.parametrize('new_pulse', [False, True])
def test_clean_event_update_during_pending_eps_recovery_does_not_erase_intent(new_pulse):
  case = pending_request_case(requested=False, new_event=True)
  _, state, feedback, _, native, stamp = case
  state.steerFaultTemporary = new_pulse
  source = feedback.honda_eps_source_ns
  assert not observe(case, stamp + 10_000_000)
  assert feedback.honda_eps_source_ns == source
  assert not output(state, native).lateral_active


def test_unexplained_native_denial_without_physical_eps_still_revokes_intent():
  case = pending_request_case(first=True)
  _, state, _, _, _, stamp = case
  state.steerFaultTemporary = False
  assert observe(case, stamp + 10_000_000)


def test_fresh_history_cannot_replace_an_expired_current_source():
  case = pending_request_case(first=True)
  _, _, _, sm, _, stamp = case
  change(sm, 'aolAxisState', lambda a: setattr(a, 'sourceCarStateMonoTime', NOW - 50_000_000))
  assert observe(case, stamp + 10_000_000)


def test_new_eps_episode_uses_its_new_actual_active_predecessor():
  case = pending_request_case(first=True)
  _, state, feedback, _, native, stamp = case
  feedback.honda_eps_source_ns = NOW - 10_000_000_000
  assert not observe(case, stamp + 10_000_000)
  assert feedback.honda_eps_source_ns == NOW
  assert not output(state, native).lateral_active



def stock_acc_recovery_case(identity='HONDA_CIVIC_BOSCH', cruise_enabled=False):
  from opendbc.car.honda.tests.test_stock_aol import final_cp
  from opendbc.car.honda.values import CAR
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  from openpilot.starpilot.tests.test_ford_reverse_intent import sample

  cp = final_cp(getattr(CAR, identity))
  cp.alternativeExperience = 32
  state = structs.CarState(canValid=True, gearShifter='drive', vEgo=15.)
  state.cruiseState.available = True
  state.cruiseState.enabled = cruise_enabled
  feedback = TransportPauseFeedback(cp)
  sm, _ = sample(cp, NOW, NOW, 1)
  change(sm, 'onroadEvents', lambda events: setattr(events[0], 'userDisable', False))
  assert not feedback.observe(sm, cp, state, NOW, latched=True, intent_producer_session=PRODUCER)
  assert feedback.lateral_only_baseline
  stamp = NOW + 10_000_000
  state.steerFaultTemporary = state.steeringPressed = True
  sm, native = eps_sample(cp, stamp, withdrawn=True)
  event = sm.messages['onroadEvents'].as_builder()
  if not cruise_enabled:
    event.onroadEvents = [*(e.to_dict() for e in event.onroadEvents), {'name': 'cruiseDisabled', 'userDisable': True}]
  sm.messages['onroadEvents'] = event.as_reader()
  assert not feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  state.steerFaultTemporary = False
  return cp, state, feedback, sm, native, stamp


@pytest.mark.parametrize('identity', ['HONDA_CIVIC_BOSCH', 'HONDA_CIVIC', 'HONDA_CIVIC_2022', 'HONDA_CRV_6G'])
@pytest.mark.parametrize('cruise_enabled', [False, True])
def test_stock_acc_eps_recovery_uses_lateral_owner_event_policy(identity, cruise_enabled):
  cp, state, feedback, sm, native, stamp = stock_acc_recovery_case(identity, cruise_enabled)
  lease = feedback.lateral_source_ns, feedback.lateral_native_ns, feedback.honda_eps_source_ns
  assert not feedback.observe(sm, cp, state, stamp + 10_000_000, latched=True, intent_producer_session=PRODUCER)
  assert not feedback.failed and not output(state, native).lateral_active
  assert lease == (feedback.lateral_source_ns, feedback.lateral_native_ns, feedback.honda_eps_source_ns)
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = [*(e.to_dict() for e in event.onroadEvents), {'name': 'buttonCancel', 'userDisable': True}]
  sm.messages['onroadEvents'] = event.as_reader()
  assert feedback.observe(sm, cp, state, stamp + 20_000_000, latched=True, intent_producer_session=PRODUCER)


@pytest.mark.parametrize('flag', ['softDisable', 'immediateDisable'])
def test_stock_cruise_disabled_fault_flags_remain_authoritative(flag):
  case = stock_acc_recovery_case()
  _, _, _, sm, _, stamp = case
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = [{'name': 'cruiseDisabled', 'userDisable': True, flag: True}]
  sm.messages['onroadEvents'] = event.as_reader()
  assert observe(case, stamp + 10_000_000)


def test_native_grant_before_host_recovery_does_not_erase_original_eps_baseline():
  case = stock_acc_recovery_case()
  cp, state, feedback, sm, native, stamp = case
  change(sm, 'aolAxisState', lambda a: setattr(a, 'desiredLateral', True))
  change(sm, 'aolAxisState', lambda a: setattr(a, 'sequence', a.sequence + 1))
  native = native_change(sm, native, requestedLateral=True, lateralAllowed=True)
  lease = feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns, feedback.honda_eps_source_ns
  assert not observe(case, stamp + 10_000_000)
  assert lease == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns, feedback.honda_eps_source_ns)
  # The host recovers from its clear sample while Card has already observed a new EPS pulse.
  state.steerFaultTemporary = True
  change(sm, 'aolAxisState', lambda a: setattr(a, 'sequence', a.sequence + 1))
  change(sm, 'aolAxisState', lambda a: setattr(a, 'lateralActive', True))
  change(sm, 'aolAxisState', lambda a: setattr(a, 'mode', 'lateralOnly'))
  assert not observe(case, stamp + 20_000_000)
  assert not output(state, native).lateral_active
  assert lease == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns, feedback.honda_eps_source_ns)


def test_brake_held_after_eps_clear_keeps_only_original_braked_episode():
  cp, state, feedback, _, _ = baseline()
  state.steerFaultTemporary = state.steeringPressed = state.brakePressed = True
  stamp = NOW + 10_000_000
  sm, native = eps_sample(cp, stamp, withdrawn=True, brake=True)
  assert not feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  state.steerFaultTemporary = False
  case = cp, state, feedback, sm, native, stamp
  assert not observe(case, stamp + 10_000_000)
  assert not output(state, native).lateral_active
  assert feedback.honda_eps_source_ns == stamp
  feedback.honda_eps_brake_event_ns = 0
  assert observe(case, stamp + 20_000_000)


@pytest.mark.parametrize('fault', ['expired_source', 'wrong_producer', 'native_fault', 'native_long'])
def test_recovered_native_grant_does_not_override_independent_vetoes(fault):
  case = stock_acc_recovery_case()
  _, state, feedback, sm, native, stamp = case
  state.steerFaultTemporary = True
  change(sm, 'aolAxisState', lambda a: setattr(a, 'desiredLateral', True))
  change(sm, 'aolAxisState', lambda a: setattr(a, 'sequence', a.sequence + 1))
  change(sm, 'aolAxisState', lambda a: setattr(a, 'lateralActive', True))
  change(sm, 'aolAxisState', lambda a: setattr(a, 'mode', 'lateralOnly'))
  native = native_change(sm, native, requestedLateral=True, lateralAllowed=True)
  if fault == 'expired_source':
    feedback.honda_eps_source_ns = stamp - 201_000_000
  elif fault == 'wrong_producer':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'intentProducerSessionId', 'different'))
  elif fault == 'native_fault':
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  else:
    native_change(sm, native, requestedLongitudinal=True)
  assert observe(case, stamp + 10_000_000)
  assert not output(state, native).lateral_active


@pytest.mark.parametrize('withdrawn', [False, True])
@pytest.mark.parametrize('veto', ['', 'cancel', 'hard', 'soft', 'above_minimum', 'pcm_on', 'other_honda'])
def test_fit_turn_eps_with_factory_cruise_speed_event_preserves_only_armed_lateral(withdrawn, veto):
  from opendbc.car.honda.tests.test_stock_aol import final_cp
  from opendbc.car.honda.values import CAR
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  from openpilot.starpilot.tests.test_ford_reverse_intent import sample

  cp = final_cp(CAR.HONDA_CIVIC_BOSCH if veto == 'other_honda' else CAR.HONDA_FIT)
  cp.alternativeExperience = 32
  state = structs.CarState(canValid=True, gearShifter='drive', vEgo=4.0, rightBlinker=True)
  state.cruiseState.available = True
  feedback = TransportPauseFeedback(cp)
  initial, _ = sample(cp, NOW, NOW, 1)
  events = initial.messages['onroadEvents'].as_builder()
  events.onroadEvents = [{'name': 'steerOverride', 'overrideLateral': True},
                         {'name': 'speedTooLow', 'userDisable': True},
                         {'name': 'belowEngageSpeed', 'noEntry': True}]
  initial.messages['onroadEvents'] = events.as_reader()
  assert not feedback.observe(initial, cp, state, NOW, latched=True, intent_producer_session=PRODUCER)
  assert feedback.lateral_only_baseline
  lease = feedback.lateral_source_ns, feedback.lateral_native_ns

  stamp = NOW + 10_000_000
  state.steeringPressed = state.steerFaultTemporary = True
  state.cruiseState.enabled = veto == 'pcm_on'
  state.vEgo = 15.0 if veto == 'above_minimum' else state.vEgo
  sm, native = eps_sample(cp, stamp, withdrawn=withdrawn)
  events = sm.messages['onroadEvents'].as_builder()
  events.onroadEvents = [*(e.to_dict() for e in initial['onroadEvents'])]
  if veto in ('hard', 'soft'):
    setattr(events.onroadEvents[1], 'immediateDisable' if veto == 'hard' else 'softDisable', True)
  if veto == 'cancel':
    events.onroadEvents = [*(e.to_dict() for e in events.onroadEvents), {'name': 'buttonCancel', 'userDisable': True}]
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  sm.messages['onroadEvents'] = events.as_reader()

  assert feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER) == bool(veto)
  assert not output(state, native).lateral_active and not output(state, native).longitudinal_active
  if not veto:
    assert lease == (feedback.lateral_source_ns, feedback.lateral_native_ns)
    if withdrawn:
      state.steerFaultTemporary = state.steeringPressed = False
      assert not feedback.observe(sm, cp, state, stamp + 10_000_000, latched=True, intent_producer_session=PRODUCER)
      assert not output(state, native).lateral_active
      assert lease == (feedback.lateral_source_ns, feedback.lateral_native_ns)


@pytest.mark.parametrize('veto', ['', 'expired_eps', 'native_fault', 'cancel', 'other_honda'])
def test_fit_native_denial_arriving_after_eps_clears_keeps_armed_intent(veto):
  from opendbc.car.honda.tests.test_stock_aol import final_cp
  from opendbc.car.honda.values import CAR
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  from openpilot.starpilot.tests.test_ford_reverse_intent import sample

  cp = final_cp(CAR.HONDA_CIVIC_BOSCH if veto == 'other_honda' else CAR.HONDA_FIT)
  cp.alternativeExperience = 32
  state = structs.CarState(canValid=True, gearShifter='drive', vEgo=4.)
  state.cruiseState.available = True
  feedback = TransportPauseFeedback(cp)
  initial, _ = sample(cp, NOW, NOW, 1)
  events = initial.messages['onroadEvents'].as_builder()
  events.onroadEvents = [{'name': 'steerOverride', 'overrideLateral': True},
                         {'name': 'speedTooLow', 'userDisable': veto != 'other_honda'}]
  initial.messages['onroadEvents'] = events.as_reader()
  assert not feedback.observe(initial, cp, state, NOW, latched=True, intent_producer_session=PRODUCER)

  stamp = NOW + 10_000_000
  state.steeringPressed = state.steerFaultTemporary = True
  received, native = sample(cp, NOW, stamp, 1, allowed=False)
  received.messages['onroadEvents'] = initial.messages['onroadEvents']
  assert not feedback.observe(received, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  assert not output(state, native).lateral_active
  original = feedback.honda_eps_source_ns

  state.steerFaultTemporary = False
  now = stamp + (201_000_000 if veto == 'expired_eps' else 10_000_000)
  cleared, native = sample(cp, stamp, now, 2, active=False, allowed=False)
  cleared.messages['onroadEvents'] = initial.messages['onroadEvents']
  if veto == 'expired_eps':
    change(cleared, 'aolAxisState', lambda a: setattr(a, 'validUntilMonoTime', now + 10_000_000))
  elif veto == 'native_fault':
    native = native_change(cleared, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  elif veto == 'cancel':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  assert feedback.observe(cleared, cp, state, now, latched=True, intent_producer_session=PRODUCER) == bool(veto)
  assert feedback.honda_eps_source_ns == original
  assert getattr(feedback, 'honda_eps_retained_native_ns', 0) == (0 if veto else native.observedMonoTime)
  assert not output(state, native).lateral_active and not output(state, native).longitudinal_active


@pytest.mark.parametrize('veto', ['', 'permanent', 'native_fault', 'cancel'])
def test_fit_eps_intent_with_newer_axis_and_delayed_event(veto):
  from opendbc.car.honda.tests.test_stock_aol import final_cp
  from opendbc.car.honda.values import CAR
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  from openpilot.starpilot.tests.test_ford_reverse_intent import sample

  cp = final_cp(CAR.HONDA_FIT)
  cp.alternativeExperience = 32
  state = structs.CarState(canValid=True, gearShifter='drive', vEgo=4.0)
  state.cruiseState.available = True
  feedback = TransportPauseFeedback(cp)

  def low_speed(sm):
    events = sm.messages['onroadEvents'].as_builder()
    events.onroadEvents = [{'name': 'speedTooLow', 'userDisable': True}]
    sm.messages['onroadEvents'] = events.as_reader()

  for sequence, stamp in enumerate((NOW, NOW + 8_000_000), start=1):
    start, _ = sample(cp, stamp, stamp, sequence)
    low_speed(start)
    assert not feedback.observe(start, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)

  stamp = NOW + 45_000_000
  axis, native = sample(cp, stamp - 10_000_000, stamp, 3, event_stamp=NOW + 30_000_000)
  low_speed(axis)
  change(axis, 'aolAxisState', lambda a: setattr(a, 'faultEventMonoTime', NOW + 35_000_000))
  state.steerFaultTemporary = state.steeringPressed = True
  if veto == 'permanent':
    state.steerFaultPermanent = True
  elif veto == 'cancel':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  elif veto == 'native_fault':
    native_change(axis, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  assert feedback.observe(axis, cp, state, stamp, latched=True, intent_producer_session=PRODUCER) == bool(veto)
  assert not output(state, native).lateral_active
