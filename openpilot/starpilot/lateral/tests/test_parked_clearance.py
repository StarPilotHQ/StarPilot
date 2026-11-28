import math
import unittest

from openpilot.starpilot.lateral import parked_clearance as pc
from openpilot.starpilot.lateral.parked_clearance import ParkedClearance, RadarPoint

DT = 0.05


def run(detector, points, seconds, *, speed=11.0, gap=1.0, room=0.6, active=True):
  shift = detector.shift
  for _ in range(round(seconds / DT)):
    shift = detector.update(points, speed, DT, gap, room, active)
  return shift


def run_limit(points, limit):
  detector = ParkedClearance()
  shift = 0.0
  for _ in range(round(4.0 / DT)):
    shift = detector.update(points, 11.0, DT, 2.0, 0.6, True, limit)
  return shift


class TestParkedClearance(unittest.TestCase):
  def test_clear_road_adds_nothing(self):
    self.assertEqual(run(ParkedClearance(), [], 5.0), 0.0)

  def test_parked_car_shifts_left_within_cap(self):
    detector = ParkedClearance()
    shift = run(detector, [RadarPoint(1, 20.0, 2.3, 0.0)], 3.0)
    self.assertAlmostEqual(shift, pc.MAX_SHIFT)
    self.assertEqual(detector.reason, 'parked')

  def test_shift_matches_missing_gap(self):
    shift = run(ParkedClearance(), [RadarPoint(1, 20.0, 2.7, 0.0)], 3.0)
    self.assertAlmostEqual(shift, 0.1)

  def test_enough_gap_adds_nothing(self):
    self.assertEqual(run(ParkedClearance(), [RadarPoint(1, 20.0, 3.0, 0.0)], 3.0), 0.0)

  def test_brief_track_ignored(self):
    self.assertEqual(run(ParkedClearance(), [RadarPoint(1, 20.0, 2.3, 0.0)], pc.CONFIRM_SECONDS - DT), 0.0)

  def test_noisy_far_object_never_confirms(self):
    detector = ParkedClearance()
    for index in range(100):
      detector.update([RadarPoint(1, 20.0, 2.8 if index % 4 == 0 else 4.4, 0.0)], 11.0, DT, 1.5, 0.6, True)
      self.assertEqual(detector.shift, 0.0)

  def test_close_roadside_target_and_high_speed_ignored(self):
    self.assertEqual(run(ParkedClearance(), [RadarPoint(1, 20.0, 1.3, 0.0)], 3.0), 0.0)
    detector = ParkedClearance()
    self.assertEqual(run(detector, [RadarPoint(1, 20.0, 2.3, 0.0)], 3.0, speed=13.0), 0.0)
    self.assertEqual(detector.reason, 'speed')

  def test_on_road(self):
    self.assertTrue(pc.on_road(2.6, 3.4, 0.4))
    self.assertFalse(pc.on_road(3.0, 2.5, 0.3))
    self.assertTrue(pc.on_road(1.8, 1.0, 0.3))
    self.assertTrue(pc.on_road(3.0, 2.5, 1.4))
    self.assertFalse(pc.on_road(3.0, None, 0.3))
    self.assertFalse(pc.on_road(3.0, math.nan, 0.3))

  def test_far_lateral_and_moving_ignored(self):
    points = [RadarPoint(1, 20.0, 4.0, 0.0), RadarPoint(2, 20.0, 2.3, 8.0), RadarPoint(3, 20.0, -2.3, 0.0), RadarPoint(4, 40.0, 2.3, 0.0)]
    self.assertEqual(run(ParkedClearance(), points, 3.0), 0.0)

  def test_left_room_limits_shift(self):
    detector = ParkedClearance()
    self.assertAlmostEqual(run(detector, [RadarPoint(1, 20.0, 2.3, 0.0)], 3.0, room=0.1), 0.1)
    self.assertEqual(detector.reason, 'left_limited')
    self.assertEqual(run(ParkedClearance(), [RadarPoint(1, 20.0, 2.3, 0.0)], 3.0, room=0.0), 0.0)

  def test_oncoming_detection(self):
    self.assertTrue(pc.oncoming_ahead([RadarPoint(2, 100.0, -2.5, -10.0)]))
    for point in (RadarPoint(2, 130.0, -2.5, -10.0), RadarPoint(2, 50.0, 2.5, -10.0), RadarPoint(2, 50.0, -2.5, 0.0), RadarPoint(2, 50.0, -6.0, -10.0)):
      self.assertFalse(pc.oncoming_ahead([point]))

  def test_speed_window(self):
    for speed in (pc.MIN_SPEED - 0.1, pc.MAX_SPEED + 0.1):
      self.assertEqual(run(ParkedClearance(), [RadarPoint(1, 20.0, 2.3, 0.0)], 3.0, speed=speed), 0.0)

  def test_rate_limited_rise_hold_and_release(self):
    detector = ParkedClearance()
    previous = 0.0
    for _ in range(60):
      shift = detector.update([RadarPoint(1, 20.0, 2.3, 0.0)], 11.0, DT, 1.0, 0.6, True)
      self.assertLessEqual(shift - previous, pc.RISE_RATE * DT + 1e-9)
      previous = shift
    held = run(detector, [], pc.HOLD_SECONDS - DT)
    self.assertAlmostEqual(held, pc.MAX_SHIFT)
    for _ in range(100):
      shift = detector.update([], 11.0, DT, 1.0, 0.6, True)
      self.assertLessEqual(previous - shift, pc.FALL_RATE * DT + 1e-9)
      previous = shift
    self.assertEqual(shift, 0.0)

  def test_inactive_resets_immediately(self):
    detector = ParkedClearance()
    run(detector, [RadarPoint(1, 20.0, 2.3, 0.0)], 3.0)
    self.assertEqual(detector.update([RadarPoint(1, 20.0, 2.3, 0.0)], 11.0, DT, 1.0, 0.6, False), 0.0)
    self.assertEqual(detector.tracks, {})

  def test_watch_tracks_without_shift(self):
    detector = ParkedClearance()
    points = [RadarPoint(1, 20.0, 2.3, 0.0)]
    for _ in range(round(1.0 / DT)):
      self.assertEqual(detector.update(points, 11.0, DT, 1.0, 0.6, True, pc.MAX_SHIFT, True), 0.0)
    self.assertEqual(detector.reason, 'inactive')
    self.assertGreater(detector.update(points, 11.0, DT, 1.0, 0.6, True), 0.0)
    self.assertEqual(detector.reason, 'parked')
    self.assertEqual(detector.update(points, 11.0, DT, 1.0, 0.6, True, pc.MAX_SHIFT, True), 0.0)

  def test_invalid_inputs_add_nothing(self):
    for speed, dt, gap, room in ((math.nan, DT, 1.0, 0.6), (11.0, 0.0, 1.0, 0.6), (11.0, DT, 0.0, 0.6), (11.0, DT, 1.0, math.inf)):
      detector = ParkedClearance()
      for _ in range(60):
        self.assertEqual(detector.update([RadarPoint(1, 20.0, 2.3, 0.0)], speed, dt, gap, room, True), 0.0)
    self.assertEqual(run(ParkedClearance(), [RadarPoint(1, math.nan, 2.3, 0.0)], 3.0), 0.0)

  def test_wide_gap_reaches_far_curb_cars(self):
    point = [RadarPoint(1, 20.0, 3.5, 0.0)]
    self.assertAlmostEqual(run(ParkedClearance(), point, 3.0, gap=2.0, room=0.6), 0.3)
    self.assertEqual(run(ParkedClearance(), point, 3.0, gap=1.5, room=0.6), 0.0)

  def test_gap_setting_capped(self):
    detector = ParkedClearance()
    run(detector, [RadarPoint(1, 20.0, 3.2, 0.0)], 3.0, gap=5.0)
    self.assertAlmostEqual(detector.demand, pc.MAX_GAP - 1.4)

  def test_limit_setting_caps_shift(self):
    point = [RadarPoint(1, 20.0, 2.3, 0.0)]
    self.assertAlmostEqual(run_limit(point, 0.1), 0.1)
    self.assertAlmostEqual(run_limit(point, 0.5), 0.5)
    self.assertAlmostEqual(run_limit(point, 9.0), 0.5)
    for bad in (0.0, -0.2, math.nan):
      self.assertEqual(run_limit(point, bad), 0.0)

  def test_path_lateral(self):
    self.assertAlmostEqual(pc.path_lateral([0.0, 10.0, 20.0], [0.0, 0.5, 1.0], 15.0, -3.0), 2.25)
    self.assertIsNone(pc.path_lateral([0.0, 10.0], [0.0, 0.5], 15.0, -3.0))
    self.assertIsNone(pc.path_lateral([0.0], [0.0], 0.0, -3.0))

  def test_left_room(self):
    self.assertAlmostEqual(pc.left_room(1.6, 0.9, 0.1, 6.0, 0.8), 0.7)
    self.assertEqual(pc.left_room(0.8, 0.9, 0.1, 6.0, 0.2), 0.0)
    self.assertAlmostEqual(pc.left_room(1.6, 0.05, 1.4, 5.4, 0.7), 2.5)
    self.assertAlmostEqual(pc.left_room(1.6, 0.05, 1.4, 5.4, 1.2), 1.5)
    self.assertEqual(pc.left_room(1.6, 0.05, 1.4, 5.4, 1.6), 0.0)
    self.assertAlmostEqual(pc.left_room(1.6, 0.4, 0.2, 6.0, 0.5), 0.5)
    self.assertAlmostEqual(pc.left_room(1.6, 0.9, 0.9, 6.0, 0.5), 0.2)
    self.assertEqual(pc.left_room(1.6, 0.4, 0.2, 6.0, 1.6), 0.0)
    self.assertEqual(pc.left_room(math.nan, 0.9, 0.1, 6.0, 0.2), 0.0)
    self.assertAlmostEqual(pc.left_room(1.6, 0.9, 0.1, 6.0, 0.8, oncoming=True), 0.5)
    self.assertEqual(pc.left_room(1.6, 0.05, 1.4, 5.4, 0.7, oncoming=True), 0.0)
    self.assertEqual(pc.left_room(1.6, 0.4, 0.2, 6.0, 0.5, oncoming=True), 0.0)


if __name__ == '__main__':
  unittest.main()
