"""Pure pixel/fixture outputs; no model loading or DNN inference."""
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.starpilot.speed_limits.vision import model
from openpilot.starpilot.speed_limits.vision.tests import test_terminal_material_false as fixture


@unittest.skipIf(model.cv2 is None, 'OpenCV is optional')
class TestExplicitYellowAdvisory(unittest.TestCase):
  def case(self, color, *, class_id=0, proposals=None):
    if proposals is None:
      proposals = [(.7, class_id, (1400, 100, 1490, 237))]
    core, face, proposals, frame = fixture.TestTerminalMaterialFalse.setup_case(self, proposals=proposals)
    frame[100:237, 1400:1490] = color
    return core, face, proposals, frame

  def run_case(self, case):
    return fixture.TestTerminalMaterialFalse.run_case(self, case)

  def test_clear_yellow_is_rejected_before_remember_or_reader(self):
    case = self.case((0, 255, 255))
    with patch.object(case[0], '_remember_detector_proposal', side_effect=AssertionError('yellow must not be remembered')):
      self.assertIsNone(self.run_case(case)[0])
    self.assertEqual(case[0].classifier_net.calls, 0)
    self.assertFalse(case[0].last_classifier_advisory_rejected)
    self.assertFalse(case[0].last_classifier_reject_dominant)

  def test_no_yellow_white_gray_and_yellow_tint_keep_rescue(self):
    for color in ((255, 255, 255), (90, 90, 90), (170, 210, 210)):
      with self.subTest(color=color):
        case = self.case(color)
        self.assertFalse(case[0]._is_yellow_advisory(case[3][100:237, 1400:1490]))
        result, _ = self.run_case(case)
        self.assertIsNotNone(result)
        self.assertEqual(result.speed_limit_mph, 40)
        self.assertGreater(case[0].classifier_net.calls, 0)

  def test_existing_primary_panel_keeps_separate_white_maximum(self):
    compound = np.zeros((220, 100, 3), np.uint8)
    compound[:120] = 255
    compound[128:] = (0, 255, 255)
    panel = model._primary_regulatory_panel(compound)
    self.assertLess(panel.shape[0], compound.shape[0])
    self.assertFalse(model.VisionModelCore._is_yellow_advisory(compound))
    self.assertTrue(model.VisionModelCore._is_yellow_advisory(compound[128:]))

  def test_class2_school_path_does_not_use_class0_veto(self):
    case = self.case((0, 255, 255), class_id=2)
    with patch.object(case[0], '_is_yellow_advisory', side_effect=AssertionError('school behavior unchanged')):
      self.run_case(case)
    self.assertGreater(case[0].classifier_net.calls, 0)

  def test_near_duplicate_yellow_proposal_uses_existing_overlap_suppression(self):
    proposals = [(.7, 0, (1400, 100, 1490, 237)), (.69, 0, (1401, 101, 1491, 238))]
    case = self.case((0, 255, 255), proposals=proposals)
    original = case[0]._is_yellow_advisory
    with patch.object(case[0], '_is_yellow_advisory', wraps=original) as predicate:
      self.assertIsNone(self.run_case(case)[0])
    self.assertEqual(predicate.call_count, 1)
    self.assertEqual(case[0].classifier_net.calls, 0)

  def test_strict_majority_disjoint_yellow_boundary(self):
    self.assertFalse(model.VisionModelCore._is_yellow_advisory(np.empty((0, 0, 3), np.uint8)))
    for yellow_rows, expected in ((12, False), (30, False), (50, False), (51, True)):
      with self.subTest(yellow_rows=yellow_rows):
        crop = np.full((100, 100, 3), 255, np.uint8)
        crop[:yellow_rows] = (0, 255, 255)
        self.assertEqual(model.VisionModelCore._is_yellow_advisory(crop), expected)

  def test_minority_yellow_keeps_reader_rescue(self):
    case = self.case((255, 255, 255))
    case[3][100:140, 1400:1490] = (0, 255, 255)
    self.assertFalse(case[0]._is_yellow_advisory(case[3][100:237, 1400:1490]))
    result, _ = self.run_case(case)
    self.assertIsNotNone(result)
    self.assertEqual(result.speed_limit_mph, 40)
    self.assertGreater(case[0].classifier_net.calls, 0)


if __name__ == '__main__':
  unittest.main()
