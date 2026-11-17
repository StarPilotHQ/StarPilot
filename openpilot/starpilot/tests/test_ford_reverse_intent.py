"""Controlled wire fault vectors; actual native/SD regression is the separate producer probe."""

from dataclasses import replace

import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.values import CAR
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.runtime import current_native, decide_axes
from openpilot.starpilot.aol.tests.test_gm_held_native import Peers
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety
from openpilot.starpilot.car.ford.reverse_intent import retain_reverse_intent

NOW = 10_000_000_000
PRODUCER = 'ford-card'
SESSION = 'ford-axis'


def cp_state():
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  with OpenpilotPrefix():
    cp = CarInterface.get_params(CAR.FORD_ESCAPE_MK4, fp, [], False, False, False)
  cp.alternativeExperience = 32
  assert cp.safetyConfigs[0].safetyParam == 0 and not cp.openpilotLongitudinalControl
  cs = structs.CarState(canValid=True, canTimeout=False, vEgo=15.0, gearShifter=structs.CarState.GearShifter.drive)
  cs.cruiseState.available = True
  return cp, cs


def sample(cp, source, observed, sequence, *, active=True, requested=True, allowed=True, event_stamp=NOW):
  cfg = cp.safetyConfigs[0]
  slot = PandaSlot(0, 'ford-panda', cfg.safetyModel.raw, cfg.safetyParam, 32, False)
  native = SafetyState(
    1,
    True,
    observed,
    observed + 200_000_000,
    cfg.safetyModel.raw,
    cfg.safetyParam,
    allowed,
    False,
    requested,
    False,
    'ford-panda',
    SESSION,
    sourcePandaStatesMonoTime=observed,
    pandaInventory=(slot,),
  )
  wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=observed)
  wire.aolSafetyWire = encode_safety(native)
  axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=source)
  a = axis.aolAxisState
  a.sessionId = a.faultSessionId = SESSION
  a.sequence, a.qualified, a.faultReason = sequence, True, 'none'
  a.observedMonoTime, a.sourceCarStateMonoTime, a.validUntilMonoTime = source, source, source + 30_000_000
  a.intentProducerSessionId, a.intentIntegrityEpoch = PRODUCER, 1
  a.faultEventMonoTime = event_stamp
  a.desiredLateral = a.lateralActive = active
  a.desiredLongitudinal = a.longitudinalActive = False
  a.nativeAcknowledged = True
  a.mode = 'lateralOnly' if active else 'off'
  panda = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=observed)
  panda.pandaStates[0].safetyModel, panda.pandaStates[0].safetyParam = cfg.safetyModel, cfg.safetyParam
  panda.pandaStates[0].alternativeExperience = 32
  events = messaging.new_message('onroadEvents', 1, valid=True, logMonoTime=event_stamp)
  events.onroadEvents[0].name, events.onroadEvents[0].userDisable = 'pcmDisable', True
  return Peers([wire.as_reader(), axis.as_reader(), panda.as_reader(), events.as_reader()]), native


def baseline():
  cp, cs = cp_state()
  f = TransportPauseFeedback(cp)
  sm, _ = sample(cp, NOW, NOW, 1)
  assert not f.observe(sm, cp, cs, NOW, latched=True, intent_producer_session=PRODUCER)
  assert f.lateral_only_baseline
  return cp, cs, f


def change(sm, service, update):
  event = sm.messages[service].as_builder()
  update(getattr(event, service))
  sm.messages[service] = event.as_reader()


def native_change(sm, native, **updates):
  native = replace(native, **updates)
  event = sm.messages['aolSafetyWire'].as_builder()
  event.aolSafetyWire = encode_safety(native)
  sm.messages['aolSafetyWire'] = event.as_reader()
  sm.logMonoTime['aolSafetyWire'] = native.observedMonoTime
  return native


