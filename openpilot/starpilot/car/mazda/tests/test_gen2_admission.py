"""GEN2 AOL host-policy and native-profile tests for the Mazda adapter."""
from types import SimpleNamespace

from opendbc.car import structs
from opendbc.car.mazda.values import CAR, MazdaSafetyFlags
from openpilot.starpilot.aol.runtime import monitoring_lateral_engaged
from openpilot.starpilot.aol.wire import SafetyState, encode_safety
from openpilot.starpilot.car.mazda import aol

MAZDA_MODEL = int(structs.CarParams.SafetyModel.mazda)
GEN1_PARAM = 0
GEN2_PARAM = int(MazdaSafetyFlags.GEN2)


def make_cp(fingerprint, flags, param, alternative_experience=0):
  cp = structs.CarParams(carFingerprint=fingerprint, brand="mazda", flags=flags, pcmCruise=True,
                         openpilotLongitudinalControl=False, passive=False, notCar=False, dashcamOnly=False,
                         minSteerSpeed=0.0, transmissionType=structs.CarParams.TransmissionType.automatic,
                         fingerprintSource=structs.CarParams.FingerprintSource.can)
  cp.alternativeExperience = alternative_experience
  cp.init("safetyConfigs", 1)
  cp.safetyConfigs[0].safetyModel = MAZDA_MODEL
  cp.safetyConfigs[0].safetyParam = param
  return cp


def gen2_cp():
  return make_cp(CAR.MAZDA_3_2019, int(MazdaSafetyFlags.GEN2), GEN2_PARAM)


def gen1_cp(alternative_experience=0):
  return make_cp(CAR.MAZDA_CX5_2022, int(MazdaSafetyFlags.GEN1), GEN1_PARAM, alternative_experience)


def test_gen2_policy_admits_the_new_profile_once_gates_close():
  policy = aol.policy_for(gen2_cp())
  assert policy.intent_supported
  assert policy.settings_supported
  assert policy.runtime_supported
  assert policy.normal_runtime_supported
  assert policy.explicit_latch
  assert policy.alternative_experience_addition == 32


def test_gen2_native_profile_is_accepted_with_its_own_safety_param():
  # The marked CP is what the host produces when AOL is enabled for this car.
  cp = make_cp(CAR.MAZDA_3_2019, int(MazdaSafetyFlags.GEN2), GEN2_PARAM, 32)
  assert aol.native_accepts_cp(cp, MAZDA_MODEL, GEN2_PARAM)
  # The stock GEN1 parameter must not be accepted for this car.
  assert not aol.native_accepts_cp(cp, MAZDA_MODEL, 0)
  # A denied lateral request is reported as a rejection, not as a grant. The
  # marked query needs the alternative-experience bit, as on a real AOL-enabled CP.
  marked = make_cp(CAR.MAZDA_3_2019, int(MazdaSafetyFlags.GEN2), GEN2_PARAM, 32)
  assert aol.native_latch_rejected(marked, type("N", (), {"requestedLateral": True, "lateralAllowed": False})())
  # The GEN2 profile belongs to gen2-qualified cars only: an admitted GEN1 car
  # must not claim it either.
  assert not aol.native_accepts_cp(gen1_cp(32), MAZDA_MODEL, GEN2_PARAM)


def test_gen1_policy_is_unchanged():
  policy = aol.policy_for(gen1_cp())
  assert policy.intent_supported and policy.settings_supported and policy.runtime_supported
  assert policy.normal_runtime_supported and policy.explicit_latch
  assert policy.alternative_experience_addition == 32
  assert aol.native_accepts_cp(gen1_cp(32), MAZDA_MODEL, GEN1_PARAM)


def test_generic_native_profile_question_recognizes_both_exact_mazda_profiles():
  assert aol.native_profile_supported(MAZDA_MODEL, GEN1_PARAM)
  assert aol.native_profile_supported(MAZDA_MODEL, GEN2_PARAM)
  for param in (3, 10, 18, 34, 66, 130, 258):
    assert not aol.native_profile_supported(MAZDA_MODEL, param)
  assert not aol.native_profile_supported(0, GEN2_PARAM)


def test_driver_monitoring_treats_gen2_native_lateral_as_engaged():
  now = 1_000_000_000
  for param in (GEN1_PARAM, GEN2_PARAM):
    axis = SimpleNamespace(qualified=True, sessionId="mazda-session", observedMonoTime=now - 1000,
                           validUntilMonoTime=now + 100_000_000, lateralActive=True, nativeAcknowledged=True)
    native = SafetyState(1, True, now - 1000, now + 100_000_000, MAZDA_MODEL, param,
                         True, False, True, False, "panda", "mazda-session")

    class SM(dict):
      valid = {"aolAxisState": True, "aolSafetyWire": True}
      alive = {"aolAxisState": True, "aolSafetyWire": True}
      seen = {"aolAxisState": True, "aolSafetyWire": True}
      logMonoTime = {"aolAxisState": now - 1000, "aolSafetyWire": now - 1000}

    sm = SM(aolAxisState=axis, aolSafetyWire=encode_safety(native))
    assert monitoring_lateral_engaged(sm, now_ns=now), param
