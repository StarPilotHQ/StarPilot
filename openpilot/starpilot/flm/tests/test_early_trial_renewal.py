"""Early renewal scheduling contracts; unit fixtures are not road/native proof."""
from dataclasses import replace
from types import SimpleNamespace as S
from typing import Any
import pytest
from openpilot.starpilot.flm import steerlearnerd
from openpilot.starpilot.flm.live_tracking_actor import LiveTrackingActor
from openpilot.starpilot.flm.live_tracking_response import response_witness_value, prepare_response_witness
from openpilot.starpilot.flm.live_trial_owner import TrackingTrialOwner
from openpilot.starpilot.flm.tests.test_live_tracking_daemon import source
from openpilot.starpilot.flm.tests.test_live_trial_owner import offer

BASE = 10_000_000_000


def setup(monkeypatch):
  old = offer(BASE)
  actor: Any = LiveTrackingActor(source_tracking=True)
  actor.certificate = response_witness_value(old.response_witness).certificate
  actor.last_publication_pose_ns = BASE
  state = {'trial_id':old.trial_id, 'phase':old.phase, 'target_sequence':old.target_sequence, 'target':old.target}
  optimizer = S(response_witness=old.response_witness, offer_state=state, sequence=old.sequence)
  optimizer._hash = lambda target: old.target_sha256
  def issue(now_ns, *, publication):
    witness = response_witness_value(optimizer.response_witness)
    return replace(old, sequence=old.sequence+1, observed_ns=now_ns, expires_ns=witness.valid_until_ns,
                   response_witness=optimizer.response_witness)
  optimizer.offer = issue
  interruptions = []
  actor.ingress = S(latest_offer=old, optimizer=optimizer, interrupt=interruptions.append)
  collector = S(ready=[(object(), object())])
  now = [BASE+50_000_000]
  observations = []
  def observe(sm, cp, current, stamp, native):
    observations.append((stamp, native))
    witness = response_witness_value(old.response_witness)
    renewed = replace(witness, last_control_ns=stamp, last_apply_ns=stamp, last_pose_ns=stamp, valid_until_ns=stamp+100_000_000)
    optimizer.response_witness = prepare_response_witness(renewed, old.binding, stamp)
  monkeypatch.setattr('openpilot.starpilot.flm.source_plant_runtime.observe', observe)
  monkeypatch.setattr('openpilot.starpilot.flm.native_health.current_native_health', lambda *args: True)
  return actor, collector, now, observations, interruptions


def early(actor, collector, now, current_source=None):
  return steerlearnerd._early_tracking_offer(S(), S(), collector, actor,
    source(now[0]) if current_source is None else current_source, now_ns=now[0], clock=lambda:now[0])


def test_new_qualified_pose_renews_before_expensive_fifo_without_extending_trial(monkeypatch):
  actor, collector, now, observations, _ = setup(monkeypatch)
  previous = actor.ingress.latest_offer
  renewed = early(actor, collector, now)
  assert renewed is not None and renewed.sequence > previous.sequence
  assert renewed.trial_id == previous.trial_id and renewed.deadline_ns == previous.deadline_ns
  assert observations == [(BASE+50_000_000, True)]
  assert renewed.expires_ns == BASE+150_000_000
  # Real typed controller owner consumes before45ms of FIFO work spends the old lease.
  owner = TrackingTrialOwner(previous.binding)
  assert owner.prepare(previous, now_ns=BASE, permission=True, binding=previous.binding)
  assert owner.prepare(renewed, now_ns=now[0], permission=True, binding=previous.binding)
  for offset in (60, 70, 80, 90, 100, 105):
    now[0] = BASE+offset*1_000_000
    assert owner.prepare(renewed, now_ns=now[0], permission=True, binding=previous.binding)
  assert now[0] > previous.expires_ns
  assert owner.confirmed_full == previous.baseline


def test_same_pose_cannot_publish_twice(monkeypatch):
  actor, collector, now, observations, _ = setup(monkeypatch)
  assert early(actor, collector, now) is not None
  assert early(actor, collector, now) is None
  assert len(observations) == 2 and actor.last_publication_pose_ns == now[0]


@pytest.mark.parametrize('boundary', ['not_source_tracking', 'no_ingress', 'no_certificate', 'restoring',
                                     'no_ready', 'disabled', 'manual', 'unknown_source', 'native_denied'])
def test_no_early_trial_authority_across_existing_boundaries(monkeypatch, boundary):
  actor, collector, now, observations, _ = setup(monkeypatch)
  current = source(now[0])
  if boundary == 'not_source_tracking':
    actor.source_tracking = False
  elif boundary == 'no_ingress':
    actor.ingress = None
  elif boundary == 'no_certificate':
    actor.certificate = None
  elif boundary == 'restoring':
    actor.restore_pending = True
  elif boundary == 'no_ready':
    collector.ready.clear()
  elif boundary == 'disabled':
    current = replace(current, enabled=False)
  elif boundary == 'manual':
    current = replace(current, manual=True)
  elif boundary == 'unknown_source':
    current = replace(current, configuration_known=False)
  else:
    monkeypatch.setattr('openpilot.starpilot.flm.native_health.current_native_health', lambda *args: False)
  assert early(actor, collector, now, current) is None
  assert observations == []


def test_pending_pose_cannot_refresh_original_witness(monkeypatch):
  actor, collector, now, observations, _ = setup(monkeypatch)
  monkeypatch.setattr('openpilot.starpilot.flm.source_plant_runtime.observe', lambda *args: None)
  old = actor.ingress.latest_offer
  assert early(actor, collector, now) is None
  assert actor.ingress.latest_offer is old and actor.last_publication_pose_ns == BASE
  assert observations == []


def test_work_crossing_original_response_expiry_withdraws_offer(monkeypatch):
  actor, collector, now, _, interruptions = setup(monkeypatch)
  def delayed(*args): now[0] = BASE+100_000_001
  monkeypatch.setattr('openpilot.starpilot.flm.source_plant_runtime.observe', delayed)
  assert early(actor, collector, now) is None
  assert interruptions == ['fresh_response_witness_unavailable']
  assert actor.last_publication_pose_ns == BASE


def test_final_serialization_still_rejects_native_loss_or_expiry(monkeypatch):
  actor, collector, now, _, _ = setup(monkeypatch)
  renewed = early(actor, collector, now)
  assert renewed is not None
  for stamp, native in ((now[0], False), (renewed.expires_ns+1, True)):
    message = steerlearnerd.tracking_message(source(stamp), now_ns=stamp, current_native=native, offer=renewed)
    assert message.starpilotSteeringTracking.trial.version == 0


def test_pending_original_bracket_does_not_rebuild_the_already_published_offer(monkeypatch):
  actor, collector, now, _, _ = setup(monkeypatch)
  actor.ingress.last_pose_ns = BASE
  collector._original_source_pose_queue = (S(mono_ns=BASE+25_000_000),)
  monkeypatch.setattr('openpilot.starpilot.flm.source_plant_runtime.observe', lambda *args: None)
  def unused(*args):
    raise AssertionError('pending bracket cannot supply a newer response witness')
  monkeypatch.setattr(actor, '_current_publication_offer', unused)
  assert early(actor, collector, now) is None
  assert actor.last_publication_pose_ns == BASE
