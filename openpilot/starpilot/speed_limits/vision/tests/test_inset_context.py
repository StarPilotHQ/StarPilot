import unittest

import cv2
import numpy as np

from openpilot.starpilot.speed_limits.vision.speed_face import SpeedFaceVerifier, inset_context_box


class InsetContextTests(unittest.TestCase):
  @staticmethod
  def scene(*, parent=False, clipped=False, below=False):
    image = np.full((256, 256, 3), .1, np.float32)
    box = np.asarray([232, 78, 250, 101] if clipped else [90, 90, 120, 125], np.float32)
    if parent:
      corners = ((225, 51), (300, 104)) if clipped else ((80, 50), (200, 130))
      cv2.rectangle(image, *corners, (.7, .7, .7), -1)
    else:
      cv2.rectangle(image, tuple(box[:2].astype(int)), tuple(box[2:].astype(int)), (.7, .7, .7), -1)
    if below:
      cv2.rectangle(image, (80, 128), (200, 200), (.7, .7, .7), -1)
    return np.ascontiguousarray(image.transpose(2, 0, 1)), box

  def test_standalone_face_keeps_original_crop(self):
    blob, box = self.scene()
    self.assertIsNone(inset_context_box(blob, box))

  def test_wider_board_includes_qualifying_context(self):
    blob, box = self.scene(parent=True)
    parent = inset_context_box(blob, box)
    self.assertIsNotNone(parent)
    self.assertLess(parent[1], box[1])
    self.assertGreater(parent[2], box[2])

  def test_camera_clipped_board_still_has_context(self):
    blob, box = self.scene(parent=True, clipped=True)
    self.assertIsNotNone(inset_context_box(blob, box))

  def test_separate_lower_plate_is_not_inset_context(self):
    blob, box = self.scene(below=True)
    self.assertIsNone(inset_context_box(blob, box))

  def test_irregular_background_is_not_board(self):
    image = np.full((256, 256, 3), .1, np.float32)
    points = np.asarray([[55,108], [89,112], [82,150], [47,135], [46,124]], np.int32)
    cv2.fillConvexPoly(image, points, (.7, .7, .7))
    box = np.asarray([78,120,90,134], np.float32)
    self.assertIsNone(inset_context_box(np.ascontiguousarray(image.transpose(2,0,1)), box))

  def test_only_inset_requires_positive_face_logit(self):
    class Net:
      def setInput(self, value):
        pass

      def forward(self, names):
        return [np.asarray([[-.7]], np.float32)]

    for parent in (False, True):
      with self.subTest(parent=parent):
        blob, box = self.scene(parent=parent)
        verifier = SpeedFaceVerifier.__new__(SpeedFaceVerifier)
        verifier.net = Net()
        verifier.begin_frame()
        candidate = (.8, 0, (90, 90, 120, 125))
        verifier._proposals[candidate] = [(blob, box, 1)]
        self.assertEqual(verifier.reject_once(candidate), parent)


if __name__ == '__main__':
  unittest.main()
