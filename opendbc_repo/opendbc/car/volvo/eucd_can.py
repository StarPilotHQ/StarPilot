import numpy as np

from opendbc.car.volvo.eucd_params import CANBUS, CarControllerEUCDParams, EcmAccState, LongMode


def create_eucd_button_msg(packer, resume=False, cancel=False, set_plus=False, minus=False, bus=0):
  msg = {
    "ACCOnOffBtn": cancel,
    "ACCOnOffBtnInv": not cancel,
    "ACCResumeBtn": resume,
    "ACCResumeBtnInv": not resume,
    "ACCSetBtn": set_plus,
    "ACCSetBtnInv": not set_plus,
    "ACCMinusBtn": minus,
    "ACCMinusBtnInv": not minus,
  }
  return packer.make_can_msg("CCButtons", bus, msg)


def create_eucd_lkas_state_msg(packer, steering_angle: float, stock_values: dict):
  # Servo state to the camera, LKA torque/active cleared
  msg = {
    "LKATorque": 0,
    "SteeringAngleServo": steering_angle,
    "byte0": stock_values["byte0"],
    "byte4": stock_values["byte4"],
    "byte7": stock_values["byte7"],
    "LKAActive": int(stock_values["LKAActive"]) & 0xF5,
    "EPSTorque": stock_values["EPSTorque"],
  }
  return packer.make_can_msg("PSCM1", 2, msg)


def calculate_eucd_lka_checksum(dat: bytes) -> int:
  steer_angle_request = ((dat[3] & 0x3F) << 8) + dat[4]
  steering_direction_request = dat[5] & 0x03
  trqlim = dat[2]

  s = (trqlim + steering_direction_request + steer_angle_request + (steer_angle_request >> 8)) & 0xFF
  return s ^ 0xFF


def create_eucd_lka_msg(packer, apply_steer: float, steer_direction: int):
  values = {
    "LKAAngleReq": apply_steer,
    "LKASteerDirection": steer_direction,
    "TrqLim": 0,

    "SET_X_22": 0x25,
    "SET_X_02": 0,
    "SET_X_10": 0x10,
    "SET_X_A4": 0xa7,
  }

  # Checksum
  dat = packer.make_can_msg("FSM2", 0, values)[1]
  values["Checksum"] = calculate_eucd_lka_checksum(dat)

  return packer.make_can_msg("FSM2", 0, values)


def eucd_brake_thresholds(v_ego: float | None) -> tuple[float, float]:
  """BRAKE enter and exit accel at this speed; None is the highway's."""
  CCP = CarControllerEUCDParams
  if v_ego is None:
    return CCP.EUCD_BRAKE_ENTER, CCP.EUCD_BRAKE_EXIT
  enter = float(np.interp(v_ego, CCP.EUCD_BRAKE_ENTER_BP, CCP.EUCD_BRAKE_ENTER_V))
  return enter, min(enter + CCP.EUCD_BRAKE_HYSTERESIS, 0.)


def eucd_next_long_mode(mode: LongMode, accel: float, brake_offset: float = 0., v_ego: float | None = None) -> LongMode:
  # brake_offset shifts the BRAKE thresholds by the slope
  CCP = CarControllerEUCDParams
  brake_enter, brake_exit = eucd_brake_thresholds(v_ego)
  if mode == LongMode.BRAKE:
    if accel <= brake_exit + brake_offset:
      return LongMode.BRAKE
  elif mode == LongMode.DRIVE:
    if accel >= CCP.EUCD_DRIVE_EXIT:
      return LongMode.DRIVE
  enter_offset = brake_offset * (1. + CCP.EUCD_UPHILL_BRAKE_ENTER_EXTRA) if brake_offset < 0. else brake_offset
  if accel < brake_enter + enter_offset:
    return LongMode.BRAKE
  if accel > CCP.EUCD_DRIVE_ENTER:
    return LongMode.DRIVE
  return LongMode.COAST


def eucd_fsm3_traction(accel: float, mode: LongMode) -> int:
  # FSM3 ACC_TractionRequest
  if mode == LongMode.BRAKE:
    return 0
  if mode == LongMode.DRIVE:
    gain, offset = CarControllerEUCDParams.EUCD_TRACTION_DRIVE
    return min(max(round(gain * accel + offset), 0), 255)
  gain, offset = CarControllerEUCDParams.EUCD_TRACTION_COAST
  return min(max(round(gain * accel + offset), -127), -1) & 0xFF


def _eucd_copy(stock, skip=()):
  # Stock frame signals, without the ones overlapping others
  return {k: v for k, v in stock.items() if k not in skip}


def create_eucd_longitudinal(packer, stock_fsm3, accel, acc_check, mode: LongMode | None = None, standstill: bool | None = None):
  # Camera FSM3 with OP's accel, ACC_Check, mode and traction; standstill set = OP's stop to 0
  values = _eucd_copy(stock_fsm3)
  values |= {
    "ACC_AccelerationRequest": accel,
    "ACC_Check": acc_check,
  }
  if standstill is not None:
    values["ACC_Standstill"] = int(standstill)
    values["ACC_NoTargetCancel"] = 0
  if mode is not None:
    values["ACC_Mode"] = int(mode)
    values["ACC_TractionRequest"] = eucd_fsm3_traction(accel, mode)
  return packer.make_can_msg("FSM3", 0, values)


