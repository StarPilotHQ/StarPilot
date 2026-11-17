from unittest.mock import patch

import pytest

from opendbc.can import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.classic_long_aol import aol_word
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC
from opendbc.safety.tests import test_hyundai_classic_long_aol as native_fixture
from opendbc.safety.tests.libsafety.libsafety_py import make_CANPacket


def controller(*, marked=True, experience=32, identity=CAR.HYUNDAI_ELANTRA_2021, lda=True):
  fp = gen_empty_fingerprint()
  if lda:
    fp[0][0x391] = 8
  fp[0][0x38d] = 8
  fp[2][0x485] = 4
  cp = CarInterface.get_params(identity, fp, [], True, False, False)
  if marked:
    cp.safetyConfigs[0].safetyParam = aol_word(cp)
  cp.alternativeExperience = experience
  if identity == CAR.HYUNDAI_ELANTRA_2021 and lda:
    assert cp.flags == 16843136  # Recorded Elantra alpha-long profile, including FCA and LDA.
  ci = CarInterface(cp)
  ci.update([])
  return ci


def step(ci, cc, parser=None):
  stamp = 10_000_000_000 + ci.CC.frame * 10_000_000
  actuators, packets = ci.CC.update(cc.as_reader(), ci.CS, stamp)
  if parser is not None:
    parser.update((stamp, packets))
  return actuators, packets


def parser():
  return CANParser(DBC[CAR.HYUNDAI_ELANTRA_2021][Bus.pt], [('LKAS11', 0), ('LFAHDA_MFC', 0)], 0)


def test_elantra_long_lateral_only_dash_matches_native_permission_after_cancel():
  case = native_fixture.TestHyundaiClassicLongAol(methodName='runTest')
  case.setUp()
  try:
    ci, can = controller(), parser()
    assert ci.CP.safetyConfigs[0].safetyParam == 0xC04
    case.reset(0xC04)
    for _ in range(6):
      case.feed(0xC04)
    case.feed(0xC04, button=2)
    case.feed(0xC04)
    assert case.request(3) == 3
    case.feed(0xC04, button=4)
    permission = case.request(3)
    assert permission == 1 and not case.safety.get_controls_allowed()
    cc = structs.CarControl(enabled=False, latActive=bool(permission & 1))
    actuators, packets = step(ci, cc, can)
    assert can.vl['LKAS11']['CF_Lkas_FcwOpt_USM'] == 2
    assert can.vl['LFAHDA_MFC']['LFA_Icon_State'] == 2
    assert actuators.torque == 0 and actuators.accel == 0
    for address, data, bus in packets:
      assert case.safety.safety_tx_hook(make_CANPacket(address, bus, data))
  finally:
    case.tearDown()


@pytest.mark.parametrize('enabled,lateral', [(False, False), (True, False), (True, True), (False, True)])
@pytest.mark.parametrize('alert', [structs.CarControl.HUDControl.VisualAlert.none, structs.CarControl.HUDControl.VisualAlert.steerRequired,
                                   structs.CarControl.HUDControl.VisualAlert.fcw])
def test_only_icon_fields_and_lkas_checksum_change(enabled, lateral, alert):
  current, original = controller(), controller()
  original.CC.classic_dash_icons = None
  new_can, old_can = parser(), parser()
  cc = structs.CarControl(enabled=enabled, latActive=lateral, hudControl={'visualAlert': alert, 'leadVisible': True})
  for _ in range(10):
    new_actuators, new_packets = step(current, cc, new_can)
    with patch('opendbc.car.hyundai.carcontroller.classic_aol_dash_enabled', return_value=False):
      old_actuators, old_packets = step(original, cc, old_can)
    assert new_actuators.to_dict() == old_actuators.to_dict()
    assert len(new_packets) == len(old_packets)
    for new, old in zip(new_packets, old_packets, strict=True):
      assert new[0] == old[0] and new[2] == old[2]
      if new[0] not in (0x340, 0x485):
        assert new == old  # Includes all SCC/FCA acceleration, lead, and AEB status packets.
    for message, excluded in [('LKAS11', {'CF_Lkas_FcwOpt_USM', 'CF_Lkas_Chksum'}), ('LFAHDA_MFC', {'LFA_Icon_State'})]:
      assert {k: v for k, v in new_can.vl[message].items() if k not in excluded} == {
        k: v for k, v in old_can.vl[message].items() if k not in excluded}


def test_disengagement_blink_and_inactive_icon_restore():
  ci, can = controller(), parser()
  for frame in range(101):
    step(ci, structs.CarControl(latActive=frame == 0), can)
    icon = 2 if frame == 0 else 3 if frame < 100 else 0
    assert can.vl['LKAS11']['CF_Lkas_FcwOpt_USM'] == (icon or 1)
    if frame % 5 == 0:
      assert can.vl['LFAHDA_MFC']['LFA_Icon_State'] == icon


@pytest.mark.parametrize('marked,experience', [(False, 0), (False, 32), (True, 0), (True, 33)])
def test_unqualified_elantra_does_not_gain_lateral_only_icon_override(marked, experience):
  ci, can = controller(marked=marked, experience=experience), parser()
  step(ci, structs.CarControl(latActive=True), can)
  assert can.vl['LKAS11']['CF_Lkas_FcwOpt_USM'] == 1
  assert can.vl['LFAHDA_MFC']['LFA_Icon_State'] == 0
