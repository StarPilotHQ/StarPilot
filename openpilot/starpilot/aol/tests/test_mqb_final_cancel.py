from dataclasses import replace
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import CAR
from openpilot.cereal import messaging
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.selfdrive.selfdrived.selfdrived import EventName, SelfdriveD
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.runtime import AxisDecision, current_native
from openpilot.starpilot.aol.tests.test_gm_held_native import Peers
from openpilot.starpilot.aol.wire import IntentState, PandaSlot, SafetyState, encode_safety
from openpilot.starpilot.car.volkswagen.aol import policy_for


def mqb_manual_cp():
  fp = gen_empty_fingerprint()
  fp[1][0xFD] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, fp, [], True, False, False)
  assert cp.transmissionType == structs.CarParams.TransmissionType.manual
  policy = policy_for(cp)
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  return cp


def native_health(cp, stamp, session):
  config = cp.safetyConfigs[-1]
  slot = PandaSlot(0, 'mqb-board', config.safetyModel.raw, config.safetyParam, cp.alternativeExperience, True)
  native = SafetyState(1, True, stamp, stamp + 200_000_000, config.safetyModel.raw, config.safetyParam,
                       True, True, True, True, 'mqb-board', session,
                       sourcePandaStatesMonoTime=stamp, pandaInventory=(slot,))
  wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=stamp)
  wire.aolSafetyWire = encode_safety(native)
  panda = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=stamp)
  board = panda.pandaStates[0]
  board.safetyModel, board.safetyParam, board.alternativeExperience = config.safetyModel, config.safetyParam, cp.alternativeExperience
  board.controlsAllowed = True
  return slot, native, wire, panda


@pytest.mark.parametrize('fault', ('none', 'assigned', 'missing_edge', 'stale', 'native_denial', 'native_fault',
                                   'hard_fault', 'main_off', 'other_user', 'no_prior_lat'))
def test_mqb_cancel_can_publish_fresh_lateral_after_old_axis_expires(fault):
  # Route 5e: the prior axis expires during processing, 28 ms after a fresh physical Cancel.
  edge = 10_000_000_000
  now = edge + 28_173_000
  cp = mqb_manual_cp()
  sd = SelfdriveD.__new__(SelfdriveD)
  sd.CP = cp
  sd.aol_session_id = 'mqb-axis'
  sd.aol_settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (9, 0, 0) if fault == 'assigned' else (0, 0, 0))
  sd.aol_axis_decision = AxisDecision(desired_lateral=True, lateral_active=True, native_acknowledged=True)
  sd.aol_car_state_log_ns = source = edge - (3_000_000 if fault == 'stale' else 0)
  sd.conditional_car_state_valid = True
  sd.aol_last_intent = IntentState('mqb-card', 2, source, source, source + 200_000_000, True, False, False, True, True)
  sd.aol_intent_companion = SimpleNamespace(integrity_epoch=44)
  sd.aol_published_input_proof = (sd.aol_session_id, edge - 11_623_000, edge - 5_618_000,
                                 edge + 24_382_000, 'mqb-card', 44, True, True, fault != 'no_prior_lat', True)
  sd.aol_authority_lost = sd.enabled = sd.active = False
  sd.aol_replay = sd.axis_transport_required = sd.initialized = True
  sd.events = Events()
  sd.events.add(EventName.buttonCancel)
  if fault == 'hard_fault':
    sd.events.add(EventName.accFaulted)
  if fault == 'other_user':
    sd.events.add(EventName.pedalPressed)
  sd.state_machine = StateMachine()
  cs = structs.CarState(canValid=True, vEgo=23.8, gearShifter='drive')
  cs.cruiseState.available = fault != 'main_off'
  if fault != 'missing_edge':
    cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  slot, native, wire, panda = native_health(cp, edge - 29_808_000, sd.aol_session_id)
  sd.sm = Peers([wire.as_reader(), panda.as_reader()])
  sd.aol_published_input_native = current_native(sd.sm, cp, now_ns=now, axis_session_id=sd.aol_session_id)
  assert sd.aol_published_input_native is not None
  if fault in ('native_denial', 'native_fault'):
    if fault == 'native_denial':
      native = replace(native, lateralAllowed=False)
    else:
      native = replace(native, pandaInventory=(replace(slot, safetyRxChecksInvalid=True),))
    wire.aolSafetyWire = encode_safety(native)
    sd.sm.messages['aolSafetyWire'] = wire.as_reader()
  result = sd._expire_axis_continuity(cs, now)
  assert result == (fault != 'none')
  assert sd.aol_axis_decision.lateral_active == (fault == 'none')
  assert not sd.aol_axis_decision.longitudinal_active
  assert (EventName.controlsMismatch in sd.events.names) == (fault != 'none')


