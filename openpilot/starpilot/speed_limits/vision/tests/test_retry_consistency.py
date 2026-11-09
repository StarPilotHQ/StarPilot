from collections import deque
from pathlib import Path
from typing import Any
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.starpilot.speed_limits.vision import model, speed_face
from openpilot.starpilot.speed_limits.vision.tests import _identity_fixture as identity_fixture
from openpilot.starpilot.speed_limits.vision.traffic_signs import TrafficSignModelCore


def probabilities(value=None, confidence=.8):
  scores = np.full(19, (1 - confidence) / 18, dtype=np.float32)
  scores[18 if value is None else model.US_CLASSIFIER_SPEED_VALUES.index(value)] = confidence
  return scores


class FakeReader:
  def __init__(self, reads):
    self.reads = reads
    self.calls = 0

  def setInput(self, blob):
    self.blob = blob

  def getLayerId(self, name):
    return 1

  def forward(self, names=None):
    output, raw = self.reads[self.calls]
    self.calls += 1
    if isinstance(output, Exception):
      raise output
    return output.copy() if names is None else [output.copy(), raw.copy()]


def core_fixture(reads, regulatory, proposals=None, cls=model.VisionModelCore):
  core: Any = cls.__new__(cls)
  core.is_metric = False
  core.net = type("DetectorFixture", (), {"rgb256": None})()
  core.classifier_net = FakeReader(reads)
  core.classifier_logits_layer = 'original_logits'
  core.classifier_input_size = 128
  core.last_classifier_forward_count = 0
  core.last_classifier_forward_duration_s = 0.
  core.last_classifier_advisory_rejected = False
  core.last_classifier_reject_dominant = False
  core.temporal_tracking_enabled = False
  core.history = deque()
  core.speed_enabled = True
  core.stop_detections = ()
  core._stop_face_filter = speed_face.StopFaceVerifier(identity_fixture.Net("aux"))
  core._is_regulatory_speed_sign = lambda crop: regulatory[core.classifier_net.calls]
  core._collect_detector_classifier_proposals = lambda frame: proposals or [(.9, 0, (40, 20, 100, 100))]
  core._iter_school_zone_read_crops = lambda crop: []
  return core


