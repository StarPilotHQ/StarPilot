"""Broader original observations remain separate from automatic qualification."""
from dataclasses import replace
from math import pi, sin
from types import SimpleNamespace

import pytest

from openpilot.starpilot.flm.offroad_evidence import RouteWindow, window_identity
from openpilot.starpilot.flm.offroad_processor import CompletedAssessment, _assess_inputs, _recorded_tracking
from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector
from openpilot.starpilot.flm.offroad_stable_policy import FLMSample
from openpilot.starpilot.flm.tests.test_offroad_backend import scope, values
from openpilot.starpilot.flm.tests import test_offroad_coordinate_policy as fixtures


@pytest.mark.parametrize('case', ('followed', 'steady_curve', 'lane_change', 'uncommanded', 'gap'))
def test_report_displays_repeated_steering_request_only_with_matching_original_response(case):
  # Keep a genuine 0.4 s delay between the request and measured response.
  def requested(t):
    if case in ('steady_curve', 'uncommanded'):
      return .1
    if case == 'lane_change':
      return .08 * sin(pi * t / 12)
    return .075 * sin(2 * pi * t / 4.5)

  collected, _ = fixtures.TestCrossDriveEvidence.inputs(1)
  base = next(iter(collected.windows.values()))[0].points[0]
  windows = []
  for w in range(6):
    if case == 'gap' and w == 3:
      continue
    points = []
    for i in range(200):
      t = (200 * w + i) / 100
      actual = .08 * sin(2 * pi * t / 4.5) if case == 'uncommanded' else requested(t - .4)
      points.append(replace(base, speed_mps=26., mono_ns=base.mono_ns + (200 * w + i) * 10_000_000,
                            desired=requested(t), actual=actual))
    points = tuple(points)
    windows.append(RouteWindow(window_identity(points), points))

  inputs = {base.context_key: windows}
  result = _recorded_tracking(inputs, inputs)
  assert result is not None
  assert result['windows'] == len(windows)
  assert result['points'][0] == [base.mono_ns, windows[0].points[0].desired, windows[0].points[0].actual]
  if case == 'followed':
    assert result['observation'] == 'followed_repeated_steering_request'
    assert result['source_end_ns'] == windows[-1].points[-1].mono_ns
  else:
    assert 'observation' not in result
    assert result['source_end_ns'] == windows[0].points[-1].mono_ns


def mixed_drive(number):
  collected, inventory = fixtures.TestCrossDriveEvidence.inputs(number)
  for key, windows in collected.windows.items():
    changed = []
    for original in windows:
      points = tuple(replace(p, desired=.6 if i < 150 else -.6, actual=.5 if i < 150 else -.5)
                     for i, p in enumerate(original.points))
      changed.append(RouteWindow(window_identity(points), points))
    collected.windows[key] = changed
  return collected, inventory


def test_speed_or_maneuver_crossing_is_visible_to_advisory_reader_without_a_fitting_cohort():
  collected, inventory = mixed_drive(1)
  assessment = _assess_inputs(inventory, collected, values(), permitted=lambda: True)
  assert len(assessment.groups) == 1
  group = assessment.groups[0]
  assert not group['cohorts']
  assert len(group['observed_original_windows']) == 2
  assert {w['cohort'] for w in group['observed_original_windows']} == {'mixed:observed'}
  assert not group['selected_original_windows']
  assert not assessment.report()['automatically_saved']


def test_mixed_observations_survive_independent_drive_history_and_reanalysis(tmp_path):
  from openpilot.starpilot.flm.offroad_history import EvidenceBank, combine

  bank = EvidenceBank(tmp_path)
  for number in range(1, 5):
    collected, inventory = mixed_drive(number)
    history = bank.retain(collected, inventory, 'same-settings', permitted=lambda: True)
  assert len(history) == 4
  assert all(row['condition'] == 'mixed:observed' for drive in history for row in drive['windows'])
  pooled, sources, order = combine(collected, inventory, history)
  assessment = _assess_inputs(inventory, pooled, values(), permitted=lambda: True, sources=sources, route_order=order)
  observed = [w for group in assessment.groups for w in group['observed_original_windows']]
  assert len(observed) == len({w['identity'] for w in observed}) == 8
  assert len({w['route'] for w in observed}) == 4
  assert not any(group['cohorts'] for group in assessment.groups)
  assert len(bank.retain(collected, inventory, 'same-settings', permitted=lambda: True)) == 4


