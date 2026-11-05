from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.starpilot.speed_limits.vision.traffic_signs import TrafficSignModelCore as VisionModelCore
from openpilot.starpilot.speed_limits.vision.stop_signs import StopDetection, SignDetectorOutputs, decode_stop_signs


class TestStopSigns(unittest.TestCase):
  def decode(self, raw):
    return decode_stop_signs(raw, ratio=1, pad_width=0, pad_height=0, region_width=256, region_height=256,
                             origin_x=100, origin_y=20, full_frame_width=600, full_frame_height=400, threshold=.5)

  def test_stop_output_is_independent_of_speed_and_uses_one_forward(self):
    speed = np.zeros((1, 5, 1344), np.float32)
    speed[0, 4, 0] = .3
    stops = np.zeros((1, 5, 4096), np.float32)
    stops[0, :, 0] = [128, 128, 40, 40, .99]
    calls = []
    net = SignDetectorOutputs(SimpleNamespace(forward=lambda names: calls.append(names) or (speed, stops), setInput=lambda _: None))
    self.assertIs(net.forward(), speed)
    self.assertIs(net.stops, stops)
    self.assertEqual(calls, [['output0', 'stop/output0']])
    self.assertEqual(self.decode(net.stops)[0].bbox, (208, 128, 248, 168))
    net.setInput(None)
    self.assertIsNone(net.stops)

  def test_corrupt_stop_output_is_rejected(self):
    speed = np.zeros((1, 5, 1344), np.float32)
    for stops in (np.zeros((1, 6, 4096)), np.zeros((1, 5, 1344)), np.full((1, 5, 4096), np.nan)):
      with self.subTest(shape=stops.shape), self.assertRaises(ValueError):
        SignDetectorOutputs(SimpleNamespace(forward=lambda _, output=stops: (speed, output))).forward()
      with self.assertRaises(ValueError):
        self.decode(stops)

  def test_stop_work_is_bounded_before_nms(self):
    raw = np.zeros((1, 5, 4096), np.float32)
    raw[0, :4] = np.array([128, 128, 20, 20])[:, None]
    raw[0, 4] = np.linspace(.6, .99, 4096)
    with patch('openpilot.starpilot.speed_limits.vision.stop_signs.cv2.dnn.NMSBoxes', return_value=[]) as nms:
      self.decode(raw)
    self.assertEqual(len(nms.call_args.args[0]), 32)
    self.assertEqual(nms.call_args.args[1], sorted(raw[0, 4].tolist(), reverse=True)[:32])

  def test_reset_and_invalid_new_frame_clear_stop_detections(self):
    core = VisionModelCore.__new__(VisionModelCore)
    core.history = deque()
    core.stop_detections = (StopDetection(.9, (10, 10, 30, 30)),)
    core.reset()
    self.assertEqual(core.stop_detections, ())
    core.stop_detections = (StopDetection(.9, (10, 10, 30, 30)),)
    with self.assertRaises(ValueError):
      core.infer(np.zeros((0, 0, 3), np.uint8))
    self.assertEqual(core.stop_detections, ())

  def test_sign_api_returns_both_outputs_without_an_extra_inference(self):
    core = VisionModelCore.__new__(VisionModelCore)
    stop = StopDetection(.9, (100, 20, 140, 60))
    core.stop_detections = (stop,)
    with patch.object(core, 'infer', return_value=None) as infer:
      result = core.infer_signs(np.zeros((256, 256, 3), np.uint8))
    infer.assert_called_once()
    self.assertIsNone(result.speed)
    self.assertEqual(result.stops, (stop,))

  def test_shipped_joint_model_runs_and_preserves_speed_output(self):
    from openpilot.starpilot.speed_limits.vision.model import VisionModelCore as SpeedCore
    frame = np.zeros((760, 1344, 3), np.uint8)
    joint = VisionModelCore(is_metric=False)
    speed = SpeedCore(is_metric=False)
    result = joint.infer_signs(frame)
    self.assertEqual(result.speed, speed.infer(frame))
    self.assertEqual(result.stops, ())
    self.assertEqual(joint.net.stops.shape, (1, 5, 4096))
    self.assertEqual(joint.last_classifier_forward_count, speed.last_classifier_forward_count)

  def test_stop_only_does_not_load_or_run_the_numeric_classifier(self):
    load = VisionModelCore._load_net
    calls = []

    def checked(cls, name, expected):
      calls.append(name)
      self.assertNotEqual(name, 'speed_limit_us_value_classifier.onnx')
      return load(name, expected)

    with patch.object(VisionModelCore, '_load_net', classmethod(checked)):
      core = VisionModelCore(is_metric=False, speed_enabled=False)
      frame = np.zeros((760, 1344, 3), np.uint8)
      with patch.object(core, '_classify_speed_limit_from_model', side_effect=AssertionError('Unused speed classifier')):
        self.assertIsNone(core.observe(frame, now=1.0))
    self.assertIsNone(core.classifier_net)
    self.assertEqual(core.last_classifier_forward_count, 0)
    self.assertEqual(core.last_detector_forward_count, 1)
    self.assertEqual(calls, ['speed_limit_us_detector.onnx'])

  def test_speed_mode_transition_discards_old_speed_evidence(self):
    core = VisionModelCore(is_metric=False)
    core.published_speed_limit_mph = 45
    core.published_support_count = 3
    core.last_published_support_at = 1.0
    core.set_speed_enabled(False)
    self.assertIsNone(core.classifier_net)
    self.assertEqual(core.published_speed_limit_mph, 0)
    self.assertEqual(core.last_published_support_at, 0)
    self.assertIsNone(core.observe(np.zeros((760, 1344, 3), np.uint8), now=2.0))
    core.set_speed_enabled(True)
    self.assertIsNotNone(core.classifier_net)
    self.assertEqual(core.published_speed_limit_mph, 0)
    self.assertEqual(core.history, deque())

  def test_stop_only_keeps_the_complete_declared_model_identity_check(self):
    verify = VisionModelCore._verified_model_path

    def checked(cls, name, expected):
      if name == 'speed_limit_us_value_classifier.onnx':
        raise ValueError('Vision model identity mismatch')
      return verify(name, expected)

    with patch.object(VisionModelCore, '_verified_model_path', classmethod(checked)), self.assertRaises(ValueError):
      VisionModelCore(is_metric=False, speed_enabled=False)
