"""Tesla set-speed rule: a held SET needs the acceptance decision and the causal ledger together."""

import unittest
from dataclasses import replace

from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import action_arbitration as arb
from openpilot.starpilot.speed_limits import overrides as ov

SESSION = "drive"
HIGH = acc.Candidate("dashboard", acc.ObservationIdentity(acc.IdentityKind.SESSION_VALUE, session_id=SESSION), 20.0)
LOW = acc.Candidate("map", acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value="road-2"), 15.0)
SAME = acc.Candidate("map", acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value="road-3"), 20.0)
ACTIVE = acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.SYSTEM, False, True, False, False)
# The rule's policy: automatic adoption waits 3 s and another source must hold 2 s (the Policy defaults are 0).
POLICY = acc.Policy(confirm_lower=False, confirm_higher=False, automatic_adoption_ns=acc.AUTOMATIC_ADOPTION_NS,
                    source_switch_ns=acc.SOURCE_SWITCH_NS)
PEDAL_OFF = ov.PedalEvidence(ov.EvidenceKind.VALID, False)
PAUSE_NS = 750_000_000


def observation(candidate=HIGH):
  return acc.Observation(acc.ObservationKind.VALID, candidate)


def rule(decision):
  # As composition does on the rule: a source gap carries no control target.
  if decision.control_target_mps is not None and decision.state.observation.kind is not acc.ObservationKind.VALID:
    return replace(decision, control_target_mps=None)
  return decision


