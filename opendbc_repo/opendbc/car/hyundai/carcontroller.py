from opendbc.car.hyundai.ioniq5pe_long_profile import qualified as pe_long_qualified
from opendbc.car.hyundai.ioniq5pe_longitudinal import PELongitudinalPolicy
from opendbc.car.hyundai import ioniq5pe_long_sender
from opendbc.car.hyundai.canfd_angle_aol import qualified as qualified_angle_aol, command_allowed as angle_aol_command_allowed
from opendbc.car.hyundai.ev9_longitudinal import (
  EV9LongitudinalPolicy,
  LegacyAngleEnvelope,
  lateral_request_allowed,
  qualified as ev9_long_qualified,
  update_blindspot_warning,
  BlindspotWarningOutput,
)
from opendbc.car.hyundai import ev9_long_sender
from opendbc.car.hyundai.ev9_long_aol import qualified as ev9_long_aol_qualified
from opendbc.car.hyundai.pe_long_aol import qualified as pe_long_aol_qualified
from opendbc.car.common.filter_simple import FirstOrderFilter
from opendbc.car.lateral import get_max_angle_vm, get_max_angle_delta_vm
import numpy as np
from opendbc.can import CANPacker
from opendbc.car import Bus, DT_CTRL, make_tester_present_msg, rate_limit, structs
from opendbc.car.lateral import apply_driver_steer_torque_limits, apply_steer_angle_limits_vm, common_fault_avoidance
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.hyundai import hyundaicanfd, hyundaican
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.ray_pedal import create_ray_pedal_command, ray_pedal_gas, ray_pedal_enabled
from opendbc.car.hyundai.ioniq6_longitudinal import Ioniq6LongitudinalPolicy
from opendbc.car.hyundai.ioniq6_bsm import BlindspotStatus
from opendbc.car.hyundai.canfd_dash_icons import CanFDDashIcons
from opendbc.car.hyundai.classic_scc_aol import qualified as qualified_classic_stock_aol
from opendbc.car.hyundai.classic_long_aol import AOL_EXPERIENCE, qualified as qualified_classic_long_aol
from opendbc.car.hyundai.canfd_stock_aol import qualified as qualified_canfd_stock_aol, qualified_long as qualified_canfd_long_aol
from opendbc.car.hyundai.ev6_aol import qualified as qualified_ev6_aol
from opendbc.car.hyundai.g90_longitudinal import G90LongitudinalPolicy
from opendbc.car.hyundai.gv70_longitudinal import is_gv70, scc_request, suppress_stock_cancel
from opendbc.car.hyundai.g90_lead import G90LeadState, eligible as g90_lead_eligible
from opendbc.car.hyundai.gv70_camera_lead import eligible as gv70_lead_eligible
from opendbc.car.hyundai.canfd_lead import select_lead, ABSENT
from opendbc.car.hyundai.torque_ev_scc import eligible as torque_ev_scc_eligible, options as torque_ev_scc_options, clock_boot_ns, fallback_lead
from opendbc.car.hyundai.ccnc_ev_stock import replacement_requested, qualified as qualified_ccnc_ev_stock
from opendbc.car.hyundai.angle_tx_recovery import AngleTxRecovery
from opendbc.car.hyundai.values import is_blended, HyundaiFlags, HyundaiSafetyFlags, Buttons, CarControllerParams, CAR
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.vehicle_model import VehicleModel

EV9_OP_LONG_ANGLE_TARGET_MAX = 140.0

VisualAlert = structs.CarControl.HUDControl.VisualAlert
LongCtrlState = structs.CarControl.Actuators.LongControlState

# EPS faults if you apply torque while the steering angle is above 90 degrees for more than 1 second
# All slightly below EPS thresholds to avoid fault
MAX_ANGLE = 85
MAX_ANGLE_FRAMES = 89
MAX_ANGLE_CONSECUTIVE_FRAMES = 2

# On some HKG CAN and CAN FD non-CANFD_ALT_BUTTONS, the cancel button (CF_Clu_CruiseSwState / CRUISE_BUTTONS = 4) is
# a pause/resume toggle, not a dedicated cancel. Firing it mid-brake inadvertently can cause a re-enable attempt
# and triggers the "SCC Conditions Not Met" alert. Delaying the button send lets factory SCC disengage
# naturally on brake press. We send ~100 ms later if it fails to do so, or if we want to cancel for another reason.
CANCEL_BUTTON_DELAY_FRAMES = 10


def angle_torque_reduction_gain(steering_torque, v_ego, lat_active, last_gain):
  # The frozen ADAS angle protocol scales driver torque into a reduction gain.
  if lat_active:
    ceiling = np.interp(v_ego, [0.5, 1.5], [1.0, 0.85])
    shelf = np.interp(v_ego, [2.0, 11.0], [0.45, 0.6])
    floor = np.interp(v_ego, [2.0, 22.0], [0.1, 0.3])
    bp1 = np.interp(v_ego, [2.0, 11.0], [75.0, 125.0])
    bp2 = np.interp(v_ego, [2.0, 11.0], [125.0, 150.0])
    bp3 = np.interp(v_ego, [2.0, 11.0], [175.0, 275.0])
    bp4 = np.interp(v_ego, [2.0, 22.0], [400.0, 700.0])
    target = np.interp(abs(steering_torque), [bp1, bp2, bp3, bp4], [ceiling, shelf, shelf, floor])
  else:
    return 0.0
  return round(rate_limit(target, last_gain, -0.014, 0.004) / 0.004) * 0.004


def classic_aol_dash_enabled(cp):
  return bool(cp.carFingerprint != CAR.KIA_RAY_EV and (qualified_classic_stock_aol(cp, marked_only=True) or
    cp.alternativeExperience == AOL_EXPERIENCE and qualified_classic_long_aol(cp, marked_only=True)))


