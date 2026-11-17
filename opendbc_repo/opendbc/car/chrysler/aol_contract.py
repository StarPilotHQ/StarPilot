"""Exact stock-ACC Chrysler AOL admission. Dashcam variants remain excluded."""
from opendbc.car import structs
from opendbc.car.chrysler.values import CAR, ChryslerFlags

CHRYSLER_AOL_EXPERIENCE = 32
CHRYSLER_AOL_CLASSIC = frozenset((
  CAR.CHRYSLER_PACIFICA_2018_HYBRID, CAR.CHRYSLER_PACIFICA_2019_HYBRID,
  CAR.CHRYSLER_PACIFICA_2018, CAR.CHRYSLER_PACIFICA_2020, CAR.DODGE_DURANGO,
  CAR.JEEP_GRAND_CHEROKEE, CAR.JEEP_GRAND_CHEROKEE_2019,
))
CHRYSLER_AOL_CARS = CHRYSLER_AOL_CLASSIC | {CAR.RAM_1500_5TH_GEN}


_BASE_FLAGS = ChryslerFlags.HIGHER_MIN_STEERING_SPEED | ChryslerFlags.HAS_BSM | ChryslerFlags.STEERING_SPEED_BYPASS
_JEEP_FLAGS = _BASE_FLAGS | ChryslerFlags.JEEP_BRAKE_HOLD_AVAILABLE


def qualified(cp, *, marked_only=False):
  if (cp is None or cp.brand != 'chrysler' or cp.carFingerprint not in CHRYSLER_AOL_CARS or
      cp.notCar or cp.passive or cp.dashcamOnly or not cp.pcmCruise or
      cp.openpilotLongitudinalControl or cp.alphaLongitudinalAvailable or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or
      len(cp.safetyConfigs) != 1 or
      cp.alternativeExperience not in ((CHRYSLER_AOL_EXPERIENCE,) if marked_only else (0, CHRYSLER_AOL_EXPERIENCE))):
    return False
  jeep = cp.carFingerprint in (CAR.JEEP_GRAND_CHEROKEE, CAR.JEEP_GRAND_CHEROKEE_2019)
  flags = _JEEP_FLAGS if jeep else _BASE_FLAGS
  expected = (1 if cp.carFingerprint == CAR.RAM_1500_5TH_GEN else
              8 if cp.carFingerprint == CAR.CHRYSLER_PACIFICA_2019_HYBRID and cp.alternativeExperience == 32 else 0)
  return bool(not int(cp.flags) & ~int(flags) and
              cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.chrysler and
              int(cp.safetyConfigs[0].safetyParam) in ((0, 4) if jeep else (expected,)) and
              cp.lateralTuning.which() in (('torque',) if cp.carFingerprint == CAR.RAM_1500_5TH_GEN else ('pid', 'torque')))


def temporary_restriction(cp, state):
  gears = structs.CarState.GearShifter
  return bool(qualified(cp, marked_only=True) and state is not None and
              state.canValid and not state.canTimeout and not state.steerFaultPermanent and
              (state.gearShifter not in (gears.drive, gears.low) or state.steerFaultTemporary or
               state.standstill or state.vEgo < cp.minSteerSpeed))


def lkas_button_supported(cp):
  # Dom's Chrysler whitelist did not enable LKAS AOL. Preserve that feature
  # disposition until an intentional end-to-end activation requirement exists.
  return False
