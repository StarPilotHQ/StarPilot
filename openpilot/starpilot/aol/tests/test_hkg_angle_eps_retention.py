from contextlib import ExitStack

import pytest

from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from opendbc.car.hyundai.tests.test_stock_angle_aol import source_profile
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.ioniq5pe_long_profile import candidate
from opendbc.car.hyundai.canfd_angle_aol import MODELS


def profile(identity):
  if identity == "pe_long":
    return candidate(source_profile(CAR.HYUNDAI_IONIQ_5_PE), enabled=True, is_release=False)
  if identity == "ev9_long":
    from opendbc.car.hyundai.ev9_longitudinal import candidate as ev9_candidate
    return ev9_candidate(source_profile(CAR.KIA_EV9), enabled=True, is_release=False)
  return source_profile(CAR(identity))


@pytest.mark.parametrize('identity', [*sorted(MODELS), 'pe_long', 'ev9_long'])
@pytest.mark.parametrize('full', (False, True))
@pytest.mark.parametrize('steering', (False, True))
def test_angle_temporary_eps_native_withdrawal_keeps_intent(identity, full, steering):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    cp = profile(identity)
    assert cp is not None
    sd, _, drive = case._authority_callers(full, cp=cp, genuine_events=True)
    feedback = TransportPauseFeedback(sd.CP)
    sm = messaging.SubMaster(['aolAxisState', 'onroadEvents', 'aolSafetyWire', 'pandaStates'])
    temporary = fixture.car_state()
    temporary.vEgoRaw = temporary.vEgo
    temporary.cruiseState.enabled = full and cp.pcmCruise
    baseline = None
    for tick in range(9):
      fault = 3 <= tick < 8
      temporary.steerFaultTemporary = fault
      axis, _, command = drive(tick, observed_state=temporary, steering=steering if fault else False,
                               allowed=not fault, native_request_lat=True, native_request_long=full)
      now = 10_000_000_000 + tick * 10_000_000
      frames = [sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']]
      for name in ('aolSafetyWire', 'pandaStates'):
        event = messaging.new_message(name, len(sd.sm[name]), valid=sd.sm.valid[name], logMonoTime=sd.sm.logMonoTime[name])
        setattr(event, name, sd.sm[name])
        frames.append(event.as_reader())
      sm.update_msgs(now / 1e9, frames)
      assert bool(axis.lateralActive and command.latActive) == (not fault)
      assert not feedback.observe(sm, sd.CP, temporary, now, latched=True,
                                  intent_producer_session='authority-card'), 'PE_TEMPORARY_EPS_CLEARED_INTENT'
      assert not feedback.failed
      if tick == 2:
        baseline = (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns)
      elif fault:
        assert (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns) == baseline


@pytest.mark.parametrize('full', (False, True))
@pytest.mark.parametrize('steering', (False, True))
def test_actual_pe_card_keeps_latch_and_recovers_after_eps_refusal(full, steering):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    from opendbc.car.hyundai.tests.test_ioniq5pe_pending_startup import TestPEPendingStartup
    startup_case = TestPEPendingStartup(methodName='runTest')
    cleanup.callback(startup_case.doCleanups)
    startup_case.setUp()
    owner, ci, _ = startup_case.instance(seal=False)
    cleanup.callback(owner.close)
    sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=full, cp=ci.CP, startup_owner=owner)
    state = fixture.car_state()
    state.vEgoRaw = state.vEgo
    for tick in range(2, 12):
      fault = 4 <= tick < 10
      state.steerFaultTemporary = fault
      axis, _, command = drive(tick, observed_state=state, steering=steering if fault else False,
                               allowed=not fault, native_request_lat=True, native_request_long=full)
      assert bool(axis.lateralActive and command.latActive) == (not fault)
      assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                     axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents']), card.aol_card_intent.latch_clear_reason
      assert card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('fault', ('permanent', 'can_invalid', 'timeout', 'acc_fault', 'door', 'sensors',
                                    'disengage', 'no_eps', 'native_invalid', 'native_fault', 'hard_event'))
