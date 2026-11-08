import copy
from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase
from opendbc.car.tesla.preap.stock import PreAPStockCarState, get_stock_parsers
from opendbc.car.tesla.hw1 import HW1CarState, get_hw1_can_parsers
from opendbc.car.tesla.values import CAR, DBC, CANBUS, GEAR_MAP, STEER_THRESHOLD, STEER_DISENGAGE_THRESHOLD, TeslaFlags, TeslaSafetyFlags

class TeslaScreenCANParser(CANParser):
  def __init__(self):
    # Screen gestures are optional; their silence is not a control-bus fault.
    super().__init__('tesla_model3_vehicle', [('UI_status2', float('nan'))], CANBUS.vehicle)

  def update(self, strings, sendcan=False):
    if strings and not isinstance(strings[0], list | tuple):
      strings = [strings]
    return super().update([(stamp, [frame for frame in frames if frame[0] == 0x3DF and len(frame[1]) == 8])
                           for stamp, frames in strings], sendcan)


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    if CP.carFingerprint == CAR.TESLA_MODEL_S_PREAP:
      self.preap = PreAPStockCarState(CP)
      self.engagement = self.preap.engagement
      self.preap_lateral_authorized = False
      return
    if CP.carFingerprint == CAR.TESLA_MODEL_S_HW1:
      self.hw1 = HW1CarState(CP)
      self.hands_on_level = 0
      self.das_control = {}
      return
    self.cooperative_steering = CP.carFingerprint == CAR.TESLA_MODEL_3 and bool(CP.safetyConfigs[0].safetyParam & TeslaSafetyFlags.COOP_STEERING)
    self.can_define = CANDefine(DBC[CP.carFingerprint][Bus.party])
    self.shifter_values = self.can_define.dv["DI_systemStatus"]["DI_gear"]

    self.active_touch_points = None
    self.autopark = False
    self.autopark_prev = False
    self.cruise_enabled_prev = False

    self.hands_on_level = 0
    self.das_control = None

  def update_screen_button(self, parser):
    events = []
    for count in parser.vl_all['UI_status2']['UI_activeTouchPoints']:
      count = int(count)
      if self.active_touch_points is not None:
        events.extend(create_button_events(count, self.active_touch_points, {3: structs.CarState.ButtonEvent.Type.lkas}))
      self.active_touch_points = count
    return events

  def update_autopark_state(self, autopark_state: str, cruise_enabled: bool):
    autopark_now = autopark_state in ("ACTIVE", "COMPLETE", "SELFPARK_STARTED")
    if autopark_now and not self.autopark_prev and not self.cruise_enabled_prev:
      self.autopark = True
    if not autopark_now:
      self.autopark = False
    self.autopark_prev = autopark_now
    self.cruise_enabled_prev = cruise_enabled

  def update(self, can_parsers) -> structs.CarState:
    if self.CP.carFingerprint == CAR.TESLA_MODEL_S_PREAP:
      ret = self.preap.update(can_parsers)
      self.hands_on_level = self.preap.hands_on_level
      self.di_cruise_state = self.preap.di_cruise_state
      return ret
    if self.CP.carFingerprint == CAR.TESLA_MODEL_S_HW1:
      ret = self.hw1.update(can_parsers)
      self.hands_on_level = self.hw1.hands_on_level
      self.das_control = self.hw1.das_control
      return ret
    cp_party = can_parsers[Bus.party]
    cp_ap_party = can_parsers[Bus.ap_party]
    ret = structs.CarState()

    # Vehicle speed
    ret.vEgoRaw = cp_party.vl["DI_speed"]["DI_vehicleSpeed"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)

    # Gas pedal
    ret.gasPressed = cp_party.vl["DI_systemStatus"]["DI_accelPedalPos"] > 0

    # Brake pedal
    ret.brakePressed = cp_party.vl["ESP_status"]["ESP_driverBrakeApply"] == 2

    # Steering wheel
    epas_status = cp_party.vl["EPAS3S_sysStatus"]
    self.hands_on_level = epas_status["EPAS3S_handsOnLevel"]
    ret.steeringAngleDeg = -epas_status["EPAS3S_internalSAS"]
    ret.steeringRateDeg = -cp_ap_party.vl["SCCM_steeringAngleSensor"]["SCCM_steeringAngleSpeed"]
    ret.steeringTorque = -epas_status["EPAS3S_torsionBarTorque"]

    # stock handsOnLevel uses >0.5 for 0.25s, but is too slow
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > STEER_THRESHOLD, 5)

    eac_status = self.can_define.dv["EPAS3S_sysStatus"]["EPAS3S_eacStatus"].get(int(epas_status["EPAS3S_eacStatus"]), None)
    ret.steerFaultPermanent = eac_status == "EAC_FAULT"
    ret.steerFaultTemporary = eac_status == "EAC_INHIBITED"

    # FSD disengages using union of handsOnLevel (slow overrides) and high angle rate faults (fast overrides, high speed)
    eac_error_code = self.can_define.dv["EPAS3S_sysStatus"]["EPAS3S_eacErrorCode"].get(int(epas_status["EPAS3S_eacErrorCode"]), None)
    ret.steeringDisengage = self.hands_on_level >= 3 or (eac_status == "EAC_INHIBITED" and
                                                         eac_error_code == "EAC_ERROR_HIGH_ANGLE_RATE_SAFETY") or (
                                                           self.cooperative_steering and abs(ret.steeringTorque) > STEER_DISENGAGE_THRESHOLD)

    # Cruise state
    cruise_state = self.can_define.dv["DI_state"]["DI_cruiseState"].get(int(cp_party.vl["DI_state"]["DI_cruiseState"]), None)
    speed_units = self.can_define.dv["DI_state"]["DI_speedUnits"].get(int(cp_party.vl["DI_state"]["DI_speedUnits"]), None)

    autopark_state = self.can_define.dv["DI_state"]["DI_autoparkState"].get(int(cp_party.vl["DI_state"]["DI_autoparkState"]), None)
    cruise_enabled = cruise_state in ("ENABLED", "STANDSTILL", "OVERRIDE", "PRE_FAULT", "PRE_CANCEL")
    self.update_autopark_state(autopark_state, cruise_enabled)

    # Match panda safety cruise engaged logic
    ret.cruiseState.enabled = cruise_enabled and not self.autopark
    if speed_units == "KPH":
      ret.cruiseState.speed = max(cp_party.vl["DI_state"]["DI_digitalSpeed"] * CV.KPH_TO_MS, 1e-3)
    elif speed_units == "MPH":
      ret.cruiseState.speed = max(cp_party.vl["DI_state"]["DI_digitalSpeed"] * CV.MPH_TO_MS, 1e-3)
    ret.cruiseState.available = cruise_state == "STANDBY" or ret.cruiseState.enabled
    ret.cruiseState.standstill = False  # This needs to be false, since we can resume from stop without sending anything special
    ret.standstill = cp_party.vl["ESP_B"]["ESP_vehicleStandstillSts"] == 1
    ret.accFaulted = cruise_state == "FAULT"

    # Gear
    ret.gearShifter = GEAR_MAP[self.can_define.dv["DI_systemStatus"]["DI_gear"].get(int(cp_party.vl["DI_systemStatus"]["DI_gear"]), "DI_GEAR_INVALID")]

    # Doors
    ret.doorOpen = cp_party.vl["UI_warning"]["anyDoorOpen"] == 1

    # Blinkers
    ret.leftBlinker = cp_party.vl["UI_warning"]["leftBlinkerBlinking"] in (1, 2)
    ret.rightBlinker = cp_party.vl["UI_warning"]["rightBlinkerBlinking"] in (1, 2)

    # Seatbelt
    ret.seatbeltUnlatched = cp_party.vl["UI_warning"]["buckleStatus"] != 1

    # Blindspot
    ret.leftBlindspot = cp_ap_party.vl["DAS_status"]["DAS_blindSpotRearLeft"] != 0
    ret.rightBlindspot = cp_ap_party.vl["DAS_status"]["DAS_blindSpotRearRight"] != 0

    # AEB
    ret.stockAeb = cp_ap_party.vl["DAS_control"]["DAS_aebEvent"] == 1

    # LKAS
    ret.stockLkas = cp_ap_party.vl["DAS_steeringControl"]["DAS_steeringControlType"] == 2  # LANE_KEEP_ASSIST

    # Stock Autosteer should be off (includes FSD)
    # TODO: find for TESLA_MODEL_X and HW2.5 vehicles
    if not (self.CP.flags & TeslaFlags.MISSING_DAS_SETTINGS):
      ret.invalidLkasSetting = cp_ap_party.vl["DAS_settings"]["DAS_autosteerEnabled"] != 0

    if self.CP.flags & TeslaFlags.AOL_SCREEN_BUTTON:
      ret.buttonEvents = self.update_screen_button(can_parsers[Bus.adas])

    # Buttons # ToDo: add Gap adjust button

    # Messages needed by carcontroller
    self.das_control = copy.copy(cp_ap_party.vl["DAS_control"])

    return ret

  def update_button_enable(self, buttonEvents):
    if self.CP.carFingerprint == CAR.TESLA_MODEL_S_PREAP and self.CP.openpilotLongitudinalControl:
      if not self.preap.enableLongControl:
        return False
      if any(event.type == structs.CarState.ButtonEvent.Type.setCruise and not event.pressed for event in buttonEvents):
        return True
    return super().update_button_enable(buttonEvents)

  @staticmethod
  def get_can_parsers(CP):
    if CP.carFingerprint == CAR.TESLA_MODEL_S_PREAP:
      return get_stock_parsers(CP)
    if CP.carFingerprint == CAR.TESLA_MODEL_S_HW1:
      return get_hw1_can_parsers(CP)
    return {
      Bus.party: CANParser(DBC[CP.carFingerprint][Bus.party], [], CANBUS.party),
      Bus.ap_party: CANParser(DBC[CP.carFingerprint][Bus.party], [], CANBUS.autopilot_party),
      **({Bus.adas: TeslaScreenCANParser()} if CP.flags & TeslaFlags.AOL_SCREEN_BUTTON else {})
    }
