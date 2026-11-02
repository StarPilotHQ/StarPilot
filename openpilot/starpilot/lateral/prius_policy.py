"""Original and EPS-retrofit Prius shaping on the existing torque controller."""

from opendbc.car.toyota.values import CAR
from openpilot.common.scalar_clip import clip_scalar
from openpilot.starpilot.lateral.controller_selection import generic_torque_supported
from openpilot.starpilot.lateral.hkg_torque_policy import HKGShapedTorquePolicy
from openpilot.starpilot.lateral import prius_shaping as shaping


def supported_cp(cp):
  return cp.brand == 'toyota' and cp.carFingerprint in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT) and generic_torque_supported(cp)


class PriusTorquePolicy(HKGShapedTorquePolicy):
  supported_cp = staticmethod(supported_cp)

  def __init__(self, parent, cp):
    super().__init__(parent, cp)
    self.standard_prius = cp.carFingerprint == CAR.TOYOTA_PRIUS

  def feedforward(self, value, setpoint, jerk, speed):
    return value * shaping.get_prius_ff_scale(setpoint, jerk, speed) * shaping.get_prius_center_taper_scale(setpoint, speed)

  def friction(self, setpoint, jerk, speed):
    scale = shaping.get_prius_friction_scale(speed, setpoint, jerk)
    scale = 1.0 + (scale - 1.0) * shaping.get_prius_center_taper_scale(setpoint, speed)
    return shaping.get_prius_friction_threshold(speed, setpoint, jerk), scale

  def jerk_deadzone(self, setpoint, jerk, measurement, speed):
    maximum = shaping.PRIUS_STANDARD_FRICTION_JERK_DEADZONE_MAX if self.standard_prius else shaping.PRIUS_FRICTION_JERK_DEADZONE_MAX
    return shaping.get_prius_friction_jerk_deadzone(speed, setpoint, maximum)

  def output(self, value, setpoint, speed):
    maximum = shaping.PRIUS_STANDARD_HIGH_SPEED_OUTPUT_TAPER_MAX if self.standard_prius else shaping.PRIUS_HIGH_SPEED_OUTPUT_TAPER_MAX
    value *= shaping.get_prius_center_taper_scale(setpoint, speed)
    value *= shaping.get_prius_high_speed_output_taper_scale(setpoint, speed, maximum)
    return clip_scalar(value, -self.parent.steer_max, self.parent.steer_max)
