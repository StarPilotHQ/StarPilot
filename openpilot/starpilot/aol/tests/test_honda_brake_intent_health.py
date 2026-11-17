"""Honda brake only retains existing LAT intent; these are controlled unit peers."""
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR, HondaFlags
from openpilot.cereal import log, messaging
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.aol.runtime import decide_axes
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback, independent_lateral_health, _healthy_transport_native
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety

NOW = 10_000_000_000


class Peers:
  def __init__(self, messages):
    self.messages = {m.which(): m for m in messages}
    self.logMonoTime = {n: int(m.logMonoTime) for n, m in self.messages.items()}
    self.seen = self.valid = self.alive = dict.fromkeys(self.messages, True)

  def __getitem__(self, name):
    return getattr(self.messages[name], name)


def fixture(*, coherent=False, long_allowed=True, brake=True, cancel=False):
  fp = gen_empty_fingerprint()
  fp[0][0x1A3] = 8
  cp = CarInterface.get_params(CAR.HONDA_CIVIC_2022, fp, [], True, False, False)
  cp.alternativeExperience = 32
  cp.fingerprintSource = structs.CarParams.FingerprintSource.fw
  config = cp.safetyConfigs[0]
  assert config.safetyParam == 10
  slots = ((PandaSlot(0, 'unit-honda', config.safetyModel.raw, 10, 32, long_allowed),) if coherent else ())
  native = SafetyState(1, True, NOW - 20_000_000, NOW + 180_000_000,
                       config.safetyModel.raw, 10, True, long_allowed, True, True,
                       'unit-honda', 'unit-session', sourcePandaStatesMonoTime=NOW + 9_000_000_000 if coherent else 0,
                       pandaInventory=slots)
  safety = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=native.observedMonoTime)
  safety.aolSafetyWire = encode_safety(native)
  pandas = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=NOW)
  p = pandas.pandaStates[0]
  p.safetyModel, p.safetyParam, p.alternativeExperience = config.safetyModel, 10, 32
  p.controlsAllowed = False
  axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=NOW - 1_000_000)
  a = axis.aolAxisState
  a.sessionId = a.faultSessionId = 'unit-session'
  a.qualified, a.sequence, a.faultReason = True, 2, 'none'
  a.observedMonoTime, a.validUntilMonoTime = axis.logMonoTime, axis.logMonoTime + 30_000_000
  a.sourceCarStateMonoTime = axis.logMonoTime - 1000
  a.desiredLateral = a.lateralActive = True
  a.nativeAcknowledged = True
  events = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=NOW - 1_000_000)
  sm = Peers([safety.as_reader(), pandas.as_reader(), axis.as_reader(), events.as_reader()])
  cs = structs.CarState(canValid=True, brakePressed=brake and not cancel, vEgo=12.5,
                        gearShifter=structs.CarState.GearShifter.drive)
  if cancel:
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=True)]
  feedback = TransportPauseFeedback(cp)
  # Unit precondition representing an already observed healthy acknowledged LAT baseline.
  feedback.session = native.axisSessionId
  feedback.healthy_feedback_ns = NOW - 10_000_000
  feedback.healthy_native_ns = native.observedMonoTime
  feedback.healthy_inventory = (native.pandaSerial, tuple(p.hardwareSerial for p in slots))
  return cp, cs, sm, native, feedback


@pytest.mark.parametrize('coherent', [False, True])
@pytest.mark.parametrize('long_allowed', [False, True])
@pytest.mark.parametrize('cancel', [False, True])
def test_honda_brake_lateral_intent_only_and_long_fail_closed(coherent, long_allowed, cancel):
  cp, cs, sm, native, feedback = fixture(coherent=coherent, long_allowed=long_allowed, cancel=cancel)
  assert _healthy_transport_native(sm, cp, cs, 'unit-session', NOW) is None
  assert independent_lateral_health(sm, cp, cs, 'unit-session', NOW) == native
  assert not feedback.observe(sm, cp, cs, NOW, latched=True, brake_source_ns=0 if cancel else NOW - 1000,
                              cancel_source_ns=NOW - 1000 if cancel else 0)
  assert (feedback.honda_cancel_ns if cancel else feedback.honda_brake_ns) == NOW - 1000
  decision = decide_axes(standard_lateral=False, standard_longitudinal=False,
    intent=SimpleNamespace(pauseLateral=False, allowedLatch=True, pauseLongitudinal=False),
    native=native, car_state=cs, initialized=True, model_ready=True, no_entry=False,
    immediate_disable=False, dm_lockout=False, pause_brake_mps=5.)
  assert decision.desired_lateral and decision.lateral_active
  assert not decision.desired_longitudinal and not decision.longitudinal_active
  assert native.validUntilMonoTime == NOW + 180_000_000


