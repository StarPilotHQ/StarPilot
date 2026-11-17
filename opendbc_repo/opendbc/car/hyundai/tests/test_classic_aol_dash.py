import pytest

from opendbc.can import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.classic_scc_aol import aol_word, qualified
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC
from opendbc.safety.tests.libsafety.libsafety_py import make_CANPacket
from opendbc.safety.tests import test_hyundai_classic_scc_aol as native_fixture


def controller(identity=CAR.HYUNDAI_SONATA_HYBRID, *, marked=True):
  fp = gen_empty_fingerprint()
  fp[0][0x391] = 8
  fp[2][0x485] = 4
  cp = CarInterface.get_params(identity, fp, [], False, False, False)
  if marked:
    cp.safetyConfigs[0].safetyParam = aol_word(cp)
    cp.alternativeExperience = 32
    assert qualified(cp, marked_only=True)
  ci = CarInterface(cp)
  ci.update([])
  parser = CANParser(DBC[identity][Bus.pt], [('LKAS11', 0), ('LFAHDA_MFC', 0)], 0)
  return ci, parser


def step(ci, parser, *, enabled=False, lateral=False):
  cc = structs.CarControl(enabled=enabled, latActive=lateral)
  frame = ci.CC.frame
  actuators, packets = ci.CC.update(cc.as_reader(), ci.CS, 10_000_000_000 + frame * 10_000_000)
  parser.update((10_000_000_000 + frame * 10_000_000, packets))
  return actuators, packets


@pytest.mark.parametrize('icon', ['LKAS11', 'LFAHDA_MFC'])
def test_recorded_sonata_lateral_only_native_permission_matches_active_dash(icon):
  case = native_fixture.TestHyundaiClassicSccAol(methodName='runTest')
  case.setUp()
  try:
    ci, parser = controller()
    assert ci.CP.safetyConfigs[0].safetyParam == 0xC02
    assert not ci.CP.openpilotLongitudinalControl and ci.CP.pcmCruise
    case.reset(0xC02)
    for _ in range(6):
      case.feed(0xC02, main=True, source='BCM_PO_11')
    case.feed(0xC02, main=True, source='BCM_PO_11', pressed=True)
    permission = case.request(1)
    assert permission == 1 and case.safety.safety_config_valid()
    assert not case.safety.get_controls_allowed()
    actuators, packets = step(ci, parser, lateral=bool(permission & 1))
    for address, data, bus in packets:
      assert case.safety.safety_tx_hook(make_CANPacket(address, bus, data))
    signal = 'CF_Lkas_FcwOpt_USM' if icon == 'LKAS11' else 'LFA_Icon_State'
    assert parser.vl[icon][signal] == 2
    assert actuators.torque == 0.0 and actuators.accel == 0.0
    assert all(address not in (0x420, 0x421) for address, _, _ in packets)
  finally:
    case.tearDown()


@pytest.mark.parametrize('identity', [CAR.HYUNDAI_SONATA_HYBRID, CAR.HYUNDAI_SONATA, CAR.HYUNDAI_ELANTRA_2021, CAR.HYUNDAI_KONA_EV])
def test_qualified_classic_stock_icons_follow_actual_lateral_and_original_blink(identity):
  ci, parser = controller(identity)
  original = ci.CP.to_dict()
  for frame in range(101):
    step(ci, parser, lateral=frame == 0)
    expected = 2 if frame == 0 else 3 if frame < 100 else 0
    assert parser.vl['LKAS11']['CF_Lkas_FcwOpt_USM'] == (expected or 1)
    if frame % 5 == 0:
      assert parser.vl['LFAHDA_MFC']['LFA_Icon_State'] == expected
    assert parser.vl['LKAS11']['CR_Lkas_StrToqReq'] == 0
    assert parser.vl['LKAS11']['CF_Lkas_MsgCount'] == frame % 16
  assert ci.CP.to_dict() == original


@pytest.mark.parametrize('marked', [False, True])
@pytest.mark.parametrize('enabled,lateral,lkas,lfa', [(False, False, 1, 0), (True, False, 2, 2), (True, True, 2, 2)])
def test_icons_follow_lateral_when_marked_and_keep_unmarked_cruise_display(marked, enabled, lateral, lkas, lfa):
  ci, parser = controller(marked=marked)
  step(ci, parser, enabled=enabled, lateral=lateral)
  if marked and not lateral:
    lkas, lfa = 1, 0
  assert parser.vl['LKAS11']['CF_Lkas_FcwOpt_USM'] == lkas
  assert parser.vl['LFAHDA_MFC']['LFA_Icon_State'] == lfa


def test_unmarked_configuration_does_not_gain_lateral_only_icon_override():
  ci, parser = controller(marked=False)
  step(ci, parser, lateral=True)
  assert parser.vl['LKAS11']['CF_Lkas_FcwOpt_USM'] == 1
  assert parser.vl['LFAHDA_MFC']['LFA_Icon_State'] == 0


@pytest.mark.parametrize('identity', [CAR.HYUNDAI_SONATA_HYBRID, CAR.HYUNDAI_SONATA,
                                     CAR.HYUNDAI_ELANTRA_2021, CAR.HYUNDAI_KONA_EV])
def test_cruise_stays_engaged_after_lateral_icon_disengages(identity):
  ci, parser = controller(identity)
  original = ci.CP.to_dict()
  for frame in range(135):
    lateral = frame < 5 or frame >= 125
    _, packets = step(ci, parser, enabled=True, lateral=lateral)
    expected = 2 if lateral else 3 if frame < 104 else 0
    assert parser.vl['LKAS11']['CF_Lkas_FcwOpt_USM'] == (expected or 1)
    if frame % 5 == 0:
      assert parser.vl['LFAHDA_MFC']['LFA_Icon_State'] == expected
    assert parser.vl['LKAS11']['CR_Lkas_StrToqReq'] == 0
    assert not any(address in (0x420, 0x421) for address, _, _ in packets)
  assert ci.CP.to_dict() == original