def selector(count=17):
  from openpilot.starpilot.flm.offroad_route import RouteReceipt

  metadata = {'domain': 'lateral_acceleration', 'route_values': tuple(values().items()), 'route_revision': 'declared',
              'controller_mode': 'starpilot', 'route_scope': scope().key()}
  windows = [{'identity': f'{i + 1:064x}', 'route': f'declared-drive-{i:02}', 'segment': 0,
              'start_ns': 10**9, 'end_ns': 3 * 10**9, 'cohort': 'mixed:observed'} for i in range(count)]
  group = {'original_metadata': metadata, 'cohorts': [], 'selected_original_windows': [], 'observed_original_windows': windows}
  cp = SimpleNamespace(brand='gm', lateralTuning=SimpleNamespace(which=lambda: 'torque'))
  receipt = RouteReceipt('declared-current-drive', 'd' * 64, (), 'a' * 64, 1, 2, 3, ())
  assessment = CompletedAssessment(receipt, (group,), {}, cp, (), {})
  reader = StableRouteSelector(assessment, '.', values(), scope(), permitted=lambda: True)
  reader.rows = {}
  # One slow unwind in the first batch and two in the final short batch:
  # support must accumulate across all original windows, not within batches.
  for i, window in enumerate(windows):
    samples = []
    for tick in range(201):
      desired = -1 + .35 * tick / 100
      actual = desired - (.25 if i in (0, 15, 16) else 0)
      samples.append(FLMSample(str(window['route']), 0, 1 + tick / 100, 20., True, False, False,
                    actual, desired, .35, actual - desired, 0, 0, 0, 0, 0, -.2, -5., 0, -.2, -.2, 0))
    reader.rows[str(window['identity'])] = samples
  return reader, tuple(w['identity'] for w in windows)


def test_original_symptoms_pool_across_batches_but_not_automatic_roles():
  from openpilot.starpilot.flm.offroad_worker import recorded_recommendations

  reader, ids = selector()
  proof = reader.observe(ids)
  assert not proof['automatic_application_allowed']
  taper = next(c for c in proof['coordinate_candidates'] if c['parameter'] == 'stable:unwind_taper_right')
  assert taper['original_windows'] == [ids[i] for i in (0, 15, 16)]
  rows = recorded_recommendations(reader.assessment, values(), {'observed_profile_reports': [proof]})
  assert rows and all(row['whole_windows'] == 3 for row in rows)
  assert all(row['parameter'].endswith('_right') for row in rows)
  with pytest.raises(ValueError, match='Bounded unique'):
    reader.profile(ids, 'mixed:observed')  # Native role sizes remain unchanged.


def test_unusable_original_span_does_not_suppress_other_independent_observations():
  reader, ids = selector()
  reader.rows[ids[5]] = None
  proof = reader.observe(ids)
  assert proof['unavailable_original_windows'] == [ids[5]]
  assert proof['coordinate_candidates']
  with pytest.raises(ValueError, match='unique'):
    reader.observe(ids + ids[:1])


def test_partial_metadata_preserves_only_observed_consistent_coordinate_baselines():
  from openpilot.starpilot.flm.offroad_observed import observed_sources, merge_sources
  from openpilot.starpilot.flm.offroad_control_metadata import read_control_metadata
  from openpilot.starpilot.flm.tests.test_configured_route_recording import control, wire

  inputs, _ = fixtures.TestCrossDriveEvidence.inputs(1)
  context, windows = next(iter(inputs.windows.items()))
  metadata = inputs.context_metadata[context]
  template = read_control_metadata(wire(control(legacy=True)))
  timeline = []
  for i, point in enumerate(windows[0].points):
    stamp = point.mono_ns
    actual = dict(values(), friction_threshold_mid=.001 if i % 2 else 0)
    row = replace(template, control_ns=stamp, state_ns=stamp, source_receipt_ns=stamp,
                  native_observed_ns=stamp, native_expires_ns=stamp + 100_000_000,
                  route_applied_coordinates=tuple(actual.items()), limited=bool(i % 2))
    timeline.append((stamp, None if i in (80, 160) else row))

  def original(row, _):
    return dict(metadata, route_values=row.route_applied_coordinates, baseline_revision=row.baseline_revision)

  source, = observed_sources(windows[:1], timeline, original)
  assert source['missing_metadata_rows'] == 2
  assert 'friction_threshold_mid' not in source['coordinate_baselines']
  assert source['coordinate_baselines']['curvy_unwind_floor_relief_left'] == 0
  assert source['windows'][0]['identity'] == windows[0].identity
  assert len(source['windows']) == 1
  changed = list(timeline)
  stamp, row = changed[150]
  assert row is not None
  changed[150] = (stamp, replace(row, baseline_revision='c' * 64))
  assert not observed_sources(windows[:1], changed, original)
  assert not observed_sources(windows[:1], [(t, None if row is None else replace(row, native_allowed=False)) for t, row in timeline], original)
  assert not observed_sources(windows[:1], timeline[30:], original), 'Missing source bookends remain unavailable'
  with pytest.raises(ValueError, match='counted twice'):
    merge_sources([source, source])


