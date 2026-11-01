"""StarPilot demand tuning on the current Ford wire protocol and native envelope."""
import math
import numpy as np

from opendbc.bluepilot_lateral.can.fordcan_ext import create_lat_ctl_msg, create_lat_ctl2_msg
from opendbc.bluepilot_lateral.core.values_ext import MODEL_T_IDXS
from opendbc.car import structs
from opendbc.car.lateral import ISO_LATERAL_ACCEL
from opendbc.car.ford.values import CAR, CarControllerParams, FordFlags
from openpilot.starpilot.car.ford.starpilot_tuning import (
  create_tuning, MAX_LATERAL_ACCEL, MACH_E_PATH_ANGLE_MAX, MACH_E_PATH_ANGLE_MAX_SPEED,
)


class StarPilotFordController:
  def __init__(self, cp, measured_curvature):
    self.cp = cp
    self.tuning = create_tuning(cp, measured_curvature)
    self.path_angle_last = 0.0

  def pack(self, owner, active, curvature, path_angle, precision=1):
    ramp = (2 if curvature or path_angle else 3) if active else 0
    if not curvature and not path_angle:
      precision = 1
    if self.cp.flags & FordFlags.CANFD:
      return [create_lat_ctl2_msg(owner.packer, owner.CAN, int(active), ramp, precision,
                                 0., -path_angle, -curvature, 0., (owner.frame // CarControllerParams.STEER_STEP) % 16)]
    return [create_lat_ctl_msg(owner.packer, owner.CAN, active, ramp, precision, 0., -path_angle, -curvature, 0.)]

  def step(self, owner, CC, CS, actuators, previous):
    state = CS.out
    active = bool(CC.latActive and state.canValid and not state.canTimeout and
                  not state.steerFaultTemporary and not state.steerFaultPermanent and
                  math.isfinite(state.vEgoRaw) and math.isfinite(actuators.curvature))
    if not active:
      self.tuning.update(structs.CarControl.new_message(latActive=False), CS, actuators)
      self.path_angle_last = 0.
      return self.pack(owner, False, 0., 0.), 0.
    self.tuning.set_inputs(owner.inputs.model, MODEL_T_IDXS, owner.inputs.lateral_delay, True)
    demand = self.tuning.update(CC, CS, actuators)
    speed = max(float(state.vEgoRaw), max(owner.speed_history, default=0) / 1000.)
    accel_cap = MAX_LATERAL_ACCEL if self.cp.flags & FordFlags.CANFD else ISO_LATERAL_ACCEL
    cap = min(.02, accel_cap / max(speed, 1.) ** 2)
    curvature = float(np.clip(demand.curvature, -cap, cap)) if demand.active else 0.
    packets, curvature = owner._constrain_curvature(
      CC, CS, actuators, previous, self.pack(owner, True, curvature, 0., demand.precision_type), curvature)
    requested_angle = 0.
    if (self.cp.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1 and len(owner.speed_history) == 6 and
        3. <= state.vEgoRaw and speed < MACH_E_PATH_ANGLE_MAX_SPEED and abs(curvature) >= .0195 and
        curvature * demand.path_angle > 0.):
      headroom = max(0., (MAX_LATERAL_ACCEL / max(speed, 1.) ** 2 - abs(curvature)) * speed)
      requested_angle = float(np.clip(demand.path_angle, -min(MACH_E_PATH_ANGLE_MAX, headroom),
                                      min(MACH_E_PATH_ANGLE_MAX, headroom)))
    # Keep the existing native path-angle ROC while releasing the old low-speed assistance.
    delta = float(np.interp(speed, [10., 15., 25.], [.055, .0425, .009]))
    path_angle = float(np.clip(requested_angle, self.path_angle_last - delta, self.path_angle_last + delta))
    path_angle = round(path_angle / .0005) * .0005
    if self.cp.flags & FordFlags.CANFD:
      if abs(curvature) * max(speed, 1.) ** 2 + abs(path_angle) * speed > MAX_LATERAL_ACCEL:
        # The library's no-curvature sentinel leaves room for a bounded path-angle unwind.
        curvature = 0.
      if abs(path_angle) * speed > MAX_LATERAL_ACCEL:
        self.path_angle_last = self.tuning.path_angle_last = self.tuning.curvature_last = 0.
        return self.pack(owner, False, 0., 0.), 0.
    if abs(curvature) > cap:
      curvature = 0.
    # Current firmware requires curvature_rate==0; retired extended-profile frames stay retired.
    packets = self.pack(owner, True, curvature, path_angle, demand.precision_type)
    self.tuning.curvature_last = curvature
    self.path_angle_last = self.tuning.path_angle_last = path_angle
    return packets, curvature
