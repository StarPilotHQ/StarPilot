from opendbc.car import structs
from opendbc.car.ford.values import CAR, FORD_CANFD_CARS, FORD_CLASSIC_CARS, FordFlags, FordSafetyFlags

AOL_EXPERIENCE = 32
MANUAL = int(FordSafetyFlags.MANUAL)
# bits 9-14 carry the BluePilot lateral library's startup bits (safety/modes/ford_param.h); bit 14 is its
# BluePilot lateral bit, set when the panda runs the library's steering checks
BP_PARAM_MASK = 0x7E00
BP_LATERAL = 0x4000
BASE_WORDS = frozenset((0, 1, 2, 3, 8, 9, 10, 11, 12, 13))
AOL_WORDS = BASE_WORDS | frozenset(word | MANUAL for word in BASE_WORDS)


def profile_word(word):
  return int(word) & ~BP_PARAM_MASK


def aol_safety_config(cp):
  if len(cp.safetyConfigs) not in (1, 2):
    return None
  if len(cp.safetyConfigs) == 2:
    passive = cp.safetyConfigs[0]
    if passive.safetyModel != structs.CarParams.SafetyModel.noOutput or passive.safetyParam != 0:
      return None
  owner = cp.safetyConfigs[-1]
  return owner if owner.safetyModel == structs.CarParams.SafetyModel.ford else None


def base_safety_param(cp):
  owner = aol_safety_config(cp)
  if owner is None:
    return -1
  manual = cp.transmissionType == structs.CarParams.TransmissionType.manual
  if cp.transmissionType not in (structs.CarParams.TransmissionType.automatic, structs.CarParams.TransmissionType.manual):
    return -1
  word = profile_word(owner.safetyParam)
  if (word & MANUAL and not manual) or (manual and cp.alternativeExperience == AOL_EXPERIENCE and not word & MANUAL):
    return -1
  return word & ~MANUAL


def qualified(cp, *, marked_only=False):
  if (cp.brand != 'ford' or cp.carFingerprint not in tuple(CAR) or cp.passive or cp.notCar or cp.dashcamOnly or
      cp.steerControlType != structs.CarParams.SteerControlType.angle or
      cp.alternativeExperience not in ((32,) if marked_only else (0, 32))):
    return False
  flags = int(cp.flags) & ~int(FordFlags.HAS_BSM)
  if cp.carFingerprint in FORD_CLASSIC_CARS and flags == 0:
    base = 0
  elif cp.carFingerprint in FORD_CANFD_CARS and flags == int(FordFlags.CANFD):
    base = 2
  elif cp.carFingerprint == CAR.FORD_EDGE_MK2 and flags == int(FordFlags.NEW_PORT | FordFlags.ALT_STEER_ANGLE):
    base = 8
  elif cp.carFingerprint == CAR.FORD_MONDEO_MK5 and flags == int(FordFlags.NEW_PORT | FordFlags.CANFD):
    base = 10
  elif cp.carFingerprint == CAR.FORD_TRANSIT_MK5 and flags == int(FordFlags.NEW_PORT | FordFlags.LKA_STEERING):
    base = 12
  else:
    return False
  safety = aol_safety_config(cp)
  return bool(cp.pcmCruise and safety is not None and
              base_safety_param(cp) == (base | int(cp.openpilotLongitudinalControl)))


def temporary_restriction(cp, state):
  gears = structs.CarState.GearShifter
  return bool(state is not None and qualified(cp, marked_only=True) and state.canValid and not state.canTimeout
              and not state.steerFaultPermanent and
              (state.gearShifter not in (gears.drive, gears.sport, gears.low) or state.steerFaultTemporary or state.vehicleSensorsInvalid))


def native_observation(native, *, latched, panda_ready, restricted, now_ns, pending_since_ns):
  lost = not panda_ready or native is None
  if not latched or lost or restricted:
    return 0, lost, False
  if native.requestedLateral and native.lateralAllowed:
    return 0, False, False
  pending_since_ns = pending_since_ns or now_ns
  return pending_since_ns, False, now_ns - pending_since_ns >= 200_000_000
