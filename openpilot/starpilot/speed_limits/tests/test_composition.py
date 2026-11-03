"""Actual SLC reducers compose into an optional native planner ceiling."""

import unittest
from dataclasses import replace

from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import action_arbitration as arb
from openpilot.starpilot.speed_limits import composition as comp
from openpilot.starpilot.speed_limits import selection as sel
from openpilot.starpilot.speed_limits import speed_domain as sd

SESSION = "composed-drive"
ACTIVE = acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.SYSTEM, False, True, False, False)
POLICY = acc.Policy(confirm_lower=False, confirm_higher=False)
SELECT = sel.SelectionPolicy(sel.SelectionMode.ORDERED, (sel.Source.DASHBOARD, sel.Source.MAP), False)
OFFSET = sd.OffsetSchedule((sd.OffsetBand(0.0, None, 2.0),))
HOST = comp.HostState(True, True, True, False, 100.0, 100.0)


def observations(speed: float | None = 15.0, *, kind: acc.ObservationKind = acc.ObservationKind.VALID):
  if kind is acc.ObservationKind.VALID:
    assert speed is not None
  candidate_speed = speed if speed is not None else 0.0
  dashboard = acc.Observation(kind, acc.Candidate(sel.Source.DASHBOARD.value,
                            acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value="road"), candidate_speed)
                              if kind is acc.ObservationKind.VALID else None)
  return {sel.Source.DASHBOARD: dashboard,
          sel.Source.MAP: acc.Observation(acc.ObservationKind.ABSENT),
          sel.Source.VISION: acc.Observation(acc.ObservationKind.ABSENT),
          sel.Source.ONLINE: acc.Observation(acc.ObservationKind.ABSENT)}


def frame(now: int, *, speed: float | None = 15.0, source_kind: acc.ObservationKind = acc.ObservationKind.VALID,
          authority: acc.Authority = ACTIVE, host: comp.HostState = HOST,
          policy: acc.Policy = POLICY, offset: sd.OffsetSchedule = OFFSET,
          selected_kind: sd.DomainStatus = sd.DomainStatus.VALID,
          ego_kind: sd.DomainStatus = sd.DomainStatus.VALID,
          ego_raw: float = 20.0, ego_cluster: float = 20.0, action: acc.DriverAction | None = None) -> comp.Frame:
  ego_pair = (sd.SpeedPair(ego_kind, ego_raw, ego_cluster, SESSION, now) if ego_kind is sd.DomainStatus.VALID
              else sd.SpeedPair(ego_kind))
  return comp.Frame(now, observations(speed, kind=source_kind), SELECT, authority, policy, offset,
                    selected_kind, ego_pair, False, host, action=action)


