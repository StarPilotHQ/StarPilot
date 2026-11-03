"""Inert numerical continuation contracts; not native or road proof."""

from dataclasses import FrozenInstanceError, replace
import unittest

from openpilot.starpilot.flm.live_tracking import _matches_demand, _demand
from openpilot.starpilot.flm.tests.test_live_tracking import episode
from openpilot.starpilot.flm.tests.test_source_study_gaps import active_study
from openpilot.starpilot.flm.tests.test_tracking_source_context import owner, refresh, source_ack


def pause():
  o, _now = active_study()
  original = o.offer_state.copy()
  scores = tuple(o.before), tuple(o.future)
  incoming = replace(episode(o, 40, 'center_taper_max', kind='subtle', profile=o._hash(original['target'])), normalization_accel=0.1)
  refresh(o, incoming.end_ns)
  assert not o.observe_episode(incoming, now_ns=incoming.end_ns)
  assert o.reason == 'trial_input_regime_ended'
  return o, original, scores, incoming


def baseline_ack(o, now):
  refresh(o, now)
  offer = o.offer(now)
  assert offer is not None and offer.phase == 'rollback'
  assert source_ack(o, offer, now + 1_000_000)
  return now + 1_000_000


def trigger(o, index, *, start=None, profile=None, **changes):
  return replace(episode(o, index, 'ff_gain_right', start=start, profile=profile), normalization_accel=0.5, **changes)


