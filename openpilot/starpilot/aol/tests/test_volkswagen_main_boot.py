"""Fresh stock MQB MAIN-on startup; Dom default follows MAIN without synthetic edge/SET."""
import pytest
from opendbc.car import structs, gen_empty_fingerprint
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import CAR
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.car.volkswagen.aol import VolkswagenStockCardIntent
from opendbc.safety.tests import test_volkswagen_stock_aol as stock_tests


@pytest.mark.parametrize('enabled', (False, True))
@pytest.mark.parametrize('main_action', (0, 9))
def test_current_valid_boot_main_default_intent_requires_no_set_or_main_cycle(enabled, main_action):
  fp = gen_empty_fingerprint()
  fp[0][0xAD] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, fp, [], False, False, False)
  owner = VolkswagenStockCardIntent(cp, AolSettings(enabled, 5., 0, main_action, (0, 0, 0), (0, 0, 0)))
  state = structs.CarState(canValid=True, vEgo=20., gearShifter=structs.CarState.GearShifter.drive)
  state.cruiseState.available = True
  for tick in range(15):
    owner.update(state, now_ns=1_000_000_000 + tick * 10_000_000)
  assert owner.allowed_latch == (enabled and main_action == 0)
  assert not state.cruiseState.enabled


@pytest.mark.parametrize('word', (8, 24))
def test_current_fresh_native_main_boot_retains_only_lateral_and_fault_does_not_rearm(word):
  owner = stock_tests.TestVolkswagenStockAol()
  try:
    owner.configure(word)
    for _ in range(12):
      owner.feed()
    owner.safety.aol_set_host_request(3)
    assert not owner.safety.get_controls_allowed()
    assert owner.safety.aol_get_permission_mask() == 1
    owner.feed(permanent=True)
    assert owner.safety.aol_get_permission_mask() == 0
    for _ in range(12):
      owner.feed()
    assert owner.safety.aol_get_permission_mask() == 0
  finally:
    owner.tearDown()


@pytest.mark.parametrize('main_action', (0, 9))
def test_startup_transport_wait_is_consumed_once_and_permanent_fault_never_rearms_held_main(main_action):
  fp = gen_empty_fingerprint()
  fp[0][0xAD] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, fp, [], False, False, False)
  owner = VolkswagenStockCardIntent(cp, AolSettings(True, 5., 0, main_action, (0, 0, 0), (0, 0, 0)))
  state = structs.CarState(canValid=True, vEgo=20., gearShifter=structs.CarState.GearShifter.drive)
  state.cruiseState.available = True
  owner.update(state, now_ns=1_000_000_000, fault_active=True)
  assert not owner.allowed_latch
  owner.update(state, now_ns=1_010_000_000, fault_active=False)
  assert owner.allowed_latch == (main_action == 0)
  assert not owner.main_startup_pending
  state.steerFaultPermanent = True
  owner.update(state, now_ns=1_020_000_000, fault_active=True)
  assert not owner.allowed_latch
  state.steerFaultPermanent = False
  owner.update(state, now_ns=1_030_000_000, fault_active=False)
  assert not owner.allowed_latch  # A held MAIN cannot undo a later disarming fault.


@pytest.mark.parametrize('word', (9, 25))
def test_debug_op_long_cold_main_grants_no_longitudinal_axis_before_native_set(word):
  from opendbc.safety.tests import test_volkswagen_long_aol as long_tests
  owner, debug = long_tests.configure(word)
  try:
    for _ in range(12):
      owner.feed()
    owner.safety.aol_set_host_request(3)
    assert not owner.safety.get_controls_allowed()
    assert owner.safety.aol_get_permission_mask() == (1 if debug else 0)
    assert not owner.safety.safety_tx_hook(owner.packet('ACC_06', {'ACC_Sollbeschleunigung_02': 0.5}))
  finally:
    owner.tearDown()


