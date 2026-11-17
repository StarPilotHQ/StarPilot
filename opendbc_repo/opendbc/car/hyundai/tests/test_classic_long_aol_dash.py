import pytest

from opendbc.can import CANParser
from opendbc.car import Bus, structs
from opendbc.car.hyundai.classic_long_aol import AOL_EXPERIENCE, CLASSIC_LONG_IDS, aol_word, qualified
from opendbc.car.hyundai.carcontroller import classic_aol_dash_enabled
from opendbc.car.hyundai.tests.test_elantra_long_aol_dash import controller, step
from opendbc.car.hyundai.values import CAR, DBC

IDENTITIES = [CAR.HYUNDAI_SONATA, CAR.HYUNDAI_SONATA_HYBRID, CAR.KIA_NIRO_EV, CAR.HYUNDAI_KONA_EV]


def parser(identity):
  return CANParser(DBC[identity][Bus.pt], [('LKAS11', 0), ('LFAHDA_MFC', 0)], 0)


@pytest.mark.parametrize('identity', IDENTITIES)
@pytest.mark.parametrize('lda', [False, True])
@pytest.mark.parametrize('late', [False, True])
def test_aol_icon_is_visible_before_any_full_engagement(identity, lda, late):
  ci = controller(identity=identity, lda=lda, marked=not late, experience=0 if late else AOL_EXPERIENCE)
  can = parser(identity)
  if late:
    assert ci.CC.classic_dash_icons is None
    step(ci, structs.CarControl(), can)
    ci.CP.safetyConfigs[0].safetyParam = aol_word(ci.CP)
    ci.CP.alternativeExperience = AOL_EXPERIENCE
    ci.CC.frame = 5
  assert qualified(ci.CP, marked_only=True)
  original = ci.CP.to_dict()
  actuators, _ = step(ci, structs.CarControl(enabled=False, latActive=True), can)
  assert can.vl['LKAS11']['CF_Lkas_FcwOpt_USM'] == 2
  assert can.vl['LFAHDA_MFC']['LFA_Icon_State'] == 2
  assert actuators.torque == 0 and actuators.accel == 0
  assert ci.CP.to_dict() == original


@pytest.mark.parametrize('identity', sorted(CLASSIC_LONG_IDS, key=str))
@pytest.mark.parametrize('lda', [False, True])
def test_exact_classic_long_family_uses_the_shared_display_qualification(identity, lda):
  ci = controller(identity=identity, lda=lda, marked=False, experience=0)
  if not qualified(ci.CP):
    assert not classic_aol_dash_enabled(ci.CP)
    return
  ci.CP.safetyConfigs[0].safetyParam = aol_word(ci.CP)
  ci.CP.alternativeExperience = AOL_EXPERIENCE
  assert classic_aol_dash_enabled(ci.CP)


@pytest.mark.parametrize('identity', IDENTITIES)
@pytest.mark.parametrize('lda', [False, True])
@pytest.mark.parametrize('enabled,lateral', [(False, False), (True, False), (True, True), (False, True)])
@pytest.mark.parametrize('alert', [structs.CarControl.HUDControl.VisualAlert.none,
                                  structs.CarControl.HUDControl.VisualAlert.steerRequired,
                                  structs.CarControl.HUDControl.VisualAlert.fcw])
def test_other_packet_fields_and_all_actuator_packets_are_preserved(identity, lda, enabled, lateral, alert):
  from functools import partial
  from unittest.mock import patch
  from opendbc.car.hyundai.tests import test_elantra_long_aol_dash as original
  with patch.object(original, 'controller', partial(controller, identity=identity, lda=lda)), \
       patch.object(original, 'parser', lambda: parser(identity)):
    original.test_only_icon_fields_and_lkas_checksum_change(enabled, lateral, alert)


@pytest.mark.parametrize('identity', IDENTITIES)
@pytest.mark.parametrize('marked,experience', [(False, 0), (False, 32), (True, 0), (True, 33)])
def test_unqualified_long_profiles_do_not_claim_active_aol_icon(identity, marked, experience):
  ci, can = controller(identity=identity, marked=marked, experience=experience), parser(identity)
  step(ci, structs.CarControl(latActive=True), can)
  assert can.vl['LKAS11']['CF_Lkas_FcwOpt_USM'] == 1
  assert can.vl['LFAHDA_MFC']['LFA_Icon_State'] == 0


@pytest.mark.parametrize('identity', IDENTITIES)
@pytest.mark.parametrize('lda', [False, True])
def test_long_profile_display_packets_are_accepted_with_lateral_only_native_permission(identity, lda):
  from opendbc.safety.tests import test_hyundai_classic_long_aol as fixture
  from opendbc.safety.tests.libsafety.libsafety_py import make_CANPacket
  case = fixture.TestHyundaiClassicLongAol(methodName='runTest')
  case.setUp()
  try:
    ci, can = controller(identity=identity, lda=lda), parser(identity)
    word = aol_word(ci.CP)
    case.reset(word)
    for _ in range(6):
      case.feed(word)
    case.feed(word, button=2)
    case.feed(word)
    assert case.request(3) == 3
    case.feed(word, button=4)
    permission = case.request(3)
    assert permission == 1 and not case.safety.get_controls_allowed()
    _, packets = step(ci, structs.CarControl(enabled=False, latActive=True), can)
    assert can.vl['LFAHDA_MFC']['LFA_Icon_State'] == 2
    for address, data, bus in packets:
      assert case.safety.safety_tx_hook(make_CANPacket(address, bus, data))
  finally:
    case.tearDown()
