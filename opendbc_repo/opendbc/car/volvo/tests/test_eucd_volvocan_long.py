import copy
import math

import pytest

from opendbc.can.packer import CANPacker
from opendbc.can.parser import CANParser
from opendbc.car.volvo import eucd_can as volvocan
from opendbc.car.volvo.eucd_params import CarControllerEUCDParams as CarControllerParams, EcmAccState, LongMode

DBC = "volvo_v60_2015_pt"
FSM3 = 0x270

# stock FSM3 frames from the camera, route 00000808 (ACC engaged); b4=0x81 data, 0x00 type
STOCK = {
  ("data", LongMode.BRAKE): ["aa7ad40081000000", "aa7ed40081000000"],
  ("data", LongMode.COAST): ["aa6ed5b881000000", "aa78d5dd81000000"],
  ("data", LongMode.DRIVE): ["aa98d65f81000000", "aa88d61d81000000"],
  ("type", LongMode.BRAKE): ["0082000000000000", "0076000000000000"],
  ("type", LongMode.COAST): ["007a01ec00000000", "007c01f300000000"],
  ("type", LongMode.DRIVE): ["0088022100000000", "008a022600000000"],
}
ALL_FRAMES = [(kind, mode, bytes.fromhex(h)) for (kind, mode), hs in STOCK.items() for h in hs]


def parse(name: str, addr: int, freq: int, dat: bytes) -> dict:
  cp = CANParser(DBC, [(name, freq)], 0)
  cp.update([(0, [(addr, dat, 0)])])
  return copy.copy(cp.vl[name])


def parse_fsm3(dat: bytes) -> dict:
  return parse("FSM3", FSM3, 50, dat)


def build(stock: bytes, accel=None, mode=None) -> bytes:
  fsm3 = parse_fsm3(stock)
  if accel is None:
    accel = fsm3["ACC_AccelerationRequest"]
  return volvocan.create_eucd_longitudinal(CANPacker(DBC), fsm3, accel, int(fsm3["ACC_Check"]), mode)[1]


@pytest.mark.parametrize("kind,mode,stock", ALL_FRAMES)
def test_relay_is_byte_identical(kind, mode, stock):
  assert build(stock) == stock


@pytest.mark.parametrize("kind,mode,stock", ALL_FRAMES)
def test_stock_mode_and_traction_reproduce_frame(kind, mode, stock):
  # Camera's own mode leaves Byte_2 untouched
  out = build(stock, mode=mode)
  assert out[2] == stock[2]
  assert out[:2] == stock[:2] and out[4:] == stock[4:]
  if mode == LongMode.BRAKE:
    assert out[3] == 0


@pytest.mark.parametrize("kind,stock_mode,stock", ALL_FRAMES)
@pytest.mark.parametrize("mode", list(LongMode))
def test_op_mode_keeps_cadence(kind, stock_mode, stock, mode):
  accel = {LongMode.BRAKE: -1.5, LongMode.COAST: -0.1, LongMode.DRIVE: 0.8}[mode]
  out = build(stock, accel=accel, mode=mode)
  assert out[2] & 0x03 == mode
  assert out[2] & 0xFC == stock[2] & 0xFC  # 0xD4 data / 0x00 type kept
  assert out[4] == stock[4]                # data/type marker kept
  assert out[0] == stock[0]                # standstill and status bits kept
  assert out[1] == math.floor((accel + 5.04) / 0.04 + 0.5)  # packer rounds half up
  assert out[3] == volvocan.eucd_fsm3_traction(accel, mode)


def test_traction():
  assert volvocan.eucd_fsm3_traction(-2.0, LongMode.BRAKE) == 0
  assert volvocan.eucd_fsm3_traction(1.0, LongMode.DRIVE) == 91
  assert volvocan.eucd_fsm3_traction(2.0, LongMode.DRIVE) == 188
  assert volvocan.eucd_fsm3_traction(-0.5, LongMode.DRIVE) == 0
  assert volvocan.eucd_fsm3_traction(-0.1, LongMode.COAST) == 256 - 18
  assert volvocan.eucd_fsm3_traction(0.5, LongMode.COAST) == 0xFF  # never non-negative in COAST


