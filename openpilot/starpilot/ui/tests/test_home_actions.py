import unittest
from dataclasses import FrozenInstanceError, replace

from openpilot.starpilot.ui.home_state import HomeAction, HomeActionKind, HomeInput
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.preview_home import reference_state


class TestHomeActions(unittest.TestCase):
  def setUp(self):
    self.actions = []
    self.state = reference_state()

  def test_compact_short_press_opens_settings_without_changing_state(self):
    handler = HomeInput(Profile.COMPACT, self.actions.append)
    handler.press(200, 100, 10)
    handler.release(200, 100, 10.5, self.state)
    self.assertEqual(self.actions, [HomeAction(HomeActionKind.OPEN_SETTINGS)])
    self.assertFalse(self.state.experimental_enabled)

  def test_compact_right_edge_is_upstream_alerts_pill_zone(self):
    handler = HomeInput(Profile.COMPACT, self.actions.append)
    handler.press(500, 200, 10)
    handler.release(500, 200, 10.5, self.state)
    self.assertEqual(self.actions, [HomeAction(HomeActionKind.OPEN_ALERTS)])

  def test_long_press_requests_one_change_and_consumes_release(self):
    for enabled in (False, True):
      with self.subTest(enabled=enabled):
        self.actions.clear()
        state = replace(self.state, experimental_enabled=enabled)
        handler = HomeInput(Profile.COMPACT, self.actions.append)
        handler.press(200, 100, 10)
        handler.tick(10.5, state)
        self.assertEqual(self.actions, [])
        handler.tick(10.501, state)
        handler.tick(12, state)
        handler.release(200, 100, 15, state)
        handler.tick(20, state)
        self.assertEqual(self.actions, [HomeAction(HomeActionKind.SET_EXPERIMENTAL, not enabled)])
        self.assertEqual(state.experimental_enabled, enabled)

  def test_unavailable_long_press_cannot_toggle_or_open_settings(self):
    handler = HomeInput(Profile.COMPACT, self.actions.append)
    handler.press(200, 100, 10)
    handler.release(200, 100, 11, replace(self.state, experimental_available=False))
    self.assertEqual(self.actions, [])

  def test_capability_is_rechecked_at_activation(self):
    handler = HomeInput(Profile.COMPACT, self.actions.append)
    handler.press(200, 100, 10)
    handler.tick(10.4, self.state)
    handler.tick(10.6, replace(self.state, experimental_available=False))
    handler.tick(12, self.state)
    self.assertEqual(self.actions, [])

  def test_leaving_home_restarts_hold_when_pointer_returns(self):
    handler = HomeInput(Profile.COMPACT, self.actions.append)
    handler.press(200, 100, 10)
    handler.move(600, 100, 10.4)
    handler.tick(11, self.state)
    handler.move(200, 100, 12)
    handler.tick(12.4, self.state)
    self.assertEqual(self.actions, [])
    handler.release(200, 100, 12.6, self.state)
    self.assertEqual(self.actions, [HomeAction(HomeActionKind.SET_EXPERIMENTAL, True)])

  def test_cancel_or_release_outside_cannot_dispatch(self):
    handler = HomeInput(Profile.COMPACT, self.actions.append)
    handler.press(200, 100, 10)
    handler.cancel()
    handler.release(200, 100, 11, self.state)
    handler.press(200, 100, 12)
    handler.release(600, 100, 13, self.state)
    handler.press(-1, 100, 14)
    handler.release(200, 100, 15, self.state)
    self.assertEqual(self.actions, [])

  def test_large_settings_and_mode_actions_respect_distinct_targets(self):
    handler = HomeInput(Profile.LARGE, self.actions.append)
    handler.press(100, 200, 10)  # Sidebar owns this press, even outside its settings icon.
    handler.release(100, 100, 10.1, self.state)
    handler.press(1500, 200, 11)
    handler.tick(15, self.state)
    handler.release(1500, 200, 16, self.state)
    handler.press(800, 800, 20)
    handler.release(100, 100, 20.1, self.state)
    handler.press(1500, 200, 21)
    handler.release(1500, 400, 21.1, self.state)
    self.assertEqual(self.actions, [HomeAction(HomeActionKind.OPEN_SETTINGS), HomeAction(HomeActionKind.OPEN_TOGGLES)])

  def test_unpaired_large_home_opens_pairing_only_from_its_button(self):
    handler = HomeInput(Profile.LARGE, self.actions.append)
    unpaired = replace(self.state, paired=False)
    handler.press(1500, 350, 10)
    handler.release(1500, 350, 10.1, unpaired)
    self.assertEqual(self.actions, [HomeAction(HomeActionKind.OPEN_PAIRING)])
    self.actions.clear()
    handler.press(1500, 350, 11)
    handler.release(1500, 350, 11.1, self.state)
    self.assertEqual(self.actions, [])

  def test_nested_view_data_is_immutable(self):
    with self.assertRaises(FrozenInstanceError):
      self.state.stats.all_time.__setattr__("drives", 5)
    with self.assertRaises(FrozenInstanceError):
      self.state.__setattr__("experimental_enabled", True)
    self.assertIsInstance(self.state.stats.records, tuple)


if __name__ == "__main__":
  unittest.main()
