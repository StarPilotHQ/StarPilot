"""Startup preferences keep the exact speed-sync safety words: co-op steering and Always On Lateral compose with it."""
from pathlib import Path
import re

import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.structs import car
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.carcontroller import CarController
from opendbc.car.tesla.carstate import CarState
from opendbc.car.tesla.screen_button import (SYNC_WORDS, apply_modern_aol, apply_screen_button, modern_aol_qualified, modern_aol_supported,
                                             screen_qualified, screen_tap_supported, speed_sync_allowed)
from opendbc.car.tesla.speed_sync import SYNC_FLAG, prepare as apply_speed_sync, selected
from opendbc.car.tesla.values import CAR, CANBUS, DBC, TeslaFlags
from openpilot.starpilot.car.tesla.coop_preferences import prepare as apply_cooperative, supported

MODERN = (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y)
# The words tesla_sync_param_valid accepts (safety/modes/tesla_speed_sync.h): 4096 on a word that carries LONG_CONTROL.
NATIVE_SYNC_WORDS = frozenset(4096 | word for word in (1, 257, 513, 769, 1537, 1793, 2049, 2305, 3073, 3329))


def sync_params(candidate=CAR.TESLA_MODEL_3, long=True):
  """A Model 3/Y with the add-on cable, as it leaves startup with Comma can set speed on Tesla dashboard requested."""
  fingerprint = gen_empty_fingerprint()
  fingerprint[CANBUS.vehicle][0x3DF] = 8
  fingerprint[CANBUS.autopilot_party][0x489] = 8
  return apply_speed_sync(CarInterface.get_params(candidate, fingerprint, [], long, False, False), True)


def raw(cp):
  return cp.as_reader().as_builder().to_bytes()


def startup(cp, coop, screen=False, brake=False):
  apply_screen_button(cp, screen, brake)
  apply_cooperative(cp, coop)
  return cp


def expected_word(coop, screen, brake):
  return 4097 | (256 if coop else 0) | ((512 | (1024 if brake else 0)) if screen else 0)


def assert_rejected(cp):
  before = raw(cp)
  assert not supported(cp)
  assert not screen_tap_supported(cp)
  assert not screen_qualified(cp)
  assert not modern_aol_supported(cp)
  apply_cooperative(cp, True)
  apply_screen_button(cp, True, True)
  apply_modern_aol(cp, True, True, True)
  assert raw(cp) == before


def test_python_words_mirror_the_native_list():
  assert SYNC_WORDS == NATIVE_SYNC_WORDS
  header = (Path(__file__).parents[3] / 'safety/modes/tesla_speed_sync.h').read_text()
  body = header[header.index('static bool tesla_sync_param_valid'):]
  assert frozenset(4096 | int(word) for word in re.findall(r'base == (\d+)U', body)) == SYNC_WORDS
  assert 'GET_FLAG(param, 4096U)' in body


@pytest.mark.parametrize('candidate', MODERN)
def test_selected_word_precondition(candidate):
  cp = sync_params(candidate)
  assert cp.safetyConfigs[0].safetyParam == 4097 and selected(cp)
  assert cp.openpilotLongitudinalControl
  assert cp.flags & TeslaFlags.HAS_VEHICLE_BUS
  assert speed_sync_allowed(cp, 4097)
  assert supported(cp) and screen_tap_supported(cp) and modern_aol_supported(cp) and not screen_qualified(cp)
  assert CarController(DBC[cp.carFingerprint], cp).speed_sync is not None


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('coop', (False, True))
def test_cooperative_on_off_keeps_sync(candidate, coop):
  cp = startup(sync_params(candidate), coop)
  assert cp.safetyConfigs[0].safetyParam == (4353 if coop else 4097)
  apply_cooperative(cp, not coop)
  assert cp.safetyConfigs[0].safetyParam == (4097 if coop else 4353)
  assert selected(cp)


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('coop', (False, True))
@pytest.mark.parametrize('screen', (False, True))
@pytest.mark.parametrize('brake', (False, True))
def test_screen_brake_matrix_composes_native_words(candidate, coop, screen, brake):
  cp = startup(sync_params(candidate), coop, screen, brake)
  expected = expected_word(coop, screen, brake)
  assert expected in NATIVE_SYNC_WORDS
  assert cp.safetyConfigs[0].safetyParam == expected
  assert supported(cp) and screen_tap_supported(cp) and selected(cp)
  assert screen_qualified(cp) == screen
  assert bool(cp.flags & TeslaFlags.AOL_SCREEN_BUTTON) == screen
  apply_screen_button(cp, not screen, brake)
  assert cp.safetyConfigs[0].safetyParam == expected_word(coop, not screen, brake)
  assert screen_qualified(cp) == (not screen)


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('coop', (False, True))
@pytest.mark.parametrize('screen', (False, True))
@pytest.mark.parametrize('brake', (False, True))
def test_always_on_lateral_composes_native_words(candidate, coop, screen, brake):
  # Ordinary Always On Lateral is 2048 and the screen tap 512; either one keeps speed sync on top.
  cp = sync_params(candidate)
  apply_modern_aol(cp, True, screen, brake)
  apply_cooperative(cp, coop)
  expected = 4097 | (512 if screen else 2048) | (1024 if brake else 0) | (256 if coop else 0)
  assert cp.safetyConfigs[0].safetyParam == expected and expected in NATIVE_SYNC_WORDS
  assert selected(cp) and supported(cp) and modern_aol_supported(cp) and modern_aol_qualified(cp)
  apply_modern_aol(cp, False, screen, brake)
  assert cp.safetyConfigs[0].safetyParam == 4097 | (256 if coop else 0)
  assert selected(cp) and not modern_aol_qualified(cp)


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('order', ('sync last', 'sync first', 'sync between'))
@pytest.mark.parametrize('aol', (None, 'ordinary', 'screen'))
def test_any_startup_order_gives_the_same_word(candidate, order, aol):
  cp = sync_params(candidate)
  apply_speed_sync(cp, False)
  steps = [lambda: apply_modern_aol(cp, aol is not None, aol == 'screen', True), lambda: apply_cooperative(cp, True)]
  steps.insert({'sync first': 0, 'sync between': 1, 'sync last': 2}[order], lambda: apply_speed_sync(cp, True))
  for _ in range(2):  # prepare, then finalize on the same parameters
    for step in steps:
      step()
  expected = 4097 | 256 | {None: 0, 'ordinary': 2048 | 1024, 'screen': 512 | 1024}[aol]
  assert cp.safetyConfigs[0].safetyParam == expected and expected in NATIVE_SYNC_WORDS


