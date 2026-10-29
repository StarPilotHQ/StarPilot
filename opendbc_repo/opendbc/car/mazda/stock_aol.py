from opendbc.car import structs
from opendbc.car.mazda.values import CAR, MazdaSafetyFlags

CARS = frozenset((CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))


def qualified(cp, *, marked_only=False):
  return (cp.brand == "mazda" and cp.carFingerprint in CARS and int(cp.flags) == int(MazdaSafetyFlags.GEN1) and
          not cp.passive and not cp.notCar and not cp.dashcamOnly and
          cp.transmissionType != structs.CarParams.TransmissionType.manual and
          cp.fingerprintSource != structs.CarParams.FingerprintSource.fixed and
          cp.pcmCruise and not cp.openpilotLongitudinalControl and len(cp.safetyConfigs) == 1 and
          cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.mazda and
          cp.safetyConfigs[0].safetyParam == 0 and cp.alternativeExperience in ((32,) if marked_only else (0, 32)))


def temporary_restriction(cp, state):
  return bool(state is not None and qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and
              not state.steerFaultPermanent and (state.gearShifter != structs.CarState.GearShifter.drive or
                                                 state.steerFaultTemporary or state.standstill or (cp.minSteerSpeed > 0 and state.vEgo < cp.minSteerSpeed)))


def native_observation(native, *, latched, panda_ready, restricted, now_ns, pending_since_ns):
  lost = not panda_ready or native is None
  if not latched or lost or restricted:
    return 0, lost, False
  if native.requestedLateral and native.lateralAllowed:
    return 0, False, False
  pending_since_ns = pending_since_ns or now_ns
  return pending_since_ns, False, now_ns - pending_since_ns >= 200_000_000
