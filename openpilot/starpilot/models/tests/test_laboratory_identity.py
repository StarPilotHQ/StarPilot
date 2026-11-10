from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import tempfile
import unittest

from openpilot.cereal import log
from openpilot.starpilot.models.laboratory_runner import LaboratoryPair
from openpilot.starpilot.models import receipt
from openpilot.starpilot.models.runtime_bridge import runtime
from openpilot.starpilot.models.status import LaboratoryLoad, ModelOutput, ModelProcess, project_laboratory_status


class TestLaboratoryIdentity(unittest.TestCase):
  def test_pair_receipt_big_roles_and_fallback_retire_both_identities(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'owned/load-v1.json'
      load = LaboratoryLoad(123, 456, 100, 'gwm8223', 'v11', 'a' * 64, 'cinquev3', 'v16', 'b' * 64, 'c' * 64)
      receipt.write_pair_receipt(load, path)
      self.assertEqual(receipt.read_pair_receipt(path), load)
      self.assertEqual(receipt.pair_receipt_path(path).stat().st_mode & 0o777, 0o600)
      receipt.pair_receipt_path(path).chmod(0o644)
      self.assertIsNone(receipt.read_pair_receipt(path))
      receipt.pair_receipt_path(path).chmod(0o600)
      process = ModelProcess(123, 456, True)
      config = {'enabled': True, 'lateralModel': 'gwm8223', 'longitudinalModel': 'cinquev3'}
      output = ModelOutput(110, True, 115, True, True, True)
      status = project_laboratory_status(config, process, load, output, 120)
      self.assertTrue(status['active'])
      self.assertFalse(project_laboratory_status(config, replace(process, start_ticks=457), load, output, 120)['active'])
      self.assertFalse(project_laboratory_status(config, process, load, replace(output, model_v2_big=False), 120)['active'])
      self.assertFalse(project_laboratory_status(config, process, load, output, 1_000_000_000)['active'])
      owner = receipt.ModelReceiptOwner(path)
      owner.start()
      self.assertIsNone(receipt.read_pair_receipt(path))
      self.assertIsNone(receipt.read_receipt(path))
      receipt.write_pair_receipt(load, path)
      raw = json.loads(receipt.pair_receipt_path(path).read_text())
      raw['roles']['longitudinal']['artifactSha256'] = 'a' * 64
      receipt.pair_receipt_path(path).write_text(json.dumps(raw))
      self.assertIsNone(receipt.read_pair_receipt(path))

  def test_loaded_pair_publishes_on_modelds_owned_identity_service(self):
    with tempfile.TemporaryDirectory() as directory:
      owner = receipt.ModelReceiptOwner(Path(directory) / 'owned/load-v1.json')
      roles = [SimpleNamespace(model_id=mid, behavior_version='v11', artifact=SimpleNamespace(sha256=digest * 64),
                               source_format=receipt.LEGACY_ABI, source_compiler_revision=receipt.LEGACY_REVISION,
                               source_manifest_sha256='') for mid, digest in (('gwm8223', 'a'), ('sc23', 'b'))]
      pair = SimpleNamespace(lateral_role=roles[0], longitudinal_role=roles[1])
      self.assertTrue(owner.loaded_pair(pair, 'c' * 64))
      class Publisher:
        def send(self, service, packet):
          self.service, self.record = service, json.loads(packet.logMessage)
          assert packet.which() == 'logMessage'
      publisher = Publisher()
      self.assertTrue(owner.publish(publisher, 10_000_000_000))
      self.assertEqual(publisher.service, 'modelIdentity')
      self.assertEqual(publisher.record['msg']['event'], 'modeld.loaded_pair')
      self.assertFalse(owner.publish(publisher, 10_000_000_001))
      owner.start()
      self.assertIsNone(receipt.read_pair_receipt(owner.receipt_file))

  def test_frozen_request_cannot_change_after_bind_or_loading(self):
    request = {'target_runtime_revision': runtime.LEGACY_REVISION, 'roles': {'lateral': 'original'}}
    with patch.object(runtime, '_selection', {'role': 'driving'}), patch.object(runtime, '_protection', None), \
         patch.object(runtime, '_laboratory_request_json', None):
      runtime.bind_laboratory_request(request)
      request['roles']['lateral'] = 'changed'
      self.assertEqual(runtime.frozen_laboratory_request()['roles']['lateral'], 'original')
      with self.assertRaisesRegex(ValueError, 'replace'):
        runtime.bind_laboratory_request(request)
      runtime._protection = Mock(spec_set=())
      with self.assertRaisesRegex(ValueError, 'before loading'):
        runtime.bind_laboratory_request(request)

  def test_missing_completed_pair_outputs_latches_failure_and_retires_warps(self):
    pair = LaboratoryPair.__new__(LaboratoryPair)
    pair.failed = False
    pair._last_outputs = None
    pair.lateral = Mock(spec_set=['last_warp'])
    pair.longitudinal = Mock(spec_set=['last_warp'])
    pair.lateral.last_warp = pair.longitudinal.last_warp = object()
    with patch.object(pair, 'run', return_value={}):
      with self.assertRaises(AssertionError):
        pair.run_frame({}, {}, {}, log.ModelDataV2.Action(), .2, .2, 2.)
    self.assertTrue(pair.failed)
    self.assertIsNone(pair.lateral.last_warp)
    self.assertIsNone(pair.longitudinal.last_warp)
