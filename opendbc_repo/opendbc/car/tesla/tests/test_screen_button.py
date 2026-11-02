"""Physical harness capability and optional gesture parser are separate."""
import pytest
from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.carstate import CarState, TeslaScreenCANParser
from opendbc.car.tesla.screen_button import apply_screen_button, screen_tap_supported, screen_qualified
from opendbc.car.tesla.values import CAR, CANBUS, TeslaFlags


def screen_params(candidate=CAR.TESLA_MODEL_3, *, harness=True, modern=True, long=False):
  fingerprint = gen_empty_fingerprint()
  if harness:
    fingerprint[CANBUS.vehicle][0x3DF] = 8
  if modern:
    fingerprint[CANBUS.party][0x054] = 8
  return CarInterface.get_params(candidate, fingerprint, [], long, False, False)


@pytest.mark.parametrize('candidate', [CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y])
@pytest.mark.parametrize('long', [False, True])
@pytest.mark.parametrize('brake', [False, True])
def test_exact_factory_harness_frozen_words_and_capability_off(candidate, long, brake):
  cp = screen_params(candidate, long=long)
  assert screen_tap_supported(cp)
  assert cp.flags & TeslaFlags.HAS_VEHICLE_BUS
  assert not screen_qualified(cp)
  apply_screen_button(cp, True, brake)
  assert cp.safetyConfigs[0].safetyParam == 512 + 1024 * brake + long
  assert screen_qualified(cp)
  assert screen_tap_supported(cp)
  assert cp.flags & TeslaFlags.AOL_SCREEN_BUTTON
  apply_screen_button(cp, False, brake)
  assert cp.safetyConfigs[0].safetyParam == int(long)
  assert screen_tap_supported(cp)
  assert not cp.flags & TeslaFlags.AOL_SCREEN_BUTTON


@pytest.mark.parametrize('candidate', [CAR.TESLA_MODEL_S_PREAP, CAR.TESLA_MODEL_S_HW1, CAR.TESLA_MODEL_X])
def test_no_legacy_or_model_x_screen_permission(candidate):
  cp = screen_params(candidate)
  before = cp.to_bytes()
  assert not screen_tap_supported(cp)
  apply_screen_button(cp, True, True)
  assert cp.to_bytes() == before


def test_no_harness_old_encoding_malformed_cp_or_reused_flags():
  for harness, modern in ((False, True), (True, False)):
    cp = screen_params(harness=harness, modern=modern)
    assert not screen_tap_supported(cp)
    before = cp.to_bytes()
    apply_screen_button(cp, True, True)
    assert cp.to_bytes() == before
  for word in (2, 3, 16, 258, 1024, 514, 1538):
    cp = screen_params()
    cp.safetyConfigs[0].safetyParam = word
    assert not screen_tap_supported(cp)
  cp = screen_params()
  fingerprint = gen_empty_fingerprint()
  fingerprint[CANBUS.party][0x054] = 8
  rebuilt = CarInterface._get_params(cp, CAR.TESLA_MODEL_3, fingerprint, [], False, False, False)
  assert not rebuilt.flags & TeslaFlags.HAS_VEHICLE_BUS


def test_literal_optional_screen_frames_baseline_edge_and_exact_dlc():
  cp = screen_params()
  apply_screen_button(cp, True, False)
  cs = CarState(cp)
  parser = TeslaScreenCANParser()
  packer = CANPacker('tesla_model3_vehicle')
  frame = packer.make_can_msg('UI_status2', 1, {'UI_activeTouchPoints': 3})
  assert frame[0] == 0x3DF and len(frame[1]) == 8 and frame[1][3] == 3
  parser.update([(1_000_000_000, [frame])])
  assert cs.update_screen_button(parser) == []
  neutral = packer.make_can_msg('UI_status2', 1, {'UI_activeTouchPoints': 0})
  parser.update([(1_020_000_000, [neutral, frame])])
  events = cs.update_screen_button(parser)
  assert [(event.type, event.pressed) for event in events] == [(structs.CarState.ButtonEvent.Type.lkas, False),
                                                              (structs.CarState.ButtonEvent.Type.lkas, True)]
  parser.update([(1_030_000_000, [(frame[0], frame[1][:-1], 1), (frame[0], frame[1], 0)])])
  assert cs.update_screen_button(parser) == []
