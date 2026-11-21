import copy

from opendbc.can.parser import CANParser
from opendbc.car import Bus, ButtonType, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.dashboard_speed_limit import Tracker as LimitTracker, parser_expiry, volvo_sign
from opendbc.car.interfaces import CarStateBase
from opendbc.car.volvo.eucd_params import CANBUS, CarControllerEUCDParams, EcmAccState
from opendbc.car.volvo.values import DBC

GearShifter = structs.CarState.GearShifter


def eucd_all_frames(cp: CANParser, name: str) -> list[dict[str, float]]:
  sigs = cp.vl_all[name]
  return [dict(zip(sigs.keys(), vals, strict=True)) for vals in zip(*sigs.values(), strict=True)]


class CarStateEUCD(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.eucd_cruise_enabled_prev = False
    self.eucd_eps_torque_timer = 0
    self.eucd_ecm_acc_engaged = False
    self.eucd_pscm_stock_values = {}
    self._eucd_cruise_speed_prev_kph = 0
    self._eucd_pending_delta = 0
    self._eucd_custom_acc_step = 1
    self.eucd_gap_button_states = {"TimeGapIncreaseBtn": False, "TimeGapDecreaseBtn": False}
    self.eucd_driver_onoff = False  # driver's ACC On/Off held (bit set, inverted copy cleared)
    self.eucd_driver_resume = False  # driver's Resume held (bit set, inverted copy cleared)
    # Set by the carcontroller: while OP owns the ECM, the ACC standstill is OP's
    self.eucd_op_owns_ecm = False
    self.eucd_op_standstill = False
    self.eucd_ecm_standstill_ready = False
    self.eucd_lka_on = False  # the driver's LKA on/off, always on lateral follows it
    self.eucd_gas_pressed = False
    self.eucd_lka_state_prev: bool | None = None
    self.dashboard_limit = LimitTracker()

  def update(self, can_parsers) -> structs.CarState:
    pt_cp = can_parsers[Bus.pt]
    cam_cp = can_parsers[Bus.cam]

    ret = structs.CarState()

    # car speed
    ret.vEgoRaw = pt_cp.vl["VehicleSpeed1"]["VehicleSpeed"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = ret.vEgoRaw < 0.1

    # Gas: 10% to press, under 9% to release, as the panda (route 00000007)
    self.eucd_gas_pressed = pt_cp.vl["AccPedal"]["AccPedal"] >= (9 if self.eucd_gas_pressed else 10)
    ret.gasPressed = self.eucd_gas_pressed

    # brake pedal
    ret.brakePressed = pt_cp.vl["Brake_Info"]["BrakePedal"] == 2

    # steering
    ret.steeringAngleDeg = pt_cp.vl["PSCM1"]["SteeringAngleServo"]
    ret.steeringRateDeg = pt_cp.vl["SAS0"]["SteeringRateOfChange"]
    eucd_steering_direction = pt_cp.vl["SAS0"]["SteeringDirection"]
    ret.steeringTorque = pt_cp.vl["PSCM1"]["EPSTorque"]
    if eucd_steering_direction:
      ret.steeringTorque = -abs(ret.steeringTorque)
    ret.steeringTorqueEps = pt_cp.vl["PSCM1"]["LKATorque"]
    ret.steeringPressed = abs(ret.steeringTorque) > 50

    # cruise state
    set_speed = pt_cp.vl["ACC_Speed"]["ACC_Speed"] * CV.KPH_TO_MS
    ret.cruiseState.speed = set_speed
    ret.cruiseState.speedCluster = ret.cruiseState.speed
    ret.cruiseState.available = bool(cam_cp.vl["FSM0"]["ACC_Available"])
    # ECM is the ACC master
    self.eucd_ecm_acc_engaged = int(pt_cp.vl["EngineData"]["ACC_State_ECM"]) == EcmAccState.ENGAGED
    ret.cruiseState.enabled = self.eucd_ecm_acc_engaged
    # Camera standstill hold, gated by the car's standstill; OP's own while it owns the ECM
    if self.eucd_op_owns_ecm:
      ret.cruiseState.standstill = self.eucd_op_standstill and ret.standstill
    else:
      ret.cruiseState.standstill = bool(cam_cp.vl["FSM3"]["ACC_Standstill"]) and ret.standstill
    # The car holds the stop (BrakeReqAck b0 bit7, route 807)
    self.eucd_car_hold = bool(pt_cp.vl["BrakeReqAck"]["StandstillHold"])
    # The ECM takes a resume only once 0x1FF b0 goes 70 -> D0, ~1.5 s after ACC_Standstill (routes 899, 89b)
    self.eucd_ecm_standstill_ready = int(pt_cp.vl["BrakeHold"]["Byte_0"]) == 0xD0
    ret.cruiseState.nonAdaptive = False
    ret.accFaulted = False
    self.eucd_acc_distance = cam_cp.vl["FSM1"]["ACC_Distance"]

    # Servo timeout while ACC is active
    if ret.cruiseState.enabled and ret.vEgo > CarControllerEUCDParams.EUCD_MIN_STEER_SPEED:
      if not self.eucd_cruise_enabled_prev:
        self.eucd_eps_torque_timer = 0

      if ret.steeringTorqueEps == 0:
        self.eucd_eps_torque_timer += 1
      else:
        self.eucd_eps_torque_timer = 0

      ret.steerFaultTemporary = self.eucd_eps_torque_timer >= CarControllerEUCDParams.EUCD_STEER_TIMEOUT
    else:
      ret.steerFaultTemporary = False

    self.eucd_cruise_enabled_prev = ret.cruiseState.enabled

    # Gear lever position
    ret.gearShifter = {
      0: GearShifter.park,
      1: GearShifter.reverse,
      2: GearShifter.neutral,
      3: GearShifter.drive,
    }[int(pt_cp.vl["GearSelector"]["GearLever"])]

    # safety
    ret.stockFcw = False
    ret.stockAeb = False

    # button presses
    ret.leftBlinker = pt_cp.vl["MiscCarInfo"]["TurnSignal"] == 1
    ret.rightBlinker = pt_cp.vl["MiscCarInfo"]["TurnSignal"] == 3

    # One accelCruise/decelCruise per frame from the pending delta
    cruise_kph_now = round(set_speed * CV.MS_TO_KPH)
    # Stopped, Set+/- only sets the speed: no accel/decel press, or the planner reads it as "go" (route 8b2, 572 s/591 s)
    if ret.standstill:
      self._eucd_pending_delta = 0
    elif ret.cruiseState.enabled and self.eucd_cruise_enabled_prev:
      self._eucd_pending_delta += cruise_kph_now - self._eucd_cruise_speed_prev_kph
    self._eucd_cruise_speed_prev_kph = cruise_kph_now

    # One event per ACC step
    step = max(1, self._eucd_custom_acc_step)
    button_events = []
    if self._eucd_pending_delta >= 1:
      button_events.append(structs.CarState.ButtonEvent(pressed=False, type=ButtonType.accelCruise))
      self._eucd_pending_delta = max(0, self._eucd_pending_delta - step)
    elif self._eucd_pending_delta <= -1:
      button_events.append(structs.CarState.ButtonEvent(pressed=False, type=ButtonType.decelCruise))
      self._eucd_pending_delta = min(0, self._eucd_pending_delta + step)

    # Gap buttons cycle the personality (bit set, inverted copy cleared)
    for signal in self.eucd_gap_button_states:
      pressed = bool(pt_cp.vl["CCButtons"][signal]) and not pt_cp.vl["CCButtons"][signal + "Inv"]
      button_events.extend(create_button_events(pressed, self.eucd_gap_button_states[signal], {True: ButtonType.gapAdjustCruise}))
      self.eucd_gap_button_states[signal] = pressed
    # Driver's On/Off as cancel, so OP disengages before the panda drops permission (route 22, 418.4 s)
    driver_onoff = bool(pt_cp.vl["CCButtons"]["ACCOnOffBtn"]) and not pt_cp.vl["CCButtons"]["ACCOnOffBtnInv"]
    button_events.extend(create_button_events(driver_onoff, self.eucd_driver_onoff, {True: ButtonType.cancel}))
    self.eucd_driver_onoff = driver_onoff
    driver_resume = bool(pt_cp.vl["CCButtons"]["ACCResumeBtn"]) and not pt_cp.vl["CCButtons"]["ACCResumeBtnInv"]
    button_events.extend(create_button_events(driver_resume, self.eucd_driver_resume, {True: ButtonType.resumeCruise}))
    self.eucd_driver_resume = driver_resume
    # LKA button: no own bit, each press flips LKA_State (route 8a8)
    lka_state = bool(pt_cp.vl["CCButtons"]["LKA_State"])
    if self.eucd_lka_state_prev is not None and lka_state != self.eucd_lka_state_prev:
      button_events += [structs.CarState.ButtonEvent(pressed=True, type=ButtonType.lkas),
                        structs.CarState.ButtonEvent(pressed=False, type=ButtonType.lkas)]
    self.eucd_lka_state_prev = lka_state
    self.eucd_lka_on = lka_state
    ret.buttonEvents = button_events

    # lock info
    ret.doorOpen = not all([pt_cp.vl["Doors"]["DriverDoorClosed"], pt_cp.vl["Doors"]["PassengerDoorClosed"]])
    ret.seatbeltUnlatched = False

    self.eucd_pscm_stock_values = pt_cp.vl["PSCM1"]
    self.eucd_stock_FSM1 = copy.copy(cam_cp.vl["FSM1"])
    self.eucd_stock_FSM3 = copy.copy(cam_cp.vl["FSM3"])
    # Frames carcontroller relays 1:1
    self.eucd_stock_FSM0_frames = eucd_all_frames(cam_cp, "FSM0")
    self.eucd_stock_FSM4_frames = eucd_all_frames(cam_cp, "FSM4")
    self.eucd_stock_WheelRear_frames = eucd_all_frames(pt_cp, "WheelRear")
    self.eucd_stock_BrakeReqAck_frames = eucd_all_frames(pt_cp, "BrakeReqAck")
    self.eucd_stock_BrakeHold_frames = eucd_all_frames(pt_cp, "BrakeHold")
    self.eucd_stock_EngineData_frames = eucd_all_frames(pt_cp, "EngineData")
    self.eucd_stock_AccHold_frames = eucd_all_frames(pt_cp, "AccHold")
    self.eucd_stock_CCButtons_frames = eucd_all_frames(pt_cp, "CCButtons")

    # TSR speed limit, 0 = no sign
    timestamp, expiry = parser_expiry(cam_cp, "FSM5", "TSR_Speed")
    if timestamp > self.dashboard_limit.observation.observed_ns:
      status, value_mps = volvo_sign(cam_cp.vl["FSM5"]["TSR_Speed"])
      self.dashboard_limit.update(timestamp, status, value_mps, valid_until_ns=expiry)

    return ret

  @staticmethod
  def get_can_parsers(CP):
    pt_messages = [
      ("VehicleSpeed1", 50),
      ("AccPedal", 100),
      ("Brake_Info", 50),
      ("PSCM1", 50),
      ("ACC_Speed", 50),
      ("MiscCarInfo", 25),
      ("Doors", 20),
      ("SAS0", 100),
      ("EngineData", 66),
      ("CCButtons", 66),
      ("GearSelector", 66),
      ("WheelRear", 50),
      ("BrakeReqAck", 50),
      ("BrakeHold", 50),
      ("AccHold", 25),
    ]
    cam_messages = [
      ("FSM0", 100),
      ("FSM1", 50),
      ("FSM3", 50),
      ("FSM4", 33),
      ("FSM5", 10),
    ]
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], pt_messages, CANBUS.pt),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], cam_messages, CANBUS.cam),
    }
