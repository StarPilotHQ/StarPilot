"""First gas-held LAT grant must be actual, current and bound to the prior owner."""
from dataclasses import replace

import pytest
from opendbc.car.ford.values import CAR
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.cereal import messaging
from openpilot.starpilot.tests.ford_alpha_test_support import NOW, PRODUCER, baseline, change, native_change, receipt
from openpilot.starpilot.tests.test_ford_reverse_intent import sample


def first_grant():
  cp, cs, _ = baseline(combined=False)
  cp.carFingerprint, cp.flags = CAR.FORD_MUSTANG_MACH_E_MK1, 3
  cp.safetyConfigs[0].safetyParam = 3
  feedback = TransportPauseFeedback(cp)
  sm, _ = sample(cp, NOW, NOW, 1, active=False, requested=False, allowed=False)
  assert not feedback.observe(sm, cp, cs, NOW, latched=False, intent_producer_session=PRODUCER)
  assert not feedback.lateral_baseline_ns
  cs.cruiseState.enabled, cs.gasPressed = True, True
  sm, native = receipt(cp, NOW + 10_000_000, NOW + 20_000_000, 2, gas=True)
  change(sm, 'aolAxisState', lambda axis: (setattr(axis, 'mode', 'off'), setattr(axis, 'lateralActive', False)))
  return cp, cs, feedback, sm, native


@pytest.mark.parametrize('fault', [
  'none', 'unlatched', 'failed', 'paused', 'producer', 'epoch', 'session', 'axis-expired', 'axis-future',
  'no-desire', 'active-long', 'bad-mode', 'native-older', 'native-no-lat', 'native-long', 'native-rx',
  'native-heartbeat', 'native-inventory', 'brake', 'eps', 'can', 'main', 'event-fault', 'event-user',
])
def test_first_gas_grant_retains_intent_only_with_current_native_proof(fault):
  cp, cs, feedback, sm, native = first_grant()
  now, latched, producer = NOW + 20_000_000, True, PRODUCER
  if fault in ('unlatched', 'failed', 'paused', 'producer'):
    if fault == 'unlatched':
      latched = False
    elif fault == 'producer':
      producer = 'other'
    else:
      setattr(feedback, fault, True)
  elif fault in ('epoch', 'session', 'no-desire', 'active-long', 'bad-mode', 'axis-future'):
    field, value = {'epoch': ('intentIntegrityEpoch', 2), 'session': ('sessionId', 'other'),
      'no-desire': ('desiredLateral', False), 'active-long': ('longitudinalActive', True),
      'bad-mode': ('mode', 'combined'), 'axis-future': ('observedMonoTime', now + 1)}[fault]
    change(sm, 'aolAxisState', lambda axis: setattr(axis, field, value))
  elif fault == 'axis-expired':
    now += 30_000_000
  elif fault.startswith('native-'):
    if fault == 'native-older':
      native = native_change(sm, native, observedMonoTime=NOW - 1, validUntilMonoTime=NOW + 199_999_999)
    elif fault == 'native-no-lat':
      native = native_change(sm, native, lateralAllowed=False)
    elif fault == 'native-long':
      native = native_change(sm, native, longitudinalAllowed=True)
    else:
      field, value = {'native-rx': ('safetyRxChecksInvalid', True), 'native-heartbeat': ('heartbeatLost', True),
        'native-inventory': ('hardwareSerial', 'other')}[fault]
      native = native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], **{field: value}),))
  elif fault in ('brake', 'eps', 'can', 'main'):
    if fault == 'main':
      cs.cruiseState.available = False
    else:
      field, value = {'brake': ('brakePressed', True), 'eps': ('steerFaultTemporary', True), 'can': ('canValid', False)}[fault]
      setattr(cs, field, value)
  elif fault in ('event-fault', 'event-user'):
    change(sm, 'onroadEvents', lambda events: setattr(events[0], 'immediateDisable' if fault == 'event-fault' else 'userDisable', True))
  before, stamps = dict(vars(feedback)), dict(sm.logMonoTime)
  result = feedback._first_ford_gas_lateral_grant(sm, cp, cs, sm['aolAxisState'], now, latched=latched, producer=producer)
  assert (result is not None) == (fault == 'none')
  assert vars(feedback) == before and sm.logMonoTime == stamps
  if result is not None:
    assert result == native and result.lateralAllowed and not result.longitudinalAllowed