@pytest.mark.parametrize('main_action', (0, 9))
def test_missing_initial_can_fields_cannot_consume_main_boot_request(main_action):
  fp = gen_empty_fingerprint()
  fp[0][0xAD] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, fp, [], False, False, False)
  owner = VolkswagenStockCardIntent(cp, AolSettings(True, 5., 0, main_action, (0, 0, 0), (0, 0, 0)))
  # Parser defaults before cruise/EPS arrive are not qualified physical OFF/fault observations.
  state = structs.CarState(canValid=False, steerFaultPermanent=True, accFaulted=True)
  owner.update(state, now_ns=1_000_000_000, fault_active=True)
  assert not owner.allowed_latch
  assert owner.main_startup_pending == (main_action == 0)
  state.canValid = True
  state.steerFaultPermanent = False
  state.accFaulted = False
  state.vEgo = 20.
  state.gearShifter = structs.CarState.GearShifter.drive
  state.cruiseState.available = True
  owner.update(state, now_ns=1_010_000_000, fault_active=False)
  assert owner.allowed_latch == (main_action == 0)
  assert not owner.main_startup_pending


@pytest.mark.parametrize('alpha', (False, True))
def test_mqb_parked_delayed_start_keeps_main_request_unconsumed(alpha):
  from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
  from openpilot.starpilot.aol.runtime import AxisDecision
  from openpilot.selfdrive.selfdrived.events import Events
  from types import SimpleNamespace
  from openpilot.selfdrive.selfdrived.state import StateMachine
  from unittest.mock import patch
  from openpilot.cereal import log
  fp = gen_empty_fingerprint()
  fp[1][0xFD] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, fp, [], alpha, False, False)
  # The regular factory owner marks CP before creating Card intent.
  from openpilot.starpilot.car.volkswagen.aol import policy_for
  policy = policy_for(cp)
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  owner = VolkswagenStockCardIntent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, standstill=True, gearShifter=structs.CarState.GearShifter.drive)
  cs.cruiseState.available = True
  owner.update(cs, now_ns=1_000_000_000, fault_active=False)
  assert not owner.allowed_latch, 'VW_PARKED_START_CONSUMED_MAIN_ARM'
  assert owner.main_startup_pending
  # A delayed host source expires normally; no accepted actuation exists to lose.
  sd = SelfdriveD.__new__(SelfdriveD)
  sd.CP = cp
  sd.aol_replay = sd.axis_transport_required = True
  sd.aol_car_state_log_ns = 1_000_000_000
  sd.conditional_car_state_valid = True
  sd.aol_axis_decision = AxisDecision()
  sd.aol_last_intent = SimpleNamespace(allowedLatch=owner.allowed_latch, carStateLogMonoTime=1_000_000_000)
  sd.aol_publication_retained_intent = None
  sd.aol_fault_reason = 'none'
  sd.aol_authority_lost = False
  sd.initialized = True
  sd.enabled = sd.active = False
  sd.events = Events()
  sd.state_machine = StateMachine()
  with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False), patch.object(sd, 'retain_ordinary_source_state', return_value=False):
    sd._expire_axis_source(cs, 1_100_000_001)
  assert not sd.aol_authority_lost
  assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
  cs.standstill = False
  cs.vEgo = cs.vEgoRaw = 1.
  owner.update(cs, now_ns=1_200_000_000, fault_active=False)
  # A speed crossing alone cannot start a manual Alpha Long request after a source gap.
  # The actual Card/Panda callers exercise arming after healthy feedback returns.
  assert owner.allowed_latch is not alpha
  assert owner.main_startup_pending is alpha


@pytest.mark.parametrize('word', (8, 24, 9, 25))
def test_parked_native_start_waits_for_current_main_and_movement(word):
  from opendbc.safety.tests import test_volkswagen_long_aol as long_tests
  owner, debug = long_tests.configure(word)
  owner.manual = word in (24, 25)
  try:
    owner.safety.set_aol_test_heartbeat(False)
    for _ in range(100):
      owner.feed(moving=False)
      owner.safety.aol_set_host_request(0)
      assert owner.safety.aol_get_permission_mask() == 0
    owner.safety.set_aol_test_heartbeat(True)
    for _ in range(12):
      owner.feed()
    assert not owner.safety.get_controls_allowed()
    assert owner.safety.aol_get_permission_mask() == (1 if word in (8, 24) or debug else 0)
  finally:
    owner.tearDown()


