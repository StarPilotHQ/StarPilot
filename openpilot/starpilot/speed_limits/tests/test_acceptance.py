import itertools
import math
import unittest
from dataclasses import FrozenInstanceError, replace
from typing import Any, cast

from openpilot.starpilot.speed_limits.acceptance import (
  CONFIRMATION_NS, AcceptedLimit, ActionKind, AdoptRequest, Authority, Candidate, DriverAction,
  IdentityKind, LongitudinalOwner, Mode, Observation, ObservationIdentity, ObservationKind, Policy, State, new_session, step,
)

NS = 1_000_000_000


def geographic(value: str) -> ObservationIdentity:
  return ObservationIdentity(IdentityKind.GEOGRAPHIC, value=value)


HIGH = Candidate("dashboard", geographic("road-1"), 29.0576)
LOW = Candidate("dashboard", geographic("road-2"), 20.1168)
ACTIVE = Authority(Mode.LONGITUDINAL_ONLY, LongitudinalOwner.SYSTEM, False, True, False, False)
PAUSED = replace(ACTIVE, longitudinal_active=False)
DISENGAGED = replace(PAUSED, fully_disengaged=True)
ABSENT = Observation(ObservationKind.ABSENT)
DEFAULT_POLICY = Policy()


def valid(candidate: Candidate) -> Observation:
  return Observation(ObservationKind.VALID, candidate)


def saved(candidate: Candidate = HIGH) -> State:
  return replace(new_session("drive", AcceptedLimit(candidate, "previous-drive", 0)), received_speed_mps=candidate.speed_mps)


