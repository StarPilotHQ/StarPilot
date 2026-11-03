"""Typed synthetic restore/optimizer contracts, not vehicle or native proof."""
from dataclasses import replace
import pytest

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.live_tracking_actor import LiveTrackingActor
from openpilot.starpilot.flm.live_tracking_ingress import LiveTrackingIngress
from openpilot.starpilot.flm.live_tracking_response import validate_response_witness
from openpilot.starpilot.flm.live_trial_contract import LiveRestoreOffer, RESTORE_VERSION, SOURCE_EXPERIMENT_STEP
from openpilot.starpilot.flm.live_trial_owner import LiveTrialApplication
from openpilot.starpilot.flm.steerlearnerd import _LiveHistoryRestore
from openpilot.starpilot.flm.tests.test_live_tracking import episode
from openpilot.starpilot.flm.tests.test_tracking_source_context import context, refresh


def restored_actor():
  certificate = context()
  actor = LiveTrackingActor(source_tracking=True)
  actor.certificate = certificate
  actor.ingress = LiveTrackingIngress(certificate.binding, certificate,
    tuple((name, 0.0) for name in NAMES), controller_domain='torque')
  actor.hold_restore(True)
  history = _LiveHistoryRestore(())
  acknowledge_restore(actor, history, 1_000_000_000)
  return actor, history


def restore_messages(actor, history, now):
  optimizer = actor.ingress.optimizer
  refresh(optimizer, now)
  target = tuple((name, 0.01 if name == 'center_taper_max' else 0.0) for name in NAMES)
  optimizer.publication_sequence += 1
  history.offer = LiveRestoreOffer(RESTORE_VERSION, actor.certificate.binding, 'a' * 64, 'b' * 64,
    target, optimizer._hash(target), optimizer.publication_sequence, now, now + 100_000_000,
    optimizer.response_witness).validate()
  application = LiveTrialApplication(1, history.offer.binding, history.offer.history_trial_id,
    history.offer.sequence, now, 'restored', target, history.offer.target_sha256,
    history.offer.target_sha256, 'actual_confirmed_history_restored')
  return application


def acknowledge_restore(actor, history, now):
  application = restore_messages(actor, history, now)
  assert history.acknowledge(actor, application, now_ns=now)
  assert history.restored and not actor.restore_pending and actor.ingress.scoring_enabled
  return application


def next_trial(actor, history, name):
  optimizer = actor.ingress.optimizer
  kind = 'subtle' if name.startswith('center_') or name == 'highway_center_taper_max' else 'large'
  for index in range(6):
    row = episode(optimizer, 10 + index, name, kind=kind, error=0.2)
    row = replace(row, normalization_accel=max(0.1, row.goal_rms))
    # Ordinary restore messages keep arriving while new evidence accumulates.
    acknowledge_restore(actor, history, row.end_ns)
    optimizer.observe_episode(row, now_ns=row.end_ns)
    acknowledge_restore(actor, history, row.end_ns + 1_000_000)
  return optimizer.offer(row.end_ns + 1_000_000)


@pytest.mark.parametrize('name', NAMES)
def test_each_coordinate_can_start_a_new_independent_trial_after_restore(name):
  actor, history = restored_actor()
  optimizer = actor.ingress.optimizer
  restored = dict(optimizer.accepted)
  trial = next_trial(actor, history, name)
  assert trial is not None and trial.phase == 'trial' and trial.names == (name,)
  assert dict(trial.baseline) == restored and optimizer.accepted == restored
  changes = [(key, value - restored[key]) for key, value in trial.target if value != restored[key]]
  assert len(changes) == 1 and changes[0][0] == name
  assert changes[0][1] == pytest.approx(SOURCE_EXPERIMENT_STEP)
  assert len(optimizer.before) == 3 and not optimizer.future and optimizer.settled_ns is None
  assert trial.trial_id != history.offer.history_trial_id
  assert trial.sequence > history.offer.sequence
  assert trial.deadline_ns == trial.started_ns + 120_000_000_000
  # The saved target is the new baseline, never a fake ACK of this trial.
  assert optimizer.last_ack is None