@pytest.mark.parametrize('mutation', ['no_brake', 'no_source', 'stale_source', 'future_source', 'no_latch',
  'wrong_profile', 'wrong_session', 'serial', 'native_expired', 'no_lat', 'lat_denied',
  'rx_fault', 'heartbeat', 'panda_config', 'axis_fault', 'hard_event', 'stale_axis', 'unacknowledged_long'])
@pytest.mark.parametrize('cancel', [False, True])
def test_honda_brake_cannot_borrow_or_renew_authority(mutation, cancel):
  cp, cs, sm, native, feedback = fixture(coherent=True, long_allowed=False, cancel=cancel)
  source, now, latched = NOW - 1000, NOW, True
  axis = sm.messages['aolAxisState'].as_builder()
  pandas = sm.messages['pandaStates'].as_builder()
  if mutation == 'no_brake':
    cs.brakePressed = False
    cs.buttonEvents = []
  if mutation == 'no_source':
    source = 0
  if mutation == 'stale_source':
    source = NOW - 30_000_001
  if mutation == 'future_source':
    source = NOW + 1
  if mutation == 'no_latch':
    latched = False
  if mutation == 'wrong_profile':
    cp.alternativeExperience = 0
  if mutation == 'wrong_session':
    native = replace(native, axisSessionId='replaced-session')
  if mutation == 'serial':
    native = replace(native, pandaSerial='replaced-serial')
  if mutation == 'native_expired':
    native = replace(native, validUntilMonoTime=NOW - 1)
  if mutation == 'no_lat':
    native = replace(native, requestedLateral=False)
  if mutation == 'lat_denied':
    native = replace(native, lateralAllowed=False)
  if mutation == 'rx_fault':
    pandas.pandaStates[0].safetyRxChecksInvalid = True
  if mutation == 'heartbeat':
    pandas.pandaStates[0].heartbeatLost = True
  if mutation == 'panda_config':
    pandas.pandaStates[0].safetyParam = 8
  if mutation == 'axis_fault':
    axis.aolAxisState.faultReason = 'critical'
  if mutation == 'stale_axis':
    now = NOW + 30_000_001
  if mutation == 'unacknowledged_long':
    axis.aolAxisState.nativeAcknowledged = False
    axis.aolAxisState.desiredLongitudinal = True
  events = sm.messages['onroadEvents']
  if mutation == 'hard_event':
    event = messaging.new_message('onroadEvents', 1, valid=True, logMonoTime=NOW - 1000)
    event.onroadEvents[0].name = 'controlsMismatch'
    event.onroadEvents[0].immediateDisable = True
    events = event.as_reader()
  safety = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=native.observedMonoTime)
  safety.aolSafetyWire = encode_safety(native)
  sm = Peers([safety.as_reader(), pandas.as_reader(), axis.as_reader(), events])
  hook = feedback._honda_cancel_lateral_current if cancel else feedback._honda_brake_lateral_current
  assert hook(sm, cp, cs, sm['aolAxisState'], now, latched=latched,
              **{'cancel_source_ns' if cancel else 'brake_source_ns': source}) is None
  if mutation != 'no_latch':
    assert feedback.observe(sm, cp, cs, now, latched=latched, brake_source_ns=0 if cancel else source,
                                               cancel_source_ns=source if cancel else 0)
  assert (feedback.honda_cancel_ns if cancel else feedback.honda_brake_ns) == 0


