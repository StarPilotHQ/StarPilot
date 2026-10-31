"""Global inheritance, legacy retention and actual planner arbitration."""
from unittest.mock import patch

from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib import longitudinal_planner as planner_module
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import update_curve_frame
from openpilot.starpilot.longitudinal.profile_document import (
  default_personality_profiles, profile_document, migrate_profile_document,
  update_personality_profile, interpolate_category_curve,
)
from openpilot.starpilot.longitudinal.profile_runtime import (
  ProfileHost, ProfileSettings, read_settings, read_traffic_settings, resolve, resolve_selected_profiles,
  SelectedProfileTuning,
)
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import V_EGO, messages, snapshot
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR


class SelectedProfilesTests(unittest.TestCase):
  def setUp(self):
    self.cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    self.path = Path(tempfile.mkdtemp(prefix='selected-profiles-'))
    self.addCleanup(shutil.rmtree, self.path, ignore_errors=True)
    self.params = Params(str(self.path))

  def document(self, acceleration='eco', braking='eco', enabled=False):
    return profile_document(default_personality_profiles(False), enabled=enabled,
                            selected_acceleration_profile=acceleration, selected_deceleration_profile=braking)

  def test_fresh_comfort_default_and_existing_choices_are_independent(self):
    fresh = profile_document(default_personality_profiles(False), enabled=False)
    self.assertEqual(fresh['selectedDecelerationProfile'], 'eco')
    self.assertEqual(ProfileHost(self.params).sample_global_braking(1_000_000_000), 'eco')
    self.assertIsNone(self.params.get('LongitudinalPersonalityProfiles'))
    for personality in range(3):
      for traffic in (False, True):
        selected = resolve_selected_profiles(fresh, personality, 10.0, self.cp, traffic_mode=traffic)
        self.assertEqual(selected.cruise_brake_magnitude, 0.6)
    for choice in ('dom_default', 'standard', 'eco', 'sport'):
      document = self.document(braking=choice)
      raw = json.dumps(document).encode()
      Path(self.params.get_param_path('LongitudinalPersonalityProfiles')).write_bytes(raw)
      migrated = migrate_profile_document(raw)
      assert migrated is not None
      self.assertEqual(migrated['selectedDecelerationProfile'], choice)
      self.assertEqual(ProfileHost(self.params).sample_global_braking(1_000_000_000), choice)
      self.assertEqual(Path(self.params.get_param_path('LongitudinalPersonalityProfiles')).read_bytes(), raw)

  def test_missing_document_production_admission_host_and_native_mpc(self):
    from openpilot.starpilot.feature_runtime import requested
    self.assertTrue(requested(self.params, 'profile'))
    selected = ProfileHost(self.params).sample_selected(1_000_000_000, 1, V_EGO, self.cp)
    self.assertEqual(selected.cruise_brake_magnitude, 0.6)
    self.assertEqual(selected.braking_style, 'eco')
    self.assertIsNone(selected.acceleration_max)
    ordinary, _ = self.run_planner()
    comfort, cruise = self.run_planner(selected)
    self.assertAlmostEqual(ordinary.a_cruise, -0.5)
    self.assertAlmostEqual(comfort.a_cruise, -0.6)
    self.assertEqual(cruise['base_brake_floor'], -0.6)
    self.assertIsNone(comfort.last_profile)
    self.assertEqual(comfort.mpc.params[0, 4], ordinary.mpc.params[0, 4])
    self.assertIsNone(self.params.get('LongitudinalPersonalityProfiles'))

  def test_saved_and_invalid_document_do_not_receive_fresh_comfort_default(self):
    from openpilot.starpilot.feature_runtime import requested
    for choice, magnitude in (('dom_default', None), ('standard', 1.2), ('sport', 2.4)):
      with self.subTest(choice=choice):
        raw = json.dumps(self.document('dom_default', choice)).encode()
        path = Path(self.params.get_param_path('LongitudinalPersonalityProfiles'))
        path.write_bytes(raw)
        selected = ProfileHost(self.params).sample_selected(1_000_000_000, 1, V_EGO, self.cp)
        self.assertEqual(selected.cruise_brake_magnitude, magnitude)
        self.assertEqual(path.read_bytes(), raw)
    path.write_bytes(b'not-json')
    self.assertFalse(requested(self.params, 'profile'))
    self.assertIsNone(ProfileHost(self.params).sample_selected(1_000_000_000, 1, V_EGO, self.cp))

  def test_inheritance_tracks_globals_without_master_or_jerk_activation(self):
    document = self.document()
    document['profiles']['aggressive']['acceleration'] = {'preset': 'sport', 'curve': []}
    document['profiles']['aggressive']['braking'] = {'preset': 'sport', 'curve': []}
    for global_choice in ('eco', 'standard'):
      document['selectedAccelerationProfile'] = global_choice
      document['selectedDecelerationProfile'] = global_choice
      self.params.put('LongitudinalPersonalityProfiles', document, block=True)
      self.assertIsNone(read_settings(self.params))
      for personality in (log.LongitudinalPersonality.standard, log.LongitudinalPersonality.relaxed):
        selected = resolve_selected_profiles(document, personality, 10.0, self.cp)
        self.assertEqual(selected.acceleration_max, interpolate_category_curve('acceleration', 10.0,
                         {'preset': global_choice, 'curve': []}, False))
        self.assertEqual(selected.cruise_brake_magnitude, 0.6 if global_choice == 'eco' else 1.2)
      aggressive = resolve_selected_profiles(document, log.LongitudinalPersonality.aggressive, 10.0, self.cp)
      self.assertEqual(aggressive.acceleration_max, interpolate_category_curve('acceleration', 10.0,
                       {'preset': 'sport', 'curve': []}, False))
      self.assertEqual(aggressive.cruise_brake_magnitude, 2.0)

  def test_v4_migration_preserves_dormant_custom_and_original_activation(self):
    document = self.document('dom_default', 'sport', enabled=True)
    document['schemaVersion'] = 4
    document.pop('extraStoppedGapM')
    document['globalBrakingResponse'] = document.pop('selectedDecelerationProfile')
    document.pop('selectedAccelerationProfile')
    for profile in document['profiles'].values():
      for category in ('acceleration', 'braking'):
        profile[category] = {'preset': 'dom_default', 'curve': []}
    document['profiles']['standard']['acceleration'] = {'preset': 'sport', 'curve': [0.9] * 10}
    document['profiles']['standard']['braking'] = {'preset': 'custom', 'curve': [0.8] * 10}
    original = deepcopy(document)
    migrated = migrate_profile_document(document)
    assert migrated is not None
    self.assertEqual(document, original)
    self.assertEqual(migrated['profiles']['standard']['acceleration']['curve'], [0.9] * 10)
    follow = dict.fromkeys(('aggressive', 'standard', 'relaxed'), (1.45, 1.45))
    jerk = dict.fromkeys(follow, (1.0, 1.0, 1.0, 1.0, 1.0))
    for enabled in (False, True):
      settings = ProfileSettings(migrated, dict.fromkeys(follow, enabled), follow, jerk)
      legacy = resolve(settings, log.LongitudinalPersonality.standard, 10.0, self.cp)
      selected = resolve_selected_profiles(migrated, log.LongitudinalPersonality.standard, 10.0, self.cp, legacy=legacy)
      if enabled:
        self.assertEqual(legacy.cruise_brake_magnitude, 0.8)
        self.assertIsNone(selected.cruise_brake_magnitude)
        assert legacy.acceleration_max is not None
      else:
        self.assertIsNone(legacy.acceleration_max)
        self.assertEqual(selected.cruise_brake_magnitude, 2.4)
      self.assertIsNone(selected.acceleration_max)
    updated = update_personality_profile(migrated['profiles'], 'standard', 'acceleration', 'selected_profile', [], False)
    assert updated is not None
    self.assertNotIn('legacyActivation', updated['standard']['acceleration'])
    self.assertEqual(updated['standard']['acceleration']['curve'], [0.9] * 10)
    self.assertTrue(updated['standard']['braking']['legacyActivation'])

  def test_prior_schema_migration_retains_original_curve_interpolation(self):
    from openpilot.starpilot.longitudinal.profile_document import PROFILE_AXES, _V1_PROFILE_AXES
    for version in (1, 2, 3):
      with self.subTest(version=version):
        profiles = default_personality_profiles(False)
        for profile in profiles.values():
          for category in ('acceleration', 'braking'):
            profile[category] = {'preset': 'dom_default', 'curve': []}
        profiles['standard']['acceleration'] = {'preset': 'custom', 'curve':
          [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8] if version == 1 else [0.8] * 10}
        saved = {'schemaVersion': version, 'enabled': True, 'axes': deepcopy(_V1_PROFILE_AXES if version == 1 else PROFILE_AXES),
                 'profiles': profiles}
        migrated = migrate_profile_document(saved)
        assert migrated is not None
        config = migrated['profiles']['standard']['acceleration']
        self.assertTrue(config['legacyActivation'])
        self.assertAlmostEqual(interpolate_category_curve('acceleration', 17.0, config, False),
                               0.54 if version == 1 else 0.8, places=6)
        if version == 1:
          self.assertEqual(config['legacyCurve'], saved['profiles']['standard']['acceleration']['curve'])
        else:
          self.assertEqual(config['curve'], saved['profiles']['standard']['acceleration']['curve'])
        self.assertEqual(migrated['selectedDecelerationProfile'], 'standard')

  def test_explicit_default_and_traffic_inheritance_are_distinct(self):
    document = self.document('sport', 'sport')
    inherited = resolve_selected_profiles(document, log.LongitudinalPersonality.standard, 10.0, self.cp, traffic_mode=True)
    self.assertEqual(inherited.cruise_brake_magnitude, 2.4)
    document['profiles']['traffic']['acceleration'] = {'preset': 'dom_default', 'curve': []}
    document['profiles']['traffic']['braking'] = {'preset': 'dom_default', 'curve': []}
    explicit = resolve_selected_profiles(document, log.LongitudinalPersonality.standard, 10.0, self.cp, traffic_mode=True)
    self.assertIsNone(explicit.acceleration_max)
    self.assertIsNone(explicit.cruise_brake_magnitude)
    self.assertIsNone(resolve_selected_profiles(document, 1, 10.0, self.cp, traffic_mode=None))

  def test_host_refresh_is_bounded_fail_closed_and_does_not_rewrite_saved_value(self):
    document = self.document()
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    raw = self.params.get('LongitudinalPersonalityProfiles')
    host = ProfileHost(self.params)
    first = host.sample_selected(1_000_000_000, 1, 10.0, self.cp)
    document['selectedDecelerationProfile'] = 'sport'
    self.params.put('LongitudinalPersonalityProfiles', document, block=True)
    self.assertEqual(host.sample_selected(1_010_000_000, 1, 10.0, self.cp), first)
    self.assertEqual(host.sample_selected(2_000_000_000, 1, 10.0, self.cp).cruise_brake_magnitude, 2.4)
    invalid = json.dumps({**document, 'selectedAccelerationProfile': []})
    saved_path = Path(self.params.get_param_path('LongitudinalPersonalityProfiles'))
    saved_path.write_text(invalid)
    self.assertIsNone(host.sample_selected(3_000_000_000, 1, 10.0, self.cp))
    self.assertEqual(saved_path.read_bytes(), invalid.encode())
    self.assertNotEqual(raw, self.params.get('LongitudinalPersonalityProfiles'))

  def test_shared_ui_edits_preserve_global_and_personality_owners(self):
    from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
    from openpilot.starpilot.ui.long_profile_feature import preset_label
    document = self.document('eco', 'sport')
    document['profiles']['aggressive']['acceleration'] = {'preset': 'sport', 'curve': [0.7] * 10}
    owner = FeatureSettingsOwner.__new__(FeatureSettingsOwner)
    self.enterContext(patch.object(owner, '_document', lambda: (document, json.dumps(document).encode(), True), create=True))
    owner.vehicle_fingerprint = lambda: str(self.cp.carFingerprint)
    owner.vehicle_params = lambda: self.cp
    self.enterContext(patch.object(owner, '_value', lambda key: ('0', None, True), create=True))
    encoded = owner._edit_document('profile:global_acceleration', preset_label('standard'))
    changed = migrate_profile_document(encoded)
    assert changed is not None
    self.assertEqual(changed['selectedAccelerationProfile'], 'standard')
    self.assertEqual(changed['selectedDecelerationProfile'], 'sport')
    self.assertEqual(changed['profiles'], document['profiles'])
    encoded = owner._edit_document('profile:standard:acceleration', 'Sport')
    changed = migrate_profile_document(encoded)
    assert changed is not None
    self.assertEqual(changed['selectedAccelerationProfile'], 'eco')
    self.assertEqual(changed['selectedDecelerationProfile'], 'sport')
    self.assertEqual(changed['profiles']['standard']['acceleration']['preset'], 'sport')

  def run_planner(self, selected=None, *, lead=False, force=False, traffic=False):
    planner = LongitudinalPlanner(self.cp, init_v=V_EGO)
    with patch.object(planner_module, 'get_cruise_accel', wraps=planner_module.get_cruise_accel) as cruise:
      for _ in range(65):
        sm, _ = messages(lead=lead, force=force)
        sm['carState'].vCruise = 50.0
        sm['carControl'].longActive = True
        traffic_tuning = resolve(read_traffic_settings(self.params), log.LongitudinalPersonality.standard,
                                 V_EGO, self.cp, traffic_mode=True) if traffic else None
        update_curve_frame(planner, sm, self.cp, 1_000_000_000, selected_profiles=selected,
                           profile_tuning=traffic_tuning, traffic_mode=traffic)
        self.assertEqual(planner.mpc.solution_status, 0)
    planner.test_cruise_profile = cruise.call_args.args[9]
    return planner, cruise.call_args.kwargs

  def test_actual_planner_uses_one_selected_brake_response_without_enabling_follow_or_jerks(self):
    ordinary, _ = self.run_planner()
    normal, _ = self.run_planner(SelectedProfileTuning(cruise_brake_magnitude=1.2))
    comfort, cruise = self.run_planner(SelectedProfileTuning(cruise_brake_magnitude=0.6, braking_style='eco'))
    sport, _ = self.run_planner(SelectedProfileTuning(cruise_brake_magnitude=2.4, braking_style='sport'))
    # The upstream native floor is independent of StarPilot's named presets.
    self.assertAlmostEqual(ordinary.a_cruise, -0.5)
    self.assertAlmostEqual(normal.a_cruise, -1.2)
    self.assertAlmostEqual(comfort.a_cruise, -0.6)
    self.assertAlmostEqual(sport.a_cruise, -2.4)
    self.assertGreater(comfort.a_cruise, normal.a_cruise)
    self.assertLess(sport.a_cruise, normal.a_cruise)
    self.assertEqual(cruise['base_brake_floor'], -0.6)
    for planner in (ordinary, normal, comfort, sport):
      self.assertEqual(planner.output_a_target, planner.a_cruise)
      self.assertEqual(str(planner.mpc.source), '0')
      self.assertFalse(planner.output_should_stop)
      self.assertIsNone(planner.last_profile)
      self.assertEqual(planner.mpc._cached_cost_weights, ordinary.mpc._cached_cost_weights)
      self.assertEqual(planner.mpc.params[:, [0, 1, 4, 5]].tolist(),
                       ordinary.mpc.params[:, [0, 1, 4, 5]].tolist())

  def test_selected_braking_retains_native_hazard_priority(self):
    for kwargs in ({'lead': True}, {'force': True}):
      with self.subTest(kwargs=kwargs):
        native, _ = self.run_planner(**kwargs)
        selected, cruise = self.run_planner(SelectedProfileTuning(cruise_brake_magnitude=2.4), **kwargs)
        self.assertEqual(snapshot(native), snapshot(selected))
        self.assertEqual(cruise['base_brake_floor'], planner_module.A_CRUISE_MIN)

  def test_traffic_explicit_and_inherited_acceleration_share_effective_curve(self):
    document = self.document('sport', 'dom_default')
    inherited = resolve_selected_profiles(document, 1, V_EGO, self.cp, traffic_mode=True)
    document['profiles']['traffic']['acceleration'] = {'preset': 'sport', 'curve': []}
    explicit = resolve_selected_profiles(document, 1, V_EGO, self.cp, traffic_mode=True)
    self.assertEqual(inherited, explicit)
    ordinary, _ = self.run_planner(traffic=True)
    inherited_planner, _ = self.run_planner(inherited, traffic=True)
    explicit_planner, _ = self.run_planner(explicit, traffic=True)
    self.assertEqual(snapshot(inherited_planner), snapshot(explicit_planner))
    self.assertEqual(inherited_planner.mpc.params[0, 1], inherited.acceleration_max)
    self.assertEqual(inherited_planner.test_cruise_profile.acceleration_max, inherited.acceleration_max)
    self.assertEqual(inherited_planner.last_profile.follow_seconds, ordinary.last_profile.follow_seconds)
    self.assertEqual(inherited_planner.last_profile.acceleration_jerk, ordinary.last_profile.acceleration_jerk)

  def test_selected_acceleration_reaches_native_mpc_and_cruise_caps(self):
    planner, _ = self.run_planner(SelectedProfileTuning(acceleration_max=0.2))
    self.assertIsNone(planner.last_profile)
    # The real MPC uses this cap as its physical acceleration limit, without
    # changing personality following-time or jerk weights.
    self.assertAlmostEqual(planner.mpc.params[0, 1], 0.2)
    self.assertLessEqual(planner.a_cruise, 0.2)
    capped = planner_module.get_cruise_accel(False, 35.0, 10.0, 0.2, 0.0, self.cp, 0.05,
                                             0.0, True, selected_acceleration_max=0.2)
    native = planner_module.get_cruise_accel(False, 35.0, 10.0, 0.2, 0.0, self.cp, 0.05, 0.0, True)
    self.assertEqual(capped, 0.2)
    self.assertGreater(native, capped)

  def test_selected_acceleration_above_stock_raises_cruise_ceiling(self):
    v_ego = 10.0
    stock = float(planner_module.get_max_accel(v_ego))
    sport = stock + 0.4
    raised = planner_module.get_cruise_accel(False, 35.0, v_ego, sport, 0.0, self.cp, 0.05,
                                             0.0, True, selected_acceleration_max=sport)
    native = planner_module.get_cruise_accel(False, 35.0, v_ego, stock, 0.0, self.cp, 0.05, 0.0, True)
    self.assertAlmostEqual(raised, sport)
    self.assertAlmostEqual(native, stock)
    e2e = planner_module.get_cruise_accel(True, 35.0, v_ego, sport, 0.0, self.cp, 0.05,
                                          0.0, True, selected_acceleration_max=sport)
    self.assertAlmostEqual(e2e, sport)


