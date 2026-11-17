"""Fresh EPS recovery retains established stock LAT intent, never an output grant."""

import pytest

from opendbc.car import structs
from opendbc.car.ford.values import CAR as Ford
from opendbc.car.hyundai.values import CAR as Hyundai
from opendbc.car.hyundai.classic_scc_aol import qualified as classic_scc_qualified
from openpilot.cereal import log
from openpilot.starpilot.aol.tests.test_stock_location_recovery import established_stock as established_stock
from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, change, native_change, sample


def recovered_pause(cp, state, feedback):
  stamp = NOW + 10_000_000
  state.steerFaultTemporary = True
  sm, _ = sample(cp, stamp, stamp, 2, active=False, requested=False, allowed=False, event_stamp=stamp)

  def eps_events(peers):
    event = peers.messages['onroadEvents'].as_builder()
    event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True},
                         {'name': 'steerTempUnavailable', 'softDisable': True}]
    peers.messages['onroadEvents'] = event.as_reader()
    change(peers, 'aolAxisState', lambda axis: setattr(axis, 'nativeAcknowledged', False))

  eps_events(sm)
  assert not feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  assert feedback.temporary_eps_event_ns == stamp
  state.steerFaultTemporary = False
  now = NOW + 170_000_000
  sm, native = sample(cp, now, now, 3, active=False, requested=False, allowed=False, event_stamp=stamp)
  eps_events(sm)
  return sm, native, now


def test_matched_stock_eps_recovery_preserves_only_armed_intent(established_stock):
  cp, state, feedback = established_stock
  sm, native, now = recovered_pause(cp, state, feedback)
  baseline = (feedback.healthy_feedback_ns, feedback.healthy_native_ns, feedback.lateral_baseline_ns,
              feedback.lateral_source_ns, feedback.lateral_native_ns)
  assert not feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
  assert feedback.temporary_host_qualified and not feedback.failed
  assert baseline == (feedback.healthy_feedback_ns, feedback.healthy_native_ns, feedback.lateral_baseline_ns,
                      feedback.lateral_source_ns, feedback.lateral_native_ns)
  assert not native.lateralAllowed and not native.longitudinalAllowed


@pytest.mark.parametrize('fault', ('invalid_can', 'permanent_eps', 'main_off', 'cancel', 'reverse', 'brake', 'gas',
  'native_denial', 'native_invalid', 'wrong_epoch', 'wrong_producer', 'wrong_session', 'expired', 'future_source',
  'immediate', 'other_user', 'long_desired', 'long_active', 'missing_anchor', 'different_anchor'))
def test_eps_recovery_never_bypasses_independent_vetoes(established_stock, fault, *, independent_classic=False):
  cp, state, feedback = established_stock
  sm, native, now = recovered_pause(cp, state, feedback)
  if independent_classic:
    assert classic_scc_qualified(cp, marked_only=True)
    state.cruiseState.available = False
    state.gasPressed = True
  producer = PRODUCER
  if fault == 'invalid_can':
    state.canValid = False
  elif fault == 'permanent_eps':
    state.steerFaultPermanent = True
  elif fault == 'main_off':
    state.cruiseState.available = False
  elif fault == 'cancel':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  elif fault == 'reverse':
    state.gearShifter = 'reverse'
  elif fault == 'brake':
    state.brakePressed = True
  elif fault == 'gas':
    state.gasPressed = True
  elif fault == 'native_denial':
    native_change(sm, native, requestedLateral=True)
  elif fault == 'native_invalid':
    sm.valid['aolSafetyWire'] = False
  elif fault == 'wrong_epoch':
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'intentIntegrityEpoch', 2))
  elif fault == 'wrong_producer':
    producer = 'other'
  elif fault == 'wrong_session':
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'sessionId', 'other'))
  elif fault == 'expired':
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'validUntilMonoTime', now - 1))
  elif fault == 'future_source':
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'sourceCarStateMonoTime', now + 1))
  elif fault == 'immediate':
    change(sm, 'onroadEvents', lambda events: setattr(events[1], 'immediateDisable', True))
  elif fault == 'other_user':
    change(sm, 'onroadEvents', lambda events: setattr(events[0], 'name', log.OnroadEvent.EventName.buttonCancel))
  elif fault in ('long_desired', 'long_active'):
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'desiredLongitudinal' if fault == 'long_desired' else 'longitudinalActive', True))
  elif fault == 'missing_anchor':
    feedback.temporary_eps_event_ns = 0
  elif fault == 'different_anchor':
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'faultEventMonoTime', NOW + 11_000_000))
  before = (feedback.healthy_feedback_ns, feedback.healthy_native_ns, feedback.lateral_baseline_ns,
            feedback.lateral_source_ns, feedback.lateral_native_ns)
  rejected = feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=producer)
  retained = (fault in ('brake', 'gas') and cp.carFingerprint in (Ford.FORD_MUSTANG_MACH_E_MK1, Ford.FORD_ESCAPE_MK4) or
              fault in ('main_off', 'gas') and classic_scc_qualified(cp, marked_only=True))
  if retained:
    assert not rejected and feedback.temporary_host_qualified
    assert not native.requestedLateral and not native.requestedLongitudinal
    assert not native.lateralAllowed and not native.longitudinalAllowed
    assert before == (feedback.healthy_feedback_ns, feedback.healthy_native_ns, feedback.lateral_baseline_ns,
                      feedback.lateral_source_ns, feedback.lateral_native_ns)
  else:
    assert rejected and not feedback.temporary_host_qualified


