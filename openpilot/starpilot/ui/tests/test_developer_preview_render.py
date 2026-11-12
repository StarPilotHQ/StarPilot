"""Synthetic onroad visuals remain presentation-only across both device profiles."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.developer_preview import OnroadVisualPreview
from openpilot.starpilot.ui.onroad import CornerHintCache, OnroadView
from openpilot.starpilot.ui.onroad_cem_gauge import AetherGauge, IndicatorType
from openpilot.starpilot.ui.onroad_navigation_favorites import NavigationFavorites
from openpilot.starpilot.ui.onroad_compact_widgets import MiciSidebarWidgets
from openpilot.starpilot.ui.onroad_conditional import status as conditional_status
from openpilot.starpilot.ui.onroad_curve import controlling, glowing, render_glow, status_label
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import Profile


def state(*, full_alert=False, cem=True, csc=True) -> OnroadState:
  return OnroadState(engaged=False, camera_available=False, speed_mps=0.0, cruise_kph=None,
                     speed_limit=SpeedLimitObservation(),
                     alert=OnroadAlert(size=AlertSize.FULL if full_alert else AlertSize.NONE),
                     visual_preview=OnroadVisualPreview('CURVE' if cem else None, .0115 if csc else None))


class DeveloperPreviewRenderTests(unittest.TestCase):
  def test_synthetic_labels_do_not_change_real_authority(self):
    both = state()
    self.assertFalse(controlling(both))
    self.assertFalse(glowing(both))
    self.assertEqual(status_label(both), 'CURVE PREVIEW')
    label = conditional_status(both)
    self.assertIsNotNone(label)
    if label is None:
      self.fail("the synthetic CEM state has no status")
    self.assertEqual(label[:2], ('CEM', 'PREVIEW CURVE'))
    self.assertIsNone(status_label(replace(both, visual_preview=None)))
    self.assertIsNone(conditional_status(replace(both, visual_preview=None)))
    self.assertIsNone(status_label(state(full_alert=True)))
    self.assertIsNone(conditional_status(state(full_alert=True)))

  def test_synthetic_curve_glow_obeys_full_alert_without_control_claim(self):
    rect = rl.Rectangle(30, 30, 1800, 1020)
    with patch.object(rl, 'draw_rectangle_gradient_v') as vertical, patch.object(rl, 'draw_rectangle_gradient_h') as horizontal:
      render_glow(rect, state(), border_width=30, time_s=1.0)
      self.assertEqual((vertical.call_count, horizontal.call_count), (2, 2))
      vertical.reset_mock()
      horizontal.reset_mock()
      render_glow(rect, state(full_alert=True), border_width=30, time_s=1.0)
      self.assertEqual((vertical.call_count, horizontal.call_count), (0, 0))

  def test_compact_center_prefers_curve_then_cem_and_never_overlays_full_alert(self):
    fonts = Mock()
    fonts.measure.return_value = NS(width=30, height=20)
    sidebar = MiciSidebarWidgets(fonts)
    with patch.object(rl, 'draw_rectangle'), patch.object(sidebar, '_confidence_ball'), \
         patch.object(sidebar, '_personality'), patch.object(sidebar, '_curve_icon') as icon, \
         patch.object(sidebar, '_chill_icon'):
      sidebar.render(rl.Rectangle(0, 0, 536, 240), state())
      icon.assert_called_once()
      icon.reset_mock()
      sidebar.render(rl.Rectangle(0, 0, 536, 240), state(csc=False))
      icon.assert_called_once()
      icon.reset_mock()
      sidebar.render(rl.Rectangle(0, 0, 536, 240), state(full_alert=True))
      icon.assert_not_called()

  def test_large_and_compact_keep_badge_even_for_full_alert(self):
    for profile in (Profile.LARGE, Profile.COMPACT):
      with self.subTest(profile=profile):
        view = OnroadView.__new__(OnroadView)
        view._fade = Mock()
        view.personality = Mock()
        fonts = Mock()
        fonts.profile = profile
        fonts.measure.return_value = NS(width=100, height=20)
        view.fonts = fonts
        view.navigation_favorites = NavigationFavorites(fonts)
        view.camera_layer = None
        view.background_layer = None
        view.pip_layer = None
        view.extra_overlays = None
        view.alert = Mock()
        view.navigation = Mock()
        view.projection_viewport = None
        view._corner_cache = CornerHintCache()
        view.unified_speed = Mock()
        view.current_speed = Mock()
        view.steering_wheel = Mock()
        view.compact_hud = Mock()
        view.compact_sidebar = Mock()
        view.torque_bar = Mock()
        view._fade_path = Path('/unused')
        with patch('openpilot.starpilot.ui.onroad.clip.begin_scissor_mode'), \
             patch('openpilot.starpilot.ui.onroad.clip.end_scissor_mode'), \
             patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_rec'), \
             patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_gradient_v'), \
             patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_lines_ex'), \
             patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle'), \
             patch('openpilot.starpilot.ui.onroad.rl.draw_texture_ex'), \
             patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_rounded'), \
             patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_rounded_lines_ex'), \
             patch('openpilot.starpilot.ui.onroad.render_corner_hint'), \
             patch.object(view, '_slc_actions'):
          view.render(state(full_alert=True))
        drawn = [call.args[0] for call in fonts.draw.call_args_list]
        self.assertTrue(any('SYNTHETIC REPLAY PREVIEW' in text for text in drawn))
        self.assertFalse(any('CEM PREVIEW' in text or 'CURVE PREVIEW' in text for text in drawn))

  def test_large_nonalert_draws_canonical_gauge_and_preview_labels_without_real_curve(self):
    view = OnroadView.__new__(OnroadView)
    view._fade = Mock()
    view.personality = Mock()
    fonts = Mock()
    fonts.profile = Profile.LARGE
    view.fonts = fonts
    view.cem_gauge = AetherGauge(fonts)
    view.navigation_favorites = NavigationFavorites(fonts)
    view.camera_layer = None
    view.background_layer = None
    view.pip_layer = None
    view.extra_overlays = None
    view.alert = Mock()
    view.navigation = Mock()
    view.projection_viewport = None
    view._corner_cache = CornerHintCache()
    view.unified_speed = Mock()
    view.current_speed = Mock()
    view.steering_wheel = Mock()
    view.torque_bar = Mock()
    with patch('openpilot.starpilot.ui.onroad.clip.begin_scissor_mode'), \
         patch('openpilot.starpilot.ui.onroad.clip.end_scissor_mode'), \
         patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_rec'), \
         patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_gradient_v'), \
         patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_gradient_h'), \
         patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_lines_ex'), \
         patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_rounded'), \
         patch('openpilot.starpilot.ui.onroad.rl.draw_rectangle_rounded_lines_ex'), \
         patch('openpilot.starpilot.ui.onroad.render_corner_hint'), \
         patch.object(view.cem_gauge, '_render_unified_road') as road, \
         patch.object(view, '_slc_actions'):
      view.render(state())
      road.assert_called_once()
      self.assertEqual(road.call_args.args[3].indicator_type, IndicatorType.ROAD_CURVE)
      self.assertEqual(road.call_args.args[3].indicator_value, .0115)
      road.reset_mock()
      view.render(replace(state(), visual_preview=OnroadVisualPreview('STOP LIGHT', None)))
    road.assert_called_once()
    self.assertEqual(road.call_args.args[3].indicator_type, IndicatorType.STOP_LIGHT)
    drawn = [call.args[0] for call in fonts.draw.call_args_list]
    self.assertIn('CURVE PREVIEW', drawn)
    self.assertFalse(any(text.startswith(('CEM PREVIEW', 'CCM PREVIEW')) for text in drawn))
    self.assertIn('SYNTHETIC REPLAY PREVIEW', drawn)
    self.assertIn('CEM + CSC', drawn)


if __name__ == '__main__':
  unittest.main()