if __name__ == '__main__':
  unittest.main()


class GlobalPowertrainPresetTests(unittest.TestCase):
  setUp = SelectedProfilesTests.setUp
  document = SelectedProfilesTests.document

  def test_inherited_ev_override_preserves_personality_custom_braking_and_traffic(self):
    from openpilot.starpilot.longitudinal.profile_runtime import GlobalPowertrainPreset
    document = self.document('sport', 'sport')
    ev = GlobalPowertrainPreset(True, False)
    base = resolve_selected_profiles(document, 1, 20., self.cp)
    selected = resolve_selected_profiles(document, 1, 20., self.cp, global_powertrain=ev)
    self.assertEqual(selected.acceleration_max, interpolate_category_curve('acceleration', 20., {'preset':'sport','curve':[]}, True, False))
    self.assertNotEqual(selected.acceleration_max, base.acceleration_max)
    self.assertEqual(selected.cruise_brake_magnitude, base.cruise_brake_magnitude)
    for config in ({'preset':'sport','curve':[]}, {'preset':'custom','curve':[.4]*10}):
      document['profiles']['standard']['acceleration'] = config
      self.assertEqual(resolve_selected_profiles(document, 1, 20., self.cp, global_powertrain=ev),
                       resolve_selected_profiles(document, 1, 20., self.cp))
    self.assertEqual(resolve_selected_profiles(document, 1, 20., self.cp, traffic_mode=True, global_powertrain=ev),
                     resolve_selected_profiles(document, 1, 20., self.cp, traffic_mode=True))
    default = self.document('dom_default', 'dom_default')
    fallback = resolve_selected_profiles(default, 1, 20., self.cp, global_powertrain=ev)
    self.assertEqual(fallback.acceleration_max, interpolate_category_curve('acceleration',20.,{'preset':'standard','curve':[]},True,False))
    self.assertIsNone(fallback.cruise_brake_magnitude)
    self.assertIsNone(resolve_selected_profiles(default,1,20.,self.cp).acceleration_max)

  def test_actual_saved_ev_choices_truck_priority_and_stock_denial(self):
    from opendbc.car.gm.tests.test_ordinary_camera import params
    from opendbc.car.gm.values import CAR as GmCAR
    from openpilot.starpilot.longitudinal.profile_runtime import read_global_powertrain_preset, GlobalPowertrainPreset
    truck = params(GmCAR.CHEVROLET_SILVERADO, alpha=True)
    before = truck.as_reader().as_builder().to_bytes()
    self.assertIsNone(read_global_powertrain_preset(self.params, truck))
    self.params.put_bool('EVTuning', True, block=True)
    self.assertEqual(read_global_powertrain_preset(self.params, truck), GlobalPowertrainPreset(True, False))
    self.params.put_bool('TruckTuning', True, block=True)
    self.assertEqual(read_global_powertrain_preset(self.params, truck), GlobalPowertrainPreset(False, True))
    self.assertTrue(self.params.get_bool('EVTuning'))
    self.params.put_bool('TruckTuning', False, block=True)
    self.assertEqual(read_global_powertrain_preset(self.params, truck), GlobalPowertrainPreset(True, False))
    self.params.put_bool('EVTuning', False, block=True)
    self.assertEqual(read_global_powertrain_preset(self.params, truck), GlobalPowertrainPreset(False, False))
    stock = params(GmCAR.CHEVROLET_SILVERADO, alpha=True, release=True)
    self.assertIsNone(read_global_powertrain_preset(self.params, stock))
    self.assertIsNone(read_global_powertrain_preset(self.params, self.cp))
    self.assertEqual(truck.as_reader().as_builder().to_bytes(), before)
    path = Path(self.params.get_param_path('EVTuning'))
    path.write_bytes(b'1\n')
    self.assertIsNone(read_global_powertrain_preset(self.params, truck))
    self.assertEqual(path.read_bytes(), b'1\n')

  def test_saved_host_refresh_changes_only_inherited_acceleration(self):
    from opendbc.car.gm.tests.test_ordinary_camera import params
    from opendbc.car.gm.values import CAR as GmCAR
    cp = params(GmCAR.CHEVROLET_SILVERADO, alpha=True)
    doc = self.document('standard','sport')
    self.params.put('LongitudinalPersonalityProfiles', doc, block=True)
    original = Path(self.params.get_param_path('LongitudinalPersonalityProfiles')).read_bytes()
    host = ProfileHost(self.params)
    absent = host.sample_selected(1_000_000_000, 1, 20., cp)
    self.assertEqual(absent, resolve_selected_profiles(doc, 1, 20., cp))
    self.params.put_bool('EVTuning', True, block=True)
    self.assertEqual(host.sample_selected(1_500_000_000, 1, 20., cp), absent)
    ev = host.sample_selected(2_000_000_000, 1, 20., cp)
    self.assertEqual(ev.acceleration_max, interpolate_category_curve('acceleration',20.,{'preset':'standard','curve':[]},True,False))
    self.assertEqual(ev.cruise_brake_magnitude, absent.cruise_brake_magnitude)
    self.params.put_bool('EVTuning', False, block=True)
    ice = host.sample_selected(3_000_000_000, 1, 20., cp)
    self.assertEqual(ice.acceleration_max, interpolate_category_curve('acceleration',20.,{'preset':'standard','curve':[]},False,False))
    self.assertEqual(Path(self.params.get_param_path('LongitudinalPersonalityProfiles')).read_bytes(), original)
    self.assertIsNone(host.sample_selected(-1, 1, 20., cp))


  def test_ev_alone_inherited_standard_gate_requires_valid_document_and_final_long_owner(self):
    from opendbc.car.gm.tests.test_ordinary_camera import params
    from opendbc.car.gm.values import CAR as GmCAR
    from openpilot.starpilot.feature_runtime import enabled
    from openpilot.starpilot.longitudinal.profile_runtime import read_global_powertrain_preset
    cp = params(GmCAR.CHEVROLET_SILVERADO, alpha=True)
    stock = params(GmCAR.CHEVROLET_SILVERADO, alpha=True, release=True)
    doc = self.document('dom_default','dom_default')
    self.params.put('LongitudinalPersonalityProfiles',doc,block=True)
    self.assertFalse(enabled(self.params,cp,'profile',{}))
    self.params.put_bool('EVTuning',False,block=True)
    self.assertTrue(enabled(self.params,cp,'profile',{}))
    selected = ProfileHost(self.params).sample_selected(1_000_000_000,1,20.,cp)
    self.assertEqual(selected.acceleration_max,interpolate_category_curve('acceleration',20.,{'preset':'standard','curve':[]},False,False))
    self.assertIsNone(selected.cruise_brake_magnitude)
    self.assertFalse(enabled(self.params,stock,'profile',{}))
    self.assertFalse(enabled(self.params,self.cp,'profile',{}))
    Path(self.params.get_param_path('LongitudinalPersonalityProfiles')).write_bytes(b'not-json')
    self.assertFalse(enabled(self.params,cp,'profile',{}))
    self.assertIsNone(ProfileHost(self.params).sample_selected(2_000_000_000,1,20.,cp))
    self.params.remove('EVTuning')
    self.params.put_bool('TruckTuning',True,block=True)
    other = params(GmCAR.CHEVROLET_EQUINOX,alpha=True)
    self.assertIsNone(read_global_powertrain_preset(self.params,other))


  def test_absent_ev_uses_final_direct_powertrain_without_boot_write(self):
    from opendbc.car.gm.tests.test_bolt_pedal import params
    from opendbc.car.gm.values import CAR as GmCAR
    from opendbc.car.structs import CarParams
    from openpilot.starpilot.longitudinal.profile_runtime import read_global_powertrain_preset, GlobalPowertrainPreset
    cp = params(GmCAR.CHEVROLET_BOLT_CC_2022_2023,pedal=True,camera=True)
    self.assertEqual(cp.transmissionType,CarParams.TransmissionType.direct)
    self.assertIsNone(read_global_powertrain_preset(self.params,cp))
    self.assertIsNone(self.params.get('EVTuning'))
    doc = self.document('sport','eco')
    self.assertEqual(resolve_selected_profiles(doc,1,20.,cp).acceleration_max,
                     interpolate_category_curve('acceleration',20.,{'preset':'sport','curve':[]},True,False))
    self.params.put_bool('TruckTuning',False,block=True)
    self.assertIsNone(read_global_powertrain_preset(self.params,cp))
    self.assertIsNone(self.params.get('EVTuning'))
    self.params.put_bool('EVTuning',False,block=True)
    self.assertEqual(read_global_powertrain_preset(self.params,cp),GlobalPowertrainPreset(False,False))
