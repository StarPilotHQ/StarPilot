"""Automatic supported-vehicle radar selection; never changes ECU or native safety permissions."""

from opendbc.car import structs
from opendbc.car.toyota.values import ARS510_RADAR_CAR, ToyotaFlags


def supported(cp):
  return bool(
    cp.brand == "toyota"
    and cp.carFingerprint in ARS510_RADAR_CAR
    and not cp.passive
    and not cp.dashcamOnly
    and not cp.notCar
    and len(cp.safetyConfigs) == 1
    and cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.toyota
  )


def configure_radar(cp):
  if not supported(cp):
    return False
  cp.flags &= ~int(ToyotaFlags.ARS510_RADAR | ToyotaFlags.ARS510_FUSED)
  if cp.flags & ToyotaFlags.DISABLE_RADAR:
    cp.radarUnavailable = True
    return False
  cp.flags |= int(ToyotaFlags.ARS510_RADAR)
  cp.radarUnavailable = False
  return True
