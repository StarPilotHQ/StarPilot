import copy
import math
from opendbc.can import CANDefine, CANParser, CANPacker
from opendbc.car import Bus, get_safety_config, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase, CarControllerBase
from opendbc.car.lateral import apply_steer_angle_limits_vm
from opendbc.car.tesla.values import DBC, GEAR_MAP, STEER_THRESHOLD, CarControllerParams, TeslaSafetyFlags
from opendbc.car.tesla.teslacan_legacy import ModelSHW1CAN
from opendbc.car.vehicle_model import VehicleModel


def get_hw1_params(ret, fingerprint, alpha_long):
  ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.tesla, TeslaSafetyFlags.HW1.value | int(alpha_long))]
  ret.steerLimitTimer = .4
  ret.steerActuatorDelay = .1
  ret.steerAtStandstill = True
  ret.steerControlType = structs.CarParams.SteerControlType.angle
  ret.radarUnavailable = fingerprint.get(1, {}).get(0x301) != 8
  ret.alphaLongitudinalAvailable = True
  ret.openpilotLongitudinalControl = alpha_long
  ret.pcmCruise = True
  return ret


def get_hw1_can_parsers(CP):
  dbc = DBC[CP.carFingerprint][Bus.party]
  return {
    Bus.party: CANParser(dbc, [], 0),
    Bus.ap_party: CANParser(dbc, [("DAS_steeringControl", 50), ("DAS_control", 25)], 2),
    Bus.pt: CANParser(dbc, [("DI_torque1", 100)], 0),
    Bus.chassis: CANParser(dbc, [("EPAS_sysStatus", 25), ("ESP_B", 50), ("BrakeMessage", 50),
      ("DI_state", 10), ("STW_ANGLHP_STAT", 50), ("DI_torque2", 100), ("GTW_carState", 10),
      ("SDM1", math.nan), ("RCM_status", math.nan)], 0),
  }


