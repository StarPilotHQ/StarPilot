import math
import numpy as np
from opendbc.can import CANPacker
from opendbc.bluepilot_lateral.hosts.starpilot.lateral import BluePilotLateral, FrameInputs  # BluePilot
from opendbc.car import ACCELERATION_DUE_TO_GRAVITY, Bus, DT_CTRL, apply_hysteresis, structs
from opendbc.car.ford import fordcan
from opendbc.car.ford.aol import qualified as qualified_aol
from opendbc.car.ford.stock_cruise import FordStockCruiseButton, qualified as stock_switch_qualified
from opendbc.car.ford.aol import profile_word
from opendbc.car.ford.values import CarControllerParams, FordFlags, CAR
from opendbc.car.interfaces import CarControllerBase, V_CRUISE_MAX

LongCtrlState = structs.CarControl.Actuators.LongControlState
VisualAlert = structs.CarControl.HUDControl.VisualAlert

def anti_overshoot(apply_curvature, apply_curvature_last, v_ego):
  diff = 0.1
  tau = 5  # 5s smooths over the overshoot
  dt = DT_CTRL * CarControllerParams.STEER_STEP
  alpha = 1 - np.exp(-dt / tau)

  lataccel = apply_curvature * (v_ego ** 2)
  last_lataccel = apply_curvature_last * (v_ego ** 2)
  last_lataccel = apply_hysteresis(lataccel, last_lataccel, diff)
  last_lataccel = alpha * lataccel + (1 - alpha) * last_lataccel

  output_curvature = last_lataccel / (max(v_ego, 1) ** 2)

  return float(np.interp(v_ego, [5, 10], [apply_curvature, output_curvature]))


def apply_creep_compensation(accel: float, v_ego: float, car_fingerprint: str, *, standstill: bool, stopping: bool) -> float:
  if car_fingerprint == CAR.FORD_MUSTANG_MACH_E_MK1 and not (standstill and stopping):
    return accel
  creep_accel = np.interp(v_ego, [1., 3.], [0.6, 0.])
  creep_accel = np.interp(accel, [0., 0.2], [creep_accel, 0.])
  accel -= creep_accel
  return float(accel)


