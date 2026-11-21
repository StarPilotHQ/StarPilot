"""RAV4 TSS2 friction/taper stages on the modern torque controller."""
import math

import numpy as np

from opendbc.car import structs
from openpilot.common.constants import CV
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.lateral.torque_shaping import get_standard_friction_threshold, sigmoid


def supported_cp(cp):
  if (cp.brand != "toyota" or cp.carFingerprint != CAR.TOYOTA_RAV4_TSS2 or cp.notCar or cp.passive or cp.dashcamOnly or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or cp.lateralTuning.which() != "torque"):
    return False
  tune = cp.lateralTuning.torque
  return (math.isfinite(tune.latAccelFactor) and tune.latAccelFactor > 0 and
          math.isfinite(tune.latAccelOffset) and math.isfinite(tune.friction) and tune.friction >= 0)


def center_envelope(setpoint, speed):
  return sigmoid((13.0 - max(speed, 0.0)) / 3.0) * sigmoid((0.30 - abs(setpoint)) / 0.08)


def friction_threshold(speed, setpoint, jerk):
  base = get_standard_friction_threshold(speed) * (1.0 + .14 * center_envelope(setpoint, speed))
  curve = float(np.interp(abs(setpoint), [.30, .85], [1.0, 0.0]))
  calm = float(np.interp(abs(jerk), [.25, .75], [1.0, 0.0]))
  increment = float(np.interp(max(speed, 0.0), [0., 2.5, 5., 10., 15., 25.], [0., 0., .04, .065, .05, .025]))
  return base + increment * curve * calm


def center_output_scale(setpoint, speed):
  speed_weight = float(np.interp(max(speed, 0.0), [15 * CV.MPH_TO_MS, 20 * CV.MPH_TO_MS,
                                                   40 * CV.MPH_TO_MS, 50 * CV.MPH_TO_MS], [0., 1., 1., 0.]))
  center_weight = float(np.interp(abs(setpoint), [.30, .65], [1., 0.]))
  return 1.0 - (.12 + .08 * speed_weight * center_weight) * center_envelope(setpoint, speed)


class Rav4TSS2TorquePolicy:
  def __init__(self, parent, cp):
    if not supported_cp(cp):
      raise ValueError("Unsupported RAV4 TSS2 torque profile")
    self.parent = parent

  friction_threshold = staticmethod(friction_threshold)
  output_scale = staticmethod(center_output_scale)

  def update(self, active, cs, vm, params, safety_limited, curvature, curvature_limited, delay):
    return self.parent.update_standard(active, cs, vm, params, safety_limited, curvature, curvature_limited, delay,
                                       friction_policy=self)