def test_mode_hysteresis():
  CCP = CarControllerParams
  m = LongMode.COAST
  m = volvocan.eucd_next_long_mode(m, -0.4)
  assert m == LongMode.COAST
  m = volvocan.eucd_next_long_mode(m, CCP.EUCD_BRAKE_ENTER - 0.01)
  assert m == LongMode.BRAKE
  m = volvocan.eucd_next_long_mode(m, CCP.EUCD_BRAKE_EXIT - 0.05)  # stays braking until BRAKE_EXIT
  assert m == LongMode.BRAKE
  m = volvocan.eucd_next_long_mode(m, CCP.EUCD_BRAKE_EXIT + 0.05)
  assert m == LongMode.COAST
  m = volvocan.eucd_next_long_mode(m, 0.06)
  assert m == LongMode.COAST
  m = volvocan.eucd_next_long_mode(m, 0.1)
  assert m == LongMode.DRIVE
  m = volvocan.eucd_next_long_mode(m, 0.05)
  assert m == LongMode.DRIVE
  m = volvocan.eucd_next_long_mode(m, -1.0)  # hard brake: DRIVE straight to BRAKE
  assert m == LongMode.BRAKE
  m = volvocan.eucd_next_long_mode(m, 1.0)
  assert m == LongMode.DRIVE


def test_brake_enters_earlier_when_slower():
  # Route 22: below 20 km/h the camera brakes from -0.1; coasting there let D creep the car toward the lead
  kph = 1 / 3.6
  assert volvocan.eucd_next_long_mode(LongMode.COAST, -0.15, v_ego=8 * kph) == LongMode.BRAKE
  assert volvocan.eucd_next_long_mode(LongMode.COAST, -0.15, v_ego=30 * kph) == LongMode.COAST
  assert volvocan.eucd_next_long_mode(LongMode.COAST, -0.35, v_ego=30 * kph) == LongMode.BRAKE
  # highway unchanged
  assert volvocan.eucd_next_long_mode(LongMode.COAST, -0.45, v_ego=100 * kph) == LongMode.COAST
  assert volvocan.eucd_next_long_mode(LongMode.COAST, -0.55, v_ego=100 * kph) == LongMode.BRAKE
  # slow: stays braking until OP asks for traction, never left with a decel request
  assert volvocan.eucd_next_long_mode(LongMode.BRAKE, -0.01, v_ego=8 * kph) == LongMode.BRAKE
  assert volvocan.eucd_next_long_mode(LongMode.BRAKE, 0.1, v_ego=8 * kph) == LongMode.DRIVE
  for v in (0., 10., 25., 40., 120.):
    enter, exit_ = volvocan.eucd_brake_thresholds(v * kph)
    assert enter < exit_ <= 0.


FSM4 = 0x31A
FSM1 = 0x260
# stock camera frames, route 00000808 (ACC engaged)
STOCK_FSM4 = ["aaf1343a8ef24600", "aaf15e5e8af3cb00", "aaf173748ab40b00", "aaf173738ab3f800"]  # Byte_5 0xF2/0xF3 brake, 0xB4/0xB3 not
STOCK_FSM1 = ["ffffb80049e37408", "5ad4bc0049e37408", "46ff000000e31408", "23f4040000e31408"]  # Byte_2 data 0xB8/0xBC, type 0x00/0x04


@pytest.mark.parametrize("stock", [bytes.fromhex(h) for h in STOCK_FSM4])
def test_fsm4_relay_is_byte_identical(stock):
  assert volvocan.create_eucd_fsm4(CANPacker(DBC), parse("FSM4", FSM4, 33, stock), 0.0)[1] == stock


def fsm4(stock: bytes, accel: float, mode) -> bytes:
  return volvocan.create_eucd_fsm4(CANPacker(DBC), parse("FSM4", FSM4, 33, stock), accel, mode)[1]


@pytest.mark.parametrize("stock", [bytes.fromhex(h) for h in STOCK_FSM4])
@pytest.mark.parametrize("accel", [-3.5, -2.0, -1.0, -0.6, -0.1])
def test_fsm4_op_brake_sets_value_from_accel(stock, accel):
  out = fsm4(stock, accel, LongMode.BRAKE)
  value = (out[5] << 8) | out[6]
  assert value == volvocan.eucd_fsm4_brake_value(accel, True)
  assert value & 0xC000 == 0xC000  # bit15 always, bit14 = hydraulic brake request
  assert out[:5] == stock[:5] and out[7:] == stock[7:]


