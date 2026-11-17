import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from openpilot.starpilot.speed_limits.vision import model


class TestFinalUnusableCrop(unittest.TestCase):
  def run_case(self, *, proposals=None, regulatory=(False, False, False), reads=None,
               control=False, custom=False, net=True, primary_reject=False, veto_at=None):
    core = object.__new__(model.VisionModelCore)
    core.latest_detector_proposal = None
    core.temporal_tracking_enabled = True
    core.last_classifier_advisory_rejected = False
    core.last_classifier_reject_dominant = False
    if net:
      core.classifier_net = object()
    proposals = proposals if proposals is not None else [(.4, 0, (1400, 100, 1490, 237))]
    if control or custom:
      core.detector_classifier_expansions = list(model.DETECTOR_CLASSIFIER_EXPANSIONS)
    regions = iter(regulatory)
    region_count = [0]

    def color(crop):
      region_count[0] += 1
      return next(regions)

    calls, identity_calls = [], []
    values = iter(reads) if reads is not None else None

    def classify(crop):
      index = len(calls)
      calls.append(region_count[0])
      result = next(values) if values is not None else (40, .8)
      core.last_classifier_advisory_rejected = result is None and not primary_reject
      core.last_classifier_reject_dominant = primary_reject and index == 0
      return result

    def reject(candidate):
      identity_calls.append(candidate)
      return len(identity_calls) == veto_at

    core._speed_face_filter = SimpleNamespace(reject_once=reject)
    with patch.multiple(
      core,
      _collect_detector_classifier_proposals=lambda frame: proposals,
      _iter_school_zone_read_crops=lambda crop: [(crop, 1.)],
      _is_regulatory_speed_sign=color,
      _classify_speed_limit_from_model=classify,
    ):
      result = core._detect_sign_from_detector_classifier(np.zeros((1100, 2000, 3), dtype=np.uint8))
    return result, core.latest_detector_proposal, calls, identity_calls

  def test_only_final_unusable_read_is_skipped(self):
    actual = self.run_case()
    control = self.run_case(control=True)
    self.assertEqual(actual[:2], control[:2])
    self.assertEqual(actual[2], [1, 2])
    self.assertEqual(control[2], [1, 2, 3])
    self.assertEqual(actual[3], control[3])

  def test_eligibility_counterexamples_keep_all_reads(self):
    cases = (
      ('strong_boundary', {'proposals': [(.60, 0, (1400, 100, 1490, 237))]}),
      ('trusted_shape_boundary', {'proposals': [(.18, 0, (1040, 100, 1120, 155))]}),
      ('tiny_trusted', {'proposals': [(.18, 0, (1040, 100, 1060, 130))]}),
      ('accumulated_scores', {'regulatory': (True, False, False)}),
      ('regulatory', {'regulatory': (True, True, True)}),
      ('custom_expansions', {'custom': True}),
      ('missing_net', {'net': False}),
    )
    for name, kwargs in cases:
      with self.subTest(name=name):
        actual = self.run_case(**kwargs)
        control = self.run_case(control=True, **kwargs)
        self.assertEqual(actual[:2], control[:2])
        self.assertEqual(actual[2], [1, 2, 3])
        self.assertEqual(actual[3], control[3])

  def test_nonfinal_proposal_keeps_its_final_read(self):
    proposals = [(.4, 0, (1400, 100, 1490, 237)), (.3, 0, (1550, 150, 1650, 300))]
    actual = self.run_case(proposals=proposals, regulatory=(False,) * 6)
    control = self.run_case(proposals=proposals, regulatory=(False,) * 6, control=True)
    self.assertEqual(actual[:2], control[:2])
    self.assertEqual(actual[2], [1, 2, 3, 4, 5])
    self.assertEqual(control[2], [1, 2, 3, 4, 5, 6])

  def test_prior_best_and_retry_conflict_are_preserved(self):
    proposals = [(.4, 0, (1400, 100, 1490, 237)), (.3, 0, (1550, 150, 1650, 300))]
    cases = (
      ((True, True, True, False, False, False), ((40, .5),) * 3 + ((50, .8),) * 3, False, 40),
      ((False, True, True, False, False, False), (None, (40, .5), None) + ((50, .8),) * 3, True, None),
    )
    for regulatory, reads, primary_reject, expected in cases:
      with self.subTest(retry_conflict=primary_reject):
        kwargs = {'proposals': proposals, 'regulatory': regulatory, 'reads': reads, 'primary_reject': primary_reject}
        actual = self.run_case(**kwargs)
        control = self.run_case(control=True, **kwargs)
        self.assertEqual(actual[:2], control[:2])
        self.assertEqual(None if actual[0] is None else actual[0].speed_limit_mph, expected)
        self.assertEqual(len(actual[2]), 5)
        self.assertEqual(len(control[2]), 6)

  def test_school_class_is_unchanged(self):
    reads = (None,) * len(model.SCHOOL_ZONE_DIRECT_EXPANSIONS) + ((40, .8),) * 3
    kwargs = {'proposals': [(.4, 2, (1400, 100, 1490, 237))], 'reads': reads}
    actual = self.run_case(**kwargs)
    control = self.run_case(control=True, **kwargs)
    self.assertEqual(actual, control)
    self.assertEqual(len(actual[2]), 4)

  def test_identity_veto_runs_before_skip(self):
    actual = self.run_case(veto_at=3)
    self.assertIsNone(actual[0])
    self.assertIsNone(actual[1])
    self.assertEqual(actual[2], [1, 2])
    self.assertEqual(len(actual[3]), 3)


  def test_saturated_tiny_best_skips_only_last_class0_read(self):
    kwargs = {'proposals': [(.5, 0, (1400, 100, 1420, 130)), (.60, 0, (1500, 100, 1590, 237))],
              'regulatory': (False,) * 6,
              'reads': ((40, .999),) * 3 + ((55, .999),) * 3}
    actual = self.run_case(**kwargs)
    control = self.run_case(control=True, **kwargs)
    self.assertEqual(actual[:2], control[:2])
    self.assertEqual(actual[0].speed_limit_mph, 40)
    self.assertEqual(actual[0].confidence, model.DETECTOR_CLASSIFIER_RESCUE_MAX_SCORE)
    self.assertEqual(len(actual[2]), 5)
    self.assertEqual(len(control[2]), 6)
    self.assertEqual(actual[3], control[3])

  def test_saturated_skip_keeps_identity_veto_before_reader(self):
    kwargs = {'proposals': [(.5, 0, (1400, 100, 1420, 130)), (.60, 0, (1500, 100, 1590, 237))],
              'regulatory': (False,) * 6, 'reads': ((40, .999),) * 3 + ((55, .999),) * 2, 'veto_at': 6}
    actual = self.run_case(**kwargs)
    control = self.run_case(control=True, **kwargs)
    self.assertEqual(actual, control)
    self.assertIsNone(actual[0])
    self.assertIsNone(actual[1])
    self.assertEqual(len(actual[3]), 6)

  def test_saturated_skip_counterexamples_keep_reads(self):
    first = (.5, 0, (1400, 100, 1420, 130))
    last = (.60, 0, (1500, 100, 1590, 237))
    cases = (
      {'proposals': [(.4, 0, first[2]), last]},
      {'proposals': [first, last], 'custom': True},
      {'proposals': [first, last, (.3, 1, (1600, 100, 1690, 237))]},
    )
    for case in cases:
      with self.subTest(case=case):
        kwargs = {'regulatory': (False,) * 6, 'reads': ((40, .999),) * 3 + ((55, .999),) * 3, **case}
        actual = self.run_case(**kwargs)
        control = self.run_case(control=True, **kwargs)
        self.assertEqual(actual, control)
        self.assertEqual(len(actual[2]), 6)

  def test_saturated_skip_keeps_best_when_last_crop_advisory_rejects(self):
    kwargs = {'proposals': [(.5, 0, (1400, 100, 1420, 130)), (.60, 0, (1500, 100, 1590, 237))],
              'regulatory': (False,) * 6,
              'reads': ((40, .999),) * 3 + ((55, .999),) * 2 + (None,)}
    actual = self.run_case(**kwargs)
    control = self.run_case(control=True, **kwargs)
    self.assertEqual(actual[:2], control[:2])
    self.assertEqual(len(actual[2]), 5)
    self.assertEqual(len(control[2]), 6)
    self.assertEqual(actual[3], control[3])

  def test_saturated_skip_keeps_generator_and_school_paths(self):
    proposals = [(.5, 0, (1400, 100, 1420, 130)), (.60, 0, (1500, 100, 1590, 237))]
    kwargs = {'regulatory': (False,) * 6, 'reads': ((40, .999),) * 3 + ((55, .999),) * 3}
    actual = self.run_case(proposals=(p for p in proposals), **kwargs)
    control = self.run_case(proposals=(p for p in proposals), control=True, **kwargs)
    self.assertEqual(actual, control)
    self.assertEqual(len(actual[2]), 6)
    kwargs = {'proposals': [proposals[0], (.60, 2, proposals[1][2])],
              'regulatory': (False,) * 6,
              'reads': ((40, .999),) * 3 + (None,) + ((55, .999),) * 3}
    actual = self.run_case(**kwargs)
    control = self.run_case(control=True, **kwargs)
    self.assertEqual(actual, control)


if __name__ == '__main__':
  unittest.main()
