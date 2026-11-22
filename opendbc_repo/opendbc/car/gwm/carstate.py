import copy

from opendbc.can import CANParser
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase
from opendbc.car.gwm.values import DBC

ButtonType = structs.CarState.ButtonEvent.Type
GearShifter = structs.CarState.GearShifter

# The stalk reports the two gap-adjust directions as separate bits. openpilot has
# a single gap button type, so both collapse onto it; the codes only need to be
# distinct so a direct reduce -> increase switch still produces both edges.
DISTANCE_REDUCE, DISTANCE_INCREASE = 1, 2
BUTTONS_DICT = {DISTANCE_REDUCE: ButtonType.gapAdjustCruise,
                DISTANCE_INCREASE: ButtonType.gapAdjustCruise}


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)

    self.steer_and_ap_stalk_msg = {}
    self.eps_stock_values = {}
    self.camera_stock_values = {}
    self.longitudinal_stock_values = {}
    self.hud_stock_values = {}

    self.is_activation_lever_pulled = False
    self.prev_activation_lever_pulled = False
    self.main_on = False
    self.acc_enabled = False
    self.steer_fault_temporary_counter = 0
    self.eps_refusing_counter = 0
    # Published by CarController each frame: this codebase gives CarState no
    # access to CarControl, and the EPS-refusal check below only means
    # anything while openpilot is actually commanding steering.
    self.lat_active = False

    # Stop & Go: track cruise state to distinguish standstill from real fault
    self.cruise_state_2 = 0

    self.prev_distance_button = 0

  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]

    ret = structs.CarState()

    self.steer_and_ap_stalk_msg = copy.copy(cp.vl["STEER_AND_AP_STALK"])
    self.eps_stock_values = copy.copy(cp.vl["RX_STEER_RELATED"])
    self.camera_stock_values = copy.copy(cp_cam.vl["STEER_CMD"])
    self.longitudinal_stock_values = copy.copy(cp_cam.vl["ACC_CMD"])
    self.hud_stock_values = copy.copy(cp_cam.vl["LATERAL_STATE"])

    self.parse_wheel_speeds(ret,
      cp.vl["WHEEL_SPEEDS"]["FRONT_LEFT_WHEEL_SPEED"],
      cp.vl["WHEEL_SPEEDS"]["FRONT_RIGHT_WHEEL_SPEED"],
      cp.vl["WHEEL_SPEEDS"]["REAR_LEFT_WHEEL_SPEED"],
      cp.vl["WHEEL_SPEEDS"]["REAR_RIGHT_WHEEL_SPEED"],
    )

    # Stop & Go fix:
    # The GWM H6 GT has no native Stop & Go - the ACC module deactivates
    # (CRUISE_STATE_2 -> 0) whenever the car stops. This is expected behaviour,
    # NOT a real ACC fault. We must NOT propagate this as accFaulted, otherwise
    # openpilot drops longitudinal control at every stop and can never resume.
    #
    # Real faults (hardware/comms errors) show up through steerFault paths and
    # other signals. The resume pulse in carcontroller re-engages the ACC ECU.
    self.cruise_state_2 = int(cp_cam.vl["ACC"]["CRUISE_STATE_2"])

    if self.CP.openpilotLongitudinalControl:
      # Standstill deactivation (CRUISE_STATE_2 = 2, sometimes 1) is normal on
      # this car and handled by the resume pulse - never fault on it. State 0
      # while MOVING is a real ACC ECU fault: observed on route 00000061
      # (seg 8, t=625.7), the ECU dropped to 0 one second into a stop&go
      # launch, locked its reported set speed at 30 km/h (its minimum) and
      # ignored the stalk until an ignition cycle. Reproduced three more times
      # at 92-120 km/h on route 000000f3--91956bcc4b (segs 64/109/170) with no
      # stalk input at all, so it is an ECU-side fault, not a command we send.
      # Since pcmCruise is true, openpilot would otherwise silently obey the
      # poisoned 30 km/h set - surface the fault instead so the driver knows to
      # cycle the ignition.
      ret.accFaulted = self.cruise_state_2 == 0 and self.main_on and ret.vEgoRaw > 1.0
    else:
      # Stock ACC path: honour the original logic
      ret.accFaulted = self.cruise_state_2 == 0

    ret.cruiseState.speed = cp_cam.vl["ACC"]["ACC_SPEED_SELECTION"] * CV.KPH_TO_MS
    if not self.CP.openpilotLongitudinalControl:
      ret.cruiseState.speed = -1

    ret.standstill = abs(ret.vEgoRaw) < 1e-3
    ret.gasPressed = cp.vl["CAR_OVERALL_SIGNALS2"]["GAS_POSITION"] > 0
    ret.brakePressed = cp.vl["BRAKE2"]["PEDAL_BRAKE_PRESSED"] != 0
    ret.parkingBrake = cp.vl["CAR_OVERALL_SIGNALS"]["DRIVE_MODE"] == 0

    ret.gearShifter = GearShifter.drive    if int(cp.vl["CAR_OVERALL_SIGNALS"]["DRIVE_MODE"]) == 1 else \
                      GearShifter.neutral  if int(cp.vl["CAR_OVERALL_SIGNALS"]["DRIVE_MODE"]) == 2 else \
                      GearShifter.reverse  if int(cp.vl["CAR_OVERALL_SIGNALS"]["DRIVE_MODE"]) == 3 else \
                      GearShifter.park

    ret.steeringAngleDeg = cp.vl["STEER_AND_AP_STALK"]["STEERING_ANGLE"] * \
                           (-1 if cp.vl["STEER_AND_AP_STALK"]["STEERING_DIRECTION"] else 1)
    ret.steeringRateDeg = cp.vl["STEER_AND_AP_STALK"]["STEERING_RATE"] * \
                          (-1 if (cp.vl["STEER_AND_AP_STALK"]["RATE_DIRECTION"] > 0) else 1)

    # Two independent 1 s debounces, both surfaced as steerFaultTemporary:
    #   EPS_FAULT_PERMANENT  - the EPS latched its own permanent fault flag.
    #   A_RX_STEER_REQUESTED - the EPS stopped honouring our torque request
    #                          while openpilot was steering (silent refusal).
    # The second gate uses self.lat_active, which CarController sets one frame
    # earlier; a single frame of lag is immaterial against a 100 frame debounce.
    self.steer_fault_temporary_counter = (self.steer_fault_temporary_counter + 1) \
                                          if (cp.vl["RX_STEER_RELATED"]["EPS_FAULT_PERMANENT"] == 1) else 0

    eps_obeying = cp.vl["RX_STEER_RELATED"]["A_RX_STEER_REQUESTED"] == 1
    self.eps_refusing_counter = (self.eps_refusing_counter + 1) \
                                 if (self.lat_active and not eps_obeying) else 0

    ret.steerFaultTemporary = self.steer_fault_temporary_counter > 100 or self.eps_refusing_counter > 100
    ret.steerFaultPermanent = False

    ret.steeringTorque = cp.vl["RX_STEER_RELATED"]["B_RX_DRIVER_TORQUE"]
    ret.steeringTorqueEps = cp.vl["RX_STEER_RELATED"]["B_RX_EPS_TORQUE"]
    # Debounced (same helper the ford port uses): the raw > 50 comparison
    # chattered with a hand resting on the wheel at highway speed - 700 km trip
    # qlogs show bursts of 10+ steeringPressed rising edges within a second at
    # 130 km/h, each one a momentary steer override degrading lateral control.
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > 50, 5)

    ret.doorOpen = any([cp.vl["DOOR_DRIVER"]["DOOR_REAR_RIGHT_OPEN"],
                        cp.vl["DOOR_DRIVER"]["DOOR_FRONT_RIGHT_OPEN"],
                        cp.vl["DOOR_DRIVER"]["DOOR_REAR_LEFT_OPEN"],
                        cp.vl["DOOR_DRIVER"]["DOOR_DRIVER_OPEN"]])
    ret.seatbeltUnlatched = bool(cp.vl["SEATBELT"]["SEAT_BELT_DRIVER_STATE"])
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(
      50,
      cp.vl["LIGHTS"]["LEFT_TURN_SIGNAL"],
      cp.vl["LIGHTS"]["RIGHT_TURN_SIGNAL"],
    )
    ret.leftBlindspot = bool(cp.vl["RADAR_BEHIND"]["BSM_LEFT"] > 0)
    ret.rightBlindspot = bool(cp.vl["RADAR_BEHIND"]["BSM_RIGHT"] > 0)

    # Two separate latches so the brake behaves like MADS/Always On Lateral:
    #   main_on      -> lateral availability; only a real stalk cancel
    #                   (AP_CANCEL_COMMAND, stalk down) clears it.
    #   acc_enabled  -> longitudinal (ACC); the brake clears ONLY this one,
    #                   so steering stays active with a foot on the brake.
    # Pulling the stalk up (falling edge of AP_ENABLE_COMMAND) re-latches
    # both, which gives openpilot the enabled rising edge (pcmEnable) it
    # needs to resume ACC after a brake-only disengage - without having to
    # cancel lateral first.
    if cp.vl["STEER_AND_AP_STALK"]["AP_CANCEL_COMMAND"]:
      self.main_on = False
      self.acc_enabled = False

    if ret.brakePressed:
      self.acc_enabled = False

    self.is_activation_lever_pulled = bool(cp.vl["STEER_AND_AP_STALK"]["AP_ENABLE_COMMAND"])
    if not self.is_activation_lever_pulled and self.prev_activation_lever_pulled:
      self.main_on = True
      self.acc_enabled = True
    self.prev_activation_lever_pulled = self.is_activation_lever_pulled

    ret.cruiseState.available = self.main_on
    ret.cruiseState.enabled = self.acc_enabled

    # Gap-adjust presses from the stalk. The host reads these off buttonEvents to
    # follow the car's own distance button, so the two direction bits have to be
    # surfaced here rather than only forwarded in create_buttons_command.
    distance_button = DISTANCE_REDUCE if cp.vl["STEER_AND_AP_STALK"]["AP_REDUCE_DISTANCE_COMMAND"] else \
                      DISTANCE_INCREASE if cp.vl["STEER_AND_AP_STALK"]["AP_INCREASE_DISTANCE_COMMAND"] else 0
    ret.buttonEvents = create_button_events(distance_button, self.prev_distance_button, BUTTONS_DICT)
    self.prev_distance_button = distance_button

    return ret

  @staticmethod
  def get_can_parsers(CP):
    pt_messages = [
      ("STEER_AND_AP_STALK", 50),
      ("RX_STEER_RELATED", 50),
      ("WHEEL_SPEEDS", 50),
      ("CAR_OVERALL_SIGNALS2", 50),
      ("BRAKE2", 50),
      ("BRAKE", 50),
      ("CAR_OVERALL_SIGNALS", 50),
      ("LIGHTS", 10),
      ("RADAR_BEHIND", 20),
      ("DOOR_DRIVER", 10),
      ("SEATBELT", 10),
    ]

    cam_messages = [
      ("STEER_CMD", 50),
      ("ACC_CMD", 50),
      ("LATERAL_STATE", 20),
      ("ACC", 20),
    ]

    # Both buses speak the same DBC on this car; the camera/ADAS stream is on bus 2.
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], pt_messages, 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], cam_messages, 2),
    }
