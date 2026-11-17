"""Typed replay of root's first-CANCEL proof plus actual parser source boundaries.

Typed guard replay is not native qualification; the real joined caller gate owns
that evidence. No native permission/controls setter is used here.
"""

from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace as NS
from typing import cast

import pytest
from opendbc.car import structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.torque_ev_startup import candidate
from openpilot.cereal import messaging
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.aol.intent import AolCardIntent, AolSettings
from openpilot.starpilot.aol.tests.test_canfd_stock_profiles import params
from openpilot.starpilot.aol.tests import test_hkg_cancel_joined as joined
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.wire import SafetyState, PandaSlot, encode_safety
from openpilot.starpilot.car.hyundai.aol import retained_shared_long_cancel_intent


class Messages(dict):
  pass


def captured_context():
  # Original bounded root observer: now11.60, oldFULL11.59/deadline11.62;
  # cached acceptedFULL11.58/deadline11.61; nativeLATtrue/LONGfalse/current11.60.
  now = 11_600_000_000
  cp = candidate(params(CAR.HYUNDAI_IONIQ_5), requested=True, is_release=False)
  cp.safetyConfigs[0].safetyParam |= 0x800
  cp.alternativeExperience = 32
  settings = AolSettings(True, 0.0, 9, 0, (0, 0, 0), (0, 0, 0))
  owner = NS(
    explicit_latch=True,
    independent_cruise_buttons=True,
    allowed_latch=True,
    pause_lateral=False,
    pause_longitudinal=False,
    _fault_inhibit=False,
    settings=settings,
  )
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  feedback = TransportPauseFeedback(cp)
  feedback.session, feedback.sequence = 'authority-session', 158
  feedback.signature = (
    'authority-session',
    158,
    11_580_000_000,
    11_580_000_000,
    11_610_000_000,
    'none',
    11_410_000_000,
    'authority-session',
    11_580_000_000,
    True,
    True,
    True,
    True,
    True,
    'authority-card',
    8,
  )
  feedback.lateral_baseline_ns = feedback.lateral_native_ns = 11_590_000_000
  feedback.lateral_source_ns = 11_580_000_000
  feedback.healthy_feedback_ns = feedback.healthy_native_ns = 11_590_000_000
  feedback.lateral_inventory = feedback.healthy_inventory = ('authority-panda', ('authority-panda',))
  feedback.availability_producer, feedback.availability_epoch = 'authority-card', 8
  feedback.temporary_host_qualified = False
  axis_event = messaging.new_message('aolAxisState', valid=True, logMonoTime=11_590_000_000)
  axis = axis_event.aolAxisState
  axis.sessionId, axis.sequence = 'authority-session', 159
  axis.sourceCarStateMonoTime = axis.observedMonoTime = 11_590_000_000
  axis.validUntilMonoTime = 11_620_000_000
  axis.mode, axis.faultReason = 'combined', 'none'
  axis.qualified = axis.nativeAcknowledged = True
  axis.lateralActive = axis.longitudinalActive = axis.desiredLateral = axis.desiredLongitudinal = True
  axis.faultSessionId = 'authority-session'
  axis.intentProducerSessionId, axis.intentIntegrityEpoch = 'authority-card', 8
  events = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=11_410_000_000)
  pandas = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
  config = cp.safetyConfigs[0]
  panda = pandas.pandaStates[0]
  panda.safetyModel, panda.safetyParam, panda.alternativeExperience = config.safetyModel, config.safetyParam, 32
  panda.controlsAllowed = False
  slot = PandaSlot(0, 'authority-panda', int(config.safetyModel.raw), config.safetyParam, 32, False)
  native = SafetyState(
    1,
    True,
    now,
    now + 30_000_000,
    int(config.safetyModel.raw),
    config.safetyParam,
    True,
    False,
    True,
    True,
    'authority-panda',
    'authority-session',
    now,
    (slot,),
  )
  sm = Messages(
    aolAxisState=axis_event.as_reader().aolAxisState,
    onroadEvents=events.as_reader().onroadEvents,
    pandaStates=pandas.as_reader().pandaStates,
    aolSafetyWire=encode_safety(native),
  )
  sm.logMonoTime = {'aolAxisState': 11_590_000_000, 'onroadEvents': 11_410_000_000, 'pandaStates': now, 'aolSafetyWire': now}
  sm.valid = dict.fromkeys(sm, True)
  sm.alive = dict(sm.valid)
  sm.seen = dict(sm.valid)
  return cp, cs, sm, feedback, owner, native, now


