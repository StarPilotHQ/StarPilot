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
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES, NAMESPACE, RouteScope, RouteStorage, startup_row
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

        def interrupted(fd, failure_at=failure_at, real_fsync=real_fsync):
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
  def test_real_controller_route_replay_preserves_limiter_and_requires_exact_hooks(self):
    from types import SimpleNamespace
    from opendbc.car import structs
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.flm.controller_replay import SelectedControllerFactory, capture_snapshot, restore_snapshot, frame_input, _run
    from openpilot.starpilot.flm.offroad_surface import replay_source
    from openpilot.starpilot.flm.torque_adapter import Objective

    cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6).as_reader()
    controller, vm = SelectedControllerFactory(cp, 'starpilot').create()
    cs = structs.CarState.new_message(vEgo=25.0, steeringAngleDeg=0.2, gearShifter='drive')
    snapshot = capture_snapshot(controller, vm)
    objective = Objective('feedforward', 'high', 'steady')
    neutral = (0.0,) * len(objective.parameters)
    original_update = controller.update
    for limited in (False, True):
      with self.subTest(limited=limited):
        inputs = frame_input(
          cs,
          vm,
          SimpleNamespace(angleOffsetDeg=0.0, roll=0.0),
          active=True,
          safety_limited=limited,
          desired_curvature=0.0001,
          curvature_limited=False,
          lat_delay=0.1,
        ) + (('control_frame', 1.0),)
        restore_snapshot(controller, vm, snapshot)
        baseline, _ = _run(controller, vm, inputs, objective, neutral)
        restore_snapshot(controller, vm, snapshot)
        source = replay_source(values(0.005), 'b' * 64)
        candidate, _ = _run(controller, vm, inputs, objective, neutral, effect_trial=source)
        self.assertEqual((source.stage_calls, source.friction_calls), (0, 0) if limited else (1, 1))
        if limited:
          self.assertEqual(candidate, baseline, 'The actual limiter inhibits all route effects')
        else:

          def omit_effect(*args):
            actual = controller.automatic_flm_effects
            controller.automatic_flm_effects = None
            try:
              return original_update(*args)
            finally:
              controller.automatic_flm_effects = actual

          restore_snapshot(controller, vm, snapshot)
          with patch.object(controller, 'update', side_effect=omit_effect), self.assertRaisesRegex(ValueError, 'selected effect hook'):
            _run(controller, vm, inputs, objective, neutral, effect_trial=replay_source(values(), 'b' * 64))

        def duplicate_effect(*args):
          result = original_update(*args)
          effect = controller.automatic_flm_effects.source
          effect.stage_calls += 1
          effect.friction_calls += 1
          return result

        restore_snapshot(controller, vm, snapshot)
        with patch.object(controller, 'update', side_effect=duplicate_effect), self.assertRaisesRegex(ValueError, 'selected effect hook'):
          _run(controller, vm, inputs, objective, neutral, effect_trial=replay_source(values(), 'b' * 64))

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
  def test_completed_drive_reports_every_candidate_without_claiming_application(self):
    from openpilot.starpilot.flm.training_report import read_report
    from openpilot.starpilot.flm.offroad_worker import qualification_summary

    checks = {
      'windows': [{'qualified': True}] * 22
      + [
        {'qualified': False, 'numerical': {'qualified': False, 'reason': 'original_scored_frame_not_eligible'}},
        {
          'qualified': False,
          'numerical': {'qualified': True},
          'registered_encoder': {'qualified': False, 'detail': 'candidate changed an actual limiter/support'},
        },
      ]
    }
    summary = qualification_summary(checks)
    self.assertEqual((summary['passed'], summary['required']), (22, 24))

    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'report.json'
      report = StatusReport(path=path, clock_ns=lambda: 10_000_000_000)
      names = ['highway_center_taper_max', 'turn_in_boost_left', 'unwind_taper_left', 'turn_in_boost_right', 'unwind_taper_right']
      suggestions = [{'parameter': name, 'delta': 0.005, 'whole_windows': 9, 'qualification': 'controller_native_checks_not_passed'} for name in names]
      recorded = {
        'version': 1,
        'domain': 'lateral_acceleration',
        'samples': 2000,
        'windows': 10,
        'error_rms': 0.12,
        'route': 'declared-completed-drive',
        'segment': 0,
        'source_start_ns': 1_000_000_000,
        'source_end_ns': 2_900_000_000,
        'points': [[1_000_000_000 + i * 100_000_000, 0.2, 0.08] for i in range(20)],
        'sampling': 'input_ordered_original_endpoints_no_interpolation',
      }
      report.set(
        {
          'status': 'collecting',
          'reason': 'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application',
          'completed_route': 'declared-completed-drive',
          'analysis_counts': {'recorded_tracking': 241573},
          'suggestions': suggestions,
          'suggestion_count': len(suggestions),
          'configuration_changed': False,
          'recorded_tracking': recorded,
          'saved_adjustments': 0,
          'qualification_summary': summary,
        }
      )
      report.publish()
      current = read_report(path, now_ns=10_000_000_000)['current']
      assert current is not None
      self.assertEqual([r[0] for r in current['details']['Recorded route suggestions']], names)
      self.assertEqual(current['details']['Recorded suggestion count'], 5)
      self.assertEqual(current['progress']['samples'], 241573)
      self.assertFalse(current['details']['Automatic configuration changed'])
      self.assertFalse(current['application']['applied'])
      self.assertFalse(current['details']['Physical road improvement proven'])
      self.assertEqual(current['details']['Recorded steering response'], recorded)
      self.assertEqual(current['details']['Saved route adjustment count'], 0)
      self.assertEqual(current['details']['Controller and encoder check windows'], [22, 24])
      self.assertEqual(
        current['details']['Automatic tune refusal reasons'], [['candidate changed an actual limiter/support', 1], ['original_scored_frame_not_eligible', 1]]
      )

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