class StagedSourceStudy(unittest.TestCase):
  def test_regime_change_keeps_only_completed_inert_rows_and_original_plan(self):
    o, original, scores, incoming = pause()
    intent, before, future = o.paused_intent
    assert (before, future) == scores and len(future) == 2
    assert incoming not in before + future
    assert intent == original and intent['required_episodes'] == 3
    assert not o.before and not o.future and o.settled_ns is None
    assert o.offer_state['phase'] == 'rollback' and o.offer_state['target'] == original['baseline']
    assert not o.offer_state['accepted'] and not any(o.accepted.values())
    with self.assertRaises(FrozenInstanceError):
      future[0].error_rms = 0.0

  def test_baseline_ack_and_unmatched_window_do_not_reapply_target(self):
    o, _original, scores, incoming = pause()
    first_ack = baseline_ack(o, incoming.end_ns + 10_000_000)
    assert o.offer_state['accepted'] and o.offer_state['phase'] == 'rollback'
    assert o.paused_intent[1:] == scores and o.paused_baseline_ns == first_ack
    row = trigger(o, 42, direction=1)
    baseline_ack(o, row.end_ns - 10_000_000)
    assert o.paused_baseline_ns == first_ack
    assert not o.observe_episode(row, now_ns=row.end_ns)
    assert o.offer_state['phase'] == 'rollback' and not any(o.accepted.values())
    assert o.paused_intent[1:] == scores and not o.future

  def test_matching_whole_baseline_window_resumes_new_generation_without_scoring_it(self):
    o, original, scores, incoming = pause()
    baseline_ack(o, incoming.end_ns + 10_000_000)
    row = trigger(o, 42)
    baseline_ack(o, row.end_ns - 10_000_000)
    assert not o.observe_episode(row, now_ns=row.end_ns)
    resumed = o.offer_state
    assert resumed['phase'] == 'trial' and not resumed['published']
    assert resumed['trial_id'] != original['trial_id'] and resumed['target_sequence'] > original['target_sequence']
    for key in ('target', 'baseline', 'demand', 'started_ns', 'deadline_ns', 'alpha', 'critical', 'required_episodes', 'look_quotas'):
      assert resumed.get(key) == original.get(key)
    assert (tuple(o.before), tuple(o.future)) == scores and row not in o.before + tuple(o.future)
    assert o.settled_ns is None and o.paused_intent is None and not any(o.accepted.values())
    unacknowledged = trigger(o, 43, profile=o._hash(resumed['target']))
    assert not o.observe_episode(unacknowledged, now_ns=unacknowledged.end_ns)
    assert len(o.future) == 2 and o.offer_state['phase'] == 'trial'
    refresh(o, unacknowledged.end_ns)
    offer = o.offer(unacknowledged.end_ns)
    assert source_ack(o, offer, unacknowledged.end_ns + 10_000_000)
    assert o.settled_ns == unacknowledged.end_ns + 10_000_000
    overlapping = trigger(o, 44, profile=offer.target_sha256, start=o.settled_ns)
    assert not o.observe_episode(overlapping, now_ns=overlapping.end_ns)
    assert len(o.future) == 2 and not any(o.accepted.values())

  def test_queued_window_starting_before_baseline_ack_is_not_resume_evidence(self):
    o, _original, scores, incoming = pause()
    ack = baseline_ack(o, incoming.end_ns + 10_000_000)
    queued = trigger(o, 100, start=ack - 1_000_000)
    baseline_ack(o, queued.end_ns - 10_000_000)
    assert not o.observe_episode(queued, now_ns=queued.end_ns)
    assert o.offer_state['phase'] == 'rollback' and o.paused_intent[1:] == scores

  def test_stale_baseline_ack_cannot_resume_even_matching_window(self):
    o, _original, scores, incoming = pause()
    baseline_ack(o, incoming.end_ns + 10_000_000)
    row = trigger(o, 42)
    refresh(o, row.end_ns)
    assert not o.observe_episode(row, now_ns=row.end_ns)
    assert o.offer_state['phase'] == 'rollback' and o.paused_intent[1:] == scores

  def test_unscored_input_window_cannot_become_a_transitive_new_anchor(self):
    o, original, scores, incoming = pause()
    baseline_ack(o, incoming.end_ns + 10_000_000)
    intermediate = trigger(o, 42, speed_low=21.5, speed_high=22.5)
    assert _matches_demand(original['demand'], intermediate)
    refresh(o, intermediate.end_ns)  # Original baseline ACK is stale.
    assert not o.observe_episode(intermediate, now_ns=intermediate.end_ns)
    different = trigger(o, 43, speed_low=23.1, speed_high=24.1)
    assert _matches_demand(_demand(intermediate), different)
    assert not _matches_demand(original['demand'], different)
    baseline_ack(o, different.end_ns - 10_000_000)
    assert not o.observe_episode(different, now_ns=different.end_ns)
    assert o.paused_intent[0]['demand'] == original['demand'] and o.paused_intent[1:] == scores
    assert o.offer_state['phase'] == 'rollback'

  def test_context_profile_and_deadline_changes_discard_inert_study(self):
    for change in ('manual', 'reset', 'binding', 'model', 'controller', 'baseline', 'deadline'):
      with self.subTest(change=change):
        o, original, _scores, incoming = pause()
        now = baseline_ack(o, incoming.end_ns + 10_000_000)
        if change in ('manual', 'reset'):
          o.interrupt('manual_override' if change == 'manual' else 'source_configuration_changed', context_changed=change == 'reset')
        else:
          if change == 'binding':
            o.binding = replace(o.binding, baseline_revision='9' * 64)
          elif change == 'model':
            o.certificate = replace(o.certificate, model_revision='9' * 64)
          elif change == 'controller':
            o.domain = 'torque_pid'
          elif change == 'baseline':
            o.accepted['ff_gain_left'] = 0.001
          else:
            now = original['deadline_ns']
          o._check_paused_demand(now)
        assert o.paused_intent is None and o.paused_demand_context is None and o.paused_baseline_ns == 0

  def test_unacknowledged_target_cannot_create_staged_study(self):
    o, _now = active_study()
    o.settled_ns = None
    o._rollback('trial_input_regime_ended')
    assert o.paused_intent is None and o.paused_demand_context is None

  def test_actual_run19_three_plus_three_rejection_remains_terminal(self):
    o = owner()
    for i in range(5):
      row = replace(episode(o, i, 'highway_center_taper_max', kind='subtle'), normalization_accel=0.1)
      refresh(o, row.end_ns)
      o.observe_episode(row, now_ns=row.end_ns)
    offer = o.offer(row.end_ns)
    assert offer is not None and o.offer_state['required_episodes'] == 3
    assert source_ack(o, offer, row.end_ns + 10_000_000)
    old = (0.04533781831071211, 0.05451536049350658, 0.04055138232722946)
    new = (0.037869039309016156, 0.039513578311132, 0.05890910591648849)
    o.before = tuple(replace(row, error_rms=error) for row, error in zip(o.before, old, strict=True))
    for i, error in enumerate(new):
      row = replace(episode(o, 20 + i, 'highway_center_taper_max', kind='subtle', profile=offer.target_sha256, error=error), normalization_accel=0.1)
      refresh(o, row.end_ns)
      assert o.observe_episode(row, now_ns=row.end_ns)  # Admission is separate from the comparison decision.
    assert o.reason == 'matched_tracking_not_consistently_improved' and o.offer_state['phase'] == 'rollback'
    assert o.paused_intent is None and o.paused_demand_context is None
    assert len(o.future) == 3 and not any(o.accepted.values())


def test_legacy_model_regime_end_remains_terminal():
  from openpilot.starpilot.flm.tests.test_live_tracking import optimizer, ready, acknowledge

  o = optimizer()
  _state, now = ready(o, 'ff_gain_right')
  offer = o.offer(now)
  assert offer is not None and acknowledge(o, offer)
  o._rollback('trial_input_regime_ended')
  assert o.paused_intent is None and o.paused_demand_context is None
  assert o.offer_state['phase'] == 'rollback' and not any(o.accepted.values())


def test_old_generation_ack_cannot_settle_resumed_study():
  o, _original, _scores, incoming = pause()
  old_offer = o.offer(incoming.end_ns)
  baseline_ack(o, incoming.end_ns + 10_000_000)
  row = trigger(o, 42)
  baseline_ack(o, row.end_ns - 10_000_000)
  assert not o.observe_episode(row, now_ns=row.end_ns)
  assert o.offer_state['phase'] == 'trial'
  assert not source_ack(o, old_offer, row.end_ns + 1_000_000)
  assert o.settled_ns is None and len(o.future) == 2

if __name__ == '__main__':
  unittest.main()
