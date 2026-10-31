"""Saved profile migration and actual native longitudinal planner behavior."""

from copy import deepcopy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import global_braking_for_frame, profile_for_frame
from openpilot.selfdrive.controls import plannerd
from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling
from openpilot.starpilot.speed_limits.acceptance import Authority, LongitudinalOwner, Mode
from openpilot.starpilot.longitudinal.profile_document import default_personality_profiles, profile_document as current_profile_document
from openpilot.starpilot.longitudinal.profile_runtime import (ProfileHost, ProfileSmoother, ProfileTuning,
                                                             read_settings, read_traffic_settings, resolve)
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import V_EGO, messages, snapshot
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR


def legacy_document(profiles, *, enabled, global_braking_response='standard'):
  profiles = deepcopy(profiles)
  for profile in profiles.values():
    for category in ('acceleration', 'braking'):
      if profile[category]['preset'] == 'selected_profile':
        profile[category]['preset'] = 'dom_default'
  document = current_profile_document(profiles, enabled=enabled, global_braking_response=global_braking_response)
  document['schemaVersion'] = 4
  document.pop('extraStoppedGapM')
  document['globalBrakingResponse'] = document.pop('selectedDecelerationProfile')
  document.pop('selectedAccelerationProfile')
  return document


class FrameSM:
  def __init__(self, data):
    self.data = data
    self.valid = {}
    self.alive = {}
    self.logMonoTime = {}

  def __getitem__(self, key):
    return self.data[key]


def required_float(value: float | None) -> float:
  if value is None:
    raise AssertionError('expected a resolved numeric profile value')
  return value


