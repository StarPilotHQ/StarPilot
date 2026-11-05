"""Saved generic runtime startup does not replace live driving authority."""

from pathlib import Path
import json
from tempfile import TemporaryDirectory

from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences
from openpilot.starpilot.feature_runtime import enabled, requested
from openpilot.starpilot.longitudinal.profile_document import default_personality_profiles, profile_document
from openpilot.starpilot.speed_limits.vision_gate import diagnostic_choice_enabled


def long_cp():
  from opendbc.car.toyota.interface import CarInterface
  from opendbc.car.toyota.values import CAR
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
  assert cp.openpilotLongitudinalControl and cp.pcmCruise
  return cp


def stock_cp():
  from opendbc.car.honda.interface import CarInterface
  from opendbc.car.honda.values import CAR
  cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC_BOSCH)
  assert not cp.openpilotLongitudinalControl
  return cp


class Files:
  def __init__(self, root: Path):
    self.root = root

  def get_param_path(self, key: str) -> str:
    return str(self.root / key)

  def get_default_value(self, key: str):
    assert key in ('CustomPersonalities', 'LongitudinalPersonalityProfiles')
    return b'0' if key == 'CustomPersonalities' else {}


def test_readable_absence_starts_factory_cem_only_on_native_long_cp():
  with TemporaryDirectory() as temporary:
    params = Files(Path(temporary))
    assert requested(params, 'conditional')
    assert enabled(params, long_cp(), 'conditional', {})
    assert not enabled(params, stock_cp(), 'conditional', {})
    for name in ('curve', 'slc', 'vision', 'profile'):
      assert not enabled(params, object(), name, {})
    (params.root / 'SafeMode').write_bytes(b'1')
    assert not requested(params, 'conditional')
    (params.root / 'SafeMode').write_bytes(b'0')
    (params.root / 'ConditionalModeConfig').write_bytes(b'{broken')
    (params.root / 'CurveSpeedController').write_bytes(b'true')
    assert not requested(params, 'conditional')
    assert not requested(params, 'curve')
    (params.root / 'ConditionalModeConfig').unlink()
    (params.root / 'ConditionalModeConfig').mkdir()
    assert not requested(params, 'conditional')  # Read error is never factory absence.


def test_native_long_cp_and_saved_document_start_owner_without_granting_stock_control():
  with TemporaryDirectory() as temporary:
    params = Files(Path(temporary))
    (params.root / 'ConditionalModeConfig').write_bytes(encode_preferences(SavedPreferences(mode=ModeChoice.STOCK)))
    (params.root / 'CurveSpeedController').write_bytes(b'1')
    for name in ('conditional', 'curve'):
      assert enabled(params, long_cp(), name, {})
      assert not enabled(params, stock_cp(), name, {})
    (params.root / 'SafeMode').write_bytes(b'1')
    assert not enabled(params, long_cp(), 'conditional', {})


def test_global_braking_choice_starts_only_qualified_profile_owner_without_custom_master():
  with TemporaryDirectory() as temporary:
    params = Files(Path(temporary))
    source = params.root / 'LongitudinalPersonalityProfiles'
    assert requested(params, 'profile')
    assert enabled(params, long_cp(), 'profile', {})
    assert not enabled(params, stock_cp(), 'profile', {})
    for response in ('eco', 'sport'):
      source.write_text(json.dumps(profile_document(default_personality_profiles(False), enabled=False,
                                                    global_braking_response=response)))
      assert requested(params, 'profile')
      assert enabled(params, long_cp(), 'profile', {})
      assert not enabled(params, stock_cp(), 'profile', {})
    source.write_text(json.dumps(profile_document(default_personality_profiles(False), enabled=False,
                                                  selected_deceleration_profile='dom_default')))
    assert not requested(params, 'profile')
    bad = profile_document(default_personality_profiles(False), enabled=False)
    bad['globalBrakingResponse'] = 'unsupported'
    source.write_text(json.dumps(bad))
    assert not requested(params, 'profile')
    source.write_text('{invalid')
    assert not requested(params, 'profile')
    (params.root / 'SafeMode').write_bytes(b'corrupt')
    assert not enabled(params, long_cp(), 'conditional', {})


