"""Retiring derived reports must never erase saved proofs or consumed starts."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.starpilot.flm.offroad_cache import AssessmentCache, MAX_FILES


class TestCacheRetention(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.cache = AssessmentCache(Path(temporary.name) / 'cache')

  def fill(self):
    return [self.cache.put({'kind': 'completed_assessment', 'unit': index}) for index in range(MAX_FILES)]

  def test_full_cache_releases_room_preserving_saved_proof_dependency_and_checkpoints(self):
    linked = self.cache.put({'kind': 'next_drive_trial'})
    saved = self.cache.put({'kind': 'next_drive_outcome', 'trial_analysis_sha256': linked})
    checkpoint = self.cache.put({'kind': 'completed_assessment', 'unit': 'checkpoint'})
    self.cache.checkpoint({'input_fingerprint': 'f' * 64, 'result': {'status': 'collecting', 'reason': 'unit', 'assessment_sha256': checkpoint}})
    for index in range(MAX_FILES - 3):
      self.cache.put({'kind': 'completed_assessment', 'unit': index})
    with self.assertRaisesRegex(ValueError, 'Assessment cache full'):
      self.cache.put({'unit': 'new'})
    self.assertEqual(self.cache.retire([saved], authorized=lambda: True), 4)
    for digest in (saved, linked, checkpoint):
      self.cache.get(digest)
    for index in range(4):
      self.cache.put({'unit': 'new', 'index': index})

  def test_consumed_claim_survives_retired_trial_and_does_not_consume_document_slot(self):
    digests = self.fill()
    self.assertTrue(self.cache.claim_trial(digests[0], 'a' * 32))
    claim = self.cache.directory / ('trial-' + digests[0] + '.json')
    raw = claim.read_bytes()
    self.cache.retire([], authorized=lambda: True)
    self.assertFalse((self.cache.directory / (digests[0] + '.json')).exists())
    self.assertEqual(claim.read_bytes(), raw)
    self.assertFalse(self.cache.claim_trial(digests[0], 'b' * 32))

  def test_recent_documents_are_kept_and_repeated_maintenance_is_idle(self):
    digests = self.fill()
    for index, digest in enumerate(digests):
      os.utime(self.cache.directory / (digest + '.json'), ns=(index + 1, index + 1))
    self.cache.retire([], authorized=lambda: True)
    for digest in digests[-8:]:
      self.cache.get(digest)
    self.assertEqual(self.cache.retire([], authorized=lambda: True), 0)

  def test_permission_loss_missing_saved_proof_and_foreign_contents_preserve_files(self):
    self.fill()
    before = set(self.cache.directory.iterdir())
    with self.assertRaisesRegex(ValueError, 'no longer permitted'):
      self.cache.retire([], authorized=lambda: False)
    self.assertEqual(set(self.cache.directory.iterdir()), before)
    with self.assertRaises(FileNotFoundError):
      self.cache.retire(['f' * 64], authorized=lambda: True)
    (self.cache.directory / 'unknown.bin').write_bytes(b'unit')
    with self.assertRaisesRegex(ValueError, 'Unexpected'):
      self.cache.retire([], authorized=lambda: True)
    self.assertTrue(before.issubset(set(self.cache.directory.iterdir())))

  def test_byte_limit_still_counts_consumed_markers(self):
    digest = self.cache.put({'unit': True})
    self.cache.claim_trial(digest, 'a' * 32)
    with patch('openpilot.starpilot.flm.offroad_cache.MAX_TOTAL', 1), self.assertRaisesRegex(ValueError, 'Assessment cache full'):
      self.cache._capacity(1)


if __name__ == '__main__':
  unittest.main()
