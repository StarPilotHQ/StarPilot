"""Behavioral traces from frozen 678af783 CEM/CCM and manual-state contracts."""

from dataclasses import replace
from typing import cast
import unittest

from openpilot.starpilot.conditional_mode import (
  Authority,
  ConditionalModePolicy,
  LeadEvidence,
  ManualIntent,
  ModeChoice,
  ModeSettings,
  Reason,
  SceneEvidence,
  next_manual_status,
  restore_manual_status,
)


AUTHORITY = Authority(True, True, False, True, True, True)
NO_LEAD = LeadEvidence(False, False, False)


def scene(now: float, speed: float = 20.0, **changes) -> SceneEvidence:
  initial = SceneEvidence(
    observed_mono_s=now,
    speed_mps=speed,
    set_speed_mps=speed + 3.0,
    lead=NO_LEAD,
    following_lead=False,
    left_blinker=False,
    right_blinker=False,
    lane_available=True,
    curve_detected=False,
    slow_lead_detected=False,
    stop_light_detected=False,
    standstill=False,
    traffic_mode=False,
    stop_sign_confirmed=False,
    forcing_stop=False,
    adjacent_lead_ambiguous=False,
    low_speed_stop_scene=False,
    launch_candidate=False,
    launch_forced_exit=False,
    launch_lead=False,
  )
  return replace(initial, **changes)


