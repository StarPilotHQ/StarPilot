"""Declared angle evidence, never measured road benefit or fabricated friction."""
from dataclasses import replace
import math
from types import SimpleNamespace

import pytest

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.offroad_analysis import analyze
from openpilot.starpilot.flm.offroad_coordinate_policy import angle_coordinate_candidates
from openpilot.starpilot.flm.offroad_evidence import RouteWindow, window_identity
from openpilot.starpilot.flm.offroad_tracking import TrackingEstimate
from openpilot.starpilot.flm.offroad_worker import recorded_recommendations
from openpilot.starpilot.flm.pid_stages import SUPPORTED, UNSUPPORTED
from openpilot.starpilot.flm.tests import test_offroad_coordinate_policy as fixtures
from openpilot.starpilot.flm.tests.test_offroad_backend import values


def angle_windows(count, *, speed=20., phase='turn_in', gain=1., lag=.2):
  result = []
  for number in range(1, count + 1):
    source = next(iter(fixtures.TestCrossDriveEvidence.inputs(number)[0].windows.values()))[0]

    def request(t):
      if phase == 'center':
        return .1 * math.sin(2 * math.pi * t)
      if phase == 'steady':
        return .6 + .1 * math.sin(2 * math.pi * t)
      curve = 1 + .25 * t + .5 * t**2
      return curve if phase == 'turn_in' else 7.25 - curve

    points = []
    for index, point in enumerate(source.points):
      t = index / 100
      desired = request(t)
      actual = gain * request(max(0., t - lag))
      if phase == 'center':
        actual = desired + .3 * math.sin(6 * math.pi * t)
      points.append(replace(point, domain='steering_angle', speed_mps=speed, desired=desired, actual=actual))
    points = tuple(points)
    result.append(RouteWindow(window_identity(points), points))
  return tuple(result)


def declared_estimate(windows, phase):
  """An explicitly declared fitted interval, not a claimed route measurement."""
  return TrackingEstimate('a' * 64, 'steering_angle', 'declared:' + phase, 0.85 if phase in ('steady', 'turn_in') else 1.,
    200_000_000, 0., (.84, .86) if phase in ('steady', 'turn_in') else (.99, 1.01), (200_000_000, 200_000_000), (0., 0.),
    .1 if phase == 'center' else 1., .2 if phase == 'center' else .01,
    tuple(w.identity for w in windows), 'b' * 64, 2. if phase == 'center' else 0., 0., .1, (.2, .2) if phase == 'center' else (.01, .01))


def test_all_26_actual_pid_coordinates_have_bounded_angle_recipes_without_friction():
  previous = {name: 0. if name in UNSUPPORTED else .01 for name in NAMES}
  found = {}
  for phase in ('center', 'steady', 'turn_in', 'unwind'):
    for speed in (2., 7.2, 11.5, 20.5, 21.5, 26.):
      windows = angle_windows(3, speed=speed, phase=phase)
      estimate = declared_estimate(windows, phase)
      for side in ('left', 'right'):
        for candidate in angle_coordinate_candidates(estimate, windows, previous, side=side, phase=phase):
          name = candidate['parameter'].removeprefix('stable:')
          found.setdefault(name, candidate)
          assert candidate['evidence_domain'] == 'steering_angle'
          assert candidate['coordinate_policy'] == 'angle_tracking_supported26_v1'
          assert 0 < abs(candidate['delta']) <= .01
          assert {n for n in NAMES if candidate['values'][n] != previous[n]} == {name}
          assert candidate['original_windows'] == sorted(w.identity for w in windows)
  assert set(found) == set(SUPPORTED)
  assert not set(found) & UNSUPPORTED
  assert not any('friction_threshold' in name for name in found)


def test_capped_primary_does_not_hide_supported_alternatives():
  from openpilot.starpilot.flm.offroad_surface import route_bounds

  previous = dict.fromkeys(NAMES, 0.)
  previous['turn_in_boost_right'] = route_bounds('turn_in_boost_right')[1]
  windows = angle_windows(3, speed=3.)
  candidates = angle_coordinate_candidates(declared_estimate(windows, 'turn_in'), windows, previous, side='left', phase='turn_in')
  names = {c['parameter'].removeprefix('stable:') for c in candidates}
  assert 'turn_in_boost_right' not in names
  assert 'crawl_turn_in_ff_boost_right' in names
  assert 'low_speed_angle_assist_max_torque' in names
  assert 'curvy_turn_in_trim_right' not in names, 'An already zero trim cannot be reduced'
  assert not names & UNSUPPORTED


