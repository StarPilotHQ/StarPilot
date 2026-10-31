"""First gas-held LAT grant must be actual, current and bound to the prior owner."""
from dataclasses import replace

import pytest
from opendbc.car.ford.values import CAR
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
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
      native = native_change(sm, native, observedMonoTime=NOW + 10_000_000)
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