class CompositionTests(unittest.TestCase):
  def setUp(self):
    accepted = acc.AcceptedLimit(observations()[sel.Source.DASHBOARD].candidate, SESSION, 0)
    self.state = comp.new_session(SESSION, accepted)
    self.state = replace(self.state, acceptance=replace(self.state.acceptance, received_speed_mps=15.))

  def run_frame(self, current: comp.Frame) -> comp.Result:
    result = comp.step(self.state, current)
    self.state = result.state
    return result

  def test_real_chain_applies_offset_and_ego_delta_once(self):
    result = self.run_frame(frame(0, ego_raw=20.0, ego_cluster=21.0))
    self.assertFalse(result.errors)
    self.assertEqual(result.selection.selected_source, sel.Source.DASHBOARD)
    self.assertEqual(result.acceptance.control_target_mps, 15.0)
    self.assertEqual(result.domain.context.offset_mps, 2.0)
    self.assertEqual(result.coordinate.cap_mps, 16.0)
    self.assertEqual(result.ceiling.speed_mps, 16.0)

  def test_source_gaps_keep_the_applied_ceiling_without_accepting_a_new_limit(self):
    self.run_frame(frame(0))
    accepted = self.state.acceptance.accepted
    for now, kind in enumerate((acc.ObservationKind.ABSENT, acc.ObservationKind.UNKNOWN, acc.ObservationKind.STALE), 1):
      result = self.run_frame(frame(now, source_kind=kind))
      self.assertEqual(result.acceptance.basis, kind.value)
      self.assertEqual(result.acceptance.control_target_mps, 15.)
      self.assertEqual(result.state.acceptance.accepted, accepted)
      self.assertEqual(result.domain.context.accepted_raw_mps, 15.)
      self.assertEqual(result.coordinate.cap_mps, 17.)
      self.assertEqual(result.ceiling.speed_mps, 17.0)

  def test_uninitialized_driver_and_missing_coordinates_suppress(self):
    unset = self.run_frame(frame(0, host=replace(HOST, driver_v_cruise_kph=255.0)))
    self.assertIsNone(unset.ceiling)
    self.assertEqual(unset.acceptance.control_target_mps, 15.0)
    missing = self.run_frame(frame(1, selected_kind=sd.DomainStatus.STALE))
    self.assertIsNone(missing.ceiling)
    resumed = self.run_frame(frame(2))
    self.assertEqual(resumed.domain.context.effective_cluster_mps, 17.0)

  def test_four_modes_stock_and_host_loss(self):
    cases = (
      acc.Authority(acc.Mode.OFF, acc.LongitudinalOwner.NONE, False, False, False, True),
      acc.Authority(acc.Mode.LATERAL_ONLY, acc.LongitudinalOwner.NONE, True, False, False, False),
      acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.STOCK, False, False, True, False),
      ACTIVE,
      acc.Authority(acc.Mode.COMBINED, acc.LongitudinalOwner.SYSTEM, True, True, False, False),
    )
    for authority in cases:
      with self.subTest(authority=authority):
        self.state = comp.new_session(SESSION)
        host = HOST if authority.can_control else replace(HOST, system_long_available=False, long_control_active=False)
        result = self.run_frame(frame(0, authority=authority, host=host))
        self.assertEqual(result.ceiling is not None, authority.can_control)
    self.state = comp.new_session(SESSION)
    self.run_frame(frame(0))
    lost = self.run_frame(frame(1, authority=replace(ACTIVE, longitudinal_active=False),
                                host=replace(HOST, long_control_active=False)))
    self.assertIsNone(lost.ceiling)
    self.assertFalse(lost.state.reset_required)
    resumed = self.run_frame(frame(2))
    self.assertEqual(resumed.ceiling.speed_mps, 17.)

  def test_host_authority_disagreement_does_not_advance_acceptance_time(self):
    self.run_frame(frame(0))
    old_acc = self.state.acceptance
    mismatch = self.run_frame(frame(100_000_000, host=replace(HOST, long_control_active=False)))
    self.assertIsNone(mismatch.ceiling)
    self.assertTrue(mismatch.errors)
    self.assertTrue(mismatch.state.reset_required)
    self.assertEqual(mismatch.state.acceptance, old_acc)

  def test_late_error_preserves_consumed_action_receipt_and_history(self):
    pending_policy = replace(POLICY, confirm_lower=True)
    self.run_frame(frame(0, speed=25.0))
    pending = self.run_frame(frame(1, speed=15.0, policy=pending_policy))
    self.assertIsNotNone(pending.acceptance.state.pending)
    action = acc.DriverAction(SESSION, 1, pending.acceptance.state.pending.decision_id, acc.ActionKind.ACCEPT)
    bad_offset = sd.OffsetSchedule(())
    failed = self.run_frame(frame(2, speed=15.0, policy=pending_policy, action=action, offset=bad_offset))
    self.assertIsNone(failed.ceiling)
    self.assertTrue(failed.errors)
    self.assertTrue(failed.state.reset_required)
    self.assertTrue(failed.acceptance.action_receipt.consumed)
    self.assertEqual(failed.acceptance.state.accepted.candidate.speed_mps, 15.0)
    self.assertEqual(failed.state.acceptance, failed.acceptance.state)

  def test_rejected_lower_limit_survives_source_loss_and_reconnect(self):
    confirmation = replace(POLICY, confirm_lower=True)
    initial = self.run_frame(frame(0, speed=25.0, policy=confirmation))
    self.assertEqual(initial.ceiling.speed_mps, 27.0)
    initial_accepted = acc.AcceptedLimit(observations(25.)[sel.Source.DASHBOARD].candidate, SESSION, 0)
    self.state = replace(self.state, acceptance=replace(self.state.acceptance, accepted=initial_accepted, pending=None, timer_running=False))
    pending = self.run_frame(frame(1, speed=15.0, policy=confirmation))
    assert pending.acceptance.state.pending is not None
    reject = acc.DriverAction(SESSION, 1, pending.acceptance.state.pending.decision_id, acc.ActionKind.REJECT)
    rejected = self.run_frame(frame(2, speed=15.0, policy=confirmation, action=reject))
    absent = self.run_frame(frame(3, source_kind=acc.ObservationKind.ABSENT, policy=confirmation))
    reconnected = self.run_frame(frame(4, speed=15.0, policy=confirmation))
    self.assertTrue(rejected.acceptance.action_receipt.consumed)
    self.assertEqual(absent.acceptance.basis, "absent")
    self.assertEqual(reconnected.acceptance.basis, "rejected")
    for result in (rejected, absent, reconnected):
      self.assertFalse(result.errors)
      self.assertEqual(result.acceptance.state.accepted.candidate.speed_mps, 25.0)
      self.assertEqual(result.domain.context.accepted_raw_mps, 25.0)
      self.assertEqual(result.ceiling.speed_mps, 27.0)

  def test_reversed_monotonic_clock_latches_without_ceiling(self):
    first = self.run_frame(frame(10))
    self.assertEqual(first.ceiling.speed_mps, 17.0)
    prior_acceptance = self.state.acceptance
    reversed_time = self.run_frame(frame(9))
    self.assertEqual(reversed_time.status, "invalid_frame")
    self.assertTrue(reversed_time.state.reset_required)
    self.assertIsNone(reversed_time.ceiling)
    self.assertEqual(reversed_time.state.acceptance, prior_acceptance)
    latched = self.run_frame(frame(11))
    self.assertEqual(latched.status, "session_reset_required")
    self.assertIsNone(latched.ceiling)

  def test_causal_driver_cruise_change_does_not_bypass_the_slc_cap(self):
    self.run_frame(frame(0))
    ledger = arb.step(arb.new_ledger(SESSION),
                      arb.BeginAction(SESSION, 1, arb.Origin.DRIVER_CRUISE, 'driver-set'), now_ns=0).state
    ledger = arb.step(ledger, arb.ResolveAction(SESSION, 1, arb.Disposition.DRIVER_INTENT), now_ns=0).state
    changed = arb.step(ledger, arb.CruiseChange(SESSION, 1, 1, 100.0 / 3.6, 110.0 / 3.6), now_ns=1)
    second_frame = replace(frame(1, host=replace(HOST, driver_v_cruise_kph=110.0, selected_cluster_kph=110.0)),
                           classified_cruise=changed)
    second = self.run_frame(second_frame)
    self.assertEqual(second.ceiling.speed_mps, 17.0)
    third = self.run_frame(frame(2, host=replace(HOST, driver_v_cruise_kph=105.0, selected_cluster_kph=106.0)))
    self.assertEqual(third.ceiling.speed_mps, 17.0)

  def test_old_ledger_result_does_not_become_a_current_speed_action(self):
    self.run_frame(frame(0))
    ledger = arb.step(arb.new_ledger(SESSION),
                      arb.BeginAction(SESSION, 1, arb.Origin.DRIVER_CRUISE, 'driver-set'), now_ns=0).state
    ledger = arb.step(ledger, arb.ResolveAction(SESSION, 1, arb.Disposition.DRIVER_INTENT), now_ns=0).state
    old = arb.step(ledger, arb.CruiseChange(SESSION, 1, 1, 100.0 / 3.6, 110.0 / 3.6), now_ns=1)
    rejected = self.run_frame(replace(frame(2, host=replace(HOST, driver_v_cruise_kph=110.0)),
                                      classified_cruise=old))
    self.assertTrue(rejected.errors)
    self.assertIsNone(rejected.ceiling)
    self.assertEqual(rejected.state.acceptance.last_timestamp_ns, 0)

  def test_new_limit_supplies_a_separate_planner_cap(self):
    accepted = self.run_frame(frame(0, speed=20.))
    self.assertEqual(accepted.acceptance.basis, 'accepted')
    self.assertEqual(accepted.domain.context.effective_cluster_mps, 22.)
    self.assertEqual(accepted.ceiling.speed_mps, 22.)

  def test_actual_native_planner_default_and_optional_cap_transitions(self):
    from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
    from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import V_EGO, message_bytes, messages, snapshot
    from opendbc.car.honda.interface import CarInterface
    from opendbc.car.honda.values import CAR

    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    for scenario in ("ordinary", "lead", "e2e", "force"):
      with self.subTest(scenario=scenario):
        self.state = comp.new_session(SESSION)
        default = LongitudinalPlanner(cp, init_v=V_EGO)
        optional = LongitudinalPlanner(cp, init_v=V_EGO)
        deviations = []
        last = None
        for n in range(40):
          active = n >= 12
          host = replace(HOST, force_decel=scenario == "force" and active)
          current = frame(n * 50_000_000, source_kind=(acc.ObservationKind.VALID if active else
                                                        acc.ObservationKind.UNKNOWN), host=host,
                          ego_raw=20.0, ego_cluster=21.0)
          result = self.run_frame(current)
          conditions = {"lead": scenario == "lead" and active, "e2e": scenario == "e2e" and active,
                        "force": scenario == "force" and active}
          left, left_envelopes = messages(**conditions)
          right, right_envelopes = messages(**conditions)
          assert current.ego_pair.raw_mps is not None and current.ego_pair.cluster_mps is not None
          assert current.host.selected_cluster_kph is not None
          for sm in (left, right):
            sm["carState"].vEgo = current.ego_pair.raw_mps
            sm["carState"].vEgoCluster = current.ego_pair.cluster_mps
            sm["carState"].vCruise = current.host.driver_v_cruise_kph
            sm["carState"].vCruiseCluster = current.host.selected_cluster_kph
            self.assertAlmostEqual(sm["carState"].vEgo, current.ego_pair.raw_mps)
            self.assertAlmostEqual(sm["carState"].vEgoCluster, current.ego_pair.cluster_mps)
            self.assertAlmostEqual(sm["carState"].vCruise, current.host.driver_v_cruise_kph)
            self.assertAlmostEqual(sm["carState"].vCruiseCluster, current.host.selected_cluster_kph)
          before = message_bytes((*left_envelopes, *right_envelopes))
          default.update(left)
          optional.update(right, cruise_ceiling=result.ceiling)
          self.assertEqual(before, message_bytes((*left_envelopes, *right_envelopes)))
          self.assertEqual(default.mpc.solution_status, 0)
          self.assertEqual(optional.mpc.solution_status, 0)
          if snapshot(default) != snapshot(optional):
            deviations.append(n)
          if not active or scenario == "force":
            self.assertIsNone(result.ceiling)
            self.assertEqual(snapshot(default), snapshot(optional))
          last = (snapshot(default), snapshot(optional), optional.last_cruise_ceiling_status)
        if scenario == "force":
          self.assertEqual(deviations, [])
          self.assertEqual(last[2], "absent")
        else:
          self.assertEqual(last[2], "applied")
          if scenario == 'ordinary':
            self.assertEqual(deviations[0], 12)
          else:
            self.assertEqual(last[0][0], last[1][0])
          if scenario == "lead":
            self.assertEqual(last[1][2], "1")
          elif scenario == "e2e":
            self.assertEqual(last[1][2], "4")


if __name__ == "__main__":
  unittest.main()
