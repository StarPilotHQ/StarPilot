import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.common.params import Params, ParamKeyFlag
from opendbc.car.structs import car
from openpilot.starpilot.schema_cache import put_cache
from openpilot.starpilot.state_migration import (
  MigrationRequired, canonical_json, decode_bundle, digest, load_snapshot, prepare_manager_start,
  restore_snapshot, snapshot_settings, stage_settings, validate_bundle,
)


class TestStateMigration(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.params = Params(str(self.root / 'params'))
    self.namespace = Path(self.params.get_param_path())
    self.storage = self.root / 'recovery'

  def tree_bytes(self):
    return {str(path.relative_to(self.root)): (path.is_symlink(), os.readlink(path) if path.is_symlink() else
            path.read_bytes() if path.is_file() else None) for path in self.root.rglob('*')}

  def test_dry_run_rejects_unsafe_and_dangling_profiles_without_mutation(self):
    self.storage.mkdir(mode=0o700)
    profiles = self.storage / 'profiles'
    profiles.symlink_to(self.storage / 'missing')
    before = self.tree_bytes()
    with self.assertRaisesRegex(ValueError, 'private, owned directory'):
      prepare_manager_start(self.params, self.storage, dry_run=True)
    self.assertEqual(self.tree_bytes(), before)
    profiles.unlink()
    profiles.mkdir(mode=0o755)
    before = self.tree_bytes()
    with self.assertRaisesRegex(ValueError, 'private, owned directory'):
      prepare_manager_start(self.params, self.storage, dry_run=True)
    self.assertEqual(self.tree_bytes(), before)

  def test_fresh_dry_run_is_read_only_and_does_not_qualify(self):
    before = self.tree_bytes()
    prepare_manager_start(self.params, self.storage, dry_run=True)
    self.assertEqual(self.tree_bytes(), before)
    put_cache(self.params, 'CarParamsPersistent', car.CarParams.new_message(), block=True)
    before = self.tree_bytes()
    with self.assertRaisesRegex(MigrationRequired, 'unqualified existing namespace'):
      prepare_manager_start(self.params, self.storage, dry_run=True)
    self.assertEqual(self.tree_bytes(), before)
    with self.assertRaises(MigrationRequired):
      prepare_manager_start(self.params, self.storage)

  def test_initialized_dry_run_validates_current_v1_v2_and_preserves_failures(self):
    import struct
    from openpilot.cereal import messaging
    from openpilot.starpilot import schema_cache as cache
    prepare_manager_start(self.params, self.storage)
    message = messaging.new_message('lateralDelay')
    for version in (1, 2):
      payload = message.as_reader().as_builder().to_bytes()
      header = canonical_json(cache._header(cache.CONTRACTS['LiveDelay'], 'LiveDelay', payload, version=version))
      raw = cache.MAGIC + struct.pack('>I', len(header)) + header + payload
      self.params.put('LiveDelay', raw, block=True)
      before = self.tree_bytes()
      prepare_manager_start(self.params, self.storage, dry_run=True)
      self.assertEqual(self.tree_bytes(), before)
    for key, raw, reason in (('LiveDelay', b'historical incompatible', 'incompatible retained cache'),
                             ('UnportedFeature', b'keep this', 'unknown saved keys')):
      self.raw(key, raw)
      before = self.tree_bytes()
      with self.assertRaisesRegex(MigrationRequired, reason):
        prepare_manager_start(self.params, self.storage, dry_run=True)
      self.assertEqual(self.tree_bytes(), before)

  def test_initialized_unknown_keys_retire_without_changing_known_state(self):
    from openpilot.cereal import messaging
    prepare_manager_start(self.params, self.storage)
    known = {'AlwaysOnLateral': b'1', 'IsMetric': b'0', 'OpenpilotEnabledToggle': b'1',
             'DongleId': b'retained-identity', 'BluetoothEnabled': b'1', 'SecOCKey': bytes(range(16))}
    for key, raw in known.items():
      self.raw(key, raw)
    put_cache(self.params, 'CalibrationParams', messaging.new_message('extrinsicsCalibration'), block=True)
    put_cache(self.params, 'CarParamsPersistent', car.CarParams.new_message(), block=True)
    known.update({key: (self.namespace / key).read_bytes() for key in ('CalibrationParams', 'CarParamsPersistent')})
    identities = {key: ((self.namespace / key).stat().st_ino, (self.namespace / key).stat().st_mtime_ns) for key in known}
    retired = {'ShareUsageStats': b'1', 'RetiredUnknownSetting': b'\xff\x00not a bool or JSON'}
    registered = {key.decode() if isinstance(key, bytes) else key for key in self.params.all_keys()}
    self.assertFalse(registered & retired.keys())
    for key, raw in retired.items():
      self.raw(key, raw)
    marker, = (self.storage / 'profiles').iterdir()
    marker_bytes = marker.read_bytes()
    before = dict(known, **retired)
    prepare_manager_start(self.params, self.storage, auto_migrate=True)
    snapshots = tuple((self.storage / 'snapshots').iterdir())
    self.assertEqual(len(snapshots), 2)
    after_retired = {key: raw for key, raw in before.items() if key != 'ShareUsageStats'}
    self.assertCountEqual([load_snapshot(snapshot) for snapshot in snapshots], [before, after_retired])
    self.assertEqual({entry.name: entry.read_bytes() for entry in self.namespace.iterdir()}, known)
    self.assertEqual({key: ((self.namespace / key).stat().st_ino, (self.namespace / key).stat().st_mtime_ns) for key in known}, identities)
    self.assertEqual(marker.read_bytes(), marker_bytes)
    for snapshot in snapshots:
      receipt = json.loads((snapshot / 'migration.json').read_bytes())
      expected_actions = {'ShareUsageStats'} if 'ShareUsageStats' in load_snapshot(snapshot) else {'RetiredUnknownSetting'}
      self.assertEqual(set(receipt['actions']), expected_actions)
      self.assertEqual(receipt['status'], 'migrated')
    prepare_manager_start(self.params, self.storage, dry_run=True)
    prepare_manager_start(self.params, self.storage, auto_migrate=True)
    self.assertEqual(set((self.storage / 'snapshots').iterdir()), set(snapshots))

  def test_initialized_unknown_retirement_fails_before_deletion(self):
    prepare_manager_start(self.params, self.storage)
    self.params.put('AlwaysOnLateral', True, block=True)
    self.raw('ShareUsageStats', b'1')
    before = self.tree_bytes()
    with patch('openpilot.starpilot.state_migration._write', side_effect=OSError('disk full')):
      with self.assertRaises(OSError):
        prepare_manager_start(self.params, self.storage, auto_migrate=True)
    self.assertEqual((self.namespace / 'ShareUsageStats').read_bytes(), b'1')
    self.assertEqual((self.namespace / 'AlwaysOnLateral').read_bytes(), b'1')
    self.assertEqual({key: value for key, value in self.tree_bytes().items() if value[0] or value[1] is not None},
                     {key: value for key, value in before.items() if value[0] or value[1] is not None})
    self.assertEqual(list((self.storage / 'snapshots').iterdir()), [])

  def test_initialized_unknown_keys_do_not_admit_invalid_known_state(self):
    prepare_manager_start(self.params, self.storage)
    self.raw('UnportedFeature', b'1')
    for key, raw in (('AlwaysOnLateral', b'true'), ('CalibrationParams', b'invalid retained calibration')):
      with self.subTest(key=key):
        self.raw(key, raw)
        before = {entry.name: entry.read_bytes() for entry in self.namespace.iterdir()}
        with self.assertRaises(MigrationRequired):
          prepare_manager_start(self.params, self.storage, auto_migrate=True)
        self.assertEqual({entry.name: entry.read_bytes() for entry in self.namespace.iterdir()}, before)
        (self.namespace / key).unlink()

  def test_retired_key_archival_does_not_admit_invalid_known_state(self):
    prepare_manager_start(self.params, self.storage)
    for key, raw in (('AlwaysOnLateral', b'true'), ('CalibrationParams', b'invalid retained calibration')):
      with self.subTest(key=key):
        self.raw('ShareUsageStats', b'1')
        self.raw(key, raw)
        before = {entry.name: entry.read_bytes() for entry in self.namespace.iterdir()}
        before_tree = self.tree_bytes()
        with self.assertRaises(MigrationRequired):
          prepare_manager_start(self.params, self.storage, dry_run=True, auto_migrate=True)
        self.assertEqual(self.tree_bytes(), before_tree)
        snapshots_before = set((self.storage / 'snapshots').iterdir()) if (self.storage / 'snapshots').exists() else set()
        identity = ((self.namespace / key).stat().st_ino, (self.namespace / key).stat().st_mtime_ns)
        with self.assertRaises(MigrationRequired):
          prepare_manager_start(self.params, self.storage, auto_migrate=True)
        retained = {name: value for name, value in before.items() if name != 'ShareUsageStats'}
        self.assertEqual({entry.name: entry.read_bytes() for entry in self.namespace.iterdir()}, retained)
        self.assertEqual(((self.namespace / key).stat().st_ino, (self.namespace / key).stat().st_mtime_ns), identity)
        snapshots = set((self.storage / 'snapshots').iterdir()) - snapshots_before
        self.assertEqual(len(snapshots), 2)
        self.assertCountEqual([load_snapshot(snapshot) for snapshot in snapshots], [before, retained])
        archived = next(snapshot for snapshot in snapshots if 'ShareUsageStats' in load_snapshot(snapshot))
        receipt = json.loads((archived / 'migration.json').read_bytes())
        self.assertEqual(set(receipt['actions']), {'ShareUsageStats'})
        self.assertEqual(receipt['status'], 'migrated')
        (self.namespace / key).unlink()

  def raw(self, key, value):
    (self.namespace / key).write_bytes(value)

  def bundle(self):
    raw = b'42'
    return {
      'format': 'starpilot-settings', 'version': 1,
      'source': {'revision': 'a' * 40, 'registry_sha256': 'b' * 64, 'snapshot_sha256': 'c' * 64},
      'preferences': {'AlwaysOnLateral': True, 'OpenpilotEnabledToggle': False},
      'pending': [{'key': 'SavedSetting', 'type': 'INT', 'raw_base64': base64.b64encode(raw).decode(), 'sha256': digest(raw)}],
      'omitted': [{'key': 'SecOCKey', 'reason': 'requires credential recovery policy'}],
    }

  def test_raw_roundtrip_preserves_unknown_empty_invalid_and_credentials(self):
    values = {'UnportedFeature': b'7', 'IsMetric': b'', 'AlwaysOnLateral': b'invalid', 'SecOCKey': b'private\0bytes'}
    for key, value in values.items():
      self.raw(key, value)
    snapshot = snapshot_settings(self.namespace, self.storage)
    self.assertEqual(load_snapshot(snapshot), values)
    self.assertEqual(snapshot_settings(self.namespace, self.storage), snapshot)
    restored = restore_snapshot(snapshot, self.root / 'restored' / self.namespace.name)
    recovered_params = Params(str(restored.parent))
    self.assertEqual({entry.name: entry.read_bytes() for entry in restored.iterdir()}, values)
    self.assertEqual(recovered_params.get('SecOCKey'), 'private\0bytes')
    self.assertEqual(snapshot.stat().st_mode & 0o777, 0o700)
    for entry in (snapshot / 'values').iterdir():
      self.assertEqual(entry.stat().st_mode & 0o777, 0o600)

  def test_snapshot_digest_matches_export_contract(self):
    self.raw('IsMetric', b'1')
    expected = [{'key': 'IsMetric', 'sha256': digest(b'1'), 'size': 1}]
    self.assertEqual(snapshot_settings(self.namespace, self.storage).name, digest(canonical_json(expected)))

  def test_restore_refuses_live_namespace(self):
    self.raw('IsMetric', b'1')
    snapshot = snapshot_settings(self.namespace, self.storage)
    with self.assertRaises(FileExistsError):
      restore_snapshot(snapshot, self.namespace)
    self.assertEqual(self.params.get('IsMetric'), True)

  def test_tampered_archive_cannot_restore(self):
    self.raw('IsMetric', b'1')
    snapshot = snapshot_settings(self.namespace, self.storage)
    (snapshot / 'values' / 'IsMetric').write_bytes(b'0')
    destination = self.root / 'restored' / 'd'
    with self.assertRaises(ValueError):
      restore_snapshot(snapshot, destination)
    self.assertFalse(destination.exists())

  def test_symlink_and_directory_keys_are_rejected_without_reading_targets(self):
    target = self.root / 'unrelated'
    target.write_bytes(b'private')
    (self.namespace / 'BadKey').symlink_to(target)
    with self.assertRaises(OSError):
      snapshot_settings(self.namespace, self.storage)
    (self.namespace / 'BadKey').unlink()
    (self.namespace / 'BadKey').mkdir()
    with self.assertRaises((ValueError, IsADirectoryError)):
      snapshot_settings(self.namespace, self.storage)
    self.assertEqual(target.read_bytes(), b'private')

  def test_recovery_storage_cannot_be_inside_params(self):
    with self.assertRaises(ValueError):
      snapshot_settings(self.namespace, self.namespace / 'backup')
    with self.assertRaises(ValueError):
      snapshot_settings(self.namespace, self.namespace.parent / 'backup')
    with self.assertRaises(ValueError):
      stage_settings(self.bundle(), self.namespace, self.namespace)
    self.assertFalse((self.namespace / 'staged').exists())

  def test_failed_archive_write_leaves_source_and_no_complete_snapshot(self):
    self.raw('UnportedFeature', b'keep me')
    with patch('openpilot.starpilot.state_migration._write', side_effect=OSError('disk full')):
      with self.assertRaises(OSError):
        prepare_manager_start(self.params, self.storage)
    self.assertEqual((self.namespace / 'UnportedFeature').read_bytes(), b'keep me')
    self.assertEqual(list((self.storage / 'snapshots').iterdir()), [])
    self.assertEqual(list((self.storage / 'profiles').iterdir()), [])

  def test_global_default_is_on_and_saved_true_false_survive_native_defaults(self):
    self.assertTrue(self.params.get_default_value('AlwaysOnLateral'))
    for value in (None, False, True):
      with self.subTest(value=value):
        if value is None:
          self.params.remove('AlwaysOnLateral')
        else:
          self.params.put_bool('AlwaysOnLateral', value, block=True)
        prepare_manager_start(self.params, self.storage)
        self.params.clear_all(ParamKeyFlag.CLEAR_ON_MANAGER_START)
        if self.params.get('AlwaysOnLateral') is None:
          self.params.put('AlwaysOnLateral', self.params.get_default_value('AlwaysOnLateral'), block=True)
        self.assertEqual(self.params.get_bool('AlwaysOnLateral'), value is not False)

  def test_unknown_key_archived_before_cleanup_can_run(self):
    self.raw('UnportedFeature', b'19')
    self.params.put_bool('AlwaysOnLateral', True, block=True)
    with self.assertRaises(MigrationRequired):
      prepare_manager_start(self.params, self.storage)
      self.params.clear_all(ParamKeyFlag.CLEAR_ON_MANAGER_START)
    self.assertEqual((self.namespace / 'UnportedFeature').read_bytes(), b'19')
    self.assertTrue(self.params.get_bool('AlwaysOnLateral'))
    snapshot, = (self.storage / 'snapshots').iterdir()
    self.assertEqual(load_snapshot(snapshot)['UnportedFeature'], b'19')

  def test_known_schema_cache_also_requires_migration_on_first_start(self):
    self.params.put('CarParamsPersistent', b'old schema', block=True)
    with self.assertRaises(MigrationRequired):
      prepare_manager_start(self.params, self.storage)
    self.assertEqual(self.params.get('CarParamsPersistent'), b'old schema')

  def test_existing_profile_can_reuse_its_own_runtime_state(self):
    prepare_manager_start(self.params, self.storage)
    put_cache(self.params, 'CarParamsPersistent', car.CarParams.new_message(), block=True)
    prepare_manager_start(self.params, self.storage)
    self.raw('UnportedFeature', b'from another runtime')
    with self.assertRaises(MigrationRequired):
      prepare_manager_start(self.params, self.storage)

  def test_marker_does_not_authorize_unversioned_cache_in_same_directory(self):
    prepare_manager_start(self.params, self.storage)
    self.params.put('CarParamsPersistent', car.CarParams.new_message().to_bytes(), block=True)
    with self.assertRaises(MigrationRequired):
      prepare_manager_start(self.params, self.storage)
    snapshot, = (self.storage / 'snapshots').iterdir()
    self.assertEqual(load_snapshot(snapshot)['CarParamsPersistent'], self.params.get('CarParamsPersistent'))

  def test_marker_cannot_authorize_swapped_namespace(self):
    prepare_manager_start(self.params, self.storage)
    other = self.root / 'other-data'
    other.mkdir()
    (other / 'CarParamsPersistent').write_bytes(b'unqualified schema')
    self.namespace.unlink()
    self.namespace.symlink_to(other)
    with self.assertRaises(MigrationRequired):
      prepare_manager_start(self.params, self.storage)

  def test_corrupt_marker_still_archives_state_and_explains_migration(self):
    prepare_manager_start(self.params, self.storage)
    marker, = (self.storage / 'profiles').iterdir()
    self.raw('UnportedFeature', b'recover me')
    marker.write_bytes(b'{"version":')
    with self.assertRaisesRegex(MigrationRequired, 'raw recovery snapshot'):
      prepare_manager_start(self.params, self.storage)
    snapshot, = (self.storage / 'snapshots').iterdir()
    self.assertEqual(load_snapshot(snapshot)['UnportedFeature'], b'recover me')
    self.assertEqual((self.namespace / 'UnportedFeature').read_bytes(), b'recover me')

  def test_noncanonical_marker_cannot_qualify_existing_state(self):
    prepare_manager_start(self.params, self.storage)
    marker, = (self.storage / 'profiles').iterdir()
    valid = marker.read_bytes()
    put_cache(self.params, 'CarParamsPersistent', car.CarParams.new_message(), block=True)
    for replacement in (b'"version":true', b'"version":2,"version":2'):
      marker.write_bytes(valid.replace(b'"version":2', replacement))
      with self.assertRaises(MigrationRequired):
        prepare_manager_start(self.params, self.storage)

  def test_noncanonical_boolean_requires_migration(self):
    for value in (b'', b'false', b'2'):
      with self.subTest(value=value):
        self.raw('AlwaysOnLateral', value)
        with self.assertRaises(MigrationRequired):
          prepare_manager_start(self.params, self.storage)
        self.assertEqual((self.namespace / 'AlwaysOnLateral').read_bytes(), value)

  def test_staging_never_activates_preferences_pending_or_credentials(self):
    bundle = self.bundle()
    destination = stage_settings(bundle, self.storage, self.namespace)
    self.assertEqual(json.loads(destination.read_bytes()), bundle)
    self.assertEqual(list(self.namespace.iterdir()), [])
    self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
    self.raw('SavedSetting', b'42')
    with self.assertRaises(MigrationRequired):
      prepare_manager_start(self.params, self.storage)

  def test_cache_provenance_is_validated(self):
    bundle = self.bundle()
    bundle['source']['cache_snapshot_sha256'] = 'd' * 64
    validate_bundle(bundle)
    bundle['source']['cache_snapshot_sha256'] = 'unknown'
    with self.assertRaises(ValueError):
      validate_bundle(bundle)

  def test_serialized_bundle_rejects_duplicate_members(self):
    bundle = self.bundle()
    encoded = canonical_json(bundle)
    self.assertEqual(decode_bundle(encoded), bundle)
    for key, value in [('version', '1'), ('AlwaysOnLateral', 'true')]:
      needle = f'"{key}":{value}'.encode()
      duplicate = encoded.replace(needle, needle + b',' + needle)
      self.assertNotEqual(encoded, duplicate)
      with self.assertRaises(ValueError):
        decode_bundle(duplicate)

  def test_bundle_rejects_unreviewed_preferences_and_integer_boolean(self):
    for key, value in [('NewControlFlag', True), ('AlwaysOnLateral', 1)]:
      bundle = self.bundle()
      bundle['preferences'][key] = value
      with self.assertRaises(ValueError):
        stage_settings(bundle, self.storage, self.namespace)
    self.assertFalse((self.storage / 'staged').exists())

  def test_bundle_rejects_duplicate_disposition_bad_hash_and_path(self):
    for field, value in [('key', 'AlwaysOnLateral'), ('sha256', '0' * 64), ('key', '../outside'), ('raw_base64', '***')]:
      bundle = self.bundle()
      bundle['pending'][0][field] = value
      with self.assertRaises(ValueError):
        stage_settings(bundle, self.storage, self.namespace)

  def test_manager_guard_precedes_bootlog_cleanup_and_registration(self):
    from openpilot.system.manager import manager
    self.raw('UnportedFeature', b'preserved')
    with patch.object(manager, 'Params', return_value=self.params), \
         patch.object(manager, 'starpilot_storage_root', return_value=self.storage), \
         patch.object(manager, 'save_bootlog') as bootlog, \
         patch.object(manager, 'register') as register, \
         patch.object(manager, 'prepare_manager_start', side_effect=MigrationRequired('fixture incompatible state')), \
         patch.object(self.params, 'clear_all') as clear:
      with self.assertRaises(MigrationRequired):
        manager.manager_init()
    bootlog.assert_not_called()
    clear.assert_not_called()
    register.assert_not_called()
    self.assertEqual((self.namespace / 'UnportedFeature').read_bytes(), b'preserved')

  def test_native_manager_defaults_preserve_driver_choices(self):
    from openpilot.system.manager import manager
    metadata = SimpleNamespace(
      release_channel=False, tested_channel=False, channel='Domathon',
      openpilot=SimpleNamespace(version='test', git_commit='a' * 40, git_commit_date='test',
                               git_origin='local', git_normalized_origin='local', is_dirty=True),
    )
    prepare_manager_start(self.params, self.storage)  # Current epoch before newly saved driver choice.
    self.params.put_bool('OpenpilotEnabledToggle', False, block=True)
    self.raw('AndroidAutoEnabled', b'1')
    with patch.dict(os.environ), \
         patch.object(manager, 'Params', return_value=self.params), \
         patch.object(manager, 'starpilot_storage_root', return_value=self.storage), \
         patch.object(manager.Paths, 'shm_path', return_value=str(self.root / 'shm')), \
         patch.object(manager, 'get_build_metadata', return_value=metadata), \
         patch.object(manager, 'save_bootlog'), \
         patch.object(manager.HARDWARE, 'get_serial', return_value='fixture'), \
         patch.object(manager.HARDWARE, 'get_device_type', return_value='pc'), \
         patch.object(manager, 'register', return_value='fixture'), \
         patch.object(manager.cloudlog, 'bind_global'):
      manager.manager_init()
      self.assertTrue(self.params.get_bool('AlwaysOnLateral'))
      self.assertFalse(self.params.get_bool('OpenpilotEnabledToggle'))
      self.assertTrue(self.params.get_bool('StarPortEnabled'))
      self.assertFalse(Path(self.params.get_param_path('AndroidAutoEnabled')).exists())
      self.params.put_bool('AlwaysOnLateral', True, block=True)
      corrupt = {'SLCOffsetSchedule': b'',
                 'LongitudinalPersonalityProfiles': b'{broken', 'LaneCenterOffset': b'bad'}
      for key, raw in corrupt.items():
        Path(self.params.get_param_path(key)).write_bytes(raw)
      retired_offset = Path(self.params.get_param_path('Offset3'))
      retired_offset.write_bytes(b'3')
      manager.manager_init()
      self.assertTrue(self.params.get_bool('AlwaysOnLateral'))
      self.assertFalse(self.params.get_bool('OpenpilotEnabledToggle'))
      for key, raw in corrupt.items():
        self.assertEqual(Path(self.params.get_param_path(key)).read_bytes(), raw)
      self.assertFalse(retired_offset.exists())


if __name__ == '__main__':
  unittest.main()
