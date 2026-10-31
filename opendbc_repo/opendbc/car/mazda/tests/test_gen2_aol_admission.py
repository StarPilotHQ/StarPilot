"""GEN2 always-on-lateral admission tests.

Implements the admission half of work package W2 of
reports/mazda-gen2-aol-implementation-spec.md. Nothing here grants actuation:
the configuration predicate and recorded evidence gates determine host admission,
while native permission remains independently enforced by Panda.

References:
  - section 1: a dedicated GEN2 profile, not a broadened GEN1 predicate and not
    alternative-experience bit 32 on its own
  - section 3: GEN2 has none of the GEN1 AOL CAN sources
  - section 4: decisions and evidence required before acceptance
  - section 5 W5: a positive test for the target profile and a rejection test
    for every excluded configuration
"""
import pytest

from opendbc.car import structs
from opendbc.car.mazda.stock_aol import (GEN2_AOL_SAFETY_PARAM, GEN2_CARS, GEN2_EVIDENCE_GATES,
                                         admitted, gen2_configuration, gen2_qualified, qualified)
from opendbc.car.mazda.values import CAR, MazdaSafetyFlags

CLOSED_GATES = {name: True for name in GEN2_EVIDENCE_GATES}
GEN1_CAR = CAR.MAZDA_CX5_2022


def make_cp(fingerprint=CAR.MAZDA_3_2019, flags=int(MazdaSafetyFlags.GEN2), param=GEN2_AOL_SAFETY_PARAM,
            alternative_experience=0):
  cp = structs.CarParams(carFingerprint=fingerprint, brand="mazda", flags=flags, pcmCruise=True,
                         openpilotLongitudinalControl=False, passive=False, notCar=False, dashcamOnly=False,
                         minSteerSpeed=0.0, transmissionType=structs.CarParams.TransmissionType.automatic,
                         fingerprintSource=structs.CarParams.FingerprintSource.can)
  cp.alternativeExperience = alternative_experience
  cp.init("safetyConfigs", 1)
  cp.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.mazda
  cp.safetyConfigs[0].safetyParam = param
  return cp


def make_gen1_cp():
  return make_cp(fingerprint=GEN1_CAR, flags=int(MazdaSafetyFlags.GEN1), param=0)


def test_real_target_car_matches_the_gen2_configuration():
  # The actual interface sets openpilot longitudinal for this car; the profile
  # grants lateral independently, so that must not exclude it.
  cp = make_cp()
  cp.openpilotLongitudinalControl = True
  assert gen2_configuration(cp)
  cp.alternativeExperience = 32
  assert gen2_qualified(cp, CLOSED_GATES, marked_only=True)


def test_readiness_is_delegated_and_no_door_or_seatbelt_signal_exists():
  # The readiness gate is closed by a recorded decision, not by a signal. This
  # pins the evidence basis: if a door/seatbelt field ever appears in the DBC, the
  # delegation decision must be revisited rather than silently inherited.
  from opendbc.can.dbc import DBC as DBCLoader
  dbc = DBCLoader("mazda_2019")
  names = {name.upper() for message in dbc.addr_to_msg.values() for name in message.sigs}
  assert not [n for n in names if any(k in n for k in ("DOOR", "SEAT", "BELT", "BUCKLE"))]
  assert GEN2_EVIDENCE_GATES['readiness'] is True


def test_target_platform_matches_the_gen2_configuration():
  assert CAR.MAZDA_3_2019 in GEN2_CARS
  assert gen2_configuration(make_cp())


def test_gen2_configuration_is_independent_of_the_evidence_gates():
  # A closed gate must change admission, never the configuration itself.
  assert gen2_configuration(make_cp()) is True
  assert gen2_qualified(make_cp(), CLOSED_GATES) is True


def test_alternative_experience_bit_is_required_by_the_marked_query_only():
  assert gen2_configuration(make_cp(alternative_experience=0))
  assert gen2_configuration(make_cp(alternative_experience=32))
  assert not gen2_configuration(make_cp(alternative_experience=0), marked_only=True)
  assert gen2_configuration(make_cp(alternative_experience=32), marked_only=True)
  assert not gen2_configuration(make_cp(alternative_experience=8))