def apply_brake_rate_limit(accel: float, gas: float, accel_last: float, v_ego: float, car_fingerprint: str,
                          *, stopping: bool, standstill: bool, fcw: bool) -> tuple[float, float]:
  mild_mach_e_braking = (
    car_fingerprint == CAR.FORD_MUSTANG_MACH_E_MK1 and
    -1.0 < gas < 0.0 and v_ego >= 5.0 and not (stopping or standstill or fcw)
  )
  if mild_mach_e_braking:
    accel = max(accel, min(accel_last, 0.0) - 2.0 * CarControllerParams.ACC_CONTROL_STEP * DT_CTRL)
    gas = accel
  else:
    accel = max(accel, accel_last - 3.5 * CarControllerParams.ACC_CONTROL_STEP * DT_CTRL)
  return float(accel), float(gas)


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.CAN = fordcan.CanBus(CP)

    self.bp = BluePilotLateral(CP, self.packer, self.CAN)  # BluePilot
    # StarPilot's openpilot side attaches the per-frame BluePilot inputs and settings (controller_extensions.py)
    self.bluepilot_inputs = None
    self.stock_cruise_button = FordStockCruiseButton()
    self.lat_ui_owned = False
    self.hands_free_cluster_input = None
    self.cluster_extended_announced = False
    self.apply_curvature_last = 0
    self.anti_overshoot_curvature_last = 0
    self.accel = 0.0
    self.gas = 0.0
    self.brake_request = False
    self.main_on_last = False
    self.lkas_enabled_last = False
    self.steer_alert_last = False
    self.lead_distance_bars_last = None
    self.distance_bar_frame = 0
    self.apply_lka_angle_last = 0.
    self.apply_lka_curvature_last = 0.

  def update(self, CC, CS, now_nanos):
    can_sends = []
    frame_inputs = self.bluepilot_inputs.update() if self.bluepilot_inputs is not None else FrameInputs()
    bp_active = self.bp.update(frame_inputs, self.frame)  # BluePilot
    if self.hands_free_cluster_input is not None:
      self.hands_free_cluster_input.update()
    if not CC.latActive:
      self.lat_ui_owned = False

    actuators = CC.actuators
    hud_control = CC.hudControl

    main_on = CS.out.cruiseState.available
    steer_alert = hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw)
    fcw_alert = hud_control.visualAlert == VisualAlert.fcw

    ### acc buttons ###
    stock_cancel = False
    stock_resume = False
    if stock_switch_qualified(self.CP):
      stock_cancel, stock_resume = self.stock_cruise_button.update(
        bool(CS.buttons_stock_values["CcAslButtnCnclResPress"]),
        CS.out.cruiseState.available, CS.out.cruiseState.enabled)

    if CC.cruiseControl.cancel:
      can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.camera, CS.buttons_stock_values, cancel=True))
      can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.main, CS.buttons_stock_values, cancel=True))
    elif (stock_cancel or stock_resume) and (self.frame % CarControllerParams.BUTTONS_STEP) == 0:
      can_sends.append(fordcan.create_button_msg(
        self.packer, self.CAN.camera, CS.buttons_stock_values, cancel=stock_cancel, resume=stock_resume))
      can_sends.append(fordcan.create_button_msg(
        self.packer, self.CAN.main, CS.buttons_stock_values, cancel=stock_cancel, resume=stock_resume))
    elif (CC.cruiseControl.resume and (not self.CP.openpilotLongitudinalControl or CC.longActive) and
          (self.frame % CarControllerParams.BUTTONS_STEP) == 0):
      can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.camera, CS.buttons_stock_values, resume=True))
      can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.main, CS.buttons_stock_values, resume=True))
    # if stock lane centering isn't off, send a button press to toggle it off
    # the stock system checks for steering pressed, and eventually disengages cruise control
    elif CS.acc_tja_status_stock_values["Tja_D_Stat"] != 0 and (self.frame % CarControllerParams.ACC_UI_STEP) == 0:
      can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.camera, CS.buttons_stock_values, tja_toggle=True))

    ### lateral control ###
    # send steer msg at 20Hz
    if (self.frame % CarControllerParams.STEER_STEP) == 0 and self.CP.flags & FordFlags.LKA_STEERING:
      # Preserve the stock LMC heartbeat without requesting lateral action.
      can_sends.append(fordcan.create_lat_ctl_msg(self.packer, self.CAN, False, 0., 0., 0., 0.,
                                                  stock_lmc=CS.lateral_motion_control))
    elif bp_active and (self.frame % CarControllerParams.STEER_STEP) == 0:  # BluePilot
      lat_sends, self.apply_curvature_last = self.bp.step_lateral(CC, CS, actuators, self.apply_curvature_last)
      can_sends.extend(lat_sends)
      self.lat_ui_owned |= CC.latActive
    elif (self.frame % CarControllerParams.STEER_STEP) == 0:
      # Bronco and some other cars consistently overshoot curv requests
      # Apply some deadzone + smoothing convergence to avoid oscillations
      if self.CP.carFingerprint in (CAR.FORD_BRONCO_SPORT_MK1, CAR.FORD_F_150_MK14):
        self.anti_overshoot_curvature_last = anti_overshoot(actuators.curvature, self.anti_overshoot_curvature_last, CS.out.vEgoRaw)
        apply_curvature = self.anti_overshoot_curvature_last
      else:
        apply_curvature = actuators.curvature

      # apply rate limits, curvature error limit, and clip to signal range
      current_curvature = -CS.out.yawRate / max(CS.out.vEgoRaw, 0.1)
      # No blending at low speed due to lack of torque wind-up and inaccurate current curvature
      if CS.out.vEgoRaw > 9:
        apply_curvature = float(np.clip(apply_curvature, current_curvature - CarControllerParams.CURVATURE_ERROR,
                                        current_curvature + CarControllerParams.CURVATURE_ERROR))
      apply_curvature = CarControllerParams.CURVATURE_LIMITS.apply_limits(apply_curvature, self.apply_curvature_last, CS.out.vEgoRaw,
                                                                          0., CC.latActive, CarControllerParams.STEER_STEP)
      self.apply_curvature_last = apply_curvature
      self.lat_ui_owned |= CC.latActive

      if self.CP.flags & FordFlags.CANFD:
        # TODO: extended mode
        # Ford uses four individual signals to dictate how to drive to the car. Curvature alone (limited to 0.02 m^-1)
        # can actuate the steering for a large portion of any lateral movements. However, in order to get further control on
        # steer actuation, the other three signals are necessary. Ford controls vehicles differently than most other makes.
        # A detailed explanation on ford control can be found here:
        # https://www.f150gen14.com/forum/threads/introducing-bluepilot-a-ford-specific-fork-for-comma3x-openpilot.24241/#post-457706
        mode = 1 if CC.latActive else 0
        counter = (self.frame // CarControllerParams.STEER_STEP) % 0x10
        can_sends.append(fordcan.create_lat_ctl2_msg(self.packer, self.CAN, mode, 0., 0., -self.apply_curvature_last, 0., counter))
      else:
        can_sends.append(fordcan.create_lat_ctl_msg(self.packer, self.CAN, CC.latActive, 0., 0., -self.apply_curvature_last, 0.))

    # send lka msg at 33Hz
    if (self.frame % CarControllerParams.LKA_STEP) == 0:
      if self.CP.flags & FordFlags.LKA_STEERING:
        source_age_ns = now_nanos - CS.lkas_available_ts_nanos
        lka_active = (CC.latActive and CS.lkas_available and 0 <= source_age_ns <= 100_000_000 and
                      (CS.out.cruiseState.enabled or qualified_aol(self.CP, marked_only=True)) and
                      not CS.out.steerFaultTemporary and
                      not CS.out.vehicleSensorsInvalid)
        if lka_active:
          desired_angle = float(np.clip(actuators.steeringAngleDeg - CS.out.steeringAngleDeg, -5.8, 5.8))
          self.apply_lka_angle_last = float(np.clip(desired_angle, self.apply_lka_angle_last - 1.,
                                                    self.apply_lka_angle_last + 1.))
          desired_curvature = -actuators.curvature
          current_curvature = CS.out.yawRate / max(CS.out.vEgoRaw, 0.1)
          if CS.out.vEgoRaw > 9.:
            desired_curvature = float(np.clip(desired_curvature, current_curvature - CarControllerParams.CURVATURE_ERROR,
                                              current_curvature + CarControllerParams.CURVATURE_ERROR))
          self.apply_lka_curvature_last = CarControllerParams.LKA_CURVATURE_LIMITS.apply_limits(
            desired_curvature, self.apply_lka_curvature_last, CS.out.vEgoRaw, 0., True, CarControllerParams.LKA_STEP)
        else:
          self.apply_lka_angle_last = 0.
          self.apply_lka_curvature_last = 0.
        self.apply_curvature_last = -self.apply_lka_curvature_last
        direction = 2 if self.apply_lka_angle_last >= 0. else 4
        can_sends.append(fordcan.create_lka_msg(self.packer, self.CAN, lka_active, self.apply_lka_angle_last,
                                                direction, self.apply_lka_curvature_last, transit=True))
      elif bp_active:  # BluePilot
        can_sends.append(self.bp.step_lka(CC, hud_control))
      else:
        can_sends.append(fordcan.create_lka_msg(self.packer, self.CAN))

    ### longitudinal control ###
    # send acc msg at 50Hz
    if self.CP.openpilotLongitudinalControl and (self.frame % CarControllerParams.ACC_CONTROL_STEP) == 0:
      accel = actuators.accel
      gas = accel
      stopping = actuators.longControlState == LongCtrlState.stopping

      if CC.longActive:
        accel = apply_creep_compensation(accel, CS.out.vEgo, self.CP.carFingerprint,
                                         standstill=CS.out.standstill, stopping=stopping)

        accel, gas = apply_brake_rate_limit(
          accel, gas, self.accel, CS.out.vEgo, self.CP.carFingerprint,
          stopping=stopping, standstill=CS.out.standstill,
          fcw=fcw_alert or CS.out.stockFcw or CS.out.stockAeb,
        )

      accel = float(np.clip(accel, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))
      gas = float(np.clip(gas, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))

      # Both gas and accel are in m/s^2, accel is used solely for braking
      if not CC.longActive or gas < CarControllerParams.MIN_GAS:
        gas = CarControllerParams.INACTIVE_GAS

      # PCM applies pitch compensation to gas/accel, but we need to compensate for the brake/pre-charge bits
      accel_due_to_pitch = 0.0
      if len(CC.orientationNED) == 3:
        accel_due_to_pitch = math.sin(CC.orientationNED[1]) * ACCELERATION_DUE_TO_GRAVITY

      accel_pitch_compensated = accel + accel_due_to_pitch
      if accel_pitch_compensated > 0.3 or not CC.longActive:
        self.brake_request = False
      elif accel_pitch_compensated < 0.0:
        self.brake_request = True

      # TODO: look into using the actuators packet to send the desired speed
      can_sends.append(fordcan.create_acc_msg(self.packer, self.CAN, CC.longActive, gas, accel, stopping, self.brake_request, v_ego_kph=V_CRUISE_MAX))

      self.accel = accel
      self.gas = gas

    # Track the actual outgoing LKA announcement for every CAN-FD display owner.
    if any(address == 0x3CA for address, _, _ in can_sends):
      self.cluster_extended_announced = True

    ### ui ###
    # Acquire the display after the first active replacement, then retain it while
    # AOL stays active, including a manual-turn steering handoff (as on Dom).
    aol_owned_ui = qualified_aol(self.CP, marked_only=True) and any(
      s.safetyModel == structs.CarParams.SafetyModel.ford and (profile_word(s.safetyParam) & ~(1 | 256)) == 2
      for s in self.CP.safetyConfigs)
    ui_enabled = CC.latActive and (not aol_owned_ui or self.lat_ui_owned)
    neutral_ui = (aol_owned_ui and self.CP.openpilotLongitudinalControl and not CC.latActive and not steer_alert and
                  CS.out.canValid and not CS.out.canTimeout and not CS.out.vehicleSensorsInvalid and
                  not CS.out.steerFaultTemporary and not CS.out.steerFaultPermanent and not CS.out.accFaulted and
                  not CS.out.doorOpen and not CS.out.seatbeltUnlatched and CS.out.gearShifter in
                  (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport, structs.CarState.GearShifter.low))
    # Native standby-display proof starts with an accepted inactive steering packet.
    neutral_ui = neutral_ui and any(address == 0x3D6 for address, _, _ in can_sends)
    if not aol_owned_ui or ui_enabled or neutral_ui:
      send_ui = (self.main_on_last != main_on) or (self.lkas_enabled_last != ui_enabled) or (self.steer_alert_last != steer_alert)
      # send lkas ui msg at 1Hz or if ui state changes
      if (self.frame % CarControllerParams.LKAS_UI_STEP) == 0 or send_ui:
        can_sends.append(fordcan.create_lkas_ui_msg(self.packer, self.CAN, main_on, ui_enabled, steer_alert, hud_control, CS.lkas_status_stock_values))

      # send acc ui msg at 5Hz or if ui state changes
      if hud_control.leadDistanceBars != self.lead_distance_bars_last:
        send_ui = True
        self.distance_bar_frame = self.frame

      acc_ui_step = CarControllerParams.ACC_UI_STEP // 2 if neutral_ui else CarControllerParams.ACC_UI_STEP
      if (self.frame % acc_ui_step) == 0 or send_ui:
        show_distance_bars = self.frame - self.distance_bar_frame < 400
        hands_free_cluster = bool(self.cluster_extended_announced and
                                  self.hands_free_cluster_input is not None and self.hands_free_cluster_input.enabled)
        can_sends.append(fordcan.create_acc_ui_msg(self.packer, self.CAN, self.CP, main_on, ui_enabled,
                                                   fcw_alert, CS.out.cruiseState.standstill, show_distance_bars,
                                                   hud_control, CS.acc_tja_status_stock_values, hands_free_cluster))

    self.main_on_last = main_on
    self.lkas_enabled_last = ui_enabled
    self.steer_alert_last = steer_alert
    self.lead_distance_bars_last = hud_control.leadDistanceBars

    new_actuators = actuators.as_builder()
    new_actuators.curvature = self.apply_curvature_last
    if self.CP.flags & FordFlags.LKA_STEERING:
      new_actuators.steeringAngleDeg = CS.out.steeringAngleDeg + self.apply_lka_angle_last
    new_actuators.accel = self.accel
    new_actuators.gas = self.gas

    self.frame += 1
    return new_actuators, can_sends