def test_real_angle_estimation_offers_low_speed_hooks_before_automatic_qualification():
  previous = dict.fromkeys(NAMES, 0.)
  windows = angle_windows(3, speed=3., gain=.85)
  report = analyze(windows, previous)
  cohort, = report['cohorts']
  assert cohort['reason'] == 'recorded_provisional_suggestion'
  assert not cohort['automatic_application_allowed']
  assert 'training' not in cohort and 'heldout' not in cohort
  names = {c['parameter'] for c in cohort['provisional_coordinate_candidates']}
  assert {'stable:turn_in_boost_right', 'stable:crawl_turn_in_ff_boost_right', 'stable:low_speed_angle_assist_max_torque'} <= names
  assessment = SimpleNamespace(groups=(), diagnostic_groups=({'cohorts': [cohort]},))
  rows = recorded_recommendations(assessment, values(), {})
  assert {r['parameter'] for r in rows} >= {'turn_in_boost_right', 'crawl_turn_in_ff_boost_right', 'low_speed_angle_assist_max_torque'}
  assert all(r['whole_windows'] == 3 for r in rows)
  assert not report['automatically_saved']


def test_six_original_angle_windows_require_disjoint_role_agreement():
  previous = dict.fromkeys(NAMES, 0.)
  windows = angle_windows(6, speed=3.)
  report = analyze(windows, previous)
  cohort, = report['cohorts']
  assert len(cohort['training']['windows']) == len(cohort['heldout']['windows']) == 3
  assert not set(cohort['training']['windows']) & set(cohort['heldout']['windows'])
  candidates = cohort['coordinate_candidates']
  assert candidates and all(c['coordinate_policy'] == 'angle_tracking_supported26_v1' for c in candidates)
  assert all(len(c['original_windows']) == 3 for c in candidates)
  assert not report['automatically_saved']
  assert all('values' not in c for c in candidates), 'Reports retain compact exact coordinate recipes'


def test_conflicting_gain_roles_are_not_published_as_suggestions():
  from unittest.mock import patch
  from openpilot.starpilot.flm.offroad_history import partition_windows

  windows = angle_windows(6, phase='steady')
  training, heldout = partition_windows(windows)
  first = declared_estimate(training, 'steady')
  second = replace(declared_estimate(heldout, 'steady'), gain=1.15, gain_interval=(1.14, 1.16))
  with patch('openpilot.starpilot.flm.offroad_analysis.estimate', side_effect=[first, second]):
    cohort, = analyze(windows, dict.fromkeys(NAMES, 0.))['cohorts']
  assert not cohort['coordinate_candidates'] and not cohort['provisional_coordinate_candidates']
  assert cohort['proposal'] is None


def test_acceleration_domains_and_original_window_identity_remain_separate():
  windows = angle_windows(3)
  estimate = declared_estimate(windows, 'turn_in')
  assert not angle_coordinate_candidates(replace(estimate, domain='lateral_acceleration'), windows, dict.fromkeys(NAMES, 0.),
                                        side='left', phase='turn_in')
  with pytest.raises(ValueError, match='original PID estimate windows'):
    angle_coordinate_candidates(estimate, windows[::-1], dict.fromkeys(NAMES, 0.), side='left', phase='turn_in')
  torque = tuple(next(iter(fixtures.TestCrossDriveEvidence.inputs(n)[0].windows.values()))[0] for n in range(1, 4))
  cohort, = analyze(torque, dict.fromkeys(NAMES, 0.))['cohorts']
  assert cohort['provisional_proposal']['parameter'] == 'ff_gain_left'
  assert 'provisional_coordinate_candidates' not in cohort


