import unittest

import numpy as np

from openpilot.starpilot.speed_limits.vision import model


class TestReaderSaturation(unittest.TestCase):
  def test_final_crop_only_skips_when_result_cannot_change(self):
    cases = (
      ('third_reject', 1., ((40, .999), (40, .999), None), (True, True, True), 2, 40, True),
      ('third_competitor', 1., ((40, .999), (40, .999), (80, 1.)), (True, True, True), 2, 40, True),
      ('third_same', 1., ((40, .999), (40, .999), (40, 1.)), (True, True, True), 2, 40, True),
      ('advisory_veto', 1., ((40, .999), (40, .999), None), (True, True, False), 3, None, False),
      ('uncapped', .6, ((40, .95), (40, .95), (40, 1.)), (True, True, True), 3, 40, True),
      ('disagreement', 1., ((40, .999), (50, .999), (80, 1.)), (True, True, True), 3, 40, False),
      ('school_competitor', 1., ((25, .999), (25, .999), (40, 1.)), (True, True, True), 3, 40, False),
    )
    for name, proposal, reads, regulatory, expected_calls, speed, strong in cases:
      with self.subTest(name=name):
        core = object.__new__(model.VisionModelCore)
        core._speed_face_filter = None
        core.latest_detector_proposal = None
        core.temporal_tracking_enabled = True
        core.last_classifier_advisory_rejected = False
        core.last_classifier_reject_dominant = False
        core._collect_detector_classifier_proposals = lambda frame, proposal=proposal: [(proposal, 0, (110, 10, 170, 70))]
        regions = iter(regulatory)
        core._is_regulatory_speed_sign = lambda crop, regions=regions: next(regions)
        calls = []

        def classify(crop, calls=calls, core=core, reads=reads):
          index = len(calls)
          calls.append(index)
          core.last_classifier_advisory_rejected = index == 2 and reads[index] is None
          core.last_classifier_reject_dominant = core.last_classifier_advisory_rejected
          return reads[index]

        core._classify_speed_limit_from_model = classify
        result = core._detect_sign_from_detector_classifier(np.zeros((100, 200, 3), dtype=np.uint8))
        self.assertEqual(len(calls), expected_calls)
        if speed is None:
          self.assertIsNone(result)
        else:
          self.assertEqual(result, model.Detection(speed, .95, strong))
          self.assertEqual(core.latest_detector_proposal.speed_limit_mph, speed)


if __name__ == '__main__':
  unittest.main()
