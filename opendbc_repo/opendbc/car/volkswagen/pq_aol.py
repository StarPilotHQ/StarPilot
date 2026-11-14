"""Checked automatic gateway Sharan profile; unverified EPS modifications grant no capability."""

from opendbc.car import structs
from opendbc.car.volkswagen.values import CAR, VolkswagenFlags
from opendbc.car.volkswagen.stock_aol import safety_config

WORDS = frozenset((8, 9))


def qualified(cp, *, marked_only=False):
  try:
    safety = safety_config(cp)
    base = int(cp.openpilotLongitudinalControl)
    return bool(
      cp.brand == 'volkswagen'
      and cp.carFingerprint == CAR.VOLKSWAGEN_SHARAN_MK2
      and cp.flags & int(VolkswagenFlags.PQ)
      and not cp.flags & ~int(VolkswagenFlags.PQ | VolkswagenFlags.HAS_BSM)
      and not cp.passive
      and not cp.notCar
      and not cp.dashcamOnly
      and cp.minSteerSpeed >= 50 / 3.6
      and cp.steerControlType == structs.CarParams.SteerControlType.torque
      and cp.transmissionType == structs.CarParams.TransmissionType.automatic
      and cp.networkLocation == structs.CarParams.NetworkLocation.gateway
      and bool(cp.pcmCruise) != bool(cp.openpilotLongitudinalControl)
      and cp.alphaLongitudinalAvailable
      and safety is not None
      and safety.safetyModel == structs.CarParams.SafetyModel.volkswagenPq
      and (
        cp.alternativeExperience == 32 and safety.safetyParam == (8 | base) or not marked_only and cp.alternativeExperience == 0 and safety.safetyParam == base
      )
    )
  except (AttributeError, TypeError, ValueError, OverflowError, IndexError):
    return False
