"""The Tesla screen's three-finger tap, decoded from its touch-count frames: once, on complete release."""
import unittest

from openpilot.starpilot.controllers.tesla_controls import TeslaInputDecoder
from openpilot.starpilot.controllers.tests.test_tesla_decoder import wheel


def touch(count):
  return bytes([0, 0, 0, count, 0, 0, 0, 0])


class ScreenGestureTests(unittest.TestCase):
  def sequence(self, counts, step=.08, bus=1, details=False):
    decoder = TeslaInputDecoder(1)
    result = []
    for i, count in enumerate(counts):
      result += decoder.feed(0x3df, touch(count), bus, 1 + i * step, details=details)
    return result

  def test_three_fingers_release_once_with_normal_ramp(self):
    self.assertEqual(self.sequence([0, 1, 2, 3, 2, 1, 0, 0]), ['screen_three_finger_tap'])
    self.assertEqual(self.sequence([0, 3, 2, 3, 0], details=True), [('screen_three_finger_tap', False)])

  def test_held_boot_long_hold_other_counts_wrong_bus(self):
    for sequence in ([3, 3, 0], [0, 1, 0], [0, 2, 0], [0, 3, 4, 3, 0]):
      self.assertEqual(self.sequence(sequence), [])
    self.assertEqual(self.sequence([0, 3, 0], step=2), [])
    self.assertEqual(self.sequence([0, 3, 0], bus=0), [])

  def test_asynchronous_neutral_does_not_require_periodic_frame(self):
    decoder = TeslaInputDecoder(1)
    result = []
    for t, count in [(1, 0), (100, 3), (100.2, 0)]:
      result += decoder.feed(0x3df, touch(count), 1, t)
    self.assertEqual(result, ['screen_three_finger_tap'])

  def test_reset_malformed_and_time_reversal_discard(self):
    for fault in ('reset', 'length', 'time'):
      d = TeslaInputDecoder(1)
      for t, count in [(1, 0), (1.1, 3)]:
        d.feed(0x3df, touch(count), 1, t)
      if fault == 'reset':
        d.reset()
      elif fault == 'length':
        d.feed(0x3df, bytes(7), 1, 1.15)
      else:
        d.feed(0x3df, bytes(8), 1, .5)
      self.assertEqual(d.feed(0x3df, bytes(8), 1, 1.2), [], fault)

  def test_existing_fast_scroll_threshold_is_preserved(self):
    for ticks in (1, 2, 3, -1, -2, -3):
      d = TeslaInputDecoder(1)
      d.feed(0x3c2, wheel(), 1, 1, details=True)
      d.feed(0x3c2, wheel(right_scroll=ticks), 1, 1.1, details=True)
      self.assertEqual(d.feed(0x3c2, wheel(), 1, 1.2, details=True),
                       [('right_wheel_up' if ticks > 0 else 'right_wheel_down', abs(ticks) >= 2)])

  def test_reset_preserves_observed_released_baseline_only(self):
    d = TeslaInputDecoder(1)
    d.feed(0x3df, bytes(8), 1, 1)
    d.reset()
    d.feed(0x3df, touch(3), 1, 2)
    self.assertEqual(d.feed(0x3df, bytes(8), 1, 2.1), ['screen_three_finger_tap'])
    d.feed(0x3df, touch(3), 1, 3)
    d.reset()
    self.assertEqual(d.feed(0x3df, bytes(8), 1, 3.1), [])