@pytest.mark.parametrize(
  'case',
  [
    'retain',
    'settings_off',
    'default_buttons',
    'configured_cancel',
    'pause',
    'not_latched',
    'no_raw_source',
    'expired_original',
    'old_native',
    'native_lat_denied',
    'native_long_allowed',
    'no_long_request',
    'wrong_session',
    'wrong_inventory',
    'panda_rx',
    'panda_fault',
    'failed_feedback',
    'changed_cp',
    'producer',
    'wrong_epoch',
    'nonfull',
    'hard_fault',
    'reverse',
    'steer_fault',
    'invalid_can',
    'no_physical_cancel',
  ],
)
def test_shared_cancel_retains_only_original_current_reason_qualified_intent(case):
  cp, cs, sm, feedback, owner, native, now = captured_context()
  source = now
  if case == 'settings_off':
    owner.settings = replace(owner.settings, enabled=False)
  elif case == 'default_buttons':
    owner.settings = replace(owner.settings, lkas_action=0)
  elif case == 'configured_cancel':
    owner.settings = replace(owner.settings, cancel_actions=(3, 0, 0))
  elif case == 'pause':
    owner.pause_lateral = True
  elif case == 'not_latched':
    owner.allowed_latch = False
  elif case == 'no_raw_source':
    source = 0
  elif case == 'expired_original':
    now = 11_610_000_001
  elif case == 'old_native':
    native = replace(native, observedMonoTime=11_580_000_000)
  elif case == 'native_lat_denied':
    native = replace(native, lateralAllowed=False)
  elif case == 'native_long_allowed':
    native = replace(native, longitudinalAllowed=True)
  elif case == 'no_long_request':
    native = replace(native, requestedLongitudinal=False)
  elif case == 'wrong_session':
    native = replace(native, axisSessionId='other')
  elif case == 'wrong_inventory':
    native = replace(native, pandaInventory=(replace(native.pandaInventory[0], safetyParam=0),))
  elif case == 'panda_rx':
    event = messaging.new_message('pandaStates', 1)
    event.pandaStates[0] = sm['pandaStates'][0]
    event.pandaStates[0].safetyRxChecksInvalid = True
    sm['pandaStates'] = event.as_reader().pandaStates
  elif case == 'panda_fault':
    native = replace(native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  elif case == 'failed_feedback':
    feedback.failed = True
  elif case == 'changed_cp':
    cp.flags ^= 0x80
  elif case == 'producer':
    feedback.availability_producer = 'other'
  elif case == 'wrong_epoch':
    feedback.availability_epoch = 9
  elif case == 'nonfull':
    event = messaging.new_message('aolAxisState')
    event.aolAxisState = sm['aolAxisState']
    event.aolAxisState.longitudinalActive = False
    sm['aolAxisState'] = event.as_reader().aolAxisState
  elif case == 'hard_fault':
    event = messaging.new_message('onroadEvents', 1)
    event.onroadEvents[0].name = 'controlsMismatch'
    event.onroadEvents[0].immediateDisable = True
    sm['onroadEvents'] = event.as_reader().onroadEvents
  elif case == 'reverse':
    cs.gearShifter = 'reverse'
  elif case == 'steer_fault':
    cs.steerFaultPermanent = True
  elif case == 'invalid_can':
    cs.canValid = False
  elif case == 'no_physical_cancel':
    cs.buttonEvents = []
  sm['aolSafetyWire'] = encode_safety(native)
  before = deepcopy(vars(feedback))
  native_before = asdict(native)
  result = retained_shared_long_cancel_intent(cp, cs, sm, feedback, owner, cancel_source_ns=source, now_ns=now, producer='authority-card')
  assert result == (case in ('retain', 'default_buttons'))
  assert vars(feedback) == before  # No lease, output, source, baseline or identity renewal.
  assert asdict(native) == native_before


@pytest.mark.parametrize('role', ['marked', 'unmarked', 'stock', 'wrong_bus', 'expired_receipt'])
def test_shared_cancel_source_is_actual_parser_evidence(role):
  stock = params(CAR.HYUNDAI_IONIQ_5)
  cp = stock if role == 'stock' else candidate(stock, requested=True, is_release=False)
  if role not in ('stock', 'unmarked'):
    cp.safetyConfigs[0].safetyParam |= 0x800
    cp.alternativeExperience = 32
  ci = CarInterface(cp)
  ci.update([])
  for tick in range(29):
    now = 10_000_000_000 + tick * 10_000_000
    frames = joined.parser_frames(ci, tick, button=4 if tick == 28 else 0)
    if role == 'wrong_bus' and tick == 28:
      frames = [(address, data, 2 if address == 0x1CF else bus) for address, data, bus in frames]
    state = ci.update([(now, frames)])
  if role == 'wrong_bus':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  received = now - 30_000_001 if role == 'expired_receipt' else now
  caller = NS(CI=ci, _aol_can_observation=(state, received, now, now))
  source = Car.aol_cancel_source(cast(Car, caller), state, now)
  assert (source == now) == (role == 'marked')
  assert source == 0 or ci.CS.ev6_aol_source_healthy


@pytest.mark.parametrize('opted_in', [False, True])
@pytest.mark.parametrize('mapped', [False, True])
def test_simultaneous_cancel_keeps_default_timestamp_side_effects(opted_in, mapped):
  owner = AolCardIntent(AolSettings(True, 0.0, 9 if mapped else 0, 0, (0, 0, 0), (0, 0, 0)), explicit_latch=True, independent_cruise_buttons=opted_in)
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  owner.update(cs, now_ns=1, fault_active=False)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True), structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  owner.update(cs, now_ns=2, fault_active=False)
  assert not owner.allowed_latch
  assert owner._last_latch_edge_ns == (0 if opted_in and mapped else 2)


