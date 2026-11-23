"""Bounded real planner/host/Params learning path; no vehicle or live IPC."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import update_curve_frame
from openpilot.selfdrive.controls.tests.test_curve_runtime_loop import bus, NOW
from openpilot.starpilot.curve_speed.actions import apply_learning, learning_snapshot
from openpilot.starpilot.curve_speed.host import CurveHost
from openpilot.starpilot.curve_speed.learning import CURVATURES, NUDGE_WEIGHT
from openpilot.starpilot.curve_speed.preferences import DOCUMENT_KEY, LEGACY_KEY, MASTER_KEY, PreferenceHost
from openpilot.starpilot.curve_speed.status import StatusPublisher, observation


def vehicle():
  cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
  cp.pcmCruise = False
  return cp


def inputs(tick, *, curve=False, manual=True, exclusion=None):
  stamp = NOW + tick * 50_000_000
  sm = bus(stamp, lead_present=exclusion == 'lead')
  for name in ('modelV2', 'carState', 'carControl', 'controlsState'):
    sm.messages[name] = sm.messages[name].as_builder()
  sm['modelV2'].orientationRate.z = [.1 if curve else 0.] * 33
  sm['carControl'].longActive = not manual
  sm['controlsState'].longControlState = LongCtrlState.off if manual else LongCtrlState.pid
  sm['controlsState'].curvature = .004 if curve else 0.
  sm['carState'].leftBlinker = exclusion == 'indicator'
  if exclusion == 'stale':
    sm.logMonoTime['modelV2'] = stamp - 1_000_000_000
  if exclusion == 'unknown_lead':
    sm.valid['radarState'] = False
  return sm, stamp


def drive(planner, host, cp, start, stop, **kwargs):
  result = None
  for tick in range(start, stop):
    sm, stamp = inputs(tick, **kwargs)
    result = update_curve_frame(planner, sm, cp, stamp, host=host)
    if kwargs.get('manual', True):
      assert not planner.last_curve_ceiling_applied
      assert result.ceiling_mps is None
  return result, sm, stamp


def test_actual_planner_settles_on_straights_then_learns_short_curve():
  cp = vehicle()
  host = CurveHost(enabled=True, replay=True)
  planner = LongitudinalPlanner(cp, init_v=25.)
  result, _, _ = drive(planner, host, cp, 1, 81)
  assert not result.training and host.runtime.curve.revision == 0
  straight_reason = getattr(result, 'learning_reason', None)
  result, sm, stamp = drive(planner, host, cp, 81, 101, curve=True)
  assert result.training and result.progress > .1
  assert host.runtime.curve.revision == 20
  assert straight_reason == 'learning_waiting_for_curve'
  assert sum(row['count'] for row in host.runtime.curve.document()['buckets'].values()) == 20
  packet = StatusPublisher().attach(None, host, result, planner, now_ns=stamp, model_ns=stamp,
                                    persistence_status='writing')
  status = observation(messaging.log_from_bytes(packet.to_bytes()).slcState, stamp)
  assert status is not None and status.training and status.progress == pytest.approx(result.progress)
  assert status.reason == 'learning' and not status.applied


@pytest.mark.parametrize('exclusion', ['lead', 'unknown_lead', 'stale', 'indicator', 'automatic', 'disabled'])
def test_unqualified_straight_time_never_settles_learning(exclusion):
  cp = vehicle()
  host = CurveHost(enabled=exclusion != 'disabled', replay=True)
  planner = LongitudinalPlanner(cp, init_v=25.)
  drive(planner, host, cp, 1, 81, manual=exclusion != 'automatic', exclusion=exclusion)
  host.runtime.enabled = True
  result, _, _ = drive(planner, host, cp, 81, 101, curve=True)
  assert host.runtime.curve.revision == 0 and not result.training
  assert result.learning_reason in ('learning_lead_clear_wait', 'learning_settling', 'learning_feedback_quiet_wait')


def test_actual_curve_longitudinal_drop_is_feedback_not_passive_settling():
  cp = vehicle()
  host = CurveHost(enabled=True, replay=True)
  planner = LongitudinalPlanner(cp, init_v=25.)
  result, _, _ = drive(planner, host, cp, 1, 81, manual=False, curve=True)
  assert host.runtime.was_controlling and planner.last_curve_ceiling_applied
  assert host.runtime.curve.revision == 0 and not result.training
  # The real driver transition out of a selected in-curve controller is
  # qualified feedback. One weighted nudge does not establish passive settling.
  result, _, _ = drive(planner, host, cp, 81, 101, curve=True)
  assert host.runtime.curve.revision == 1 and not result.training
  assert sum(row['count'] for row in host.runtime.curve.document()['buckets'].values()) == NUDGE_WEIGHT
  assert result.learning_reason == 'learning_feedback_quiet_wait'


def test_saved_learning_roundtrip_galaxy_progress_automatic_use_and_reset(tmp_path):
  params = Params(str(tmp_path))
  params.put_bool(MASTER_KEY, True, block=True)
  legacy = b'{"0.001":{"average":2.2,"count":1}}'
  Path(params.get_param_path(LEGACY_KEY)).write_bytes(legacy)
  cp = vehicle()
  owner = PreferenceHost(params)
  try:
    host = owner.make_host(replay=True)
    owner.refresh(host, NOW)
    planner = LongitudinalPlanner(cp, init_v=25.)
    drive(planner, host, cp, 1, 81)
    result, _, stamp = drive(planner, host, cp, 81, 101, curve=True)
    result, _, stamp = drive(planner, host, cp, 101, 102)
    assert result.dirty_revision is not None
    owner.persist(host, result, stamp)
    assert owner.worker is not None
    owner.worker.join(timeout=2.)
    assert not owner.worker.is_alive()
    owner.persist(host, SimpleNamespace(dirty_revision=None), stamp + 50_000_000)
    assert owner.status == 'saved' and not host.runtime.curve.dirty
    saved = learning_snapshot(params)
    # This is the SAME canonical progress source consumed by the Galaxy row.
    assert saved.progress is not None
    assert saved.progress == pytest.approx(host.runtime.curve.progress) and saved.progress > .1
    document = json.loads(Path(params.get_param_path(DOCUMENT_KEY)).read_bytes())
    assert document == host.runtime.curve.document()
    assert Path(params.get_param_path(LEGACY_KEY)).read_bytes() == legacy
  finally:
    owner.close()

  restored_owner = PreferenceHost(params)
  try:
    restored = restored_owner.make_host(replay=True)
    restored_owner.refresh(restored, NOW + 10_000_000_000)
    assert restored.runtime.enabled
    assert restored.runtime.curve.document() == document  # No adopt action.
    default = CurveHost(enabled=True, replay=True)
    restored_planner = LongitudinalPlanner(cp, init_v=25.)
    default_planner = LongitudinalPlanner(cp, init_v=25.)
    # Run the unchanged approach filter through its ego+2m/s initialization;
    # a selected future ceiling need not yet control a slower current vehicle.
    for tick in range(220, 250):
      sm, stamp = inputs(tick, curve=True, manual=False)
      actual = update_curve_frame(restored_planner, sm, cp, stamp, host=restored)
      baseline = update_curve_frame(default_planner, sm, cp, stamp, host=default)
    assert actual.candidate_mps > baseline.candidate_mps
    assert actual.ceiling_mps is not None
    assert restored_planner.last_curve_ceiling_applied
    assert restored.runtime.was_controlling
  finally:
    restored_owner.close()

  saved = learning_snapshot(params)
  assert apply_learning(params, 'curve_reset', saved.sources, authorized=lambda: True).committed
  assert learning_snapshot(params).progress == 0.
  assert params.get_bool(MASTER_KEY)
  assert Path(params.get_param_path(LEGACY_KEY)).read_bytes() == legacy
  reset_owner = PreferenceHost(params)
  try:
    reset = reset_owner.make_host(replay=True)
    reset_owner.refresh(reset, NOW + 15_000_000_000)
    assert reset.runtime.curve.document()['buckets'] == {}  # Legacy cannot revive.
    reset_planner = LongitudinalPlanner(cp, init_v=25.)
    pristine = CurveHost(enabled=True, replay=True)
    pristine_planner = LongitudinalPlanner(cp, init_v=25.)
    for tick in range(320, 350):
      sm, stamp = inputs(tick, curve=True, manual=False)
      actual = update_curve_frame(reset_planner, sm, cp, stamp, host=reset)
      baseline = update_curve_frame(pristine_planner, sm, cp, stamp, host=pristine)
    assert actual.candidate_mps == baseline.candidate_mps
    assert reset_planner.last_curve_ceiling_applied
  finally:
    reset_owner.close()


def test_actual_host_24_by_200_progress_is_coverage_and_saturates():
  cp = vehicle()
  host = CurveHost(enabled=True, replay=True)
  # Settle via the actual host/lead projection, without recording straights.
  for tick in range(1, 81):
    sm, stamp = inputs(tick)
    host.sample(sm, cp, stamp)
  tick = 81
  for bucket, curvature in enumerate(CURVATURES):
    for _ in range(200):
      sm, stamp = inputs(tick, curve=True)
      # 55m/s makes even the lowest bucket exceed the unchanged1.3m/s²
      # observation gate. Manual speed means no ceiling/control authority.
      sm['carState'].vEgo = 55.
      sm['carState'].vCruise = 198.
      sm['controlsState'].curvature = curvature
      sm['modelV2'].velocity.x = [55.] * 33
      sm['modelV2'].orientationRate.z = [55. * curvature] * 33
      result = host.sample(sm, cp, stamp)
      assert result.training and result.ceiling_mps is None
      tick += 1
    assert result.progress == pytest.approx(100. * (bucket + 1) / 24)
  document = host.runtime.curve.document()['buckets']
  assert len(document) == 24 and all(row['count'] == 200 for row in document.values())
  assert host.runtime.curve.revision == 4800 and result.progress == 100.
  sm, stamp = inputs(tick, curve=True)
  result = host.sample(sm, cp, stamp)
  assert not result.training and result.learning_reason == 'learning_below_comfort' and result.progress == 100.