@pytest.mark.parametrize('fault', [
  'none', 'expired-source', 'expired-native', 'producer', 'epoch', 'session', 'unlatched',
  'native-lat', 'native-heartbeat', 'native-rx', 'native-inventory', 'native-profile', 'event-fault',
])
@pytest.mark.parametrize('newer_axis', [False, True])
def test_gas_held_source_gap_retains_only_bounded_independent_intent(fault, newer_axis):
  cp, cs, feedback, sm, _ = first_grant()
  assert not feedback.observe(sm, cp, cs, NOW + 20_000_000, latched=True, intent_producer_session=PRODUCER)
  sm, native = receipt(cp, NOW + 30_000_000, NOW + 30_000_000, 3, gas=True)
  assert not feedback.observe(sm, cp, cs, NOW + 30_000_000, latched=True, intent_producer_session=PRODUCER)
  assert feedback.lateral_baseline_ns and feedback.lateral_inventory
  now, producer, latched = NOW + 65_000_000, PRODUCER, True
  if newer_axis:
    axis_event = sm.messages['aolAxisState'].as_builder()
    axis_event.logMonoTime = NOW + 40_000_000
    axis_event.aolAxisState.sequence = 4
    axis_event.aolAxisState.observedMonoTime = NOW + 40_000_000
    axis_event.aolAxisState.validUntilMonoTime = NOW + 70_000_000
    sm.messages['aolAxisState'] = axis_event.as_reader()
    sm.logMonoTime['aolAxisState'] = NOW + 40_000_000
  if fault == 'expired-source':
    now = NOW + 230_000_001
  elif fault == 'expired-native':
    native_change(sm, native, validUntilMonoTime=now - 1)
  elif fault == 'producer':
    producer = 'other'
  elif fault == 'unlatched':
    latched = False
  elif fault in ('epoch', 'session'):
    change(sm, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch' if fault == 'epoch' else 'sessionId', 2 if fault == 'epoch' else 'other'))
  elif fault == 'native-lat':
    native_change(sm, native, lateralAllowed=False)
  elif fault in ('native-heartbeat', 'native-rx', 'native-inventory'):
    field, value = {'native-heartbeat': ('heartbeatLost', True), 'native-rx': ('safetyRxChecksInvalid', True),
                    'native-inventory': ('hardwareSerial', 'other')}[fault]
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], **{field: value}),))
  elif fault == 'native-profile':
    native_change(sm, native, safetyParam=native.safetyParam ^ 1)
  elif fault == 'event-fault':
    change(sm, 'onroadEvents', lambda events: setattr(events[0], 'immediateDisable', True))
  stamps = dict(sm.logMonoTime)
  failed = feedback.observe(sm, cp, cs, now, latched=latched, intent_producer_session=producer)
  assert failed == (fault not in ('none', 'unlatched'))
  assert sm.logMonoTime == stamps
  if fault == 'none':
    assert now - sm['aolAxisState'].sourceCarStateMonoTime > 30_000_000
    assert native.lateralAllowed and not native.longitudinalAllowed
    # Re-observing never renews the original source's 200ms intent window.
    assert feedback.observe(sm, cp, cs, NOW + 230_000_001, latched=True, intent_producer_session=PRODUCER)


@pytest.mark.parametrize('fault', ['none', 'native-lat', 'epoch', 'expired-native', 'event-fault'])
def test_first_gas_native_grant_survives_pending_axis_ack(fault):
  cp, cs, feedback, sm, native = first_grant()
  assert not feedback.observe(sm, cp, cs, NOW + 20_000_000, latched=True, intent_producer_session=PRODUCER)
  sm, _ = receipt(cp, NOW + 30_000_000, NOW + 30_000_000, 3, gas=True)
  native_change(sm, native)
  change(sm, 'aolAxisState', lambda a: (setattr(a, 'mode', 'off'), setattr(a, 'lateralActive', False)))
  if fault == 'native-lat':
    native_change(sm, native, lateralAllowed=False)
  elif fault == 'epoch':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', 2))
  elif fault == 'expired-native':
    native_change(sm, native, validUntilMonoTime=NOW + 29_000_000)
  elif fault == 'event-fault':
    change(sm, 'onroadEvents', lambda events: setattr(events[0], 'immediateDisable', True))
  assert feedback.observe(sm, cp, cs, NOW + 30_000_000, latched=True,
                          intent_producer_session=PRODUCER) == (fault != 'none')


