import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, ButtonType, CanData, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.dashboard_speed_limit import Status
from opendbc.car.volvo.eucd_can import calculate_eucd_lka_checksum, create_eucd_lka_msg
from opendbc.car.volvo.eucd_carcontroller import CarControllerEUCD
from opendbc.car.volvo.eucd_carstate import CarStateEUCD
from opendbc.car.volvo.eucd_params import CarControllerEUCDParams, EcmAccState
from opendbc.car.volvo.interface import CarInterface
from opendbc.car.volvo.values import CAR, DBC, VolvoSafetyFlags

GearShifter = structs.CarState.GearShifter
DBC_NAME = DBC[CAR.VOLVO_V60][Bus.pt]


def params(alpha=False, release=False):
  cp = CarInterface.get_non_essential_params(CAR.VOLVO_V60)
  return CarInterface._get_params(cp, CAR.VOLVO_V60, {0: {}, 1: {}, 2: {}}, [], alpha, release, False)


def required_frames(**overrides):
  packer = CANPacker(DBC_NAME)
  values = {
    "VehicleSpeed1": {"VehicleSpeed": 72},
    "AccPedal": {"AccPedal": 0},
    "Brake_Info": {"BrakePedal": 0},
    "PSCM1": {"SteeringAngleServo": 12.5, "EPSTorque": 10, "LKATorque": 5},
    "ACC_Speed": {"ACC_Speed": 100},
    "MiscCarInfo": {"TurnSignal": 1},
    "Doors": {"DriverDoorClosed": 1, "PassengerDoorClosed": 1},
    "SAS0": {"SteeringRateOfChange": 2, "SteeringDirection": 0},
    "EngineData": {"ACC_State_ECM": EcmAccState.ENGAGED},
    "CCButtons": {},
    "GearSelector": {"GearLever": 3},
    "WheelRear": {}, "BrakeReqAck": {}, "BrakeHold": {}, "AccHold": {},
  }
  cam_values = {"FSM0": {"ACC_Available": 1}, "FSM1": {}, "FSM3": {"ACC_Standstill": 0}, "FSM4": {}, "FSM5": {"TSR_Speed": 0}}
  for name, signals in overrides.items():
    (cam_values if name in cam_values else values)[name] = signals
  return [CanData(*packer.make_can_msg(name, 0, signals)) for name, signals in values.items()] + \
         [CanData(*packer.make_can_msg(name, 2, signals)) for name, signals in cam_values.items()]


def car_state(**overrides):
  cp = params()
  cs = CarStateEUCD(cp)
  parsers = cs.get_can_parsers(cp)
  frames = required_frames(**overrides)
  for parser in parsers.values():
    parser.update([(1_000_000_000, frames)])
  return cs, parsers, cp


def test_params_and_alpha_long():
  for alpha in (False, True):
    for release in (False, True):
      cp = params(alpha, release)
      config = cp.safetyConfigs[0]
      assert (config.safetyModel, config.safetyParam) == (structs.CarParams.SafetyModel.volvo, VolvoSafetyFlags.EUCD.value)
      assert cp.pcmCruise and cp.alphaLongitudinalAvailable
      assert cp.openpilotLongitudinalControl == alpha
      assert not cp.radarUnavailable  # Delphi ESR 2.5 on the aux bus
      assert cp.steerControlType == structs.CarParams.SteerControlType.angle
      assert cp.minSteerSpeed == 0.
      assert cp.steerActuatorDelay == pytest.approx(0.2)
      assert cp.steerLimitTimer == pytest.approx(0.8)
      assert cp.steerAtStandstill


def test_interface_selects_the_eucd_implementation():
  cp = params()
  interface = CarInterface(cp)
  assert isinstance(interface.CS, CarStateEUCD)
  assert isinstance(interface.CC, CarControllerEUCD)


def test_required_can_and_state_contract():
  cs, parsers, _ = car_state()
  assert all(parser.can_valid for parser in parsers.values())
  ret = cs.update(parsers)
  assert ret.vEgoRaw == pytest.approx(72 * CV.KPH_TO_MS)
  assert ret.steeringAngleDeg == pytest.approx(12.5, abs=0.05)
  assert ret.gearShifter == GearShifter.drive
  assert ret.cruiseState.available and ret.cruiseState.enabled
  assert ret.cruiseState.speed == pytest.approx(100 * CV.KPH_TO_MS)
  assert ret.leftBlinker and not ret.rightBlinker
  assert not ret.doorOpen


@pytest.mark.parametrize("state, engaged", [(EcmAccState.OFF, False), (EcmAccState.STANDBY, False), (EcmAccState.ENGAGED, True)])
def test_cruise_follows_the_ecm(state, engaged):
  cs, parsers, _ = car_state(EngineData={"ACC_State_ECM": state})
  assert cs.update(parsers).cruiseState.enabled == engaged