def test_first_reverse_join_retains_only_intent_without_renewing_prior_leases():
  cp, cs, f = baseline()
  cs.gearShifter = structs.CarState.GearShifter.reverse
  sm, native = sample(cp, NOW + 10_000_000, NOW + 20_000_000, 2, allowed=False)
  before = (f.healthy_feedback_ns, f.healthy_native_ns, f.lateral_baseline_ns, f.lateral_source_ns, f.lateral_native_ns)
  stamps = dict(sm.logMonoTime)
  assert not f.observe(sm, cp, cs, NOW + 20_000_000, latched=True, intent_producer_session=PRODUCER)
  assert not f.failed and f.sequence == 1
  assert before == (f.healthy_feedback_ns, f.healthy_native_ns, f.lateral_baseline_ns, f.lateral_source_ns, f.lateral_native_ns)
  assert sm.logMonoTime == stamps and current_native(sm, cp, now_ns=NOW + 20_000_000, axis_session_id=SESSION) == native
  intent = type('Intent', (), {'allowedLatch': True, 'pauseLateral': False, 'pauseLongitudinal': False})()
  axes = decide_axes(
    standard_lateral=False,
    standard_longitudinal=False,
    intent=intent,
    native=native,
    car_state=cs,
    initialized=True,
    model_ready=True,
    no_entry=False,
    immediate_disable=False,
    dm_lockout=False,
    pause_brake_mps=0.0,
  )
  assert not axes.desired_lateral and not axes.desired_longitudinal and not axes.lateral_active and not axes.longitudinal_active


def test_actual_off_proof_and_drive_recovery_use_unchanged_native_admission():
  cp, cs, f = baseline()
  cs.gearShifter = structs.CarState.GearShifter.reverse
  sm, _ = sample(cp, NOW + 10_000_000, NOW + 20_000_000, 2, allowed=False)
  assert not f.observe(sm, cp, cs, NOW + 20_000_000, latched=True, intent_producer_session=PRODUCER)
  sm, native = sample(cp, NOW + 20_000_000, NOW + 30_000_000, 3, active=False, requested=False, allowed=False)
  assert not retain_reverse_intent(f, sm, cp, cs, sm['aolAxisState'], NOW + 30_000_000, latched=True, producer=PRODUCER)
  assert not f.observe(sm, cp, cs, NOW + 30_000_000, latched=True, intent_producer_session=PRODUCER)
  cs.gearShifter = structs.CarState.GearShifter.drive
  assert not f.observe(sm, cp, cs, NOW + 30_000_000, latched=True, intent_producer_session=PRODUCER)
  assert not native.lateralAllowed and not native.longitudinalAllowed
  fresh, grant = sample(cp, NOW + 40_000_000, NOW + 40_000_000, 4)
  assert not f.observe(fresh, cp, cs, NOW + 40_000_000, latched=True, intent_producer_session=PRODUCER)
  assert current_native(fresh, cp, now_ns=NOW + 40_000_000, axis_session_id=SESSION).lateralAllowed
  assert not grant.longitudinalAllowed


NEGATIVES = (
  'drive',
  'neutral',
  'park',
  'can-invalid',
  'timeout',
  'eps-temporary',
  'eps-permanent',
  'sensor',
  'acc-fault',
  'gas',
  'steering-disengage',
  'main-off',
  'button',
  'wrong-brand',
  'long-cp',
  'unlatched',
  'failed',
  'paused',
  'pending',
  'wrong-producer',
  'wrong-epoch',
  'wrong-session',
  'wrong-fault-session',
  'axis-invalid',
  'axis-unacknowledged',
  'axis-unqualified',
  'axis-stale',
  'source-future',
  'source-stale',
  'sequence-replay',
  'old-baseline',
  'native-stale',
  'native-retrograde',
  'native-future',
  'native-incompatible',
  'native-param',
  'native-inventory-fault',
  'native-heartbeat-lost',
  'native-serial',
  'native-controls',
  'native-long-request',
  'native-long-grant',
  'legacy-native',
  'panda-config',
  'panda-rx-invalid',
  'panda-heartbeat',
  'panda-relay',
  'panda-controls',
  'panda-unseen',
  'event-stale',
  'event-unseen',
  'event-critical',
  'event-unrelated',
  'axis-critical',
  'axis-long-desire',
  'axis-long-active',
  'missing-baseline',
)


