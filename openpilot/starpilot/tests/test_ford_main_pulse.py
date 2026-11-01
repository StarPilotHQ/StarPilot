"""Ford auto-MAIN synthetic pulse must stay within its original input window."""

import pytest
from opendbc.car import structs
from dataclasses import replace
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.car.ford.aol import FordCardIntent


def state(*, available, **kwargs):
  return structs.CarState(
    canValid=True, cruiseState=structs.CarState.CruiseState(available=available), gearShifter=structs.CarState.GearShifter.drive, **kwargs
  ).as_reader()


@pytest.mark.parametrize('guard', ('late', 'backwards', 'cancel', 'main-off', 'can', 'permanent', 'acc', 'disabled', 'fault'))
def test_pending_main_cannot_replay_after_stale_or_disarming_input(guard):
  intent = FordCardIntent(
    None, AolSettings(enabled=True, pause_brake_mps=0.0, lkas_action=0, main_action=0, distance_actions=(0, 0, 0), cancel_actions=(0, 0, 0))
  )
  intent.update(state(available=False), now_ns=1_000_000_000, fault_active=False)
  intent.update(state(available=True), now_ns=1_010_000_000, fault_active=False)
  assert not intent.allowed_latch
  kwargs = {}
  available = True
  now = 1_020_000_000
  if guard == 'late':
    now = 1_040_000_001
  elif guard == 'backwards':
    now = 1_009_000_000
  elif guard == 'cancel':
    kwargs['buttonEvents'] = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=True)]
  elif guard == 'main-off':
    available = False
  elif guard == 'can':
    kwargs['canTimeout'] = True
  elif guard == 'permanent':
    kwargs['steerFaultPermanent'] = True
  elif guard == 'acc':
    kwargs['accFaulted'] = True
  elif guard == 'disabled':
    intent.settings = replace(intent.settings, enabled=False)
  intent.update(state(available=available, **kwargs), now_ns=now, fault_active=guard == 'fault')
  assert not intent.allowed_latch
  intent.update(state(available=True), now_ns=max(now, 1_020_000_000) + 10_000_000, fault_active=False)
  # A new actual MAIN rise is separate; stale pending delivery has been consumed.
  if guard != 'main-off':
    assert not intent.allowed_latch


def test_main_pulse_arms_once_after_prior_main_off_feedback_clears():
  intent = FordCardIntent(None, AolSettings(True, 0.0, 0, 0, (0, 0, 0), (0, 0, 0)))
  intent.update(state(available=False), now_ns=1_000_000_000, fault_active=False)
  intent.update(state(available=True), now_ns=1_010_000_000, fault_active=False)
  assert not intent.allowed_latch
  intent.update(state(available=True), now_ns=1_020_000_000, fault_active=False)
  assert intent.allowed_latch
  intent.update(state(available=True), now_ns=1_030_000_000, fault_active=False)
  assert intent.allowed_latch and not intent.main_pulse


@pytest.mark.parametrize('edge_fault', ('host', 'permanent', 'acc', 'disabled'))
def test_fault_at_original_main_edge_cannot_arm_after_fault_clears(edge_fault):
  intent = FordCardIntent(None, AolSettings(True, 0.0, 0, 0, (0, 0, 0), (0, 0, 0)))
  intent.update(state(available=False), now_ns=1_000_000_000, fault_active=False)
  if edge_fault == 'disabled':
    intent.settings = replace(intent.settings, enabled=False)
  cs = state(available=True, steerFaultPermanent=edge_fault == 'permanent', accFaulted=edge_fault == 'acc')
  intent.update(cs, now_ns=1_010_000_000, fault_active=edge_fault == 'host')
  intent.settings = replace(intent.settings, enabled=True)
  intent.update(state(available=True), now_ns=1_020_000_000, fault_active=False)
  assert not intent.allowed_latch
  intent.update(state(available=True), now_ns=1_030_000_000, fault_active=False)
  assert not intent.allowed_latch