class TestAcceptance(unittest.TestCase):
  def test_set_alignment_requires_an_explicit_boolean(self):
    for malformed in (None, 0, 1, 'yes'):
      with self.subTest(value=malformed):
        result = step(saved(), valid(LOW), ACTIVE, Policy(), now_ns=0, set_speed_matches=cast(Any, malformed))
        self.assertTrue(result.errors)
        self.assertIsNone(result.history_write)
        self.assertIsNone(result.control_target_mps)

  def test_manual_confirmation_requires_longitudinal_engagement_for_every_source(self):
    lateral_only = Authority(Mode.LATERAL_ONLY, LongitudinalOwner.NONE, True, False, False, False)
    for source, authority in itertools.product(('dashboard', 'map', 'vision', 'online'), (DISENGAGED, lateral_only, PAUSED)):
      with self.subTest(source=source, authority=authority):
        candidate = replace(LOW, source=source)
        inactive = step(saved(), valid(candidate), authority, Policy(), now_ns=0)
        self.assertIsNone(inactive.state.pending)
        self.assertFalse(inactive.state.timer_running)
        self.assertEqual(inactive.display_candidate, candidate)
        self.assertEqual(inactive.state.accepted, saved().accepted)
        self.assertIsNone(inactive.history_write)
        self.assertIsNone(inactive.control_target_mps)
        engaged = step(inactive.state, valid(candidate), ACTIVE, Policy(), now_ns=20 * NS)
        self.assertIsNotNone(engaged.state.pending)
        self.assertEqual(engaged.state.pending.active_elapsed_ns, 0)

  def test_disengagement_cancels_confirmation_without_declining_or_consuming_an_old_action(self):
    pending = self.pending()
    action = DriverAction('drive', 1, pending.state.pending.decision_id, ActionKind.ACCEPT)
    inactive = step(pending.state, valid(LOW), DISENGAGED, Policy(), now_ns=5 * NS, action=action)
    self.assertIsNone(inactive.state.pending)
    self.assertFalse(inactive.state.timer_running)
    self.assertFalse(inactive.state.rejected)
    self.assertFalse(inactive.action_receipt.consumed)
    self.assertEqual(inactive.state.accepted, pending.state.accepted)
    resumed = step(inactive.state, valid(LOW), ACTIVE, Policy(), now_ns=20 * NS,
                   action=replace(action, sequence_id=2))
    self.assertIsNotNone(resumed.state.pending)
    self.assertNotEqual(resumed.state.pending.decision_id, pending.state.pending.decision_id)
    self.assertEqual(resumed.state.pending.active_elapsed_ns, 0)
    self.assertFalse(resumed.action_receipt.consumed)
    self.assertEqual(resumed.state.accepted, pending.state.accepted)

  def test_accelerator_override_keeps_an_engaged_confirmation(self):
    pending = self.pending()
    overriding = replace(ACTIVE, longitudinal_active=False, accelerator_override=True)
    held = step(pending.state, valid(LOW), overriding, Policy(), now_ns=NS)
    self.assertEqual(held.state.pending.decision_id, pending.state.pending.decision_id)
    accepted = self.act(held.state, ActionKind.ACCEPT, authority=overriding, now_ns=2 * NS)
    self.assertTrue(accepted.action_receipt.consumed)
    self.assertEqual(accepted.control_target_mps, LOW.speed_mps)

  def test_sources_share_confirmation_across_disengage_and_source_loss(self):
    for source, confirmation in itertools.product(('dashboard', 'map', 'vision', 'online'), (False, True)):
      policy = Policy(confirmation, confirmation)
      candidate = replace(LOW, source=source)
      first = step(new_session('drive'), valid(candidate), DISENGAGED, policy, now_ns=0)
      self.assertIsNone(first.state.pending)
      self.assertEqual(first.history_write is None, confirmation)
      self.assertIsNone(first.control_target_mps)
      ready = step(first.state, valid(candidate), ACTIVE, policy, now_ns=NS)
      accepted = self.act(ready.state, ActionKind.ACCEPT, now_ns=4 * NS, policy=policy) if confirmation else ready
      self.assertEqual(accepted.control_target_mps, candidate.speed_mps)
      lost = step(accepted.state, ABSENT, ACTIVE, policy, now_ns=5 * NS)
      self.assertEqual(lost.control_target_mps, candidate.speed_mps)
      returned = step(lost.state, valid(candidate), ACTIVE, policy, now_ns=6 * NS)
      self.assertIsNone(returned.state.pending)
      self.assertIsNone(returned.history_write)

  def pending(self, candidate=LOW, authority=ACTIVE, policy=DEFAULT_POLICY, state=None, now_ns=0):
    decision = step(saved() if state is None else state, valid(candidate), authority, policy, now_ns=now_ns)
    self.assertEqual(decision.errors, ())
    self.assertIsNotNone(decision.state.pending)
    return decision

  def act(self, state, kind, *, now_ns=NS, sequence=0, authority=ACTIVE, policy=DEFAULT_POLICY):
    self.assertIsNotNone(state.pending)
    action = DriverAction(state.session_id, sequence, state.pending.decision_id, kind)
    return step(state, state.observation, authority, policy, now_ns=now_ns, action=action)

  def adopt_request(self, state, sequence=0):
    self.assertIsNotNone(state.presentation)
    return AdoptRequest(state.session_id, sequence, state.presentation.presentation_id, state.presentation.candidate)

  def test_reject_then_source_loss_never_activates_rejected_limit(self):
    policy = Policy()
    first = step(new_session("drive"), valid(HIGH), ACTIVE, policy, now_ns=0)
    initial = self.act(first.state, ActionKind.ACCEPT, policy=policy, now_ns=NS)
    self.assertEqual(initial.history_write.candidate, HIGH)
    proposed = self.pending(state=initial.state, policy=policy, now_ns=2 * NS)
    self.assertEqual(proposed.control_target_mps, HIGH.speed_mps)
    rejected = self.act(proposed.state, ActionKind.REJECT, policy=policy, now_ns=3 * NS, sequence=1)
    lost = step(rejected.state, ABSENT, ACTIVE, policy, now_ns=4 * NS)
    returned = step(lost.state, valid(LOW), ACTIVE, policy, now_ns=5 * NS)
    for decision in (rejected, lost, returned):
      self.assertEqual(decision.control_target_mps, HIGH.speed_mps)
      self.assertEqual(decision.state.accepted, initial.history_write)
      self.assertIsNone(decision.state.pending)
      self.assertIsNone(decision.history_write)
      self.assertEqual(decision.state.rejected, LOW)
    self.assertTrue(rejected.action_receipt.consumed)
    self.assertEqual(returned.basis, "rejected")

  def test_source_gaps_use_the_previous_accepted_limit_without_inventing_an_observation(self):
    for source, kind in itertools.product(('dashboard', 'map', 'vision', 'online', 'future-source'),
        (ObservationKind.ABSENT, ObservationKind.UNKNOWN, ObservationKind.STALE)):
      with self.subTest(source=source, kind=kind):
        prior = saved(replace(HIGH, source=source))
        policy = Policy()
        result = step(prior, Observation(kind), ACTIVE, policy, now_ns=300 * NS)
        self.assertEqual(result.control_target_mps, prior.accepted.candidate.speed_mps)
        self.assertEqual(result.state.accepted, prior.accepted)
        self.assertEqual(result.state.observation.kind, kind)
        self.assertIsNone(result.display_candidate)
        self.assertIsNone(result.history_write)
        empty = step(new_session("drive"), Observation(kind), ACTIVE, policy, now_ns=300 * NS)
        self.assertIsNone(empty.control_target_mps)

  def test_confirmed_vision_is_retained_until_changed_limit_is_accepted(self):
    policy = Policy()
    first = replace(HIGH, source='vision')
    previous = saved(first)
    for kind in (ObservationKind.ABSENT, ObservationKind.UNKNOWN, ObservationKind.STALE):
      lost = step(previous, Observation(kind), ACTIVE, policy, now_ns=NS)
      self.assertEqual(lost.control_target_mps, first.speed_mps)
      self.assertEqual(lost.state.accepted, previous.accepted)
    next_sign = replace(LOW, source='vision')
    pending = step(lost.state, valid(next_sign), ACTIVE, policy, now_ns=2 * NS)
    self.assertEqual(pending.control_target_mps, first.speed_mps)
    self.assertIsNotNone(pending.state.pending)
    recovered = step(pending.state, valid(first), ACTIVE, policy, now_ns=3 * NS)
    self.assertIsNone(recovered.state.pending)
    self.assertFalse(step(recovered.state, valid(first), ACTIVE, policy, now_ns=4 * NS).errors)
    for kind in ActionKind:
      action = DriverAction('drive', 1, pending.state.pending.decision_id, kind)
      acted = step(pending.state, valid(next_sign), ACTIVE, policy, now_ns=3 * NS, action=action)
      target = next_sign.speed_mps if kind is ActionKind.ACCEPT else first.speed_mps
      gap = step(acted.state, Observation(ObservationKind.UNKNOWN), ACTIVE, policy, now_ns=300 * NS)
      self.assertEqual(gap.control_target_mps, target)
      self.assertEqual(gap.state.accepted.candidate.speed_mps, target)
    timeout = step(pending.state, valid(next_sign), ACTIVE, policy, now_ns=33 * NS)
    self.assertEqual(timeout.basis, 'timeout_reject')
    self.assertEqual(timeout.control_target_mps, first.speed_mps)

  def test_source_gaps_keep_the_decision_presentation_and_original_deadline(self):
    for kind in (ObservationKind.UNKNOWN, ObservationKind.STALE, ObservationKind.ABSENT):
      with self.subTest(kind=kind):
        before = self.pending()
        missing = step(before.state, Observation(kind), ACTIVE, Policy(), now_ns=NS)
        after = self.pending(state=missing.state, now_ns=2 * NS)
        self.assertEqual(missing.state.pending.decision_id, before.state.pending.decision_id)
        self.assertEqual(missing.state.presentation, before.state.presentation)
        self.assertEqual(missing.control_target_mps, HIGH.speed_mps)
        self.assertEqual(before.state.pending.decision_id, after.state.pending.decision_id)
        self.assertEqual(after.state.pending.active_elapsed_ns, 2 * NS)
        late = DriverAction("drive", 1, before.state.pending.decision_id, ActionKind.ACCEPT)
        result = step(missing.state, valid(LOW), ACTIVE, Policy(), now_ns=3 * NS, action=late)
        self.assertTrue(result.action_receipt.consumed)
        self.assertEqual(result.action_receipt.status, "accept")
        self.assertEqual(result.state.accepted.candidate, LOW)
        expired = step(after.state, Observation(kind), ACTIVE, Policy(), now_ns=CONFIRMATION_NS)
        self.assertEqual(expired.basis, 'timeout_reject')
        self.assertIsNone(expired.state.pending)
        self.assertEqual(expired.control_target_mps, HIGH.speed_mps)
        returned = step(expired.state, valid(LOW), ACTIVE, Policy(), now_ns=CONFIRMATION_NS + NS)
        self.assertEqual(returned.basis, 'rejected')

  def test_retained_confirmation_can_be_accepted_or_rejected_without_a_source(self):
    before = self.pending()
    for kind in (ActionKind.ACCEPT, ActionKind.REJECT):
      with self.subTest(kind=kind):
        action = DriverAction('drive', 1, before.state.pending.decision_id, kind)
        result = step(before.state, ABSENT, ACTIVE, Policy(), now_ns=NS, action=action)
        expected = LOW if kind is ActionKind.ACCEPT else HIGH
        self.assertEqual(result.control_target_mps, expected.speed_mps)
        self.assertEqual(result.state.accepted.candidate, expected)
        self.assertTrue(result.action_receipt.consumed)
        self.assertIsNone(result.state.pending)
        self.assertIsNone(result.state.presentation)
        self.assertEqual(result.state.observation.kind, ObservationKind.ABSENT)
        held = step(result.state, ABSENT, ACTIVE, Policy(), now_ns=2 * NS)
        self.assertFalse(held.errors)
        self.assertEqual(held.control_target_mps, expected.speed_mps)
    inactive = step(before.state, ABSENT, PAUSED, Policy(), now_ns=NS)
    self.assertIsNone(inactive.state.pending)

  def test_source_gap_actions_respect_the_original_deadline(self):
    for kind, late in itertools.product(ActionKind, (False, True)):
      with self.subTest(kind=kind, late=late):
        before = self.pending()
        action = DriverAction('drive', 1, before.state.pending.decision_id, kind)
        result = step(before.state, ABSENT, ACTIVE, Policy(), now_ns=CONFIRMATION_NS + int(late), action=action)
        self.assertEqual(result.action_receipt.consumed, not late)
        self.assertEqual(result.basis, 'timeout_reject' if late else f'driver_{kind.value}')
        self.assertEqual(result.control_target_mps, LOW.speed_mps if kind is ActionKind.ACCEPT and not late else HIGH.speed_mps)
        self.assertIsNone(result.state.pending)

  def test_recorded_vision_expiry_burst_is_one_decision_or_one_automatic_adoption(self):
    # Route 00000048: decisions 52, 53 and 54 were the same 35 mph episode.
    previous = replace(LOW, source='vision', speed_mps=20 * .44704)
    reading = replace(HIGH, source='vision', speed_mps=35 * .44704)
    trace = ((0, True), (1_301_000_754, False), (1_700_337_698, True),
             (3_000_507_210, False), (3_449_913_699, True))
    for manual in (False, True):
      with self.subTest(manual=manual):
        state = new_session('drive', AcceptedLimit(previous, 'drive', 0))
        policy = Policy(confirm_lower=manual, confirm_higher=manual)
        ids, writes = [], []
        for timestamp, available in trace:
          result = step(state, valid(reading) if available else Observation(ObservationKind.STALE),
                        ACTIVE, policy, now_ns=timestamp)
          self.assertFalse(result.errors)
          state = result.state
          if state.pending is not None:
            ids.append(state.pending.decision_id)
          if result.history_write is not None:
            writes.append(result.history_write)
          if not available:
            self.assertEqual(result.control_target_mps, result.state.accepted.candidate.speed_mps)
        self.assertEqual(len(set(ids)), 1 if manual else 0)
        self.assertEqual(len(writes), 0 if manual else 1)

  def test_provider_changes_use_the_same_immediate_adoption_or_confirmation_flow(self):
    mapped = replace(HIGH, source='map')
    automatic = Policy(confirm_lower=False, confirm_higher=False)
    for source, speed, manual in itertools.product(('map', 'dashboard', 'vision'),
        (LOW.speed_mps, HIGH.speed_mps + 5 * .44704), (False, True)):
      with self.subTest(source=source, speed=speed, manual=manual):
        first = step(new_session('drive'), valid(mapped), ACTIVE, automatic, now_ns=0)
        reading = replace(LOW, source=source, speed_mps=speed)
        policy = Policy(confirm_lower=manual, confirm_higher=manual)
        result = step(first.state, valid(reading), ACTIVE, policy, now_ns=NS)
        self.assertFalse(result.errors)
        self.assertEqual(result.basis, 'pending' if manual else 'accepted')
        self.assertEqual(result.control_target_mps, mapped.speed_mps if manual else speed)
        self.assertEqual(result.history_write is not None, not manual)
        if manual:
          self.assertEqual(result.state.accepted, first.state.accepted)
          self.assertEqual(result.state.pending.active_elapsed_ns, 0)
          expired = step(result.state, valid(reading), ACTIVE, policy, now_ns=NS + CONFIRMATION_NS)
          self.assertEqual(expired.basis, 'timeout_reject')
          self.assertEqual(expired.control_target_mps, mapped.speed_mps)
        else:
          self.assertIsNone(result.state.pending)
          self.assertEqual(result.state.accepted.candidate, reading)

  def test_explicit_adoption_of_a_replacement_still_requires_its_current_presentation(self):
    mapped = replace(LOW, source='map', speed_mps=30 * .44704)
    vision = replace(HIGH, source='vision', speed_mps=35 * .44704)
    automatic = Policy(confirm_lower=False, confirm_higher=False)
    first = step(new_session('drive'), valid(mapped), ACTIVE, automatic, now_ns=0)
    shown = step(first.state, valid(vision), ACTIVE, Policy(), now_ns=NS)
    request = AdoptRequest('drive', 1, shown.state.presentation.presentation_id, vision)
    for invalid in (replace(request, session_id='foreign'), replace(request, presentation_id=request.presentation_id + 1)):
      held = step(shown.state, valid(vision), ACTIVE, Policy(), now_ns=NS + 1, adopt=invalid)
      self.assertEqual(held.basis, 'pending')
      self.assertFalse(held.adoption_receipt.consumed)
      self.assertIsNone(held.history_write)
      self.assertEqual(held.state.accepted, first.state.accepted)
    explicit = step(shown.state, valid(vision), ACTIVE, Policy(), now_ns=NS + 1, adopt=request)
    self.assertTrue(explicit.adoption_receipt.consumed)
    self.assertEqual(explicit.state.accepted.candidate, vision)

  def test_same_received_speed_does_not_prompt_again_for_a_new_zone(self):
    rejected = self.act(self.pending().state, ActionKind.REJECT)
    next_zone = replace(LOW, observation_identity=geographic('road-3'))
    result = step(rejected.state, valid(next_zone), ACTIVE, Policy(), now_ns=2 * NS)
    self.assertIsNone(result.state.pending)
    self.assertEqual(result.basis, 'rejected')

  def test_same_received_speed_does_not_prompt_again_on_source_switch(self):
    rejected = self.act(self.pending().state, ActionKind.REJECT)
    mapped = replace(LOW, source='map', observation_identity=geographic('map-road'))
    result = step(rejected.state, valid(mapped), ACTIVE, Policy(), now_ns=2 * NS)
    self.assertEqual(result.basis, 'rejected')
    self.assertIsNone(result.state.pending)
    self.assertEqual(result.control_target_mps, HIGH.speed_mps)

  def test_changed_pending_candidate_starts_new_clock_and_id(self):
    first = self.pending()
    progressed = step(first.state, valid(LOW), ACTIVE, Policy(), now_ns=29 * NS)
    changed = self.pending(candidate=replace(LOW, speed_mps=19.0), state=progressed.state, now_ns=40 * NS)
    self.assertNotEqual(changed.state.pending.decision_id, first.state.pending.decision_id)
    self.assertEqual(changed.state.pending.active_elapsed_ns, 0)

  def test_confirmation_expires_at_thirty_seconds(self):
    pending = self.pending()
    ten_seconds = step(pending.state, valid(LOW), ACTIVE, Policy(), now_ns=10 * NS)
    self.assertIsNotNone(ten_seconds.state.pending)
    before = step(ten_seconds.state, valid(LOW), ACTIVE, Policy(), now_ns=30 * NS - 1)
    self.assertIsNotNone(before.state.pending)
    expired = step(before.state, valid(LOW), ACTIVE, Policy(), now_ns=30 * NS)
    self.assertEqual(expired.basis, "timeout_reject")
    self.assertEqual(expired.state.rejected, LOW)
    self.assertIsNone(expired.state.pending)
    self.assertIsNone(expired.history_write)
    self.assertEqual(expired.control_target_mps, HIGH.speed_mps)

  def test_disengagement_cancels_confirmation_instead_of_timing_out(self):
    start = self.pending()
    pause = step(start.state, valid(LOW), PAUSED, Policy(), now_ns=5 * NS)
    self.assertIsNone(pause.state.pending)
    inactive = step(pause.state, valid(LOW), PAUSED, Policy(), now_ns=CONFIRMATION_NS)
    self.assertFalse(inactive.state.rejected)
    self.assertIsNone(inactive.control_target_mps)
    resumed = self.pending(state=inactive.state, now_ns=CONFIRMATION_NS + NS)
    self.assertNotEqual(resumed.state.pending.decision_id, start.state.pending.decision_id)
    self.assertEqual(resumed.state.pending.active_elapsed_ns, 0)
    expired = step(resumed.state, valid(LOW), ACTIVE, Policy(), now_ns=2 * CONFIRMATION_NS + NS)
    self.assertEqual(expired.basis, 'timeout_reject')
    self.assertEqual(expired.state.accepted.candidate, HIGH)

  def test_no_implicit_ttl_or_maximum_active_gap(self):
    # Freshness belongs to the adapter: an explicitly valid active interval counts.
    start = self.pending()
    result = step(start.state, valid(LOW), ACTIVE, Policy(), now_ns=1000 * NS)
    self.assertEqual(result.basis, "timeout_reject")

  def test_action_at_deadline_precedes_automatic_timeout(self):
    for kind in ActionKind:
      with self.subTest(kind=kind):
        result = self.act(self.pending().state, kind, now_ns=CONFIRMATION_NS)
        self.assertEqual(result.basis, "driver_accept" if kind is ActionKind.ACCEPT else "driver_reject")
        self.assertTrue(result.action_receipt.consumed)

  def test_first_and_changed_limits_adopt_immediately_or_require_confirmation(self):
    for confirmation in (False, True):
      policy = Policy(confirmation, confirmation)
      for prior, candidate in itertools.product((new_session('drive'), saved()), (LOW, replace(HIGH, speed_mps=31.))):
        with self.subTest(confirmation=confirmation, prior=prior.accepted, candidate=candidate):
          first = step(prior, valid(candidate), ACTIVE, policy, now_ns=0)
          self.assertEqual(first.state.pending is not None, confirmation)
          if confirmation:
            self.assertFalse(first.state.pending.automatic)
            self.assertEqual(first.state.accepted, prior.accepted)
            self.assertIsNone(first.history_write)
          else:
            self.assertEqual(first.state.accepted.candidate, candidate)
            self.assertEqual(first.history_write, first.state.accepted)
            self.assertEqual(first.control_target_mps, candidate.speed_mps)
            repeated = step(first.state, valid(candidate), ACTIVE, policy, now_ns=1)
            self.assertIsNone(repeated.history_write)

  def test_unchanged_speed_keeps_approval_without_silently_accepting_new_identity(self):
    for toward in (0.0, math.inf):
      near = replace(HIGH, speed_mps=math.nextafter(HIGH.speed_mps, toward))
      result = self.pending(candidate=near)
      self.assertNotEqual(result.state.pending.candidate.speed_mps, HIGH.speed_mps)
    same_speed = replace(HIGH, source="map", observation_identity=geographic("mapped-road"))
    result = step(saved(), valid(same_speed), ACTIVE, Policy(), now_ns=0)
    self.assertIsNone(result.state.pending)
    self.assertIsNone(result.history_write)
    self.assertEqual(result.state.accepted.candidate, HIGH)
    unchanged = step(result.state, valid(same_speed), ACTIVE, Policy(), now_ns=NS)
    self.assertIsNone(unchanged.history_write)
    automatic = step(saved(), valid(same_speed), ACTIVE, Policy(False, False), now_ns=0)
    self.assertIsNone(automatic.history_write)

  def test_disengaged_automatic_adoption_is_immediate_without_commanding_speed(self):
    ready = step(saved(), valid(LOW), DISENGAGED, Policy(False, False), now_ns=0)
    self.assertEqual(ready.state.accepted.candidate, LOW)
    self.assertIsNotNone(ready.history_write)
    self.assertIsNone(ready.control_target_mps)

  def test_automatic_adoption_still_waits_for_authority_then_adopts_without_delay(self):
    policy = Policy(False, False)
    pending = self.pending(authority=PAUSED, policy=policy)
    self.assertEqual(pending.state.accepted.candidate, HIGH)
    self.assertIsNone(pending.history_write)
    ready = step(pending.state, valid(LOW), ACTIVE, policy, now_ns=1)
    self.assertIsNone(ready.state.pending)
    self.assertEqual(ready.control_target_mps, LOW.speed_mps)

  def test_zero_active_axes_does_not_imply_fully_disengaged(self):
    result = step(saved(), valid(LOW), PAUSED, Policy(), now_ns=0)
    self.assertIsNone(result.state.pending)
    self.assertEqual(result.state.accepted.candidate, HIGH)
    self.assertIsNone(result.history_write)
    self.assertIsNone(result.control_target_mps)

  def test_modes_stock_owner_and_inactive_long_cannot_confirm_or_gain_control(self):
    contexts = [
      Authority(Mode.OFF, LongitudinalOwner.NONE, False, False, False, False),
      Authority(Mode.LATERAL_ONLY, LongitudinalOwner.SYSTEM, True, False, False, False),
      Authority(Mode.LATERAL_ONLY, LongitudinalOwner.STOCK, True, False, True, False),
      Authority(Mode.COMBINED, LongitudinalOwner.STOCK, True, False, True, False),
      Authority(Mode.LONGITUDINAL_ONLY, LongitudinalOwner.STOCK, False, False, True, False),
      PAUSED,
    ]
    for authority in contexts:
      with self.subTest(authority=authority):
        pending = self.pending()
        inactive = step(pending.state, valid(LOW), authority, Policy(), now_ns=NS)
        acted = self.act(pending.state, ActionKind.ACCEPT, authority=authority)
        lost = step(acted.state, ABSENT, authority, Policy(), now_ns=2 * NS)
        for decision in (inactive, acted, lost):
          self.assertIsNone(decision.control_target_mps)
          self.assertIsNone(decision.state.pending)
          self.assertIsNone(decision.history_write)
        self.assertFalse(acted.action_receipt.consumed)
        self.assertEqual(acted.state.accepted.candidate, HIGH)
        self.assertEqual(acted.state.last_action_sequence, 0)

  def test_combined_and_long_only_have_equal_longitudinal_decisions(self):
    for authority in (ACTIVE, replace(ACTIVE, mode=Mode.COMBINED, lateral_active=True)):
      pending = self.pending(authority=authority)
      accepted = self.act(pending.state, ActionKind.ACCEPT, authority=authority)
      self.assertEqual(accepted.control_target_mps, LOW.speed_mps)
      self.assertEqual(accepted.history_write.candidate, LOW)

  def test_all_authority_combinations_reject_contradictions(self):
    combinations = itertools.product(Mode, LongitudinalOwner, (False, True), (False, True), (False, True), (False, True))
    for mode, owner, lateral, longitudinal, stock, disengaged in combinations:
      authority = Authority(mode, owner, lateral, longitudinal, stock, disengaged)
      invalid = ((lateral and mode not in (Mode.LATERAL_ONLY, Mode.COMBINED)) or
                 (longitudinal and (mode not in (Mode.LONGITUDINAL_ONLY, Mode.COMBINED) or owner is not LongitudinalOwner.SYSTEM)) or
                 (stock and owner is not LongitudinalOwner.STOCK) or (disengaged and (lateral or longitudinal or stock)))
      with self.subTest(authority=authority):
        result = step(saved(), valid(LOW), authority, Policy(False, False), now_ns=0)
        self.assertEqual(bool(result.errors), bool(invalid))
        if invalid:
          self.assertIsNone(result.control_target_mps)
          self.assertIsNone(result.history_write)
        elif not longitudinal:
          self.assertIsNone(result.control_target_mps)

  def test_display_only_clears_pending_and_never_accepts_or_falls_back(self):
    pending = self.pending()
    action = DriverAction("drive", 1, pending.state.pending.decision_id, ActionKind.ACCEPT)
    for authority, observation in itertools.product((ACTIVE, DISENGAGED), (valid(LOW), ABSENT)):
      with self.subTest(authority=authority, observation=observation):
        result = step(pending.state, observation, authority, Policy(display_only=True), now_ns=NS, action=action)
        self.assertIsNone(result.state.pending)
        self.assertEqual(result.state.accepted, pending.state.accepted)
        self.assertIsNone(result.history_write)
        self.assertIsNone(result.control_target_mps)
        self.assertEqual(result.display_candidate, observation.candidate)
        self.assertFalse(result.action_receipt.consumed)
        self.assertEqual(result.state.last_action_sequence, 1)

  def test_repeated_and_out_of_order_actions_are_seen_but_not_consumed(self):
    pending = self.pending()
    first = self.act(pending.state, ActionKind.ACCEPT, sequence=10)
    self.assertTrue(first.action_receipt.consumed)
    seen = replace(pending.state, last_action_sequence=10)
    for sequence in (10, 9):
      result = self.act(seen, ActionKind.ACCEPT, sequence=sequence, now_ns=2 * NS)
      self.assertFalse(result.action_receipt.consumed)
      self.assertEqual(result.action_receipt.status, "already_seen")
      self.assertEqual(result.state.last_action_sequence, 10)
      self.assertEqual(result.state.accepted.candidate, HIGH)
    accepted = self.act(seen, ActionKind.ACCEPT, sequence=11, now_ns=2 * NS)
    self.assertTrue(accepted.action_receipt.consumed)
    self.assertEqual(accepted.state.accepted.candidate, LOW)

  def test_wrong_session_does_not_poison_sequence_watermark(self):
    pending = self.pending()
    foreign = DriverAction("other-drive", 10_000, pending.state.pending.decision_id, ActionKind.ACCEPT)
    result = step(pending.state, valid(LOW), ACTIVE, Policy(), now_ns=NS, action=foreign)
    self.assertEqual(result.action_receipt.status, "foreign_session")
    self.assertFalse(result.action_receipt.consumed)
    self.assertEqual(result.state.last_action_sequence, -1)
    accepted = self.act(result.state, ActionKind.ACCEPT, sequence=0, now_ns=2 * NS)
    self.assertTrue(accepted.action_receipt.consumed)

  def test_unrelated_action_is_acknowledged_and_watermarked_without_consuming_it(self):
    pending = self.pending()
    unrelated = DriverAction("drive", 3, 987, ActionKind.REJECT)
    result = step(pending.state, valid(LOW), ACTIVE, Policy(), now_ns=NS, action=unrelated)
    self.assertTrue(result.action_receipt.acknowledged)
    self.assertFalse(result.action_receipt.consumed)
    self.assertEqual(result.action_receipt.status, "different_decision")
    self.assertEqual(result.state.last_action_sequence, 3)
    self.assertIsNone(result.state.rejected)

  def test_event_cannot_accept_a_decision_not_previously_presented(self):
    action = DriverAction("drive", 0, 1, ActionKind.ACCEPT)
    result = step(saved(), valid(LOW), ACTIVE, Policy(), now_ns=0, action=action)
    self.assertEqual(result.state.pending.decision_id, 1)
    self.assertEqual(result.action_receipt.status, "not_presented")
    self.assertFalse(result.action_receipt.consumed)
    self.assertEqual(result.state.last_action_sequence, 0)
    self.assertEqual(result.state.accepted.candidate, HIGH)

  def test_invalid_input_fails_closed_and_valid_action_is_still_seen(self):
    pending = self.pending(now_ns=10 * NS)
    action = DriverAction("drive", 2, pending.state.pending.decision_id, ActionKind.ACCEPT)
    result = step(pending.state, valid(LOW), ACTIVE, Policy(), now_ns=9 * NS, action=action)
    self.assertTrue(result.errors)
    self.assertIsNone(result.control_target_mps)
    self.assertIsNone(result.state.pending)
    self.assertFalse(result.state.timer_running)
    self.assertEqual(result.state.last_timestamp_ns, 10 * NS)
    self.assertIsNone(result.history_write)
    self.assertFalse(result.action_receipt.consumed)
    self.assertEqual(result.action_receipt.status, "invalid_input")
    self.assertEqual(result.state.last_action_sequence, 2)
    recovered = self.pending(state=result.state, now_ns=20 * NS)
    self.assertEqual(recovered.state.pending.active_elapsed_ns, 0)

  def test_clock_requires_nonnegative_monotonic_integer_nanoseconds(self):
    invalid_times: tuple[Any, ...] = (-1, 0.0, True, math.nan, math.inf, "10", None)
    for now_ns in invalid_times:
      with self.subTest(now_ns=now_ns):
        result = step(saved(), valid(LOW), ACTIVE, Policy(), now_ns=now_ns)
        self.assertTrue(result.errors)
        self.assertIsNone(result.control_target_mps)
    pending = self.pending(now_ns=NS)
    same = step(pending.state, valid(LOW), ACTIVE, Policy(), now_ns=NS)
    self.assertEqual(same.state.pending.active_elapsed_ns, 0)

  def test_candidate_requires_finite_positive_speed_and_explicit_bounded_identity(self):
    malformed = [replace(LOW, speed_mps=value) for value in (0, -1, True, math.nan, math.inf, "20", None, 10 ** 1000)]
    malformed += [replace(LOW, source=""), replace(LOW, observation_identity=geographic("")), replace(LOW, source=" "),
                  replace(LOW, observation_identity=geographic("x" * 257)),
                  replace(LOW, source="a\nb")]
    for candidate in malformed:
      with self.subTest(candidate=candidate):
        result = step(saved(), valid(candidate), ACTIVE, Policy(False, False), now_ns=0)
        self.assertTrue(result.errors)
        self.assertIsNone(result.control_target_mps)
        self.assertIsNone(result.history_write)

  def test_observation_classification_is_explicit_and_consistent(self):
    malformed: list[Any] = [Observation(ObservationKind.VALID), Observation(cast(Any, "valid"), LOW), None]
    malformed += [Observation(kind, LOW) for kind in (ObservationKind.ABSENT, ObservationKind.UNKNOWN, ObservationKind.STALE)]
    for observation in malformed:
      with self.subTest(observation=observation):
        result = step(saved(), observation, ACTIVE, Policy(), now_ns=0)
        self.assertTrue(result.errors)
        self.assertIsNone(result.control_target_mps)

  def test_policy_authority_and_actions_reject_coerced_types(self):
    for field in ("confirm_lower", "confirm_higher", "display_only"):
      result = step(saved(), valid(LOW), ACTIVE, replace(Policy(), **{field: 1}), now_ns=0)
      self.assertTrue(result.errors)
    for authority in (replace(ACTIVE, mode="longitudinal_only"), replace(ACTIVE, owner="system"), replace(ACTIVE, fully_disengaged=0)):
      result = step(saved(), valid(LOW), authority, Policy(), now_ns=0)
      self.assertTrue(result.errors)
    pending = self.pending()
    action = DriverAction("drive", 1, pending.state.pending.decision_id, ActionKind.ACCEPT)
    for invalid in (replace(action, sequence_id=True), replace(action, sequence_id=-1), replace(action, decision_id=0),
                    replace(action, session_id=""), replace(action, kind="accept")):
      result = step(pending.state, valid(LOW), ACTIVE, Policy(), now_ns=NS, action=invalid)
      self.assertTrue(result.errors)
      self.assertIsNone(result.control_target_mps)
      self.assertIsNone(result.action_receipt)
      self.assertEqual(result.state.last_action_sequence, -1)

  def test_invalid_state_never_returns_a_control_target(self):
    malformed = [replace(saved(), session_id=""), replace(saved(), rejected=set()), replace(saved(), last_timestamp_ns=-1),
                 replace(saved(), next_decision_id=0), replace(saved(), timer_running=True), replace(saved(), accepted=LOW)]
    for state in malformed:
      with self.subTest(state=state):
        result = step(state, valid(LOW), ACTIVE, Policy(False, False), now_ns=0)
        self.assertTrue(result.errors)
        self.assertIsNone(result.control_target_mps)
        self.assertIsNone(result.history_write)

  def test_pending_candidate_cannot_also_be_rejected(self):
    state = replace(self.pending().state, rejected=LOW)
    result = step(state, valid(HIGH), ACTIVE, Policy(False, False), now_ns=NS)
    self.assertTrue(result.errors)
    self.assertIsNone(result.control_target_mps)
    self.assertIsNone(result.history_write)
    self.assertIs(result.state, state)
    self.assertEqual(result.state.accepted.candidate, HIGH)

  def test_returning_to_the_previous_limit_clears_the_obsolete_choice(self):
    other = self.pending()
    returned = step(other.state, valid(HIGH), ACTIVE, Policy(), now_ns=NS)
    stale = DriverAction('drive', 1, other.state.pending.decision_id, ActionKind.REJECT)
    held = step(returned.state, valid(HIGH), ACTIVE, Policy(), now_ns=2 * NS, action=stale)
    self.assertFalse(held.errors)
    self.assertEqual(held.basis, 'accepted')
    self.assertIsNone(returned.state.pending)
    self.assertFalse(held.action_receipt.consumed)
    self.assertEqual(held.state.accepted, returned.state.accepted)
    self.assertFalse(held.state.rejected)
    self.assertIsNone(held.state.pending)

  def test_new_decline_replaces_the_previous_decline(self):
    first = self.act(self.pending().state, ActionKind.REJECT)
    other = replace(LOW, speed_mps=19.)
    pending = self.pending(candidate=other, state=first.state, now_ns=2 * NS)
    self.assertEqual(pending.state.rejected, LOW)
    second = self.act(pending.state, ActionKind.REJECT, sequence=1, now_ns=3 * NS)
    self.assertEqual(second.state.rejected, other)
    returned = self.pending(state=second.state, now_ns=4 * NS)
    self.assertEqual(returned.control_target_mps, HIGH.speed_mps)

  def test_state_and_inputs_are_immutable_and_new_session_validates_history(self):
    original = saved()
    step(original, valid(LOW), ACTIVE, Policy(), now_ns=0)
    self.assertIsNone(original.pending)
    self.assertIsNone(original.last_timestamp_ns)
    with self.assertRaises(FrozenInstanceError):
      cast(Any, original).session_id = "changed"
    for session, history in (("", None), ("drive", LOW), ("drive", AcceptedLimit(replace(HIGH, speed_mps=0), "old", 0))):
      with self.assertRaises(ValueError):
        new_session(session, cast(Any, history))

  def test_identity_kinds_are_explicit_and_not_interchangeable(self):
    identities = (geographic("road"), ObservationIdentity(IdentityKind.PRODUCER_EPISODE, value="episode"),
                  ObservationIdentity(IdentityKind.SESSION_VALUE, session_id="drive"))
    for identity in identities:
      candidate = replace(HIGH, observation_identity=identity)
      result = step(saved(), valid(candidate), ACTIVE, Policy(False, False), now_ns=0)
      self.assertEqual(result.errors, ())
      self.assertEqual(result.display_candidate, candidate)
    self.assertEqual(len({replace(HIGH, observation_identity=identity) for identity in identities}), 3)

  def test_session_value_retains_rejection_across_dropout_and_source_switching(self):
    identity = ObservationIdentity(IdentityKind.SESSION_VALUE, session_id="drive")
    candidate = replace(LOW, observation_identity=identity)
    pending = self.pending(candidate=candidate)
    rejected = self.act(pending.state, ActionKind.REJECT)
    absent = step(rejected.state, ABSENT, ACTIVE, Policy(), now_ns=2 * NS)
    alternate = step(absent.state, valid(replace(candidate, source="map")), ACTIVE, Policy(), now_ns=3 * NS)
    returned = step(alternate.state, valid(Candidate("dashboard", ObservationIdentity(IdentityKind.SESSION_VALUE, session_id="drive"),
                                                    LOW.speed_mps)), ACTIVE, Policy(), now_ns=4 * NS)
    self.assertEqual(returned.state.rejected, candidate)
    self.assertEqual(returned.basis, "rejected")
    self.assertEqual(returned.state.accepted.candidate, HIGH)
    self.assertIsNone(alternate.state.pending)
    self.assertIsNone(returned.state.pending)

  def test_session_value_has_no_alternate_token_and_cannot_cross_sessions(self):
    malformed = [ObservationIdentity(IdentityKind.SESSION_VALUE, value="invented", session_id="drive"),
                 ObservationIdentity(IdentityKind.SESSION_VALUE), ObservationIdentity(IdentityKind.SESSION_VALUE, session_id="other"),
                 ObservationIdentity(IdentityKind.GEOGRAPHIC, value="road", session_id="drive"),
                 ObservationIdentity(IdentityKind.PRODUCER_EPISODE, value=""),
                 ObservationIdentity(cast(Any, "session_value"), session_id="drive"), cast(Any, "road")]
    for identity in malformed:
      with self.subTest(identity=identity):
        result = step(saved(), valid(replace(LOW, observation_identity=identity)), ACTIVE, Policy(), now_ns=0)
        self.assertTrue(result.errors)
        self.assertIsNone(result.control_target_mps)
        self.assertIsNone(result.history_write)
    candidate = replace(HIGH, observation_identity=ObservationIdentity(IdentityKind.SESSION_VALUE, session_id="old"))
    history = AcceptedLimit(candidate, "old", 0)
    with self.assertRaises(ValueError):
      new_session("drive", history)
    with self.assertRaises(ValueError):
      new_session("drive", replace(history, session_id="drive"))
    # No implicit rekeying: independently qualified geographic history retains its original accepted session.
    qualified = AcceptedLimit(HIGH, "old", 0)
    self.assertIs(new_session("drive", qualified).accepted, qualified)

  def test_explicit_adopt_of_different_limit_clears_rejection(self):
    first_reject = self.act(self.pending().state, ActionKind.REJECT)
    other = replace(LOW, speed_mps=19.0)
    second_pending = self.pending(candidate=other, state=first_reject.state, now_ns=2 * NS)
    second_reject = self.act(second_pending.state, ActionKind.REJECT, sequence=1, now_ns=3 * NS)
    shown = step(second_reject.state, valid(LOW), ACTIVE, Policy(), now_ns=4 * NS)
    request = self.adopt_request(shown.state, sequence=2)
    adopted = step(shown.state, valid(LOW), ACTIVE, Policy(), now_ns=5 * NS, adopt=request)
    self.assertEqual(adopted.basis, "driver_adopt")
    self.assertTrue(adopted.adoption_receipt.consumed)
    self.assertIsNone(adopted.action_receipt)
    self.assertIsNone(adopted.state.rejected)
    self.assertEqual(adopted.history_write.candidate, LOW)
    self.assertEqual(adopted.control_target_mps, LOW.speed_mps)
    self.assertIsNone(adopted.state.pending)
    self.assertEqual(adopted.adoption_receipt.sequence_id, request.sequence_id)
    self.assertEqual(adopted.state.accepted, adopted.history_write)
    next_offer = self.pending(candidate=other, state=adopted.state, now_ns=6 * NS)
    self.assertEqual(next_offer.control_target_mps, LOW.speed_mps)

  def test_adopt_already_accepted_candidate_keeps_control_target(self):
    shown = step(saved(), valid(HIGH), ACTIVE, Policy(), now_ns=0)
    for authority in (ACTIVE, replace(ACTIVE, mode=Mode.COMBINED, lateral_active=True)):
      result = step(shown.state, valid(HIGH), authority, Policy(), now_ns=NS, adopt=self.adopt_request(shown.state))
      self.assertTrue(result.adoption_receipt.consumed)
      self.assertIsNone(result.history_write)
      self.assertEqual(result.state.accepted, shown.state.accepted)
      self.assertEqual(result.control_target_mps, HIGH.speed_mps)

  def test_explicit_adopt_clears_same_speed_rejection_after_provider_change(self):
    rejected = self.act(self.pending().state, ActionKind.REJECT)
    refreshed = replace(LOW, source='map', observation_identity=geographic('refreshed'))
    shown = step(rejected.state, valid(refreshed), ACTIVE, Policy(), now_ns=2 * NS)
    adopted = step(shown.state, valid(refreshed), ACTIVE, Policy(), now_ns=3 * NS,
                   adopt=self.adopt_request(shown.state, sequence=1))
    self.assertTrue(adopted.adoption_receipt.consumed)
    self.assertFalse(adopted.state.rejected)
    held = step(adopted.state, valid(refreshed), ACTIVE, Policy(), now_ns=4 * NS)
    self.assertFalse(held.errors)
    self.assertEqual(held.basis, 'accepted')

  def test_adopt_precedes_confirmation_timeout(self):
    pending = self.pending()
    result = step(pending.state, valid(LOW), ACTIVE, Policy(), now_ns=CONFIRMATION_NS, adopt=self.adopt_request(pending.state))
    self.assertEqual(result.basis, "driver_adopt")
    self.assertEqual(result.state.accepted.candidate, LOW)
    self.assertIsNone(result.state.rejected)

  def test_adopt_requires_prior_presentation_even_when_new_id_is_guessed(self):
    guessed = AdoptRequest("drive", 0, 1, LOW)
    unseen = step(saved(), valid(LOW), ACTIVE, Policy(), now_ns=0, adopt=guessed)
    self.assertEqual(unseen.state.presentation.presentation_id, guessed.presentation_id)
    self.assertFalse(unseen.adoption_receipt.consumed)
    self.assertEqual(unseen.adoption_receipt.status, "different_presentation")
    self.assertIsNone(unseen.history_write)
    replay = step(unseen.state, valid(LOW), ACTIVE, Policy(), now_ns=NS, adopt=guessed)
    self.assertEqual(replay.adoption_receipt.status, "already_seen")
    fresh = step(replay.state, valid(LOW), ACTIVE, Policy(), now_ns=2 * NS, adopt=replace(guessed, sequence_id=1))
    self.assertTrue(fresh.adoption_receipt.consumed)

  def test_adopt_requires_live_evidence_and_an_unchanged_presentation(self):
    shown = self.pending()
    request = self.adopt_request(shown.state)
    for observation in (valid(replace(LOW, source="map")), ABSENT, Observation(ObservationKind.UNKNOWN), Observation(ObservationKind.STALE)):
      with self.subTest(observation=observation):
        changed = step(shown.state, observation, ACTIVE, Policy(), now_ns=NS, adopt=request)
        self.assertFalse(changed.adoption_receipt.consumed)
        self.assertIsNone(changed.history_write)
        replayed = step(changed.state, valid(LOW), ACTIVE, Policy(), now_ns=2 * NS, adopt=request)
        self.assertFalse(replayed.adoption_receipt.consumed)
        returned = step(changed.state, valid(LOW), ACTIVE, Policy(), now_ns=2 * NS, adopt=replace(request, sequence_id=1))
        if observation.kind is ObservationKind.VALID:
          self.assertFalse(returned.adoption_receipt.consumed)
          self.assertNotEqual(returned.state.presentation.presentation_id, request.presentation_id)
        else:
          self.assertTrue(returned.adoption_receipt.consumed)
          self.assertEqual(returned.state.accepted.candidate, LOW)
          self.assertEqual(returned.state.presentation.presentation_id, request.presentation_id)

  def test_adopt_exact_candidate_cannot_be_swapped_under_existing_context(self):
    shown = self.pending()
    for other in (replace(LOW, source="map"), replace(LOW, speed_mps=19.0),
                  replace(LOW, observation_identity=geographic("other-road"))):
      request = replace(self.adopt_request(shown.state), candidate=other)
      result = step(shown.state, valid(LOW), ACTIVE, Policy(), now_ns=NS, adopt=request)
      self.assertFalse(result.adoption_receipt.consumed)
      self.assertEqual(result.adoption_receipt.status, "different_presentation")

  def test_adopt_is_recorded_for_any_authority_but_only_commands_with_system_long(self):
    rejected = self.act(self.pending().state, ActionKind.REJECT)
    request = self.adopt_request(rejected.state, sequence=1)
    contexts = [PAUSED, DISENGAGED, Authority(Mode.OFF, LongitudinalOwner.NONE, False, False, False, False),
                Authority(Mode.LATERAL_ONLY, LongitudinalOwner.NONE, True, False, False, False),
                Authority(Mode.LATERAL_ONLY, LongitudinalOwner.STOCK, True, False, True, False),
                Authority(Mode.COMBINED, LongitudinalOwner.STOCK, True, False, True, False)]
    for authority in contexts:
      with self.subTest(authority=authority):
        result = step(rejected.state, valid(LOW), authority, Policy(), now_ns=2 * NS, adopt=request)
        self.assertTrue(result.adoption_receipt.consumed)
        self.assertEqual(result.adoption_receipt.status, "adopt")
        self.assertIsNone(result.state.rejected)
        self.assertEqual(result.state.accepted.candidate, LOW)
        self.assertIsNone(result.control_target_mps)
        self.assertEqual(result.state.last_action_sequence, 1)
    display = step(rejected.state, valid(LOW), ACTIVE, Policy(display_only=True), now_ns=2 * NS, adopt=request)
    self.assertFalse(display.adoption_receipt.consumed)
    self.assertEqual(display.adoption_receipt.status, "display_only")
    self.assertEqual(display.state.rejected, LOW)
    self.assertIsNone(display.history_write)

  def test_adopt_foreign_session_does_not_advance_watermark(self):
    shown = self.pending()
    foreign = replace(self.adopt_request(shown.state, sequence=999), session_id="other")
    result = step(shown.state, valid(LOW), ACTIVE, Policy(), now_ns=NS, adopt=foreign)
    self.assertEqual(result.adoption_receipt.status, "foreign_session")
    self.assertEqual(result.state.last_action_sequence, -1)
    accepted = step(result.state, valid(LOW), ACTIVE, Policy(), now_ns=2 * NS, adopt=self.adopt_request(result.state))
    self.assertTrue(accepted.adoption_receipt.consumed)

  def test_adoption_and_confirmation_share_seen_sequence_watermark(self):
    shown = self.pending()
    request = replace(self.adopt_request(shown.state, sequence=5), presentation_id=999)
    paused = step(shown.state, valid(LOW), PAUSED, Policy(), now_ns=NS, adopt=request)
    self.assertIsNone(paused.state.pending)
    repeated_action = DriverAction("drive", 5, shown.state.pending.decision_id, ActionKind.ACCEPT)
    repeated = step(paused.state, valid(LOW), ACTIVE, Policy(), now_ns=2 * NS, action=repeated_action)
    self.assertEqual(repeated.action_receipt.status, "already_seen")
    self.assertFalse(repeated.action_receipt.consumed)
    fresh = step(repeated.state, valid(LOW), ACTIVE, Policy(), now_ns=3 * NS,
                 action=replace(repeated_action, sequence_id=6, decision_id=repeated.state.pending.decision_id))
    self.assertTrue(fresh.action_receipt.consumed)
    old_adopt = step(fresh.state, valid(LOW), ACTIVE, Policy(), now_ns=4 * NS, adopt=request)
    self.assertFalse(old_adopt.adoption_receipt.consumed)

  def test_ambiguous_action_and_adopt_fail_closed_and_watermark_both(self):
    shown = self.pending()
    for ordinary_sequence, adopt_sequence in ((3, 7), (7, 3), (7, 7)):
      with self.subTest(ordinary=ordinary_sequence, adopt=adopt_sequence):
        action = DriverAction("drive", ordinary_sequence, shown.state.pending.decision_id, ActionKind.ACCEPT)
        adopt = self.adopt_request(shown.state, sequence=adopt_sequence)
        result = step(shown.state, valid(LOW), ACTIVE, Policy(), now_ns=NS, action=action, adopt=adopt)
        self.assertIn("ambiguous action and adopt request", result.errors)
        self.assertFalse(result.action_receipt.consumed)
        self.assertFalse(result.adoption_receipt.consumed)
        self.assertEqual(result.state.last_action_sequence, 7)
        self.assertIsNone(result.control_target_mps)
        self.assertIsNone(result.history_write)
        self.assertEqual(result.state.accepted.candidate, HIGH)
        self.assertIsNone(result.state.presentation)

  def test_invalid_adopt_or_clock_never_produces_an_effect(self):
    shown = self.pending(now_ns=10 * NS)
    request = self.adopt_request(shown.state, sequence=1)
    for malformed in (replace(request, sequence_id=True), replace(request, presentation_id=0),
                      replace(request, session_id=""), replace(request, candidate=replace(LOW, speed_mps=0))):
      result = step(shown.state, valid(LOW), ACTIVE, Policy(), now_ns=11 * NS, adopt=malformed)
      self.assertTrue(result.errors)
      self.assertIsNone(result.adoption_receipt)
      self.assertIsNone(result.control_target_mps)
      self.assertEqual(result.state.last_action_sequence, -1)
    reversed_clock = step(shown.state, valid(LOW), ACTIVE, Policy(), now_ns=9 * NS, adopt=request)
    self.assertTrue(reversed_clock.errors)
    self.assertFalse(reversed_clock.adoption_receipt.consumed)
    self.assertEqual(reversed_clock.state.last_action_sequence, 1)

  def test_invalid_presentation_state_is_inert(self):
    shown = self.pending()
    for state in (replace(shown.state, presentation=None), replace(shown.state, next_presentation_id=1),
                  replace(shown.state, presentation=replace(shown.state.presentation, candidate=HIGH))):
      result = step(state, valid(LOW), ACTIVE, Policy(), now_ns=NS, adopt=self.adopt_request(shown.state))
      self.assertTrue(result.errors)
      self.assertIsNone(result.control_target_mps)
      self.assertIsNone(result.history_write)


if __name__ == "__main__":
  unittest.main()
