"""Stock Ford's native temporary EPS denial withdraws output, not existing intent."""

from dataclasses import replace

import pytest
from openpilot.cereal import messaging
from openpilot.starpilot.tests.test_ford_location_intent import IDENTITIES, location_case
from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, change, native_change, sample


def case(identity=IDENTITIES[0]):
  cp, cs, feedback, _, _, _ = location_case(identity, False)
  stamp = NOW + 10_000_000
  peers, native = sample(cp, stamp, stamp, 2, allowed=False)
  cs.steerFaultTemporary = True
  return cp, cs, feedback, peers, native, stamp


@pytest.mark.parametrize('identity', IDENTITIES)
@pytest.mark.parametrize('brake,gas', [(False, False), (True, False), (False, True), (True, True)])
def test_stock_temporary_eps_join_preserves_only_original_intent_and_recovers(identity, brake, gas):
  cp, cs, feedback, peers, _, stamp = case(identity)
  cs.brakePressed = brake
  cs.gasPressed = gas
  if brake:
    event = messaging.new_message('onroadEvents', 2, valid=True, logMonoTime=NOW)
    event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True}, {'name': 'pedalPressed', 'userDisable': True}]
    peers.messages['onroadEvents'] = event.as_reader()
  baseline = (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns, feedback.healthy_feedback_ns, feedback.healthy_native_ns)
  stamps = dict(peers.logMonoTime)
  assert not feedback.observe(peers, cp, cs, stamp, latched=True, intent_producer_session=PRODUCER)
  assert not feedback.failed
  assert baseline == (
    feedback.lateral_baseline_ns,
    feedback.lateral_source_ns,
    feedback.lateral_native_ns,
    feedback.healthy_feedback_ns,
    feedback.healthy_native_ns,
  )
  assert peers.logMonoTime == stamps
  next_stamp = stamp + 10_000_000
  withdrawn, _ = sample(cp, next_stamp, next_stamp, 3, active=False, requested=False, allowed=False, event_stamp=next_stamp)
  change(withdrawn, 'aolAxisState', lambda axis: setattr(axis, 'nativeAcknowledged', False))
  event = messaging.new_message('onroadEvents', 3 if brake else 2, valid=True, logMonoTime=next_stamp)
  event.onroadEvents = [{'name': 'pcmDisable', 'userDisable': True}, {'name': 'steerTempUnavailableSilent', 'warning': True}] + (
    [{'name': 'pedalPressed', 'userDisable': True}] if brake else []
  )
  withdrawn.messages['onroadEvents'] = event.as_reader()
  withdrawn.logMonoTime['onroadEvents'] = next_stamp
  assert not feedback.observe(withdrawn, cp, cs, next_stamp, latched=True, intent_producer_session=PRODUCER)
  assert feedback.temporary_eps_event_ns == next_stamp
  assert baseline == (
    feedback.lateral_baseline_ns,
    feedback.lateral_source_ns,
    feedback.lateral_native_ns,
    feedback.healthy_feedback_ns,
    feedback.healthy_native_ns,
  )
  cs.steerFaultTemporary = False
  recovery_stamp = next_stamp + 10_000_000
  if brake or gas:
    recovering, _ = sample(cp, recovery_stamp, recovery_stamp, 4, active=False, requested=False,
                           allowed=False, event_stamp=next_stamp)
    change(recovering, 'aolAxisState', lambda axis: setattr(axis, 'nativeAcknowledged', False))
    recovering.messages['onroadEvents'] = event.as_reader()
    recovering.logMonoTime['onroadEvents'] = next_stamp
    assert not feedback.observe(recovering, cp, cs, recovery_stamp, latched=True, intent_producer_session=PRODUCER)
    assert baseline == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns,
                        feedback.healthy_feedback_ns, feedback.healthy_native_ns)
    recovery_stamp += 10_000_000
  recovered, _ = sample(cp, recovery_stamp, recovery_stamp, 5, event_stamp=recovery_stamp)
  assert not feedback.observe(recovered, cp, cs, recovery_stamp, latched=True, intent_producer_session=PRODUCER)
  assert not feedback.failed and feedback.lateral_source_ns == recovery_stamp


