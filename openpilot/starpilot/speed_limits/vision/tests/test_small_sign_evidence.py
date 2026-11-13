from types import SimpleNamespace
import unittest

import numpy as np

from openpilot.starpilot.speed_limits.vision.speed_face import SpeedFaceVerifier
from openpilot.starpilot.speed_limits.vision.tests.test_retry_consistency import core_fixture, probabilities


class TestSmallSignEvidence(unittest.TestCase):
  def infer(self, *, score=.85, logit=2.9, isolated=True, bound=True, reads=((20, .983), (20, .968), (20, .996)), box=(881, 339, 908, 373)):
    proposal = (score, 0, box)
    values = [probabilities(value, confidence) for value, confidence in reads]
    core = core_fixture([(value, value) for value in values], [True] * len(values), [proposal])
    core._tiny_recovery = True
    core._speed_face_filter = SimpleNamespace(
      last_identity_logit=logit, last_verified_candidate=proposal if bound else (.84, 0, box),
      last_isolated_face=isolated, reject_once=lambda candidate: False, end_frame=lambda: None)
    result = core.infer(np.zeros((760, 1344, 3), np.uint8))
    return result, core.classifier_net.calls

  def test_bound_identity_and_three_strong_crops_can_overcome_size_cap(self):
    result, calls = self.infer()
    self.assertEqual(result.speed_limit_mph, 20)
    self.assertEqual(result.confidence, .95)
    self.assertTrue(result.strong_consensus)
    self.assertEqual(calls, 3)

  def test_each_evidence_requirement_preserves_original_size_cap(self):
    for change in ({'score': .79}, {'logit': 2.74}, {'logit': float('nan')},
                   {'isolated': False}, {'bound': False},
                   {'reads': ((20, .983), (20, .95), (20, .996))},
                   {'reads': ((20, .983), (20, .968), (20, .99))},
                   {'reads': ((20, .983), (70, .968), (20, .996))}):
      with self.subTest(change=change):
        result, _ = self.infer(**change)
        self.assertTrue(result is None or result.confidence <= .74)

  def test_original_minimum_native_pixels_still_required(self):
    result, _ = self.infer(box=(881, 339, 894, 356))
    self.assertIsNone(result)

  def test_identity_evidence_expires_with_frame_binding(self):
    face = SpeedFaceVerifier.__new__(SpeedFaceVerifier)
    face.begin_frame()
    face.last_identity_logit = 9.
    face.last_verified_candidate = (.85, 0, (881, 339, 908, 373))
    face.last_isolated_face = True
    face.end_frame()
    self.assertIsNone(face.last_identity_logit)
    self.assertIsNone(face.last_verified_candidate)
    self.assertFalse(face.last_isolated_face)
    face.begin_frame()
    self.assertIsNone(face.last_identity_logit)
