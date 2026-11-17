import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('handoff_state_migration', Path(__file__).resolve().parents[1] / 'state_migration.py')
assert spec is not None
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class TestDomHandoff(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.root = Path(temporary.name)
    self.namespace = self.root / 'params' / 'd'
    self.namespace.mkdir(parents=True)
    self.storage = self.root / 'recovery'
    self.types = {'AlphaLongitudinalEnabled': 'BOOL', 'IsMetric': 'BOOL', 'CalibrationParams': 'BYTES', 'DongleId': 'STRING'}
    self.params = SimpleNamespace(get_param_path=lambda: str(self.namespace), all_keys=lambda: self.types,
                                  get_type=lambda key: SimpleNamespace(name=self.types[key]))
    cache = SimpleNamespace(CACHE_KEYS={'CalibrationParams'}, inspect_cache=lambda key, raw: SimpleNamespace(status='valid'))
    self.patch = patch.dict('sys.modules', {'openpilot.starpilot.schema_cache': cache})
    self.patch.start()
    self.addCleanup(self.patch.stop)
    self.start()
    self.profile = next((self.storage / 'profiles').iterdir())
    self.handoff = self.namespace.parent / f'.starpilot-dom-handoff-{migration.digest(str(self.namespace).encode())}.json'

  def start(self, **kwargs):
    return migration.prepare_manager_start(self.params, self.storage, auto_migrate=True, **kwargs)

  def write(self, **values):
    for key, raw in values.items():
      (self.namespace / key).write_bytes(raw)

  def mark_dom(self, token='a' * 32, **changes):
    record: dict = {'format': 'starpilot-dom-handoff', 'version': 1, 'namespace': str(self.namespace),
              'target': str(self.namespace.resolve()), 'token': token}
    record.update(changes)
    migration._atomic_write(self.handoff, migration.canonical_json(record))

  def test_handoff_archives_foreign_settings_and_keeps_identity(self):
    self.write(IsMetric=b'1', CalibrationParams=b'Dom calibration', DomSetting=b'old', DongleId=b'identity')
    original = migration._read_namespace(self.namespace)
    self.mark_dom()
    self.start()
    self.assertEqual(migration._read_namespace(self.namespace), {'IsMetric': b'1', 'DongleId': b'identity'})
    self.assertIn(original, [migration.load_snapshot(path) for path in (self.storage / 'snapshots').iterdir()])
    self.assertEqual(json.loads(self.profile.read_bytes())['consumed_dom_token'], 'a' * 32)
    self.assertTrue(self.handoff.exists())

  def test_handoff_keeps_alpha_longitudinal_choice(self):
    self.write(AlphaLongitudinalEnabled=b'1', DomSetting=b'old')
    self.mark_dom()
    self.start()
    self.assertEqual(migration._read_namespace(self.namespace), {'AlphaLongitudinalEnabled': b'1'})
    self.write(AlphaLongitudinalEnabled=b'0')
    self.start()
    self.assertEqual((self.namespace / 'AlphaLongitudinalEnabled').read_bytes(), b'0')

  def test_same_token_and_marker_removal_preserve_rebase_preferences(self):
    self.mark_dom()
    self.start()
    self.write(IsMetric=b'1', CalibrationParams=b'rebase calibration')
    self.start()
    self.assertEqual((self.namespace / 'IsMetric').read_bytes(), b'1')
    self.handoff.unlink()
    self.start()
    self.assertEqual((self.namespace / 'CalibrationParams').read_bytes(), b'rebase calibration')

  def test_new_dom_token_resets_again(self):
    self.mark_dom()
    self.start()
    self.write(IsMetric=b'1', CalibrationParams=b'rebase calibration')
    self.mark_dom('b' * 32)
    self.start()
    self.assertEqual((self.namespace / 'IsMetric').read_bytes(), b'1')
    self.assertFalse((self.namespace / 'CalibrationParams').exists())
    self.assertEqual(json.loads(self.profile.read_bytes())['consumed_dom_token'], 'b' * 32)

  def test_malformed_wrong_target_and_bad_permissions_never_reset(self):
    self.write(IsMetric=b'1')
    for changes in ({'target': str(self.root)}, {'token': 'invalid'}, {'version': True}):
      with self.subTest(changes=changes):
        self.mark_dom(**changes)
        with self.assertRaises(migration.MigrationRequired):
          self.start()
        self.assertEqual((self.namespace / 'IsMetric').read_bytes(), b'1')
    self.mark_dom()
    self.handoff.chmod(0o644)
    with self.assertRaises(migration.MigrationRequired):
      self.start()
    self.assertEqual((self.namespace / 'IsMetric').read_bytes(), b'1')

  def test_archive_failure_does_not_consume(self):
    self.write(IsMetric=b'1')
    before = self.profile.read_bytes()
    self.mark_dom()
    with patch.object(migration, 'load_snapshot', return_value={'wrong': b'value'}):
      with self.assertRaises(migration.MigrationRequired):
        self.start()
    self.assertEqual(self.profile.read_bytes(), before)
    self.assertEqual((self.namespace / 'IsMetric').read_bytes(), b'1')
    self.start()
    self.assertEqual((self.namespace / 'IsMetric').read_bytes(), b'1')

  def test_partial_reset_failure_retries_same_token(self):
    self.write(IsMetric=b'1', CalibrationParams=b'old', DomSetting=b'old')
    original = migration._read_namespace(self.namespace)
    before = self.profile.read_bytes()
    self.mark_dom()
    unlink = Path.unlink
    def fail(path, *args, **kwargs):
      if path == self.namespace.resolve() / 'DomSetting':
        raise OSError('reset failed')
      return unlink(path, *args, **kwargs)
    with patch.object(Path, 'unlink', fail):
      with self.assertRaises(migration.MigrationRequired):
        self.start()
    self.assertEqual(self.profile.read_bytes(), before)
    self.assertIn(original, [migration.load_snapshot(path) for path in (self.storage / 'snapshots').iterdir()])
    self.start()
    self.assertEqual(migration._read_namespace(self.namespace), {'IsMetric': b'1'})

  def test_strict_and_dry_run_do_not_consume(self):
    self.mark_dom()
    before = self.profile.read_bytes()
    with self.assertRaises(migration.MigrationRequired):
      self.start(dry_run=True)
    with self.assertRaises(migration.MigrationRequired):
      migration.prepare_manager_start(self.params, self.storage)
    self.assertEqual(self.profile.read_bytes(), before)

  def test_namespace_markers_are_isolated(self):
    other = self.namespace.parent / 'other'
    other.mkdir()
    self.mark_dom()
    other_params = SimpleNamespace(get_param_path=lambda: str(other), all_keys=lambda: self.types,
                                   get_type=self.params.get_type)
    migration.prepare_manager_start(other_params, self.storage, auto_migrate=True)
    self.assertNotIn('consumed_dom_token', json.loads((self.storage / 'profiles' /
                     f'{migration.digest(str(other).encode())}.json').read_bytes()))


if __name__ == '__main__':
  unittest.main()
