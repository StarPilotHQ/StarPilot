"""Completed-number/worker ownership regression; no native or road proof.

The established numerical fixture supplies synthetic endpoints and derivatives.
Actual actor/ingress/window ownership, source expiry and withdrawal execute.
"""
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, full_profile_sha256
from openpilot.starpilot.flm.live_tracking_actor import LiveTrackingActor
from openpilot.starpilot.flm.live_tracking_ingress import LiveTrackingIngress
from openpilot.starpilot.flm.runtime_adapter_registry import SourceState
from openpilot.starpilot.flm.tests.test_live_tracking_episodes import requested_completed_builder, numerical_probe_from_saved


def pending_inline(monkeypatch):
  builder, group, name, queued = requested_completed_builder(monkeypatch)
  ingress = LiveTrackingIngress(builder.binding, builder.certificate, tuple((name, 0.) for name in NAMES), controller_domain='torque')
  ingress.episodes = builder
  polls = []
  probe = SimpleNamespace(poll=lambda **kwargs: polls.append(kwargs), poll_probe=lambda **kwargs: None, child=None)
  actor: Any = LiveTrackingActor(probe_process=probe)
  actor.ingress, actor.certificate, actor.seed = ingress, builder.certificate, builder.binding
  actor.token = tuple(vars(actor.seed).values())
  token = (actor.token, actor.certificate.model_revision, full_profile_sha256(builder.binding.baseline_revision, ingress.optimizer.accepted))
  actor.prepared_probe_context = {'fixture': 'original numerical ownership only'}
  actor.probe_preparation = SimpleNamespace(pid=123, token=token, canceled=False, started=queued)
  source = SourceState(True, False, builder.binding.reset_generation, builder.binding.source_epoch,
    queued, queued + 500_000_000, builder.binding.cp_sha256,
    calibration_generation=builder.binding.calibration_generation, persistent_source_epoch=builder.binding.persistent_source_epoch)
  return actor, source, SimpleNamespace(ready=[]), builder, group, name, queued, polls


def test_input_gap_retains_owned_inline_results_without_restoring_permission(monkeypatch):
  actor, source, collector, builder, group, name, queued, polls = pending_inline(monkeypatch)
  saved = next(iter(builder.completed.values()))
  episode_id, deadline = saved['episode'].episode_id, saved['deadline']
  results = [numerical_probe_from_saved(builder, saved, point, group, name) for point in saved['points']]
  assert actor.probe_token is None  # Normal inline execution has no external child.
  assert actor.tick(collector, source, now_ns=queued + 1, current_native=False, evidence_delay_only=True) is None
  assert actor.reason == 'qualified_control_output_pair_required'
  assert episode_id in builder.completed
  assert builder.completed[episode_id]['deadline'] == deadline
  assert not builder.ticks and not builder.endpoints and not actor.ingress.monitor.rows
  assert actor.ingress.latest_offer is None and actor.ingress.optimizer.response_witness is None
  assert polls == [{'token': None, 'now_ns': queued + 1}]
  for index, result in enumerate(results):
    assert actor.ingress.observe_probe(result, now_ns=queued + index + 2)
  (episode,) = builder.drain(now_ns=queued + 8)
  assert episode.episode_id == episode_id and episode.sensitivity[0].native_endpoints == 4
  assert actor.ingress.latest_offer is None and actor.ingress.optimizer.response_witness is None
  assert actor.application is None  # Retained numbers never grant an actuator target.


@pytest.mark.parametrize('failure', ['missing_context', 'reaped', 'canceled', 'expired', 'future',
  'owner_changed', 'model_changed', 'accepted_changed', 'disabled', 'manual', 'source_expired', 'source_changed'])
def test_unowned_inline_result_cannot_keep_completed_window(monkeypatch, failure):
  actor, source, collector, builder, _group, _name, queued, _polls = pending_inline(monkeypatch)
  if failure == 'missing_context':
    actor.prepared_probe_context = None
  elif failure == 'reaped':
    actor.probe_preparation.pid = None
  elif failure == 'canceled':
    actor.probe_preparation.canceled = True
  elif failure == 'expired':
    actor.probe_preparation.started = queued - 30_000_000_001
  elif failure == 'future':
    actor.probe_preparation.started = queued + 2
  elif failure == 'owner_changed':
    actor.probe_preparation.token = ('other', *actor.probe_preparation.token[1:])
  elif failure == 'model_changed':
    actor.probe_preparation.token = (actor.token, 'other', actor.probe_preparation.token[2])
  elif failure == 'accepted_changed':
    actor.ingress.optimizer.accepted[NAMES[0]] = .01
  elif failure == 'disabled':
    source = replace(source, enabled=False)
  elif failure == 'manual':
    source = replace(source, manual=True)
  elif failure == 'source_expired':
    source = replace(source, expires_ns=queued)
  elif failure == 'source_changed':
    source = replace(source, source_epoch='9' * 64)
  assert actor.tick(collector, source, now_ns=queued + 1, current_native=False) is None
  assert not builder.completed and not builder.probe_requests and not builder.ready
  assert actor.ingress.latest_offer is None and actor.application is None
