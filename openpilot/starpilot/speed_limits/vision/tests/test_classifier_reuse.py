import ast
from collections import deque
import dataclasses
import inspect
import textwrap
import types
import unittest
from unittest.mock import Mock

import numpy as np

from openpilot.starpilot.speed_limits.vision import model


def uncached_reference():
  tree = ast.parse(textwrap.dedent(inspect.getsource(model.VisionModelCore._detect_sign_from_detector_classifier)))
  class Calls(ast.NodeTransformer):
    replaced = 0
    def visit_Call(self, node):
      self.generic_visit(node)
      if isinstance(node.func, ast.Name) and node.func.id == 'classify_crop':
        node.func = ast.Attribute(value=ast.Name(id='self', ctx=ast.Load()),
                                  attr='_classify_speed_limit_from_model', ctx=ast.Load())
        self.replaced += 1
      return node
  calls = Calls()
  tree = calls.visit(tree)
  assert calls.replaced == 2
  namespace = dict(vars(model))
  exec(compile(ast.fix_missing_locations(tree), '<uncached numeric oracle>', 'exec'), namespace)
  return namespace['_detect_sign_from_detector_classifier']


def fixture(frame, proposals, mode='counted_reject', uncached=False, after_read=None):
  core = model.VisionModelCore.__new__(model.VisionModelCore)
  core.calls = []
  core.last_classifier_forward_count = 0
  core.latest_detector_proposal = None
  core.history = deque()
  core.classifier_net = object()
  core.classifier_input_size = 128
  core.classifier_logits_layer = None
  core.speed_enabled = True
  core.last_classifier_advisory_rejected = False
  core.last_classifier_reject_dominant = False
  core._collect_detector_classifier_proposals = Mock(side_effect=lambda _: proposals)
  core._is_regulatory_speed_sign = Mock(side_effect=lambda crop: mode == 'counted_reject' or bool(int(crop.sum()) % 3))
  def reader(crop):
    import hashlib
    digest = hashlib.sha256(crop.tobytes()).hexdigest()
    core.calls.append((digest, crop.shape))
    core.last_classifier_forward_count += 1
    value = int(digest[:8], 16)
    core.last_classifier_advisory_rejected = bool(value % 7 == 0)
    core.last_classifier_reject_dominant = bool(value % 5 == 0)
    if mode == 'counted_reject':
      core.last_classifier_advisory_rejected = core.last_classifier_reject_dominant = False
    if after_read is not None:
      after_read(core, len(core.calls))
    if mode == 'school_fallback':
      return 45, .9
    if mode in ('reject', 'counted_reject'):
      return None
    if mode == 'accepted':
      return 25, .94
    if value % 4 == 0:
      return None
    return [20, 25, 35, 45][value % 4], .6 + (value % 39) / 100
  core._classify_speed_limit_from_model = Mock(side_effect=reader)
  if uncached:
    core._detect_sign_from_detector_classifier = Mock(side_effect=types.MethodType(uncached_reference(), core))
  return core


def result(core, frame):
  detection = core._detect_sign_from_detector_classifier(frame)
  assert core.last_classifier_forward_count == len(core.calls)
  return (dataclasses.asdict(detection) if detection else None,
          dataclasses.asdict(core.latest_detector_proposal) if core.latest_detector_proposal else None,
          core.last_classifier_advisory_rejected, core.last_classifier_reject_dominant)


class TestClassifierReuse(unittest.TestCase):
  def test_school_fallback_preserves_votes_and_both_flags(self):
    frame = np.random.default_rng(5).integers(0, 256, (200, 240, 3), dtype=np.uint8)
    proposals = [(.9, 2, (85, 22, 160, 122))]
    original = fixture(frame, proposals, 'school_fallback', uncached=True)
    reused = fixture(frame, proposals, 'school_fallback')
    self.assertEqual(result(original, frame), result(reused, frame))
    self.assertEqual((len(original.calls), len(reused.calls)), (11, 10))
    self.assertEqual(original.calls[0], original.calls[-1])

  def test_none_and_flags_reused_without_counting_a_new_read(self):
    frame = np.random.default_rng(6).integers(0, 256, (200, 240, 3), dtype=np.uint8)
    proposals = [(.9, 0, (85, 22, 160, 122)), (.8, 0, (85, 22, 160, 122))]
    original = fixture(frame, proposals, uncached=True)
    reused = fixture(frame, proposals)
    self.assertEqual(result(original, frame), result(reused, frame))
    self.assertEqual((len(original.calls), len(reused.calls)), (6, 3))
    reused.reset()
    result(reused, frame)
    self.assertEqual(len(reused.calls), 6)

  def test_failure_does_not_leave_cross_invocation_cache(self):
    frame = np.zeros((200, 240, 3), np.uint8)
    def fail_second(_, count):
      if count == 2:
        raise ValueError('reader failure')
    core = fixture(frame, [(.9, 0, (85, 22, 160, 122))], after_read=fail_second)
    with self.assertRaisesRegex(ValueError, 'reader failure'):
      result(core, frame)
    self.assertEqual(len(core.calls), 2)
    result(core, frame)
    self.assertEqual(len(core.calls), 5)

  def test_model_request_and_mode_changes_invalidate_reuse(self):
    frame = np.random.default_rng(8).integers(0, 256, (200, 240, 3), dtype=np.uint8)
    proposals = [(.9, 0, (85, 22, 160, 122)), (.8, 0, (85, 22, 160, 122))]
    changes = {'classifier_net': object(), 'classifier_input_size': 129,
               'classifier_logits_layer': 'different request', 'speed_enabled': False,
               'CLASSIFIER_SHA256': '0' * 64}
    for key, value in changes.items():
      with self.subTest(key=key):
        def change(core, count, key=key, value=value):
          if count == 3:
            setattr(core, key, value)
        core = fixture(frame, proposals, after_read=change)
        result(core, frame)
        self.assertEqual(len(core.calls), 6)

  def test_same_view_with_changed_pixels_is_not_reused(self):
    frame = np.random.default_rng(9).integers(0, 256, (200, 240, 3), dtype=np.uint8)
    proposals = [(.9, 0, (85, 22, 160, 122)), (.8, 0, (85, 22, 160, 122))]
    def change(_, count):
      if count == 3:
        frame[:] ^= 255
    core = fixture(frame, proposals, after_read=change)
    result(core, frame)
    self.assertEqual(len(core.calls), 6)

  def test_memory_budget_falls_back_without_dropping_decisions(self):
    frame = np.zeros((1000, 1000, 3), np.uint8)
    proposals = [(.9, 0, (500, 100, 850, 450)), (.8, 0, (500, 100, 850, 450))]
    original = fixture(frame, proposals, uncached=True)
    reused = fixture(frame, proposals)
    self.assertEqual(result(original, frame), result(reused, frame))
    self.assertEqual(len(original.calls), 6)
    self.assertGreater(len(reused.calls), 3)
    self.assertLessEqual(len(reused.calls), 6)

  def test_oversize_crops_use_original_reader(self):
    frame = np.zeros((1600, 1600, 3), np.uint8)
    proposals = [(.9, 0, (600, 100, 1400, 900)), (.8, 0, (600, 100, 1400, 900))]
    core = fixture(frame, proposals)
    result(core, frame)
    self.assertEqual(len(core.calls), 6)


if __name__ == '__main__':
  unittest.main()
