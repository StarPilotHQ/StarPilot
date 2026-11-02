"""Presentation edge cases without constructing device services or GPU resources."""

from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import clip, feature_settings as view, settings_geometry as geometry
from openpilot.starpilot.ui.feature_settings_state import (
  FeatureInput, FeatureRow, FeatureSettingsState, FeatureUiAction, FEATURE_ROW_TOP, FEATURE_ROW_HEIGHT, FEATURE_VISIBLE_ROWS,
  feature_scroll, feature_row_top, feature_page_counter_left, FEATURE_PAGE_COUNTER_WIDTH, feature_footer_buttons,
  value_buttons, value_editor_rect, value_done_rect,
)
from openpilot.starpilot.ui.presentation import FontRole, Profile


def fake_fonts():
  return SimpleNamespace(profile=Profile.LARGE, draw=Mock(),
                         measure=lambda text, _role, size: SimpleNamespace(width=len(text) * size * .5),
                         vertical_ink=lambda *_args: (6, 33))


class FeatureVisualTests(unittest.TestCase):
  def test_screen_off_help_is_visible_with_toggle_on_or_off(self):
    from openpilot.starpilot.ui.display_preferences import SCREEN_OFF, SCREEN_OFF_DESCRIPTION
    self.drawing()
    for expanded in (True, False):
      for value in ("Off", "On"):
        fonts = fake_fonts()
        row = FeatureRow(SCREEN_OFF, "Turn Off Comma Screen", value, choices=("Off", "On"),
                         available=True, reason=SCREEN_OFF_DESCRIPTION)
        view.FeatureSettingsView(fonts).render(FeatureSettingsState(rows=(row,), sidebar_expanded=expanded))
        self.assertIn(SCREEN_OFF_DESCRIPTION, [call.args[0] for call in fonts.draw.call_args_list])

  def test_buttons_share_purple_feedback_without_changing_disabled_or_selected_controls(self):
    self.drawing()
    action = FeatureRow("wifi:test", "Network", "Available", available=True,
                        actions=(("CONNECT", True), ("Forget", True), ("Disabled", False)))
    number = FeatureRow("number", "Level", "50", available=True, step=5, minimum=0, maximum=100)
    preset = replace(number, presets=(("25", "25%"), ("50", "50%")), unit="%")
    for expanded in (True, False):
      editor = FeatureSettingsState(rows=(preset,), editor=True, sidebar_expanded=expanded)
      editor_buttons = value_buttons(editor, preset, feature_row_top(editor))
      done = value_done_rect(editor)
      cases = [(FeatureSettingsState(rows=(action,), sidebar_expanded=expanded), (1500, 189)),
               (FeatureSettingsState(rows=(FeatureRow("pip:reset", "Reset", "", available=True),), sidebar_expanded=expanded), (1930, 189)),
               (FeatureSettingsState(rows=(replace(number, repair_value="25"),), sidebar_expanded=expanded), (1930, 189)),
               (FeatureSettingsState(rows=(number,), sidebar_expanded=expanded), (1800, 220)),
               (FeatureSettingsState(rows=(number,), sidebar_expanded=expanded), (2000, 220)),
               (editor, (done[0] + done[2] / 2, done[1] + done[3] / 2))]
      cases.extend((editor, (x + width / 2, y + height / 2)) for _, (x, y, width, height), enabled in editor_buttons if enabled)
      for scroll, direction in ((0, 1), (5, -1)):
        state = FeatureSettingsState(rows=(number,) * 7, scroll=scroll, sidebar_expanded=expanded)
        x, y, width, height = feature_footer_buttons(state)[0 if direction == -1 else 1]
        cases.append((state, (x + width / 2, y + height / 2)))
      for state, point in cases:
        with self.subTest(expanded=expanded, point=point, row=state.rows[0]):
          touch = FeatureInput(Mock())
          touch.press(*point, state)
          self.assertIsNotNone(touch.pressed_action(state), (state, point))
          renderer = view.FeatureSettingsView(fake_fonts())
          renderer.pressed_action = touch.pressed_action
          rl.draw_rectangle_rounded.reset_mock()
          rl.draw_rectangle_rounded_lines_ex.reset_mock()
          renderer.render(state)
          fills = [call for call in rl.draw_rectangle_rounded.call_args_list if call.args[-1] == geometry.CONTROL_PRESSED_BG]
          borders = [call for call in rl.draw_rectangle_rounded_lines_ex.call_args_list if call.args[-1] == geometry.ACCENT]
          self.assertEqual(len(fills), 1)
          self.assertEqual(len(borders), 1)
          bounds = fills[0].args[0]
          self.assertTrue(bounds.x <= point[0] <= bounds.x + bounds.width and bounds.y <= point[1] <= bounds.y + bounds.height)
          touch.cancel()
          rl.draw_rectangle_rounded.reset_mock()
          renderer.render(state)
          self.assertFalse(any(call.args[-1] == geometry.CONTROL_PRESSED_BG for call in rl.draw_rectangle_rounded.call_args_list))
      state = FeatureSettingsState(rows=(action,), sidebar_expanded=expanded)
      renderer = view.FeatureSettingsView(fake_fonts())
      renderer.pressed_action = Mock(return_value=FeatureUiAction("action", action, 2))
      rl.draw_rectangle_rounded.reset_mock()
      renderer.render(state)
      self.assertFalse(any(call.args[-1] == geometry.CONTROL_PRESSED_BG for call in rl.draw_rectangle_rounded.call_args_list))
      for state, point in ((FeatureSettingsState(rows=(action,), sidebar_expanded=expanded), (1500, 189)),
                           (editor, (editor_buttons[1][1][0] + 20, editor_buttons[1][1][1] + 20))):
        renderer = view.FeatureSettingsView(fake_fonts())
        renderer.pressed_action = Mock(return_value=FeatureInput.target(*point, state))
        disabled = replace(state, rows=(replace(state.rows[0], available=False),))
        rl.draw_rectangle_rounded.reset_mock()
        renderer.render(disabled)
        self.assertFalse(any(call.args[-1] == geometry.CONTROL_PRESSED_BG for call in rl.draw_rectangle_rounded.call_args_list))

  def test_system_value_units_and_confirmation_hint_share_drawn_touch_bounds(self):
    from openpilot.starpilot.ui.runtime_app import SYSTEM_PRESETS
    from openpilot.starpilot.ui.power_owner import HOUR_CHOICES, VOLT_CHOICES
    self.drawing()
    rows = (
      FeatureRow("ScreenBrightness", "Brightness", "42", unit="%", step=1, minimum=5, maximum=100),
      FeatureRow("ScreenTimeout", "Timeout", "30", unit="s", step=5, minimum=5, maximum=60),
      FeatureRow("power:hours", "Parked time", "6 h", choices=HOUR_CHOICES),
      FeatureRow("power:volts", "Voltage", "12.0 V", choices=VOLT_CHOICES),
    )
    for expanded in (True, False):
      for row, expected in zip(rows, ("42%", "30 s", "6 h", "12.0 V"), strict=True):
        row = replace(row, available=True, presets=SYSTEM_PRESETS[row.key])
        state = FeatureSettingsState(rows=(row,), sidebar_expanded=expanded, editor=True,
                                     save_hint="Changes require confirmation" if row.key.startswith("power:") else "Changes save automatically")
        fonts = fake_fonts()
        view.FeatureSettingsView(fonts).render(state)
        text = [call.args[0] for call in fonts.draw.call_args_list]
        self.assertIn(expected, text)
        self.assertIn(state.save_hint, text)
        self.assertIn("Done", text)
        self.assertNotIn("Next", text)
        for index, (value, (x, y, width, height), enabled) in enumerate(value_buttons(state, row, feature_row_top(state))):
          label = value if value in ("-", "+") else dict(row.presets)[value]
          drawn = next(call for call in fonts.draw.call_args_list if call.args[0] == label and call.args[1] == FontRole.MEDIUM)
          self.assertAlmostEqual(drawn.args[3] + fonts.measure(*drawn.args[:3]).width / 2, x + width / 2, delta=.01)
          self.assertGreater(width, fonts.measure(label, FontRole.MEDIUM, drawn.args[2]).width + 24)
          self.assertEqual(height, 200)
          self.assertLessEqual(x + width, 2094.01)
          target = FeatureInput.target(x + width / 2, y + height / 2, state)
          if enabled:
            self.assertEqual(target.direction, (-1 if value == "-" else 1) if value in ("-", "+") else index)
          else:
            self.assertIsNone(target)

  def test_sound_overview_shows_values_without_edit_controls(self):
    self.drawing()
    for expanded in (True, False):
      rows = (FeatureRow("EngageVolume", "Engagement Chime", "40", source=b"40", unit="%", presets=(("25", "25%"),), step=5,
                         minimum=0, maximum=100, available=True, page="EngageVolume"),
              FeatureRow("SoundPack", "Sound Pack", "StarPilot Sounds", available=True, page="SoundPack"))
      state = FeatureSettingsState(page="sounds:overview", rows=rows, sidebar_expanded=expanded)
      fonts = fake_fonts()
      view.FeatureSettingsView(fonts).render(state)
      text = [call.args[0] for call in fonts.draw.call_args_list]
      self.assertIn("40%", text)
      self.assertIn("StarPilot Sounds", text)
      self.assertNotIn("Mute", text)
      self.assertNotIn("+", text)
      self.assertNotIn("-", text)
      for i, row in enumerate(rows):
        target = FeatureInput.target(800, feature_row_top(state) + i * FEATURE_ROW_HEIGHT + 77, state)
        self.assertEqual(target.kind, "open")
        self.assertEqual(target.row, row)

  def test_disabled_display_summaries_mute_labels_values_and_explain_the_master(self):
    from openpilot.starpilot.ui.runtime_app import SYSTEM_PRESETS
    self.drawing()
    reason = "Saved; custom display is off"
    rows = tuple(FeatureRow(key, label, value, unit=unit, available=True, reason=reason, page=key, presets=SYSTEM_PRESETS[key])
                 for key, label, value, unit in (("ScreenBrightness", "Parked Brightness", "42", "%"),
                                                 ("ScreenBrightnessOnroad", "Driving Brightness", "64", "%"),
                                                 ("ScreenTimeout", "Parked Screen Timeout", "30", "s"),
                                                 ("ScreenTimeoutOnroad", "Driving Interaction Timeout", "10", "s")))
    for expanded in (True, False):
      for available in (True, False):
        fonts = fake_fonts()
        state = FeatureSettingsState(rows=tuple(replace(row, available=available) for row in rows), sidebar_expanded=expanded)
        view.FeatureSettingsView(fonts).render(state)
        calls = fonts.draw.call_args_list
        explanations = [call for call in calls if call.args[0] == reason]
        self.assertEqual(len(explanations), 0 if available else len(rows))
        for index, (row, value) in enumerate(zip(rows, ("42%", "64%", "30 s", "10 s"), strict=True)):
          label = next(call for call in calls if call.args[0] == row.label)
          saved = next(call for call in calls if call.args[0] == value)
          self.assertIs(label.args[-1], geometry.TEXT_PRIMARY if available else geometry.TEXT_MUTED)
          self.assertIs(saved.args[-1], geometry.TEXT_SECONDARY if available else geometry.TEXT_MUTED)
          target = FeatureInput.target(800, feature_row_top(state) + (index + .5) * FEATURE_ROW_HEIGHT, state)
          self.assertEqual(target.kind, "open" if available else "details")
          if not available:
            self.assertLess(label.args[4] + 33, explanations[index].args[4] + 6)

  def test_sound_value_and_legal_presets_share_drawn_touch_bounds(self):
    from openpilot.starpilot.ui.runtime_app import SOUND_PRESETS
    self.drawing()
    for expanded in (True, False):
      for key, minimum in (("WarningSoftVolume", 25), ("EngageVolume", 0)):
        for value in ("40", "50", "Auto", *(() if minimum else ("0",))):
          row = FeatureRow(key, "Alert", value, source=value.encode(), step=5, minimum=minimum, maximum=100,
                           available=True, reason="Existing details", unit="%",
                           presets=tuple((v, "Mute" if v == "0" else v if v == "Auto" else v + "%")
                                         for v in SOUND_PRESETS if v == "Auto" or minimum <= int(v) <= 100))
          state = FeatureSettingsState(page="sounds", rows=(row,), sidebar_expanded=expanded, editor=True)
          fonts = fake_fonts()
          rl.draw_line_ex.reset_mock()
          rl.draw_rectangle_rounded_lines_ex.reset_mock()
          with patch.object(view, "draw_settings_header"):
            view.FeatureSettingsView(fonts).render(state)
          rl.draw_line_ex.assert_not_called()
          selected = [call for call in rl.draw_rectangle_rounded_lines_ex.call_args_list if call.args[-1] == view.VALUE_SELECTED_BORDER]
          self.assertEqual(len(selected), 1 if value == "40" else 2)
          rl.draw_rectangle_rounded_lines_ex.reset_mock()
          text = [call.args[0] for call in fonts.draw.call_args_list]
          self.assertIn("Muted" if value == "0" else value if value == "Auto" else value + "%", text)
          self.assertNotIn(row.reason, text)
          self.assertEqual("Mute" in text, minimum == 0)
          self.assertEqual("10%" in text, minimum == 0)
          saved = next(call for call in fonts.draw.call_args_list if call.args[1] == FontRole.SEMI_BOLD)
          self.assertEqual(saved.args[2], 140)
          self.assertEqual(saved.args[-1], geometry.ACCENT)
          self.assertIn("Done", text)
          self.assertNotIn("Back", text)
          self.assertNotIn("Next", text)
          buttons = value_buttons(state, row, feature_row_top(state))
          first, last = buttons[1][1], buttons[-2][1]
          cx, cy, editor_width, editor_height = value_editor_rect(state)
          self.assertEqual(cx, (520 if expanded else 20) + 55)
          self.assertAlmostEqual(saved.args[3] + fonts.measure(*saved.args[:3]).width / 2, cx + editor_width / 2)
          top, bottom = fonts.vertical_ink(*saved.args[:3])
          self.assertAlmostEqual(saved.args[4] + (top + bottom) / 2, buttons[0][1][1] + buttons[0][1][3] / 2)
          self.assertEqual(first[0], cx)
          self.assertAlmostEqual(last[0] + last[2], 2094)
          self.assertGreater(first[1], buttons[0][1][1] + buttons[0][1][3])
          bx, by, bw, bh = value_done_rect(state)
          done = next(call for call in fonts.draw.call_args_list if call.args[0] == "Done")
          self.assertAlmostEqual(done.args[3] + fonts.measure(*done.args[:3]).width / 2, bx + bw / 2)
          self.assertLessEqual(by + bh, cy + editor_height)
          self.assertGreater(by, last[1] + last[3])
          self.assertEqual((bx, bw, bh), (cx, editor_width, 140))
          self.assertEqual(bx + bw, cx + editor_width)
          self.assertEqual(FeatureInput.target(bx + bw / 2, by + bh / 2, state).kind, "back")
          self.assertEqual(FeatureInput.target(bx + 20, by + bh - 5, state).kind, "back")
          self.assertEqual(FeatureInput.target(bx + bw - 20, by + bh - 5, state).kind, "back")
          self.assertIsNone(FeatureInput.target(1900, by + bh + 10, state))
          for index, (button, (x, y, width, height), enabled) in enumerate(value_buttons(state, row, feature_row_top(state))):
            label = "Mute" if button == "0" else button if button in ("-", "+", "Auto") else button + "%"
            drawn = next(call for call in fonts.draw.call_args_list if call.args[0] == label and call.args[1] == FontRole.MEDIUM)
            self.assertEqual(height, 200)
            self.assertEqual(drawn.args[2], 64 if button in ("-", "+") else 44)
            self.assertAlmostEqual(drawn.args[3] + fonts.measure(*drawn.args[:3]).width / 2, x + width / 2, delta=.01)
            target = FeatureInput.target(x + width / 2, y + height / 2, state)
            if enabled:
              self.assertEqual(target.kind, "change" if button in ("-", "+") else "action")
              self.assertEqual(target.direction, (-1 if button == "-" else 1) if button in ("-", "+") else index)
            else:
              self.assertIsNone(target)
          self.assertEqual(value_buttons(replace(state, editor=False), row), ())
          self.assertEqual(FeatureInput.target(1300, feature_row_top(state) + 157, state).kind, "details")
          self.assertIsNone(FeatureInput.target(1300, feature_row_top(state) + 330, state))
          disabled = replace(state, rows=(replace(row, available=False),))
          fonts.draw.reset_mock()
          with patch.object(view, "draw_settings_header"):
            view.FeatureSettingsView(fonts).render(disabled)
          self.assertIn("Editing unavailable", [call.args[0] for call in fonts.draw.call_args_list])
          saved = next(call for call in fonts.draw.call_args_list if call.args[1] == FontRole.SEMI_BOLD)
          self.assertEqual(saved.args[-1], geometry.TEXT_MUTED)
          for _, (x, y, width, height), enabled in value_buttons(disabled, disabled.rows[0], feature_row_top(disabled)):
            self.assertFalse(enabled)
            self.assertIsNone(FeatureInput.target(x + width / 2, y + height / 2, disabled))

  def test_page_counter_counts_partial_pages_and_aligns_with_footer_in_both_layouts(self):
    self.drawing()
    for expanded in (True, False):
      for count, scroll, expected in ((0, 0, "1/1"), (1, 0, "1/1"), (5, 0, "1/1"), (6, 0, "1/2"),
                                       (6, 5, "2/2"), (20, 0, "1/4"), (20, 15, "4/4"), (13, 10, "3/3")):
        with self.subTest(expanded=expanded, count=count, scroll=scroll):
          fonts = fake_fonts()
          state = FeatureSettingsState(rows=(FeatureRow("", "Heading", ""),) * count, scroll=scroll, sidebar_expanded=expanded)
          view.FeatureSettingsView(fonts).render(state, -80)
          counter, = [call for call in fonts.draw.call_args_list if call.args[0] == expected]
          self.assertEqual(counter.args[1:3], (FontRole.MEDIUM, view.DETAIL_SIZE))
          self.assertIs(counter.args[-1], geometry.TEXT_SECONDARY)
          left = 520 if expanded else 20
          self.assertEqual(counter.args[3] + fonts.measure(*counter.args[:3]).width / 2, (left + 25 + 2120) / 2)
          for label in ("Previous", "Next"):
            button = next(call for call in fonts.draw.call_args_list if call.args[0] == label)
            self.assertEqual(counter.args[4], button.args[4])

  def test_page_change_fades_through_one_row_pass_and_preserves_direction(self):
    self.drawing()
    rows = tuple(FeatureRow("", str(index), "") for index in range(10))
    half = view.PAGE_TRANSITION_TIME / 2
    with patch.object(view, "draw_settings_header"), patch.object(view.time, "monotonic") as clock:
      for direction in (1, -1):
        start = FeatureSettingsState(rows=rows, scroll=0 if direction == 1 else 5)
        end = replace(start, scroll=5 if direction == 1 else 0)
        fonts = fake_fonts()
        renderer = view.FeatureSettingsView(fonts)
        clock.return_value = 0.0
        renderer.render(start, -direction * 80)
        for elapsed, shown, offset, alpha in ((0, start, -direction * 44, 0),
                                             (half / 2, start, -direction * 52, 128),
                                             (half, end, direction * 60, 255),
                                             (half * 1.5, end, direction * 30, 128),
                                             (view.PAGE_TRANSITION_TIME, end, 0, 0)):
          with self.subTest(direction=direction, elapsed=elapsed):
            clock.return_value = elapsed
            fonts.draw.reset_mock()
            rl.draw_rectangle_rec.reset_mock()
            for transform in (rl.rl_push_matrix, rl.rl_translatef, rl.rl_pop_matrix):
              transform.reset_mock()
            renderer.render(end)
            counter, = [call for call in fonts.draw.call_args_list if call.args[0] == f"{end.scroll // FEATURE_VISIBLE_ROWS + 1}/2"]
            self.assertEqual(counter.args[4], 980 + 35 - (6 + 33) / 2)
            labels = [call.args[0] for call in fonts.draw.call_args_list if call.args[0].isdigit()]
            self.assertEqual(labels, [row.label for row in shown.rows[shown.scroll:shown.scroll + 5]])
            self.assertEqual(rl.rl_push_matrix.call_count, int(bool(offset)))
            self.assertEqual(rl.rl_pop_matrix.call_count, int(bool(offset)))
            if offset:
              self.assertAlmostEqual(rl.rl_translatef.call_args.args[0], offset)
            else:
              rl.rl_translatef.assert_not_called()
            fills = rl.draw_rectangle_rec.call_args_list
            self.assertEqual(len(fills), int(bool(offset)) + int(alpha != 0))
            if alpha:
              self.assertAlmostEqual(fills[-1].args[1].a, alpha, delta=1)
        self.assertFalse(renderer.reset(end))

  def test_transition_interruptions_show_current_rows_at_rest(self):
    rows = tuple(FeatureRow("", str(index), "") for index in range(10))
    start = FeatureSettingsState(rows=rows)
    end = replace(start, scroll=5)
    with patch.object(view.time, "monotonic", return_value=0):
      changes = (replace(end, page="other"), replace(end, title="Other"), replace(end, subtitle="Description"),
                 replace(end, sidebar_expanded=False), replace(end, rows=rows[:-1]))
      for changed in changes:
        renderer = view.FeatureSettingsView(fake_fonts())
        renderer._page_frame(start, 0, False)
        renderer._page_frame(end, 0, False)
        self.assertEqual(renderer._page_frame(changed, 0, False), (changed, 0, 0))
        self.assertIsNone(renderer._transition)
      renderer = view.FeatureSettingsView(fake_fonts())
      renderer._page_frame(start, 0, False)
      renderer._page_frame(end, 0, False)
      self.assertTrue(renderer.reset(end))
      self.assertEqual(renderer._page_frame(end, 0, False), (end, 0, 0))
      # Rejected/boundary gestures never change scroll and cannot start a transition.
      renderer._page_frame(end, -18, True)
      self.assertEqual(renderer._page_frame(end, 0, False), (end, 0, 0))
      # A second drag takes over immediately.
      renderer._page_frame(start, 0, False)
      self.assertEqual(renderer._page_frame(start, 24, True), (start, 24, 0))

  def test_pending_swap_and_fast_press_release_are_handled_without_an_intermediate_frame(self):
    rows = tuple(FeatureRow("", str(index), "") for index in range(10))
    start = FeatureSettingsState(rows=rows)
    end = replace(start, scroll=5)
    renderer = view.FeatureSettingsView(fake_fonts())
    with patch.object(view.time, "monotonic", return_value=0):
      renderer._page_frame(start, 0, False)
      self.assertTrue(renderer.reset(end))
      self.assertEqual(renderer._page_frame(end, 0, False), (end, 0, 0))
      renderer.reset(start)
      self.assertEqual(renderer._page_frame(end, 0, False), (start, 0, 0))
      renderer.reset()
      self.assertEqual(renderer._page_frame(end, 0, False), (end, 0, 0))

  def test_pull_policy_reuses_gesture_thresholds_for_both_signs(self):
    for sign in (-1, 1):
      for raw, expected in ((20, 0), (36, 0), (60, 24), (80, 44), (96, 60), (120, 60), (300, 60)):
        self.assertEqual(view.feature_pull(sign * raw, True), sign * expected)
        self.assertEqual(view.feature_pull(sign * raw, False), sign * min(expected, 18))

  def test_edge_reveal_feathers_inside_body_with_scroll_authority(self):
    self.drawing()
    rows = (FeatureRow("", "Heading", ""),) * 7
    for expanded, subtitle in ((True, ""), (False, ""), (True, "Description"), (False, "Description")):
      left = 520 if expanded else 20
      for scroll, drag, can_page in ((0, -37.5, True), (5, 37.5, True), (0, -80, True), (5, 80, True),
                                     (0, -300, True), (5, 300, True), (0, 80, False), (5, -80, False), (0, 20, True), (0, 0, False)):
        with self.subTest(expanded=expanded, subtitle=subtitle, scroll=scroll, drag=drag):
          rl.draw_rectangle_rec.reset_mock()
          rl.draw_rectangle_gradient_ex.reset_mock()
          rl.draw_line_ex.reset_mock()
          for transform in (rl.rl_push_matrix, rl.rl_translatef, rl.rl_pop_matrix):
            transform.reset_mock()
          draws = Mock()
          for name in ("draw_rectangle_gradient_ex", "draw_line_ex", "begin_scissor_mode", "draw_rectangle_rec"):
            draws.attach_mock(getattr(rl, name), name)
          fonts = fake_fonts()
          state = FeatureSettingsState(rows=rows, scroll=scroll, sidebar_expanded=expanded, subtitle=subtitle)
          row_top = feature_row_top(state)
          with patch.object(view, "feature_scroll", wraps=feature_scroll) as paging, patch.object(view, "draw_settings_header"):
            view.FeatureSettingsView(fonts).render(state, drag)
          if drag:
            paging.assert_called_once_with(scroll, 1 if drag < 0 else -1, len(rows))
          else:
            paging.assert_not_called()
          self.assertEqual(rl.begin_scissor_mode.call_args.args, (left + 25, row_top, 2100 - left, 5 * FEATURE_ROW_HEIGHT))
          pull = view.feature_pull(drag, can_page)
          self.assertEqual(rl.rl_push_matrix.call_count, int(bool(pull)))
          self.assertEqual(rl.rl_pop_matrix.call_count, int(bool(pull)))
          if pull:
            rl.rl_translatef.assert_called_once_with(pull, 0, 0)
          else:
            rl.rl_translatef.assert_not_called()
          fills = rl.draw_rectangle_rec.call_args_list
          cue = can_page and abs(drag) > 36
          self.assertEqual(len(fills), int(bool(pull)))
          self.assertEqual(rl.draw_rectangle_gradient_ex.call_count, 3 * int(cue))
          self.assertEqual(rl.draw_line_ex.call_count, 2 * int(cue))
          if pull:
            self.assertIs(fills[-1].args[1], geometry.PANEL_BG)
            rect = fills[-1].args[0]
            self.assertEqual((rect.x, rect.y, rect.width, rect.height), (left + 25, row_top, 2100 - left, 5 * FEATURE_ROW_HEIGHT))
          if cue:
            gradient, top_fade, bottom_fade = rl.draw_rectangle_gradient_ex.call_args_list
            gap = abs(pull)
            bounds = gradient.args[0]
            gap_x = 2125 - gap if drag < 0 else left + 25
            self.assertEqual((bounds.x, bounds.y, bounds.width, bounds.height),
                             (gap_x, row_top, gap, 5 * FEATURE_ROW_HEIGHT))
            edge = rl.color_lerp(geometry.PANEL_BG, view.EDGE_ACCENT, gap / 60)
            color_left, color_right = (geometry.PANEL_BG, edge) if drag < 0 else (edge, geometry.PANEL_BG)
            for actual, expected in zip(gradient.args[1:], (color_left, color_left, color_right, color_right), strict=True):
              self.assertEqual((actual.r, actual.g, actual.b, actual.a), (expected.r, expected.g, expected.b, expected.a))
            for fade, y, colors in (
              (top_fade, row_top, (geometry.PANEL_BG, view.EDGE_FADE_CLEAR, view.EDGE_FADE_CLEAR, geometry.PANEL_BG)),
              (bottom_fade, row_top + 5 * FEATURE_ROW_HEIGHT - 96,
               (view.EDGE_FADE_CLEAR, geometry.PANEL_BG, geometry.PANEL_BG, view.EDGE_FADE_CLEAR)),
            ):
              rect = fade.args[0]
              self.assertEqual((rect.x, rect.y, rect.width, rect.height), (gap_x, y, gap, 96))
              self.assertEqual(fade.args[1:], colors)
            self.assertEqual(rl.draw_line_ex.call_args.args[0].x, (2095 if drag < 0 else left + 55) + (8 if drag < 0 else -8))
            order = [call[0] for call in draws.mock_calls]
            self.assertLess(max(index for index, name in enumerate(order) if name == "begin_scissor_mode"),
                            order.index("draw_rectangle_gradient_ex"))
            self.assertLess(max(index for index, name in enumerate(order) if name == "draw_rectangle_gradient_ex"),
                            order.index("draw_line_ex"))
            self.assertLess(max(index for index, name in enumerate(order) if name == "draw_line_ex"),
                            order.index("draw_rectangle_rec"))
          footer = [call for call in fonts.draw.call_args_list if call.args[0] in ("Previous", "Next")]
          self.assertEqual(len(footer), 2)

  def test_edge_failure_restores_scissor_before_rows_move(self):
    self.drawing()
    with patch.object(rl, "draw_rectangle_gradient_ex", side_effect=RuntimeError("edge failed")):
      with self.assertRaisesRegex(RuntimeError, "edge failed"):
        view.FeatureSettingsView(fake_fonts()).render(FeatureSettingsState(rows=(FeatureRow("", "Heading", ""),) * 7), -80)
    rl.rl_push_matrix.assert_not_called()
    self.assertEqual(rl.end_scissor_mode.call_count, 2)

  def test_row_failure_restores_matrix_and_scissor(self):
    self.drawing()
    with patch.object(rl, "draw_line", side_effect=RuntimeError("row failed")), patch.object(view, "draw_settings_header"):
      with self.assertRaisesRegex(RuntimeError, "row failed"):
        view.FeatureSettingsView(fake_fonts()).render(FeatureSettingsState(rows=(FeatureRow("", "Heading", ""),)), -80)
    rl.rl_pop_matrix.assert_called_once()
    rl.end_scissor_mode.assert_called()

  def test_description_space_and_row_hit_targets_share_the_rendered_layout(self):
    self.drawing()
    rows = tuple(FeatureRow(str(index), "Setting", "", available=True, actions=(("OPEN", True),)) for index in range(5))
    for expanded in (True, False):
      for subtitle, expected_top in (("", 112), ("Panel description", 164)):
        with self.subTest(expanded=expanded, subtitle=subtitle):
          state = FeatureSettingsState(subtitle=subtitle, rows=rows, sidebar_expanded=expanded)
          fonts = fake_fonts()
          rl.begin_scissor_mode.reset_mock()
          rl.draw_line.reset_mock()
          view.FeatureSettingsView(fonts).render(state)
          self.assertEqual(feature_row_top(state), expected_top)
          self.assertEqual(rl.begin_scissor_mode.call_args.args[1], expected_top)
          separators = [call for call in rl.draw_line.call_args_list if call.args[-1] == geometry.ROW_SEPARATOR]
          for index, row in enumerate(rows):
            y = expected_top + (index + .5) * FEATURE_ROW_HEIGHT
            action = FeatureInput.target(1930, y, state)
            self.assertEqual((action.kind, action.row), ("action", row))
            self.assertEqual(separators[index].args[1], expected_top + (index + 1) * FEATURE_ROW_HEIGHT - 3)
          self.assertEqual(FeatureInput.target(1930, expected_top + 5 * FEATURE_ROW_HEIGHT, state).kind, "scroll")
          gap = FeatureInput.target(1930, 108, state)
          self.assertEqual(gap.kind if gap else None, "details" if subtitle else None)
          self.assertEqual(FeatureInput.target(1930, 1015, state).kind, "scroll")
          descriptions = [call for call in fonts.draw.call_args_list if call.args[0] == subtitle]
          self.assertEqual(len(descriptions), int(bool(subtitle)))
          if descriptions:
            self.assertLess(descriptions[0].args[4] + 33, expected_top)

  def drawing(self):
    stack = ExitStack()
    self.addCleanup(stack.close)
    for name in ("draw_rectangle_rec", "draw_rectangle_gradient_ex", "draw_rectangle_rounded", "draw_rectangle_rounded_lines_ex", "draw_line", "draw_line_ex",
                 "draw_circle", "begin_scissor_mode", "end_scissor_mode", "rl_push_matrix", "rl_pop_matrix", "rl_translatef"):
      stack.enter_context(patch.object(rl, name))
    return stack

  def test_boolean_uses_display_value_and_excludes_other_controls(self):
    row = FeatureRow("switch", "Switch", "Off", b"1", ("Off", "On"), available=True)
    self.assertIs(view._boolean_value(row), False)
    self.assertIs(view._boolean_value(replace(row, page="child")), False)
    self.assertIs(view._boolean_value(replace(row, value="On", available=False)), True)
    for changes in ({"key": ""}, {"repair_value": "Off"}, {"step": 1},
                    {"key": "pip:reset"}, {"key": "pip:format:test"}, {"key": "long_repair:test"},
                    {"choices": ("Stock", "On")}, {"choices": ("Off", "Auto", "On")},
                    {"value": "Invalid saved choice"}, {"choices": ()}):
      with self.subTest(changes=changes):
        self.assertIsNone(view._boolean_value(replace(row, **changes)))

  def test_parent_toggle_and_open_have_separate_targets_and_navigation_stays_available(self):
    self.drawing()
    for expanded in (True, False):
      for available in (True, False):
        row = FeatureRow("parent", "Custom Display Settings", "Off", choices=("Off", "On"), available=available,
                         page="display", reason="Brightness and screen timing")
        state = FeatureSettingsState(page="system", rows=(row,), sidebar_expanded=expanded)
        fonts = fake_fonts()
        with patch.object(view, "draw_aether_toggle") as toggle:
          view.FeatureSettingsView(fonts).render(state)
        self.assertEqual(toggle.call_args.kwargs["available"], available)
        opened = next(call for call in fonts.draw.call_args_list if call.args[0] == "Open >")
        x = opened.args[3] + fonts.measure(*opened.args[:3]).width / 2
        y = feature_row_top(state) + FEATURE_ROW_HEIGHT / 2
        self.assertEqual(FeatureInput.target(x, y, state), FeatureUiAction("open", row))
        self.assertEqual(FeatureInput.target(800, y, state), FeatureUiAction("open", row))
        self.assertEqual(FeatureInput.target(2000, y, state), FeatureUiAction("change", row) if available else None)
        self.assertIsNone(FeatureInput.target(1730, y, state))
        invalid = replace(state, rows=(replace(row, value="Invalid saved choice", repair_value="Off"),))
        self.assertEqual(FeatureInput.target(2000, y, invalid).kind, "open")

  def test_toggle_stars_only_for_editable_on_and_thumb_keeps_saved_position(self):
    self.drawing()
    rect = rl.Rectangle(1993, 150, 113, 61)
    with patch.object(geometry, "draw_constellation_nodes") as stars:
      centers = {}
      for available in (True, False):
        for value in (False, True):
          rl.draw_rectangle_rounded.reset_mock()
          stars.reset_mock()
          geometry.draw_aether_toggle(rect, value, available=available, seed_id="lane:switch")
          self.assertEqual(stars.call_count, int(available and value))
          self.assertTrue(all(call.args[-1].a > 0 for call in rl.draw_rectangle_rounded.call_args_list))
          knob = next(call.args[0] for call in rl.draw_rectangle_rounded.call_args_list
                      if call.args[0].width == 44)
          centers[available, value] = knob.x + knob.width / 2
      self.assertEqual(centers[True, False], centers[False, False])
      self.assertEqual(centers[True, True], centers[False, True])
      self.assertLess(centers[True, False], centers[True, True])

  def test_constellations_are_stable_bounded_and_visible_before_thumb(self):
    geometry._toggle_constellation.cache_clear()
    self.addCleanup(geometry._toggle_constellation.cache_clear)
    original = geometry._toggle_constellation("lane:first")
    for index in range(160):
      nodes, edges = geometry._toggle_constellation(f"lane:{index}")
      self.assertTrue(2 <= len(nodes) <= 4)
      self.assertTrue(all(node['x'] * 113 + 3 * 0.45 < 62 for node in nodes))
      self.assertTrue(all(0 <= first < len(nodes) and 0 <= second < len(nodes) for first, second in edges))
    self.assertLessEqual(geometry._toggle_constellation.cache_info().currsize, 128)
    self.assertEqual(original, geometry._toggle_constellation("lane:first"))

  def test_mixed_rows_do_not_replace_repairs_actions_or_selectors(self):
    self.drawing()
    fonts = fake_fonts()
    row = FeatureRow("switch", "Switch", "On", choices=("Off", "On"), available=True)
    rows = (row, replace(row, available=False), replace(row, key="repair", repair_value="Off"),
            replace(row, key="power", choices=("Stock", "On")), replace(row, key="pip:reset"),
            FeatureRow("", "Runtime", "Healthy"), FeatureRow("", "Following", ""))
    with patch.object(view, "draw_aether_toggle") as toggles:
      for scroll in range(0, len(rows), FEATURE_VISIBLE_ROWS):
        view.FeatureSettingsView(fonts).render(FeatureSettingsState(rows=rows, scroll=scroll))
    self.assertEqual(toggles.call_count, 2)
    texts = [call.args[0] for call in fonts.draw.call_args_list]
    for text in ("Set Off", "+", "Reset…", "Healthy", "Following"):
      self.assertIn(text, texts)
    healthy = next(call for call in fonts.draw.call_args_list if call.args[0] == "Healthy")
    self.assertEqual(healthy.args[-1], geometry.TEXT_SECONDARY)
    plus = next(call for call in fonts.draw.call_args_list if call.args[0] == "+")
    self.assertEqual(plus.args[2], view.DETAIL_SIZE)

  def test_repair_targets_stay_complete_and_fit_the_button(self):
    self.drawing()
    fonts = fake_fonts()
    renderer = view.FeatureSettingsView(fonts)
    for target in ("0", "Off", "On", "Stock", "Reset", "Auto", "30 s", "10 s", "4.0", "starpilot"):
      with self.subTest(target=target):
        fonts.draw.reset_mock()
        row = FeatureRow("repair", "Repair", "Invalid", available=True, repair_value=target)
        renderer.render(FeatureSettingsState(rows=(row,)))
        labels = [call for call in fonts.draw.call_args_list if call.args[0] in (f"Set {target}", "Set", target)]
        self.assertEqual(" ".join(call.args[0] for call in labels), f"Set {target}")
        for label in labels:
          self.assertLessEqual(fonts.measure(*label.args[:3]).width, 346)
          self.assertEqual(label.args[2], view.DETAIL_SIZE)

  def test_long_text_fits_and_subtitle_ink_clears_rows(self):
    self.drawing()
    fonts = fake_fonts()
    renderer = view.FeatureSettingsView(fonts)
    state = FeatureSettingsState(title="Long title " * 30, subtitle="Subtitle with descenders",
                                 rows=(FeatureRow("switch", "Long label " * 30, "On", choices=("Off", "On"), available=True),))
    renderer.render(state)
    title = next(call for call in fonts.draw.call_args_list if call.args[0].startswith("Long title"))
    self.assertLessEqual(title.args[3] + fonts.measure(*title.args[:3]).width, 2110)
    label = next(call for call in fonts.draw.call_args_list if call.args[0].startswith("Long label"))
    self.assertLessEqual(fonts.measure(*label.args[:3]).width, 1149)
    subtitle = next(call for call in fonts.draw.call_args_list if call.args[0] == state.subtitle)
    self.assertLess(subtitle.args[4] + 33, feature_row_top(state))
    self.assertEqual(renderer._elide("Fits", FontRole.NORMAL, 27, 100), "Fits")
    renderer.render(FeatureSettingsState(rows=(FeatureRow("choice", "Choice", "Long value", available=True),)))
    selector = next(call for call in fonts.draw.call_args_list if call.args[0] == "Long value")
    self.assertEqual(selector.args[2], view.LABEL_SIZE)

  def test_clip_restored_on_failure_at_translated_widget_position(self):
    self.drawing()
    fonts = fake_fonts()
    viewport = rl.Rectangle(20, 30, 2160, 1080)
    with clip.placed_at(viewport):
      with patch.object(view, "draw_settings_header", side_effect=RuntimeError("drawing failed")):
        with self.assertRaisesRegex(RuntimeError, "drawing failed"):
          view.FeatureSettingsView(fonts).render(FeatureSettingsState())
      self.assertEqual(rl.begin_scissor_mode.call_args.args, (20, 30, 2160, 1080))
    rl.end_scissor_mode.assert_called()

  def test_all_rows_reachable_without_skipping_or_repeating_on_last_page(self):
    for count in (0, 1, 4, 5, 8, 9, 13):
      with self.subTest(count=count):
        rows = tuple(FeatureRow("", str(index), "", page=str(index), available=True) for index in range(count))
        scroll, reached = 0, []
        while True:
          state = FeatureSettingsState(rows=rows, scroll=scroll)
          for index in range(min(FEATURE_VISIBLE_ROWS, count - scroll)):
            action = FeatureInput.target(600, FEATURE_ROW_TOP + (index + .5) * FEATURE_ROW_HEIGHT, state)
            self.assertEqual(action.kind, "open")
            reached.append(action.row)
          next_scroll = feature_scroll(scroll, 1, count)
          if next_scroll == scroll:
            break
          scroll = next_scroll
        self.assertEqual(reached, list(rows))
        footer_y = FEATURE_ROW_TOP + FEATURE_VISIBLE_ROWS * FEATURE_ROW_HEIGHT
        self.assertEqual(FeatureInput.target(600, footer_y, state), FeatureUiAction("scroll", direction=-1))
        counter_center = feature_page_counter_left(state) + FEATURE_PAGE_COUNTER_WIDTH / 2
        self.assertIsNone(FeatureInput.target(counter_center, footer_y, state))
        while scroll:
          scroll = feature_scroll(scroll, -1, count)
        self.assertEqual(scroll, 0)

  def test_header_uses_tile_border_and_stars_inside_clip_before_text(self):
    self.drawing()
    for expanded in (True, False):
      fonts = fake_fonts()

      def background(rect, accent):
        self.assertIs(accent, geometry.ACCENT)
        x, y, width, height = rl.begin_scissor_mode.call_args.args
        self.assertLessEqual(x, rect.x - 10)
        self.assertLessEqual(y, rect.y - 10)
        self.assertGreaterEqual(x + width, rect.x + rect.width + 10)
        self.assertGreaterEqual(y + height, rect.y + rect.height + 10)

      def constellation(nodes, edges, rect, accent, glow, fonts=fonts):
        self.assertEqual((nodes, edges), geometry.constellation("settings-header", rect))
        self.assertIs(rect, header.call_args.args[0])
        self.assertIs(accent, geometry.ACCENT)
        self.assertEqual(glow, 1.0)
        fonts.draw.assert_not_called()

      with patch.object(geometry, "draw_hud_background", side_effect=background) as header, \
           patch.object(geometry, "draw_constellation_nodes", side_effect=constellation) as stars:
        view.FeatureSettingsView(fonts).render(FeatureSettingsState(sidebar_expanded=expanded))
      header.assert_called_once()
      stars.assert_called_once()

  def test_header_omits_generic_roots_and_retains_nested_parents(self):
    self.drawing()
    for expanded in (True, False):
      for title, parent, back, expected in (
        ("Driving Model", "StarPilot", True, ["< Back", "Driving Model"]),
        ("Device", "Settings", True, ["< Back", "Device"]),
        ("Lane Centering", "Driving Controls", True, ["< Back", "Driving Controls > ", "Lane Centering"]),
        ("Aggressive", "Long Planner", True, ["< Back", "Long Planner > ", "Aggressive"]),
        ("StarPilot", "", False, ["StarPilot"]),
      ):
        with self.subTest(expanded=expanded, title=title):
          fonts = fake_fonts()
          rl.draw_line.reset_mock()
          geometry.draw_settings_header(fonts, expanded, title, parent, back=back)
          self.assertEqual([call.args[0] for call in fonts.draw.call_args_list], expected[1:] if expanded and back else expected)
          self.assertEqual(rl.draw_line.call_count, int(back and not expanded))

  def test_numeric_value_is_read_only_and_buttons_keep_source_binding(self):
    row = FeatureRow("number", "Number", "75", b"75", step=5, available=True)
    state = FeatureSettingsState(rows=(row,))
    self.assertEqual(FeatureInput.target(1930, FEATURE_ROW_TOP + 50, state).kind, "details")
    self.assertEqual(FeatureInput.target(1820, FEATURE_ROW_TOP + 140, state).direction, -1)
    self.assertEqual(FeatureInput.target(2000, FEATURE_ROW_TOP + 140, state).direction, 1)
    self.assertEqual(FeatureInput.target(1925, FEATURE_ROW_TOP + 140, state).kind, "details")
    self.assertIsNone(FeatureInput.target(2000, FEATURE_ROW_TOP + 140, replace(state, rows=(replace(row, available=False),))))
    actions = []
    owner = FeatureInput(actions.append)
    owner.press(2000, FEATURE_ROW_TOP + 140, state)
    owner.release(2000, FEATURE_ROW_TOP + 140, replace(state, sidebar_expanded=False))
    self.assertFalse(actions)

  def test_header_taps_are_inert_except_collapsed_back(self):
    row = FeatureRow("key", "Setting", "On")
    for expanded in (True, False):
      state = FeatureSettingsState(sidebar_expanded=expanded, parent_title="Driving Controls", subtitle="Help", rows=(row,))
      left = 520 if expanded else 20
      actions = []
      controller = FeatureInput(actions.append)
      for x in (left + 208, 1000, 2140):
        for y in (12, 50, 100):
          controller.press(x, y, state)
          controller.release(x, y, state)
          self.assertFalse(actions)
      controller.press(left + 60, 50, state)
      controller.release(left + 60, 50, state)
      self.assertEqual(actions, [] if expanded else [FeatureUiAction("back")])
      self.assertEqual(FeatureInput.target(1000, 120, state), FeatureUiAction("details"))
      self.assertEqual(FeatureInput.target(1000, feature_row_top(state) + 77, state), FeatureUiAction("details", row))

  def test_collapsed_header_and_content_share_the_expanded_keyline(self):
    self.drawing()
    fonts = fake_fonts()
    for expanded, left in ((True, 520), (False, 20)):
      fonts.draw.reset_mock()
      state = FeatureSettingsState(title="Lane Centering", parent_title="Driving Controls", sidebar_expanded=expanded,
                                   subtitle="Help", rows=(FeatureRow("key", "Setting", "On", choices=("Off", "On"), available=True),))
      view.FeatureSettingsView(fonts).render(state)
      label = next(call for call in fonts.draw.call_args_list if call.args[0] == "Setting")
      self.assertEqual(label.args[3], left + 55)
      self.assertEqual(label.args[2], 50)
      self.assertEqual(label.args[1], FontRole.NORMAL)
      self.assertTrue(all(call.args[2] in (35, 44, 50) for call in fonts.draw.call_args_list))
      header = [call for call in fonts.draw.call_args_list
                if call.args[0] == "< Back" or call.args[0].startswith("Driving Controls") or call.args[0] == "Lane Centering"]
      self.assertEqual(len(header), 2 if expanded else 3)
      self.assertEqual(header[0].args[3], left + 34 if expanded else left + 28)
      self.assertTrue(all(call.args[1:3] == (FontRole.MEDIUM, 44) for call in header))
      self.assertEqual(len({call.args[4] for call in header}), 1)
      self.assertNotEqual(FeatureInput.target(left + 60, 120, state).kind, "back")
      for x in (left + 60, left + 207):
        self.assertEqual(FeatureInput.target(x, 76, state), None if expanded else FeatureUiAction("back"))
      self.assertIsNone(FeatureInput.target(left + 208, 76, state))
      actions = []
      owner = FeatureInput(actions.append)
      owner.press(left + 60, 76, state)
      owner.release(left + 60, 76, replace(state, sidebar_expanded=not expanded))
      self.assertFalse(actions)


if __name__ == "__main__":
  unittest.main()
