"""Registered Ioniq6 CAN encoding/decoding; declared state, no road/native claim."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from opendbc.car.steering_projection import TorqueProjectionObserver

from openpilot.starpilot.flm.fullrate_collector import FullrateCollector, AppliedEvidence, drain_pair
from openpilot.starpilot.flm.learning_wire import read_control, read_applied
from openpilot.starpilot.flm.torque_emission_replay import EmissionHistory
from openpilot.starpilot.flm.tests.test_fullrate_pipeline import snapshots, serialized_control, serialized_output
from openpilot.starpilot.flm.tests.test_paired_evidence_drain import transport

BASE = 10_000_000_000


def original_rows(count=17):
  factory = interfaces[CAR.HYUNDAI_IONIQ_6]
  cp = factory.get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
  ci = factory(cp)
  state = structs.CarState.new_message(canValid=True, canTimeout=False, vEgo=20., vEgoRaw=20.,
    steeringAngleDeg=0., steeringRateDeg=0., steeringTorque=0., standstill=False)
  state.cruiseState.enabled = state.cruiseState.available = True
  state.cruiseState.speed = 20.
  ci.CS.out = state.as_reader()
  observer = TorqueProjectionObserver(cp)
  ci.CC.learning_projection_observer = observer
  command = structs.CarControl.new_message(enabled=True, latActive=True, longActive=False)
  command.actuators.torque = 0.
  rows = []
  for index in range(count):
    stamp = BASE + index * 10_000_000
    control = replace(snapshots(stamp)[0], domain='normalized_torque', requested=0., baseline_prediction=0.)
    actuators, packets = ci.apply(command.as_reader(), stamp + 1_000_000)
    projection = observer.current
    assert projection is not None and projection.steer_requested
    assert any(msg[0] == projection.can_address and msg[1] == projection.can_data and msg[2] == projection.can_bus for msg in packets)
    output = AppliedEvidence(stamp, stamp, stamp + 1_000_000, 'normalized_torque', float(actuators.torque),
      True, True, False, False, False, True, projection, ci.CC.frame, stamp + 1_000_000)
    rows.append((control, output))
  return cp, rows


def exercise(rows, cp, now):
  history = EmissionHistory(cp, identity=rows[0][0].identity, baseline_revision=rows[0][0].baseline_revision)
  adapter = SimpleNamespace(command_domain='normalized_torque', record_actual_carrier=history.record_carrier,
    record_actual_emission=history.record)
  collector = FullrateCollector(adapter)
  sockets, receive, _ = transport([serialized_control(c) for c, _ in rows], [serialized_output(o) for _, o in rows])
  accepted = []

  def control(event, current, stamp):
    accepted.append(('control', current.control(read_control(event), stamp)))

  def output(event, current, stamp):
    accepted.append(('output', current.output(read_applied(event), stamp)))

  assert drain_pair(sockets[0], control, sockets[1], output, receive, collector, now)
  for command, emission in collector.ready:
    history.verify(command, emission)
  return collector, accepted


def test_mixed_age_original_hkg_frames_join_with_actual_can_decoder():
  cp, rows = original_rows()
  collector, accepted = exercise(rows, cp, BASE + 165_000_000)
  assert len(collector.ready) == 10
  assert sum(not good for kind, good in accepted if kind == 'control') == 7
  assert sum(not good for kind, good in accepted if kind == 'output') == 7
  assert collector.training_interruptions['stale_control'] == 7
  assert collector.training_interruptions['stale_applied_output'] == 7


@pytest.mark.parametrize('failure', ('gap', 'regression', 'future', 'denied'))
def test_original_hkg_carrier_and_native_denials_still_invalidate(failure):
  cp, rows = original_rows()
  rows = rows[-3:]
  c, o = rows[-1]
  changes = {'gap': {'carrier_frame': o.carrier_frame + 1},
             'regression': {'carrier_frame': o.carrier_frame - 2},
             'future': {'carrier_observed_ns': BASE + 200_000_000},
             'denied': {'native_allowed': False}}[failure]
  rows[-1] = c, replace(o, **changes)
  collector, accepted = exercise(rows, cp, BASE + 165_000_000)
  assert not collector.ready
  if failure == 'denied':
    assert collector.reason == 'applied_native_witness_unavailable'
  else:
    assert accepted[-1] == ('output', False)
    assert collector.reason == 'actual_carrier_gap_or_clock'


@pytest.mark.parametrize('native_current', (True, False))
def test_paired_collector_reaches_real_ingress_without_inventing_scores_or_trials(native_current):
  from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
  from openpilot.starpilot.flm.live_tracking_ingress import LiveTrackingIngress
  from openpilot.starpilot.flm.tests.test_tracking_source_context import context

  cp, rows = original_rows()
  first = rows[0][0]
  # Explicit source-context unit seam. No physical pose, response witness,
  # probe result or fitted episode is supplied by this transport test.
  binding = replace(context().binding, identity=first.identity, source_epoch=first.source_epoch,
    baseline_revision=first.baseline_revision, controller_revision=first.controller_revision,
    native_identity=first.native_identity, native_required=first.native_required)
  certificate = context(binding)
  ingress = LiveTrackingIngress(certificate.binding, certificate, tuple((name, 0.) for name in NAMES), controller_domain='torque')
  rows = [(replace(c, features=(1., 1.), applied_coordinates=(0., 0.),
    stage_values=(('setpoint', .1),), replay_inputs=(('desired_curvature', .1 / 400.), ('vEgo', 20.),
      ('roll', 0.), ('curvature_limited', 0.), ('control_frame', float(index)))), o) for index, (c, o) in enumerate(rows)]
  history = EmissionHistory(cp, identity=first.identity, baseline_revision=first.baseline_revision)
  adapter = SimpleNamespace(command_domain='normalized_torque', record_actual_carrier=history.record_carrier,
    record_actual_emission=history.record)
  collector = FullrateCollector(adapter)
  collector.live_tracking = ingress
  collector._learning_current_native_allowed = native_current  # Declared unit authority only.
  sockets, receive, _ = transport([serialized_control(c) for c, _ in rows], [serialized_output(o) for _, o in rows])
  now = BASE + 165_000_000
  assert drain_pair(sockets[0], lambda e, c, n: c.control(read_control(e), n),
    sockets[1], lambda e, c, n: c.output(read_applied(e), n), receive, collector, now)
  for control, output in collector.ready:
    history.verify(control, output)
  expected = [c.control_ns for c, _ in rows[7:]] if native_current else []
  assert list(ingress.episodes.ticks) == expected
  assert [c.control_ns for c in ingress.control_history] == expected
  assert ingress.episodes.emission_count == len(expected)
  assert not ingress.episodes.endpoints and not ingress.episodes.completed and not ingress.episodes.ready
  assert ingress.monitor.witness(now) is None
  assert ingress.optimizer.offer(now) is None
  assert not ingress.optimizer.before and not ingress.optimizer.future


def test_lossless_quota_preserves_current_actual_hkg_native_emission_records():
  from openpilot.starpilot.flm.fullrate_collector import MAX_DRAIN, PairedDrainState

  cp, rows = original_rows(MAX_DRAIN + 3)
  history = EmissionHistory(cp, identity=rows[0][0].identity, baseline_revision=rows[0][0].baseline_revision)
  adapter = SimpleNamespace(command_domain='normalized_torque', record_actual_carrier=history.record_carrier,
    record_actual_emission=history.record)
  collector = FullrateCollector(adapter)
  sockets, receive, _ = transport([serialized_control(c) for c, _ in rows], [serialized_output(o) for _, o in rows])
  state = PairedDrainState(*sockets)
  seen = []

  def control(event, current, now):
    row = read_control(event)
    seen.append(('control', row.control_ns))
    current.control(row, now)

  def output(event, current, now):
    row = read_applied(event)
    seen.append(('output', row.apply_ns))
    current.output(row, now)

  assert not drain_pair(sockets[0], control, sockets[1], output, receive, collector, BASE + 341_000_000, state)
  assert list(collector.ready) == rows[25:MAX_DRAIN - 1]
  assert collector.training_interruptions['stale_control'] == 25
  assert collector.dropped == 0
  for command, emission in collector.ready:
    history.verify(command, emission)
  assert drain_pair(sockets[0], control, sockets[1], output, receive, collector, BASE + 342_000_000, state)
  assert list(collector.ready) == rows[25:]
  assert len(seen) == len(set(seen)) == 2 * len(rows)
  for command, emission in collector.ready:
    history.verify(command, emission)
  assert collector.training_interruptions['actual_carrier_gap_or_clock'] == 0
  assert collector.training_interruptions['fullrate_overrun'] == 0
