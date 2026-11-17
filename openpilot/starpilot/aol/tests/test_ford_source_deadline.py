"""Ford2b recorded held-brake source cutoff; private actual-caller reproduction."""
from contextlib import ExitStack
from unittest import mock

import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.aol import BP_LATERAL
from opendbc.car.ford.values import CAR
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_ordinary_source_availability import settings

SOURCE=10_020_000_000
PUBLISHED_OFFSET=5_920_000
SOURCE_AGE=33_452_000

def profile(bluepilot=False):
  fingerprint = gen_empty_fingerprint()
  fingerprint[0].update({0x5A: 8, 0x3A6: 8, 0x3A7: 8})
  fingerprint[2].update({0x3D6: 8, 0x186: 8})
  cp = CarInterface.get_params(CAR.FORD_MUSTANG_MACH_E_MK1, fingerprint, [], True, False, False)
  cp.alternativeExperience = 32
  assert cp.flags == 3 and cp.safetyConfigs[0].safetyParam == 3
  if bluepilot:
    cp.safetyConfigs[0].safetyParam |= BP_LATERAL
  return cp

def callers(case):
  sd, controls, drive = case._authority_callers(False, cp=profile(), genuine_events=True)
  def stationary(tick, **kwargs):
    state = kwargs.get('observed_state')
    if state is None:
      state = fixture.car_state(brake=kwargs.get('brake', False))
    state.vEgo = state.vEgoRaw = 0.
    state.standstill = True
    kwargs['observed_state'] = state
    return drive(tick, **kwargs)
  return sd, controls, stationary

@pytest.mark.parametrize('pedal', ['brake', 'gas'])
@pytest.mark.parametrize('socket_timeout', [False, True])
@pytest.mark.parametrize('published_offset,source_age', [(PUBLISHED_OFFSET, SOURCE_AGE)])
def test_ford_held_brake_keeps_only_the_recorded_unexpired_proof(socket_timeout, published_offset, source_age, pedal):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, controls, drive = callers(case)
    for tick in (0, 1):
      stamp = 10_000_000_000 + tick * 10_000_000
      drive(tick, brake=pedal == 'brake', gas=pedal == 'gas', long_allowed=False, **settings('lateral_only', stamp, tick + 1))
    old, state, command = drive(2, state_stamp=SOURCE, offset_ns=published_offset, brake=pedal == 'brake', gas=pedal == 'gas',
      long_allowed=False, **settings('lateral_only', SOURCE, 3))
    assert old.lateralActive and command.latActive and not old.longitudinalActive and not command.longActive
    assert not state.enabled
    deadline = old.validUntilMonoTime
    assert deadline == SOURCE + published_offset + 30_000_000
    old_data = old.to_dict()
    sd.car_state_poller = mock.Mock()
    sd.car_state_poller.poll.return_value = []
    axis, state, command = drive(5, timeout=socket_timeout, state_stamp=SOURCE, offset_ns=source_age - 30_000_000,
      brake=pedal == 'brake', gas=pedal == 'gas', long_allowed=False, **settings('lateral_only', SOURCE, 3))
    assert sd.aol_axis_publication_deferred, 'FORD_HELD_BRAKE_SOURCE_WAIT_EXCLUDED'
    assert axis.to_dict() == old_data and axis.validUntilMonoTime == deadline
    assert not state.enabled and not command.longActive
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=deadline + 1):
      expired, _ = controls.state_control()
    assert not expired.latActive and not expired.longActive
    fresh = SOURCE + 40_000_000
    axis, state, command = drive(6, state_stamp=fresh, brake=pedal == 'brake', gas=pedal == 'gas',
      long_allowed=False, **settings('lateral_only', fresh, 4))
    assert axis.lateralActive and command.latActive and not state.enabled and not command.longActive, (axis.to_dict(), sd.events.names, sd.aol_axis_decision)


