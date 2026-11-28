import os
import time

import numpy as np

from opendbc.can import CANPacker
from opendbc.car import Bus, DT_CTRL, structs
from opendbc.car.lateral import apply_meas_steer_torque_limits
from opendbc.car.chrysler import chryslercan
from opendbc.car.chrysler.aol_contract import qualified as aol_qualified
from opendbc.car.chrysler.brake_hold import brake_hold_supported, create_das_3_command
from opendbc.car.chrysler.values import (
  CAR, CUSW_CARS, RAM_CARS, RAM_DT, CarControllerParams, ChryslerFlags,
  WK2_2019_BRAKE_ENTER, WK2_2019_BRAKE_EXIT, WK2_2019_STANDSTILL_DECEL, WK2_2019_STOCK_BRAKE_FLOOR,
  WK2_2019_TORQUE_BASE_V, WK2_2019_TORQUE_GAIN_V, WK2_2019_TORQUE_SPEED_BP,
)
from opendbc.car.structs import CarState
from opendbc.car.interfaces import CarControllerBase

LongCtrlState = structs.CarControl.Actuators.LongControlState


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.apply_torque_last = 0
    self.accel_last = 0.0

    self.hud_count = 0
    self.last_lkas_falling_edge = 0
    self.lkas_control_bit_prev = False
    self.last_button_frame = 0
    self.jeep_hold_replay = "REPLAY" in os.environ
    self.jeep_decel = -2.0
    self.last_das_3_counter = -1
    self.jeep_hold_cancelled = False
    self.jeep_cruise_was_active = False
    self.jeep_long_braking = False

    self.packer = CANPacker(dbc_names[Bus.pt])
    self.params = CarControllerParams(CP)

  def update(self, CC, CS, now_nanos):
    _projection = self.learning_projection_observer
    if _projection is not None:
      _projection.begin(CC.actuators.torque, self.frame)
    can_sends = []

    lkas_active = CC.latActive and self.lkas_control_bit_prev

    # cruise buttons
    if (self.frame - self.last_button_frame) * DT_CTRL > 0.05:
      das_bus = 2 if self.CP.carFingerprint in RAM_CARS else 0

      # ACC cancellation
      pause_stock_acc = bool(aol_qualified(self.CP, marked_only=True) and
        CS.aol_pause_longitudinal and 0 < CS.aol_pause_longitudinal_stamp_ns <= now_nanos and
        now_nanos - CS.aol_pause_longitudinal_stamp_ns <= 30_000_000 and
        CS.out.canValid and not CS.out.canTimeout)
      if CC.cruiseControl.cancel or (pause_stock_acc and CS.out.cruiseState.enabled):
        self.last_button_frame = self.frame
        can_sends.append(chryslercan.create_cruise_buttons(self.packer, CS.button_counter + 1, das_bus, cancel=True))

      # ACC resume from standstill
      elif (CC.cruiseControl.resume and not pause_stock_acc and
            (not aol_qualified(self.CP, marked_only=True) or CS.out.cruiseState.enabled)):
        self.last_button_frame = self.frame
        can_sends.append(chryslercan.create_cruise_buttons(self.packer, CS.button_counter + 1, das_bus, resume=True))

    # HUD alerts
    if self.frame % 25 == 0:
      if CS.lkas_car_model != -1:
        can_sends.append(chryslercan.create_lkas_hud(self.packer, self.CP, lkas_active, CC.hudControl.visualAlert,
                                                     self.hud_count, CS.lkas_car_model, CS.auto_high_beam))
        self.hud_count += 1

    # steering
    if self.frame % self.params.STEER_STEP == 0:

      # TODO: can we make this more sane? why is it different for all the cars?
      lkas_control_bit = self.lkas_control_bit_prev
      if self.CP.flags & ChryslerFlags.STEERING_SPEED_BYPASS:
        lkas_control_bit = CC.latActive
      elif self.CP.carFingerprint in RAM_DT:
        # Arm in the EPS speed window, then keep the request through lower speeds.
        if self.CP.minEnableSpeed <= CS.out.vEgo <= self.CP.minEnableSpeed + 0.5:
          lkas_control_bit = True
        if self.CP.minEnableSpeed >= 14.5 and CS.out.gearShifter != CarState.GearShifter.drive:
          lkas_control_bit = False
      elif CS.out.vEgo > self.CP.minSteerSpeed:
        lkas_control_bit = True
      elif self.CP.flags & ChryslerFlags.HIGHER_MIN_STEERING_SPEED:
        if CS.out.vEgo < (self.CP.minSteerSpeed - 3.0):
          lkas_control_bit = False
      elif self.CP.carFingerprint in RAM_CARS:
        if CS.out.vEgo < (self.CP.minSteerSpeed - 0.5):
          lkas_control_bit = False
      elif self.CP.carFingerprint in CUSW_CARS:
        if CS.out.vEgo < (self.CP.minSteerSpeed - 2.0):
          lkas_control_bit = False

      # EPS faults if LKAS re-enables too quickly
      lkas_control_bit = lkas_control_bit and (self.frame - self.last_lkas_falling_edge > 200)

      if not lkas_control_bit and self.lkas_control_bit_prev:
        self.last_lkas_falling_edge = self.frame
      self.lkas_control_bit_prev = lkas_control_bit

      # steer torque
      new_torque = int(round(CC.actuators.torque * self.params.STEER_MAX))
      if _projection is not None:
        _projection.input(new_torque, self.params.STEER_MAX, self.apply_torque_last, CS.out.steeringTorqueEps, self.params, limiter='measured')
      apply_torque = apply_meas_steer_torque_limits(new_torque, self.apply_torque_last, CS.out.steeringTorqueEps, self.params)
      if not lkas_active or not lkas_control_bit:
        apply_torque = 0
      self.apply_torque_last = apply_torque

      can_sends.append(chryslercan.create_lkas_command(self.packer, self.CP, int(apply_torque), lkas_control_bit))
      if _projection is not None:
        _projection.emit('chrysler.LKAS_COMMAND', apply_torque, lkas_control_bit, can_sends[-1])

    # WK2 2019 alpha-long only: replace DAS_3 while engaged. No other Chrysler fingerprint.
    if (self.CP.carFingerprint == CAR.JEEP_GRAND_CHEROKEE_2019 and
        self.CP.openpilotLongitudinalControl):
      CS.brake_hold = False
      self.update_jeep_wk2_2019_long(CC, CS, now_nanos, can_sends)
    elif brake_hold_supported(self.CP) and self.CP.safetyConfigs[0].safetyParam == 4:
      withdraw = bool(aol_qualified(self.CP, marked_only=True) and CS.aol_pause_longitudinal and
                      0 < CS.aol_pause_longitudinal_stamp_ns <= now_nanos and
                      now_nanos - CS.aol_pause_longitudinal_stamp_ns <= 30_000_000)
      self.update_jeep_brake_hold(CC, CS, now_nanos, can_sends, withdraw=withdraw)
    else:
      CS.brake_hold = False

    self.frame += 1

    new_actuators = CC.actuators.as_builder()
    new_actuators.torque = self.apply_torque_last / self.params.STEER_MAX
    new_actuators.torqueOutputCan = self.apply_torque_last
    new_actuators.accel = self.accel_last

    if _projection is not None:
      _projection.finish(new_actuators.torque)
    return new_actuators, can_sends

  def update_jeep_wk2_2019_long(self, CC, CS, now_nanos, can_sends):
    """JEEP_GRAND_CHEROKEE_2019-only cooperative DAS_3 longitudinal commands."""
    if self.frame % self.params.LONG_STEP != 0:
      return
    now_boot_ns = now_nanos if self.jeep_hold_replay else time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    if (not CS.das_3 or not CS.out.canValid or CS.out.canTimeout or
        not 0 <= now_boot_ns - CS.das_3_timestamp <= 100_000_000):
      self.accel_last = 0.0
      self.jeep_long_braking = False
      return

    counter = int(CS.das_3["COUNTER"])
    changed = counter != self.last_das_3_counter
    self.last_das_3_counter = counter

    # Cooperative: only replace DAS_3 while OP long is actively engaged.
    long_active = bool(CC.longActive and CC.enabled)
    if not long_active:
      self.accel_last = 0.0
      self.jeep_long_braking = False
      return

    stock = CS.das_3
    stock_decel_req = int(stock["ACC_DECEL_REQ"])
    stock_braking = bool(stock["ACC_ACTIVE"]) and stock_decel_req == 1
    stock_accel = float(stock["ACC_DECEL"])

    # OEM collision handling owns DAS_3: stop replacing it so the stock warning and braking
    # reach the ESC unmodified. Hard stock braking yields even without ACC_ACTIVE (AEB need not
    # set it). Panda safety enforces the same yield.
    if (CS.out.stockFcw or stock["COLLISION_BRK_PREP"] or stock["ACC_FAULTED"] or stock_decel_req > 1 or
        (stock_decel_req == 1 and stock_accel < self.params.ACCEL_MIN)):
      if stock_decel_req == 1:
        # Resume from the stock request so the hand-back does not step the brakes.
        self.accel_last = max(self.params.ACCEL_MIN, min(self.accel_last, stock_accel))
        self.jeep_long_braking = True
      return

    actuators = CC.actuators
    standstill = CS.out.standstill
    stopping = actuators.longControlState == LongCtrlState.stopping
    starting = (actuators.longControlState == LongCtrlState.starting or
                (actuators.longControlState == LongCtrlState.pid and standstill))
    accel = float(actuators.accel)
    if stopping and standstill:
      accel = min(accel, WK2_2019_STANDSTILL_DECEL)
    accel = max(self.params.ACCEL_MIN, min(self.params.ACCEL_MAX, accel))
    max_delta = 0.1 * self.params.LONG_STEP
    accel = max(self.accel_last - max_delta, min(self.accel_last + max_delta, accel))
    if stock_braking and not standstill and stock_accel < WK2_2019_STOCK_BRAKE_FLOOR:
      # Stock radar wants firm braking: never brake less than it, and do not rate limit into it.
      accel = min(accel, stock_accel)
    self.accel_last = accel

    if accel < WK2_2019_BRAKE_ENTER:
      self.jeep_long_braking = True
    elif accel > WK2_2019_BRAKE_EXIT:
      self.jeep_long_braking = False

    v_ego = CS.out.vEgo
    torque = (np.interp(v_ego, WK2_2019_TORQUE_SPEED_BP, WK2_2019_TORQUE_BASE_V) +
              np.interp(v_ego, WK2_2019_TORQUE_SPEED_BP, WK2_2019_TORQUE_GAIN_V) * accel)
    torque = float(max(self.params.TORQUE_MIN, min(self.params.TORQUE_MAX, torque)))

    can_sends.append(chryslercan.create_das_3_long_command(
      self.packer, 2 if changed else 3, stock,
      braking=self.jeep_long_braking, accel=accel, torque=torque, stopping=stopping, starting=starting,
    ))
    # Resume button helps OEM leave standstill when starting from a stop (provisional).
    if starting and self.frame % 10 == 0:
      can_sends.append(chryslercan.create_cruise_buttons(self.packer, CS.button_counter + 1, 0, resume=True))

  def update_jeep_brake_hold(self, CC, CS, now_nanos, can_sends, *, withdraw=False):
    # CAN Event/parser timestamps are BOOTTIME. Card passes MONOTONIC for
    # ordinary live Chrysler controllers, and original CAN time during replay.
    # Select the established replay contract explicitly; never infer an offset
    # or renew the parser's original source timestamp at command time.
    now_boot_ns = now_nanos if self.jeep_hold_replay else time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    if (not CS.das_3 or not CS.out.canValid or CS.out.canTimeout or
        not 0 <= now_boot_ns - CS.das_3_timestamp <= 100_000_000):
      CS.brake_hold = False
      return
    counter = int(CS.das_3["COUNTER"])
    changed = counter != self.last_das_3_counter
    self.last_das_3_counter = counter
    active = CS.out.cruiseState.enabled
    if active and not self.jeep_cruise_was_active:
      self.jeep_hold_cancelled = False
    self.jeep_cruise_was_active = active
    if CC.cruiseControl.cancel or CS.jeep_driver_cancel or withdraw:
      self.jeep_hold_cancelled = True
    if self.jeep_hold_cancelled:
      CS.brake_hold = False
      return
    decel = float(CS.das_3["ACC_DECEL"])
    if not CS.brake_hold and CS.out.cruiseState.enabled and decel < -0.5 and CS.out.standstill:
      CS.brake_hold = True
      self.jeep_decel = -2.0
    if (CC.cruiseControl.cancel or CS.jeep_driver_cancel or withdraw or CS.out.gasPressed or CS.out.brakePressed or
        CS.out.gearShifter != CarState.GearShifter.drive or not CS.out.standstill or
        not CS.out.cruiseState.available or CS.out.accFaulted):
      CS.brake_hold = False
      return
    if not CS.brake_hold:
      return
    if CS.out.cruiseState.enabled:
      self.jeep_decel = max(-3.0, min(-0.5, min(self.jeep_decel, decel)))
      return
    can_sends.append(create_das_3_command(self.packer, 2 if changed else 3, self.jeep_decel, CS.das_3))
    if self.frame % 10 == 0:
      can_sends.append(chryslercan.create_cruise_buttons(self.packer, CS.button_counter + 1, 0, resume=True))