@pytest.mark.parametrize('fault', ('none', 'unverified', 'mutated', 'session', 'revoked', 'old', 'hard_fault', 'native_denial'))
def test_mqb_armed_source_gap_handles_split_recovery(fault):
  from openpilot.starpilot.aol.intent_companion import IntentCompanion
  from openpilot.starpilot.aol.transport_pause import AvailabilityPause, _healthy_transport_native
  from openpilot.starpilot.aol.wire import encode_intent

  cp = mqb_manual_cp()
  session = 'mqb-axis'
  start = 10_000_000_000
  _, native, wire, panda = native_health(cp, start - 30_000_000, session)
  # Route 5b: the raw intent resumption arrives 3 ms before the failed publication.
  # SubMaster can still hold the earlier raw-verified projection.
  companion = IntentCompanion(None)
  pause = AvailabilityPause(cp, session)
  state = structs.CarState(canValid=True, gearShifter='drive', vEgo=0.68, gasPressed=True, steeringPressed=True)
  state.cruiseState.available = True

  def intent(stamp, sequence, **changes):
    value = IntentState('mqb-card', sequence, stamp, stamp, stamp + 200_000_000, True, False, False, True, True)
    value = replace(value, **changes)
    envelope = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=stamp)
    envelope.aolIntentWire = encode_intent(value)
    return envelope, value

  def observe(now, message, *, active=False, faults=()):
    sm = Peers([wire.as_reader(), panda.as_reader(), message.as_reader()])
    healthy = _healthy_transport_native(sm, cp, state, session, now)
    return pause.observe(cp=cp, session=session, sm=sm, cs=state, native=healthy, now_ns=now, companion=companion,
                         intent=accepted, events=faults, model_ready=True, source_current=active,
                         prior_lateral_active=active, hard_fault=False, healthy_native=healthy, source_expired=not active)

  first, accepted = intent(start, 1)
  companion._observe(start, True, first.aolIntentWire)
  companion.verified_sm_projection = (start, accepted, companion.integrity_epoch, companion.session)
  assert not observe(start + 6_000_000, first, active=True)
  projection, prior = intent(start + 10_000_000, 2)
  companion._observe(projection.logMonoTime, True, projection.aolIntentWire)
  health_events = Events()
  health_events.add(EventName.commIssue)
  assert observe(start + 34_000_000, projection, faults=health_events.to_msg())
  if fault == 'native_denial':
    wire.aolSafetyWire = encode_safety(replace(native, lateralAllowed=False))
  if fault == 'hard_fault':
    health_events.add(EventName.accFaulted)
  if fault != 'unverified':
    companion.raw_projections[projection.logMonoTime] = (prior, companion.integrity_epoch, companion.session)
  next_raw, _ = intent(start + 106_000_000, 3,
                       producerSessionId='other' if fault == 'session' else companion.session,
                       allowedLatch=fault != 'revoked')
  companion._observe(next_raw.logMonoTime, True, next_raw.aolIntentWire)
  if fault == 'mutated':
    projection, _ = intent(projection.logMonoTime, 2, pauseLateral=True)
  now = start + (201_000_000 if fault == 'old' else 109_000_000)
  if fault == 'old':
    _, _, wire, panda = native_health(cp, now - 20_000_000, session)
  assert observe(now, projection, faults=health_events.to_msg()) == (fault == 'none')
