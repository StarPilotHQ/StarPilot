"""Exact BASE GEN1 AOL admission; never infer Extreme/bridge capability."""
from opendbc.car import structs
from opendbc.car.rivian.values import CAR


def qualified(cp, *, marked_only=False):
  try:
    return bool(cp.brand == 'rivian' and cp.carFingerprint == CAR.RIVIAN_R1 and int(cp.flags) == 0 and
      not (cp.passive or cp.notCar or cp.dashcamOnly) and isinstance(cp.radarUnavailable, bool) and
      cp.fingerprintSource != structs.CarParams.FingerprintSource.fixed and
      len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.rivian and
      cp.safetyConfigs[0].safetyParam == int(cp.openpilotLongitudinalControl) and
      cp.alternativeExperience in ((32,) if marked_only else (0, 32)))
  except (AttributeError, TypeError, ValueError, IndexError):
    return False


def temporary_restriction(cp, state):
  return bool(qualified(cp, marked_only=True) and state is not None and state.canValid and not state.canTimeout and
    not state.steerFaultPermanent and not state.accFaulted and
    (state.gearShifter != structs.CarState.GearShifter.drive or state.steerFaultTemporary or state.standstill))


def native_observation(native, *, latched, panda_ready, restricted, now_ns, pending_since_ns):
  lost = not panda_ready or native is None
  if not latched or lost or restricted:
    return 0, lost, False
  if native.requestedLateral and native.lateralAllowed:
    return 0, False, False
  pending_since_ns = pending_since_ns or now_ns
  return pending_since_ns, False, now_ns - pending_since_ns >= 200_000_000


def temporary_eps_restriction(cp, state):
  """Exact armed LAT-only EPS withdrawal; physical disarms stay authoritative."""
  return bool(qualified(cp, marked_only=True) and state is not None and state.canValid and not state.canTimeout and
    state.steerFaultTemporary and not state.steerFaultPermanent and
    state.gearShifter == structs.CarState.GearShifter.drive and state.cruiseState.available and
    not state.cruiseState.enabled and not state.brakePressed and not state.regenBraking and not state.gasPressed and
    not state.doorOpen and not state.seatbeltUnlatched and not state.accFaulted and
    not state.vehicleSensorsInvalid and not state.steeringDisengage and not state.buttonEvents)
