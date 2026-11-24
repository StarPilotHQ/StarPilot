import tempfile
from pathlib import Path
from unittest import TestCase
from types import SimpleNamespace

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot.aol.defaults import default_enabled, enroll_missing_preference
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner


class TestAolInitialDefault(TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    self.cp = CarInterface.get_params(CAR.HONDA_CIVIC_BOSCH, {0: {}, 1: {}, 2: {}}, [], True, False, False)

  def test_actual_supported_policy_and_canonical_owner_share_default(self):
    self.assertFalse(int(self.cp.safetyConfigs[0].safetyParam) & 0x20)
    self.assertTrue(default_enabled(self.cp))
    owner = FeatureSettingsOwner(self.params, lambda group: True, vehicle_fingerprint=lambda: self.cp.carFingerprint,
                                 vehicle_params=lambda: self.cp)
    row = next(row for row in owner.snapshot('aol', parked=True, system_long=True, lateral_context=True, metric=False).rows if row.key == 'AlwaysOnLateral')
    self.assertEqual((row.value, row.default_value), ('On', 'On'))
    self.assertTrue(enroll_missing_preference(self.params, self.cp))
    self.assertTrue(read_settings(self.params).enabled)
    self.assertFalse(enroll_missing_preference(self.params, self.cp))

  def test_preference_default_is_on_without_vehicle_or_brand_qualification(self):
    vehicles = [None, SimpleNamespace(), SimpleNamespace(notCar=True), SimpleNamespace(passive=True),
                SimpleNamespace(dashcamOnly=True)]
    vehicles += [SimpleNamespace(brand=brand) for brand in ('body', 'chrysler', 'ford', 'gm', 'honda', 'hyundai',
                 'kia', 'genesis', 'mazda', 'nissan', 'rivian', 'subaru', 'tesla', 'toyota', 'volkswagen', 'volvo', 'unknown')]
    for cp in vehicles:
      with self.subTest(cp=cp):
        self.assertTrue(default_enabled(cp))

  def test_missing_preference_can_enroll_before_vehicle_identification(self):
    self.assertTrue(enroll_missing_preference(self.params, None))
    self.assertEqual(Path(self.params.get_param_path('AlwaysOnLateral')).read_bytes(), b'1')

  def test_explicit_off_on_and_invalid_migration_bytes_always_win(self):
    for raw in (b'0', b'1', b'invalid'):
      with self.subTest(raw=raw):
        Path(self.params.get_param_path('AlwaysOnLateral')).write_bytes(raw)
        self.assertFalse(enroll_missing_preference(self.params, self.cp))
        self.assertEqual(Path(self.params.get_param_path('AlwaysOnLateral')).read_bytes(), raw)

  def test_runtime_unavailability_never_rewrites_existing_choice(self):
    self.params.put_bool('AlwaysOnLateral', False, block=True)
    self.cp.passive = True
    self.assertTrue(default_enabled(self.cp))
    self.assertFalse(enroll_missing_preference(self.params, self.cp))
    self.assertFalse(self.params.get_bool('AlwaysOnLateral'))

  def test_irregular_preference_is_preserved_without_startup_failure(self):
    target = Path(self.params.get_param_path('AlwaysOnLateral'))
    target.mkdir()
    self.assertFalse(enroll_missing_preference(self.params, self.cp))
    self.assertTrue(target.is_dir())

  def test_real_invalid_lock_is_preserved_without_startup_failure(self):
    namespace = Path(self.params.get_param_path()).absolute()
    lock = namespace.parent / '.lock'
    lock.unlink(missing_ok=True)
    lock.symlink_to(namespace / 'nonexistent')
    self.assertFalse(enroll_missing_preference(self.params, self.cp))
    self.assertTrue(lock.is_symlink())
    self.assertFalse((namespace / 'AlwaysOnLateral').exists())

  def test_real_missing_namespace_write_failure_does_not_abort_startup(self):
    namespace = Path(self.params.get_param_path()).absolute()
    if namespace.is_symlink():
      namespace.unlink()
    else:
      import shutil
      shutil.rmtree(namespace)
    self.assertFalse(enroll_missing_preference(self.params, self.cp))

  def test_symlink_preference_read_failure_preserves_external_explicit_choice(self):
    outside = Path(self.temp.name) / 'saved-off'
    outside.write_bytes(b'0')
    target = Path(self.params.get_param_path('AlwaysOnLateral'))
    target.symlink_to(outside)
    self.assertFalse(enroll_missing_preference(self.params, self.cp))
    self.assertTrue(target.is_symlink())
    self.assertEqual(outside.read_bytes(), b'0')


class TestActualFreshCardStartup(TestCase):
  def construct(self, preference=None, *, enabled=True):
    from unittest.mock import patch
    self.enterContext(patch.dict('os.environ'))
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.car.card import Car
    prefix = OpenpilotPrefix()
    prefix.__enter__()
    self.addCleanup(prefix.__exit__, None, None, None)
    params = Params()
    recovery = tempfile.TemporaryDirectory()
    self.addCleanup(recovery.cleanup)
    storage = Path(recovery.name) / 'recovery'
    from openpilot.system.manager import manager
    from openpilot.starpilot.state_migration import prepare_manager_start
    prepare_manager_start(params, storage)
    if preference is not None:
      Path(params.get_param_path('AlwaysOnLateral')).write_bytes(preference)
    metadata = SimpleNamespace(
      release_channel=False, tested_channel=False, channel='SecretGoodStarPilot',
      openpilot=SimpleNamespace(version='test', git_commit='a' * 40, git_commit_date='test',
                               git_origin='local', git_normalized_origin='local', is_dirty=True),
    )
    with patch.object(manager, 'Params', return_value=params), \
         patch.object(manager, 'starpilot_storage_root', return_value=storage), \
         patch.object(manager.Paths, 'shm_path', return_value=str(Path(recovery.name) / 'shm')), \
         patch.object(manager, 'get_build_metadata', return_value=metadata), \
         patch.object(manager, 'save_bootlog'), \
         patch.object(manager.HARDWARE, 'get_serial', return_value='fixture'), \
         patch.object(manager.HARDWARE, 'get_device_type', return_value='pc'), \
         patch.object(manager, 'register_at_startup', return_value='fixture'), \
         patch.object(manager.cloudlog, 'bind_global'):
      manager.manager_init()
    target = Path(params.get_param_path('AlwaysOnLateral'))
    if preference is None:
      self.assertEqual(target.read_bytes(), b'1', 'Manager must initialize AOL On before vehicle identification')
    else:
      self.assertEqual(target.read_bytes(), preference, 'Manager must preserve the saved AOL preference')
    params.put_bool('OpenpilotEnabledToggle', enabled, block=True)
    params.put_bool('IsReleaseBranch', False, block=True)
    cp = CarInterface.get_params(CAR.HONDA_CIVIC_BOSCH, {0: {}, 1: {}, 2: {}}, [], True, False, False)
    self.assertFalse(int(cp.safetyConfigs[0].safetyParam) & 0x20)
    ci = CarInterface(cp)
    with patch.dict('os.environ', {'AOL_REPLAY_RUNTIME': '0'}):
      host = Car(CI=ci, RI=ci.RadarInterface(cp))
    return host, params

  def test_missing_preference_bootstraps_actual_same_startup_transport_and_cp(self):
    host, params = self.construct()
    self.assertTrue(params.get_bool('AlwaysOnLateral'))
    self.assertEqual(Path(params.get_param_path('AlwaysOnLateral')).read_bytes(), b'1')
    self.assertTrue(host.aol_replay)
    self.assertTrue(host.aol_settings.enabled)
    self.assertIsNotNone(host.aol_card_intent)
    self.assertIn('aolSafetyWire', host.sm.services)
    self.assertIn('aolIntentWire', host.pm.sock)
    self.assertTrue(int(host.CP.safetyConfigs[0].safetyParam) & 0x20)
    from opendbc.car.structs import car
    with car.CarParams.from_bytes(params.get('CarParams')) as published:
      self.assertTrue(int(published.safetyConfigs[0].safetyParam) & 0x20)

  def test_explicit_disabled_preference_keeps_actual_startup_disabled(self):
    host, params = self.construct(b'0')
    self.assertFalse(params.get_bool('AlwaysOnLateral'))
    self.assertEqual(Path(params.get_param_path('AlwaysOnLateral')).read_bytes(), b'0')
    self.assertFalse(host.aol_replay)
    self.assertIsNone(host.aol_settings)
    self.assertFalse(int(host.CP.safetyConfigs[0].safetyParam) & 0x20)

  def test_explicit_enabled_preference_survives_manager_and_card_startup(self):
    host, params = self.construct(b'1')
    self.assertEqual(Path(params.get_param_path('AlwaysOnLateral')).read_bytes(), b'1')
    self.assertTrue(host.aol_replay)
    self.assertTrue(host.aol_settings.enabled)

  def test_global_on_preference_does_not_enable_a_passive_car(self):
    host, params = self.construct(enabled=False)
    self.assertEqual(Path(params.get_param_path('AlwaysOnLateral')).read_bytes(), b'1')
    self.assertTrue(host.CP.passive)
    self.assertFalse(host.aol_qualified)
    self.assertIsNone(host.aol_card_intent)
    from opendbc.car.structs import car
    self.assertTrue(all(config.safetyModel == car.CarParams.SafetyModel.noOutput for config in host.CP.safetyConfigs))
    self.assertTrue(all(int(config.safetyParam) == 0 for config in host.CP.safetyConfigs))