@pytest.mark.parametrize("accel, camera", [(-3.0, 12568), (-2.5, 12688), (-2.0, 12821), (-1.0, 13084), (-0.5, 13212)])
def test_fsm4_brake_value_as_the_camera(accel, camera):
  # Route 29, 1110 s: the old map asked ~0.5 m/s^2 less than the camera at -2.8 and the stop ended at 1.5 m
  assert abs((volvocan.eucd_fsm4_brake_value(accel, True) & 0x3FFF) - camera) <= 10


def test_fsm4_brake_value_matches_stock_levels():
  # Matches the stock Byte_5 levels
  assert volvocan.eucd_fsm4_brake_value(-3.5, True) >> 8 == 0xF0
  assert volvocan.eucd_fsm4_brake_value(0.0, True) >> 8 == 0xF4
  values = [volvocan.eucd_fsm4_brake_value(a / 10, True) for a in range(-40, 5)]
  assert values == sorted(values)  # stronger decel, lower value
  # Smooth: a small step moves a few counts
  assert all(abs(volvocan.eucd_fsm4_brake_value(a / 50, True) - volvocan.eucd_fsm4_brake_value((a + 1) / 50, True)) < 16 for a in range(-200, 10))


@pytest.mark.parametrize("stock", [bytes.fromhex(h) for h in STOCK_FSM4[2:]])
@pytest.mark.parametrize("mode", [LongMode.COAST, LongMode.DRIVE])
def test_fsm4_no_brake_keeps_camera_value(stock, mode):
  assert fsm4(stock, 0.3, mode) == stock


@pytest.mark.parametrize("stock", [bytes.fromhex(h) for h in STOCK_FSM4[:2]])
@pytest.mark.parametrize("mode", [LongMode.COAST, LongMode.DRIVE])
def test_fsm4_camera_brake_cleared_when_op_does_not_brake(stock, mode):
  out = fsm4(stock, 0.3, mode)
  value = (out[5] << 8) | out[6]
  assert value == volvocan.eucd_fsm4_brake_value(0.3, False)
  assert value & 0xC000 == 0x8000
  assert out[:5] == stock[:5] and out[7:] == stock[7:]


@pytest.mark.parametrize("stock", [bytes.fromhex(h) for h in STOCK_FSM1])
def test_fsm1_relay_is_byte_identical(stock):
  assert volvocan.create_eucd_radar(CANPacker(DBC), parse("FSM1", FSM1, 50, stock))[1] == stock


@pytest.mark.parametrize("stock", [bytes.fromhex(h) for h in STOCK_FSM1])
@pytest.mark.parametrize("mode", list(LongMode))
def test_fsm1_brake_bit_follows_op(stock, mode):
  out = volvocan.create_eucd_radar(CANPacker(DBC), parse("FSM1", FSM1, 50, stock), mode)[1]
  assert out[2] == (stock[2] & ~0x04) | (0x04 if mode == LongMode.BRAKE else 0)
  assert out[:2] == stock[:2] and out[3:] == stock[3:]


WHEEL_REAR_ADDR = 0x220
BRAKE_REQ_ACK_ADDR = 0x299
# Stock WheelRear and BrakeReqAck frames, route 816
STOCK_WHEEL_REAR = ["8b11e000a2577a30", "8b11e400a245a216", "8b11e500a1f6a1d3"]
STOCK_BRAKE_REQ_ACK = ["40054c020dff2235", "4005480201f72239"]


@pytest.mark.parametrize("stock", [bytes.fromhex(h) for h in STOCK_WHEEL_REAR])
@pytest.mark.parametrize("standby", [False, True])
def test_wheel_rear_relay_to_camera(stock, standby):
  addr, dat, bus = volvocan.create_eucd_cam_wheel_status(CANPacker(DBC), parse("WheelRear", WHEEL_REAR_ADDR, 50, stock), standby)
  assert (addr, bus) == (WHEEL_REAR_ADDR, 2)
  # standby: no brake request (bit2) nor ACC brake applied (bit0), 0xE5 -> 0xE0, and no hold (b0 bit6)
  assert dat[2] == (stock[2] & ~0x05 if standby else stock[2])
  assert dat[0] == (stock[0] & ~0x40 if standby else stock[0])
  assert dat[1] == stock[1] and dat[3:] == stock[3:]