class ConditionalModePolicyTest(unittest.TestCase):
  def test_committed_stop_remains_experimental_without_detector_geometry(self):
    policy = ConditionalModePolicy()
    for tick in range(40):
      now = 10. + tick * .05
      evidence = scene(now, speed=0. if tick >= 20 else 8., standstill=tick >= 20,
                       stop_light_detected=None, standstill_stop_hold=None, forcing_stop=True)
      result = policy.step(now, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, evidence)
      self.assertEqual(result.reason, Reason.CEM_STOP)
      self.assertTrue(result.effective_longitudinal)
    override = policy.step(12., ModeChoice.CEM, ManualIntent.FORCE_CHILL, AUTHORITY,
                           scene(12., forcing_stop=True, stop_light_detected=None))
    self.assertEqual(override.reason, Reason.MANUAL_CHILL)
    self.assertFalse(override.effective_longitudinal)
    unavailable = policy.step(12.05, ModeChoice.CEM, ManualIntent.NONE, replace(AUTHORITY, fresh=False),
                              scene(12.05, forcing_stop=True))
    self.assertFalse(unavailable.effective_longitudinal)

  def test_stock_is_inert_and_lateral_only_is_not_long_authority(self):
    policy = ConditionalModePolicy()
    lateral_only = replace(AUTHORITY, long_active=False)
    stock = policy.step(10.0, ModeChoice.STOCK, ManualIntent.NONE, lateral_only, scene(10.0), stock_experimental=True)
    self.assertTrue(stock.requested_experimental)
    self.assertFalse(stock.effective_longitudinal)
    self.assertEqual(stock.reason, Reason.STOCK)
    self.assertTrue(stock.warm)
    self.assertFalse(policy.step(10.1, ModeChoice.STOCK, ManualIntent.NONE, AUTHORITY, scene(10.1)).requested_experimental)
    no_capability = policy.step(10.2, ModeChoice.STOCK, ManualIntent.NONE, replace(AUTHORITY, system_long_capable=False), scene(10.2), stock_experimental=True)
    self.assertTrue(no_capability.requested_experimental)
    self.assertFalse(no_capability.effective_longitudinal)

  def test_frozen_manual_wheel_cycle_and_persisted_normalization(self):
    # Frozen experimental_state.py numeric CE/CC statuses: CEM 1=Chill, 2=EXP;
    # CCM 1=EXP, 2=Chill. Both clear the override on the next press.
    self.assertEqual(next_manual_status(ModeChoice.CEM, 0, True), 1)
    self.assertEqual(next_manual_status(ModeChoice.CEM, 0, False), 2)
    self.assertEqual(next_manual_status(ModeChoice.CEM, 2, False), 0)
    self.assertEqual(next_manual_status(ModeChoice.CCM, 0, True), 2)
    self.assertEqual(next_manual_status(ModeChoice.CCM, 0, False), 1)
    self.assertEqual(next_manual_status(ModeChoice.CCM, 1, True), 0)
    self.assertEqual(restore_manual_status(ModeChoice.CEM, 0, 2, True), ManualIntent.FORCE_EXPERIMENTAL)
    self.assertEqual(restore_manual_status(ModeChoice.CEM, 1, 2, True), ManualIntent.FORCE_CHILL)
    self.assertEqual(restore_manual_status(ModeChoice.CEM, 0, 2, False), ManualIntent.NONE)
    self.assertEqual(restore_manual_status(ModeChoice.CCM, 0, 1, True), ManualIntent.FORCE_EXPERIMENTAL)
    self.assertEqual(restore_manual_status(ModeChoice.CCM, 3, 99, True), ManualIntent.NONE)

  def test_manual_intent_survives_scene_dropout_but_not_authority_dropout(self):
    policy = ConditionalModePolicy()
    stale_scene = scene(8.0)
    forced = policy.step(10.0, ModeChoice.CEM, ManualIntent.FORCE_EXPERIMENTAL, AUTHORITY, stale_scene)
    self.assertTrue(forced.requested_experimental)
    self.assertTrue(forced.effective_longitudinal)
    self.assertEqual((forced.reason, forced.status_code), (Reason.MANUAL_EXPERIMENTAL, 2))
    lateral_only = policy.step(10.1, ModeChoice.CEM, ManualIntent.FORCE_EXPERIMENTAL, replace(AUTHORITY, long_active=False), stale_scene)
    self.assertTrue(lateral_only.requested_experimental)
    self.assertFalse(lateral_only.effective_longitudinal)
    for unavailable in (replace(AUTHORITY, fresh=False), replace(AUTHORITY, system_long_capable=False), replace(AUTHORITY, safe_mode=True)):
      denied = policy.step(10.2, ModeChoice.CEM, ManualIntent.FORCE_EXPERIMENTAL, unavailable, stale_scene)
      self.assertFalse(denied.requested_experimental)
      self.assertEqual(denied.reason, Reason.UNAVAILABLE)

  def test_cem_frozen_speed_precedence_and_hysteresis_trace(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), cem_speed_mps=15.0, cem_curves=True)
    first = policy.step(10.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.0, speed=10.0, curve_detected=True), settings)
    self.assertEqual((first.requested_experimental, first.reason, first.status_code), (True, Reason.CEM_SPEED, 6))
    held = policy.step(10.1, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.1, speed=20.0), settings)
    self.assertEqual((held.reason, held.status_code), (Reason.CEM_HOLD, 6))
    self.assertTrue(held.requested_experimental)
    policy.step(10.3, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.3, speed=20.0), settings)
    self.assertTrue(policy.step(10.49, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.49, speed=20.0), settings).requested_experimental)
    self.assertFalse(policy.step(10.51, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.51, speed=20.0), settings).requested_experimental)

  def test_cem_frozen_trigger_order_and_unknown_not_false(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), cem_open_road=True, cem_signal_mps=15.0, cem_curves=True, cem_curves_with_lead=True)
    open_road = policy.step(1.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(1.0, speed=20.0, set_speed_mps=20.0, curve_detected=True), settings)
    self.assertEqual(open_road.reason, Reason.CEM_OPEN_ROAD)
    signal = policy.step(
      1.1, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(1.1, speed=10.0, left_blinker=True, lane_available=False, curve_detected=True), settings
    )
    self.assertEqual(signal.reason, Reason.CEM_SIGNAL)
    unknown_lane = policy.step(2.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(2.0, speed=10.0, left_blinker=True, lane_available=None), settings)
    self.assertEqual(unknown_lane.reason, Reason.NO_TRIGGER)
    self.assertFalse(unknown_lane.requested_experimental)

  def test_cem_stale_scene_clears_automatic_hold_and_reentry(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), cem_speed_mps=15.0)
    self.assertTrue(policy.step(1.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(1.0, speed=10.0), settings).requested_experimental)
    stale = policy.step(1.1, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(0.5, speed=10.0), settings)
    self.assertEqual((stale.reason, stale.requested_experimental), (Reason.SCENE_UNAVAILABLE, False))
    self.assertEqual(policy.step(1.2, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(1.2, speed=20.0), settings).reason, Reason.NO_TRIGGER)

  def test_cem_scene_reasons_preserve_frozen_priority_and_status(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), cem_curves=True)
    selected = policy.step(
      4.0,
      ModeChoice.CEM,
      ManualIntent.NONE,
      AUTHORITY,
      scene(4.0, curve_detected=True, slow_lead_detected=True, stop_light_detected=True),
      settings,
    )
    self.assertEqual((selected.reason, selected.status_code), (Reason.CEM_CURVE, 3))
    policy.reset()
    selected = policy.step(4.1, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(4.1, stop_light_detected=True), settings)
    self.assertEqual((selected.reason, selected.status_code), (Reason.CEM_STOP, 8))
    policy.reset()
    selected = policy.step(4.2, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(4.2), settings)
    self.assertEqual((selected.reason, selected.status_code), (Reason.NO_TRIGGER, 0))

  def test_cem_slow_lead_hold_requires_credible_current_lead(self):
    lead = LeadEvidence(True, True, False, 45.0, 15.0, 0.0, 0.9)
    for loses_lead in (False, True):
      with self.subTest(loses_lead=loses_lead):
        policy = ConditionalModePolicy()
        first = policy.step(10.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.0, lead=lead, following_lead=True, slow_lead_detected=True))
        self.assertEqual((first.requested_experimental, first.status_code), (True, 4))
        for tick in range(1, 31):
          now = 10.0 + tick * 0.05
          current_lead = NO_LEAD if loses_lead and tick >= 8 else lead
          result = policy.step(now, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(now, lead=current_lead, following_lead=not loses_lead))
          self.assertEqual(result.requested_experimental, tick < (10 if loses_lead else 30))
        # Reappearing geometry without a new slow-lead trigger cannot revive it.
        self.assertFalse(policy.step(11.55, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(11.55, lead=lead, following_lead=True)).requested_experimental)

  def test_cem_open_road_handoff_is_bounded_and_rechecks_closing_lead(self):
    settings = replace(ModeSettings(), cem_open_road=True)
    for closes_fast in (False, True):
      with self.subTest(closes_fast=closes_fast):
        policy = ConditionalModePolicy()
        first = policy.step(10.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.0, set_speed_mps=20.0), settings)
        self.assertEqual(first.reason, Reason.CEM_OPEN_ROAD)
        for tick in range(1, 18):
          now = 10.0 + tick * 0.05
          lead = LeadEvidence(True, True, True, 60.0, 15.0 if closes_fast and tick >= 11 else 20.0, 0.0)
          result = policy.step(now, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(now, set_speed_mps=20.0, lead=lead, following_lead=True), settings)
          self.assertEqual(result.requested_experimental, tick < (11 if closes_fast else 16))

  def test_cem_stop_release_suppresses_reentry_but_retains_new_stop_evidence(self):
    settings = replace(ModeSettings(), cem_speed_mps=15.0, cem_open_road=True)
    for release_at_standstill in (False, True):
      with self.subTest(release_at_standstill=release_at_standstill):
        policy = ConditionalModePolicy()
        held = policy.step(10.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.0, speed=0.0, standstill=True, standstill_stop_hold=True), settings)
        self.assertEqual((held.reason, held.status_code, held.requested_experimental), (Reason.CEM_STOP, 8, True))
        if release_at_standstill:
          released = policy.step(
            10.05, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.05, speed=0.0, standstill=True, standstill_stop_hold=False), settings
          )
          self.assertFalse(released.requested_experimental)
        for tick in range(41):
          now = 10.1 + tick * 0.05
          result = policy.step(now, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(now, speed=10.0, set_speed_mps=10.0, slow_lead_detected=True), settings)
          self.assertEqual(result.requested_experimental, tick == 40)
        # A newly qualified stop scene is never masked by launch suppression.
        policy.step(12.15, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(12.15, speed=0.0, standstill=True, standstill_stop_hold=True), settings)
        stop = policy.step(12.2, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(12.2, speed=10.0, stop_light_detected=True), settings)
        self.assertEqual((stop.reason, stop.status_code), (Reason.CEM_STOP, 8))

  def test_cem_unknown_standstill_scene_keeps_existing_stop_hold(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), cem_speed_mps=15.0)
    cold = policy.step(9.95, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(9.95, speed=0.0, standstill=True), settings)
    self.assertEqual((cold.reason, cold.requested_experimental), (Reason.SCENE_UNAVAILABLE, False))
    policy.step(10.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.0, speed=0.0, standstill=True, standstill_stop_hold=True), settings)
    for tick in range(1, 21):
      now = 10.0 + tick * 0.05
      unknown = policy.step(now, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(now, speed=0.0, standstill=True), settings)
      self.assertEqual((unknown.reason, unknown.requested_experimental), (Reason.CEM_STOP, True))
    released = policy.step(11.05, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY,
                           scene(11.05, speed=0.0, standstill=True, standstill_stop_hold=False), settings)
    self.assertFalse(released.requested_experimental)
    policy.step(11.1, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(9.0, speed=10.0), settings)
    cleared = policy.step(11.15, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(11.15, speed=20.0), settings)
    self.assertFalse(cleared.requested_experimental)

  def test_cem_manual_override_preserves_observed_stop_release_quiet_period(self):
    settings = replace(ModeSettings(), cem_speed_mps=15.0)
    for manual_on_release in (False, True):
      with self.subTest(manual_on_release=manual_on_release):
        policy = ConditionalModePolicy()
        policy.step(10.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.0, speed=0.0, standstill=True, standstill_stop_hold=True), settings)
        for tick in range(1, 43):
          now = 10.0 + tick * 0.05
          intent = ManualIntent.FORCE_CHILL if tick == (1 if manual_on_release else 3) else ManualIntent.NONE
          result = policy.step(now, ModeChoice.CEM, intent, AUTHORITY, scene(now, speed=10.0), settings)
          self.assertEqual(result.requested_experimental, tick >= 41)
        # A different mode/session must not inherit that CEM suppression.
        policy.step(12.15, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(12.15), settings)
        resumed = policy.step(12.2, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(12.2, speed=10.0), settings)
        self.assertEqual(resumed.reason, Reason.CEM_SPEED)

  def test_ccm_frozen_speed_entry_dwell_exit_and_veto(self):
    policy = ConditionalModePolicy()

    def fast(now):
      return scene(now, speed=55 * 0.44704, set_speed_mps=60 * 0.44704)

    first = policy.step(10.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, fast(10.0))
    self.assertTrue(first.requested_experimental)
    policy.step(10.2, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, fast(10.2))
    policy.step(10.4, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, fast(10.4))
    entered = policy.step(10.5, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, fast(10.5))
    self.assertEqual((entered.reason, entered.status_code, entered.requested_experimental), (Reason.CCM_SPEED, 6, False))
    held = policy.step(10.6, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(10.6, speed=20.0))
    self.assertEqual((held.reason, held.requested_experimental), (Reason.CCM_HOLD, False))
    for now in (10.8, 11.0, 11.2, 11.4):
      policy.step(now, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(now, speed=20.0))
    self.assertFalse(policy.step(11.6, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(11.6, speed=20.0)).requested_experimental)
    self.assertTrue(policy.step(11.71, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(11.71, speed=20.0)).requested_experimental)
    self.assertEqual(policy.step(12.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(12.0, speed=25.0, left_blinker=True)).reason, Reason.CCM_VETO)

  def test_ccm_unknown_veto_cannot_enter_chill(self):
    policy = ConditionalModePolicy()
    fast = scene(10.0, speed=25.0, set_speed_mps=30.0, adjacent_lead_ambiguous=None)
    first = policy.step(10.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, fast)
    later = policy.step(10.5, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, replace(fast, observed_mono_s=10.5))
    self.assertEqual((first.reason, later.reason), (Reason.SCENE_UNAVAILABLE, Reason.SCENE_UNAVAILABLE))
    self.assertTrue(later.requested_experimental)

  def test_ccm_stable_radar_lead_confirmation_and_hard_veto(self):
    policy = ConditionalModePolicy()
    lead = LeadEvidence(True, True, True, distance_m=60.0, speed_mps=21.0, accel_mps2=0.0)

    def followed(now):
      return scene(now, speed=22.0, lead=lead, following_lead=True)

    self.assertTrue(policy.step(20.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, followed(20.0)).requested_experimental)
    for now in (20.2, 20.4, 20.6):
      policy.step(now, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, followed(now))
    policy.step(20.8, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, followed(20.8))
    self.assertTrue(policy.step(20.9, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, followed(20.9)).requested_experimental)
    entered = policy.step(21.01, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, followed(21.01))
    self.assertEqual((entered.reason, entered.status_code, entered.requested_experimental), (Reason.CCM_LEAD, 4, False))
    veto = policy.step(21.02, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, replace(followed(21.02), left_blinker=True))
    self.assertEqual((veto.reason, veto.requested_experimental), (Reason.CCM_VETO, True))

  def test_ccm_qualified_launch_and_missing_launch_proof(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), ccm_launch=True)
    qualified = scene(30.0, speed=0.0, standstill=True, launch_candidate=True, low_speed_stop_scene=True)
    launch = policy.step(30.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, qualified, settings)
    self.assertEqual((launch.reason, launch.requested_experimental), (Reason.CCM_LAUNCH, False))
    missing = policy.step(30.1, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, replace(qualified, observed_mono_s=30.1, launch_candidate=None), settings)
    self.assertEqual((missing.reason, missing.requested_experimental), (Reason.CCM_VETO, True))

  def test_ccm_launch_exit_bypasses_dwell_and_keeps_frozen_lead_status(self):
    settings = replace(ModeSettings(), ccm_launch=True)
    for lead_launch in (False, True):
      with self.subTest(lead_launch=lead_launch):
        policy = ConditionalModePolicy()
        launch = policy.step(
          10.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(10.0, speed=0.5, launch_candidate=True, launch_lead=lead_launch), settings
        )
        self.assertFalse(launch.requested_experimental)
        self.assertEqual(launch.status_code, 4 if lead_launch else 6)
        exit_result = policy.step(
          10.05, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(10.05, speed=7.0, launch_candidate=False, launch_forced_exit=True), settings
        )
        self.assertTrue(exit_result.requested_experimental)
        self.assertEqual(exit_result.status_code, 0)

  def test_ccm_launch_expiry_cannot_extend_chill_with_unknown_exit_evidence(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), ccm_launch=True)
    policy.step(10.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(10.0, speed=0.5, launch_candidate=True), settings)
    missing = policy.step(
      10.05, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(10.05, speed=0.5, launch_candidate=None, launch_forced_exit=None), settings
    )
    self.assertEqual((missing.reason, missing.requested_experimental), (Reason.SCENE_UNAVAILABLE, True))

  def test_ccm_launch_exit_allows_independent_qualified_speed_candidate(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), ccm_launch=True)
    policy.step(10.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(10.0, speed=0.5, launch_candidate=True), settings)
    speed = policy.step(
      10.05,
      ModeChoice.CCM,
      ManualIntent.NONE,
      AUTHORITY,
      scene(10.05, speed=25.0, set_speed_mps=30.0, launch_candidate=False, launch_forced_exit=True),
      settings,
    )
    self.assertEqual((speed.reason, speed.requested_experimental, speed.status_code), (Reason.CCM_SPEED, False, 6))

  def test_mode_switch_discards_previous_timers(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), cem_speed_mps=15.0)
    self.assertTrue(policy.step(1.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(1.0, speed=10.0), settings).requested_experimental)
    changed = policy.step(1.1, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(1.1, speed=20.0), settings)
    self.assertEqual((changed.reason, changed.requested_experimental), (Reason.NO_TRIGGER, True))
    back = policy.step(1.2, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(1.2, speed=20.0), settings)
    self.assertEqual((back.reason, back.requested_experimental), (Reason.NO_TRIGGER, False))

  def test_manual_override_clears_pending_confirmation_and_old_hold(self):
    policy = ConditionalModePolicy()
    fast = scene(10.0, speed=25.0, set_speed_mps=30.0)
    self.assertTrue(policy.step(10.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, fast).requested_experimental)
    manual = policy.step(10.1, ModeChoice.CCM, ManualIntent.FORCE_CHILL, AUTHORITY, replace(fast, observed_mono_s=10.1))
    self.assertEqual((manual.reason, manual.requested_experimental), (Reason.MANUAL_CHILL, False))
    resumed = policy.step(10.2, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, replace(fast, observed_mono_s=10.2))
    self.assertEqual((resumed.reason, resumed.requested_experimental), (Reason.NO_TRIGGER, True))
    policy.step(10.4, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, replace(fast, observed_mono_s=10.4))
    self.assertFalse(policy.step(10.56, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, replace(fast, observed_mono_s=10.56)).requested_experimental)

    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), cem_speed_mps=15.0)
    self.assertTrue(policy.step(20.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(20.0, speed=10.0), settings).requested_experimental)
    policy.step(20.1, ModeChoice.CEM, ManualIntent.FORCE_CHILL, AUTHORITY, scene(20.1, speed=20.0), settings)
    resumed = policy.step(20.2, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(20.2, speed=20.0), settings)
    self.assertEqual((resumed.reason, resumed.requested_experimental), (Reason.NO_TRIGGER, False))

  def test_raw_mode_string_is_rejected_not_misrouted(self):
    policy = ConditionalModePolicy()
    # Deliberately bypass the type annotation to exercise the runtime boundary.
    result = policy.step(
      10.0, cast(ModeChoice, 'conditional_experimental'), ManualIntent.NONE, AUTHORITY, scene(10.0, speed=10.0), replace(ModeSettings(), cem_speed_mps=15.0)
    )
    self.assertEqual((result.reason, result.requested_experimental), (Reason.UNAVAILABLE, False))

  def test_invalid_values_and_clock_rollback_fail_closed(self):
    policy = ConditionalModePolicy()
    settings = replace(ModeSettings(), ccm_speed_mps=10**350)
    result = policy.step(10.0, ModeChoice.CCM, ManualIntent.NONE, AUTHORITY, scene(10.0), settings)
    self.assertEqual(result.reason, Reason.UNAVAILABLE)
    self.assertFalse(result.requested_experimental)
    no_speed = policy.step(10.1, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.1, speed=float('nan')))
    self.assertEqual(no_speed.reason, Reason.SCENE_UNAVAILABLE)
    rollback = policy.step(9.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(9.0))
    self.assertEqual(rollback.reason, Reason.UNAVAILABLE)


if __name__ == '__main__':
  unittest.main()
