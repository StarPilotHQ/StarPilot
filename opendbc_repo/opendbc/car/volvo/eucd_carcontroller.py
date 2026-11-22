"""Volvo V60 (EUCD) controller; with OP's long on, OP owns the ECM and the camera's ACC is shown standby."""
import math

from opendbc.can.packer import CANPacker
from opendbc.car import Bus, DT_CTRL
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.lateral import apply_std_steer_angle_limits
from opendbc.car.volvo.eucd_can import (create_eucd_button_msg, create_eucd_lka_msg, create_eucd_lkas_state_msg, create_eucd_longitudinal,
                                        create_eucd_fsm0, create_eucd_fsm4, create_eucd_radar, create_eucd_cam_wheel_status,
                                        create_eucd_cam_brake_request_ack, create_eucd_cam_brake_hold, create_eucd_cam_engine_data,
                                        create_eucd_resume_button, create_eucd_cam_acc_hold, create_eucd_cam_button, eucd_fsm4_brake_value,
                                        eucd_next_long_mode)
from opendbc.car.volvo.eucd_params import CarControllerEUCDParams, LongMode, SteerDirection


def eucd_cam_clean(CS) -> bool:
  """Camera engaged with no cancel nor abort: its frames can be the ECM's templates."""
  fsm3 = CS.eucd_stock_FSM3
  if fsm3["ACC_NoTargetCancel"] or int(fsm3["Byte_6"]):
    return False
  if any(f["ACC_AbortCode"] or not f["ACC_Enabled"] for f in CS.eucd_stock_FSM0_frames):
    return False
  return not any(int(f["Byte_2"]) == 0xFC for f in CS.eucd_stock_FSM4_frames)