@pytest.mark.parametrize('cancel', [False, True])
def test_release_and_held_brake_cannot_extend_original_deadline(cancel):
  cp, cs, sm, native, feedback = fixture(long_allowed=False, cancel=cancel)
  source = NOW - 1000
  assert not feedback.observe(sm, cp, cs, NOW, latched=True, brake_source_ns=0 if cancel else source,
                              cancel_source_ns=source if cancel else 0)
  cs.brakePressed = False
  cs.buttonEvents = []
  assert not feedback.observe(sm, cp, cs, NOW + 1, latched=True)
  assert (feedback.honda_cancel_ns if cancel else feedback.honda_brake_ns) == source
  cs.brakePressed = not cancel
  if cancel:
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=True)]
  assert not feedback.observe(sm, cp, cs, NOW + 2, latched=True, brake_source_ns=0 if cancel else NOW + 2,
                              cancel_source_ns=NOW + 2 if cancel else 0)
  assert (feedback.honda_cancel_ns if cancel else feedback.honda_brake_ns) == source
  axis = sm['aolAxisState']
  hook = feedback._honda_cancel_lateral_current if cancel else feedback._honda_brake_lateral_current
  assert hook(sm, cp, cs, axis, source + 200_000_001, latched=True,
              **{'cancel_source_ns' if cancel else 'brake_source_ns': source + 200_000_001}) is None
  assert (feedback.honda_cancel_ns if cancel else feedback.honda_brake_ns) == source


def test_card_brake_source_keeps_boot_and_monotonic_clocks_separate():
  cp, cs, _, _, _ = fixture()
  card: Any = Car.__new__(Car)
  card.CP = cp
  message = 'BRAKE_MODULE' if cp.flags & HondaFlags.BOSCH_ALT_BRAKE else 'POWERTRAIN_DATA'
  parser = SimpleNamespace(vl={message: {'BRAKE_PRESSED': 1}},
                           ts_nanos={message: {'BRAKE_PRESSED': NOW + 9_000_000_000}})
  card.CI = SimpleNamespace(can_parsers={Bus.pt: parser}, CS=SimpleNamespace(brake_switch_active=False))
  card._aol_can_observation = (cs, NOW - 1000, NOW + 9_000_000_000, NOW + 9_000_001_000)
  assert card.aol_honda_brake_source(cs, NOW) == NOW - 1000
  parser.ts_nanos[message]['BRAKE_PRESSED'] -= 30_000_001
  assert card.aol_honda_brake_source(cs, NOW) == 0
  parser.ts_nanos[message]['BRAKE_PRESSED'] += 30_000_001
  assert card.aol_honda_brake_source(structs.CarState(**cs.to_dict()), NOW) == 0
  parser.vl[message]['BRAKE_PRESSED'] = 0
  assert card.aol_honda_brake_source(cs, NOW) == 0


@pytest.mark.parametrize('mutation', ['none', 'cold', 'withdrawn', 'no_source', 'brake', 'steering', 'gear',
  'soft_pause', 'transport_pause', 'availability_pause', 'session', 'serial', 'cp', 'older', 'expired',
  'fault', 'model', 'requested_denial', 'new_session', 'no_prior', 'uninitialized',
  'latest_expired', 'temporary_steer', 'unqualified', 'user_disable', 'rx_fault',
  'receipt_before_source', 'stale_source', 'producer', 'no_prior_source', 'long_override'])
