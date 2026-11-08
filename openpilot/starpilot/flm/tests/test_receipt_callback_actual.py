"""Parent-boundary source renewal with genuine collector admission and blocks."""
from openpilot.starpilot.flm.tests._drain_fixture import separate_drain_fixture
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from openpilot.common.params import Params
from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.flm import steerlearnerd
from openpilot.starpilot.flm.fullrate_collector import FullrateCollector
from openpilot.starpilot.flm.runtime_adapter_registry import read_source, SOURCE_LEASE_NS
from openpilot.starpilot.flm.tests.test_fullrate_pipeline import BASE as FIXTURE_BASE, SyntheticAdapter, snapshots, serialized_control, serialized_output


BASE = FIXTURE_BASE + 14_000_000_000


@pytest.mark.parametrize('boundary', ['control', 'output'])
@pytest.mark.parametrize('change', [None, 'epoch', 'calibration', 'cp', 'reset', 'manual', 'off', 'expired', 'future', 'slow_read'])
def test_parent_renews_actual_source_and_retains_real_collector_block(tmp_path, boundary, change, queued_calibration=True):
  cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
  params = Params(str(tmp_path))
  params.put_bool('OpenpilotEnabledToggle', True, block=True)
  current = [BASE + 490_000_000]
  reads, instances = [], []
  metadata_reconciled = [False]
  metadata_reads = []
  initial = read_source(cp, params, now_ns=BASE, cp_sha256='b' * 64, calibration=(0., 0., 0.),
                        vehicle_session='test-vehicle', calibration_generation='c' * 32)
  assert initial.enabled and not initial.manual

  def reader(now):
    reads.append(now)
    if len(reads) == 1:
      return initial
    # This is a genuine new Params read, with its own stamp and lease.
    assert metadata_reconciled[0]  # Actual queued SM reconciliation precedes source read.
    metadata_reads.append(now)
    calibration = (0., 0., 0.) if queued_calibration else ()
    renewed = read_source(cp, params, now_ns=now, cp_sha256=initial.cp_sha256, calibration=calibration,
                          vehicle_session='test-vehicle', calibration_generation=initial.calibration_generation)
    changes = {'epoch': {'source_epoch': 'd' * 64}, 'calibration': {'calibration_generation': 'd' * 32},
               'cp': {'cp_sha256': 'd' * 64}, 'reset': {'reset': '1'}, 'manual': {'manual': True},
               'off': {'enabled': False}, 'expired': {'observed_ns': now - SOURCE_LEASE_NS - 1, 'expires_ns': now - 1},
               'future': {'observed_ns': now + 1, 'expires_ns': now + SOURCE_LEASE_NS + 1}}
    if change == 'slow_read':
      current[0] += SOURCE_LEASE_NS + 1
    return replace(renewed, **changes.get(change, {}))

  class Adapter(SyntheticAdapter):
    def select_objective(self, key):
      pass
    def prepare_control(self, row):
      return row

  def factory(adapter):
    collector = FullrateCollector(adapter)
    # Build one genuine independent block through normal control/output/physical
    # admission. No collector guards, buffers or windows are patched.
    for index in range(281):
      stamp = BASE - 14_000_000_000 + index * 50_000_000
      row, applied, physical = snapshots(stamp)
      row = replace(row, source_epoch=initial.source_epoch)
      assert collector.control(row, stamp + 1_000_000)
      assert collector.output(applied, stamp + 1_000_000)
      collector.physical(physical, stamp + 1_000_000)
    assert len(collector.blocks) == 1
    collector.split('fixture_independent_block_finished')
    instances.append(collector)
    return collector

  sm = Mock(updated={'deviceMotion': False}, logMonoTime={'deviceMotion': 0})
  outer_updates = [0]
  def update(timeout=None):
    if timeout == 0:
      metadata_reconciled[0] = True
      return
    outer_updates[0] += 1
    if outer_updates[0] > 1:
      raise StopIteration
  sm.update.side_effect = update
  fitter = Mock()
  fitter.child = object()  # Existing pending child; no fitting runs in this test.
  fitter.poll.return_value = None
  control_socket, output_socket = object(), object()

  def row_at(stamp):
    row, applied, _ = snapshots(stamp)
    # Commands remain fresh; only the parent's separately cached receipt crosses
    # its boundary. The second control carries the fresh producer receipt.
    row = replace(row, source_epoch=initial.source_epoch,
                  source_receipt_ns=BASE if stamp < BASE + SOURCE_LEASE_NS else stamp)
    return serialized_control(row), serialized_output(applied)

  after_drain = []
  def drain(socket, callback, receive, sink, now):
    if socket is control_socket:
      event, _ = row_at(current[0])
      callback(event, sink, now)
      fitter.cancel.reset_mock()  # Pending work begins after initial identity setup.
      if boundary == 'control':
        current[0] = BASE + 510_000_000
        event, _ = row_at(current[0])
        callback(event, sink, now)
    else:
      stamp = BASE + 490_000_000 if boundary == 'output' else current[0]
      current[0] = BASE + 512_000_000
      _, event = row_at(stamp)
      callback(event, sink, now)
      after_drain.append(current[0])

  def native(*args):
    # Arrival bookkeeping now samples health before draining/renewing receipts.
    # Those calls confer no native authority; stop only at the original
    # post-drain physical-admission boundary, before any fitting/publication.
    if after_drain and current[0] >= BASE + 512_000_000:
      raise StopIteration
    return False

  persistence = Mock()
  with patch.object(steerlearnerd, 'FullrateCollector', side_effect=factory), \
       patch.object(steerlearnerd, 'drain_pair', separate_drain_fixture(drain)), \
       patch('openpilot.starpilot.flm.native_health.current_native_health', native):
    with pytest.raises(StopIteration):
      steerlearnerd.run(cp, sm, control_socket, output_socket, Mock(), Mock(return_value=Adapter()), Mock(),
                        reader, lambda row: ('ff',), lambda row: 'ff', Mock(), persistence, fitter, Mock(), clock=lambda: current[0])
  expected_reads = [BASE + 490_000_000, BASE + (510_000_000 if boundary == 'control' else 512_000_000)]
  # The final queued-input refresh requires its own genuine Params read when
  # history remains. Explicit configuration changes discard the collector.
  if change in (None, 'expired', 'future', 'slow_read'):
    assert len(after_drain) == 1
    expected_reads.append(after_drain[0])
  assert reads == expected_reads
  assert metadata_reads
  assert any(call.args == (0,) for call in sm.update.call_args_list)
  if change is None and queued_calibration:
    assert len(instances) == 1
    assert len(instances[0].blocks) == 1
    assert instances[0].ready  # Genuine admitted control/output join survived.
    assert fitter.cancel.call_count == 0  # Same-source renewal leaves pending fitting alive.
  elif change in ('expired', 'future', 'slow_read') or (change is None and not queued_calibration):
    # An unavailable receipt/calibration is uncertainty, not an explicit reset.
    # Preserve only the genuine historical block; withdraw every current proof.
    collector = instances[0]
    assert len(collector.blocks) == 1
    assert not collector.ready and not collector.controls and not collector.applied
    assert not collector.raw_points and collector.active_since == 0
    assert collector.last_qualified_response == 0
    assert collector.fit_result is None and collector.fit_validation is None
    assert collector.result is None and collector.last_validation is None
    assert collector.adapter.fit_profile is None
    assert not collector.adapter.current_physical_valid
    assert collector.adapter.last_physical_ns == 0
    assert fitter.cancel.call_count >= 1
  else:
    # Fresh explicit off/manual/configuration changes still destroy history.
    assert not instances[0].blocks
    assert fitter.cancel.call_count >= 1
  persistence.save_current.assert_not_called()
  assert reads == expected_reads  # No repeated read outside the three declared phases.


def test_old_command_cannot_enter_collector_after_receipt_renewal():
  collector = FullrateCollector(SyntheticAdapter())
  row, _, _ = snapshots(BASE)
  assert not collector.control(row, BASE + 100_000_001)
  assert not collector.controls and not collector.ready


def test_parent_expired_receipt_without_queued_valid_calibration_keeps_only_history(tmp_path):
  test_parent_renews_actual_source_and_retains_real_collector_block(tmp_path, 'output', None, queued_calibration=False)
