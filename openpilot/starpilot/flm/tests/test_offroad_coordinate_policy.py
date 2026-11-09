"""Coordinate proposal contracts using declared unit fixtures."""

import json
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.offroad_runtime import RouteEffects
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES, RouteStorage, startup_row
from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES, RouteSurfaceEffects
from openpilot.starpilot.flm.tests.test_offroad_backend import scope, trial, values
from openpilot.starpilot.flm.torque_stages import StageFrame
from openpilot.starpilot.saved_source import read_saved


class TestAllThirtyCoordinateProposals(unittest.TestCase):
  @staticmethod
  def observations(bucket, speed, side='left', severity=1.0):
    return [(str(index), {'bucket': bucket, 'direction': side, 'severity': severity, 'speed_mps': speed}) for index in range(3)]

  def test_all_thirty_have_symptom_bound_candidates_that_store_load_and_change_their_actual_hook(self):
    from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates
    from openpilot.starpilot.flm.offroad_surface import route_bounds

    # Declared symptoms, not measured road improvement. Nonzero prior trims make
    # their window edges meaningful; zero-amplitude windows must not be moved.
    previous = dict.fromkeys(ROUTE_NAMES, 0.01)
    cases: list[tuple[str, float]] = [('center_chatter', speed) for speed in (2, 7, 12, 20, 30)]
    buckets = ('understeer', 'oversteer', 'late_turn_in', 'early_turn_in', 'low_speed_unwillingness', 'unwind_too_slow', 'unwind_too_fast')
    cases += [(bucket, speed) for bucket in buckets for speed in (2, 7.2, 11.5, 20.5, 21.5, 26)]
    candidates = {}
    for bucket, speed in cases:
      for side in ('left', 'right'):
        for candidate in coordinate_candidates(self.observations(bucket, speed, side), previous):
          candidates.setdefault(candidate['parameter'].removeprefix('stable:'), candidate)
    self.assertEqual(set(candidates), set(NAMES), 'A declared 30-knob learner must have a reachable proposal for every knob')
    for name, candidate in candidates.items():
      assert candidate is not None
      with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
        changed = {key for key in ROUTE_NAMES if candidate['values'][key] != previous[key]}
        self.assertEqual(changed, {name})
        self.assertLessEqual(abs(candidate['delta']), 0.01)
        low, high = route_bounds(name)
        self.assertTrue(low <= candidate['values'][name] <= high)
        params = Params(directory)
        row = {'scope': scope().document(), 'values': candidate['values'], 'previous': previous, 'route_sha256s': ['d' * 64],
               'source_start_ns': 1, 'source_end_ns': 2, 'completed_wall_ns': 3, 'analysis_sha256': 'e' * 64,
               'sequence': 1, 'state': 'ready_for_next_drive'}
        initial = dict(row, values=previous, previous=values())
        self.assertTrue(RouteStorage(params).save(initial, expected=None, authorized=lambda: True))
        baseline_raw, readable = read_saved(params, KEY, MAX_BYTES)
        self.assertTrue(readable)
        row.update(sequence=2, route_sha256s=['f' * 64], source_start_ns=4, source_end_ns=5, completed_wall_ns=6)
        self.assertTrue(RouteStorage(params).save(row, expected=baseline_raw, authorized=lambda: True))
        restored, _ = startup_row(params, scope())
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored['values'], candidate['values'])
        fresh = RouteEffects(scope(), tuple((key, restored['values'][key]) for key in ROUTE_NAMES), (), previous=tuple(previous.items()))
        self.assertEqual(fresh.status, 'configured_pending_actual_stage')
        self.assertEqual(fresh.admitted_ns, 0, 'Stored coordinates cannot supply live authority')
        before = RouteSurfaceEffects({NAMES[0]: previous[NAMES[0]]}, scope().baseline_revision, accumulated=previous)
        after = fresh.source
        differences = []
        stamp = 0
        for speed in (2, 5, 10, 15, 21, 26, 30):
          for requested in (-1.5, -0.05, 0.05, 1.5):
            for jerk in (-1.0, 1.0):
              stamp += 1
              output = []
              for effect in (before, after):
                effect.begin_frame(stamp)
                frame = StageFrame(speed, requested, jerk, 0.3, 0.3, True, False, False)
                output.append((
                  *effect.selected_stage(frame, original_anchor=effect.original_effect_anchor),
                  effect.selected_deadzone(speed, 0.0, 0.1, 0.1, requested, original_anchor=effect.original_effect_anchor),
                  effect.selected_output(speed, requested, 30.0, 5.0, -0.2, 0.1, original_anchor=effect.original_effect_anchor),
                ))
              differences.append(any(abs(a - b) > 1e-12 for a, b in zip(*output, strict=True)))
        self.assertTrue(any(differences), 'The proposed knob must affect its actual existing selected hook')

  def test_missing_conflicting_or_unsupported_evidence_cannot_generate_changes(self):
    from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates

    previous = values()
    source = self.observations('understeer', 15)
    self.assertEqual(coordinate_candidates(source[:2], previous), [])
    self.assertEqual(coordinate_candidates(source + self.observations('oversteer', 15), previous), [])
    for bucket in ('model_limited', 'saturation_limited', 'unknown'):
      self.assertEqual(coordinate_candidates(self.observations(bucket, 15), previous), [])
    for bad in (None, True, float('nan'), float('inf'), -1):
      invalid = self.observations('understeer', 15)
      for _, summary in invalid:
        summary['speed_mps'] = bad
      self.assertEqual(coordinate_candidates(invalid, previous), [])
    self.assertEqual(previous, values())

  def test_speed_knots_side_phase_and_relative_units_are_preserved(self):
    from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates

    previous = values()
    for speed, expected in ((2, {'crawl', 'low'}), (5, {'low'}), (10, {'mid'}), (15, {'fast'}), (30, {'highway'})):
      proposed = coordinate_candidates(self.observations('center_chatter', speed), previous)
      deadbands = {p['parameter'].split('_')[2] for p in proposed if 'center_deadband_' in p['parameter']}
      self.assertEqual(deadbands, expected)
      self.assertTrue(all(p['coordinate_units'] == 'physical_normalized' for p in proposed if 'center_deadband_' in p['parameter']))
    proposed = coordinate_candidates(self.observations('early_turn_in', 11.5, 'right'), previous)
    self.assertFalse(any('left' in p['parameter'] or 'speed_min' in p['parameter'] or 'speed_max' in p['parameter'] for p in proposed))
    self.assertTrue(any(p['parameter'] == 'stable:curvy_turn_in_trim_right' and p['delta'] > 0 for p in proposed))
    previous['curvy_turn_in_trim_right'] = 0.02
    expanded = coordinate_candidates(self.observations('early_turn_in', 11.5, 'right'), previous)
    edge = next(p for p in expanded if p['parameter'] == 'stable:curvy_turn_in_trim_speed_min')
    self.assertLess(edge['delta'], 0, 'Earlier trim onset reduces an observed early turn-in')
    self.assertEqual(edge['coordinate_units'], 'relative_speed_window')
    self.assertFalse(any('speed_min' in p['parameter'] for p in coordinate_candidates(self.observations('early_turn_in', 30, 'right'), previous)))

  def test_coordinate_alternatives_freeze_before_heldout_and_require_independent_support(self):
    from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates
    from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector

    roles = trial()['before']
    selector = object.__new__(StableRouteSelector)
    selector.previous = values()
    selector.rows = dict.fromkeys(roles['training']['windows'] + roles['heldout']['windows'], [])
    training = coordinate_candidates(self.observations('low_speed_unwillingness', 2), values())
    frozen = json.loads(json.dumps(training))
    for heldout in (training, [], coordinate_candidates(self.observations('early_turn_in', 2), values())):
      events = []

      def profile(windows, cohort, events=events, heldout=heldout):
        role = 'training' if windows == tuple(roles['training']['windows']) else 'heldout'
        events.append(role)
        return None, {'coordinate_candidates': training if role == 'training' else heldout}

      with patch.object(selector, 'profile', new=profile):
        legacy, proof = selector.propose({}, {'cohort': 'declared', **roles})
      self.assertIsNone(legacy)
      self.assertEqual(events, ['training', 'heldout'])
      self.assertEqual(proof['coordinate_candidates'], training if heldout == training else [])
      self.assertEqual(training, frozen, 'Heldout must not resize or replace the frozen proposals')


if __name__ == '__main__':
  unittest.main()
