import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from openpilot.starpilot.speed_limits.vision.model import Detection, VisionModelCore
from openpilot.starpilot.speed_limits.vision.traffic_signs import TrafficSignModelCore, left_panel_region


class TestMedianFallback(unittest.TestCase):
  def setUp(self):
    self.core = TrafficSignModelCore.__new__(TrafficSignModelCore)
    self.core.net = object()
    self.core.stop_detections = ('original STOP result',)
    self.frame = np.zeros((760, 1344, 3), np.uint8)
    self.face = SimpleNamespace(last_forward_count=1, last_forward_duration_s=.01)
    def begin():
      self.face.last_forward_count = 0
      self.face.last_forward_duration_s = 0.
    self.face.begin_frame = begin
    self.core._speed_face_filter = self.face

  def test_preserves_even_weak_original_reading(self):
    original = Detection(65, .64)
    with patch.object(VisionModelCore, '_detect_sign_from_detector_classifier', return_value=original), \
         patch('openpilot.starpilot.speed_limits.vision.traffic_signs.left_panel_region') as geometry:
      self.assertIs(self.core._detect_sign_from_detector_classifier(self.frame), original)
      geometry.assert_not_called()

  def test_no_extra_forward_without_unique_panel(self):
    with patch.object(VisionModelCore, '_detect_sign_from_detector_classifier', return_value=None), \
         patch('openpilot.starpilot.speed_limits.vision.traffic_signs.left_panel_region', return_value=None), \
         patch.object(VisionModelCore, '_collect_detector_classifier_proposals_from_region') as forward:
      self.assertIsNone(self.core._detect_sign_from_detector_classifier(self.frame))
      forward.assert_not_called()

  def test_fresh_region_binding_and_cumulative_cost(self):
    proposals = [(.8, 0, (493, 344, 523, 390))]
    expected = Detection(65, .95)
    def detect(frame, **kwargs):
      if not kwargs:
        return None
      self.assertIs(kwargs['proposals'], proposals)
      self.face.last_forward_count = 1
      self.face.last_forward_duration_s = .02
      return expected
    with patch.object(VisionModelCore, '_detect_sign_from_detector_classifier', side_effect=detect), \
         patch('openpilot.starpilot.speed_limits.vision.traffic_signs.left_panel_region', return_value=(377, 243, 633, 499)), \
         patch.object(VisionModelCore, '_collect_detector_classifier_proposals_from_region', return_value=proposals) as forward:
      self.assertIs(self.core._detect_sign_from_detector_classifier(self.frame), expected)
      args = forward.call_args.args
      np.testing.assert_array_equal(args[1], self.frame[243:499, 377:633])
      self.assertEqual(args[2:6], (377, 243, 1344, 760))
      self.assertEqual(self.face.last_forward_count, 2)
      self.assertAlmostEqual(self.face.last_forward_duration_s, .03)
      self.assertEqual(self.core.stop_detections, ('original STOP result',))

  def test_failure_propagates_without_losing_cost(self):
    with patch.object(VisionModelCore, '_detect_sign_from_detector_classifier', return_value=None), \
         patch('openpilot.starpilot.speed_limits.vision.traffic_signs.left_panel_region', return_value=(377, 243, 633, 499)), \
         patch.object(VisionModelCore, '_collect_detector_classifier_proposals_from_region', side_effect=ValueError('bad output')):
      with self.assertRaisesRegex(ValueError, 'bad output'):
        self.core._detect_sign_from_detector_classifier(self.frame)
      self.assertEqual(self.face.last_forward_count, 1)
      self.assertAlmostEqual(self.face.last_forward_duration_s, .01)

  def test_geometry_finds_one_panel_and_rejects_ambiguous_pair(self):
    def panel(left):
      cv2.rectangle(self.frame, (left, 350), (left + 28, 390), (180, 180, 180), -1)
      cv2.rectangle(self.frame, (left + 5, 359), (left + 23, 364), (25, 25, 25), -1)
      cv2.rectangle(self.frame, (left + 5, 376), (left + 23, 381), (25, 25, 25), -1)
    panel(493)
    region = left_panel_region(self.frame)
    self.assertIsNotNone(region)
    x1, y1, x2, y2 = region
    self.assertEqual((x2 - x1, y2 - y1), (256, 256))
    self.assertTrue(x1 < 493 < 521 < x2 and y1 < 350 < 390 < y2)
    panel(560)
    self.assertIsNone(left_panel_region(self.frame))

  def test_geometry_rejects_non_native_or_blank_inputs(self):
    self.assertIsNone(left_panel_region(self.frame))
    self.assertIsNone(left_panel_region(self.frame.astype(np.float32)))
    self.assertIsNone(left_panel_region(np.zeros((16, 100, 3), np.uint8)))


if __name__ == '__main__':
  unittest.main()
