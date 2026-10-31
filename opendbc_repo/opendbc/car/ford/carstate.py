from opendbc.car.gps import CarGpsTracker, get_car_gps_config
import math

from opendbc.can import CANDefine, CANParser
from opendbc.can.dbc import DBC as CANDBC
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.ford.fordcan import CanBus
from opendbc.car.ford.stock_cruise import FordStockCruiseButton
from opendbc.car.ford.values import CAR, DBC, CarControllerParams, FordFlags
from opendbc.car.interfaces import CarStateBase
from opendbc.car.dashboard_speed_limit import Tracker as LimitTracker, ford_sign, parser_expiry

ButtonType = structs.CarState.ButtonEvent.Type
GearShifter = structs.CarState.GearShifter
TransmissionType = structs.CarParams.TransmissionType


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.car_gps_tracker = CarGpsTracker(CP)
    self.car_gps_supported = self.car_gps_tracker.config is not None
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])
    if CP.transmissionType == TransmissionType.automatic:
      if CP.carFingerprint == CAR.FORD_MONDEO_MK5:
        self.shifter_values = can_define.dv["Gear_Shift_by_Wire_FD1"]["TrnRng_D_RqGsm"]
      elif CP.flags & FordFlags.ALT_STEER_ANGLE:
        self.shifter_values = can_define.dv["TransGearData"]["GearLvrPos_D_Actl"]
      else:
        self.shifter_values = can_define.dv["PowertrainData_10"]["TrnRng_D_Rq"]

    self.distance_button = 0
    self.dashboard_limit = LimitTracker()
    self.lc_button = 0
    self.cancel_button = False
    self.cancel_resume_button = FordStockCruiseButton()
    self.cruise_buttons = {ButtonType.accelCruise: False, ButtonType.decelCruise: False}
    self.steering_angle_offset_deg = 0.0
    self.lkas_available = False
    self.lkas_available_ts_nanos = 0
    self.lateral_motion_control = None

  def get_car_gps(self):
    return self.car_gps_tracker.get()

  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]

    ret = structs.CarState()

    # Occasionally on startup, the ABS module recalibrates the steering pinion offset, so we need to block engagement
    # The vehicle usually recovers out of this state within a minute of normal driving
    if self.CP.flags & FordFlags.ALT_STEER_ANGLE:
      park_aid = cp.vl["ParkAid_Data"]
      ret.vehicleSensorsInvalid = (cp.ts_nanos["ParkAid_Data"]["ExtSteeringAngleReq2"] == 0 or
                                   cp.ts_nanos["SteeringPinion_Data_Alt"]["StePinRelInit_An_Sns"] == 0 or
                                   int((park_aid["ExtSteeringAngleReq2"] + 1000) * 10) in (32766, 32767) or
                                   park_aid["EPASExtAngleStatReq"] != 0 or park_aid["ApaSys_D_Stat"] not in (0, 1))
    else:
      ret.vehicleSensorsInvalid = cp.vl["SteeringPinion_Data"]["StePinCompAnEst_D_Qf"] != 3

    # car speed
    ret.vEgoRaw = cp.vl["BrakeSysFeatures"]["Veh_V_ActlBrk"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.yawRate = cp.vl["Yaw_Data_FD1"]["VehYaw_W_Actl"]
    ret.standstill = cp.vl["DesiredTorqBrk"]["VehStop_D_Stat"] == 1

    # gas pedal
    ret.gasPressed = cp.vl["EngVehicleSpThrottle"]["ApedPos_Pc_ActlArb"] / 100. > 1e-6

    # brake pedal
    ret.brakePressed = cp.vl["EngBrakeData"]["BpedDrvAppl_D_Actl"] == 2
    ret.parkingBrake = cp.vl["DesiredTorqBrk"]["PrkBrkStatus"] in (1, 2)

    # steering wheel
    if self.CP.flags & FordFlags.ALT_STEER_ANGLE:
      initial_angle = cp.vl["SteeringPinion_Data_Alt"]["StePinRelInit_An_Sns"]
      if not ret.vehicleSensorsInvalid:
        self.steering_angle_offset_deg = cp.vl["ParkAid_Data"]["ExtSteeringAngleReq2"] - initial_angle
      ret.steeringAngleDeg = initial_angle + self.steering_angle_offset_deg
    else:
      ret.steeringAngleDeg = cp.vl["SteeringPinion_Data"]["StePinComp_An_Est"]
    ret.steeringTorque = cp.vl["EPAS_INFO"]["SteeringColumnTorque"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > CarControllerParams.STEER_DRIVER_ALLOWANCE, 5)
    ret.steerFaultTemporary = cp.vl["EPAS_INFO"]["EPAS_Failure"] == 1
    ret.steerFaultPermanent = cp.vl["EPAS_INFO"]["EPAS_Failure"] in (2, 3)
    ret.espDisabled = cp.vl["Cluster_Info1_FD1"]["DrvSlipCtlMde_D_Rq"] != 0  # 0 is default mode

    if self.CP.flags & FordFlags.CANFD:
      # this signal is always 0 on non-CAN FD cars
      ret.steerFaultTemporary |= cp.vl["Lane_Assist_Data3_FD1"]["LatCtlSte_D_Stat"] not in (1, 2, 3)
    if self.CP.flags & FordFlags.LKA_STEERING:
      self.lkas_available_ts_nanos = cp.ts_nanos["Lane_Assist_Data3_FD1"]["LaActAvail_D_Actl"]
      self.lkas_available = (self.lkas_available_ts_nanos > 0 and
                             cp.vl["Lane_Assist_Data3_FD1"]["LaActAvail_D_Actl"] == 3 and
                             cp.vl["Lane_Assist_Data3_FD1"]["LaActDeny_B_Actl"] == 0)
      self.lateral_motion_control = cp_cam.vl["LateralMotionControl"]
      ret.steerFaultTemporary |= not self.lkas_available

    # cruise state
    is_metric = cp.vl["INSTRUMENT_PANEL"]["METRIC_UNITS"] == 1 if not self.CP.flags & FordFlags.CANFD else False
    ret.cruiseState.speed = cp.vl["EngBrakeData"]["Veh_V_DsplyCcSet"] * (CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS)
    ret.cruiseState.enabled = cp.vl["EngBrakeData"]["CcStat_D_Actl"] in (4, 5)
    ret.cruiseState.available = cp.vl["EngBrakeData"]["CcStat_D_Actl"] in (3, 4, 5)
    ret.cruiseState.nonAdaptive = cp.vl["Cluster_Info1_FD1"]["AccEnbl_B_RqDrv"] == 0
    ret.cruiseState.standstill = cp.vl["EngBrakeData"]["AccStopMde_D_Rq"] == 3
    ret.accFaulted = cp.vl["EngBrakeData"]["CcStat_D_Actl"] in (1, 2)
    if not self.CP.openpilotLongitudinalControl:
      ret.accFaulted = ret.accFaulted or cp_cam.vl["ACCDATA"]["CmbbDeny_B_Actl"] == 1

    # gear
    if self.CP.transmissionType == TransmissionType.automatic:
      if self.CP.carFingerprint == CAR.FORD_MONDEO_MK5:
        gear = self.shifter_values.get(cp.vl["Gear_Shift_by_Wire_FD1"]["TrnRng_D_RqGsm"])
      elif self.CP.flags & FordFlags.ALT_STEER_ANGLE:
        gear = self.shifter_values.get(cp.vl["TransGearData"]["GearLvrPos_D_Actl"])
      else:
        gear = self.shifter_values.get(cp.vl["PowertrainData_10"]["TrnRng_D_Rq"])
      if self.CP.carFingerprint in (CAR.FORD_EDGE_MK2, CAR.FORD_MONDEO_MK5) and gear == "SPORT_DRIVESPORT":
        gear = "SPORT"
      ret.gearShifter = self.parse_gear_shifter(gear)
    elif self.CP.transmissionType == TransmissionType.manual:
      if bool(cp.vl["BCM_Lamp_Stat_FD1"]["RvrseLghtOn_B_Stat"]):
        ret.gearShifter = GearShifter.reverse
      else:
        ret.gearShifter = GearShifter.drive

    # safety
    ret.stockFcw = bool(cp_cam.vl["ACCDATA_3"]["FcwVisblWarn_B_Rq"])
    ret.stockAeb = bool(cp_cam.vl["ACCDATA_2"]["CmbbBrkDecel_B_Rq"])

    # button presses
    ret.leftBlinker = cp.vl["Steering_Data_FD1"]["TurnLghtSwtch_D_Stat"] == 1
    ret.rightBlinker = cp.vl["Steering_Data_FD1"]["TurnLghtSwtch_D_Stat"] == 2
    # TODO: block this going to the camera otherwise it will enable stock TJA
    ret.genericToggle = bool(cp.vl["Steering_Data_FD1"]["TjaButtnOnOffPress"])
    prev_distance_button = self.distance_button
    prev_lc_button = self.lc_button
    self.distance_button = cp.vl["Steering_Data_FD1"]["AccButtnGapTogglePress"]
    self.lc_button = bool(cp.vl["Steering_Data_FD1"]["TjaButtnOnOffPress"])
    prev_cancel_button = self.cancel_button
    cancel, _ = self.cancel_resume_button.update(
      bool(cp.vl["Steering_Data_FD1"]["CcAslButtnCnclResPress"]),
      ret.cruiseState.available, ret.cruiseState.enabled)
    self.cancel_button = bool(cp.vl["Steering_Data_FD1"]["CcAslButtnCnclPress"]) or cancel

    # lock info
    ret.doorOpen = any([cp.vl["BodyInfo_3_FD1"]["DrStatDrv_B_Actl"], cp.vl["BodyInfo_3_FD1"]["DrStatPsngr_B_Actl"],
                        cp.vl["BodyInfo_3_FD1"]["DrStatRl_B_Actl"], cp.vl["BodyInfo_3_FD1"]["DrStatRr_B_Actl"]])
    ret.seatbeltUnlatched = cp.vl["RCMStatusMessage2_FD1"]["FirstRowBuckleDriver"] == 2

    # blindspot sensors
    if self.CP.flags & FordFlags.HAS_BSM:
      cp_bsm = cp_cam if self.CP.flags & FordFlags.CANFD else cp
      ret.leftBlindspot = cp_bsm.vl["Side_Detect_L_Stat"]["SodDetctLeft_D_Stat"] != 0
      ret.rightBlindspot = cp_bsm.vl["Side_Detect_R_Stat"]["SodDetctRight_D_Stat"] != 0

    # Stock steering buttons so that we can passthru blinkers etc.
    self.buttons_stock_values = cp.vl["Steering_Data_FD1"]
    # Stock values from IPMA so that we can retain some stock functionality
    self.acc_tja_status_stock_values = cp_cam.vl["ACCDATA_3"]
    self.lkas_status_stock_values = cp_cam.vl["IPMA_Data"]

    button_events = [
      *create_button_events(self.distance_button, prev_distance_button, {1: ButtonType.gapAdjustCruise}),
      *create_button_events(self.lc_button, prev_lc_button, {1: ButtonType.lkas}),
    ]

    if self.CP.openpilotLongitudinalControl:
      button_events += create_button_events(self.cancel_button, prev_cancel_button, {1: ButtonType.cancel})
      for button, signals in (
        (ButtonType.accelCruise, ('CcAslButtnSetIncPress', 'CcAslButtnResIncPress', 'CcAslButtnIndxIncPress')),
        (ButtonType.decelCruise, ('CcAslButtnSetDecPress', 'CcAslButtnResDecPress', 'CcAslButtnIndxDecPress')),
      ):
        pressed = any(self.buttons_stock_values[signal] for signal in signals)
        button_events += create_button_events(pressed, self.cruise_buttons[button], {1: button})
        self.cruise_buttons[button] = pressed

    ret.buttonEvents = button_events

    timestamp, expiry = parser_expiry(cp_cam, "Traffic_RecognitnData", "TsrVLim1MsgTxt_D_Rq")
    if timestamp > self.dashboard_limit.observation.observed_ns:
      speed = int(cp_cam.vl["Traffic_RecognitnData"]["TsrVLim1MsgTxt_D_Rq"])
      unit = int(cp_cam.vl["Traffic_RecognitnData"]["TsrVlUnitMsgTxt_D_Rq"])
      status, value_mps = ford_sign(speed, unit)
      self.dashboard_limit.update(timestamp, status, value_mps, valid_until_ns=expiry)

    self.car_gps_tracker.update(cp)
    return ret

  @staticmethod
  def get_can_parsers(CP):
    dbc_name = DBC[CP.carFingerprint][Bus.pt]
    cam_messages = [("Traffic_RecognitnData", math.nan)] if "Traffic_RecognitnData" in CANDBC(dbc_name).name_to_msg else []
    pt_messages = []
    if CP.flags & FordFlags.NEW_PORT:
      # Subscribe before the first update so the new ports cannot miss their first source frame.
      pt_messages = [
        ("BrakeSysFeatures", 50), ("Yaw_Data_FD1", 100), ("DesiredTorqBrk", 50),
        ("EngVehicleSpThrottle", 100), ("EngBrakeData", 10), ("EPAS_INFO", 50),
        ("Cluster_Info1_FD1", 10), ("Steering_Data_FD1", 10),
        ("BodyInfo_3_FD1", 2), ("RCMStatusMessage2_FD1", 10),
      ]
      if CP.flags & FordFlags.ALT_STEER_ANGLE:
        pt_messages += [("ParkAid_Data", 50), ("SteeringPinion_Data_Alt", 100)]
      else:
        pt_messages.append(("SteeringPinion_Data", 100))
      if CP.flags & FordFlags.CANFD:
        pt_messages.append(("Lane_Assist_Data3_FD1", 30))
      else:
        pt_messages.append(("INSTRUMENT_PANEL", 1))
      if CP.transmissionType == TransmissionType.automatic:
        gear = ("Gear_Shift_by_Wire_FD1" if CP.flags & FordFlags.CANFD else
                "TransGearData" if CP.flags & FordFlags.ALT_STEER_ANGLE else "PowertrainData_10")
        pt_messages.append((gear, 10))
    if CP.transmissionType == TransmissionType.manual:
      pt_messages.append(("BCM_Lamp_Stat_FD1", 10))
    if CP.flags & FordFlags.LKA_STEERING:
      pt_messages.append(("Lane_Assist_Data3_FD1", 30))
      cam_messages.append(("LateralMotionControl", 20))
    gps = get_car_gps_config(CP)
    if gps is not None:
      pt_messages += [(name, float("nan")) for name in gps.messages]

    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], pt_messages, CanBus(CP).main),
      Bus.cam: CANParser(dbc_name, cam_messages, CanBus(CP).camera),
    }