class TestOffroadRouteCoverage(unittest.TestCase):
  def test_native_windows_veto_ineligible_envelopes_between_tracking_samples(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_processor import continuous_control_window

    window = TestOffroadTrackingPolicy.windows('declared-envelope-coverage', 1.0)[0]
    start, end = window.points[0].mono_ns, window.points[-1].mono_ns
    metadata = {'scope': 'declared-source'}
    timeline = []
    for stamp in range(start - 30_000_000, end + 30_000_001, 5_000_000):
      control = SimpleNamespace(
        control_ns=stamp,
        state_ns=stamp,
        source_receipt_ns=stamp,
        enabled=True,
        active=True,
        valid=True,
        manual=False,
        override=False,
        fault=False,
        saturated=False,
        limited=False,
        native_required=True,
        native_allowed=True,
        native_observed_ns=stamp - 1_000_000,
        native_expires_ns=stamp + 100_000_000,
        metadata=metadata,
      )
      timeline.append((stamp, control))

    def reader(control, domain):
      return control.metadata

    self.assertTrue(continuous_control_window(window, timeline, reader, metadata))
    index = next(i for i, (stamp, _) in enumerate(timeline) if stamp == start + 5_000_000)
    control = timeline[index][1]
    for changes in ({'limited': True}, {'native_allowed': False}, {'metadata': {'scope': 'changed'}}):
      with self.subTest(changes=changes):
        changed = list(timeline)
        changed[index] = (timeline[index][0], SimpleNamespace(**(vars(control) | changes)))
        self.assertFalse(continuous_control_window(window, changed, reader, metadata))
    absent: list[tuple[int, SimpleNamespace | None]] = list(timeline)
    absent[index] = (timeline[index][0], None)
    self.assertFalse(continuous_control_window(window, absent, reader, metadata))

  def test_actual_aggregation_keeps_classifier_and_separate_qualification_windows(self):
    import math
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_processor as processor
    from openpilot.starpilot.flm.offroad_evidence import RoutePoint, RouteWindow, window_identity
    from openpilot.starpilot.flm.offroad_route import RouteReceipt

    # Declared post-collection fixture. This exercises the original aggregation
    # path; no real source, native permission, acceptance or saved tune is mocked.
    def windows(duration):
      result = []
      for segment in range(6):
        points = tuple(
          RoutePoint(
            'declared-aggregation',
            segment,
            1_000_000_000 + segment * 10_000_000_000 + i * 10_000_000,
            'lateral_acceleration',
            'a' * 64,
            0.02 * math.sin(i / 20),
            0.018 * math.sin(i / 20),
            25.0,
            0.1,
            0.1,
            1,
            1,
            True,
            False,
            False,
            True,
          )
          for i in range(duration * 100)
        )
        result.append(RouteWindow(window_identity(points), points))
      return result

    continuous, observed = windows(2), windows(6)
    metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION}
    route = {'routeId': 'declared-aggregation'}
    receipt = RouteReceipt('declared-aggregation', 'b' * 64, (), 'c' * 64, 1, 2, 3, ())

    def collected(root, selected, *, permitted, consume):
      self.assertEqual(selected, route)
      self.assertTrue(permitted())
      self.assertIsNotNone(consume.__closure__)
      cells = dict(zip(consume.__code__.co_freevars, consume.__closure__, strict=True))
      cells['state'].cell_contents['cp'] = SimpleNamespace()
      cells['windows'].cell_contents['a' * 64] = continuous
      cells['stable_windows'].cell_contents['a' * 64] = continuous
      cells['baseline_tracking'].cell_contents['a' * 64] = observed
      cells['context_metadata'].cell_contents['a' * 64] = metadata
      cells['bookends'].cell_contents.update({w.identity: (w.points[0].mono_ns, w.points[-1].mono_ns) for w in observed})
      return receipt

    with patch.object(processor, 'latest_route', return_value=route), patch.object(processor, 'walk_completed_route', side_effect=collected):
      assessment = processor.analyze_latest(None, None, dict.fromkeys(NAMES, 0.0), permitted=lambda: True)
    self.assertEqual(len(assessment.stable_groups), 1)
    group = assessment.stable_groups[0]
    self.assertEqual(group['tracking_source_grade'], 'bookended_zero_route_tracking')
    self.assertEqual({w['identity'] for w in group['selected_original_windows']}, {w.identity for w in observed})
    self.assertEqual({w['identity'] for w in group['qualification_original_windows']}, {w.identity for w in continuous})
    roles = group['cohorts'][0]['qualification_roles']
    self.assertEqual(len(roles['training']['windows']), 3)
    self.assertEqual(len(roles['heldout']['windows']), 3)
    self.assertFalse(set(roles['training']['windows']) & set(roles['heldout']['windows']))
    primary = assessment.groups[0]
    self.assertEqual(primary['tracking_source_grade'], 'bookended_zero_route_tracking')
    self.assertEqual(primary['cohorts'][0]['whole_windows'], 18)
    self.assertEqual({w['identity'] for w in primary['qualification_original_windows']}, {w.identity for w in continuous})
    self.assertEqual(assessment.counts['qualified_whole_windows'], 6)
    self.assertEqual(assessment.counts['proposal_tracking_whole_windows'], 18)
    recorded = assessment.recorded_tracking
    self.assertIsNotNone(recorded)
    assert recorded is not None
    self.assertEqual((recorded['samples'], recorded['windows']), (3600, 18))
    self.assertEqual(len(recorded['points']), 64)
    self.assertEqual(recorded['points'][0], [observed[0].points[0].mono_ns, observed[0].points[0].desired, observed[0].points[0].actual])
    self.assertEqual(recorded['source_end_ns'], observed[0].points[199].mono_ns)
    self.assertEqual(recorded['sampling'], 'input_ordered_original_endpoints_no_interpolation')
    self.assertFalse(assessment.report()['automatically_saved'])

  def test_primary_statistics_keep_original_children_and_never_supply_qualification(self):
    from openpilot.starpilot.flm.offroad_baseline_windows import tracking_proposal_windows
    from openpilot.starpilot.flm.offroad_evidence import RouteWindow, whole_windows, window_identity

    original = TestOffroadTrackingPolicy.windows('declared-source', 0.9)[0]
    short = replace(original, points=original.points[:200], identity='')
    short = replace(short, identity=window_identity(short.points))
    first = short.points[0].mono_ns
    points = tuple(replace(p, mono_ns=p.mono_ns + block * 2_000_000_000) for block in range(3) for p in short.points)
    parent = RouteWindow(window_identity(points), points)
    self.assertEqual(parent.points[0].mono_ns, first)
    expected = whole_windows(parent.points)
    continuous = {'nonzero': (short,)}
    selected, grades = tracking_proposal_windows(continuous, {'known-zero': (parent,)})
    self.assertEqual(selected['known-zero'], list(expected))
    self.assertEqual(tuple(p for w in selected['known-zero'] for p in w.points), points)
    self.assertEqual(selected['nonzero'], (short,))
    self.assertEqual(continuous, {'nonzero': (short,)})
    self.assertEqual(grades, {'known-zero': 'bookended_zero_route_tracking'})

  def test_continuous_center_does_not_hide_other_baseline_maneuvers(self):
    from openpilot.starpilot.flm.offroad_baseline_windows import stable_proposal_windows

    center = tuple(w for i in range(3) for w in TestOffroadTrackingPolicy.windows('declared-continuous-center-' + str(i), 1.0))
    observed = TestOffroadTrackingPolicy.windows('declared-observed-maneuvers', 0.9)
    continuous = {'zero-context': center, 'nonzero-context': center}
    selected, grades = stable_proposal_windows(continuous, {'zero-context': observed})
    self.assertEqual(selected['zero-context'], observed)
    self.assertEqual(selected['nonzero-context'], center)
    self.assertEqual(grades, {'zero-context': 'bookended_zero_route_tracking'})
    self.assertEqual(continuous['zero-context'], center)
    # Continuous controller/native evidence remains separate and unchanged.
    self.assertNotEqual(selected['zero-context'], continuous['zero-context'])

  def test_missing_observed_baseline_retains_continuous_outcome_windows(self):
    from openpilot.starpilot.flm.offroad_baseline_windows import stable_proposal_windows

    windows = TestOffroadTrackingPolicy.windows('declared-nonzero-outcome', 1.0)
    for observed in ({}, {'same-context': ()}):
      with self.subTest(observed=observed):
        selected, grades = stable_proposal_windows({'same-context': windows}, observed)
        self.assertEqual(selected, {'same-context': windows})
        self.assertEqual(grades, {})

  def test_observed_baseline_requires_zero_map_and_unchanged_original_context(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_baseline_windows import baseline_windows
    from openpilot.starpilot.flm.offroad_evidence import RoutePoint

    points = tuple(
      RoutePoint(
        'declared-baseline-only',
        0,
        1_000_000_000 + i * 10_000_000,
        'lateral_acceleration',
        'a' * 64,
        0.1,
        0.1,
        25.0,
        0.1,
        0.1,
        1_000_000_000 + i * 10_000_000,
        1_000_000_000 + i * 10_000_000,
        True,
        False,
        False,
        True,
      )
      for i in range(600)
    )
    metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION, 'baseline_revision': 'b' * 64}
    timeline = tuple((p.mono_ns, SimpleNamespace(control_ns=p.mono_ns, metadata=metadata)) for p in (points[0], points[300], points[-1]))
    result = baseline_windows(points, timeline, lambda control, domain: control.metadata)
    self.assertEqual(len(result), 1)
    self.assertEqual(tuple((p.mono_ns, p.desired, p.actual) for p in result[0][1].points), tuple((p.mono_ns, p.desired, p.actual) for p in points))
    for changed in ({**metadata, 'route_values': tuple(values(0.005).items())}, {**metadata, 'baseline_revision': 'c' * 64}):
      conflicting = list(timeline)
      conflicting[1] = (points[300].mono_ns, SimpleNamespace(control_ns=points[300].mono_ns, metadata=changed))
      self.assertEqual(baseline_windows(points, conflicting, lambda control, domain: control.metadata), ())
    self.assertEqual(baseline_windows(points, timeline[:-1] + ((points[-1].mono_ns, None),), lambda control, domain: control.metadata), ())
    self.assertEqual(baseline_windows(tuple(replace(p, driver_override=True) for p in points), timeline, lambda control, domain: control.metadata), ())


