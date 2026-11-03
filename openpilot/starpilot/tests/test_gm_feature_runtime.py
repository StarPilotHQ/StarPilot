"""Finalized GM driving owners start optional hosts without granting authority."""
import json
from pathlib import Path
import unittest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.gm.feature_capabilities import display_supported, longitudinal_supported
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import CAR
from opendbc.car.gm.tests.test_camera_acc_pedal import CAMERA_IDS, fingerprint as camera_pedal_fingerprint
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.speed_limits import planner_host as slc_host
from openpilot.starpilot.feature_runtime import enabled, slc_runtime_settings, vision_control_enabled
from openpilot.starpilot.longitudinal.profile_document import default_personality_profiles, profile_document
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


def configured(params, identity, *, alpha=False, release=False, removed=False, alternate=False, pedal=False, disable=False):
  params.put_bool('GMPedalLongitudinal', pedal, block=True)
  fp = gen_empty_fingerprint()
  if not alternate:
    fp[0][0xBE] = 6
  if identity in (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_SUBURBAN):
    fp[1][0x460] = 8
  if identity in (CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_2019):
    fp[0][0x2FF] = 8
  if identity == CAR.CHEVROLET_VOLT_CC:
    fp[0].update({0xBE: 6, 0x3D1: 8, 0xC9: 8, 0x1E1: 7, 0x1F5: 8, 0x34A: 5, 0x1C4: 8, 0xBD: 7})
  elif identity == CAR.CHEVROLET_VOLT_CAMERA:
    if removed:
      fp[0].update({0x184: 8, 0x34A: 5, 0x1C4: 8, 0xC9: 8, 0x1E1: 7, 0xF1 if alternate else 0xBE: 6})
    else:
      fp[2][0x320] = 6
  elif not removed:
    fp[2][0x320] = 8
    fp[2][0x180] = 4
  if pedal:
    fp[0][0x201] = 6
    if identity in CAMERA_IDS:
      fp = camera_pedal_fingerprint(camera=not removed, be=not alternate)
  cp = CarInterface.get_params(identity, fp, [], alpha, release, False)
  if disable:
    VehicleStartupPreferences(disable_bolt_long=True).prepare(cp)
  return cp


LONG_CASES = (
  (CAR.CHEVROLET_VOLT, {}), (CAR.CHEVROLET_VOLT, {'alternate': True}),
  (CAR.CHEVROLET_VOLT_ASCM, {'alpha': True}), (CAR.CHEVROLET_VOLT_CAMERA, {'alpha': True}),
  (CAR.CHEVROLET_VOLT_CAMERA, {'alpha': True, 'removed': True}),
  (CAR.CHEVROLET_VOLT_2019, {'alpha': True}), (CAR.CHEVROLET_VOLT_2019, {'alpha': True, 'alternate': True}),
  (CAR.CHEVROLET_VOLT_CC, {}), (CAR.CHEVROLET_BOLT_EUV, {'alpha': True}),
  (CAR.CHEVROLET_BOLT_ACC_2022_2023, {'alpha': True}), (CAR.CHEVROLET_SUBURBAN, {}),
  (CAR.CHEVROLET_BOLT_CC_2017, {}), (CAR.CHEVROLET_BOLT_CC_2018_2021, {'removed': True}),
  (CAR.CHEVROLET_BOLT_CC_2022_2023, {}), (CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, {}),
  (CAR.CHEVROLET_BOLT_CC_2017, {'pedal': True}),
  *((identity, {'pedal': True}) for identity in CAMERA_IDS),
)
STOCK_CASES = (
  (CAR.CHEVROLET_VOLT, {'disable': True}), (CAR.CHEVROLET_VOLT, {'disable': True, 'alternate': True}),
  (CAR.CHEVROLET_VOLT_CC, {'disable': True}), (CAR.CHEVROLET_VOLT_CAMERA, {}),
  (CAR.CHEVROLET_VOLT_CAMERA, {'removed': True}), (CAR.CHEVROLET_VOLT_2019, {}),
  (CAR.CHEVROLET_VOLT_ASCM, {}), (CAR.CHEVROLET_BOLT_EUV, {}),
  (CAR.CHEVROLET_BOLT_ACC_2022_2023, {'release': True, 'alpha': True}),
  (CAR.CHEVROLET_BOLT_CC_2022_2023, {'disable': True}),
  *((identity, {'pedal': True, 'disable': True}) for identity in CAMERA_IDS),
  *((identity, {'pedal': True, 'release': True}) for identity in CAMERA_IDS),
)