@pytest.mark.parametrize('alpha', (False, True))
def test_main_on_during_permanent_fault_does_not_queue_automatic_rearm(alpha):
  fp = gen_empty_fingerprint()
  fp[1][0xFD] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, fp, [], alpha, False, False)
  owner = VolkswagenStockCardIntent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, vEgo=20., gearShifter=structs.CarState.GearShifter.drive)
  owner.update(cs, now_ns=1_000_000_000, fault_active=False)
  cs.cruiseState.available = True
  cs.steerFaultPermanent = True
  owner.update(cs, now_ns=1_010_000_000, fault_active=True)
  assert not owner.allowed_latch and not owner.main_startup_pending
  cs.steerFaultPermanent = False
  owner.update(cs, now_ns=1_020_000_000, fault_active=False)
  assert not owner.allowed_latch


def test_stock_mqb_main_rearm_waits_only_for_the_current_native_receipt():
  from openpilot.cereal import messaging
  from openpilot.starpilot.aol.tests.test_gm_held_native import Peers
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety
  from openpilot.starpilot.car.volkswagen.aol import awaiting_initial_request, policy_for

  fp = gen_empty_fingerprint()
  fp[0][0xAD] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_ATLAS_MK1, fp, [], False, False, False)
  policy = policy_for(cp)
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  owner = VolkswagenStockCardIntent(cp, AolSettings(True, 5., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, vEgo=8., vEgoRaw=8., gearShifter=structs.CarState.GearShifter.drive)
  cs.cruiseState.available = True
  arm = 2_000_000_000
  owner.update(cs, now_ns=arm - 300_000_000)
  cs.cruiseState.available = False
  owner.update(cs, now_ns=arm - 100_000_000)
  assert not owner.allowed_latch
  cs.cruiseState.available = True
  owner.update(cs, now_ns=arm)
  assert owner.allowed_latch

  now = arm + 110_000_000
  stamp = now - 2_000_000
  event = messaging.new_message('onroadEvents', 1, valid=True, logMonoTime=stamp)
  event.onroadEvents[0].name = 'pcmDisable'
  event.onroadEvents[0].userDisable = True
  axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=stamp)
  axis.aolAxisState.sessionId = axis.aolAxisState.faultSessionId = 'drive'
  axis.aolAxisState.intentProducerSessionId = 'card'
  axis.aolAxisState.sequence = 2
  axis.aolAxisState.faultReason = 'none'

  axis.aolAxisState.qualified = True
  axis.aolAxisState.desiredLateral = True
  axis.aolAxisState.observedMonoTime = axis.aolAxisState.faultEventMonoTime = stamp
  axis.aolAxisState.sourceCarStateMonoTime = stamp - 3_000_000
  axis.aolAxisState.validUntilMonoTime = stamp + 30_000_000
  health = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=stamp)
  health.pandaStates[0].safetyModel = 'volkswagen'
  health.pandaStates[0].safetyParam = 8
  health.pandaStates[0].alternativeExperience = 32
  native = SafetyState(1, True, stamp, stamp + 200_000_000, 15, 8, False, False, True, False, 'board', 'drive',
                       stamp, (PandaSlot(0, 'board', 15, 8, 32),))
  wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=stamp)
  wire.aolSafetyWire = encode_safety(native)
  feedback = TransportPauseFeedback(cp)
  feedback.session, feedback.sequence, feedback.ever_lateral_active = 'drive', 1, True
  feedback.signature = (None,) * 14 + ('card', 0)
  def waiting():
    return awaiting_initial_request(cp, cs, Peers([axis, event, health, wire]), native, feedback,
      owner, now_ns=now, producer='card')
  assert waiting()
  assert owner.intent_publication(cs, now)[-1] == arm + 200_000_000
  event.onroadEvents[0].name = 'buttonCancel'
  assert not waiting()
  event.onroadEvents[0].name = 'pcmDisable'
  cs.cruiseState.enabled = True
  owner.update(cs, now_ns=now)
  cs.cruiseState.enabled = False
  assert not waiting()
  owner.update(cs, now_ns=arm + 200_000_001)
  assert not owner.allowed_latch
