"""Portable backend contracts. All observations/history here are declared unit fixtures."""

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, CanonicalSurfaceEffects
from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
from openpilot.starpilot.flm.offroad_daemon import StatusReport
from openpilot.starpilot.flm.offroad_quality import from_configuration
from openpilot.starpilot.flm.offroad_runtime import RouteEffects
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES, RouteScope, RouteStorage, startup_row
from openpilot.starpilot.flm.offroad_surface import CURVE_NAMES, REVISION, ROUTE_NAMES, RouteSurfaceEffects
from openpilot.starpilot.flm.route_recording import BASES, decode as decode_recording, encode as encode_recording
from openpilot.starpilot.flm.torque_stages import StageFrame
from openpilot.starpilot.saved_source import read_saved


def scope():
  return RouteScope('a' * 64, 'declared-unit-controller', 'b' * 64, 'c' * 64, '0', 'starpilot', 'torque')


def values(amount=0.0):
  result = dict.fromkeys(ROUTE_NAMES, 0.0)
  result['center_taper_max'] = amount
  return result


def trial():
  ids = [str(i) * 64 for i in range(1, 7)]
  return {
    'kind': 'next_drive_trial',
    'parameter': 'center_taper_max',
    'delta': 0.01,
    'cohort': 'declared-unit',
    'before': {'training': {'windows': ids[:3]}, 'heldout': {'windows': ids[3:]}},
    'gross_baseline': [
      {
        'identity': identity,
        'metrics': {'rmse': 0.1, 'error_reversal_hz': 0.5},
        'demand': {'goal_rms': 0.2, 'speed_low': 20.0, 'speed_high': 21.0, 'duration_ns': 2_000_000_000, 'goal_shape': [0.0] * 8},
      }
      for identity in ids
    ],
  }


