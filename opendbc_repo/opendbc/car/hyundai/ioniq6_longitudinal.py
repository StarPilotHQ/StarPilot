"""Ioniq 6 acceleration calibration, independent of ECU ownership and CAN packing.

The active-drive calibration comes from StarPilot 678af783. Inactive control and
pedal override reset its history so a later engagement cannot reuse old demand.
"""

from dataclasses import dataclass

import numpy as np

from opendbc.car import DT_CTRL, structs
from opendbc.car.hyundai.values import CarControllerParams

LongState = structs.CarControl.Actuators.LongControlState
RESPONSE = 1.2
MIN_JERK = 0.5 * RESPONSE
MAX_JERK = 4.8 * RESPONSE
ACCEL_STEP = 6.0 / 50.0 * RESPONSE
DECEL_STEP = 15.0 / 50.0 * RESPONSE
LOOKAHEAD_SPEEDS = (2.0, 5.0, 20.0)
LOOKAHEAD_TIMES = (0.3 / RESPONSE, 0.45 / RESPONSE, 0.6 / RESPONSE)
LOWER_JERK_ERRORS = (-2.0, -1.5, -1.0, -0.25, -0.1, -0.025, -0.01, -0.005)
LOWER_JERK_VALUES = tuple(value * (MAX_JERK / 3.3) for value in (3.3, 1.5, 1.0, 0.8, 0.7, 0.65, 0.55, 0.5))


def launch_floor(speed: float) -> float:
  return float(np.interp(speed, (0.0, 0.6, 1.25, 2.5), (0.75, 0.6, 0.4, 0.0)))


@dataclass
class Ioniq6LongitudinalState:
  desired_accel: float = 0.0
  actual_accel: float = 0.0
  accel_last: float = 0.0
  jerk_upper: float = 0.0
  jerk_lower: float = 0.0
  launch_active: bool = False
  stopping: bool = False
  long_control_state_last: int = LongState.off


@dataclass(frozen=True)
class AccelerationRequest:
  accel: float = 0.0
  stopping: bool = False
  jerk_upper: float = 3.0
  jerk_lower: float = 1.0


