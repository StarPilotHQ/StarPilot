import unittest
from unittest.mock import Mock, patch

import numpy as np

from openpilot.starpilot.speed_limits.vision import model
from openpilot.starpilot.speed_limits.vision.model import VisionModelCore


class TestAdvisoryRejection(unittest.TestCase):
  def test_pinned_classifier_returns_original_logits_in_one_real_forward(self):
    if model.cv2 is None:
      self.skipTest('OpenCV DNN is not available on this host')
    core = VisionModelCore(is_metric=False)
    self.assertIsNotNone(core.classifier_logits_layer)
    core._classify_speed_limit_from_model(np.zeros((32, 32, 3), np.uint8))
    self.assertEqual(core.last_classifier_forward_count, 1)
    self.assertIsInstance(core.last_classifier_advisory_rejected, bool)

  def classifier(self, *, original_digit=True):
    core = VisionModelCore.__new__(VisionModelCore)
    logits = np.full((1, 19), -12., np.float32)
    logits[0, 8 if original_digit else 18] = 12.
    probabilities = np.zeros((1, 19), np.float32)
    probabilities[0, 8], probabilities[0, 18] = .01, .99
    core.classifier_net = Mock()
    core.classifier_net.forward.return_value = (probabilities, logits)
    core.classifier_logits_layer = 'original_logits'
    core.classifier_input_size = 128
    core.last_classifier_forward_count = 0
    core.last_classifier_forward_duration_s = 0.
    return core

  def test_advisory_rejection_uses_original_logits_in_the_same_forward(self):
    core = self.classifier()
    self.assertIsNone(core._classify_speed_limit_from_model(np.zeros((32, 32, 3), np.uint8)))
    self.assertTrue(core.last_classifier_advisory_rejected)
    core.classifier_net.forward.assert_called_once_with(['output0', 'original_logits'])
    self.assertEqual(core.last_classifier_forward_count, 1)
    self.assertIsNone(core._classify_speed_limit_from_model(np.empty((0, 0, 3), np.uint8)))
    self.assertFalse(core.last_classifier_advisory_rejected)

  def test_generic_rejection_and_invalid_logits_cannot_be_an_advisory_veto(self):
    core = self.classifier(original_digit=False)
    crop = np.zeros((32, 32, 3), np.uint8)
    self.assertIsNone(core._classify_speed_limit_from_model(crop))
    self.assertFalse(core.last_classifier_advisory_rejected)
    probabilities, logits = core.classifier_net.forward.return_value
    for invalid in (logits[:, :18], np.full((1, 19), np.nan, np.float32)):
      core.classifier_net.forward.return_value = probabilities, invalid
      with self.assertRaisesRegex(ValueError, 'Invalid original classifier logits'):
        core._classify_speed_limit_from_model(crop)
      self.assertFalse(core.last_classifier_advisory_rejected)

  def detector(self, proposals):
    core = VisionModelCore.__new__(VisionModelCore)
    core._collect_detector_classifier_proposals = Mock(return_value=proposals)
    core._remember_detector_proposal = Mock()
    core.last_classifier_reject_dominant = False
    return core

  def test_confident_nonregulatory_rejection_stops_only_that_proposal(self):
    core = self.detector([(.99, 0, (240, 30, 280, 90)), (.98, 0, (241, 31, 281, 91)),
                          (.97, 0, (310, 30, 350, 90))])
    calls = 0

    def classify(_crop):
      nonlocal calls
      calls += 1
      core.last_classifier_advisory_rejected = calls == 2
      core.last_classifier_reject_dominant = core.last_classifier_advisory_rejected
      return None if calls == 2 else (35 if calls == 1 else 55, .99)

    with patch.object(core, '_classify_speed_limit_from_model', side_effect=classify), \
         patch.object(core, '_is_regulatory_speed_sign', side_effect=lambda _crop: calls >= 2):
      result = core._detect_sign_from_detector_classifier(np.random.default_rng(0).integers(0, 256, (200, 400, 3), dtype=np.uint8))
    assert result is not None
    self.assertEqual(result.speed_limit_mph, 55)
    self.assertEqual(calls, 4)  # Two advisory reads, then the existing two-read strong consensus.

  def test_white_regulatory_crop_keeps_its_expansion_fallback(self):
    core = self.detector([(.99, 0, (240, 30, 280, 90))])
    calls = 0

    def classify(_crop):
      nonlocal calls
      calls += 1
      core.last_classifier_advisory_rejected = calls == 1
      core.last_classifier_reject_dominant = core.last_classifier_advisory_rejected
      return None if calls == 1 else (55, .99)

    with patch.object(core, '_classify_speed_limit_from_model', side_effect=classify), \
         patch.object(core, '_is_regulatory_speed_sign', return_value=True):
      result = core._detect_sign_from_detector_classifier(np.random.default_rng(0).integers(0, 256, (200, 400, 3), dtype=np.uint8))
    assert result is not None
    self.assertEqual(result.speed_limit_mph, 55)
    self.assertEqual(calls, len(model.DETECTOR_CLASSIFIER_EXPANSIONS))