def test_sd_honda_unexpected_zero_grant_loss_is_not_cold_or_commanded_pause(mutation):
  from openpilot.selfdrive.selfdrived.events import Events
  from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
  from openpilot.starpilot.aol.runtime import AxisDecision
  from openpilot.starpilot.aol.transport_pause import cp_identity

  cp, cs, sm, prior, _ = fixture(coherent=True, brake=False)
  prior = replace(prior, observedMonoTime=NOW - 40_000_000, validUntilMonoTime=NOW + 160_000_000)
  native = replace(prior, observedMonoTime=NOW - 10_000_000, validUntilMonoTime=NOW + 190_000_000,
                   requestedLateral=False, lateralAllowed=False, requestedLongitudinal=False, longitudinalAllowed=False)
  sd: Any = SelfdriveD.__new__(SelfdriveD)
  sd.CP, sd.sm, sd.aol_session_id = cp, sm, 'unit-session'
  sd.initialized, sd.events, sd.aol_fault_reason = True, Events(), 'none'
  sd.aol_transport_pause = SimpleNamespace(cp=cp_identity(cp), session='unit-session', active=False, observed_native=prior)
  sd.aol_availability_pause = SimpleNamespace(active=False)
  sm.all_checks = lambda services: True
  sm.messages['extrinsicsCalibration'] = SimpleNamespace(extrinsicsCalibration=SimpleNamespace(
    calStatus=log.ExtrinsicsCalibration.Status.calibrated))
  previous, requested = AxisDecision(lateral_active=True), AxisDecision(desired_lateral=True)
  source_current = True
  prior_intent = SimpleNamespace(producerSessionId='unit-producer', carStateLogMonoTime=NOW - 10_000_000)
  intent = SimpleNamespace(producerSessionId='unit-producer')
  if mutation == 'cold':
    previous = AxisDecision()
  elif mutation == 'withdrawn':
    requested = AxisDecision()
  elif mutation == 'no_source':
    source_current = False
  elif mutation == 'brake':
    cs.brakePressed = True
  elif mutation == 'steering':
    cs.steeringPressed = True
  elif mutation == 'gear':
    cs.gearShifter = structs.CarState.GearShifter.park
  elif mutation == 'soft_pause':
    sd.aol_fault_reason = 'availabilityPause'
  elif mutation == 'transport_pause':
    sd.aol_transport_pause.active = True
  elif mutation == 'availability_pause':
    sd.aol_availability_pause.active = True
  elif mutation == 'session':
    native = replace(native, axisSessionId='replacement')
  elif mutation == 'serial':
    native = replace(native, pandaSerial='replacement')
  elif mutation == 'cp':
    sd.aol_transport_pause.cp = ()
  elif mutation == 'older':
    native = replace(native, observedMonoTime=prior.observedMonoTime - 1)
  elif mutation == 'expired':
    prior = replace(prior, validUntilMonoTime=NOW - 1)
  elif mutation == 'fault':
    sd.events.add(log.OnroadEvent.EventName.controlsMismatch)
  elif mutation == 'model':
    sm.all_checks = lambda services: False
  elif mutation == 'requested_denial':
    native = replace(native, requestedLateral=True)
  elif mutation == 'new_session':
    sd.aol_transport_pause.session = 'replacement'
  elif mutation == 'no_prior':
    prior = None
  elif mutation == 'uninitialized':
    sd.initialized = False
  elif mutation == 'latest_expired':
    native = replace(native, validUntilMonoTime=NOW - 1)
  elif mutation == 'temporary_steer':
    cs.steerFaultTemporary = True
  elif mutation == 'unqualified':
    cp.alternativeExperience = 0
  elif mutation == 'user_disable':
    sd.events.add(log.OnroadEvent.EventName.buttonCancel)
  elif mutation == 'rx_fault':
    pandas = sm.messages['pandaStates'].as_builder()
    pandas.pandaStates[0].safetyRxChecksInvalid = True
    sm.messages['pandaStates'] = pandas.as_reader()
  if mutation == 'receipt_before_source':
    prior_intent.carStateLogMonoTime = NOW - 9_999_999
  elif mutation == 'stale_source':
    prior_intent.carStateLogMonoTime = NOW - 30_000_001
  elif mutation == 'producer':
    intent.producerSessionId = 'replacement'
  elif mutation == 'no_prior_source':
    prior_intent.carStateLogMonoTime = 0
  elif mutation == 'long_override':
    sd.events.add(log.OnroadEvent.EventName.gasPressedOverride)
  sd.aol_transport_pause.observed_native = prior
  event = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=native.observedMonoTime)
  event.aolSafetyWire = encode_safety(native)
  sm.messages['aolSafetyWire'] = event.as_reader()
  sm.logMonoTime['aolSafetyWire'] = native.observedMonoTime
  assert sd.honda_native_lateral_grant_lost(cs, native, previous, requested,
    source_current=source_current, previous_intent=prior_intent, intent=intent, now_ns=NOW) == (mutation == 'none')
  assert native.validUntilMonoTime == (NOW - 1 if mutation == 'latest_expired' else NOW + 190_000_000)
  assert not native.lateralAllowed and not native.longitudinalAllowed


