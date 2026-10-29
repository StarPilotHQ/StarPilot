from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.lateral import apply_driver_steer_torque_limits
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.mazda import mazdacan
from opendbc.car.mazda.values import CarControllerParams, Buttons, MazdaSafetyFlags
from openpilot.common.realtime import DT_CTRL
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params

VisualAlert = structs.CarControl.HUDControl.VisualAlert
LongCtrlState = structs.CarControl.Actuators.LongControlState

class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.apply_torque_last = 0
    self.ti_apply_torque_last = 0
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.brake_counter = 0
    self.ccp = CarControllerParams(CP)
    self.hold_timer_frame = 0
    self.hold_delay_frame = 0
    self.resume_timer_frame = 0
    self.acc_filter = FirstOrderFilter(0.0, .1, DT_CTRL, initialized=False)
    self.filtered_acc_last = 0
    self.long_active_last = False
    self.params = Params()
    # CEM state is injected by controlsd each frame (see controlsd.py apply path).
    self.experimental_mode = False



  def update(self, CC, CS, now_nanos):
    _projection = self.learning_projection_observer
    if _projection is not None:
      _projection.begin(CC.actuators.torque, self.frame)
    can_sends = []

    apply_torque = 0
    ti_apply_torque = 0

    if CC.latActive:
      # calculate steer and also set limits due to driver torque
      new_torque = int(round(CC.actuators.torque * self.ccp.STEER_MAX))
      if _projection is not None:
        _projection.input(new_torque, self.ccp.STEER_MAX, self.apply_torque_last, CS.out.steeringTorque, self.ccp, limiter='driver')
      apply_torque = apply_driver_steer_torque_limits(new_torque, self.apply_torque_last,
                                                      CS.out.steeringTorque, self.ccp)
      if self.CP.flags & MazdaSafetyFlags.TORQUE_INTERCEPTOR:
        if CS.ti_lkas_allowed:
          ti_new_torque = int(round(CC.actuators.torque * self.ccp.STEER_MAX))
          ti_apply_torque = apply_driver_steer_torque_limits(ti_new_torque, self.apply_torque_last,
                                                    CS.out.steeringTorque, self.ccp)

    self.apply_torque_last = apply_torque
    self.ti_apply_torque_last = ti_apply_torque

    if self.CP.flags & MazdaSafetyFlags.GEN1:
      if CC.cruiseControl.cancel:
        # If brake is pressed, let us wait >70ms before trying to disable crz to avoid
        # a race condition with the stock system, where the second cancel from openpilot
        # will disable the crz 'main on'. crz ctrl msg runs at 50hz. 70ms allows us to
        # read 3 messages and most likely sync state before we attempt cancel.
        self.brake_counter = self.brake_counter + 1
        if self.frame % 10 == 0 and not (CS.out.brakePressed and self.brake_counter < 7):
          # Cancel Stock ACC if it's enabled while OP is disengaged
          # Send at a rate of 10hz until we sync with stock ACC state
          can_sends.append(mazdacan.create_button_cmd(self.packer, self.CP, CS.crz_btns_counter, Buttons.CANCEL))
      else:
        self.brake_counter = 0
        if CC.cruiseControl.resume and self.frame % 5 == 0:
          # Mazda Stop and Go requires a RES button (or gas) press if the car stops more than 3 seconds
          # Send Resume button when planner wants car to move
          can_sends.append(mazdacan.create_button_cmd(self.packer, self.CP, CS.crz_btns_counter, Buttons.RESUME))

      # send HUD alerts (no FSC means no camera lane-info to drive them)
      if self.frame % 50 == 0 and not (self.CP.flags & MazdaSafetyFlags.NO_FSC):
        ldw = CC.hudControl.visualAlert == VisualAlert.ldw
        steer_required = CC.hudControl.visualAlert == VisualAlert.steerRequired
        # TODO: find a way to silence audible warnings so we can add more hud alerts
        steer_required = steer_required and CS.lkas_allowed_speed
        can_sends.append(mazdacan.create_alert_command(self.packer, CS.cam_laneinfo, ldw, steer_required))

      if self.CP.openpilotLongitudinalControl:
        hold = False
        if CS.out.standstill:
          hold = (self.frame - self.hold_timer_frame) < 600
        else:
          self.hold_timer_frame = self.frame

          stock_acc = CS.crz_info["ACCEL_CMD"]
          op_acc = CC.actuators.accel * 1150
          op_acc = max(-1000, min(op_acc, 1000))

          if self.params.get_bool("BlendedACC"):
            if CC.longActive:
              if not self.long_active_last:
                self.acc_filter.initialized = False
              target_acc = op_acc if bool(self.experimental_mode) else stock_acc
              acc_output = self.acc_filter.update(target_acc)
            else:
              acc_output = stock_acc
          else:
            acc_output = op_acc

          acc_output = max(-1000, min(acc_output, 1000))
          CS.crz_info["ACCEL_CMD"] = acc_output

        if self.frame % 2 == 0:
          can_sends.extend(mazdacan.create_radar_command(self.packer, self.frame, CC.longActive, CS, hold))

    elif self.CP.flags & MazdaSafetyFlags.GEN2:
      if self.CP.openpilotLongitudinalControl:
        stock_acc = CS.acc["ACCEL_CMD"]
        op_acc = (CC.actuators.accel * 200) + 2000

        if self.params.get_bool("BlendedACC"):
          if CC.longActive:
            if not self.long_active_last:
              self.acc_filter.initialized = False
            target_acc = op_acc if bool(self.experimental_mode) else stock_acc
            acc_output = self.acc_filter.update(target_acc)
          else:
            acc_output = stock_acc
        else:
          acc_output = op_acc if CC.longActive else stock_acc

        CS.acc["ACCEL_CMD"] = acc_output

      resume = False
      hold = False
      if self.frame % 2 == 0: # send ACC command at 50hz
        """
        Without this hold/resum logic, the car will only stop momentarily.
        It will then start creeping forward again. This logic allows the car to
        apply the electric brake to hold the car. The hold delay also fixes a
        bug with the stock ACC where it sometimes will apply the brakes too early
        when coming to a stop.
        """
        if CS.out.standstill: # if we're stopped
          if not ((self.frame - self.hold_delay_frame) < 50): # and we have been stopped for more than hold_delay duration. This prevents a hard brake if we aren't fully stopped.
            if ((CC.cruiseControl.resume and CC.actuators.longControlState != LongCtrlState.stopping) or
                CC.cruiseControl.override or CS.out.gasPressed or
                (CC.actuators.longControlState == LongCtrlState.starting) or CS.acc["RESUME"]): # if we are resuming or overriding, we want to release the brake
              self.resume_timer_frame = self.frame # reset the resume timer so its active
            else: # otherwise we're holding
              hold = (self.frame - self.hold_timer_frame) < 600 # hold for 6s. This allows the electric brake to hold the car.

        else: # if we're moving
          self.hold_timer_frame = self.frame # reset the hold timer so its active when we stop
          self.hold_delay_frame = self.frame # reset the hold delay

        resume = (self.frame - self.resume_timer_frame) < 50 # stay on for 0.5s to release the brake. This allows the car to move.
        can_sends.append(mazdacan.create_acc_cmd(self.packer, CS.acc, hold, resume))


    # send steering command
    can_sends.extend(mazdacan.create_steering_control(
      self.packer, self.CP, self.frame, apply_torque, CS.cam_lkas,
      ti_apply_torque if self.CP.flags & MazdaSafetyFlags.TORQUE_INTERCEPTOR else None))
    if _projection is not None and len(can_sends) > 0:
      # only an actual steering packet counts as steering evidence (a trailing
      # cruise-button or HUD packet must not be mislabeled as such)
      if self.CP.flags & (MazdaSafetyFlags.GEN2 | MazdaSafetyFlags.GEN3):
        if can_sends[-1][0] == 0x249:
          _projection.emit('mazda.EPS_LKAS', apply_torque, CC.latActive, can_sends[-1])
      elif not (self.CP.flags & MazdaSafetyFlags.TORQUE_INTERCEPTOR) and can_sends[-1][0] == 0x243:
        _projection.emit('mazda.CAM_LKAS', apply_torque, CC.latActive, can_sends[-1])
      # GEN1 TI has two independent torque paths; a single observation would be
      # ambiguous, so no projection is emitted for that configuration.

    new_actuators = CC.actuators.as_builder()
    new_actuators.torque = apply_torque / self.ccp.STEER_MAX
    new_actuators.torqueOutputCan = apply_torque

    self.long_active_last = CC.longActive
    self.frame += 1
    if _projection is not None:
      _projection.finish(new_actuators.torque)
    return new_actuators, can_sends