class TestOffroadCache(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.cache = AssessmentCache(self.root / 'cache')

  def test_hash_content_and_tamper_refusal(self):
    digest = self.cache.put({'kind': 'declared-assessment', 'permission': False})
    self.assertEqual(AssessmentCache(self.cache.directory).get(digest)['permission'], False)
    (self.cache.directory / (digest + '.json')).write_text('{}')
    with self.assertRaises(ValueError):
      self.cache.get(digest)

  def test_claim_is_exclusive_across_fresh_readers(self):
    digest = self.cache.put(trial())
    self.assertTrue(self.cache.claim_trial(digest, 'a' * 32))
    self.assertFalse(AssessmentCache(self.cache.directory).claim_trial(digest, 'b' * 32))
    self.assertFalse(self.cache.claim_trial(digest, 'a' * 32))
    path = self.cache.directory / ('trial-' + digest + '.json')
    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
    self.assertEqual(json.loads(path.read_bytes()), {'analysis_sha256': digest, 'vehicle_session': 'a' * 32})

  def test_interrupted_claim_stays_consumed(self):
    for failure_at in (1, 2):
      with self.subTest(failure_at=failure_at):
        cache = AssessmentCache(self.root / str(failure_at))
        real_fsync = os.fsync
        calls = 0

        def interrupted(fd):
          nonlocal calls
          calls += 1
          if calls == failure_at:
            raise OSError('Declared failed fsync')
          return real_fsync(fd)

        with patch('os.fsync', side_effect=interrupted), self.assertRaises(OSError):
          cache.claim_trial('d' * 64, 'e' * 32)
        self.assertFalse(cache.claim_trial('d' * 64, 'f' * 32))

  def test_empty_claim_identity_capacity_and_unsafe_directory(self):
    (self.cache.directory / ('trial-' + 'd' * 64 + '.json')).touch(mode=0o600)
    self.assertFalse(self.cache.claim_trial('d' * 64, 'e' * 32))
    for session in ('', 'e' * 31, 'z' * 32):
      with self.subTest(session=session), self.assertRaises(ValueError):
        self.cache.claim_trial('d' * 64, session)
    with patch('openpilot.starpilot.flm.offroad_cache.MAX_FILES', 0), self.assertRaises(ValueError):
      self.cache.claim_trial('e' * 64, 'f' * 32)
    self.cache.directory.chmod(0o777)
    with self.assertRaises(ValueError):
      AssessmentCache(self.cache.directory)
    self.cache.directory.chmod(0o700)

  def test_actual_params_namespaces_are_isolated_and_writable(self):
    one, two = (Params(str(self.root / name)) for name in ('one', 'two'))
    first, second = directory_for_params(one), directory_for_params(two)
    self.assertNotEqual(first, second)
    self.assertEqual(first.parent, Path(one.get_param_path(KEY)).parent.resolve().parent)
    digest = AssessmentCache(first).put({'unit': True})
    self.assertEqual(AssessmentCache(directory_for_params(one)).get(digest), {'unit': True})
    self.assertFalse(Path(one.get_param_path(KEY)).exists())

  def test_restart_withdraws_trial_and_accepted_rows_do_not_claim(self):
    row = {'state': 'ready_for_next_drive', 'values': values(0.01), 'analysis_sha256': self.cache.put(trial())}
    with (
      patch('openpilot.starpilot.flm.offroad_worker._current_scope', return_value=scope()),
      patch('openpilot.starpilot.flm.offroad_store.startup_row', return_value=(row, 'loaded')),
    ):
      first = from_configuration(None, None, self.cache.directory, vehicle_session='a' * 32)
      self.assertIsNotNone(first)
      self.assertIsNone(first.stopped)
      restarted = from_configuration(None, None, self.cache.directory, vehicle_session='b' * 32)
      self.assertEqual(restarted.stopped, 'baseline_unavailable')
      owner = RouteEffects(scope(), tuple(values(0.01).items()), (), previous=tuple(values(0.005).items()))
      owner.observe_current_status(restarted.marker)
      self.assertEqual(dict(owner.values), values(0.005))
      self.assertFalse(owner.frame_allowed)
      self.assertEqual(owner.admitted_ns, 0)
      row['state'] = 'accepted_after_drive'
      self.assertIsNone(from_configuration(None, None, self.cache.directory, vehicle_session='c' * 32).stopped)


class TestOffroadSurface(unittest.TestCase):
  def test_zero_curves_preserve_canonical_and_interpolation_is_relative(self):
    for speed, expected in ((0.0, 0.0), (2.5, 0.005), (5.0, 0.01), (10.0, 0.02), (20.0, 0.035), (30.0, 0.04)):
      with self.subTest(speed=speed):
        frame = StageFrame(speed, 0.4, 0.2, 0.4, 0.3, True, False, False)
        zero = values()
        canonical = CanonicalSurfaceEffects({NAMES[0]: 0.0}, 'a' * 64, accumulated={n: zero[n] for n in NAMES})
        route = RouteSurfaceEffects({NAMES[0]: 0.0}, 'a' * 64, accumulated=zero)
        canonical.begin_frame(1)
        route.begin_frame(1)
        self.assertEqual(
          canonical.selected_stage(frame, original_anchor=canonical.original_effect_anchor),
          route.selected_stage(frame, original_anchor=route.original_effect_anchor),
        )
        curve = dict(zero, **dict(zip(CURVE_NAMES, (0.0, 0.01, 0.02, 0.03, 0.04), strict=True)))
        route = RouteSurfaceEffects({NAMES[0]: 0.0}, 'a' * 64, accumulated=curve)
        route.begin_frame(1)
        ff, threshold = route.selected_stage(frame, original_anchor=route.original_effect_anchor)
        self.assertEqual(ff, frame.baseline_ff)
        self.assertAlmostEqual(threshold, frame.baseline_threshold * (1.0 + expected))

  def test_invalid_bounds_inactive_and_wire_cardinality_refuse(self):
    for value in (0.25001, float('nan'), True):
      invalid = values()
      invalid[CURVE_NAMES[0]] = value
      with self.subTest(value=value), self.assertRaises(ValueError):
        RouteSurfaceEffects({NAMES[0]: 0.0}, 'a' * 64, accumulated=invalid)
    frame = StageFrame(10.0, 0.4, 0.2, 0.4, 0.3, False, False, False)
    route = RouteSurfaceEffects({NAMES[0]: 0.0}, 'a' * 64, accumulated=values(0.01))
    route.begin_frame(1)
    self.assertEqual(route.selected_stage(frame, original_anchor=route.original_effect_anchor), (0.4, 0.3))
    from types import SimpleNamespace

    schema = BASES[0] + '@' + REVISION
    stage = tuple(('unit.' + str(i), float(i)) for i in range(40))
    evidence = SimpleNamespace(
      route_effect_revision=REVISION,
      stage_schema_revision=schema,
      route_applied_coordinates=tuple(values().items()),
      route_configuration_scope='a' * 64,
      stage_values=stage,
      live_applied_coordinates=(),
      effect_trace=None,
    )
    encoded = encode_recording(evidence)
    self.assertEqual(len(encoded), 83)
    self.assertEqual(decode_recording(schema, encoded)[1], tuple(values().items()))
    with self.assertRaises(ValueError):
      decode_recording(schema, encoded[:-1])


class TestOffroadStore(unittest.TestCase):
  def test_atomic_parked_rollback_and_identity_bounds(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      owner = RouteStorage(params)
      row = {
        'scope': scope().document(),
        'values': values(0.005),
        'previous': values(),
        'route_sha256s': ['d' * 64],
        'source_start_ns': 1,
        'source_end_ns': 2,
        'completed_wall_ns': 3,
        'analysis_sha256': 'e' * 64,
        'sequence': 1,
        'state': 'ready_for_next_drive',
      }
      self.assertFalse(owner.save(row, expected=None, authorized=lambda: False))
      self.assertEqual(read_saved(params, KEY, MAX_BYTES), (None, True))
      self.assertTrue(owner.save(row, expected=None, authorized=lambda: True))
      raw, readable = read_saved(params, KEY, MAX_BYTES)
      self.assertTrue(readable)
      self.assertFalse(owner.save(row, expected=None, authorized=lambda: True))
      self.assertIsNone(startup_row(params, replace(scope(), reset_generation='1'))[0])
      self.assertFalse(owner.rollback(scope=scope(), expected=raw, authorized=lambda: False))
      self.assertEqual(read_saved(params, KEY, MAX_BYTES), (raw, True))
      self.assertTrue(owner.rollback(scope=scope(), expected=raw, authorized=lambda: True))
      self.assertEqual(startup_row(params, scope())[0]['values'], values())


class TestOffroadReport(unittest.TestCase):
  def test_typed_current_application_and_completed_analysis_remain_distinct(self):
    with tempfile.TemporaryDirectory() as directory:
      now = 10_000_000_000
      control = ControlEvidence(
        identity='unit',
        generation=1,
        control_ns=now,
        state_ns=now,
        domain='torque',
        stage_revision='',
        response_revision='',
        requested=0.0,
        features=(),
        baseline_prediction=0.0,
        enabled=True,
        manual=False,
        active=True,
        valid=True,
        override=False,
        fault=False,
        saturated=False,
        limited=False,
        model_wobble=False,
        controller_chatter=False,
        route_applied_coordinates=tuple(values(0.01).items()),
        route_effect_revision=REVISION,
        route_configuration_scope='a' * 64,
      )
      path = Path(directory) / 'report.json'
      report = StatusReport(path=path, clock_ns=lambda: now)
      current = {
        'status': 'collecting',
        'reason': 'declared-unit',
        'enabled': True,
        'actual_control': control,
        'actual_control_ns': now,
        'actual_coordinates': control.route_applied_coordinates,
        'completed_route': 'declared-unit-route',
        'analysis_counts': {'recorded_tracking': 1000, 'qualified_whole_windows': 6},
      }
      report.set(current)
      report.publish()
      observed = json.loads(path.read_bytes())['current']
      self.assertTrue(observed['application']['applied'])
      self.assertEqual(observed['progress']['samples'], 1000)
      self.assertFalse(observed['details']['Physical road improvement proven'])
      for replacement in (None, replace(control, manual=True), replace(control, limited=True), replace(control, control_ns=now - 100_000_001)):
        with self.subTest(control=replacement):
          report.next_ns = 0
          report.set(dict(current, actual_control=replacement))
          report.publish()
          self.assertFalse(json.loads(path.read_bytes())['current']['application']['applied'])


class TestOffroadTrackingPolicy(unittest.TestCase):
  @staticmethod
  def windows(prefix, gain, amplitude=1.0):
    import math
    from openpilot.starpilot.flm.offroad_evidence import RoutePoint, RouteWindow, window_identity

    # Declared numerical observations only: no recorded route, current clock,
    # native grant, permission, automatic save or physical-road claim.
    windows = []
    for number in range(3):
      points = []
      for index in range(301):
        stamp = 1_000_000_000 + number * 4_000_000_000 + index * 10_000_000
        desired = amplitude * (0.4 + 0.2 * math.sin(2 * math.pi * 0.7 * index / 100 + number * 0.3))
        points.append(
          RoutePoint(prefix, number, stamp, 'lateral_acceleration', 'a' * 64, desired, gain * desired, 20.0, 0.1, 0.1, stamp, stamp, True, False, False, True)
        )
      frozen_points = tuple(points)
      windows.append(RouteWindow(window_identity(frozen_points), frozen_points))
    return tuple(windows)

  def test_repeatable_tracking_error_produces_bounded_recommendation(self):
    from openpilot.starpilot.flm.offroad_tracking import estimate, suggest

    proposals = []
    for role in ('training', 'heldout'):
      observed = estimate(self.windows('declared-' + role, 0.85), cohort='2:left:steady')
      assert observed is not None
      self.assertAlmostEqual(observed.gain, 0.85, places=8)
      proposal = suggest(observed, dict.fromkeys(NAMES, 0.0), side='left', phase='steady')
      assert proposal is not None
      self.assertEqual(proposal['parameter'], 'ff_gain_left')
      self.assertGreater(proposal['delta'], 0)
      self.assertLessEqual(proposal['delta'], 0.01)
      self.assertFalse(proposal['road_improvement_confirmed'])
      self.assertTrue(all(v == 0 for n, v in proposal['values'].items() if n != 'ff_gain_left'))
      proposals.append(proposal['values'])
    self.assertEqual(proposals[0], proposals[1])

  def test_matched_response_does_not_generate_a_change(self):
    from openpilot.starpilot.flm.offroad_tracking import estimate, suggest

    observed = estimate(self.windows('declared-matched', 1.0), cohort='2:left:steady')
    assert observed is not None
    self.assertIsNone(suggest(observed, dict.fromkeys(NAMES, 0.0), side='left', phase='steady'))

  def test_smaller_request_does_not_confirm_unchanged_tracking(self):
    from openpilot.starpilot.flm.offroad_tracking import estimate, compare_observed

    before = estimate(self.windows('declared-before', 0.85), cohort='2:left:steady')
    after = estimate(self.windows('declared-smaller-request', 0.85, 0.8), cohort='2:left:steady')
    assert before is not None and after is not None
    self.assertFalse(compare_observed(before, after)['improved'])

  def test_supported_tracking_improvement_is_recognized(self):
    from openpilot.starpilot.flm.offroad_tracking import estimate, compare_observed

    before = estimate(self.windows('declared-before', 0.85), cohort='2:left:steady')
    after = estimate(self.windows('declared-improved', 0.95), cohort='2:left:steady')
    assert before is not None and after is not None
    result = compare_observed(before, after)
    self.assertTrue(result['improved'])
    self.assertEqual(result['improvement_kind'], 'forced_tracking')


if __name__ == '__main__':
  unittest.main()