@pytest.mark.parametrize('standard_enabled', [False, True])
@pytest.mark.parametrize('delay_ns', [10_000_000, 30_000_000, 30_000_001])
def test_first_main_arms_when_ordinary_cruise_precedes_pulse(standard_enabled, delay_ns):
  intent = FordCardIntent(None, AolSettings(True, 0.0, 0, 0, (0, 0, 0), (0, 0, 0)))
  intent.update(state(available=False), now_ns=1_000_000_000, fault_active=True)
  # Unit input for the readiness decision; the coupled regression obtains this
  # decision from actual Card/SD publications and native receive hooks.
  intent.main_idle_transition_qualified = True
  edge = 1_010_000_000
  intent.update(state(available=True), now_ns=edge, fault_active=True, standard_enabled=standard_enabled)
  assert intent.main_pending_ns == edge and not intent.allowed_latch
  intent.update(state(available=True), now_ns=edge + delay_ns, fault_active=False,
                standard_enabled=standard_enabled)
  assert intent.allowed_latch == (delay_ns <= 30_000_000)
  assert not intent.main_pending_ns


def startup_receipt(source_pause):
  from openpilot.starpilot.tests import test_ford_reverse_intent as wire
  cp, cs = wire.cp_state()
  intent = FordCardIntent(cp, AolSettings(True, 0.0, 0, 0, (0, 0, 0), (0, 0, 0)))
  intent.update(state(available=False), now_ns=wire.NOW - 10_000_000, fault_active=True)
  sm, native = wire.sample(cp, wire.NOW, wire.NOW + 10_000_000, 1, active=False, requested=False, allowed=False)
  def off(axis):
    axis.nativeAcknowledged = True
    if source_pause:
      axis.sourceCarStateMonoTime = 0
      axis.faultReason = 'availabilityPause'
  wire.change(sm, 'aolAxisState', off)
  wire.change(sm, 'onroadEvents', lambda events: setattr(events[0], 'name', 'wrongCarMode'))
  return intent, cp, cs, sm, native, wire.NOW + 10_000_000


def test_first_main_qualifies_only_current_off_receipt():
  from openpilot.starpilot.tests import test_ford_reverse_intent as wire
  intent, cp, cs, sm, native, now = startup_receipt(False)
  stamps = dict(sm.logMonoTime)
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=wire.PRODUCER)
  assert intent.main_idle_transition_qualified
  assert not intent.allowed_latch and sm.logMonoTime == stamps
  intent.update(cs.as_reader(), fault_active=True, now_ns=now)
  assert intent.main_pending_ns == now and not intent.allowed_latch
  # A genuine ongoing fault consumes the pending input, even after eligibility.
  intent.update(cs.as_reader(), fault_active=True, now_ns=now + 10_000_000)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now + 20_000_000)
  assert not intent.allowed_latch and not intent.main_pending_ns


STARTUP_REFUSALS = (
  'stale', 'future', 'deadline', 'producer', 'session', 'event-stamp', 'wrong-event', 'critical',
  'soft', 'can', 'timeout', 'permanent', 'temporary', 'acc', 'sensor', 'door', 'gear', 'disengage',
  'native-rx', 'heartbeat', 'native-request', 'native-grant', 'axis-active', 'axis-ack-no-native', 'panda-missing',
)


