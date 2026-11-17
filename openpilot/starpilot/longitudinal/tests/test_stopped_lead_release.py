"""Focused CEM release guards; no plant or emergency stopping-distance claim."""

import unittest
from dataclasses import replace
from openpilot.starpilot.longitudinal import experimental_release as m

KEY = ('settings-owner', 1, 1000000000, 'conditional_experimental')
LEAD = m.ReleaseLead(4.48, -0.046, -0.005, True, 1.0, 0.326)


def frame(i, experimental=False, **changes):
  t = 10000000000 + i * 50000000
  return replace(m.ReleaseFrame(t + 1000000, t, experimental, 0.43, LEAD), **changes)


def armed():
  o = m.ExperimentalRelease()
  assert o.step(KEY, frame(0, True), previous_target=-0.4, target=-0.4, follow_seconds=1.45, blocked=False) is None
  return o


def step(o, f=None, **kw):
  args = {'previous_target': -0.403112471, 'target': 0.545009136, 'follow_seconds': 1.45, 'blocked': False}
  args.update(kw)
  return o.step(KEY, frame(1) if f is None else f, **args)


class ReleaseCases(unittest.TestCase):
  def test_logged_stopped_radar_handoff_caps_positive_request(self):
    self.assertEqual(step(armed()), 0.06)

  def test_never_manufactures_braking(self):
    for previous in (-3.5, -1.0, -0.4, 0.0, 0.1, 0.3):
      value = step(armed(), previous_target=previous, target=1.0)
      self.assertIsNotNone(value)
      self.assertGreaterEqual(value, 0.0)
      self.assertLessEqual(value, 1.0)

  def test_stronger_braking_and_negative_requests_are_untouched(self):
    for target in (-3.5, -3.0, -1.0, -0.4, -0.2, -0.01, 0.0):
      for previous in (-3.5, -0.4, 0.0, 0.5):
        self.assertIsNone(step(armed(), target=target, previous_target=previous))

  def test_sustained_modes_and_owner_loss_do_not_arm(self):
    self.assertIsNone(step(m.ExperimentalRelease()))
    self.assertIsNone(step(armed(), frame(1, True)))
    o = armed()
    self.assertIsNone(o.step(None, frame(1), previous_target=-0.4, target=0.5, follow_seconds=1.45, blocked=False))
    self.assertIsNone(step(o, frame(2)))

  def test_conditional_chill_keeps_its_existing_low_speed_behavior(self):
    key = (*KEY[:3], 'conditional_chill')
    owner = m.ExperimentalRelease()
    for index, experimental in ((0, True), (1, False)):
      self.assertIsNone(owner.step(key, frame(index, experimental), previous_target=-0.4, target=0.5,
                                  follow_seconds=1.45, blocked=False))

  def test_stopped_branch_rejects_departure_vision_only_and_bad_geometry(self):
    for lead in (
      None,
      replace(LEAD, radar=False),
      replace(LEAD, speed=0.5),
      replace(LEAD, speed=-0.50001),
      replace(LEAD, acceleration=0.20001),
      replace(LEAD, acceleration=-0.20001),
      replace(LEAD, lateral_offset=1.5001),
      replace(LEAD, distance=0.43 * 1.45 - 0.001),
    ):
      with self.subTest(lead=lead):
        self.assertIsNone(step(armed(), frame(1, lead=lead)))

  def test_boundaries_timestamps_and_blockers(self):
    for speed in (0.0, 2.5):
      self.assertIsNone(step(armed(), frame(1, speed=speed)))
    for f in (None, frame(1, model_ns=0), frame(1, now_ns=1), frame(1, speed=float('nan')), frame(1, lead=replace(LEAD, speed=float('inf')))):
      o = armed()
      self.assertIsNone(o.step(KEY, f, previous_target=-0.4, target=0.5, follow_seconds=1.45, blocked=False))
    for kw in ({'blocked': True}, {'follow_seconds': 0.49}, {'target': float('nan')}, {'previous_target': float('inf')}):
      self.assertIsNone(step(armed(), **kw))

  def test_moving_lead_existing_oracle_is_unchanged(self):
    lead = m.ReleaseLead(11.0, 6.8, 0.0, True, 1.0, 0.0)
    o = m.ExperimentalRelease()
    step(o, frame(0, True, speed=6.75, lead=lead))
    self.assertAlmostEqual(step(o, frame(1, speed=6.75, lead=lead), previous_target=-0.25, target=-0.03), -0.19)


if __name__ == '__main__':
  unittest.main()
