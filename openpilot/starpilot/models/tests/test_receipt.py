from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from openpilot.starpilot.models.catalog import BUNDLED_CURRENT
from openpilot.starpilot.models.receipt import (ModelReceiptOwner, ReceiptUnavailable, artifact_digest, artifact_identity,
                                                clear_receipt, prepare_artifact, process_start_ticks, read_receipt,
                                                record_prepared,
                                                write_receipt, logged_model_load, log_loaded_model)
from openpilot.starpilot.models.status import ModelLoad, ModelVariant


class TestModelReceipt(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.path = self.root / "owned" / "load-v1.json"
    self.load = ModelLoad(os.getpid(), 12345, 987654321, BUNDLED_CURRENT, ModelVariant.SMALL, "a" * 64)

  def test_atomic_roundtrip_clear_and_malformed(self):
    self.assertIsNone(read_receipt(self.path))
    write_receipt(self.load, self.path)
    self.assertEqual(read_receipt(self.path), self.load)
    self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
    self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
    raw = self.path.read_text()
    self.path.write_text(raw.replace('"pid":', '"pid":1,"pid":'))
    self.assertIsNone(read_receipt(self.path))
    self.path.write_bytes(b"{" * 1025)
    self.assertIsNone(read_receipt(self.path))
    self.path.write_text(json.dumps({"version": 1}))
    self.assertIsNone(read_receipt(self.path))
    clear_receipt(self.path)
    self.assertFalse(self.path.exists())

  def test_catalog_id_roundtrip_and_wrong_hardware_rejection(self):
    for model_id, variant in (("sc23", ModelVariant.SMALL), ("cinquev3", ModelVariant.CHESTNUT)):
      load = replace(self.load, model_id=model_id, variant=variant)
      write_receipt(load, self.path)
      self.assertEqual(read_receipt(self.path), load)
    with self.assertRaises(ReceiptUnavailable):
      write_receipt(replace(self.load, model_id="cinquev3"), self.path)
    with self.assertRaises(ReceiptUnavailable):
      write_receipt(replace(self.load, model_id="unknown"), self.path)

  def test_symlink_fifo_and_unowned_directory_are_unavailable(self):
    target = self.root / "outside"
    target.write_bytes(b"untouched")
    self.path.parent.symlink_to(self.root, target_is_directory=True)
    self.assertIsNone(read_receipt(self.path))
    with self.assertRaises(OSError):
      write_receipt(self.load, self.path)
    clear_receipt(self.path)
    self.assertEqual(target.read_bytes(), b"untouched")
    self.path.parent.unlink()
    self.path.parent.mkdir(mode=0o700)
    os.mkfifo(self.path)
    self.assertIsNone(read_receipt(self.path))
    clear_receipt(self.path)
    self.path.parent.chmod(0o755)
    self.assertIsNone(read_receipt(self.path))
    with self.assertRaises(ReceiptUnavailable):
      write_receipt(self.load, self.path)

  def test_invalid_namespace_is_unavailable_to_reader(self):
    with mock.patch.dict(os.environ, {"OPENPILOT_PREFIX": "../../escape"}):
      self.assertIsNone(read_receipt())

  def test_installed_artifact_same_file_and_regular(self):
    artifact = self.root / "model.pkl"
    artifact.write_bytes(b"compiled-artifact")
    identity = artifact_identity(artifact)
    self.assertEqual(len(artifact_digest(artifact, identity)), 64)
    prepared = prepare_artifact(artifact, identity)
    recorded = record_prepared(prepared, ModelVariant.SMALL, receipt_file=self.path,
                               pid=421, start_ticks=12345, now_mono_ns=987654321)
    self.assertEqual(read_receipt(self.path), recorded)
    artifact.write_bytes(b"changed")
    with self.assertRaises(ReceiptUnavailable):
      artifact_digest(artifact, identity)
    artifact.unlink()
    artifact.symlink_to(self.root / "outside")
    with self.assertRaises(ReceiptUnavailable):
      artifact_identity(artifact)
    artifact.unlink()
    os.mkfifo(artifact)
    with self.assertRaises(ReceiptUnavailable):
      artifact_identity(artifact)

  def test_proc_start_parser(self):
    path = self.root / "421" / "stat"
    path.parent.mkdir()
    fields = ["S"] + ["0"] * 18 + ["98765"] + ["0"] * 30
    path.write_text("421 (modeld with ) parens) " + " ".join(fields))
    self.assertEqual(process_start_ticks(421, proc_root=self.root), 98765)
    self.assertEqual(process_start_ticks(422, proc_root=self.root), 0)

  def test_best_effort_start_and_fallback_transitions(self):
    artifact = self.root / "compiled.pkl"
    artifact.write_bytes(b"compiled-small")
    reasons = []
    recorded = []
    owner = ModelReceiptOwner(self.path, reasons.append, record=recorded.append)
    owner.start()
    identity = owner.capture(artifact)
    self.assertIsNotNone(identity)
    if identity is None:
      self.fail("regular fixture artifact had no identity")
    # A diagnostic identity failure leaves model operation available.
    self.assertIsNone(owner.prepare(artifact, None))
    self.assertEqual(reasons, ["artifact-identity-unavailable"])
    prepared = owner.prepare(artifact, identity)
    self.assertIsNotNone(prepared)
    # Runtime fallback only emits a tiny receipt: even a broken hash/open path
    # cannot be reached from loaded() after startup preparation.
    with mock.patch('openpilot.starpilot.models.receipt.artifact_digest', side_effect=AssertionError('bulk I/O')):
      with mock.patch('openpilot.starpilot.models.receipt.process_start_ticks', return_value=12345):
        self.assertTrue(owner.loaded(prepared, ModelVariant.SMALL, "chestnut-load-failed"))
    self.assertEqual(read_receipt(self.path).fallback_reason, "chestnut-load-failed")
    self.assertEqual(recorded, [read_receipt(self.path)])
    owner.start()
    self.assertIsNone(read_receipt(self.path))

  def test_unknown_or_failed_replacement_retires_previous_file_and_heartbeat(self):
    write_receipt(self.load, self.path)
    reasons = []
    owner = ModelReceiptOwner(self.path, reasons.append, record=lambda _load: None)
    owner.load = self.load
    self.assertFalse(owner.loaded(None, ModelVariant.CHESTNUT))
    self.assertIsNone(owner.load)
    self.assertIsNone(read_receipt(self.path))
    self.assertEqual(reasons, ['prepared-artifact-unavailable'])
    write_receipt(self.load, self.path)
    artifact = self.root / 'compiled.pkl'
    artifact.write_bytes(b'compiled')
    prepared = prepare_artifact(artifact, artifact_identity(artifact))
    with mock.patch('openpilot.starpilot.models.receipt.record_prepared', side_effect=OSError('record unavailable')):
      self.assertFalse(owner.loaded(prepared, ModelVariant.SMALL))
    self.assertIsNone(read_receipt(self.path))
    self.assertEqual(reasons[-1], 'record-failed')

  def test_failed_clear_remains_nonfatal_and_unavailable_heartbeat(self):
    write_receipt(self.load, self.path)
    reasons = []
    owner = ModelReceiptOwner(self.path, reasons.append, record=lambda _load: None)
    owner.load = self.load
    with mock.patch('openpilot.starpilot.models.receipt.clear_receipt', side_effect=OSError('unlink failed')):
      self.assertFalse(owner.loaded(None, ModelVariant.CHESTNUT))
    self.assertIsNone(owner.load)
    self.assertEqual(read_receipt(self.path), self.load)  # Last-confirmed only, never current-identity proof.
    self.assertEqual(reasons, ['clear-failed', 'prepared-artifact-unavailable'])

    class Publisher:
      raw = b''
      def send(self, _service, message):
        self.raw = message.to_bytes()

    publisher = Publisher()
    self.assertTrue(owner.publish(publisher, self.load.loaded_mono_ns + 1))
    from openpilot.cereal import messaging
    message = messaging.log_from_bytes(publisher.raw)
    self.assertEqual(json.loads(str(message.logMessage))['msg']['event'], 'modeld.identity-unavailable')

  def test_runtime_stall_reason_roundtrips_without_altering_loaded_identity(self):
    load = replace(self.load, fallback_reason='chestnut-run-stalled')
    write_receipt(load, self.path)
    self.assertEqual(read_receipt(self.path), load)
    with mock.patch('openpilot.common.swaglog.cloudlog.event') as record:
      log_loaded_model(load)
    payload = {'event': record.call_args.args[0], **record.call_args.kwargs}
    self.assertEqual(logged_model_load(json.dumps({'process': load.pid, 'msg': payload})), load)

  def test_log_identity_roundtrip_is_actual_loaded_variant_and_bound_to_process(self):
    with mock.patch('openpilot.common.swaglog.cloudlog.event') as record:
      log_loaded_model(self.load)
    payload = {'event': record.call_args.args[0], **record.call_args.kwargs}
    raw = {'process': self.load.pid, 'msg': payload}
    self.assertEqual(logged_model_load(json.dumps(raw)), self.load)
    raw['process'] += 1
    self.assertIsNone(logged_model_load(json.dumps(raw)))
    raw['process'] = self.load.pid
    payload['modelId'] = 'cinquev3'  # A big selection cannot masquerade as the small runner.
    self.assertIsNone(logged_model_load(json.dumps(raw)))
    for value in ('not json', '{' * 8193, '[]', json.dumps({'msg': 'loading model'})):
      self.assertIsNone(logged_model_load(value))

  def test_log_failure_does_not_invalidate_successful_live_receipt(self):
    reasons = []
    owner = ModelReceiptOwner(self.path, reasons.append, record=mock.Mock(side_effect=OSError('logger absent')))
    artifact = self.root / 'compiled.pkl'
    artifact.write_bytes(b'compiled')
    prepared = prepare_artifact(artifact, artifact_identity(artifact))
    with mock.patch('openpilot.starpilot.models.receipt.process_start_ticks', return_value=12345):
      self.assertTrue(owner.loaded(prepared, ModelVariant.SMALL))
    self.assertIsNotNone(read_receipt(self.path))
    self.assertEqual(reasons, ['log-failed'])


  def test_jetson_identity_published_beside_local_receipt(self):
    from collections import namedtuple
    from openpilot.cereal import messaging
    Runtime = namedtuple('Runtime', 'state active sha256 checkpoint')
    sha = 'ab' * 32

    class Publisher:
      def __init__(self):
        self.raw = []
      def send(self, _service, message):
        self.raw.append(json.loads(str(messaging.log_from_bytes(message.to_bytes()).logMessage))['msg'])

    reasons = []
    owner = ModelReceiptOwner(self.path, reasons.append, record=lambda _load: None)
    owner.load = self.load
    publisher = Publisher()
    runtime = Runtime('active', True, sha, 'ckpt')
    t = self.load.loaded_mono_ns + 1
    with mock.patch('openpilot.common.swaglog.cloudlog.event') as record:
      self.assertTrue(owner.publish(publisher, t, remote=lambda: runtime))
      self.assertFalse(owner.publish(publisher, t + 1, remote=lambda: runtime))  # rate limit holds
      self.assertTrue(owner.publish(publisher, t + 5_000_000_000, remote=lambda: runtime))
      self.assertEqual(record.call_count, 1)  # once per Jetson model, not per heartbeat
      self.assertEqual(record.call_args.kwargs['sha256'], sha)
      self.assertEqual([m['event'] for m in publisher.raw], ['modeld.loaded', 'modeld.jetlink'] * 2)
      self.assertEqual(publisher.raw[1], {'event': 'modeld.jetlink', 'version': 1, 'active': True, 'sha256': sha,
                                          'checkpoint': 'ckpt', 'sinceMonoNs': t})
      # The Jetson marker is never read as a local load.
      self.assertIsNone(logged_model_load(json.dumps({'process': self.load.pid, 'msg': publisher.raw[1]})))

      publisher.raw.clear()
      self.assertTrue(owner.publish(publisher, t + 10_000_000_000, remote=lambda: Runtime('waiting', False, None, None)))
      self.assertEqual([m['event'] for m in publisher.raw], ['modeld.loaded'])
      self.assertEqual(record.call_args.kwargs['active'], False)

      publisher.raw.clear()
      self.assertTrue(owner.publish(publisher, t + 15_000_000_000, remote=lambda: Runtime('active', True, 'not-a-sha', None)))
      self.assertEqual([m['event'] for m in publisher.raw], ['modeld.loaded'])
      self.assertTrue(owner.publish(publisher, t + 20_000_000_000, remote=lambda: None))  # link off
      self.assertTrue(owner.publish(publisher, t + 25_000_000_000, remote=mock.Mock(side_effect=OSError)))
    self.assertEqual(reasons, ['remote-publication-failed'])

if __name__ == "__main__":
  unittest.main()
