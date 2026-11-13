from dataclasses import replace
import unittest
from typing import cast

from openpilot.starpilot.speed_limits.vision.stop_observation import StopBox, StopConfirmation, StopEvidence, STOP_MAX_AGE_NS

BASE = 100_000_000_000


def sample(stamp, frame, confidence=.8, box=(780, 200, 840, 260)):
  return StopEvidence('camera', 'road', stamp, stamp + 2_000_000_000, frame, 1344, 760, (StopBox(confidence, box),))


class StopDisplayConfirmationTests(unittest.TestCase):
  def test_compensated_pair_displays_with_control_confirmation_unchanged(self):
    for confidences in ((.67658, .74177), (.69997, .7953)):
      with self.subTest(confidences=confidences):
        display, control = StopConfirmation(display_only=True), StopConfirmation()
        first = sample(BASE, 10, confidences[0])
        second = sample(BASE + 1_100_000_000, 32, confidences[1], (900, 170, 1000, 270))
        self.assertEqual(display.observe(first, now_ns=first.observed_ns + 500_000_000), 0)
        self.assertEqual(control.observe(first, now_ns=first.observed_ns + 500_000_000), 0)
        self.assertEqual(display.observe(second, now_ns=second.observed_ns + 500_000_000), second.observed_ns)
        self.assertEqual(control.observe(second, now_ns=second.observed_ns + 500_000_000), 0)

  def test_one_high_confidence_view_never_confirms(self):
    owner = StopConfirmation(display_only=True)
    first = sample(BASE, 10, .99)
    self.assertEqual(owner.observe(first, now_ns=BASE + 100_000_000), 0)
    self.assertEqual(owner.observe(first, now_ns=BASE + 500_000_000), 0)

  def test_two_weak_views_cannot_borrow_confirmation(self):
    owner = StopConfirmation(display_only=True)
    for i in range(4):
      s = sample(BASE + i * 600_000_000, 10 + i * 12, .69)
      self.assertEqual(owner.observe(s, now_ns=s.observed_ns + 100_000_000), 0)

  def test_expired_and_fresh_negative_clear_display(self):
    owner = StopConfirmation(display_only=True)
    first, second = sample(BASE, 10, .68), sample(BASE + 600_000_000, 22, .8, (900, 170, 1000, 270))
    owner.observe(first, now_ns=BASE + 100_000_000)
    self.assertEqual(owner.observe(second, now_ns=second.observed_ns + 100_000_000), second.observed_ns)
    self.assertEqual(owner.observe(second, now_ns=second.observed_ns + STOP_MAX_AGE_NS), 0)
    owner.observe(first, now_ns=BASE + 100_000_000)
    owner.observe(second, now_ns=second.observed_ns + 100_000_000)
    negative = replace(sample(BASE + 1_200_000_000, 34), boxes=())
    self.assertEqual(owner.observe(negative, now_ns=negative.observed_ns + 100_000_000), 0)

  def test_matching_session_geometry_and_spacing_remain_required(self):
    first = sample(BASE, 10, .68)
    good = sample(BASE + 600_000_000, 22, .8, (900, 170, 1000, 270))
    for second in (replace(good, session='other'), replace(good, frame_id=9),
                   replace(good, observed_ns=BASE + 50_000_000),
                   replace(good, observed_ns=BASE + STOP_MAX_AGE_NS),
                   replace(good, boxes=(StopBox(.8, (20, 20, 50, 50)),))):
      with self.subTest(second=second):
        owner = StopConfirmation(display_only=True)
        owner.observe(first, now_ns=BASE + 100_000_000)
        self.assertEqual(owner.observe(second, now_ns=second.observed_ns + 100_000_000), 0)

  def test_weak_pair_needs_improving_confidence_and_approach(self):
    first = sample(BASE, 10, .68)
    for confidence, box in ((.8, (780, 200, 840, 260)),
                            (.8, (780, 190, 850, 270)),
                            (.67, (900, 170, 1000, 270))):
      owner = StopConfirmation(display_only=True)
      owner.observe(first, now_ns=BASE + 100_000_000)
      second = sample(BASE + 600_000_000, 22, confidence, box)
      self.assertEqual(owner.observe(second, now_ns=second.observed_ns + 100_000_000), 0)

  def test_existing_strong_stationary_pair_still_confirms(self):
    for owner in (StopConfirmation(), StopConfirmation(display_only=True)):
      owner.observe(sample(BASE, 10), now_ns=BASE + 100_000_000)
      self.assertEqual(owner.observe(sample(BASE + 600_000_000, 22), now_ns=BASE + 700_000_000), BASE + 600_000_000)

  def test_reset_preserves_explicit_display_role(self):
    owner = StopConfirmation(display_only=True)
    for _ in range(2):
      owner.reset()
      owner.observe(sample(BASE, 10, .68), now_ns=BASE + 100_000_000)
      self.assertEqual(owner.observe(sample(BASE + 600_000_000, 22, .8, (900, 170, 1000, 270)), now_ns=BASE + 700_000_000), BASE + 600_000_000)

  def test_role_requires_boolean(self):
    for value in (1, None, 'true'):
      with self.assertRaises(ValueError):
        StopConfirmation(display_only=cast(bool, value))
