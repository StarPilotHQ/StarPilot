"""Broader original observations remain separate from automatic qualification."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from openpilot.starpilot.flm.offroad_evidence import RouteWindow, window_identity
from openpilot.starpilot.flm.offroad_processor import CompletedAssessment, _assess_inputs
from openpilot.starpilot.flm.offroad_stable_bridge import StableRouteSelector
from openpilot.starpilot.flm.offroad_stable_policy import FLMSample
from openpilot.starpilot.flm.tests.test_offroad_backend import scope, values
from openpilot.starpilot.flm.tests.test_offroad_coordinate_policy import TestCrossDriveEvidence as CrossDriveFixture


def mixed_drive(number):
  collected, inventory = CrossDriveFixture.inputs(number)
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

  inputs, _ = CrossDriveFixture.inputs(1)
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
