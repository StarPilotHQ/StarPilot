"""Keep the gas Kona's Stable target filter and its matching lookahead together."""
import math

from opendbc.car import structs
from opendbc.car.hyundai.values import CAR, HyundaiFlags


def seconds(cp):
  required = HyundaiFlags.CANFD | HyundaiFlags.CCNC | HyundaiFlags.CANFD_CAMERA_SCC
  excluded = HyundaiFlags.CANFD_ANGLE_STEERING | HyundaiFlags.CANFD_LKA_STEER_MSG | HyundaiFlags.NON_SCC
  if (cp.brand != 'hyundai' or cp.carFingerprint != CAR.HYUNDAI_KONA_2ND_GEN or
      cp.passive or cp.dashcamOnly or cp.notCar or cp.flags & required != required or cp.flags & excluded or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or cp.lateralTuning.which() != 'torque' or
      not math.isclose(cp.steerActuatorDelay, .1, rel_tol=1e-6)):
    return 0.
  owners = [c for c in cp.safetyConfigs if c.safetyModel not in
            (structs.CarParams.SafetyModel.silent, structs.CarParams.SafetyModel.noOutput)]
  if (len(owners) != 1 or owners[0].safetyModel != structs.CarParams.SafetyModel.hyundaiCanfd or
      owners[0].safetyParam & ~0x820 not in (0x408, 0x40C)):
    return 0.
  return .1
