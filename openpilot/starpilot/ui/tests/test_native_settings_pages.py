"""Paginated native controls retain callbacks, fresh state, and network authority."""

import ast
from collections.abc import Callable
from enum import Enum
from pathlib import Path
import sys
from types import SimpleNamespace as NS, ModuleType
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.feature_settings_state import (
  FEATURE_ROW_TOP, FEATURE_ROW_HEIGHT, FeatureInput, FeatureUiAction, feature_row_top, value_buttons, value_done_rect,
)
from openpilot.starpilot.ui.starpilot_settings_adapter_large import StarPilotSettingsAdapterLarge
from openpilot.starpilot.ui.network_panel import NetworkPanelBridge
from openpilot.starpilot.ui.tests.test_feature_settings_visuals import fake_fonts
from openpilot.system.ui.lib.application import gui_app, MouseEvent, MousePos
from openpilot.system.ui.lib.wifi_manager import Network, SecurityType, WifiManager
from openpilot.system.ui.widgets import DialogResult, Widget
from openpilot.system.ui.widgets.list_view import ButtonAction, DualButtonAction, ItemAction, MultipleButtonAction, ToggleAction
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.system.ui.widgets.network import NetworkUI, PanelType, UIState, WifiManagerUI


def item(title, action=None):
  return NS(title=title, action_item=action, enabled=True, is_visible=True, callback=Mock(), description="Details",
            description_opened_callback=None)


def button_action():
  action = Mock(spec=ButtonAction)
  action.text, action.value, action.enabled = "OPEN", "Status", True
  return action