@pytest.mark.parametrize('fault', NEGATIVES)
def test_reverse_join_never_bypasses_current_fault_source_identity_or_long_veto(fault):
  cp, cs, f = baseline()
  cs.gearShifter = structs.CarState.GearShifter.reverse
  now = NOW + 20_000_000
  sm, native = sample(cp, NOW + 10_000_000, now, 2, allowed=False)
  latched, producer = True, PRODUCER
  gears = {'drive': 'drive', 'neutral': 'neutral', 'park': 'park'}
  physical = {
    'can-invalid': ('canValid', False),
    'timeout': ('canTimeout', True),
    'eps-temporary': ('steerFaultTemporary', True),
    'eps-permanent': ('steerFaultPermanent', True),
    'sensor': ('vehicleSensorsInvalid', True),
    'acc-fault': ('accFaulted', True),
    'gas': ('gasPressed', True),
    'steering-disengage': ('steeringDisengage', True),
  }
  if fault in gears:
    cs.gearShifter = getattr(structs.CarState.GearShifter, gears[fault])
  elif fault in physical:
    setattr(cs, *physical[fault])
  elif fault == 'main-off':
    cs.cruiseState.available = False
  elif fault == 'button':
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=True)]
  elif fault == 'wrong-brand':
    cp.brand = 'gm'
  elif fault == 'long-cp':
    cp.openpilotLongitudinalControl = True
  elif fault == 'unlatched':
    latched = False
  elif fault in ('failed', 'paused', 'pending'):
    setattr(f, {'failed': 'failed', 'paused': 'paused', 'pending': 'pending_event'}[fault], True if fault != 'pending' else NOW)
  elif fault == 'wrong-producer':
    producer = 'other-card'
  elif fault == 'wrong-epoch':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', 2))
  elif fault == 'wrong-session':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'sessionId', 'other-axis'))
  elif fault == 'wrong-fault-session':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'faultSessionId', 'other-axis'))
  elif fault == 'axis-invalid':
    sm.valid['aolAxisState'] = False
  elif fault == 'axis-unacknowledged':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'nativeAcknowledged', False))
  elif fault == 'axis-unqualified':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'qualified', False))
  elif fault == 'axis-stale':
    now = NOW + 50_000_001
  elif fault == 'source-future':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'sourceCarStateMonoTime', NOW + 30_000_000))
  elif fault == 'source-stale':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'sourceCarStateMonoTime', NOW - 20_000_000))
  elif fault == 'sequence-replay':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'sequence', 1))
  elif fault == 'old-baseline':
    f.lateral_source_ns = NOW - 30_000_000
  elif fault == 'native-stale':
    native = native_change(sm, native, observedMonoTime=NOW - 210_000_000, validUntilMonoTime=NOW - 10_000_000)
  elif fault == 'native-retrograde':
    native = native_change(sm, native, observedMonoTime=NOW - 1)
  elif fault == 'native-future':
    native = native_change(sm, native, observedMonoTime=now + 1)
  elif fault == 'native-incompatible':
    native = native_change(sm, native, compatible=False)
  elif fault == 'native-param':
    native = native_change(sm, native, safetyParam=cp.safetyConfigs[0].safetyParam ^ 8)
  elif fault in ('native-inventory-fault', 'native-heartbeat-lost', 'native-controls'):
    field = {'native-inventory-fault': 'safetyRxChecksInvalid', 'native-heartbeat-lost': 'heartbeatLost', 'native-controls': 'controlsAllowed'}[fault]
    native = native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], **{field: True}),))
  elif fault == 'native-serial':
    native = native_change(sm, native, pandaSerial='other-panda', pandaInventory=(replace(native.pandaInventory[0], hardwareSerial='other-panda'),))
    assert current_native(sm, cp, now_ns=now, axis_session_id=SESSION) is not None
  elif fault == 'native-long-request':
    native = native_change(sm, native, requestedLongitudinal=True)
  elif fault == 'native-long-grant':
    native = native_change(sm, native, longitudinalAllowed=True)
  elif fault == 'legacy-native':
    native = native_change(sm, native, pandaInventory=(), sourcePandaStatesMonoTime=0)
  elif fault in ('panda-config', 'panda-rx-invalid', 'panda-heartbeat', 'panda-controls'):
    field, value = {
      'panda-config': ('safetyParam', cp.safetyConfigs[0].safetyParam ^ 8),
      'panda-rx-invalid': ('safetyRxChecksInvalid', True),
      'panda-heartbeat': ('heartbeatLost', True),
      'panda-controls': ('controlsAllowed', True),
    }[fault]
    change(sm, 'pandaStates', lambda ps: setattr(ps[0], field, value))
  elif fault == 'panda-relay':
    change(sm, 'pandaStates', lambda ps: setattr(ps[0], 'faults', ['relayMalfunction']))
  elif fault == 'panda-unseen':
    sm.seen['pandaStates'] = False
  elif fault == 'event-stale':
    sm.logMonoTime['onroadEvents'] = NOW - 1_500_000_000
  elif fault == 'event-unseen':
    sm.seen['onroadEvents'] = False
  elif fault == 'event-critical':
    change(sm, 'onroadEvents', lambda e: setattr(e[0], 'immediateDisable', True))
  elif fault == 'event-unrelated':
    change(sm, 'onroadEvents', lambda e: setattr(e[0], 'name', log.OnroadEvent.EventName.commIssue))
  elif fault == 'axis-critical':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'faultReason', 'critical'))
  elif fault == 'axis-long-desire':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'desiredLongitudinal', True))
  elif fault == 'axis-long-active':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'longitudinalActive', True))
  elif fault == 'missing-baseline':
    f.lateral_only_baseline = False
  else:
    raise AssertionError(fault)
  assert not retain_reverse_intent(f, sm, cp, cs, sm['aolAxisState'], now, latched=latched, producer=producer)
  if fault in (
    'drive',
    'gas',
    'eps-permanent',
    'native-heartbeat-lost',
    'native-param',
    'panda-rx-invalid',
    'wrong-producer',
    'wrong-epoch',
    'source-stale',
    'event-critical',
    'native-long-request',
  ):
    assert f.observe(sm, cp, cs, now, latched=latched, intent_producer_session=producer)
    assert f.failed


