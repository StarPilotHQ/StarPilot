import unittest

import numpy as np

from openpilot.starpilot.speed_limits.vision.model import (
  US_CLASSIFIER_SPEED_VALUES, VisionModelCore, _primary_regulatory_panel,
)


def stacked_panels():
  image = np.full((200, 100, 3), 200, dtype=np.uint8)
  image[5:105, 10:90] = 20
  image[108:190, 10:90] = 20
  return image


class TestSpeedPanels(unittest.TestCase):
  def test_separate_lower_plate_is_excluded(self):
    image = stacked_panels()
    crop = _primary_regulatory_panel(image)
    self.assertGreaterEqual(crop.shape[0], 105)
    self.assertLessEqual(crop.shape[0], 108)
    np.testing.assert_array_equal(crop, image[:crop.shape[0]])

  def test_single_plate_and_short_header_keep_original_input(self):
    single = np.full((200, 100, 3), 200, dtype=np.uint8)
    single[5:195, 10:90] = 20
    header = single.copy()
    header[30:40] = 200
    for image in (single, header, stacked_panels().astype(np.float32), np.zeros((40, 30, 3), dtype=np.uint8)):
      self.assertIs(_primary_regulatory_panel(image), image)

  def test_read_uses_one_existing_classifier_forward(self):
    class Net:
      calls = 0

      def setInput(self, blob):
        self.blob = blob

      def forward(self):
        self.calls += 1
        scores = np.zeros((1, len(US_CLASSIFIER_SPEED_VALUES) + 1), dtype=np.float32)
        scores[0, US_CLASSIFIER_SPEED_VALUES.index(70)] = .99
        return scores

    core = object.__new__(VisionModelCore)
    core.classifier_net = Net()
    core.classifier_input_size = 128
    core.classifier_logits_layer = None
    core.last_classifier_forward_count = 0
    core.last_classifier_forward_duration_s = 0.
    result = core._classify_speed_limit_from_model(stacked_panels())
    assert result is not None
    self.assertEqual(result[0], 70)
    self.assertEqual(core.classifier_net.calls, 1)
    self.assertEqual(core.last_classifier_forward_count, 1)


if __name__ == '__main__':
  unittest.main()
