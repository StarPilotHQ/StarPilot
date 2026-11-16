import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'state_migration.py'
spec = importlib.util.spec_from_file_location('isolated_state_migration', SOURCE)
assert spec is not None
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class TestFirstStartMigration(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.namespace = self.root / 'params' / 'd'
    self.namespace.mkdir(parents=True)
    self.storage = self.root / 'recovery'
    self.types = {'IsRhdDetected': 'BOOL', 'LanguageSetting': 'STRING', 'AlphaLongitudinalEnabled': 'BOOL', 'IsMetric': 'BOOL',
                  'ForceStops': 'BOOL', 'OpenpilotEnabledToggle': 'BOOL', 'SteerFriction': 'FLOAT',
                  'CalibrationParams': 'BYTES', 'CarParamsCache': 'BYTES', 'CarParamsPersistent': 'BYTES', 'CarParamsPrevRoute': 'BYTES',
                  'LiveParametersV2': 'BYTES', 'LiveDelay': 'BYTES',
                  'LiveTorqueParameters': 'BYTES', 'SecOCKey': 'BYTES', 'DongleId': 'STRING',
                  'BluetoothEnabled': 'BOOL', 'GsmMetered': 'BOOL', 'HasAcceptedTerms': 'STRING', 'CompletedTrainingVersion': 'STRING'}
    self.params = SimpleNamespace(get_param_path=lambda: str(self.namespace), all_keys=lambda: list(self.types),
                                  get_type=lambda key: SimpleNamespace(name=self.types[key]))
    self.cache = SimpleNamespace(CACHE_KEYS={'CalibrationParams', 'CarParamsCache', 'CarParamsPersistent', 'CarParamsPrevRoute',
                                            'LiveParametersV2', 'LiveDelay', 'LiveTorqueParameters'},
                                 inspect_cache=lambda key, raw: SimpleNamespace(status='valid' if raw.startswith(b'envelope:') else 'legacy'))
    self.converter = SimpleNamespace(migrate_legacy_cache=lambda key, raw: b'envelope:' + raw if key == 'CalibrationParams' else None)
    self.addCleanup(patch.stopall)
    patch.dict('sys.modules', {'openpilot.starpilot.schema_cache': self.cache,
                              'openpilot.starpilot.legacy_cache_migration': self.converter}).start()

  def write(self, values):
    for key, value in values.items():
      (self.namespace / key).write_bytes(value)

  def values(self):
    return migration._read_namespace(self.namespace)

  def start(self, **kwargs):
    migration.prepare_manager_start(self.params, self.storage, auto_migrate=True, **kwargs)

  def test_dashboard_history_survives_settings_reset_outside_params(self):
    saved = {'GalaxyDashboardStats': {'version': 1, 'routes': {'old': {'distanceMeters': 1500, 'duration': 60,
                                        'engagedSeconds': 30, 'date': '2026-10-01T12:00:00+00:00',
                                        'model': 'Original Model', 'segmentCount': 1, 'analysisComplete': True}}},
             'StarPilotStats': {'StarPilotMeters': 5000, 'StarPilotDrives': 3},
             'ApiCache_DriveStats': {'all': {'distance': 10, 'routes': 5, 'minutes': 30}}}
    values = {key: json.dumps(value).encode() for key, value in saved.items()}
    self.write(values | {'ForceStops': b'1'})
    self.start()
    history = self.storage / 'galaxy' / 'legacy-dashboard-stats.json'
    self.assertEqual(json.loads(history.read_bytes()), {'version': 1, 'params': saved})
    self.assertEqual(history.stat().st_mode & 0o777, 0o600)
    self.assertFalse(set(values) & self.values().keys())
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), values | {'ForceStops': b'1'})
    before = history.read_bytes()
    self.start()
    self.assertEqual(history.read_bytes(), before)
    from openpilot.starpilot.galaxy.drive_stats import DriveStatsOwner
    owner = DriveStatsOwner(root=self.root / 'logs', store=self.storage / 'galaxy' / 'drive-stats.json',
                            permitted=lambda: False)
    try:
      dashboard = owner.snapshot()
      self.assertAlmostEqual(dashboard['totals']['distanceMeters'], 16093.44)
      self.assertEqual(dashboard['totals']['drives'], 5)
      self.assertEqual(dashboard['models'][0]['name'], 'Original Model')
      self.assertEqual(dashboard['models'][0]['drives'], 1)
    finally:
      owner.close()

  def test_epoch_two_archives_settings_and_calibration_retains_only_operational_state(self):
    original = {'IsMetric': b'1', 'ForceStops': b'0', 'SteerFriction': b'0.123',
                'SecOCKey': b'private\x00bytes', 'CalibrationParams': b'legacy calibration',
                'CarParamsPersistent': b'old identity', 'OldDomSetting': b'9',
                'BluetoothEnabled': b'1', 'DongleId': b'operational-identity', 'HasAcceptedTerms': b'accepted'}
    self.write(original)
    self.start()
    expected = {key: value for key, value in original.items() if key in migration.OPERATIONAL_KEYS}
    self.assertEqual(self.values(), expected)
    snapshots = list((self.storage / 'snapshots').iterdir())
    self.assertEqual(len(snapshots), 1)
    self.assertEqual(migration.load_snapshot(snapshots[0]), original)
    receipt = json.loads((snapshots[0] / 'migration.json').read_bytes())
    self.assertEqual(receipt['status'], 'migrated')
    before = {str(path): path.read_bytes() for path in self.storage.rglob('*') if path.is_file()}
    self.start()
    self.assertEqual({str(path): path.read_bytes() for path in self.storage.rglob('*') if path.is_file()}, before)

  def test_vehicle_preferences_keep_canonical_choices_and_new_settings_survive_restart(self):
    self.types.update(dict.fromkeys(migration.VEHICLE_PREFERENCE_KEYS, 'BOOL'))
    original = {key: b'1' if index % 2 else b'0' for index, key in enumerate(sorted(migration.VEHICLE_PREFERENCE_KEYS))}
    original.update({'AlphaLongitudinalEnabled': b'1', 'CarParamsCache': b'old fingerprint',
                     'CarParamsPersistent': b'old safety configuration', 'CarParamsPrevRoute': b'old route', 'SteerFriction': b'0.337'})
    self.write(original)
    self.start()
    expected = {key: raw for key, raw in original.items() if key in migration.OPERATIONAL_KEYS}
    self.assertEqual(self.values(), expected)
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), original)
    for key in migration.VEHICLE_PREFERENCE_KEYS:
      (self.namespace / key).write_bytes(b'0' if expected[key] == b'1' else b'1')
    changed = self.values()
    self.start()
    self.assertEqual(self.values(), changed)

  def test_invalid_vehicle_preferences_are_archived_without_enabling_them(self):
    self.types.update(dict.fromkeys(migration.VEHICLE_PREFERENCE_KEYS, 'BOOL'))
    original = dict.fromkeys(migration.VEHICLE_PREFERENCE_KEYS, b'true')
    self.write(original)
    self.start()
    self.assertEqual(self.values(), {})
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), original)

  def test_vehicle_preference_keys_are_registered_booleans(self):
    from openpilot.common.params import Params
    params = Params(str(self.root / 'native-params'))
    for key in migration.VEHICLE_PREFERENCE_KEYS:
      self.assertEqual(params.get_type(key).name, 'BOOL', key)

  def test_regional_choices_survive_migration_and_later_changes(self):
    original = {'IsMetric': b'1', 'IsRhdDetected': b'1', 'LanguageSetting': b'main_ko',
                'SteerFriction': b'0.337', 'CarParamsPersistent': b'old fingerprint'}
    self.write(original)
    self.start()
    self.assertEqual(self.values(), {key: original[key] for key in ('IsMetric', 'IsRhdDetected', 'LanguageSetting')})
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), original)
    self.write({'IsMetric': b'0', 'IsRhdDetected': b'0', 'LanguageSetting': b'fr'})
    changed = self.values()
    self.start()
    self.assertEqual(self.values(), changed)

  def test_every_supported_current_and_legacy_language_is_retained(self):
    catalog = json.loads((SOURCE.parents[1] / 'selfdrive/ui/translations/languages.json').read_text())
    for code in catalog.values():
      for prefix in ('', 'main_'):
        with self.subTest(language=prefix + code), tempfile.TemporaryDirectory() as directory:
          namespace = Path(directory) / 'params' / 'd'
          namespace.mkdir(parents=True)
          params = SimpleNamespace(get_param_path=lambda namespace=namespace: str(namespace), all_keys=self.params.all_keys, get_type=self.params.get_type)
          raw = (prefix + code).encode()
          (namespace / 'LanguageSetting').write_bytes(raw)
          migration.prepare_manager_start(params, Path(directory) / 'state', auto_migrate=True)
          self.assertEqual((namespace / 'LanguageSetting').read_bytes(), raw)

  def test_invalid_regional_values_are_archived(self):
    for language in (b'', b'\xff', b'unknown', b'main_', b'e' * 33):
      with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
        namespace = Path(directory) / 'params' / 'd'
        namespace.mkdir(parents=True)
        params = SimpleNamespace(get_param_path=lambda namespace=namespace: str(namespace), all_keys=self.params.all_keys, get_type=self.params.get_type)
        original = {'IsMetric': b'true', 'IsRhdDetected': b'2', 'LanguageSetting': language}
        for key, raw in original.items():
          (namespace / key).write_bytes(raw)
        storage = Path(directory) / 'state'
        migration.prepare_manager_start(params, storage, auto_migrate=True)
        self.assertEqual(migration._read_namespace(namespace), {})
        self.assertEqual(migration.load_snapshot(next((storage / 'snapshots').iterdir())), original)

  def test_legacy_explicit_wheel_side_wins_only_with_valid_override(self):
    for override in (b'0', b'1', b'true'):
      for side in (b'0', b'1', b'invalid'):
        with self.subTest(override=override, side=side), tempfile.TemporaryDirectory() as directory:
          namespace = Path(directory) / 'params' / 'd'
          namespace.mkdir(parents=True)
          params = SimpleNamespace(get_param_path=lambda namespace=namespace: str(namespace), all_keys=self.params.all_keys, get_type=self.params.get_type)
          original = {'IsRhdDetected': b'1', 'IsRHDOverride': override, 'IsRHD': side}
          for key, raw in original.items():
            (namespace / key).write_bytes(raw)
          migration.prepare_manager_start(params, Path(directory) / 'state', auto_migrate=True)
          expected = side if override == b'1' and side in (b'0', b'1') else b'1'
          self.assertEqual(migration._read_namespace(namespace), {'IsRhdDetected': expected})

  def test_regional_defaults_are_not_invented_for_a_fresh_install(self):
    self.start()
    self.assertEqual(self.values(), {})

  def test_onboarding_keeps_only_previously_saved_acceptance(self):
    self.write({'HasAcceptedTerms': b'2', 'CompletedTrainingVersion': b'0.2.0', 'ForceStops': b'1'})
    self.start()
    self.assertEqual(self.values(), {'HasAcceptedTerms': b'2', 'CompletedTrainingVersion': b'0.2.0'})
    self.start()
    self.assertEqual(self.values(), {'HasAcceptedTerms': b'2', 'CompletedTrainingVersion': b'0.2.0'})

  def test_fresh_install_does_not_fabricate_onboarding_acceptance(self):
    self.start()
    self.assertNotIn('HasAcceptedTerms', self.values())
    self.assertNotIn('CompletedTrainingVersion', self.values())

  def test_security_key_library_survives_preference_reset_and_restart(self):
    self.types['SecOCKeys'] = 'STRING'
    library = b'[{"name":"family car","value":"abababababababababababababababab"}]'
    original = {'SecOCKey': b'ab' * 16, 'SecOCKeys': library, 'SteerFriction': b'0.2'}
    self.write(original)
    self.start()
    self.assertEqual(self.values(), {'SecOCKey': original['SecOCKey'], 'SecOCKeys': library})
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), original)
    self.start()
    self.assertEqual(self.values()['SecOCKeys'], library)

  def test_alpha_longitudinal_preserves_both_values_and_later_changes(self):
    for value in (b'0', b'1'):
      with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
        namespace = Path(directory) / 'params' / 'd'
        namespace.mkdir(parents=True)
        storage = Path(directory) / 'recovery'
        params = SimpleNamespace(get_param_path=lambda namespace=namespace: str(namespace), all_keys=self.params.all_keys, get_type=self.params.get_type)
        (namespace / 'AlphaLongitudinalEnabled').write_bytes(value)
        migration.prepare_manager_start(params, storage, auto_migrate=True)
        self.assertEqual((namespace / 'AlphaLongitudinalEnabled').read_bytes(), value)
        changed = b'1' if value == b'0' else b'0'
        (namespace / 'AlphaLongitudinalEnabled').write_bytes(changed)
        migration.prepare_manager_start(params, storage, auto_migrate=True)
        self.assertEqual((namespace / 'AlphaLongitudinalEnabled').read_bytes(), changed)

  def test_legacy_longitudinal_choice_requires_canonical_value_and_never_overrides_alpha(self):
    for legacy in (b'0', b'1', b'true', b'', b'2'):
      for alpha in (None, b'0', b'1', b'invalid'):
        with self.subTest(legacy=legacy, alpha=alpha), tempfile.TemporaryDirectory() as directory:
          namespace = Path(directory) / 'params' / 'd'
          namespace.mkdir(parents=True)
          storage = Path(directory) / 'recovery'
          params = SimpleNamespace(get_param_path=lambda namespace=namespace: str(namespace), all_keys=self.params.all_keys, get_type=self.params.get_type)
          (namespace / 'ExperimentalLongitudinalEnabled').write_bytes(legacy)
          if alpha is not None:
            (namespace / 'AlphaLongitudinalEnabled').write_bytes(alpha)
          original = migration._read_namespace(namespace)
          migration.prepare_manager_start(params, storage, auto_migrate=True)
          expected = alpha if alpha in (b'0', b'1') else legacy if alpha is None and legacy in (b'0', b'1') else None
          path = namespace / 'AlphaLongitudinalEnabled'
          self.assertEqual(path.read_bytes() if path.exists() else None, expected)
          self.assertFalse((namespace / 'ExperimentalLongitudinalEnabled').exists())
          self.assertIn(original, [migration.load_snapshot(path) for path in (storage / 'snapshots').iterdir()])

  def test_invalid_operational_bool_archived_all_old_preferences_removed(self):
    original = {'IsMetric': b'True', 'ForceStops': b'1', 'OpenpilotEnabledToggle': b'true',
                'BluetoothEnabled': b'true', 'GsmMetered': b'1'}
    self.write(original)
    self.start()
    self.assertEqual(self.values(), {'GsmMetered': b'1'})
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), original)

  def test_fresh_driving_settings_leave_galaxy_tunnel_pairing_untouched(self):
    from openpilot.starpilot.galaxy.remote import RemotePairing
    pairing = RemotePairing(self.storage / 'galaxy')
    assert pairing.pair('a' * 64) is not None
    path = pairing.root / pairing.FILE
    saved = path.read_bytes()
    self.write({'CalibrationParams': b'old calibration', 'SteerFriction': b'0.2', 'DongleId': b'identity'})
    self.start()
    self.assertEqual(self.values(), {'DongleId': b'identity'})
    self.assertEqual(path.read_bytes(), saved)
    assert pairing.read() is not None

  def test_default_and_dry_run_remain_strict_without_mutation(self):
    self.write({'SteerFriction': b'0.2'})
    with self.assertRaises(migration.MigrationRequired):
      self.start(dry_run=True)
    self.assertFalse(self.storage.exists())
    with self.assertRaises(migration.MigrationRequired):
      migration.prepare_manager_start(self.params, self.storage)
    self.assertEqual(self.values(), {'SteerFriction': b'0.2'})

  def test_archive_readback_failure_preserves_all_bytes_and_does_not_qualify(self):
    original = {'CalibrationParams': b'bad calibration', 'OldDomSetting': b'1'}
    self.write(original)
    with patch.object(migration, 'load_snapshot', return_value={}):
      with self.assertRaises(migration.MigrationRequired):
        self.start()
    self.assertEqual(self.values(), original)
    self.assertEqual(list((self.storage / 'profiles').iterdir()), [])
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), original)

  def test_partial_failure_snapshot_survives_retry_and_marker_is_last(self):
    original = {'CarParamsPersistent': b'old car', 'OldDomSetting': b'2', 'SecOCKey': b'credential'}
    self.write(original)
    actual_write = migration._atomic_write
    def fail_receipt(path, raw):
      if path.name == 'migration.json' and json.loads(raw).get('attempted') == ['CarParamsPersistent', 'OldDomSetting']:
        raise OSError('injected')
      return actual_write(path, raw)
    with patch.object(migration, '_atomic_write', side_effect=fail_receipt):
      with self.assertRaises((OSError, migration.MigrationRequired)):
        self.start()
    self.assertEqual(list((self.storage / 'profiles').iterdir()), [])
    self.assertIn(original, [migration.load_snapshot(path) for path in (self.storage / 'snapshots').iterdir()])
    self.start()
    self.assertEqual(self.values(), {'SecOCKey': b'credential'})
    self.assertIn(original, [migration.load_snapshot(path) for path in (self.storage / 'snapshots').iterdir()])

  def test_initialized_vehicle_cache_contract_upgrade_preserves_every_other_byte(self):
    self.start()
    original = {'CarParamsPersistent': b'legacy car', 'CarParamsPrevRoute': b'legacy route',
                'CalibrationParams': b'envelope:calibration', 'LiveTorqueParameters': b'envelope:learner',
                'IsMetric': b'1', 'ForceStops': b'1', 'SecOCKey': b'private\x00bytes'}
    self.write(original)
    marker = next((self.storage / 'profiles').iterdir())
    marker_bytes = marker.read_bytes()
    with self.assertRaises(migration.MigrationRequired):
      self.start(dry_run=True)
    self.assertEqual(self.values(), original)
    self.start()
    expected = {key: raw for key, raw in original.items() if key not in ('CarParamsPersistent', 'CarParamsPrevRoute')}
    self.assertEqual(self.values(), expected)
    self.assertEqual(marker.read_bytes(), marker_bytes)
    snapshot = next((self.storage / 'snapshots').iterdir())
    self.assertEqual(migration.load_snapshot(snapshot), original)
    self.assertEqual(set(json.loads((snapshot / 'migration.json').read_bytes())['actions']),
                     {'CarParamsPersistent', 'CarParamsPrevRoute'})
    self.start()
    self.assertEqual(self.values(), expected)

  def test_initialized_three_stale_car_caches_preserve_valid_state_and_restart(self):
    self.start()
    original = {'CarParamsCache': b'incompatible schema cache', 'CarParamsPersistent': b'incompatible persistent',
                'CarParamsPrevRoute': b'incompatible previous route', 'CalibrationParams': b'envelope:calibration',
                'LiveParametersV2': b'envelope:vehicle learner', 'LiveDelay': b'envelope:delay',
                'IsMetric': b'1', 'ForceStops': b'1', 'SteerFriction': b'0.123', 'DongleId': b'identity'}
    self.write(original)
    marker = next((self.storage / 'profiles').iterdir())
    marker_bytes = marker.read_bytes()
    self.start()
    retired = {'CarParamsCache', 'CarParamsPersistent', 'CarParamsPrevRoute'}
    expected = {key: value for key, value in original.items() if key not in retired}
    self.assertEqual(self.values(), expected)
    self.assertEqual(marker.read_bytes(), marker_bytes)
    snapshot = next((self.storage / 'snapshots').iterdir())
    self.assertEqual(migration.load_snapshot(snapshot), original)
    self.assertEqual(set(json.loads((snapshot / 'migration.json').read_bytes())['actions']), retired)
    before = {str(path): path.read_bytes() for path in self.storage.rglob('*') if path.is_file()}
    self.start()
    self.assertEqual(self.values(), expected)
    self.assertEqual({str(path): path.read_bytes() for path in self.storage.rglob('*') if path.is_file()}, before)

  def test_initialized_isolated_stale_car_params_cache_is_reconstructible(self):
    self.start()
    original = {'CarParamsCache': b'incompatible cache', 'CalibrationParams': b'envelope:calibration',
                'IsMetric': b'1', 'ForceStops': b'0'}
    self.write(original)
    with self.assertRaises(migration.MigrationRequired):
      self.start(dry_run=True)
    self.assertEqual(self.values(), original)
    self.start()
    self.assertEqual(self.values(), {key: value for key, value in original.items() if key != 'CarParamsCache'})
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), original)

  def test_initialized_retired_setting_is_archived_and_other_state_kept(self):
    self.start()
    original = {'ShareUsageStats': b'1', 'IsMetric': b'1', 'ForceStops': b'0'}
    self.write(original)
    self.start()
    self.assertEqual(self.values(), {'IsMetric': b'1', 'ForceStops': b'0'})
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), original)

  def test_initialized_valid_car_caches_are_retained_without_archive(self):
    self.start()
    original = {key: b'envelope:' + key.encode() for key in self.cache.CACHE_KEYS}
    original.update(IsMetric=b'1', ForceStops=b'1', SteerFriction=b'0.321')
    self.write(original)
    self.start()
    self.start()
    self.assertEqual(self.values(), original)
    self.assertFalse((self.storage / 'snapshots').exists())

  def test_initialized_cache_retirement_receipt_failure_preserves_raw_snapshot_and_all_state(self):
    self.start()
    original = {'CarParamsCache': b'incompatible cache', 'CarParamsPersistent': b'incompatible persistent',
                'CarParamsPrevRoute': b'incompatible route', 'CalibrationParams': b'envelope:calibration',
                'LiveParametersV2': b'envelope:learner', 'LiveDelay': b'envelope:delay', 'ForceStops': b'1'}
    self.write(original)
    marker = next((self.storage / 'profiles').iterdir())
    marker_bytes = marker.read_bytes()
    actual_write = migration._atomic_write
    def fail_receipt(path, raw):
      if path.name == 'migration.json':
        raise OSError('receipt storage failure')
      return actual_write(path, raw)
    with patch.object(migration, '_atomic_write', side_effect=fail_receipt):
      with self.assertRaises(OSError):
        self.start()
    self.assertEqual(self.values(), original)
    self.assertEqual(marker.read_bytes(), marker_bytes)
    snapshot = next((self.storage / 'snapshots').iterdir())
    self.assertEqual(migration.load_snapshot(snapshot), original)

  def test_initialized_critical_cache_corruption_still_rejected(self):
    self.start()
    original = {'CarParamsPersistent': b'legacy car', 'CalibrationParams': b'bad calibration'}
    self.write(original)
    with self.assertRaises(migration.MigrationRequired):
      self.start()
    self.assertEqual(self.values(), original)

  def test_initialized_vehicle_cache_upgrade_does_not_admit_other_invalid_state(self):
    self.start()
    for key, raw in (('OldDomSetting', b'1'), ('IsMetric', b'true'), ('ForceStops', b'true'),
                     ('LiveTorqueParameters', b'bad learner')):
      with self.subTest(key=key):
        original: dict = {'CarParamsPersistent': b'legacy car', key: raw}
        self.write(original)
        with self.assertRaises(migration.MigrationRequired):
          self.start()
        self.assertEqual(self.values(), original)
        for entry in self.namespace.iterdir():
          entry.unlink()

  def test_snapshot_failure_prevents_every_mutation(self):
    original = {'OldDomSetting': b'2', 'IsMetric': b'1'}
    self.write(original)
    with patch.object(migration, '_save_snapshot', side_effect=OSError('storage unavailable')):
      with self.assertRaises(OSError):
        self.start()
    self.assertEqual(self.values(), original)
    self.assertEqual(list((self.storage / 'profiles').iterdir()), [])

  def test_old_epoch_resets_once_then_new_choices_survive(self):
    self.start()
    marker = next((self.storage / 'profiles').iterdir())
    old = json.loads(marker.read_bytes())
    old.update(version=1, schema_epoch=1)
    marker.write_bytes(migration.canonical_json(old))
    self.write({'CalibrationParams': b'envelope:old calibration', 'ForceStops': b'1', 'SecOCKey': b'auth'})
    self.start()
    self.assertEqual(self.values(), {'SecOCKey': b'auth'})
    self.assertEqual(json.loads(marker.read_bytes())['schema_epoch'], 2)
    self.write({'ForceStops': b'1', 'IsMetric': b'1'})
    self.start()
    self.assertEqual(self.values(), {'SecOCKey': b'auth', 'ForceStops': b'1', 'IsMetric': b'1'})

  def test_marker_failure_happens_only_after_verified_archive_and_reset(self):
    original = {'CalibrationParams': b'legacy calibration', 'DongleId': b'identity'}
    self.write(original)
    actual_write = migration._atomic_write
    def fail_marker(path, raw):
      if path.parent.name == 'profiles':
        raise OSError('marker failed')
      return actual_write(path, raw)
    with patch.object(migration, '_atomic_write', side_effect=fail_marker):
      with self.assertRaises(OSError):
        self.start()
    self.assertEqual(self.values(), {'DongleId': b'identity'})
    self.assertEqual(list((self.storage / 'profiles').iterdir()), [])
    self.assertEqual(migration.load_snapshot(next((self.storage / 'snapshots').iterdir())), original)
    self.start()
    self.assertEqual(self.values(), {'DongleId': b'identity'})


if __name__ == '__main__':
  unittest.main()