class NativeSettingsPageTests(unittest.TestCase):
  def test_initial_cruise_editor_uses_real_control_and_params_in_both_units(self):
    import tempfile
    from openpilot.common.params import Params
    from openpilot.selfdrive.ui.layouts.settings import initial_cruise_speed as initial_speed

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      ui = NS(params=params, is_metric=True, initial_cruise_speed=80, CP=NS(pcmCruise=False), has_longitudinal_control=True)

      def refresh():
        ui.is_metric = params.get_bool("IsMetric")
        ui.initial_cruise_speed = params.get("InitialCruiseSpeedKph", return_default=True)
        row.is_visible = ui.CP is not None and ui.has_longitudinal_control and not ui.CP.pcmCruise

      ui.update_params = refresh
      with patch.object(initial_speed, "ui_state", ui):
        for metric in (True, False):
          with self.subTest(metric=metric):
            params.put_bool("IsMetric", metric, block=True)
            params.put("InitialCruiseSpeedKph", 80, block=True)
            ui.is_metric, ui.initial_cruise_speed = metric, 80
            with patch.object(gui_app, 'font', return_value=rl.Font()):
              control = initial_speed.InitialCruiseSpeedControl()
            row = item("Initial Max Speed", control)
            pane = self.panel([row])
            pane.title = "Toggles"
            pane.panel._update_toggles = refresh
            refresh()
            before = params.get("InitialCruiseSpeedKph")
            displayed = pane.snapshot().rows[0]
            self.assertTrue(displayed.available)
            self.assertTrue(displayed.value.endswith("km/h" if metric else "mph"))
            self.assertTrue(displayed.presets)
            self.click(pane)
            self.assertTrue(pane.snapshot().editor)
            self.choose(pane, "+")
            self.assertGreater(params.get("InitialCruiseSpeedKph"), before)
            editor = pane.snapshot()
            minimum, maximum = editor.rows[0].choices[0], editor.rows[0].choices[-1]
            self.choose(pane, minimum)
            self.assertEqual(params.get("InitialCruiseSpeedKph"), 40)
            self.choose(pane, "-")
            self.assertEqual(params.get("InitialCruiseSpeedKph"), 40)
            self.choose(pane, maximum)
            self.assertEqual(params.get("InitialCruiseSpeedKph"), 145)
            self.choose(pane, "+")
            self.assertEqual(params.get("InitialCruiseSpeedKph"), 145)

            # Native callback rejects an input tied to an earlier displayed value.
            stale = pane.snapshot().rows[0]
            params.put("InitialCruiseSpeedKph", 100, block=True)
            pane._emit(FeatureUiAction("change", stale, -1))
            self.assertEqual(params.get("InitialCruiseSpeedKph"), 100)
            current = pane.snapshot().rows[0]
            control.set_enabled(False)
            pane._emit(FeatureUiAction("change", current, -1))
            self.assertEqual(params.get("InitialCruiseSpeedKph"), 100)
            control.set_enabled(True)
            current = pane.snapshot().rows[0]
            ui.CP.pcmCruise = True
            pane._emit(FeatureUiAction("change", current, -1))
            self.assertEqual(params.get("InitialCruiseSpeedKph"), 100)
            self.assertEqual(pane.snapshot().rows, ())
            ui.CP.pcmCruise = False
            refresh()

            # Done uses the existing touch router and keeps the parent panel alive.
            self.choose(pane, "-")
            state = pane.snapshot()
            x, y, width, height = value_done_rect(state)
            pos = MousePos(x + width / 2, y + height / 2)
            pane._handle_mouse_press(pos)
            pane._handle_mouse_release(pos)
            self.assertFalse(pane.snapshot().editor)
            self.assertTrue(pane.active)
            pane.hide_event()

  def panel(self, items):
    owner = NS(_scroller=NS(_items=items), show_event=Mock(), hide_event=Mock(), _update_state=Mock())
    pane = StarPilotSettingsAdapterLarge(owner, fake_fonts(), "Device", Mock())
    pane.show_event()
    return pane

  def click(self, pane, x=1930, index=0):
    pos = MousePos(x + pane.origin[0], FEATURE_ROW_TOP + (index + .5) * FEATURE_ROW_HEIGHT + pane.origin[1])
    pane._handle_mouse_press(pos)
    pane._handle_mouse_release(pos)

  def choose(self, pane, value):
    state = pane.snapshot()
    _, (x, y, width, height), _ = next(button for button in value_buttons(state, state.rows[0], feature_row_top(state)) if button[0] == value)
    pos = MousePos(x + width / 2 + pane.origin[0], y + height / 2 + pane.origin[1])
    pane._handle_mouse_press(pos)
    pane._handle_mouse_release(pos)

  def test_native_render_forwards_current_drag(self):
    pane = self.panel([item(str(index), button_action()) for index in range(7)])
    pane._handle_mouse_press(MousePos(1000, 200))
    pane._handle_mouse_event(MouseEvent(MousePos(920, 200), 0, False, False, True, 1))
    self.assertEqual(pane.input.drag_x, -80)
    with patch.object(pane.view, "render") as render, patch("openpilot.starpilot.ui.starpilot_settings_adapter_large.placed_at"):
      pane._render(rl.Rectangle(0, 0, 2160, 1080))
      render.assert_called_once_with(pane.snapshot(), -80)
      pane.input.cancel()
      pane._render(rl.Rectangle(0, 0, 2160, 1080))
      self.assertEqual(render.call_args.args[1], 0)

  def test_native_panels_forward_feedback_and_clear_it_when_hidden_or_disabled(self):
    pane = self.panel([item("Update", button_action())])
    state = pane.snapshot()
    pos = MousePos(1930, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT / 2)
    pane._handle_mouse_press(pos)
    self.assertEqual(pane.view.pressed_action(state), FeatureUiAction("action", state.rows[0], 0))
    pane.set_enabled(False)
    self.assertIsNone(pane.view.pressed_action(state))
    pane.hide_event()
    pane.show_event()
    self.assertIsNone(pane.view.pressed_action(pane.snapshot()))

  def test_press_during_transition_finishes_it_without_activating_replaced_rows(self):
    rows = [item(str(index), button_action()) for index in range(7)]
    pane = self.panel(rows)
    start = pane.snapshot()
    pane.view._page_frame(start, 0, False)
    pane._emit(FeatureUiAction("scroll"))
    with patch("openpilot.starpilot.ui.feature_settings.time.monotonic", return_value=0):
      pane.view._page_frame(pane.snapshot(), 0, False)
      self.click(pane)
      rows[5].callback.assert_not_called()
      self.assertIsNone(pane.view._transition)
      self.click(pane)
      rows[5].callback.assert_not_called()
      pane.view._page_frame(pane.snapshot(), 0, False)
      self.click(pane)
      rows[5].callback.assert_called_once()
      # Footer navigation remains usable during the transition.
      pane.view._page_frame(start, 0, False)
      pos = MousePos(1720, 1015)
      pane._handle_mouse_press(pos)
      pane._handle_mouse_release(pos)
      self.assertEqual(pane.scroll, 5)
    pane.hide_event()
    self.assertIsNone(pane.view._last_state)
    pane.show_event()
    self.assertIsNone(pane.view._last_state)

  def test_every_row_reachable_and_page_clamps_when_visibility_changes(self):
    items = [item(str(index), button_action()) for index in range(12)]
    pane = self.panel(items)
    reached = []
    for _page in range(3):
      state = pane.snapshot()
      reached.extend(row.label for row in state.rows[state.scroll:state.scroll + 5])
      pane._emit(FeatureUiAction("scroll"))
    self.assertEqual(reached, [str(index) for index in range(12)])
    self.assertEqual(pane.scroll, 10)
    for row in items[5:]:
      row.is_visible = False
    self.assertEqual(pane.snapshot().scroll, 0)
    pane.show_event()
    self.assertEqual(pane.scroll, 0)
    self.assertFalse(hasattr(pane, "scroll_panel"))

  def test_swipes_started_during_transition_preserve_recognition_and_suppress_row_actions(self):
    for direction in (1, -1):
      for distance, duration, accepted in ((200, .5, True), (80, .075, True), (80, .5, False)):
        with self.subTest(direction=direction, distance=distance, duration=duration):
          rows = [item(str(index), button_action()) for index in range(15)]
          pane = self.panel(rows)
          pane.scroll = 0 if direction == 1 else 10
          with patch("openpilot.starpilot.ui.feature_settings.time.monotonic", return_value=0):
            pane.view._page_frame(pane.snapshot(), 0, False)
            pane.scroll = 5
            pane.view._page_frame(pane.snapshot(), 0, False)
            self.assertIsNotNone(pane.view._transition)
            x = 1930 if direction == 1 else 1800
            pane._handle_mouse_press(MousePos(x, 200))
            for timestamp, fraction in ((0, 0), (duration / 2, .5), (duration, 1)):
              pane._handle_mouse_event(MouseEvent(MousePos(x - direction * distance * fraction, 200),
                                                 0, False, False, True, timestamp))
            pane._handle_mouse_release(MousePos(x - direction * distance, 200))
          self.assertEqual(pane.scroll, 5 + direction * 5 if accepted else 5)
          self.assertTrue(all(not row.callback.called for row in rows))

  def test_buttons_cancel_on_disabled_hidden_changed_page_and_drag(self):
    row = item("Update", button_action())
    pane = self.panel([row, *[item(str(index), button_action()) for index in range(6)]])
    pos = MousePos(1930, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT / 2)
    for change in (lambda: setattr(row.action_item, "enabled", False), lambda: setattr(row, "is_visible", False),
                   lambda: setattr(pane, "scroll", 5)):
      pane._handle_mouse_press(pos)
      change()
      pane._handle_mouse_release(pos)
      row.callback.assert_not_called()
      row.action_item.enabled, row.is_visible, pane.scroll = True, True, 0
    pane._handle_mouse_press(pos)
    pane._handle_mouse_event(MouseEvent(MousePos(1930, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT / 2 + 40), 0, False, False, True, 1))
    pane._handle_mouse_release(pos)
    row.callback.assert_not_called()
    self.click(pane)
    row.callback.assert_called_once_with()

  def test_footer_and_actions_use_the_rendered_origin_in_both_sidebar_modes(self):
    rows = [item(str(index), button_action()) for index in range(7)]
    pane = self.panel(rows)
    for width, left in ((1560, 550), (2060, 50)):
      with patch.object(Widget, "render"):
        pane.render(rl.Rectangle(left + 40, 55, width, 1030))
      pos = MousePos(1720 + pane.origin[0], 1015 + pane.origin[1])
      pane._handle_mouse_press(pos)
      pane._handle_mouse_release(pos)
      self.assertEqual(pane.scroll, 5)
      self.click(pane)
      pos = MousePos(900 + pane.origin[0], 1015 + pane.origin[1])
      pane._handle_mouse_press(pos)
      pane._handle_mouse_release(pos)
      self.assertEqual(pane.scroll, 0)
    self.assertEqual(rows[5].callback.call_count, 2)

  def test_widget_dispatch_keeps_pagination_and_buttons_clickable_without_scrolling(self):
    rows = [item(str(index), button_action()) for index in range(7)]
    pane = self.panel(rows)
    bounds = rl.Rectangle(550, 25, 1560, 1030)
    with (patch.object(pane, '_render'), patch.object(gui_app, '_show_touches', False),
          patch('openpilot.system.ui.widgets.device', NS(awake=True)),
          patch.object(rl, 'get_mouse_wheel_move', return_value=-10) as wheel):
      for x, y in ((1720, 1015), (1930, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT / 2), (900, 1015)):
        for pressed, released in ((True, False), (False, True)):
          event = MouseEvent(MousePos(x, y), 0, pressed, released, pressed, 1)
          with patch.object(gui_app, '_mouse_events', [event]):
            pane.render(bounds)
      rows[5].callback.assert_called_once_with()
      self.assertEqual(pane.scroll, 0)
      wheel.assert_not_called()

  def test_widget_swipes_use_rendered_origin_and_never_activate_rows(self):
    for width, left in ((1560, 550), (2060, 50)):
      with self.subTest(width=width):
        rows = [item(str(index), button_action()) for index in range(13)]
        rows[5].action_item.enabled = False
        pane = self.panel(rows)
        bounds = rl.Rectangle(left + 40, 55, width, 1030)
        with (patch.object(pane, '_render'), patch.object(gui_app, '_show_touches', False),
              patch('openpilot.system.ui.widgets.device', NS(awake=True)),
              patch.object(gui_app, '_mouse_events', []), patch.object(gui_app, 'push_widget') as dialog):
          pane.render(bounds)
          for start, end, index, expected in ((2000, 1700, 0, 5), (2000, 1700, 0, 10), (2000, 1700, 4, 10),
                                            (1800, 2100, 4, 5), (1800, 2100, 0, 0), (1800, 2100, 0, 0)):
            y = FEATURE_ROW_TOP + (index + .5) * FEATURE_ROW_HEIGHT + pane.origin[1]
            for x, pressed, released in ((start, True, False), (end, False, False), (end, False, True)):
              event = MouseEvent(MousePos(x + pane.origin[0], y), 0, pressed, released, not released, 1)
              with patch.object(gui_app, '_mouse_events', [event]):
                pane.render(bounds)
            self.assertEqual(pane.scroll, expected)
          dialog.assert_not_called()
        for row in rows:
          row.callback.assert_not_called()

  def test_batched_flicks_use_event_times_in_both_sidebar_modes(self):
    for width, left in ((1560, 550), (2060, 50)):
      rows = [item(str(index), button_action()) for index in range(13)]
      pane = self.panel(rows)
      bounds = rl.Rectangle(left + 40, 55, width, 1030)
      with (patch.object(pane, '_render'), patch.object(gui_app, '_show_touches', False),
            patch('openpilot.system.ui.widgets.device', NS(awake=True)), patch.object(gui_app, '_mouse_events', [])):
        pane.render(bounds)
        for duration, last_down, expected in ((.075, 80, 5), (.5, 80, 5), (.075, 55, 5)):
          events = [MouseEvent(MousePos(2000 - distance + pane.origin[0], 200 + pane.origin[1]), 0,
                               index == 0, index == 3, index != 3, 1 + timestamp)
                    for index, (timestamp, distance) in enumerate(((0, 0), (duration / 2, last_down / 2),
                                                                  (duration, last_down), (duration + .01, 80)))]
          with patch.object(gui_app, '_mouse_events', events):
            pane.render(bounds)
          self.assertEqual(pane.scroll, expected)
      for row in rows:
        row.callback.assert_not_called()

  def test_widget_swipes_cross_panel_boundaries_without_activating_rows(self):
    for width, left in ((1560, 550), (2060, 50)):
      outside = 400 if width == 1560 else 10
      edge = 545 if width == 1560 else 45
      cases = ((900, (outside,), outside, 0, 5),
               (900, (), outside, 0, 5),  # release outside without a move
               (900, (outside,), 600, 0, 5),  # reenter on release
               (1900, (2145,), 2145, 5, 0),
               (edge + 15, (edge - 35,), edge - 35, 0, 0),  # short drift
               (outside, (900,), 900, 0, 0),  # first page
               (outside, (600, 900), 900, 5, 0 if width == 1560 else 5),
               (outside, (), 900, 5, 0 if width == 1560 else 5),  # enter on release
               (outside, (900,), outside, 5, 5),  # finish in sidebar
               (outside, (900,), 2145, 5, 5),  # finish beyond panel
               (edge - 26, (edge + 15, edge + 54), edge + 54, 5, 0 if width == 1560 else 5),  # fast entry flick
               (edge + 60, (edge + 25, edge - 10, edge - 45), edge - 45, 0, 5))  # fast flick
      for start, moves, end, initial, expected in cases:
        with self.subTest(width=width, start=start, end=end, moves=moves):
          rows = [item(str(index), button_action()) for index in range(7)]
          pane = self.panel(rows)
          pane.scroll = initial
          with (patch.object(pane, '_render'), patch.object(gui_app, '_show_touches', False),
                patch('openpilot.system.ui.widgets.device', NS(awake=True)),
                patch.object(gui_app, '_mouse_events', []), patch.object(gui_app, 'push_widget') as dialog,
                patch.object(pane.view, 'reset', wraps=pane.view.reset) as reset):
            pane.render(rl.Rectangle(left + 40, 55, width, 1030))
            positions = (start, *moves, end)
            for index, x in enumerate(positions):
              released = index == len(positions) - 1
              event = MouseEvent(MousePos(x + pane.origin[0], 200 + pane.origin[1]),
                                 0, index == 0, released, not released, 1 + index * .025)
              with patch.object(gui_app, '_mouse_events', [event]):
                pane.render()
            self.assertEqual(pane.scroll, expected)
            self.assertIsNone(pane.input.held)
            if start < (520 if width == 1560 else 20):
              reset.assert_not_called()
            dialog.assert_not_called()
          for row in rows:
            row.callback.assert_not_called()

  def test_sidebar_swipes_respect_parent_clipping_and_widget_input_gates(self):
    for clipped, valid, enabled, awake, slot in ((True, True, True, True, 0), (False, False, True, True, 0),
                                                (False, True, False, True, 0), (False, True, True, False, 0),
                                                (False, True, True, True, 1)):
      with self.subTest(clipped=clipped, valid=valid, enabled=enabled, awake=awake, slot=slot):
        rows = [item(str(index), button_action()) for index in range(7)]
        pane = self.panel(rows)
        pane.scroll = 5
        with (patch.object(pane, '_render'), patch.object(gui_app, '_show_touches', False),
              patch('openpilot.system.ui.widgets.device', NS(awake=awake)), patch.object(gui_app, '_mouse_events', [])):
          pane.render(rl.Rectangle(550, 25, 1560, 1030))
          if clipped:
            pane.set_parent_rect(pane.rect)
          pane.set_enabled(enabled)
          pane.set_touch_valid_callback(lambda valid=valid: valid)
          events = [MouseEvent(MousePos(x, 200), slot, i == 0, i == 2, i != 2, 1 + i * .025)
                    for i, x in enumerate((400, 900, 900))]
          with patch.object(gui_app, '_mouse_events', events):
            pane.render()
          self.assertEqual(pane.scroll, 5)
          self.assertIsNone(pane.input.held)
        for row in rows:
          row.callback.assert_not_called()

  def test_widget_release_does_not_restore_a_disabled_tap_or_repeat_an_action(self):
    row = item("Update", button_action())
    pane = self.panel([row])
    pos = MousePos(1930, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT / 2)
    with (patch.object(pane, '_render'), patch.object(gui_app, '_show_touches', False),
          patch('openpilot.system.ui.widgets.device', NS(awake=True)), patch.object(gui_app, '_mouse_events', [])):
      pane.render(rl.Rectangle(550, 25, 1560, 1030))
      with patch.object(gui_app, '_mouse_events', [MouseEvent(pos, 0, True, False, True, 1)]):
        pane.render()
      pane.set_enabled(False)
      pane.render()
      pane.set_enabled(True)
      with patch.object(gui_app, '_mouse_events', [MouseEvent(pos, 0, False, True, False, 2)]):
        pane.render()
      row.callback.assert_not_called()
      self.assertIsNone(pane.input.held)
      for pressed, released in ((True, False), (False, True), (False, True)):
        with patch.object(gui_app, '_mouse_events', [MouseEvent(pos, 0, pressed, released, pressed, 3)]):
          pane.render()
      row.callback.assert_called_once_with()

  def test_toggle_calls_its_native_callback_once_and_respects_displayed_value(self):
    action = ToggleAction(callback=Mock())
    row = item("Automatic updates", action)
    row.callback = None
    pane = self.panel([row])
    pane._handle_mouse_press(MousePos(2000, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT / 2))
    action.set_state(True)
    pane._handle_mouse_release(MousePos(2000, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT / 2))
    action.toggle._callback.assert_not_called()
    self.click(pane, 2000)
    action.toggle._callback.assert_called_once_with(False)

  def test_power_buttons_remain_distinct_and_hidden_shutdown_is_not_exposed(self):
    with patch('openpilot.system.ui.lib.application.gui_app.font', return_value=rl.Font()):
      action = DualButtonAction("Reboot", "Power Off", left_callback=Mock(), right_callback=Mock())
    pane = self.panel([item("", action)])
    self.click(pane, 1500)
    self.click(pane)
    action.left_button._click_callback.assert_called_once_with()
    action.right_button._click_callback.assert_called_once_with()
    action.right_button.set_visible(False)
    self.assertEqual(pane.snapshot().rows[0].actions, (("Reboot", True),))

  def test_choices_reuse_feature_controls_and_cancel_when_stale_disabled_or_inactive(self):
    with patch('openpilot.system.ui.lib.application.gui_app.font', return_value=rl.Font()):
      action = MultipleButtonAction(["Default", "Metered", "Unmetered"], 255, callback=Mock())
    pane = self.panel([item("Metered", action)])
    self.assertEqual(pane.snapshot().rows[0].choices, tuple(action.buttons))
    pos = MousePos(2000, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT / 2)
    pane._handle_mouse_press(pos)
    action.selected_button = 1
    pane._handle_mouse_release(pos)
    action.callback.assert_not_called()
    self.click(pane, 2000)
    self.assertTrue(pane.snapshot().editor)
    self.choose(pane, 'Unmetered')
    action.callback.assert_called_once_with(2)
    self.choose(pane, 'Metered')
    self.assertEqual(action.callback.call_args.args, (1,))
    action.callback.reset_mock()
    action.set_enabled(False)
    self.choose(pane, 'Unmetered')
    action.set_enabled(True)
    pane.hide_event()
    self.click(pane, 2000)
    action.callback.assert_not_called()

  def test_developer_ssh_preserves_native_fetcher_loading_and_add_remove_actions(self):
    # Execute the actual SSH control with its Params/network dependencies isolated.
    source = Path('openpilot/selfdrive/ui/widgets/ssh_key.py')
    nodes: list[ast.stmt] = [node for node in ast.parse(source.read_text()).body
                             if isinstance(node, ast.ClassDef) and node.name in ('SshKeyActionState', 'SshKeyAction')]
    params = NS(get=lambda _: '', remove=Mock())
    module = ModuleType('openpilot.selfdrive.ui.widgets.ssh_key')
    module.__dict__.update(Enum=Enum, ItemAction=ItemAction, Params=lambda: params, SshKeyFetcher=lambda _: Mock(),
                           Keyboard=lambda **_: Mock(), gui_app=gui_app, Button=Button, ButtonStyle=ButtonStyle,
                           FontWeight=NS(NORMAL=0), Callable=Callable, DialogResult=DialogResult, rl=rl, MousePos=MousePos, tr=lambda value: value,
                           tr_noop=lambda value: value, BUTTON_BORDER_RADIUS=50, BUTTON_FONT_SIZE=35)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), module.__dict__)
    with patch.dict(sys.modules, {module.__name__: module}), patch.object(gui_app, 'font', return_value=rl.Font()):
      action = module.SshKeyAction()
      pane = self.panel([item('SSH Keys', action)])
      pane.title = 'Developer'
      self.assertEqual(pane.snapshot().rows[0].actions, (('ADD', True),))
      with patch.object(gui_app, 'push_widget') as pushed:
        self.click(pane)
        pushed.assert_called_once_with(action._keyboard)
      action._state = module.SshKeyActionState.LOADING
      self.assertEqual(pane.snapshot().rows[0].actions, (('LOADING', False),))
      self.click(pane)
      action._fetcher.clear.assert_not_called()
      pane._update_state()
      action._fetcher.update.assert_called_once()
      action._state, action._username = module.SshKeyActionState.REMOVE, 'my-user'
      self.assertEqual(pane.snapshot().rows[0].value, 'my-user')
      self.click(pane)
      action._fetcher.clear.assert_called_once()

  def test_toggles_keep_native_fresh_state_request_guards(self):
    action = ToggleAction(callback=Mock())
    row = item('Enable openpilot', action)
    with patch.object(gui_app, 'font', return_value=rl.Font()):
      personality = MultipleButtonAction(['Aggressive', 'Standard', 'Relaxed'], 255, selected_index=1, callback=Mock())
    pane = self.panel([row, item('Driving Personality', personality)])
    pane.title = 'Toggles'
    pane.panel._toggles = {'OpenpilotEnabledToggle': row}
    pane.panel.request_toggle, pane.panel.request_personality = Mock(return_value=False), Mock(return_value=False)
    self.click(pane, 2000)
    pane.panel.request_toggle.assert_called_once_with('OpenpilotEnabledToggle', True)
    action.toggle._callback.assert_not_called()
    self.assertFalse(action.get_state())
    self.click(pane, 2000, index=1)
    self.choose(pane, 'Relaxed')
    pane.panel.request_personality.assert_called_once_with(2)
    personality.callback.assert_not_called()
    self.assertEqual(personality.selected_button, 1)

  def test_toggles_saved_state_matches_display_after_changes(self):
    import tempfile
    from openpilot.common.params import Params
    from openpilot.starpilot.galaxy.localization_settings import LocalizationSettingsOwner

    # Run the real controller methods with only device services isolated.
    source = Path('openpilot/selfdrive/ui/layouts/settings/toggles.py')
    layout = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.ClassDef) and node.name == 'TogglesLayout')
    methods = {'_update_toggles', '_update_state', '_toggle_callback',
               'request_toggle', 'request_personality', '_set_longitudinal_personality'}
    layout.bases = []
    layout.body = [node for node in layout.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    ui = NS(CP=None, engaged=False, parked=True, update_params=Mock(), sm=NS(updated={'selfdriveState': False}))
    app = NS(push_widget=Mock())
    namespace = ModuleType('toggles_controller_test')
    namespace.__dict__.update(tr=lambda text: text, ui_state=ui)
    exec(compile(ast.Module(body=[layout], type_ignores=[]), str(source), 'exec'), namespace.__dict__)
    owner = namespace.TogglesLayout()
    class SavedParams(Params):
      def __getitem__(self, key):
        return self.get(key)

    saved = SavedParams(self.enterContext(tempfile.TemporaryDirectory()))
    for key in ('IsLdwEnabled', 'ExperimentalMode', 'IsRhdDetected', 'IsDriverViewEnabled', 'IsMetric'):
      saved.put_bool(key, False, block=True)
    saved.put('LongitudinalPersonality', 1, block=True)
    owner._params = saved
    owner._localization = LocalizationSettingsOwner(saved, lambda: True, lambda: ui.parked)
    toggle = item('Lane Departure Warnings', ToggleAction())
    experimental = item('Experimental Mode', ToggleAction())
    rhd = item('Right Hand Driving', ToggleAction())
    experimental.set_description = Mock()
    with patch.object(gui_app, 'font', return_value=rl.Font()):
      personality = item('Driving Personality', MultipleButtonAction(['Aggressive', 'Standard', 'Relaxed'], 255, selected_index=1))
    owner._toggles = {'IsLdwEnabled': toggle, 'ExperimentalMode': experimental, 'IsRhdDetected': rhd}
    owner._toggle_defs = dict.fromkeys(owner._toggles, ('', '', '', False))
    owner._locked_toggles = set()
    owner._slc_offsets = NS(_raw=lambda key: b'0')
    owner._update_experimental_mode_icon = Mock()
    owner._long_personality_setting = personality
    owner._initial_cruise_speed = NS(action_item=NS(refresh=Mock()))
    owner._scroller = NS(_items=[toggle, personality, experimental, rhd])
    pane = self.panel(owner._scroller._items)
    pane.panel, pane.title = owner, 'Toggles'
    for desired in (True, False):
      self.click(pane, 2000)
      pane._update_state()
      self.assertEqual(saved['IsLdwEnabled'], desired)
      self.assertEqual(pane.snapshot().rows[0].value, 'On' if desired else 'Off')
    self.click(pane, 2000, index=1)
    for expected in (2, 0, 1):
      self.choose(pane, personality.action_item.buttons[expected])
      pane._update_state()
      self.assertEqual(saved['LongitudinalPersonality'], expected)
      self.assertEqual(pane.snapshot().rows[0].value, personality.action_item.buttons[expected])
    pane._emit(FeatureUiAction('back'))
    owner._toggle_defs['IsLdwEnabled'] = ('', '', '', True)
    ui.engaged, ui.parked = True, False
    self.click(pane, 2000)
    self.assertFalse(saved['IsLdwEnabled'])
    self.assertEqual(pane.snapshot().rows[0].value, 'Off')
    for desired in (True, False):
      self.click(pane, 2000, index=2)
      pane._update_state()
      self.assertEqual(saved['ExperimentalMode'], desired)
      self.assertEqual(pane.snapshot().rows[2].value, 'On' if desired else 'Off')
    app.push_widget.assert_not_called()

  def test_native_choice_editor_preserves_position_done_and_touch_guards(self):
    with patch.object(gui_app, 'font', return_value=rl.Font()):
      action = MultipleButtonAction(['Default', 'Metered', 'Unmetered'], 255, callback=Mock())
    pane = self.panel([*(item(str(i), button_action()) for i in range(5)), item('Wi-Fi Network Metered', action)])
    pane.scroll = 5
    self.click(pane)
    state = pane.snapshot()
    row = state.rows[0]
    self.assertTrue(state.editor)
    self.assertEqual(state.parent_title, 'Device')
    _, (x, y, width, height), _ = next(button for button in value_buttons(state, row, feature_row_top(state)) if button[0] == 'Unmetered')
    pos = MousePos(x + width / 2, y + height / 2)
    pane._handle_mouse_press(pos)
    action.set_selected_button(1)
    pane._handle_mouse_release(pos)
    action.callback.assert_not_called()
    state = pane.snapshot()
    x, y, width, height = value_done_rect(state)
    done = MousePos(x + width / 2, y + height / 2)
    pane._handle_mouse_press(done)
    pane._handle_mouse_release(done)
    self.assertFalse(pane.snapshot().editor)
    self.assertEqual(pane.scroll, 5)
    pane.on_back.assert_not_called()

  def network_panel(self):
    manager = Mock(spec=WifiManager, is_connection_saved=lambda ssid: ssid == "Saved", connected_ssid="Saved",
                 wifi_state=NS(ssid="Saved"), set_active=Mock(), process_callbacks=Mock(), activate_connection=Mock(),
                 connect_to_network=Mock(), forget_connection=Mock())
    wifi = WifiManagerUI.__new__(WifiManagerUI)
    Widget.__init__(wifi)
    wifi._wifi_manager, wifi._action_guard = manager, None
    wifi.state, wifi._state_network = UIState.IDLE, None
    wifi._networks = [Network("Saved", 90, SecurityType.OPEN, False), Network("Open", 50, SecurityType.OPEN, False),
                      Network("Locked", 40, SecurityType.WPA, False)]
    owner = NetworkUI.__new__(NetworkUI)
    Widget.__init__(owner)
    owner._wifi_panel, owner._advanced_panel = wifi, Mock()
    owner._advanced_panel._scroller = NS(_items=[])
    owner._current_panel, owner._action_guard = PanelType.WIFI, None
    owner._children = [wifi]
    pane = StarPilotSettingsAdapterLarge(owner, fake_fonts(), "Network", Mock())
    allowed = [True]
    bridge = NetworkPanelBridge(owner, lambda: allowed[0])
    bridge.presentation = pane
    self.assertTrue(bridge.enter())
    return pane, bridge, wifi, manager, allowed

  def test_network_scan_connect_password_forget_and_authority_use_existing_owner(self):
    pane, bridge, wifi, manager, allowed = self.network_panel()
    manager.set_active.assert_called_once_with(True)
    self.assertIsNone(FeatureInput.target(1500, FEATURE_ROW_TOP + FEATURE_ROW_HEIGHT * 1.5, pane.snapshot()))  # connected network: Connect disabled
    self.click(pane, index=1)
    self.assertEqual(wifi.state, UIState.SHOW_FORGET_CONFIRM)
    wifi.state, wifi._state_network = UIState.IDLE, None
    self.click(pane, index=2)
    manager.connect_to_network.assert_called_once_with('Open', '')
    wifi.state, wifi._state_network = UIState.IDLE, None
    self.click(pane, index=3)
    self.assertEqual(wifi.state, UIState.NEEDS_AUTH)
    allowed[0] = False
    wifi.forget_network(wifi._networks[0])
    manager.forget_connection.assert_not_called()
    bridge.leave()
    manager.set_active.assert_called_with(False)
    self.assertFalse(pane.active)

  def test_network_tabs_reset_pages_and_back_returns_to_wifi(self):
    pane, bridge, wifi, manager, allowed = self.network_panel()
    self.click(pane)
    self.assertEqual(pane.panel._current_panel, PanelType.ADVANCED)
    pane.scroll = 5
    pane._emit(FeatureUiAction("back"))
    self.assertEqual(pane.panel._current_panel, PanelType.WIFI)
    self.assertEqual(pane.scroll, 0)
    pane.on_back.assert_not_called()
    pane._emit(FeatureUiAction("back"))
    pane.on_back.assert_called_once_with()
    bridge.leave()


if __name__ == '__main__':
  unittest.main()