def test_notchy_curve_advice_reaches_only_speed_points_with_three_original_windows():
  import math

  reader, ids = selector(3)
  for key in ids:
    original = reader.rows[key]
    assert original is not None
    # Six source seconds retain the classifier's original edge exclusions
    # and long notchy-curve window; no sample-count threshold is shortened.
    reader.spans[key]['end_ns'] = 7 * 10**9
    reader.rows[key] = [replace(original[0], t=1 + tick / 100, desired_la=.8, desired_jerk=0.,
                        actual_la=.8 + .16 * math.sin(4 * math.pi * (1 + tick / 100)),
                        error=.16 * math.sin(4 * math.pi * (1 + tick / 100))) for tick in range(601)]
  proof = reader.observe(ids)
  curve = [candidate for candidate in proof['coordinate_candidates'] if 'friction_threshold_' in candidate['parameter']]
  assert {candidate['parameter'] for candidate in curve} == {'stable:friction_threshold_fast', 'stable:friction_threshold_highway'}
  assert all(len(candidate['original_windows']) == 3 and 0 < candidate['delta'] <= .01 for candidate in curve)
  assert not proof['automatic_application_allowed']
  assert not [candidate for candidate in reader.observe(ids[:2])['coordinate_candidates'] if 'friction_threshold_' in candidate['parameter']]


def test_many_events_in_one_window_and_conflicting_curve_steps_do_not_inflate_support():
  from openpilot.starpilot.flm.offroad_observed import curve_candidates

  previous = values()
  proposed = {'values': dict(previous, friction_threshold_highway=.005)}
  observation = {'bucket': 'notchy_mid_curve', 'direction': 'left', 'speed_mps': 30., 'severity': 1.}
  assert not curve_candidates([(proposed, [('one-window', observation)] * 30)], previous)
  positive = [(str(i), observation) for i in range(3)]
  rows = curve_candidates([(proposed, positive)], previous)
  assert len(rows) == 1 and len(rows[0]['original_windows']) == 3
  reduction = {'values': dict(previous, friction_threshold_highway=-.005)}
  negative = [(str(i + 3), dict(observation, bucket='low_speed_unwillingness')) for i in range(3)]
  assert not curve_candidates([(proposed, positive), (reduction, negative)], previous)


def recorded_highway_samples():
  import gzip
  import json
  from pathlib import Path

  fixture = json.loads(gzip.decompress((Path(__file__).parent / 'fixtures/recorded_highway_oscillation.json.gz').read_bytes()))
  assert fixture['original_rlog_sha256'] == '5178ca6d6f2ae882853651165cbc74e72ae22dfaaff773b90fcca08e6e1a0038'
  return [FLMSample(route='recorded-highway', **dict(zip(fixture['columns'], row, strict=True))) for row in fixture['rows']]


def oscillation_events(samples):
  from openpilot.starpilot.flm.offroad_stable_policy import cadenced_runs, classify_current_stable

  return [event for run in cadenced_runs(samples) for summary in classify_current_stable(list(run))[0]
          if summary['bucket'] == 'center_chatter' for event in summary['events']
          if event.get('metrics', {}).get('detector') == 'sustained_tracking_oscillation']


def test_recorded_highway_wobble_is_found_at_the_bookmark_without_new_application_authority():
  samples = recorded_highway_samples()
  events = oscillation_events(samples)
  assert events
  # Event indices belong to each cadenced run; the plot keeps its original timestamps.
  from openpilot.starpilot.flm.offroad_stable_policy import cadenced_runs, classify_current_stable
  hits = [run[event['startIdx']].t <= 66.935690417 <= run[event['endIdx']].t
          for run in cadenced_runs(samples) for summary in classify_current_stable(list(run))[0]
          if summary['bucket'] == 'center_chatter' for event in summary['events']
          if event.get('metrics', {}).get('detector') == 'sustained_tracking_oscillation']
  assert any(hits), 'The marked episode itself must be detected, not only another section'
  assert all(min(e['metrics'][key] for key in ('trackingErrorExcursions', 'steeringExcursions', 'outputExcursions')) >= 6 for e in events)
  # Detection alone still cannot create the three independent source-window votes.
  from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates, recorded_observation
  observation = recorded_observation('center_chatter', 'center', events[0]['peakScore'], 35.)
  assert not coordinate_candidates([('same-source-window', observation)] * 20, values())
  assert coordinate_candidates([(str(i), observation) for i in range(3)], values())
  assert not oscillation_events([replace(s, actual_la=s.desired_la, error=0.) for s in samples])


