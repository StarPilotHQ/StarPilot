from dataclasses import replace
from types import SimpleNamespace

import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety
from openpilot.starpilot.car.mazda.stock_intent import MazdaStockCardIntent


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
@pytest.mark.parametrize('blocker', ('disabled', 'main_toggle', 'lkas_toggle', 'active', 'permanent', 'fault'))
def test_startup_main_never_overrides_saved_off_or_disarming_fault(identity, blocker):
  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  if blocker == 'disabled':
    settings = replace(settings, enabled=False)
  elif blocker == 'main_toggle':
    settings = replace(settings, main_action=9)
  elif blocker == 'lkas_toggle':
    settings = replace(settings, lkas_action=9)
  intent = MazdaStockCardIntent(cp, settings)
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  cs.cruiseState.enabled = blocker == 'active'
  cs.steerFaultPermanent = blocker == 'permanent'
  intent.update(cs.as_reader(), fault_active=blocker == 'fault', now_ns=1_000_000_000)
  assert not intent.allowed_latch
  cs.cruiseState.enabled = False
  cs.steerFaultPermanent = False
  for tick in range(10):
    intent.update(cs.as_reader(), fault_active=False, now_ns=1_010_000_000 + tick * 10_000_000)
    assert not intent.allowed_latch


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_invalid_can_cannot_supply_startup_main(identity):
  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  intent = MazdaStockCardIntent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=False, canTimeout=True)
  cs.cruiseState.available = True
  intent.update(cs.as_reader(), fault_active=False, now_ns=1_000_000_000)
  assert not intent.allowed_latch and intent.startup_main_pending


def idle_startup_proof(cp, now, session):
  model = int(structs.CarParams.SafetyModel.mazda)
  slot = PandaSlot(0, 'panda', model, 0, 32)
  native = SafetyState(1, True, now, now + 200_000_000, model, 0, False, False, False, False,
                       'panda', session, now, (slot,))
  axis = SimpleNamespace(sessionId=session, sourceCarStateMonoTime=now, observedMonoTime=now,
                         validUntilMonoTime=now + 30_000_000, mode='off', lateralActive=False,
                         longitudinalActive=False, qualified=True, desiredLateral=False,
                         desiredLongitudinal=False, nativeAcknowledged=True, optionalSetRelease=False,
                         faultReason='none', faultEventMonoTime=now, faultSessionId=session,
                         intentProducerSessionId='card', intentIntegrityEpoch=1)
  panda = SimpleNamespace(safetyModel=SimpleNamespace(raw=model), safetyParam=0, alternativeExperience=32,
                          safetyRxChecksInvalid=False, heartbeatLost=False, faults=[])
  messages = {'onroadEvents': [], 'aolAxisState': axis, 'aolSafetyWire': encode_safety(native), 'pandaStates': [panda]}

  class SM(SimpleNamespace):
    def __getitem__(self, key):
      return messages[key]

  sm = SM(logMonoTime={'onroadEvents': now, 'aolAxisState': now, 'aolSafetyWire': now, 'pandaStates': now},
          seen={'onroadEvents': True, 'aolAxisState': True, 'aolSafetyWire': True, 'pandaStates': True},
          valid={'onroadEvents': True, 'aolAxisState': True, 'aolSafetyWire': True, 'pandaStates': True},
          alive={'onroadEvents': True, 'aolAxisState': True, 'aolSafetyWire': True, 'pandaStates': True})
  return sm, native


