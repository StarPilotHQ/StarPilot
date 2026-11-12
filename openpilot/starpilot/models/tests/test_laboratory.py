import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from openpilot.starpilot.models.catalog import ARTIFACT_ABI, CATALOG_PATH, COMPILER_REVISION
from openpilot.starpilot.models.laboratory import ModelLaboratory, configuration
from openpilot.starpilot.models.manager import ModelError, ModelManager, artifact_path, atomic_json, validate_manifest


class LaboratoryTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.enterContext(patch('openpilot.starpilot.models.manager.SHIPPED_MODELS', self.root / 'shipped'))
    self.parked = True
    self.gpu = False
    self.manifest = json.loads(CATALOG_PATH.read_text())
    self.payloads = {}
    for mid in ('gwm8223', 'sc23'):
      row = next(row for row in self.manifest['models'] if row['id'] == mid)
      for variant in ('standard', 'amd'):
        content = f'{mid} compiled for {variant}'.encode()
        path = self.root / mid / f"{mid}_driving_{'amd_' if variant == 'amd' else ''}tinygrad.pkl"
        self.payloads[path.name] = content
        metadata = {'artifact_format': ARTIFACT_ABI, 'artifact_sha256': hashlib.sha256(content).hexdigest(),
                    'artifact_size': len(content), 'artifact_chunk_count': 0}
        if variant == 'standard':
          row.update(metadata)
        else:
          row['accelerator_artifacts'] = {'amd': {**metadata, 'execution_device': 'AMD', 'compiler_revision': COMPILER_REVISION}}
    atomic_json(self.root / 'catalog.json', self.manifest)

    def opener(url, timeout):
      if '/manifests/' in url:
        return io.BytesIO(json.dumps(self.manifest).encode())
      return io.BytesIO(self.payloads[url.rsplit('/', 1)[-1]])

    self.manager = ModelManager(root=self.root, parked=lambda: self.parked, gpu_present=lambda: self.gpu, opener=opener)
    self.addCleanup(self.manager.close)
    self.lab = ModelLaboratory(self.manager, runtime_supported=False)
    self.config = {'enabled': True, 'lateralModel': 'gwm8223', 'longitudinalModel': 'sc23'}

  def download(self, mid='gwm8223', variant='amd'):
    if variant == 'amd':
      self.lab.action('download', {'model': mid})
    else:
      self.manager.action('download', {'model': mid})
    self.manager.worker.join(3)
    self.assertFalse(self.manager.worker.is_alive())
    self.assertEqual(self.manager.snapshot()['progress'], 'Downloaded!')

  def ready_snapshot(self):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
      result = self.lab.snapshot()
      if not any(row['checking'] for row in result['models']):
        return result
      time.sleep(0.01)
    self.fail('Laboratory verification stalled')

  def test_catalog_discovery_keeps_unpublished_and_big_rows_visible(self):
    result = self.ready_snapshot()
    rows = {row['value']: row for row in result['models']}
    self.assertEqual(set(rows), {row['id'] for row in self.manifest['models']})
    self.assertEqual(result['summary']['catalog'], len(self.manifest['models']))
    published = sum(bool(row.get('artifact_sha256')) if row.get('uses_external_gpu') else True for row in self.manifest['models'])
    self.assertEqual(result['summary']['published'], published)
    self.assertFalse(result['runtimeSupported'])
    big = rows['cinquev3']
    self.assertFalse(big['small'])
    self.assertTrue(big['modelLabEligible'])
    self.assertEqual(big['modelLabStatus'], 'missing')
    unpublished = next(row for row in rows.values() if row['modelLabStatus'] == 'unpublished')
    self.assertFalse(unpublished['modelLabArtifactAvailable'])
    self.assertFalse(unpublished['modelLabArtifactInstalled'])
    self.assertEqual(unpublished['modelLabReason'], 'A compatible artifact has not been published for this model.')
    self.assertEqual(rows['gwm8223']['modelLabStatus'], 'missing')
    self.download()
    verified = next(row for row in self.ready_snapshot()['models'] if row['value'] == 'gwm8223')
    self.assertEqual(verified['modelLabStatus'], 'runtime-unavailable')
    self.assertFalse(self.lab.snapshot()['runtime']['active'])

  def test_laboratory_download_prepares_both_backends_without_changing_selection(self):
    self.download()
    self.assertTrue(artifact_path('gwm8223', self.root, 'standard').is_file())
    self.assertTrue(artifact_path('gwm8223', self.root, 'amd').is_file())
    self.assertFalse(configuration(self.root)['enabled'])
    self.assertFalse((self.root / 'preferences.json').exists())

  def test_original_chestnut_variant_downloads_when_normal_variant_is_unpublished(self):
    row = next(row for row in self.manifest['models'] if row['id'] == 'gwm8223')
    for field in ('artifact_sha256', 'artifact_size', 'artifact_chunk_count', 'artifact_format'):
      row.pop(field, None)
    atomic_json(self.root / 'catalog.json', self.manifest)
    self.download()
    self.assertFalse(artifact_path('gwm8223', self.root, 'standard').exists())
    self.assertTrue(artifact_path('gwm8223', self.root, 'amd').exists())

  def test_direct_laboratory_page_reuses_the_existing_catalog_refresh(self):
    self.manager.refresh_catalog = True
    self.lab.snapshot()
    worker = self.manager.worker
    self.assertIsNotNone(worker)
    worker.join(3)
    self.assertFalse(worker.is_alive())
    self.assertEqual(self.manager.snapshot()['progress'], 'Catalog updated')
    self.lab.snapshot()
    self.assertIs(self.manager.worker, worker)

  def test_two_big_models_are_rejected_before_configuration_is_written(self):
    self.gpu = True
    lab = ModelLaboratory(self.manager, runtime_supported=True)
    pair = {'enabled': True, 'lateralModel': 'cinquev3', 'longitudinalModel': 'resfit'}
    with self.assertRaisesRegex(ModelError, 'Two Big models cannot'):
      lab.action('configure', pair)
    self.assertFalse((self.root / 'laboratory.json').exists())

  def test_previous_big_pair_is_suspended_without_destroying_saved_choices(self):
    from openpilot.starpilot.models.laboratory import freeze_request
    pair = {'enabled': True, 'lateralModel': 'cinquev3', 'longitudinalModel': 'resfit'}
    atomic_json(self.root / 'laboratory.json', pair)
    original = (self.root / 'laboratory.json').read_bytes()
    self.assertEqual(configuration(self.root), pair | {'enabled': False})
    self.assertIsNone(freeze_request(self.root))
    self.assertEqual((self.root / 'laboratory.json').read_bytes(), original)
    snapshot = self.lab.snapshot()
    self.assertFalse(snapshot['configuration']['enabled'])
    self.assertIn('Two Big models cannot', snapshot['configurationError'])

  def test_amd_download_and_delete_preserve_normal_small_artifact(self):
    self.download(variant='standard')
    normal = artifact_path('gwm8223', self.root)
    normal_bytes = normal.read_bytes()
    self.assertEqual(self.ready_snapshot()['summary']['ready'], 0)
    self.download()
    amd = artifact_path('gwm8223', self.root, 'amd')
    self.assertNotEqual(normal_bytes, amd.read_bytes())
    self.assertEqual(self.ready_snapshot()['summary']['ready'], 1)
    self.assertFalse(self.lab.snapshot()['chestnutReady'])
    result = self.lab.action('delete', {'model': 'gwm8223'})
    self.assertEqual(result['summary']['ready'], 0)
    self.assertFalse(amd.exists())
    self.assertEqual(normal.read_bytes(), normal_bytes)

  def test_hash_mismatch_never_installs_amd_variant(self):
    filename = artifact_path('gwm8223', self.root, 'amd').name
    self.payloads[filename] = b'corrupt'
    self.lab.action('download', {'model': 'gwm8223'})
    self.manager.worker.join(3)
    self.assertIn('checksum', self.manager.snapshot()['progress'])
    self.assertFalse(artifact_path('gwm8223', self.root, 'amd').exists())
    self.assertFalse(list(self.root.glob('*/.download-*')))

  def test_wrong_runtime_hardware_and_model_class_are_rejected(self):
    for field, value in (('compiler_revision', 'old-runtime'), ('execution_device', 'QCOM'),
                         ('artifact_sha256', 'invalid'), ('artifact_format', 'old-format')):
      with self.subTest(field=field):
        bad = copy.deepcopy(self.manifest)
        row = next(row for row in bad['models'] if row['id'] == 'gwm8223')
        row['accelerator_artifacts']['amd'][field] = value
        with self.assertRaises(ModelError):
          validate_manifest(bad)
    bad = copy.deepcopy(self.manifest)
    small = next(row for row in bad['models'] if row['id'] == 'gwm8223')
    big = next(row for row in bad['models'] if row['id'] == 'cinquev3')
    big['accelerator_artifacts'] = small['accelerator_artifacts']
    with self.assertRaises(ModelError):
      validate_manifest(bad)

  def test_normal_models_and_old_amd_bytes_do_not_satisfy_pair_admission(self):
    self.gpu = True
    ready_runtime = ModelLaboratory(self.manager, runtime_supported=True)
    for mid in ('gwm8223', 'sc23'):
      self.download(mid, 'standard')
    with self.assertRaisesRegex(ModelError, 'verify both model'):
      ready_runtime.action('configure', self.config)
    self.download()
    self.download('sc23')
    artifact_path('sc23', self.root, 'amd').write_bytes(b'old compiled bytes')
    with self.assertRaisesRegex(ModelError, 'verify both model'):
      ready_runtime.action('configure', self.config)
    self.assertFalse(configuration(self.root)['enabled'])

  def test_enable_requires_runtime_hardware_distinct_models_and_verified_variants(self):
    self.download()
    self.download('sc23')
    self.gpu = True
    with self.assertRaisesRegex(ModelError, 'not available in this build'):
      self.lab.action('configure', self.config)
    ready_runtime = ModelLaboratory(self.manager, runtime_supported=True)
    for invalid in ({**self.config, 'longitudinalModel': 'gwm8223'},
                    {**self.config, 'lateralModel': 'cinquev3'}, {**self.config, 'enabled': 'true'}):
      with self.assertRaises(ModelError):
        ready_runtime.action('configure', invalid)
    self.gpu = False
    with self.assertRaisesRegex(ModelError, 'Chestnut'):
      ready_runtime.action('configure', self.config)
    self.gpu = True
    saved = ready_runtime.action('configure', self.config)
    self.assertTrue(saved['configuration']['enabled'])
    self.assertFalse(saved['runtime']['active'])
    with self.assertRaisesRegex(ModelError, 'Disable'):
      ready_runtime.action('delete', {'model': 'sc23'})
    self.gpu = False
    ready_runtime.action('configure', {**self.config, 'enabled': False})
    self.assertFalse(configuration(self.root)['enabled'])

  def test_small_lateral_big_longitudinal_uses_the_existing_big_artifact(self):
    self.download()
    row = next(row for row in self.manifest['models'] if row['id'] == 'cinquev3')
    content = b'verified Big policy'
    row.update(artifact_sha256=hashlib.sha256(content).hexdigest(), artifact_size=len(content), artifact_chunk_count=0)
    atomic_json(self.root / 'catalog.json', self.manifest)
    path = artifact_path('cinquev3', self.root)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    self.gpu = True
    ready_runtime = ModelLaboratory(self.manager, runtime_supported=True)
    config = {**self.config, 'longitudinalModel': 'cinquev3'}
    result = ready_runtime.action('configure', config)
    self.assertEqual(result['configuration'], config)
    big = next(row for row in self.ready_snapshot()['models'] if row['value'] == 'cinquev3')
    self.assertTrue(big['modelLabArtifactInstalled'])
    self.assertTrue(big['modelLabEligible'])
    with self.assertRaises(ModelError):
      ready_runtime.action('delete', {'model': 'cinquev3'})
    self.assertEqual(path.read_bytes(), content)

  def test_parked_state_and_independent_progress_are_preserved(self):
    self.parked = False
    for action, payload in (('download', {'model': 'gwm8223'}), ('delete', {'model': 'gwm8223'}),
                            ('configure', {**self.config, 'enabled': False})):
      with self.assertRaises(ModelError):
        self.lab.action(action, payload)
    self.parked = True
    self.download()
    observer = ModelManager(root=self.root, parked=lambda: True, gpu_present=lambda: False)
    self.addCleanup(observer.close)
    snapshot = ModelLaboratory(observer, runtime_supported=False).snapshot()
    self.assertEqual(snapshot['download']['variant'], 'amd')
    self.assertEqual(snapshot['download']['progress'], 'Downloaded!')
    self.assertEqual(snapshot['download']['jobId'], self.manager.job_id)
    self.assertFalse(snapshot['runtimeSupported'])


if __name__ == '__main__':
  unittest.main()