@pytest.mark.parametrize('identity', (CAR.GENESIS_GV60_EV_1ST_GEN, CAR.HYUNDAI_IONIQ_5,
                                     CAR.HYUNDAI_KONA_EV_2ND_GEN))
@pytest.mark.parametrize('fault', ('none', 'no_cancel', 'lat_denied', 'rx', 'heartbeat', 'expired', 'same_health'))
def test_shared_long_cancel_health_can_precede_native_status(identity, fault):
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.hyundai.canfd_stock_aol import qualified_long
  from openpilot.starpilot.aol.transport_pause import cp_identity

  cp, cs, sm, feedback, owner, native, now = captured_context()
  if identity == CAR.GENESIS_GV60_EV_1ST_GEN:
    fingerprint = gen_empty_fingerprint()
    fingerprint[0][0x1cf] = 8
    cp = CarInterface.get_params(identity, fingerprint, [], True, False, False)
    cp.safetyConfigs[0].safetyParam |= 0x800
    cp.alternativeExperience = 32
  else:
    cp.carFingerprint = identity
  assert qualified_long(cp, marked_only=True)
  feedback.cp = cp_identity(cp)
  config = cp.safetyConfigs[0]
  slot = replace(native.pandaInventory[0], safetyModel=int(config.safetyModel.raw), safetyParam=config.safetyParam,
                 controlsAllowed=True)
  native = replace(native, safetyModel=int(config.safetyModel.raw), safetyParam=config.safetyParam,
                   longitudinalAllowed=True, pandaInventory=(slot,), sourcePandaStatesMonoTime=now)
  sm['aolSafetyWire'] = encode_safety(native)
  pandas = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
  panda = pandas.pandaStates[0]
  panda.safetyModel, panda.safetyParam, panda.alternativeExperience = config.safetyModel, config.safetyParam, 32
  panda.controlsAllowed = True
  sm['pandaStates'] = pandas.as_reader().pandaStates
  if fault == 'no_cancel':
    cs.buttonEvents = []
  assert not feedback.observe(sm, cp, cs, now, latched=True, cancel_source_ns=now,
                              intent_producer_session='authority-card')
  assert bool(feedback.longitudinal_cancel_ns) == (fault != 'no_cancel')
  original_receipt = feedback.longitudinal_cancel_ns
  cs.buttonEvents = []
  pandas.pandaStates[0].controlsAllowed = False
  pandas.pandaStates[0].safetyRxChecksInvalid = fault == 'rx'
  pandas.pandaStates[0].heartbeatLost = fault == 'heartbeat'
  sm['pandaStates'] = pandas.as_reader().pandaStates
  sm.logMonoTime['pandaStates'] = now + (0 if fault == 'same_health' else 10_000_000)
  if fault == 'lat_denied':
    sm['aolSafetyWire'] = encode_safety(replace(native, lateralAllowed=False))
  later = now + (201_000_000 if fault == 'expired' else 10_000_000)
  retained = feedback._longitudinal_cancel_current(sm, cp, cs, sm['aolAxisState'], later,
                                                  latched=True, cancel_source_ns=0)
  assert (retained is not None) == (fault == 'none')
  assert feedback.longitudinal_cancel_ns == original_receipt
