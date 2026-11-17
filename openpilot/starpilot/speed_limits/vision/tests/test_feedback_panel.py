import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from openpilot.starpilot.speed_limits.vision.model import Detection, VisionModelCore
from openpilot.starpilot.speed_limits.vision.speed_face import SpeedFaceVerifier
from openpilot.starpilot.speed_limits.vision.stop_signs import StopDetection
from openpilot.starpilot.speed_limits.vision.traffic_signs import TrafficSignModelCore, feedback_panel_region


class TestFeedbackPanel(unittest.TestCase):
  def setUp(self):
    self.frame = np.zeros((760, 1344, 3), np.uint8)
    cv2.rectangle(self.frame, (1000, 340), (1020, 354), (0, 170, 240), -1)

  def test_requires_unique_native_amber_emission(self):
    region, emission = feedback_panel_region(self.frame)
    self.assertEqual(emission, (1000, 340, 21, 15))
    x1, y1, x2, y2 = region
    self.assertEqual((x2 - x1, y2 - y1), (384, 384))
    self.assertTrue(x1 <= emission[0] < x2 and y1 <= emission[1] < y2)
    cv2.rectangle(self.frame, (1150, 340), (1170, 354), (0, 170, 240), -1)
    self.assertIsNone(feedback_panel_region(self.frame))

  def test_rejects_blank_low_saturation_left_and_non_native_inputs(self):
    self.assertIsNone(feedback_panel_region(np.zeros_like(self.frame)))
    self.assertIsNone(feedback_panel_region(self.frame.astype(np.float32)))
    self.assertIsNone(feedback_panel_region(self.frame[:300]))
    self.assertIsNone(feedback_panel_region(np.flip(self.frame, axis=1)))
    self.frame[340:355, 1000:1021] = 180
    self.assertIsNone(feedback_panel_region(self.frame))

  def test_upper_neural_plate_only_and_original_stop_cost_preserved(self):
    core = TrafficSignModelCore.__new__(TrafficSignModelCore)
    core.net = object()
    stops = (StopDetection(.8, (800, 200, 860, 260)),)
    core.stop_detections = stops
    face = Mock(spec=SpeedFaceVerifier, last_forward_count=1, last_forward_duration_s=.01)
    core._speed_face_filter = face
    def begin():
      face.last_forward_count = 0
      face.last_forward_duration_s = 0.
    face.begin_frame = begin
    upper = (.85, 0, (1001, 260, 1022, 300))
    feedback = (.99, 0, (995, 330, 1030, 365))
    unrelated = (.95, 0, (1100, 260, 1120, 300))
    school = (.98, 2, (1001, 260, 1022, 300))
    expected = Detection(60, .84)
    def detect(frame, **kwargs):
      if not kwargs:
        return None
      self.assertEqual(kwargs['proposals'], [upper])
      face.last_forward_count = 1
      face.last_forward_duration_s = .02
      return expected
    with patch.object(VisionModelCore, '_detect_sign_from_detector_classifier', side_effect=detect), \
         patch('openpilot.starpilot.speed_limits.vision.traffic_signs.left_panel_region', return_value=None), \
         patch.object(VisionModelCore, '_collect_detector_classifier_proposals_from_region',
                      return_value=[feedback, unrelated, school, upper]) as forward:
      self.assertIs(core._detect_sign_from_detector_classifier(self.frame), expected)
      self.assertEqual(forward.call_count, 1)
      self.assertIs(core.stop_detections, stops)
      self.assertEqual(face.last_forward_count, 2)
      self.assertAlmostEqual(face.last_forward_duration_s, .03)

  def test_existing_reading_and_median_take_precedence(self):
    core = TrafficSignModelCore.__new__(TrafficSignModelCore)
    core.net = object()
    original = Detection(50, .64)
    with patch.object(VisionModelCore, '_detect_sign_from_detector_classifier', return_value=original), \
         patch('openpilot.starpilot.speed_limits.vision.traffic_signs.feedback_panel_region') as geometry:
      self.assertIs(core._detect_sign_from_detector_classifier(self.frame), original)
      geometry.assert_not_called()


if __name__ == '__main__':
  unittest.main()
