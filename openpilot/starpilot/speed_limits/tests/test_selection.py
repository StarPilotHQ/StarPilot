from itertools import permutations, product
import math
from typing import Any, cast
import unittest
from dataclasses import replace

from openpilot.starpilot.speed_limits.acceptance import Candidate, IdentityKind, Observation, ObservationIdentity, ObservationKind
from openpilot.starpilot.speed_limits.acceptance import (AcceptedLimit, ActionKind, AdoptRequest, Authority, DriverAction,
                                                        LongitudinalOwner, Mode, Policy, new_session, step)
from openpilot.starpilot.speed_limits.selection import SelectionMode, SelectionPolicy, Source, select_limit


def geographic(value):
  return ObservationIdentity(IdentityKind.GEOGRAPHIC, value=value)


def observations(**speeds):
  values = {source: Observation(ObservationKind.ABSENT) for source in Source}
  values.update({Source(source): Observation(ObservationKind.VALID, Candidate(source, geographic(f"{source}-zone"), speed))
                 for source, speed in speeds.items()})
  return values


def policy(mode=SelectionMode.ORDERED, slots=(Source.VISION, Source.MAP), online=True):
  return SelectionPolicy(mode, slots, online)


class TestSourceSelection(unittest.TestCase):
  def test_priority_order_and_unused_source(self):
    supplied = observations(dashboard=30, map=20, vision=10, online=40)
    for first, second in permutations((Source.DASHBOARD, Source.MAP, Source.VISION), 2):
      with self.subTest(first=first, second=second):
        selected = select_limit(supplied, policy(slots=(first, second)))
        self.assertIs(selected.observation, supplied[first])
        self.assertEqual(selected.selected_source, first)
        missing = dict(supplied)
        missing[first] = Observation(ObservationKind.ABSENT)
        self.assertEqual(select_limit(missing, policy(slots=(first, second))).selected_source, second)
    self.assertEqual(select_limit(supplied, policy(slots=(Source.MAP, Source.MAP))).selected_source, Source.MAP)

  def test_extrema_vision_requires_explicit_slot(self):
    for mode, vision in ((SelectionMode.HIGHEST, 40), (SelectionMode.LOWEST, 5)):
      for slots in ((Source.MAP, Source.DASHBOARD), (None, None), (Source.MAP, Source.MAP)):
        with self.subTest(mode=mode, slots=slots):
          selected = select_limit(observations(dashboard=30, map=20, vision=vision), policy(mode, slots))
          self.assertEqual(selected.selected_source, Source.DASHBOARD if mode == SelectionMode.HIGHEST else Source.MAP)
          self.assertNotIn(Source.VISION, selected.considered)
      for slots in ((Source.VISION, None), (None, Source.VISION), (None, None, Source.VISION)):
        selected = select_limit(observations(dashboard=30, map=20, vision=vision), policy(mode, slots))
        self.assertEqual(selected.selected_source, Source.VISION)

  def test_three_ranked_sources_fall_through_in_order(self):
    slots = (Source.MAP, Source.DASHBOARD, Source.VISION)
    supplied = observations(map=35, dashboard=45, vision=25)
    for source in slots:
      self.assertIs(select_limit(supplied, policy(slots=slots)).selected_source, source)
      supplied[source] = Observation(ObservationKind.STALE)

  def test_extrema_ties_keep_stable_dashboard_map_vision_order(self):
    original = observations(dashboard=20, map=20, vision=20)
    for mode in (SelectionMode.HIGHEST, SelectionMode.LOWEST):
      for order in permutations(Source):
        selected = select_limit({source: original[source] for source in order}, policy(mode))
        self.assertEqual(selected.selected_source, Source.DASHBOARD)
      original_without_dashboard = dict(original)
      original_without_dashboard[Source.DASHBOARD] = Observation(ObservationKind.ABSENT)
      self.assertEqual(select_limit(original_without_dashboard, policy(mode)).selected_source, Source.MAP)

  def test_online_never_preempts_valid_primary(self):
    for mode in SelectionMode:
      for online in (1.0, 50.0):
        selected = select_limit(observations(map=20, online=online), policy(mode))
        self.assertEqual(selected.selected_source, Source.MAP)
        self.assertNotIn(Source.ONLINE, selected.considered)

  def test_online_fallback_and_disabled_provider(self):
    # Dashboard is valid but not configured in ordered mode; it cannot preempt fallback.
    supplied = observations(dashboard=30, online=20)
    selected = select_limit(supplied, policy())
    self.assertEqual(selected.selected_source, Source.ONLINE)
    self.assertEqual(selected.reason, "online_fallback")
    for slots in ((Source.VISION, Source.MAP), (None, None)):
      selected = select_limit(supplied, policy(slots=slots, online=False))
      self.assertIsNone(selected.selected_source)
      self.assertEqual(selected.observation.kind, ObservationKind.ABSENT)

  def test_incomplete_sources_do_not_become_absent(self):
    missing = (ObservationKind.ABSENT, ObservationKind.UNKNOWN, ObservationKind.STALE)
    for first, second, online in product(missing, repeat=3):
      supplied = observations()
      for source, kind in ((Source.VISION, first), (Source.MAP, second), (Source.ONLINE, online)):
        supplied[source] = Observation(kind)
      selected = select_limit(supplied, policy())
      with self.subTest(first=first, second=second, online=online):
        self.assertIsNone(selected.selected_source)
        self.assertIsNone(selected.observation.candidate)
        if ObservationKind.UNKNOWN in (first, second, online):
          self.assertEqual(selected.observation.kind, ObservationKind.UNKNOWN)
        elif ObservationKind.STALE in (first, second, online):
          self.assertEqual(selected.observation.kind, ObservationKind.STALE)
        else:
          self.assertEqual(selected.observation.kind, ObservationKind.ABSENT)
        self.assertEqual(selected.unavailable, ((Source.VISION, first), (Source.MAP, second), (Source.ONLINE, online)))

  def test_healthy_alternative_can_replace_unavailable_source(self):
    for unavailable in (ObservationKind.UNKNOWN, ObservationKind.STALE):
      supplied = observations(map=20, online=25)
      supplied[Source.VISION] = Observation(unavailable)
      selected = select_limit(supplied, policy())
      self.assertEqual(selected.selected_source, Source.MAP)
      self.assertIn((Source.VISION, unavailable), selected.unavailable)
      supplied[Source.MAP] = Observation(ObservationKind.ABSENT)
      self.assertEqual(select_limit(supplied, policy()).selected_source, Source.ONLINE)

  def test_disabled_or_unselected_unknown_sources_do_not_block_absence(self):
    supplied = observations()
    supplied[Source.VISION] = supplied[Source.ONLINE] = Observation(ObservationKind.UNKNOWN)
    selected = select_limit(supplied, policy(slots=(Source.DASHBOARD, Source.MAP), online=False))
    self.assertEqual(selected.observation.kind, ObservationKind.ABSENT)

  def test_minimum_threshold_is_explicit_and_inclusive(self):
    supplied = observations(vision=math.nextafter(1.0, 0.0), map=1.0)
    selected = select_limit(supplied, policy())
    self.assertEqual(selected.selected_source, Source.MAP)
    self.assertEqual(selected.below_minimum, (Source.VISION,))
    supplied[Source.MAP] = Observation(ObservationKind.ABSENT)
    selected = select_limit(supplied, policy(online=False))
    self.assertEqual(selected.observation.kind, ObservationKind.ABSENT)

  def test_malformed_candidates_fail_closed(self):
    candidates = [Candidate("dashboard", geographic("zone"), 20), Candidate("vision", geographic(""), 20),
                  Candidate("vision", geographic("zone"), float("nan")), Candidate("vision", geographic("zone"), float("inf")),
                  Candidate("vision", geographic("zone"), 0), Candidate("vision", geographic("zone"), -1), Candidate("vision", geographic("zone"), True),
                  Candidate("vision", geographic("zone"), 10 ** 400), Candidate("vision", geographic("zone\x00"), 20)]
    for candidate in candidates:
      supplied = observations(map=20, online=30)
      supplied[Source.VISION] = Observation(ObservationKind.VALID, candidate)
      with self.subTest(candidate=candidate):
        selected = select_limit(supplied, policy())
        self.assertEqual(selected.reason, "invalid_input")
        self.assertEqual(selected.observation.kind, ObservationKind.UNKNOWN)
        self.assertIsNone(selected.selected_source)
        self.assertTrue(selected.errors)

  def test_structural_input_errors_fail_closed(self):
    supplied = observations(vision=20)
    invalid_observations: list[Any] = [None, {}, {source.value: value for source, value in supplied.items()},
                            {**supplied, Source.MAP: Observation(cast(Any, "valid"), Candidate("map", geographic("zone"), 20))},
                            {**supplied, Source.MAP: Observation(ObservationKind.STALE, Candidate("map", geographic("zone"), 20))},
                            {**supplied, Source.MAP: Observation(ObservationKind.VALID)}]
    invalid_policies: list[Any] = [None, SelectionPolicy(cast(Any, "ordered"), (Source.VISION, Source.MAP), True),
                        SelectionPolicy(SelectionMode.ORDERED, (Source.ONLINE, Source.MAP), True),
                        SelectionPolicy(SelectionMode.ORDERED, cast(Any, (Source.VISION,)), True),
                        SelectionPolicy(SelectionMode.ORDERED, (Source.VISION, Source.MAP), cast(Any, 1))]
    for malformed in invalid_observations:
      self.assertEqual(select_limit(malformed, policy()).reason, "invalid_input")
    for malformed in invalid_policies:
      self.assertEqual(select_limit(supplied, malformed).reason, "invalid_input")

  def test_rejected_limit_never_becomes_selected_fallback(self):
    for mode in (Mode.LONGITUDINAL_ONLY, Mode.COMBINED):
      with self.subTest(mode=mode):
        authority = Authority(mode, LongitudinalOwner.SYSTEM, mode == Mode.COMBINED, True, False, False)
        acceptance_policy = Policy(confirm_lower=True, confirm_higher=False)
        source_policy = policy(slots=(Source.DASHBOARD, None), online=False)
        candidate = observations(dashboard=29.0576)[Source.DASHBOARD].candidate
        state = replace(new_session("selection-regression", AcceptedLimit(candidate, "selection-regression", 0)), received_speed_mps=29.0576)
        # Values and sequence reproduce the observed accepted65 / reject45 / lost-source case.
        for index, (speed, reject) in enumerate(((29.0576, False), (20.1168, False), (20.1168, True), (None, False))):
          chosen = select_limit(observations(dashboard=speed) if speed is not None else observations(), source_policy)
          action = DriverAction(state.session_id, 1, state.pending.decision_id, ActionKind.REJECT) if reject else None
          decision = step(state, chosen.observation, authority, acceptance_policy, now_ns=index * 50_000_000, action=action)
          self.assertFalse(decision.errors)
          self.assertEqual(decision.control_target_mps, 29.0576)
          self.assertEqual(decision.state.accepted.candidate.speed_mps, 29.0576)
          self.assertEqual(decision.state.pending is not None, index == 1)
          if index > 0:
            self.assertIsNone(decision.history_write)
          if reject:
            self.assertTrue(decision.action_receipt.consumed)
          state = decision.state
        returned = select_limit(observations(dashboard=20.1168), source_policy)
        decision = step(state, returned.observation, authority, acceptance_policy, now_ns=200_000_000)
        self.assertEqual(decision.basis, "rejected")
        self.assertEqual(decision.control_target_mps, 29.0576)

  def test_uncertainty_and_axis_ownership_survive_composition(self):
    system = Authority(Mode.LONGITUDINAL_ONLY, LongitudinalOwner.SYSTEM, False, True, False, False)
    acceptance_policy = Policy(confirm_higher=False)
    source_policy = policy(slots=(Source.MAP, None), online=False)
    history = AcceptedLimit(observations(map=20)[Source.MAP].candidate, "uncertainty", 0)
    state = replace(new_session("uncertainty", history), received_speed_mps=20)
    accepted = step(state, select_limit(observations(map=20), source_policy).observation,
                    system, acceptance_policy, now_ns=0)
    for kind in (ObservationKind.UNKNOWN, ObservationKind.STALE):
      supplied = observations()
      supplied[Source.MAP] = Observation(kind)
      selected = select_limit(supplied, source_policy)
      decision = step(accepted.state, selected.observation, system, acceptance_policy, now_ns=1)
      self.assertEqual(decision.state.accepted, accepted.state.accepted)
      self.assertEqual(decision.control_target_mps, accepted.state.accepted.candidate.speed_mps)
      self.assertEqual(decision.state.observation.kind, kind)
    for authority in (Authority(Mode.LATERAL_ONLY, LongitudinalOwner.NONE, True, False, False, False),
                      Authority(Mode.LATERAL_ONLY, LongitudinalOwner.STOCK, True, False, True, False),
                      Authority(Mode.OFF, LongitudinalOwner.NONE, False, False, False, True)):
      selected = select_limit(observations(), source_policy)
      decision = step(accepted.state, selected.observation, authority, acceptance_policy, now_ns=1)
      self.assertEqual(decision.state.accepted, accepted.state.accepted)
      self.assertIsNone(decision.control_target_mps)

  def test_selector_preserves_identity_kind_without_inventing_geography(self):
    for identity in (geographic("road-1"), ObservationIdentity(IdentityKind.PRODUCER_EPISODE, value="episode-1"),
                     ObservationIdentity(IdentityKind.SESSION_VALUE, session_id="drive")):
      supplied = observations()
      candidate = Candidate("dashboard", identity, 20)
      supplied[Source.DASHBOARD] = Observation(ObservationKind.VALID, candidate)
      selected = select_limit(supplied, policy(slots=(Source.DASHBOARD, None)))
      self.assertIs(selected.observation.candidate, candidate)
      self.assertIs(selected.observation.candidate.observation_identity, identity)
      self.assertEqual(selected.errors, ())

  def test_acceptance_checks_session_after_structural_source_selection(self):
    supplied = observations()
    supplied[Source.DASHBOARD] = Observation(
      ObservationKind.VALID, Candidate("dashboard", ObservationIdentity(IdentityKind.SESSION_VALUE, session_id="previous-drive"), 20))
    selected = select_limit(supplied, policy(slots=(Source.DASHBOARD, None)))
    self.assertEqual(selected.errors, ())
    authority = Authority(Mode.LONGITUDINAL_ONLY, LongitudinalOwner.SYSTEM, False, True, False, False)
    decision = step(new_session("current-drive"), selected.observation, authority, Policy(False, False), now_ns=0)
    self.assertIn("invalid observation", decision.errors)
    self.assertIsNone(decision.control_target_mps)
    self.assertIsNone(decision.history_write)

  def test_dashboard_session_value_rejection_and_adopt_through_selection(self):
    authority = Authority(Mode.LONGITUDINAL_ONLY, LongitudinalOwner.SYSTEM, False, True, False, False)
    acceptance_policy = Policy(confirm_higher=False)
    selection_policy = policy(slots=(Source.DASHBOARD, None), online=False)
    candidate = Candidate("dashboard", ObservationIdentity(IdentityKind.SESSION_VALUE, session_id="drive"), 29.0576)
    state = replace(new_session("drive", AcceptedLimit(candidate, "drive", 0)), received_speed_mps=29.0576)
    for index, speed in enumerate((29.0576, 20.1168, 20.1168, None, 20.1168)):
      supplied = observations()
      if speed is not None:
        supplied[Source.DASHBOARD] = Observation(
          ObservationKind.VALID, Candidate("dashboard", ObservationIdentity(IdentityKind.SESSION_VALUE, session_id="drive"), speed))
      selected = select_limit(supplied, selection_policy)
      action = DriverAction("drive", 0, state.pending.decision_id, ActionKind.REJECT) if index == 2 else None
      decision = step(state, selected.observation, authority, acceptance_policy, now_ns=index, action=action)
      self.assertEqual(decision.errors, ())
      self.assertEqual(decision.control_target_mps, 29.0576)
      state = decision.state
    self.assertEqual(decision.basis, "rejected")
    shown = state.presentation
    request = AdoptRequest("drive", 1, shown.presentation_id, shown.candidate)
    adopted = step(state, selected.observation, authority, acceptance_policy, now_ns=5, adopt=request)
    self.assertTrue(adopted.adoption_receipt.consumed)
    self.assertEqual(adopted.control_target_mps, 20.1168)
    self.assertIsNone(adopted.state.rejected)
    self.assertEqual(adopted.adoption_receipt.sequence_id, request.sequence_id)
    self.assertEqual(adopted.history_write.candidate, shown.candidate)


if __name__ == "__main__":
  unittest.main()