def test_vision_control_needs_saved_master_and_vision_source_on_native_long_cp():
  with TemporaryDirectory() as temporary:
    params = Files(Path(temporary))
    (params.root / 'SpeedLimitController').write_bytes(b'1')
    (params.root / 'SLCPriority1').write_bytes(b'Dashboard')
    (params.root / 'SLCPriority2').write_bytes(b'Vision')
    from openpilot.starpilot.feature_runtime import vision_control_enabled
    assert requested(params, 'vision')
    assert enabled(params, long_cp(), 'vision', {})
    assert enabled(params, stock_cp(), 'vision', {})  # Stock owner may display selected Vision.
    assert vision_control_enabled(params, long_cp())
    assert not vision_control_enabled(params, stock_cp())
    (params.root / 'SpeedLimitController').write_bytes(b'0')
    assert enabled(params, long_cp(), 'vision', {})  # Default-on display still needs its selected Vision producer.
    assert not vision_control_enabled(params, long_cp())


def test_explicit_replay_flags_remain_available_for_existing_replay_fixtures():
  with TemporaryDirectory() as temporary:
    params = Files(Path(temporary))
    assert enabled(params, None, 'conditional', {'CONDITIONAL_MODE_REPLAY_RUNTIME': '1'})
    assert enabled(params, None, 'curve', {'CURVE_REPLAY_RUNTIME': '1'})
    assert enabled(params, None, 'slc', {'SLC_REPLAY_RUNTIME': '1'})
    assert enabled(params, None, 'vision', {'SLC_REPLAY_RUNTIME': '1', 'SLC_VISION_DEVELOPMENT': '1'})
    assert not enabled(params, None, 'vision', {'SLC_VISION_DEVELOPMENT': '1'})


def test_saved_vision_choice_is_offered_for_native_display_vehicle_or_explicit_replay():
  assert diagnostic_choice_enabled({}, long_cp())
  assert diagnostic_choice_enabled({}, stock_cp())  # Source visibility grants no speed authority.
  assert not diagnostic_choice_enabled({})
  assert diagnostic_choice_enabled({'SLC_REPLAY_RUNTIME': '1', 'SLC_VISION_DEVELOPMENT': '1'})


def test_gm_saved_profiles_require_existing_longitudinal_owner():
  from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
  from opendbc.car.gm.tests.test_ascm_intercept import params as ascm_params
  from opendbc.car.gm.values import CAR, PEDAL_BOLT_CAR, GMSafetyFlags
  from opendbc.car.gm.profiles import profiles_supported

  from openpilot.common.params import Params

  with TemporaryDirectory() as temporary:
    params = Params(temporary)
    root = Path(params.get_param_path('CustomPersonalities')).parent
    (root / 'CustomPersonalities').write_bytes(b'1')
    (root / 'LongitudinalPersonalityProfiles').write_text(json.dumps(
      profile_document(default_personality_profiles(True), enabled=True)))
    for alpha in (False, True):
      for candidate in PEDAL_BOLT_CAR:
        cp = pedal_params(candidate, setting=True, pedal=True, alpha_long=alpha)
        assert enabled(params, cp, 'profile', {})
        assert enabled(params, cp, 'conditional', {})
        for field in ('passive', 'dashcamOnly', 'notCar'):
          setattr(cp, field, True)
          assert not enabled(params, cp, 'profile', {})
          setattr(cp, field, False)
        cp.openpilotLongitudinalControl = False
        assert not enabled(params, cp, 'profile', {})
        assert enabled(params, pedal_params(candidate, setting=False, pedal=True, alpha_long=alpha), 'profile', {})
      assert enabled(params, ascm_params(CAR.CHEVROLET_BOLT_EUV, alpha=alpha), 'profile', {}) == alpha
      assert enabled(params, ascm_params(CAR.CHEVROLET_BOLT_ACC_2022_2023, alpha=alpha), 'profile', {}) == alpha
      cp = ascm_params(CAR.CHEVROLET_VOLT_ASCM, sascm=True, alpha=alpha)
      assert enabled(params, cp, 'profile', {}) == alpha
      assert not enabled(params, ascm_params(CAR.CHEVROLET_VOLT_ASCM, sascm=False, alpha=alpha), 'profile', {})
    cp = ascm_params(CAR.CHEVROLET_VOLT, radar=True)
    assert profiles_supported(cp)
    assert enabled(params, cp, 'profile', {})
    cp.safetyConfigs[0].safetyParam |= int(GMSafetyFlags.NO_ACC)
    assert not profiles_supported(cp)
    assert not enabled(params, ascm_params(CAR.CHEVROLET_VOLT_CAMERA), 'profile', {})
    assert not enabled(params, ascm_params(CAR.CHEVROLET_VOLT_2019), 'profile', {})
    (root / 'CustomPersonalities').write_bytes(b'0')
    (root / 'LongitudinalPersonalityProfiles').write_text(json.dumps(
      profile_document(default_personality_profiles(True), enabled=True, selected_deceleration_profile='dom_default')))
    assert not enabled(params, ascm_params(CAR.CHEVROLET_VOLT, radar=True), 'profile', {})
    (root / 'CustomPersonalities').write_bytes(b'1')
    (root / 'LongitudinalPersonalityProfiles').write_text('{broken')
    assert not enabled(params, ascm_params(CAR.CHEVROLET_VOLT, radar=True), 'profile', {})


