"""Pending suggestions retain their original sources, never automatic qualification."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from openpilot.starpilot.flm.offroad_daemon import StatusReport
from openpilot.starpilot.flm.offroad_worker import recent_findings
from openpilot.starpilot.flm.tests.test_offroad_coordinate_policy import TestLatestUsefulDriveReport as ReviewFixture


@pytest.fixture
def review():
  fixture = ReviewFixture()
  fixture.setUp()
  yield fixture
  fixture.doCleanups()


def partial(review, result):
  document = review.cache.get(result['assessment_sha256'])
  route = document['assessment']['route']
  route.update(last_intact_wall_ns=route['completed_wall_ns'], completed_wall_ns=0)
  return dict(result, partial_drive=True, assessment_sha256=review.cache.put(dict(document, kind='partial_assessment')))


def test_pending_partial_findings_survive_backlog_and_cache_retirement(review):
  first = partial(review, review.driving)
  pending = recent_findings(review.cache, [first], first)
  expected = deepcopy(pending['recommendations'])
  for number in range(4, 17):
    latest = partial(review, review.result(number))
    latest.update(analysis_counts={'diagnostic_whole_windows': 1})
    pending = recent_findings(review.cache, [latest, pending], latest)
    review.cache.checkpoint({'input_fingerprint': f'{number:064x}', 'result': pending})
    review.cache.retire([], authorized=lambda: True)
    assert pending['recommendations'] == expected
    assert pending['saved_adjustments'] == 0 and pending['configuration_changed'] is False
    assert pending['analysis_counts'] == latest['analysis_counts']
  assert all(link[1] == first['assessment_sha256'] for link in pending['recommendation_assessments'])
  assert review.cache.get(first['assessment_sha256'])['kind'] == 'partial_assessment'


def test_review_only_rebases_unchanged_knobs_after_acceptance(review):
  from openpilot.starpilot.flm.tests.test_offroad_backend import scope, values

  first = review.driving
  latest = review.result(4, profile=replace(scope(), persistent_source_epoch='d' * 64))
  for result in (first, latest):
    document = review.cache.get(result['assessment_sha256'])
    document['assessment']['review_settings_sha256'] = 'e' * 64
    result['assessment_sha256'] = review.cache.put(document)
  latest['review_values'] = values()
  latest['review_values']['unwind_taper_right'] = .005
  pending = recent_findings(review.cache, [latest, first], latest)
  assert len(pending['recommendations']) == 4
  assert all(r['parameter'] != 'unwind_taper_right' for r in pending['recommendations'])
  assert 'configured_values' not in pending and pending['saved_adjustments'] == 0
  document = review.cache.get(latest['assessment_sha256'])
  document['assessment']['review_settings_sha256'] = 'f' * 64
  latest['assessment_sha256'] = review.cache.put(document)
  assert recent_findings(review.cache, [latest, first], latest)['recommendations'] == []


@pytest.mark.parametrize('field,value', (('baseline_revision', 'd' * 64),
  ('controller_revision', 'different-controller'), ('controller_mode', 'native'), ('reset_generation', '1')))
def test_matching_review_settings_never_bridge_a_different_vehicle_or_controller(review, field, value):
  from openpilot.starpilot.flm.tests.test_offroad_backend import scope

  first = review.driving
  latest = review.result(4, profile=replace(scope(), **{field: value}))
  for result in (first, latest):
    document = review.cache.get(result['assessment_sha256'])
    document['assessment']['review_settings_sha256'] = 'e' * 64
    result['assessment_sha256'] = review.cache.put(document)
  assert recent_findings(review.cache, [latest, first], latest)['recommendations'] == []


def test_same_fingerprint_keeps_pending_reviews_after_car_params_change(review):
  from openpilot.starpilot.flm.tests.test_offroad_backend import scope

  first = review.driving
  latest = review.result(4, profile=replace(scope(), cp_sha256='d' * 64, baseline_revision='e' * 64))
  for result in (first, latest):
    document = review.cache.get(result['assessment_sha256'])
    result['vehicle'] = document['assessment']['vehicle'] = 'HYUNDAI_SONATA_HYBRID'
    document['assessment']['review_settings_sha256'] = 'f' * 64
    result['assessment_sha256'] = review.cache.put(document)
  pending = recent_findings(review.cache, [latest, first], latest)
  assert pending['recommendations'] == first['recommendations']
  assert all(link[1] == first['assessment_sha256'] for link in pending['recommendation_assessments'])
  assert pending['saved_adjustments'] == 0
  document = review.cache.get(first['assessment_sha256'])
  first['vehicle'] = document['assessment']['vehicle'] = 'another-fingerprint'
  first['assessment_sha256'] = review.cache.put(document)
  assert recent_findings(review.cache, [latest, first], latest)['recommendations'] == []


@pytest.mark.parametrize('fault', ('document', 'source', 'route', 'duplicate'))
def test_carried_findings_require_intact_original_provenance(review, fault):
  pending = recent_findings(review.cache, [review.latest, review.driving], review.latest)
  name = pending['recommendations'][0]['parameter']
  link = pending['recommendation_assessments'][0]
  if fault == 'document':
    link[1] = 'f' * 64
  elif fault == 'source':
    link[2] = 'f' * 64
  elif fault == 'route':
    pending['recommendation_sources'][0][1] = 'different-route'
  else:
    pending['recommendation_assessments'].append(list(link))
  latest = review.result(4)
  result = recent_findings(review.cache, [latest, pending], latest)
  assert name not in {r['parameter'] for r in result['recommendations']}
  assert len(result['recommendations']) == 4


def test_new_analysis_error_keeps_pending_review_without_claiming_a_saved_tune(review):
  pending = recent_findings(review.cache, [review.latest, review.driving], review.latest)
  status = StatusReport(path=Path(review.directory) / 'status.json', clock_ns=lambda: 1_000_000_000)
  status.set(pending)
  for _ in range(3):
    status.set({'status': 'unavailable', 'reason': 'Manual coordinate settings require review: context_changed'})
    assert status.current['recommendations'] == pending['recommendations']
    assert status.current['recommendation_assessments'] == pending['recommendation_assessments']
    assert status.current['status'] == 'unavailable'
    assert status.current['retained_analysis'] is False
    assert 'configured_values' not in status.current and 'actual_application_proven' not in status.current
  status.publish()
  assert status.previous == pending


def test_partial_worker_persists_combined_review_without_auto_qualification(tmp_path):
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  from openpilot.starpilot.flm.tests.test_offroad_backend import scope
  from openpilot.starpilot.flm.tests.test_offroad_fault_isolation import TestPartialDriveReview, TestRouteFaultIsolation

  root = tmp_path / 'logs'
  maker = TestPartialDriveReview()
  name, _, _ = maker.segment(root, 18, 0)
  maker.segment(root, 18, 1, ending='endOfRoute', fault='truncated')
  params, worker = TestRouteFaultIsolation().setup_worker(str(tmp_path), name)
  worker.scope_for = lambda cp, _: replace(scope(), cp_sha256=car_params_digest(cp))
  suggestion = {'parameter': 'ff_gain_left', 'baseline': 0., 'target': .005, 'whole_windows': 6, 'reason': 'declared-review-only'}
  with patch('openpilot.starpilot.flm.offroad_worker.recorded_recommendations', return_value=[suggestion]):
    first = worker.run_once()
  name, _, _ = maker.segment(root, 19, 0)
  maker.segment(root, 19, 1, ending='endOfRoute', fault='truncated')
  params.put('CurrentRoute', name, block=True)
  params.put('RouteCount', 20, block=True)
  with patch('openpilot.starpilot.flm.offroad_worker.recorded_recommendations', return_value=[]):
    second = worker.run_once()
  assert second['recommendations'] == first['recommendations']
  assert second['recommendation_assessments'] == first['recommendation_assessments']
  assert worker.cache.latest()['result']['recommendations'] == first['recommendations']
  assert second['analysis_counts']['evidence_drives'] == 0
  assert second['saved_adjustments'] == 0 and params.get('AutomaticSteeringProfiles') is None


def test_repeated_context_errors_and_restart_do_not_evict_the_pending_review(tmp_path):
  from openpilot.starpilot.flm.offroad_daemon import run_parked_analysis
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  from openpilot.starpilot.flm.tests.test_offroad_backend import scope
  from openpilot.starpilot.flm.tests.test_offroad_fault_isolation import TestPartialDriveReview, TestRouteFaultIsolation

  root = tmp_path / 'logs'
  maker = TestPartialDriveReview()
  name, _, _ = maker.segment(root, 18, 0)
  maker.segment(root, 18, 1, ending='endOfRoute', fault='truncated')
  params, worker = TestRouteFaultIsolation().setup_worker(str(tmp_path), name)
  worker.scope_for = lambda cp, _: replace(scope(), cp_sha256=car_params_digest(cp))
  idea = {'parameter': 'ff_gain_left', 'baseline': 0., 'target': .005, 'whole_windows': 6, 'reason': 'declared-review-only'}
  with patch('openpilot.starpilot.flm.offroad_worker.recorded_recommendations', return_value=[idea]):
    first = worker.run_once()
  status = StatusReport(path=tmp_path / 'status.json')
  status.set(first)
  for number in range(19, 30):
    name, _, _ = maker.segment(root, number, 0, ending='endOfRoute')
    params.put('CurrentRoute', name, block=True)
    params.put('RouteCount', number + 1, block=True)
    with patch.object(worker, 'assessor', side_effect=ValueError('Manual coordinate settings require review: context_changed')), \
         patch('openpilot.starpilot.flm.offroad_daemon.traceback.print_exc'):
      assert run_parked_analysis(worker, status) is False
    assert status.current['recommendations'] == first['recommendations']
    assert status.current['status'] == 'unavailable'
    worker.cache.retire([], authorized=lambda: True)
  restarted = StatusReport(path=tmp_path / 'restart.json')
  restarted.restore(worker.cache)
  assert restarted.previous['recommendations'] == first['recommendations']
  assert worker.cache.get(first['assessment_sha256'])['kind'] == 'partial_assessment'
  assert params.get('AutomaticSteeringProfiles') is None
