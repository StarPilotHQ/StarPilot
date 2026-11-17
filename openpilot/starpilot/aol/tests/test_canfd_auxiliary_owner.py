from dataclasses import replace
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_owner import config_index
from opendbc.car.hyundai.canfd_stock_aol import qualified, qualified_long
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.runtime import current_native
from openpilot.starpilot.aol.tests.test_panda_inventory import NOW, bound_state, sm_for
from openpilot.starpilot.car.hyundai.aol import create_intent, native_accepts_cp, policy_for
from openpilot.starpilot.ui.wheel_feature import WheelFeature


def factory(car=CAR.KIA_SPORTAGE_5TH_GEN, *, offset=4, alpha=True, lka=0, alternate=True, hybrid=False):
  fp = gen_empty_fingerprint()
  bus = offset + int(bool(lka))
  fp[bus].update({0x1aa if alternate else 0x1cf: 16 if alternate else 8,
                  0x40: 32, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24})
  if lka:
    fp[offset + 2].update({0x110: 32, 0x362: 32} if lka == 2 else {0x50: 16, 0x2a4: 24})
  if hybrid:
    fp[bus][0xfa] = 8
  fw = [structs.CarParams.CarFw(ecu='adas')] if lka else []
  return CarInterface.get_params(car, fp, fw, alpha, False, False)


def mark(cp):
  policy = policy_for(cp)
  assert policy.intent_supported and policy.runtime_supported and policy.settings_supported
  auxiliary = cp.safetyConfigs[0].to_dict() if len(cp.safetyConfigs) == 2 else None
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  if auxiliary is not None:
    assert cp.safetyConfigs[0].to_dict() == auxiliary
  assert native_accepts_cp(cp, int(cp.safetyConfigs[-1].safetyModel.raw), cp.safetyConfigs[-1].safetyParam)


def test_recorded_sportage_layout_arms_from_lkas_without_changing_saved_action():
  cp = factory()
  assert cp.flags == 8206 and cp.safetyConfigs[-1].safetyParam == 0x2c
  assert cp.safetyConfigs[0].safetyModel == 'noOutput' and len(cp.safetyConfigs) == 2
  assert qualified_long(cp)
  owner = SimpleNamespace(vehicle_params=lambda: cp, configuration_vehicle=lambda: False,
                          configuration_longitudinal=lambda: False, longitudinal_available=lambda: True)
  assert 'Toggle AOL' in WheelFeature(owner).choices('LKASButtonControl')
  mark(cp)
  assert config_index(cp) == 1 and cp.safetyConfigs[-1].safetyParam == 0x82c
  assert cp.alternativeExperience == 32
  intent = create_intent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=10.)
  cs.cruiseState.available = True
  intent.update(cs, now_ns=1, fault_active=False)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  intent.update(cs, now_ns=2, fault_active=False)
  assert intent.allowed_latch
  for event in ('cancel', None):
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=event, pressed=True)] if event else []
    cs.brakePressed = event is None
    intent.update(cs, now_ns=3 if event else 4, fault_active=False)
    assert intent.allowed_latch
  state = bound_state(cp)
  assert current_native(sm_for(cp, state), cp, now_ns=NOW) == state


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('hybrid', (False, True))
@pytest.mark.parametrize('lka,alternate', ((0, False), (0, True), (1, False), (2, False)))
def test_sportage_existing_profile_is_independent_of_auxiliary_slot(alpha, hybrid, lka, alternate):
  for offset in (0, 4):
    cp = factory(offset=offset, alpha=alpha, hybrid=hybrid, lka=lka, alternate=alternate)
    assert (qualified_long(cp) if alpha else qualified(cp))
    mark(cp)
    assert config_index(cp) == offset // 4
    assert (qualified_long(cp, marked_only=True) if alpha else qualified(cp, marked_only=True))
    state = bound_state(cp)
    assert current_native(sm_for(cp, state), cp, now_ns=NOW) == state


