"""Selected CANFD safety slot, retaining the exact captured-startup topology."""
from functools import lru_cache

from capnp.lib.capnp import _DynamicStructBuilder, _DynamicStructReader

from opendbc.car.structs import CarParams
from opendbc.car.hyundai.values import CAR, HyundaiFlags


def topology_index(cp):
  if len(cp.safetyConfigs) == 1:
    return 0 if cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.hyundaiCanfd else None
  if len(cp.safetyConfigs) != 2:
    return None
  auxiliary, owner = cp.safetyConfigs
  required = HyundaiFlags.CANFD | HyundaiFlags.EV | HyundaiFlags.CANFD_LKA_STEER_MSG
  excluded = (HyundaiFlags.CANFD_ANGLE_STEERING | HyundaiFlags.CANFD_LKA_STEER_MSG_ALT |
              HyundaiFlags.CANFD_ALT_BUTTONS | HyundaiFlags.CANFD_CAMERA_SCC | HyundaiFlags.CCNC | HyundaiFlags.HYBRID)
  if (cp.brand != 'hyundai' or cp.carFingerprint not in (CAR.KIA_EV6, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN) or
      cp.passive or cp.dashcamOnly or cp.notCar or cp.steerControlType != CarParams.SteerControlType.torque or
      cp.flags & required != required or cp.flags & excluded or
      auxiliary.safetyModel != CarParams.SafetyModel.noOutput or auxiliary.safetyParam != 0 or
      owner.safetyModel != CarParams.SafetyModel.hyundaiCanfd):
    return None
  return 1


def _config_index_uncached(cp):
  owner = topology_index(cp)
  if owner is None and len(cp.safetyConfigs) == 2:
    auxiliary, selected = cp.safetyConfigs
    try:
      factory = CAR(cp.carFingerprint)
    except ValueError:
      return None
    if (cp.brand == 'hyundai' and factory.config.flags & HyundaiFlags.CANFD and
        not factory.config.flags & HyundaiFlags.CANFD_ANGLE_STEERING and
        cp.flags & HyundaiFlags.CANFD and not cp.flags & HyundaiFlags.CANFD_ANGLE_STEERING and
        not cp.passive and not cp.dashcamOnly and not cp.notCar and
        cp.steerControlType == CarParams.SteerControlType.torque and
        auxiliary.safetyModel == CarParams.SafetyModel.noOutput and auxiliary.safetyParam == 0 and
        selected.safetyModel == CarParams.SafetyModel.hyundaiCanfd):
      return 1
  if owner != 1:
    return owner
  raw = int(cp.safetyConfigs[owner].safetyParam)
  if cp.openpilotLongitudinalControl and not cp.pcmCruise:
    word = 0x2015 if cp.carFingerprint == CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN else 0x15
    if raw == word and cp.alternativeExperience == 0:
      return 1
    if cp.carFingerprint in (CAR.KIA_EV6, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN) and raw == (word | 0x800) and cp.alternativeExperience == 32:
      return 1
  elif not cp.openpilotLongitudinalControl and cp.pcmCruise and cp.alternativeExperience == 0 and raw in (0x11, 0x811):
    return 1
  return None


@lru_cache(maxsize=32)
def _config_index_for_bytes(key):
  with CarParams.from_bytes(key) as cp:
    return _config_index_uncached(cp)


def config_index(cp):
  if type(cp) in (_DynamicStructBuilder, _DynamicStructReader):
    configs = cp.safetyConfigs
    if len(configs) == 1:
      return 0 if configs[0].safetyModel == CarParams.SafetyModel.hyundaiCanfd else None
    if cp.schema == CarParams.schema and len(configs) == 2:
      try:
        snapshot = cp.copy() if type(cp) is _DynamicStructBuilder else cp.as_builder()
        key = snapshot.to_bytes()
        if len(key) <= 65536:
          return _config_index_for_bytes(key)
      except Exception:
        pass
  return _config_index_uncached(cp)
