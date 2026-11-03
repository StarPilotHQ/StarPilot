from math import isfinite
from capnp.lib.capnp import _DynamicStructBuilder, _DynamicStructReader

from opendbc.car import structs
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.ccnc_ev_stock import qualified as stock_qualified, metadata_qualified
from opendbc.car.hyundai.ev9_longitudinal import copy_cp

STOCK_PARAM = 0x5491
LONG_PARAM = 0x5495


def required(cp):

  return cp.carFingerprint == CAR.HYUNDAI_IONIQ_5_PE and cp.openpilotLongitudinalControl


def candidate(stock, *, enabled, is_release):
  if (not enabled or is_release or stock.carFingerprint != CAR.HYUNDAI_IONIQ_5_PE or
      not stock.alphaLongitudinalAvailable or not stock_qualified(stock)):
    return None
  cp = copy_cp(stock)
  cp.alphaLongitudinalAvailable = True
  cp.openpilotLongitudinalControl = True
  cp.pcmCruise = False
  cp.safetyConfigs[0].safetyParam = LONG_PARAM
  cp.deprecated.startingState = True
  cp.deprecated.startAccel = 1.0
  cp.deprecated.vEgoStarting = 0.4
  cp.deprecated.vEgoStopping = 0.3
  cp.longitudinalActuatorDelay = 0.35
  cp.deprecated.stoppingDecelRate = 0.4
  return cp


def qualified(cp):
  if (not required(cp) or cp.pcmCruise or not cp.alphaLongitudinalAvailable or
      cp.alternativeExperience not in (0, 32) or len(cp.safetyConfigs) != 1 or
      cp.safetyConfigs[0].safetyParam != LONG_PARAM or
      cp.steerControlType != structs.CarParams.SteerControlType.angle):
    return False
  deprecated = cp.deprecated
  if (not deprecated.startingState or not all(isfinite(value) for value in (deprecated.startAccel, deprecated.vEgoStarting, deprecated.vEgoStopping,
                                             deprecated.stoppingDecelRate, cp.longitudinalActuatorDelay)) or
      abs(deprecated.startAccel - 1.) > 1e-6 or abs(deprecated.vEgoStarting - .4) > 1e-6 or
      abs(deprecated.vEgoStopping - .3) > 1e-6 or abs(deprecated.stoppingDecelRate - .4) > 1e-6 or
      abs(cp.longitudinalActuatorDelay - .35) > 1e-6):
    return False
  if type(cp) in (_DynamicStructBuilder, _DynamicStructReader) and cp.schema == structs.CarParams.schema:
    return metadata_qualified(cp)
  stock = copy_cp(cp)
  stock.alternativeExperience = 0
  stock.openpilotLongitudinalControl = False
  stock.pcmCruise = True
  stock.alphaLongitudinalAvailable = False
  stock.safetyConfigs[0].safetyParam = STOCK_PARAM
  return stock_qualified(stock)