def eucd_fsm4_brake_value(accel: float, brake: bool) -> int:
  # FSM4 brake word: 0x8000 | brake flag 0x4000 | 14-bit target
  CCP = CarControllerEUCDParams
  if brake:
    lo, hi = CCP.EUCD_FSM4_BRAKE_ACCEL_RANGE
    return 0xC000 | int(round(CCP.EUCD_FSM4_BRAKE_ZERO + CCP.EUCD_FSM4_BRAKE_GAIN * min(max(accel, lo), hi)))
  return 0x8000 | int(round(float(np.interp(accel, CCP.EUCD_FSM4_NO_BRAKE_ACCEL_BP, CCP.EUCD_FSM4_NO_BRAKE_VALUE_V))))


def create_eucd_fsm4(packer, stock_fsm4, accel: float, mode: LongMode | None = None):
  # Camera FSM4; brake flag and target follow OP's accel when either side brakes
  values = _eucd_copy(stock_fsm4)
  op_brake = mode == LongMode.BRAKE
  cam_brake = bool(stock_fsm4["ACC_BrakeRequest"])
  if mode is not None and (op_brake or cam_brake):
    value = eucd_fsm4_brake_value(accel, op_brake)
    values["Byte_5_b7"] = (value >> 15) & 1
    values["ACC_BrakeRequest"] = (value >> 14) & 1
    values["ACC_BrakeTarget"] = value & 0x3FFF
  return packer.make_can_msg("FSM4", 0, values)


def create_eucd_fsm0(packer, stock_fsm0, ecm_engaged: bool):
  # Camera FSM0, never ACC enabled without the ECM engaged
  values = _eucd_copy(stock_fsm0, skip=("ACC_BrakeAlert",))
  if not ecm_engaged:
    values["ACC_Enabled"] = 0
  return packer.make_can_msg("FSM0", 0, values)


def create_eucd_cam_wheel_status(packer, stock_wheel_rear, standby: bool):
  # WheelRear to the camera; standby: no ACC brake request, applied brake nor hold (seen in standby it latches FC, routes 886/888)
  values = _eucd_copy(stock_wheel_rear, skip=("WhlSpdRR", "WhlSpdLR"))
  if standby:
    values |= {"BrakeReqActive": 0, "ACC_BrakeApplied": 0, "StandstillHold": 0}
  return packer.make_can_msg("WheelRear", CANBUS.cam, values)


def create_eucd_cam_brake_request_ack(packer, stock_brake_req_ack, standby: bool):
  # BrakeReqAck to the camera; standby: no brake ack (active low) nor hold
  values = _eucd_copy(stock_brake_req_ack)
  if standby:
    values |= {"BrakeReqNotAck": 1, "StandstillHold": 0}
  return packer.make_can_msg("BrakeReqAck", CANBUS.cam, values)


def create_eucd_cam_button(packer, stock_ccbuttons, standby: bool):
  # CEM's CCButtons to the camera; standby: Set, Minus and Resume shown released, or the camera would engage its ACC
  values = _eucd_copy(stock_ccbuttons)
  if standby:
    values |= {"ACCSetBtn": 0, "ACCSetBtnInv": 1, "ACCMinusBtn": 0, "ACCMinusBtnInv": 1, "ACCResumeBtn": 0, "ACCResumeBtnInv": 1}
  return packer.make_can_msg("CCButtons", CANBUS.cam, values)


def create_eucd_resume_button(packer, stock_ccbuttons):
  # CEM's CCButtons to the ECM with Resume pressed, the other bytes as the CEM's
  values = _eucd_copy(stock_ccbuttons)
  values |= {"ACCResumeBtn": 1, "ACCResumeBtnInv": 0}
  return packer.make_can_msg("CCButtons", CANBUS.pt, values)


def create_eucd_cam_engine_data(packer, stock_engine_data, show_standby: bool):
  # EngineData to the camera; show_standby: ECM engaged shown as standby (driver cancelled), as in route 881
  values = _eucd_copy(stock_engine_data)
  if show_standby and int(values["ACC_State_ECM"]) == EcmAccState.ENGAGED:
    values["ACC_State_ECM"] = EcmAccState.STANDBY
    values["Byte_2"] = int(values["Byte_2"]) & ~0x20  # E7 -> C7
  return packer.make_can_msg("EngineData", CANBUS.cam, values)


def create_eucd_cam_acc_hold(packer, stock_acc_hold, show_standby: bool):
  # 0x325 to the camera; show_standby: no ACC engaged (C4) nor ACC standstill hold (87), as standby (84)
  values = _eucd_copy(stock_acc_hold)
  if show_standby:
    values |= {"ACC_Engaged": 0, "ACC_StandstillHold": 0}
  return packer.make_can_msg("AccHold", CANBUS.cam, values)


def create_eucd_cam_brake_hold(packer, stock_brake_hold, frozen_b0: int | None):
  # 0x1FF to the camera; b0 held while stopped (its 0x70 -> 0xD0 fires the camera's brake alert)
  values = _eucd_copy(stock_brake_hold)
  if frozen_b0 is not None:
    values["Byte_0"] = frozen_b0
  return packer.make_can_msg("BrakeHold", CANBUS.cam, values)


def create_eucd_radar(packer, stock_fsm1, mode: LongMode | None = None):
  # Camera FSM1; ACC_Braking follows OP braking
  values = _eucd_copy(stock_fsm1)
  if mode is not None:
    values["ACC_Braking"] = 1 if mode == LongMode.BRAKE else 0
  return packer.make_can_msg("FSM1", 0, values)
