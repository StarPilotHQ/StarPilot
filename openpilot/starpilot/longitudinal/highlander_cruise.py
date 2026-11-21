"""Gas Highlander driver-speed reductions through the existing native cruise horizon."""

import math
import numpy as np

from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N, get_accel_from_plan
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (
  LongitudinalMpc, T_IDXS, T_DIFFS, get_safe_obstacle_distance, A_CHANGE_COST, J_EGO_COST, DANGER_ZONE_COST,
)
from openpilot.selfdrive.modeld.constants import ModelConstants


def owner_for(cp, dt):
  if (cp.carFingerprint == CAR.TOYOTA_HIGHLANDER_TSS2 and not cp.flags & ToyotaFlags.HYBRID and
      cp.openpilotLongitudinalControl and not (cp.passive or cp.dashcamOnly or cp.notCar)):
    return ButtonCruise(cp.longitudinalActuatorDelay + dt, dt)
  return None


class ButtonCruise:
  def __init__(self, action_t, dt):
    self.action_t, self.dt = action_t, dt
    self.native = None
    self.filtered_speed = None
    self.planned_accel = 0.
    self.drive = self.last_ns = 0
    self.previous_set = None
    self.active = False

  def stop(self):
    if self.active and self.native is not None:
      self.native.reset()
    self.active = False
    self.filtered_speed = None

  def sample(self, sm, *, now_ns, drive_id, target_speed, brake_floor, speed, previous_accel, reference, blocked):
    services = ('carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState', 'modelV2')
    car = sm['carState']
    setting = float(car.vCruise) * CV.KPH_TO_MS
    values = (setting, target_speed, brake_floor, speed, car.vEgo, previous_accel, self.action_t, self.dt)
    current = all(sm.valid[name] and sm.alive[name] and
                  drive_id < sm.logMonoTime[name] <= now_ns and now_ns - sm.logMonoTime[name] <= 150_000_000 and
                  drive_id < int(sm.recv_time[name] * 1e9) <= now_ns and
                  now_ns - int(sm.recv_time[name] * 1e9) <= 150_000_000 for name in services)
    valid = (drive_id > 0 and current and all(math.isfinite(value) for value in values) and
             0. < setting < 70. and abs(setting - target_speed) < 1e-6 and
             -3.5 <= brake_floor < 0. and speed > 2.5 and 0. < self.dt <= .25 and self.action_t > 0. and
             sm['selfdriveState'].enabled and sm['carControl'].longActive and car.canValid and not car.canTimeout and
             not (car.gasPressed or car.brakePressed or car.standstill or sm['controlsState'].forceDecel) and
             not sm['selfdriveState'].experimentalMode)
    if (not valid or self.drive != drive_id or now_ns <= self.last_ns or
        self.last_ns and now_ns - self.last_ns > 150_000_000):
      self.stop()
      self.previous_set = setting if valid else None
      self.drive, self.last_ns = drive_id, now_ns
      return None
    decreased = self.previous_set is not None and setting < self.previous_set - .001
    increased = self.previous_set is not None and setting > self.previous_set + .001
    self.previous_set, self.last_ns = setting, now_ns
    if blocked or increased or car.vEgo <= setting + .05 or reference.solution_status != 0:
      self.stop()
      return None
    if decreased:
      self.active = True
    if not self.active:
      return None
    weights = reference._cached_cost_weights
    if (weights is None or not np.all(np.isfinite(reference.params)) or
        not .5 <= reference.params[0, 4] <= 3. or reference.params[0, 1] < 0.):
      self.stop()
      return None
    if self.native is None:
      self.native = LongitudinalMpc(self.dt)
    native = self.native
    stage, constraints = weights
    if not all(math.isfinite(value) and value >= 0. for value in (*stage, *constraints)):
      self.stop()
      return None
    # Stable 0b2061 cruise costs: retain saved jerk factors, not lead-MPC cost multipliers.
    mph = car.vEgo * CV.MS_TO_MPH
    obstacle_cost = float(np.interp(mph, [0., 35., 55., 70.], [3., 3., 2.5, 2.]))
    distance_factor = 1. + .4 * float(np.interp(mph, [0., 35., 55., 70.], [0., .06, .06, .05]))
    native.set_cost_weights((obstacle_cost, 0., 0., 0., stage[4] / A_CHANGE_COST * distance_factor,
                             stage[5] / J_EGO_COST * distance_factor),
                            (*constraints[:3], constraints[3] / DANGER_ZONE_COST * distance_factor))
    if self.filtered_speed is None:
      self.filtered_speed = FirstOrderFilter(car.vEgo, 2., self.dt)
      self.planned_accel = previous_accel
    self.filtered_speed.update(car.vEgo)
    speed = max(0., self.filtered_speed.x)
    native.set_cur_state(speed, self.planned_accel)
    # Dom's cruise obstacle restores a shaped speed response without changing lead MPC costs.
    horizon = np.clip(setting, speed + T_IDXS * brake_floor * 1.05,
                      speed + T_IDXS * max(0., float(reference.params[0, 1])) * 1.05)
    native.params[:] = reference.params
    native.params[:, 2] = np.cumsum(T_DIFFS * horizon) + get_safe_obstacle_distance(horizon, reference.params[0, 4])
    native.params[:, 3] = native.a_prev
    status = native.run()
    if status != 0 or not np.all(np.isfinite(native.a_solution)) or not np.all(np.isfinite(native.v_solution)):
      self.stop()
      return None
    next_accel = float(np.interp(self.dt, T_IDXS, native.a_solution))
    self.filtered_speed.x += self.dt * (self.planned_accel + next_accel) / 2.
    self.planned_accel = next_accel
    grid = ModelConstants.T_IDXS[:CONTROL_N]
    target = get_accel_from_plan(np.interp(grid, T_IDXS, native.v_solution),
                                 np.interp(grid, T_IDXS, native.a_solution), grid, self.action_t)
    if not math.isfinite(target):
      self.stop()
      return None
    return float(np.clip(target, brake_floor, 0.))
