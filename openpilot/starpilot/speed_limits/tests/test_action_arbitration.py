"""Causal effect contracts, including delayed confirmation/adoption speed changes."""

from dataclasses import FrozenInstanceError, replace
from typing import Any, cast
import unittest

from openpilot.starpilot.speed_limits import action_arbitration as arb
from openpilot.starpilot.speed_limits import acceptance as accept


SESSION = "drive"
AUTHORITY = accept.Authority(accept.Mode.LONGITUDINAL_ONLY, accept.LongitudinalOwner.SYSTEM, False, True, False, False)
CANDIDATE = accept.Candidate("dashboard", accept.ObservationIdentity(accept.IdentityKind.SESSION_VALUE, session_id=SESSION), 20.0)
OBSERVATION = accept.Observation(accept.ObservationKind.VALID, CANDIDATE)


def begin(ledger=None, action_id=1, origin=arb.Origin.DRIVER_CRUISE, context="accepted-context-1", now=1):
  return arb.step(ledger or arb.new_ledger(SESSION), arb.BeginAction(SESSION, action_id, origin, context), now_ns=now).state


def resolve(ledger, disposition=arb.Disposition.DRIVER_INTENT, action_id=1, now=2):
  return arb.step(ledger, arb.ResolveAction(SESSION, action_id, disposition), now_ns=now)


def change(ledger, effect_id=1, action_id=1, previous=20.0, selected=25.0, now=3):
  return arb.step(ledger, arb.CruiseChange(SESSION, effect_id, action_id, previous, selected), now_ns=now)


