import copy
import math
import unittest
from openpilot.starpilot.curve_speed.learning import CURVATURES, GRID, KEYS, MAX_COUNT, LearnedCurve, bucket_index, monotone_fit


class TestLearning(unittest.TestCase):
  def test_sparse_conflicting_samples_use_weighted_monotone_fit(self):
    for actual, expected in zip(monotone_fit([3.0, 1.0, 4.0], [1, 9, 2]), (1.2, 1.2, 4.0), strict=True):
      self.assertAlmostEqual(actual, expected)
    result = LearnedCurve.load({KEYS[5]: {'average': 3.0, 'count': 1000}, KEYS[6]: {'average': 1.2, 'count': 1}})
    assert result.valid
    values = [result.curve.comfort(k) for k in CURVATURES]
    speeds = [math.sqrt(value / k) for value, k in zip(values, CURVATURES, strict=True)]
    assert all((a >= b for a, b in zip(speeds, speeds[1:], strict=False)))
    assert values[5] > LearnedCurve().comfort(CURVATURES[5])

  def test_gentle_curves_may_learn_more_comfort_than_tighter_street_curves(self):
    highway, street = bucket_index(0.004), bucket_index(0.0055)
    result = LearnedCurve.load({KEYS[highway]: {'average': 2.7, 'count': 600}, KEYS[street]: {'average': 1.57, 'count': 600}})
    assert result.valid
    self.assertGreater(result.curve.comfort(CURVATURES[highway]), 2.5)
    self.assertLess(result.curve.comfort(CURVATURES[street]), 1.8)

  def test_legacy_duplicate_buckets_merge_without_mutating_or_dirtying_saved_input(self):
    document = {'0.001': {'average': 2.0, 'count': 3}, '-0.0010001': {'average': 3.0, 'count': 1}}
    original = copy.deepcopy(document)
    loaded = LearnedCurve.load(document)
    assert loaded.valid and loaded.migrated and (not loaded.curve.dirty)
    assert document == original
    assert loaded.curve.document()['buckets'] == {KEYS[bucket_index(0.001)]: {'average': 2.25, 'count': 4}}
    assert LearnedCurve.load(loaded.curve.document()).curve.document() == loaded.curve.document()

  def test_bad_documents_cannot_be_partially_rewritten(self):
    for document in [
      [],
      {'version': True, 'buckets': {}},
      {'version': 2, 'buckets': {}},
      {'version': 1, 'buckets': {}, 'extra': 0},
      {'nan': {'average': 2.0, 'count': 1}},
      {'0.01': {'average': math.inf, 'count': 1}},
      {'0.01': {'average': 2.0, 'count': math.inf}},
      {'0.01': {'average': 2.0, 'count': 1.5}},
      {'0.01': {'average': 2.0, 'count': True}},
      {'0.01': {'average': 2.0, 'count': MAX_COUNT + 1}},
      {'0.01': {'average': -1.0, 'count': 1}},
      {'0.01': {'average': 2.0, 'count': 0}},
      {str(i): {'average': 2.0, 'count': 1} for i in range(257)},
      {'0.001': {'average': 2.0, 'count': MAX_COUNT}, '-0.001': {'average': 2.0, 'count': 1}},
    ]:
      with self.subTest(document=document):
        result = LearnedCurve.load(document)
        assert not result.valid
        assert not result.curve.dirty
        assert result.curve.document() == {'version': 1, 'buckets': {}}

  def test_learning_caps_historic_weight_but_preserves_progress_count(self):
    loaded = LearnedCurve.load({KEYS[10]: {'average': 2.0, 'count': 10000}})
    loaded.curve.observe(GRID[10], 3.0)
    sample = loaded.curve.document()['buckets'][KEYS[10]]
    self.assertAlmostEqual(sample['average'], (600 * 2.0 + 3.0) / 601)
    self.assertEqual(sample['count'], 10001)
    self.assertAlmostEqual(loaded.curve.progress, 100 / 24)

  def test_nudge_is_bounded_and_uses_separate_weight(self):
    curve = LearnedCurve()
    curve.nudge(0.01, 100.0)
    assert curve.document()['buckets'][KEYS[bucket_index(0.01)]] == {'average': 3.2, 'count': 20}
    curve.nudge(0.01, -1.0)
    sample = curve.document()['buckets'][KEYS[bucket_index(0.01)]]
    self.assertAlmostEqual(sample['average'], 2.2)
    self.assertEqual(sample['count'], 40)

  def test_write_acknowledgement_does_not_lose_newer_samples(self):
    curve = LearnedCurve()
    curve.observe(0.001, 1.7)
    writing_revision = curve.revision
    curve.observe(0.002, 1.9)
    curve.acknowledge_saved(writing_revision)
    assert curve.dirty
    curve.acknowledge_saved(curve.revision)
    assert not curve.dirty
    with self.assertRaises(ValueError):
      curve.acknowledge_saved(writing_revision)

  def test_invalid_live_observation_does_not_poison_prior(self):
    for value in [math.nan, math.inf, True, 10**1000]:
      with self.subTest(value=value):
        curve = LearnedCurve()
        before = curve.document()
        with self.assertRaises(ValueError):
          curve.observe(value, 2.0)
        assert curve.document() == before and (not curve.dirty)

  def test_calibration_saturates_independently_of_mean(self):
    curve = LearnedCurve.load({key: {'average': 2.0, 'count': 1000} for key in KEYS}).curve
    assert curve.progress == 100
    assert 1.2 <= curve.average_comfort <= 3.2
    assert LearnedCurve().average_comfort == 2.0
