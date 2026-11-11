"""Declared original samples test observation counts, never physical improvement."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.starpilot.flm.offroad_behavior import behavior_candidates
from openpilot.starpilot.flm.offroad_stable_policy import FLMSample
from openpilot.starpilot.flm.tests.test_offroad_backend import values


class TestObservedBehavior(unittest.TestCase):
  @staticmethod
  def samples(number, *, bad=True, start=0, route=None):
    result = []
    for index in range(201):
      t = index / 100
      desired = -1.5 + .3 * t
      actual = desired - (.25 if bad else 0)
      result.append(FLMSample(route or f'declared-drive-{number}', 0, start + t + 1, 25., True, False, False,
                              actual, desired, .3, actual - desired, 0., 0., 0., 0., 0., .1, -20., 0., .1, .1, 0.))
    return result

  def recommend(self, rows, **kwargs):
    return behavior_candidates(tuple(rows), rows, dict.fromkeys(rows, 'same-source-context'), values(), '2:right:unwind', **kwargs)

  def test_four_of_five_sections_report_right_unwind_without_claiming_five_turns(self):
    rows = {str(i): self.samples(i, bad=i != 4) for i in range(5)}
    candidates = self.recommend(rows)
    self.assertEqual({r['parameter'] for r in candidates}, {
      'stable:unwind_threshold_increase_right', 'stable:unwind_taper_right',
      'stable:curvy_unwind_floor_relief_right', 'stable:curvy_unwind_extra_reduction_right',
    })
    for row in candidates:
      self.assertIn('4 of 5 retained higher-speed right turn-exit sections across 5 drive(s)', row['evidence'])
      self.assertIn('steering unwound too slowly', row['evidence'])
      self.assertIn('not a count of distinct turns', row['evidence'])
      self.assertEqual((row['measured_sections'], row['matched_sections'], row['evidence_drives']), (5, 4, 5))
      self.assertGreater(row['delta'], 0)
      self.assertLessEqual(row['delta'], .01)

  def test_adjacent_windows_are_one_section_not_five_votes(self):
    rows = {str(i): self.samples(i, start=i * 2.01, route='same-route') for i in range(5)}
    self.assertEqual(self.recommend(rows), [])

  def test_missing_section_cannot_be_removed_from_the_denominator(self):
    rows = {str(i): self.samples(i) for i in range(5)}
    rows['4'] = None
    self.assertEqual(self.recommend(rows), [])

  def test_opposing_behavior_cannot_offer_one_direction(self):
    rows = {str(i): self.samples(i) for i in range(5)}
    rows['4'] = [replace(s, actual_la=s.desired_la + .25, error=.25) for s in rows['4']]
    self.assertEqual(self.recommend(rows), [])

  def test_sparse_or_cancelled_evidence_cannot_offer_a_change(self):
    rows = {str(i): self.samples(i) for i in range(2)}
    self.assertEqual(self.recommend(rows), [])
    with self.assertRaisesRegex(ValueError, 'cancelled'):
      self.recommend(rows, permitted=lambda: False)

  def test_overlapping_sections_are_refused(self):
    rows = {str(i): self.samples(i, route='same-route') for i in range(3)}
    with self.assertRaisesRegex(ValueError, 'repeat or overlap'):
      self.recommend(rows)

  def test_recommendation_survives_too_few_automatic_windows(self):
    from openpilot.starpilot.flm.offroad_processor import _assess_inputs
    from openpilot.starpilot.flm.tests.test_offroad_coordinate_policy import TestCrossDriveEvidence

    collected, inventory = TestCrossDriveEvidence.inputs(1)
    assessment = _assess_inputs(inventory, collected, values(), permitted=lambda: True)
    group = assessment.groups[0]
    self.assertEqual(len(group['recommendation_original_windows']), 2)
    self.assertEqual(group['selected_original_windows'], [])
    self.assertNotIn('training', group['cohorts'][0])

  def test_worker_publishes_behavior_without_native_trial_or_settings_write(self):
    from openpilot.common.params import Params
    from openpilot.starpilot.flm import offroad_worker as worker_module
    from openpilot.starpilot.flm.offroad_processor import _assess_inputs
    from openpilot.starpilot.flm.offroad_store import KEY
    from openpilot.starpilot.flm.tests.test_offroad_backend import scope
    from openpilot.starpilot.flm.tests.test_offroad_coordinate_policy import TestCrossDriveEvidence

    collected, inventory = TestCrossDriveEvidence.inputs(1)
    assessment = _assess_inputs(inventory, collected, values(), permitted=lambda: True)
    proposed = self.recommend({str(i): self.samples(i, bad=i != 4) for i in range(5)})
    class Selector:
      def __init__(self, *args, **kwargs):
        pass

      def recommendations(self, group, *, stable):
        return proposed

      def propose(self, *args):
        raise AssertionError('Observation-only evidence must not create automatic roles')

    def refuse(*args, **kwargs):
      raise AssertionError('Observation-only recommendations must not enter automatic qualification')

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      with (
        patch.object(worker_module, 'latest_route', return_value=inventory),
        patch.object(worker_module, 'pending_routes', return_value=[inventory]),
        patch.object(worker_module, 'qualify', new=refuse),
        patch('openpilot.starpilot.flm.offroad_stable_bridge.StableRouteSelector', new=Selector),
      ):
        worker = worker_module.OffroadWorker(params, directory, Path(directory) / 'cache', permitted=lambda: True,
          assessor=lambda *args, **kwargs: assessment, checker=refuse, scope_for=lambda *args: scope())
        result = worker.run_once()
      self.assertFalse(result['configuration_changed'])
      self.assertIsNone(params.get(KEY))
      self.assertEqual(len(result['recommendations']), 4)
      self.assertTrue(all('4 of 5' in row['evidence'] for row in result['recommendations']))


if __name__ == '__main__':
  unittest.main()
