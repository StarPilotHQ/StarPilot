"""Conservative Impreza EPS guard adapted from Matt Purnell's Subaru work.

Source: mpurnell1/opendbc c08d4d86d81048447ce0e2dbe1f50745adce096e,
opendbc/car/subaru/carstate.py (high-angle refusal guard).
"""
from opendbc.car.subaru.values import CAR, SubaruFlags


def selected(cp):
  return bool(cp.carFingerprint == CAR.SUBARU_IMPREZA_2020 and
              cp.flags & SubaruFlags.STEER_RATE_LIMITED and
              not cp.flags & (SubaruFlags.LKAS_ANGLE | SubaruFlags.PREGLOBAL) and
              not cp.openpilotLongitudinalControl)


class HighAngleGuard:
  def __init__(self):
    self.cut = False

  def update(self, vdc_angle, speed_kph, driver_torque):
    # EPS reads VDC angle and Brake_Pedal speed; its own angle can differ substantially.
    if speed_kph < 50 and abs(vdc_angle) >= 88 and abs(driver_torque) < 120:
      self.cut = True
    elif abs(vdc_angle) < 84 or speed_kph >= 50:
      self.cut = False
    return self.cut
