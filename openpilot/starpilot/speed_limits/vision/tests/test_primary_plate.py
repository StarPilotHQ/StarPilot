import unittest

import numpy as np

from openpilot.starpilot.speed_limits.vision.tests.test_retry_consistency import core_fixture


class TestPrimaryPlate(unittest.TestCase):
  def infer(self, future, *, as_iterator=False):
    proposals = [(.6335, 0, (1536, 436, 1612, 583)), future]
    core = core_fixture([], [True], proposals)
    if as_iterator:
      core._collect_detector_classifier_proposals = lambda frame: iter(proposals)
    core._classify_speed_limit_from_model = lambda crop: (70 if crop.shape[0] < 140 else 75, .999)
    return core.infer(np.zeros((1208, 1928, 3), np.uint8))

  def test_near_tied_upper_plate_excludes_minimum_context(self):
    result = self.infer((.6334, 0, (1537, 438, 1609, 535)))
    self.assertEqual(result.speed_limit_mph, 70)

  def test_low_score_lower_plate_and_unrelated_boxes_keep_order(self):
    for future in ((.60, 0, (1537, 438, 1609, 535)),
                   (.6334, 0, (1537, 535, 1609, 590)),
                   (.6334, 0, (1420, 438, 1492, 535))):
      with self.subTest(future=future):
        self.assertEqual(self.infer(future).speed_limit_mph, 75)

  def test_iterator_input_keeps_existing_order(self):
    self.assertEqual(self.infer((.6334, 0, (1537, 438, 1609, 535)), as_iterator=True).speed_limit_mph, 75)


if __name__ == '__main__':
  unittest.main()