def test_newer_active_proof_cannot_extend_original_healthy_source_lease():
  cp, cs, f = baseline()
  cs.gearShifter = structs.CarState.GearShifter.reverse
  sm, _ = sample(cp, NOW + 10_000_000, NOW + 20_000_000, 2, allowed=False)
  assert not f.observe(sm, cp, cs, NOW + 20_000_000, latched=True, intent_producer_session=PRODUCER)
  newer, _ = sample(cp, NOW + 30_000_000, NOW + 40_000_000, 3, allowed=False)
  assert f.observe(newer, cp, cs, NOW + 40_000_000, latched=True, intent_producer_session=PRODUCER)
  assert f.failed and f.healthy_native_ns == NOW and f.lateral_source_ns == NOW


def test_reverse_qualification_keeps_malformed_configuration_fail_closed():
  assert not retain_reverse_intent(None, None, None, None, None, NOW, latched=True, producer=PRODUCER)


@pytest.mark.parametrize('event,immediate,soft,expected', [
  ('pedalPressed', False, False, True), ('pedalPressed', True, False, False),
  ('pedalPressed', False, True, False), ('controlsMismatch', True, False, False),
  ('steerUnavailable', True, False, False),
])
def test_brake_user_disable_at_reverse_join_retains_only_existing_lateral_intent(event, immediate, soft, expected):
  cp, cs, feedback = baseline()
  cs.gearShifter = structs.CarState.GearShifter.reverse
  sm, native = sample(cp, NOW + 10_000_000, NOW + 20_000_000, 2, allowed=False)
  change(sm, 'onroadEvents', lambda events: setattr(events[0], 'name', event))
  change(sm, 'onroadEvents', lambda events: setattr(events[0], 'immediateDisable', immediate))
  change(sm, 'onroadEvents', lambda events: setattr(events[0], 'softDisable', soft))
  before = (feedback.healthy_feedback_ns, feedback.healthy_native_ns, feedback.lateral_baseline_ns,
            feedback.lateral_source_ns, feedback.lateral_native_ns)
  assert retain_reverse_intent(feedback, sm, cp, cs, sm['aolAxisState'], NOW + 20_000_000,
                               latched=True, producer=PRODUCER) == expected
  assert before == (feedback.healthy_feedback_ns, feedback.healthy_native_ns, feedback.lateral_baseline_ns,
                    feedback.lateral_source_ns, feedback.lateral_native_ns)
  assert not native.lateralAllowed and not native.longitudinalAllowed
