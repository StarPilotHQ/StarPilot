from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.chrysler.aol_contract import qualified as aol_qualified
from opendbc.car.chrysler.values import CAR, CUSW_CARS, DBC, STEER_THRESHOLD, RAM_CARS, ChryslerFlags
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase

ButtonType = structs.CarState.ButtonEvent.Type


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.CP = CP
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])

    self.auto_high_beam = 0
    self.button_counter = 0
    self.lkas_car_model = -1

    if CP.carFingerprint in RAM_CARS:
      self.shifter_values = can_define.dv["Transmission_Status"]["Gear_State"]
    else:
      self.shifter_values = can_define.dv["GEAR"]["PRNDL"]

    self.distance_button = 0
    self.aol_pause_longitudinal = False
    self.aol_pause_longitudinal_stamp_ns = 0
    self.aol_buttons = {
      ButtonType.mainCruise: 0,
      ButtonType.cancel: 0,
      ButtonType.accelCruise: 0,
      ButtonType.decelCruise: 0,
      ButtonType.resumeCruise: 0,
    }
    self.brake_hold = False
    self.das_3 = {}
    self.das_3_timestamp = 0
    self.jeep_driver_cancel = False

  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]

    if self.CP.carFingerprint in CUSW_CARS:
      return self.update_cusw(cp, cp_cam)

    ret = structs.CarState()

    buttons = cp.vl["CRUISE_BUTTONS"]
    prev_distance_button = self.distance_button
    # WK2 2019-21 only: Distance+ joins Distance− for gapAdjustCruise. Other Chrysler unchanged.
    if self.CP.carFingerprint == CAR.JEEP_GRAND_CHEROKEE_2019:
      self.distance_button = int(bool(buttons["ACC_Distance_Dec"] or buttons["ACC_Distance_Inc"]))
    else:
      self.distance_button = buttons["ACC_Distance_Dec"]

    # lock info
    ret.doorOpen = any([cp.vl["BCM_1"]["DOOR_OPEN_FL"],
                        cp.vl["BCM_1"]["DOOR_OPEN_FR"],
                        cp.vl["BCM_1"]["DOOR_OPEN_RL"],
                        cp.vl["BCM_1"]["DOOR_OPEN_RR"]])
    ret.seatbeltUnlatched = cp.vl["ORC_1"]["SEATBELT_DRIVER_UNLATCHED"] == 1

    # brake pedal
    ret.brakePressed = cp.vl["ESP_1"]['Brake_Pedal_State'] == 1  # Physical brake pedal switch

    # gas pedal
    ret.gasPressed = cp.vl["ECM_5"]["Accelerator_Position"] > 1e-5

    # car speed
    if self.CP.carFingerprint in RAM_CARS:
      ret.vEgoRaw = cp.vl["ESP_8"]["Vehicle_Speed"] * CV.KPH_TO_MS
      ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(cp.vl["Transmission_Status"]["Gear_State"], None))
    else:
      ret.vEgoRaw = (cp.vl["SPEED_1"]["SPEED_LEFT"] + cp.vl["SPEED_1"]["SPEED_RIGHT"]) / 2.
      ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(cp.vl["GEAR"]["PRNDL"], None))
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = not ret.vEgoRaw > 0.001

    # button presses
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_stalk(200, cp.vl["STEERING_LEVERS"]["TURN_SIGNALS"] == 1,
                                                                       cp.vl["STEERING_LEVERS"]["TURN_SIGNALS"] == 2)
    ret.genericToggle = cp.vl["STEERING_LEVERS"]["HIGH_BEAM_PRESSED"] == 1

    # steering wheel
    ret.steeringAngleDeg = cp.vl["STEERING"]["STEERING_ANGLE"] + cp.vl["STEERING"]["STEERING_ANGLE_HP"]
    ret.steeringRateDeg = cp.vl["STEERING"]["STEERING_RATE"]
    ret.steeringTorque = cp.vl["EPS_2"]["COLUMN_TORQUE"]
    ret.steeringTorqueEps = cp.vl["EPS_2"]["EPS_TORQUE_MOTOR"]
    ret.steeringPressed = abs(ret.steeringTorque) > STEER_THRESHOLD

    # cruise state
    cp_cruise = cp_cam if self.CP.carFingerprint in RAM_CARS else cp

    ret.cruiseState.available = cp_cruise.vl["DAS_3"]["ACC_AVAILABLE"] == 1
    ret.cruiseState.enabled = cp_cruise.vl["DAS_3"]["ACC_ACTIVE"] == 1
    ret.cruiseState.speed = cp_cruise.vl["DAS_4"]["ACC_SET_SPEED_KPH"] * CV.KPH_TO_MS
    ret.cruiseState.nonAdaptive = cp_cruise.vl["DAS_4"]["ACC_STATE"] in (1, 2)  # 1 NormalCCOn and 2 NormalCCSet
    ret.cruiseState.standstill = cp_cruise.vl["DAS_3"]["ACC_STANDSTILL"] == 1
    ret.accFaulted = cp_cruise.vl["DAS_3"]["ACC_FAULTED"] != 0

    # Jeep brake-hold (both GC gens) and/or WK2-2019 alpha-long need a fresh DAS_3 source.
    wk2_2019_long = (self.CP.carFingerprint == CAR.JEEP_GRAND_CHEROKEE_2019 and
                     self.CP.openpilotLongitudinalControl)
    if self.CP.flags & ChryslerFlags.JEEP_BRAKE_HOLD_AVAILABLE or wk2_2019_long:
      self.das_3 = dict(cp_cruise.vl["DAS_3"])
      self.jeep_driver_cancel = bool(buttons["ACC_Cancel"])
      self.das_3_timestamp = cp_cruise.ts_nanos["DAS_3"]["COUNTER"]
      if self.CP.flags & ChryslerFlags.JEEP_BRAKE_HOLD_AVAILABLE and not wk2_2019_long:
        ret.brakeHoldActive = self.brake_hold

    # WK2 2019 alpha-long only: software cruise from stalk; stock ACC_ACTIVE is not the owner.
    if wk2_2019_long:
      ret.cruiseState.available = (cp_cruise.vl["DAS_3"]["ACC_AVAILABLE"] == 1 or
                                   cp_cruise.vl["DAS_4"]["ACC_STATE"] in (3, 4) or
                                   bool(buttons["Cruise_OnOff"] or buttons["ACC_OnOff"]))
      ret.cruiseState.nonAdaptive = False
      ret.cruiseState.enabled = False

    if self.CP.carFingerprint in RAM_CARS:
      # Auto High Beam isn't Located in this message on chrysler or jeep currently located in 729 message
      self.auto_high_beam = cp_cam.vl["DAS_6"]['AUTO_HIGH_BEAM_ON']
      ret.steerFaultTemporary = cp.vl["EPS_3"]["DASM_FAULT"] == 1
    else:
      ret.steerFaultTemporary = cp.vl["EPS_2"]["LKAS_TEMPORARY_FAULT"] == 1
      ret.steerFaultPermanent = cp.vl["EPS_2"]["LKAS_STATE"] == 4

    # blindspot sensors
    if self.CP.flags & ChryslerFlags.HAS_BSM:
      ret.leftBlindspot = cp.vl["BSM_1"]["LEFT_STATUS"] == 1
      ret.rightBlindspot = cp.vl["BSM_1"]["RIGHT_STATUS"] == 1

    self.lkas_car_model = cp_cam.vl["DAS_6"]["CAR_MODEL"]
    self.button_counter = buttons["COUNTER"]

    button_events = create_button_events(self.distance_button, prev_distance_button, {1: ButtonType.gapAdjustCruise})
    if wk2_2019_long:
      # WK2 2019 only: map OEM ACC stalk to software-cruise button events.
      long_buttons = {
        ButtonType.accelCruise: bool(buttons["ACC_Accel"]),
        ButtonType.decelCruise: bool(buttons["ACC_Decel"]),
        ButtonType.resumeCruise: bool(buttons["ACC_Resume"]),
        ButtonType.cancel: bool(buttons["ACC_Cancel"]),
        ButtonType.mainCruise: bool(buttons["Cruise_OnOff"] or buttons["ACC_OnOff"]),
      }
      for button, value in long_buttons.items():
        prev = self.aol_buttons.get(button, 0)
        button_events += create_button_events(value, prev, {1: button})
        self.aol_buttons[button] = value
    elif aol_qualified(self.CP, marked_only=True):
      values = {ButtonType.mainCruise: bool(buttons["Cruise_OnOff"] or buttons["ACC_OnOff"]),
                ButtonType.cancel: bool(buttons["ACC_Cancel"])}
      for button, value in values.items():
        button_events += create_button_events(value, self.aol_buttons[button], {1: button})
        self.aol_buttons[button] = value
    ret.buttonEvents = button_events

    return ret

  def update_cusw(self, cp, cp_cam):
    ret = structs.CarState()

    ret.doorOpen = any([cp.vl["DOORS"]["DOOR_OPEN_FL"],
                        cp.vl["DOORS"]["DOOR_OPEN_FR"],
                        cp.vl["DOORS"]["DOOR_OPEN_RL"],
                        cp.vl["DOORS"]["DOOR_OPEN_RR"]])
    ret.seatbeltUnlatched = bool(cp.vl["SEATBELT_STATUS"]["SEATBELT_DRIVER_UNLATCHED"])

    ret.brakePressed = bool(cp.vl["BRAKE_3"]["DRIVER_BRAKE_SWITCH"])
    ret.gasPressed = cp.vl["ACCEL_GAS"]["GAS_HUMAN"] > 0

    ret.espDisabled = bool(cp.vl["TRACTION_BUTTON"]["TRACTION_OFF"])

    ret.vEgoRaw = cp.vl["BRAKE_1"]["VEHICLE_SPEED"]
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = not ret.vEgoRaw > 0.001
    self.parse_wheel_speeds(ret,
      cp.vl["WHEEL_SPEEDS_FRONT"]["WHEEL_SPEED_FL"],
      cp.vl["WHEEL_SPEEDS_REAR"]["WHEEL_SPEED_RR"],
      cp.vl["WHEEL_SPEEDS_REAR"]["WHEEL_SPEED_RL"],
      cp.vl["WHEEL_SPEEDS_FRONT"]["WHEEL_SPEED_FR"],
      unit=1,
    )

    ret.leftBlinker = cp.vl["STEERING_LEVERS"]["TURN_SIGNALS"] == 1
    ret.rightBlinker = cp.vl["STEERING_LEVERS"]["TURN_SIGNALS"] == 2
    ret.steeringAngleDeg = cp.vl["STEERING"]["STEER_ANGLE"]
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(cp.vl["GEAR"]["PRNDL"], None))

    ret.cruiseState.speed = cp.vl["ACC_HUD"]["ACC_SET_SPEED_KMH"] * CV.KPH_TO_MS
    ret.cruiseState.available = bool(cp.vl["ACC_CONTROL"]["ACC_MAIN_ON"])
    ret.cruiseState.enabled = bool(cp.vl["ACC_CONTROL"]["ACC_ACTIVE"])

    ret.steeringTorque = cp.vl["EPS_STATUS"]["TORQUE_DRIVER"]
    ret.steeringTorqueEps = cp.vl["EPS_STATUS"]["TORQUE_MOTOR"]
    ret.steeringPressed = abs(ret.steeringTorque) > STEER_THRESHOLD
    ret.steerFaultPermanent = bool(cp.vl["EPS_STATUS"]["LKAS_FAULT"])

    if self.CP.flags & ChryslerFlags.HAS_BSM:
      ret.leftBlindspot = bool(cp.vl["BSM_LEFT"]["LEFT_DETECTED"])
      ret.rightBlindspot = bool(cp.vl["BSM_RIGHT"]["RIGHT_DETECTED"])

    self.lkas_car_model = cp_cam.vl["DAS_6"]["CAR_MODEL"]

    return ret

  @staticmethod
  def get_can_parsers(CP):
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 2),
    }
