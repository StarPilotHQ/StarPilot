"""Current production lifecycle with synthetic nets and the actual typed/native stack."""

import unittest
from unittest.mock import patch
import numpy as np
from openpilot.starpilot.speed_limits.vision import traffic_signs, stop_signs
from openpilot.starpilot.speed_limits.vision.tests import _identity_fixture as fixture


class TestIdentityNativeLifecycle(unittest.TestCase):
  def setUp(self):
    from openpilot.starpilot.speed_limits.vision import speed_face
    loader = patch.object(speed_face.cv2.dnn, "readNetFromONNX", side_effect=fixture.load)
    loader.start()
    self.addCleanup(loader.stop)

  def test_stop_only_shared_aux_has_zero_speed_head_and_reader(self):
    before = len(fixture.LOADS)
    core = traffic_signs.TrafficSignModelCore(is_metric=False, speed_enabled=False)
    self.assertEqual(fixture.LOADS[before:], ['joint', 'aux'])
    self.assertIsNone(core.classifier_net)
    self.assertIsNone(core._speed_face_filter)
    core.net.net.stop_hits = True
    result = core.infer_signs(np.zeros((760, 1344, 3), np.uint8))
    self.assertIsNone(result.speed)
    self.assertEqual(len(result.stops), 1)
    self.assertEqual(core.last_classifier_forward_count, 0)
    self.assertTrue(core._stop_face_filter.last_rows)
    self.assertIsNone(core.net.rgb256)

  def test_mode_switch_reuses_one_aux_and_failed_enable_is_atomic(self):
    before = len(fixture.LOADS)
    core = traffic_signs.TrafficSignModelCore(is_metric=False)
    self.assertEqual(fixture.LOADS[before:], ['joint', 'reader', 'aux'])
    net = core._stop_face_filter.net
    self.assertIs(net, core._speed_face_filter.net)
    core.set_speed_enabled(False)
    self.assertIsNone(core.classifier_net)
    self.assertIsNone(core._speed_face_filter)
    with patch.object(core, '_load_net', side_effect=RuntimeError('Synthetic reader refusal')), self.assertRaises(RuntimeError):
      core.set_speed_enabled(True)
    self.assertFalse(core.speed_enabled)
    self.assertIs(core._stop_face_filter.net, net)
    self.assertIsNone(core.classifier_net)
    before = len(fixture.LOADS)
    core.set_speed_enabled(True)
    self.assertEqual(fixture.LOADS[before:], ['reader'])
    self.assertIs(core._speed_face_filter.net, net)

  def test_nonfinite_STOP_head_invalidates_entire_current_and_clears_refs(self):
    core = traffic_signs.TrafficSignModelCore(is_metric=False, speed_enabled=False)
    core.net.net.stop_hits = True
    core.published_speed_limit_mph = 55
    core._stop_face_filter.net.logit = float('nan')
    with self.assertRaises(ValueError):
      core.infer_signs(np.zeros((760, 1344, 3), np.uint8))
    self.assertEqual(core.stop_detections, ())
    self.assertEqual(core.published_speed_limit_mph, 0)
    self.assertIsNone(core.net.rgb256)

  def test_retained_top4_veto_does_not_refill_or_rescore(self):
    raw = np.zeros((1, 5, 4096), np.float32)
    for i in range(5):
      raw[0, :, i] = [20 + i * 40, 40, 12, 12, 0.99 - i * 0.01]
    seen = []
    result = stop_signs.decode_stop_signs(
      raw,
      ratio=1,
      pad_width=0,
      pad_height=0,
      region_width=256,
      region_height=256,
      origin_x=0,
      origin_y=0,
      full_frame_width=1280,
      full_frame_height=720,
      threshold=0.6,
      accept_dense_id=lambda i: seen.append(i) or i != 0,
    )
    self.assertEqual(seen, [0, 1, 2, 3])
    self.assertEqual(len(result), 3)
    self.assertEqual([x.confidence for x in result], [float(raw[0, 4, i]) for i in [1, 2, 3]])
