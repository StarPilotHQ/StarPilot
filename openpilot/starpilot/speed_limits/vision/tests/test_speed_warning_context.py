"""Pure pixel geometry and single-call verifier behavior; no model loading."""
import unittest
from unittest import mock

import numpy as np

from openpilot.starpilot.speed_limits.vision import speed_face

cv2 = speed_face.cv2


class TestSpeedWarningContext(unittest.TestCase):
  def setUp(self):
    if cv2 is None:
      self.skipTest("OpenCV is unavailable")

  def image(self):
    return np.zeros((256, 256, 3), np.uint8)

  def blob(self, image):
    return np.ascontiguousarray(image.transpose(2, 0, 1), dtype=np.float32) / np.float32(255)

  def diamond(self, image, points=None):
    points = points or [[128, 40], [216, 128], [128, 216], [40, 128]]
    cv2.fillConvexPoly(image, np.asarray(points, np.int32), (255, 255, 0))
    return image

  def test_diamond_keeps_entire_original_box_support(self):
    image = self.diamond(self.image())
    box = np.array([75, 115, 150, 190], np.float32)
    context = speed_face.warning_context_box(self.blob(image), box)
    np.testing.assert_array_equal(context, [40, 40, 217, 217])
    self.assertTrue(np.all(context[:2] < box[:2]) and np.all(context[2:] > box[2:]))

  def test_circle_ellipse_rectangle_and_school_header_fall_back(self):
    circle, ellipse, rectangle, header = [self.image() for _ in range(4)]
    cv2.circle(circle, (128, 128), 85, (255, 255, 0), -1)
    cv2.ellipse(ellipse, (128, 128), (95, 60), 20, 0, 360, (255, 255, 0), -1)
    cv2.rectangle(rectangle, (40, 40), (216, 216), (255, 255, 0), -1)
    cv2.rectangle(header, (70, 30), (180, 65), (255, 255, 0), -1)
    for image in [circle, ellipse, rectangle, header]:
      with self.subTest(kind=id(image)):
        self.assertIsNone(speed_face.warning_context_box(self.blob(image), [100, 90, 155, 180]))

  def test_border_outside_center_and_incomplete_containment_fall_back(self):
    image = self.diamond(self.image())
    self.assertIsNone(speed_face.warning_context_box(self.blob(image), [45, 45, 65, 65]))
    self.assertIsNone(speed_face.warning_context_box(self.blob(image), [20, 100, 180, 180]))
    touching = self.diamond(self.image(), [[128, 0], [240, 128], [128, 240], [16, 128]])
    self.assertIsNone(speed_face.warning_context_box(self.blob(touching), [100, 100, 160, 160]))

  def test_multiple_associations_are_ambiguous(self):
    image = self.diamond(self.image())
    contour = np.asarray([[128, 40], [216, 128], [128, 216], [40, 128]], np.int32)[:, None]
    with mock.patch.object(speed_face.cv2, 'findContours', return_value=([contour, contour.copy()], None)):
      self.assertIsNone(speed_face.warning_context_box(self.blob(image), [100, 100, 160, 160]))

  def test_verifier_keeps_dense_binding_and_one_speed_request(self):
    blob = self.blob(self.diamond(self.image()))
    verifier = object.__new__(speed_face.SpeedFaceVerifier)
    verifier.net = mock.Mock()
    verifier.net.forward.return_value = [np.array([[-8]], np.float32)]
    verifier.begin_frame()
    candidate = (.8, 0, (1, 2, 3, 4))
    box = np.array([100, 100, 160, 160], np.float32)
    verifier._proposals[candidate] = [(blob, box, 77)]
    self.assertTrue(verifier.reject_once(candidate))
    self.assertEqual(verifier.last_dense_id, 77)
    self.assertEqual(verifier.last_forward_count, 1)
    verifier.net.forward.assert_called_once_with(['speed_face_logit'])
    self.assertFalse(verifier.reject_once(candidate))
    self.assertEqual(verifier.net.forward.call_count, 1)

  def test_original_unresolved_and_invalid_crop_gates_precede_context(self):
    verifier = object.__new__(speed_face.SpeedFaceVerifier)
    verifier.net = mock.Mock()
    candidate = (.8, 0, (1, 2, 3, 4))
    for blob, box, invalid in [
      (self.blob(self.image()), [10, 10, 12, 12], False),
      (np.full((3, 256, 256), np.nan, np.float32), [10, 10, 30, 30], True),
    ]:
      verifier.begin_frame()
      verifier._proposals[candidate] = [(blob, np.asarray(box, np.float32), 77)]
      with mock.patch.object(speed_face, 'warning_context_box') as context:
        if invalid:
          with self.assertRaises(ValueError):
            verifier.reject_once(candidate)
        else:
          self.assertFalse(verifier.reject_once(candidate))
        context.assert_not_called()
      verifier.net.forward.assert_not_called()
      self.assertEqual(verifier.last_forward_count, 0)

  def test_concave_outline_cannot_borrow_its_convex_hull_shape(self):
    contour = np.asarray([[128, 40], [216, 128], [145, 128], [128, 216],
                          [40, 128], [110, 128]], np.int32)[:, None]
    hull = cv2.convexHull(contour)
    quad = cv2.approxPolyDP(hull, .02 * cv2.arcLength(hull, True), True)
    self.assertEqual(len(quad), 4)
    self.assertTrue(cv2.isContourConvex(quad))
    self.assertFalse(cv2.isContourConvex(contour))
    with mock.patch.object(cv2, 'findContours', return_value=([contour], None)):
      self.assertIsNone(speed_face.warning_context_box(self.blob(self.image()), [100, 100, 160, 160]))

  def test_invalid_outline_does_not_resolve_multiple_hull_associations(self):
    valid = np.asarray([[128, 40], [216, 128], [128, 216], [40, 128]], np.int32)[:, None]
    invalid = np.asarray([[128, 40], [216, 128], [145, 128], [128, 216],
                          [40, 128], [110, 128]], np.int32)[:, None]
    for contours in ([invalid, valid], [valid, invalid]):
      with self.subTest(invalid_first=contours[0] is invalid):
        with mock.patch.object(cv2, 'findContours', return_value=(contours, None)):
          self.assertIsNone(speed_face.warning_context_box(self.blob(self.image()), [100, 100, 160, 160]))
