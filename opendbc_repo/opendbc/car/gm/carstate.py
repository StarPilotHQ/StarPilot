from opendbc.car.gm.steering_authority import SteeringAuthority, STEERING_COMMAND_TIMEOUT_NS
from opendbc.car.gm.values import is_volt_flashed_profile
from opendbc.car.gps import CarGpsTracker, get_car_gps_config
from opendbc.car.gm.values import gm_control_word, is_volt_one_pedal, camera_acc_pedal_profile, BrakeSource, volt_cc_pedal_profile
from opendbc.car.gm.values import is_volt_longitudinal, is_gm_auto_hold
from opendbc.car.gm.auto_hold import config_for as auto_hold_config_for, stopped_for_hold
import copy
from opendbc.can import CANDefine, CANParser, CANPacker
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase
from opendbc.car.gm.gmcan import pedal_crc
from opendbc.car.gm.cc_longitudinal import VoltCcPhysical
from opendbc.car.gm.ordinary_cc import PhysicalObservation
from opendbc.car.gm.hybrid_cc import HybridButtons, HybridStatusPhysical
from opendbc.car.gm.values import malibu_hybrid_profile
from opendbc.car.gm.conventional_pedal import CancelCredit
from opendbc.car.gm.values import (DBC, AccState, CruiseButtons, STEER_THRESHOLD, SDGM_CAR, ALT_ACCS,
                                   ASCM_INTERCEPT_CAR, ORDINARY_ASCM_CAR, ORDINARY_SDGM_CAR, GMFlags, GMSafetyFlags, NO_ACC_BOLT_CAR,
                                   CC_GATEWAY_STOCK_CAR, requires_camera_state_sources, is_conventional_cc_pedal_profile, is_silverado_cc_pedal_profile,
                                   is_volt_cc_profile, is_volt_cc_f1_profile, is_silverado_cc_stock_profile, is_ordinary_cc_profile,
                                   is_malibu_cc_f1_profile, VOLT_BSM_CAR, CAR,
                                   is_volt_gateway_profile, is_volt_gateway_alternate_brake, is_bolt_cc_profile, BOLT_CC_WORDS,
                                   is_bolt_pedal_profile, is_bolt_pedal_removed_profile, is_bolt_present_no_acc_pedal_profile, is_volt_camera_removed,
                                   is_ordinary_camera_profile, is_ordinary_camera_removed)

ButtonType = structs.CarState.ButtonEvent.Type
TransmissionType = structs.CarParams.TransmissionType
NetworkLocation = structs.CarParams.NetworkLocation

STANDSTILL_THRESHOLD = 10 * 0.0311
PEDAL_SENSOR_TIMEOUT_NS = 100_000_000  # Five expected 50 Hz frames.

