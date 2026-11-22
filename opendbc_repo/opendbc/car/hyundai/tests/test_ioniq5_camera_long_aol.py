import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_owner import config_index
from opendbc.car.hyundai.canfd_stock_aol import qualified_long
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.starpilot.car.hyundai.aol import independent_cruise_buttons_supported, native_accepts_cp, policy_for


CAMERA_LONG_CARS = (CAR.HYUNDAI_IONIQ_5, CAR.KIA_NIRO_EV_2ND_GEN)


def camera_long(offset, alpha=True, *, identity=CAR.HYUNDAI_IONIQ_5):
  fp = gen_empty_fingerprint()
  fp[offset].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1BA: 24, 0x1E5: 16})
  fp[offset + 2].update({0x12A: 16, 0x1A0: 32})
  fp[offset + 1].update({0x110: 32, 0x100: 24})
  cp = CarInterface.get_params(identity, fp, [], alpha, False, False)
  assert cp.openpilotLongitudinalControl and not cp.pcmCruise
  assert cp.flags & HyundaiFlags.CANFD_CAMERA_SCC
  assert not cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG
  assert config_index(cp) == offset // 4
  assert cp.safetyConfigs[offset // 4].safetyParam == 0x0D
  return cp


@pytest.mark.parametrize('offset', [0, 4])
@pytest.mark.parametrize('identity', CAMERA_LONG_CARS)
def test_factory_camera_long_uses_existing_paired_axis_profile(identity, offset):
  cp = camera_long(offset, identity=identity)
  before = cp.to_dict()
  assert qualified_long(cp)
  assert independent_cruise_buttons_supported(cp)
  policy = policy_for(cp)
  assert policy.intent_supported and policy.runtime_supported and policy.explicit_latch
  assert policy.safety_param_addition == 0x800 and policy.alternative_experience_addition == 32
  assert cp.to_dict() == before
  owner = config_index(cp)
  cp.safetyConfigs[owner].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  assert qualified_long(cp, marked_only=True)
  assert independent_cruise_buttons_supported(cp, marked_only=True)
  assert native_accepts_cp(cp, cp.safetyConfigs[owner].safetyModel.raw, 0x80D)
  if owner:
    assert cp.safetyConfigs[0].safetyParam == 0
    assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput


@pytest.mark.parametrize('offset', [0, 4])
@pytest.mark.parametrize('mutation', ['alternate_buttons', 'unknown_word', 'wrong_experience', 'passive', 'angle', 'hda2'])
@pytest.mark.parametrize('identity', CAMERA_LONG_CARS)
def test_camera_long_extension_keeps_unrecognized_contracts_closed(identity, offset, mutation):
  cp = camera_long(offset, identity=identity)
  owner = config_index(cp)
  cp.safetyConfigs[owner].safetyParam = 0x80D
  cp.alternativeExperience = 32
  if mutation == 'alternate_buttons':
    cp.flags |= int(HyundaiFlags.CANFD_ALT_BUTTONS)
    cp.safetyConfigs[owner].safetyParam = 0x82D
  elif mutation == 'unknown_word':
    cp.safetyConfigs[owner].safetyParam = 0x80C
  elif mutation == 'wrong_experience':
    cp.alternativeExperience = 0
  elif mutation == 'passive':
    cp.passive = True
  elif mutation == 'angle':
    cp.flags |= int(HyundaiFlags.CANFD_ANGLE_STEERING)
  elif mutation == 'hda2':
    cp.flags = (cp.flags & ~int(HyundaiFlags.CANFD_CAMERA_SCC)) | int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    cp.safetyConfigs[owner].safetyParam = 0x815
  assert qualified_long(cp, marked_only=True) == (mutation == 'hda2' and identity == CAR.HYUNDAI_IONIQ_5)


@pytest.mark.parametrize('offset', [0, 4])
@pytest.mark.parametrize('button', ['lkas', 'mainCruise'])
@pytest.mark.parametrize('identity', CAMERA_LONG_CARS)
def test_default_physical_button_arms_and_brake_cancel_keep_intent(identity, offset, button):
  from openpilot.starpilot.aol.intent import AolSettings
  from openpilot.starpilot.car.hyundai.aol import create_intent

  cp = camera_long(offset, identity=identity)
  cp.safetyConfigs[config_index(cp)].safetyParam = 0x80D
  cp.alternativeExperience = 32
  settings = AolSettings(enabled=True, pause_brake_mps=0.0, lkas_action=0, main_action=0,
                         distance_actions=(0, 0, 0), cancel_actions=(0, 0, 0))
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


@pytest.mark.parametrize('identity', [CAR.HYUNDAI_KONA_EV_2ND_GEN, CAR.HYUNDAI_IONIQ_5_N, CAR.KIA_NIRO_HEV_2ND_GEN])
def test_camera_extension_does_not_admit_unqualified_identities(identity):
  cp = camera_long(0)
  cp.carFingerprint = identity
  assert not qualified_long(cp)
  cp.safetyConfigs[0].safetyParam = 0x80D
  cp.alternativeExperience = 32
  assert not qualified_long(cp, marked_only=True)


@pytest.mark.parametrize('offset', [0, 4])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('identity', CAMERA_LONG_CARS)
def test_actual_card_marks_camera_owner_with_recorded_default_buttons(identity, offset, enabled):
  import os
  from unittest.mock import patch
  from opendbc.car.hyundai.radar_interface import RadarInterface
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner

  cp = camera_long(offset, identity=identity)
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlwaysOnLateral', enabled, block=True)
    saved.put('PauseAOLOnBrake', 0.0, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    saved.put('DistanceButtonControl', 1, block=True)
    assert saved.get('LKASButtonControl') is None
    ci = CarInterface(cp)
    card = Car(CI=ci, RI=RadarInterface(cp))
    try:
      with structs.CarParams.from_bytes(saved.get('CarParams')) as published:
        assert published.openpilotLongitudinalControl and not published.pcmCruise
        assert published.safetyConfigs[-1].safetyParam == (0x80D if enabled else 0x0D)
        assert published.alternativeExperience == (32 if enabled else 0)
        assert qualified_long(published, marked_only=enabled)
        assert config_index(published) == offset // 4
        assert not CarInterface.startup_required(published)
        features = FeatureSettingsOwner(saved, lambda _: True, vehicle_fingerprint=lambda: str(published.carFingerprint),
                                       vehicle_params=lambda: published)
        page = features.snapshot('wheel', parked=True, system_long=True, lateral_context=True, metric=False)
        row = next(row for row in page.rows if row.key == 'wheel:LKASButtonControl')
        assert 'Toggle AOL' in row.choices
      assert card.aol_qualified == enabled
      if enabled:
        assert card.aol_card_intent.explicit_latch and card.aol_card_intent.independent_cruise_buttons
        assert card.aol_settings.default_physical_buttons
        assert card.aol_settings.lkas_action == card.aol_settings.main_action == 0
      assert saved.get('LKASButtonControl') is None
      assert saved.get('MainCruiseButtonControl') == 0
      assert saved.get('DistanceButtonControl') == 1
    finally:
      del card
