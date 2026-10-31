import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.structs import car
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.car.hyundai.aol import create_intent
from opendbc.car.hyundai.classic_scc_aol import CLASSIC_SCC_IDS, qualified


def settings(lkas=0, main=0):
  return AolSettings(True, 0., lkas, main, (0, 0, 0), (0, 0, 0))


def params(identity=CAR.KIA_CEED, lda=True):
  fingerprint = gen_empty_fingerprint()
  if lda:
    fingerprint[0][0x50C] = 8
  fingerprint[0][0x38D] = 8
  fingerprint[2][0x485] = 4
  return CarInterface.get_params(identity, fingerprint, [], False, False, False)


def state(button=None, pressed=True, available=True):
  cs = car.CarState(canValid=True, gearShifter='drive', vEgo=8.)
  cs.cruiseState.available = available
  if button is not None:
    cs.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=pressed)]
  return cs


def edge(owner, button, stamp):
  owner.update(state(button, True), now_ns=stamp)
  owner.update(state(button, False), now_ns=stamp+10_000_000)


@pytest.mark.parametrize('button', ['lkas', 'mainCruise'])
def test_recorded_ceed_defaults_arm_and_toggle_without_cruise(button):
  cp = params()
  assert cp.carFingerprint == CAR.KIA_CEED and cp.flags == 0x1040180
  assert str(cp.safetyConfigs[0].safetyModel) == 'hyundaiLegacy'
  assert qualified(cp)
  original = cp.to_dict()
  saved = settings()
  owner = create_intent(cp, saved)
  owner.update(state(), now_ns=1_000_000_000)
  assert not owner.allowed_latch
  edge(owner, button, 1_010_000_000)
  assert owner.allowed_latch and not owner.pause_lateral
  cs = state('cancel')
  cs.brakePressed = True
  owner.update(cs, now_ns=1_030_000_000)
  assert owner.allowed_latch
  edge(owner, button, 1_040_000_000)
  assert not owner.allowed_latch and owner.pause_lateral
  assert cp.to_dict() == original and owner.settings == saved


def test_every_qualified_classic_lda_identity_uses_default_physical_toggle():
  seen = []
  for identity in CLASSIC_SCC_IDS:
    cp = params(identity)
    if not qualified(cp) or not cp.flags & HyundaiFlags.HAS_LDA_BUTTON:
      continue
    owner = create_intent(cp, settings())
    owner.update(state(), now_ns=1_000_000_000)
    edge(owner, 'lkas', 1_010_000_000)
    assert owner.allowed_latch, identity
    edge(owner, 'lkas', 1_040_000_000)
    assert not owner.allowed_latch, identity
    seen.append(identity)
  assert CAR.KIA_CEED in seen and len(seen) > 20


@pytest.mark.parametrize('lkas,main', [(3, 0), (4, 0), (0, 3), (0, 4), (9, 0), (0, 9), (9, 9)])
def test_saved_assignments_keep_the_existing_owner_behavior(lkas, main):
  from openpilot.starpilot.aol.intent import AolCardIntent
  from openpilot.starpilot.car.hyundai.forte_intent import ForteCardIntent
  cp = params()
  actual = create_intent(cp, settings(lkas, main))
  reference = ForteCardIntent(cp, settings(lkas, main))
  reference.independent_cruise_buttons = True
  reference.lkas_button_supported = reference.has_lkas
  for index, button in enumerate((None, 'mainCruise', 'mainCruise', 'lkas', 'lkas', 'cancel', None)):
    cs = state(button, pressed=index not in (2, 4))
    stamp = 1_000_000_000 + index*10_000_000
    actual.update(cs, now_ns=stamp)
    if lkas == 9 or main == 9:
      AolCardIntent.update(reference, cs, now_ns=stamp)
      reference.physical_latch = reference.allowed_latch
    else:
      reference.update(cs, now_ns=stamp)
    assert (actual.allowed_latch, actual.pause_lateral, actual.pause_longitudinal) == (
      reference.allowed_latch, reference.pause_lateral, reference.pause_longitudinal)