def test_honda_slc_controls_only_with_openpilot_long():
  from types import SimpleNamespace
  from openpilot.starpilot.feature_runtime import slc_runtime_settings, slc_transport_capable, vision_control_enabled

  def honda(**overrides):
    return SimpleNamespace(**{'brand': 'honda', 'carFingerprint': 'HONDA_CIVIC_2022', 'openpilotLongitudinalControl': True,
                              'pcmCruise': False, 'passive': False, 'dashcamOnly': False, 'notCar': False, **overrides})

  with TemporaryDirectory() as temporary:
    params = Files(Path(temporary))
    (params.root / 'SpeedLimitController').write_bytes(b'1')
    (params.root / 'SLCPriority1').write_bytes(b'Dashboard')
    (params.root / 'SLCPriority2').write_bytes(b'Map Data')
    assert slc_transport_capable(honda(), {})
    assert enabled(params, honda(), 'slc', {})
    assert slc_runtime_settings(params, honda(), {}).enabled
    assert not enabled(params, honda(), 'vision', {})
    assert not vision_control_enabled(params, honda())
    stock = honda(openpilotLongitudinalControl=False, pcmCruise=True)
    assert enabled(params, stock, 'slc', {})
    assert not slc_runtime_settings(params, stock, {}).enabled
    assert not slc_transport_capable(honda(dashcamOnly=True), {})
    assert not enabled(params, honda(dashcamOnly=True), 'slc', {})


def test_vision_stop_request_is_independent_of_speed_and_preserves_native_authority():
  from openpilot.system.manager.process_config import vision_slc_development
  from unittest.mock import patch
  with TemporaryDirectory() as temporary, patch.dict('os.environ', {}, clear=True):
    params = Files(Path(temporary))
    for key, value in {'SpeedLimitController': b'0', 'ShowSpeedLimits': b'0',
                       'SLCPriority1': b'Dashboard', 'SLCPriority2': b'Map Data', 'ForceStops': b'1'}.items():
      (params.root / key).write_bytes(value)
    assert not requested(params, 'slc')
    assert not requested(params, 'vision')
    assert requested(params, 'vision_stop')
    assert enabled(params, long_cp(), 'vision_stop', {})
    assert vision_slc_development(True, params, long_cp())
    assert not vision_slc_development(False, params, long_cp())
    for cp in (stock_cp(), None, object()):
      assert not enabled(params, cp, 'vision_stop', {})
      assert not vision_slc_development(True, params, cp)
    (params.root / 'SpeedLimitController').write_bytes(b'broken')
    assert requested(params, 'vision_stop')
    assert vision_slc_development(True, params, long_cp())
    for key, value in (('ForceStops', b'0'), ('ForceStops', b'true'), ('ForceStops', b'1'),
                       ('SafeMode', b'1'), ('SafeMode', b'corrupt')):
      (params.root / key).write_bytes(value)
      if (key, value) != ('ForceStops', b'1'):
        assert not enabled(params, long_cp(), 'vision_stop', {})
    (params.root / 'SafeMode').write_bytes(b'0')
    assert enabled(params, long_cp(), 'vision_stop', {})
    (params.root / 'ForceStops').unlink()
    (params.root / 'ForceStops').mkdir()
    assert not requested(params, 'vision_stop')
