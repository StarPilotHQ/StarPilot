"""Accepted limits and offsets convert to the native planner coordinate once."""

import unittest
from dataclasses import replace
from typing import Any, cast

from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import speed_domain as sd

SESSION = 'coordinates'
ACTIVE = acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.SYSTEM, False, True, False, False)
POLICY = acc.Policy(confirm_lower=False, confirm_higher=False)
SCHEDULE = sd.OffsetSchedule((sd.OffsetBand(0., None, 2.),))


def candidate(speed):
  return acc.Candidate('map', acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value=f'road-{speed}'), speed)


class SpeedDomainTests(unittest.TestCase):
  def setUp(self):
    self.accepted = acc.step(acc.new_session(SESSION), acc.Observation(acc.ObservationKind.VALID, candidate(20.)),
                             ACTIVE, POLICY, now_ns=0)

  def pair(self, raw=20., cluster=20., *, now=0, session=SESSION):
    return sd.SpeedPair(sd.DomainStatus.VALID, raw, cluster, session, now)

  def test_offset_and_ego_delta_apply_once(self):
    selected = sd.selected_pair_from_kph(30., 31. * 3.6, session_id=SESSION, timestamp_ns=0)
    assert selected.cluster_mps is not None
    self.assertAlmostEqual(selected.cluster_mps, 31.)
    domain = sd.resolve(self.accepted, SCHEDULE, selected, self.pair(25., 26.))
    assert domain.context is not None
    coordinate = sd.to_planner_coordinate(domain.context)
    self.assertEqual(coordinate.cap_mps, 21.)
    self.assertEqual(coordinate.cluster_target_mps, 22.)
    self.assertEqual(coordinate.ego_delta_mps, 1.)

  def test_negative_offsets_cannot_remove_the_ceiling(self):
    for offset, delta in ((-20., 0.), (-30., 0.), (-19., 2.)):
      with self.subTest(offset=offset, delta=delta):
        domain = sd.resolve(self.accepted, sd.OffsetSchedule((sd.OffsetBand(0., None, offset),)),
                            self.pair(), self.pair(20., 20. + delta))
        assert domain.context is not None
        coordinate = sd.to_planner_coordinate(domain.context)
        self.assertEqual(coordinate.cluster_target_mps, 20.)
        self.assertEqual(coordinate.cap_mps, 20. - delta)

  def test_foreign_or_old_coordinates_cannot_bind_to_acceptance(self):
    for selected, ego in ((self.pair(now=1), self.pair()), (self.pair(session='other'), self.pair())):
      result = sd.resolve(self.accepted, SCHEDULE, selected, ego)
      self.assertEqual(result.status, sd.DomainStatus.INVALID)
      self.assertIsNone(result.context)

  def test_malformed_acceptance_and_policy_are_invalid(self):
    for decision in (
      replace(self.accepted, state=replace(self.accepted.state, accepted='bad')),
      replace(self.accepted, state=replace(self.accepted.state, accepted=acc.AcceptedLimit(cast(Any, 'bad'), SESSION, 0))),
      replace(self.accepted, state=replace(self.accepted.state, observation='bad')),
      replace(self.accepted, policy=replace(POLICY, display_only=True)),
      replace(self.accepted, policy=replace(POLICY, confirm_lower='bad')),
      replace(self.accepted, state=replace(self.accepted.state, observation=acc.Observation(acc.ObservationKind.STALE))),
    ):
      result = sd.resolve(decision, SCHEDULE, self.pair(), self.pair())
      self.assertEqual(result.status, sd.DomainStatus.INVALID)
      self.assertIsNone(result.context)

  def test_missing_source_can_only_reuse_the_accepted_limit(self):
    missing = acc.step(self.accepted.state, acc.Observation(acc.ObservationKind.UNKNOWN), ACTIVE, POLICY, now_ns=1)
    pair = self.pair(now=1)
    result = sd.resolve(missing, SCHEDULE, pair, pair)
    assert result.context is not None
    self.assertEqual(result.context.accepted_raw_mps, 20.)
    self.assertEqual(sd.to_planner_coordinate(result.context).cap_mps, 22.)
    self.assertEqual(sd.resolve(replace(missing, control_target_mps=21.), SCHEDULE, pair, pair).status, sd.DomainStatus.INVALID)
    unavailable = sd.resolve(missing, SCHEDULE, sd.SpeedPair(sd.DomainStatus.STALE), pair)
    self.assertEqual(unavailable.status, sd.DomainStatus.UNAVAILABLE)

  def test_cluster_measurements_below_raw_do_not_add_a_delta(self):
    domain = sd.resolve(self.accepted, SCHEDULE, self.pair(20., 19.), self.pair(20., 19.))
    assert domain.context is not None
    coordinate = sd.to_planner_coordinate(domain.context)
    self.assertEqual(coordinate.cap_mps, 22.)
    self.assertEqual(coordinate.ego_delta_mps, 0.)

  def test_stock_and_inactive_modes_cannot_gain_control(self):
    for authority in (
      acc.Authority(acc.Mode.OFF, acc.LongitudinalOwner.NONE, False, False, False, True),
      acc.Authority(acc.Mode.LATERAL_ONLY, acc.LongitudinalOwner.NONE, True, False, False, False),
      acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.STOCK, False, False, True, False),
    ):
      accepted = acc.step(self.accepted.state, self.accepted.state.observation, authority, POLICY, now_ns=0)
      self.assertEqual(sd.resolve(accepted, SCHEDULE, self.pair(), self.pair()).status, sd.DomainStatus.UNAVAILABLE)

  def test_composed_cap_reaches_native_planner_without_mutating_inputs(self):
    from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
    from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling
    from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import V_EGO, message_bytes, messages, snapshot
    from opendbc.car.honda.interface import CarInterface
    from opendbc.car.honda.values import CAR

    accepted = acc.step(acc.new_session(SESSION), acc.Observation(acc.ObservationKind.VALID, candidate(15.)), ACTIVE, POLICY, now_ns=0)
    domain = sd.resolve(accepted, SCHEDULE, self.pair(25., 26.), self.pair(20., 21.))
    assert domain.context is not None
    coordinate = sd.to_planner_coordinate(domain.context)
    assert coordinate.cap_mps is not None
    self.assertEqual(coordinate.cap_mps, 16.)
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    ordinary, composed = LongitudinalPlanner(cp, init_v=V_EGO), LongitudinalPlanner(cp, init_v=V_EGO)
    for _ in range(40):
      left, left_envelopes = messages()
      right, right_envelopes = messages()
      for sm in (left, right):
        sm['carState'].vCruise, sm['carState'].vCruiseCluster, sm['carState'].vEgoCluster = 90., 93.6, 21.
      before = message_bytes((*left_envelopes, *right_envelopes))
      ordinary.update(left)
      composed.update(right, cruise_ceiling=CruiseCeiling(coordinate.cap_mps, ACTIVE))
      self.assertEqual(before, message_bytes((*left_envelopes, *right_envelopes)))
      self.assertEqual(ordinary.mpc.solution_status, 0)
      self.assertEqual(composed.mpc.solution_status, 0)
    self.assertEqual(composed.last_cruise_ceiling_status, 'applied')
    self.assertNotEqual(snapshot(ordinary), snapshot(composed))
