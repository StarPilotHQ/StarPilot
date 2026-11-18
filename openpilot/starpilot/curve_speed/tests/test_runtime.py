import unittest
from dataclasses import replace
from types import SimpleNamespace as NS

from openpilot.starpilot.curve_speed.runtime import DriverEvent, Frame, Runtime
from openpilot.starpilot.curve_speed.status import StatusPublisher, observation
from openpilot.starpilot.curve_speed.target import CurveProfile


BASE = 10_000_000_000


def frame(n: int, **kwargs) -> Frame:
  now = BASE + n * 50_000_000
  profile = CurveProfile((0.01,), (0.0,), now)
  return replace(Frame(now, now, True, profile, True, True, True, False, 30.0, 25.0,
                       0.01, False, False, False, False, 1, False), **kwargs)


def arm_selected(rt: Runtime) -> None:
  for n in range(1, 27):
    item = frame(n)
    rt.step(item)
    rt.confirm_applied(item.now_ns, applied=True)
  assert rt.was_controlling


class TestCurveRuntime(unittest.TestCase):
  def test_disabled_diagnostic_has_no_authority(self):
    result = Runtime(enabled=False).step(frame(1))
    assert result.candidate_mps is not None
    self.assertLess(result.candidate_mps, 30.0)
    self.assertIsNone(result.ceiling_mps)

  def test_qualified_cap_and_axis_exclusions(self):
    rt = Runtime(enabled=True)
    self.assertIsNotNone(rt.step(frame(1)).ceiling_mps)
    self.assertIsNone(rt.step(frame(2, system_longitudinal=False)).ceiling_mps)
    self.assertIsNone(rt.step(frame(3, force_decel=True)).ceiling_mps)
    self.assertIsNotNone(rt.step(frame(4, blinker=True)).ceiling_mps)
    self.assertIsNone(rt.step(frame(5, long_active=False)).ceiling_mps)

  def test_manual_gas_releases_cap_even_when_long_active_stays_true(self):
    rt = Runtime(enabled=True)
    arm_selected(rt)
    self.assertIsNone(rt.step(frame(27, gas_pressed=True)).ceiling_mps)
    self.assertIsNone(rt.step(frame(28, gas_pressed=False)).ceiling_mps)
    self.assertTrue(rt.override)

  def test_candidate_loses_to_other_cap_and_cannot_claim_feedback(self):
    rt = Runtime(enabled=True)
    for n in range(1, 27):
      item = frame(n)
      self.assertIsNotNone(rt.step(item).ceiling_mps)
      self.assertTrue(rt.confirm_applied(item.now_ns, applied=False))
    self.assertFalse(rt.was_controlling)
    rt.step(frame(27, brake_pressed=True, long_active=False))
    self.assertEqual(rt.curve.revision, 0)
    self.assertFalse(rt.glow)

  def test_glow_starts_with_a_shallow_applied_cap_and_bridges_a_brief_outbid(self):
    rt = Runtime(enabled=True)

    def run(n: int, applied: bool):
      item = frame(n, ego_mps=29.6)
      result = rt.step(item)
      rt.confirm_applied(item.now_ns, applied=applied)
      return result

    for n in range(1, 6):
      result = run(n, True)
    assert result.ceiling_mps is not None
    self.assertGreater(result.ceiling_mps, 29.0)
    self.assertFalse(rt.was_controlling)
    self.assertTrue(rt.glow)
    run(6, False)
    self.assertTrue(rt.glow)
    run(7, True)
    for n in range(8, 18):
      run(n, False)
    self.assertTrue(rt.glow)
    run(18, False)
    self.assertFalse(rt.glow)

  def test_accel_press_latches_override_and_confirmation_does_not(self):
    rt = Runtime(enabled=True)
    arm_selected(rt)
    press = DriverEvent('card-a', 1, BASE + 26 * 50_000_000, 'curveAccelPress', 'accel', BASE + 26 * 50_000_000)
    self.assertIsNone(rt.step(frame(27), event=press).ceiling_mps)
    self.assertIsNone(rt.step(frame(28)).ceiling_mps)
    self.assertTrue(rt.override)
    # An old producer session cannot reset the high-water mark.
    replay = DriverEvent('card-old', 3, BASE + 26 * 50_000_000, 'curveAccelPress', 'accel', BASE + 26 * 50_000_000)
    rt.step(frame(29), event=replay)
    self.assertEqual(rt.event_session, 'card-a')
    rt.step(frame(30, measured_curvature=0.0, profile=CurveProfile((0.0,), (0.0,), BASE + 30 * 50_000_000)))
    self.assertFalse(rt.override)

  def test_blinker_only_suppresses_outside_curve(self):
    rt = Runtime(enabled=True)
    self.assertIsNotNone(rt.step(frame(1, blinker=True)).ceiling_mps)
    outside = frame(2, blinker=True, measured_curvature=0.0)
    self.assertIsNone(rt.step(outside).ceiling_mps)

  def test_stale_future_duplicate_and_gap_release(self):
    rt = Runtime(enabled=True)
    self.assertIsNotNone(rt.step(frame(1)).ceiling_mps)
    self.assertIsNone(rt.step(frame(2, model_ns=BASE + 50_000_000)).ceiling_mps)
    self.assertIsNone(rt.step(frame(3, model_ns=BASE + 500_000_000)).ceiling_mps)
    self.assertIsNotNone(rt.step(frame(4)).ceiling_mps)
    self.assertIsNotNone(rt.step(frame(20)).ceiling_mps)  # discontinuity resets filter, then fresh frame can rearm

  def test_isolated_stale_frame_holds_target_without_ceiling(self):
    rt = Runtime(enabled=True)
    arm_selected(rt)
    held = rt.filter.target
    assert held is not None
    stale = frame(27, inputs_fresh=False)
    self.assertIsNone(rt.step(stale).ceiling_mps)
    rt.confirm_applied(stale.now_ns, applied=False)
    self.assertTrue(rt.was_controlling)
    self.assertTrue(rt.glow)
    self.assertEqual(rt.filter.target, held)
    resumed = rt.step(frame(28)).ceiling_mps
    assert resumed is not None
    self.assertLessEqual(resumed, held)

  def test_stale_hold_publishes_valid_glowing_status(self):
    rt = Runtime(enabled=True)
    arm_selected(rt)
    stale = frame(27, inputs_fresh=False)
    result = rt.step(stale)
    rt.confirm_applied(stale.now_ns, applied=False)
    planner = NS(last_curve_ceiling_applied=False, last_curve_ceiling_status='absent')
    event = StatusPublisher().attach(None, NS(runtime=rt), result, planner, now_ns=stale.now_ns,
                                     model_ns=frame(26).now_ns, persistence_status='idle')
    status = observation(event.slcState, stale.now_ns)
    assert status is not None
    self.assertTrue(status.glow)
    self.assertFalse(status.controlling)

  def test_stale_hold_expires_after_max_step(self):
    rt = Runtime(enabled=True)
    arm_selected(rt)
    for n in range(27, 31):
      item = frame(n, inputs_fresh=False)
      rt.step(item)
      rt.confirm_applied(item.now_ns, applied=False)
    self.assertIsNotNone(rt.filter.target)
    item = frame(31, inputs_fresh=False)
    rt.step(item)
    rt.confirm_applied(item.now_ns, applied=False)
    self.assertIsNone(rt.filter.target)
    self.assertFalse(rt.glow)
    self.assertFalse(rt.was_controlling)

  def test_gas_override_during_stale_hold_still_latches(self):
    rt = Runtime(enabled=True)
    arm_selected(rt)
    stale = frame(27, inputs_fresh=False, gas_pressed=True)
    rt.step(stale)
    rt.confirm_applied(stale.now_ns, applied=False)
    self.assertIsNone(rt.step(frame(28, gas_pressed=True)).ceiling_mps)
    self.assertTrue(rt.override)

  def test_invalid_document_never_learns_or_persists(self):
    rt = Runtime({'version': 1, 'buckets': {'nan': {'average': 2.0, 'count': 1}}}, enabled=True)
    self.assertFalse(rt.document_valid)
    for n in range(1, 70):
      result = rt.step(frame(n, long_active=False))
    self.assertIsNone(result.ceiling_mps)
    self.assertIsNone(result.dirty_revision)
    self.assertEqual(rt.curve.revision, 0)

  def test_stale_settle_resets_and_disabled_feedback_stays_clean(self):
    rt = Runtime(enabled=True)
    for n in range(1, 35):
      rt.step(frame(n, long_active=False))
    self.assertEqual(rt.curve.revision, 0)
    rt.step(frame(35, inputs_fresh=False, long_active=False))
    for n in range(36, 68):
      rt.step(frame(n, long_active=False))
    self.assertEqual(rt.curve.revision, 0)
    rt.enabled = False
    rt.step(frame(68, brake_pressed=True, long_active=False))
    self.assertEqual(rt.curve.revision, 0)

  def test_disable_during_active_curve_does_not_nudge(self):
    rt = Runtime(enabled=True)
    arm_selected(rt)
    rt.enabled = False
    result = rt.step(frame(27, brake_pressed=True, long_active=False))
    self.assertIsNone(result.ceiling_mps)
    self.assertEqual(rt.curve.revision, 0)
    self.assertFalse(rt.override)

  def test_sLC_confirmation_does_not_nudge(self):
    rt = Runtime(enabled=True)
    rt.step(frame(1))
    event = DriverEvent('card', 1, BASE + 50_000_000, 'confirmationAccept', 'accel', BASE + 50_000_000)
    rt.step(frame(2), event=event)
    self.assertEqual(rt.curve.revision, 0)
    self.assertEqual(rt.watch_until_ns, 0)

  def test_brake_edge_nudges_once_but_transport_loss_does_not(self):
    rt = Runtime(enabled=True)
    arm_selected(rt)
    rt.step(frame(27, brake_pressed=True, long_active=False))
    self.assertEqual(rt.curve.revision, 1)
    rt.step(frame(28, brake_pressed=True, long_active=False))
    self.assertEqual(rt.curve.revision, 1)
    rt2 = Runtime(enabled=True)
    arm_selected(rt2)
    rt2.step(frame(27, inputs_fresh=False, long_active=False, brake_pressed=True))
    self.assertEqual(rt2.curve.revision, 0)

  def test_training_requires_driver_ownership_no_lead_and_quiet(self):
    rt = Runtime(enabled=True)
    for n in range(1, 150):
      rt.step(frame(n, long_active=False))
    self.assertGreater(rt.curve.revision, 0)
    revision = rt.curve.revision
    for n in range(150, 200):
      rt.step(frame(n, long_active=True))
    self.assertEqual(rt.curve.revision, revision)
    rt2 = Runtime(enabled=True, no_lead=True)
    self.assertIsNone(rt2.step(frame(1, following_lead=None)).ceiling_mps)
    self.assertIsNone(rt2.step(frame(2, following_lead=True)).ceiling_mps)
    self.assertIsNotNone(rt2.step(frame(3, following_lead=False)).ceiling_mps)

  def test_learning_holds_after_radar_lead_dropout(self):
    rt = Runtime(enabled=True)
    for n in range(1, 130):
      rt.step(frame(n, long_active=False, tracking_lead=True))
    self.assertEqual(rt.curve.revision, 0)
    for n in range(130, 145):
      rt.step(frame(n, long_active=False, tracking_lead=False))
    self.assertEqual(rt.curve.revision, 0)
    for n in range(145, 210):
      rt.step(frame(n, long_active=False, tracking_lead=False))
    self.assertGreater(rt.curve.revision, 0)

  def test_filtered_tracking_and_following_have_separate_authority(self):
    rt = Runtime(enabled=True, no_lead=True)
    # A tracked but distant lead does not block the NoLead control gate;
    # it still blocks manual training.
    for n in range(1, 80):
      rt.step(frame(n, long_active=False, tracking_lead=True, following_lead=False))
    self.assertEqual(rt.curve.revision, 0)
    self.assertIsNotNone(rt.step(frame(80, tracking_lead=True, following_lead=False)).ceiling_mps)
    self.assertIsNone(rt.step(frame(81, tracking_lead=False, following_lead=True)).ceiling_mps)
    self.assertIsNone(rt.step(frame(82, tracking_lead=False, following_lead=None)).ceiling_mps)

  def test_drive_change_resets_event_and_target(self):
    rt = Runtime(enabled=True)
    rt.step(frame(1))
    self.assertIsNotNone(rt.filter.target)
    result = rt.step(frame(2, drive_id=2, inputs_fresh=False))
    self.assertIsNone(result.ceiling_mps)
    self.assertIsNone(rt.filter.target)
    old = DriverEvent('old-card', 1, BASE + 50_000_000, 'curveAccelPress', 'accel', BASE + 50_000_000)
    rt.step(frame(3, drive_id=2), event=old)
    self.assertEqual(rt.event_session, '')


if __name__ == '__main__':
  unittest.main()
