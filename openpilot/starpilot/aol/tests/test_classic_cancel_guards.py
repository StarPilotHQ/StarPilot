
from dataclasses import replace

import pytest
from opendbc.car import structs
from opendbc.car.hyundai.classic_long_aol import independent_buttons_qualified
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.cereal import messaging
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.tests import test_hkg_shared_cancel_guard as guards
from openpilot.starpilot.aol.tests.test_classic_long_profiles import params
from openpilot.starpilot.aol.transport_pause import cp_identity
from openpilot.starpilot.aol.wire import encode_safety
from openpilot.starpilot.car.hyundai.aol import create_intent
from openpilot.starpilot.aol.tests import classic_cancel_test_helpers as joined


def marked():
  cp = params(CAR.HYUNDAI_SONATA, lda=True)
  cp.safetyConfigs[0].safetyParam = 0xC04
  cp.alternativeExperience = 32
  return cp


@pytest.mark.parametrize('case', ['retain', 'settings_off', 'default_buttons', 'configured_cancel', 'pause', 'not_latched',
  'no_raw_source', 'expired_original', 'old_native', 'native_lat_denied', 'native_long_allowed', 'no_long_request',
  'wrong_session', 'wrong_inventory', 'panda_rx', 'panda_fault', 'failed_feedback', 'changed_cp', 'producer',
  'wrong_epoch', 'nonfull', 'hard_fault', 'reverse', 'steer_fault', 'invalid_can', 'no_physical_cancel'])
def test_sonata_reuses_all_current_guard_fault_and_original_deadline_negatives(case, monkeypatch):
  original = guards.captured_context
  def classic_context():
    _, cs, sm, feedback, owner, native, now = original()
    cp = marked()
    feedback.cp = cp_identity(cp)
    config = cp.safetyConfigs[0]
    model = int(config.safetyModel.raw)
    native = replace(native, safetyModel=model, safetyParam=config.safetyParam,
      pandaInventory=(replace(native.pandaInventory[0], safetyModel=model, safetyParam=config.safetyParam),))
    pandas = messaging.new_message('pandaStates', 1)
    pandas.pandaStates[0].safetyModel = config.safetyModel
    pandas.pandaStates[0].safetyParam = config.safetyParam
    pandas.pandaStates[0].alternativeExperience = 32
    sm['pandaStates'] = pandas.as_reader().pandaStates
    sm['aolSafetyWire'] = encode_safety(native)
    return cp, cs, sm, feedback, owner, native, now
  monkeypatch.setattr(guards, 'captured_context', classic_context)


  guards.test_shared_cancel_retains_only_original_current_reason_qualified_intent(case)


@pytest.mark.parametrize('case', ['exact', 'unmarked', 'experience', 'no_lda', 'other_classic', 'stock', 'two_panda', 'extra_word', 'release'])
def test_sonata_scope_never_promotes_other_classic_contracts(case):
  cp = marked()
  if case == 'unmarked':
    cp.safetyConfigs[0].safetyParam = 4
  elif case == 'experience':
    cp.alternativeExperience = 0
  elif case == 'no_lda':
    cp.flags = int(cp.flags) & ~int(HyundaiFlags.HAS_LDA_BUTTON)
    cp.safetyConfigs[0].safetyParam = 0x404
  elif case == 'other_classic':
    cp.carFingerprint = CAR.HYUNDAI_IONIQ
  elif case == 'stock':
    cp.openpilotLongitudinalControl = False
  elif case == 'two_panda':
    owner = structs.CarParams.SafetyConfig(safetyModel=str(cp.safetyConfigs[0].safetyModel), safetyParam=0xC04)
    cp.safetyConfigs = [structs.CarParams.SafetyConfig(safetyModel='noOutput'), owner]
  elif case == 'extra_word':
    cp.safetyConfigs[0].safetyParam |= 0x80
  elif case == 'release':
    cp = params(CAR.HYUNDAI_SONATA, lda=True, release=True)
  assert independent_buttons_qualified(cp) == (case == 'exact')


@pytest.mark.parametrize('role', ['marked', 'unmarked', 'wrong_bus', 'expired_receipt', 'foreign_state'])
def test_sonata_cancel_source_requires_current_real_parser_batch(role):
  cp = marked() if role != 'unmarked' else params(CAR.HYUNDAI_SONATA, lda=True)
  ci = CarInterface(cp)
  ci.update([])
  for tick in range(29):
    now = 10_000_000_000 + tick * 10_000_000
    frames = joined.classic_frames(ci, tick, button=4 if tick == 28 else 0)
    if role == 'wrong_bus' and tick == 28:
      frames = [(address, data, 1 if address == 0x4F1 else bus) for address, data, bus in frames]
    state = ci.update([(now, frames)])
  if role == 'wrong_bus':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  observed = state.as_reader().as_builder() if role == 'foreign_state' else state
  receipt = now - 30_000_001 if role == 'expired_receipt' else now
  caller = Car.__new__(Car)
  caller.CI = ci
  caller._aol_can_observation = (observed, receipt, now, now)
  assert (caller.aol_cancel_source(state, now) == now) == (role == 'marked')


@pytest.mark.parametrize('lkas,main', [(9, 0), (0, 9), (0, 0)])
def test_qualified_buttons_preserve_saved_mapping_and_cancel_behavior(lkas, main):
  owner = create_intent(marked(), AolSettings(True, 0., lkas, main, (0, 0, 0), (0, 0, 0)))
  assert owner.independent_cruise_buttons
  state = structs.CarState(canValid=True, gearShifter='drive')
  state.cruiseState.available = True
  owner.update(state, now_ns=1)
  state.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(state, now_ns=2)
  assert owner.allowed_latch == (main != 9)
  state.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=False)]
  owner.update(state, now_ns=3)
  if main == 9:
    state.buttonEvents = [structs.CarState.ButtonEvent(type='mainCruise', pressed=True)]
    owner.update(state, now_ns=4)
    assert owner.allowed_latch
    state.buttonEvents = [structs.CarState.ButtonEvent(type='mainCruise', pressed=False)]
    owner.update(state, now_ns=5)
  original_edge = owner._last_latch_edge_ns
  state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  owner.update(state, now_ns=6)
  assert owner.allowed_latch
  assert owner._last_latch_edge_ns == original_edge
