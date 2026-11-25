from opendbc.car import Bus, ButtonType, create_button_events, structs
from opendbc.can.parser import CANParser
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase
from opendbc.car.volvo.helpers import pscm_status_supported
from opendbc.car.volvo.values import CAR, DBC, VolvoC1PlatformConfig

GearShifter = structs.CarState.GearShifter
TransmissionType = structs.CarParams.TransmissionType

SPEED_TO_MS = 0.003977 # Must match VOLVO_SPEED_TO_MS in opendbc/safety/modes/volvo_cma.h
STEERING_PRESSED_THRESHOLD = 2


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.is_c1 = isinstance(CAR(CP.carFingerprint).config, VolvoC1PlatformConfig)
    self.gas_pressed_prev = False
    self.dispatch_lca_2_msg = False
    self.msg_pscm = {}
    self.msg_lca = {}
    self.msg_lca_2 = {}
    self.msg_lca_3 = {}
    self.pilot_assist_engaged = False
    self.msg_lca_5 = {}
    self.msg_pscm_related = {}
    self.pscm_related_raw = b''
    self.msg_lca_4 = {}
    self.c1_msg_pscm = {}
    self.c1_lka_torque = 0
    self.c1_button_states = {
      "ACCOnOffBtn": False,
      "ACCStopBtn": False,
      "ACCSetBtn": False,
      "ACCResumeBtn": False,
      "ACCMinusBtn": False,
      "TimeGapIncreaseBtn": False,
      "TimeGapDecreaseBtn": False,
    }

  def update(self, can_parsers) -> structs.CarState:
    if self.is_c1:
      return self._update_c1(can_parsers)
    return self._update_spa(can_parsers)

  def _update_spa(self, can_parsers):
    cp_main = can_parsers[Bus.main]
    cp_pt = can_parsers[Bus.pt]
    cp_party = can_parsers[Bus.party]

    ret = structs.CarState()
    ret.vEgoRaw = cp_main.vl["SPEED"]["SPEED"] * SPEED_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = ret.vEgoRaw <= 0.1 # 0.1 m/s
    ret.gasPressed = cp_pt.vl["ECM_1"]["ACCELERATOR_PEDAL_POS"] > 1.0  # percent
    ret.brakePressed = cp_main.vl["LCA_2"]["BRAKE_PEDAL_PRESSED_B"] == 1
    ret.parkingBrake = False # TODO: add parking brake
    ret.espActive = cp_main.vl["LCA_2"]["ESC_ACTUATING"] == 1 and cp_main.vl["LCA_2"]["ESC_ELIGIBLE"] == 1
    ret.steeringAngleDeg = cp_party.vl['PSCM']['PSCM_ANGLE_SENSOR']
    ret.steeringTorque = -cp_party.vl['DRIVER_INPUT']['STEERING_DRIVER_INPUT']  # Car right turn is negative, openpilot right turn is positive
    ret.steeringPressed = abs(ret.steeringTorque) > STEERING_PRESSED_THRESHOLD
    self.msg_pscm_related = cp_party.vl['PSCM_RELATED']
    ret.steerFaultTemporary = not pscm_status_supported(self.msg_pscm_related, self.pscm_related_raw)
    self.eps_active = True  # Assume EPS is active for now

    cruise_raw = cp_pt.vl["BUS1_CRUISE_CONTROL"]["CRUISE_CONTROL_SPA_ENABLED"] == 1 # inverted (0 = cruise on, 1 = cruise off)
    ret.cruiseState.enabled = cruise_raw

    ret.cruiseState.available = True  # TODO: Determine actual availability

    self.gas_pressed_prev = ret.gasPressed
    ret.cruiseState.speed = 0  # TODO: Find cruise set speed
    ret.cruiseState.nonAdaptive = False
    ret.cruiseState.standstill = ret.standstill # TODO: Find cruise control standstill signal

    gearPosition = cp_main.vl['GEAR_POSITION']['GEAR_POSITION']
    if gearPosition == 0:
      ret.gearShifter = GearShifter.park
    elif gearPosition == 1:
      ret.gearShifter = GearShifter.reverse
    elif gearPosition == 2:
      ret.gearShifter = GearShifter.neutral
    elif gearPosition == 3:
      ret.gearShifter = GearShifter.drive
    elif gearPosition == 4: # B mode
      ret.gearShifter = GearShifter.drive

    ret.leftBlinker = False # TODO FlexRay
    ret.rightBlinker = False # TODO FlexRay

    ret.doorOpen = False # TODO: add door open
    ret.seatbeltUnlatched = False # TODO: add seatbelt unlatched

    # Store entire message dictionaries
    self.msg_pscm = cp_party.vl['PSCM']
    self.msg_lca = cp_main.vl['LCA']
    self.msg_lca_2 = cp_main.vl['LCA_2']
    self.msg_lca_3 = cp_main.vl['LCA_3']
    self.msg_lca_4 = cp_main.vl['LCA_4']
    self.msg_lca_5 = cp_main.vl['LCA_5']

    self.pilot_assist_engaged = cp_main.vl['LCA_2']['PILOT_ASSIST_ENGAGED'] == 1

    return ret

  def _update_c1(self, can_parsers):
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]
    ret = structs.CarState()

    ret.vEgoRaw = cp.vl["VehicleSpeed1"]["VehicleSpeed"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = ret.vEgoRaw < 0.1

    ret.steeringAngleDeg = cp.vl["PSCM1"]["SteeringAngleServo"]
    ret.steeringTorque = cp.vl["PSCM1"]["LKATorque"]
    ret.steeringPressed = False

    ret.gasPressed = cp.vl["PedalandBrake"]["AccPedal"] > 5.0
    ret.brakePressed = bool(cp.vl["PedalandBrake"]["BrakePedalActive2"] or
                            cp.vl["PedalandBrake"]["BrakePedalActive"])

    ret.gearShifter = {
      0: GearShifter.park,
      1: GearShifter.reverse,
      2: GearShifter.neutral,
      3: GearShifter.drive,
    }.get(int(cp.vl["TCM0"]["GearShifter"]), GearShifter.unknown)

    ret.cruiseState.available = bool(cp_cam.vl["FSM0"]["ACCStatusOnOff"])
    ret.cruiseState.enabled = bool(cp_cam.vl["FSM0"]["ACCStatusActive"])
    ret.cruiseState.speed = cp.vl["ACC"]["SpeedTargetACC"] * CV.KPH_TO_MS
    ret.cruiseState.nonAdaptive = False
    ret.cruiseState.standstill = ret.standstill

    turn_signal = int(cp.vl["MiscCarInfo"]["TurnSignal"])
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_stalk(
      50, turn_signal == 1, turn_signal == 3)
    ret.doorOpen = False
    ret.seatbeltUnlatched = False

    button_types = {
      "ACCOnOffBtn": ButtonType.mainCruise,
      "ACCStopBtn": ButtonType.cancel,
      "ACCSetBtn": ButtonType.setCruise,
      "ACCResumeBtn": ButtonType.resumeCruise,
      "ACCMinusBtn": ButtonType.decelCruise,
      "TimeGapIncreaseBtn": ButtonType.gapAdjustCruise,
      "TimeGapDecreaseBtn": ButtonType.gapAdjustCruise,
    }
    button_events = []
    for signal, button_type in button_types.items():
      pressed = bool(cp.vl["CCButtons"][signal])
      button_events.extend(create_button_events(pressed, self.c1_button_states[signal], {True: button_type}))
      self.c1_button_states[signal] = pressed
    ret.buttonEvents = button_events

    self.c1_msg_pscm = cp.vl["PSCM1"]
    self.c1_lka_torque = int(cp.vl["PSCM1"]["LKATorque"])

    return ret

  @staticmethod
  def get_can_parsers(CP):
    if isinstance(CAR(CP.carFingerprint).config, VolvoC1PlatformConfig):
      return {
        Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [
          ("VehicleSpeed1", 50),
          ("CCButtons", 100),
          ("PSCM1", 50),
          ("PedalandBrake", 100),
          ("TCM0", 10),
          ("ACC", 17),
          ("MiscCarInfo", 25),
        ], 0),
        Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.cam], [
          ("FSM0", 100),
          ("FSM1", 50),
        ], 2),
      }

    return {
      Bus.main: CANParser(DBC[CP.carFingerprint][Bus.main], [
        ("SPEED", 50), ("GEAR_POSITION", 40), ("LCA", 100), ("LCA_2", 50),
        ("LCA_3", 67), ("LCA_4", 29), ("LCA_5", 50),
      ], 0),
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [
        ("ECM_1", 100), ("BUS1_CRUISE_CONTROL", 5),
      ], 1),
      Bus.party: CANParser(DBC[CP.carFingerprint][Bus.party], [
        ("PSCM", 100), ("DRIVER_INPUT", 100), ("PSCM_RELATED", 100),
      ], 2),
    }