@pytest.mark.parametrize(
  'fault',
  [
    'no-eps',
    'permanent-eps',
    'can-invalid',
    'timeout',
    'brake-without-eps',
    'gas-without-eps',
    'regen',
    'door',
    'seatbelt',
    'reverse',
    'main-off',
    'not-latched',
    'producer',
    'epoch',
    'session',
    'expired',
    'stale-source',
    'no-baseline',
    'native-rx',
    'native-heartbeat',
    'native-fault',
    'native-config',
    'native-long',
    'axis-long',
    'hard-event',
    'cancel',
  ],
)
@pytest.mark.parametrize('gas', [False, True])
def test_stock_eps_cannot_hide_independent_fault_or_borrow_another_owner(fault, gas):
  cp, cs, feedback, peers, native, stamp = case()
  latched, producer = True, PRODUCER
  cs.gasPressed = gas
  if fault == 'gas-without-eps':
    cs.gasPressed = True
    cs.steerFaultTemporary = False
  elif fault == 'brake-without-eps':
    cs.brakePressed = True
    cs.steerFaultTemporary = False
  elif fault in ('no-eps', 'permanent-eps', 'can-invalid', 'timeout', 'brake-without-eps', 'gas', 'regen', 'door', 'seatbelt'):
    field = {
      'no-eps': 'steerFaultTemporary',
      'permanent-eps': 'steerFaultPermanent',
      'can-invalid': 'canValid',
      'timeout': 'canTimeout',
      'brake': 'brakePressed',
      'gas': 'gasPressed',
      'regen': 'regenBraking',
      'door': 'doorOpen',
      'seatbelt': 'seatbeltUnlatched',
    }[fault]
    setattr(cs, field, fault not in ('no-eps', 'can-invalid'))
  elif fault == 'reverse':
    cs.gearShifter = 'reverse'
  elif fault == 'main-off':
    cs.cruiseState.available = False
  elif fault == 'not-latched':
    before = dict(vars(feedback))
    assert not feedback._temporary_steering_intent_current(
      peers, cp, cs, peers['aolAxisState'], stamp, stamp, latched=False, producer=producer, signature=feedback.signature
    )
    assert vars(feedback) == before
    return
  elif fault == 'producer':
    producer = 'other-card'
  elif fault in ('epoch', 'session', 'stale-source', 'axis-long'):
    field, value = {
      'epoch': ('intentIntegrityEpoch', 2),
      'session': ('sessionId', 'other-axis'),
      'stale-source': ('sourceCarStateMonoTime', stamp - 30_000_001),
      'axis-long': ('desiredLongitudinal', True),
    }[fault]
    change(peers, 'aolAxisState', lambda axis: setattr(axis, field, value))
  elif fault == 'expired':
    stamp += 200_000_001
  elif fault == 'no-baseline':
    feedback.lateral_only_baseline = False
  elif fault == 'native-config':
    native_change(peers, native, safetyParam=native.safetyParam ^ 1)
  elif fault == 'native-long':
    native_change(peers, native, requestedLongitudinal=True)
  elif fault in ('native-rx', 'native-heartbeat', 'native-fault'):
    updates = {'native-rx': {'safetyRxChecksInvalid': True}, 'native-heartbeat': {'heartbeatLost': True}, 'native-fault': {'faults': 8}}[fault]
    native_change(peers, native, pandaInventory=(replace(native.pandaInventory[0], **updates),))
  elif fault == 'hard-event':
    change(peers, 'onroadEvents', lambda events: setattr(events[0], 'immediateDisable', True))
  elif fault == 'cancel':
    change(peers, 'onroadEvents', lambda events: setattr(events[0], 'name', 'buttonCancel'))
  assert feedback.observe(peers, cp, cs, stamp, latched=latched, intent_producer_session=producer)
  assert feedback.failed
