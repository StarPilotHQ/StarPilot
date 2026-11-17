"""Shared StarPilot torque shaping using the vehicle's own calibration and conversion."""

from opendbc.car.hyundai.values import HyundaiFlags

from openpilot.common.scalar_clip import clip_scalar
from openpilot.starpilot.lateral.controller_selection import generic_torque_supported
from openpilot.starpilot.lateral.hkg_torque_policy import HKGShapedTorquePolicy
from openpilot.starpilot.lateral.hkg_vehicle_shaping import get_hkg_canfd_base_friction_threshold
from openpilot.starpilot.lateral.torque_shaping import get_standard_friction_threshold


class GenericTorquePolicy(HKGShapedTorquePolicy):
  supported_cp = staticmethod(generic_torque_supported)

  def __init__(self, parent, cp):
    super().__init__(parent, cp)
    self.hkg_canfd = bool(cp.brand == 'hyundai' and cp.flags & HyundaiFlags.CANFD and
                          not cp.flags & HyundaiFlags.CANFD_ANGLE_STEERING)

  def feedforward(self, value, setpoint, jerk, speed):
    return value

  def friction(self, setpoint, jerk, speed):
    threshold = get_hkg_canfd_base_friction_threshold if self.hkg_canfd else get_standard_friction_threshold
    return threshold(speed), 1.0

  def output(self, value, setpoint, speed):
    return clip_scalar(value, -self.parent.steer_max, self.parent.steer_max)