class CarControllerEUCD(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.eucd_apply_steer_prev = 0
    self.eucd_apply_steer_dir_prev = SteerDirection.NONE
    self.eucd_latActive_prev = False
    self.eucd_steer_blocked = False
    self.eucd_steer_blocked_cnt = 0
    self.eucd_steer_dir_bf_block = SteerDirection.NONE
    # Resume (SNG)
    self.eucd_last_resume_frame = 0
    self.eucd_distance = 0
    self.eucd_waiting = False
    self.eucd_sng_count = 0
    self.eucd_op_resume_frames = 0
    self.eucd_sng_ack_frames = 0
    self.eucd_resume_hold_frames = 0  # Resume held to the ECM, frames left
    self.eucd_last_ccbuttons = None  # CEM's last CCButtons, copied for the Resume
    # Longitudinal
    self.eucd_next_long_tx_nanos = 0
    self.eucd_long_mode = LongMode.COAST
    self.eucd_accel = 0.0
    self.eucd_own = False  # OP owns the ECM with the camera's templates, the camera shown ACC standby
    self.eucd_exit_standby_frames = 0  # OP's long ended with the ECM still engaged: camera still shown standby
    self.eucd_tpl_fsm0: dict[int, dict] = {}  # camera's clean frames: FSM0 by Byte_0, FSM3 data/type, FSM4
    self.eucd_tpl_fsm3: dict[str, dict] = {}
    self.eucd_tpl_fsm4: dict | None = None
    self.eucd_brake_hold_b0: int | None = None
    # OP's stop to 0 (lead, red light, stop sign), as the camera's (route 807)
    self.eucd_driver_gas = False  # driver on the gas: OP's long off, the ECM kept engaged with no accel nor brake
    self.eucd_stopped_frames = 0
    self.eucd_car_hold_frames = 0
    self.eucd_stopping = False  # FSM4 b1 F9
    self.eucd_holding = False  # the car holds the stop: brake request released
    self.eucd_standstill = False  # FSM3 ACC_Standstill to the ECM, and the cruise standstill carstate reports
    self.eucd_driving_off = False  # resume sent, standstill released
    self.eucd_drive_off_request = False  # set with the resume burst, taken on the next frame
    self.eucd_drive_off_frames = 0
    self.eucd_drive_off_moved = False

  def update(self, CC, CS, now_nanos):
    actuators = CC.actuators
    can_sends = []

    if CC.cruiseControl.cancel and CS.out.vEgo > CarControllerEUCDParams.EUCD_MIN_STEER_SPEED and not CS.out.brakePressed:
      can_sends.append(create_eucd_button_msg(self.packer, cancel=True))

    if self.frame % 2 == 0:
      can_sends += self._eucd_lateral(CC, CS)

    self._eucd_ownership(CC, CS)
    # Carstate takes the ACC standstill from OP while it owns the ECM
    CS.eucd_op_owns_ecm = self.eucd_own
    CS.eucd_op_standstill = self.eucd_standstill
    self._eucd_resume_buttons(CC, CS, can_sends)
    for stock_fsm0 in CS.eucd_stock_FSM0_frames:
      can_sends.append(create_eucd_fsm0(self.packer, self._eucd_fsm0(stock_fsm0, CC.hudControl.leadVisible), CS.eucd_ecm_acc_engaged))

    if now_nanos >= self.eucd_next_long_tx_nanos:
      period = int(CarControllerEUCDParams.EUCD_LONG_TX_PERIOD * 1e9)
      next_tx = self.eucd_next_long_tx_nanos + period
      if self.eucd_next_long_tx_nanos == 0 or next_tx <= now_nanos:
        next_tx = now_nanos + period
      self.eucd_next_long_tx_nanos = next_tx
      can_sends += self._eucd_longitudinal(CC, CS)

    can_sends += self._eucd_relay_fsm4(CS)
    can_sends += self._eucd_relay_to_camera(CS)

    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = self.eucd_apply_steer_prev
    if CC.longActive:
      new_actuators.accel = self.eucd_accel

    self.frame += 1
    return new_actuators, can_sends

  def _eucd_lateral(self, CC, CS):
    """FSM2 steering request and PSCM1 servo state to the camera, 50 Hz."""
    CCP = CarControllerEUCDParams
    actuators = CC.actuators
    if CC.latActive and CS.out.vEgo > CCP.EUCD_MIN_STEER_SPEED:
      apply_steer = apply_std_steer_angle_limits(
        actuators.steeringAngleDeg, self.eucd_apply_steer_prev, CS.out.vEgoRaw,
        CS.out.steeringAngleDeg, CC.latActive, CCP.EUCD_ANGLE_LIMITS
      )
      apply_steer_dir = SteerDirection.LEFT if apply_steer > 0 else SteerDirection.RIGHT

      error = CS.out.steeringAngleDeg - apply_steer
      error_with_deadzone = 0 if abs(error) < CCP.EUCD_DEADZONE else error

      if not self.eucd_latActive_prev:
        self.eucd_apply_steer_dir_prev = apply_steer_dir

      # Block direction flips until the servo catches up
      if self.eucd_steer_blocked:
        if (apply_steer_dir == self.eucd_steer_dir_bf_block) or (self.eucd_steer_blocked_cnt <= 0) or (error_with_deadzone == 0):
          self.eucd_steer_blocked = False
      else:
        if apply_steer_dir != self.eucd_apply_steer_dir_prev and error_with_deadzone != 0:
          self.eucd_steer_blocked = True
          self.eucd_steer_blocked_cnt = CCP.EUCD_BLOCK_LEN
          self.eucd_steer_dir_bf_block = self.eucd_apply_steer_dir_prev

      if self.eucd_steer_blocked:
        self.eucd_steer_blocked_cnt -= 1
        apply_steer_dir = SteerDirection.NONE
      elif error_with_deadzone == 0:
        apply_steer_dir = self.eucd_apply_steer_dir_prev
    else:
      apply_steer = 0
      apply_steer_dir = SteerDirection.NONE

    self.eucd_apply_steer_prev = apply_steer
    self.eucd_apply_steer_dir_prev = apply_steer_dir
    self.eucd_latActive_prev = CC.latActive

    return [
      create_eucd_lka_msg(self.packer, apply_steer, int(apply_steer_dir)),
      # Servo state to the camera
      create_eucd_lkas_state_msg(self.packer, CS.out.steeringAngleDeg, CS.eucd_pscm_stock_values),
    ]

  def _eucd_resume_buttons(self, CC, CS, can_sends):
    """Resume burst when the lead moves away."""
    CCP = CarControllerEUCDParams
    at_standstill = CS.out.cruiseState.enabled and CS.out.cruiseState.standstill and CS.out.vEgo < 0.01

    self.eucd_op_resume_frames = self.eucd_op_resume_frames + 1 if CC.cruiseControl.resume else 0
    op_resume = self.eucd_op_resume_frames * DT_CTRL >= CCP.EUCD_SNG_OP_RESUME_TIME

    if (self.frame - self.eucd_last_resume_frame) * DT_CTRL > CCP.EUCD_RESUME_BURST_INTERVAL:
      if at_standstill and not self.eucd_waiting:
        self.eucd_distance = CS.eucd_acc_distance
        self.eucd_waiting = True
        self.eucd_sng_count = 0
      # In OP's stop only the planner's resume drives off (as Ford/Toyota), not the camera's distance jitter (route 884)
      lead_moved = CS.eucd_acc_distance > self.eucd_distance and not self.eucd_own
      # The driver's Resume also drives off OP's stop: the ECM takes it, but held in standstill it stays (route 8ab, 436 s)
      driver_resume = CS.eucd_driver_resume and self.eucd_own
      drive_off = lead_moved or op_resume or driver_resume or self.eucd_driver_gas
      # The ECM takes the driver's Resume at once, so OP follows it without waiting for its ready (route 38, 42 s)
      if at_standstill and self.eucd_waiting and drive_off and (driver_resume or self._eucd_drive_off_ready(CS)):
        self.eucd_resume_hold_frames = CCP.EUCD_RESUME_HOLD_FRAMES
        if self.eucd_sng_count == 0:
          self.eucd_sng_ack_frames = round(CCP.EUCD_SNG_ACK_TIME / CCP.EUCD_LONG_TX_PERIOD)  # counted down per FSM3
        self.eucd_sng_count += 1
        self.eucd_drive_off_request = self.eucd_own
      if self.eucd_waiting and (self.eucd_sng_count >= CCP.EUCD_RESUME_BURST_MAX or not CS.out.cruiseState.standstill):
        self.eucd_waiting = False
        self.eucd_last_resume_frame = self.frame

    # Resume held as the driver's: the CEM's frame with Resume pressed, every frame
    if CS.eucd_stock_CCButtons_frames:
      self.eucd_last_ccbuttons = CS.eucd_stock_CCButtons_frames[-1]
    if self.eucd_resume_hold_frames > 0 and self.eucd_last_ccbuttons is not None:
      can_sends.append(create_eucd_resume_button(self.packer, self.eucd_last_ccbuttons))
    self.eucd_resume_hold_frames = max(self.eucd_resume_hold_frames - 1, 0)

  # ---- OP owning the ECM ----

  def _eucd_ownership(self, CC, CS):
    """Templates, and when OP takes and gives back the ECM."""
    CCP = CarControllerEUCDParams
    clean = eucd_cam_clean(CS)
    if CS.eucd_ecm_acc_engaged and clean and not self.eucd_own:
      for f in CS.eucd_stock_FSM0_frames:
        self.eucd_tpl_fsm0[int(f["Byte_0"])] = f
      # ACC_Check is the camera's instant ack: kept out of the template, the ECM braked in the drive-off (routes 8a2, 8a3)
      self.eucd_tpl_fsm3["data" if int(CS.eucd_stock_FSM3["FrameKind"]) == 0x81 else "type"] = {**CS.eucd_stock_FSM3, "ACC_Check": 0}
      if CS.eucd_stock_FSM4_frames:
        self.eucd_tpl_fsm4 = CS.eucd_stock_FSM4_frames[-1]
    have_templates = self.eucd_tpl_fsm0 and len(self.eucd_tpl_fsm3) == 2 and self.eucd_tpl_fsm4 is not None

    # Driver's gas keeps OP owning (the panda keeps the camera off the ECM) until OP's long is back (route 518bf777|6e)
    self.eucd_driver_gas = self.eucd_own and CC.enabled and (CS.out.gasPressed or (self.eucd_driver_gas and not CC.longActive))
    # Ended by the driver with the ECM still engaged: the camera kept in standby until the ECM drops (route 896)
    if self.eucd_exit_standby_frames > 0:
      done = not CS.eucd_ecm_acc_engaged or self.eucd_exit_standby_frames * DT_CTRL >= CCP.EUCD_EXIT_STANDBY_MAX
      self.eucd_exit_standby_frames = 0 if (done or self.eucd_own) else self.eucd_exit_standby_frames + 1
    if not (CC.longActive or self.eucd_driver_gas):
      if self.eucd_own and CS.eucd_ecm_acc_engaged:
        self.eucd_exit_standby_frames = 1
      self._eucd_release()
    elif not self.eucd_own and have_templates and clean:
      self.eucd_own = True
      if CS.out.standstill:
        # Engaged at a stop: in standstill at once, as the camera, or the car keeps holding (route 38, 40 s)
        self.eucd_stopping = self.eucd_standstill = True
        self.eucd_stopped_frames = int(CCP.EUCD_STANDSTILL_DELAY / DT_CTRL)

    if self.eucd_own:
      self._eucd_update_stop(CC, CS)
    self.eucd_drive_off_request = False

  def _eucd_release(self):
    self.eucd_own = self.eucd_driving_off = self.eucd_driver_gas = False
    self.eucd_standstill = self.eucd_stopping = self.eucd_holding = False
    self.eucd_stopped_frames = self.eucd_car_hold_frames = 0

  def _eucd_update_stop(self, CC, CS):
    """Stopping, the car's hold, standstill and the drive-off, as the camera does them."""
    CCP = CarControllerEUCDParams
    standstill = CS.out.standstill
    self.eucd_stopped_frames = self.eucd_stopped_frames + 1 if standstill else 0

    # The driver's gas drives off as OP's resume does
    self.eucd_drive_off_request |= self.eucd_driver_gas and not self.eucd_driving_off
    if self.eucd_drive_off_request:
      self.eucd_driving_off, self.eucd_drive_off_frames, self.eucd_drive_off_moved = True, 0, False
    if self.eucd_driving_off:
      # Stopped again (or never moved): standstill sequence again, resume retried
      self.eucd_drive_off_frames += 1
      self.eucd_drive_off_moved |= not standstill
      if standstill and self.eucd_drive_off_moved:
        self.eucd_driving_off = False
        self.eucd_stopped_frames = 1
      elif standstill and self.eucd_drive_off_frames * DT_CTRL > CCP.EUCD_GO_RETRY_TIME:
        # Never moved: straight back to standstill, so the resume burst goes again
        self.eucd_driving_off = False
        self.eucd_stopping = True
        self.eucd_stopped_frames = int(CCP.EUCD_STANDSTILL_DELAY / DT_CTRL)

    # Latched until the resume only once in standstill; before it OP drives off on traction, as the camera (route 807)
    at_stop = not self.eucd_driving_off and standstill and (self.eucd_standstill or CS.eucd_ecm_standstill_ready)
    stopping = not self.eucd_driving_off and ((CS.out.vEgo < CCP.EUCD_STOPPING_FLAG_SPEED and CC.actuators.accel <= 0.) or
                                              (at_stop and (self.eucd_stopping or CS.eucd_car_hold)))
    self.eucd_car_hold_frames = self.eucd_car_hold_frames + 1 if (stopping and standstill and CS.eucd_car_hold) else 0
    self.eucd_stopping = stopping
    # Held until OP asks to drive after the resume, so no brake pulse at the start
    self.eucd_holding = not self.eucd_driver_gas and (
      self.eucd_car_hold_frames > CCP.EUCD_HOLD_RELEASE_FRAMES or
      (self.eucd_holding and self.eucd_driving_off and standstill and CC.actuators.accel <= 0.))
    self.eucd_standstill = stopping and self.eucd_stopped_frames * DT_CTRL >= CCP.EUCD_STANDSTILL_DELAY

  def _eucd_drive_off_ready(self, CS) -> bool:
    """The resume burst may go: the ECM ready to drive off from its standstill (or it never said so in time)."""
    if not self.eucd_own or CS.eucd_ecm_standstill_ready:
      return True
    CCP = CarControllerEUCDParams
    return self.eucd_stopped_frames * DT_CTRL >= CCP.EUCD_STANDSTILL_DELAY + CCP.EUCD_ECM_READY_TIMEOUT

  def _eucd_fsm0(self, stock_fsm0, lead: bool):
    """FSM0 to the ECM: the camera's, or its template while OP owns the ECM (its standby kept from the ECM)."""
    if not self.eucd_own:
      return stock_fsm0
    tpl = self.eucd_tpl_fsm0.get(int(stock_fsm0["Byte_0"]), next(iter(self.eucd_tpl_fsm0.values())))
    # Lead icon from OP's lead, not the template's frozen one, which flickered between keys (route 37, seg 3)
    return {**tpl, "ACC_FrontCar": int(lead)}

  # ---- Longitudinal ----

  def _eucd_longitudinal(self, CC, CS):
    """FSM1/FSM3 at 50 Hz: OP's accel and mode on the camera's template, or the camera's own frames."""
    CCP = CarControllerEUCDParams
    if self.eucd_own:
      if self.eucd_driver_gas:
        accel, mode = 0., LongMode.COAST
      else:
        accel, mode = self._eucd_long_decision(CC, CS)
      if self.eucd_holding:
        # The car holds the stop: the camera's standstill request (route 807)
        accel, mode = CCP.EUCD_HOLD_ACCEL, LongMode.BRAKE
      elif self.eucd_stopping:
        # Stopping flag on: at least the camera's brake, or it drops to 0 and the car rolls on (route 2e, 59 s)
        accel, mode = min(accel, CCP.EUCD_STOPPING_ACCEL), LongMode.BRAKE
      self.eucd_long_mode = mode
      fsm3 = self.eucd_tpl_fsm3["data" if int(CS.eucd_stock_FSM3["FrameKind"]) == 0x81 else "type"]
      standstill = self.eucd_standstill
    elif CS.eucd_ecm_acc_engaged:
      # The camera drives (OP's long off, or its frames not seen yet): its frames as they are, with the resume's ACC_Check
      accel, mode, standstill = float(CS.eucd_stock_FSM3["ACC_AccelerationRequest"]), None, None
      self.eucd_long_mode = LongMode.COAST
      fsm3 = CS.eucd_stock_FSM3
    else:
      # ECM off: the panda forwards the camera's FSM1/FSM3, a copy would be duplicated or blocked (route 2c, 837 s)
      self.eucd_long_mode = LongMode.COAST
      return []
    self.eucd_accel = accel

    if self.eucd_sng_ack_frames > 0:
      acc_check = 1
      self.eucd_sng_ack_frames -= 1
    else:
      acc_check = int(fsm3["ACC_Check"])
    return [
      create_eucd_radar(self.packer, CS.eucd_stock_FSM1, mode),
      create_eucd_longitudinal(self.packer, fsm3, accel, acc_check, mode, standstill=standstill),
    ]

  def _eucd_long_decision(self, CC, CS):
    """OP's accel within the limits and rate, and its mode."""
    CCP = CarControllerEUCDParams
    accel = min(max(CC.actuators.accel, CCP.EUCD_ACCEL_MIN), CCP.EUCD_ACCEL_MAX)
    accel = self._eucd_jerk_limit(accel, self.eucd_accel)
    brake_offset = self._eucd_slope_brake_offset(CC, CS.out.vEgo)
    return accel, eucd_next_long_mode(self.eucd_long_mode, accel, brake_offset, CS.out.vEgo)

  def _eucd_jerk_limit(self, accel, last_accel):
    """Rate limit OP's accel request: braking only against jumps, rising moderate."""
    CCP = CarControllerEUCDParams
    dt = CCP.EUCD_LONG_TX_PERIOD
    return min(max(accel, last_accel - CCP.EUCD_JERK_DOWN * dt), last_accel + CCP.EUCD_JERK_UP * dt)

  @staticmethod
  def _eucd_slope_brake_offset(CC, v_ego=None):
    """Coast accel from pitch, faded out at low speed where coasting does not slow the car (route 22, 878 s)."""
    if len(CC.orientationNED) != 3:
      return 0.
    CCP = CarControllerEUCDParams
    offset = min(-CCP.EUCD_COAST_PITCH_GAIN * math.sin(CC.orientationNED[1]), CCP.EUCD_DOWNHILL_BRAKE_OFFSET_MAX)
    if v_ego is None:
      return offset
    lo, hi = CCP.EUCD_BRAKE_ENTER_BP
    return offset * min(max((v_ego - lo) / (hi - lo), 0.), 1.)

  def _eucd_relay_fsm4(self, CS):
    """FSM4 to the ECM: OP's brake on the camera's template, or the camera's own."""
    sends = []
    for stock_fsm4 in CS.eucd_stock_FSM4_frames:
      if not self.eucd_own:
        sends.append(create_eucd_fsm4(self.packer, stock_fsm4, self.eucd_accel))
        continue
      # b0 alternates 55/AA as the camera's, b1 F1 -> F9 stopping, no brake but OP's
      fsm4 = {**self.eucd_tpl_fsm4, "Byte_0": stock_fsm4["Byte_0"], "ACC_BrakeRequest": 0, "ACC_Stopping": int(self.eucd_stopping),
              "Byte_5_b7": 1, "ACC_BrakeTarget": eucd_fsm4_brake_value(0., False) & 0x3FFF}
      # The car holds (or the driver's gas): brake request released, like the camera 50 ms after the hold
      mode = None if (self.eucd_holding or self.eucd_driver_gas) else self.eucd_long_mode
      sends.append(create_eucd_fsm4(self.packer, fsm4, self.eucd_accel, mode))
    return sends

  def _eucd_relay_to_camera(self, CS):
    """Car frames to the camera: with OP owning the ECM, ACC standby, no ACC brake, hold nor engaging button shown."""
    standby = self.eucd_own or self.eucd_exit_standby_frames > 0
    sends = []
    for stock_wheel_rear in CS.eucd_stock_WheelRear_frames:
      sends.append(create_eucd_cam_wheel_status(self.packer, stock_wheel_rear, standby))
    for stock_brake_req_ack in CS.eucd_stock_BrakeReqAck_frames:
      sends.append(create_eucd_cam_brake_request_ack(self.packer, stock_brake_req_ack, standby))
    for stock_engine_data in CS.eucd_stock_EngineData_frames:
      sends.append(create_eucd_cam_engine_data(self.packer, stock_engine_data, standby))
    for stock_acc_hold in CS.eucd_stock_AccHold_frames:
      sends.append(create_eucd_cam_acc_hold(self.packer, stock_acc_hold, standby))
    for stock_ccbuttons in CS.eucd_stock_CCButtons_frames:
      sends.append(create_eucd_cam_button(self.packer, stock_ccbuttons, standby))
    # 0x1FF b0 held while stopped (its 0x70 -> 0xD0 fires the camera's brake alert)
    hold_mask = self.eucd_own and CS.out.standstill
    for stock_brake_hold in CS.eucd_stock_BrakeHold_frames:
      if not hold_mask or self.eucd_brake_hold_b0 is None:
        self.eucd_brake_hold_b0 = int(stock_brake_hold["Byte_0"])
      sends.append(create_eucd_cam_brake_hold(self.packer, stock_brake_hold, self.eucd_brake_hold_b0 if hold_mask else None))
    return sends