@pytest.mark.parametrize('mutation', ['none', 'stale', 'future', 'other_state', 'not_cancel', 'released', 'wrong_profile'])
def test_card_cancel_source_requires_matching_fresh_parsed_can(mutation):
  cp, cs, _, _, _ = fixture(cancel=True)
  card: Any = Car.__new__(Car)
  card.CP = cp
  stamp = NOW + 9_000_000_000
  parser = SimpleNamespace(vl={'SCM_BUTTONS': {'CRUISE_BUTTONS': 2}},
                           ts_nanos={'SCM_BUTTONS': {'CRUISE_BUTTONS': stamp}})
  card.CI = SimpleNamespace(can_parsers={Bus.pt: parser}, CS=SimpleNamespace())
  card._aol_can_observation = (cs, NOW - 1000, stamp, stamp + 1000)
  if mutation == 'stale':
    parser.ts_nanos['SCM_BUTTONS']['CRUISE_BUTTONS'] -= 30_000_001
  if mutation == 'future':
    parser.ts_nanos['SCM_BUTTONS']['CRUISE_BUTTONS'] += 1
  if mutation == 'other_state':
    cs = structs.CarState(**cs.to_dict())
  if mutation == 'not_cancel':
    parser.vl['SCM_BUTTONS']['CRUISE_BUTTONS'] = 3
  if mutation == 'released':
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=False)]
  if mutation == 'wrong_profile':
    cp.alternativeExperience = 0
  assert card.aol_cancel_source(cs, NOW) == (NOW - 1000 if mutation == 'none' else 0)


def test_cancel_retains_existing_native_lateral_only_receipt():
  cp, cs, sm, native, feedback = fixture(coherent=True, long_allowed=False, cancel=True)
  native = replace(native, requestedLongitudinal=False)
  safety = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=native.observedMonoTime)
  safety.aolSafetyWire = encode_safety(native)
  sm.messages['aolSafetyWire'] = safety.as_reader()
  assert feedback._honda_cancel_lateral_current(sm, cp, cs, sm['aolAxisState'], NOW,
    latched=True, cancel_source_ns=NOW - 1000) == native
  assert feedback.honda_cancel_ns == NOW - 1000
  assert feedback.honda_brake_ns == 0


@pytest.mark.parametrize('mutation', ['none', 'no_latch', 'no_previous', 'lat_denied', 'expired_native',
  'stale_source', 'session', 'wrong_cp', 'configured_cancel', 'main_off', 'pause', 'eps', 'invalid_can',
  'no_button', 'protocol', 'inventory'])
def test_honda_cancel_no_entry_exception_requires_existing_current_lateral(mutation):
  from openpilot.starpilot.car.honda.aol import physical_cancel_lateral_allowed
  from openpilot.starpilot.aol.runtime import AxisDecision
  cp, cs, _, native, _ = fixture(coherent=True, cancel=True)
  cs.cruiseState.available = True
  source = NOW - 1000
  intent = SimpleNamespace(settingsQualified=True, allowedLatch=True, lateralArmed=True, pauseLateral=False,
    producerSessionId='unit-producer', carStateLogMonoTime=source, observedMonoTime=NOW - 500,
    validUntilMonoTime=NOW + 199_999_500)
  settings = SimpleNamespace(enabled=True, cancel_actions=(0, 0, 0))
  previous = AxisDecision(lateral_active=True, native_acknowledged=True)
  if mutation == 'no_latch':
    intent.allowedLatch = False
  if mutation == 'no_previous':
    previous = None
  if mutation == 'lat_denied':
    native = replace(native, lateralAllowed=False)
  if mutation == 'expired_native':
    native = replace(native, validUntilMonoTime=NOW - 1)
  if mutation == 'stale_source':
    intent.carStateLogMonoTime = source = NOW - 30_000_001
  if mutation == 'session':
    native = replace(native, axisSessionId='changed-session')
  if mutation == 'wrong_cp':
    cp.alternativeExperience = 0
  if mutation == 'configured_cancel':
    settings.cancel_actions = (9, 0, 0)
  if mutation == 'main_off':
    cs.cruiseState.available = False
  if mutation == 'pause':
    intent.pauseLateral = True
  if mutation == 'eps':
    cs.steerFaultPermanent = True
  if mutation == 'invalid_can':
    cs.canValid = False
  if mutation == 'no_button':
    cs.buttonEvents = []
  if mutation == 'protocol':
    native = replace(native, protocolVersion=0)
  if mutation == 'inventory':
    native = replace(native, pandaInventory=())
  assert physical_cancel_lateral_allowed(cp, cs, intent, native, settings=settings, previous=previous,
    source_ns=source, now_ns=NOW, axis_session_id='unit-session') == (mutation == 'none')