class HW1CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.can_defines = CANDefine(DBC[CP.carFingerprint][Bus.party]).dv
    self.hands_on_level = 0
    self.das_control = {}

  def update(self, can_parsers):
    cp_ap_party = can_parsers[Bus.ap_party]
    cp_pt = can_parsers[Bus.pt]
    cp_ap_pt = can_parsers[Bus.ap_party]
    cp_chassis = can_parsers[Bus.chassis]
    ret = structs.CarState()

    # Vehicle speed
    ret.vEgoRaw = cp_chassis.vl["ESP_B"]["ESP_vehicleSpeed"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)

    # Gas and brake
    ret.gasPressed = cp_pt.vl["DI_torque1"]["DI_pedalPos"] > 0
    ret.brakePressed = cp_chassis.vl["BrakeMessage"]["driverBrakeStatus"] != 1

    # Steering wheel and EPAS status
    epas_status = cp_chassis.vl["EPAS_sysStatus"]
    self.hands_on_level = epas_status["EPAS_handsOnLevel"]
    ret.steeringAngleDeg = -epas_status["EPAS_internalSAS"]
    ret.steeringRateDeg = -cp_chassis.vl["STW_ANGLHP_STAT"]["StW_AnglHP_Spd"]
    ret.steeringTorque = -epas_status["EPAS_torsionBarTorque"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > STEER_THRESHOLD, 5)

    eac_status = self.can_defines["EPAS_sysStatus"]["EPAS_eacStatus"].get(int(epas_status["EPAS_eacStatus"]), None)
    ret.steerFaultPermanent = eac_status == "EAC_FAULT"
    ret.steerFaultTemporary = eac_status == "EAC_INHIBITED"
    eac_error_code = self.can_defines["EPAS_sysStatus"]["EPAS_eacErrorCode"].get(int(epas_status["EPAS_eacErrorCode"]), None)
    ret.steeringDisengage = self.hands_on_level >= 3 or (
      eac_status == "EAC_INHIBITED" and eac_error_code == "EAC_ERROR_HIGH_ANGLE_RATE_SAFETY"
    )

    # Cruise
    cruise_state = self.can_defines["DI_state"]["DI_cruiseState"].get(int(cp_chassis.vl["DI_state"]["DI_cruiseState"]), None)
    speed_units = self.can_defines["DI_state"]["DI_speedUnits"].get(int(cp_chassis.vl["DI_state"]["DI_speedUnits"]), None)
    cruise_enabled = cruise_state in ("ENABLED", "STANDSTILL", "OVERRIDE", "PRE_FAULT", "PRE_CANCEL")
    ret.cruiseState.enabled = cruise_enabled
    if speed_units == "KPH":
      ret.cruiseState.speed = max(cp_chassis.vl["DI_state"]["DI_hw1CruiseSet"] * CV.KPH_TO_MS, 1e-3)
    elif speed_units == "MPH":
      ret.cruiseState.speed = max(cp_chassis.vl["DI_state"]["DI_hw1CruiseSet"] * CV.MPH_TO_MS, 1e-3)
    ret.cruiseState.available = cruise_state == "STANDBY" or ret.cruiseState.enabled
    ret.cruiseState.standstill = False
    ret.standstill = ret.vEgoRaw < 0.1
    ret.accFaulted = cruise_state == "FAULT"

    # Gear, body state, and safety state
    ret.gearShifter = GEAR_MAP[self.can_defines["DI_torque2"]["DI_gear"].get(
      int(cp_chassis.vl["DI_torque2"]["DI_gear"]), "DI_GEAR_INVALID")]

    doors = ("DOOR_STATE_FL", "DOOR_STATE_FR", "DOOR_STATE_RL", "DOOR_STATE_RR", "DOOR_STATE_FrontTrunk", "BOOT_STATE")
    ret.doorOpen = any(
      self.can_defines["GTW_carState"][door].get(int(cp_chassis.vl["GTW_carState"][door]), "OPEN") == "OPEN"
      for door in doors
    )
    ret.leftBlinker = cp_chassis.vl["GTW_carState"]["BC_indicatorLStatus"] == 1
    ret.rightBlinker = cp_chassis.vl["GTW_carState"]["BC_indicatorRStatus"] == 1

    _ = cp_chassis.vl["SDM1"]
    _ = cp_chassis.vl["RCM_status"]
    sd_time = cp_chassis.ts_nanos["SDM1"]["SDM_bcklDrivStatus"]
    rcm_time = cp_chassis.ts_nanos["RCM_status"]["RCM_buckleDriverStatus"]
    if sd_time and cp_chassis._last_update_nanos - sd_time <= 1_000_000_000:
      ret.seatbeltUnlatched = cp_chassis.vl["SDM1"]["SDM_bcklDrivStatus"] != 1
    elif rcm_time and cp_chassis._last_update_nanos - rcm_time <= 1_000_000_000:
      ret.seatbeltUnlatched = cp_chassis.vl["RCM_status"]["RCM_buckleDriverStatus"] != 1
    else:
      ret.seatbeltUnlatched = True

    ret.stockAeb = cp_ap_pt.vl["DAS_control"]["DAS_aebEvent"] == 1
    ret.stockLkas = cp_ap_party.vl["DAS_steeringControl"]["DAS_steeringControlType"] == 2
    self.das_control = copy.copy(cp_ap_pt.vl["DAS_control"])
    return ret


class HW1CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.codec = ModelSHW1CAN(CANPacker(dbc_names[Bus.party]))
    self.apply_angle_last = 0.
    self.VM = VehicleModel(CP)

  def update(self, CC, CS, now_nanos):
    sends = []
    lat_active = CC.latActive and CS.hands_on_level < 3
    if self.frame % 2 == 0:
      self.apply_angle_last = apply_steer_angle_limits_vm(CC.actuators.steeringAngleDeg, self.apply_angle_last,
        CS.out.vEgoRaw, CS.out.steeringAngleDeg, lat_active, CarControllerParams, self.VM)
      sends.append(self.codec.create_steering_control(self.frame // 2, self.apply_angle_last, lat_active))
    if self.CP.openpilotLongitudinalControl and self.frame % 4 == 0:
      sends.append(self.codec.create_longitudinal_command(13 if CC.cruiseControl.cancel else 4,
        CC.actuators.accel, self.frame // 4, CS.out.vEgo, CC.longActive, CS.out.gasPressed))
    elif not self.CP.openpilotLongitudinalControl and CC.cruiseControl.cancel:
      sends.append(self.codec.create_longitudinal_command(13, 0, CS.das_control["DAS_controlCounter"] + 1,
        CS.out.vEgo, False, CS.out.gasPressed))
    actuators = CC.actuators.as_builder()
    actuators.steeringAngleDeg = self.apply_angle_last
    self.frame += 1
    return actuators, sends
