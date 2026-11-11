"""Pure regulatory-material pixels; no model construction or inference."""
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.starpilot.speed_limits.vision import model


@unittest.skipIf(model.cv2 is None, 'OpenCV is optional')
class TestMaterialValidation(unittest.TestCase):
  def validate(self, image):
    return model.VisionModelCore._is_regulatory_speed_sign(None, image)

  def panel(self, hue=None):
    hsv = np.zeros((100, 80, 3), np.uint8)
    if hue is not None:
      hsv[:] = (hue, 255, 255)
    hsv[10:90, 15:65] = (0, 0, 255)
    hsv[40:50, 25:55] = 0
    return model.cv2.cvtColor(hsv, model.cv2.COLOR_HSV2BGR)

  def test_plain_panel_and_each_color_veto(self):
    self.assertTrue(self.validate(self.panel()))
    for hue in (25, 0, 60, 110):
      with self.subTest(hue=hue):
        self.assertFalse(self.validate(self.panel(hue)))

  def test_empty_white_dark_and_component_rejections(self):
    self.assertFalse(self.validate(np.empty((0, 0, 3), np.uint8)))
    for value in (0, 255):
      self.assertFalse(self.validate(np.full((100, 80, 3), value, np.uint8)))
    image = np.zeros((100, 80, 3), np.uint8)
    image[40:60, 5:75] = 255
    self.assertFalse(self.validate(image))

  def test_early_rejection_does_not_evaluate_later_hue_masks(self):
    operations = []

    class Hue:
      def __init__(self, values):
        self.values = values

      def __ge__(self, value):
        operations.append(('ge', value))
        return self.values >= value

      def __le__(self, value):
        operations.append(('le', value))
        return self.values <= value

    class HSV:
      def __init__(self, values):
        self.values = values

      def __getitem__(self, key):
        values = self.values[key]
        return Hue(values) if key[2] == 0 else values

    for image, expected in ((np.zeros((100, 80, 3), np.uint8), []),
                            (self.panel(25), [('ge', 12), ('le', 45)])):
      operations.clear()
      hsv = model.cv2.cvtColor(image, model.cv2.COLOR_BGR2HSV)
      with patch.object(model.cv2, 'cvtColor', return_value=HSV(hsv)):
        self.assertFalse(self.validate(image))
      self.assertEqual(operations, expected)


if __name__ == '__main__':
  unittest.main()