@pytest.mark.parametrize("gear, expected", [(0, GearShifter.park), (1, GearShifter.reverse), (2, GearShifter.neutral), (3, GearShifter.drive)])
def test_gear_lever(gear, expected):
  cs, parsers, _ = car_state(GearSelector={"GearLever": gear})
  assert cs.update(parsers).gearShifter == expected


def test_gas_press_and_release_have_the_panda_hysteresis():
  # A foot held at 10% flickered gas each frame (route 00000007): 10% presses, under 9% releases, as the panda
  cs, parsers, _ = car_state()
  t = 1_000_000_000
  for pedal, pressed in ((9.9, False), (10, True), (9.9, True), (9, True), (8.9, False), (9.9, False), (10, True)):
    t += 10_000_000
    for parser in parsers.values():
      parser.update([(t, required_frames(AccPedal={"AccPedal": pedal}))])
    assert cs.update(parsers).gasPressed == pressed, pedal


def test_driver_onoff_is_a_cancel_button():
  # Route 22, 418.4 s: the driver's On/Off reached the panda's permission before OP saw the ECM disengage
  cs, parsers, _ = car_state()
  t = 1_000_000_000
  events = []
  for pressed in (False, True, True, False):
    t += 20_000_000
    buttons = {"ACCOnOffBtn": 1, "ACCOnOffBtnInv": 0} if pressed else {"ACCOnOffBtnInv": 1}
    for parser in parsers.values():
      parser.update([(t, required_frames(CCButtons=buttons))])
    events.append([(e.type, e.pressed) for e in cs.update(parsers).buttonEvents if e.type == ButtonType.cancel])
  assert events == [[], [(ButtonType.cancel, True)], [], [(ButtonType.cancel, False)]]


def control(lat_active, angle=0., cancel=False):
  cc = structs.CarControl()
  cc.latActive = lat_active
  cc.actuators.steeringAngleDeg = angle
  cc.cruiseControl.cancel = cancel
  return cc.as_reader()


def controller_state(speed=20., angle=0., brake=False):
  cs = type("CS", (), {})()
  cs.out = structs.CarState(vEgo=speed, vEgoRaw=speed, steeringAngleDeg=angle, brakePressed=brake)
  cs.eucd_pscm_stock_values = {"byte0": 0, "byte4": 0, "byte7": 0, "LKAActive": 0xFF, "EPSTorque": 0}
  cs.eucd_ecm_acc_engaged = False
  cs.eucd_ecm_standstill_ready = False
  cs.eucd_car_hold = False
  cs.eucd_acc_distance = 255
  cs.eucd_driver_resume = False
  cs.eucd_stock_FSM1 = {"ACC_Braking": 0}
  cs.eucd_stock_FSM3 = {"ACC_AccelerationRequest": 0., "ACC_Mode": 1, "ACC_NoTargetCancel": 0, "Byte_6": 0, "FrameKind": 0x81, "ACC_Check": 0}
  for name in ("FSM0", "FSM4", "WheelRear", "BrakeReqAck", "BrakeHold", "EngineData", "AccHold", "CCButtons"):
    setattr(cs, f"eucd_stock_{name}_frames", [])
  return cs


def controller():
  return CarControllerEUCD({Bus.pt: DBC_NAME}, params())


def test_inactive_sends_zero_angle_and_no_direction():
  sends = controller().update(control(False, 10.), controller_state(), 0)[1]
  assert [s[0] for s in sends][:2] == [0x262, 0x246]
  assert sends[0][1][5] & 0x3 == 0
  assert ((sends[0][1][3] & 0x3F) << 8) + sends[0][1][4] == 8192  # 0 deg


def test_active_steering_is_rate_limited_and_50hz():
  cc = controller()
  new_actuators, sends = cc.update(control(True, 10.), controller_state(), 0)
  assert [s[0] for s in sends][:2] == [0x262, 0x246]
  assert 0 < new_actuators.steeringAngleDeg <= 0.15 + 1e-3
  assert 0x262 not in [s[0] for s in cc.update(control(True, 10.), controller_state(), 0)[1]]
  assert sends[0][1][5] & 0x3 == 2  # left


def test_direction_flip_pauses_on_none():
  cc = controller()
  cc.eucd_apply_steer_prev = 0.05
  cc.eucd_apply_steer_dir_prev = 2
  cc.eucd_latActive_prev = True
  sends = cc.update(control(True, -10.), controller_state(angle=5.), 0)[1]
  assert sends[0][1][5] & 0x3 == 0
  assert cc.eucd_steer_blocked and cc.eucd_steer_blocked_cnt == 7