@pytest.mark.parametrize('qualified', [True, False])
def test_additional_pid_coordinate_reaches_original_checks_and_only_then_atomic_save(tmp_path, qualified):
  from unittest.mock import patch
  from openpilot.common.params import Params
  from openpilot.starpilot.flm import offroad_worker as worker
  from openpilot.starpilot.flm.offroad_processor import CompletedAssessment
  from openpilot.starpilot.flm.offroad_route import RouteReceipt
  from openpilot.starpilot.flm.offroad_store import KEY, startup_row
  from openpilot.starpilot.flm.offroad_surface import REVISION
  from openpilot.starpilot.flm.tests.test_offroad_backend import scope, trial

  # Declared native protocol responses isolate dispatch/storage; actual PID
  # constructors, neutral state and effect stages are checked in the PID suite.
  current_scope = replace(scope(), controller_mode='native', controller_domain='torque_pid')
  before = trial()
  candidate = {'parameter': 'stable:center_deadband_low_deg', 'delta': .005,
               'coordinate_policy': 'angle_tracking_supported26_v1', 'policy': 'observed_angle_tracking_v1',
               'original_windows': before['before']['training']['windows']}
  cohort = dict(before['before'], cohort='0:both:center', whole_windows=6, proposal=None,
                coordinate_candidates=[candidate], provisional_coordinate_candidates=[candidate])
  metadata = {'route_values': tuple(values().items()), 'route_revision': REVISION, 'controller_mode': 'native',
              'route_scope': current_scope.key(), 'domain': 'steering_angle'}
  group = {'original_metadata': metadata, 'original_context_sha256': 'f' * 64, 'cohorts': [cohort],
           'selected_original_windows': before['gross_baseline']}
  receipt = RouteReceipt('declared-pid-drive', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
  inventory = {'routeId': receipt.route_id}
  assessment = CompletedAssessment(receipt, (group,), {}, SimpleNamespace(carFingerprint='declared-torque-PID'), (), inventory)
  calls = []

  def originals(actual, original, ids, proposed, root, *, permitted):
    calls.append(tuple(ids))
    assert permitted() and proposed['center_deadband_low_deg'] > 0
    assert all(proposed[name] == 0 for name in UNSUPPORTED)
    return [{'window': wid, 'qualified': qualified, 'numerical': {'qualified': qualified, 'changed_frames': 1},
             'registered_encoder': {'qualified': qualified, 'candidate_changed_emissions': 1}} for wid in ids]

  def assessor(params, root, previous, *, permitted):
    assert previous(assessment.cp) == values()
    return assessment

  params = Params(str(tmp_path / 'params'))
  with patch.object(worker, 'latest_route', return_value=inventory), patch('openpilot.starpilot.flm.offroad_checks._original_results', originals):
    result = worker.OffroadWorker(params, tmp_path, tmp_path / 'cache', permitted=lambda: True,
      assessor=assessor, scope_for=lambda cp, params: current_scope).run_once()
  assert calls, 'The new coordinate cannot bypass controller/native qualification'
  assert result['configuration_changed'] is qualified
  fresh, _ = startup_row(params, current_scope)
  if qualified:
    assert fresh is not None and fresh['values']['center_deadband_low_deg'] == .005
    assert fresh['state'] == 'ready_for_next_drive'
    assert len(calls[-1]) == 6
  else:
    assert fresh is None and params.get(KEY) is None
  assert not result['actual_application_proven']


@pytest.mark.parametrize('independent_lag, supported', [(.2, True), (0., False)])
def test_independent_original_pid_route_must_support_the_same_additional_coordinate(independent_lag, supported):
  from unittest.mock import patch
  from openpilot.starpilot.flm.offroad_checks import fit_independent_route
  from openpilot.starpilot.flm.offroad_prior_inputs import PriorOriginals
  from openpilot.starpilot.flm.offroad_route import RouteReceipt, SelectedSourceReceipt
  from openpilot.starpilot.flm.tests.test_offroad_backend import trial

  windows = []
  for segment, window in enumerate(angle_windows(6, speed=3., lag=independent_lag)):
    offset = segment * 4_000_000_000
    points = tuple(replace(p, route='declared-prior-drive', segment=segment, mono_ns=p.mono_ns + offset,
                           control_header_ns=p.control_header_ns + offset, output_header_ns=p.output_header_ns + offset) for p in window.points)
    windows.append(RouteWindow(window_identity(points), points))
  previous = values()
  proposed = dict(previous, crawl_turn_in_ff_boost_right=.005)
  proposal = {'parameter': 'stable:crawl_turn_in_ff_boost_right', 'delta': .005, 'values': proposed,
              'coordinate_policy': 'angle_tracking_supported26_v1', 'policy': 'observed_angle_tracking_v1'}
  cp = SimpleNamespace()
  assessment = SimpleNamespace(receipt=RouteReceipt('declared-current-drive', 'd' * 64, (('current', 'c' * 64, 1),), 'a' * 64, 1, 2, 100, ()))
  metadata = {'domain': 'steering_angle', 'vehicle_model_anchor': (), 'route_values': tuple(previous.items())}
  before = trial()
  group = {'original_metadata': metadata, 'selected_original_windows': before['gross_baseline']}
  cohort = dict(before['before'], cohort='0:left:turn_in')
  prior = PriorOriginals(cp, SelectedSourceReceipt('declared-prior-drive', 'e' * 64, (('prior', 'f' * 64, 1),),
    'a' * 64, 1, 2, 50, (), (0,)), {}, tuple({'identity': w.identity, 'original_metadata': metadata} for w in windows), tuple(windows))
  calls = []

  def originals(actual, original, ids, values, root, *, permitted):
    calls.append(actual.receipt.route_id)
    return [{'window': wid, 'qualified': True, 'numerical': {'qualified': True, 'changed_frames': 1},
             'registered_encoder': {'qualified': True, 'candidate_changed_emissions': 1}} for wid in ids]

  with patch('openpilot.starpilot.flm.offroad_checks._original_results', originals), \
       patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='a' * 64):
    _, proof, checked = fit_independent_route(assessment, group, cohort, prior, proposal, previous, '.', permitted=lambda: True)
  assert proof['independent_tracking']['qualified'] is supported
  assert checked['qualified'] is supported
  assert calls == ['declared-current-drive', 'declared-prior-drive'] if supported else calls == ['declared-current-drive']
  assert proof['validation_evaluations'] == 1