def test_angle_eps_retention_preserves_independent_vetoes(fault):
  from openpilot.cereal import log
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = case._authority_callers(True, cp=profile('pe_long'), genuine_events=True)
    feedback = TransportPauseFeedback(sd.CP)
    sm = messaging.SubMaster(['aolAxisState', 'onroadEvents', 'aolSafetyWire', 'pandaStates'])
    state = fixture.car_state()
    state.vEgoRaw = state.vEgo
    for tick in range(4):
      kwargs = {}
      if tick == 3:
        state.steerFaultTemporary = fault != 'no_eps'
        fields = {'permanent': ('steerFaultPermanent', True), 'can_invalid': ('canValid', False),
                  'timeout': ('canTimeout', True), 'acc_fault': ('accFaulted', True), 'door': ('doorOpen', True),
                  'sensors': ('vehicleSensorsInvalid', True), 'disengage': ('steeringDisengage', True)}
        if fault in fields:
          setattr(state, *fields[fault])
        kwargs = {'allowed': False, 'native_valid': fault != 'native_invalid', 'panda_fault': fault == 'native_fault',
                  'critical_event': log.OnroadEvent.EventName.canError if fault == 'hard_event' else None}
      drive(tick, observed_state=state, native_request_lat=True, native_request_long=True, **kwargs)
      now = 10_000_000_000 + tick * 10_000_000
      frames = [sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']]
      for name in ('aolSafetyWire', 'pandaStates'):
        event = messaging.new_message(name, len(sd.sm[name]), valid=sd.sm.valid[name], logMonoTime=sd.sm.logMonoTime[name])
        setattr(event, name, sd.sm[name])
        frames.append(event.as_reader())
      sm.update_msgs(now / 1e9, frames)
      rejected = feedback.observe(sm, sd.CP, state, now, latched=True, intent_producer_session='authority-card')
      assert rejected == (tick == 3)


@pytest.mark.parametrize('identity', ('pe_long', 'ev9_long'))
@pytest.mark.parametrize('full', (False, True))
@pytest.mark.parametrize('steering', (False, True))
@pytest.mark.parametrize('delivery', ('native_first', 'event_first', 'together', 'native_pause_first'))
def test_angle_eps_split_publications_preserve_only_armed_intent(identity, full, steering, delivery):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    cp = profile(identity)
    assert cp is not None
    sd, _, drive = case._authority_callers(full, cp=cp, genuine_events=True)
    feedback = TransportPauseFeedback(sd.CP)
    sm = messaging.SubMaster(['aolAxisState', 'onroadEvents', 'aolSafetyWire', 'pandaStates'])
    state = fixture.car_state()
    state.vEgoRaw = state.vEgo
    state.cruiseState.enabled = full and cp.pcmCruise
    for tick in range(9):
      fault = 3 <= tick < 8
      state.steerFaultTemporary = fault and not (tick == 3 and delivery == 'native_pause_first')
      axis, _, command = drive(tick, observed_state=state, steering=steering if fault or delivery == 'native_pause_first' else False,
        gas=delivery == 'native_pause_first',
        allowed=not fault, native_request_lat=True, native_request_long=full)
      now = 10_000_000_000 + tick * 10_000_000
      frames = [sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']]
      for name in ('aolSafetyWire', 'pandaStates'):
        message = messaging.new_message(name, len(sd.sm[name]), valid=sd.sm.valid[name],
          logMonoTime=sd.sm.logMonoTime[name])
        setattr(message, name, sd.sm[name])
        frames.append(message.as_reader())
      assert bool(axis.lateralActive and command.latActive) == (not fault)
      if tick == 3 and delivery == 'native_pause_first':
        assert axis.desiredLateral and not axis.lateralActive and axis.nativeAcknowledged
        state.steerFaultTemporary = True
      if tick == 3 and delivery not in ('together', 'native_pause_first'):
        sm.update_msgs(now / 1e9, frames[2:] if delivery == 'native_first' else frames[:2])
        assert not feedback.observe(sm, sd.CP, state, now, latched=True,
          intent_producer_session='authority-card'), 'NATIVE_FIRST_EPS_DISARMED'
        assert not feedback.failed
      joined = (frames[:2] if delivery == 'native_first' else frames[2:]) if tick == 3 and delivery in ('native_first', 'event_first') else frames
      sm.update_msgs(now / 1e9, joined)
      assert not feedback.observe(sm, sd.CP, state, now, latched=True,
        intent_producer_session='authority-card'), 'NATIVE_PAUSE_EPS_DISARMED' if delivery == 'native_pause_first' else 'EPS_JOIN_DISARMED'
      assert not feedback.failed
      if fault:
        assert not axis.lateralActive and not command.latActive


@pytest.mark.parametrize('identity', ('pe_long', 'ev9_long'))
@pytest.mark.parametrize('native_long_pending', (False, True))
def test_stopped_full_request_keeps_existing_lateral_intent_without_granting_commands(identity, native_long_pending):
  from opendbc.car import structs
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    cp = profile(identity)
    assert cp is not None
    sd, _, drive = case._authority_callers(False, cp=cp, genuine_events=True)
    feedback = TransportPauseFeedback(sd.CP)
    sm = messaging.SubMaster(['aolAxisState', 'onroadEvents', 'aolSafetyWire', 'pandaStates'])
    state = fixture.car_state()
    state.vEgoRaw = state.vEgo
    for tick in range(7):
      state.standstill = tick in (3, 4, 5)
      state.vEgo = state.vEgoRaw = 0. if state.standstill else 5.
      state.buttonEnable = tick == 4
      state.buttonEvents = ([structs.CarState.ButtonEvent(type='accelCruise', pressed=False)] if tick == 4 else [])
      requested_long = tick >= 5 or tick == 4 and native_long_pending
      allowed_long = tick >= 5
      state.cruiseState.enabled = allowed_long
      state.cruiseState.standstill = allowed_long and state.standstill
      axis, _, command = drive(tick, observed_state=state, native_request_lat=True,
        allowed=not state.standstill, native_request_long=requested_long, long_allowed=allowed_long,
        panda_controls_allowed=allowed_long)
      now = 10_000_000_000 + tick * 10_000_000
      frames = [sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']]
      for name in ('aolSafetyWire', 'pandaStates'):
        message = messaging.new_message(name, len(sd.sm[name]), valid=sd.sm.valid[name],
          logMonoTime=sd.sm.logMonoTime[name])
        setattr(message, name, sd.sm[name])
        frames.append(message.as_reader())
      sm.update_msgs(now / 1e9, frames)
      if tick == 4:
        assert axis.desiredLongitudinal and not axis.longitudinalActive
        assert not axis.lateralActive and not command.latActive and not command.longActive
      if tick == 5:
        assert axis.longitudinalActive and not axis.lateralActive and not command.latActive
        assert all(not p.controlsAllowed for p in sm['pandaStates'])
        veto = messaging.SubMaster(['aolAxisState', 'onroadEvents', 'aolSafetyWire', 'pandaStates'])
        latest = messaging.new_message('pandaStates', len(sm['pandaStates']), valid=True, logMonoTime=now)
        latest.pandaStates = sm['pandaStates']
        veto.update_msgs(now / 1e9, [*frames[:3], latest.as_reader()])
        assert not feedback._angle_stop_intent_current(veto, sd.CP, state, axis, now, now,
          latched=True, producer='authority-card')
      assert not feedback.observe(sm, sd.CP, state, now, latched=True,
        intent_producer_session='authority-card'), f'STOPPED_FULL_REQUEST_DISARMED tick={tick}'
      assert not feedback.failed