@pytest.mark.parametrize('coop_first', (False, True))
@pytest.mark.parametrize('brake', (False, True))
def test_repeated_startup_ordering_preserves_sync(coop_first, brake):
  cp = sync_params()
  expected = expected_word(True, True, brake)
  for _ in range(3):
    if coop_first:
      apply_cooperative(cp, True)
      apply_screen_button(cp, True, brake)
    else:
      apply_screen_button(cp, True, brake)
      apply_cooperative(cp, True)
    assert cp.safetyConfigs[0].safetyParam == expected
    assert screen_qualified(cp)
  apply_cooperative(cp, False)
  assert cp.safetyConfigs[0].safetyParam == expected & ~256
  apply_screen_button(cp, False, brake)
  assert cp.safetyConfigs[0].safetyParam == 4097
  apply_cooperative(cp, True)
  assert cp.safetyConfigs[0].safetyParam == 4353


def test_sync_longitudinal_off_rejected():
  for word in (4096, 4097, 4352):
    cp = sync_params(long=False)
    assert not cp.safetyConfigs[0].safetyParam & SYNC_FLAG, 'never requested without longitudinal control'
    cp.safetyConfigs[0].safetyParam = word
    assert_rejected(cp)
  cp = sync_params()
  cp.openpilotLongitudinalControl = False
  assert_rejected(cp)


def test_sync_without_vehicle_bus_or_with_unknown_flag_rejected():
  cp = sync_params()
  cp.flags &= ~int(TeslaFlags.HAS_VEHICLE_BUS)
  assert_rejected(cp)
  cp = sync_params()
  cp.flags |= 64
  assert_rejected(cp)


@pytest.mark.parametrize('word', (4097 | 2, 4097 | 4, 4097 | 16, 4097 | 8192, 4097 | 1024, 4353 | 8192, 4097 | 512 | 2048))
def test_sync_with_unknown_safety_bit_rejected(word):
  cp = sync_params()
  cp.safetyConfigs[0].safetyParam = word
  assert_rejected(cp)


def test_screen_word_must_match_the_screen_flag():
  cp = sync_params()
  cp.safetyConfigs[0].safetyParam = 4097 | 512  # the screen word without the screen flag in CarParams
  assert_rejected(cp)


@pytest.mark.parametrize('candidate', MODERN)
def test_real_controller_and_carstate_cooperate_after_sync_startup(candidate):
  cp = startup(sync_params(candidate), True)
  assert cp.safetyConfigs[0].safetyParam == 4353
  cs = CarState(cp)
  cs.hands_on_level = 0
  cs.das_control = {'DAS_controlCounter': 0}
  cs.out.vEgo = cs.out.vEgoRaw = 20.
  cs.out.steeringAngleDeg = 0.
  cs.out.gasPressed = False
  controller = CarController(DBC[candidate], cp)
  assert controller.coop_enabled and controller.speed_sync is not None
  cc = car.CarControl.new_message(latActive=True)
  for tick in range(10):
    cs.out.steeringTorque, cs.out.steeringDisengage = 1., False
    _, sends = controller.update(cc.as_reader(), cs, 1_000_000_000 + tick * 10_000_000)
    assert not [message for message in sends if message[0] == 0x3C2], 'no scroll request without the comma set speed context'
  assert controller.steering_limit_info.get('valid', False)
  cs.out.steeringTorque, cs.out.steeringDisengage = 5.1, True
  controller.update(cc.as_reader(), cs, 1_100_000_000)
  assert not controller.steering_limit_info.get('valid', False)
  assert cp.safetyConfigs[0].safetyParam == 4353