def test_pscm_relay_clears_lka_state():
  sends = controller().update(control(False), controller_state(angle=4.), 0)[1]
  assert sends[1][2] == 2
  parser_values = {"LKAActive": 0xFF & 0xF5}
  packer = CANPacker(DBC_NAME)
  expected = packer.make_can_msg("PSCM1", 2, {**parser_values, "SteeringAngleServo": 4., "LKATorque": 0, "EPSTorque": 0})
  assert sends[1][1] == expected[1]


@pytest.mark.parametrize("speed, brake, sent", [(20., False, True), (0., False, False), (20., True, False)])
def test_cancel_only_while_moving_and_off_the_brake(speed, brake, sent):
  sends = controller().update(control(False, cancel=True), controller_state(speed, brake=brake), 0)[1]
  assert (0x127 in [s[0] for s in sends]) == sent


def test_lka_checksum_matches_the_frame():
  packer = CANPacker(DBC_NAME)
  _, dat, _ = create_eucd_lka_msg(packer, -7.5, 1)
  assert dat[6] == calculate_eucd_lka_checksum(dat)


def test_engine_firmware_identifies_the_v60():
  from opendbc.car.fw_versions import match_fw_to_car
  fw = structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.engine, address=0x7e0, subAddress=0, brand="volvo",
                               fwVersion=b"31361041 AJ" + bytes(13))
  assert match_fw_to_car([fw], "")[1] == {CAR.VOLVO_V60}


CCBUTTONS = 0x127
# CCButtons frames from V60 routes: idle, time gap increase, time gap decrease
IDLE = bytes.fromhex("88108761a7ff0000")
GAP_INCREASE = bytes.fromhex("88108761a5ff0200")
GAP_DECREASE = bytes.fromhex("88108761a3ff0400")
# increase bit set without its inverted copy cleared: not a press
GAP_INCREASE_NO_INV = bytes.fromhex("88108761a7ff0200")


@pytest.fixture
def car():
  cp = params()
  return CarStateEUCD(cp), CarStateEUCD.get_can_parsers(cp)


def step(car, t, frames):
  cs, parsers = car
  for parser in parsers.values():
    parser.update([(t, frames)])
  return cs.update(parsers)


def button_step(car, t, dat):
  return [(be.type, be.pressed) for be in step(car, t, [CanData(CCBUTTONS, dat, 0)]).buttonEvents]


@pytest.mark.parametrize("pressed", [GAP_INCREASE, GAP_DECREASE])
def test_gap_button_press_and_release(car, pressed):
  assert button_step(car, 1_000_000, IDLE) == []
  assert button_step(car, 2_000_000, pressed) == [(ButtonType.gapAdjustCruise, True)]
  assert button_step(car, 3_000_000, pressed) == []
  assert button_step(car, 4_000_000, IDLE) == [(ButtonType.gapAdjustCruise, False)]


def test_gap_button_needs_inverted_signal(car):
  assert button_step(car, 1_000_000, IDLE) == []
  assert button_step(car, 2_000_000, GAP_INCREASE_NO_INV) == []


def test_driver_resume_events(car):
  # Resume needs the bit set and its inverted copy cleared, frames from route 8ab
  resume = bytes.fromhex("88108761a7df0020")
  no_inv = bytes.fromhex("88108761a7ff0020")
  assert button_step(car, 1_000_000, IDLE) == []
  assert button_step(car, 2_000_000, no_inv) == []
  assert button_step(car, 3_000_000, resume) == [(ButtonType.resumeCruise, True)]
  assert button_step(car, 4_000_000, resume) == []
  assert button_step(car, 5_000_000, IDLE) == [(ButtonType.resumeCruise, False)]


@pytest.mark.parametrize("speed_kph, expected", [(0, []), (40, [(ButtonType.accelCruise, False)])])
def test_set_plus_stopped_is_not_accel(car, speed_kph, expected):
  # Stopped, Set+ only sets the speed: an accelCruise there reads as "go" to the planner (route 8b2, 572 s)
  packer = CANPacker(DBC_NAME)
  events = []
  for t, set_kph in enumerate((30, 30, 35)):
    msgs = [packer.make_can_msg("EngineData", 0, {"ACC_State_ECM": EcmAccState.ENGAGED}),
            packer.make_can_msg("VehicleSpeed1", 0, {"VehicleSpeed": speed_kph}),
            packer.make_can_msg("ACC_Speed", 0, {"ACC_Speed": set_kph})]
    ret = step(car, 1_000_000 * (t + 1), [CanData(*m) for m in msgs])
    events = [(be.type, be.pressed) for be in ret.buttonEvents]
  assert events == expected


