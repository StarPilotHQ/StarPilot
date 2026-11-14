import ast
import copy
import hashlib
import importlib.util
import json
import math
import os
import re
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
  spec = importlib.util.spec_from_file_location(name, path)
  assert spec is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


migration = load('external_reset_state_helpers', ROOT / 'state_migration.py')
owner_ast = ast.parse((ROOT / 'navigation' / 'owner.py').read_text())
selected: list[ast.stmt] = [node for node in owner_ast.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                 and node.name in ('ValidationError', 'destination', 'favorite_place', 'valid_google_key')]
selected.extend(node for node in owner_ast.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id in ('ROUTE_AVOIDANCES', 'MAP_PROVIDERS', 'GOOGLE_KEY_CHARS', 'SAFE_ID') for target in node.targets))
owner_class = next(node for node in owner_ast.body if isinstance(node, ast.ClassDef) and node.name == 'NavigationOwner')
selected.append(next(node for node in owner_class.body if isinstance(node, ast.FunctionDef) and node.name == 'read'))
namespace: dict = {'json': json, 'math': math, 're': re, 'hashlib': hashlib, 'os': os, 'copy': copy, 'MAX_DOCUMENT': 256 * 1024,
                   'MAX_FAVORITES': 100, 'MAX_RECENTS': 10, 'FAVORITE_LABELS': ('home', 'work'),
                   'GPS_MODES': next(ast.literal_eval(node.value) for node in ast.parse((ROOT / 'gps' / 'source.py').read_text()).body
                                     if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == 'GPS_MODES' for t in node.targets))}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / 'navigation' / 'owner.py'), 'exec'), namespace)
NavigationOwner = type('NavigationOwner', (), {'read': namespace['read']})
with patch.dict(sys.modules, {'openpilot.starpilot.state_migration': migration}):
  reset = load('external_reset_subject', ROOT / 'external_preferences_reset.py')