def test_recorded_turn_forecasts_use_the_existing_turn_repair_with_fresh_sources():
  import json
  from pathlib import Path
  from openpilot.cereal import log
  from openpilot.starpilot.lateral.lane_change_smoothing import LaneChangeSmoother
  from openpilot.starpilot.lateral.signal_turn import SignalTurnEvidence
  from openpilot.starpilot.lateral.tests.test_signal_turn import BASE, OFFSET, frame

  fixture = json.loads((Path(__file__).parent / 'fixtures/recorded_turn_forecasts.json').read_text())
  assert fixture['source_sha256'] == '8a6d6067a6e0d4596fa8fcbbf4cf564d811d04b3844f6b5500f1ee0cacae2d1e'
  owner, smoother = SignalTurnEvidence(), LaneChangeSmoother()
  confirmed_before_lane_change = False
  for row in fixture['rows']:
    # Original numerical inputs; explicitly declared fresh receiver clocks, not a historical authority reconstruction.
    sm, _, model = frame(0, 1)
    now = BASE + row['dt_ns']
    sm.logMonoTime, sm.recv_time = dict.fromkeys(sm, now), dict.fromkeys(sm, now / 1e9)
    for name, value in row['car'].items():
      setattr(sm['carState'], name, value)
    sm['carControl'].latActive, sm['deviceState'].started = row['lat_active'], row['started']
    sm['longitudinalPlan'].modelMonoTime, sm['longitudinalPlan'].shouldStop = now, row['plan_stop']
    model.timestampEof, model.action.shouldStop = now + OFFSET, row['model_stop']
    model.meta.desirePrediction, model.meta.laneChangeState = row['prediction'], row['lane_change_state']
    ready = owner.observe(sm, model, model_ns=now, now_ns=now, now_boot_ns=now + OFFSET, valid=row['model_valid'], enabled=True)
    state = model.meta.laneChangeState
    if ready and state in (log.LaneChangeState.off, log.LaneChangeState.preLaneChange):
      confirmed_before_lane_change = True
    if state == log.LaneChangeState.laneChangeStarting:
      assert confirmed_before_lane_change and ready
      factor = smoother.factor(active=True, lane_change_state=state, speed=row['car']['vEgo'], minimum_speed=0., duration=5.777777777777778,
                               previous=.0015, desired=.015, turn_assist=True, intersection_turn=ready)
      assert factor == 1., 'A confirmed intersection must not receive the extra lane-change slowing'
  assert confirmed_before_lane_change


@pytest.mark.parametrize('kind', ['follows_request', 'small_error', 'one_lane_change', 'steady_curve', 'wheel_only',
                                 'output_only', 'driver', 'inactive', 'saturated', 'slow', 'gap', 'segment_boundary'])
def test_commanded_maneuvers_and_unusable_sources_are_not_sustained_tracking_wobble(kind):
  import math

  template = recorded_highway_samples()[0]
  samples = []
  for i in range(80):
    t = i / 10
    wave = math.sin(2 * math.pi * t)
    desired = .3 * wave
    actual = desired + .2 * math.sin(2 * math.pi * t + .8)
    angle, output = 1.5 * wave, .15 * wave
    if kind == 'follows_request':
      actual = desired
    if kind == 'small_error':
      actual = desired + .02 * wave
    if kind == 'one_lane_change':
      desired = .3 * math.sin(math.pi * t / 4)
      actual = desired + .15 * math.cos(math.pi * t / 4)
      angle, output = 1.5 * math.sin(math.pi * t / 4), .15 * math.sin(math.pi * t / 4)
    if kind == 'steady_curve':
      desired, actual, angle, output = .25, .27, 2., .1
    if kind == 'wheel_only':
      output = 0.
    if kind == 'output_only':
      angle = 0.
    samples.append(replace(template, t=t, desired_la=desired, actual_la=actual, error=actual-desired, desired_jerk=0.,
                           steering_angle_deg=angle, output=output, v_ego=20. if kind == 'slow' else 35.,
                           steering_pressed=kind == 'driver' and i == 35, lat_active=not (kind == 'inactive' and i == 35),
                           saturated=kind == 'saturated' and i == 35,
                           segment=74 if kind == 'segment_boundary' and i >= 40 else 73))
  if kind == 'gap':
    samples = [s for i, s in enumerate(samples) if i < 35 or i > 40]
  assert not oscillation_events(samples)


@pytest.mark.parametrize('phase', [0., 1.5707963267948966])
def test_sustained_wobble_does_not_depend_on_window_phase_or_steering_offset(phase):
  import math

  template = recorded_highway_samples()[0]
  samples = []
  for i in range(60):
    t = i / 10
    wave = math.sin(2 * math.pi * t + phase)
    desired = .3 * wave
    error = .2 * math.sin(2 * math.pi * t + phase + .8)
    samples.append(replace(template, t=t, desired_la=desired, actual_la=desired + error, error=error, desired_jerk=0.,
                           steering_angle_deg=3. + 1.5 * wave, output=.2 + .15 * wave, v_ego=35.,
                           steering_pressed=False, lat_active=True, saturated=False))
  assert oscillation_events(samples)
