"""First native grant may join only the exact previously validated axis lease."""
import pytest
from openpilot.starpilot.tests.test_ford_first_gas_grant import first_grant
from openpilot.starpilot.tests.ford_alpha_test_support import NOW, PRODUCER, change, native_change


def pending_grant():
  cp, cs, feedback, sm, native = first_grant()
  event = sm.messages['aolAxisState'].as_builder()
  event.logMonoTime = NOW + 20_000_000
  event.aolAxisState.observedMonoTime = NOW + 20_000_000
  event.aolAxisState.validUntilMonoTime = NOW + 50_000_000
  event.aolAxisState.lateralActive = True
  event.aolAxisState.mode = 'lateralOnly'
  sm.messages['aolAxisState'] = event.as_reader()
  sm.logMonoTime['aolAxisState'] = event.logMonoTime
  native_change(sm, native, requestedLateral=False, lateralAllowed=False, requestedLongitudinal=False)
  assert not feedback.observe(sm, cp, cs, NOW + 30_000_000, latched=True, intent_producer_session=PRODUCER)
  assert not feedback.ever_lateral_active and not feedback.lateral_native_ns
  native_change(sm, native, observedMonoTime=NOW + 40_000_000, validUntilMonoTime=NOW + 240_000_000)
  return cp, cs, feedback, sm


@pytest.mark.parametrize('fault', [
  'none', 'expired', 'source', 'sequence', 'publication', 'deadline', 'epoch', 'producer', 'session',
  'fault', 'off', 'no-prior', 'prior-stale', 'prior-active', 'prior-native', 'pending', 'retaining',
  'source-retaining', 'native-denied', 'native-expired', 'native-old', 'native-session', 'native-invalid',
  'brake', 'cancel', 'cancel-release', 'gas-off', 'cruise-off', 'can-invalid', 'eps', 'unlatched',
])
def test_first_grant_only_joins_the_original_validated_source(fault):
  cp, cs, feedback, sm = pending_grant()
  now = NOW + 45_000_000
  axis = sm['aolAxisState']
  assert now - axis.sourceCarStateMonoTime > 30_000_000
  assert now < axis.validUntilMonoTime
  if fault == 'expired':
    now = axis.validUntilMonoTime + 1
  elif fault in ('source', 'sequence', 'publication', 'deadline', 'epoch', 'producer', 'session', 'fault', 'off'):
    field, value = {
      'source': ('sourceCarStateMonoTime', axis.sourceCarStateMonoTime - 1),
      'sequence': ('sequence', axis.sequence + 1), 'publication': ('observedMonoTime', axis.observedMonoTime + 1),
      'deadline': ('validUntilMonoTime', axis.validUntilMonoTime + 1), 'epoch': ('intentIntegrityEpoch', 2),
      'producer': ('intentProducerSessionId', 'foreign'), 'session': ('sessionId', 'foreign'),
      'fault': ('faultReason', 'critical'), 'off': ('desiredLateral', False),
    }[fault]
    change(sm, 'aolAxisState', lambda a: setattr(a, field, value))
  elif fault in ('no-prior', 'prior-stale', 'prior-active', 'prior-native', 'pending', 'retaining', 'source-retaining'):
    field, value = {
      'no-prior': ('signature', None), 'prior-stale': ('healthy_feedback_ns', axis.sourceCarStateMonoTime + 30_000_001),
      'prior-active': ('ever_lateral_active', True), 'prior-native': ('lateral_native_ns', NOW),
      'pending': ('pending_event', 1), 'retaining': ('retention_started_ns', NOW),
      'source-retaining': ('source_retention_started_ns', NOW),
    }[fault]
    setattr(feedback, field, value)
  elif fault.startswith('native-'):
    from openpilot.starpilot.aol.wire import decode_safety
    native = decode_safety(sm['aolSafetyWire'])
    field, value = {
      'native-denied': ('lateralAllowed', False), 'native-expired': ('validUntilMonoTime', now - 1),
      'native-old': ('observedMonoTime', feedback.healthy_native_ns),
      'native-session': ('axisSessionId', 'foreign'), 'native-invalid': ('compatible', False),
    }[fault]
    native_change(sm, native, **{field: value})
  elif fault in ('cancel', 'cancel-release'):
    cs.buttonEvents = [{'type': 'cancel', 'pressed': fault == 'cancel'}]
  elif fault == 'cruise-off':
    cs.cruiseState.enabled = False
  elif fault in ('brake', 'gas-off', 'can-invalid', 'eps'):
    field, value = {'brake': ('brakePressed', True), 'gas-off': ('gasPressed', False),
                    'can-invalid': ('canValid', False), 'eps': ('steerFaultTemporary', True)}[fault]
    setattr(cs, field, value)
  before = dict(vars(feedback)), dict(sm.logMonoTime), sm['aolAxisState'].to_dict()
  result = feedback._first_ford_gas_lateral_grant(sm, cp, cs, sm['aolAxisState'], now,
                                               latched=fault != 'unlatched', producer=PRODUCER)
  assert (result is not None) == (fault == 'none')
  assert before == (dict(vars(feedback)), dict(sm.logMonoTime), sm['aolAxisState'].to_dict())
  if result is not None:
    assert result.lateralAllowed and not result.longitudinalAllowed
    assert not feedback.observe(sm, cp, cs, now, latched=True, intent_producer_session=PRODUCER)
    assert not feedback._same_validated_ford_first_source(sm['aolAxisState'], now + 1)
