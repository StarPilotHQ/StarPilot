"""Exact stock-ACC button controller selection; no direct longitudinal actuation."""

from opendbc.car import structs
from opendbc.car.subaru.stock_aol import IMPREZA_MAIN_AOL
from opendbc.car.subaru.values import CAR, SubaruFlags

SOFTWARE_CRUISE = 512


def eligible(cp):
  try:
    return bool(
      cp.brand == 'subaru'
      and cp.carFingerprint == CAR.SUBARU_IMPREZA_2020
      and cp.pcmCruise
      and not cp.openpilotLongitudinalControl
      and not cp.alphaLongitudinalAvailable
      and not cp.passive
      and not cp.dashcamOnly
      and not cp.notCar
      and cp.transmissionType != structs.CarParams.TransmissionType.manual
      and cp.alternativeExperience in (0, 32)
      and int(cp.flags) & ~int(SubaruFlags.HAS_BSM | SubaruFlags.SEND_INFOTAINMENT | SubaruFlags.SMOOTHING_BOUND) == int(CAR.SUBARU_IMPREZA_2020.config.flags)
      and len(cp.safetyConfigs) == 1
      and cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.subaru
      and (not cp.safetyConfigs[0].safetyParam & IMPREZA_MAIN_AOL or cp.alternativeExperience == 32)
      and (cp.safetyConfigs[0].safetyParam & ~IMPREZA_MAIN_AOL) in (0, 8, SOFTWARE_CRUISE, SOFTWARE_CRUISE | 8)
    )
  except (AttributeError, TypeError, ValueError, IndexError):
    return False


def selected(cp):
  return eligible(cp) and bool(cp.safetyConfigs[0].safetyParam & SOFTWARE_CRUISE)


def prepare(cp, enabled):
  if enabled and eligible(cp):
    cp.safetyConfigs[0].safetyParam |= SOFTWARE_CRUISE
  return cp
