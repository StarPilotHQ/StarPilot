from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.starpilot.speed_limits.vision.model import Detection, VisionModelCore
from openpilot.starpilot.speed_limits.vision.speed_face import SpeedFaceVerifier
from openpilot.starpilot.speed_limits.vision.stop_signs import CapturedSignDetectorOutputs
from openpilot.starpilot.speed_limits.vision.traffic_signs import TrafficSignModelCore


class Net:
  def __init__(self, outputs):
    self.outputs = outputs
    self.requests = []

  def setInput(self, blob):
    pass

  def forward(self, names):
    self.requests.append(names)
    return self.outputs


def outputs():
  return [np.zeros((1, 5, 1344), np.float32), np.zeros((1, 5, 4096), np.float32), np.zeros((1, 1, 1024), np.float32)]


class TestTinyBranch(unittest.TestCase):
  def test_three_valid_outputs_share_one_forward_and_clear_on_input(self):
    values = outputs()
    net = Net(values)
    captured = CapturedSignDetectorOutputs(net)
    captured.setInput(np.zeros((1, 3, 256, 256), np.float32))
    self.assertIs(captured.forward(), values[0])
    self.assertIs(captured.stops, values[1])
    self.assertIs(captured.tiny_scores, values[2])
    self.assertEqual(net.requests, [['output0', 'stop/output0', 'tiny/scores']])
    captured.setInput(np.zeros((1, 3, 256, 256), np.float32))
    self.assertIsNone(captured.speed)
    self.assertIsNone(captured.tiny_scores)

  def test_malformed_new_output_invalidates_whole_observation(self):
    bad = [outputs()[:2], [*outputs()[:2], np.zeros((1, 1, 1023), np.float32)],
           [*outputs()[:2], np.zeros((1, 1, 1024), np.float64)],
           [*outputs()[:2], np.full((1, 1, 1024), float('nan'), np.float32)],
           [*outputs()[:2], np.full((1, 1, 1024), 1.01, np.float32)]]
    for values in bad:
      with self.subTest(shapes=[x.shape for x in values]):
        captured = CapturedSignDetectorOutputs(Net(values))
        with self.assertRaises(ValueError):
          captured.forward()
        self.assertIsNone(captured.speed)
        self.assertIsNone(captured.stops)
        self.assertIsNone(captured.tiny_scores)

  def test_original_even_weak_result_precedes_recovery(self):
    core = TrafficSignModelCore.__new__(TrafficSignModelCore)
    core.net = object()
    original = Detection(25, .64)
    with patch.object(VisionModelCore, '_detect_sign_from_detector_classifier', return_value=original), \
         patch.object(TrafficSignModelCore, '_recover_small_sign') as recover:
      self.assertIs(core._detect_sign_from_detector_classifier(np.zeros((760, 1344, 3), np.uint8)), original)
      recover.assert_not_called()

  def test_recovery_needs_one_unique_small_previously_unadmitted_object(self):
    for old_score, extra, height, expected in ((.01, False, 34, True), (.06, False, 34, False),
                                              (.01, True, 34, False), (.01, False, 60, False)):
      with self.subTest(old_score=old_score, extra=extra, height=height):
        core = TrafficSignModelCore.__new__(TrafficSignModelCore)
        speed, _, tiny = outputs()
        speed[0, :, 588] = [120, 150, 24, height, old_score]
        tiny[0, 0, 588] = .85
        if extra:
          speed[0, :, 600] = [210, 150, 24, 34, .01]
          tiny[0, 0, 600] = .85
        core.net = SimpleNamespace(speed=speed, tiny_scores=tiny, rgb256=np.zeros((3, 256, 256), np.float32))
        core._capture_small_sign(ratio=1., pad_width=0., pad_height=0., width=256, height=256,
                                 origin_x=600, origin_y=0, full_width=1344, full_height=760)
        self.assertEqual(core._tiny_candidate is not None, expected)
        if expected:
          record = core._tiny_candidate
          assert record is not None
          self.assertEqual(record[3], 588)

  def test_recovery_discards_weak_results_and_consumes_binding_once(self):
    for detection in (Detection(20, .95, True), Detection(20, .74, True), None):
      core = TrafficSignModelCore.__new__(TrafficSignModelCore)
      face = SpeedFaceVerifier.__new__(SpeedFaceVerifier)
      face.begin_frame()
      face.last_forward_count = 1
      face.last_forward_duration_s = .01
      core._speed_face_filter = face
      candidate = (.85, 0, (708, 133, 732, 167))
      core._tiny_candidate = (candidate, np.zeros((3, 256, 256), np.float32), np.array([120, 150, 24, 34]), 588)
      def infer(frame, *, proposals, owner=core, expected=candidate, verifier=face, response=detection):
        self.assertTrue(owner._tiny_recovery)
        self.assertEqual(proposals, [expected])
        verifier.last_forward_count = 1
        verifier.last_forward_duration_s = .02
        return response
      with patch.object(VisionModelCore, '_detect_sign_from_detector_classifier', side_effect=infer):
        result = core._recover_small_sign(np.zeros((760, 1344, 3), np.uint8))
      self.assertIs(result, detection if detection is not None and detection.confidence >= .9 else None)
      self.assertFalse(core._tiny_recovery)
      self.assertIsNone(core._tiny_candidate)
      self.assertIsNone(core._recover_small_sign(None))
      self.assertEqual(face.last_forward_count, 2)
      self.assertAlmostEqual(face.last_forward_duration_s, .03)