def test_boot_fault_requires_a_fresh_main_cycle_after_recovery():
  NOW = 1_000_000_000
  PRODUCER = 'card'
  cp = CarInterface.get_params(CAR.MAZDA_CX5_2022, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  intent = MazdaStockCardIntent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  cs.steerFaultPermanent = True

  # The TI2 boot violation consumes startup eligibility, as intended. Recovery
  # alone must not arm: only a fresh MAIN off/on observation may reopen it.
  for tick in range(1, 3):
    now = NOW + tick * 10_000_000
    sm, native = idle_startup_proof(cp, now, 'startup-session')
    intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
    intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert not intent.allowed_latch and not intent.startup_main_pending

  cs.steerFaultPermanent = False
  cs.cruiseState.available = False
  now = NOW + 30_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert not intent.allowed_latch
  assert intent.startup_main_pending

  cs.cruiseState.available = True
  for tick in range(4, 7):
    now = NOW + tick * 10_000_000
    sm, native = idle_startup_proof(cp, now, 'startup-session')
    intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
    intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert intent.allowed_latch
  # The startup arm branch clears pending; if it stayed set, the latch came only
  # from the synthetic MAIN pulse and the two-fresh-sample rule never fired.
  assert not intent.startup_main_pending


@pytest.mark.parametrize('consume', ('permanent', 'fault_active'))
def test_fault_consume_resets_the_native_readiness_timestamp(consume):
  NOW = 1_000_000_000
  PRODUCER = 'card'
  cp = CarInterface.get_params(CAR.MAZDA_CX5_2022, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  intent = MazdaStockCardIntent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True

  now = NOW + 10_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert intent.startup_main_source_ns and intent.startup_binding is not None

  cs.steerFaultPermanent = consume == 'permanent'
  now = NOW + 20_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=consume == 'fault_active', now_ns=now)
  assert not intent.startup_main_pending
  assert intent.startup_main_source_ns == 0 and intent.startup_binding is None

  cs.steerFaultPermanent = False
  cs.cruiseState.available = False
  now = NOW + 30_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert intent.startup_main_pending

  cs.cruiseState.available = True
  now = NOW + 40_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  # The first fresh proof after a fault only initializes the new timestamp; it
  # must not arm from the stale pre-fault source or from the MAIN gesture alone.
  assert intent.startup_main_pending
  now = NOW + 50_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert not intent.startup_main_pending


def test_main_off_reopen_requires_two_fresh_proofs():
  NOW = 1_000_000_000
  PRODUCER = 'card'
  cp = CarInterface.get_params(CAR.MAZDA_CX5_2022, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  intent = MazdaStockCardIntent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True

  now = NOW + 10_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert intent.startup_main_source_ns and intent.startup_binding is not None

  cs.cruiseState.available = False
  for tick in range(2, 52):
    now = NOW + tick * 10_000_000
    sm, native = idle_startup_proof(cp, now, 'startup-session')
    intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
    intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert intent.startup_main_pending
  assert intent.startup_main_source_ns == 0 and intent.startup_binding is None

  cs.cruiseState.available = True
  now = NOW + 52_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert intent.startup_main_pending
  now = NOW + 53_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert not intent.startup_main_pending


def test_ineligible_consume_resets_native_readiness_timestamp():
  NOW = 1_000_000_000
  PRODUCER = 'card'
  cp = CarInterface.get_params(CAR.MAZDA_CX5_2022, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  intent = MazdaStockCardIntent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True

  now = NOW + 10_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert intent.startup_main_source_ns and intent.startup_binding is not None

  intent.settings = replace(intent.settings, enabled=False)
  now = NOW + 20_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert not intent.startup_main_pending
  assert intent.startup_main_source_ns == 0 and intent.startup_binding is None


@pytest.mark.parametrize('blocker', ('disabled', 'permanent', 'acc_fault'))
def test_main_off_does_not_reopen_startup_for_blockers(blocker):
  NOW = 1_000_000_000
  PRODUCER = 'card'
  cp = CarInterface.get_params(CAR.MAZDA_CX5_2022, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  settings = AolSettings(blocker != 'disabled', 0., 0, 0, (0, 0, 0), (0, 0, 0))
  intent = MazdaStockCardIntent(cp, settings)
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  cs.steerFaultPermanent = blocker == 'permanent'
  cs.accFaulted = blocker == 'acc_fault'

  now = NOW + 10_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert not intent.startup_main_pending

  cs.cruiseState.available = False
  now = NOW + 20_000_000
  sm, native = idle_startup_proof(cp, now, 'startup-session')
  intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=PRODUCER)
  intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert not intent.startup_main_pending and not intent.allowed_latch


@pytest.mark.parametrize('bad', ('none', 'producer', 'session', 'future', 'expired', 'unacknowledged',
                                 'active', 'native_request', 'panda_fault', 'axis_invalid', 'events_stale'))
def test_startup_admission_requires_bound_current_idle_proof(bad):
  from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, change, native_change, sample

  cp = CarInterface.get_params(CAR.MAZDA_CX5_2022, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  intent = MazdaStockCardIntent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  for tick in range(1, 4):
    now = NOW + tick * 10_000_000
    sm, native = sample(cp, now, now, tick, active=False, requested=False, allowed=False, event_stamp=now)
    producer = PRODUCER
    if bad == 'producer':
      producer = 'other-card'
    elif bad == 'session':
      change(sm, 'aolAxisState', lambda axis: setattr(axis, 'sessionId', 'other-axis'))
    elif bad == 'future':
      change(sm, 'aolAxisState', lambda axis, stamp=now: setattr(axis, 'sourceCarStateMonoTime', stamp + 1))
    elif bad == 'expired':
      change(sm, 'aolAxisState', lambda axis, stamp=now: setattr(axis, 'validUntilMonoTime', stamp - 1))
    elif bad == 'unacknowledged':
      change(sm, 'aolAxisState', lambda axis: setattr(axis, 'nativeAcknowledged', False))
    elif bad == 'active':
      change(sm, 'aolAxisState', lambda axis: setattr(axis, 'lateralActive', True))
    elif bad == 'native_request':
      native = native_change(sm, native, requestedLateral=True)
    elif bad == 'panda_fault':
      change(sm, 'pandaStates', lambda pandas: setattr(pandas[0], 'safetyRxChecksInvalid', True))
    elif bad == 'axis_invalid':
      sm.valid['aolAxisState'] = False
    elif bad == 'events_stale':
      sm.logMonoTime['onroadEvents'] = now - 1_500_000_001
    intent.observe_startup_readiness(sm, cs, native=native, panda_ready=True, now_ns=now, producer=producer)
    intent.update(cs.as_reader(), fault_active=False, now_ns=now)
  assert intent.allowed_latch is (bad == 'none')
  assert not native.lateralAllowed and not native.longitudinalAllowed