@pytest.mark.parametrize('fault', ['none', 'native-lat', 'native-rx', 'epoch', 'critical-event', 'expired-source'])
@pytest.mark.parametrize('phase', ['active-warning', 'withdrawal'])
def test_gas_lag_withdrawal_preserves_intent_without_restoring_axes(fault, phase):
  cp, cs, feedback, sm, _ = first_grant()
  assert not feedback.observe(sm, cp, cs, NOW + 20_000_000, latched=True, intent_producer_session=PRODUCER)
  sm, native = receipt(cp, NOW + 30_000_000, NOW + 30_000_000, 3, gas=True)
  assert not feedback.observe(sm, cp, cs, NOW + 30_000_000, latched=True, intent_producer_session=PRODUCER)
  sm, _ = receipt(cp, NOW + 40_000_000, NOW + 40_000_000, 4, gas=True)
  native_change(sm, native)
  event = messaging.new_message('onroadEvents', 1, valid=True, logMonoTime=NOW + 40_000_000)
  event.onroadEvents[0].name = 'selfdrivedLagging'
  event.onroadEvents[0].softDisable = True
  sm.messages['onroadEvents'] = event.as_reader()
  sm.logMonoTime['onroadEvents'] = NOW + 40_000_000
  def withdrawal(axis):
    axis.mode, axis.faultReason = 'off', 'critical'
    axis.faultEventMonoTime = NOW + 40_000_000
    axis.desiredLateral = axis.lateralActive = axis.desiredLongitudinal = axis.longitudinalActive = False
  if phase == 'withdrawal':
    change(sm, 'aolAxisState', withdrawal)
  now = NOW + 40_000_000
  if fault == 'native-lat':
    native_change(sm, native, lateralAllowed=False)
  elif fault == 'native-rx':
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], safetyRxChecksInvalid=True),))
  elif fault == 'epoch':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', 2))
  elif fault == 'critical-event':
    change(sm, 'onroadEvents', lambda events: setattr(events[0], 'immediateDisable', True))
  elif fault == 'expired-source':
    now += 200_000_001
  before = sm['aolAxisState'].to_dict()
  assert feedback.observe(sm, cp, cs, now, latched=True, intent_producer_session=PRODUCER) == (fault != 'none')
  assert sm['aolAxisState'].to_dict() == before
  if phase == 'withdrawal':
    assert not any(before[n] for n in ('desiredLateral', 'lateralActive', 'desiredLongitudinal', 'longitudinalActive'))
  elif fault == 'none':
    assert feedback.ordinary_warning_qualified


@pytest.mark.parametrize('fault', ['none', 'lag-warning', 'expired-source', 'native-rx', 'epoch', 'native-lat-denied', 'critical-event'])
def test_gas_zero_command_echo_retains_original_intent_proof_only(fault):
  cp, cs, feedback, sm, _ = first_grant()
  assert not feedback.observe(sm, cp, cs, NOW + 20_000_000, latched=True, intent_producer_session=PRODUCER)
  sm, native = receipt(cp, NOW + 30_000_000, NOW + 30_000_000, 3, gas=True)
  assert not feedback.observe(sm, cp, cs, NOW + 30_000_000, latched=True, intent_producer_session=PRODUCER)
  original = (feedback.signature, feedback.lateral_source_ns, feedback.lateral_native_ns)
  sm, native = receipt(cp, NOW + 40_000_000, NOW + 40_000_000, 4, gas=True)
  native = native_change(sm, native, requestedLateral=False, lateralAllowed=False,
                         requestedLongitudinal=False, longitudinalAllowed=False)
  now = NOW + 40_000_000
  if fault == 'lag-warning':
    def warning(events):
      events[0].name = 'selfdrivedLagging'
      events[0].overrideLongitudinal = False
      events[0].softDisable = True
    change(sm, 'onroadEvents', warning)
  elif fault == 'expired-source':
    now = NOW + 230_000_001
  elif fault == 'native-rx':
    native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], safetyRxChecksInvalid=True),))
  elif fault == 'epoch':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', 2))
  elif fault == 'native-lat-denied':
    native_change(sm, native, requestedLateral=True)
  elif fault == 'critical-event':
    change(sm, 'onroadEvents', lambda events: setattr(events[0], 'immediateDisable', True))
  from openpilot.starpilot.car.ford.command_pause import retain_command_pause_intent
  before = dict(vars(feedback))
  retained = retain_command_pause_intent(feedback, sm, cp, cs, sm['aolAxisState'], now,
                                        latched=True, producer=PRODUCER)
  assert retained == (fault in ('none', 'lag-warning'))
  assert vars(feedback) == before
  if retained:
    assert not feedback.observe(sm, cp, cs, now, latched=True, intent_producer_session=PRODUCER)
    assert (feedback.signature, feedback.lateral_source_ns, feedback.lateral_native_ns) == original
    assert not any((native.requestedLateral, native.lateralAllowed, native.requestedLongitudinal, native.longitudinalAllowed))


