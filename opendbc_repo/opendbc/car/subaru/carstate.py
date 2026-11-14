import copy
from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, structs, create_button_events
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase
from opendbc.car.subaru.values import CAR, DBC, CanBus, SubaruFlags
from opendbc.car import CanSignalRateCalculator


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    from opendbc.car.subaru.startup import selected, StartupSources
    self.startup_sources = StartupSources(CP) if selected(CP) else None
    from opendbc.car.subaru.avh import selected as avh_selected, AvhSources
    self.avh_sources = AvhSources() if avh_selected(CP) else None
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])
    self.shifter_values = can_define.dv["Transmission"]["Gear"]

    self.sng_sources = {}
    self.sng_source_valid = {}
    self.sng_lead_source_ns = 0
    self.sng_lead_present = False
    self.sng_lead_ready = False
    self.close_distance = 0.0
    self.software_buttons = dict.fromkeys(("Main", "Set", "Resume"), 0)
    self.software_button_source_ns = 0
    self.software_button_counter = None
    self.software_button_consumed_ns = 0
    self.software_buttons_msg = {}
    self.es_lkas_alert_dat = b""
    self.angle_rate_calulator = CanSignalRateCalculator(50)
    from opendbc.car.subaru.high_angle_guard import HighAngleGuard, selected
    self.high_angle_guard = HighAngleGuard() if selected(CP) else None
    self.high_angle_cut = False

  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]
    cp_alt = can_parsers[Bus.alt]
    cp_angle = can_parsers[Bus.main] if self.CP.flags & SubaruFlags.D_PLATFORM else cp
    ret = structs.CarState()

    throttle_msg = cp.vl["Throttle"] if not (self.CP.flags & SubaruFlags.HYBRID) else cp_alt.vl["Throttle_Hybrid"]
    ret.gasPressed = throttle_msg["Throttle_Pedal"] > 1e-5
    if self.CP.flags & SubaruFlags.PREGLOBAL:
      ret.brakePressed = cp.vl["Brake_Pedal"]["Brake_Pedal"] > 0
    else:
      cp_brakes = cp_alt if self.CP.flags & SubaruFlags.GLOBAL_GEN2 else cp
      ret.brakePressed = cp_brakes.vl["Brake_Status"]["Brake"] == 1

    cp_es_distance = cp_alt if self.CP.flags & (SubaruFlags.GLOBAL_GEN2 | SubaruFlags.HYBRID) else cp_cam
    if not (self.CP.flags & SubaruFlags.HYBRID):
      eyesight_fault = bool(cp_es_distance.vl["ES_Distance"]["Cruise_Fault"])

      # if openpilot is controlling long, an eyesight fault is a non-critical fault. otherwise it's an ACC fault
      if self.CP.openpilotLongitudinalControl:
        ret.carFaultedNonCritical = eyesight_fault
      else:
        ret.accFaulted = eyesight_fault

    cp_wheels = cp_alt if self.CP.flags & SubaruFlags.GLOBAL_GEN2 else cp
    self.parse_wheel_speeds(ret,
      cp_wheels.vl["Wheel_Speeds"]["FL"],
      cp_wheels.vl["Wheel_Speeds"]["FR"],
      cp_wheels.vl["Wheel_Speeds"]["RL"],
      cp_wheels.vl["Wheel_Speeds"]["RR"],
    )
    ret.standstill = ret.vEgoRaw == 0

    # continuous blinker signals for assisted lane change
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(50, cp.vl["Dashlights"]["LEFT_BLINKER"],
                                                                      cp.vl["Dashlights"]["RIGHT_BLINKER"])

    if self.CP.flags & SubaruFlags.HAS_BSM:
      ret.leftBlindspot = (cp_angle.vl["BSD_RCTA"]["L_ADJACENT"] == 1) or (cp_angle.vl["BSD_RCTA"]["L_APPROACHING"] == 1)
      ret.rightBlindspot = (cp_angle.vl["BSD_RCTA"]["R_ADJACENT"] == 1) or (cp_angle.vl["BSD_RCTA"]["R_APPROACHING"] == 1)

    cp_transmission = cp_alt if self.CP.flags & SubaruFlags.HYBRID else cp
    can_gear = int(cp_transmission.vl["Transmission"]["Gear"])
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(can_gear, None))

    if not (self.CP.flags & SubaruFlags.LKAS_ANGLE):
      ret.steeringAngleDeg = cp_angle.vl["Steering_Torque"]["Steering_Angle"]
      steering_updated = len(cp_angle.vl_all["Steering_Torque"]["Steering_Angle"]) > 0
    else:
      # Steering_Torque->Steering_Angle exists on SUBARU_FORESTER_2022, SUBARU_OUTBACK_2023, SUBARU_ASCENT_2023 where
      # it is identical to Steering_2's signal. However, it is always zero on newer LKAS_ANGLE cars
      # such as 2024+ Crosstrek, 2023+ Ascent, etc. Use a universal signal for LKAS_ANGLE cars.
      ret.steeringAngleDeg = cp_angle.vl["Steering_2"]["Steering_Angle"]
      steering_updated = len(cp_angle.vl_all["Steering_2"]["Steering_Angle"]) > 0

    if not (self.CP.flags & SubaruFlags.PREGLOBAL):
      # ideally we get this from the car, but unclear if it exists. diagnostic software doesn't even have it
      ret.steeringRateDeg = self.angle_rate_calulator.update(ret.steeringAngleDeg, steering_updated)

    ret.steeringTorque = cp_angle.vl["Steering_Torque"]["Steer_Torque_Sensor"]
    ret.steeringTorqueEps = cp_angle.vl["Steering_Torque"]["Steer_Torque_Output"]

    steer_threshold = 75 if self.CP.flags & SubaruFlags.PREGLOBAL else 80
    ret.steeringPressed = abs(ret.steeringTorque) > steer_threshold

    cp_cruise = cp_alt if self.CP.flags & SubaruFlags.GLOBAL_GEN2 else cp
    cp_es_brake = cp_alt if self.CP.flags & SubaruFlags.GLOBAL_GEN2 else cp_cam

    if self.CP.carFingerprint in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_ASCENT_2023, CAR.SUBARU_FORESTER_2022, CAR.SUBARU_OUTBACK_2023):
      ret.cruiseState.enabled = cp_es_brake.vl["ES_Status"]["Cruise_Activated"] != 0
      ret.cruiseState.available = cp_cam.vl["ES_DashStatus"]["Cruise_On"] != 0
    elif self.CP.flags & (SubaruFlags.HYBRID | SubaruFlags.LKAS_ANGLE):
      # ES_DashStatus->Cruise_Activated_Dash is likely intended for the dash display only, as it falls
      # during user gas override and at standstill. ES_Status is missing on hybrid, so we use ES_Brake instead

      # TODO: ES_Brake->Cruise_Activated has been seen staying high when Crosstrek 2025 angle LKAS user pressed
      #  brake while engaged at a stop. ES_Status and ES_DashStatus->Signal7 correctly fell, but is either missing or
      #  always zero on hybrids. Probably need to split angle & hybrid. 0x27 and 0x225 on hybrids may work for them.
      ret.cruiseState.enabled = cp_es_brake.vl["ES_Brake"]['Cruise_Activated'] != 0
      ret.cruiseState.available = cp_cam.vl["ES_DashStatus"]['Cruise_On'] != 0
    else:
      ret.cruiseState.enabled = cp_cruise.vl["CruiseControl"]["Cruise_Activated"] != 0
      ret.cruiseState.available = cp_cruise.vl["CruiseControl"]["Cruise_On"] != 0
    ret.cruiseState.speed = cp_cam.vl["ES_DashStatus"]["Cruise_Set_Speed"] * CV.KPH_TO_MS

    if (self.CP.flags & SubaruFlags.PREGLOBAL and cp.vl["Dash_State2"]["UNITS"] == 1) or \
       (not (self.CP.flags & SubaruFlags.PREGLOBAL) and cp.vl["Dashlights"]["UNITS"] == 1):
      ret.cruiseState.speed *= CV.MPH_TO_KPH

    ret.seatbeltUnlatched = cp.vl["Dashlights"]["SEATBELT_FL"] == 1
    ret.doorOpen = any([cp.vl["BodyInfo"]["DOOR_OPEN_RR"],
                        cp.vl["BodyInfo"]["DOOR_OPEN_RL"],
                        cp.vl["BodyInfo"]["DOOR_OPEN_FR"],
                        cp.vl["BodyInfo"]["DOOR_OPEN_FL"]])
    ret.steerFaultPermanent = cp_angle.vl["Steering_Torque"]["Steer_Error_1"] == 1

    if self.CP.flags & SubaruFlags.PREGLOBAL:
      self.cruise_button = cp_cam.vl["ES_Distance"]["Cruise_Button"]
      self.ready = not cp_cam.vl["ES_DashStatus"]["Not_Ready_Startup"]
    else:
      if self.high_angle_guard is not None:
        self.high_angle_cut = self.high_angle_guard.update(
          cp.vl["Brake_Pressure_L_R"]["Steering_Angle"], cp.vl["Brake_Pedal"]["Speed"], ret.steeringTorque)
      ret.steerFaultTemporary = cp_angle.vl["Steering_Torque"]["Steer_Warning"] == 1 or self.high_angle_cut
      ret.cruiseState.nonAdaptive = cp_cam.vl["ES_DashStatus"]["Conventional_Cruise"] == 1
      ret.cruiseState.standstill = cp_cam.vl["ES_DashStatus"]["Cruise_State"] == 3
      ret.stockFcw = (cp_cam.vl["ES_LKAS_State"]["LKAS_Alert"] == 1) or \
                     (cp_cam.vl["ES_LKAS_State"]["LKAS_Alert"] == 2)

      self.es_lkas_state_msg = copy.copy(cp_cam.vl["ES_LKAS_State"])
      self.es_brake_msg = copy.copy(cp_es_brake.vl["ES_Brake"])

      # TODO: Hybrid cars don't have ES_Distance, need a replacement
      if not (self.CP.flags & SubaruFlags.HYBRID):
        # 8 is known AEB, there are a few other values related to AEB we ignore
        ret.stockAeb = (cp_es_distance.vl["ES_Brake"]["AEB_Status"] == 8) and \
                       (cp_es_distance.vl["ES_Brake"]["Brake_Pressure"] != 0)

        self.es_status_msg = copy.copy(cp_es_brake.vl["ES_Status"])
        self.cruise_control_msg = copy.copy(cp_cruise.vl["CruiseControl"])

    if not (self.CP.flags & SubaruFlags.HYBRID):
      self.es_distance_msg = copy.copy(cp_es_distance.vl["ES_Distance"])

    from opendbc.car.subaru.software_cruise import selected
    if selected(self.CP):
      buttons = cp.vl["Cruise_Buttons"]
      source_ns = int(cp.ts_nanos["Cruise_Buttons"]["COUNTER"])
      counter = int(buttons["COUNTER"])
      if source_ns > self.software_button_source_ns and counter != self.software_button_counter:
        self.software_button_source_ns = source_ns
        self.software_button_counter = counter
        self.software_buttons_msg = dict(buttons)
      button_events = []
      for name, kind in (("Main", structs.CarState.ButtonEvent.Type.mainCruise),
                         ("Set", structs.CarState.ButtonEvent.Type.decelCruise),
                         ("Resume", structs.CarState.ButtonEvent.Type.accelCruise)):
        pressed = int(bool(buttons[name]))
        button_events.extend(create_button_events(pressed, self.software_buttons[name], {1: kind}))
        self.software_buttons[name] = pressed
      ret.buttonEvents = button_events
      ret.cruiseState.speedCluster = ret.cruiseState.speed

    from opendbc.car.subaru.sng import selected as sng_selected
    if sng_selected(self.CP):
      cp.vl["Brake_Pedal"]
      lead = cp_es_distance.vl["ES_Distance"]
      self.close_distance = float(lead["Close_Distance"])
      self.sng_lead_present = bool(lead["Car_Follow"]) and not bool(lead["Cruise_Fault"])
      self.sng_lead_source_ns = int(cp_es_distance.ts_nanos["ES_Distance"]["COUNTER"])

    self.es_dashstatus_msg = copy.copy(cp_cam.vl["ES_DashStatus"])
    if self.CP.flags & SubaruFlags.ASCENT_LKAS_ALERT:
      alert = cp_cam.vl["ES_LKAS_Alert"]
      self.es_lkas_alert_dat = (bytes((int(alert["CHECKSUM"]), int(alert["COUNTER"]) | (int(alert["Signal1"]) << 4))) +
                                int(alert["Payload"]).to_bytes(6, "little"))
    if self.CP.flags & SubaruFlags.SEND_INFOTAINMENT:
      self.es_infotainment_msg = copy.copy(cp_cam.vl["ES_Infotainment"])

    return ret

  @staticmethod
  def get_can_parsers(CP):
    from opendbc.car.subaru.high_angle_guard import selected
    guard_checks = [("Brake_Pressure_L_R", 50), ("Brake_Pedal", 50)] if selected(CP) else []
    parsers = {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], guard_checks, CanBus.alt if CP.flags & SubaruFlags.D_PLATFORM else CanBus.main),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_Alert", 0)] if CP.flags & SubaruFlags.ASCENT_LKAS_ALERT else [], CanBus.camera),
      Bus.alt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], CanBus.alt)
    }
    if CP.flags & SubaruFlags.D_PLATFORM:
      parsers[Bus.main] = CANParser(DBC[CP.carFingerprint][Bus.pt], [], CanBus.main)
    return parsers
