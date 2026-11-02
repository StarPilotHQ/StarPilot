"""Curve visual authority and frozen-status geometry without a device UI."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.curve_speed.status import CurveObservation
from openpilot.starpilot.ui.onroad_compact_widgets import MiciSidebarWidgets
from openpilot.starpilot.ui.onroad_curve import controlling, glow_color, glowing, intensity, period, render_glow, status_label, training
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation


def curve(*, applied=True, controlling_speed=True, training_sample=False, glow=True):
  return CurveObservation(True, 16.0, 14.0, applied, controlling_speed, training_sample,
                          25.0, 2.0, 60.0, "available", "selected", "saved", glow, True)


def onroad(**changes):
  state = OnroadState(engaged=True, camera_available=True, speed_mps=20.0, cruise_kph=80.0,
                      speed_limit=SpeedLimitObservation(), curve=curve(), show_curve_status=True,
                      longitudinal_active=True, slc_system_long_available=True)
  return replace(state, **changes)


class CurveVisualTests(unittest.TestCase):
  def test_frozen_curvature_intensity_color_and_pulse_bounds(self):
    self.assertEqual(intensity(None), 0.70)
    self.assertEqual(intensity(0.0001), 0.70)
    self.assertEqual(intensity(-0.02), 1.0)
    self.assertEqual(intensity(0.5), 1.0)
    self.assertEqual(period(0.70), 4.5)
    self.assertEqual(period(1.0), 1.5)
    self.assertEqual((glow_color(0.70).r, glow_color(0.70).g, glow_color(0.70).b), (34, 197, 94))
    self.assertEqual((glow_color(1.0).r, glow_color(1.0).g, glow_color(1.0).b), (201, 34, 49))

  def test_control_label_needs_selected_owner_and_real_long_authority(self):
    state = onroad()
    self.assertTrue(controlling(state))
    self.assertEqual(status_label(state), "CURVE 31 MPH")
    self.assertIsNone(status_label(onroad(show_curve_status=False)))
    self.assertFalse(controlling(onroad(longitudinal_active=False)))
    self.assertIsNone(status_label(onroad(longitudinal_active=False)))
    self.assertFalse(controlling(onroad(slc_system_long_available=False)))
    self.assertFalse(controlling(onroad(curve=curve(applied=False, controlling_speed=False))))
    self.assertFalse(glowing(onroad(curve=None)))

  def test_manual_learning_and_stale_absence_never_claim_control(self):
    manual = onroad(curve=curve(applied=False, controlling_speed=False, training_sample=True, glow=False),
                    longitudinal_active=False, slc_system_long_available=False)
    self.assertTrue(training(manual))
    self.assertFalse(controlling(manual))
    self.assertEqual(status_label(manual), "CURVE LEARNING 25%")
    self.assertIsNone(status_label(replace(manual, curve=None)))

  def test_glow_uses_four_frozen_edges_only_with_observation(self):
    rect = rl.Rectangle(30, 30, 1800, 1020)
    with patch.object(rl, "draw_rectangle_gradient_v") as vertical, patch.object(rl, "draw_rectangle_gradient_h") as horizontal:
      render_glow(rect, onroad(curve=None), border_width=30, time_s=1.0)
      self.assertEqual((vertical.call_count, horizontal.call_count), (0, 0))
      render_glow(rect, onroad(), border_width=30, time_s=1.0)
      self.assertEqual((vertical.call_count, horizontal.call_count), (2, 2))
      self.assertEqual(vertical.call_args_list[0].args[:4], (30, 30, 1800, 75))
      baseline_color = vertical.call_args_list[0].args[4]
      self.assertEqual((baseline_color.r, baseline_color.g, baseline_color.b), (34, 197, 94))
      vertical.reset_mock()
      render_glow(rect, onroad(curve=replace(curve(), road_curvature=0.02)), border_width=30, time_s=1.0)
      sharp_color = vertical.call_args_list[0].args[4]
      self.assertEqual((sharp_color.r, sharp_color.g, sharp_color.b), (201, 34, 49))

  def test_compact_curve_cue_is_independent_of_large_status_flag(self):
    widget = MiciSidebarWidgets()
    with patch.object(rl, "draw_rectangle"), patch.object(widget, "_confidence_ball"), \
         patch.object(widget, "_personality"), patch.object(widget, "_curve_icon") as curve_icon, \
         patch.object(widget, "_chill_icon") as chill_icon:
      widget.render(rl.Rectangle(0, 0, 536, 240), onroad(show_curve_status=False))
      curve_icon.assert_called_once()
      chill_icon.assert_not_called()
      widget.render(rl.Rectangle(0, 0, 536, 240), onroad(curve=None))
      chill_icon.assert_called_once()


if __name__ == "__main__":
  unittest.main()