def test_gas_recovery_before_native_echo_preserves_original_proof():
  cp, cs, feedback, sm, _ = first_grant()
  assert not feedback.observe(sm, cp, cs, NOW + 20_000_000, latched=True, intent_producer_session=PRODUCER)
  sm, native = receipt(cp, NOW + 30_000_000, NOW + 30_000_000, 3, gas=True)
  assert not feedback.observe(sm, cp, cs, NOW + 30_000_000, latched=True, intent_producer_session=PRODUCER)
  source, grant = feedback.lateral_source_ns, feedback.lateral_native_ns
  sm, _ = receipt(cp, NOW + 40_000_000, NOW + 40_000_000, 4, gas=True)
  zero = native_change(sm, native, observedMonoTime=NOW + 40_000_000, validUntilMonoTime=NOW + 240_000_000,
                       requestedLateral=False, lateralAllowed=False, requestedLongitudinal=False, longitudinalAllowed=False)
  event = messaging.new_message('onroadEvents', 1, valid=True, logMonoTime=NOW + 40_000_000)
  event.onroadEvents[0].name, event.onroadEvents[0].softDisable = 'selfdrivedLagging', True
  sm.messages['onroadEvents'], sm.logMonoTime['onroadEvents'] = event.as_reader(), NOW + 40_000_000
  def withdrawal(axis):
    axis.mode, axis.faultReason = 'off', 'availabilityPause'
    axis.faultEventMonoTime = NOW + 40_000_000
    axis.desiredLateral = axis.lateralActive = axis.desiredLongitudinal = axis.longitudinalActive = axis.nativeAcknowledged = False
  change(sm, 'aolAxisState', withdrawal)
  assert not feedback.observe(sm, cp, cs, NOW + 40_000_000, latched=True, intent_producer_session=PRODUCER)
  assert feedback.ordinary_warning_retained
  sm, _ = receipt(cp, NOW + 50_000_000, NOW + 50_000_000, 5, gas=True)
  native_change(sm, zero)
  change(sm, 'aolAxisState', lambda axis: setattr(axis, 'faultEventMonoTime', NOW + 50_000_000))
  before = (feedback.signature, feedback.lateral_source_ns, feedback.lateral_native_ns)
  assert not feedback.observe(sm, cp, cs, NOW + 50_000_000, latched=True, intent_producer_session=PRODUCER)
  assert (feedback.signature, feedback.lateral_source_ns, feedback.lateral_native_ns) == before
  assert feedback.lateral_source_ns == source and feedback.lateral_native_ns == grant
  sm, _ = receipt(cp, NOW + 60_000_000, NOW + 60_000_000, 6, gas=True)
  assert not feedback.observe(sm, cp, cs, NOW + 60_000_000, latched=True, intent_producer_session=PRODUCER)
  assert feedback.lateral_source_ns > source and feedback.lateral_native_ns > grant


def test_repeated_gas_lag_pause_keeps_original_source_cutoff():
  cp, cs, feedback, sm, _ = first_grant()
  assert not feedback.observe(sm, cp, cs, NOW + 20_000_000, latched=True, intent_producer_session=PRODUCER)
  sm, native = receipt(cp, NOW + 30_000_000, NOW + 30_000_000, 3, gas=True)
  assert not feedback.observe(sm, cp, cs, NOW + 30_000_000, latched=True, intent_producer_session=PRODUCER)
  event = messaging.new_message('onroadEvents', 1, valid=True, logMonoTime=NOW + 40_000_000)
  event.onroadEvents[0].name, event.onroadEvents[0].softDisable = 'selfdrivedLagging', True
  sm.messages['onroadEvents'], sm.logMonoTime['onroadEvents'] = event.as_reader(), NOW + 40_000_000
  for observed, sequence in ((NOW + 40_000_000, 4), (NOW + 50_000_000, 5)):
    axis = sm.messages['aolAxisState'].as_builder()
    axis.logMonoTime = observed
    value = axis.aolAxisState
    value.sequence, value.observedMonoTime, value.validUntilMonoTime = sequence, observed, observed + 30_000_000
    value.mode, value.faultReason, value.faultEventMonoTime = 'off', 'availabilityPause', NOW + 40_000_000
    value.desiredLateral = value.lateralActive = value.desiredLongitudinal = value.longitudinalActive = value.nativeAcknowledged = False
    sm.messages['aolAxisState'], sm.logMonoTime['aolAxisState'] = axis.as_reader(), observed
    now = observed if sequence == 4 else NOW + 65_000_000
    assert not feedback.observe(sm, cp, cs, now, latched=True, intent_producer_session=PRODUCER)
    assert not feedback.observe(sm, cp, cs, now, latched=True, intent_producer_session=PRODUCER)
    assert feedback.lateral_source_ns == NOW + 30_000_000
  assert feedback.observe(sm, cp, cs, NOW + 230_000_001, latched=True, intent_producer_session=PRODUCER)
