from dataclasses import replace
import math
import unittest

from openpilot.starpilot.longitudinal.force_stop import ForceStop, StopFrame, vehicle_tune


BASE = 100_000_000_000
TICK = 50_000_000


def frame(tick, **changes):
  return replace(StopFrame(BASE + tick * TICK, 10., 80., True, False, False, False, True, False,
                           False, False, 200., 30., False, 0., False, 0.), **changes)


class ForceStopTests(unittest.TestCase):
  def setUp(self):
    self.owner = ForceStop()
    self.tune = vehicle_tune('HYUNDAI_IONIQ_6')

  def test_original_approach_commit_kinematics_and_obstacle(self):
    for tick in range(40):
      plan = self.owner.step(frame(tick, model_should_stop=True), self.tune)
      # The existing 0.5s accumulator crosses .5 on frame 10 with binary floats.
      distance = 80. if tick < 10 else 80. - (tick - 9) * .5
      self.assertEqual(plan.forcing, tick >= 10)
      assert plan.speed_ceiling_mps is not None
      self.assertAlmostEqual(plan.speed_ceiling_mps, math.sqrt(1.3 * (distance - 6)))
      assert plan.obstacle_m is not None
      self.assertAlmostEqual(plan.obstacle_m, distance * .93)
      self.assertEqual(plan.jerk_scale, .32)
      self.assertFalse(plan.should_stop)

  def test_activation_threshold_hysteresis_preserves_commit_timer(self):
    for tick in range(20):
      plan = self.owner.step(frame(tick, horizon_m=99. if tick % 2 == 0 else 103.), self.tune)
    self.assertTrue(plan.forcing)
    self.assertGreaterEqual(self.owner.timer, .5)
    cold = ForceStop()
    for tick in range(20):
      plan = cold.step(frame(tick, horizon_m=103.), self.tune)
    self.assertFalse(plan.forcing)
    self.assertGreater(plan.approach_distance_m, 0)

  def test_lead_and_curve_veto_before_commit(self):
    for changes in ({'lead_present': True, 'lead_distance_m': 40., 'lead_speed_mps': 9.},
                    {'tracking_lead': True}, {'driving_in_curve': True}):
      with self.subTest(changes=changes):
        self.owner.reset()
        for tick in range(20):
          plan = self.owner.step(frame(tick, **changes), self.tune)
          self.assertFalse(plan.forcing)
          self.assertIsNone(plan.speed_ceiling_mps)
          self.assertIsNone(plan.obstacle_m)

  def test_lead_appearing_does_not_abandon_committed_stop(self):
    for tick in range(15):
      self.owner.step(frame(tick), self.tune)
    plan = self.owner.step(frame(15, lead_present=True, lead_distance_m=20, lead_speed_mps=0, tracking_lead=True), self.tune)
    self.assertTrue(plan.forcing)

  def test_accelerator_releases_then_suppresses_for_ten_seconds(self):
    for tick in range(15):
      self.owner.step(frame(tick), self.tune)
    for tick in range(15, 215):
      plan = self.owner.step(frame(tick, pedal_override=tick == 15), self.tune)
      self.assertFalse(plan.forcing)
      self.assertIsNone(plan.speed_ceiling_mps)
    resumed = self.owner.step(frame(215), self.tune)
    assert resumed.speed_ceiling_mps is not None

  def test_resume_releases_standstill_hold_but_does_not_create_override_when_idle(self):
    self.owner.step(frame(0, light=False, horizon_m=190., resume_requested=True), self.tune)
    self.assertEqual(self.owner.override_until_ns, 0)
    self.assertEqual(self.owner.resume_until_ns, 0)
    held = frame(1, speed_mps=0., horizon_m=30., standstill=True, raw_model_stopped=True)
    self.owner.reset()
    self.assertTrue(self.owner.step(held, self.tune).should_stop)
    released = self.owner.step(replace(held, model_ns=held.model_ns + TICK, resume_requested=True), self.tune)
    self.assertFalse(released.should_stop)
    self.assertFalse(released.forcing)
    self.assertTrue(released.resume_override)
    self.assertEqual(self.owner.override_until_ns, held.model_ns + TICK + 10_000_000_000)

  def test_light_release_requires_half_second_continuously_clear(self):
    for tick in range(15):
      self.owner.step(frame(tick), self.tune)
    for tick in range(15, 25):
      self.assertTrue(self.owner.step(frame(tick, light=False), self.tune).forcing)
    cleared = self.owner.step(frame(25, light=False), self.tune)
    self.assertFalse(cleared.forcing)
    self.assertIsNone(cleared.obstacle_m)

  def test_committed_stop_survives_unavailable_perception_and_uses_elapsed_odometry(self):
    for tick in range(15):
      previous = self.owner.step(frame(tick, horizon_m=30.), self.tune)
    # A valid current model event may have unusable predicted stop geometry.
    # Repeated unknown perception cannot become a clear-light release.
    for tick in range(15, 35):
      plan = self.owner.step(frame(tick, horizon_m=None, road_curvature=None,
                                   light=False, perception_available=False), self.tune)
      self.assertTrue(plan.forcing)
      self.assertAlmostEqual(plan.tracked_distance_m, max(0., previous.tracked_distance_m - .5))
      assert plan.speed_ceiling_mps is not None
      assert previous.speed_ceiling_mps is not None
      self.assertLessEqual(plan.speed_ceiling_mps, previous.speed_ceiling_mps)
      previous = plan
    held = self.owner.step(frame(35, speed_mps=0., standstill=True, horizon_m=None,
                                 road_curvature=None, light=False, perception_available=False), self.tune)
    self.assertTrue(held.should_stop)
    for tick in range(36, 75):
      self.assertTrue(self.owner.step(frame(tick, speed_mps=-1e-44, standstill=True, horizon_m=None,
                                          road_curvature=None, light=False, perception_available=False), self.tune).should_stop)
    expired = self.owner.step(frame(75, speed_mps=0., standstill=True, horizon_m=None,
                                    road_curvature=None, light=False, perception_available=False), self.tune)
    self.assertFalse(expired.forcing)
    self.assertFalse(expired.manual_hold)

  def test_missing_perception_distance_uses_actual_model_interval(self):
    for tick in range(15):
      previous = self.owner.step(frame(tick, horizon_m=30.), self.tune)
    # One skipped 20 Hz tick still carries fresh source authority. Odometry
    # integrates both intervals instead of moving the remembered stop line.
    plan = self.owner.step(frame(16, horizon_m=None, road_curvature=None, perception_available=False), self.tune)
    self.assertTrue(plan.forcing)
    self.assertAlmostEqual(plan.tracked_distance_m, previous.tracked_distance_m - 1.)

  def test_negative_light_with_stopping_path_cannot_release_commitment(self):
    for tick in range(15):
      self.owner.step(frame(tick, horizon_m=30.), self.tune)
    for tick in range(15, 55):
      plan = self.owner.step(frame(tick, horizon_m=30., light=False, raw_model_stopped=True), self.tune)
      self.assertTrue(plan.forcing)
    # It is not necessary to reach standstill after a qualified reopened path.
    for tick in range(55, 66):
      plan = self.owner.step(frame(tick, horizon_m=190., light=False), self.tune)
    self.assertFalse(plan.forcing)

  def test_unknown_perception_never_acquires_and_driver_release_still_works(self):
    for tick in range(20):
      plan = self.owner.step(frame(tick, horizon_m=None, road_curvature=None, perception_available=False), self.tune)
      self.assertFalse(plan.forcing)
      self.assertIsNone(plan.speed_ceiling_mps)
    for action in ('pedal_override', 'resume_requested'):
      self.owner.reset()
      for tick in range(15):
        self.owner.step(frame(tick), self.tune)
      plan = self.owner.step(frame(15, horizon_m=None, road_curvature=None, perception_available=False,
                                  **{action: True}), self.tune)
      self.assertFalse(plan.forcing)
      self.assertIsNone(plan.speed_ceiling_mps)

  def test_standstill_hold_expires_after_two_seconds_without_rearming(self):
    for tick in range(40):
      plan = self.owner.step(frame(tick, speed_mps=0., horizon_m=30., raw_model_stopped=True, standstill=True), self.tune)
      self.assertTrue(plan.forcing)
      self.assertTrue(plan.should_stop)
      self.assertTrue(plan.manual_hold)
    for tick in range(40, 240):
      plan = self.owner.step(frame(tick, speed_mps=0., horizon_m=30., raw_model_stopped=True, standstill=True), self.tune)
      self.assertFalse(plan.forcing)
      self.assertFalse(plan.should_stop)
      self.assertFalse(plan.manual_hold)

  def test_moving_stop_hold_expires_after_two_seconds(self):
    for tick in range(15):
      self.owner.step(frame(tick, horizon_m=30.), self.tune)
    for tick in range(15, 55):
      plan = self.owner.step(frame(tick, speed_mps=0., horizon_m=30., standstill=True), self.tune)
      self.assertTrue(plan.manual_hold)
    plan = self.owner.step(frame(55, speed_mps=0., horizon_m=30., standstill=True), self.tune)
    self.assertFalse(plan.forcing)
    self.assertFalse(plan.should_stop)

  def test_standstill_hold_releases_after_open_scene_dwell(self):
    self.assertTrue(self.owner.step(frame(0, speed_mps=0., horizon_m=30., raw_model_stopped=True,
                                         standstill=True), self.tune).manual_hold)
    for tick in range(1, 16):
      plan = self.owner.step(frame(tick, speed_mps=0., horizon_m=190., light=False, standstill=True), self.tune)
      self.assertTrue(plan.manual_hold)
    plan = self.owner.step(frame(16, speed_mps=0., horizon_m=190., light=False, standstill=True), self.tune)
    self.assertFalse(plan.manual_hold)
    self.assertFalse(plan.forcing)
    self.assertFalse(plan.should_stop)

  def test_manual_hold_survives_roll_until_each_driver_action(self):
    for action in ('resume_requested', 'pedal_override'):
      self.owner.reset()
      held = self.owner.step(frame(0, speed_mps=0., horizon_m=30., raw_model_stopped=True, standstill=True), self.tune)
      self.assertTrue(held.manual_hold)
      for tick in range(1, 31):
        plan = self.owner.step(frame(tick, speed_mps=.01, horizon_m=30., raw_model_stopped=True, standstill=False), self.tune)
        self.assertTrue(plan.manual_hold)
        self.assertEqual(plan.speed_ceiling_mps, 0.)
      release = self.owner.step(frame(31, speed_mps=0., horizon_m=30., raw_model_stopped=True, standstill=True,
                                     **{action: True}), self.tune)
      self.assertFalse(release.manual_hold)
      self.assertFalse(release.forcing)
      self.assertFalse(release.should_stop)

  def test_dashboard_sign_arms_only_slow_and_with_model_agreement(self):
    for changes, armed in (({}, True), ({'speed_mps': 18.5}, False), ({'horizon_m': 60.}, False),
                           ({'tracking_lead': True}, False)):
      with self.subTest(changes=changes):
        self.owner.reset()
        for tick in range(6):
          plan = self.owner.step(frame(tick, **{'speed_mps': 8., 'horizon_m': 45., 'light': False, 'sign': True, **changes}),
                                 self.tune)
        self.assertEqual(plan.forcing, armed)
        if armed:
          self.assertLessEqual(plan.tracked_distance_m, 27.)

  def test_dashboard_sign_hold_waits_for_each_driver_action(self):
    for action in ('pedal_override', 'resume_requested'):
      with self.subTest(action=action):
        self.owner.reset()
        for tick in range(15):
          self.owner.step(frame(tick, speed_mps=8., horizon_m=45., light=False, sign=True), self.tune)
        for tick in range(15, 415):
          plan = self.owner.step(frame(tick, speed_mps=0., horizon_m=190., light=False, standstill=True), self.tune)
          self.assertTrue(plan.manual_hold)
          self.assertTrue(plan.should_stop)
        released = self.owner.step(frame(415, speed_mps=0., horizon_m=190., light=False, standstill=True,
                                         **{action: True}), self.tune)
        self.assertFalse(released.manual_hold)
        self.assertFalse(released.forcing)
        for tick in range(416, 600):
          plan = self.owner.step(frame(tick, speed_mps=0., horizon_m=190., light=False, standstill=True), self.tune)
          self.assertFalse(plan.forcing)

  def test_latched_sign_survives_light_and_sign_dropout(self):
    for tick in range(15):
      self.owner.step(frame(tick, speed_mps=8., horizon_m=45., sign=True), self.tune)
    self.assertTrue(self.owner.sign_confirmed)
    for tick in range(15, 45):
      plan = self.owner.step(frame(tick, speed_mps=1., horizon_m=190., light=False), self.tune)
      self.assertTrue(plan.forcing)

  def test_sign_seed_never_moves_the_stop_farther(self):
    for tick in range(50):
      previous = self.owner.step(frame(tick, speed_mps=8., horizon_m=45., sign=True), self.tune)
    self.assertLess(previous.tracked_distance_m, 20.)
    plan = self.owner.step(frame(50, speed_mps=8., horizon_m=20., sign=True), self.tune)
    self.assertLessEqual(plan.tracked_distance_m, previous.tracked_distance_m)

  def test_positive_offset_keeps_stop_target_until_native_stop(self):
    for tick in range(40):
      plan = self.owner.step(frame(tick, horizon_m=12.), self.tune, 8)
      if plan.forcing:
        self.assertTrue(plan.should_stop or plan.obstacle_m is not None)
    self.assertTrue(plan.should_stop)

  def test_odometry_ceiling_bounds_reopened_model(self):
    for tick in range(15):
      self.owner.step(frame(tick), self.tune)
    cap = self.owner.distance_cap
    plan = self.owner.step(frame(15, horizon_m=190.), self.tune)
    self.assertAlmostEqual(self.owner.distance_cap, cap - .5)
    self.assertLessEqual(plan.tracked_distance_m, cap - .5 + 15)

  def test_offset_and_handoff_keep_native_should_stop(self):
    for offset in (-20, 0, 20):
      with self.subTest(offset=offset):
        self.owner.reset()
        for tick in range(15):
          plan = self.owner.step(frame(tick, horizon_m=12.), self.tune, offset)
        expected = max(0., plan.tracked_distance_m + offset * .3048 - 6)
        assert plan.speed_ceiling_mps is not None
        self.assertAlmostEqual(plan.speed_ceiling_mps, math.sqrt(1.3 * expected))
        assert plan.speed_ceiling_mps is not None
        self.assertEqual(plan.should_stop, plan.speed_ceiling_mps <= .5)

  def test_current_platform_rules(self):
    self.assertEqual(vehicle_tune('TOYOTA_CAMRY_TSS2').handoff_m, 4.5)
    self.assertEqual(vehicle_tune('TOYOTA_CAMRY_TSS2').distance_bias_m, 6.)
    self.assertEqual(vehicle_tune('HYUNDAI_ELANTRA_2021').lead_veto_m, 90.)
    self.assertEqual(vehicle_tune('HYUNDAI_ELANTRA_2021').jerk_scale, .8)
    santa_fe = vehicle_tune('HYUNDAI_SANTA_FE_2022')
    for tick in range(15):
      self.owner.step(frame(tick), santa_fe)
    for tick in range(15, 35):
      plan = self.owner.step(frame(tick, speed_mps=2., light=False, horizon_m=30.), santa_fe)
      self.assertTrue(plan.forcing)

  def test_old_duplicate_gap_and_disabled_clear_owner(self):
    for defect in ('duplicate', 'gap', 'disabled'):
      self.owner.reset()
      for tick in range(15):
        self.owner.step(frame(tick), self.tune)
      value = frame(14 if defect == 'duplicate' else 20 if defect == 'gap' else 15,
                    enabled=defect != 'disabled')
      self.assertFalse(self.owner.step(value, self.tune).forcing)
      self.assertFalse(self.owner.forcing)


if __name__ == '__main__':
  unittest.main()


  def test_resume_override_is_only_for_a_previous_hold_and_ends_at_speed_lead_or_timeout(self):
    for cause in ('speed', 'lead', 'tracked', 'timeout'):
      with self.subTest(cause=cause):
        self.owner.reset()
        held = frame(0, speed_mps=0., horizon_m=30., standstill=True, raw_model_stopped=True)
        self.owner.step(held, self.tune)
        self.assertTrue(self.owner.step(replace(held, model_ns=BASE + TICK, resume_requested=True), self.tune).resume_override)
        for tick in range(2, 123 if cause == 'timeout' else 3):
          changes = {'speed': {'speed_mps': 2.}, 'lead': {'lead_present': True},
                     'tracked': {'tracking_lead': True}, 'timeout': {}}[cause]
          result = self.owner.step(replace(held, model_ns=BASE + tick * TICK, **changes), self.tune)
        self.assertFalse(result.resume_override)
    self.owner.reset()
    first = self.owner.step(frame(0, speed_mps=0., horizon_m=30., standstill=True, raw_model_stopped=True, resume_requested=True), self.tune)
    self.assertFalse(first.resume_override)
