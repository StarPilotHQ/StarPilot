import unittest

import numpy as np

from openpilot.starpilot.speed_limits.vision.tests.test_retry_consistency import core_fixture, probabilities


class TestWidthRounding(unittest.TestCase):
  def test_narrow_plate_needs_original_height_area_and_consistent_reads(self):
    for width, height, confident in ((27, 43, True), (28, 40, True), (27, 41, False), (26, 44, False), (29, 39, False)):
      with self.subTest(width=width, height=height):
        values = [probabilities(30, .992), probabilities(30, .847), probabilities(70, .906)]
        core = core_fixture([(value, value) for value in values], [False, False, False],
                            proposals=[(.447, 0, (770, 354, 770 + width, 354 + height))])
        result = core.infer(np.zeros((760, 1344, 3), np.uint8))
        self.assertIsNotNone(result)
        self.assertEqual(result.speed_limit_mph, 30)
        self.assertEqual(result.confidence >= .83, confident)


if __name__ == '__main__':
  unittest.main()
