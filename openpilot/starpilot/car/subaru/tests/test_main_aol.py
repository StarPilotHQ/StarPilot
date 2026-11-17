"""A fresh physical MAIN waits for its own current fault observation."""
from types import SimpleNamespace

from opendbc.car import structs
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.car.subaru.aol import SubaruStockCardIntent


def state():
  cs = structs.CarState(canValid=True, gearShifter=structs.CarState.GearShifter.drive)
  cs.cruiseState.available = False
  return cs


def observe(owner, cs, now, event, fault):
  sm = SimpleNamespace(logMonoTime={'onroadEvents': event}, valid={'onroadEvents': True})
  owner.observe_startup_readiness(sm, cs, now_ns=now)
  owner.update(cs, now_ns=now, fault_active=fault)


def owner():
  return SubaruStockCardIntent(AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)), main_arm=True)


def test_main_waits_for_post_edge_observation_and_allows_stopped_intent():
  intent, cs = owner(), state()
  cs.standstill = cs.brakePressed = True
  observe(intent, cs, 1_000_000, 1_000_000, True)
  cs.cruiseState.available = True
  observe(intent, cs, 10_000_000, 1_000_000, True)
  assert not intent.allowed_latch
  observe(intent, cs, 20_000_000, 10_000_000, False)
  assert intent.allowed_latch
  assert not cs.cruiseState.enabled


def test_new_post_main_fault_consumes_gesture_without_rearming_when_it_clears():
  intent, cs = owner(), state()
  observe(intent, cs, 1_000_000, 1_000_000, False)
  cs.cruiseState.available = True
  observe(intent, cs, 10_000_000, 1_000_000, False)
  observe(intent, cs, 20_000_000, 10_000_000, True)
  observe(intent, cs, 30_000_000, 20_000_000, False)
  assert not intent.allowed_latch


def test_expired_main_edge_and_startup_held_main_cannot_arm():
  intent, cs = owner(), state()
  cs.cruiseState.available = True
  observe(intent, cs, 1_000_000, 1_000_000, False)
  assert not intent.allowed_latch
  cs.cruiseState.available = False
  observe(intent, cs, 2_000_000, 2_000_000, False)
  cs.cruiseState.available = True
  observe(intent, cs, 10_000_000, 2_000_000, False)
  observe(intent, cs, 211_000_000, 2_000_000, False)
  observe(intent, cs, 220_000_000, 220_000_000, False)
  assert not intent.allowed_latch


def test_physical_acc_fault_cancels_pending_main_edge():
  intent, cs = owner(), state()
  observe(intent, cs, 1_000_000, 1_000_000, False)
  cs.cruiseState.available = True
  observe(intent, cs, 10_000_000, 1_000_000, False)
  cs.accFaulted = True
  observe(intent, cs, 20_000_000, 10_000_000, False)
  cs.accFaulted = False
  observe(intent, cs, 30_000_000, 20_000_000, False)
  assert not intent.allowed_latch


def test_impreza_main_setting_is_exact_and_preserves_stock_assist_selection():
  from opendbc.car.subaru.interface import CarInterface
  from opendbc.car.subaru.values import CAR
  from opendbc.car.subaru.stock_aol import IMPREZA_MAIN_AOL, qualified
  from opendbc.car.subaru import software_cruise, sng
  from openpilot.starpilot.car.subaru.aol import policy_for, create_intent

  for word in (0, 8, 512, 520):
    cp = CarInterface.get_non_essential_params(CAR.SUBARU_IMPREZA_2020)
    cp.safetyConfigs[0].safetyParam = word
    policy = policy_for(cp)
    assert policy.intent_supported and policy.safety_param_addition == IMPREZA_MAIN_AOL
    assert create_intent(cp, owner().settings).main_arm
    cp.alternativeExperience = 32
    cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
    assert qualified(cp, marked_only=True)
    assert create_intent(cp, owner().settings).main_arm
    assert software_cruise.eligible(cp) and sng.eligible(cp)
    cp.alternativeExperience = 0
    assert not qualified(cp) and not software_cruise.eligible(cp) and not sng.eligible(cp)
    cp.alternativeExperience = 32
    cp.safetyConfigs[0].safetyParam |= 2
    assert not qualified(cp)
  cp = CarInterface.get_non_essential_params(CAR.SUBARU_FORESTER)
  cp.alternativeExperience = 32
  cp.safetyConfigs[0].safetyParam |= IMPREZA_MAIN_AOL
  assert not qualified(cp) and not sng.eligible(cp)


def test_outback_main_intent_uses_only_existing_exact_native_profiles():
  from opendbc.car.subaru.interface import CarInterface
  from opendbc.car.subaru.values import CAR, SubaruFlags
  from opendbc.car.subaru.stock_aol import qualified
  from openpilot.starpilot.car.subaru.aol import create_intent

  for stopped_start in (False, True):
    cp = CarInterface.get_non_essential_params(CAR.SUBARU_OUTBACK_2023)
    if stopped_start:
      cp.flags = int(cp.flags) | int(SubaruFlags.STOP_START_OFF)
      cp.safetyConfigs[0].safetyParam |= 256
    assert qualified(cp)
    assert create_intent(cp, owner().settings).main_arm
    cp.alternativeExperience = 32
    assert qualified(cp, marked_only=True)
    cp.safetyConfigs[0].safetyParam |= 2
    assert not qualified(cp) and not create_intent(cp, owner().settings).main_arm
  for candidate in (CAR.SUBARU_OUTBACK, CAR.SUBARU_LEGACY_2025):
    cp = CarInterface.get_non_essential_params(candidate)
    cp.alternativeExperience = 32
    assert qualified(cp) and not create_intent(cp, owner().settings).main_arm