@pytest.mark.parametrize('fault', (None, 'missing_anchor', 'different_anchor', 'long_desired', 'long_active',
                                  'belt_immediate', 'belt_user', 'belt_no_entry'))
def test_unbelted_eps_recovery_keeps_exact_anchor_and_long_withdrawal(established_stock, fault):
  cp, state, feedback = established_stock
  sm, native, now = recovered_pause(cp, state, feedback)
  state.seatbeltUnlatched = True
  event = sm.messages['onroadEvents'].as_builder()
  event.onroadEvents = [row.to_dict() for row in sm['onroadEvents']] + [
    {'name': 'seatbeltNotLatched', 'softDisable': True, 'noEntry': True}]
  sm.messages['onroadEvents'] = event.as_reader()
  if fault == 'missing_anchor':
    feedback.temporary_eps_event_ns = 0
  elif fault == 'different_anchor':
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'faultEventMonoTime', NOW + 11_000_000))
  elif fault in ('long_desired', 'long_active'):
    change(sm, 'aolAxisState', lambda axis: setattr(axis,
      'desiredLongitudinal' if fault == 'long_desired' else 'longitudinalActive', True))
  elif fault in ('belt_immediate', 'belt_user', 'belt_no_entry'):
    field = {'belt_immediate': 'immediateDisable', 'belt_user': 'userDisable', 'belt_no_entry': 'noEntry'}[fault]
    change(sm, 'onroadEvents', lambda events: setattr(events[2], field, fault != 'belt_no_entry'))
  baseline = (feedback.healthy_feedback_ns, feedback.healthy_native_ns, feedback.lateral_baseline_ns,
              feedback.lateral_source_ns, feedback.lateral_native_ns)
  rejected = feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
  assert rejected == (fault is not None)
  assert feedback.temporary_host_qualified == (fault is None)
  assert baseline == (feedback.healthy_feedback_ns, feedback.healthy_native_ns, feedback.lateral_baseline_ns,
                      feedback.lateral_source_ns, feedback.lateral_native_ns)
  assert not native.requestedLateral and not native.lateralAllowed
  assert not native.requestedLongitudinal and not native.longitudinalAllowed


@pytest.mark.parametrize('established_stock', [Hyundai.HYUNDAI_SONATA_HYBRID], indirect=True)
@pytest.mark.parametrize('fault', ('invalid_can', 'permanent_eps', 'cancel', 'reverse', 'brake',
  'native_denial', 'native_invalid', 'wrong_epoch', 'wrong_producer', 'wrong_session', 'expired', 'future_source',
  'immediate', 'other_user', 'long_desired', 'long_active', 'missing_anchor', 'different_anchor'))
def test_classic_main_off_and_gas_keep_independent_eps_recovery_vetoes(established_stock, fault):
  test_eps_recovery_never_bypasses_independent_vetoes(established_stock, fault, independent_classic=True)
