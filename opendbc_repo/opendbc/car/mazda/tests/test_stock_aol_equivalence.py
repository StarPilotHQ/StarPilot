"""The cheaper stock_aol predicates return exactly what the original ones did.

gen2_configuration reads the safety config list once, and temporary_restriction
evaluates its state terms before admission. Both are pure reorderings. The reference
functions below are the original expressions, kept verbatim, and every CarParams
perturbation (singly and in pairs) is crossed with every CarState combination.
"""
import itertools

from opendbc.car import structs
from opendbc.car.mazda import stock_aol
from opendbc.car.mazda.values import CAR, MazdaSafetyFlags

GEN1 = int(MazdaSafetyFlags.GEN1)
GEN2 = int(MazdaSafetyFlags.GEN2)
MAZDA = structs.CarParams.SafetyModel.mazda
OTHER_MODEL = structs.CarParams.SafetyModel.toyota
AUTOMATIC = structs.CarParams.TransmissionType.automatic
MANUAL = structs.CarParams.TransmissionType.manual
CAN = structs.CarParams.FingerprintSource.can
FIXED = structs.CarParams.FingerprintSource.fixed
DRIVE = structs.CarState.GearShifter.drive
PARK = structs.CarState.GearShifter.park


def reference_gen2_configuration(cp, *, marked_only=False):
  return (cp.brand == "mazda" and cp.carFingerprint in stock_aol.GEN2_CARS and
          int(cp.flags) == int(MazdaSafetyFlags.GEN2) and
          not cp.passive and not cp.notCar and not cp.dashcamOnly and
          cp.transmissionType == structs.CarParams.TransmissionType.automatic and
          cp.fingerprintSource != structs.CarParams.FingerprintSource.fixed and
          cp.pcmCruise and len(cp.safetyConfigs) == 1 and
          cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.mazda and
          cp.safetyConfigs[0].safetyParam == stock_aol.GEN2_AOL_SAFETY_PARAM and
          cp.alternativeExperience in ((32,) if marked_only else (0, 32)))


def reference_admitted(cp, *, marked_only=False):
  gates = stock_aol.GEN2_EVIDENCE_GATES
  gen2 = bool(gates.keys() == stock_aol.GEN2_EVIDENCE_GATES.keys() and all(gates.values()) and
              reference_gen2_configuration(cp, marked_only=marked_only))
  return stock_aol.qualified(cp, marked_only=marked_only) or gen2


def reference_temporary_restriction(cp, state):
  return bool(state is not None and reference_admitted(cp, marked_only=True) and state.canValid and not state.canTimeout and
              not state.steerFaultPermanent and (state.gearShifter != structs.CarState.GearShifter.drive or
                                                 state.steerFaultTemporary or state.standstill or (cp.minSteerSpeed > 0 and state.vEgo < cp.minSteerSpeed)))


BASE = dict(brand="mazda", fingerprint=CAR.MAZDA_3_2019, flags=GEN2, passive=False, notCar=False, dashcamOnly=False,
            transmission=AUTOMATIC, source=CAN, pcmCruise=True, configs=((MAZDA, GEN2),), experience=32, minSteerSpeed=0.0)
VARIANTS = dict(brand=("toyota",), fingerprint=(CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9),
                flags=(GEN1, 0, GEN2 | int(MazdaSafetyFlags.TORQUE_INTERCEPTOR)), passive=(True,), notCar=(True,),
                dashcamOnly=(True,), transmission=(MANUAL,), source=(FIXED,), pcmCruise=(False,),
                configs=((), ((OTHER_MODEL, GEN2),), ((MAZDA, 0),), ((MAZDA, GEN2), (MAZDA, GEN2)), ((MAZDA, 0), (MAZDA, GEN2))),
                experience=(0, 96), minSteerSpeed=(5.0,))


def make_cp(spec):
  cp = structs.CarParams(carFingerprint=spec['fingerprint'], brand=spec['brand'], flags=spec['flags'], passive=spec['passive'],
                         notCar=spec['notCar'], dashcamOnly=spec['dashcamOnly'], transmissionType=spec['transmission'],
                         fingerprintSource=spec['source'], pcmCruise=spec['pcmCruise'], minSteerSpeed=spec['minSteerSpeed'])
  cp.alternativeExperience = spec['experience']
  cp.init("safetyConfigs", len(spec['configs']))
  for slot, (model, param) in zip(cp.safetyConfigs, spec['configs'], strict=True):
    slot.safetyModel = model
    slot.safetyParam = param
  return cp


def cp_specs():
  yield dict(BASE)
  for key, values in VARIANTS.items():
    for value in values:
      yield {**BASE, key: value}
  for (key_a, values_a), (key_b, values_b) in itertools.combinations(VARIANTS.items(), 2):
    for value_a, value_b in itertools.product(values_a, values_b):
      yield {**BASE, key_a: value_a, key_b: value_b}
  # GEN1 baseline and its single perturbations, so the unchanged GEN1 path is covered too.
  gen1 = {**BASE, 'fingerprint': CAR.MAZDA_CX5_2022, 'flags': GEN1, 'configs': ((MAZDA, 0),)}
  yield gen1
  for key, values in VARIANTS.items():
    for value in values:
      yield {**gen1, key: value}


def states():
  yield None
  for valid, timeout, permanent, gear, temporary, standstill, speed in itertools.product(
      (True, False), (False, True), (False, True), (DRIVE, PARK), (False, True), (False, True), (10.0, 1.0)):
    state = structs.CarState(canValid=valid, canTimeout=timeout, steerFaultPermanent=permanent, gearShifter=gear,
                             steerFaultTemporary=temporary, standstill=standstill, vEgo=speed)
    yield state.as_reader()


def test_predicates_match_the_original_expressions():
  all_states = list(states())
  admitted_seen = restricted_seen = unrestricted_seen = 0
  for spec in cp_specs():
    builder = make_cp(spec)
    for cp in (builder, builder.as_reader()):
      for marked_only in (False, True):
        expected = reference_gen2_configuration(cp, marked_only=marked_only)
        assert stock_aol.gen2_configuration(cp, marked_only=marked_only) == expected, (spec, marked_only)
        assert stock_aol.admitted(cp, marked_only=marked_only) == reference_admitted(cp, marked_only=marked_only), (spec, marked_only)
      admitted_seen += stock_aol.admitted(cp, marked_only=True)
      for state in all_states:
        expected = reference_temporary_restriction(cp, state)
        assert stock_aol.temporary_restriction(cp, state) is expected, (spec, state)
        restricted_seen += expected
        unrestricted_seen += not expected
  # The sweep must exercise both outcomes, or it proves nothing.
  assert admitted_seen > 0 and restricted_seen > 0 and unrestricted_seen > 0