class TestGmFeatureRuntime(unittest.TestCase):
  def save_requests(self, params):
    params.put_bool('CurveSpeedController', True, block=True)
    params.put_bool('SpeedLimitController', True, block=True)
    params.put('LongitudinalPersonalityProfiles', profile_document(default_personality_profiles(False), enabled=False,
                                                                  global_braking_response='sport'), block=True)

  def test_actual_finalized_long_and_stock_startup_matrix(self):
    with OpenpilotPrefix():
      params = Params()
      self.save_requests(params)
      for identity, kwargs in LONG_CASES:
        with self.subTest(identity=identity, kwargs=kwargs):
          cp = configured(params, identity, **kwargs)
          self.assertTrue(cp.openpilotLongitudinalControl, (identity, kwargs, cp.safetyConfigs, cp.flags))
          self.assertTrue(longitudinal_supported(cp))
          for feature in ('conditional', 'curve', 'slc', 'profile'):
            self.assertTrue(enabled(params, cp, feature, {}), feature)
          self.assertTrue(enabled(params, cp, 'vision', {}))
      for identity, kwargs in STOCK_CASES:
        with self.subTest(identity=identity, kwargs=kwargs):
          cp = configured(params, identity, **kwargs)
          self.assertFalse(cp.openpilotLongitudinalControl, (identity, kwargs, cp.safetyConfigs, cp.flags))
          self.assertFalse(longitudinal_supported(cp))
          self.assertTrue(display_supported(cp), (identity, kwargs, cp.safetyConfigs, cp.flags, cp.dashcamOnly))
          for feature in ('conditional', 'curve', 'profile'):
            self.assertFalse(enabled(params, cp, feature, {}), feature)
          self.assertTrue(enabled(params, cp, 'vision', {}))
          self.assertFalse(vision_control_enabled(params, cp))
          self.assertTrue(enabled(params, cp, 'slc', {}))
          settings = slc_runtime_settings(params, cp, {})
          self.assertTrue(settings.display)  # Saved controller-on also requests display.
          self.assertFalse(settings.enabled)
          self.assertTrue(settings.acceptance.display_only)

  def test_default_off_saved_stock_and_invalid_cp_preserve_admission_boundaries(self):
    from openpilot.starpilot.conditional_mode.policy import ModeChoice
    from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences
    with OpenpilotPrefix():
      params = Params()
      cp = configured(params, CAR.CHEVROLET_VOLT_CC)
      self.assertTrue(enabled(params, cp, 'conditional', {}))  # Readable factory CEM.
      self.assertTrue(enabled(params, cp, 'profile', {}))  # Fresh Comfort is the readable missing-document default.
      self.assertFalse(enabled(params, cp, 'curve', {}))
      self.assertTrue(enabled(params, cp, 'slc', {}))
      self.assertTrue(enabled(params, cp, 'vision', {}))
      settings = slc_runtime_settings(params, cp, {})
      self.assertTrue(settings.display)
      self.assertFalse(settings.enabled)
      self.assertTrue(settings.acceptance.display_only)
      self.assertFalse(vision_control_enabled(params, cp))
      params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.STOCK))), block=True)
      self.assertTrue(enabled(params, cp, 'conditional', {}))  # Owner retains Stock session lifecycle.
      params.put_bool('SafeMode', True, block=True)
      self.assertFalse(enabled(params, cp, 'conditional', {}))
      params.put_bool('SafeMode', False, block=True)
      Path(params.get_param_path('ConditionalModeConfig')).write_bytes(b'{broken')
      self.assertFalse(enabled(params, cp, 'conditional', {}))
      self.save_requests(params)
      for mutation in ('passive', 'dashcamOnly', 'notCar', 'word', 'extra_config', 'brand'):
        bad = cp.as_reader().as_builder()
        if mutation in ('passive', 'dashcamOnly', 'notCar'):
          setattr(bad, mutation, True)
        elif mutation == 'word':
          bad.safetyConfigs[0].safetyParam |= 0x8000
        elif mutation == 'brand':
          bad.brand = 'unknown'
        else:
          bad.safetyConfigs = list(bad.safetyConfigs) * 2
        self.assertFalse(longitudinal_supported(bad), mutation)
        self.assertFalse(display_supported(bad), mutation)
      release = configured(params, CAR.CHEVROLET_VOLT_CC, release=True)
      self.assertTrue(release.dashcamOnly)
      self.assertFalse(display_supported(release))
      for feature, flag in (('conditional', 'CONDITIONAL_MODE_REPLAY_RUNTIME'), ('curve', 'CURVE_REPLAY_RUNTIME'),
                            ('slc', 'SLC_REPLAY_RUNTIME'), ('profile', 'LONG_PLANNER_REPLAY_RUNTIME')):
        self.assertTrue(enabled(params, None, feature, {flag: '1'}))

  def test_real_curve_owner_keeps_current_authority_and_freshness(self):
    from openpilot.starpilot.curve_speed.preferences import PreferenceHost
    from openpilot.starpilot.curve_speed.tests.test_host import Bus, NOW, sample_messages
    with OpenpilotPrefix():
      params = Params()
      self.save_requests(params)
      cp = configured(params, CAR.CHEVROLET_VOLT_CC)
      preferences = PreferenceHost(params)
      try:
        host = preferences.make_host()
        preferences.refresh(host, NOW)
        stamp = [NOW]
        host.clock_pair = lambda: (stamp[0], stamp[0] + 2_000_000_000)
        sm = Bus(sample_messages())
        self.assertIsNone(host.sample(sm, cp, stamp[0], follow_time_s=1.45).ceiling_mps)  # Clock epoch arms first.
        stamp[0] += 50_000_000
        sm.logMonoTime = dict.fromkeys(sm.messages, stamp[0])
        sm.messages['modelV2'].timestampEof = stamp[0] + 2_000_000_000
        self.assertIsNotNone(host.sample(sm, cp, stamp[0], follow_time_s=1.45).ceiling_mps)
        sm.logMonoTime['radarState'] = NOW - 1_000_000_000
        self.assertIsNone(host.sample(sm, cp, stamp[0], follow_time_s=1.45).ceiling_mps)
        sm = Bus(sample_messages())
        sm.messages['carControl'].longActive = False
        self.assertIsNone(host.sample(sm, cp, stamp[0], follow_time_s=1.45).ceiling_mps)
        disabled = configured(params, CAR.CHEVROLET_VOLT_CC, disable=True)
        self.assertIsNone(host.sample(Bus(sample_messages()), disabled, NOW, follow_time_s=1.45).ceiling_mps)
      finally:
        preferences.close()

  def test_actual_gm_unknown_sign_source_never_creates_speed_cap(self):
    from openpilot.cereal import messaging
    from opendbc.car.gm.carstate import CarState
    from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
    from openpilot.starpilot.speed_limits.runtime import Runtime
    from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM
    with OpenpilotPrefix():
      params = Params()
      self.save_requests(params)
      for disabled in (False, True):
        cp = configured(params, CAR.CHEVROLET_VOLT_CC, disable=disabled)
        self.assertFalse(hasattr(CarState(cp), 'dashboard_limit'))
        data, envelopes = messages()
        data['carState'].canValid = True
        data['carState'].canTimeout = False
        data['carControl'].longActive = not disabled
        data['carControl'].enabled = True
        dashboard = messaging.new_message('slcDashboardObservation')
        data['slcDashboardObservation'] = dashboard.slcDashboardObservation
        sm = ReplaySM(data, 2_000_000_000)
        runtime = Runtime(slc_runtime_settings(params, cp, {}), session_id='gm-source')
        output = runtime.step(sm, cp, now_ns=2_000_000_000)
        self.assertFalse(output.message.slcState.hasAccepted)
        self.assertFalse(output.message.slcState.hasPending)
        self.assertFalse(output.message.slcState.hasCeiling)
        self.assertIsNone(output.result.ceiling)

  def test_actual_planner_startup_constructs_long_hosts_and_display_only_stock_host(self):
    from unittest.mock import Mock, patch
    from openpilot.selfdrive.controls import plannerd
    class EndLoop(Exception):
      pass
    with OpenpilotPrefix():
      params = Params()
      self.save_requests(params)
      for disabled in (False, True):
        cp = configured(params, CAR.CHEVROLET_VOLT_CC, disable=disabled)
        params.put('CarParams', cp.to_bytes(), block=True)
        sm = Mock()
        sm.update.side_effect = EndLoop
        with (patch.object(plannerd, 'Params', return_value=params),
              patch.object(plannerd, 'config_realtime_process'),
              patch.object(plannerd, 'ConditionalPlannerHost', wraps=plannerd.ConditionalPlannerHost) as conditional,
              patch.object(plannerd, 'CurvePreferenceHost', wraps=plannerd.CurvePreferenceHost) as curve,
              patch.object(plannerd, 'ProfileHost', wraps=plannerd.ProfileHost) as profile,
              patch.object(slc_host, 'SlcRuntime', wraps=slc_host.SlcRuntime) as slc,
              patch.object(plannerd.messaging, 'sub_sock'),
              patch.object(plannerd.messaging, 'SubMaster', return_value=sm),
              patch.object(plannerd.messaging, 'PubMaster'),
              patch.dict('os.environ', {}, clear=True)):
          with self.assertRaises(EndLoop):
            plannerd.main()
          self.assertEqual(conditional.call_count, int(not disabled))
          self.assertEqual(curve.call_count, int(not disabled))
          self.assertEqual(profile.call_count, int(not disabled))
          slc.assert_called_once()
          settings = slc.call_args.args[0]
          self.assertEqual(settings.enabled, not disabled)
          self.assertTrue(settings.display)
          self.assertEqual(settings.acceptance.display_only, disabled)