BUTTONS_DICT = {CruiseButtons.RES_ACCEL: ButtonType.accelCruise, CruiseButtons.DECEL_SET: ButtonType.decelCruise,
                CruiseButtons.MAIN: ButtonType.mainCruise, CruiseButtons.CANCEL: ButtonType.cancel}


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.steering_authority = SteeringAuthority(CP)
    self.car_gps_tracker = CarGpsTracker(CP)
    self.car_gps_supported = self.car_gps_tracker.config is not None
    self.hybrid_profile = malibu_hybrid_profile(CP)
    self.hybrid_buttons = HybridButtons()
    self.hybrid_sources = ()
    self.hybrid_status = None
    self.gm_auto_hold_config = auto_hold_config_for(CP)
    self.camera_pedal_profile = camera_acc_pedal_profile(CP)
    self.camera_pedal_sources = ()
    self.camera_pedal_rear = (0., 0.)
    self.camera_pedal_forward = False
    self.stock_fcw_alert = 0
    self.ordinary_removed_sources = ()
    self.volt_removed_sources = ()
    self.volt_removed_credit_ns = 0
    self.volt_removed_button_ns = 0
    self.volt_removed_observed_ns = 0
    self.volt_removed_counter = None
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])
    self.shifter_values = can_define.dv["ECMPRDNL2"]["PRNDL2"]
    self.cluster_speed_hyst_gap = CV.KPH_TO_MS / 2.
    self.cluster_min_speed = CV.KPH_TO_MS / 2.

    self.loopback_lka_steering_cmd_updated = False
    self.bolt_steering_pending: tuple[int, int] | None = None
    self.loopback_lka_steering_cmd_counter = 0
    self.loopback_lka_steering_cmd_ts_nanos = 0
    self.pt_lka_steering_cmd_counter = 0
    self.cam_lka_steering_cmd_counter = 0
    self.buttons_counter = 0

    self.distance_button = 0
    self.flashed_volt_profile = is_volt_flashed_profile(CP)
    self.flashed_lkas_button = None
    self.pedal_sensor_healthy = False
    self.pedal_sensor_ts_nanos = 0
    self.bolt_pedal_gear_ts_nanos = 0
    self.bolt_pedal_main_ts_nanos = 0
    self.pedal_sensor_counter = None
    self.pedal_packer = CANPacker(DBC[CP.carFingerprint][Bus.pt]) if CP.flags & GMFlags.PEDAL_LONG.value else None
    self.stock_acc_status_ts_nanos = 0
    self.bolt_acc_pedal_secondary_friction = (CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and
                                             int(CP.safetyConfigs[0].safetyParam) == 0x1CD and is_bolt_pedal_profile(CP))
    self.bolt_acc_pedal_friction_unavailable = False
    self.bolt_acc_brake_clock_ns = 0
    self.bolt_acc_brake_clock_counter = 0
    self.bolt_pedal_removed_sources = ()
    self.bolt_pedal_removed_stock_ts_nanos = 0
    self.bolt_pedal_stock_active = False
    self.bolt_pedal_stock_ts_nanos = 0
    self.bolt_pedal_removed_stock_active = False
    self.bolt_pedal_removed_acc_active = False
    self.bolt_pedal_standstill_ts_nanos = 0
    self.volt_cc_physical = None
    self.volt_cc_button_counter = None
    self.volt_cc_button_source_ns = self.volt_cc_button_credit_ns = 0
    self.volt_gateway_source_ns = ()
    self.volt_sng_sources = ()
    self.gm_auto_hold_sources = ()
    self.gm_auto_hold_brake = 0.
    self.gm_auto_hold_forward = self.gm_auto_hold_moving = False
    self.gm_auto_hold_wheel_ns = 0
    self.gm_auto_hold_unavailable = True
    self.gm_auto_hold_engaged = False
    self.volt_one_pedal_mode = False
    self.volt_one_pedal_mode_ns = 0
    self.volt_one_pedal_moving = False
    self.volt_one_pedal_stopped = False
    self.cc_gateway_cruise_ts_nanos = 0
    self.silverado_stock_sources = ()
    self.cc_gateway_buttons_ts_nanos = 0
    self.camera_stock_status_ts_nanos = 0
    self.camera_stock_sources_valid = False
    self.volt_cc_pedal_profile = volt_cc_pedal_profile(CP)
    self.volt_cc_pedal_sources = ()
    self.conventional_pedal_sources = ()
    no_acc_pedal_cancel = (is_bolt_present_no_acc_pedal_profile(CP) or is_bolt_present_no_acc_pedal_profile(CP, stock_only=True) or
                          CP.carFingerprint in NO_ACC_BOLT_CAR and is_bolt_pedal_removed_profile(CP))
    self.conventional_cancel_credit = CancelCredit(neutral_interval_ns=100_000_000, allow_non_acc_neutral=True) if no_acc_pedal_cancel else CancelCredit()
    self.silverado_pedal_sources = ()
    self.bolt_cc_profile = is_bolt_cc_profile(CP)
    self.bolt_cc_removed = self.bolt_cc_profile and CP.safetyConfigs[0].safetyParam == BOLT_CC_WORDS[CP.carFingerprint][1]
    self.bolt_cc_sources = ()

  def update_button_enable(self, buttonEvents: list[structs.CarState.ButtonEvent]):
    if not self.CP.pcmCruise:
      for b in buttonEvents:
        # The ECM allows enabling on falling edge of set, but only rising edge of resume
        if (b.type == ButtonType.accelCruise and b.pressed) or \
          (b.type == ButtonType.decelCruise and not b.pressed):
          return True
    return False

  def get_car_gps(self):
    return self.car_gps_tracker.get()

  def update(self, can_parsers) -> structs.CarState:
    bolt_pedal_profile = is_bolt_pedal_profile(self.CP)
    volt_camera_removed = is_volt_camera_removed(self.CP)
    pt_cp = can_parsers[Bus.pt]
    cam_cp = can_parsers[Bus.cam]
    loopback_cp = can_parsers[Bus.loopback]

    ret = structs.CarState()
    pedal_stock_no_acc = self.CP.carFingerprint in NO_ACC_BOLT_CAR and is_bolt_pedal_profile(self.CP, stock_only=True)
    removed_pedal = is_bolt_pedal_removed_profile(self.CP) or is_bolt_pedal_removed_profile(self.CP, stock_only=True)
    if removed_pedal:
      self.bolt_pedal_removed_acc_active = pt_cp.vl["AcceleratorPedal2"]["CruiseState"] != AccState.OFF
      fields = (("PSCMStatus", "LKATorqueDelivered", 300_000_000), ("EBCMWheelSpdRear", "RLWheelSpd", 100_000_000),
                ("ASCMSteeringButton", "RollingCounter", 100_000_000), ("AcceleratorPedal2", "CruiseState", 300_000_000),
                ("ECMEngineStatus", "CruiseMainOn", 300_000_000), ("ECMAcceleratorPos", "BrakePedalPos", 300_000_000),
                ("ECMPRDNL2", "PRNDL2", 100_000_000), ("ECMCruiseControl", "CruiseActive", 300_000_000),
                ("EBCMRegenPaddle", "RegenPaddle", 100_000_000))
      if not self.CP.openpilotLongitudinalControl:
        fields = tuple(row for row in fields if row[0] not in ("ECMAcceleratorPos", "EBCMRegenPaddle"))
      self.bolt_pedal_removed_sources = tuple((pt_cp.ts_nanos[name][signal], limit) for name, signal, limit in fields)
      self.bolt_pedal_removed_stock_ts_nanos = pt_cp.ts_nanos["ECMCruiseControl"]["CruiseActive"]
      self.bolt_pedal_removed_stock_active = bool(pt_cp.vl["ECMCruiseControl"]["CruiseActive"])
    present_no_acc_pedal = (is_bolt_present_no_acc_pedal_profile(self.CP) or
                            is_bolt_present_no_acc_pedal_profile(self.CP, stock_only=True))
    if bolt_pedal_profile or present_no_acc_pedal:
      self.bolt_pedal_gear_ts_nanos = (self.conventional_cancel_credit.gear_ns if present_no_acc_pedal else
                                     pt_cp.ts_nanos["ECMPRDNL2"]["PRNDL2"])
      self.bolt_pedal_main_ts_nanos = pt_cp.ts_nanos["ECMEngineStatus"]["CruiseMainOn"]

    if self.camera_pedal_profile is not None:
      analog = (("ECMAcceleratorPos", "BrakePedalPos") if self.camera_pedal_profile.brake_source == BrakeSource.BE else
                ("EBCMBrakePedalPosition", "BrakePedalPosition"))
      fields = (("PSCMStatus", "LKATorqueDelivered", 300_000_000),
                ("EBCMWheelSpdRear", "RLWheelSpd", 100_000_000),
                ("ASCMSteeringButton", "RollingCounter", 100_000_000),
                ("AcceleratorPedal2", "CruiseState", 300_000_000),
                ("ECMEngineStatus", "CruiseMainOn", 300_000_000),
                (*analog, 300_000_000), ("ECMPRDNL2", "PRNDL2",
                                       1_000_000_000 if self.camera_pedal_profile.topology in ("gateway", "ascm", "sdgm") else 100_000_000))
      self.camera_pedal_sources = tuple((pt_cp.ts_nanos[name][signal], limit) for name, signal, limit in fields)
      if self.camera_pedal_profile.volt:
        self.camera_pedal_sources += ((pt_cp.ts_nanos["EBCMRegenPaddle"]["RegenPaddle"], 100_000_000),)
      rear = pt_cp.vl["EBCMWheelSpdRear"]
      self.camera_pedal_rear = (rear["RLWheelSpd"], rear["RRWheelSpd"])
      gear = pt_cp.vl["ECMPRDNL2"]
      self.camera_pedal_forward = gear["PRNDL2"] in (4, 6) or gear["ManualMode"] == 1 and 4 <= gear["PRNDL2"] <= 7

    if is_conventional_cc_pedal_profile(self.CP) and not is_silverado_cc_pedal_profile(self.CP):
      source_fields = (("PSCMStatus", "LKATorqueDelivered"), ("EBCMBrakePedalPosition", "BrakePedalPosition"),
                       ("ECMEngineStatus", "CruiseMainOn"), ("ECMCruiseControl", "CruiseActive"),
                       ("ASCMSteeringButton", "RollingCounter"), ("AcceleratorPedal2", "CruiseState"),
                       ("ECMPRDNL2", "PRNDL2"), ("GAS_SENSOR", "COUNTER_PEDAL"))
      self.conventional_pedal_sources = tuple(pt_cp.ts_nanos[name][field] for name, field in source_fields)
      if self.CP.carFingerprint == CAR.CHEVROLET_MALIBU_CC and not self.CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG:
        self.conventional_pedal_sources += (pt_cp.ts_nanos["ECMAcceleratorPos"]["BrakePedalPos"],)

    if is_silverado_cc_pedal_profile(self.CP):
      analog = (("EBCMBrakePedalPosition", "BrakePedalPosition") if self.CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG else
                ("ECMAcceleratorPos", "BrakePedalPos"))
      source_fields = (("PSCMStatus", "LKATorqueDelivered"), analog, ("ECMEngineStatus", "CruiseMainOn"),
                       ("ECMCruiseControl", "CruiseActive"), ("ASCMSteeringButton", "RollingCounter"),
                       ("AcceleratorPedal2", "CruiseState"), ("ECMPRDNL2", "PRNDL2"), ("GAS_SENSOR", "COUNTER_PEDAL"))
      self.silverado_pedal_sources = tuple(pt_cp.ts_nanos[name][field] for name, field in source_fields)
      self.silverado_brake_analog = pt_cp.vl[analog[0]][analog[1]] / (208. if self.CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG else 1.)
      self.pt_lka_steering_cmd_counter = pt_cp.vl["ASCMLKASteeringCmd"]["RollingCounter"]

    if self.hybrid_profile is not None:
      fields = (("PSCMStatus", "LKATorqueDelivered", 300_000_000),
                ("EBCMWheelSpdRear", "RLWheelSpd", 100_000_000),
                ("ASCMSteeringButton", "RollingCounter", 100_000_000),
                ("AcceleratorPedal2", "CruiseState", 300_000_000),
                ("ECMEngineStatus", "CruiseMainOn", 300_000_000),
                ("ECMCruiseControl", "CruiseActive", 300_000_000),
                ("ECMPRDNL2", "PRNDL2", 1_000_000_000),
                ("EBCMRegenPaddle", "RegenPaddle", 100_000_000))
      self.hybrid_sources = tuple((pt_cp.ts_nanos[name][signal], limit) for name, signal, limit in fields)
    prev_cruise_buttons = self.cruise_buttons
    prev_distance_button = self.distance_button
    self.cruise_buttons = pt_cp.vl["ASCMSteeringButton"]["ACCButtons"]
    self.distance_button = pt_cp.vl["ASCMSteeringButton"]["DistanceButton"]
    self.buttons_counter = pt_cp.vl["ASCMSteeringButton"]["RollingCounter"]
    if (self.CP.carFingerprint in CC_GATEWAY_STOCK_CAR or self.CP.carFingerprint == CAR.CHEVROLET_VOLT_CC):
      self.cc_gateway_buttons_ts_nanos = pt_cp.ts_nanos["ASCMSteeringButton"]["RollingCounter"]
      self.cc_gateway_cruise_ts_nanos = pt_cp.ts_nanos["ECMCruiseControl"]["CruiseActive"]
    if is_silverado_cc_stock_profile(self.CP):
      fields = (("PSCMStatus", "LKATorqueDelivered", 300_000_000),
                ("EBCMWheelSpdRear", "RLWheelSpd", 100_000_000),
                ("ASCMSteeringButton", "RollingCounter", 300_000_000),
                ("AcceleratorPedal2", "AcceleratorPedal2", 300_000_000),
                ("ECMEngineStatus", "CruiseMainOn", 300_000_000),
                ("ECMAcceleratorPos", "BrakePedalPos", 300_000_000),
                ("ECMPRDNL2", "PRNDL2", 1_000_000_000),
                ("ECMCruiseControl", "CruiseActive", 300_000_000))
      self.silverado_stock_sources = tuple((pt_cp.ts_nanos[name][signal], limit) for name, signal, limit in fields)
    if is_ordinary_camera_removed(self.CP):
      names = (("PSCMStatus", "LKATorqueDelivered"), ("EBCMBrakePedalPosition", "BrakePedalPosition"),
               ("ECMEngineStatus", "CruiseMainOn"), ("AcceleratorPedal2", "CruiseState"),
               ("ASCMSteeringButton", "RollingCounter"), ("ECMPRDNL2", "PRNDL2"))
      self.ordinary_removed_sources = tuple(pt_cp.ts_nanos[name][field] for name, field in names)

    if (volt_camera_removed or self.camera_pedal_profile is not None and
        self.camera_pedal_profile.topology in ("gateway", "ascm", "sdgm") and not self.camera_pedal_profile.longitudinal):
      names = (("PSCMStatus", "LKATorqueDelivered"), ("EBCMWheelSpdRear", "RLWheelSpd"),
               ("AcceleratorPedal2", "CruiseState"), ("ECMEngineStatus", "CruiseMainOn"),
               ("ASCMSteeringButton", "RollingCounter"), ("EBCMRegenPaddle", "RegenPaddle"))
      self.volt_removed_sources = tuple(pt_cp.ts_nanos[name][field] for name, field in names)
      button = pt_cp.vl["ASCMSteeringButton"]
      stamp = pt_cp.ts_nanos["ASCMSteeringButton"]["RollingCounter"]
      counter = int(button["RollingCounter"])
      neutral = (button["ACCButtons"] == CruiseButtons.UNPRESS and button["ACCAlwaysOne"] == 1 and
                 button["DistanceButton"] == 0 and button["LKAButton"] == 0 and button["DriveModeButton"] == 0 and
                 button["SteeringButtonChecksum"] == 0xFF + counter * 0x4EF)
      if stamp != self.volt_removed_button_ns:
        timely = 0 < stamp - self.volt_removed_observed_ns <= 100_000_000
        first = self.volt_removed_counter is None
        if neutral and (first or timely and counter == (self.volt_removed_counter + 1) % 4):
          self.volt_removed_credit_ns = stamp
        elif not neutral or not timely or counter != self.volt_removed_counter:
          self.volt_removed_credit_ns = 0
        if first or counter != self.volt_removed_counter:
          self.volt_removed_observed_ns = stamp
        self.volt_removed_counter, self.volt_removed_button_ns = counter, stamp
    if requires_camera_state_sources(self.CP) and not pedal_stock_no_acc and not is_ordinary_camera_removed(self.CP):
      self.camera_stock_status_ts_nanos = cam_cp.ts_nanos["ASCMActiveCruiseControlStatus"]["ACCCruiseState"]
      self.camera_stock_sources_valid = pt_cp.can_valid and cam_cp.can_valid
    if is_volt_cc_profile(self.CP) or is_ordinary_cc_profile(self.CP):
      button = pt_cp.vl["ASCMSteeringButton"]
      button_ns = pt_cp.ts_nanos["ASCMSteeringButton"]["RollingCounter"]
      counter = int(button["RollingCounter"])
      neutral = bool(button["ACCButtons"] == CruiseButtons.UNPRESS and button["ACCAlwaysOne"] == 1 and
                     button["DistanceButton"] == 0 and button["LKAButton"] == 0 and button["DriveModeButton"] == 0)
      if button_ns != self.volt_cc_button_source_ns:
        first = self.volt_cc_button_counter is None
        timely = 0 < button_ns - self.volt_cc_button_source_ns <= 100_000_000
        forward = not first and counter == (self.volt_cc_button_counter + 1) % 4
        duplicate = not first and counter == self.volt_cc_button_counter
        if neutral and (first or (timely and forward)):
          self.volt_cc_button_credit_ns = button_ns
        elif not neutral or not timely or not duplicate:
          self.volt_cc_button_credit_ns = 0
        self.volt_cc_button_counter, self.volt_cc_button_source_ns = counter, button_ns
      physical_type = PhysicalObservation if is_ordinary_cc_profile(self.CP) else VoltCcPhysical
      source_ns = (
        pt_cp.ts_nanos["ECMCruiseControl"]["CruiseActive"],
         pt_cp.ts_nanos["ASCMSteeringButton"]["RollingCounter"],
         pt_cp.ts_nanos["ECMEngineStatus"]["CruiseMainOn"],
         (pt_cp.ts_nanos["EBCMBrakePedalPosition"]["BrakePedalPosition"] if is_malibu_cc_f1_profile(self.CP) or is_volt_cc_f1_profile(self.CP) else
          pt_cp.ts_nanos["ECMAcceleratorPos"]["BrakePedalPos"]),
         pt_cp.ts_nanos["ECMPRDNL2"]["PRNDL2"],
         pt_cp.ts_nanos["AcceleratorPedal2"]["AcceleratorPedal2"])
      if is_volt_cc_profile(self.CP):
        source_ns += (pt_cp.ts_nanos["EBCMRegenPaddle"]["RegenPaddle"],)
      self.volt_cc_physical = physical_type(
        pt_cp.ts_nanos["EBCMWheelSpdRear"]["RLWheelSpd"], source_ns, neutral,
        bool(pt_cp.vl["EBCMWheelSpdRear"]["RLWheelDir"] == 1 and pt_cp.vl["EBCMWheelSpdRear"]["RRWheelDir"] == 1),
        self.volt_cc_button_credit_ns)
    if is_volt_gateway_profile(self.CP) and not self.CP.openpilotLongitudinalControl:
      brake_name, brake_signal = (("EBCMBrakePedalPosition", "BrakePedalPosition")
                                  if is_volt_gateway_alternate_brake(self.CP) else ("ECMAcceleratorPos", "BrakePedalPos"))
      # Card can finalize the preference after the CI has already been created.
      # Observe the same physical messages before reading their lazy timestamps.
      for name in ("PSCMStatus", "EBCMWheelSpdRear", "ASCMSteeringButton", brake_name,
                   "AcceleratorPedal2", "ECMEngineStatus", "EBCMRegenPaddle"):
        pt_cp.vl[name]
      self.volt_gateway_source_ns = (
        pt_cp.ts_nanos["PSCMStatus"]["LKADriverAppldTrq"],
        pt_cp.ts_nanos["EBCMWheelSpdRear"]["RLWheelSpd"],
        pt_cp.ts_nanos["ASCMSteeringButton"]["RollingCounter"],
        pt_cp.ts_nanos[brake_name][brake_signal],
        pt_cp.ts_nanos["AcceleratorPedal2"]["AcceleratorPedal2"],
        pt_cp.ts_nanos["ECMEngineStatus"]["CruiseMainOn"],
        pt_cp.ts_nanos["EBCMRegenPaddle"]["RegenPaddle"])
    if is_volt_longitudinal(self.CP):
      # Observe only actual resume/driver sources, using the finalized brake owner.
      alternate = is_volt_gateway_alternate_brake(self.CP) or (
        volt_camera_removed and bool(self.CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG))
      c9 = self.CP.networkLocation == NetworkLocation.fwdCamera and not (
        gm_control_word(self.CP) & (GMSafetyFlags.ASCM_INTERCEPT | GMSafetyFlags.SDGM) and
        not gm_control_word(self.CP) & GMSafetyFlags.BRAKE_C9)
      brake = (("EBCMBrakePedalPosition", "BrakePedalPosition") if alternate else
               ("ECMEngineStatus", "BrakePressed") if c9 else ("ECMAcceleratorPos", "BrakePedalPos"))
      names = (("AcceleratorPedal2", "CruiseState", 100_000_000),
               ("ECMPRDNL2", "PRNDL2", 300_000_000),
               ("ECMEngineStatus", "BrakePressed", 100_000_000),
               ("EBCMRegenPaddle", "RegenPaddle", 100_000_000),
               (*brake, 300_000_000))
      for name, _, _ in names:
        pt_cp.vl[name]
      self.volt_sng_sources = tuple((pt_cp.ts_nanos[name][signal], limit) for name, signal, limit in names)
      if requires_camera_state_sources(self.CP):
        cam_cp.vl["ASCMActiveCruiseControlStatus"]
        self.volt_sng_sources += ((cam_cp.ts_nanos["ASCMActiveCruiseControlStatus"]["ACCCruiseState"], 100_000_000),)
    gm_auto_hold = is_gm_auto_hold(self.CP)
    if gm_auto_hold:
      alternate = is_volt_gateway_alternate_brake(self.CP)
      alternate_force = alternate or (volt_camera_removed and bool(self.CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG)) or (
        self.camera_pedal_profile is not None and self.camera_pedal_profile.volt and
        self.camera_pedal_profile.brake_source == BrakeSource.F1)
      c9 = self.CP.networkLocation == NetworkLocation.fwdCamera and (
        not gm_control_word(self.CP) & (GMSafetyFlags.ASCM_INTERCEPT | GMSafetyFlags.SDGM) or
        bool(gm_control_word(self.CP) & GMSafetyFlags.BRAKE_C9))
      brake_name, brake_signal = (("EBCMBrakePedalPosition", "BrakePedalPosition") if alternate else
                                  ("ECMEngineStatus", "BrakePressed") if c9 else ("ECMAcceleratorPos", "BrakePedalPos"))
      names = (("ECMEngineStatus", "CruiseMainOn"), ("ECMPRDNL2", "PRNDL2"), (brake_name, brake_signal),
               ("AcceleratorPedal2", "CruiseState"), ("EBCMWheelSpdRear", "RLWheelSpd"),
               ("EBCMFrictionBrakeStatus", "FrictionBrakeUnavailable"))
      if self.CP.transmissionType == TransmissionType.direct:
        names += (("EBCMRegenPaddle", "RegenPaddle"),)
      for name, _ in names:
        pt_cp.vl[name]
      self.gm_auto_hold_sources = tuple((pt_cp.ts_nanos[name][signal], 300_000_000) for name, signal in names)
      if self.camera_pedal_profile is not None and self.camera_pedal_profile.volt:
        self.gm_auto_hold_sources += ((pt_cp.ts_nanos["GAS_SENSOR"]["COUNTER_PEDAL"], 100_000_000),)
      # C9 is pressed authority, never analog force; missing-BE profiles retain minimum hold.
      absent_be = c9 and bool(gm_control_word(self.CP) & (GMSafetyFlags.ASCM_INTERCEPT | GMSafetyFlags.SDGM))
      if alternate_force:
        self.gm_auto_hold_brake = pt_cp.vl["EBCMBrakePedalPosition"]["BrakePedalPosition"] / 208.
        if c9:
          self.gm_auto_hold_sources += ((pt_cp.ts_nanos["EBCMBrakePedalPosition"]["BrakePedalPosition"], 300_000_000),)
      else:
        self.gm_auto_hold_brake = 0. if absent_be else pt_cp.vl["ECMAcceleratorPos"]["BrakePedalPos"]
        if c9 and not absent_be:
          self.gm_auto_hold_sources += ((pt_cp.ts_nanos["ECMAcceleratorPos"]["BrakePedalPos"], 300_000_000),)
      gear = pt_cp.vl["ECMPRDNL2"]["PRNDL2"]
      self.gm_auto_hold_forward = gear in (4, 6) or (4 <= gear <= 7 and pt_cp.vl["ECMPRDNL2"]["ManualMode"] == 1)
      wheels = pt_cp.vl["EBCMWheelSpdRear"]
      self.gm_auto_hold_moving = wheels["RLWheelSpd"] >= 12 * .0311 and wheels["RRWheelSpd"] >= 12 * .0311
      self.gm_auto_hold_wheel_ns = pt_cp.ts_nanos["EBCMWheelSpdRear"]["RLWheelSpd"]
      self.gm_auto_hold_unavailable = bool(pt_cp.vl["EBCMFrictionBrakeStatus"]["FrictionBrakeUnavailable"])
    volt_one_pedal = is_volt_one_pedal(self.CP)
    if volt_one_pedal:
      gear = pt_cp.vl["ECMPRDNL2"]
      low = gear["PRNDL2"] == 6 and not gear["ManualMode"]
      mode_ns = pt_cp.ts_nanos["EVDriveMode"]["SinglePedalModeActive"]
      mode_current = 0 < mode_ns <= pt_cp._last_update_nanos and pt_cp._last_update_nanos - mode_ns <= 300_000_000
      self.volt_one_pedal_mode_ns = pt_cp.ts_nanos["ECMPRDNL2"]["PRNDL2"] if low else mode_ns
      self.volt_one_pedal_mode = low or (mode_current and bool(pt_cp.vl["EVDriveMode"]["SinglePedalModeActive"]))
      rear = pt_cp.vl["EBCMWheelSpdRear"]
      self.volt_one_pedal_moving = rear["RLWheelSpd"] > 10 * .0311 and rear["RRWheelSpd"] > 10 * .0311
      self.volt_one_pedal_stopped = rear["RLWheelSpd"] <= 10 * .0311 and rear["RRWheelSpd"] <= 10 * .0311
    self.pscm_status = copy.copy(pt_cp.vl["PSCMStatus"])

    # Variables used for avoiding LKAS faults
    self.loopback_lka_steering_cmd_updated = len(loopback_cp.vl_all["ASCMLKASteeringCmd"]["RollingCounter"]) > 0
    if self.loopback_lka_steering_cmd_updated:
      self.loopback_lka_steering_cmd_counter = loopback_cp.vl["ASCMLKASteeringCmd"]["RollingCounter"]
      self.loopback_lka_steering_cmd_ts_nanos = loopback_cp.ts_nanos["ASCMLKASteeringCmd"]["RollingCounter"]
    if (self.CP.networkLocation == NetworkLocation.fwdCamera and
        not (is_conventional_cc_pedal_profile(self.CP) and self.CP.flags & GMFlags.NO_CAMERA) and
        not (self.camera_pedal_profile is not None and self.camera_pedal_profile.removed) and
        not (self.volt_cc_pedal_profile is not None and self.volt_cc_pedal_profile.removed) and
        not (self.hybrid_profile is not None and self.hybrid_profile.removed) and not removed_pedal):
      if not is_conventional_cc_pedal_profile(self.CP):
        self.pt_lka_steering_cmd_counter = pt_cp.vl["ASCMLKASteeringCmd"]["RollingCounter"]
      if not self.bolt_cc_removed and not volt_camera_removed and not is_ordinary_camera_removed(self.CP):
        self.cam_lka_steering_cmd_counter = cam_cp.vl["ASCMLKASteeringCmd"]["RollingCounter"]

    # This is to avoid a fault where you engage while still moving backwards after shifting to D.
    # An Equinox has been seen with an unsupported status (3), so only check if either wheel is in reverse (2)
    left_whl_sign = -1 if pt_cp.vl["EBCMWheelSpdRear"]["RLWheelDir"] == 2 else 1
    right_whl_sign = -1 if pt_cp.vl["EBCMWheelSpdRear"]["RRWheelDir"] == 2 else 1
    if self.gm_auto_hold_config.continued_stop_speed > .02:
      # Publish unscaled physical rear speeds for the exact SDGM retained-hold bound.
      ret.wheelSpeeds.rl = pt_cp.vl["EBCMWheelSpdRear"]["RLWheelSpd"] * CV.KPH_TO_MS
      ret.wheelSpeeds.rr = pt_cp.vl["EBCMWheelSpdRear"]["RRWheelSpd"] * CV.KPH_TO_MS
    self.parse_wheel_speeds(ret,
      left_whl_sign * pt_cp.vl["EBCMWheelSpdFront"]["FLWheelSpd"],
      right_whl_sign * pt_cp.vl["EBCMWheelSpdFront"]["FRWheelSpd"],
      left_whl_sign * pt_cp.vl["EBCMWheelSpdRear"]["RLWheelSpd"],
      right_whl_sign * pt_cp.vl["EBCMWheelSpdRear"]["RRWheelSpd"],
    )
    # sample rear wheel speeds to match the safety which only uses the rear CAN message
    # standstill=True if ECM allows engagement with brake
    ret.standstill = abs(pt_cp.vl["EBCMWheelSpdRear"]["RLWheelSpd"]) <= STANDSTILL_THRESHOLD and \
                     abs(pt_cp.vl["EBCMWheelSpdRear"]["RRWheelSpd"]) <= STANDSTILL_THRESHOLD

    if pt_cp.vl["ECMPRDNL2"]["ManualMode"] == 1:
      ret.gearShifter = self.parse_gear_shifter("T")
    else:
      ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(pt_cp.vl["ECMPRDNL2"]["PRNDL2"], None))

    source_be_brake = (gm_control_word(self.CP) &
                       (GMSafetyFlags.ASCM_INTERCEPT | GMSafetyFlags.SDGM).value and
                       not gm_control_word(self.CP) & GMSafetyFlags.BRAKE_C9.value)
    if is_volt_cc_f1_profile(self.CP):
      ret.brakePressed = (pt_cp.vl["EBCMBrakePedalPosition"]["BrakePedalPosition"] >= 6 or
                          pt_cp.vl["ECMEngineStatus"]["BrakePressed"] != 0)
    elif is_malibu_cc_f1_profile(self.CP):
      ret.brakePressed = (pt_cp.vl["EBCMBrakePedalPosition"]["BrakePedalPosition"] >= 21 or
                          pt_cp.vl["EBCMBrakePedalPosition"]["BrakePressed"] != 0 or
                          pt_cp.vl["ECMEngineStatus"]["BrakePressed"] != 0)
    elif is_conventional_cc_pedal_profile(self.CP) and self.CP.carFingerprint == CAR.CHEVROLET_MALIBU_CC:
      ret.brakePressed = (pt_cp.vl["EBCMBrakePedalPosition"]["BrakePedalPosition"] / 0xD0 >= .10
                          if self.CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG else
                          pt_cp.vl["ECMAcceleratorPos"]["BrakePedalPos"] >= 8)
    elif volt_camera_removed:
      ret.brakePressed = pt_cp.vl["ECMEngineStatus"]["BrakePressed"] != 0
    elif is_volt_gateway_alternate_brake(self.CP):
      pedal_position = pt_cp.vl["EBCMBrakePedalPosition"]["BrakePedalPosition"]
      ret.brakePressed = pedal_position >= 6
    elif self.CP.networkLocation == NetworkLocation.fwdCamera and not source_be_brake:
      ret.brakePressed = pt_cp.vl["ECMEngineStatus"]["BrakePressed"] != 0
    else:
      # Some Volt 2016-17 have loose brake pedal push rod retainers which causes the ECM to believe
      # that the brake is being intermittently pressed without user interaction.
      # To avoid a cruise fault we need to use a conservative brake position threshold
      # https://static.nhtsa.gov/odi/tsbs/2017/MC-10137629-9999.pdf
      ret.brakePressed = pt_cp.vl["ECMAcceleratorPos"]["BrakePedalPos"] >= 8

    # Regen braking is braking
    if self.CP.transmissionType == TransmissionType.direct:
      ret.regenBraking = pt_cp.vl["EBCMRegenPaddle"]["RegenPaddle"] != 0

    ret.gasPressed = pt_cp.vl["AcceleratorPedal2"]["AcceleratorPedal2"] / 254. > 1e-5

    if (self.CP.flags & GMFlags.PEDAL_LONG.value and
        not (self.hybrid_profile is not None and not self.hybrid_profile.longitudinal) and
        not (removed_pedal and not self.CP.openpilotLongitudinalControl) and
        (self.camera_pedal_profile is None or self.camera_pedal_profile.longitudinal) and
        (self.volt_cc_pedal_profile is None or self.volt_cc_pedal_profile.longitudinal)):
      sensor = pt_cp.vl["GAS_SENSOR"]
      sensor_ts = pt_cp.ts_nanos["GAS_SENSOR"]["COUNTER_PEDAL"]
      counter = int(sensor["COUNTER_PEDAL"])
      new_sample = sensor_ts > self.pedal_sensor_ts_nanos
      counter_changed = self.pedal_sensor_counter is None or counter != self.pedal_sensor_counter
      tracks = (sensor["INTERCEPTOR_GAS"], sensor["INTERCEPTOR_GAS2"])
      sensor_bytes = self.pedal_packer.make_can_msg("GAS_SENSOR", 0, {
        "INTERCEPTOR_GAS": tracks[0], "INTERCEPTOR_GAS2": tracks[1],
        "STATE": sensor["STATE"], "COUNTER_PEDAL": counter,
      })[1]
      # The physical pedal reports independent 12-bit ADC channels.
      first = int.from_bytes(sensor_bytes[:2], "big")
      second = int.from_bytes(sensor_bytes[2:4], "big")
      if self.hybrid_profile is not None and self.hybrid_profile.pedal and self.hybrid_profile.longitudinal:
        camera_ns = (() if self.hybrid_profile.removed else
                     (cam_cp.ts_nanos["ASCMLKASteeringCmd"]["RollingCounter"],
                      cam_cp.ts_nanos["AEBCmd"]["AEBCmdActive"]))
        self.hybrid_status = HybridStatusPhysical(
          main_ns=pt_cp.ts_nanos["ECMEngineStatus"]["CruiseMainOn"],
          main_on=bool(pt_cp.vl["ECMEngineStatus"]["CruiseMainOn"]),
          brake_pressed=bool(pt_cp.vl["ECMEngineStatus"]["BrakePressed"]),
          accelerator_pressed=bool(pt_cp.vl["AcceleratorPedal2"]["AcceleratorPedal2"]),
          sensor_gas=125677 * first + 251976 * second > 198510000,
          camera_ns=camera_ns,
        )
      tracks_valid = 0 <= first <= 4095 and 0 <= second <= 4095
      checksum_valid = int(sensor["CHECKSUM_PEDAL"]) == pedal_crc(sensor_bytes)
      if new_sample:
        self.pedal_sensor_ts_nanos = sensor_ts
        self.pedal_sensor_counter = counter
        self.pedal_sensor_healthy = bool(counter_changed and sensor["STATE"] == 0 and tracks_valid and checksum_valid)
      elif sensor_ts < self.pedal_sensor_ts_nanos:
        self.pedal_sensor_healthy = False
        self.pedal_sensor_ts_nanos = 0
        self.pedal_sensor_counter = None
      if self.pedal_sensor_healthy:
        if bolt_pedal_profile:
          ret.gasPressed = first + second > 1190
        elif self.camera_pedal_profile is not None and self.camera_pedal_profile.longitudinal:
          ret.gasPressed = ret.gasPressed or first + second > 1190
        else:
          ret.gasPressed = (125677 * first + 251976 * second > 198510000 if self.volt_cc_pedal_profile is not None else sum(tracks) / 2. > 23.0)

    ret.steeringAngleDeg = pt_cp.vl["PSCMSteeringAngle"]["SteeringWheelAngle"]
    ret.steeringRateDeg = pt_cp.vl["PSCMSteeringAngle"]["SteeringWheelRate"]
    ret.steeringTorque = pt_cp.vl["PSCMStatus"]["LKADriverAppldTrq"]
    ret.steeringTorqueEps = pt_cp.vl["PSCMStatus"]["LKATorqueDelivered"]
    ret.steeringPressed = abs(ret.steeringTorque) > STEER_THRESHOLD

    # 0 inactive, 1 active, 2 temporarily limited, 3 failed
    self.lkas_status = pt_cp.vl["PSCMStatus"]["LKATorqueDeliveredStatus"]
    pending = self.bolt_steering_pending
    receipt_timeout = pending is not None and pt_cp._last_update_nanos - pending[1] > STEERING_COMMAND_TIMEOUT_NS
    ret.steerFaultTemporary = self.lkas_status == 2 or receipt_timeout
    ret.steerFaultPermanent = self.lkas_status == 3

    # 1 - open, 0 - closed
    ret.doorOpen = (pt_cp.vl["BCMDoorBeltStatus"]["FrontLeftDoor"] == 1 or
                    pt_cp.vl["BCMDoorBeltStatus"]["FrontRightDoor"] == 1 or
                    pt_cp.vl["BCMDoorBeltStatus"]["RearLeftDoor"] == 1 or
                    pt_cp.vl["BCMDoorBeltStatus"]["RearRightDoor"] == 1)

    # 1 - latched
    ret.seatbeltUnlatched = pt_cp.vl["BCMDoorBeltStatus"]["LeftSeatBelt"] == 0
    if int(self.CP.safetyConfigs[0].safetyParam) == 0x1CD and bolt_pedal_profile:
      self.bolt_acc_brake_clock_ns = cam_cp.ts_nanos["EBCMFrictionBrakeCmd"]["RollingCounter"]
      self.bolt_acc_brake_clock_counter = int(cam_cp.vl["EBCMFrictionBrakeCmd"]["RollingCounter"])

    ret.leftBlinker = pt_cp.vl["BCMTurnSignals"]["TurnSignals"] == 1
    ret.rightBlinker = pt_cp.vl["BCMTurnSignals"]["TurnSignals"] == 2

    ret.parkingBrake = pt_cp.vl["BCMGeneralPlatformStatus"]["ParkBrakeSwActive"] == 1
    ret.cruiseState.available = pt_cp.vl["ECMEngineStatus"]["CruiseMainOn"] != 0
    ret.espDisabled = pt_cp.vl["ESPStatus"]["TractionControlOn"] != 1
    acc_state = pt_cp.vl["AcceleratorPedal2"]["CruiseState"]
    friction_unavailable = pt_cp.vl["EBCMFrictionBrakeStatus"]["FrictionBrakeUnavailable"] == 1
    self.bolt_acc_pedal_friction_unavailable = self.bolt_acc_pedal_secondary_friction and friction_unavailable
    # This exact interceptor owner drives through pedal/regen; friction is secondary.
    # Retain the ECU bit for controller withdrawal and never hide a stock ACC fault.
    secondary_only = self.bolt_acc_pedal_secondary_friction and acc_state == AccState.OFF
    ret.accFaulted = acc_state == AccState.FAULTED or (friction_unavailable and not secondary_only)

    ret.cruiseState.enabled = pt_cp.vl["AcceleratorPedal2"]["CruiseState"] != AccState.OFF
    if volt_camera_removed:
      # A missing camera can leave the ECU faulted; that is not engaged cruise.
      ret.cruiseState.enabled &= pt_cp.vl["AcceleratorPedal2"]["CruiseState"] != AccState.FAULTED
    ret.cruiseState.standstill = pt_cp.vl["AcceleratorPedal2"]["CruiseState"] == AccState.STANDSTILL
    if self.flashed_volt_profile:
      # There is no stock cruise/ACC controller. Preserve the actual EBCM fault.
      ret.accFaulted = friction_unavailable
      ret.brakePressed |= bool(pt_cp.vl["ECMEngineStatus"]["BrakePressed"])
      ret.cruiseState.enabled = False
      ret.cruiseState.standstill = False
    if (self.CP.carFingerprint in CC_GATEWAY_STOCK_CAR or self.CP.carFingerprint == CAR.CHEVROLET_VOLT_CC):
      # AcceleratorPedal2 CruiseState is an ACC state, not a conventional-cruise fault source.
      ret.accFaulted = False
      ret.cruiseState.enabled = pt_cp.vl["ECMCruiseControl"]["CruiseActive"] != 0
      if not is_conventional_cc_pedal_profile(self.CP):
        ret.cruiseState.standstill = False
      ret.cruiseState.speed = pt_cp.vl["ECMCruiseControl"]["CruiseSetSpeed"] * CV.KPH_TO_MS
      ret.cruiseState.nonAdaptive = not (is_silverado_cc_stock_profile(self.CP) or
                                         is_ordinary_cc_profile(self.CP) or is_conventional_cc_pedal_profile(self.CP) or
                                         is_volt_cc_profile(self.CP))
    if self.hybrid_profile is not None:
      ret.accFaulted = False
      ret.cruiseState.enabled = bool(pt_cp.vl["ECMCruiseControl"]["CruiseActive"])
      ret.cruiseState.speed = pt_cp.vl["ECMCruiseControl"]["CruiseSetSpeed"] * CV.KPH_TO_MS
      ret.cruiseState.nonAdaptive = False
      if not self.hybrid_profile.removed:
        ret.stockAeb = cam_cp.vl["AEBCmd"]["AEBCmdActive"] != 0
    if self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and self.CP.flags & GMFlags.PEDAL_LONG.value:
      self.stock_acc_status_ts_nanos = pt_cp.ts_nanos["AcceleratorPedal2"]["CruiseState"]
    if present_no_acc_pedal:
      self.bolt_pedal_stock_active = bool(pt_cp.vl["ECMCruiseControl"]["CruiseActive"])
      self.bolt_pedal_stock_ts_nanos = pt_cp.ts_nanos["ECMCruiseControl"]["CruiseActive"]
    if self.CP.carFingerprint in NO_ACC_BOLT_CAR and not self.bolt_cc_profile:
      ret.accFaulted = False
      ret.cruiseState.enabled = pt_cp.vl["ECMCruiseControl"]["CruiseActive"] != 0 if pedal_stock_no_acc else False
      if bolt_pedal_profile:
        self.bolt_pedal_standstill_ts_nanos = pt_cp.ts_nanos["AcceleratorPedal2"]["CruiseState"]
        stamp = self.bolt_pedal_standstill_ts_nanos
        ret.cruiseState.standstill = (0 < stamp <= pt_cp._last_update_nanos and
                                     pt_cp._last_update_nanos - stamp <= 300_000_000 and
                                     pt_cp.vl["AcceleratorPedal2"]["CruiseState"] == AccState.STANDSTILL)
      else:
        ret.cruiseState.standstill = False
    if (self.hybrid_profile is None and not removed_pedal and self.volt_cc_pedal_profile is None and self.CP.networkLocation == NetworkLocation.fwdCamera
        and not volt_camera_removed
        and not is_conventional_cc_pedal_profile(self.CP) and not is_ordinary_camera_removed(self.CP)
        and not (self.camera_pedal_profile is not None and self.camera_pedal_profile.removed)):
      if (self.CP.carFingerprint not in ALT_ACCS or self.camera_pedal_profile is not None or is_ordinary_camera_profile(self.CP) or
          is_ordinary_camera_profile(self.CP, longitudinal=True)) and not self.bolt_cc_profile and self.CP.carFingerprint not in NO_ACC_BOLT_CAR:
        ret.cruiseState.speed = cam_cp.vl["ASCMActiveCruiseControlStatus"]["ACCSpeedSetpoint"] * CV.KPH_TO_MS
        # This FCW signal only works for SDGM cars. CAM cars send FCW on GMLAN but this bit is always 0 for them
        ret.stockFcw = cam_cp.vl["ASCMActiveCruiseControlStatus"]["FCWAlert"] != 0
      else:
        ret.cruiseState.speed = pt_cp.vl["ECMCruiseControl"]["CruiseSetSpeed"] * CV.KPH_TO_MS
      if (self.CP.pcmCruise and self.CP.carFingerprint not in NO_ACC_BOLT_CAR and self.CP.carFingerprint not in ASCM_INTERCEPT_CAR and
          (self.CP.carFingerprint not in ALT_ACCS or self.camera_pedal_profile is not None or is_ordinary_camera_profile(self.CP) or
               self.CP.carFingerprint == CAR.CHEVROLET_SUBURBAN_CAMERA)):
        # The alternate set-speed source still uses camera ACC state.
        ret.cruiseState.nonAdaptive = cam_cp.vl["ASCMActiveCruiseControlStatus"]["ACCCruiseState"] not in (2, 3)

      if self.CP.carFingerprint not in SDGM_CAR and not self.bolt_cc_removed:
        ret.stockAeb = cam_cp.vl["AEBCmd"]["AEBCmdActive"] != 0

    if removed_pedal:
      ret.cruiseState.speed = pt_cp.vl["ECMCruiseControl"]["CruiseSetSpeed"] * CV.KPH_TO_MS
      if self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL or not self.CP.openpilotLongitudinalControl:
        ret.cruiseState.enabled = bool(pt_cp.vl["ECMCruiseControl"]["CruiseActive"])

    if pedal_stock_no_acc:
      ret.cruiseState.speed = pt_cp.vl["ECMCruiseControl"]["CruiseSetSpeed"] * CV.KPH_TO_MS
      ret.cruiseState.nonAdaptive = False
      ret.accFaulted = False

    if (self.CP.carFingerprint in (ORDINARY_ASCM_CAR | ORDINARY_SDGM_CAR) or
        is_ordinary_camera_profile(self.CP) or is_ordinary_camera_profile(self.CP, longitudinal=True) or
        self.CP.carFingerprint in (CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_2019)) and not volt_camera_removed and \
        not is_ordinary_camera_removed(self.CP):
      self.stock_fcw_alert = int(cam_cp.vl["ASCMActiveCruiseControlStatus"]["FCWAlert"]) & 0x3
      ret.stockFcw = self.stock_fcw_alert != 0

    if (self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and
        bolt_pedal_profile and not removed_pedal):
      self.stock_fcw_alert = int(cam_cp.vl["ASCMActiveCruiseControlStatus"]["FCWAlert"]) & 0x3
      ret.stockFcw = self.stock_fcw_alert != 0

    if self.volt_cc_pedal_profile is not None:
      ret.cruiseState.nonAdaptive = False
      ret.brakePressed = bool(pt_cp.vl["ECMEngineStatus"]["BrakePressed"])
      ret.accFaulted = False  # ACC status is not a conventional-cruise fault source.
      ret.cruiseState.enabled = bool(pt_cp.vl["ECMCruiseControl"]["CruiseActive"])
      ret.cruiseState.speed = pt_cp.vl["ECMCruiseControl"]["CruiseSetSpeed"] * CV.KPH_TO_MS
      ret.cruiseState.standstill = pt_cp.vl["AcceleratorPedal2"]["CruiseState"] == AccState.STANDSTILL
      names = (("PSCMStatus", "LKATorqueDelivered", 300_000_000),
               ("ECMEngineStatus", "CruiseMainOn", 300_000_000),
               ("ECMCruiseControl", "CruiseActive", 300_000_000),
               ("AcceleratorPedal2", "CruiseState", 300_000_000),
               ("EBCMWheelSpdRear", "RLWheelSpd", 100_000_000),
               ("EBCMRegenPaddle", "RegenPaddle", 100_000_000),
               ("ECMAcceleratorPos" if self.volt_cc_pedal_profile.brake_source.value == "BE" else "EBCMBrakePedalPosition",
                "BrakePedalPos" if self.volt_cc_pedal_profile.brake_source.value == "BE" else "BrakePedalPosition", 300_000_000),
               ("ASCMSteeringButton", "RollingCounter", 300_000_000))
      if self.volt_cc_pedal_profile.longitudinal:
        names += (("ECMPRDNL2", "PRNDL2", 1_000_000_000),)
      self.volt_cc_pedal_sources = tuple((pt_cp.ts_nanos[n][f], limit) for n, f, limit in names)
    if self.bolt_cc_profile:
      ret.brakePressed = bool(pt_cp.vl["ECMEngineStatus"]["BrakePressed"])
      ret.accFaulted = False
      ret.cruiseState.enabled = (cam_cp.vl["ASCMActiveCruiseControlStatus"]["ACCCmdActive"] != 0
                                 if (self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and
                                     self.CP.safetyConfigs[0].safetyParam == 0xC140) else
                                 pt_cp.vl["ECMCruiseControl"]["CruiseActive"] != 0)
      ret.cruiseState.standstill = (pt_cp.vl["AcceleratorPedal2"]["CruiseState"] == AccState.STANDSTILL
                                    if self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL else False)
      if (self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and
          self.CP.safetyConfigs[0].safetyParam == 0xC140):
        ret.stockFcw = cam_cp.vl["ASCMActiveCruiseControlStatus"]["FCWAlert"] != 0
      ret.cruiseState.speed = pt_cp.vl["ECMCruiseControl"]["CruiseSetSpeed"] * CV.KPH_TO_MS
      ret.cruiseState.nonAdaptive = False

    if self.CP.flags & GMFlags.HAS_BSM.value:
      ret.leftBlindspot = pt_cp.vl["BCMBlindSpotMonitor"]["LeftBSM"] == 1
      ret.rightBlindspot = pt_cp.vl["BCMBlindSpotMonitor"]["RightBSM"] == 1

    # Don't add event if transitioning from INIT, unless it's to an actual button
    if self.cruise_buttons != CruiseButtons.UNPRESS or prev_cruise_buttons != CruiseButtons.INIT:
      ret.buttonEvents = [
        *create_button_events(self.cruise_buttons, prev_cruise_buttons, BUTTONS_DICT,
                              unpressed_btn=CruiseButtons.UNPRESS),
        *create_button_events(self.distance_button, prev_distance_button,
                              {1: ButtonType.gapAdjustCruise})
      ]

    if self.hybrid_profile is not None:
      now = pt_cp._last_update_nanos
      healthy = (pt_cp.can_valid and not pt_cp.bus_timeout and
                 (self.hybrid_profile.removed or cam_cp.can_valid and not cam_cp.bus_timeout) and
                 ret.cruiseState.available and
                 ret.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low,
                                     structs.CarState.GearShifter.manumatic) and
                 not ret.steerFaultTemporary and not ret.steerFaultPermanent and
                 all(stamp > 0 and 0 <= now - stamp <= limit for stamp, limit in self.hybrid_sources))
      if self.hybrid_profile.pedal and self.hybrid_profile.longitudinal:
        healthy = (healthy and self.pedal_sensor_healthy and
                   0 < self.pedal_sensor_ts_nanos <= now <= self.pedal_sensor_ts_nanos + 100_000_000)
      _, semantic, edges = self.hybrid_buttons.update(
        healthy, enable_ready=healthy and not ret.brakePressed and not ret.gasPressed and not ret.regenBraking)
      from opendbc.car.gm.hybrid_cc import HybridPhysical
      self.volt_cc_physical = HybridPhysical(now, tuple(stamp for stamp, _ in self.hybrid_sources),
                                            self.hybrid_buttons.slot.credit_ns)
      self.cruise_buttons = CruiseButtons.INIT if semantic is None else semantic
      ret.buttonEvents = [event for old, new in edges for event in
                          create_button_events(new, old, BUTTONS_DICT, unpressed_btn=CruiseButtons.UNPRESS)] + create_button_events(
                            self.distance_button, prev_distance_button, {1: ButtonType.gapAdjustCruise})
    if self.flashed_volt_profile:
      lkas_events = []
      for pressed in pt_cp.vl_all["ASCMSteeringButton"]["LKAButton"]:
        if self.flashed_lkas_button is not None:
          lkas_events.extend(create_button_events(pressed, self.flashed_lkas_button, {1: ButtonType.lkas}))
        self.flashed_lkas_button = pressed
      if lkas_events:
        ret.buttonEvents = [event.to_dict() for event in ret.buttonEvents] + lkas_events

    if ret.vEgo < self.CP.minSteerSpeed:
      ret.lowSpeedAlert = True

    hold_sources_current = ((self.camera_pedal_profile is None or not self.camera_pedal_profile.volt or self.pedal_sensor_healthy) and
                            bool(self.gm_auto_hold_sources) and
                            all(0 < stamp <= pt_cp._last_update_nanos and pt_cp._last_update_nanos - stamp <= limit
                                for stamp, limit in self.gm_auto_hold_sources))
    hold_config = self.gm_auto_hold_config
    hold_stopped = ret.standstill or (hold_config.continued_stop_speed > .02 and
                                     stopped_for_hold(ret, hold_config, self.gm_auto_hold_engaged))
    hold_current = (gm_auto_hold and pt_cp.can_valid and not pt_cp.bus_timeout and
                    (not requires_camera_state_sources(self.CP) or cam_cp.can_valid and not cam_cp.bus_timeout) and
                    hold_sources_current and not self.gm_auto_hold_unavailable and self.gm_auto_hold_forward and
                    ret.cruiseState.available and hold_stopped and
                    not ret.gasPressed and not ret.regenBraking)
    if volt_one_pedal and (
        self.camera_pedal_profile is not None and self.camera_pedal_profile.volt and not self.camera_pedal_profile.auto_hold or
        self.camera_pedal_profile is None and int(self.CP.safetyConfigs[0].safetyParam) < 0xD110):
      hold_current = hold_current and self.volt_one_pedal_mode and self.volt_one_pedal_stopped and not ret.brakePressed
    if not hold_current:
      self.gm_auto_hold_engaged = False
    ret.brakeHoldActive = bool(hold_current and self.gm_auto_hold_engaged and ret.standstill)

    self.car_gps_tracker.update(pt_cp, speed=ret.vEgo, backward=(left_whl_sign < 0 or right_whl_sign < 0 or
                                ret.gearShifter == structs.CarState.GearShifter.reverse))
    return ret

  @staticmethod
  def get_can_parsers(CP):
    bolt_pedal_profile = is_bolt_pedal_profile(CP) or is_bolt_pedal_profile(CP, stock_only=True)
    pt_messages = []
    flashed_volt = is_volt_flashed_profile(CP)
    if (not flashed_volt and is_volt_gateway_profile(CP) and not CP.openpilotLongitudinalControl and
        camera_acc_pedal_profile(CP) is None):
      pt_messages += [("PSCMStatus", 10), ("EBCMWheelSpdRear", 10), ("ASCMSteeringButton", 10),
                      ("AcceleratorPedal2", 10), ("ECMEngineStatus", 10), ("EBCMRegenPaddle", 40)]
      if not is_volt_gateway_alternate_brake(CP):
        pt_messages.append(("ECMAcceleratorPos", 10))
    if is_volt_gateway_alternate_brake(CP):
      pt_messages.append(("EBCMBrakePedalPosition", 100))
    if is_volt_one_pedal(CP):
      pt_messages.append(("EVDriveMode", float('nan')))
    if flashed_volt:
      pt_messages += [("PSCMStatus", 10), ("ESPStatus", 10), ("EBCMWheelSpdFront", 20),
                      ("EBCMWheelSpdRear", 20), ("EBCMFrictionBrakeStatus", 20), ("PSCMSteeringAngle", 100),
                      ("ECMPRDNL2", 10), ("AcceleratorPedal2", 33), ("ECMEngineStatus", 100),
                      ("BCMTurnSignals", 1), ("BCMDoorBeltStatus", 10), ("BCMGeneralPlatformStatus", 10),
                      ("ASCMSteeringButton", 33), ("EBCMRegenPaddle", 40)]
    if CP.carFingerprint in VOLT_BSM_CAR and CP.flags & GMFlags.HAS_BSM.value:
      pt_messages.append(("BCMBlindSpotMonitor", float('nan')))
    if CP.flags & GMFlags.PEDAL_LONG.value and not is_bolt_pedal_removed_profile(CP, stock_only=True):
      pt_messages.append(("GAS_SENSOR", 50))
      if CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and not bolt_pedal_profile:
        pt_messages.append(("AcceleratorPedal2", 10))
    if (not (is_bolt_pedal_removed_profile(CP) or is_bolt_pedal_removed_profile(CP, stock_only=True)) and
        CP.networkLocation == NetworkLocation.fwdCamera and (not is_conventional_cc_pedal_profile(CP) or is_silverado_cc_pedal_profile(CP))):
      pt_messages += [
        ("ASCMLKASteeringCmd", float('nan')),
      ]
    if CP.carFingerprint in ORDINARY_SDGM_CAR:
      # Frozen SDGM PT subscriptions: steering, vehicle state, stock ACC and
      # driver input are required; the PT camera command remains optional.
      pt_messages += [
        ("PSCMStatus", 10), ("ESPStatus", 10),
        ("EBCMWheelSpdFront", 20), ("EBCMWheelSpdRear", 20),
        ("EBCMFrictionBrakeStatus", 20), ("PSCMSteeringAngle", 100),
        ("ECMPRDNL2", 10), ("AcceleratorPedal2", 33),
        ("ECMEngineStatus", 100), ("BCMTurnSignals", 1),
        ("BCMDoorBeltStatus", 10), ("BCMGeneralPlatformStatus", 10),
        ("ASCMSteeringButton", 33),
      ]
      if not gm_control_word(CP) & GMSafetyFlags.BRAKE_C9.value:
        pt_messages.append(("ECMAcceleratorPos", 80))
    if (CP.carFingerprint in CC_GATEWAY_STOCK_CAR or CP.carFingerprint == CAR.CHEVROLET_VOLT_CC):
      # No camera or ACC status dependency on this gateway conventional-cruise path.
      pt_messages += [
        ("PSCMStatus", 10), ("ESPStatus", 10),
        ("EBCMWheelSpdFront", 20), ("EBCMWheelSpdRear", 20),
        ("EBCMFrictionBrakeStatus", 20), ("PSCMSteeringAngle", 100),
        ("ECMPRDNL2", 10), ("AcceleratorPedal2", 33),
        ("ECMEngineStatus", 100), ("BCMTurnSignals", 1),
        ("BCMDoorBeltStatus", 10), ("BCMGeneralPlatformStatus", 10),
        ("ASCMSteeringButton", 33), ("ECMAcceleratorPos", 80),
        ("ECMCruiseControl", 10),
      ]
    if (requires_camera_state_sources(CP) or is_volt_camera_removed(CP) or camera_acc_pedal_profile(CP) is not None or
        bolt_pedal_profile) and CP.carFingerprint not in ORDINARY_SDGM_CAR:
      # Required stock signals are checked on their observed PT bus; the
      # camera command on PT is only a counter source and remains optional.
      pt_messages += [
        ("PSCMStatus", 10), ("ESPStatus", 10),
        ("EBCMWheelSpdFront", 20), ("EBCMWheelSpdRear", 20),
        ("EBCMFrictionBrakeStatus", 20), ("PSCMSteeringAngle", 100),
        ("ECMAcceleratorPos", 80), ("ECMPRDNL2", 10 if CP.carFingerprint == CAR.CHEVROLET_VOLT_CAMERA else 40),
        ("AcceleratorPedal2", 33), ("ECMEngineStatus", 100),
        ("BCMTurnSignals", 1), ("BCMDoorBeltStatus", 10),
        ("BCMGeneralPlatformStatus", 10), ("ASCMSteeringButton", 33),
      ]
      if CP.transmissionType == TransmissionType.direct:
        pt_messages.append(("EBCMRegenPaddle", 50 if CP.carFingerprint == CAR.CHEVROLET_VOLT_CAMERA else 40))
      if CP.carFingerprint in ALT_ACCS or bolt_pedal_profile:
        pt_messages.append(("ECMCruiseControl", 10))

    if is_ordinary_camera_profile(CP, longitudinal=CP.openpilotLongitudinalControl):
      pt_messages.append(("EBCMBrakePedalPosition", 10))

    if (is_volt_camera_removed(CP) or is_ordinary_camera_profile(CP, longitudinal=CP.openpilotLongitudinalControl)) and \
        CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG:
      pt_messages = [(name, frequency) for name, frequency in pt_messages if name != "ECMAcceleratorPos"]
      pt_messages = [(name, frequency) for name, frequency in pt_messages if name != "EBCMBrakePedalPosition"]
      pt_messages.append(("EBCMBrakePedalPosition", 100))

    if CP.carFingerprint == CAR.CHEVROLET_VOLT_CC:
      pt_messages.append(("EBCMRegenPaddle", 50))
      if is_volt_cc_f1_profile(CP):
        pt_messages = [(name, frequency) for name, frequency in pt_messages if name != "ECMAcceleratorPos"]
        pt_messages.append(("EBCMBrakePedalPosition", 100))

    if is_bolt_cc_profile(CP):
      pt_messages += [("PSCMStatus", 10), ("ECMCruiseControl", 10), ("ASCMSteeringButton", 33),
                      ("ECMEngineStatus", 100), ("AcceleratorPedal2", 33), ("ECMPRDNL2", 10),
                      ("EBCMWheelSpdRear", 20), ("EBCMRegenPaddle", 40)]

    if CP.carFingerprint == CAR.CHEVROLET_VOLT_2019 and gm_control_word(CP) & GMSafetyFlags.BRAKE_C9.value:
      pt_messages = [(name, frequency) for name, frequency in pt_messages if name != "ECMAcceleratorPos"]

    if is_conventional_cc_pedal_profile(CP):
      pt_messages = [(name, frequency) for name, frequency in pt_messages
                     if name not in ("EBCMBrakePedalPosition", "ECMCruiseControl", "ECMPRDNL2")]
      pt_messages += [("EBCMBrakePedalPosition", 100 if CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG else 10),
                      ("ECMCruiseControl", 10), ("ECMPRDNL2", 40)]
      if CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG:
        pt_messages = [(name, frequency) for name, frequency in pt_messages if name != "ECMAcceleratorPos"]

    if is_silverado_cc_pedal_profile(CP) and not CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG:
      pt_messages = [(name, frequency) for name, frequency in pt_messages if name != "EBCMBrakePedalPosition"]

    if is_malibu_cc_f1_profile(CP):
      pt_messages = [(name, frequency) for name, frequency in pt_messages if name != "ECMAcceleratorPos"]
      pt_messages.append(("EBCMBrakePedalPosition", 100))

    if is_ordinary_camera_removed(CP):
      pt_messages = [(name, frequency) for name, frequency in pt_messages if name != "ECMCruiseControl"]

    cc_profile = volt_cc_pedal_profile(CP)
    if cc_profile is not None:
      excluded = {"ECMAcceleratorPos", "EBCMBrakePedalPosition", "ECMCruiseControl", "ECMPRDNL2", "GAS_SENSOR"}
      if cc_profile.removed:
        excluded.add("ASCMLKASteeringCmd")
      pt_messages = [(n, rate) for n, rate in pt_messages if n not in excluded]
      pt_messages += [("ECMAcceleratorPos" if cc_profile.brake_source.value == "BE" else "EBCMBrakePedalPosition", 100),
                      ("ECMCruiseControl", 10), ("ECMPRDNL2", 10 if cc_profile.longitudinal else float("nan"))]
      if cc_profile.longitudinal:
        pt_messages.append(("GAS_SENSOR", 50))
    profile = camera_acc_pedal_profile(CP)
    if profile is not None:
      excluded = {"ECMAcceleratorPos", "EBCMBrakePedalPosition", "ECMCruiseControl", "ECMPRDNL2", "GAS_SENSOR"}
      if profile.removed:
        excluded.add("ASCMLKASteeringCmd")
      pt_messages = [(name, frequency) for name, frequency in pt_messages if name not in excluded]
      pt_messages += [("ECMAcceleratorPos" if profile.brake_source == BrakeSource.BE else "EBCMBrakePedalPosition", 100),
                      ("ECMPRDNL2", (10 if profile.topology in ("gateway", "ascm", "sdgm") else 40) if profile.longitudinal else float("nan"))]
      if profile.longitudinal:
        pt_messages.append(("GAS_SENSOR", 50))

    hybrid = malibu_hybrid_profile(CP)
    if hybrid is not None:
      pt_messages = [("PSCMStatus", 10), ("ESPStatus", 10), ("EBCMWheelSpdFront", 20),
                     ("EBCMWheelSpdRear", 20), ("EBCMFrictionBrakeStatus", 20), ("PSCMSteeringAngle", 100),
                     ("ECMPRDNL2", 10), ("AcceleratorPedal2", 33), ("ECMEngineStatus", 100),
                     ("BCMTurnSignals", 1), ("BCMDoorBeltStatus", 10), ("BCMGeneralPlatformStatus", 10),
                     ("ASCMSteeringButton", 33), ("ECMCruiseControl", 10), ("EBCMRegenPaddle", 50),
                     ("EBCMBrakePedalPosition", 100) if CP.flags & GMFlags.NO_ACCELERATOR_POS_MSG else ("ECMAcceleratorPos", 80)]
      if hybrid.pedal and hybrid.longitudinal:
        pt_messages.append(("GAS_SENSOR", 50))
      if not hybrid.removed:
        pt_messages.append(("ASCMLKASteeringCmd", float("nan")))
    loopback_messages = [
      ("ASCMLKASteeringCmd", float('nan')),
    ]

    cam_messages = []
    if CP.carFingerprint in ASCM_INTERCEPT_CAR:
      # These camera messages are required except OEM AEB, which is not reliable at startup.
      cam_messages = [("ASCMLKASteeringCmd", 10), ("AEBCmd", float('nan')),
                      ("ASCMActiveCruiseControlStatus", 25)]
    elif CP.carFingerprint in ORDINARY_SDGM_CAR:
      cam_messages = [("ASCMLKASteeringCmd", 10), ("ASCMActiveCruiseControlStatus", 25)]
    elif requires_camera_state_sources(CP):
      cam_messages = [("ASCMLKASteeringCmd", 10), ("ASCMActiveCruiseControlStatus", 25)]
      if CP.carFingerprint != CAR.CHEVROLET_VOLT_2019:
        cam_messages.append(("AEBCmd", 10))

    if is_bolt_cc_profile(CP):
      removed = CP.safetyConfigs[0].safetyParam == BOLT_CC_WORDS[CP.carFingerprint][1]
      cam_messages = [] if removed else [("ASCMLKASteeringCmd", 10), ("AEBCmd", 10)]
      if CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and not removed:
        cam_messages.append(("ASCMActiveCruiseControlStatus", 25))

    if bolt_pedal_profile:
      removed = is_bolt_pedal_removed_profile(CP) or is_bolt_pedal_removed_profile(CP, stock_only=True)
      cam_messages = [] if removed else [("ASCMLKASteeringCmd", 10), ("AEBCmd", 10)]
      if CP.carFingerprint not in NO_ACC_BOLT_CAR and not removed:
        cam_messages.append(("ASCMActiveCruiseControlStatus", 25))

    if is_conventional_cc_pedal_profile(CP):
      cam_messages = [] if CP.flags & GMFlags.NO_CAMERA else [("ASCMLKASteeringCmd", 10), ("AEBCmd", 10)]

    if is_ordinary_camera_removed(CP):
      cam_messages = []

    if profile is not None:
      cam_messages = [] if profile.removed else [
        ("ASCMLKASteeringCmd", 10), ("ASCMActiveCruiseControlStatus", 25),
        *(([("AEBCmd", float("nan") if profile.topology == "ascm" else 10)]) if profile.topology != "sdgm" else []),
      ]

    if cc_profile is not None:
      cam_messages = [] if cc_profile.removed else [("ASCMLKASteeringCmd", 10), ("AEBCmd", 10)]
    if is_bolt_pedal_removed_profile(CP) or is_bolt_pedal_removed_profile(CP, stock_only=True):
      pt_messages = [(name, frequency) for name, frequency in pt_messages if name != "ASCMLKASteeringCmd"]
    if hybrid is not None:
      cam_messages = [] if hybrid.removed else [("ASCMLKASteeringCmd", 10), ("AEBCmd", 10)]

    if int(CP.safetyConfigs[0].safetyParam) == 0x1CD and is_bolt_pedal_profile(CP):
      cam_messages.append(("EBCMFrictionBrakeCmd", 25))

    gps = get_car_gps_config(CP)
    if gps is not None:
      pt_messages += [(name, float("nan")) for name in gps.messages]

    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], pt_messages, 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], cam_messages, 2),
      Bus.loopback: CANParser(DBC[CP.carFingerprint][Bus.pt], loopback_messages, 128),
    }
