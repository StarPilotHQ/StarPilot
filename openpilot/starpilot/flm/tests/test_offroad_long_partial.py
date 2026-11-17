"""Declared long recordings: bounded suggestions, never automatic qualification."""

from pathlib import Path
from unittest.mock import patch

import pytest

from openpilot.starpilot.flm.offroad_partial import MAX_LONG_REVIEW_SEGMENTS, partial_inventory, walk_partial_route
from openpilot.starpilot.flm.offroad_route import PartialRouteReceipt, RouteInputError, latest_route, source_revisions
from openpilot.starpilot.flm.offroad_store import KEY, RouteStorage
from openpilot.starpilot.flm.tests import test_offroad_coordinate_policy as fixtures
from openpilot.starpilot.flm.tests import test_offroad_fault_isolation as recordings


def test_355_segment_drive_gets_bounded_partial_review_and_preserves_saved_tune(tmp_path):
  root = tmp_path / 'logs'
  maker = recordings.TestPartialDriveReview()
  for number in range(355):
    name, path, _ = maker.segment(root, 18, number, ending='endOfRoute' if number == 354 else 'endOfSegment')
  params, worker = recordings.TestRouteFaultIsolation().setup_worker(str(tmp_path), name)
  saved = fixtures.TestSavedSetupCapacity.row(1)
  assert RouteStorage(params).save(saved, expected=None, authorized=lambda: True)
  before = params.get(KEY)
  inventory = latest_route(params, root, permitted=lambda: True, inventory_only=True)
  assert inventory['selection_error'] == 'Latest drive is open, incomplete or unavailable'
  revisions = source_revisions(root, inventory)
  result = worker.run_once()
  assert result['partial_drive'] and result['partial_segments'] == [MAX_LONG_REVIEW_SEGMENTS, 355]
  assert result['analysis_counts']['recorded_lateral_request_rows'] == MAX_LONG_REVIEW_SEGMENTS * 50
  assert result['configuration_changed'] is False and params.get(KEY) == before
  document = worker.cache.get(result['assessment_sha256'])
  receipt = document['assessment']['route']
  assert receipt['decoded_segments'][0] == 0 and receipt['decoded_segments'][-1] == 354
  assert len(receipt['omitted_segments']) == 355 - MAX_LONG_REVIEW_SEGMENTS
  assert receipt['completed_wall_ns'] == 0 and document['kind'] == 'partial_assessment'
  # An unchanged long route is cached, while a late repair/damage is noticed.
  with patch('openpilot.starpilot.flm.offroad_partial.analyze_partial', side_effect=AssertionError('Unchanged input was repeated')):
    assert worker.run_once()['partial_drive']
  path.write_bytes(b'damaged late segment')
  assert source_revisions(root, inventory) != revisions
  later = worker.run_once()
  assert later['partial_segments'] == [MAX_LONG_REVIEW_SEGMENTS - 1, 355]
  assert params.get(KEY) == before and worker.cache.get(result['assessment_sha256']) == document


def test_byte_limit_retains_only_already_decoded_complete_segments(tmp_path):
  maker = recordings.TestPartialDriveReview()
  name, first, _ = maker.segment(tmp_path, 18, 0)
  maker.segment(tmp_path, 18, 1, ending='endOfRoute')
  route = partial_inventory(tmp_path, name, permitted=lambda: True)
  consumed = []
  with patch('openpilot.starpilot.flm.offroad_partial.MAX_TOTAL_BYTES', first.stat().st_size):
    receipt = walk_partial_route(tmp_path, route, permitted=lambda: True, consume=lambda n, rows: consumed.append(n))
  assert type(receipt) is PartialRouteReceipt and consumed == [0]
  assert receipt.omitted_segments == ((1, 'Partial input byte bound reached'),)
  assert receipt.completed_wall_ns == 0


def test_real_duplicate_segment_numbers_still_refuse(tmp_path):
  name, first, _ = recordings.TestPartialDriveReview().segment(tmp_path, 18, 0)
  alias = Path(str(first.parent)[:-1] + '00')
  alias.mkdir()
  (alias / first.name).write_bytes(first.read_bytes())
  with pytest.raises(RouteInputError, match='Duplicate original segment number'):
    partial_inventory(tmp_path, name, permitted=lambda: True)