def process_hud_alert(enabled, fingerprint, hud_control):
  sys_warning = (hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw))

  # initialize to no line visible
  # TODO: this is not accurate for all cars
  sys_state = 1
  if hud_control.leftLaneVisible and hud_control.rightLaneVisible or sys_warning:
    # HUD alert only display when LKAS status is active
    sys_state = 3 if enabled or sys_warning else 4
  elif hud_control.leftLaneVisible:
    sys_state = 5
  elif hud_control.rightLaneVisible:
    sys_state = 6

  # initialize to no warnings
  left_lane_warning = 0
  right_lane_warning = 0
  if hud_control.leftLaneDepart:
    left_lane_warning = 1 if fingerprint in (CAR.GENESIS_G90, CAR.GENESIS_G80) else 2
  if hud_control.rightLaneDepart:
    right_lane_warning = 1 if fingerprint in (CAR.GENESIS_G90, CAR.GENESIS_G80) else 2

  return sys_warning, sys_state, left_lane_warning, right_lane_warning


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    from opendbc.car.hyundai.gv60_startup import qualified as gv60_long_qualified
    self.gv60_longitudinal = gv60_long_qualified(CP)
    self.CAN = CanBus(CP)
    self.params = CarControllerParams(CP)
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.angle_limit_counter = 0
    self.angle_vm = VehicleModel(CP) if CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING else None
    self.apply_angle_last = 0.0
    self.angle_gain_last = 0.0
    self.angle_tx_recovery = (AngleTxRecovery(0xCB, self.CAN.ECAN)
      if pe_long_qualified(CP) or ev9_long_qualified(CP) else AngleTxRecovery(0x110, self.CAN.ACAN)
      if CP.carFingerprint in (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9) and
      (qualified_angle_aol(CP, marked_only=True) or qualified_ccnc_ev_stock(CP)) else None)

    self.accel_last = 0
    self.apply_torque_last = 0
    self.car_fingerprint = CP.carFingerprint
    self.last_button_frame = 0
    self.cancel_counter = 0
    self.blended_longitudinal = None
    self.blended_previous_lateral = False
    self.blended_disengage_frame = None
    self.last_ccnc_161_ts_ns = 0
    self.last_ccnc_162_ts_ns = 0
    self.last_ccnc_now_ns = 0
    self.ccnc_baselined = False
    self.ioniq6_longitudinal = (Ioniq6LongitudinalPolicy()
                                if CP.carFingerprint == CAR.HYUNDAI_IONIQ_6 and CP.openpilotLongitudinalControl else None)
    self.pe_longitudinal = PELongitudinalPolicy() if pe_long_qualified(CP) else None
    self.pe_startup = None
    self.ev9_longitudinal = EV9LongitudinalPolicy() if ev9_long_qualified(CP) else None
    self.ev9_lead_inputs = None
    self.ev9_legacy_envelope = LegacyAngleEnvelope() if self.ev9_longitudinal is not None or self.pe_longitudinal is not None else None
    self.ev9_angle_filter = FirstOrderFilter(0.0, 0.2, DT_CTRL) if self.ev9_longitudinal is not None or self.pe_longitudinal is not None else None
    self.ioniq6_accel_request = None
    self.ioniq6_lead_inputs = None
    self.torque_ev_scc_enabled = torque_ev_scc_eligible(CP)
    self.torque_ev_lead_inputs = None
    self.torque_ev_floor_boot_ns = clock_boot_ns() if self.torque_ev_scc_enabled else 0
    self.adrv_template = None
    self.gv70_stock_fallback = False
    self.gv70_lead_inputs = None
    self.gv70_lead_enabled = gv70_lead_eligible(CP)
    self.g90_lead_inputs = None
    self.g90_lead_state = G90LeadState() if g90_lead_eligible(CP) else None
    self.g90_longitudinal = (G90LongitudinalPolicy()
                              if CP.carFingerprint == CAR.GENESIS_G90 and CP.openpilotLongitudinalControl else None)
    ioniq6_status = (self.ioniq6_longitudinal is not None and
                     int(CP.safetyConfigs[-1].safetyParam) in (0x8015, 0x8095, 0x8815, 0x8895))
    self.ioniq6_bsm_enabled = ioniq6_status
    self.ev6_aol_status = ((CP.carFingerprint == CAR.KIA_EV6 and (qualified_canfd_stock_aol(CP) or qualified_ev6_aol(CP))) or
                           (CP.carFingerprint == CAR.KIA_SPORTAGE_5TH_GEN and
                            (qualified_canfd_stock_aol(CP) or qualified_canfd_long_aol(CP))))
    self.canfd_dash_icons = CanFDDashIcons() if ioniq6_status or self.torque_ev_scc_enabled or self.ev6_aol_status else None
    self.classic_dash_icons = CanFDDashIcons() if classic_aol_dash_enabled(CP) else None
    self.classic_lfa_icon = None
    self.ioniq6_bsm_counter = 0
    self.ioniq6_bsm_last_can_ns = 0
    self._ray_pedal = ray_pedal_enabled(CP)
    self._ray_pedal_gas_last = 0.0
    self._ray_pedal_packer = CANPacker("hyundai_kia_ray_pedal") if self._ray_pedal else None
    self._ray_lfa_8byte = CP.carFingerprint == CAR.KIA_RAY_EV and bool(CP.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CAN_REFRESH_MSGS)
    self._ray_lfa_packer = CANPacker("hyundai_kia_ray_lfa") if self._ray_lfa_8byte else None
    self._ray_lkas11_active = False
    self._ray_lfa_icon = 0
    self._ray_prev_lat_active = False
    self._ray_disengage_frame = None

  def update(self, CC, CS, now_nanos):
    pending_startup_required = self.pe_longitudinal is not None or self.ev9_longitudinal is not None
    if pending_startup_required and (self.pe_startup is None or not self.pe_startup.can_emit(self)):
      return structs.CarControl.Actuators.new_message(), []
    if pending_startup_required and not self.pe_startup.handed_off:
      CC = structs.CarControl.new_message().as_reader()
    _projection = self.learning_projection_observer
    if _projection is not None:
      _projection.begin(CC.actuators.torque, self.frame)
    actuators = CC.actuators
    if self.CP.carFingerprint == CAR.KIA_EV6_2025 and self.CP.dashcamOnly:
      return actuators.as_builder(), []
    hud_control = CC.hudControl
    # Card finalizes the AOL safety marker after CarInterface constructs us.
    if self.classic_dash_icons is None and classic_aol_dash_enabled(self.CP):
      self.classic_dash_icons = CanFDDashIcons()
    if self.classic_dash_icons is not None:
      self.classic_lfa_icon = self.classic_dash_icons.update(self.frame, CC.enabled, CC.latActive)
    if self.CP.carFingerprint == CAR.KIA_RAY_EV:
      if CC.latActive:
        self._ray_disengage_frame = None
      elif self._ray_prev_lat_active:
        self._ray_disengage_frame = self.frame - 1
      disengaging = self._ray_disengage_frame is not None and (self.frame - self._ray_disengage_frame) * DT_CTRL < 1.0
      self._ray_lfa_icon = 2 if CC.enabled or CC.latActive else 3 if disengaging else 0
      self._ray_prev_lat_active = CC.latActive

    # steering torque
    if self.CP.flags & HyundaiFlags.CANFD and self.angle_vm is None:
      self.params = CarControllerParams(self.CP, CS.out.vEgoRaw)
    new_torque = int(round(actuators.torque * self.params.STEER_MAX))
    if _projection is not None:
      _projection.input(new_torque, self.params.STEER_MAX, self.apply_torque_last, CS.out.steeringTorque, self.params, limiter='driver')
    apply_torque = (0 if self.angle_vm is not None else
                    apply_driver_steer_torque_limits(new_torque, self.apply_torque_last, CS.out.steeringTorque, self.params))

    # >90 degree steering fault prevention
    self.angle_limit_counter, apply_steer_req = common_fault_avoidance(abs(CS.out.steeringAngleDeg) >= MAX_ANGLE, CC.latActive,
                                                                       self.angle_limit_counter, MAX_ANGLE_FRAMES,
                                                                       MAX_ANGLE_CONSECUTIVE_FRAMES)

    if not CC.latActive:
      apply_torque = 0

    self.apply_torque_last = apply_torque

    # Original lead hysteresis counts100Hz controller updates, not50Hz SCC.
    lead_data = None
    if self.g90_lead_state is not None and self.g90_lead_inputs is not None:
      lead_data = self.g90_lead_state.update(self.g90_lead_inputs.update(), hud_control.leadVisible)

    # accel + longitudinal
    accel = float(np.clip(actuators.accel, self.params.ACCEL_MIN, self.params.ACCEL_MAX))
    stopping = actuators.longControlState == LongCtrlState.stopping
    if self.pe_longitudinal is not None:
      request = self.pe_longitudinal.update(self.frame, active=CC.enabled and CC.longActive,
        override=CC.cruiseControl.override, accel=accel, speed=CS.out.vEgo, measured_accel=CS.out.aEgo,
        control_state=actuators.longControlState, last_sent_accel=self.accel_last)
      accel, stopping = request.accel, request.stopping
    if self.ev9_longitudinal is not None:
      accel = self.ev9_longitudinal.update(self.frame, float(np.clip(actuators.accel, -3.5, 3.5)), CS.out.vEgo,
                                            CS.out.aEgo, actuators.longControlState, CC.enabled, CC.cruiseControl.override)
    if self.ioniq6_longitudinal is not None:
      self.ioniq6_accel_request = self.ioniq6_longitudinal.update(
        self.frame, active=CC.enabled and CC.longActive, override=CC.cruiseControl.override,
        accel=accel, speed=CS.out.vEgo, measured_accel=CS.out.aEgo,
        control_state=actuators.longControlState, last_sent_accel=self.accel_last)
      accel = self.ioniq6_accel_request.accel
      stopping = self.ioniq6_accel_request.stopping
    if self.g90_longitudinal is not None:
      accel = self.g90_longitudinal.update(accel, CS.out.vEgo, actuators.longControlState, self.CP.openpilotLongitudinalControl)
    set_speed_in_units = hud_control.setSpeed * (CV.MS_TO_KPH if CS.is_metric else CV.MS_TO_MPH)

    can_sends = []

    # *** common hyundai stuff ***

    if self.blended_longitudinal is not None:
      can_sends.extend(self.blended_longitudinal.tester_present(self.frame))

    # tester present - w/ no response (keeps relevant ECU disabled)
    if (self.frame % 100 == 0 and not (self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC) and
        self.CP.openpilotLongitudinalControl and not self._ray_pedal and self.blended_longitudinal is None):
      # for longitudinal control, either radar or ADAS driving ECU
      addr, bus = 0x7d0, self.CAN.ECAN if self.CP.flags & HyundaiFlags.CANFD else 0
      if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG.value:
        addr, bus = 0x730, self.CAN.ECAN
      can_sends.append(make_tester_present_msg(addr, bus, suppress_response=True))

      # for blinkers
      if self.CP.flags & HyundaiFlags.CANFD_ENABLE_BLINKERS:
        can_sends.append(make_tester_present_msg(0x7b1, self.CAN.ECAN, suppress_response=True))

    # Delay the cancel button send so the brake can disengage factory SCC first.
    # Reset whenever openpilot is no longer requesting cancel.
    self.cancel_counter = self.cancel_counter + 1 if CC.cruiseControl.cancel else 0

    # *** CAN/CAN FD specific ***
    if self.CP.flags & HyundaiFlags.CANFD:
      can_sends.extend(self.create_canfd_msgs(apply_steer_req, apply_torque, set_speed_in_units, accel,
                                              stopping, hud_control, CS, CC, now_nanos))
    else:
      # Hold torque with induced temporary fault when cutting the actuation bit
      # FIXME: we don't use this with CAN FD?
      torque_fault = CC.latActive and not apply_steer_req

      can_sends.extend(self.create_can_msgs(apply_steer_req, apply_torque, torque_fault, set_speed_in_units, accel,
                                            stopping, hud_control, actuators, CS, CC, lead_data=lead_data))

    new_actuators = actuators.as_builder()
    new_actuators.torque = apply_torque / self.params.STEER_MAX
    new_actuators.torqueOutputCan = apply_torque
    if self.angle_vm is not None:
      new_actuators.steeringAngleDeg = self.apply_angle_last
    new_actuators.accel = accel

    if self.angle_tx_recovery is not None:
      self.angle_tx_recovery.record(can_sends, self.frame)
    self.frame += 1
    if _projection is not None:
      _projection.finish(new_actuators.torque)
    can_sends.extend(CS.daw.command(now_nanos))
    return new_actuators, can_sends

  def create_can_msgs(self, apply_steer_req, apply_torque, torque_fault, set_speed_in_units, accel, stopping,
                      hud_control, actuators, CS, CC, *, lead_data=None):
    _projection = self.learning_projection_observer
    can_sends = []

    # HUD messages
    hud_enabled = CC.enabled or (self.CP.carFingerprint == CAR.KIA_STINGER_2022 and CC.latActive)
    sys_warning, sys_state, left_lane_warning, right_lane_warning = process_hud_alert(hud_enabled, self.car_fingerprint,
                                                                                      hud_control)

    if is_blended(self.CP):
      if CC.latActive:
        self.blended_disengage_frame = None
      elif self.blended_previous_lateral:
        self.blended_disengage_frame = (self.frame - 1 if self.blended_longitudinal is not None
                                          else self.frame)
      self.blended_previous_lateral = CC.latActive
      disengaging = self.blended_disengage_frame is not None and self.frame - self.blended_disengage_frame < 100
      icon = 2 if CC.enabled or CC.latActive else 3 if disengaging else 1
      if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
        can_sends.append(hyundaican.create_blended_steering(self.packer, self.CAN, apply_torque, apply_steer_req, icon))
        if _projection is not None:
          _projection.emit('hyundai.BLENDED_TORQUE', apply_torque, apply_steer_req, can_sends[-1])
        if self.blended_longitudinal is not None and self.blended_longitudinal.owner.active():
          can_sends.extend(hyundaican.create_lkas11_can_canfd_blended(
            self.packer, self.frame, self.CP, apply_torque, apply_steer_req, torque_fault, CS.lkas11,
            sys_warning, sys_state, CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible,
            left_lane_warning, right_lane_warning, {}, include_alerts=False, counter_mod=0xF,
            fcw_opt_usm=2 if apply_steer_req or icon == 3 else 1))
        if (self.frame % 5 == 0 and CS.out.canValid and not CS.out.canTimeout and
            CS.blended_camera_current):
          can_sends.append(hyundaicanfd.create_suppress_lfa(self.packer, self.CAN, CS.lfa_block_msg, False))
      else:
        _projection_packet_index = len(can_sends) if _projection is not None else 0
        can_sends.extend(hyundaican.create_lkas11_can_canfd_blended(
          self.packer, self.frame, self.CP, apply_torque, apply_steer_req, torque_fault, CS.lkas11,
          sys_warning, sys_state, CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible,
          left_lane_warning, right_lane_warning, CS.msg_364))
        if _projection is not None:
          _projection.emit('hyundai.BLENDED_LKAS11', apply_torque, apply_steer_req, can_sends[_projection_packet_index])
    elif self.CP.carFingerprint != CAR.KIA_RAY_EV or self._ray_lkas11_active:
      can_sends.append(hyundaican.create_lkas11(self.packer, self.frame, self.CP, apply_torque, apply_steer_req,
                                              torque_fault, CS.lkas11, sys_warning, sys_state, hud_enabled,
                                              hud_control.leftLaneVisible, hud_control.rightLaneVisible,
                                              left_lane_warning, right_lane_warning,
                                              ray_lka_icon=(self._ray_lfa_icon or 1) if self.CP.carFingerprint == CAR.KIA_RAY_EV else None,
                                              aol_lka_icon=(self.classic_lfa_icon or 1) if self.classic_lfa_icon is not None else None))
    if _projection is not None and can_sends:
      if not is_blended(self.CP) and (self.CP.carFingerprint != CAR.KIA_RAY_EV or self._ray_lkas11_active):
        _projection.emit('hyundai.LKAS11', apply_torque, apply_steer_req, can_sends[-1])
    if self.CP.carFingerprint == CAR.KIA_RAY_EV:
      self._ray_lkas11_active = True

    # Button messages
    if not self.CP.openpilotLongitudinalControl or self._ray_pedal:
      if self._ray_pedal and CC.enabled and CS.out.cruiseState.enabled:
        if (self.frame - self.last_button_frame) * DT_CTRL > 0.04:
          can_sends.append(hyundaican.create_clu11(self.packer, self.frame, CS.clu11, Buttons.CANCEL, self.CP))
          self.last_button_frame = self.frame
      elif self.cancel_counter > CANCEL_BUTTON_DELAY_FRAMES:
        can_sends.append(hyundaican.create_clu11(self.packer, self.frame, CS.clu11, Buttons.CANCEL, self.CP))
      elif CC.cruiseControl.resume and not self._ray_pedal and self.blended_longitudinal is None:
        # send resume at a max freq of 10Hz
        if (self.frame - self.last_button_frame) * DT_CTRL > 0.1:
          # send 25 messages at a time to increases the likelihood of resume being accepted
          can_sends.extend([hyundaican.create_clu11(self.packer, self.frame, CS.clu11, Buttons.RES_ACCEL, self.CP)] * 25)
          if (self.frame - self.last_button_frame) * DT_CTRL >= 0.15:
            self.last_button_frame = self.frame

    if self._ray_pedal and self.frame % 4 == 0:
      pedal_active = (CC.longActive and CS.ray_pedal_valid and CS.ray_pedal_state == 0 and
                      not CC.cruiseControl.override and not CS.out.gasPressed and not CS.out.brakePressed)
      self._ray_pedal_gas_last = ray_pedal_gas(self._ray_pedal_gas_last, CS.out.vEgo, accel, hud_control.setSpeed) if pedal_active else 0.0
      can_sends.append(create_ray_pedal_command(self._ray_pedal_packer, self._ray_pedal_gas_last, (self.frame // 4) & 0xF))

    if self.blended_longitudinal is not None:
      can_sends.extend(self.blended_longitudinal.commands(self.frame, CC, CS, accel, stopping, set_speed_in_units))

    if self.frame % 2 == 0 and self.CP.openpilotLongitudinalControl and not self._ray_pedal and self.blended_longitudinal is None:
      # TODO: unclear if this is needed
      jerk = 3.0 if actuators.longControlState == LongCtrlState.pid else 1.0
      use_fca = self.CP.flags & HyundaiFlags.USE_FCA.value
      can_sends.extend(hyundaican.create_acc_commands(self.packer, CC.enabled, accel, jerk, int(self.frame / 2),
                                                      hud_control, set_speed_in_units, stopping,
                                                      CC.cruiseControl.override, use_fca, self.CP, lead_data=lead_data))

    # 20 Hz LFA MFA message
    if self.frame % 5 == 0 and (self.CP.flags & HyundaiFlags.SEND_LFA.value or
                               self.blended_longitudinal is not None and self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG):
      if self._ray_lfa_8byte:
        can_sends.append(hyundaican.create_ray_lfahda_mfc(self._ray_lfa_packer, CC.latActive, self._ray_lfa_icon))
      elif is_blended(self.CP):
        lfa_icon = 2 if CC.enabled or CC.latActive else 3 if disengaging else 0
        can_sends.append(hyundaican.create_blended_lfahda(self.packer, self.CAN, self.frame, lfa_icon))
      else:
        can_sends.append(hyundaican.create_lfahda_mfc(self.packer, CC.enabled, lfa_icon=self.classic_lfa_icon))

    # 5 Hz ACC options
    if self.frame % 20 == 0 and self.CP.openpilotLongitudinalControl and not self._ray_pedal and self.blended_longitudinal is None:
      can_sends.extend(hyundaican.create_acc_opt(self.packer, self.CP))

    # 2 Hz front radar options
    if self.frame % 50 == 0 and self.CP.openpilotLongitudinalControl and not self._ray_pedal and self.blended_longitudinal is None:
      can_sends.append(hyundaican.create_frt_radar_opt(self.packer))

    return can_sends

  def create_canfd_msgs(self, apply_steer_req, apply_torque, set_speed_in_units, accel, stopping, hud_control, CS, CC, now_nanos):
    _projection = self.learning_projection_observer
    can_sends = []
    canfd_lfa_icon = (self.canfd_dash_icons.update(self.frame, CC.enabled, CC.latActive)
                       if self.canfd_dash_icons is not None else None)

    lka_steering = self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG
    lka_steering_long = lka_steering and self.CP.openpilotLongitudinalControl
    ccnc_non_lka = bool(self.CP.flags & HyundaiFlags.CCNC and not lka_steering)
    if ccnc_non_lka:
      # A new controller instance or reversed clock must wait for a fresh pair.
      if not self.ccnc_baselined or now_nanos < self.last_ccnc_now_ns:
        self.last_ccnc_161_ts_ns = CS.ccnc_161_ts_ns
        self.last_ccnc_162_ts_ns = CS.ccnc_162_ts_ns
        self.ccnc_baselined = True
      self.last_ccnc_now_ns = now_nanos

    ccnc_longitudinal = self.pe_longitudinal or self.ev9_longitudinal
    pe_long = self.pe_longitudinal is not None
    long_sender = ioniq5pe_long_sender if pe_long else ev9_long_sender
    ev9_long = ccnc_longitudinal is not None
    angle_aol = qualified_angle_aol(self.CP, marked_only=True)
    angle_aol_active = angle_aol and CC.latActive and angle_aol_command_allowed(self.CP, CS.out)
    ccnc_ev_stock = self.CP.carFingerprint in (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9) and not ev9_long
    ccnc_ev_replacement = replacement_requested(self.CP, CC, CS.out) if ccnc_ev_stock else False

    # steering control
    if ev9_long:
      drive = CS.out.gearShifter == structs.CarState.GearShifter.drive
      measured = CS.angle_steering_angle
      if self.angle_tx_recovery is not None and self.angle_tx_recovery.take_rejection(self.frame):
        self.apply_angle_last = float(np.clip(measured, -self.params.ANGLE_LIMITS.STEER_ANGLE_MAX,
                                              self.params.ANGLE_LIMITS.STEER_ANGLE_MAX))
        self.angle_gain_last = 0.0
        if self.ev9_angle_filter is not None:
          self.ev9_angle_filter.x = self.apply_angle_last
      safety_speed = max(CS.out.vEgoRaw - 1.0, 1.0)
      bound = min(get_max_angle_vm(safety_speed, self.ev9_legacy_envelope, self.params),
                  get_max_angle_vm(safety_speed, self.angle_vm, self.params))
      ev9_lateral_allowed = lateral_request_allowed(CC, CS.out, CS.angle_steering_fault,
                                                   independent=(ev9_long_aol_qualified(self.CP, marked_only=True) or
                                                                pe_long_aol_qualified(self.CP, marked_only=True)))
      active = bool(ev9_lateral_allowed and CC.latActive and abs(measured) <= bound and abs(self.apply_angle_last) <= bound)
      self.ev9_angle_filter.update_alpha(float(np.interp(CS.out.vEgo, [5.0, 10.0, 20.0], [0.2, 0.1, 0.0])))
      desired = self.ev9_angle_filter.update(float(np.clip(CC.actuators.steeringAngleDeg,
                                                         -(self.params.ANGLE_LIMITS.STEER_ANGLE_MAX if pe_long else EV9_OP_LONG_ANGLE_TARGET_MAX),
                                                         self.params.ANGLE_LIMITS.STEER_ANGLE_MAX if pe_long else EV9_OP_LONG_ANGLE_TARGET_MAX)))
      # Preserve the original two VM passes and its final tolerance-envelope delta.
      angle = apply_steer_angle_limits_vm(desired, self.apply_angle_last, CS.out.vEgoRaw, measured,
                                         active, self.params, self.angle_vm)
      angle = apply_steer_angle_limits_vm(angle or desired, self.apply_angle_last, CS.out.vEgoRaw, measured,
                                         active, self.params, self.ev9_legacy_envelope)
      if active:
        delta = min(get_max_angle_delta_vm(safety_speed, self.ev9_legacy_envelope, self.params),
                    self.params.ANGLE_LIMITS.MAX_ANGLE_RATE)
        angle = float(np.clip(angle, self.apply_angle_last - delta, self.apply_angle_last + delta))
      # Exact typed EV9 safety envelope is an additional bound, not a new tune.
      self.apply_angle_last = apply_steer_angle_limits_vm(angle, self.apply_angle_last, safety_speed, measured,
                                                        active, self.params, self.angle_vm)
      self.angle_gain_last = angle_torque_reduction_gain(CS.out.steeringTorque, CS.out.vEgoRaw, active, self.angle_gain_last)
      if not active:
        self.ev9_angle_filter.x = self.apply_angle_last
      # Native checks health even for an inactive CB; do not emit stale measurements.
      if CS.out.canValid and not CS.out.canTimeout:
        if drive:
          can_sends.append(long_sender.create_angle_adas_cmd(self.packer, self.CAN, self.apply_angle_last,
                                                                active, self.angle_gain_last))
        else:
          can_sends.extend(long_sender.create_inactive_angle_steering_messages(self.packer, self.CAN,
                                                                                 self.apply_angle_last))
    elif self.angle_vm is not None:
      drive_gear_required = self.CP.carFingerprint in (CAR.HYUNDAI_AZERA_HEV_7TH_GEN,
                                                       CAR.KIA_SORENTO_HEV_4TH_GEN_LFA2,
                                                       CAR.KIA_SPORTAGE_HEV_2026,
                                                       CAR.HYUNDAI_SANTA_FE_HEV_5TH_GEN,
                                                       CAR.KIA_SPORTAGE_2026)
      angle_active = bool(CC.latActive and CS.out.cruiseState.enabled and not CS.out.brakePressed and
                          not CS.out.gasPressed and not CS.out.steerFaultTemporary and
                          (not (self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT or drive_gear_required) or
                           CS.out.gearShifter == structs.CarState.GearShifter.drive))
      if angle_aol:
        angle_active = angle_aol_active
      if ccnc_ev_stock:
        angle_active = ccnc_ev_replacement
      if self.angle_tx_recovery is not None and self.angle_tx_recovery.take_rejection(self.frame):
        self.apply_angle_last = CS.out.steeringAngleDeg
        self.angle_gain_last = 0.0
      desired_angle = float(np.clip(CC.actuators.steeringAngleDeg, -self.params.ANGLE_LIMITS.STEER_ANGLE_MAX,
                                    self.params.ANGLE_LIMITS.STEER_ANGLE_MAX))
      safety_speed = max(CS.out.vEgoRaw - 1.0, 1.0)
      self.apply_angle_last = apply_steer_angle_limits_vm(
        desired_angle, self.apply_angle_last, safety_speed, CS.out.steeringAngleDeg,
        angle_active, self.params, self.angle_vm)
      self.angle_gain_last = angle_torque_reduction_gain(CS.out.steeringTorque, CS.out.vEgoRaw,
                                                         angle_active, self.angle_gain_last)
      if not ccnc_ev_stock or ccnc_ev_replacement:
        can_sends.extend(
          hyundaicanfd.create_angle_steering_messages(
            self.packer,
            self.CP,
            self.CAN,
            CC.enabled or (angle_aol and angle_active),
            angle_active,
            self.apply_angle_last,
            self.angle_gain_last,
            CS.angle_lkas_status,
            CS.out.steeringAngleDeg,
          )
        )
    else:
      steering_status_active = CC.enabled
      if ((self.CP.carFingerprint == CAR.KIA_CARNIVAL_2025 and self.CP.safetyConfigs[-1].safetyParam == 0x28B4) or
          self.ev6_aol_status or (self.ioniq6_longitudinal is not None and
                                 self.CP.safetyConfigs[-1].safetyParam in (0x8815, 0x8895))):
        # AOL may steer while ordinary cruise engagement remains off.
        steering_status_active |= CC.latActive
      can_sends.extend(hyundaicanfd.create_steering_messages(self.packer, self.CP, self.CAN,
                                                            steering_status_active, apply_steer_req, apply_torque))
      if _projection is not None:
        _projection.emit('hyundai.CANFD_TORQUE', apply_torque, apply_steer_req, can_sends[-1])

    # prevent LFA from activating on LKA steering cars by sending "no lane lines detected" to ADAS ECU
    if (
      self.frame % 5 == 0
      and lka_steering
      and (not angle_aol or angle_aol_active)
      and (not ccnc_ev_stock or ccnc_ev_replacement)
      and (not ev9_long or ev9_lateral_allowed)
    ):
      can_sends.append(hyundaicanfd.create_suppress_lfa(self.packer, self.CAN, CS.lfa_block_msg,
                                                        self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT))

    # LFA and HDA icons
    if self.frame % 5 == 0 and (not lka_steering or lka_steering_long) and not ev9_long:
      if ccnc_non_lka:
        # Re-emit one paired camera display sample at most once. Missing or
        # held optional frames must not be replaced by a zero-filled payload.
        pair_ts = max(CS.ccnc_161_ts_ns, CS.ccnc_162_ts_ns)
        fresh_pair = (CS.ccnc_161_ts_ns > self.last_ccnc_161_ts_ns and
                      CS.ccnc_162_ts_ns > self.last_ccnc_162_ts_ns and
                      pair_ts <= now_nanos and
                      now_nanos - min(CS.ccnc_161_ts_ns, CS.ccnc_162_ts_ns) <= 50_000_000 and
                      abs(CS.ccnc_161_ts_ns - CS.ccnc_162_ts_ns) <= 50_000_000)
        if fresh_pair:
          support_current = (CS.ccnc_1b5_ts_ns > 0 and CS.ccnc_1b5_ts_ns <= now_nanos and
                             abs(CS.ccnc_1b5_ts_ns - pair_ts) <= 50_000_000)
          can_sends.extend(hyundaicanfd.create_ccnc(self.packer, self.CAN, self.CP.openpilotLongitudinalControl,
                                                    CC.enabled, hud_control, CC.leftBlinker, CC.rightBlinker,
                                                    CS.ccnc_161.copy(), CS.ccnc_162.copy(), CS.ccnc_1b5 if support_current else None,
                                                    CS.is_metric, CS.out, CS.out.cruiseState.available, CC.latActive))
          self.last_ccnc_161_ts_ns = CS.ccnc_161_ts_ns
          self.last_ccnc_162_ts_ns = CS.ccnc_162_ts_ns
      else:
        can_sends.append(hyundaicanfd.create_lfahda_cluster(self.packer, self.CAN, CC.enabled, lfa_icon=canfd_lfa_icon))

    # blinkers
    if lka_steering and self.CP.flags & HyundaiFlags.CANFD_ENABLE_BLINKERS:
      can_sends.extend(hyundaicanfd.create_spas_messages(self.packer, self.CAN, CC.leftBlinker, CC.rightBlinker))

    if self.CP.openpilotLongitudinalControl:
      if ev9_long:
        left_escalated = CS.left_blindspot_from_radar and CC.leftBlinker and not CC.rightBlinker
        right_escalated = CS.right_blindspot_from_radar and CC.rightBlinker and not CC.leftBlinker
        left_warning, right_warning = BlindspotWarningOutput(), BlindspotWarningOutput()
        if self.frame % 5 == 0:
          left_warning = update_blindspot_warning(ccnc_longitudinal.left_warning, left_escalated, CC.leftBlinker)
          right_warning = update_blindspot_warning(ccnc_longitudinal.right_warning, right_escalated, CC.rightBlinker)
        can_sends.extend(long_sender.create_ccnc_adrv_messages(
          self.packer, self.CP, self.CAN, self.frame, CC.enabled, CS.out.cruiseState.available, hud_control, CS.out,
          CS.is_metric, CC.latActive or CC.enabled, active and self.angle_gain_last != 0.0 and not CS.out.steeringPressed,
          CS.left_blindspot_from_radar, CS.right_blindspot_from_radar, drive_gear=drive, hba_icon=CS.hba_icon,
          left_escalated=left_escalated, right_escalated=right_escalated,
          left_warning_lamp=left_warning.mirror_lamp_active, right_warning_lamp=right_warning.mirror_lamp_active,
          left_sound_active=left_warning.sound_active, right_sound_active=right_warning.sound_active))
        can_sends.append(long_sender.create_radar_heartbeat(self.frame, CS.out.brakePressed, CS.out.gasPressed))
      elif lka_steering:
        can_sends.extend(hyundaicanfd.create_adrv_messages(
          self.packer, self.CAN, self.frame, template=self.adrv_template,
          drive_gear=CS.out.gearShifter == structs.CarState.GearShifter.drive,
          speed=CS.out.vEgoRaw if self.CP.carFingerprint == CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN else None))
        if self.gv60_longitudinal and self.frame % 4 == 0:
          can_sends.append(hyundaicanfd.create_gv60_radar_heartbeat(self.frame // 4, CS.out.brakePressed, CS.out.gasPressed))
        if self.ioniq6_longitudinal is not None and self.frame % 4 == 0:
          can_sends.append(hyundaicanfd.create_ioniq6_radar_heartbeat(self.frame // 4, CS.out.brakePressed, CS.out.gasPressed))
        if self.ioniq6_bsm_enabled and self.frame % 5 == 0:
          sources = getattr(CS, "ioniq6_bsm_sources", None)
          can_now_ns = getattr(CS, "ioniq6_bsm_now_ns", 0)
          status = (sources.status(can_now_ns) if sources is not None and can_now_ns > self.ioniq6_bsm_last_can_ns else None)
          if status is not None:
            # Neutralize only the injected dashboard indication below 5 mph.
            if CS.out.vEgoRaw < 5 * CV.MPH_TO_MS:
              status = BlindspotStatus(0, 0)
            can_sends.extend(hyundaicanfd.create_ioniq6_blindspot_status(self.ioniq6_bsm_counter, status))
            self.ioniq6_bsm_counter = (self.ioniq6_bsm_counter + 1) & 0xff
            self.ioniq6_bsm_last_can_ns = can_now_ns
      elif not ccnc_non_lka:
        can_sends.extend(hyundaicanfd.create_fca_warning_light(self.packer, self.CAN, self.frame))
      if self.frame % 2 == 0:
        acc_enabled = CC.enabled
        acc_options = {}
        if self.torque_ev_scc_enabled:
          lead = (self.torque_ev_lead_inputs.update(CS.torque_ev_camera_lead, hud_control.leadVisible, now_nanos)
                  if self.torque_ev_lead_inputs is not None else
                  fallback_lead(CS.torque_ev_camera_lead, hud_control.leadVisible, self.torque_ev_floor_boot_ns))
          acc_options = torque_ev_scc_options(CS.out.cruiseState.available, CC.actuators.longControlState, lead)
        elif is_gv70(self.CP):
          request = scc_request(CC.enabled, CC.cruiseControl.override, stopping, accel, self.accel_last)
          accel = request.accel
          acc_options = {"direct_accel": True, "raw_accel": request.raw_accel,
                         "jerk_upper": request.jerk_upper, "jerk_lower": request.jerk_lower}
        elif self.ioniq6_accel_request is not None:
          lead = (self.ioniq6_lead_inputs.update(CS.ioniq6_camera_lead, hud_control.leadVisible, now_nanos)
                  if self.ioniq6_lead_inputs is not None else
                  select_lead(None, None, hud_visible=hud_control.leadVisible, observed_ns=now_nanos, epoch_floor_ns=0))
          acc_enabled = CC.enabled
          acc_options = {"direct_accel": True, "main_mode_acc": int(CS.out.cruiseState.available),
                         "jerk_upper": self.ioniq6_accel_request.jerk_upper, "jerk_lower": self.ioniq6_accel_request.jerk_lower,
                         "lead_visible": lead.visible, "lead_distance": lead.distance, "lead_rel_speed": lead.relative_speed}
        if self.gv70_lead_enabled:
          lead = self.gv70_lead_inputs.update(CS.gv70_camera_lead, hud_control.leadVisible, now_nanos) \
            if self.gv70_lead_inputs is not None else ABSENT
          acc_options.update(lead_distance=lead.distance, lead_rel_speed=lead.relative_speed, lead_visible=lead.visible)
        if ev9_long:
          tuning = ccnc_longitudinal.tuning
          dynamic = CC.actuators.longControlState in (LongCtrlState.starting, LongCtrlState.pid, LongCtrlState.stopping)
          jerk_lower = tuning.jerk_lower if dynamic else (5.0 if CC.enabled else 1.0)
          jerk_upper = tuning.jerk_upper if dynamic else (3.0 if CC.actuators.longControlState == LongCtrlState.pid else 1.0)
          zero = ccnc_longitudinal.update_stop(CC.enabled, CC.cruiseControl.override, stopping, CS.out.vEgo)
          if zero is not None:
            accel = zero
          lead = (self.ev9_lead_inputs.update(CS.ev9_camera_lead, hud_control.leadVisible, now_nanos)
                  if self.ev9_lead_inputs is not None else ABSENT)
          lead_visible, lead_distance, lead_rel_speed = lead.visible, lead.distance, lead.relative_speed
          accel = float(np.clip(accel, -3.5, 2.0 if pe_long else 2.2))
          can_sends.append(long_sender.create_ccnc_acc_control(
            self.packer, self.CAN, CC.enabled, accel, ccnc_longitudinal.stop.stop_request,
            ccnc_longitudinal.stop.cruise_standstill, CC.cruiseControl.override, set_speed_in_units,
            int(CS.out.cruiseState.available), lead_distance, lead_rel_speed, lead_visible, CS.out.vEgo,
            jerk_lower=jerk_lower, jerk_upper=jerk_upper))
        else:
          can_sends.append(hyundaicanfd.create_acc_control(self.packer, self.CAN, acc_enabled, self.accel_last, accel, stopping, CC.cruiseControl.override,
                                                           set_speed_in_units, hud_control, **acc_options))
        self.accel_last = accel
    else:
      # button presses
      if (self.frame - self.last_button_frame) * DT_CTRL > 0.25:
        # cruise cancel
        if CC.cruiseControl.cancel and not suppress_stock_cancel(
            self.CP, CS.out.brakePressed, CC.latActive, stock_fallback=self.gv70_stock_fallback):
          # Here we send ACC message to cancel, not buttons. Don't delay
          if self.CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
            can_sends.append(hyundaicanfd.create_acc_cancel(self.packer, self.CP, self.CAN, CS.cruise_info))
            self.last_button_frame = self.frame
          elif self.cancel_counter > CANCEL_BUTTON_DELAY_FRAMES:
            for _ in range(20):
              can_sends.append(hyundaicanfd.create_buttons(self.packer, self.CP, self.CAN, CS.buttons_counter + 1, Buttons.CANCEL))
            self.last_button_frame = self.frame

        # cruise standstill resume
        elif CC.cruiseControl.resume:
          if (not self.CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING and
              self.CP.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CARNIVAL_ALT_RESUME):
            source = CS.cruise_buttons_alt_msg
            source_age = now_nanos - CS.cruise_buttons_alt_ts_ns
            if (source and CS.cruise_buttons_alt_ts_ns > 0 and 0 <= source_age <= 100_000_000 and
                source["CRUISE_BUTTONS"] == 0 and source["ADAPTIVE_CRUISE_MAIN_BTN"] == 0 and
                source["NORMAL_CRUISE_MAIN_BTN"] == 0 and source["LDA_BTN"] == 0):
              can_sends.append(hyundaicanfd.create_carnival_alt_resume(self.packer, self.CP, self.CAN, source))
              self.last_button_frame = self.frame
          elif self.CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
            pass
          else:
            for _ in range(20):
              can_sends.append(hyundaicanfd.create_buttons(self.packer, self.CP, self.CAN, CS.buttons_counter + 1, Buttons.RES_ACCEL))
            self.last_button_frame = self.frame

    return can_sends
