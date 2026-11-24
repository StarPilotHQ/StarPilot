"""A fresh physical MAIN waits for its own current fault observation."""
from types import SimpleNamespace

from opendbc.car import structs
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.runtime import AxisDecision
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
  legacy = CarInterface.get_non_essential_params(CAR.SUBARU_LEGACY_2025)
  legacy.alternativeExperience = 32
  legacy_intent = create_intent(legacy, owner().settings)
  assert qualified(legacy) and legacy_intent.main_arm and legacy_intent.allow_manumatic
  legacy_cs = state()
  legacy_cs.gearShifter = structs.CarState.GearShifter.manumatic
  observe(legacy_intent, legacy_cs, 1_000_000, 1_000_000, False)
  legacy_cs.cruiseState.available = True
  observe(legacy_intent, legacy_cs, 10_000_000, 1_000_000, False)
  observe(legacy_intent, legacy_cs, 20_000_000, 10_000_000, False)
  assert legacy_intent.allowed_latch

  # Pressing MAIN while still in Park, passing through Reverse, then shifting to Drive arms AOL.
  park_intent = create_intent(legacy, owner().settings)
  park_cs = state()
  park_cs.gearShifter = structs.CarState.GearShifter.park
  observe(park_intent, park_cs, 1_000_000, 1_000_000, False)
  park_cs.cruiseState.available = True
  observe(park_intent, park_cs, 10_000_000, 1_000_000, False)
  assert not park_intent.allowed_latch
  park_cs.gearShifter = structs.CarState.GearShifter.reverse
  observe(park_intent, park_cs, 20_000_000, 10_000_000, False)
  assert not park_intent.allowed_latch
  park_cs.gearShifter = structs.CarState.GearShifter.drive
  observe(park_intent, park_cs, 30_000_000, 20_000_000, False)
  observe(park_intent, park_cs, 40_000_000, 30_000_000, False)
  assert park_intent.allowed_latch

  outback = CarInterface.get_non_essential_params(CAR.SUBARU_OUTBACK)
  outback.alternativeExperience = 32
  assert qualified(outback) and not create_intent(outback, owner().settings).main_arm


