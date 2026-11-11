"""Synthetic pixels/default methods and fake outputs; no model loading or inference."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.starpilot.speed_limits.vision import model, speed_face


class Reader:
  def __init__(self, advisory=False, confidence=.999):
    self.calls = 0
    self.advisory = advisory
    self.confidence = confidence

  def setInput(self, value):
    self.input = value

  def forward(self, names=None):
    self.calls += 1
    values = np.zeros((1, 19), np.float32)
    values[0, model.US_CLASSIFIER_SPEED_VALUES.index(40)] = self.confidence
    values[0, 18] = 1 - self.confidence
    if self.advisory:
      rejected = np.zeros((1, 19), np.float32)
      rejected[0, 18] = .95
      rejected[0, model.US_CLASSIFIER_SPEED_VALUES.index(40)] = .05
      return [rejected, values]
    return values


class Head:
  def __init__(self, logit):
    self.logit = logit
    self.calls = 0

  def setInput(self, value):
    self.input = value

  def forward(self, names):
    assert names == ['speed_face_logit']
    self.calls += 1
    return [np.array([[self.logit]], np.float32)]


@unittest.skipIf(model.cv2 is None, 'OpenCV is optional')
class TestTerminalMaterialFalse(unittest.TestCase):
  def setup_case(self, *, proposals=None, control=False, face_type=speed_face.SpeedFaceVerifier):
    core = object.__new__(model.VisionModelCore)
    core.latest_detector_proposal = None
    core.temporal_tracking_enabled = True
    core.classifier_net = Reader()
    core.classifier_input_size = 128
    core.classifier_logits_layer = None
    core.last_classifier_forward_count = 0
    core.last_classifier_forward_duration_s = 0.
    core.last_classifier_advisory_rejected = False
    core.last_classifier_reject_dominant = False
    face = object.__new__(face_type)
    face.begin_frame()
    face._checked = True
    face.net = Head(8)
    core._speed_face_filter = face
    if control:
      core.detector_classifier_expansions = list(model.DETECTOR_CLASSIFIER_EXPANSIONS)
    proposals = proposals if proposals is not None else [(.4, 0, (1400, 100, 1490, 237))]
    frame = np.zeros((1100, 2000, 3), np.uint8)
    return core, face, proposals, frame

  def run_case(self, case):
    core, _, proposals, frame = case
    with patch.object(core, '_collect_detector_classifier_proposals', return_value=proposals):
      result = core._detect_sign_from_detector_classifier(frame)
    return result, core.latest_detector_proposal

  def test_terminal_canonical_elides_only_unadmittable_reader_work(self):
    actual, control = self.setup_case(), self.setup_case(control=True)
    self.assertEqual(self.run_case(actual), self.run_case(control))
    self.assertEqual(actual[0].classifier_net.calls, 0)
    self.assertEqual(control[0].classifier_net.calls, 3)
    self.assertFalse(actual[0].last_classifier_advisory_rejected)

  def test_first_original_identity_keep_or_veto_is_preserved(self):
    for logit in (-8, 8):
      for kind in (speed_face.SpeedFaceVerifier, speed_face.SharedSpeedFaceVerifier):
        with self.subTest(logit=logit, kind=kind):
          case = self.setup_case(face_type=kind)
          core, face, proposals, _ = case
          face._checked = False
          face.net = Head(logit)
          face._proposals[proposals[0]] = [(np.zeros((3, 256, 256), np.float32), np.array([80, 80, 100, 110], np.float32), 13)]
          self.assertIsNone(self.run_case(case)[0])
          self.assertEqual(face.net.calls, 1)
          self.assertEqual(face.last_dense_id, 13)
          self.assertEqual(core.classifier_net.calls, 0)

  def test_later_material_true_falls_back_and_keeps_output(self):
    actual, control = self.setup_case(), self.setup_case(control=True)
    for case in (actual, control):
      case[3][220:271, 1430:1480] = 255
    self.assertEqual(self.run_case(actual), self.run_case(control))
    self.assertGreater(actual[0].classifier_net.calls, 0)

  def test_custom_material_and_reader_methods_fall_back(self):
    for name in ('_is_regulatory_speed_sign', '_classify_speed_limit_from_model'):
      case = self.setup_case()
      with patch.object(case[0], name, wraps=getattr(case[0], name)):
        self.run_case(case)
      self.assertEqual(case[0].classifier_net.calls, 2)

  def test_stateful_advisory_veto_callback_keeps_original_order(self):
    case = self.setup_case()
    core = case[0]
    core.classifier_net = Reader(advisory=True)
    core.classifier_logits_layer = 'original'
    calls = []

    def veto(candidate):
      calls.append(candidate)
      return len(calls) == 2

    core._speed_face_filter = SimpleNamespace(reject_once=veto, _checked=True)
    self.assertIsNone(self.run_case(case)[0])
    self.assertEqual(len(calls), 1)
    self.assertEqual(core.classifier_net.calls, 1)
    self.assertTrue(core.last_classifier_advisory_rejected)

  def test_strong_trusted_and_custom_expansions_keep_reads(self):
    variants = [
      {'proposals': [(.6, 0, (1400, 100, 1490, 237))]},
      {'proposals': [(.18, 0, (1040, 100, 1120, 155))]},
      {'control': True},
    ]
    for kwargs in variants:
      with self.subTest(kwargs=kwargs):
        case = self.setup_case(**kwargs)
        self.run_case(case)
        self.assertGreaterEqual(case[0].classifier_net.calls, 3)

  def test_noncanonical_face_and_unchecked_custom_method_fall_back(self):
    class CustomFace(speed_face.SpeedFaceVerifier):
      pass

    for kind in (CustomFace, speed_face.SpeedFaceVerifier):
      case = self.setup_case(face_type=kind)
      if kind is speed_face.SpeedFaceVerifier:
        with patch.object(case[1], 'reject_once', return_value=False):
          self.run_case(case)
      else:
        self.run_case(case)
      self.assertEqual(case[0].classifier_net.calls, 2)

    case = self.setup_case()
    case = (case[0], case[1], case[2], case[3].astype(np.float32))
    self.run_case(case)
    self.assertEqual(case[0].classifier_net.calls, 2)

  def test_previous_winner_and_latest_proposal_match_control(self):
    proposals = [(.4, 0, (1100, 400, 1190, 537)), (.3, 0, (1400, 100, 1490, 237))]
    actual = self.setup_case(proposals=proposals)
    control = self.setup_case(proposals=proposals, control=True)
    for case in (actual, control):
      case[0].classifier_net = Reader(confidence=.6)
      case[3][400:537, 1100:1190] = 255
      case[3][430:485, 1130:1160] = 0
    actual_result = self.run_case(actual)
    self.assertEqual(actual_result, self.run_case(control))
    self.assertIsNotNone(actual_result[0])
    self.assertEqual(actual_result[0].speed_limit_mph, 40)
    self.assertGreater(actual[0].classifier_net.calls, 0)
    self.assertLess(actual[0].classifier_net.calls, control[0].classifier_net.calls)


  def test_descending_suffix_empty_votes_and_ties_preserve_tracking(self):
    proposals = [(.4, 0, (1400, 100, 1490, 237)), (.4, 0, (1550, 150, 1650, 300)),
                 (.3, 0, (1650, 200, 1750, 350))]
    for advisory in (False, True):
      with self.subTest(advisory=advisory):
        actual = self.setup_case(proposals=proposals)
        control = self.setup_case(proposals=proposals, control=True)
        for case in (actual, control):
          case[0].classifier_net = Reader(advisory=advisory)
          case[0].classifier_logits_layer = 'original' if advisory else None
        self.assertEqual(self.run_case(actual), self.run_case(control))
        self.assertEqual(actual[0].classifier_net.calls, 0)
        self.assertGreater(control[0].classifier_net.calls, 0)
        self.assertEqual(actual[0].latest_detector_proposal.bbox, proposals[0][2])
        self.assertFalse(actual[0].last_classifier_advisory_rejected)

  def test_suffix_future_eligible_or_unsupported_falls_back(self):
    first = (.4, 0, (1400, 100, 1490, 237))
    variants = [(.5, 0, (1550, 150, 1650, 300)), (.6, 0, (1550, 150, 1650, 300)),
                (.3, 2, (1550, 150, 1650, 300)), (.3, 0, (1040, 100, 1120, 155)),
                (.3, 0, (1550, 150, 1550, 300))]
    for future in variants:
      with self.subTest(future=future):
        actual = self.setup_case(proposals=[first, future])
        control = self.setup_case(proposals=[first, future], control=True)
        self.assertEqual(self.run_case(actual), self.run_case(control))
        self.assertGreater(actual[0].classifier_net.calls, 0)

  def test_suffix_future_material_true_keeps_advisory_behavior(self):
    proposals = [(.4, 0, (1400, 100, 1490, 237)), (.3, 0, (1550, 150, 1650, 300))]
    actual, control = self.setup_case(proposals=proposals), self.setup_case(proposals=proposals, control=True)
    for case in (actual, control):
      case[3][150:300, 1550:1650] = 255
      case[3][190:240, 1580:1620] = 0
      case[0].classifier_net = Reader(advisory=True)
      case[0].classifier_logits_layer = 'original'
    self.assertEqual(self.run_case(actual), self.run_case(control))
    self.assertGreater(actual[0].classifier_net.calls, 0)
    self.assertEqual(actual[0].last_classifier_advisory_rejected, control[0].last_classifier_advisory_rejected)

  def test_suffix_custom_remember_retains_callbacks(self):
    proposals = [(.4, 0, (1400, 100, 1490, 237)), (.3, 0, (1550, 150, 1650, 300))]
    actual = self.setup_case(proposals=proposals)
    calls = []
    original = actual[0]._remember_detector_proposal
    def remember(*args, **kwargs):
      calls.append((args, kwargs))
      return original(*args, **kwargs)
    with patch.object(actual[0], '_remember_detector_proposal', side_effect=remember):
      self.run_case(actual)
    self.assertEqual(len(calls), 2)
    self.assertGreater(actual[0].classifier_net.calls, 0)

  def test_suffix_prior_winner_remains_and_skips_only_suffix(self):
    proposals = [(.3, 0, (1100, 400, 1190, 537)), (.4, 0, (1400, 100, 1490, 237)),
                 (.3, 0, (1550, 150, 1650, 300))]
    actual, control = self.setup_case(proposals=proposals), self.setup_case(proposals=proposals, control=True)
    for case in (actual, control):
      case[0].classifier_net = Reader(confidence=.6)
      case[3][400:537, 1100:1190] = 255
      case[3][430:485, 1130:1160] = 0
    result = self.run_case(actual)
    self.assertEqual(result, self.run_case(control))
    self.assertIsNotNone(result[0])
    self.assertEqual(result[0].speed_limit_mph, 40)
    self.assertLess(actual[0].classifier_net.calls, control[0].classifier_net.calls)


  def test_suffix_preserves_prior_retry_conflict_return(self):
    class RetryReader(Reader):
      def forward(self, names=None):
        values = super().forward(names)
        if self.calls == 2:
          return [values, values]
        rejected = np.zeros((1, 19), np.float32)
        rejected[0, 18] = .99
        rejected[0, model.US_CLASSIFIER_SPEED_VALUES.index(40)] = .01
        return [rejected, rejected]

    proposals = [(.5, 0, (1100, 400, 1190, 537)), (.4, 0, (1400, 100, 1490, 237)),
                 (.3, 0, (1550, 150, 1650, 300))]
    actual, control = self.setup_case(proposals=proposals), self.setup_case(proposals=proposals, control=True)
    results = []
    for case in (actual, control):
      core = case[0]
      core.classifier_net = RetryReader(confidence=.61)
      core.classifier_logits_layer = 'original'
      material_calls = []
      patch_refs = []
      def material(crop, material_calls=material_calls, patch_refs=patch_refs):
        material_calls.append(crop.shape)
        if len(material_calls) == 3:
          patch_refs[0].stop()
        return len(material_calls) == 2
      material_patch = patch.object(core, '_is_regulatory_speed_sign', side_effect=material)
      patch_refs.append(material_patch)
      material_patch.start()
      try:
        results.append(self.run_case(case))
      finally:
        material_patch.stop()
    self.assertEqual(results[0], results[1])
    self.assertIsNone(results[0][0])
    self.assertEqual(actual[0].latest_detector_proposal.speed_limit_mph, 40)
    self.assertLess(actual[0].classifier_net.calls, control[0].classifier_net.calls)