class TestExternalPreferencesReset(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.root = Path(temporary.name)
    self.storage = self.root / 'storage'
    self.models = self.root / 'models' / 'v26'
    self.models.mkdir(parents=True)
    self.navigation = self.storage / 'navigation'
    self.navigation.mkdir(parents=True)
    self.model_file = self.models / 'preferences.json'
    self.nav_file = self.navigation / 'settings.json'
    self.original: dict = {'model_preferences': b'{"small":"old","randomizer":true}',
                     'navigation_settings': json.dumps({'version': 1, 'revision': 'old', 'enabled': True,
                       'token': 'credential', 'destination': {'name': 'Home', 'latitude': 1, 'longitude': 2},
                       'favorites': [], 'routeChoice': 2}).encode()}
    self.model_file.write_bytes(self.original['model_preferences'])
    self.nav_file.write_bytes(self.original['navigation_settings'])
    self.payload = self.models / 'model.thneed'
    self.payload.write_bytes(b'model-payload')
    patcher = patch.dict(sys.modules, {'openpilot.starpilot.navigation.owner': SimpleNamespace(NavigationOwner=NavigationOwner)})
    patcher.start()
    self.addCleanup(patcher.stop)

  def run_reset(self):
    return reset.reset_external_preferences(self.storage, self.models)

  def archive(self):
    journal = json.loads((self.storage / 'fresh-external-profile-v2' / 'journal.json').read_bytes())
    return migration.load_snapshot(Path(journal['snapshot']))

  def test_fresh_defaults_archive_and_credentials(self):
    self.assertEqual(self.run_reset(), ())
    self.assertFalse(self.model_file.exists())
    nav = json.loads(self.nav_file.read_bytes())
    self.assertEqual(nav['token'], 'credential')
    self.assertFalse(nav['enabled'])
    self.assertIsNone(nav['destination'])
    self.assertEqual(nav['favorites'], [])
    self.assertEqual(nav['routeChoice'], 0)
    self.assertNotEqual(nav['revision'], 'old')
    self.assertEqual(self.archive(), self.original)
    self.assertEqual(self.payload.read_bytes(), b'model-payload')

  def test_restart_preserves_new_choices(self):
    self.run_reset()
    self.model_file.write_bytes(b'new model choice')
    self.nav_file.write_bytes(b'new navigation choice')
    self.run_reset()
    self.assertEqual(self.model_file.read_bytes(), b'new model choice')
    self.assertEqual(self.nav_file.read_bytes(), b'new navigation choice')

  def test_fresh_profile_keeps_existing_galaxy_pairing_bytes(self):
    galaxy = self.storage / 'galaxy'
    galaxy.mkdir(mode=0o700)
    values: dict = {'remote-v1.json': b'{"version":1,"slug":"ExistingGalaxy01","authHash":"' + b'a' * 64 +
              b'","session":"' + b'b' * 64 + b'"}', 'access-v1.json': b'existing password verifier',
              'remote-unpaired-v1': b''}
    for name, raw in values.items():
      (galaxy / name).write_bytes(raw)
      (galaxy / name).chmod(0o600)
    self.run_reset()
    self.assertEqual({path.name: path.read_bytes() for path in galaxy.iterdir()}, values)
    self.assertFalse(self.model_file.exists())

  def test_invalid_navigation_archived_and_disabled(self):
    for raw in (b'broken JSON', b'{"token":"credential"}', self.original['navigation_settings'].replace(b'"token": "credential"', b'"token": 4')):
      with self.subTest(raw=raw):
        with tempfile.TemporaryDirectory() as directory:
          storage = Path(directory) / 'storage'
          (storage / 'navigation').mkdir(parents=True)
          path = storage / 'navigation' / 'settings.json'
          path.write_bytes(raw)
          self.assertTrue(reset.reset_external_preferences(storage, Path(directory) / 'models'))
          fresh = json.loads(path.read_bytes())
          self.assertEqual(fresh['token'], '')
          self.assertFalse(fresh['enabled'])
          journal = json.loads((storage / 'fresh-external-profile-v2' / 'journal.json').read_bytes())
          self.assertEqual(migration.load_snapshot(Path(journal['snapshot']))['navigation_settings'], raw)

  def test_archive_failure_changes_nothing(self):
    with patch.object(reset, '_save_snapshot', side_effect=OSError('disk full')):
      with self.assertRaises(OSError):
        self.run_reset()
    self.assertEqual(self.model_file.read_bytes(), self.original['model_preferences'])
    self.assertEqual(self.nav_file.read_bytes(), self.original['navigation_settings'])

  def test_partial_write_resumes_without_repeated_model_reset(self):
    atomic = reset._atomic_write
    def fail_navigation(path, data):
      if path == self.nav_file:
        raise OSError('interrupted')
      return atomic(path, data)
    with patch.object(reset, '_atomic_write', side_effect=fail_navigation):
      with self.assertRaises(OSError):
        self.run_reset()
    self.assertFalse(self.model_file.exists())
    self.assertFalse((self.storage / 'fresh-external-profile-v2' / 'complete.json').exists())
    self.run_reset()
    self.assertFalse(json.loads(self.nav_file.read_bytes())['enabled'])
    self.assertEqual(self.archive(), self.original)

  def test_partial_reset_refuses_overwriting_new_choice(self):
    atomic = reset._atomic_write
    def fail_navigation(path, data):
      if path == self.nav_file:
        raise OSError('interrupted')
      return atomic(path, data)
    with patch.object(reset, '_atomic_write', side_effect=fail_navigation):
      with self.assertRaises(OSError):
        self.run_reset()
    self.model_file.write_bytes(b'new choice')
    with self.assertRaisesRegex(ValueError, 'changed during reset'):
      self.run_reset()
    self.assertEqual(self.model_file.read_bytes(), b'new choice')
    self.assertEqual(self.nav_file.read_bytes(), self.original['navigation_settings'])

  def test_absent_preferences_stay_absent(self):
    self.model_file.unlink()
    self.nav_file.unlink()
    self.run_reset()
    self.assertFalse(self.model_file.exists())
    self.assertFalse(self.nav_file.exists())
    self.assertEqual(self.archive(), {})

  def test_symlink_preferences_rejected_before_mutation(self):
    self.model_file.unlink()
    self.model_file.symlink_to(self.payload)
    with self.assertRaises(OSError):
      self.run_reset()
    self.assertEqual(self.payload.read_bytes(), b'model-payload')
    self.assertEqual(self.nav_file.read_bytes(), self.original['navigation_settings'])


if __name__ == '__main__':
  unittest.main()


def test_fresh_preferences_disable_scheduled_automatic_installation(tmp_path):
  from openpilot.starpilot.software.auto_install import InstallSettings
  settings = InstallSettings(tmp_path / 'software' / 'auto-install')
  value = settings.read()
  assert value is not None
  settings.save(True, '0 0 * * *', value['revision'], lambda: True)
  assert reset.reset_external_preferences(tmp_path, tmp_path / 'models') == ()
  value = settings.read()
  assert value is not None and not value['enabled']
