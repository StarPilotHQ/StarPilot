import math
import unittest

import numpy as np

from openpilot.starpilot.speed_limits.vision.tests.test_retry_consistency import core_fixture, probabilities


class TestProposalCalibration(unittest.TestCase):
  def infer(self, proposal, calibration):
    values = [probabilities(30, .992), probabilities(30, .847), probabilities(70, .906)]
    core = core_fixture([(value, value) for value in values], [True, True, True],
                        proposals=[(proposal, 0, (770, 354, 830, 434))])
    core.PROPOSAL_LOGIT_CALIBRATION = calibration
    return core.infer(np.zeros((760, 1344, 3), np.uint8))

  def test_admission_intercept_does_not_inflate_numeric_confidence(self):
    for raw in (.21, .3, .4):
      with self.subTest(raw=raw):
        calibrated = 1 / (1 + math.exp(-(math.log(raw / (1 - raw)) + .16)))
        baseline = self.infer(raw, 0.)
        candidate = self.infer(calibrated, .16)
        self.assertEqual(candidate.speed_limit_mph, baseline.speed_limit_mph)
        self.assertAlmostEqual(candidate.confidence, baseline.confidence, places=7)
        self.assertEqual(candidate.strong_consensus, baseline.strong_consensus)


if __name__ == '__main__':
  unittest.main()