class TestOffroadTrackingPolicy(unittest.TestCase):
  def test_saved_unit_configuration_reports_successful_original_checks(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_worker import OffroadWorker
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt

    declared = trial()
    roles = declared['before']
    baseline = declared['gross_baseline']
    proposed = {'values': values(0.005), 'parameter': 'center_taper_max', 'delta': 0.005}
    cohort = dict(roles, cohort='declared-positive-report', whole_windows=6, proposal=proposed, qualification_roles=roles)
    metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION, 'controller_mode': 'starpilot', 'route_scope': scope().key()}
    group = {
      'original_metadata': metadata,
      'original_context_sha256': 'f' * 64,
      'cohorts': [cohort],
      'qualification_original_windows': baseline,
      'selected_original_windows': baseline,
      'tracking_source_grade': 'declared-unit-only',
    }
    cp = SimpleNamespace(carFingerprint='declared-torque-unit')
    receipt = RouteReceipt('declared-positive-report', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
    inventory = {'routeId': receipt.route_id}
    assessment = CompletedAssessment(receipt, (group,), {}, cp, (), inventory)

    def assessed(params, root, previous, *, permitted):
      previous(cp)
      return assessment

    def declared_checks(actual, original, ids, trial_values, root, *, permitted):
      # Declared protocol responses only, not a native qualification claim.
      return [
        {
          'window': wid,
          'qualified': True,
          'numerical': {'qualified': True, 'changed_frames': 1},
          'registered_encoder': {'qualified': True, 'candidate_changed_emissions': 1},
        }
        for wid in ids
      ]

    from openpilot.starpilot.flm.offroad_prior_inputs import PriorOriginals
    from openpilot.starpilot.flm.offroad_route import SelectedSourceReceipt
    from openpilot.starpilot.flm.offroad_tracking import TrackingEstimate
    from openpilot.starpilot.flm.learned_lifecycle import decode

    prior = PriorOriginals(
      cp,
      SelectedSourceReceipt('declared-independent', 'e' * 64, (), 'a' * 64, 1, 2, 2, (), (0,)),
      {},
      tuple({'identity': str(i) * 64, 'original_metadata': metadata} for i in range(7, 10))
      + tuple({'identity': c * 64, 'original_metadata': metadata} for c in ('a', 'b', 'c')),
      (),
    )
    observed = TrackingEstimate(
      'declared', 'lateral_acceleration', '2:both:center', 1.0, 0, 0.0, (0.9, 1.1), (0, 0), (-0.1, 0.1), 1.0, 0.1, (), 'f' * 64, 1.0, 0.1, 0.1
    )
    cohort['cohort'] = '2:both:center'
    for independent in (False, True):
      with self.subTest(independent=independent), tempfile.TemporaryDirectory() as directory:
        params = Params(directory)
        with (
          patch('openpilot.starpilot.flm.offroad_worker.latest_route', return_value=inventory),
          patch('openpilot.starpilot.flm.offroad_checks._original_results', new=declared_checks),
          patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='a' * 64),
          patch('openpilot.starpilot.flm.offroad_tracking.estimate', return_value=observed),
          patch('openpilot.starpilot.flm.offroad_tracking.suggest', return_value=proposed),
        ):
          worker = OffroadWorker(
            params,
            directory,
            Path(directory) / 'cache',
            permitted=lambda: True,
            assessor=assessed,
            scope_for=lambda cp, params: scope(),
            prior_provider=(lambda *args, **kwargs: prior) if independent else None,
          )
          result = worker.run_once()
        self.assertTrue(result['configuration_changed'])
        self.assertEqual(result['qualification_summary'], {'passed': 12 if independent else 6, 'required': 12 if independent else 6, 'refusals': []})
        self.assertEqual(result['saved_adjustments'], 1)
        self.assertFalse(result['actual_application_proven'])
        raw, readable = read_saved(params, KEY, MAX_BYTES)
        self.assertTrue(readable)
        self.assertEqual(decode(raw)[NAMESPACE][scope().key()]['route_sha256s'], ['d' * 64] + (['e' * 64] if independent else []))

  def test_prior_source_reader_requires_real_end_and_matching_cp(self):
    import bz2
    from contextlib import contextmanager
    from openpilot.cereal import log
    from opendbc.car import structs
    from openpilot.starpilot.flm import offroad_prior_inputs as inputs
    from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest

    cp = structs.CarParams.new_message(carFingerprint='declared-source-unit').as_reader()
    cp_sha = car_params_digest(cp)
    names = ('declared--0', 'declared--1', 'declared--2')
    route = {'routeId': '00000001--1234567890', 'segments': [{'files': {}} for _ in names]}
    payloads = {}
    for i, name in enumerate(names):
      stamp = (i + 1) * 1_000_000_000
      events = []
      if i == 0:
        event = log.Event.new_message(logMonoTime=stamp, carParams=cp)
        events.append(event.to_bytes())
      event = log.Event.new_message(logMonoTime=stamp, valid=True)
      event.init('carState')
      events.append(event.to_bytes())
      if i == 2:
        event = log.Event.new_message(logMonoTime=stamp, valid=True)
        event.init('clocks').wallTimeNanos = 1_800_000_000_000_000_000
        events.append(event.to_bytes())
        event = log.Event.new_message(logMonoTime=stamp, valid=True)
        event.init('sentinel').type = 'endOfRoute'
        events.append(event.to_bytes())
      payloads[name] = bz2.compress(b''.join(events))

    @contextmanager
    def source(root, name, expected, permitted, **kwargs):
      yield payloads[name], 'bz2', lambda: True, ('declared-original', name)

    with (
      patch.object(inputs, '_selected', return_value=(route['routeId'], names)),
      patch.object(inputs, '_route_names', return_value=set(names)),
      patch.object(inputs, '_closed_source', new=source),
    ):
      receipt = inputs._selected_sources('.', route, permitted=lambda: True, consume=lambda *args: None, cp_sha256=cp_sha)
      self.assertFalse(receipt.whole_route_decoded)
      self.assertEqual(receipt.cp_sha256, cp_sha)
      self.assertEqual(receipt.decoded_segments, (0, 1, 2))
      with self.assertRaises(inputs.PriorIdentityMismatch):
        inputs._selected_sources('.', route, permitted=lambda: True, consume=lambda *args: None, cp_sha256='f' * 64)
      payloads[names[-1]] = bz2.compress(b'')
      with self.assertRaises(ValueError):
        inputs._selected_sources('.', route, permitted=lambda: True, consume=lambda *args: None, cp_sha256=cp_sha)

  def test_independent_drive_fits_all_current_windows_and_never_retries_validation(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_checks as checks
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_prior_inputs import PriorOriginals, interior_segments
    from openpilot.starpilot.flm.offroad_route import RouteReceipt, SelectedSourceReceipt
    from openpilot.starpilot.flm.offroad_tracking import TrackingEstimate

    self.assertEqual(interior_segments(100), (7, 15, 22, 30, 38, 45, 53, 60, 68, 76, 83, 91))
    cp = SimpleNamespace(carFingerprint='declared-independent-unit')
    ids = tuple(str(i) * 64 for i in range(1, 7))
    valid_ids = tuple(str(i) * 64 for i in range(7, 10)) + ('a' * 64, 'b' * 64, 'c' * 64)
    roles = {'training': {'windows': ids[:3]}, 'heldout': {'windows': ids[3:]}}
    metadata = {'controller_mode': 'starpilot', 'route_scope': scope().key(), 'baseline_values': (1.0,), 'route_values': tuple(values().items())}
    original = tuple({'identity': wid} for wid in ids)
    cohort = {'cohort': '2:both:center', 'qualification_roles': roles}
    group = {'original_metadata': metadata, 'qualification_original_windows': original}
    current = CompletedAssessment(RouteReceipt('declared-current', 'd' * 64, (), 'a' * 64, 10, 20, 30, ()), (), {}, cp, (), {})
    prior = PriorOriginals(
      cp,
      SelectedSourceReceipt('declared-prior', 'e' * 64, (), 'a' * 64, 1, 2, 3, (), (0, 1)),
      {},
      tuple({'identity': wid, 'original_metadata': dict(metadata)} for wid in valid_ids),
      (),
    )
    proposed = {'parameter': 'center_taper_max', 'delta': 0.005, 'values': values(0.005)}
    replication = dict(proposed)
    observed = TrackingEstimate(
      'declared', 'lateral_acceleration', '2:both:center', 1.0, 0, 0.0, (0.9, 1.1), (0, 0), (-0.1, 0.1), 1.0, 0.1, (), 'f' * 64, 1.0, 0.1, 0.1
    )
    calls = []

    def recorded(actual, source, selected, trial_values, root, *, permitted):
      self.assertTrue(permitted())
      calls.append((actual is prior, tuple(selected), trial_values['center_taper_max']))
      okay = actual is not prior and trial_values['center_taper_max'] <= 0.004
      return [
        {
          'window': wid,
          'qualified': okay,
          'numerical': {'qualified': True, 'changed_frames': 1},
          'registered_encoder': {'qualified': okay, 'candidate_changed_emissions': 1},
        }
        for wid in selected
      ]

    with (
      patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='a' * 64),
      patch.object(checks, '_original_results', new=recorded),
      patch('openpilot.starpilot.flm.offroad_tracking.estimate', return_value=observed),
      patch('openpilot.starpilot.flm.offroad_tracking.suggest', return_value=replication),
    ):
      fitted, proof, result = checks.fit_independent_route(current, group, cohort, prior, proposed, values(), '.', permitted=lambda: True)
    self.assertIsNotNone(fitted)
    assert fitted is not None and result is not None
    self.assertEqual(calls, [(False, ids, 0.005), (False, ids, 0.004), (True, valid_ids, 0.004)])
    self.assertEqual(proof['validation_evaluations'], 1)
    self.assertFalse(result['qualified'])
    self.assertEqual(proof['selected_fraction'], 0.8)

    calls.clear()
    with (
      patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='a' * 64),
      patch.object(checks, '_original_results', new=recorded),
      patch('openpilot.starpilot.flm.offroad_tracking.estimate', return_value=observed),
      patch('openpilot.starpilot.flm.offroad_tracking.suggest', return_value=None),
    ):
      fitted, proof, result = checks.fit_independent_route(current, group, cohort, prior, proposed, values(), '.', permitted=lambda: True)
    self.assertEqual(calls, [(False, ids, 0.005), (False, ids, 0.004)])
    assert result is not None
    self.assertEqual(result['reason'], 'independent_drive_tracking_does_not_support_change')
    self.assertFalse(result['qualified'])
    from openpilot.starpilot.flm.offroad_worker import qualification_summary

    self.assertEqual(qualification_summary(result), {'passed': 6, 'required': 12, 'refusals': [('independent_native_validation_not_run', 6)]})
    with patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='0' * 64), self.assertRaises(ValueError):
      checks.fit_independent_route(current, group, cohort, prior, proposed, values(), '.', permitted=lambda: True)

  def test_training_feasibility_is_bounded_and_heldout_refusal_never_retries(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_checks import fit_training_step
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt
    from openpilot.starpilot.flm.offroad_worker import OffroadWorker

    train, heldout = tuple(str(i) * 64 for i in range(1, 4)), tuple(str(i) * 64 for i in range(4, 7))
    roles = {'training': {'windows': train}, 'heldout': {'windows': heldout}}
    proposed = values(0.005)
    proposal = {'values': proposed, 'parameter': 'center_taper_max', 'delta': 0.005}
    cohort = {'cohort': 'declared-training', 'whole_windows': 6, 'proposal': proposal, 'qualification_roles': roles}
    metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION, 'controller_mode': 'starpilot', 'route_scope': scope().key()}
    group = {'original_metadata': metadata, 'original_context_sha256': 'f' * 64, 'cohorts': [cohort], 'qualification_original_windows': []}
    cp = SimpleNamespace(carFingerprint='declared-torque-unit')
    receipt = RouteReceipt('declared-training-drive', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
    inventory = {'routeId': 'declared-training-drive'}
    assessment = CompletedAssessment(receipt, (group,), {}, cp, (), inventory)
    calls = []

    def original_checks(actual, original, ids, trial_values, root, *, permitted):
      self.assertTrue(permitted())
      self.assertIs(actual, assessment)
      amount = trial_values['center_taper_max']
      calls.append((tuple(ids), amount))
      self.assertTrue(set(ids) in (set(train), set(train + heldout)))
      return [{'window': wid, 'qualified': amount <= 0.004 and wid not in heldout} for wid in ids]

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)

      def assessed(params, root, previous, *, permitted):
        previous(cp)
        return assessment

      with (
        patch('openpilot.starpilot.flm.offroad_worker.latest_route', return_value=inventory),
        patch('openpilot.starpilot.flm.offroad_checks._original_results', new=original_checks),
      ):
        worker = OffroadWorker(params, directory, Path(directory) / 'cache', permitted=lambda: True, assessor=assessed, scope_for=lambda cp, params: scope())
        result = worker.run_once()
      self.assertEqual(calls, [(train, 0.005), (train, 0.004), (train + heldout, 0.004)])
      self.assertFalse(result['configuration_changed'])
      self.assertEqual(result['reason'], 'original_controller_or_registered_encoder_check_refused')
      self.assertEqual(read_saved(params, KEY, MAX_BYTES), (None, True))

    calls.clear()

    def infeasible(actual, original, ids, trial_values, root, *, permitted):
      calls.append(tuple(ids))
      return [{'window': wid, 'qualified': False} for wid in ids]

    with patch('openpilot.starpilot.flm.offroad_checks._original_results', new=infeasible):
      fitted, proof = fit_training_step(assessment, group, cohort, proposal, values(), '.', permitted=lambda: True)
    self.assertIsNone(fitted)
    self.assertEqual(calls, [train] * 5)
    self.assertIsNone(proof['selected_fraction'])

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