def test_duplicate_restore_preserves_pending_trial_and_fresh_future_evidence():
  actor, history = restored_actor()
  optimizer = actor.ingress.optimizer
  trial = next_trial(actor, history, 'ff_gain_right')
  assert trial is not None
  now = trial.observed_ns + 1_000_000
  refresh(optimizer, now)
  assert optimizer.acknowledge_application(trial_id=trial.trial_id, target_sequence=trial.target_sequence,
    phase='trial', target_sha256=trial.target_sha256, applied_full30=trial.target,
    control_ns=now, binding=trial.binding, now_ns=now, status='applied', confirmed_target_sha256='')
  row = episode(optimizer, 100, 'ff_gain_right', profile=trial.target_sha256, error=0.001,
    start=now + 1_000_000_000)
  row = replace(row, normalization_accel=max(0.1, row.goal_rms))
  refresh(optimizer, row.end_ns)
  assert optimizer.observe_episode(row, now_ns=row.end_ns)
  assert len(optimizer.future) == 1
  state, before, future, settled = optimizer.offer_state, optimizer.before, tuple(optimizer.future), optimizer.settled_ns
  acknowledge_restore(actor, history, row.end_ns + 1_000_000)
  assert optimizer.offer_state is state and optimizer.before == before
  assert tuple(optimizer.future) == future and optimizer.settled_ns == settled
  assert optimizer.accepted['ff_gain_right'] == 0.0  # One future row cannot confirm.


@pytest.mark.parametrize('change', ('status', 'binding', 'trial', 'target', 'hash', 'future', 'stale'))
def test_duplicate_ack_keeps_existing_application_guards(change):
  actor, history = restored_actor()
  now = 2_000_000_000
  application = restore_messages(actor, history, now)
  edits = {
    'status': {'status': 'applied'},
    'binding': {'binding': replace(application.binding, source_epoch='9' * 64)},
    'trial': {'trial_id': '9' * 64},
    'target': {'applied': tuple((name, 0.0) for name in NAMES)},
    'hash': {'applied_sha256': '9' * 64},
    'future': {'control_ns': now + 1},
    'stale': {'control_ns': now - 100_000_001},
  }
  previous = actor.ingress.latest_restore_offer
  accepted = dict(actor.ingress.optimizer.accepted)
  assert not history.acknowledge(actor, replace(application, **edits[change]), now_ns=now)
  assert actor.ingress.latest_restore_offer is previous and actor.ingress.optimizer.accepted == accepted


def test_duplicate_ack_still_requires_a_current_response_witness():
  actor, history = restored_actor()
  now = 2_000_000_000
  application = restore_messages(actor, history, now)
  expired = now + 100_000_001
  with pytest.raises(ValueError):
    validate_response_witness(history.offer.response_witness, application.binding, expired)
  with pytest.raises(ValueError):
    history.acknowledge(actor, replace(application, control_ns=expired), now_ns=expired)


@pytest.mark.parametrize('field', ('history_scope', 'history_trial_id'))
def test_different_history_cannot_replace_the_acknowledged_restore(field):
  actor, history = restored_actor()
  now = 2_000_000_000
  application = restore_messages(actor, history, now)
  history.offer = replace(history.offer, **{field: '9' * 64}).validate()
  if field == 'history_trial_id':
    application = replace(application, trial_id=history.offer.history_trial_id)
  previous = actor.ingress.latest_restore_offer
  assert not history.acknowledge(actor, application, now_ns=now)
  assert actor.ingress.latest_restore_offer is previous


def test_old_restore_cannot_replace_a_later_accepted_map():
  actor, history = restored_actor()
  optimizer = actor.ingress.optimizer
  # Declared later-acceptance state; this does not claim a physical comparison.
  optimizer.accepted['ff_gain_right'] = SOURCE_EXPERIMENT_STEP
  later = dict(optimizer.accepted)
  application = restore_messages(actor, history, 2_000_000_000)
  assert not history.acknowledge(actor, application, now_ns=application.control_ns)
  assert optimizer.accepted == later


def test_new_ingress_adopts_history_independently():
  first, _ = restored_actor()
  second, _ = restored_actor()
  assert second.ingress is not first.ingress
  assert second.ingress.optimizer.accepted == first.ingress.optimizer.accepted
  assert not second.ingress.optimizer.baseline and not second.ingress.optimizer.future
