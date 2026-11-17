from unittest.mock import Mock, patch
import os

import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_stock_aol import qualified_long
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.starpilot.car.hyundai.aol import policy_for, native_accepts_cp, create_intent
from openpilot.starpilot.aol.intent import AolSettings, AOL_TOGGLE


def camera_params(offset=0, alpha=False):
  fp = gen_empty_fingerprint()
  fp[offset].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1BA: 24, 0x1E5: 16})
  fp[offset + 2].update({0x12A: 16, 0x1A0: 32})
  fp[offset + 1].update({0x110: 32, 0x100: 24})
  return CarInterface.get_params(CAR.KIA_EV6, fp, [], alpha, False, False)


@pytest.mark.parametrize('offset', (0, 4))
def test_factory_hda1_alpha_binds_existing_camera_native_profile(offset):
  cp = camera_params(offset, True)
  assert cp.alphaLongitudinalAvailable and cp.openpilotLongitudinalControl and not cp.pcmCruise
  assert cp.flags & HyundaiFlags.CANFD_CAMERA_SCC
  assert not cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG
  assert cp.safetyConfigs[-1].safetyParam == 0x0D
  assert not CarInterface.startup_required(cp)
  with patch('opendbc.car.hyundai.interface.disable_ecu') as disable:
    CarInterface.init(cp, Mock(), Mock())
  disable.assert_not_called()
  assert qualified_long(cp), 'EV6_HDA1_LONG_AOL_PROFILE_MISSING'
  policy = policy_for(cp)
  assert policy.runtime_supported and policy.explicit_latch
  assert policy.safety_param_addition == 0x800 and policy.alternative_experience_addition == 32
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  assert cp.safetyConfigs[-1].safetyParam == 0x80D
  assert qualified_long(cp, marked_only=True)
  assert native_accepts_cp(cp, int(structs.CarParams.SafetyModel.hyundaiCanfd), 0x80D)
  assert not native_accepts_cp(cp, int(structs.CarParams.SafetyModel.hyundaiCanfd), 0x815)
  owner = create_intent(cp, AolSettings(True, 0., AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0)))
  from openpilot.starpilot.car.hyundai.ev6_intent import Ev6CardIntent
  assert not isinstance(owner, Ev6CardIntent)
  assert owner.independent_cruise_buttons


@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('enabled', (False, True))
def test_actual_card_publishes_hda1_long_opt_in_without_hda2_takeover(offset, enabled):
  from opendbc.car.hyundai.radar_interface import RadarInterface
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car
  cp = camera_params(offset, True)
  assert qualified_long(cp), 'EV6_HDA1_CARD_AOL_PROFILE_MISSING'
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlwaysOnLateral', enabled, block=True)
    saved.put('LKASButtonControl', 9, block=True)
    ci = CarInterface(cp)
    card = Car(CI=ci, RI=RadarInterface(cp))
    try:
      with structs.CarParams.from_bytes(saved.get('CarParams')) as published:
        assert published.openpilotLongitudinalControl and not published.pcmCruise
        assert published.safetyConfigs[-1].safetyParam == (0x80D if enabled else 0x0D)
        assert published.alternativeExperience == (32 if enabled else 0)
        assert not CarInterface.startup_required(published)
        assert qualified_long(published, marked_only=enabled)
    finally:
      del card


@pytest.mark.parametrize('offset', (0, 4))
def test_actual_controller_and_native_reuse_exact_camera_contract(offset):
  from opendbc.car.hyundai.tests import test_ioniq6_camera_long as contract
  with patch.object(contract, 'camera_params', camera_params):
    contract.test_camera_long_actual_controller_packets_use_existing_native_contract(offset)


@pytest.mark.parametrize('incidental', ('cancel', 'mainCruise'))
def test_hda1_long_lkas_latch_uses_shared_independent_button_contract(incidental):
  from openpilot.starpilot.aol.tests import test_hkg_independent_cancel_contract as contract
  cp = camera_params(alpha=True)
  assert qualified_long(cp), 'EV6_HDA1_CANCEL_PROFILE_MISSING'
  with patch.object(contract, 'params', lambda *args, **kwargs: cp):
    contract.test_stock_lkas_owned_aol_survives_unassigned_cruise_gesture(CAR.KIA_EV6, False, incidental)


@pytest.mark.parametrize('mutation', ('radar', 'alternate_buttons', 'angle', 'experience', 'wrong_word', 'aux_word'))
def test_hda1_long_never_borrows_other_native_profiles(mutation):
  cp = camera_params(4, True)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  if mutation == 'radar':
    cp.flags = int(cp.flags) & ~int(HyundaiFlags.CANFD_CAMERA_SCC)
    cp.flags |= int(HyundaiFlags.CANFD_RADAR_SCC)
    cp.safetyConfigs[-1].safetyParam = 0x805
  elif mutation == 'alternate_buttons':
    cp.flags |= int(HyundaiFlags.CANFD_ALT_BUTTONS)
    cp.safetyConfigs[-1].safetyParam = 0x82D
  elif mutation == 'angle':
    cp.steerControlType = structs.CarParams.SteerControlType.angle
  elif mutation == 'experience':
    cp.alternativeExperience = 33
  elif mutation == 'wrong_word':
    cp.safetyConfigs[-1].safetyParam = 0x815
  else:
    cp.safetyConfigs[0].safetyParam = 1
  assert not qualified_long(cp)
  assert not policy_for(cp).runtime_supported