class TestActionArbitration(unittest.TestCase):
  def test_confirmation_consumption_survives_release_and_long_delay(self):
    pending = accept.step(accept.new_session(SESSION), OBSERVATION, AUTHORITY, accept.Policy(), now_ns=0)
    ledger = begin()
    decision = accept.step(pending.state, OBSERVATION, AUTHORITY, accept.Policy(), now_ns=2,
                           action=accept.DriverAction(SESSION, 1, pending.state.pending.decision_id, accept.ActionKind.ACCEPT))
    bound = arb.resolve_acceptance(ledger, 1, decision, now_ns=2)
    self.assertEqual(bound.status, "resolved")
    # Button release does not complete a transaction whose cruise effect is pending.
    delayed = change(bound.state, now=60_000_000_000)
    self.assertEqual(delayed.change.disposition, arb.Disposition.SLC_CONSUMED)
    self.assertEqual(delayed.change.action_id, 1)
    self.assertEqual(delayed.change.started_at_ns, 1)
    self.assertEqual(delayed.change.context_id, "accepted-context-1")

  def test_adoption_receipt_and_effect_keep_originating_action(self):
    shown = accept.step(accept.new_session(SESSION), OBSERVATION, AUTHORITY, accept.Policy(), now_ns=0)
    request = accept.AdoptRequest(SESSION, 1, shown.state.presentation.presentation_id, CANDIDATE)
    adopted = accept.step(shown.state, OBSERVATION, AUTHORITY, accept.Policy(), now_ns=2, adopt=request)
    ledger = arb.resolve_acceptance(begin(), 1, adopted, now_ns=2).state
    result = change(ledger, previous=25.0, selected=adopted.control_target_mps, now=10_000_000_000)
    self.assertEqual(result.change.disposition, arb.Disposition.SLC_CONSUMED)
    self.assertEqual(result.change.action_id, adopted.adoption_receipt.sequence_id)

  def test_final_unconsumed_receipt_does_not_swallow_cruise_input(self):
    shown = accept.step(accept.new_session(SESSION), OBSERVATION, AUTHORITY, accept.Policy(), now_ns=0)
    decision = accept.step(shown.state, OBSERVATION, AUTHORITY, accept.Policy(), now_ns=2,
                           action=accept.DriverAction(SESSION, 1, 999, accept.ActionKind.ACCEPT))
    self.assertFalse(decision.action_receipt.consumed)
    ledger = arb.resolve_acceptance(begin(), 1, decision, now_ns=2).state
    result = change(ledger)
    self.assertEqual(result.change.disposition, arb.Disposition.DRIVER_INTENT)

  def test_deliberate_increase_and_decrease_remain_distinct_from_policy(self):
    ledger = resolve(begin()).state
    for selected in (15.0, 25.0):
      result = change(ledger, selected=selected)
      self.assertEqual(result.change.disposition, arb.Disposition.DRIVER_INTENT)
      self.assertEqual(result.change.selected_mps, selected)
    # Above/below-limit eligibility belongs to the separate override/stock policy.

  def test_initial_or_steady_scalar_sample_cannot_establish_fresh_intent(self):
    ledger = resolve(begin()).state
    for previous in (None, 25.0):
      result = change(ledger, previous=previous, selected=25.0)
      self.assertEqual(result.change.disposition, arb.Disposition.UNRESOLVED)
      self.assertEqual(result.state.last_effect_id, 1)

  def test_missing_unknown_and_future_origins_never_infer_intent(self):
    for action_id in (None, 0, 999):
      result = change(resolve(begin()).state, action_id=action_id)
      self.assertEqual(result.change.disposition, arb.Disposition.UNRESOLVED)
      self.assertIsNone(result.change.context_id)

  def test_early_effect_is_not_replayed_after_later_resolution(self):
    early = change(begin(), now=2)
    self.assertEqual(early.change.disposition, arb.Disposition.UNRESOLVED)
    resolved = resolve(early.state, now=3)
    repeated = change(resolved.state, now=4)
    self.assertIsNone(repeated.change)
    self.assertEqual(repeated.status, "already_seen_effect")
    self.assertEqual(change(repeated.state, effect_id=2, now=5).change.disposition, arb.Disposition.DRIVER_INTENT)

  def test_automatic_and_unrelated_effects_never_become_driver_intent(self):
    for origin, disposition in ((arb.Origin.AUTOMATIC, arb.Disposition.AUTOMATIC),
                                (arb.Origin.DRIVER_OTHER, arb.Disposition.UNRELATED)):
      ledger = resolve(begin(origin=origin), disposition).state
      self.assertEqual(change(ledger).change.disposition, disposition)

  def test_multiple_pending_actions_resolve_and_deliver_independently(self):
    ledger = begin(begin(), action_id=2, context="accepted-context-2", now=2)
    ledger = resolve(ledger, action_id=2, now=3).state
    ledger = resolve(ledger, arb.Disposition.SLC_CONSUMED, action_id=1, now=4).state
    second = change(ledger, action_id=2, now=5)
    first = change(second.state, effect_id=2, action_id=1, now=6)
    self.assertEqual(second.change.disposition, arb.Disposition.DRIVER_INTENT)
    self.assertEqual(second.change.context_id, "accepted-context-2")
    self.assertEqual(first.change.disposition, arb.Disposition.SLC_CONSUMED)
    self.assertEqual(first.change.context_id, "accepted-context-1")

  def test_completion_prevents_late_effect_from_rearming_or_reopening(self):
    ledger = resolve(begin()).state
    completed = arb.step(ledger, arb.CompleteAction(SESSION, 1), now_ns=3)
    self.assertEqual(completed.state.transactions, ())
    late = change(completed.state, now=4)
    self.assertEqual(late.change.disposition, arb.Disposition.UNRESOLVED)
    reopened = arb.step(late.state, arb.BeginAction(SESSION, 1, arb.Origin.DRIVER_CRUISE, "new-context"), now_ns=5)
    self.assertEqual(reopened.status, "already_seen_action")
    self.assertEqual(reopened.state.transactions, ())

  def test_duplicate_and_out_of_order_effects_never_emit_twice(self):
    first = change(resolve(begin()).state, effect_id=10)
    for effect_id in (9, 10):
      repeated = change(first.state, effect_id=effect_id, now=4)
      self.assertIsNone(repeated.change)
      self.assertEqual(repeated.status, "already_seen_effect")
    self.assertEqual(change(first.state, effect_id=11, now=4).change.disposition, arb.Disposition.DRIVER_INTENT)

  def test_foreign_session_cannot_watermark_or_complete_current_actions(self):
    ledger = resolve(begin()).state
    for event in (arb.BeginAction("other", 999, arb.Origin.DRIVER_CRUISE, "ctx"),
                  arb.ResolveAction("other", 1, arb.Disposition.SLC_CONSUMED),
                  arb.CompleteAction("other", 1), arb.CruiseChange("other", 999, 1, 20.0, 25.0)):
      result = arb.step(ledger, event, now_ns=100)
      self.assertIs(result.state, ledger)
      self.assertIsNone(result.change)
      self.assertEqual(result.status, "foreign_session")

  def test_conflicting_final_resolution_latches_closed(self):
    ledger = resolve(begin(), arb.Disposition.SLC_CONSUMED).state
    conflict = resolve(ledger, arb.Disposition.DRIVER_INTENT, now=3)
    self.assertTrue(conflict.errors)
    self.assertTrue(conflict.state.reset_required)
    self.assertIsNone(change(conflict.state, now=4).change)
    self.assertEqual(conflict.state.transactions[0].disposition, arb.Disposition.SLC_CONSUMED)

  def test_repeated_matching_resolution_is_idempotent(self):
    ledger = resolve(begin()).state
    repeated = resolve(ledger, now=3)
    self.assertEqual(repeated.status, "already_resolved")
    self.assertFalse(repeated.state.reset_required)

  def test_origin_cannot_be_reclassified_as_driver_cruise(self):
    for origin in (arb.Origin.AUTOMATIC, arb.Origin.DRIVER_OTHER):
      conflict = resolve(begin(origin=origin))
      self.assertTrue(conflict.errors)
      self.assertIsNone(change(conflict.state).change)

  def test_capacity_exhaustion_preserves_consumption_and_latches(self):
    ledger = arb.new_ledger(SESSION)
    for action_id in range(arb.MAX_TRANSACTIONS):
      ledger = begin(ledger, action_id=action_id, now=action_id)
    ledger = resolve(ledger, arb.Disposition.SLC_CONSUMED, action_id=0, now=arb.MAX_TRANSACTIONS).state
    full = arb.step(ledger, arb.BeginAction(SESSION, arb.MAX_TRANSACTIONS, arb.Origin.DRIVER_CRUISE, "ctx"),
                    now_ns=arb.MAX_TRANSACTIONS + 1)
    self.assertEqual(full.status, "session_reset_required")
    self.assertEqual(len(full.state.transactions), arb.MAX_TRANSACTIONS)
    self.assertEqual(full.state.transactions[0].disposition, arb.Disposition.SLC_CONSUMED)
    self.assertIsNone(change(full.state, now=arb.MAX_TRANSACTIONS + 2).change)
    self.assertFalse(arb.new_ledger("next-drive").reset_required)

  def test_explicit_completion_releases_capacity(self):
    ledger = arb.new_ledger(SESSION)
    for action_id in range(arb.MAX_TRANSACTIONS + 10):
      ledger = begin(ledger, action_id=action_id, now=2 * action_id)
      ledger = arb.step(ledger, arb.CompleteAction(SESSION, action_id), now_ns=2 * action_id + 1).state
      self.assertFalse(ledger.reset_required)
    self.assertEqual(ledger.transactions, ())

  def test_invalid_speeds_and_ids_latch_without_classifying(self):
    event = arb.CruiseChange(SESSION, 1, 1, 20.0, 25.0)
    invalid = [replace(event, selected_mps=v) for v in (True, 0, -1, float("nan"), float("inf"), 10 ** 1000)]
    invalid += [replace(event, effect_id=True), replace(event, action_id=-1), replace(event, previous_mps=0)]
    for supplied in invalid:
      result = arb.step(resolve(begin()).state, supplied, now_ns=3)
      self.assertTrue(result.errors)
      self.assertIsNone(result.change)
      self.assertTrue(result.state.reset_required)

  def test_reversed_clock_latches_and_invalid_state_is_inert(self):
    ledger = resolve(begin()).state
    reversed_time = change(ledger, now=0)
    self.assertTrue(reversed_time.errors)
    self.assertTrue(reversed_time.state.reset_required)
    for invalid in (replace(ledger, transactions=cast(Any, [])), replace(ledger, last_action_id=-1),
                    replace(ledger, transactions=ledger.transactions * 2), replace(ledger, session_id="")):
      result = change(invalid)
      self.assertIs(result.state, invalid)
      self.assertIsNone(result.change)
      self.assertEqual(result.status, "invalid_state")

  def test_receipt_replay_errors_and_wrong_action_do_not_resolve(self):
    pending = accept.step(accept.new_session(SESSION), OBSERVATION, AUTHORITY, accept.Policy(), now_ns=0)
    accepted = accept.step(pending.state, OBSERVATION, AUTHORITY, accept.Policy(), now_ns=2,
                           action=accept.DriverAction(SESSION, 1, pending.state.pending.decision_id, accept.ActionKind.ACCEPT))
    assert accepted.action_receipt is not None
    for decision in (replace(accepted, errors=("invalid input",)), replace(accepted, action_receipt=None),
                     replace(accepted, action_receipt=replace(accepted.action_receipt, sequence_id=2)),
                     replace(accepted, action_receipt=replace(accepted.action_receipt, consumed=False, status="seen")),
                     replace(accepted, action_receipt=replace(accepted.action_receipt, consumed=False, status="already_seen"))):
      result = arb.resolve_acceptance(begin(), 1, decision, now_ns=2)
      self.assertEqual(result.status, "unresolved_acceptance")
      self.assertEqual(change(result.state).change.disposition, arb.Disposition.UNRESOLVED)

  def test_malformed_acceptance_result_cannot_classify_driver_intent(self):
    decision = accept.Decision(accept.new_session(SESSION))
    for malformed in (replace(decision, state=cast(Any, None)),
                      replace(decision, action_receipt=cast(Any, "consumed")),
                      replace(decision, action_receipt=accept.ActionReceipt(1, True, cast(Any, 0), "accepted"))):
      result = arb.resolve_acceptance(begin(), 1, malformed, now_ns=2)
      self.assertTrue(result.errors)
      self.assertTrue(result.state.reset_required)
      self.assertIsNone(change(result.state).change)

  def test_acceptance_clock_must_belong_to_transaction_lifecycle(self):
    pending = accept.step(accept.new_session(SESSION), OBSERVATION, AUTHORITY, accept.Policy(), now_ns=0)
    accepted = accept.step(pending.state, OBSERVATION, AUTHORITY, accept.Policy(), now_ns=2,
                           action=accept.DriverAction(SESSION, 1, pending.state.pending.decision_id, accept.ActionKind.ACCEPT))
    for timestamp in (0, 100):
      invalid = replace(accepted, state=replace(accepted.state, last_timestamp_ns=timestamp))
      result = arb.resolve_acceptance(begin(), 1, invalid, now_ns=3)
      self.assertTrue(result.errors)
      self.assertIsNone(change(result.state, now=4).change)

  def test_ledger_is_immutable(self):
    ledger = begin()
    resolve(ledger)
    self.assertEqual(ledger.transactions[0].disposition, arb.Disposition.UNRESOLVED)
    with self.assertRaises(FrozenInstanceError):
      cast(Any, ledger).session_id = "changed"


if __name__ == "__main__":
  unittest.main()
