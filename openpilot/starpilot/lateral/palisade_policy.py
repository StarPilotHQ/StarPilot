import numpy as np

from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.starpilot.lateral import palisade_shaping as shaping
from openpilot.starpilot.lateral.hkg_torque_policy import HKGShapedTorquePolicy, supported_torque_cp


PALISADE_CARS = frozenset((CAR.HYUNDAI_PALISADE, CAR.HYUNDAI_PALISADE_2023,))


def supported_cp(cp):
  return supported_torque_cp(cp, PALISADE_CARS)


class PalisadeTorquePolicy(HKGShapedTorquePolicy):
  FACTOR_MULT = shaping.PALISADE_BASE_LAT_ACCEL_FACTOR_MULT
  supported_cp = staticmethod(supported_cp)

  def __init__(self, parent, cp):
    super().__init__(parent, cp)
    self.hda2 = cp.carFingerprint == CAR.HYUNDAI_PALISADE_2023 and bool(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG)
    self.center_output_taper = 0.27 if self.hda2 else shaping.PALISADE_CENTER_OUTPUT_TAPER_MAX
    self.feedforward_demand = 0.0
    self.feedforward_filter = FirstOrderFilter(0.0, 0.20, self.dt, initialized=False) if self.hda2 else None

  def update(self, active, cs, vm, params, safety_limited, curvature, curvature_limited, delay):
    if self.feedforward_filter is not None:
      if not active or cs.steeringPressed or self.previous_pressed or cs.vEgo <= 15.0:
        self.feedforward_filter.initialized = False
      self.feedforward_demand = curvature * cs.vEgo**2
      if active:
        self.feedforward_filter.update(self.feedforward_demand)
    return super().update(active, cs, vm, params, safety_limited, curvature, curvature_limited, delay)

  def feedforward(self, value, setpoint, jerk, speed):
    if self.feedforward_filter is not None and speed > 15.0:
      weight = min((speed - 15.0) / 7.0, 1.0)
      correction = max(-0.28, min(self.feedforward_filter.x - self.feedforward_demand, 0.28))
      value += weight * correction
    return value * shaping.get_palisade_ff_scale(setpoint, jerk, speed) * shaping.get_palisade_center_taper_scale(setpoint, speed)

  def friction(self, setpoint, jerk, speed):
    taper = shaping.get_palisade_center_taper_scale(setpoint, speed)
    threshold = shaping.get_palisade_friction_threshold(speed, setpoint, jerk)
    if self.hda2 and speed > 24. and abs(setpoint) < 1.0 and not self.previous_pressed:
      highway = float(np.interp(speed, (24., 29.), (0., 1.)))
      shallow = float(np.interp(abs(setpoint), (.25, 1.0), (1., 0.)))
      threshold += .20 * highway * shallow
    return threshold, 1.0 + (shaping.get_palisade_friction_scale(speed, setpoint, jerk) - 1.0) * taper

  def output(self, value, setpoint, speed):
    return value * shaping.get_palisade_center_output_scale(setpoint, speed, taper_max=self.center_output_taper)


  def output_context(self, value, setpoint, jerk, measurement, speed):
    output = self.output(value, setpoint, speed)
    if not self.hda2 or speed <= 24. or self.previous_pressed:
      return output
    highway = float(np.interp(speed, (24., 29.), (0., 1.)))
    transient = float(np.interp(abs(jerk), (.35, .9), (0., 1.)))
    small_curve = float(np.interp(abs(setpoint), (.18, .40, .55, .85), (0., 1., 1., 0.)))
    return output * (1.0 - .12 * highway * transient * small_curve)