class ProfileRuntimeTests(unittest.TestCase):
  def setUp(self):
    self.path = Path(tempfile.mkdtemp(prefix='long-profile-'))
    self.addCleanup(shutil.rmtree, self.path, ignore_errors=True)
    self.params = Params(str(self.path))
    self.cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)

  def test_traffic_defaults_apply_without_custom_master(self):
    settings = read_traffic_settings(self.params)
    self.assertTrue(settings.valid)
    self.assertFalse(settings.profile_enabled)
    for speed, expected in ((0.0, 0.75), (12.5, 1.175), (25.0, 1.6), (30.0, 1.6)):
      with self.subTest(speed=speed):
        tuning = resolve(settings, log.LongitudinalPersonality.aggressive, speed, self.cp, traffic_mode=True)
        assert tuning is not None
        self.assertEqual(tuning.personality_id, 'traffic')
        self.assertAlmostEqual(tuning.follow_seconds, expected)
        self.assertEqual((tuning.acceleration_jerk, tuning.deceleration_jerk, tuning.speed_jerk,
                          tuning.speed_decrease_jerk, tuning.danger_jerk), (1.0,) * 5)
        self.assertAlmostEqual(required_float(tuning.cruise_brake_magnitude), 0.42)
    for speed, expected in ((0.0, 1.10), (5.0, 0.87), (10.0, 0.67), (12.5, 0.60),
                            (15.0, 0.53), (20.0, 0.44), (25.0, 0.34), (40.0, 0.23)):
      with self.subTest(acceleration_speed=speed):
        tuning = resolve(settings, log.LongitudinalPersonality.aggressive, speed, self.cp, traffic_mode=True)
        self.assertAlmostEqual(required_float(tuning.acceleration_max), expected)
    host = ProfileHost(self.params)
    self.assertAlmostEqual(host.sample(1_000_000_000, log.LongitudinalPersonality.aggressive,
                                       12.5, self.cp, traffic_mode=True).follow_seconds, 1.175)
    self.assertIsNone(host.sample(1_010_000_000, log.LongitudinalPersonality.aggressive,
                                  12.5, self.cp, traffic_mode=None))
    self.assertIsNone(ProfileSmoother().sample(None, log.LongitudinalPersonality.standard, 0.05, traffic_mode=None))

  def test_global_braking_is_independent_of_custom_master_and_v3_migrates_read_only(self):
    host = ProfileHost(self.params)
    self.assertEqual(host.sample_global_braking(1_000_000_000), 'eco')
    self.assertIsNone(host.sample(1_000_000_000, log.LongitudinalPersonality.standard, V_EGO, self.cp))
    document = legacy_document(default_personality_profiles(False), enabled=False, global_braking_response='eco')
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    self.assertEqual(host.sample_global_braking(2_000_000_000), 'eco')
    self.assertIsNone(host.sample(2_000_000_000, log.LongitudinalPersonality.standard, V_EGO, self.cp))
    document['schemaVersion'] = 3
    document.pop('globalBrakingResponse')
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    raw = self.params.get('LongitudinalPersonalityProfiles')
    self.assertEqual(host.sample_global_braking(3_000_000_000), 'standard')
    self.assertEqual(self.params.get('LongitudinalPersonalityProfiles'), raw)
    Path(self.params.get_param_path('LongitudinalPersonalityProfiles')).write_text('{invalid')
    self.assertIsNone(host.sample_global_braking(4_000_000_000))

  def test_global_braking_host_bridge_requires_fresh_car_and_control_sources(self):
    self.params.put('LongitudinalPersonalityProfiles',
                    legacy_document(default_personality_profiles(False), enabled=False,
                                     global_braking_response='sport'), block=True)
    data, _ = messages()
    data['carState'].canValid = True
    frame = FrameSM(data)
    now_ns = 5_000_000_000
    for name in ('carState', 'carControl', 'selfdriveState', 'controlsState'):
      frame.valid[name] = frame.alive[name] = True
      frame.logMonoTime[name] = now_ns
    host = ProfileHost(self.params)
    self.assertEqual(global_braking_for_frame(host, frame, now_ns), 'sport')
    frame.alive['carControl'] = False
    self.assertIsNone(global_braking_for_frame(host, frame, now_ns + 1))
    frame.alive['carControl'] = True
    frame.logMonoTime['carState'] = now_ns - 1_000_000_000
    self.assertIsNone(global_braking_for_frame(host, frame, now_ns + 1))

  def test_traffic_saved_jerk_and_follow_interpolate_on_0_to_25_mps(self):
    self.params.put_bool('CustomPersonalities', True, block=True)
    self.params.put('TrafficFollow', 0.9, block=True)
    self.params.put('RelaxedFollow', 1.7, block=True)
    for suffix in ('JerkAcceleration', 'JerkDeceleration', 'JerkSpeed', 'JerkSpeedDecrease', 'JerkDanger'):
      self.params.put('Traffic' + suffix, 150.0, block=True)
      self.params.put('Relaxed' + suffix, 50.0, block=True)
    settings = read_traffic_settings(self.params)
    self.assertTrue(settings.valid)
    for speed, follow, jerk in ((0.0, 0.9, 1.5), (12.5, 1.3, 1.0),
                                (25.0, 1.7, 0.5), (30.0, 1.7, 0.5)):
      with self.subTest(speed=speed):
        tuning = resolve(settings, log.LongitudinalPersonality.relaxed, speed, self.cp, traffic_mode=True)
        assert tuning is not None
        self.assertAlmostEqual(tuning.follow_seconds, follow)
        for component in (tuning.acceleration_jerk, tuning.deceleration_jerk, tuning.speed_jerk,
                          tuning.speed_decrease_jerk, tuning.danger_jerk):
          self.assertAlmostEqual(component, jerk)
    profiles = default_personality_profiles(False)
    profiles['traffic']['following'] = {'preset': 'custom', 'curve': [2.25] * 10}
    self.params.put('LongitudinalPersonalityProfiles', legacy_document(profiles, enabled=True), block=True)
    document_tuning = resolve(read_traffic_settings(self.params), log.LongitudinalPersonality.relaxed,
                              12.5, self.cp, traffic_mode=True)
    assert document_tuning is not None
    self.assertAlmostEqual(document_tuning.follow_seconds, 2.25)

  def test_traffic_acceleration_and_braking_saved_categories_require_enabled_owner(self):
    profiles = default_personality_profiles(False)
    profiles['traffic']['acceleration'] = {'preset': 'custom', 'curve': [1.4] * 10}
    profiles['traffic']['braking'] = {'preset': 'custom', 'curve': [0.35] * 10}
    self.params.put('LongitudinalPersonalityProfiles', legacy_document(profiles, enabled=True), block=True)
    self.params.put_bool('CustomPersonalities', True, block=True)
    self.params.put_bool('TrafficPersonalityProfile', True, block=True)

    tuning = resolve(read_traffic_settings(self.params), log.LongitudinalPersonality.standard,
                     12.5, self.cp, traffic_mode=True)
    assert tuning is not None
    self.assertAlmostEqual(required_float(tuning.acceleration_max), 1.4)
    self.assertAlmostEqual(required_float(tuning.cruise_brake_magnitude), 0.35)
    self.assertTrue(tuning.traffic_braking_custom)
    applied = ProfileSmoother().sample(tuning, log.LongitudinalPersonality.standard, 0.05, traffic_mode=True)
    assert applied is not None
    self.assertLess(applied.cruise_brake_magnitude, 1.2)

    self.params.put('LongitudinalPersonalityProfiles', legacy_document(profiles, enabled=False), block=True)
    disabled_document = resolve(read_traffic_settings(self.params), log.LongitudinalPersonality.standard,
                                12.5, self.cp, traffic_mode=True)
    self.assertAlmostEqual(required_float(disabled_document.acceleration_max), 0.60)
    self.assertAlmostEqual(required_float(disabled_document.cruise_brake_magnitude), 0.42)
    self.assertFalse(disabled_document.traffic_braking_custom)
    self.params.put('LongitudinalPersonalityProfiles', legacy_document(profiles, enabled=True), block=True)

    self.params.put_bool('TrafficPersonalityProfile', False, block=True)
    retained_profile = resolve(read_traffic_settings(self.params), log.LongitudinalPersonality.standard,
                               12.5, self.cp, traffic_mode=True)
    self.assertAlmostEqual(required_float(retained_profile.cruise_brake_magnitude), 0.35)
    self.assertTrue(retained_profile.traffic_braking_custom)
    self.assertAlmostEqual(required_float(retained_profile.acceleration_max), 1.4)
    self.params.put_bool('CustomPersonalities', False, block=True)
    disabled_master = resolve(read_traffic_settings(self.params), log.LongitudinalPersonality.standard,
                              12.5, self.cp, traffic_mode=True)
    self.assertAlmostEqual(required_float(disabled_master.cruise_brake_magnitude), 0.42)
    self.assertFalse(disabled_master.traffic_braking_custom)
    self.assertNotAlmostEqual(required_float(disabled_master.acceleration_max), 1.4)
    self.assertIsNone(read_settings(self.params))

  def test_traffic_valid_historical_six_acceleration_intersects_native_without_losing_other_categories(self):
    profiles = default_personality_profiles(False)
    profiles['traffic']['acceleration'] = {'preset': 'custom', 'curve': [6.0] * 10}
    profiles['traffic']['braking'] = {'preset': 'custom', 'curve': [0.35] * 10}
    profiles['traffic']['following'] = {'preset': 'custom', 'curve': [2.25] * 10}
    self.params.put('LongitudinalPersonalityProfiles', legacy_document(profiles, enabled=True), block=True)
    self.params.put_bool('CustomPersonalities', True, block=True)
    self.params.put_bool('TrafficPersonalityProfile', True, block=True)
    settings = read_traffic_settings(self.params)
    self.assertTrue(settings.valid)
    tuning = resolve(settings, log.LongitudinalPersonality.standard, 12.5, self.cp, traffic_mode=True)
    assert tuning is not None
    self.assertAlmostEqual(required_float(tuning.acceleration_max), 2.0)  # Native ACCEL_MAX; keep other valid categories.
    self.assertAlmostEqual(required_float(tuning.cruise_brake_magnitude), 0.35)
    self.assertAlmostEqual(tuning.follow_seconds, 2.25)
    ordinary = ProfileTuning('standard', 1.45, 1.0, 1.0, 1.0, 1.0, 1.0,
                             cruise_brake_magnitude=0.35)
    self.assertIsNone(ProfileSmoother().sample(ordinary, log.LongitudinalPersonality.standard,
                                               0.05, traffic_mode=False))

  def test_half_second_traffic_is_supported_but_malformed_values_are_unavailable(self):
    self.params.put_bool('CustomPersonalities', True, block=True)
    self.params.put('TrafficFollow', 0.5, block=True)
    settings = read_traffic_settings(self.params)
    self.assertTrue(settings.valid)
    tuning = resolve(settings, log.LongitudinalPersonality.standard, 0.0, self.cp, traffic_mode=True)
    assert tuning is not None
    self.assertAlmostEqual(tuning.follow_seconds, 0.5)
    self.assertEqual(self.params.get('TrafficFollow'), 0.5)
    assert read_settings(self.params) is not None
    self.params.put('TrafficFollow', 0.75, block=True)
    Path(self.params.get_param_path('TrafficJerkDanger')).write_bytes(b'nan')
    self.assertFalse(read_traffic_settings(self.params).valid)
    assert read_settings(self.params) is not None
    self.params.put_bool('CustomPersonalities', False, block=True)
    self.assertTrue(read_traffic_settings(self.params).valid)  # Saved custom bytes are inactive with master off.

  def test_traffic_unknown_mode_drops_prior_target_and_nontraffic_stays_legacy(self):
    self.params.put_bool('CustomPersonalities', True, block=True)
    host = ProfileHost(self.params)
    traffic = host.sample(1_000_000_000, log.LongitudinalPersonality.standard, 12.5, self.cp, traffic_mode=True)
    assert traffic is not None
    smoother = ProfileSmoother()
    assert smoother.sample(traffic, log.LongitudinalPersonality.standard, 0.05, traffic_mode=True) is not None
    self.assertIsNone(host.sample(1_010_000_000, log.LongitudinalPersonality.standard, 12.5, self.cp, traffic_mode=None))
    self.assertIsNone(smoother.sample(None, log.LongitudinalPersonality.standard, 0.05, traffic_mode=None))
    self.assertIsNone(smoother.applied)
    ordinary = host.sample(1_020_000_000, log.LongitudinalPersonality.standard, 45 * 0.44704, self.cp, traffic_mode=False)
    assert ordinary is not None
    self.assertAlmostEqual(ordinary.follow_seconds, 1.45)
    self.params.put('TrafficFollow', 0.5, block=True)
    half_second = host.sample(1_030_000_000, log.LongitudinalPersonality.standard, 12.5, self.cp, traffic_mode=True)
    assert half_second is not None
    self.assertAlmostEqual(half_second.follow_seconds, 1.05)

  def test_traffic_smoother_rejects_invalid_target_and_starts_from_native_personality(self):
    smoother = ProfileSmoother()
    target = resolve(read_traffic_settings(self.params), log.LongitudinalPersonality.standard,
                     25.0, self.cp, traffic_mode=True)
    assert target is not None
    first = smoother.sample(target, log.LongitudinalPersonality.standard, 0.05, traffic_mode=True)
    assert first is not None
    self.assertAlmostEqual(first.follow_seconds, 1.50)  # Ordinary standard starts at 1.45 s.
    self.assertGreaterEqual(first.follow_seconds, min(1.45, target.follow_seconds))
    invalid = ProfileTuning('traffic', 0.49, 1.0, 1.0, 1.0, 1.0, 1.0)
    self.assertIsNone(smoother.sample(invalid, log.LongitudinalPersonality.standard, 0.05,
                                      traffic_mode=True))
    self.assertIsNone(smoother.applied)
    self.assertIsNone(smoother.sample(None, log.LongitudinalPersonality.standard, 0.05,
                                      traffic_mode=True))

  def test_fresh_traffic_off_fades_to_native_but_unknown_clears_immediately(self):
    traffic = resolve(read_traffic_settings(self.params), log.LongitudinalPersonality.standard,
                      0.0, self.cp, traffic_mode=True)
    assert traffic is not None
    self.assertAlmostEqual(traffic.follow_seconds, 0.75)
    smoother = ProfileSmoother()
    for _ in range(20):
      active = smoother.sample(traffic, log.LongitudinalPersonality.standard, 0.05, traffic_mode=True)
    assert active is not None
    self.assertAlmostEqual(active.follow_seconds, 0.75)

    previous = active.follow_seconds
    for _ in range(20):
      ordinary = smoother.sample(None, log.LongitudinalPersonality.standard, 0.05, traffic_mode=False)
      if ordinary is None:
        break
      self.assertGreaterEqual(ordinary.follow_seconds, previous)
      self.assertLessEqual(ordinary.follow_seconds - previous, 0.05 + 1e-9)
      previous = ordinary.follow_seconds
    else:
      self.fail('fresh Traffic OFF did not converge to the ordinary native profile')
    self.assertIsNone(smoother.applied)
    self.assertAlmostEqual(previous, 1.45, delta=0.05)

    for _ in range(20):
      active = smoother.sample(traffic, log.LongitudinalPersonality.standard, 0.05, traffic_mode=True)
    assert active is not None
    self.assertIsNone(smoother.sample(None, log.LongitudinalPersonality.standard, 0.05, traffic_mode=None))
    self.assertIsNone(smoother.applied)

  def test_saved_preference_off_and_legacy_values(self):
    assert read_settings(self.params) is None
    self.params.put_bool('CustomPersonalities', True, block=True)
    self.params.put('StandardFollow', 2.0, block=True)
    self.params.put('StandardFollowHigh', 1.0, block=True)
    self.params.put('StandardJerkAcceleration', 175.0, block=True)
    settings = read_settings(self.params)
    assert settings is not None
    self.assertTrue(settings.profile_enabled['standard'])
    assert settings.document is not None
    self.assertEqual(settings.document['selectedDecelerationProfile'], 'eco')
    self.assertIsNone(self.params.get('LongitudinalPersonalityProfiles'))
    low = resolve(settings, log.LongitudinalPersonality.standard, 45 * 0.44704, self.cp)
    high = resolve(settings, log.LongitudinalPersonality.standard, 70 * 0.44704, self.cp)
    assert low is not None and high is not None
    self.assertAlmostEqual(low.follow_seconds, 2.0)
    self.assertAlmostEqual(high.follow_seconds, 1.0)
    self.assertAlmostEqual(low.acceleration_jerk, 1.75)

  def test_document_and_malformed_saved_settings(self):
    self.params.put_bool('CustomPersonalities', True, block=True)
    profiles = default_personality_profiles(False)
    profiles['standard']['following'] = {'preset': 'custom', 'curve': [2.25] * 10}
    document = legacy_document(profiles, enabled=True)
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    settings = read_settings(self.params)
    assert settings is not None
    tuning = resolve(settings, log.LongitudinalPersonality.standard, V_EGO, self.cp)
    assert tuning is not None
    self.assertAlmostEqual(tuning.follow_seconds, 2.25)
    self.params.put('LongitudinalPersonalityProfiles', {'schemaVersion': 999}, block=True)
    self.assertIsNone(read_settings(self.params))
    document['schemaVersion'] = 2
    document.pop('globalBrakingResponse')
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    migrated = read_settings(self.params)
    assert migrated is not None and migrated.document is not None
    self.assertEqual(migrated.document['schemaVersion'], 6)
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    Path(self.params.get_param_path('LongitudinalPersonalityProfiles')).write_text('{broken')
    self.assertIsNone(read_settings(self.params))
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    Path(self.params.get_param_path('StandardFollow')).write_text('not-a-float')
    self.assertIsNone(read_settings(self.params))

  def test_host_refresh_and_disable(self):
    self.params.put_bool('CustomPersonalities', True, block=True)
    host = ProfileHost(self.params)
    assert host.sample(1_000_000_000, log.LongitudinalPersonality.standard, V_EGO, self.cp) is not None
    self.params.put_bool('CustomPersonalities', False, block=True)
    assert host.sample(1_100_000_000, log.LongitudinalPersonality.standard, V_EGO, self.cp) is not None
    self.assertIsNone(host.sample(2_000_000_000, log.LongitudinalPersonality.standard, V_EGO, self.cp))

  def test_launch_profile_distinguishes_presets_manual_and_disabled(self):
    self.params.put_bool('CustomPersonalities', True, block=True)
    for preset in ('dom_default', 'eco', 'standard', 'sport', 'custom'):
      profiles = default_personality_profiles(True)
      profiles['standard']['acceleration'] = {'preset': preset, 'curve': [0.3] * 10 if preset == 'custom' else []}
      self.params.put('LongitudinalPersonalityProfiles', legacy_document(profiles, enabled=True), block=True)
      host = ProfileHost(self.params)
      tuning = host.sample(1_000_000_000, log.LongitudinalPersonality.standard, 0.0, self.cp)
      assert tuning is not None
      self.assertEqual(tuning.custom_acceleration, preset == 'custom')
      self.assertEqual(tuning.acceleration_max is None, preset == 'dom_default')
      self.assertFalse(host.disabled)
    self.params.put_bool('CustomPersonalities', False, block=True)
    self.assertIsNone(host.sample(2_000_000_000, log.LongitudinalPersonality.standard, 0.0, self.cp))
    self.assertTrue(host.disabled)
    Path(self.params.get_param_path('LongitudinalPersonalityProfiles')).write_text('{broken')
    self.assertIsNone(host.sample(3_000_000_000, log.LongitudinalPersonality.standard, 0.0, self.cp))
    self.assertFalse(host.disabled)

  def test_gm_normal_saved_profile_reaches_planner_and_revokes_when_inactive(self):
    from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
    from opendbc.car.gm.tests.test_ascm_intercept import params as ascm_params
    from opendbc.car.gm.values import CAR as GM
    from openpilot.starpilot.feature_runtime import enabled

    self.params.put_bool('CustomPersonalities', True, block=True)
    profiles = default_personality_profiles(True)
    profiles['standard']['acceleration'] = {'preset': 'custom', 'curve': [0.6] * 10}
    profiles['standard']['following'] = {'preset': 'custom', 'curve': [2.25] * 10}
    document = legacy_document(profiles, enabled=True)
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    for cp in (pedal_params(GM.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, setting=True, pedal=True),
               ascm_params(GM.CHEVROLET_BOLT_EUV, alpha=True),
               ascm_params(GM.CHEVROLET_VOLT, radar=True),
               ascm_params(GM.CHEVROLET_VOLT_ASCM, sascm=True, alpha=True)):
      self.assertTrue(enabled(self.params, cp, 'profile', {}))
      host = ProfileHost(self.params)
      sm, _ = messages()
      sm['carControl'].longActive = True
      sm['carState'].canValid = True
      frame = FrameSM(sm)
      for name in ('carState', 'carControl', 'selfdriveState', 'controlsState'):
        frame.valid[name] = frame.alive[name] = True
        frame.logMonoTime[name] = 1_000_000_000
      tuning = profile_for_frame(host, frame, cp, 1_000_000_000)
      assert tuning is not None
      self.assertAlmostEqual(tuning.acceleration_max, 0.6)
      planner = LongitudinalPlanner(cp, init_v=V_EGO)
      for _ in range(40):
        planner.update(sm, profile_tuning=tuning)
      assert planner.last_profile is not None
      self.assertAlmostEqual(planner.last_profile.acceleration_max, 0.6)
      self.assertAlmostEqual(planner.mpc.params[0, 4], 2.25)
      sm['carControl'].longActive = False
      planner.update(sm, profile_tuning=tuning)
      self.assertIsNone(planner.last_profile)
      frame.logMonoTime['carState'] = 800_000_000
      self.assertIsNone(profile_for_frame(host, frame, cp, 1_000_000_000))
      self.assertEqual(self.params.get('LongitudinalPersonalityProfiles'), document)

  def test_valid_profile_intersects_native_limits_without_dropping_other_tuning(self):
    self.params.put_bool('CustomPersonalities', True, block=True)
    profiles = default_personality_profiles(False)
    profiles['standard']['acceleration'] = {'preset': 'sport_plus', 'curve': []}
    profiles['standard']['following'] = {'preset': 'custom', 'curve': [2.25] * 10}
    document = legacy_document(profiles, enabled=True)
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    settings = read_settings(self.params)
    tuning = resolve(settings, log.LongitudinalPersonality.standard, 0.0, self.cp)
    assert tuning is not None
    self.assertEqual(tuning.acceleration_max, 2.0)
    self.assertEqual(tuning.follow_seconds, 2.25)
    planner = LongitudinalPlanner(self.cp, init_v=0.0)
    for _ in range(40):
      sm, _ = messages()
      sm['carState'].vEgo = 0.0
      sm['carControl'].longActive = True
      planner.update(sm, profile_tuning=tuning)
      assert planner.last_profile is not None
      self.assertEqual(planner.mpc.solution_status, 0)
      self.assertLessEqual(planner.mpc.params[0, 1], 2.0)
    self.assertAlmostEqual(planner.mpc.params[0, 4], 2.25)
    self.assertEqual(self.params.get('LongitudinalPersonalityProfiles'), document)

  def test_plannerd_input_freshness_and_host_refresh(self):
    self.params.put_bool('CustomPersonalities', True, block=True)
    host = ProfileHost(self.params)
    sm, _ = messages()
    sm['carState'].canValid = True
    sm = FrameSM(sm)
    for service in ('carState', 'carControl', 'selfdriveState', 'controlsState'):
      sm.valid[service] = sm.alive[service] = True
      sm.logMonoTime[service] = 1_000_000_000
    assert profile_for_frame(host, sm, self.cp, 1_000_000_000) is not None
    sm['carState'].canValid = False
    self.assertIsNone(profile_for_frame(host, sm, self.cp, 1_000_000_000))
    sm['carState'].canValid = True
    sm['carState'].canTimeout = True
    self.assertIsNone(profile_for_frame(host, sm, self.cp, 1_000_000_000))
    sm['carState'].canTimeout = False
    sm.logMonoTime['carState'] = 800_000_000
    self.assertIsNone(profile_for_frame(host, sm, self.cp, 1_000_000_000))
    sm.logMonoTime['carState'] = 1_000_000_001
    self.assertIsNone(profile_for_frame(host, sm, self.cp, 1_000_000_000))
    sm.logMonoTime['carState'] = 1_000_000_000
    sm.valid['carState'] = False
    self.assertIsNone(profile_for_frame(host, sm, self.cp, 1_000_000_000))

  def test_real_plannerd_loop_feeds_native_solver_only_when_opted_in(self):
    class EndLoop(Exception):
      pass

    class LoopSM(FrameSM):
      def __init__(self, data):
        data['deviceState'] = plannerd.messaging.new_message('deviceState').deviceState
        data['deviceState'].started = True
        data['deviceState'].startedMonoTime = 500_000_000
        data['starpilotNavigation'] = plannerd.messaging.new_message('starpilotNavigation').starpilotNavigation
        super().__init__(data)
        self.updated = {'modelV2': True}
        self.seen = dict.fromkeys(data, True)
        self.recv_time = dict.fromkeys(data, 1.0)
        self.frame = 0
        for name in data:
          self.valid[name] = self.alive[name] = True
          self.logMonoTime[name] = 1_000_000_000

      def update(self):
        self.frame += 1
        if self.frame > 32:
          raise EndLoop
        for name in self.data:
          self.logMonoTime[name] = 1_000_000_000 + self.frame * 50_000_000
          self.recv_time[name] = self.logMonoTime[name] / 1e9

      def all_checks(self, services=None):
        return True

    class Publisher:
      def __init__(self):
        self.plans = []

      def send(self, service, message):
        if service == 'longitudinalPlan':
          self.plans.append((float(message.longitudinalPlan.aTarget),
                             tuple(message.longitudinalPlan.speeds)))

    self.params.put('CarParams', self.cp.to_bytes(), block=True)
    self.params.put('StandardFollow', 2.5, block=True)
    self.params.put('StandardFollowHigh', 2.5, block=True)
    results = []
    for enabled in (False, True):
      self.params.put_bool('CustomPersonalities', enabled, block=True)
      # Explicit native response removes the separate default Comfort request;
      # this comparison isolates the saved following/jerk master switch.
      self.params.put('LongitudinalPersonalityProfiles', current_profile_document(
        default_personality_profiles(False), enabled=enabled,
        selected_deceleration_profile='dom_default'), block=True)
      data, _ = messages(lead=True)
      data['carState'].canValid = True
      data['carControl'].longActive = True
      sm = LoopSM(data)
      publisher = Publisher()
      environment = {}  # Normal admission follows the actual saved request.
      with (mock.patch.object(plannerd, 'Params', return_value=self.params),
            mock.patch.object(plannerd, 'config_realtime_process'),
            mock.patch.object(plannerd, 'LeadApproachPreferences', return_value=None),
            mock.patch.object(plannerd.messaging, 'SubMaster', return_value=sm),
            mock.patch.object(plannerd.messaging, 'PubMaster', return_value=publisher),
            mock.patch.object(plannerd.time, 'monotonic_ns', side_effect=lambda source=sm: source.logMonoTime['modelV2']),
            mock.patch.object(plannerd, 'paired_clocks_ns',
                              side_effect=lambda source=sm: (source.logMonoTime['modelV2'], source.logMonoTime['modelV2'], 0)),
            mock.patch.dict('os.environ', environment, clear=True)):
        with self.assertRaises(EndLoop):
          plannerd.main()
      self.assertEqual(len(publisher.plans), 32)
      results.append(publisher.plans[-1])
    self.assertNotEqual(results[0], results[1])

  def test_native_default_exact_and_selected_profile_changes_solver(self):
    baseline = LongitudinalPlanner(self.cp, init_v=V_EGO)
    explicit_off = LongitudinalPlanner(self.cp, init_v=V_EGO)
    tuned = LongitudinalPlanner(self.cp, init_v=V_EGO)
    selected = ProfileTuning('standard', 2.5, 1.5, 1.5, 1.5, 1.5, 1.5, 0.5, 0.75)
    for _ in range(32):
      sm, _ = messages(lead=True)
      sm['carControl'].longActive = True
      baseline.update(sm)
      explicit_off.update(sm, profile_tuning=None)
      tuned.update(sm, profile_tuning=selected)
      self.assertEqual(snapshot(baseline), snapshot(explicit_off))
      self.assertEqual(baseline.mpc.solution_status, 0)
      self.assertEqual(explicit_off.mpc.solution_status, 0)
      self.assertEqual(tuned.mpc.solution_status, 0)
    assert tuned.last_profile is not None
    self.assertGreater(tuned.mpc.params[0, 4], baseline.mpc.params[0, 4])
    self.assertLess(tuned.mpc.params[0, 1], baseline.mpc.params[0, 1])
    self.assertLessEqual(tuned.mpc.params[0, 1], 2.0)
    self.assertNotEqual(snapshot(tuned), snapshot(baseline))

  def test_default_native_golden_from_committed_planner(self):
    # Captured by loading the committed pre-profile LongitudinalPlanner class
    # independently against the same native acados solver and message inputs.
    golden = {
      'ordinary': (0.9333333333333333, 0.9333333333333333, '0', 20.74389339004308),
      'lead': (-2.7921389094943962, 0.9333333333333333, '1', 17.200449813861578),
      'e2e': (-2.0, 1.4933333333333327, '4', 17.883714999983308),
      'force': (-0.5, -0.5, '0', 19.53261473430761),
    }
    for case, (target, cruise, source, speed0) in golden.items():
      with self.subTest(case=case):
        planner = LongitudinalPlanner(self.cp, init_v=V_EGO)
        for _ in range(32):
          sm, _ = messages(lead=case == 'lead', e2e=case == 'e2e', force=case == 'force')
          sm['carControl'].longActive = True
          planner.update(sm)
        actual = (float(planner.output_a_target), float(planner.a_cruise), str(planner.mpc.source),
                  float(planner.v_desired_trajectory[0]))
        self.assertAlmostEqual(planner.output_a_target, target, places=6, msg=f'actual native planner tuple: {actual!r}')
        self.assertAlmostEqual(planner.a_cruise, cruise, places=6)
        self.assertEqual(str(planner.mpc.source), source)
        self.assertAlmostEqual(planner.v_desired_trajectory[0], speed0, places=6)

  def test_stock_owner_and_lat_only_are_inert(self):
    selected = ProfileTuning('standard', 2.5, 1.5, 1.5, 1.5, 1.5, 1.5, 0.5, 0.75)
    for stock, lat_only in ((True, False), (False, True)):
      with self.subTest(stock=stock, lat_only=lat_only):
        host_cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
        if stock:
          host_cp.openpilotLongitudinalControl = False
          host_cp.pcmCruise = True
        baseline = LongitudinalPlanner(host_cp, init_v=V_EGO)
        opted = LongitudinalPlanner(host_cp, init_v=V_EGO)
        for _ in range(4):
          sm, _ = messages()
          sm['carControl'].longActive = not lat_only
          baseline.update(sm)
          opted.update(sm, profile_tuning=selected)
        self.assertEqual(snapshot(baseline), snapshot(opted))
        self.assertIsNone(opted.last_profile)

  def test_live_switch_is_bounded_and_smooth_invalid_fades_to_stock(self):
    planner = LongitudinalPlanner(self.cp, init_v=V_EGO)
    tight = ProfileTuning('standard', 2.5, 1.5, 1.5, 1.5, 1.5, 1.5, 0.5, 0.75)
    loose = ProfileTuning('standard', 0.8, 0.5, 0.5, 0.5, 0.5, 0.5, 1.8, 1.5)
    previous = None
    for target in [tight] * 20 + [loose] * 20 + [None] * 40:
      sm, _ = messages()
      sm['carControl'].longActive = True
      planner.update(sm, profile_tuning=target)
      applied = planner.last_profile
      if previous is not None and applied is not None:
        self.assertLessEqual(abs(applied.follow_seconds - previous.follow_seconds), planner.dt + 1e-8)
        self.assertLessEqual(abs(applied.acceleration_max - previous.acceleration_max), 2 * planner.dt + 1e-8)
      previous = applied
      self.assertEqual(planner.mpc.solution_status, 0)
    self.assertIsNone(planner.last_profile)

  def test_malformed_live_tuning_or_timing_cannot_enter_solver(self):
    smoother = ProfileSmoother()
    selected = ProfileTuning('standard', 2.5, 1.5, 1.5, 1.5, 1.5, 1.5, 0.5, 0.75)
    self.assertIsNone(smoother.sample(selected, log.LongitudinalPersonality.standard, float('nan')))
    self.assertIsNone(smoother.sample(ProfileTuning('standard', float('nan'), 1, 1, 1, 1, 1),
                                      log.LongitudinalPersonality.standard, 0.05))
    self.assertIsNone(smoother.sample(ProfileTuning('aggressive', 2.5, 1, 1, 1, 1, 1),
                                      log.LongitudinalPersonality.standard, 0.05))

  def test_soft_profile_cannot_weaken_mandatory_force_decel(self):
    baseline = LongitudinalPlanner(self.cp, init_v=V_EGO)
    tuned = LongitudinalPlanner(self.cp, init_v=V_EGO)
    soft = ProfileTuning('standard', 1.45, 1.0, 1.0, 1.0, 1.0, 1.0, None, 0.5)
    for frame in range(60):
      sm, _ = messages(force=frame >= 20)
      sm['carControl'].longActive = True
      baseline.update(sm)
      tuned.update(sm, profile_tuning=soft)
      if frame >= 20:
        self.assertLessEqual(tuned.a_cruise, baseline.a_cruise + 1e-8)
    self.assertAlmostEqual(tuned.a_cruise, baseline.a_cruise)

  def test_slc_ceiling_and_force_decel_keep_priority(self):
    selected = ProfileTuning('standard', 2.5, 1.5, 1.5, 1.5, 1.5, 1.5, 0.5, 0.75)
    authority = Authority(Mode.LONGITUDINAL_ONLY, LongitudinalOwner.SYSTEM, False, True, False, False)
    ceiling = CruiseCeiling(15.0, authority)
    tuned = LongitudinalPlanner(self.cp, init_v=V_EGO)
    for _ in range(24):
      sm, _ = messages()
      sm['carControl'].longActive = True
      tuned.update(sm, cruise_ceiling=ceiling, profile_tuning=selected)
    self.assertEqual(tuned.last_cruise_ceiling_status, 'applied')
    self.assertLess(tuned.a_cruise, 0.0)
    for _ in range(24):
      sm, _ = messages(force=True)
      sm['carControl'].longActive = True
      tuned.update(sm, cruise_ceiling=ceiling, profile_tuning=selected)
    self.assertEqual(tuned.last_cruise_ceiling_status, 'force_decel')
    self.assertLessEqual(tuned.a_cruise, -0.5)

  def test_slc_lower_ceiling_keeps_native_or_stronger_profile_braking(self):
    authority = Authority(Mode.LONGITUDINAL_ONLY, LongitudinalOwner.SYSTEM, False, True, False, False)
    ceiling = CruiseCeiling(15.0, authority)
    baseline = LongitudinalPlanner(self.cp, init_v=V_EGO)
    soft = LongitudinalPlanner(self.cp, init_v=V_EGO)
    strong = LongitudinalPlanner(self.cp, init_v=V_EGO)
    soft_tuning = ProfileTuning('standard', 1.45, 1, 1, 1, 1, 1, None, 0.75)
    strong_tuning = ProfileTuning('standard', 1.45, 1, 1, 1, 1, 1, None, 1.8)
    for _ in range(40):
      sm, _ = messages()
      sm['carControl'].longActive = True
      baseline.update(sm, cruise_ceiling=ceiling)
      soft.update(sm, cruise_ceiling=ceiling, profile_tuning=soft_tuning)
      strong.update(sm, cruise_ceiling=ceiling, profile_tuning=strong_tuning)
      self.assertEqual(soft.last_cruise_ceiling_status, 'applied')
      self.assertLessEqual(soft.a_cruise, baseline.a_cruise + 1e-8)
      self.assertLessEqual(strong.a_cruise, baseline.a_cruise + 1e-8)
    self.assertAlmostEqual(baseline.a_cruise, -0.5)
    self.assertAlmostEqual(soft.a_cruise, -0.75)
    self.assertLess(strong.a_cruise, soft.a_cruise)

  def test_selected_profile_keeps_lead_and_e2e_as_braking_candidates(self):
    selected = ProfileTuning('standard', 2.5, 1.5, 1.5, 1.5, 1.5, 1.5, 0.5, 0.75)
    for case in ('lead', 'e2e'):
      with self.subTest(case=case):
        tuned = LongitudinalPlanner(self.cp, init_v=V_EGO)
        for _ in range(32):
          sm, _ = messages(lead=case == 'lead', e2e=case == 'e2e')
          sm['carControl'].longActive = True
          tuned.update(sm, profile_tuning=selected)
        self.assertEqual(str(tuned.mpc.source), '1' if case == 'lead' else '4')
        if case == 'e2e':
          self.assertLessEqual(tuned.output_a_target, -2.0)


if __name__ == '__main__':
  unittest.main()
