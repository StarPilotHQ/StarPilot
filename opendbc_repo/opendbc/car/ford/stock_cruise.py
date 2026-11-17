"""Ford physical cancel/resume switch context; no new cruise authority."""

from opendbc.car import structs
from opendbc.car.ford.aol import base_safety_param
from opendbc.car.ford.values import CAR, FordFlags, FordSafetyFlags


class FordStockCruiseButton:
  """Resolve Ford's context-sensitive cancel/resume switch for stock ACC."""

  def __init__(self):
    self.pressed = False
    self.cancel = False
    self.resume = False

  def update(self, pressed: bool, cruise_available: bool, cruise_enabled: bool) -> tuple[bool, bool]:
    if pressed and not self.pressed:
      self.cancel = cruise_available and cruise_enabled
      self.resume = cruise_available and not cruise_enabled
    elif not pressed:
      self.cancel = False
      self.resume = False

    self.pressed = pressed
    return self.cancel, self.resume


def qualified(CP) -> bool:
  """Existing stock profiles with the paired physical-driver button contract."""
  if (CP.brand != "ford" or CP.carFingerprint not in tuple(CAR) or CP.openpilotLongitudinalControl or CP.alternativeExperience not in (0, 32) or
      CP.passive or CP.dashcamOnly or CP.notCar or len(CP.safetyConfigs) not in (1, 2) or
      CP.flags & ~int(FordFlags.CANFD | FordFlags.HAS_BSM | FordFlags.ALT_STEER_ANGLE | FordFlags.NEW_PORT | FordFlags.LKA_STEERING)):
    return False
  if len(CP.safetyConfigs) == 2 and (CP.safetyConfigs[0].safetyModel != structs.CarParams.SafetyModel.noOutput or
                                  CP.safetyConfigs[0].safetyParam != 0):
    return False
  safety = CP.safetyConfigs[-1]
  if safety.safetyModel != structs.CarParams.SafetyModel.ford or base_safety_param(CP) not in (0, 2, 8, 10, 12):
    return False
  expected = (int(FordSafetyFlags.CANFD) if CP.flags & FordFlags.CANFD else 0)
  expected |= int(FordSafetyFlags.NEW_PORT) if CP.flags & FordFlags.NEW_PORT else 0
  expected |= int(FordSafetyFlags.LKA_STEERING) if CP.flags & FordFlags.LKA_STEERING else 0
  return base_safety_param(CP) == expected
