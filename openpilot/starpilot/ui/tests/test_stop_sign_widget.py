from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch, MagicMock
import unittest

from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation, ObservationKind
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.large_speed_geometry import LARGE_SIGN_SCALE, SIGN_HEIGHT, LIMIT_RECTS
from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget
from openpilot.starpilot.ui.stop_sign_widget import render_stop_sign, stop_sign_bounds


class TestStopSignWidget(unittest.TestCase):
  def state(self):
    state = OnroadState(True, True, 10., 55., SpeedLimitObservation(ObservationKind.VALID, 'vision', 15.))
    return replace(state, observed_ns=10_000_000_000, stop_sign_observed_ns=9_000_000_000)

  def test_approved_full_screen_positions(self):
    state = self.state()
    self.assertEqual(stop_sign_bounds(Profile.COMPACT, state)[:3], (226, 24, 70.))
    left, top, size, _ = stop_sign_bounds(Profile.LARGE, state)
    x, y, width, _ = LIMIT_RECTS['large']
    self.assertEqual(left, x + width + 24)
    self.assertAlmostEqual(top + size/2, y + SIGN_HEIGHT/2*LARGE_SIGN_SCALE)
    self.assertAlmostEqual(size, 100*LARGE_SIGN_SCALE)

  def test_camera_age_expiry_and_missing_observation_hide(self):
    state = self.state()
    for stamp, visible in [(9_000_000_000, True), (8_500_000_000, False), (0, False), (10_000_000_001, False)]:
      with patch('openpilot.starpilot.ui.stop_sign_widget.draw_stop_sign') as draw:
        render_stop_sign(NS(profile=Profile.COMPACT), replace(state, stop_sign_observed_ns=stamp))
        self.assertEqual(draw.called, visible)

  def test_stop_is_independent_of_a_missing_numeric_speed_read(self):
    state = replace(self.state(), speed_limit=SpeedLimitObservation())
    widget = SpeedLimitWidget.__new__(SpeedLimitWidget)
    widget.profile = Profile.COMPACT
    widget.fonts = MagicMock(profile=Profile.COMPACT)
    widget.pulse = MagicMock()
    with patch('openpilot.starpilot.ui.stop_sign_widget.draw_stop_sign') as draw:
      widget.render(state)
    draw.assert_called_once()

  def test_stop_remains_visible_when_speed_display_and_slc_are_disabled(self):
    state = self.state()
    state = replace(state, speed_limit=SpeedLimitObservation(),
                    appearance=replace(state.appearance, show_speed_limit_sign=False))
    self.assertFalse(state.speed_limit.action_enabled)
    widget = SpeedLimitWidget.__new__(SpeedLimitWidget)
    widget.profile = Profile.COMPACT
    widget.fonts = MagicMock(profile=Profile.COMPACT)
    widget.pulse = MagicMock()
    with patch('openpilot.starpilot.ui.stop_sign_widget.draw_stop_sign') as draw:
      widget.render(state)
    draw.assert_called_once()