class RetryConsistencyTests(unittest.TestCase):
  def setUp(self):
    self.no_model_loads = patch('cv2.dnn.readNetFromONNX', side_effect=AssertionError('Unit fixture must not load models'))
    self.no_model_loads.start()
    self.addCleanup(self.no_model_loads.stop)
    self.frame = np.zeros((200, 200, 3), dtype=np.uint8)
    self.reject = probabilities()
    self.numeric = probabilities(25, .75)

  def pair(self, scores):
    return scores, scores

  def test_conflicting_single_context_read_stops_at_original_short_circuit(self):
    reads = [self.pair(self.reject), self.pair(self.reject), self.pair(self.numeric)]
    proposals = [(.9, 0, (40, 20, 100, 100)), (.8, 0, (105, 20, 165, 100))]
    core = core_fixture(reads, [False, True, True], proposals)
    self.assertIsNone(core.infer(self.frame))
    self.assertEqual(core.classifier_net.calls, 3)
    self.assertEqual(core.last_classifier_forward_count, 3)
    self.assertFalse(core.last_classifier_advisory_rejected)

  def test_direct_numeric_evidence_remains_accepted(self):
    core = core_fixture([self.pair(self.numeric), self.pair(self.reject), self.pair(self.reject)], [True, True, True])
    self.assertEqual(core.infer(self.frame).speed_limit_mph, 25)
    self.assertEqual(core.classifier_net.calls, 3)

  def test_two_expanded_views_keep_existing_consensus(self):
    core = core_fixture([self.pair(self.reject), self.pair(self.numeric), self.pair(self.numeric)], [False, True, True])
    self.assertEqual(core.infer(self.frame).speed_limit_mph, 25)
    self.assertEqual(core.classifier_net.calls, 3)

  def test_raw_and_adjusted_reject_must_both_dominate(self):
    core = core_fixture([(self.reject, self.numeric), self.pair(self.reject), self.pair(self.numeric)], [False, True, True])
    self.assertEqual(core.infer(self.frame).speed_limit_mph, 25)

  def test_regulatory_primary_does_not_trigger_context_conflict(self):
    core = core_fixture([self.pair(self.reject), self.pair(self.reject), self.pair(self.numeric)], [True, True, True])
    self.assertEqual(core.infer(self.frame).speed_limit_mph, 25)

  def test_school_fallback_is_unchanged(self):
    core = core_fixture([self.pair(self.reject), self.pair(self.reject), self.pair(self.numeric)],
                        [False, True, True], [(.9, 2, (40, 20, 100, 100))])
    self.assertEqual(core.infer(self.frame).speed_limit_mph, 25)
    self.assertEqual(core.classifier_net.calls, 3)

  def test_new_winner_does_not_inherit_previous_proposal_conflict(self):
    low = probabilities(25, .6001)
    high = probabilities(25, .9)
    reads = [self.pair(self.reject), self.pair(self.reject), self.pair(low)] + [self.pair(high)] * 3
    proposals = [(.9, 0, (40, 20, 100, 100)), (.8, 0, (105, 20, 165, 100))]
    core = core_fixture(reads, [False, True, True, True, True, True], proposals)
    self.assertEqual(core.infer(self.frame).speed_limit_mph, 25)
    self.assertEqual(core.classifier_net.calls, 6)

  def test_missing_original_logits_does_not_invent_primary_reject_evidence(self):
    core = core_fixture([self.pair(self.reject), self.pair(self.reject), self.pair(self.numeric)], [False, True, True])
    core.classifier_logits_layer = None
    self.assertEqual(core.infer(self.frame).speed_limit_mph, 25)

  def test_empty_read_clears_previous_reject_evidence(self):
    core = core_fixture([self.pair(self.reject)], [False])
    self.assertIsNone(core._classify_speed_limit_from_model(self.frame))
    self.assertTrue(core.last_classifier_reject_dominant)
    self.assertIsNone(core._classify_speed_limit_from_model(self.frame[:0]))
    self.assertFalse(core.last_classifier_reject_dominant)

  def test_disabled_reader_clears_previous_reject_evidence_without_forward(self):
    core = core_fixture([], [])
    core.last_classifier_reject_dominant = True
    core.classifier_net = None
    self.assertIsNone(core._classify_speed_limit_from_model(self.frame))
    self.assertFalse(core.last_classifier_reject_dominant)
    self.assertEqual(core.last_classifier_forward_count, 0)

  def test_failed_read_cannot_leave_reject_evidence(self):
    core = core_fixture([(RuntimeError('reader failure'), self.reject)], [False])
    core.last_classifier_reject_dominant = True
    with self.assertRaisesRegex(RuntimeError, 'reader failure'):
      core._classify_speed_limit_from_model(self.frame)
    self.assertFalse(core.last_classifier_reject_dominant)

  def test_invalid_original_logits_cannot_leave_reject_evidence(self):
    core = core_fixture([(self.reject, np.zeros(18, dtype=np.float32))], [False])
    core.last_classifier_reject_dominant = True
    with self.assertRaisesRegex(ValueError, 'Invalid original classifier logits'):
      core._classify_speed_limit_from_model(self.frame)
    self.assertFalse(core.last_classifier_reject_dominant)

  def test_reset_and_stop_only_mode_clear_evidence(self):
    core = core_fixture([], [], cls=TrafficSignModelCore)
    core.last_classifier_reject_dominant = True
    core.reset()
    self.assertFalse(core.last_classifier_reject_dominant)
    core.last_classifier_reject_dominant = True
    from openpilot.starpilot.speed_limits.vision.stop_signs import CapturedStopDetectorOutputs
    with patch.object(core, '_detector_for_mode', return_value=CapturedStopDetectorOutputs(identity_fixture.Net('stop'))), patch.object(core, '_verified_model_path', return_value=Path('unused')):
      core.set_speed_enabled(False)
    self.assertFalse(core.last_classifier_reject_dominant)
    self.assertIsNone(core.classifier_net)
    self.assertIsNone(core.infer(self.frame))
    self.assertEqual(core.last_classifier_forward_count, 0)

  def test_stop_only_constructor_has_no_numeric_evidence_or_numeric_load(self):
    with patch.object(model.VisionModelCore, '_load_net', return_value=identity_fixture.Net('stop')) as load, \
         patch.object(TrafficSignModelCore, '_verified_model_path', return_value=Path('unused')), \
         patch('openpilot.starpilot.speed_limits.vision.traffic_signs.SpeedFaceVerifier', side_effect=lambda _: identity_fixture.fake_identity()), \
         patch.object(TrafficSignModelCore, '_validate_model_abi'):
      core = TrafficSignModelCore(is_metric=False, speed_enabled=False)
    self.assertFalse(core.last_classifier_reject_dominant)
    self.assertIsNone(core.classifier_net)
    self.assertEqual(load.call_count, 1)
    self.assertEqual(load.call_args.args[0], 'traffic_signs_us_stop_detector.onnx')
    self.assertEqual(core.last_classifier_forward_count, 0)


if __name__ == '__main__':
  unittest.main()
