"""Exact RAV4 2023–25 LTA ownership; stock/radar-disable/filter remain distinct."""
from opendbc.car import structs
from opendbc.car.toyota.values import CAR, ToyotaFlags


def configured(cp):
  if (cp is None or cp.brand != 'toyota' or cp.carFingerprint != CAR.TOYOTA_RAV4_TSS2_2023 or
      cp.notCar or cp.passive or cp.dashcamOnly or cp.secOcRequired or not cp.pcmCruise or len(cp.safetyConfigs) != 1 or
      cp.steerControlType != structs.CarParams.SteerControlType.angle):
    return False
  required = ToyotaFlags.TSS2 | ToyotaFlags.NO_DSU | ToyotaFlags.RADAR_ACC | ToyotaFlags.ANGLE_CONTROL | ToyotaFlags.RAISED_ACCEL_LIMIT
  filtered = bool(cp.flags & ToyotaFlags.RADAR_CAN_FILTER)
  if filtered:
    required |= ToyotaFlags.RADAR_CAN_FILTER
  elif cp.openpilotLongitudinalControl:
    required |= ToyotaFlags.DISABLE_RADAR
  allowed = required | ToyotaFlags.HYBRID | ToyotaFlags.HAS_BSM | ToyotaFlags.ARS510_RADAR
  expected = 0x449 | (0x1000 if filtered else 0) | (0 if cp.openpilotLongitudinalControl else 0x200)
  config = cp.safetyConfigs[0]
  return bool(cp.flags & required == required and not int(cp.flags) & ~int(allowed) and
              config.safetyModel == structs.CarParams.SafetyModel.toyota and config.safetyParam == expected)


def prepare_stock(cp):
  if not configured(cp):
    return False
  cp.openpilotLongitudinalControl = False
  cp.flags &= ~int(ToyotaFlags.DISABLE_RADAR)
  cp.safetyConfigs[0].safetyParam |= 0x200
  cp.autoResumeSng = False
  return True
