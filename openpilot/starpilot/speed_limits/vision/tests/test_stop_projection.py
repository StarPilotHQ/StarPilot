from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.starpilot.speed_limits.vision import model, observation, traffic_signs
from openpilot.starpilot.speed_limits.vision.stop_signs import CapturedSignDetectorOutputs, CapturedStopDetectorOutputs


class TestStopProjection(unittest.TestCase):
  def raw_net(self, name):
    stops = np.zeros((1, 5, 4096), np.float32)
    speed = np.zeros((1, 5, 1344), np.float32)
    calls = []

    def forward(names=None):
      calls.append(names)
      if name == 'speed_limit_us_value_classifier.onnx':
        return np.zeros((1, 19), np.float32)
      if names == ['stop/output0']:
        return [stops]
      return [speed, stops, np.zeros((1, 1, 1024), np.float32)]

    return SimpleNamespace(setInput=lambda _: None, forward=forward, getLayerId=lambda _: 0, calls=calls)

  def core(self, enabled=True):
    owner = self

    class Identity:
      def __init__(self, _):
        self.net = object()

      def begin_frame(self):
        pass

      def end_frame(self):
        pass

      def close(self):
        self.net = None

    def load(cls, name, expected):
      owner.loads.append(name)
      return owner.raw_net(name)

    self.loads = []
    self.enterContext(patch.object(model.VisionModelCore, '_load_net', classmethod(load)))
    self.enterContext(patch.object(traffic_signs.TrafficSignModelCore, '_verified_model_path', return_value=Path('verified-model')))
    self.enterContext(patch.object(traffic_signs, 'SpeedFaceVerifier', Identity))
    return traffic_signs.TrafficSignModelCore(is_metric=False, speed_enabled=enabled)

  def test_stop_output_has_no_speed_tensor(self):
    raw = self.raw_net('traffic_signs_us_stop_detector.onnx')
    wrapped = CapturedStopDetectorOutputs(raw)
    wrapped.setInput(np.zeros((1, 3, 256, 256), np.float32))
    self.assertEqual(wrapped.forward_stops().shape, (1, 5, 4096))
    self.assertEqual(raw.calls, [['stop/output0']])
    with self.assertRaises(ValueError):
      wrapped.forward()

  def test_stop_output_rejects_invalid_shape_dtype_or_nonfinite_values(self):
    values = [np.zeros((1, 5, 1344), np.float32), np.zeros((1, 5, 4096), np.float64), np.full((1, 5, 4096), np.nan, np.float32)]
    for value in values:
      with self.subTest(shape=value.shape, dtype=value.dtype):
        wrapped = CapturedStopDetectorOutputs(SimpleNamespace(forward=lambda _, value=value: [value]))
        wrapped.stops = 'previous'
        with self.assertRaises(ValueError):
          wrapped.forward_stops()
        self.assertIsNone(wrapped.stops)

  def test_stop_output_requires_exactly_one_result(self):
    for outputs in ([], [None, None], None):
      wrapped = CapturedStopDetectorOutputs(SimpleNamespace(forward=lambda _, value=outputs: value))
      with self.assertRaises(ValueError):
        wrapped.forward_stops()

  def test_stop_capture_is_owned_and_cleared_by_new_input(self):
    wrapped = CapturedStopDetectorOutputs(self.raw_net('traffic_signs_us_stop_detector.onnx'))
    blob = np.zeros((1, 3, 256, 256), np.float32)
    wrapped.setInput(blob)
    captured = wrapped.rgb256
    blob.fill(1)
    self.assertEqual(float(captured.max()), 0)
    self.assertFalse(captured.flags.writeable)
    wrapped.forward_stops()
    wrapped.setInput(blob)
    self.assertIsNone(wrapped.stops)

  def test_stop_only_constructor_loads_no_reader_or_joint_detector(self):
    core = self.core(False)
    self.assertEqual(self.loads, ['traffic_signs_us_stop_detector.onnx'])
    self.assertIsNone(core.classifier_net)
    self.assertIsNone(core._speed_face_filter)
    self.assertIsNotNone(core._stop_face_filter.net)

  def test_mode_changes_validate_and_share_one_identity_network(self):
    core = self.core(True)
    identity = core._stop_face_filter.net
    core.published_speed_limit_mph = 45
    core.set_speed_enabled(False)
    self.assertIsInstance(core.net, CapturedStopDetectorOutputs)
    self.assertIsNone(core.classifier_net)
    self.assertIsNone(core._speed_face_filter)
    self.assertEqual(core.published_speed_limit_mph, 0)
    core.set_speed_enabled(True)
    self.assertIs(type(core.net), CapturedSignDetectorOutputs)
    self.assertIs(core._speed_face_filter.net, identity)
    self.assertIs(core._stop_face_filter.net, identity)

  def test_same_mode_reuses_loaded_networks(self):
    core = self.core(False)
    with patch.object(core, '_detector_for_mode', side_effect=AssertionError('Unexpected reload')):
      core.set_speed_enabled(False)

  def test_failed_replacement_keeps_previous_mode_and_clears_observation(self):
    core = self.core(True)
    original = core.net
    core.stop_detections = ('previous',)
    with patch.object(core, '_validate_candidate', side_effect=ValueError('Invalid candidate')):
      with self.assertRaises(ValueError):
        core.set_speed_enabled(False)
    self.assertIs(core.net, original)
    self.assertTrue(core.speed_enabled)
    self.assertEqual(core.stop_detections, ())

  def test_both_modes_bind_all_assets_and_the_wire_identity(self):
    core = self.core(False)
    self.assertEqual(len(core.ASSET_MANIFEST), 4)
    self.assertEqual(core.MODEL_ID, observation.MODEL_ID)
    with patch.object(core, '_verified_model_path') as verify, patch.object(core, '_detector_for_mode'):
      core._load_detector_net()
    self.assertEqual(
      [call.args[0] for call in verify.call_args_list], ['traffic_signs_us_detector.onnx', 'traffic_signs_us_stop_detector.onnx', 'traffic_sign_identity.onnx']
    )
    self.assertEqual(verify.call_args_list[1].args[1], core.STOP_PROJECTION_SHA256)
