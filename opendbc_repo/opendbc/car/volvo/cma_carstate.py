from opendbc.car import Bus, structs
from opendbc.can.parser import CANParser
from opendbc.car.interfaces import CarStateBase
from opendbc.car.volvo.values import CAR, DBC, VolvoSPAPlatformConfig

GearShifter = structs.CarState.GearShifter

# main-bus SPEED (0x60) is raw counts in the DBC; measured against GPS ground speed.
# Must match VOLVO_SPEED_TO_MS in opendbc/safety/modes/volvo.h.
SPEED_TO_MS = 0.003977
STEERING_PRESSED_THRESHOLD = 2


class CarStateCMA(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.is_spa = isinstance(CAR(CP.carFingerprint).config, VolvoSPAPlatformConfig)
    self.gas_pressed_prev = False
    self.dispatch_lca_2_msg = False
    self.msg_pscm = {}
    self.msg_lca = {}
    self.msg_lca_2 = {}
    self.msg_lca_3 = {}
    self.msg_gear_position = {}
    self.pilot_assist_engaged = False
    self.pscm_status_supported = False
    self.pscm_rearm_required = False
    self.msg_lca_5 = {}  # Formerly msg_speed_1
    self.msg_speed = {}
    self.msg_speed_2 = {}
    self.msg_0x1a = {}
    self.msg_egsm = {}
    self.msg_pscm_related = {}
    self.msg_lca_4 = {}
    self.msg_lca_6 = {}
    self.msg_lca_7 = {}
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
    cp_main = can_parsers[Bus.main]
    cp_pt = can_parsers[Bus.pt]
    cp_party = can_parsers[Bus.party]
    ret = structs.CarState()

    # car speed
    # SPEED on the main bus, not BUS1_SPEED on the PT bus: the main bus is identical
    # across harnesses, while which car bus lands on PT (bus 1) is not, and the PT DBC
    # in use depends on the fingerprint. Regressed against GPS ground speed over two
    # routes on different harnesses: r=0.99989 both, residual sd 0.35-0.40 km/h.
    ret.vEgoRaw = cp_main.vl["SPEED"]["SPEED"] * SPEED_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = ret.vEgoRaw <= 0.1 # 0.1 m/s

    # gas
    # CMA ECM_1.ACCELERATOR_PEDAL_POS is raw 0-255 (DBC factor 1, idle ~20).
    # SPA ECM_1.ACCELERATOR_PEDAL_POS is DBC-scaled to percent (factor 0.00390625, idle ~0).
    # Thresholds must match volvo.h (see opendbc/safety/modes/volvo.h GAS_PRESSED_THRESHOLD_*)
    # and opendbc/safety/tests/test_volvo.py::test_gas_threshold_self_consistent.
    if self.is_spa:
      ret.gasPressed = cp_pt.vl["ECM_1"]["ACCELERATOR_PEDAL_POS"] > 1.0  # percent
    else:
      ret.gasPressed = cp_pt.vl["ECM_1"]["ACCELERATOR_PEDAL_POS"] > 20+1  # raw counts, 20 baseline + 1 tolerance

    # brake
    #ret.brakePressed = bool(cp_main.vl["LCA_2"]["BRAKE_PEDAL_PRESSED_A"] or cp_main.vl["LCA_2"]["BRAKE_PEDAL_PRESSED_B"])
    # BRAKE_PEDAL_PRESSED_A goes active when user starts pressing brake pedal, but no brake light is on yet due to tolerance
    # BRAKE_PEDAL_PRESSED_B goes active when when the brake pedal is pressed above minimum threshold, brake light is on
    ret.brakePressed = cp_main.vl["LCA_2"]["BRAKE_PEDAL_PRESSED_B"] == 1
    ret.parkingBrake = False # TODO: add parking brake

    # stability control - becomes true when ESC intervenes (e.g., aquaplaning)
    ret.espActive = cp_main.vl["LCA_2"]["ESC_ACTUATING"] == 1 and cp_main.vl["LCA_2"]["ESC_ELIGIBLE"] == 1

    # steering wheel
    ret.steeringAngleDeg = cp_party.vl['PSCM']['PSCM_ANGLE_SENSOR'] # openpilot expects a negative value for a right turn
    #ret.steeringAngleDeg = cp_party.vl['SAS']['SAS_ANGLE_SENSOR']

    ret.steeringTorque = -cp_party.vl['DRIVER_INPUT']['STEERING_DRIVER_INPUT']  # Car right turn is negative, openpilot right turn is positive
    driver_input = abs(cp_party.vl['DRIVER_INPUT']['STEERING_DRIVER_INPUT'])
    ret.steeringPressed = driver_input > STEERING_PRESSED_THRESHOLD

    # EPS status - placeholder until actual signal is found
    self.eps_active = True  # Assume EPS is active for now

    if self.is_spa:
      # SPA: byte 0 bit 1, inverted (0 = cruise on, 1 = cruise off)
      cruise_raw = cp_pt.vl["BUS1_CRUISE_CONTROL"]["CRUISE_CONTROL_SPA_ENABLED"] == 1
    else:
      # CMA: two separate boolean signals
      cruise_raw = cp_pt.vl["BUS1_CRUISE_CONTROL"]["CRUISE_CONTROL_ENABLED"] == 1 or cp_pt.vl["BUS1_CRUISE_CONTROL"]["CRUISE_CONTROL_ENABLED_IDLE_TRAFFIC"] == 1

    ret.cruiseState.enabled = cruise_raw

    self.gas_pressed_prev = ret.gasPressed
    ret.cruiseState.available = cruise_raw  # This platform has no decoded MAIN toggle.
    ret.cruiseState.speed = 0  # TODO: Find cruise set speed (not required for lateral control)
    ret.cruiseState.nonAdaptive = False
    ret.cruiseState.standstill = ret.standstill # False # Todo: Find cruise control standstill signal

    # gear
    gearPosition = cp_main.vl['GEAR_POSITION']['GEAR_POSITION'] # 0: P; 1: R; 2: N; 3: D; 4: B;
    if gearPosition == 0:
      ret.gearShifter = GearShifter.park
    elif gearPosition == 1:
      ret.gearShifter = GearShifter.reverse
    elif gearPosition == 2:
      ret.gearShifter = GearShifter.neutral
    elif gearPosition == 3:
      ret.gearShifter = GearShifter.drive
    elif gearPosition == 4:
      ret.gearShifter = GearShifter.drive

    # blinkers TODO FlexRay
    ret.leftBlinker = False
    ret.rightBlinker = False

    # lock info TODO FlexRay
    ret.doorOpen = False # TODO: add door open
    ret.seatbeltUnlatched = False # TODO: add seatbelt unlatched

    # Store entire message dictionaries
    self.msg_pscm = cp_party.vl['PSCM']
    self.msg_lca = cp_main.vl['LCA']
    self.msg_lca_2 = cp_main.vl['LCA_2']
    self.msg_lca_3 = cp_main.vl['LCA_3']
    self.msg_lca_4 = cp_main.vl['LCA_4']
    self.msg_lca_5 = cp_main.vl['LCA_5']
    self.msg_lca_6 = cp_main.vl['LCA_6']
    self.msg_lca_7 = cp_main.vl['LCA_7']
    self.msg_speed = cp_main.vl['SPEED']
    self.msg_speed_2 = cp_main.vl['SPEED_2']
    self.msg_gear_position = cp_main.vl['GEAR_POSITION']
    self.msg_egsm = cp_party.vl['EGSM']
    self.msg_pscm_related = cp_party.vl['PSCM_RELATED']

    self.pilot_assist_engaged = cp_main.vl['LCA_2']['PILOT_ASSIST_ENGAGED'] == 1

    return ret

  @staticmethod
  def get_can_parsers(CP):
    # Require each receiver/source used by state or replacement output. Empty lists
    # silently accept missing sources and leave replacement payloads at DBC zero.
    spa = isinstance(CAR(CP.carFingerprint).config, VolvoSPAPlatformConfig)
    return {
      Bus.main: CANParser(DBC[CP.carFingerprint][Bus.main], [
        ("SPEED", 50), ("SPEED_2", 50), ("GEAR_POSITION", 40),
        ("LCA", 100), ("LCA_2", 50), ("LCA_3", 67),
        ("LCA_4", 29), ("LCA_5", 50), ("LCA_6", 25), ("LCA_7", 29),
      ], 0),
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [
        ("ECM_1", 100 if spa else 17), ("BUS1_CRUISE_CONTROL", 5 if spa else 10),
      ], 1),
      Bus.party: CANParser(DBC[CP.carFingerprint][Bus.party], [
        ("PSCM", 100), ("DRIVER_INPUT", 100), ("EGSM", 100), ("PSCM_RELATED", 100),
      ], 2),
    }
