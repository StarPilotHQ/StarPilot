"""Default torque law for exact conventional-cruise GM owners."""

import math
import numpy as np

from opendbc.car.gm.values import CAR, is_ordinary_cc_profile, is_ordinary_cc_stock_profile, is_conventional_cc_pedal_profile, malibu_hybrid_profile
from opendbc.car.structs import CarParams
from openpilot.starpilot.lateral.gm_ordinary_policy import GMOrdinaryTorquePolicy


def supported_cp(cp):
  if (not (is_ordinary_cc_profile(cp) or is_ordinary_cc_stock_profile(cp) or
           is_conventional_cc_pedal_profile(cp) or malibu_hybrid_profile(cp) is not None) or
      cp.steerControlType != CarParams.SteerControlType.torque or cp.lateralTuning.which() != 'torque'):
    return False
  tune = cp.lateralTuning.torque
  return (math.isfinite(tune.latAccelFactor) and tune.latAccelFactor > 0 and
          math.isfinite(tune.latAccelOffset) and math.isfinite(tune.friction) and tune.friction >= 0)


class OrdinaryCcTorquePolicy(GMOrdinaryTorquePolicy):
  def __init__(self, parent, cp):
    if not supported_cp(cp):
      raise ValueError('Unsupported conventional-cruise GM torque profile')
    self.yukon = cp.carFingerprint == CAR.GMC_YUKON_CC
    super().__init__(parent, cp)

  def feedforward(self, value, setpoint, jerk, speed):
    if not self.yukon:
      return value
    phase = math.tanh((setpoint * jerk) / .14)
    speed_weight = float(np.interp(speed, [12.0, 30.0], [0.0, 1.0]))
    x = (abs(setpoint) - .35) / .18
    z = math.exp(-x) if x >= 0.0 else math.exp(x)
    lat_weight = 1.0 / (1.0 + z) if x >= 0.0 else z / (1.0 + z)
    scale = 1.0 + ((.08 * max(phase, 0.0) - .12 * max(-phase, 0.0)) * speed_weight * lat_weight)
    return value * scale
