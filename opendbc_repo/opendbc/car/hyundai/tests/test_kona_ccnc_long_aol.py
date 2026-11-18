import os
from unittest.mock import Mock, patch

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_owner import config_index
from opendbc.car.hyundai.canfd_stock_aol import qualified_long
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.starpilot.car.hyundai.aol import independent_cruise_buttons_supported, native_accepts_cp, policy_for

CARS = (CAR.HYUNDAI_KONA_2ND_GEN, CAR.HYUNDAI_KONA_HEV_2ND_GEN)


def kona_params(car, alternate=False, offset=0, alpha=True):
  fp = gen_empty_fingerprint()
  fp[offset].update({0x1AA if alternate else 0x1CF: 16 if alternate else 8,
                    0xFA if car == CAR.HYUNDAI_KONA_HEV_2ND_GEN else 0x100: 32,
                    0x175: 24, 0xA0: 24, 0xEA: 24, 0x125: 16})
  fp[offset + 2].update({0x12A: 16, 0x1A0: 32, 0x161: 16, 0x162: 32})
  cp = CarInterface.get_params(car, fp, [], alpha, False, False)
  assert cp.openpilotLongitudinalControl == alpha and cp.pcmCruise != alpha
  assert cp.flags & HyundaiFlags.CCNC and cp.flags & HyundaiFlags.CANFD_CAMERA_SCC
  assert not cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG
  assert config_index(cp) == offset // 4
  assert cp.safetyConfigs[-1].safetyParam == (0x408 | (2 if car == CARS[1] else 0) | (32 if alternate else 0) | (4 if alpha else 0))
  return cp


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
def test_factory_ccnc_alpha_admits_only_matching_owner(car, alternate, offset):
  cp = kona_params(car, alternate, offset)
  original = cp.to_dict()
  assert qualified_long(cp) and independent_cruise_buttons_supported(cp)
  policy = policy_for(cp)
  assert policy.intent_supported and policy.runtime_supported and policy.explicit_latch
  assert policy.safety_param_addition == 0x800 and policy.alternative_experience_addition == 32
  assert cp.to_dict() == original
  word = int(cp.safetyConfigs[-1].safetyParam) | 0x800
  cp.safetyConfigs[-1].safetyParam = word
  cp.alternativeExperience = 32
  assert qualified_long(cp, marked_only=True)
  assert independent_cruise_buttons_supported(cp, marked_only=True)
  assert native_accepts_cp(cp, cp.safetyConfigs[-1].safetyModel.raw, word)
  assert not native_accepts_cp(cp, cp.safetyConfigs[-1].safetyModel.raw, word ^ 2)
  if offset:
    assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput
    assert cp.safetyConfigs[0].safetyParam == 0


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('button', ('lkas', 'mainCruise'))
def test_default_buttons_brake_cancel_and_permanent_fault(car, alternate, button):
  from openpilot.starpilot.aol.intent import AolSettings
  from openpilot.starpilot.car.hyundai.aol import create_intent
  cp = kona_params(car, alternate)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  intent = create_intent(cp, settings)
  assert intent.explicit_latch and intent.independent_cruise_buttons
  cs = structs.CarState.new_message(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  intent.update(cs, now_ns=1)
  cs.buttonEvents = [{'type': button, 'pressed': True}]
  intent.update(cs, now_ns=2)
  assert intent.output(cs) == (True, False, False)
  cs.buttonEvents = []
  cs.brakePressed = True
  intent.update(cs, now_ns=3)
  assert intent.output(cs) == (True, False, False)
  cs.brakePressed = False
  cs.buttonEvents = [{'type': 'cancel', 'pressed': True}]
  intent.update(cs, now_ns=4)
  assert intent.output(cs) == (True, False, False)
  cs.steerFaultPermanent = True
  intent.update(cs, now_ns=5)
  assert not intent.output(cs)[0]


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('enabled', (False, True))
def test_actual_card_marker_and_settings_preserve_startup_contract(car, alternate, offset, enabled):
  from opendbc.car.hyundai.radar_interface import RadarInterface
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car
  cp = kona_params(car, alternate, offset)
  base_word = int(cp.safetyConfigs[-1].safetyParam)
  required = CarInterface.startup_required(cp)
  with patch('opendbc.car.hyundai.interface.disable_ecu') as disable:
    CarInterface.init(cp, Mock(), Mock())
    original_calls = [(call.args[2:], call.kwargs) for call in disable.call_args_list]
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlwaysOnLateral', enabled, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    saved.put('DistanceButtonControl', 1, block=True)
    assert saved.get('LKASButtonControl') is None
    card = Car(CI=CarInterface(cp), RI=RadarInterface(cp))
    try:
      with structs.CarParams.from_bytes(saved.get('CarParams')) as published:
        assert published.openpilotLongitudinalControl and not published.pcmCruise
        assert published.safetyConfigs[-1].safetyParam == (base_word | (0x800 if enabled else 0))
        assert published.alternativeExperience == (32 if enabled else 0)
        assert qualified_long(published, marked_only=enabled)
        assert CarInterface.startup_required(published) == required
        with patch('opendbc.car.hyundai.interface.disable_ecu') as disable:
          CarInterface.init(published, Mock(), Mock())
          assert [(call.args[2:], call.kwargs) for call in disable.call_args_list] == original_calls
      assert card.aol_qualified == enabled
      if enabled:
        assert card.aol_card_intent.explicit_latch and card.aol_card_intent.independent_cruise_buttons
        assert card.aol_settings.default_physical_buttons
      assert saved.get('LKASButtonControl') is None
      assert saved.get('MainCruiseButtonControl') == 0
      assert saved.get('DistanceButtonControl') == 1
    finally:
      card.vehicle_startup.close()


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('mutation', ('identity', 'camera', 'ccnc', 'angle', 'experience', 'fuel', 'passive', 'dashcam', 'owner'))
def test_unmatched_contracts_remain_denied(car, mutation):
  cp = kona_params(car, offset=4)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  if mutation == 'identity':
    cp.carFingerprint = CAR.KIA_SPORTAGE_5TH_GEN
  elif mutation == 'camera':
    cp.flags = int(cp.flags) & ~int(HyundaiFlags.CANFD_CAMERA_SCC)
  elif mutation == 'ccnc':
    cp.flags = int(cp.flags) & ~int(HyundaiFlags.CCNC)
  elif mutation == 'angle':
    cp.steerControlType = structs.CarParams.SteerControlType.angle
  elif mutation == 'experience':
    cp.alternativeExperience = 33
  elif mutation == 'fuel':
    cp.safetyConfigs[-1].safetyParam ^= 2
  elif mutation == 'passive':
    cp.passive = True
  elif mutation == 'dashcam':
    cp.dashcamOnly = True
  else:
    cp.safetyConfigs[0].safetyParam = 1
  assert not qualified_long(cp, marked_only=True)
  assert not policy_for(cp).runtime_supported


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('alternate', (False, True))
def test_alpha_choice_preserves_identical_steering_packets(car, alternate, clock):
  from opendbc.car.hyundai.tests import test_canfd_lfa_status as contract
  contract.test_alpha_on_and_off_emit_identical_steering_for_identical_commands(car, alternate, clock)


@pytest.fixture
def clock(monkeypatch):
  from opendbc.car.hyundai.tests.test_canfd_lfa_status import clock as fixture
  return fixture.__wrapped__(monkeypatch)


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('alpha', (False, True))
def test_wheel_choices_keep_aol_with_either_longitudinal_owner(car, alternate, alpha):
  from openpilot.starpilot.ui.wheel_feature import WheelFeature

  class Parameters:
    def get(self, key, *args, **kwargs):
      return None

    def get_bool(self, key):
      return False

  class Owner:
    params = Parameters()

    def vehicle_params(self):
      return cp

    def configuration_vehicle(self):
      return False

    def configuration_longitudinal(self):
      return False

    def longitudinal_available(self):
      return cp.openpilotLongitudinalControl

  cp = kona_params(car, alternate, alpha=alpha)
  feature = WheelFeature(Owner())
  assert 'Toggle AOL' in feature.choices('LKASButtonControl')
  assert 'Toggle AOL' in feature.choices('MainCruiseButtonControl')