@pytest.mark.parametrize("change", (
  "manual_transmission", "unknown_transmission", "direct_transmission", "cvt_transmission",
  "passive", "not_car", "dashcam_only", "fixed_fingerprint", "no_pcm_cruise",
  "two_safety_configs", "wrong_safety_model", "gen1_safety_param", "gen3_flags",
  "gen2_platform_outside_initial_admission", "gen1_platform", "wrong_brand"))
def test_gen2_configuration_rejects_excluded_configurations(change):
  cp = make_cp()
  if change == "manual_transmission":
    cp.transmissionType = structs.CarParams.TransmissionType.manual
  elif change == "unknown_transmission":
    cp.transmissionType = structs.CarParams.TransmissionType.unknown
  elif change == "direct_transmission":
    cp.transmissionType = structs.CarParams.TransmissionType.direct
  elif change == "cvt_transmission":
    cp.transmissionType = structs.CarParams.TransmissionType.cvt
  elif change == "passive":
    cp.passive = True
  elif change == "not_car":
    cp.notCar = True
  elif change == "dashcam_only":
    cp.dashcamOnly = True
  elif change == "fixed_fingerprint":
    cp.fingerprintSource = structs.CarParams.FingerprintSource.fixed
  elif change == "no_pcm_cruise":
    cp.pcmCruise = False
  elif change == "two_safety_configs":
    cp.init("safetyConfigs", 2)
    cp.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.mazda
    cp.safetyConfigs[1].safetyModel = structs.CarParams.SafetyModel.mazda
  elif change == "wrong_safety_model":
    cp.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.elm327
  elif change == "gen1_safety_param":
    cp.safetyConfigs[0].safetyParam = 0
  elif change == "gen3_flags":
    cp = make_cp(flags=int(MazdaSafetyFlags.GEN3))
  elif change == "gen2_platform_outside_initial_admission":
    cp = make_cp(fingerprint=CAR.MAZDA_CX_30)
  elif change == "gen1_platform":
    cp = make_gen1_cp()
  elif change == "wrong_brand":
    cp.brand = "toyota"

  assert not gen2_configuration(cp)
  assert not gen2_qualified(cp, CLOSED_GATES)


def test_gen2_aol_is_blocked_while_any_evidence_gate_is_open():
  cp = make_cp()
  for open_gate in GEN2_EVIDENCE_GATES:
    gates = {name: name != open_gate for name in GEN2_EVIDENCE_GATES}
    assert not gen2_qualified(cp, gates), f"gate {open_gate} must block GEN2 AOL on its own"


def test_incomplete_or_extended_gate_tables_can_never_admit():
  # all({}.values()) is True, so a caller passing an empty, partial or widened
  # table must still fail closed on the target car.
  cp = make_cp()
  assert not gen2_qualified(cp, {})
  assert not gen2_qualified(cp, {'main': True})
  assert not gen2_qualified(cp, dict(CLOSED_GATES, unknown_gate=True))


def test_production_evidence_gates_match_the_recorded_evidence():
  # Tripwire: changing a recorded gate is deliberate and must update the evidence
  # report. This assertion is not itself evidence: the executable main, readiness,
  # TI2-fault and ACC-isolation regressions in the Mazda host and safety suites
  # are the behavioral checks behind these recorded decisions.
  assert GEN2_EVIDENCE_GATES == {'main': True, 'readiness': True,
                                 'ti2_faults': True, 'acc_isolation': True}


def test_target_car_is_admitted_once_every_gate_is_closed():
  cp = make_cp()
  assert gen2_configuration(cp)
  assert gen2_qualified(cp)
  assert admitted(cp)
  # The alternative-experience marker is separate from admission: it is only
  # present once the host actually enables AOL for this car.
  assert not admitted(cp, marked_only=True)
  assert admitted(make_cp(alternative_experience=32), marked_only=True)


def test_stock_gen1_admission_is_unchanged():
  cp = make_gen1_cp()
  assert qualified(cp)
  assert qualified(cp, marked_only=True) is False  # marked-only needs experience 32
  assert qualified(make_cp(fingerprint=GEN1_CAR, flags=int(MazdaSafetyFlags.GEN1),
                           param=0, alternative_experience=32), marked_only=True)
  assert admitted(cp)
  assert not admitted(make_cp(), marked_only=True)