@pytest.mark.parametrize('fault', ['new_brake', 'lateral_denied', 'rx', 'steer_fault', 'source_invalid'])
def test_ford_held_brake_extension_does_not_preserve_a_new_fault(fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = callers(case)
    held = fault != 'new_brake'
    for tick in (0, 1, 2):
      stamp = 10_000_000_000 + tick * 10_000_000
      drive(tick, brake=held, state_stamp=stamp, offset_ns=PUBLISHED_OFFSET if tick == 2 else 0,
        long_allowed=False, **settings('lateral_only', stamp, tick + 1))
    sd.car_state_poller = mock.Mock()
    sd.car_state_poller.poll.return_value = []
    options = settings('lateral_only', SOURCE, 3)
    changes = {}
    if fault == 'lateral_denied':
      options['allowed'] = False
    elif fault == 'rx':
      changes['panda_fault'] = True
    elif fault in ('steer_fault', 'source_invalid'):
      state = fixture.car_state(brake=True)
      if fault == 'steer_fault':
        state.steerFaultPermanent = True
      else:
        state.canValid = False
      changes['observed_state'] = state
    _, _, command = drive(5, state_stamp=SOURCE, offset_ns=SOURCE_AGE - 30_000_000,
      brake=True, long_allowed=False, **options, **changes)
    assert not sd.aol_axis_publication_deferred
    assert not command.longActive


@pytest.mark.parametrize('corruption', ['retained_valid', 'retained_source', 'queue_invalid', 'expired', 'native_denied', 'native_rx'])
def test_timeout_cannot_borrow_an_untrusted_or_expired_receipt(corruption):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = callers(case)
    for tick in (0, 1, 2):
      stamp = 10_000_000_000 + tick * 10_000_000
      old, _, command = drive(tick, brake=True, long_allowed=False, state_stamp=stamp,
        offset_ns=5_631_474 if tick == 2 else 0, **settings('lateral_only', stamp, tick + 1))
    assert old.lateralActive and command.latActive
    sd.car_state_poller = mock.Mock()
    sd.car_state_poller.poll.return_value = []
    original = sd.defer_expired_axis_publication
    def corrupt_receipt(cs, initial_state, **kwargs):
      if corruption == 'retained_valid':
        sd.car_state_retained_valid = False
      elif corruption == 'retained_source':
        sd.car_state_retained_source_ns -= 1
      elif corruption == 'queue_invalid':
        sd.car_state_queue_invalid = True
      return original(cs, initial_state, **kwargs)
    options = settings('lateral_only', SOURCE, 3)
    if corruption == 'native_denied':
      options['allowed'] = False
    elif corruption == 'native_rx':
      options['panda_fault'] = True
    with mock.patch.object(sd, 'defer_expired_axis_publication', side_effect=corrupt_receipt) as intercept:
      _, _, command = drive(6 if corruption == 'expired' else 5, timeout=True, state_stamp=SOURCE,
        offset_ns=0 if corruption == 'expired' else 894_772, brake=True, long_allowed=False, **options)
    if corruption in ('retained_valid', 'retained_source', 'queue_invalid'):
      assert intercept.called
    assert not sd.aol_axis_publication_deferred
    assert not command.latActive and not command.longActive

@pytest.mark.parametrize('fault', ['relay', 'native_invalid', 'new_gas', 'button', 'producer', 'session', 'identity', 'safety_word'])
def test_ford_hold_does_not_borrow_changed_authority(fault):
  from dataclasses import replace

  from openpilot.starpilot.aol.wire import decode_intent, encode_intent
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = callers(case)
    for tick in (0, 1, 2):
      stamp = 10_000_000_000 + tick * 10_000_000
      old, _, command = drive(tick, brake=True, long_allowed=False, state_stamp=stamp,
        offset_ns=PUBLISHED_OFFSET if tick == 2 else 0, **settings('lateral_only', stamp, tick + 1))
    assert old.lateralActive and command.latActive
    sd.car_state_poller = mock.Mock()
    sd.car_state_poller.poll.return_value = []
    options = settings('lateral_only', SOURCE, 3)
    changes = {}
    if fault == 'relay':
      changes['relay_fault'] = True
    elif fault == 'native_invalid':
      changes['native_valid'] = False
    elif fault == 'new_gas':
      changes['gas'] = True
    elif fault == 'button':
      state = fixture.car_state(brake=True)
      state.buttonEvents = [{'type': 'cancel', 'pressed': True}]
      changes['observed_state'] = state
    elif fault == 'producer':
      msg = options['intent_message'].copy()
      value = decode_intent(msg.aolIntentWire)
      msg.logMonoTime = SOURCE + SOURCE_AGE
      msg.aolIntentWire = encode_intent(replace(value, producerSessionId='foreign', sequence=4,
        observedMonoTime=msg.logMonoTime, validUntilMonoTime=msg.logMonoTime+200_000_000))
      options['intent_message'] = msg
      pending = [msg.as_reader()]
      receive = messaging.recv_one_or_none
      def queued(sock):
        if sock is sd.aol_intent_companion.sock:
          return pending.pop(0) if pending else None
        return receive(sock)
      cleanup.enter_context(mock.patch('openpilot.cereal.messaging.recv_one_or_none', side_effect=queued))
    elif fault == 'session':
      sd.aol_session_id = 'foreign'
    else:
      cp = sd.CP.as_builder()
      if fault == 'identity':
        cp.carFingerprint = 'FORD_F_150_MK14'
      else:
        cp.safetyConfigs[0].safetyParam = 18
      sd.CP = cp.as_reader()
    _, _, command = drive(5, state_stamp=SOURCE, offset_ns=SOURCE_AGE-30_000_000,
      brake=True, long_allowed=False, **options, **changes)
    assert not sd.aol_axis_publication_deferred
    assert not command.longActive

@pytest.mark.parametrize('edge', ['main_off', 'cancel', 'brake_release'])
@pytest.mark.parametrize('native_allowed', [False, True])
def test_fresh_successor_cannot_reuse_a_held_brake_proof_across_new_edges(edge, native_allowed):
  from dataclasses import replace

  from openpilot.starpilot.aol.wire import decode_intent, encode_intent
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = callers(case)
    for tick in (0, 1, 2):
      stamp = 10_000_000_000 + tick * 10_000_000
      old, _, command = drive(tick, brake=True, long_allowed=False, state_stamp=stamp,
        offset_ns=PUBLISHED_OFFSET if tick == 2 else 0, **settings('lateral_only', stamp, tick+1))
    assert command.latActive
    old_deadline = old.validUntilMonoTime
    sd.car_state_poller = mock.Mock()
    sd.car_state_poller.poll.return_value = []
    held, _, _ = drive(5, timeout=True, state_stamp=SOURCE, offset_ns=SOURCE_AGE-30_000_000,
      brake=True, long_allowed=False, **settings('lateral_only', SOURCE, 3))
    assert sd.aol_axis_publication_deferred and held.validUntilMonoTime == old_deadline
    fresh = SOURCE+40_000_000
    state = fixture.car_state(brake=edge != 'brake_release')
    if edge != 'brake_release':
      state.buttonEvents = [{'type': 'mainCruise' if edge == 'main_off' else 'cancel', 'pressed': True}]
    options = settings('lateral_only', fresh, 4)
    if edge != 'brake_release':
      wire = options['intent_message']
      wire.aolIntentWire = encode_intent(replace(decode_intent(wire.aolIntentWire), allowedLatch=False, lateralArmed=False))
      options['latched'] = False
    # A fresh physical change cannot borrow the old native permission either.
    options['allowed'] = native_allowed
    axis, _, command = drive(6, state_stamp=fresh, observed_state=state,
      long_allowed=False, **options)
    assert not sd.aol_axis_publication_deferred
    assert not command.longActive and not axis.longitudinalActive
    if edge != 'brake_release' or not native_allowed:
      assert not command.latActive and not axis.lateralActive
    else:
      assert axis.sourceCarStateMonoTime == fresh
      assert axis.validUntilMonoTime != old_deadline
    assert old.validUntilMonoTime == old_deadline < fresh


@pytest.mark.parametrize('socket_timeout,fault', [(timeout, fault)
  for fault in ('none', 'lat_denied', 'rx', 'relay', 'brake', 'gas_release', 'button', 'expired')
  for timeout in ((False,) if fault in ('brake', 'gas_release', 'button') else (False, True))])
@pytest.mark.parametrize('bluepilot', [False, True])
def test_combined_gas_override_keeps_only_its_original_lateral_proof(bluepilot, socket_timeout, fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, controls, drive = case._authority_callers(True, cp=profile(bluepilot), genuine_events=True)
    cs = fixture.car_state()
    cs.cruiseState.enabled = True
    for tick in range(3):
      stamp = 10_000_000_000 + tick * 10_000_000
      old, state, command = drive(tick, observed_state=cs, gas=True, state_stamp=stamp,
        offset_ns=PUBLISHED_OFFSET if tick == 2 else 0, long_allowed=False, **settings('combined', stamp, tick+1))
    assert state.enabled and old.lateralActive and old.desiredLongitudinal and not old.longitudinalActive
    assert command.latActive and not command.longActive
    previous = old.to_dict()
    sd.car_state_poller = mock.Mock()
    sd.car_state_poller.poll.return_value = []
    changes = settings('combined', SOURCE, 3)
    if fault == 'lat_denied':
      changes['allowed'] = False
    elif fault == 'rx':
      changes['panda_fault'] = True
    elif fault == 'relay':
      changes['relay_fault'] = True
    elif fault == 'brake':
      cs.brakePressed = True
    elif fault == 'button':
      cs.buttonEvents = [{'type': 'cancel', 'pressed': True}]
    held, state, command = drive(6 if fault == 'expired' else 5, timeout=socket_timeout, observed_state=cs,
      gas=fault != 'gas_release', state_stamp=SOURCE, offset_ns=SOURCE_AGE-30_000_000,
      long_allowed=False, **changes)
    if fault != 'none':
      assert not sd.aol_axis_publication_deferred
      assert not command.latActive and not command.longActive
      return
    assert sd.aol_axis_publication_deferred
    assert held.to_dict() == previous and state.enabled and not command.longActive
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=old.validUntilMonoTime+1):
      expired, _ = controls.state_control()
    assert not expired.latActive and not expired.longActive
    fresh = SOURCE+40_000_000
    axis, state, command = drive(6, observed_state=cs, gas=True, state_stamp=fresh,
      long_allowed=False, **settings('combined', fresh, 4))
    assert state.enabled and axis.lateralActive and command.latActive and not command.longActive


@pytest.mark.parametrize('gas', [False, True])
@pytest.mark.parametrize('after_deadline', [False, True])
@pytest.mark.parametrize('pulse', ['healthy', 'cancel_then_enable', 'brake_then_clear', 'permanent_eps',
                                  'invalid_then_valid', 'missing_intent', 'lateral_denied', 'temporary_eps'])
def test_ford_merge_reconciles_late_input_before_axis_decision(gas, after_deadline, pulse):
  from collections import deque
  from openpilot.starpilot.aol.tests.test_carstate_queue import message
  from openpilot.starpilot.aol.tests.test_input_deadline_continuity import intent

  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = case._authority_callers(True, cp=profile(bluepilot=True), genuine_events=True)
    cs = fixture.car_state()
    cs.cruiseState.enabled = True
    for tick in range(3):
      stamp = 10_000_000_000 + tick * 10_000_000
      old, _, command = drive(tick, observed_state=cs, gas=gas, state_stamp=stamp,
        offset_ns=PUBLISHED_OFFSET if tick == 2 else 0, long_allowed=not gas,
        **settings('combined', stamp, tick+1))
    assert old.lateralActive and command.latActive and old.longitudinalActive == (not gas)
    source = SOURCE
    fresh = source + (36_000_000 if after_deadline else 29_563_312)
    observed = source + (40_000_000 if after_deadline else 32_956_912)
    assert (observed > old.validUntilMonoTime) == after_deadline
    now = [source + 27_000_000]
    queue, wires = deque(), deque()
    original_receive = messaging.recv_one_or_none
    original_car_update = sd._update_car_event_sample
    injected = False

    def receive(sock):
      if sock is sd.car_state_sock:
        return queue.popleft() if queue else None
      if sock is sd.aol_intent_companion.sock:
        return wires.popleft() if wires else None
      return original_receive(sock)

    def car_update(sample, previous):
      nonlocal injected
      result = original_car_update(sample, previous)
      if not injected:
        injected = True
        late = fixture.car_state()
        late.cruiseState.available = late.cruiseState.enabled = True
        late.gasPressed = gas
        if pulse == 'cancel_then_enable':
          late.buttonEvents = [{'type': 'cancel', 'pressed': True}]
        elif pulse == 'brake_then_clear':
          late.brakePressed = True
        elif pulse == 'permanent_eps':
          late.steerFaultPermanent = True
        elif pulse == 'temporary_eps':
          late.steerFaultTemporary = True
        queue.append(message(fresh, late, valid=pulse != 'invalid_then_valid'))
        if pulse != 'missing_intent':
          wires.append(intent(fresh, 4, latched=pulse != 'cancel_then_enable').as_reader())
        if pulse in ('cancel_then_enable', 'brake_then_clear', 'invalid_then_valid'):
          clear = fixture.car_state()
          clear.cruiseState.available = clear.cruiseState.enabled = True
          clear.gasPressed = gas
          clear.buttonEnable = pulse == 'cancel_then_enable'
          queue.append(message(fresh + 100_000, clear))
          wires.append(intent(fresh + 100_000, 5).as_reader())
        now[0] = observed
      return result

    sd.car_state_poller = mock.Mock()
    sd.car_state_poller.poll.return_value = []
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', side_effect=lambda: now[0]), \
         mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one_or_none', side_effect=receive), \
         mock.patch.object(sd, '_update_car_event_sample', side_effect=car_update) as car_events, \
         mock.patch.object(sd.state_machine, 'update', wraps=sd.state_machine.update) as transition:
      axis, state, command = drive(4, offset_ns=7_000_000, observed_state=cs,
        gas=gas, state_stamp=source, long_allowed=not gas,
        **dict(settings('combined', source, 3), allowed=pulse != 'lateral_denied'))
    assert not queue and transition.call_count == 1
    assert car_events.call_count == (3 if pulse.endswith(('enable', 'clear', 'valid')) else 2)
    assert old.validUntilMonoTime == source + PUBLISHED_OFFSET + 30_000_000
    if pulse == 'healthy':
      assert axis.sourceCarStateMonoTime == fresh and sd.aol_last_intent.carStateLogMonoTime == fresh
      assert axis.lateralActive and command.latActive and state.enabled
      assert axis.longitudinalActive == (not gas) and command.longActive == (not gas)
      assert log.OnroadEvent.EventName.commIssue not in sd.events.names
      assert not sd.aol_authority_lost
    else:
      if pulse not in ('brake_then_clear', 'temporary_eps', 'lateral_denied'):
        assert not axis.lateralActive and not command.latActive
      if pulse == 'temporary_eps':
        assert not axis.lateralActive and not command.latActive
      if pulse == 'lateral_denied':
        assert not axis.lateralActive and not command.latActive
      if pulse in ('cancel_then_enable', 'brake_then_clear', 'permanent_eps', 'invalid_then_valid', 'missing_intent'):
        assert not axis.longitudinalActive and not command.longActive
      if pulse == 'cancel_then_enable':
        assert log.OnroadEvent.EventName.buttonCancel in sd.events.names
        assert not sd.events.contains(fixture.ET.ENABLE)
      if pulse == 'invalid_then_valid':
        assert axis.sourceCarStateMonoTime == 0 and not sd.conditional_car_state_valid