def test_legacy_2025_manumatic_and_aol_intent():
  from opendbc.car.subaru.interface import CarInterface
  from opendbc.car.subaru.stock_aol import qualified
  from opendbc.car.subaru.values import CAR
  from openpilot.starpilot.aol.intent import independent_axis_requested
  from openpilot.starpilot.car.events import unsupported_gear
  from openpilot.starpilot.car.subaru.aol import create_intent

  Gear = structs.CarState.GearShifter
  ButtonEvent = structs.CarState.ButtonEvent
  ButtonType = ButtonEvent.Type

  assert Gear.manumatic in CarInterface.DRIVABLE_GEARS

  legacy = CarInterface.get_non_essential_params(CAR.SUBARU_LEGACY_2025)
  legacy.alternativeExperience = 32
  outback_2023 = CarInterface.get_non_essential_params(CAR.SUBARU_OUTBACK_2023)
  ascent_2023 = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  assert not unsupported_gear(legacy, Gear.manumatic)
  assert unsupported_gear(outback_2023, Gear.manumatic)
  assert unsupported_gear(ascent_2023, Gear.manumatic)

  aol_settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  disabled_settings = AolSettings(False, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  assert independent_axis_requested(aol_settings)
  assert not independent_axis_requested(disabled_settings)

  legacy_intent = create_intent(legacy, aol_settings)
  assert qualified(legacy) and legacy_intent.main_arm and legacy_intent.allow_manumatic

  legacy_cs = state()
  legacy_cs.gearShifter = Gear.drive
  observe(legacy_intent, legacy_cs, 1_000_000, 1_000_000, False)
  legacy_cs.cruiseState.available = True
  observe(legacy_intent, legacy_cs, 10_000_000, 1_000_000, False)
  observe(legacy_intent, legacy_cs, 20_000_000, 10_000_000, False)
  assert legacy_intent.allowed_latch

  legacy_cs.gearShifter = Gear.manumatic
  legacy_cs.buttonEvents = [
    ButtonEvent.new_message(type=ButtonType.lkas, pressed=True),
    ButtonEvent.new_message(type=ButtonType.lkas, pressed=False),
  ]
  observe(legacy_intent, legacy_cs, 30_000_000, 20_000_000, False)
  assert legacy_intent.allowed_latch

  legacy_cs.buttonEvents = []
  observe(legacy_intent, legacy_cs, 40_000_000, 30_000_000, False)
  assert legacy_intent.allowed_latch


def test_legacy_2025_line_visibility_and_passive_dashcam_chimes():
  import time
  from openpilot.cereal import log, messaging
  from openpilot.common.params import Params
  from openpilot.selfdrive.controls.controlsd import _update_subaru_line_visibility
  from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
  from openpilot.selfdrive.selfdrived.events import Events
  from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
  from openpilot.starpilot.controllers.mode_actions import SwitchbackCooldown

  vis, timer = _update_subaru_line_visibility(None, 0.0, 0.55, 0.01)
  assert vis and timer == 0.0
  vis, timer = _update_subaru_line_visibility(vis, timer, 0.42, 0.01)
  assert vis and timer == 0.0
  for _ in range(24):
    vis, timer = _update_subaru_line_visibility(vis, timer, 0.35, 0.01)
    assert vis
  vis, timer = _update_subaru_line_visibility(vis, timer, 0.35, 0.01)
  assert not vis
  for _ in range(99):
    vis, timer = _update_subaru_line_visibility(vis, timer, 0.56, 0.01)
    assert not vis
  vis, timer = _update_subaru_line_visibility(vis, timer, 0.56, 0.01)
  assert vis

  now = time.monotonic_ns()
  names = ('deviceState', 'carControl', 'longitudinalPlan', 'starpilotLongitudinalPlan', 'modelV2', 'slcState')
  data = {name: getattr(messaging.new_message(name), name) for name in names}
  data['carControl'].hudControl.lanesVisible = True

  class _Sm:
    def __init__(self):
      self.frame = 1
      self.seen = dict.fromkeys(names, True)
      self.valid = dict.fromkeys(names, True)
      self.alive = dict.fromkeys(names, True)
      self.logMonoTime = dict.fromkeys(names, now)
      self.recv_time = dict.fromkeys(names, now / 1e9)

    def __getitem__(self, key):
      return data[key]

  drive = SelfdriveD.__new__(SelfdriveD)
  drive.CP = structs.CarParams(carFingerprint="SUBARU_LEGACY_2025", passive=True)
  drive.enabled = False
  drive.personality = log.LongitudinalPersonality.standard
  drive.is_metric = False
  drive.events = Events()
  drive.AM = AlertManager()
  drive.params = Params()
  drive.switchback_capable = False
  drive.switchback_setting_ns = now
  drive.switchback_cooldown_ns = 0
  drive.switchback_cooldown = SwitchbackCooldown()
  drive.aol_axis_decision = AxisDecision()
  drive.aol_fault_reason = 'none'
  drive.radar_recovery_enabled = False
  drive.legacy_2025_green_wheel_active = True
  vars(drive)['state_machine'] = SimpleNamespace(current_alert_types=[], soft_disable_timer=0)
  vars(drive)['sm'] = _Sm()

  cs = structs.CarState(canValid=True, canTimeout=False)
  cs.cruiseState.available = True

  drive.update_alerts(cs)
  assert not drive.legacy_2025_green_wheel_active
  assert drive.AM.current_alert.alert_type == ""

  drive.CP.passive = False
  drive.sm.frame += 1
  drive.update_alerts(cs)
  assert drive.legacy_2025_green_wheel_active
  assert drive.AM.current_alert.alert_type == "legacy2025AolEngage/enable"


def test_legacy_2025_manumatic_driver_monitoring_isolation(tmp_path):
  from unittest.mock import patch
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.selfdrive.monitoring.dmonitoringd import _read_allow_manumatic
  from openpilot.selfdrive.monitoring.policy import DriverMonitoring

  params = Params(str(tmp_path))
  assert _read_allow_manumatic(params) is None
  for fingerprint, expected in (("SUBARU_OUTBACK_2023", False), ("SUBARU_LEGACY_2025", True)):
    params.put("CarParams", structs.CarParams(carFingerprint=fingerprint).to_bytes(), block=True)
    assert _read_allow_manumatic(params) is expected

  car_state = structs.CarState(vEgo=10.0, gearShifter=structs.CarState.GearShifter.manumatic)
  selfdrive_state = messaging.new_message('selfdriveState').selfdriveState
  model_v2 = messaging.new_message('modelV2').modelV2
  model_v2.meta.disengagePredictions.brakeDisengageProbs = [0.0]
  extrinsics = messaging.new_message('extrinsicsCalibration').extrinsicsCalibration
  extrinsics.rpyCalib = [0.0, 0.0, 0.0]
  driver_state = messaging.new_message('driverStateV2').driverStateV2
  driver_state.leftDriverData.faceOrientation = [0.0, 0.0, 0.0]
  driver_state.leftDriverData.facePosition = [0.0, 0.0]
  driver_state.leftDriverData.faceOrientationStd = [0.0, 0.0]
  driver_state.leftDriverData.facePositionStd = [0.0, 0.0]
  sm = {
    'carState': car_state,
    'selfdriveState': selfdrive_state,
    'modelV2': model_v2,
    'extrinsicsCalibration': extrinsics,
    'driverStateV2': driver_state,
  }

  with patch('openpilot.selfdrive.monitoring.policy.Params', return_value=params):
    default_dm = DriverMonitoring(always_on=True)
    default_dm.awareness = 0.8
    default_dm.run_step(sm)
    assert default_dm.awareness == 1.0

    legacy_dm = DriverMonitoring(always_on=True, allow_manumatic=True)
    legacy_dm.run_step(sm)
    car_state.gearShifter = structs.CarState.GearShifter.drive
    drive_dm = DriverMonitoring(always_on=True, allow_manumatic=True)
    drive_dm.run_step(sm)
    assert legacy_dm.awareness == drive_dm.awareness < 1.0
