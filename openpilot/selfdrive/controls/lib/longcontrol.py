import numpy as np
import math
from opendbc.car.structs import car
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N
from openpilot.common.pid import PIDController
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.starpilot.longitudinal.extension import LongitudinalContext, create_extension
from openpilot.starpilot.longitudinal.takeoffs import TakeoffStart, eligible as takeoff_eligible

CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]

LongCtrlState = car.CarControl.Actuators.LongControlState


def long_control_state_trans(active, long_control_state, should_stop, brake_pressed, cruise_standstill):
  starting_condition = (not should_stop and
                        not cruise_standstill and
                        not brake_pressed)

  if not active:
    long_control_state = LongCtrlState.off

  else:
    if long_control_state == LongCtrlState.off:
      if not starting_condition:
        long_control_state = LongCtrlState.stopping
      else:
        long_control_state = LongCtrlState.pid

    elif long_control_state == LongCtrlState.stopping:
      if starting_condition:
        long_control_state = LongCtrlState.pid

    elif long_control_state == LongCtrlState.pid:
      if should_stop:
        long_control_state = LongCtrlState.stopping

  return long_control_state

class LongControl:
  def __init__(self, CP, startup_preferences=None):
    self.CP = CP
    self.long_control_state = LongCtrlState.off
    self.extension = create_extension(CP, startup_preferences)
    self.stopping_decel_rate = self.extension.stopping_decel_rate if self.extension is not None else 1.0
    self.pid = PIDController(self.extension.kp if self.extension is not None else 0.0,
                             (self.extension.ki if self.extension is not None and self.extension.ki is not None
                              else (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV)),
                             rate=1 / DT_CTRL)
    self.last_output_accel = 0.0
    self.takeoff_start = TakeoffStart() if takeoff_eligible(CP) else None
    self.takeoff_starting = False

  def reset(self, *, reset_start: bool = True):
    self.pid.reset()
    if self.extension is not None:
      self.extension.reset(reset_start=reset_start)

  def update(self, active, CS, a_target, should_stop, accel_limits, *, context=None):
    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    context = context if context is not None else LongitudinalContext()
    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    planner_target = a_target
    previous_state = self.long_control_state
    enabled_takeoff = bool(context.faster_takeoffs and self.takeoff_start is not None)
    ioniq_start = self.extension is not None and self.extension.ioniq6_start is not None
    native_previous = (LongCtrlState.pid if previous_state == LongCtrlState.starting and
                       (self.takeoff_starting or enabled_takeoff and ioniq_start) else previous_state)
    native_state = long_control_state_trans(active, native_previous, should_stop,
                                           CS.brakePressed, CS.cruiseState.standstill and
                                           not (self.extension is not None and self.extension.ignore_cruise_standstill))
    owned_start = self.extension is not None and any((self.extension.gm_start, self.extension.vehicle_stop,
                                                     self.extension.stopping_policy))
    selected = bool(enabled_takeoff and not owned_start and self.takeoff_start.select(
      active=active, CS=CS, target=a_target, should_stop=should_stop, limits=accel_limits,
      evidence=context.start_evidence, stock_hold_allowed=ioniq_start))
    if selected:
      native_state = LongCtrlState.starting
    self.long_control_state = native_state
    if self.extension is not None:
      self.long_control_state = self.extension.transition(
        native_state, previous_state, active, CS, a_target, should_stop, accel_limits,
        context=context)
    self.takeoff_starting = selected and self.long_control_state == LongCtrlState.starting
    if self.long_control_state == LongCtrlState.off:
      self.reset()
      output_accel = 0.

    elif self.long_control_state == LongCtrlState.stopping:
      output_accel = self.last_output_accel
      if output_accel > self.CP.stopAccel:
        output_accel = min(output_accel, 0.0)
        # TODO: can we just go straight to stopAccel?
        output_accel -= self.stopping_decel_rate * DT_CTRL  # m/s^2/s while trying to stop
      if self.extension is not None:
        output_accel = self.extension.stopping_output(output_accel, a_target, should_stop, CS)
      if (context.force_stop_braking and self.extension is not None and self.extension.ioniq6_start is not None and
          should_stop and not CS.standstill and CS.vEgo > .3 and math.isfinite(a_target)):
        output_accel = min(a_target, 0.0)
      self.reset(reset_start=False)

    elif self.long_control_state == LongCtrlState.starting and self.extension is not None and self.extension.starting:
      output_accel = self.extension.starting_output(self.pid, a_target, accel_limits, context)

    else:  # LongCtrlState.pid
      if self.extension is not None:
        a_target = self.extension.target(a_target, CS, should_stop, self.last_output_accel, context)
      error = a_target - CS.aEgo
      feedforward, freeze_integrator = a_target, False
      if self.extension is not None:
        feedforward, freeze_integrator = self.extension.prepare_pid(
          self.pid, a_target, error, CS, self.last_output_accel, accel_limits, should_stop, context)
      output_accel = self.pid.update(error, speed=CS.vEgo, feedforward=feedforward,
                                     freeze_integrator=freeze_integrator)
      if self.extension is not None:
        output_accel = self.extension.shape_output(output_accel, a_target, error, CS, self.last_output_accel,
                                                  previous_state, should_stop, context)

    if self.takeoff_starting:
      output_accel = min(output_accel, planner_target)
    self.last_output_accel = np.clip(output_accel, accel_limits[0], accel_limits[1])
    return self.last_output_accel