@pytest.mark.parametrize("tsr_kph, status", [(0, Status.ABSENT), (30, Status.VALID), (80, Status.VALID), (120, Status.VALID)])
def test_tsr_speed_limit(tsr_kph, status):
  cs, parsers, _ = car_state(FSM5={"TSR_Speed": tsr_kph})
  packer = CANPacker(DBC_NAME)
  # the parser learns the FSM5 cadence over repeated frames before the tracker trusts it
  for i in range(1, 12):
    frame = CanData(*packer.make_can_msg("FSM5", 2, {"TSR_Speed": tsr_kph}))
    for parser in parsers.values():
      parser.update([(1_000_000_000 + i * 100_000_000, [frame])])
    cs.update(parsers)
  observation = cs.dashboard_limit.observation
  assert observation.status == status
  assert observation.speed_mps == pytest.approx(tsr_kph * CV.KPH_TO_MS)


def test_longitudinal_tx_is_50hz_and_keeps_the_relays():
  cc = controller()
  cs = controller_state()
  cs.eucd_stock_CCButtons_frames = [{"ACCResumeBtn": 0, "ACCResumeBtnInv": 1, "ACCSetBtn": 0, "ACCSetBtnInv": 1}]
  cs.eucd_ecm_acc_engaged = True
  sent = []
  for i in range(10):
    sent += [s[0] for s in cc.update(control(False), cs, i * 10_000_000)[1]]
  assert sent.count(0x270) == sent.count(0x260) == 5  # FSM3, FSM1
  assert sent.count(0x127) == 10  # CEM frames relayed to the camera


def test_no_fsm1_fsm3_with_the_ecm_off():
  # Route 2c, 837 s: with the ECM off the panda forwards the camera's frames and blocked OP's copies
  cc = controller()
  cs = controller_state()
  sent = []
  for i in range(10):
    sent += [s[0] for s in cc.update(control(False), cs, i * 10_000_000)[1]]
  assert 0x270 not in sent and 0x260 not in sent


def test_lka_press_events(car):
  packer = CANPacker(DBC_NAME)
  cs, _ = car
  presses = []
  for t, lka in enumerate((1, 1, 0, 0, 1)):
    ret = step(car, 1_000_000 * (t + 1), [CanData(*packer.make_can_msg("CCButtons", 0, {"LKA_State": lka}))])
    presses.append(sum(be.type == ButtonType.lkas and be.pressed for be in ret.buttonEvents))
    assert cs.eucd_lka_on == bool(lka)
  assert presses == [0, 0, 1, 0, 1]  # one press per LKA_State flip, none at start


def test_radar_publishes_an_esr_track():
  # card crashed on the first valid track: aRel/yvRel/measured are deprecated in the v7 RadarPoint
  from opendbc.car.volvo.eucd_params import CANBUS
  from opendbc.car.volvo.radar_interface import DELPHI_ESR_TRACK_NAMES, RadarInterface
  ri = RadarInterface(params())
  packer = CANPacker(DBC[CAR.VOLVO_V60][Bus.radar])
  track = {"CAN_TX_TRACK_STATUS": 3, "CAN_TX_TRACK_RANGE": 40., "CAN_TX_TRACK_ANGLE": 0., "CAN_TX_TRACK_RANGE_RATE": -2.}
  ret = None
  for sweep in range(4):
    frames = [CanData(*packer.make_can_msg(name, CANBUS.body, track if name == "Target1" else {})) for name in DELPHI_ESR_TRACK_NAMES]
    ret = ri.update([(1_000_000_000 + sweep * 50_000_000, frames)])
  assert ret is not None and len(ret.points) == 1
  point = ret.points[0]
  assert point.dRel == pytest.approx(40., abs=0.1)
  assert point.yRel == pytest.approx(0., abs=0.1)
  assert point.vRel == pytest.approx(-2., abs=0.01)


def test_stopping_keeps_the_camera_brake():
  # Route 2e, 59 s: the planner's -0.15 at the stopping flag dropped the brake to 0 and the car rolled on a climb
  cc = controller()
  cs = controller_state(speed=0.5)
  cs.eucd_ecm_acc_engaged = True
  cc.eucd_own = True
  cc.eucd_tpl_fsm3 = {"data": dict(cs.eucd_stock_FSM3), "type": {**cs.eucd_stock_FSM3, "FrameKind": 0}}
  cc.eucd_tpl_fsm4 = {"Byte_0": 0x55, "ACC_BrakeRequest": 0, "ACC_BrakeTarget": 13332, "Byte_5_b7": 1}
  cc.eucd_tpl_fsm0 = {0: {"Byte_0": 0, "ACC_AbortCode": 0, "ACC_Enabled": 1}}
  CC = structs.CarControl(enabled=True, latActive=False, longActive=True)
  CC.actuators.accel = -0.15
  sends = cc.update(CC.as_reader(), cs, 0)[1]
  fsm3 = next(s[1] for s in sends if s[0] == 0x270)
  assert cc.eucd_stopping and fsm3[2] & 0x3 == 0  # BRAKE
  assert fsm3[1] == round((CarControllerEUCDParams.EUCD_STOPPING_ACCEL + 5.04) / 0.04)
