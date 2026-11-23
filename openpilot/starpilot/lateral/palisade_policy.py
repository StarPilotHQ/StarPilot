from opendbc.car.hyundai.values import CAR, HyundaiFlags
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
    hda2 = cp.carFingerprint == CAR.HYUNDAI_PALISADE_2023 and cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG
    self.center_output_taper = 0.27 if hda2 else shaping.PALISADE_CENTER_OUTPUT_TAPER_MAX

  def feedforward(self, value, setpoint, jerk, speed):
    return value * shaping.get_palisade_ff_scale(setpoint, jerk, speed) * shaping.get_palisade_center_taper_scale(setpoint, speed)

  def friction(self, setpoint, jerk, speed):
    taper = shaping.get_palisade_center_taper_scale(setpoint, speed)
    return (shaping.get_palisade_friction_threshold(speed, setpoint, jerk),
            1.0 + (shaping.get_palisade_friction_scale(speed, setpoint, jerk) - 1.0) * taper)

  def output(self, value, setpoint, speed):
    return value * shaping.get_palisade_center_output_scale(setpoint, speed, taper_max=self.center_output_taper)