@pytest.mark.parametrize('curvature, expected', [(.0001, 'ff_gain_left'), (-.0001, 'ff_gain_right')])
def test_pid_angle_sign_maps_to_the_actual_registered_controller_hook(curvature, expected):
  from opendbc.car import structs
  from openpilot.starpilot.flm.offroad_tracking import suggest
  from openpilot.starpilot.flm.tests import test_pid_replay as pid
  from openpilot.starpilot.flm.tests.test_pid_stage_core import PidTrialSource
  from openpilot.starpilot.flm.pid_stages import PidObservation

  factory = pid.factory()
  baseline, vm = factory.create()
  selected, selected_vm = factory.create()
  cs = structs.CarState.new_message(vEgo=20., steeringAngleDeg=0., gearShifter='drive')
  params = SimpleNamespace(angleOffsetDeg=0., roll=0.)
  _, angle, _ = baseline.update(True, cs, vm, params, False, curvature, False, .2)
  observed_side = 'left' if angle > 0 else 'right'
  windows = angle_windows(3, phase='steady')
  proposal = suggest(declared_estimate(windows, 'steady'), dict.fromkeys(NAMES, 0.), side=observed_side, phase='steady')
  assert proposal['parameter'] == expected
  source = PidTrialSource({expected: proposal['delta']})
  selected.automatic_flm_effects = source
  selected.automatic_flm_observation = PidObservation()
  wrong, wrong_vm = factory.create()
  wrong_source = PidTrialSource({'ff_gain_right' if expected.endswith('left') else 'ff_gain_left': proposal['delta']})
  wrong.automatic_flm_effects = wrong_source
  wrong.automatic_flm_observation = PidObservation()
  baseline, vm = factory.create()
  changed = False
  for _ in range(80):
    source.begin_frame()
    wrong_source.begin_frame()
    selected.automatic_flm_observation.begin_frame()
    wrong.automatic_flm_observation.begin_frame()
    original, _, _ = baseline.update(True, cs, vm, params, False, curvature, False, .2)
    actual, _, _ = selected.update(True, cs, selected_vm, params, False, curvature, False, .2)
    wrong_output, _, _ = wrong.update(True, cs, wrong_vm, params, False, curvature, False, .2)
    assert wrong_output == original, 'The old angle-side mapping targeted an inactive hook'
    changed |= actual != original
    assert abs(actual) <= selected.steer_max
  assert changed, 'An angle-side label must select the hook that actually executes'
