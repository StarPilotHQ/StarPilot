import numpy as np

from opendbc.car.gm.feature_capabilities import longitudinal_supported
from opendbc.car.gm.values import (CAR, NO_ACC_BOLT_CAR, ORDINARY_ASCM_CAR, ORDINARY_CAMERA_CAR, ORDINARY_SDGM_CAR)
from opendbc.car.gm.aol import GM_BASE_GATEWAY_IDS


VOLT_TUNE_CARS = frozenset((CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_2019,
                           CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_CC))


CONFIGURABLE_ACC_TUNE_CARS = (ORDINARY_ASCM_CAR | ORDINARY_CAMERA_CAR | ORDINARY_SDGM_CAR |
                              GM_BASE_GATEWAY_IDS | NO_ACC_BOLT_CAR | frozenset((CAR.CHEVROLET_BOLT_EUV,
                                CAR.CHEVROLET_BOLT_ACC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, CAR.CHEVROLET_MALIBU_HYBRID_CC)))

def tune_options(cp):
  if not longitudinal_supported(cp):
    return (0,)
  if cp.carFingerprint in VOLT_TUNE_CARS:
    return (0,)
  return (0, 1) if cp.carFingerprint in CONFIGURABLE_ACC_TUNE_CARS else (0,)


def acc_tune_limits(speed, accel_max, brake_switch, zero_gas):
  cap = min(accel_max, float(np.interp(speed, (0., 4., 12.), (1.25, 1.6, accel_max))))
  bias = int(round(np.interp(speed, (0., 6., 15., 30.), (40., 85., 130., 170.))))
  return cap, min(zero_gas, brake_switch + bias)
