from dataclasses import replace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpilot.starpilot.ui import onroad_border
from openpilot.starpilot.ui.appearance_preferences import OnroadAppearance
from openpilot.starpilot.ui.onroad_border import half_colors, render_compact_half_borders, render_large_half_borders
from openpilot.starpilot.ui.onroad_state import AlertSize, BorderSignals, OnroadAlert
from openpilot.starpilot.ui.preview_shell import reference_onroad
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
from openpilot.starpilot.ui.runtime_app import visual_border_state
from openpilot.starpilot.ui.pip_sidecam import Signals
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, vehicle_ui


def state(signals: BorderSignals | None, *, signal_enabled: bool = True, blindspot_enabled: bool = True):
  return replace(reference_onroad('onroad_engaged_no_camera'), camera_available=True,
                 border_signals=signals,
                 appearance=OnroadAppearance(show_signal_border=signal_enabled,
                                             show_blindspot_border=blindspot_enabled))


class TestOnroadBorder(unittest.TestCase):
  def test_visual_warning_joins_oem_border_without_requiring_oem_bsm(self):
    current = state(BorderSignals(False, True, False, False))
    visual = visual_border_state(current, Signals(True, False, False, False, False, True, False))
    self.assertEqual(visual.border_signals, BorderSignals(False, True, True, False))
    self.assertEqual(half_colors(visual, 0.1)[0], onroad_border.BLINDSPOT_RED)
    self.assertIs(visual_border_state(current, Signals(True, False, False, False, False)), current)

  def test_red_blindspot_wins_over_same_side_amber_blinker(self):
    current = state(BorderSignals(True, True, True, False))
    left, right = half_colors(current, 0.3)
    self.assertEqual(left, onroad_border.BLINDSPOT_RED)
    self.assertIsNone(right)  # 250 ms flicker is off; red remains steady.
    self.assertEqual(half_colors(current, 0.1), (onroad_border.BLINDSPOT_RED, onroad_border.BLINKER_AMBER))

  def test_saved_switches_and_full_alert_preserve_visual_priority(self):
    signals = BorderSignals(True, False, True, False)
    self.assertEqual(half_colors(state(signals, signal_enabled=False), 0.1)[0], onroad_border.BLINDSPOT_RED)
    self.assertEqual(half_colors(state(signals, blindspot_enabled=False), 0.1)[0], onroad_border.BLINKER_AMBER)
    self.assertEqual(half_colors(state(signals, signal_enabled=False, blindspot_enabled=False), 0.1), (None, None))
    self.assertEqual(half_colors(replace(state(signals), alert=OnroadAlert(size=AlertSize.FULL)), 0.1), (None, None))
    self.assertEqual(half_colors(replace(state(signals), alert=OnroadAlert(size=AlertSize.SMALL, critical=True)), 0.1), (None, None))
    self.assertEqual(half_colors(state(None), 0.1), (None, None))

  def test_each_half_is_clipped_to_camera_content_and_draws_same_inset_border(self):
    current = state(BorderSignals(True, False, True, True))
    with patch.object(onroad_border.clip, 'begin_scissor_mode') as begin, \
         patch.object(onroad_border.clip, 'end_scissor_mode') as end, \
         patch.object(onroad_border.rl, 'draw_rectangle_rounded_lines_ex') as draw:
      render_compact_half_borders(current, 0.1)
    self.assertEqual([call.args for call in begin.call_args_list], [(0, 0, 238, 240), (238, 0, 238, 240)])
    self.assertEqual(end.call_count, 2)
    self.assertEqual(draw.call_count, 2)
    self.assertEqual([(call.args[0].x, call.args[0].y, call.args[0].width, call.args[0].height)
                      for call in draw.call_args_list], [(4, 4, 468, 232)] * 2)

  def test_starport_large_edge_tints_each_half(self):
    content = onroad_border.rl.Rectangle(30, 30, 1860, 1020)
    with patch.object(onroad_border.clip, 'begin_scissor_mode') as begin, \
         patch.object(onroad_border.clip, 'end_scissor_mode') as end, \
         patch.object(onroad_border.rl, 'draw_rectangle_rounded_lines_ex') as draw:
      render_large_half_borders(state(BorderSignals(True, False, False, True)), 0.1, content, 1080)
    self.assertEqual([call.args for call in begin.call_args_list], [(0, 0, 960, 1080), (960, 0, 960, 1080)])
    self.assertEqual(end.call_count, 2)
    self.assertEqual([(call.args[1:], call.args[0].width) for call in draw.call_args_list],
                     [((0.12, 10, 30, onroad_border.BLINKER_AMBER), 1860), ((0.12, 10, 30, onroad_border.BLINDSPOT_RED), 1860)])
    with patch.object(onroad_border.rl, 'draw_rectangle_rounded_lines_ex') as draw:
      render_large_half_borders(state(None), 0.1, content, 1080)
    draw.assert_not_called()

  def test_stale_or_invalid_car_clears_visual_signal(self):
    ui = vehicle_ui(self.enterContext(TemporaryDirectory()),
                    openpilotLongitudinalControl=True, carFingerprint="OTHER", pcmCruise=False)
    car = ui.sm['carState']
    car.canValid = True
    car.canTimeout = False
    car.leftBlinker = True
    car.rightBlinker = False
    car.leftBlindspot = True
    car.rightBlindspot = False
    adapter = RuntimeSnapshotAdapter(ui)
    fresh = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
    self.assertEqual(fresh.border_signals, BorderSignals(True, False, True, False))
    ui.sm.logMonoTime['carState'] = NOW - 1_000_000_000
    self.assertIsNone(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.border_signals)
    ui.sm.logMonoTime['carState'] = NOW
    car.canValid = False
    self.assertIsNone(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.border_signals)
