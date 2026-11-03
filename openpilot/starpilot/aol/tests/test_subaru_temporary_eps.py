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
def test_eps_retention_does_not_mask_independent_vetoes(fault):
  cp, state, feedback = baseline()
  state.steerFaultTemporary = True
  now = NOW + 10_000_000
  sm, native = sample(cp, now, now, 2, allowed=False)
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