class OverrideTests(unittest.TestCase):
  def setUp(self):
    state = replace(acc.new_session(SESSION, acc.AcceptedLimit(HIGH, SESSION, 0)), received_speed_mps=HIGH.speed_mps)
    self.accepted = acc.step(state, observation(), ACTIVE, POLICY, now_ns=0)
    self.state = ov.new_session(SESSION)
    self.selected = ov.SelectedEvidence(ov.EvidenceKind.VALID, 20.0)
    self.first = self.reduce(0)

  def decision(self, now, *, candidate=HIGH, authority=ACTIVE, policy=POLICY, source=None, adopt=None):
    obs = observation(candidate) if source is None else acc.Observation(source)
    result = rule(acc.step(self.accepted.state, obs, authority, policy, now_ns=now, adopt=adopt))
    self.accepted = result
    return result

  def reduce(self, now, *, acceptance=None, authority=ACTIVE, policy=POLICY, selected=None, pedal=PEDAL_OFF, change=None):
    result = ov.step(self.state, self.accepted if acceptance is None else acceptance, authority, policy,
                     self.selected if selected is None else selected, pedal, now_ns=now, change=change)
    self.state = result.state
    return result

  def classified(self, *, effect=1, now=2, previous=20.0, chosen=25.0, context=None, disposition=arb.Disposition.DRIVER_INTENT):
    ledger = arb.new_ledger(SESSION)
    context = self.first.context if context is None else context
    ledger = arb.step(ledger, arb.BeginAction(SESSION, 1, arb.Origin.DRIVER_CRUISE, context.context_id), now_ns=1).state
    ledger = arb.step(ledger, arb.ResolveAction(SESSION, 1, disposition), now_ns=1).state
    result = arb.step(ledger, arb.CruiseChange(SESSION, effect, 1, previous, chosen), now_ns=now)
    return result.change

  def arm(self, now=2, chosen=25.0):
    change = self.classified(now=now, chosen=chosen)
    self.decision(now)
    return self.reduce(now, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, chosen), change=change)

  def test_fresh_causal_intent_persists_and_scalar_does_not_rearm(self):
    armed = self.arm()
    self.assertEqual(armed.contribution_mps, 25.0)
    self.assertEqual(armed.event_receipt.status, "driver_intent")
    self.assertTrue(armed.event_receipt.consumed)
    self.decision(3)
    self.assertEqual(self.reduce(3, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps, 25.0)
    self.decision(4, candidate=replace(LOW, source=HIGH.source))
    # Automatic adoption waits AUTOMATIC_ADOPTION_NS on the rule.
    self.assertEqual(self.reduce(4, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps, 25.)
    self.decision(4 + acc.AUTOMATIC_ADOPTION_NS, candidate=replace(LOW, source=HIGH.source))
    self.assertIsNone(self.reduce(4 + acc.AUTOMATIC_ADOPTION_NS, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps)
    self.decision(5 + acc.AUTOMATIC_ADOPTION_NS, candidate=LOW)
    self.assertIsNone(self.reduce(5 + acc.AUTOMATIC_ADOPTION_NS, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps)

  def test_decrease_only_reduces_an_existing_override(self):
    for existing, previous, chosen, expected in (
      (False, 30.0, 29.0, 29.0), (False, 30.0, 20.0, None), (False, 30.0, 19.0, 19.0),
      (True, 25.0, 24.0, 24.0), (True, 25.0, 20.0, None), (True, 25.0, 19.0, 19.0),
      (True, 30.0, 29.0, 29.0), (True, 30.0, 25.0, 25.0),
    ):
      with self.subTest(existing=existing, chosen=chosen):
        self.setUp()
        if existing:
          self.arm()
        self.decision(2)
        self.reduce(2, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, previous))
        self.decision(3)
        result = self.reduce(3, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, chosen),
                             change=self.classified(effect=2, now=3, previous=previous, chosen=chosen))
        self.assertFalse(result.errors)
        self.assertTrue(result.event_receipt.consumed)
        self.assertEqual(result.state.persistent_selected_mps, expected)
        self.assertEqual(result.contribution_mps, expected)
        self.assertEqual(self.accepted.control_target_mps, HIGH.speed_mps)

  def test_manual_raise_survives_higher_limit_below_it_but_lower_limit_clears(self):
    self.arm(chosen=25.)
    self.decision(3, candidate=SAME)
    self.assertEqual(self.reduce(3, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.)).contribution_mps, 25.)
    mid = replace(SAME, speed_mps=22.)
    self.decision(4, candidate=mid)
    self.assertEqual(self.reduce(4, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.)).contribution_mps, 25.)
    self.decision(3_000_000_004, candidate=mid)
    self.assertEqual(self.reduce(3_000_000_004, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.)).contribution_mps, 25.)
    self.decision(3_000_000_005, candidate=LOW)
    self.reduce(3_000_000_005, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.))
    self.decision(6_000_000_005, candidate=LOW)
    self.assertIsNone(self.reduce(6_000_000_005, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.)).contribution_mps)

  def test_pedal_tracks_current_ego_and_release_restores_persistence(self):
    self.arm()
    self.decision(3)
    self.assertEqual(self.reduce(3, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0),
                                 pedal=ov.PedalEvidence(ov.EvidenceKind.VALID, True, 28.0)).contribution_mps, 25.0)
    self.decision(4)
    self.assertEqual(self.reduce(4, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0),
                                 pedal=ov.PedalEvidence(ov.EvidenceKind.VALID, True, 23.0)).contribution_mps, 25.0)
    self.decision(5)
    self.assertEqual(self.reduce(5, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps, 25.0)

  def test_temporary_inactivity_invalidates_origin_and_grace_expires(self):
    self.arm()
    paused = replace(ACTIVE, longitudinal_active=False)
    self.decision(3, authority=paused)
    self.assertIsNone(self.reduce(3, authority=paused, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).context)
    self.decision(3 + PAUSE_NS - 1, authority=paused)
    self.reduce(3 + PAUSE_NS - 1, authority=paused, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0))
    self.decision(3 + PAUSE_NS - 1)
    resumed = self.reduce(3 + PAUSE_NS - 1, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0))
    self.assertEqual(resumed.contribution_mps, 25.0)
    self.assertNotEqual(resumed.context.context_id, self.first.context.context_id)
    self.decision(PAUSE_NS + 10, authority=paused)
    self.reduce(PAUSE_NS + 10, authority=paused, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0))
    self.decision(2 * PAUSE_NS + 10)
    self.assertEqual(self.reduce(2 * PAUSE_NS + 10, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps, 25.)

  def test_stale_missing_and_display_only_suppress_output_and_watermark(self):
    self.arm()
    change = self.classified(effect=2, now=3, chosen=26.0)
    self.decision(3, source=acc.ObservationKind.STALE)
    stale = self.reduce(3, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 26.0), change=change)
    self.assertEqual(stale.contribution_mps, 26.)
    self.assertEqual(stale.state.last_effect_id, 2)
    self.decision(4)
    self.assertEqual(self.reduce(4, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0), change=change).event_receipt.status, "already_seen")
    display = acc.Policy(confirm_lower=False, confirm_higher=False, display_only=True)
    self.decision(5, policy=display)
    self.assertIsNone(self.reduce(5, policy=display, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps)

  def test_source_gaps_preserve_the_manual_choice(self):
    for kind in (acc.ObservationKind.ABSENT, acc.ObservationKind.UNKNOWN, acc.ObservationKind.STALE):
      with self.subTest(kind=kind):
        self.setUp()
        self.arm()
        self.decision(3, source=kind)
        result = self.reduce(3, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0))
        self.assertEqual(result.contribution_mps, 25.0)
        self.assertEqual(result.context, self.first.context)

  def test_delayed_acceptance_consumed_effect_cannot_arm_after_release(self):
    pending_policy = acc.Policy(confirm_lower=True, confirm_higher=True)
    state = replace(acc.new_session(SESSION, acc.AcceptedLimit(HIGH, SESSION, 0)), received_speed_mps=HIGH.speed_mps)
    pending = acc.step(state, observation(LOW), ACTIVE, pending_policy, now_ns=0)
    action = acc.DriverAction(SESSION, 1, pending.state.pending.decision_id, acc.ActionKind.ACCEPT)
    ledger = arb.step(arb.new_ledger(SESSION), arb.BeginAction(SESSION, 1, arb.Origin.DRIVER_CRUISE,
                                                               self.first.context.context_id), now_ns=1).state
    accepted = acc.step(pending.state, observation(LOW), ACTIVE, pending_policy, now_ns=2, action=action)
    ledger = arb.resolve_acceptance(ledger, 1, accepted, now_ns=2).state
    delayed = arb.step(ledger, arb.CruiseChange(SESSION, 1, 1, 20.0, 25.0), now_ns=60_000_000_000).change
    self.assertEqual(delayed.disposition, arb.Disposition.SLC_CONSUMED)
    self.decision(60_000_000_000)
    result = self.reduce(60_000_000_000, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0), change=delayed)
    self.assertIsNone(result.contribution_mps)
    self.assertEqual(result.state.last_effect_id, 1)

  def test_adoption_clears_once_and_current_pedal_still_contributes(self):
    self.arm()
    shown = self.decision(3, candidate=LOW)
    adopt = acc.AdoptRequest(SESSION, 2, shown.state.presentation.presentation_id, LOW)
    adopted = self.decision(4, candidate=LOW, adopt=adopt)
    result = self.reduce(4, acceptance=adopted, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0),
                         pedal=ov.PedalEvidence(ov.EvidenceKind.VALID, True, 27.0))
    self.assertIsNone(result.state.persistent_selected_mps)
    self.assertIsNone(result.contribution_mps)
    self.assertNotEqual(result.context.context_id, self.first.context.context_id)
    duplicate = self.reduce(4, acceptance=adopted, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0))
    self.assertEqual(duplicate.context, result.context)

  def test_mismatched_snapshot_and_inactive_origin_fail_closed(self):
    change = self.classified()
    self.decision(2)
    mismatch = self.reduce(2, policy=replace(POLICY, confirm_lower=True), change=change)
    self.assertTrue(mismatch.errors)
    self.assertEqual(mismatch.state.last_effect_id, 1)
    self.assertTrue(mismatch.state.reset_required)
    self.decision(3)
    self.assertIsNone(self.reduce(3).context)
    self.state = ov.new_session(SESSION)
    old = self.reduce(3).context
    paused = replace(ACTIVE, longitudinal_active=False)
    self.decision(4, authority=paused)
    self.reduce(4, authority=paused)
    self.decision(5)
    late = replace(change, effect_id=2, context_id=old.context_id, started_at_ns=4)
    result = self.reduce(5, change=late, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0))
    self.assertIsNone(result.contribution_mps)
    self.assertEqual(result.event_receipt.status, "unqualified_effect")

  def test_pending_lower_and_rejection_keep_accepted_target_and_intent(self):
    self.arm()
    confirmation = acc.Policy(confirm_lower=True, confirm_higher=True)
    lower = replace(LOW, source=HIGH.source)
    pending = self.decision(3, candidate=lower, policy=confirmation)
    self.assertEqual(pending.basis, "pending")
    self.assertEqual(self.reduce(3, policy=confirmation, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps, 25.0)
    action = acc.DriverAction(SESSION, 2, pending.state.pending.decision_id, acc.ActionKind.REJECT)
    rejected = acc.step(pending.state, observation(lower), ACTIVE, confirmation, now_ns=4, action=action)
    self.accepted = rejected
    self.assertEqual(self.reduce(4, policy=confirmation, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps, 25.0)
    self.assertEqual(rejected.state.rejected, lower)

  def test_provider_flapping_preserves_override_until_the_new_limit_qualifies(self):
    self.arm()
    for now, candidate in ((3, LOW), (4, HIGH), (5, LOW)):
      self.decision(now, candidate=candidate)
      retained = self.reduce(now, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.))
      self.assertEqual(retained.contribution_mps, 25.)
    now = 5 + acc.SOURCE_SWITCH_NS
    self.decision(now, candidate=LOW)
    now += acc.AUTOMATIC_ADOPTION_NS  # then the automatic-adoption wait
    self.decision(now, candidate=LOW)
    adopted = self.reduce(now, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.))
    self.assertIsNone(adopted.contribution_mps)

  def test_mode_owner_change_suppresses_output_without_forgetting_manual_choice(self):
    self.arm()
    stock = acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.STOCK, False, False, True, False)
    self.decision(3, authority=stock)
    self.reduce(3, authority=stock, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0))
    self.assertEqual(self.state.persistent_selected_mps, 25.)
    self.decision(4)
    self.assertEqual(self.reduce(4, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0)).contribution_mps, 25.)

  def test_previous_session_history_does_not_arm_overrides_without_a_live_limit(self):
    prior = acc.AcceptedLimit(acc.Candidate("map", acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value="road"), 20.0),
                              "prior-drive", 0)
    policy = acc.Policy()
    decision = rule(acc.step(acc.new_session(SESSION, prior), acc.Observation(acc.ObservationKind.ABSENT), ACTIVE, policy, now_ns=1))
    result = ov.step(ov.new_session(SESSION), decision, ACTIVE, policy, self.selected, PEDAL_OFF, now_ns=1)
    self.assertFalse(result.errors)
    self.assertIsNone(result.context)

  def test_automatic_foreign_and_completed_effects_are_watermarked_without_intent(self):
    self.decision(2)
    automatic = replace(self.classified(now=2), disposition=arb.Disposition.AUTOMATIC)
    got = self.reduce(2, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0), change=automatic)
    self.assertIsNone(got.contribution_mps)
    self.assertEqual(got.state.last_effect_id, 1)
    self.decision(3)
    foreign = replace(automatic, session_id="other", effect_id=2, disposition=arb.Disposition.DRIVER_INTENT)
    self.assertEqual(self.reduce(3, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0), change=foreign).event_receipt.status,
                     "foreign_session")
    self.decision(4)
    completed = replace(automatic, effect_id=2, action_id=1, context_id=None, disposition=arb.Disposition.UNRESOLVED)
    result = self.reduce(4, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0), change=completed)
    self.assertIsNone(result.contribution_mps)
    self.assertEqual(result.state.last_effect_id, 2)

  def test_inconsistent_acceptance_target_and_reversed_clock_fail_closed(self):
    self.decision(2)
    forged = replace(self.accepted, control_target_mps=21.0)
    bad = self.reduce(2, acceptance=forged)
    self.assertTrue(bad.errors)
    self.assertIsNone(bad.contribution_mps)
    self.assertTrue(bad.state.reset_required)
    reversed_clock = self.reduce(1)
    self.assertTrue(reversed_clock.errors)

  def test_zero_ego_speed_is_valid_but_negative_bool_and_nan_are_not(self):
    self.arm()
    for now, pressed in ((3, True), (4, False)):
      self.decision(now)
      zero = self.reduce(now, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0),
                         pedal=ov.PedalEvidence(ov.EvidenceKind.VALID, pressed, 0.0))
      self.assertFalse(zero.errors)
      self.assertEqual(zero.contribution_mps, 25.0)
    for invalid in (-0.1, True, float("nan")):
      with self.subTest(invalid=invalid):
        self.decision(5)
        bad = ov.step(self.state, self.accepted, ACTIVE, POLICY, ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0),
                      ov.PedalEvidence(ov.EvidenceKind.VALID, True, invalid), now_ns=5)
        self.assertTrue(bad.errors)
        self.assertTrue(bad.state.reset_required)

  def test_malformed_effect_latches_session_and_cannot_replay_after_repair(self):
    self.arm()
    fresh = self.classified(effect=2, now=4, chosen=26.0)
    self.decision(3)
    malformed = replace(fresh, selected_mps=float("nan"))
    bad = self.reduce(3, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 26.0), change=malformed)
    self.assertTrue(bad.state.reset_required)
    self.assertIsNone(bad.state.persistent_selected_mps)
    self.assertIsNone(bad.state.context)
    self.decision(4)
    retry = self.reduce(4, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 26.0), change=fresh)
    self.assertIsNone(retry.contribution_mps)
    self.assertEqual(retry.basis, "session_reset_required")
    next_candidate = acc.Candidate("map", acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value="new-road"), 20.0)
    next_acceptance = acc.step(acc.new_session("next-drive"), observation(next_candidate), ACTIVE, POLICY, now_ns=0)
    next_state = ov.step(ov.new_session("next-drive"), next_acceptance, ACTIVE, POLICY, self.selected, PEDAL_OFF, now_ns=0)
    next_acceptance = acc.step(next_acceptance.state, observation(next_candidate), ACTIVE, POLICY, now_ns=1)
    replay = ov.step(next_state.state, next_acceptance, ACTIVE, POLICY, ov.SelectedEvidence(ov.EvidenceKind.VALID, 26.0),
                     PEDAL_OFF, now_ns=1, change=fresh)
    self.assertIsNone(replay.contribution_mps)
    self.assertEqual(replay.event_receipt.status, "foreign_session")

  def test_malformed_state_is_inert(self):
    forged = replace(self.state, context=replace(self.first.context, context_id="caller-proof"))
    self.decision(2)
    result = ov.step(forged, self.accepted, ACTIVE, POLICY, self.selected, PEDAL_OFF, now_ns=2)
    self.assertTrue(result.errors)
    self.assertIsNone(result.contribution_mps)
    self.assertIs(result.state, forged)

  def test_reversed_clock_clears_previously_armed_state_and_stays_latched(self):
    self.arm()
    bad = self.reduce(1)
    self.assertTrue(bad.state.reset_required)
    self.assertIsNone(bad.state.persistent_selected_mps)
    self.assertIsNone(bad.context)
    self.decision(3)
    recovered_input = self.reduce(3)
    self.assertEqual(recovered_input.basis, "session_reset_required")
    self.assertIsNone(recovered_input.contribution_mps)

  def test_target_change_requires_new_action_from_the_new_eligible_context(self):
    self.arm()
    old_effect = self.classified(effect=2, now=3, chosen=26.0)
    self.decision(3, candidate=replace(LOW, source=HIGH.source))
    self.decision(3 + acc.AUTOMATIC_ADOPTION_NS, candidate=replace(LOW, source=HIGH.source))
    lower = self.reduce(3 + acc.AUTOMATIC_ADOPTION_NS, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 26.0), change=old_effect)
    self.assertIsNone(lower.contribution_mps)
    self.assertFalse(lower.event_receipt.consumed)
    self.assertNotEqual(lower.context, self.first.context)

    ledger = arb.step(arb.new_ledger(SESSION), arb.BeginAction(SESSION, 2, arb.Origin.DRIVER_CRUISE,
                                                               lower.context.context_id), now_ns=3_000_000_004).state
    ledger = arb.step(ledger, arb.ResolveAction(SESSION, 2, arb.Disposition.DRIVER_INTENT), now_ns=3_000_000_004).state
    fresh = arb.step(ledger, arb.CruiseChange(SESSION, 3, 2, 26.0, 27.0), now_ns=3_000_000_005).change
    self.decision(3_000_000_005, candidate=LOW)
    rearmed = self.reduce(3_000_000_005, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 27.0), change=fresh)
    self.assertTrue(rearmed.event_receipt.consumed)
    self.assertEqual(rearmed.contribution_mps, 27.0)

  def test_missing_selected_or_pedal_evidence_suppresses_output(self):
    self.arm()
    for now, selected, pedal in (
      (3, ov.SelectedEvidence(ov.EvidenceKind.STALE), PEDAL_OFF),
      (4, ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0), ov.PedalEvidence(ov.EvidenceKind.UNKNOWN)),
      (5, ov.SelectedEvidence(ov.EvidenceKind.ABSENT), PEDAL_OFF),
    ):
      self.decision(now)
      result = self.reduce(now, selected=selected, pedal=pedal)
      self.assertIsNone(result.contribution_mps)
      self.assertIsNone(result.context)

  def test_same_value_adoption_rotates_once_and_delayed_effect_is_consumed(self):
    self.arm()
    shown = self.decision(3)
    request = acc.AdoptRequest(SESSION, 2, shown.state.presentation.presentation_id, HIGH)
    ledger = arb.step(arb.new_ledger(SESSION), arb.BeginAction(SESSION, 2, arb.Origin.DRIVER_CRUISE,
                                                               self.first.context.context_id), now_ns=3).state
    adopted = self.decision(4, adopt=request)
    ledger = arb.resolve_acceptance(ledger, 2, adopted, now_ns=4).state
    result = self.reduce(4, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0))
    self.assertIsNone(result.state.persistent_selected_mps)
    self.assertNotEqual(result.context.context_id, self.first.context.context_id)
    again = self.reduce(4, acceptance=adopted, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 25.0))
    self.assertEqual(again.context, result.context)
    delayed = arb.step(ledger, arb.CruiseChange(SESSION, 3, 2, 25.0, 20.0), now_ns=10_000_000_000).change
    self.assertEqual(delayed.disposition, arb.Disposition.SLC_CONSUMED)
    self.decision(10_000_000_000)
    no_rearm = self.reduce(10_000_000_000, selected=ov.SelectedEvidence(ov.EvidenceKind.VALID, 20.0), change=delayed)
    self.assertIsNone(no_rearm.contribution_mps)
    self.assertEqual(no_rearm.event_receipt.status, "unqualified_effect")


if __name__ == "__main__":
  unittest.main()