@pytest.mark.parametrize('source_pause', [False, True])
@pytest.mark.parametrize('fault', STARTUP_REFUSALS)
def test_first_main_does_not_admit_faulted_or_unowned_receipt(source_pause, fault):
  from openpilot.starpilot.tests import test_ford_reverse_intent as wire
  intent, cp, cs, sm, native, now = startup_receipt(source_pause)
  producer, panda_ready = wire.PRODUCER, True
  if fault == 'stale':
    now += 30_000_001
  elif fault == 'future':
    now = wire.NOW
  elif fault == 'deadline':
    wire.change(sm, 'aolAxisState', lambda axis: setattr(axis, 'validUntilMonoTime', wire.NOW + 30_000_001))
  elif fault == 'producer':
    producer = 'other-card'
  elif fault == 'session':
    wire.change(sm, 'aolAxisState', lambda axis: setattr(axis, 'faultSessionId', 'other-axis'))
  elif fault == 'event-stamp':
    wire.change(sm, 'aolAxisState', lambda axis: setattr(axis, 'faultEventMonoTime', wire.NOW - 1))
    if not source_pause:
      sm.logMonoTime['onroadEvents'] = wire.NOW + 1
  elif fault in ('wrong-event', 'critical', 'soft'):
    def event_change(events):
      events[0].name = 'controlsMismatch'
      events[0].immediateDisable = fault == 'critical'
      events[0].softDisable = fault == 'soft'
    wire.change(sm, 'onroadEvents', event_change)
  elif fault in ('can', 'timeout', 'permanent', 'temporary', 'acc', 'sensor', 'door', 'disengage'):
    key = {'can': 'canValid', 'timeout': 'canTimeout', 'permanent': 'steerFaultPermanent', 'temporary': 'steerFaultTemporary',
           'acc': 'accFaulted', 'sensor': 'vehicleSensorsInvalid', 'door': 'doorOpen', 'disengage': 'steeringDisengage'}[fault]
    setattr(cs, key, fault != 'can')
  elif fault == 'gear':
    cs.gearShifter = 'reverse'
  elif fault in ('native-rx', 'heartbeat'):
    wire.change(sm, 'pandaStates', lambda pandas: setattr(pandas[0], 'safetyRxChecksInvalid' if fault == 'native-rx' else 'heartbeatLost', True))
  elif fault in ('native-request', 'native-grant'):
    native = wire.native_change(sm, native, **{'requestedLateral' if fault == 'native-request' else 'lateralAllowed': True})
  elif fault == 'axis-active':
    wire.change(sm, 'aolAxisState', lambda axis: setattr(axis, 'lateralActive', True))
  elif fault == 'axis-ack-no-native':
    wire.change(sm, 'aolAxisState', lambda axis: setattr(axis, 'nativeAcknowledged', True))
    native = None
  elif fault == 'panda-missing':
    panda_ready = False
  before = native
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=panda_ready, now_ns=now, producer=producer)
  assert not intent.main_idle_transition_qualified
  assert native == before and not intent.allowed_latch
  intent.update(cs.as_reader(), fault_active=True, now_ns=now)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now + 10_000_000)
  assert not intent.allowed_latch and not intent.main_pending_ns


def test_source_zero_availability_fault_cannot_qualify_first_main():
  from openpilot.starpilot.tests import test_ford_reverse_intent as wire
  intent, cp, cs, sm, native, now = startup_receipt(True)
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=wire.PRODUCER)
  assert not intent.main_idle_transition_qualified
  intent.update(cs.as_reader(), fault_active=True, now_ns=now)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now + 10_000_000)
  assert not intent.allowed_latch and not intent.main_pending_ns


@pytest.mark.parametrize('acknowledged', [False, True])
@pytest.mark.parametrize('gas', [False, True])
def test_acknowledged_idle_main_arms_before_cruise_brake_withdrawal(acknowledged, gas):
  from openpilot.starpilot.tests import test_ford_reverse_intent as wire
  intent, cp, cs, sm, native, now = startup_receipt(False)
  cs.gasPressed = gas
  wire.change(sm, 'aolAxisState', lambda axis: setattr(axis, 'nativeAcknowledged', acknowledged))
  original_native = native
  original_stamps = dict(sm.logMonoTime)
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=wire.PRODUCER)
  assert intent.main_idle_transition_qualified
  assert not intent.allowed_latch and native == original_native and sm.logMonoTime == original_stamps
  intent.update(cs.as_reader(), fault_active=True, now_ns=now, standard_enabled=True)
  assert intent.main_pending_ns == now and not intent.allowed_latch
  intent.update(cs.as_reader(), fault_active=False, now_ns=now + 10_000_000, standard_enabled=True)
  assert intent.allowed_latch and not intent.pause_lateral and not intent.main_pending_ns
  assert intent.physical_arm_ns == now + 10_000_000
