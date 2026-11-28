from dataclasses import replace
from unittest.mock import patch

import pytest

from opendbc.car import structs
from opendbc.car.hyundai.classic_long_aol import CLASSIC_LONG_IDS, aol_word, qualified
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.tests.test_classic_family_cancel_caller import classic_frames
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.tests.test_classic_long_profiles import params
from openpilot.starpilot.car.hyundai.aol import create_intent, policy_for

Button = structs.CarState.ButtonEvent.Type


def configure(identity=CAR.HYUNDAI_SONATA):
  cp = params(identity, lda=True)
  cp.safetyConfigs[0].safetyParam = aol_word(cp)
  cp.alternativeExperience = 32
  assert qualified(cp, marked_only=True)
  return cp


def saved(mapped):
  return AolSettings(True, 0., 9 if mapped == Button.lkas else 0,
                     9 if mapped == Button.mainCruise else 0, (0, 0, 0), (0, 0, 0))


def state(button=None, pressed=False):
  value = structs.CarState(canValid=True, gearShifter='drive')
  value.cruiseState.available = True
  if button is not None:
    value.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=pressed)]
  return value


def update(owner, tick, button=None, pressed=False, **kwargs):
  owner.update(state(button, pressed), now_ns=10_000_000_000 + tick * 10_000_000, fault_active=False, **kwargs)


@pytest.mark.parametrize('identity', sorted(CLASSIC_LONG_IDS, key=str))
@pytest.mark.parametrize('mapped', [Button.mainCruise, Button.lkas])
def test_actual_family_factory_keeps_unassigned_physical_button_independent(identity, mapped):
  if identity == CAR.HYUNDAI_I30_2026:
    cp = params(identity, lda=True)
    assert cp.dashcamOnly and not cp.openpilotLongitudinalControl
    assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput
    assert not qualified(cp, marked_only=True)
    return
  cp = configure(identity)
  before = cp.to_dict()
  settings = saved(mapped)
  owner = create_intent(cp, settings)
  other = Button.lkas if mapped == Button.mainCruise else Button.mainCruise
  update(owner, 0)
  update(owner, 1, other, True)
  assert not owner.allowed_latch, 'UNASSIGNED_BUTTON_ALIASES_CONFIGURED_TOGGLE'
  update(owner, 2, other, False)
  update(owner, 3, mapped, True)
  assert owner.allowed_latch and not owner.pause_lateral
  update(owner, 4, mapped, False)
  update(owner, 5, other, True)
  assert owner.allowed_latch and not owner.pause_lateral
  update(owner, 6, other, False)
  update(owner, 7, mapped, True)
  assert not owner.allowed_latch and owner.pause_lateral
  assert owner.settings == settings and cp.to_dict() == before


@pytest.mark.parametrize('mapped', [Button.mainCruise, Button.lkas])
def test_sonata_actual_can_parser_keeps_main_and_lkas_distinct(mapped):
  cp = configure()
  ci = CarInterface(cp)
  owner = create_intent(cp, saved(mapped))
  other = Button.lkas if mapped == Button.mainCruise else Button.mainCruise
  plan = [(None, False)] * 20 + [(other, True)] * 2 + [(other, False)] * 2
  plan += [(mapped, True)] * 2 + [(mapped, False)] * 2 + [(other, True)] * 2 + [(other, False)] * 2
  observed = set()
  for tick, (button, pressed) in enumerate(plan):
    now = 10_000_000_000 + tick * 10_000_000
    frames = classic_frames(ci, tick, main=int(button == Button.mainCruise and pressed),
                            lkas=int(button == Button.lkas and pressed))
    with patch('opendbc.car.hyundai.classic_scc_aol.time.clock_gettime_ns', return_value=now):
      parsed = ci.update([(now, frames)])
    owner.update(parsed, now_ns=now, fault_active=False)
    for event in parsed.buttonEvents:
      if event.pressed:
        observed.add(event.type.raw)
        assert event.type == button
    if 20 <= tick < 24:
      assert parsed.canValid and not owner.allowed_latch, 'UNASSIGNED_BUTTON_ALIASES_CONFIGURED_TOGGLE'
    if tick >= 24:
      assert parsed.canValid and owner.allowed_latch and not owner.pause_lateral
  assert observed == {int(Button.mainCruise), int(Button.lkas)}


@pytest.mark.parametrize('mapped', [Button.mainCruise, Button.lkas])
def test_live_mapping_changes_do_not_need_owner_reconstruction(mapped):
  cp = configure()
  first = Button.lkas if mapped == Button.mainCruise else Button.mainCruise
  owner = create_intent(cp, saved(first))
  update(owner, 0)
  owner.settings = saved(mapped)
  update(owner, 1, first, True)
  assert not owner.allowed_latch, 'UNASSIGNED_BUTTON_ALIASES_CONFIGURED_TOGGLE'
  update(owner, 2, first, False)
  update(owner, 3, mapped, True)
  assert owner.allowed_latch and not owner.pause_lateral


@pytest.mark.parametrize('defect', ['can', 'timeout', 'fault', 'disabled', 'consumed'])
def test_main_mapping_cannot_bypass_existing_denials(defect):
  owner = create_intent(configure(), saved(Button.mainCruise))
  update(owner, 0)
  value = state(Button.mainCruise, True)
  if defect == 'can':
    value.canValid = False
  elif defect == 'timeout':
    value.canTimeout = True
  elif defect == 'disabled':
    owner.settings = replace(owner.settings, enabled=False)
  owner.update(value, now_ns=10_010_000_000, fault_active=defect == 'fault',
               consumed_buttons=frozenset((int(Button.mainCruise),)) if defect == 'consumed' else frozenset())
  assert not owner.allowed_latch


@pytest.mark.parametrize('button', [Button.mainCruise, Button.lkas])
def test_existing_unmapped_native_default_stays_unchanged(button):
  settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  owner = create_intent(configure(), settings)
  update(owner, 0)
  update(owner, 1, button, True)
  assert owner.allowed_latch


@pytest.mark.parametrize('mapped', [Button.mainCruise, Button.lkas])
def test_stock_scc_reference_mapping_remains_independent(mapped):
  cp = params(CAR.HYUNDAI_SONATA, lda=True, release=True)
  policy = policy_for(cp)
  cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  owner = create_intent(cp, saved(mapped))
  other = Button.lkas if mapped == Button.mainCruise else Button.mainCruise
  update(owner, 0)
  update(owner, 1, other, True)
  assert not owner.allowed_latch
  update(owner, 2, other, False)
  update(owner, 3, mapped, True)
  assert owner.allowed_latch
