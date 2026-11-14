"""Private MQB final-factory, intent and serialized receipt qualification."""
from types import SimpleNamespace
import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.stock_aol import MQB_CARS, qualified
from openpilot.starpilot.aol.vehicle import policy_for, native_matches_cp
from openpilot.starpilot.car.volkswagen.aol import VolkswagenStockCardIntent
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.runtime import current_native, decide_axes
from openpilot.starpilot.aol.wire import SafetyState, IntentState, encode_safety, encode_intent, decode_intent


def params(identity, manual=False, alpha=False, gateway=False):
  fp = gen_empty_fingerprint()
  if not manual:
    fp[0][0xAD] = 8
  if gateway:
    fp[1][0xFD] = 8
  return CarInterface.get_params(identity, fp, [], alpha, False, False)

@pytest.mark.parametrize("identity", sorted(MQB_CARS))
@pytest.mark.parametrize("manual", (False, True))
@pytest.mark.parametrize("gateway", (False, True))
def test_factory_exact_marked_words_and_stock_long_separation(identity, manual, gateway):
  cp = params(identity, manual, gateway=gateway)
  assert qualified(cp)
  policy = policy_for(cp)
  assert policy.intent_supported and policy.normal_runtime_supported
  cp.alternativeExperience |= policy.alternative_experience_addition
  cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
  assert cp.safetyConfigs[0].safetyParam == (24 if manual else 8)
  assert qualified(cp, marked_only=True)
  assert native_matches_cp(cp, 15, 24 if manual else 8)
  assert not cp.openpilotLongitudinalControl and cp.pcmCruise
  long_cp = params(identity, manual, alpha=True, gateway=gateway)
  long_policy = policy_for(long_cp)
  assert long_policy.intent_supported == gateway
  if gateway:
    long_cp.alternativeExperience |= long_policy.alternative_experience_addition
    long_cp.safetyConfigs[0].safetyParam |= long_policy.safety_param_addition
    assert long_cp.safetyConfigs[0].safetyParam == (25 if manual else 9)
    assert native_matches_cp(long_cp, 15, 25 if manual else 9)
    assert long_cp.openpilotLongitudinalControl and not long_cp.pcmCruise
  for field, bad in (("passive", True), ("dashcamOnly", True), ("notCar", True), ("flags", 1 << 20),
                     ("alternativeExperience", 33), ("transmissionType", structs.CarParams.TransmissionType.unknown)):
    altered = structs.CarParams(**cp.to_dict())
    setattr(altered, field, bad)
    assert not qualified(altered)
  assert not qualified(None) and not qualified(SimpleNamespace(brand="volkswagen"))

@pytest.mark.parametrize("manual", (False, True))
def test_intent_main_brake_temporary_fault_and_current_serialized_native_receipt(manual):
  identity = sorted(MQB_CARS)[0]
  cp = params(identity, manual)
  policy = policy_for(cp)
  cp.alternativeExperience = 32
  cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
  settings = AolSettings(True, 5., 0, 9, (0, 0, 0), (0, 0, 0))
  owner = VolkswagenStockCardIntent(cp, settings)
  cs = structs.CarState(canValid=True, canTimeout=False, gearShifter=structs.CarState.GearShifter.drive,
                        vEgo=20., standstill=False, buttonEvents=[])
  now = 1_000_000_000
  for tick, main in enumerate((True, False, True, True)):
    cs.cruiseState.available = main
    owner.update(cs, now_ns=now + tick * 10_000_000)
  assert owner.allowed_latch
  wire = encode_intent(IntentState("card", 1, now, now, now + 200_000_000,
                                  *owner.output(cs), True, owner.allowed_latch))
  intent = decode_intent(wire)
  assert intent is not None and intent.allowedLatch
  native_wire = encode_safety(SafetyState(1, True, now, now + 200_000_000, 15,
    cp.safetyConfigs[0].safetyParam, True, False, True, False, "panda", "drive"))
  sm = SimpleNamespace(seen={"aolSafetyWire": True}, valid={"aolSafetyWire": True},
    logMonoTime={"aolSafetyWire": now}, __getitem__=None)
  class SM:
    seen = sm.seen
    valid = sm.valid
    alive = {"aolSafetyWire": True}
    logMonoTime = sm.logMonoTime
    def __getitem__(self, key):
      return native_wire
  native = current_native(SM(), cp, now_ns=now, axis_session_id="drive")
  assert native is not None
  assert current_native(SM(), cp, now_ns=now, axis_session_id="other") is None
  assert current_native(SM(), cp, now_ns=now + 200_000_001, axis_session_id="drive") is None
  for brake, speed, temporary, expected in ((False, 20., False, True), (True, 3., False, False),
                                          (True, 20., False, True), (False, 20., True, False)):
    cs.brakePressed, cs.vEgo, cs.steerFaultTemporary = brake, speed, temporary
    decision = decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent, native=native,
      car_state=cs, initialized=True, model_ready=True, no_entry=False, immediate_disable=False,
      dm_lockout=False, pause_brake_mps=settings.pause_brake_mps)
    assert decision.lateral_active == expected
    assert not decision.longitudinal_active
  cs.steerFaultTemporary = False
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=True)]
  owner.update(cs, now_ns=now + 50_000_000)
  # Unassigned Cancel ends cruise, not separately armed lateral intent.
  assert owner.allowed_latch
  cs.buttonEvents = []
  cs.cruiseState.available = False
  owner.update(cs, now_ns=now + 60_000_000)
  # MAIN off must still revoke the lateral latch.
  assert not owner.allowed_latch


@pytest.mark.parametrize('manual', (False, True))
def test_gateway_long_profile_rejects_changed_identity_bus_and_safety_context(manual):
  from opendbc.car.volkswagen.long_aol import qualified as long_qualified
  cp = params(sorted(MQB_CARS)[0], manual, alpha=True, gateway=True)
  policy = policy_for(cp)
  assert policy.intent_supported
  cp.alternativeExperience = 32
  cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
  assert long_qualified(cp, marked_only=True)
  changes = (
    ('passive', True), ('notCar', True), ('dashcamOnly', True), ('flags', 1 << 20),
    ('networkLocation', structs.CarParams.NetworkLocation.fwdCamera),
    ('brand', 'toyota'),
    ('transmissionType', structs.CarParams.TransmissionType.unknown),
    ('steerControlType', structs.CarParams.SteerControlType.angle),
    ('openpilotLongitudinalControl', False), ('pcmCruise', True),
    ('alphaLongitudinalAvailable', False), ('alternativeExperience', 33),
  )
  for field, value in changes:
    altered = structs.CarParams(**cp.to_dict())
    setattr(altered, field, value)
    assert not long_qualified(altered), field
    assert not policy_for(altered).intent_supported, field
  for word in (0, 1, 8, 24, 25 if not manual else 9, 0x8009):
    altered = structs.CarParams(**cp.to_dict())
    altered.safetyConfigs[0].safetyParam = word
    assert not long_qualified(altered)
  altered = structs.CarParams(**cp.to_dict())
  altered.safetyConfigs = [structs.CarParams.SafetyConfig(safetyModel=structs.CarParams.SafetyModel.noOutput, safetyParam=1),
                           *altered.safetyConfigs]
  assert not long_qualified(altered)
  altered = structs.CarParams(**cp.to_dict())
  altered.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.volkswagenMeb
  assert not long_qualified(altered)
