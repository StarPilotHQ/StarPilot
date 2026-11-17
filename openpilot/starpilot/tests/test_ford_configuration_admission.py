"""Ford dual-Panda and transmission admission remains exact and fail-closed."""
from dataclasses import replace
import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.aol import qualified
from openpilot.starpilot.aol.vehicle import native_matches_cp, policy_for
from openpilot.starpilot.aol.runtime import native_inventory_matches_cp
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState
from openpilot.starpilot.tests.ford_lifecycle_helpers import FORD_FACTORIES


@pytest.mark.parametrize('identity,base', FORD_FACTORIES)
@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('manual', [False, True])
def test_exact_dual_owner_and_manual_identity(identity, base, alpha, manual):
  fp = gen_empty_fingerprint()
  if not manual:
    fp[4][0x5A] = 8
  fp[6].update({0x3D6: 8, 0x186: 8})
  cp = CarInterface.get_params(identity, fp, [], alpha, True, False)
  policy = policy_for(cp)
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  word = base | int(alpha) | (256 if manual else 0)
  assert cp.safetyConfigs[-1].safetyParam == word
  assert qualified(cp, marked_only=True) and native_matches_cp(cp, 6, word)
  # bits 9-14 are the BluePilot lateral library's; bit 15 belongs to no one
  bluepilot = cp.as_reader().as_builder()
  bluepilot.safetyConfigs[-1].safetyParam = word | 0x4000
  assert qualified(bluepilot, marked_only=True) and native_matches_cp(bluepilot, 6, word | 0x4000)
  for field, value in [('safetyParam', word ^ 256), ('safetyParam', word | 0x8000), ('safetyModel', structs.CarParams.SafetyModel.noOutput)]:
    bad = cp.as_reader().as_builder()
    setattr(bad.safetyConfigs[-1], field, value)
    assert not qualified(bad) and not native_matches_cp(bad, 6, word)
  for field, value in [('safetyParam', 1), ('safetyModel', structs.CarParams.SafetyModel.ford)]:
    bad = cp.as_reader().as_builder()
    setattr(bad.safetyConfigs[0], field, value)
    assert not qualified(bad) and not native_matches_cp(bad, 6, word)
  bad = cp.as_reader().as_builder()
  bad.safetyConfigs = [cp.safetyConfigs[0], cp.safetyConfigs[0], cp.safetyConfigs[-1]]
  assert not qualified(bad)
  slots = (PandaSlot(0, 'passive', int(cp.safetyConfigs[0].safetyModel.raw), 0, 32, False, False), PandaSlot(1, 'ford', 6, word, 32, True, False))
  state = SafetyState(1, True, 100, 200, 6, word, True, bool(alpha), True, bool(alpha), 'ford', 'session',
                      sourcePandaStatesMonoTime=100, pandaInventory=slots)
  assert native_inventory_matches_cp(state, cp)
  assert not native_inventory_matches_cp(replace(state, sourcePandaStatesMonoTime=0, pandaInventory=()), cp)
  for index, changes in [(0, {'controlsAllowed': True}), (0, {'safetyParam': 1}), (0, {'hardwareSerial': 'ford'}),
                         (0, {'heartbeatLost': True}), (1, {'safetyRxChecksInvalid': True}),
                         (1, {'slotIndex': 0}), (1, {'safetyParam': word ^ 1}), (1, {'alternativeExperience': 0})]:
    invalid = list(slots)
    invalid[index] = replace(invalid[index], **changes)
    assert not native_inventory_matches_cp(replace(state, pandaInventory=tuple(invalid)), cp)


def test_marked_manual_policy_survives_cards_alternative_experience_reset():
  fp = gen_empty_fingerprint()
  cp = CarInterface.get_params(FORD_FACTORIES[0][0], fp, [], False, False, False)
  initial = policy_for(cp)
  assert cp.safetyConfigs[-1].safetyParam == 0 and initial.safety_param_addition == 256
  cp.safetyConfigs[-1].safetyParam |= initial.safety_param_addition
  cp.alternativeExperience |= initial.alternative_experience_addition
  assert native_matches_cp(cp, 6, 256)
  cp.alternativeExperience = 0  # Car resets this before reapplying saved settings.
  recovered = policy_for(cp)
  assert recovered.intent_supported and recovered.safety_param_addition == 256
  cp.safetyConfigs[-1].safetyParam |= recovered.safety_param_addition
  cp.alternativeExperience |= recovered.alternative_experience_addition
  assert native_matches_cp(cp, 6, 256)
