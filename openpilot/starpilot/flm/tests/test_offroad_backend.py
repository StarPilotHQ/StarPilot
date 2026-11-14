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
    with patch('openpilot.starpilot.flm.offroad_cache.MAX_TOTAL', 0), self.assertRaises(ValueError):
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
      claim = self.cache.directory / ('trial-' + row['analysis_sha256'] + '.json')
      self.assertFalse(claim.exists(), 'A parked constructor does not consume an untested trial')
      from types import SimpleNamespace

      stamp = 1_000_000_000
      control = SimpleNamespace(
        valid=True,
        enabled=True,
        active=False,
        manual=False,
        override=False,
        fault=False,
        limited=False,
        saturated=False,
        control_ns=stamp,
        route_effect_revision=REVISION,
        route_configuration_scope=scope().key(),
        route_applied_coordinates=tuple(values(0.01).items()),
      )
      event = SimpleNamespace(logMonoTime=stamp)
      first.observe(event, {}, control, now_ns=stamp)
      self.assertFalse(claim.exists())
      stamp += 10_000_000
      event.logMonoTime = control.control_ns = stamp
      control.active = True
      with patch('openpilot.starpilot.flm.offroad_quality.read_point', return_value=None):
        first.observe(event, {}, control, now_ns=stamp)
      self.assertTrue(claim.exists(), 'The first valid current active route frame consumes the trial once')
      restarted = from_configuration(None, None, self.cache.directory, vehicle_session='b' * 32)
      self.assertEqual(restarted.stopped, 'baseline_unavailable')
      owner = RouteEffects(scope(), tuple(values(0.01).items()), (), previous=tuple(values(0.005).items()))
      owner.observe_current_status(restarted.marker)
      self.assertEqual(dict(owner.values), values(0.005))
      self.assertFalse(owner.frame_allowed)
      self.assertEqual(owner.admitted_ns, 0)
      row['state'] = 'accepted_after_drive'
      self.assertIsNone(from_configuration(None, None, self.cache.directory, vehicle_session='c' * 32).stopped)

  def test_unmeasured_sessions_wait_but_withdrawn_trials_still_roll_back(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_worker import assess_next_drive, POLICY
    from openpilot.starpilot.flm.offroad_surface import stop_marker

    declared = dict(trial(), policy=POLICY, values=values(0.01), original_metadata={})
    empty = SimpleNamespace(groups=(), stable_groups=(), quality_stop_markers=())
    self.assertEqual(assess_next_drive(declared, empty, scope(), trial_started=False)['outcome'], 'pending')
    self.assertEqual(assess_next_drive(declared, empty, scope(), trial_started=True)['outcome'], 'pending')
    marker = stop_marker(scope().key(), declared['values'], 'gross_tracking_or_oscillation_worse')
    stopped = SimpleNamespace(groups=(), stable_groups=(), quality_stop_markers=({'reason': marker},))
    self.assertEqual(assess_next_drive(declared, stopped, scope(), trial_started=False)['outcome'], 'rolled_back')

  def test_actual_start_is_blocked_before_controller_reuse_and_rollback_restores_prior_map(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_runtime import configure

    params = Params(str(self.root / 'startup'))
    cache = AssessmentCache(directory_for_params(params))
    row = {'state': 'ready_for_next_drive', 'values': values(0.01), 'previous': values(), 'analysis_sha256': cache.put(trial())}
    with (
      patch('openpilot.starpilot.flm.offroad_runtime.configuration_data', return_value=(scope(), (), ())),
      patch('openpilot.starpilot.flm.offroad_runtime.startup_row', return_value=(row, 'loaded')),
    ):
      owner, status = configure(None, None, SimpleNamespace(), params, manual_status='not_configured')
      self.assertIsNotNone(owner)
      self.assertEqual(status, 'configured_pending_actual_stage')
      self.assertTrue(cache.claim_trial(row['analysis_sha256'], 'a' * 32))
      owner, status = configure(None, None, SimpleNamespace(), params, manual_status='not_configured')
      self.assertIsNone(owner, 'A genuine consumed trial cannot apply even one frame after restart')
      self.assertEqual(status, 'provisional_trial_already_started')
      row['state'] = 'rolled_back'
      row['values'] = values(0.005)
      owner, status = configure(None, None, SimpleNamespace(), params, manual_status='not_configured')
      self.assertIsNotNone(owner, 'Rollback must restore the previous known map, including a nonzero accepted tune')
      assert owner is not None
      self.assertEqual(dict(owner.values), row['values'])
      self.assertEqual(status, 'configured_pending_actual_stage')

  def test_last_steering_drive_selection_ignores_only_recorded_inactive_sessions(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_processor as module

    latest = {'routeId': '00000012--aaaaaaaaaa', 'segments': [{'files': {'rlog': True}}]}
    earlier = {'routeId': '00000011--bbbbbbbbbb', 'segments': [{'files': {'rlog': True}}]}
    counts = {'recorded_lateral_request_rows': 100, 'active_lateral_request_rows': 0, 'invalid_lateral_request_rows': 0}
    parked = SimpleNamespace(receipt=SimpleNamespace(cp_sha256='a' * 64), counts=dict(counts), quality_stops=[])
    driving = SimpleNamespace(receipt=SimpleNamespace(cp_sha256='a' * 64), counts=dict(counts, active_lateral_request_rows=70), quality_stops=[])
    calls = []

    def collect(root, route, *, permitted, walker=None):
      self.assertTrue(permitted())
      self.assertTrue(callable(walker))
      calls.append(route['routeId'])
      return parked if route is latest else driving

    with (
      patch.object(module, 'latest_route', return_value=latest),
      patch.object(module, 'collect_route_inputs', new=collect),
      patch('openpilot.starpilot.flm.offroad_route.route_inventory', return_value={'scanIncomplete': False, 'routes': [latest, earlier]}),
    ):
      route, collected = module.collect_latest_steering_inputs(None, '.', permitted=lambda: True)
      self.assertIs(route, earlier)
      self.assertIs(collected, driving)
      self.assertEqual(calls, [latest['routeId'], earlier['routeId']])
      calls.clear()
      parked.counts['active_lateral_request_rows'] = 1
      route, _ = module.collect_latest_steering_inputs(None, '.', permitted=lambda: True)
      self.assertIs(route, latest, 'A failed or weak active drive must never cause a search for favorable older results')
      self.assertEqual(calls, [latest['routeId']])
      calls.clear()
      parked.counts['active_lateral_request_rows'] = 0
      driving.receipt.cp_sha256 = 'b' * 64
      route, _ = module.collect_latest_steering_inputs(None, '.', permitted=lambda: True)
      self.assertIs(route, latest, 'An older car cannot be substituted')

  def test_selected_previous_drive_keeps_both_source_inventories_guarded(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_worker as module
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt

    parked = {'routeId': '00000012--aaaaaaaaaa'}
    selected = {'routeId': '00000011--bbbbbbbbbb'}
    cp = SimpleNamespace(carFingerprint='declared-unit-only')
    receipt = RouteReceipt(selected['routeId'], 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
    assessment = CompletedAssessment(receipt, (), {}, cp, (), selected)

    def assessed(params, root, previous, *, permitted):
      previous(cp)
      return assessment

    for intact in (True, False):
      with self.subTest(selected_source_intact=intact), tempfile.TemporaryDirectory() as directory:
        with (
          patch.object(module, 'latest_route', return_value=parked),
          patch.object(module, 'route_inventory', return_value={'scanIncomplete': False, 'routes': [parked, selected] if intact else [parked]}),
        ):
          params = Params(directory)
          worker = module.OffroadWorker(
            params, directory, Path(directory) / 'cache', permitted=lambda: True, assessor=assessed, scope_for=lambda cp, params: scope()
          )
          if intact:
            result = worker.run_once()
            self.assertEqual(result['completed_route'], selected['routeId'])
            self.assertFalse(result['configuration_changed'])
          else:
            with self.assertRaisesRegex(ValueError, 'Source or settings changed'):
              worker.run_once()
          self.assertEqual(read_saved(params, KEY, MAX_BYTES), (None, True))


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

  def test_inhibited_original_frames_are_verified_unchanged_and_cannot_earn_a_trial(self):
    from types import SimpleNamespace
    from opendbc.car import structs
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.flm import controller_replay
    from openpilot.starpilot.flm.controller_replay import SelectedControllerFactory, capture_snapshot, frame_input, final_command
    from openpilot.starpilot.flm.offroad_controller_checks import check_candidate
    from openpilot.starpilot.flm.torque_adapter import Objective
    from openpilot.starpilot.flm.torque_observation import TorqueObservation

    # Declared input epoch through the actual selected controller; no live source or native grant.
    cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6).as_reader()
    actual, vm = SelectedControllerFactory(cp, 'starpilot').create()
    observation = TorqueObservation()
    actual.automatic_flm_observation = observation
    cs = structs.CarState.new_message(vEgo=25.0, steeringAngleDeg=0.0, gearShifter='drive')
    params = SimpleNamespace(angleOffsetDeg=0.0, roll=0.0)
    objective = Objective('feedforward', 'high', 'steady')
    captured = []
    for tick in range(140):
      stamp = 1_000_000_000 + tick * 10_000_000
      inhibited = tick % 20 < 4
      before = capture_snapshot(actual, vm)
      inputs = frame_input(
        cs, vm, params, active=True, safety_limited=inhibited, desired_curvature=0.00002, curvature_limited=inhibited and tick % 2 == 0, lat_delay=0.1
      )
      inputs += (('control_frame', float(tick + 1)),)
      observation.begin_frame()
      output, log = controller_replay._run(actual, vm, inputs, objective, (0.0, 0.0))
      trace = observation.finish_frame(actual, log, selected_command=output)
      self.assertIsNotNone(trace)
      assert trace is not None
      schema, stage, coordinates = trace
      captured.append(
        ControlEvidence(
          identity='a' * 64,
          generation=1,
          control_ns=stamp,
          state_ns=stamp,
          domain='normalized_torque',
          stage_revision='declared-selected-controller',
          response_revision='declared-no-plant',
          requested=final_command(output),
          features=(0.0, 0.0),
          baseline_prediction=final_command(output),
          enabled=True,
          manual=False,
          active=True,
          valid=True,
          override=False,
          fault=False,
          saturated=bool(log.saturated),
          limited=inhibited,
          model_wobble=False,
          controller_chatter=False,
          stage_schema_revision=schema,
          stage_values=stage + (('actual_delta_ff', 0.0), ('actual_delta_threshold', 0.0)),
          applied_coordinates=coordinates,
          source_epoch='b' * 64,
          source_receipt_ns=stamp,
          controller_mode='starpilot',
          baseline_revision='c' * 64,
          controller_revision='declared-component-controller',
          replay_inputs=inputs,
          replay_snapshot=before.values,
          snapshot_sequence=tick + 1,
        )
      )
    original = tuple(captured)
    result = check_candidate(cp, original, values(), require_change=False)
    self.assertTrue(result['qualified'], result)
    self.assertGreater(result['verified_inhibited_frames'], 0)
    self.assertGreaterEqual(result['scored_frames'], 50)
    self.assertEqual(len(result['commands']), len(original))
    self.assertTrue(all(before == after for _, before, after in result['commands']))
    self.assertFalse(check_candidate(cp, original, values(), require_change=True)['qualified'])
    hinted = tuple(replace(row, limited=True) for row in original)
    no_delivery = check_candidate(cp, hinted, values(), require_change=False)
    self.assertFalse(no_delivery['qualified'])
    self.assertEqual(no_delivery['reason'], 'original_hint_requires_exact_unclipped_native_pair')
    with patch('openpilot.starpilot.flm.offroad_controller_checks.original_delivery_context', return_value='clipped_context'):
      # Declared context classification only; real encoder proofs are tested separately.
      context_only = check_candidate(cp, hinted, values(), require_change=False)
    self.assertFalse(context_only['qualified'])
    self.assertEqual(context_only['scored_frames'], 0)
    self.assertEqual(context_only['changed_frames'], 0)
    self.assertGreater(context_only['verified_clipped_context_frames'], 0)
    with patch('openpilot.starpilot.flm.offroad_controller_checks.original_nonemitted_control', return_value=True):
      # Declared classification only; the separate test uses real native carriers.
      not_emitted = check_candidate(cp, hinted, values(), require_change=False, original_outputs=(SimpleNamespace(control_ns=0),))
    self.assertFalse(not_emitted['qualified'])
    self.assertEqual(not_emitted['scored_frames'], 0)
    self.assertEqual(not_emitted['changed_frames'], 0)
    self.assertGreater(not_emitted['verified_nonemitted_context_frames'], 0)
    original_run = controller_replay._run

    def corrupt_only_candidate(*args, **kwargs):
      output, log = original_run(*args, **kwargs)
      if kwargs.get('effect_trial') is not None and dict(args[2])['safety_limited']:
        output += 0.0001
      return output, log

    with patch.object(controller_replay, '_run', new=corrupt_only_candidate):
      refused = check_candidate(cp, original, values(), require_change=False)
    self.assertFalse(refused['qualified'])
    self.assertEqual(refused['reason'], 'original_limited_frame_was_changed')

  def test_combined_original_inhibition_is_context_only(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.torque_eligibility import replay_context

    frame = SimpleNamespace(
      healthy=True,
      manual=False,
      actual_lac_command=0.05,
      actual_stage_residual=(0.0, 0.0),
      controller_domain='torque',
      torque_values=tuple({'effective_factor': 3.0, 'offset': 0.0, 'friction': 0.09, 'pid_pos_limit': 1.0, 'pid_neg_limit': -1.0}.items()),
      inputs=(('curvature_limited', 1.0), ('safety_limited', 1.0)),
    )
    self.assertTrue(replay_context(frame))
    for change in (
      {'healthy': False},
      {'manual': True},
      {'actual_lac_command': None},
      {'actual_stage_residual': None},
      {'torque_values': ()},
      {'controller_domain': 'torque_pid', 'torque_values': ()},
      {'inputs': (('curvature_limited', 1.0), ('safety_limited', 0.0))},
      {'inputs': (('curvature_limited', 2.0), ('safety_limited', 1.0))},
      {'inputs': (('curvature_limited', float('nan')), ('safety_limited', 1.0))},
      {'inputs': (('curvature_limited', 1.0),)},
    ):
      with self.subTest(change=change):
        self.assertFalse(replay_context(SimpleNamespace(**(vars(frame) | change))))

  def test_historical_hint_requires_every_exact_original_unclipped_delivery(self):
    from opendbc.car.toyota.values import CAR
    from openpilot.starpilot.flm.offroad_controller_checks import original_unclipped_hint
    from openpilot.starpilot.flm.tests.test_torque_emission_join import interface, settle, cc, emitted

    with tempfile.TemporaryDirectory() as directory:
      ci = interface(CAR.TOYOTA_COROLLA_TSS2)
      settle(ci, cc(0.01))
      base, output = emitted(ci, cc(0.01), Path(directory))
      control = ControlEvidence(
        **vars(base),
        generation=1,
        state_ns=output.state_ns,
        domain='normalized_torque',
        stage_revision='declared-codec-source',
        response_revision='declared-no-plant',
        features=(0.0,),
        baseline_prediction=base.requested,
        enabled=True,
        manual=False,
        active=True,
        valid=True,
        override=False,
        fault=False,
        saturated=False,
        limited=True,
        model_wobble=False,
        controller_chatter=False,
        stage_schema_revision='torque-selected-stage-snapshot-v1@' + REVISION,
        source_receipt_ns=base.control_ns - 1,
        replay_inputs=(('active', 1.0), ('safety_limited', 0.0), ('curvature_limited', 0.0)),
      )
      self.assertTrue(original_unclipped_hint(control, (output,)))
      self.assertFalse(original_unclipped_hint(control, ()))
      for changed in (
        replace(output, limited=True),
        replace(output, native_allowed=False),
        replace(output, fault=True),
        replace(output, override=True),
        replace(output, control_ns=output.control_ns + 1),
        replace(output, apply_ns=control.control_ns + 30_000_001),
      ):
        with self.subTest(changed=changed):
          self.assertFalse(original_unclipped_hint(control, (output, changed)))
      old = replace(control, source_receipt_ns=control.control_ns - 500_000_001)
      self.assertFalse(original_unclipped_hint(old, (output,)))
      required = replace(
        control,
        native_required=True,
        native_allowed=True,
        native_identity='a' * 64,
        native_observed_ns=control.control_ns - 1,
        native_expires_ns=output.apply_ns,
      )
      self.assertTrue(original_unclipped_hint(required, (output,)))
      self.assertFalse(original_unclipped_hint(replace(required, native_expires_ns=output.apply_ns - 1), (output,)))
      self.assertTrue(control.limited)
      self.assertFalse(output.limited)

  def test_real_clipped_encoder_output_is_context_and_never_an_unclipped_sample(self):
    from openpilot.starpilot.flm.tests.test_trace_context import clipped_context
    from openpilot.starpilot.flm.tests.test_torque_emission_join import cc, emitted
    from openpilot.starpilot.flm.offroad_controller_checks import original_delivery_context, original_unclipped_hint

    with tempfile.TemporaryDirectory() as directory:
      ci, base, output = clipped_context(Path(directory))
      control = ControlEvidence(
        **vars(base),
        generation=1,
        state_ns=output.state_ns,
        domain='normalized_torque',
        stage_revision='declared-codec-source',
        response_revision='declared-no-plant',
        features=(0.0,),
        baseline_prediction=base.requested,
        enabled=True,
        manual=False,
        active=True,
        valid=True,
        override=False,
        fault=False,
        saturated=False,
        limited=True,
        model_wobble=False,
        controller_chatter=False,
        stage_schema_revision='torque-selected-stage-snapshot-v1@' + REVISION,
        source_receipt_ns=base.control_ns - 1,
        replay_inputs=(('active', 1.0), ('safety_limited', 0.0), ('curvature_limited', 0.0)),
      )
      self.assertEqual(original_delivery_context(control, (output,)), 'clipped_context')
      self.assertFalse(original_unclipped_hint(control, (output,)))
      for changed in (
        replace(output, native_allowed=False),
        replace(output, fault=True),
        replace(output, override=True),
        replace(output, control_ns=output.control_ns + 1),
        replace(output, apply_ns=control.control_ns + 30_000_001),
      ):
        with self.subTest(changed=changed):
          self.assertIsNone(original_delivery_context(control, (changed,)))
      self.assertIsNone(original_delivery_context(replace(control, source_receipt_ns=control.control_ns - 500_000_001), (output,)))
      required = replace(
        control,
        native_required=True,
        native_allowed=True,
        native_identity='a' * 64,
        native_observed_ns=control.control_ns - 1,
        native_expires_ns=output.apply_ns,
      )
      self.assertEqual(original_delivery_context(required, (output,)), 'clipped_context')
      self.assertIsNone(original_delivery_context(replace(required, native_expires_ns=output.apply_ns - 1), (output,)))
      self.assertTrue(output.limited)
      # A repeated real request may leave the limiter on the following Card tick.
      _, next_output = emitted(ci, cc(base.requested), Path(directory), control_ns=control.control_ns)
      no_hint = replace(control, limited=False)
      self.assertFalse(next_output.limited)
      self.assertEqual(original_delivery_context(no_hint, (output, next_output)), 'clipped_context')
      self.assertFalse(original_unclipped_hint(no_hint, (output, next_output)))
      self.assertIsNone(original_delivery_context(no_hint, (output, replace(next_output, native_allowed=False))))

  def test_consecutive_real_native_carriers_prove_a_control_tick_was_not_emitted(self):
    from opendbc.car.toyota.values import CAR
    from openpilot.starpilot.flm.offroad_controller_checks import original_nonemitted_control
    from openpilot.starpilot.flm.tests.test_torque_emission_join import interface, settle, cc, emitted

    def command(base, output):
      return ControlEvidence(
        **vars(base),
        generation=1,
        state_ns=output.state_ns,
        domain='normalized_torque',
        stage_revision='declared-codec-source',
        response_revision='declared-no-plant',
        features=(0.0,),
        baseline_prediction=base.requested,
        enabled=True,
        manual=False,
        active=True,
        valid=True,
        override=False,
        fault=False,
        saturated=False,
        limited=True,
        model_wobble=False,
        controller_chatter=False,
        controller_mode='native',
        controller_revision='declared-encoder-controller',
        stage_schema_revision='torque-selected-stage-snapshot-v1@' + REVISION,
        source_receipt_ns=base.control_ns - 1,
        replay_inputs=(('active', 1.0), ('safety_limited', 0.0), ('curvature_limited', 0.0)),
      )

    with tempfile.TemporaryDirectory() as directory:
      ci = interface(CAR.TOYOTA_COROLLA_TSS2)
      settle(ci, cc(0.01))
      first, before = emitted(ci, cc(0.01), Path(directory))
      second, after = emitted(ci, cc(0.01), Path(directory))
      a, b = command(first, before), command(second, after)
      stamp = (a.control_ns + b.control_ns) // 2
      skipped = replace(a, control_ns=stamp, source_receipt_ns=stamp - 1)
      originals = {a.control_ns: a, b.control_ns: b, stamp: skipped}
      self.assertTrue(original_nonemitted_control(ci.CP, skipped, (before, after), originals))
      self.assertFalse(original_nonemitted_control(ci.CP, skipped, (before,), originals))
      self.assertFalse(original_nonemitted_control(ci.CP, a, (before, after), originals))
      for changed in (
        replace(after, carrier_frame=after.carrier_frame + 1),
        replace(after, native_allowed=False),
        replace(after, fault=True),
        replace(after, apply_ns=stamp + 30_000_001),
      ):
        with self.subTest(changed=changed):
          self.assertFalse(original_nonemitted_control(ci.CP, skipped, (before, changed), originals))
      changed_originals = {**originals, b.control_ns: replace(b, identity='f' * 64)}
      self.assertFalse(original_nonemitted_control(ci.CP, skipped, (before, after), changed_originals))
      self.assertFalse(original_nonemitted_control(ci.CP, replace(skipped, source_receipt_ns=stamp - 500_000_001), (before, after), originals))
      expired = replace(
        skipped, native_required=True, native_allowed=True, native_identity='a' * 64, native_observed_ns=stamp - 1, native_expires_ns=after.apply_ns - 1
      )
      self.assertFalse(original_nonemitted_control(ci.CP, expired, (before, after), originals))

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
  def test_lapped_parked_subscriber_waits_for_real_freshness_and_preserves_safety_refusals(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_daemon import parked_status, wait_for_parked

    def fixture(refresh=True):
      now = [100.0]
      flags = {'IsOffroad': True, 'IsEngaged': False}
      params = SimpleNamespace(get_bool=lambda key: flags[key])

      class Messages:
        def __init__(self):
          self.data = {
            'deviceState': SimpleNamespace(started=False),
            'pandaStates': [SimpleNamespace(ignitionLine=False, ignitionCan=False, controlsAllowed=False, faults=[])],
            'peripheralState': SimpleNamespace(),
          }
          self.seen = dict.fromkeys(self.data, True)
          self.alive = dict.fromkeys(self.data, True)
          self.valid = dict.fromkeys(self.data, True)
          self.recv_time = dict.fromkeys(self.data, now[0])
          self.alive['pandaStates'] = False
          self.recv_time['pandaStates'] = 97.0
          self.calls = []

        def __getitem__(self, service):
          return self.data[service]

        def update(self, timeout):
          self.calls.append(timeout)
          now[0] += timeout / 1000
          if timeout and refresh:
            self.alive['pandaStates'] = True
            self.recv_time['pandaStates'] = now[0]

      return now, flags, params, Messages()

    now, _, params, sm = fixture()
    self.assertFalse(parked_status(sm, params, now=now[0]))
    self.assertTrue(wait_for_parked(sm, params, clock=lambda now=now: now[0]))
    self.assertEqual(sm.calls, [0, 20])
    self.assertTrue(parked_status(sm, params, now=now[0]))

    for unavailable in ('no_new_message', 'invalid', 'unseen'):
      with self.subTest(unavailable=unavailable):
        now, _, params, sm = fixture(refresh=unavailable != 'no_new_message')
        if unavailable == 'invalid':
          sm.valid['pandaStates'] = False
        elif unavailable == 'unseen':
          sm.seen['pandaStates'] = False
        self.assertFalse(wait_for_parked(sm, params, clock=lambda now=now: now[0]))
        self.assertLessEqual(now[0], 100.8)
        self.assertLessEqual(len(sm.calls), 40)

    for unsafe in ('onroad', 'engaged', 'started', 'ignition', 'controls', 'fault', 'stopped'):
      with self.subTest(unsafe=unsafe):
        now, flags, params, sm = fixture()
        if unsafe == 'onroad':
          flags['IsOffroad'] = False
        elif unsafe == 'engaged':
          flags['IsEngaged'] = True
        elif unsafe == 'started':
          sm['deviceState'].started = True
        elif unsafe == 'ignition':
          sm['pandaStates'][0].ignitionCan = True
        elif unsafe == 'controls':
          sm['pandaStates'][0].controlsAllowed = True
        elif unsafe == 'fault':
          sm['pandaStates'][0].faults.append('declared-fault')
        self.assertFalse(wait_for_parked(sm, params, stopped=lambda unsafe=unsafe: unsafe == 'stopped', clock=lambda now=now: now[0]))
        self.assertEqual(sm.calls, [0], 'Actual unsafe state refuses immediately instead of waiting')

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
  def test_actual_log_metadata_preserves_only_original_inhibited_input_flags(self):
    from openpilot.cereal import log, messaging
    from openpilot.starpilot.flm.offroad_control_metadata import read_control_metadata
    from openpilot.starpilot.flm.offroad_processor import _control_eligible, _control_reconstructible
    from openpilot.starpilot.flm.native_refinement_wire import EXTENDED_INPUTS

    stamp = 1_000_000_000
    original = [{'name': 'active', 'number': 1.0}, {'name': 'safety_limited', 'boolean': True}]

    def decoded(inputs, **changes):
      event = messaging.new_message('starpilotLateralState')
      event.valid, event.logMonoTime = True, stamp
      payload = {
        'version': 1,
        'controlMonoTime': stamp,
        'sourceStateMonoTime': stamp,
        'sourceReceiptMonoTime': stamp,
        'enabled': True,
        'active': True,
        'valid': True,
        'limited': True,
        'controllerMode': 'starpilot',
        'controllerRevision': 'declared-serialized-metadata-only',
        'baselineRevision': 'b' * 64,
        'baselineValues': [1.0],
        'stageSchemaRevision': 'torque-selected-stage-snapshot-v1',
        'replayInputs': inputs,
        **changes,
      }
      event.starpilotLateralState.learningEvidence = payload
      with log.Event.from_bytes(event.to_bytes()) as reader:
        return read_control_metadata(reader)

    metadata = decoded(original)
    self.assertEqual(dict(metadata.replay_inputs), {'active': 1.0, 'safety_limited': 1.0})
    self.assertTrue(_control_reconstructible(metadata, stamp))
    self.assertFalse(_control_eligible(metadata, stamp))
    self.assertFalse(metadata.full_replay_validated)
    pending = decoded([original[0], {'name': 'safety_limited', 'number': 0.0}])
    self.assertTrue(_control_reconstructible(pending, stamp))
    self.assertFalse(_control_eligible(pending, stamp))
    self.assertFalse(pending.full_replay_validated)
    for bad in (
      [],
      original[:1],
      original + original[:1],
      [original[0], {'name': 'safety_limited', 'text': 'true'}],
      [original[0], {'name': 'safety_limited', 'number': float('nan')}],
      original + [{'name': 'unneeded', 'number': 0.0}] * EXTENDED_INPUTS,
    ):
      with self.subTest(inputs=bad):
        self.assertFalse(_control_reconstructible(decoded(bad), stamp))
    for changes in (
      {'stageSchemaRevision': 'unknown-original-stage'},
      {'manual': True},
      {'override': True},
      {'fault': True},
      {'saturated': True},
      {'nativeRequired': True, 'nativeAllowed': False},
    ):
      with self.subTest(changes=changes):
        self.assertFalse(_control_reconstructible(decoded(original, **changes), stamp))
    # Unneeded malformed replay vectors remain unvalidated metadata, never replay proof.
    strict = decoded([{'name': 'unneeded', 'text': 'not-a-frame'}], limited=False)
    self.assertTrue(_control_eligible(strict, stamp))
    self.assertEqual(strict.replay_inputs, ())
    self.assertFalse(strict.full_replay_validated)

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
    limited = SimpleNamespace(
      **(
        vars(control)
        | {'limited': True, 'replay_inputs': (('active', 1.0), ('safety_limited', 1.0)), 'stage_schema_revision': 'torque-selected-stage-snapshot-v1'}
      )
    )
    from openpilot.starpilot.flm.offroad_processor import _control_eligible

    self.assertFalse(_control_eligible(limited, timeline[index][0]))
    changed = list(timeline)
    changed[index] = (timeline[index][0], limited)
    self.assertTrue(continuous_control_window(window, changed, reader, metadata))
    for flags in ({'native_allowed': False}, {'saturated': True}, {'manual': True}, {'override': True}, {'fault': True}):
      changed[index] = (timeline[index][0], SimpleNamespace(**(vars(limited) | flags)))
      self.assertFalse(continuous_control_window(window, changed, reader, metadata))
    absent: list[tuple[int, SimpleNamespace | None]] = list(timeline)
    absent[index] = (timeline[index][0], None)
    self.assertFalse(continuous_control_window(window, absent, reader, metadata))

  def test_baseline_children_keep_observed_endpoints_and_all_original_envelope_vetoes(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_evidence import RouteWindow, whole_windows, window_identity
    from openpilot.starpilot.flm.offroad_processor import baseline_control_windows

    # Declared clocks differ from a second controller-run grid by14ms.
    template = TestOffroadTrackingPolicy.windows('declared-aligned-inputs', 1.0)[0].points[0]
    points = tuple(replace(template, mono_ns=1_014_000_000 + i * 10_000_000) for i in range(600))
    parent = RouteWindow(window_identity(points), points)
    metadata = {'route_values': tuple(values().items()), 'scope': 'declared-original'}
    timeline = []
    for stamp in range(points[0].mono_ns - 30_000_000, points[-1].mono_ns + 30_000_001, 5_000_000):
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

    observed = whole_windows(parent.points)
    aligned = baseline_control_windows(parent, timeline, reader, metadata)
    self.assertEqual(len(aligned), 3)
    self.assertEqual(aligned, observed)
    self.assertEqual(baseline_control_windows(parent, timeline, reader, dict(metadata, route_values=tuple(values(0.005).items()))), ())
    index = next(i for i, (stamp, _) in enumerate(timeline) if stamp == points[0].mono_ns + 5_000_000)
    original = timeline[index]
    for change in (None, {'native_allowed': False}, {'metadata': dict(metadata, scope='changed')}, {'override': True}):
      changed: list[tuple[int, SimpleNamespace | None]] = list(timeline)
      changed[index] = (original[0], None if change is None else SimpleNamespace(**(vars(original[1]) | change)))
      with self.subTest(change=change):
        actual = baseline_control_windows(parent, changed, reader, metadata)
        self.assertEqual(actual, observed[1:], 'One bad original envelope vetoes its entire fixed child')
    self.assertEqual(parent.points, points)

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

  def test_segment_snapshots_do_not_alias_cumulative_original_windows(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_processor as processor
    from openpilot.starpilot.flm.offroad_route import RouteReceipt

    # Isolate the real segment boundary. The next segment appends a continuous
    # child and independently adds that same original to the cumulative native list.
    originals = [SimpleNamespace(identity=str(i) * 64) for i in range(1, 7)]
    receipt = RouteReceipt('declared-two-segments', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())

    def walk(root, route, *, permitted, consume):
      self.assertTrue(permitted())
      cells = dict(zip(consume.__code__.co_freevars, consume.__closure__, strict=True))
      native = cells['baseline_qualified'].cell_contents
      published = cells['windows'].cell_contents
      cells['baseline_tracking'].cell_contents['context'] = []
      native['context'] = list(originals[:3])
      consume(0, ())
      published['context'].extend(originals[3:])
      native['context'].extend(originals[3:])
      consume(1, ())
      return receipt

    inputs = processor.collect_route_inputs(None, {'routeId': receipt.route_id}, permitted=lambda: True, walker=walk)
    self.assertEqual(inputs.windows['context'], originals)
    self.assertEqual(len({w.identity for w in inputs.windows['context']}), 6)
    self.assertEqual(inputs.counts['whole_control_window_refusals'], 0)

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
  def test_current_disjoint_windows_do_not_trigger_an_older_drive_rescan(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_worker as module
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt

    # Dispatch-only fixture: training refuses, so no accepted tune is fabricated.
    roles = trial()['before']
    proposal = {'parameter': 'center_taper_max', 'delta': 0.005, 'values': values(0.005)}
    cohort = {'cohort': 'declared-current-drive', 'whole_windows': 6, 'qualification_roles': roles,
              'training': dict(roles['training'], reference_rms=0.5), 'heldout': dict(roles['heldout'], reference_rms=0.5), 'proposal': proposal}
    metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION, 'controller_mode': 'starpilot', 'route_scope': scope().key()}
    group = {'original_metadata': metadata, 'original_context_sha256': 'f' * 64, 'cohorts': [cohort]}
    cp = SimpleNamespace(carFingerprint='declared-current-drive')
    receipt = RouteReceipt('declared-current-drive', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
    inventory = {'routeId': receipt.route_id}
    assessment = CompletedAssessment(receipt, (group,), {}, cp, (), inventory)
    calls = []

    def assessed(params, root, previous, *, permitted):
      previous(cp)
      return assessment

    def refuse_training(actual, original, selected, proposed, previous, root, *, permitted):
      self.assertIs(actual, assessment)
      self.assertEqual(selected['qualification_roles'], roles)
      self.assertEqual(proposed, proposal)
      self.assertTrue(permitted())
      calls.append('current_drive_training')
      return None, {'declared_training_refusal': True}

    def refused_check(*args, **kwargs):
      self.fail('A refused training step cannot enter validation')

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      with (
        patch.object(module, 'latest_route', return_value=inventory),
        patch.object(module, 'analyze_latest', new=assessed),
        patch.object(module, 'qualify', new=refused_check),
        patch('openpilot.starpilot.flm.offroad_checks.fit_training_step', new=refuse_training),
        patch('openpilot.starpilot.flm.offroad_prior_inputs.prior_originals', side_effect=AssertionError('Unexpected older drive rescan')),
        patch('openpilot.starpilot.flm.offroad_prior_inputs.prior_stable_originals', side_effect=AssertionError('Unexpected older drive rescan')),
      ):
        worker = module.OffroadWorker(params, directory, Path(directory) / 'cache', permitted=lambda: True,
          assessor=assessed, checker=refused_check, scope_for=lambda cp, params: scope())
        result = worker.run_once()
      self.assertEqual(calls, ['current_drive_training'])
      self.assertFalse(result['configuration_changed'])
      self.assertEqual(result['reason'], 'no_new_native_feasible_training_step')
      self.assertEqual(read_saved(params, KEY, MAX_BYTES), (None, True))

  def test_quieting_trial_is_frozen_before_unrelated_heldout_turn_disagreement(self):
    import copy
    from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector

    # Declared proposal-policy regression, using the retained profile's numeric shape.
    # Actual controller/native qualification and automatic storage are not mocked.
    roles = trial()['before']
    cohort = {'cohort': '2:left:steady', **roles}
    original = values()
    original.update(friction_threshold_mid=0.004625, friction_threshold_fast=0.0061875, friction_threshold_highway=0.0061875, unwind_taper_left=0.01)
    training = {
      'values': original,
      'changed_parameters': tuple(name for name, value in original.items() if value),
      'maximum_coordinate_step': 0.01,
      'bounded_profile_scale': 0.625,
    }
    independent = values()
    independent.update(friction_threshold_mid=0.0071, friction_threshold_fast=0.0095, friction_threshold_highway=0.0095)
    frozen = copy.deepcopy(training)
    for unsupported in (None, 0.0, -0.0095, 0.0001):
      with self.subTest(unsupported_primary=unsupported):
        selector = object.__new__(StableRouteSelector)
        selector.previous = values()
        selector.rows = dict.fromkeys(roles['training']['windows'] + roles['heldout']['windows'], [])
        events = []
        heldout_values = dict(independent)
        if unsupported is not None:
          heldout_values['friction_threshold_fast'] = unsupported

        def declared_profile(windows, selected_cohort, events=events, heldout_values=heldout_values):
          self.assertEqual(selected_cohort, cohort['cohort'])
          if windows == tuple(roles['training']['windows']):
            events.append('training')
            return copy.deepcopy(frozen), {'declared_role': 'training'}
          self.assertEqual(windows, tuple(roles['heldout']['windows']))
          events.append('heldout')
          return {'values': heldout_values}, {'declared_role': 'heldout'}

        with patch.object(selector, 'profile', side_effect=declared_profile):
          proposed, proof = selector.propose({}, cohort)
        self.assertEqual(events, ['training', 'heldout'])
        self.assertEqual(proof['selection'], 'training_only_conservative_friction_trial_before_heldout')
        expected = values()
        expected.update(friction_threshold_mid=0.002775, friction_threshold_fast=0.0037125, friction_threshold_highway=0.0037125)
        self.assertEqual(proof['frozen_training_profile']['values'], expected)
        self.assertEqual(training, frozen)
        if unsupported is None:
          self.assertIsNotNone(proposed)
          assert proposed is not None
          self.assertEqual(proposed['values'], expected)
          self.assertEqual(proposed['values']['unwind_taper_left'], 0.0)
          self.assertEqual(set(proof['frozen_training_profile']['changed_parameters']), set(CURVE_NAMES[2:]))
          self.assertTrue(proof['all_original_windows_still_required'])
        else:
          self.assertIsNone(proposed, 'Missing, reversed or weaker primary evidence must still refuse; no resizing or fallback')

  def test_turn_only_training_profile_keeps_original_values(self):
    from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector

    roles = trial()['before']
    mapped = values()
    mapped['unwind_taper_left'] = 0.005
    profile = {'values': mapped, 'changed_parameters': ('unwind_taper_left',), 'maximum_coordinate_step': 0.005, 'bounded_profile_scale': 1.0}
    selector = object.__new__(StableRouteSelector)
    selector.previous = values()
    selector.rows = dict.fromkeys(roles['training']['windows'] + roles['heldout']['windows'], [])
    with patch.object(selector, 'profile', return_value=(profile, {'declared_proposal_only': True})):
      proposed, proof = selector.propose({}, {'cohort': '2:left:unwind', **roles})
    self.assertIsNotNone(proposed)
    assert proposed is not None
    self.assertEqual(proposed['values'], mapped)
    self.assertEqual(proposed['delta'], 0.005)
    self.assertEqual(proof['selection'], 'training_values_only_no_heldout_resizing')

  def test_heldout_profile_supports_frozen_training_step_without_identical_floats(self):
    import copy
    from openpilot.starpilot.flm.offroad_stable_bridge import independent_profile_support

    previous = values()
    training = {'values': values()}
    heldout = {'values': values()}
    training['values']['ff_gain_right'] = 0.0099
    heldout['values']['ff_gain_right'] = 0.0109
    training['values']['unwind_taper_right'] = -0.0074
    heldout['values']['unwind_taper_right'] = -0.0082
    original = copy.deepcopy(training)
    self.assertTrue(independent_profile_support(training, heldout, previous))
    self.assertTrue(independent_profile_support(training, training, previous))
    for invalid in (0.0, -0.0109, 0.0098, float('nan'), float('inf'), True):
      unsupported = copy.deepcopy(heldout)
      unsupported['values']['ff_gain_right'] = invalid
      with self.subTest(invalid=invalid):
        self.assertFalse(independent_profile_support(training, unsupported, previous))
    self.assertFalse(independent_profile_support(None, heldout, previous))
    self.assertFalse(independent_profile_support(training, None, previous))
    self.assertFalse(independent_profile_support({'values': values()}, heldout, previous))
    self.assertFalse(independent_profile_support(training, {'values': {}}, previous))
    self.assertEqual(training, original, 'Held-out evidence must never resize the proposed map')

  def test_recommended_profile_is_ranked_before_fit_even_with_a_tracking_proposal(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_worker as module
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt

    # Declared dispatch test. The final checker REFUSES; no accepted tune is mocked.
    events = []
    roles = trial()['before']
    metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION, 'controller_mode': 'starpilot', 'route_scope': scope().key()}
    tracking = {
      'cohort': 'declared-center',
      'whole_windows': 6,
      'proposal': {'parameter': 'center_taper_max', 'delta': 0.005, 'values': values(0.005)},
      'training': dict(roles['training'], reference_rms=0.05),
      'heldout': dict(roles['heldout'], reference_rms=0.05),
    }
    stable = {
      'cohort': 'declared-steady',
      'whole_windows': 6,
      'qualification_roles': roles,
      'training': dict(roles['training'], reference_rms=0.6),
      'heldout': dict(roles['heldout'], reference_rms=0.5),
    }
    group = {'original_metadata': metadata, 'original_context_sha256': 'f' * 64, 'cohorts': [tracking]}
    stable_group = dict(group, cohorts=[stable])
    cp = SimpleNamespace(carFingerprint='declared-dispatch-only')
    receipt = RouteReceipt('declared-profile-drive', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
    inventory = {'routeId': receipt.route_id}
    assessment = CompletedAssessment(receipt, (group,), {}, cp, (), inventory, stable_groups=(stable_group,))
    mapped = values()
    mapped['ff_gain_right'] = 0.005
    proposal = {'parameter': 'stable:ff_gain_right', 'delta': 0.005, 'values': mapped, 'policy': 'original_stable_recommended_bounded_route_v1'}

    class DeclaredSelector:
      def __init__(self, *args, **kwargs):
        pass

      def propose(self, *args):
        events.append('profile_before_fit')
        return proposal, {'reason': 'declared_replicated_profile', 'all_original_windows_still_required': True}

    def assessed(params, root, previous, *, permitted):
      previous(cp)
      return assessment

    def training(actual, original, cohort, selected, previous, root, *, permitted):
      self.assertIs(actual, assessment)
      self.assertEqual(selected, proposal)
      events.append('training')
      return selected, {'declared_dispatch_only': True}

    def refused(actual, original, cohort, selected, root, *, permitted):
      self.assertEqual(selected, mapped)
      events.append('all_original_checks')
      return {'qualified': False, 'windows': [], 'reason': 'declared_native_refusal'}

    def scalar_provider(*args, **kwargs):
      self.fail('A multi-coordinate profile must not enter the single-parameter response validator')

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      with (
        patch.object(module, 'latest_route', return_value=inventory),
        patch.object(module, 'qualify', new=refused),
        patch('openpilot.starpilot.flm.offroad_stable_bridge.StableRouteSelector', new=DeclaredSelector),
        patch('openpilot.starpilot.flm.offroad_checks.fit_training_step', new=training),
      ):
        worker = module.OffroadWorker(
          params,
          directory,
          Path(directory) / 'cache',
          permitted=lambda: True,
          assessor=assessed,
          checker=refused,
          scope_for=lambda cp, params: scope(),
          prior_provider=scalar_provider,
        )
        result = worker.run_once()
      self.assertEqual(events, ['profile_before_fit', 'training', 'all_original_checks'])
      self.assertEqual(result['suggestion']['parameter'], proposal['parameter'])
      self.assertEqual(result['suggestion_count'], 1)
      self.assertEqual(
        result['suggestions'],
        [
          {
            'parameter': 'ff_gain_right',
            'delta': 0.005,
            'whole_windows': stable['whole_windows'],
            'qualification': 'controller_native_checks_not_passed',
          }
        ],
      )
      self.assertEqual(result['reason'], 'declared_native_refusal')
      self.assertFalse(result['configuration_changed'])
      self.assertEqual(read_saved(params, KEY, MAX_BYTES), (None, True))

  def test_reference_input_coverage_never_ranks_by_actual_errors_or_fit_results(self):
    from openpilot.starpilot.flm.offroad_worker import input_coverage

    straight = {'whole_windows': 195, 'training': {'reference_rms': 0.05}, 'heldout': {'reference_rms': 0.05}}
    turn = {'whole_windows': 6, 'training': {'reference_rms': 0.45}, 'heldout': {'reference_rms': 0.40}}
    self.assertGreater(input_coverage(turn), input_coverage(straight))
    original = input_coverage(turn)
    turn['training'].update(residual_rms=1000.0, gain=-1000.0, error_reversal_hz=1000.0)
    self.assertEqual(input_coverage(turn), original)
    self.assertEqual(input_coverage({'whole_windows': 100000}), 0.0)

  def test_old_source_inputs_require_exact_current_vehicle_settings_and_numeric_baseline(self):
    import copy
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.flm.offroad_worker import _current_configuration, recorded_source_contract
    from openpilot.starpilot.flm.runtime_adapter_registry import baseline_revision

    cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    cp.carVin = 'KMHM34AA0NA000001'
    cp = cp.as_reader()
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('OpenpilotEnabledToggle', True, block=True)
      params.put_bool('ForceAutoTuneOff', False, block=True)
      self.assertEqual(read_saved(params, 'OpenpilotEnabledToggle', 8), (b'1', True))
      self.assertEqual(read_saved(params, 'ForceAutoTuneOff', 8), (b'0', True))
      current, descriptor, numeric = _current_configuration(cp, params)
      previous_descriptor = replace(descriptor, controller_revision='declared-previous-implementation')
      previous_baseline = baseline_revision(previous_descriptor, numeric)
      previous = replace(current, controller_revision=previous_descriptor.controller_revision, baseline_revision=previous_baseline)
      metadata = {
        'cp_sha256': current.cp_sha256,
        'controller_mode': current.controller_mode,
        'controller_revision': previous.controller_revision,
        'baseline_revision': previous_baseline,
        'baseline_values': numeric,
        'route_scope': previous.key(),
      }
      original = copy.deepcopy(metadata)
      contract = recorded_source_contract(cp, metadata, current, params)
      self.assertIsNotNone(contract)
      assert contract is not None
      self.assertEqual(contract['original_scope'], previous.document())
      self.assertEqual(contract['current_scope'], current.document())
      self.assertFalse(contract['stored_tune_migrated'])
      self.assertEqual(metadata, original)
      for changed in (
        {'route_scope': ''},
        {'cp_sha256': 'f' * 64},
        {'controller_mode': 'stock'},
        {'baseline_values': (numeric[0] + 0.01, *numeric[1:])},
        {'baseline_revision': 'f' * 64},
        {'controller_revision': 'different-previous-implementation'},
        {'route_scope': replace(previous, persistent_source_epoch='f' * 64).key()},
        {'route_scope': replace(previous, reset_generation='1').key()},
        {'baseline_revision': baseline_revision(replace(previous_descriptor, policy='different-policy'), numeric)},
      ):
        with self.subTest(changed=changed):
          self.assertIsNone(recorded_source_contract(cp, dict(metadata, **changed), current, params))
      with patch('openpilot.starpilot.flm.offroad_worker._current_configuration', return_value=(replace(current, reset_generation='1'), descriptor, numeric)):
        with self.assertRaisesRegex(ValueError, 'changed during source comparison'):
          recorded_source_contract(cp, metadata, current, params)
      self.assertEqual(read_saved(params, KEY, MAX_BYTES), (None, True))

  def test_completed_measurements_survive_failed_qualification_and_cached_restart(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_worker as module
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt

    roles = trial()['before']
    proposal = {'values': values(0.005), 'parameter': 'center_taper_max', 'delta': 0.005}
    cohort = dict(roles, cohort='declared-refusal', whole_windows=6, proposal=proposal)
    metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION, 'controller_mode': 'starpilot', 'route_scope': scope().key()}
    group = {'original_metadata': metadata, 'original_context_sha256': 'f' * 64, 'cohorts': [cohort]}
    receipt = RouteReceipt('declared-completed-drive', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
    inventory = {'routeId': receipt.route_id}
    recorded = {'domain': 'lateral_acceleration', 'samples': 1200, 'windows': 6, 'error_rms': 0.1, 'points': [[1, 0.2, 0.1], [2, 0.3, 0.2]]}
    assessment = CompletedAssessment(
      receipt,
      (group,),
      {'recorded_tracking': 1200, 'qualified_whole_windows': 6},
      SimpleNamespace(carFingerprint='declared-unit-car'),
      (),
      inventory,
      recorded_tracking=recorded,
    )

    def assessed(params, root, previous, *, permitted):
      previous(assessment.cp)
      return assessment

    def refused(*args, **kwargs):
      raise ValueError('Declared original-window refusal')

    with tempfile.TemporaryDirectory() as directory, patch.object(module, 'latest_route', return_value=inventory):
      params = Params(directory)
      worker = module.OffroadWorker(
        params, directory, Path(directory) / 'cache', permitted=lambda: True, assessor=assessed, checker=refused, scope_for=lambda cp, params: scope()
      )
      with self.assertRaisesRegex(ValueError, 'Declared original-window refusal') as caught:
        worker.run_once()
      result = worker.record_failure(caught.exception)
      self.assertIsNotNone(result)
      assert result is not None
      self.assertEqual(result['status'], 'unavailable')
      self.assertEqual(result['completed_route'], receipt.route_id)
      self.assertEqual(result['source_sha256'], receipt.source_sha256)
      self.assertEqual(result['recorded_tracking'], recorded)
      self.assertFalse(result['configuration_changed'])
      self.assertFalse(result['actual_application_proven'])
      self.assertNotIn('suggestion', result)
      self.assertEqual(read_saved(params, KEY, MAX_BYTES), (None, True))
      fresh = module.OffroadWorker(
        params,
        directory,
        Path(directory) / 'cache',
        permitted=lambda: True,
        assessor=lambda *args, **kwargs: self.fail('A cached refusal must not repeat numerical work'),
      )
      self.assertEqual(fresh.run_once(), result)
      status_path = Path(directory) / 'status.json'
      report = StatusReport(path=status_path)
      report.set(result)
      report.publish()
      current = json.loads(status_path.read_bytes())['current']
      self.assertEqual(current['details']['Recorded steering response'], recorded)
      self.assertEqual(current['progress']['samples'], 1200)
      self.assertFalse(current['application']['applied'])
      # Source change invalidates the completed-data error checkpoint as well.
      with patch.object(module, 'latest_route', return_value={'routeId': 'changed-input'}):
        self.assertIsNone(worker.record_failure(caught.exception))

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

  def test_prior_profile_join_keeps_original_metadata_key_and_rejects_other_map(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_prior_inputs as module
    from openpilot.starpilot.flm.offroad_route import SelectedSourceReceipt

    # Declared input-join fixture only; no candidate fitting or accepted tune is mocked.
    current_id, prior_id = '0000000a--aaaaaaaaaa', '00000009--bbbbbbbbbb'
    metadata_key, comparison_key, other_key = 'd' * 64, 'e' * 64, 'f' * 64
    metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION, 'baseline_revision': 'c' * 64}
    changed = dict(metadata, route_values=tuple(values(0.01).items()))

    def window(segment, identity):
      points = tuple(
        SimpleNamespace(segment=segment, mono_ns=segment * 10_000_000_000 + i * 10_000_000, context_key=comparison_key, desired=0.2, speed_mps=25.0)
        for i in range(8)
      )
      return SimpleNamespace(identity=identity, points=points, domain='lateral_acceleration')

    native = [window(n, f'{n:064x}') for n in range(1, 7)]
    profile = [window(n, f'{n + 10:064x}') for n in range(1, 4)]
    wrong_map = [window(n, f'{n + 20:064x}') for n in range(1, 7)]
    receipt = SelectedSourceReceipt(prior_id, 'b' * 64, (), 'a' * 64, 1, 2, 3, (), tuple(range(8)))
    collected = SimpleNamespace(
      receipt=receipt,
      state={'cp': object()},
      windows={metadata_key: native, other_key: wrong_map},
      context_metadata={metadata_key: metadata, other_key: changed},
      baseline_tracking={},
      stable_windows={metadata_key: profile, other_key: wrong_map},
    )
    route = {'routeId': prior_id, 'segments': [{'files': {'rlog': True}} for _ in range(8)]}
    assessment = SimpleNamespace(receipt=SimpleNamespace(
      route_id=current_id, cp_sha256='a' * 64, completed_wall_ns=10, segments=((current_id + '--0', 'c' * 64, 1),),
    ))
    with (
      patch.object(module, 'route_inventory', return_value={'scanIncomplete': False, 'routes': [route]}),
      patch.object(module, 'collect_route_inputs', return_value=collected),
      patch.object(module, 'cohort', return_value='2:right:steady'),
    ):
      result = module.prior_stable_originals(assessment, {'original_metadata': metadata}, {'cohort': '2:right:steady'}, '.', permitted=lambda: True)
    self.assertIsNotNone(result)
    assert result is not None
    self.assertEqual(tuple(row['identity'] for row in result.windows), tuple(w.identity for w in native))
    self.assertEqual(tuple(row['identity'] for row in result.profile_windows), tuple(w.identity for w in profile))
    self.assertEqual(result.profile_metadata, metadata)
    self.assertEqual(result.receipt, receipt)
    self.assertEqual(result.source_inventory, route)
    self.assertFalse({row['identity'] for row in result.profile_windows} & {w.identity for w in wrong_map})

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
    from openpilot.starpilot.flm.offroad_prior_inputs import selected_segments

    base = set(interior_segments(100)) | {0, 99}
    additional = [n for n in range(100) if n not in base][:29]
    self.assertEqual(selected_segments(100), tuple(sorted(base)))
    self.assertEqual(selected_segments(100, expanded=True), tuple(sorted(base | set(additional))))
    self.assertEqual(len(selected_segments(100, expanded=True)), 43)
    for count in (1, 2, 3, 41, 100, 180):
      selected = selected_segments(count, expanded=True)
      self.assertLessEqual(len(selected), 43)
      self.assertEqual(tuple(sorted(set(selected))), selected)
      self.assertTrue({0, count - 1}.issubset(selected))
      self.assertTrue(all(0 <= n < count for n in selected))
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
    native_boundary = 0.004
    emitted_changes = 1

    def recorded(actual, source, selected, trial_values, root, *, permitted):
      self.assertTrue(permitted())
      calls.append((actual is prior, tuple(selected), trial_values['center_taper_max']))
      okay = actual is not prior and trial_values['center_taper_max'] <= native_boundary
      return [
        {
          'window': wid,
          'qualified': okay,
          'numerical': {'qualified': True, 'changed_frames': 1},
          'registered_encoder': {'qualified': okay, 'candidate_changed_emissions': emitted_changes},
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
    native_boundary = proposed['delta'] * 0.05
    calls.clear()
    with (
      patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='a' * 64),
      patch.object(checks, '_original_results', new=recorded),
      patch('openpilot.starpilot.flm.offroad_tracking.estimate', return_value=observed),
      patch('openpilot.starpilot.flm.offroad_tracking.suggest', return_value=replication),
    ):
      fitted, proof, result = checks.fit_independent_route(current, group, cohort, prior, proposed, values(), '.', permitted=lambda: True)
    assert fitted is not None and result is not None
    self.assertEqual(proof['selected_fraction'], 0.05)
    self.assertEqual(proof['validation_evaluations'], 1)
    self.assertFalse(result['qualified'])
    self.assertEqual([ids for _, ids, _ in calls], [ids] * 7 + [valid_ids])
    self.assertEqual(calls[-1], (True, valid_ids, native_boundary))
    self.assertEqual(fitted['values']['center_taper_max'], native_boundary)

    emitted_changes = 0
    calls.clear()
    with (
      patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='a' * 64),
      patch.object(checks, '_original_results', new=recorded),
      patch('openpilot.starpilot.flm.offroad_tracking.estimate', return_value=observed) as independent_estimate,
      patch('openpilot.starpilot.flm.offroad_tracking.suggest', return_value=replication),
    ):
      fitted, proof, result = checks.fit_independent_route(current, group, cohort, prior, proposed, values(), '.', permitted=lambda: True)
    self.assertIsNone(fitted)
    self.assertIsNone(result)
    self.assertEqual(proof['validation_evaluations'], 0)
    self.assertEqual([row[1] for row in calls], [ids] * 7)
    self.assertTrue(all(not row[0] for row in calls))
    independent_estimate.assert_not_called()

    with patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='0' * 64), self.assertRaises(ValueError):
      checks.fit_independent_route(current, group, cohort, prior, proposed, values(), '.', permitted=lambda: True)

  def test_independent_stable_profile_freezes_before_validation_and_never_retries(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm import offroad_checks as checks
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_prior_inputs import PriorOriginals
    from openpilot.starpilot.flm.offroad_route import RouteReceipt, SelectedSourceReceipt

    cp = SimpleNamespace(carFingerprint='declared-independent-stable-unit')
    ids = tuple(str(i) * 64 for i in range(1, 7))
    valid_ids = tuple(str(i) * 64 for i in range(7, 10)) + ('a' * 64, 'b' * 64, 'c' * 64)
    roles = {'training': {'windows': ids[:3]}, 'heldout': {'windows': ids[3:]}}
    metadata = {'controller_mode': 'starpilot', 'route_scope': scope().key(), 'baseline_values': (1.0,), 'route_values': tuple(values().items())}
    cohort = {'cohort': '2:right:steady', 'qualification_roles': roles}
    group = {'original_metadata': metadata, 'qualification_original_windows': tuple({'identity': wid} for wid in ids)}
    current = CompletedAssessment(RouteReceipt('declared-current', 'd' * 64, (), 'a' * 64, 10, 20, 30, ()), (), {}, cp, (), {})
    prior = PriorOriginals(
      cp,
      SelectedSourceReceipt('declared-prior', 'e' * 64, (), 'a' * 64, 1, 2, 3, (), (0, 1)),
      {},
      tuple({'identity': wid, 'original_metadata': dict(metadata)} for wid in valid_ids),
      (),
      tuple({'identity': wid} for wid in valid_ids[:3]),
      dict(metadata),
    )
    proposed_values = values()
    proposed_values['friction_threshold_fast'] = 0.01
    proposal = {
      'parameter': 'stable:friction_threshold_fast',
      'delta': 0.01,
      'values': proposed_values,
      'policy': 'original_stable_recommended_bounded_route_v1',
    }
    events = []
    observed = {'values': dict(proposed_values)}

    class DeclaredSelector:
      def __init__(self, actual, root, previous, actual_scope, *, permitted):
        self.asserted = actual is prior and actual_scope == scope() and permitted()
        if not self.asserted:
          raise ValueError('Wrong independent source/scope')

      def profile(self, windows, actual_cohort):
        events.append(('independent_profile', windows))
        return observed, {'declared_unit_only': True}

    def recorded(actual, source, selected, trial_values, root, *, permitted):
      amount = trial_values['friction_threshold_fast']
      events.append(('validation' if actual is prior else 'training', tuple(selected), amount))
      okay = actual is not prior and amount <= 0.001
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
      patch('openpilot.starpilot.flm.offroad_stable_bridge.StableRouteSelector', new=DeclaredSelector),
    ):
      fitted, proof, result = checks.fit_independent_route(current, group, cohort, prior, proposal, values(), '.', permitted=lambda: True, scope=scope())
    assert fitted is not None and result is not None
    self.assertEqual(proof['selected_fraction'], 0.1)
    self.assertEqual(proof['validation_evaluations'], 1)
    self.assertFalse(result['qualified'], 'A failed independent native window must refuse the entire map')
    self.assertEqual([e[0] for e in events], ['training'] * 6 + ['independent_profile', 'validation'])
    self.assertEqual(events[-1][1], valid_ids)
    self.assertEqual(fitted['values']['friction_threshold_fast'], 0.001)
    self.assertEqual(proposed_values['friction_threshold_fast'], 0.01)

    events.clear()
    observed['values']['friction_threshold_fast'] = -0.01
    with (
      patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='a' * 64),
      patch.object(checks, '_original_results', new=recorded),
      patch('openpilot.starpilot.flm.offroad_stable_bridge.StableRouteSelector', new=DeclaredSelector),
    ):
      _, proof, result = checks.fit_independent_route(current, group, cohort, prior, proposal, values(), '.', permitted=lambda: True, scope=scope())
    assert result is not None
    self.assertFalse(result['qualified'])
    self.assertEqual(result['unperformed_windows'], 6)
    self.assertEqual([e[0] for e in events], ['training'] * 6 + ['independent_profile'])

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
      return [
        {'window': wid, 'qualified': amount <= 0.004 and wid not in heldout,
         'numerical': {'qualified': True, 'changed_frames': 1},
         'registered_encoder': {'qualified': amount <= 0.004 and wid not in heldout, 'candidate_changed_emissions': 1}}
        for wid in ids
      ]

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
    self.assertEqual(calls, [train] * 7)
    self.assertIsNone(proof['selected_fraction'])

  def test_training_can_select_an_alternative_but_validation_never_can(self):
    import hashlib
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
    from openpilot.starpilot.flm.offroad_route import RouteReceipt
    from openpilot.starpilot.flm.offroad_worker import OffroadWorker

    # Declared dispatch responses only; the final validation always refuses.
    for scenario in ('first_refuses', 'zero_native_change', 'heldout_refuses', 'overlap', 'all_refuse'):
      with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
        names = ('center_taper_max', 'turn_in_boost_left', 'unwind_taper_right')
        roles = [
          {role: {'windows': tuple(hashlib.sha256(f'{index}:{role}:{n}'.encode()).hexdigest() for n in range(3))}
           for role in ('training', 'heldout')}
          for index in range(3)
        ]
        if scenario == 'overlap':
          roles[1]['heldout']['windows'] = (roles[0]['training']['windows'][0], *roles[1]['heldout']['windows'][1:])
        cohorts = [
          {'cohort': f'declared-{index}', 'whole_windows': 6, 'qualification_roles': roles[index],
           'training': {'reference_rms': 3 - index}, 'heldout': {'reference_rms': 3 - index},
           'proposal': {'parameter': name, 'delta': 0.005, 'values': dict(values(), **{name: 0.005})}}
          for index, name in enumerate(names)
        ]
        metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION,
                    'controller_mode': 'starpilot', 'route_scope': scope().key()}
        group = {'original_metadata': metadata, 'original_context_sha256': 'f' * 64,
                 'cohorts': cohorts, 'qualification_original_windows': []}
        cp = SimpleNamespace(carFingerprint='declared-candidate-selection-only')
        receipt = RouteReceipt('declared-candidates', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
        inventory = {'routeId': receipt.route_id}
        assessment = CompletedAssessment(receipt, (group,), {}, cp, (), inventory)
        calls = []

        def checked(actual, original, ids, trial_values, root, *, permitted, assessment=assessment, names=names, roles=roles, calls=calls, scenario=scenario):
          self.assertIs(actual, assessment)
          self.assertTrue(permitted())
          index = next(i for i, name in enumerate(names) if trial_values[name])
          train, heldout = (roles[index][role]['windows'] for role in ('training', 'heldout'))
          validation = tuple(ids) == train + heldout
          self.assertIn(tuple(ids), (train, train + heldout))
          calls.append((index, validation, tuple(ids)))
          feasible = scenario != 'all_refuse' and (index > 0 or scenario == 'heldout_refuses')
          native_change = int(feasible)
          if scenario == 'zero_native_change' and index == 0:
            feasible = True
          return [
            {'window': wid, 'qualified': feasible and not validation,
             'numerical': {'qualified': True, 'changed_frames': 50},
             'registered_encoder': {'qualified': feasible and not validation, 'candidate_changed_emissions': native_change}}
            for wid in ids
          ]

        def assessed(params, root, previous, *, permitted, cp=cp, assessment=assessment):
          previous(cp)
          return assessment

        params = Params(directory)
        with (
          patch('openpilot.starpilot.flm.offroad_worker.latest_route', return_value=inventory),
          patch('openpilot.starpilot.flm.offroad_checks._original_results', new=checked),
        ):
          worker = OffroadWorker(params, directory, Path(directory) / 'cache', permitted=lambda: True,
                                 assessor=assessed, scope_for=lambda cp, params: scope())
          result = worker.run_once()
        self.assertFalse(result['configuration_changed'])
        self.assertEqual(read_saved(params, KEY, MAX_BYTES), (None, True))
        if scenario == 'all_refuse':
          self.assertEqual([i for i, _, _ in calls], [0] * 7 + [1] * 7 + [2] * 7)
          self.assertFalse(any(validation for _, validation, _ in calls))
          self.assertEqual(result['reason'], 'no_new_native_feasible_training_step')
        else:
          selected = 0 if scenario == 'heldout_refuses' else 2 if scenario == 'overlap' else 1
          self.assertEqual([i for i, _, _ in calls], ([] if selected == 0 else [0] * 7) + [selected] * 2)
          self.assertEqual([(i, validation) for i, validation, _ in calls if validation], [(selected, True)])
          self.assertEqual(result['reason'], 'original_controller_or_registered_encoder_check_refused')
          report = worker.cache.get(result['assessment_sha256'])['assessment']
          self.assertEqual(len(report['native_candidate_training']), 1 if selected == 0 else 2)
          validation_ids = set(roles[selected]['heldout']['windows'])
          self.assertTrue(all(not validation_ids.intersection(ids) for _, validation, ids in calls if not validation))

  def test_zero_native_change_cannot_consume_independent_validation(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_checks import fit_training_step

    train = tuple(str(i) * 64 for i in range(1, 4))
    heldout = tuple(str(i) * 64 for i in range(4, 7))
    cohort = {'qualification_roles': {'training': {'windows': train}, 'heldout': {'windows': heldout}}}
    proposal = {'values': values(0.005), 'delta': 0.005}
    calls = []

    def unchanged(actual, original, ids, trial_values, root, *, permitted):
      self.assertTrue(permitted())
      calls.append(tuple(ids))
      return [
        {'window': wid, 'qualified': True,
         'numerical': {'qualified': True, 'changed_frames': 50},
         'registered_encoder': {'qualified': True, 'candidate_changed_emissions': 0}}
        for wid in ids
      ]

    with patch('openpilot.starpilot.flm.offroad_checks._original_results', new=unchanged):
      fitted, proof = fit_training_step(SimpleNamespace(), {'qualification_original_windows': []}, cohort, proposal, values(), '.', permitted=lambda: True)
    self.assertIsNone(fitted)
    self.assertIsNone(proof['selected_fraction'])
    self.assertEqual(calls, [train] * 7)
    self.assertTrue(all(not row['feasible'] for row in proof['attempts']))
    self.assertEqual(proof['heldout_windows_not_evaluated'], heldout)

  def test_recommended_profile_can_fit_below_coarse_native_quantization_boundary(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_checks import fit_training_step, qualify

    train = tuple(str(i) * 64 for i in range(1, 4))
    heldout = tuple(str(i) * 64 for i in range(4, 7))
    roles = {'training': {'windows': train}, 'heldout': {'windows': heldout}}
    group = {'qualification_original_windows': []}
    cohort = {'qualification_roles': roles}
    previous = values()
    desired = values()
    desired['friction_threshold_fast'] = 0.0099
    desired['friction_threshold_highway'] = 0.0099
    assessment = SimpleNamespace()
    calls = []

    def checked(actual, original, ids, trial_values, root, *, permitted):
      self.assertIs(actual, assessment)
      self.assertTrue(permitted())
      self.assertTrue(set(ids) in (set(train), set(train + heldout)))
      amount = trial_values['friction_threshold_fast']
      self.assertEqual(amount, trial_values['friction_threshold_highway'])
      calls.append((tuple(ids), amount))
      return [
        {'window': wid, 'qualified': amount <= boundary and wid not in heldout,
         'numerical': {'qualified': True, 'changed_frames': 1},
         'registered_encoder': {'qualified': amount <= boundary and wid not in heldout, 'candidate_changed_emissions': 1}}
        for wid in ids
      ]

    for policy in ('observed_tracking_v1', 'original_stable_recommended_bounded_route_v1'):
      for fraction, training_count in ((0.1, 6), (0.05, 7)):
        proposal = {'values': desired, 'delta': 0.0099, 'policy': policy}
        boundary = desired['friction_threshold_fast'] * fraction
        calls.clear()
        with patch('openpilot.starpilot.flm.offroad_checks._original_results', new=checked):
          fitted, proof = fit_training_step(assessment, group, cohort, proposal, previous, '.', permitted=lambda: True)
          assert fitted is not None
          frozen = dict(fitted['values'])
          self.assertEqual(proof['selected_fraction'], fraction)
          self.assertEqual([ids for ids, _ in calls], [train] * training_count)
          self.assertEqual(frozen['friction_threshold_fast'], boundary)
          self.assertEqual(previous, values())
          self.assertEqual(desired['friction_threshold_fast'], 0.0099)
          result = qualify(assessment, group, cohort, frozen, '.', permitted=lambda: True)
          self.assertFalse(result['qualified'])
          self.assertEqual(calls[-1], (train + heldout, boundary))
          self.assertEqual(len(calls), training_count + 1)
          self.assertEqual(fitted['values'], frozen)

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