@pytest.mark.parametrize("stock", [bytes.fromhex(h) for h in STOCK_BRAKE_REQ_ACK])
@pytest.mark.parametrize("standby", [False, True])
def test_brake_req_ack_relay_to_camera(stock, standby):
  addr, dat, bus = volvocan.create_eucd_cam_brake_request_ack(CANPacker(DBC), parse("BrakeReqAck", BRAKE_REQ_ACK_ADDR, 50, stock), standby)
  assert (addr, bus) == (BRAKE_REQ_ACK_ADDR, 2)
  assert dat[5] == (stock[5] | 0x08 if standby else stock[5])  # 0xF7 -> 0xFF
  assert dat[0] == (stock[0] & ~0x80 if standby else stock[0])
  assert dat[1:5] == stock[1:5] and dat[6:] == stock[6:]


FSM0 = 0x51
# Camera FSM0 before and after an abort, route 816
FSM0_OK = bytes.fromhex("15e3860091955f7a")
FSM0_ABORT = bytes.fromhex("15e3821091955f7e")


def fsm0(stock: bytes, ecm_engaged: bool) -> bytes:
  return volvocan.create_eucd_fsm0(CANPacker(DBC), parse("FSM0", FSM0, 100, stock), ecm_engaged)[1]


@pytest.mark.parametrize("stock", [FSM0_OK, FSM0_ABORT])
def test_fsm0_relay_is_byte_identical(stock):
  assert fsm0(stock, True) == stock


def test_fsm0_never_enabled_without_ecm():
  out = fsm0(FSM0_OK, False)
  assert out[2] == FSM0_OK[2] & ~0x04
  assert out[:2] == FSM0_OK[:2] and out[3:] == FSM0_OK[3:]


ENGINE_DATA = 0x12A


# Stock 0x12A Byte_4: 0xA8 standby, 0x98 off, 0xB8 engaged
@pytest.mark.parametrize("b4,state", [(0xA8, EcmAccState.STANDBY), (0x98, EcmAccState.OFF), (0xB8, EcmAccState.ENGAGED)])
def test_ecm_acc_state_decoding(b4, state):
  dat = bytes([0xEA, 0x00, 0xC7, 0xF8, b4, 0xF8, 0xE5, 0x7A])
  assert int(parse("EngineData", ENGINE_DATA, 66, dat)["ACC_State_ECM"]) == state


def test_resume_button_copies_the_cem_frame():
  stock = bytes.fromhex("88108761a7ff0000")  # CEM's CCButtons, no button pressed (route 89e)
  addr, dat, bus = volvocan.create_eucd_resume_button(CANPacker(DBC), parse("CCButtons", 0x127, 50, stock))
  assert (addr, bus) == (0x127, 0)
  pressed = parse("CCButtons", 0x127, 50, dat)
  assert pressed["ACCResumeBtn"] == 1 and pressed["ACCResumeBtnInv"] == 0
  # only the Resume bits differ from the CEM's frame: b7 bit5 set, its inverted copy b5 bit5 cleared
  expected = bytearray(stock)
  expected[5] &= ~0x20
  expected[7] |= 0x20
  assert dat == bytes(expected)


@pytest.mark.parametrize("standby", [False, True])
def test_camera_buttons_engaging_released_in_standby(standby):
  # Set+ to raise the ECM's speed would engage the camera's ACC while it is shown standby
  packer = CANPacker(DBC)
  pressed = {"ACCSetBtn": 1, "ACCSetBtnInv": 0, "ACCMinusBtn": 1, "ACCMinusBtnInv": 0, "ACCResumeBtn": 1, "ACCResumeBtnInv": 0,
             "ACCOnOffBtn": 0, "ACCOnOffBtnInv": 1, "TimeGapIncreaseBtn": 1, "TimeGapIncreaseBtnInv": 0, "LKA_State": 1}
  stock = packer.make_can_msg("CCButtons", 0, pressed)[1]
  out = parse("CCButtons", 0x127, 66, volvocan.create_eucd_cam_button(packer, parse("CCButtons", 0x127, 66, stock), standby)[1])
  for btn in ("ACCSetBtn", "ACCMinusBtn", "ACCResumeBtn"):
    assert out[btn] == (0 if standby else 1) and out[btn + "Inv"] == (1 if standby else 0)
  assert out["TimeGapIncreaseBtn"] == 1 and out["LKA_State"] == 1 and out["ACCOnOffBtnInv"] == 1  # the rest as the CEM's