def test_no_lda_main_availability_keeps_existing_contract():
  from openpilot.starpilot.car.hyundai.forte_intent import ForteCardIntent
  cp = params(lda=False)
  actual, reference = create_intent(cp, settings()), ForteCardIntent(cp, settings())
  for index, available in enumerate((False, True, True, False)):
    cs = state(available=available)
    actual.update(cs, now_ns=1_000_000_000+index*10_000_000)
    reference.update(cs, now_ns=1_000_000_000+index*10_000_000)
    assert actual.allowed_latch == reference.allowed_latch


def test_actual_card_preserves_recorded_missing_lkas_preference():
  import os
  from pathlib import Path
  from unittest.mock import patch
  from opendbc.car.hyundai.radar_interface import RadarInterface
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car

  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlwaysOnLateral', True, block=True)
    saved.put_bool('AlphaLongitudinalEnabled', False, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    assert not Path(saved.get_param_path('LKASButtonControl')).exists()
    cp = params()
    card = Car(CI=CarInterface(cp), RI=RadarInterface(cp))
    try:
      assert card.aol_qualified and card.aol_card_intent is not None
      assert card.CP.safetyConfigs[0].safetyParam == 0xC00 and card.CP.alternativeExperience == 32
      assert not card.CP.openpilotLongitudinalControl and card.CP.pcmCruise
      owner = card.aol_card_intent
      owner.update(state(), now_ns=1_000_000_000)
      edge(owner, 'lkas', 1_010_000_000)
      assert owner.allowed_latch
      assert saved.get('MainCruiseButtonControl') == 0
      assert not Path(saved.get_param_path('LKASButtonControl')).exists()
      assert saved.get_bool('AlwaysOnLateral') and not saved.get_bool('AlphaLongitudinalEnabled')
    finally:
      card.vehicle_startup.close()


@pytest.mark.parametrize('button', ['lkas', 'mainCruise'])
def test_default_ceed_host_intent_joins_unchanged_native_legacy_lateral_only(button):
  from opendbc.safety.tests import test_hyundai_classic_scc_aol as native_fixture
  from opendbc.safety.tests.libsafety.libsafety_py import make_CANPacket
  case = native_fixture.TestHyundaiClassicSccAol(methodName='runTest')
  case.setUp()
  try:
    cp = params()
    owner = create_intent(cp, settings())
    owner.update(state(), now_ns=1_000_000_000)
    case.reset(0xC00, mode=car.CarParams.SafetyModel.hyundaiLegacy)
    for _ in range(6):
      case.feed(0xC00, source='CLU13')
    assert case.request(1) == 0
    case.request(0)
    if button == 'lkas':
      case.feed(0xC00, source='CLU13', pressed=True)
    else:
      assert case.safety.safety_rx_hook(make_CANPacket(0x4F1, 0, bytes.fromhex('28557af0')))
    edge(owner, button, 1_010_000_000)
    assert owner.allowed_latch
    assert case.request(1 if owner.allowed_latch else 0) == 1
    assert not case.safety.get_controls_allowed()
    assert case.request(3) == 1
    assert not case.safety.safety_tx_hook(case.packer.make_can_msg_safety('SCC12', 0, {'ACCMode': 1, 'aReqRaw': 1., 'aReqValue': 1.}))
    case.safety.set_aol_test_heartbeat(False)
    assert case.request(1) == 0
    edge(owner, button, 1_040_000_000)
    assert not owner.allowed_latch
  finally:
    case.tearDown()


@pytest.mark.parametrize('choice', [3, 4, 11, 22])
def test_non_aol_saved_choice_is_not_treated_as_unassigned(choice):
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.starpilot.aol.intent import read_settings
  with OpenpilotPrefix():
    saved = Params()
    saved.put_bool('AlwaysOnLateral', True, block=True)
    saved.put('LKASButtonControl', choice, block=True)
    current = read_settings(saved)
    assert not current.default_physical_buttons
    owner = create_intent(params(), current)
    owner.update(state(), now_ns=1_000_000_000)
    edge(owner, 'lkas', 1_010_000_000)
    assert not owner.allowed_latch
    assert saved.get('LKASButtonControl') == choice