@pytest.mark.parametrize('lka', (0, 1, 2))
def test_existing_stock_canfd_profiles_keep_exact_word_with_auxiliary(lka):
  checked = []
  for car in CAR:
    if not car.config.flags & HyundaiFlags.CANFD or car.config.flags & HyundaiFlags.CANFD_ANGLE_STEERING:
      continue
    single = factory(car, offset=0, alpha=False, lka=lka, alternate=False)
    if not qualified(single):
      continue
    cp = factory(car, alpha=False, lka=lka, alternate=False)
    assert qualified(cp), str(car)
    assert cp.safetyConfigs[-1].safetyParam == single.safetyConfigs[0].safetyParam
    mark(cp)
    state = bound_state(cp)
    assert current_native(sm_for(cp, state), cp, now_ns=NOW) == state
    checked.append(car)
  assert len(checked) > 20


@pytest.mark.parametrize('change', ('aux_model', 'aux_word', 'owner_model', 'owner_word', 'order', 'third_slot',
                                  'unknown', 'brand', 'passive', 'dashcamOnly', 'notCar', 'angle', 'experience'))
def test_auxiliary_slot_never_bypasses_profile_admission(change):
  cp = factory()
  mark(cp)
  if change == 'aux_model':
    cp.safetyConfigs[0].safetyModel = 'allOutput'
  elif change == 'aux_word':
    cp.safetyConfigs[0].safetyParam = 1
  elif change == 'owner_model':
    cp.safetyConfigs[-1].safetyModel = 'hyundai'
  elif change == 'owner_word':
    cp.safetyConfigs[-1].safetyParam ^= 1
  elif change == 'order':
    cp.safetyConfigs = list(reversed(cp.safetyConfigs))
  elif change == 'third_slot':
    cp.safetyConfigs = [*cp.safetyConfigs, structs.CarParams.SafetyConfig(safetyModel='noOutput')]
  elif change == 'unknown':
    cp.carFingerprint = 'UNKNOWN_HYUNDAI'
  elif change == 'brand':
    cp.brand = 'ford'
  elif change == 'angle':
    cp.steerControlType = 'angle'
  elif change == 'experience':
    cp.alternativeExperience = 0
  else:
    setattr(cp, change, True)
  assert not qualified_long(cp, marked_only=True)
  assert not native_accepts_cp(cp, int(structs.CarParams.SafetyModel.hyundaiCanfd), 0x82c)


@pytest.mark.parametrize('slot', (0, 1))
@pytest.mark.parametrize('fault', ('safetyRxChecksInvalid', 'heartbeatLost', 'faults'))
def test_both_native_inventory_slots_keep_all_live_health_checks(slot, fault):
  cp = factory()
  mark(cp)
  state = bound_state(cp)
  inventory = list(state.pandaInventory)
  inventory[slot] = replace(inventory[slot], **{fault: 1 if fault == 'faults' else True})
  state = replace(state, pandaInventory=tuple(inventory))
  assert current_native(sm_for(cp, state), cp, now_ns=NOW) is None


def test_actual_card_marks_recorded_slot_and_preserves_saved_quick_select():
  import os
  from unittest.mock import patch
  from opendbc.car.hyundai.radar_interface import RadarInterface
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car

  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlwaysOnLateral', True, block=True)
    saved.put_bool('AlphaLongitudinalEnabled', True, block=True)
    saved.put('LKASButtonControl', 11, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    cp = factory()
    auxiliary = cp.safetyConfigs[0].to_dict()
    card = Car(CI=CarInterface(cp), RI=RadarInterface(cp))
    try:
      assert card.aol_qualified and card.aol_card_intent is not None
      assert card.CP.safetyConfigs[0].to_dict() == auxiliary
      assert card.CP.safetyConfigs[1].safetyParam == 0x82c and card.CP.alternativeExperience == 32
      assert card.aol_card_intent.independent_cruise_buttons
      assert saved.get('LKASButtonControl') == 11
      assert saved.get('MainCruiseButtonControl') == 0
      assert saved.get_bool('AlwaysOnLateral') and saved.get_bool('AlphaLongitudinalEnabled')
    finally:
      card.vehicle_startup.close()
