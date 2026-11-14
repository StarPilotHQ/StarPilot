import unittest

import numpy as np

from openpilot.starpilot.speed_limits.vision.tests.test_retry_consistency import core_fixture, probabilities


class TestSmallObjectRepeat(unittest.TestCase):
  def setUp(self):
    self.frame = np.random.default_rng(21).integers(0, 256, (760, 1344, 3), dtype=np.uint8)
    self.proposals = [(.8, 0, (800, 300, 825, 331)), (.7, 0, (799, 300, 826, 332)),
                      (.6, 0, (800, 301, 826, 331))]

  def core(self, proposals=None, values=None):
    values = values or [50] * 9
    reads = [(probabilities(value, .94), probabilities(value, .94)) for value in values]
    return core_fixture(reads, [True] * 20, proposals or self.proposals)

  def test_three_agreeing_crops_keep_the_weak_result_without_duplicate_reads(self):
    core = self.core()
    detection = core.infer(self.frame)
    self.assertEqual(detection.speed_limit_mph, 50)
    self.assertEqual(detection.confidence, .64)
    self.assertFalse(detection.strong_consensus)
    self.assertEqual(core.classifier_net.calls, 3)

  def test_distinct_objects_still_receive_their_numeric_reads(self):
    core = self.core([*self.proposals, (.5, 0, (900, 300, 925, 331))], [50] * 12)
    self.assertIsNotNone(core.infer(self.frame))
    self.assertGreater(core.classifier_net.calls, 3)

  def test_conflicting_crops_do_not_suppress_later_proposals(self):
    core = self.core(values=[50, 40, 50, 50, 50, 50, 50, 50, 50])
    self.assertIsNotNone(core.infer(self.frame))
    self.assertGreater(core.classifier_net.calls, 3)

  def test_unsaturated_weak_read_keeps_a_stronger_overlapping_proposal(self):
    confidences = [.61, .62, .99, .999, .999, .999]
    reads = [(probabilities(50, c), probabilities(50, c)) for c in confidences]
    proposals = [(.8, 0, (800, 300, 827, 339)), (.7, 0, (799, 299, 827, 340))]
    core = core_fixture(reads, [True] * 10, proposals)
    detection = core.infer(self.frame)
    self.assertEqual(detection.speed_limit_mph, 50)
    self.assertEqual(detection.confidence, .95)
    self.assertTrue(detection.strong_consensus)
    self.assertEqual(core.classifier_net.calls, 6)

  def test_weak_read_does_not_replace_a_confirmed_limit(self):
    core = self.core()
    core.published_speed_limit_mph = 60
    core.published_confidence = .95
    core.published_support_count = 1
    core.previous_published_speed_limit_mph = 0
    core.last_publish_change_at = 1.
    core.episode = 1
    result = core.observe(self.frame, now=100.)
    self.assertEqual(result, (60, .95, 1, 1))
    self.assertEqual(core.classifier_net.calls, 3)


if __name__ == '__main__':
  unittest.main()