def update_calibration(state: Ioniq6LongitudinalState, accel: float, speed: float, measured_accel: float,
                       control_state: int) -> None:
  """Advance the original active-control calibration once per 50 ms."""
  starting = control_state == LongState.starting
  state.stopping = control_state == LongState.stopping
  restarting = (state.long_control_state_last in (LongState.stopping, LongState.starting) and
                control_state in (LongState.starting, LongState.pid) and accel > 0.0 and speed < 0.5)

  if accel <= 0.0 or speed >= 2.5 or accel < launch_floor(speed):
    state.launch_active = False
  elif starting or state.launch_active or (state.long_control_state_last == LongState.starting and control_state == LongState.pid):
    state.launch_active = True

  hold_jerk = float(np.interp(speed, (0.0, 0.15, 0.6, 1.2, 2.0, 3.0), (0.35, 0.40, 0.48, 0.65, 0.85, 1.10))) * RESPONSE if state.stopping else 0.0
  upper_limit = float(np.interp(speed, (0.0, 5.0, 20.0), (2.0, 3.0, 2.0))) * RESPONSE if control_state == LongState.pid else MIN_JERK
  if state.stopping and accel > state.accel_last:
    # Follow an already gentler planner request without weakening stronger braking.
    upper_limit = hold_jerk
  lower_limit = float(np.interp(speed, (0.0, 5.0, 20.0), (5.0, 3.5, 3.0))) * RESPONSE
  horizon = float(np.interp(speed, LOOKAHEAD_SPEEDS, LOOKAHEAD_TIMES))
  desired_jerk = float(np.clip((accel - state.accel_last) / horizon, -MAX_JERK, MAX_JERK))
  measured_error = measured_accel - state.accel_last
  lower_jerk = float(np.interp(measured_error, LOWER_JERK_ERRORS, LOWER_JERK_VALUES)) if measured_error < 0.0 else MIN_JERK
  state.jerk_upper = min(max(desired_jerk, MIN_JERK), upper_limit)
  state.jerk_lower = min(lower_jerk, lower_limit)

  if state.stopping:
    # A saturated braking request must survive the comfort soft-stop cap.
    if speed <= 2.0 and (speed <= .01 or accel > CarControllerParams.ACCEL_MIN):
      brake_cap = float(np.interp(speed, (0.0, 0.08, 0.25, 0.6, 1.2, 2.0, 3.0), (-0.15, -0.16, -0.22, -0.42, -0.78, -1.15, -1.40)))
      state.desired_accel = min(0.0, max(accel, brake_cap))
      state.jerk_upper = min(state.jerk_upper, hold_jerk)
    else:
      state.desired_accel = float(np.clip(accel, CarControllerParams.ACCEL_MIN, 0.0))
  else:
    state.desired_accel = float(np.clip(accel, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))
    if state.launch_active:
      state.desired_accel = max(state.desired_accel, launch_floor(speed))
      state.jerk_upper = max(state.jerk_upper, float(np.interp(speed, (0.0, 2.5), (4.8, 3.2))) * RESPONSE)
      state.jerk_lower = max(state.jerk_lower, 1.0)
    if restarting:
      release_jerk = float(np.interp(speed, (0.0, 0.15, 0.5), (3.6 * RESPONSE, 4.2 * RESPONSE, 4.8 * RESPONSE)))
      state.jerk_upper = min(state.jerk_upper, release_jerk)

  step = (state.jerk_upper if state.desired_accel >= state.accel_last else state.jerk_lower) * DT_CTRL * 5.0
  state.actual_accel = float(np.clip(state.desired_accel, state.accel_last - step, state.accel_last + step))
  state.accel_last = state.actual_accel
  state.long_control_state_last = control_state


class Ioniq6LongitudinalPolicy:
  def __init__(self):
    self.state = Ioniq6LongitudinalState()
    self.was_active = False

  def update(self, frame: int, *, active: bool, override: bool, accel: float, speed: float, measured_accel: float,
             control_state: int, last_sent_accel: float) -> AccelerationRequest:
    enabled = active and not override and control_state in (LongState.starting, LongState.pid, LongState.stopping)
    if not enabled:
      self.state = Ioniq6LongitudinalState()
      self.was_active = False
      return AccelerationRequest()

    accel = float(np.clip(accel, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))
    # The 20 Hz calibration may not run on this 100 Hz call. Withdraw the
    # launch floor immediately when a fresh host target becomes softer; the
    # ordinary bounded decrease below still handles residual output.
    if accel <= 0.0 or speed >= 2.5 or accel < launch_floor(speed):
      self.state.launch_active = False
    if frame % 5 == 0 or not self.was_active:
      update_calibration(self.state, accel, speed, measured_accel, control_state)
    self.was_active = True
    smoothed = accel >= self.state.actual_accel or self.state.launch_active or self.state.stopping
    if smoothed:
      return AccelerationRequest(self.state.actual_accel, self.state.stopping, self.state.jerk_upper, self.state.jerk_lower)

    # Preserve the faster response to a new braking request, using the last
    # actual 50 Hz SCC output rather than integrating at the 100 Hz host rate.
    output = float(np.clip(accel, last_sent_accel - DECEL_STEP, last_sent_accel + ACCEL_STEP))
    self.state.desired_accel = accel
    self.state.actual_accel = self.state.accel_last = output
    self.state.jerk_upper, self.state.jerk_lower = 3.0, 5.0
    self.state.launch_active = False
    self.state.stopping = control_state == LongState.stopping
    self.state.long_control_state_last = control_state
    return AccelerationRequest(output, self.state.stopping, 3.0 if control_state == LongState.pid else 1.0, 5.0)
