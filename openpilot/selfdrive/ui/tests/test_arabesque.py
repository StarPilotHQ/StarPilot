"""Exercise real layout routing and Widget input with native service owners isolated."""

import ast
from dataclasses import replace
from datetime import datetime
from enum import IntEnum
import gc
from pathlib import Path
import subprocess
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
import weakref

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.selfdrive.ui.layouts.arabesque import ArabesqueLayout
from openpilot.selfdrive.ui.layouts.arabesque import desktop
from openpilot.selfdrive.ui.layouts.arabesque.theme import DEFAULT_THEME
from openpilot.selfdrive.ui.layouts.arabesque.tools import clock
from openpilot.selfdrive.ui.layouts.arabesque.tools import terminal
from openpilot.system.ui.lib.application import gui_app, FontWeight, MouseEvent, MousePos
from openpilot.system.ui.widgets import Widget

ROOT = Path(__file__).resolve().parents[2]


def layout_classes(path, names, namespace):
  # UI owners import device-native IPC; keep these tests usable on a desktop too.
  tree = ast.parse(path.read_text())
  tree.body = ast.parse('from __future__ import annotations').body + [
    node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in names
  ]
  exec(compile(ast.fix_missing_locations(tree), str(path), 'exec'), namespace)
  return NS(**namespace)


@pytest.fixture
def shell(monkeypatch):
  import openpilot.system.ui.widgets as widgets
  monkeypatch.setattr(desktop, 'PC', True)
  monkeypatch.setattr(widgets, 'device', NS(awake=True))
  monkeypatch.setattr(gui_app, '_mouse_events', [])
  monkeypatch.setattr(gui_app, '_show_touches', False)
  monkeypatch.setattr(desktop, 'gui_label', Mock())
  monkeypatch.setattr(terminal, 'gui_label', Mock())
  monkeypatch.setattr(terminal.Terminal, 'show_event', lambda self: None)
  for name in ('draw_rectangle_rec', 'draw_rectangle_rounded', 'draw_rectangle_rounded_lines_ex', 'draw_line_ex', 'draw_ring',
               'draw_circle_v', 'draw_triangle', 'draw_ellipse', 'draw_ellipse_lines', 'begin_scissor_mode', 'end_scissor_mode'):
    monkeypatch.setattr(rl, name, lambda *a: None)
  return ArabesqueLayout(Mock())


def event(x, y, *, pressed=False, released=False, slot=0):
  return MouseEvent(MousePos(x, y), slot, pressed, released, pressed, 1.0)


def frame(view, *events):
  gui_app._mouse_events = list(events)
  view.render()


def tap(view, rect):
  x, y = rect.x + rect.width / 2, rect.y + rect.height / 2
  frame(view, event(x, y, pressed=True))
  frame(view, event(x, y, released=True))


def test_menu_dismissal_tool_title_and_desktop_return(shell):
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  tap(shell, shell._menu_button)
  assert shell._menu_open
  tap(shell, shell._launchers[0][0])
  assert not shell._menu_open
  assert shell._active_tool is None
  tap(shell, shell._launchers[0][0])
  assert isinstance(shell._active_tool, terminal.Terminal)
  assert not gui_app.mouse_events
  frame(shell)
  assert desktop.gui_label.call_args_list[-1].args[1] == '< Desktop'
  assert desktop.gui_label.call_args_list[-2].args[1] == 'Terminal'
  assert any(call.args[1] == 'Opening local shell…' for call in terminal.gui_label.call_args_list)
  tap(shell, shell._back_button)
  assert shell._active_tool is None
  frame(shell)
  assert any(call.args[1] == 'Arabesque' for call in desktop.gui_label.call_args_list)
  shell._on_exit.assert_not_called()
  tap(shell, shell._menu_button)
  tap(shell, shell._exit_button)
  shell._on_exit.assert_called_once()


def test_drag_stray_release_second_finger_and_hidden_press_do_not_activate(shell):
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  tap(shell, shell._menu_button)
  frame(shell, event(900, 400, pressed=True), event(100, 170, released=True))
  assert not shell._menu_open
  frame(shell, event(60, 50, pressed=True, slot=1), event(60, 50, released=True, slot=1))
  assert not shell._menu_open
  frame(shell, event(60, 50, released=True))
  assert not shell._menu_open
  tap(shell, shell._menu_button)
  frame(shell, event(100, 170, pressed=True))
  shell.hide_event()
  frame(shell, event(100, 170, released=True))
  shell._on_exit.assert_not_called()


@pytest.mark.parametrize('bounds', [(0, 0, 2160, 1080), (0, 0, 1920, 1080), (45, 30, 1280, 720), (0, 0, 2560, 1080)])
def test_layout_anchors_and_touch_targets(shell, bounds):
  shell.set_rect(rl.Rectangle(*bounds))
  rect, dock = shell.rect, shell._dock
  assert dock.x + dock.width / 2 == pytest.approx(rect.x + rect.width / 2)
  assert dock.y + dock.height < rect.y + rect.height
  assert dock.width >= 44 and dock.height >= 44
  frame(shell)
  captions = [call.args[0] for call in desktop.gui_label.call_args_list if call.args[1] in ('Terminal', 'Files', 'Rebecca', 'Firefox')]
  assert len(captions) == 4
  for (target, _, _), caption in zip(shell._launchers, captions, strict=True):
    icon_x, icon_y = target.x + target.width / 2, dock.y + 72 * shell._scale
    assert caption.x + caption.width / 2 == pytest.approx(icon_x)
    assert caption.y >= icon_y + 64 * shell._scale
    assert caption.y + caption.height < shell._dock_shelf.y
    assert min(target.width, target.height) >= 44
  back_left, front_left, front_right, back_right = shell._dock_top
  assert back_left.x > front_left.x and back_right.x < front_right.x
  assert back_left.y < dock.y + 136 * shell._scale < front_left.y
  assert front_left.y == pytest.approx(shell._dock_shelf.y)
  assert front_right.y == pytest.approx(shell._dock_shelf.y)
  assert shell._dock_shelf.y + shell._dock_shelf.height < dock.y + dock.height
  assert shell._bar.width == rect.width
  assert shell._exit_button.y > shell._bar.y + shell._bar.height
  assert min(shell._menu_button.width, shell._menu_button.height) >= 44
  assert min(shell._back_button.width, shell._back_button.height) >= 44
  assert shell._back_button.x + shell._back_button.width < shell._clock_button.x
  assert shell._clock_button.x + shell._clock_button.width <= rect.x + rect.width
  assert min(shell._clock_button.width, shell._clock_button.height) >= 44
  assert shell._clock_label.x + shell._clock_label.width == pytest.approx(rect.x + rect.width - 40 * shell._scale)
  assert shell._clock_label.y + shell._clock_label.height / 2 == pytest.approx(shell._bar.y + shell._bar.height / 2)
  for target, _ in shell._clock_buttons:
    assert min(target.width, target.height) >= 44
    assert target.x >= shell._clock_menu.x
    assert target.x + target.width <= shell._clock_menu.x + shell._clock_menu.width
  assert shell._tool_rect.y == shell._bar.y + shell._bar.height
  assert shell._tool_rect.x == rect.x
  assert shell._tool_rect.width == rect.width
  assert shell._tool_rect.y + shell._tool_rect.height == pytest.approx(rect.y + rect.height)
  tap(shell, shell._menu_button)
  assert shell._menu_open
  tap(shell, shell._launchers[0][0])
  tap(shell, shell._launchers[0][0])
  frame(shell)
  assert shell._active_tool.rect == shell._tool_rect
  label = desktop.gui_label.call_args
  assert label.args[0] is shell._back_button
  assert label.args[1] == '< Desktop'
  assert label.kwargs['alignment'] == desktop.TextAlignment.CENTER


def test_tool_theme_override_does_not_change_desktop_or_defaults(shell, monkeypatch):
  draw = Mock()
  monkeypatch.setattr(rl, 'draw_rectangle_rec', draw)
  custom = replace(DEFAULT_THEME, background=rl.Color(25, 31, 37, 255), text=rl.Color(240, 230, 220, 255), font_weight=FontWeight.BOLD)
  assert terminal.Terminal()._theme is DEFAULT_THEME
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  shell._open_tool('Terminal', terminal.Terminal(theme=custom))
  frame(shell)
  assert draw.call_args_list[1].args[1] is custom.background
  assert any(call.args[3] is custom.text and call.args[4] is FontWeight.BOLD for call in terminal.gui_label.call_args_list)
  assert desktop.gui_label.call_args_list[0].args[3] is DEFAULT_THEME.text
  assert DEFAULT_THEME.background.r == 12


@pytest.fixture
def probe():
  class Probe(Widget):
    def __init__(self):
      super().__init__()
      self.show_event = Mock()
      self.hide_event = Mock()
      self._handle_mouse_press = Mock()
      self._handle_mouse_release = Mock()
      self._update_state = Mock()
      self.render_calls = Mock()

    def _render(self, rect, /):
      self.render_calls(rect)
  return Probe()


def test_dock_is_hidden_for_tools_and_returns_on_desktop(shell, probe, monkeypatch):
  triangles = Mock()
  monkeypatch.setattr(rl, 'draw_triangle', triangles)
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  frame(shell)
  assert any(call.args[0] is shell._dock_top[0] for call in triangles.call_args_list)

  shell._open_tool('Probe', probe)
  triangles.reset_mock()
  frame(shell)
  assert not any(call.args[0] is shell._dock_top[0] for call in triangles.call_args_list)
  assert probe.rect == shell._tool_rect

  # The bottom edge now belongs to the tool, including the former dock area.
  x, y = shell.rect.x + shell.rect.width / 2, shell.rect.y + shell.rect.height - 1
  frame(shell, event(x, y, pressed=True))
  frame(shell, event(x, y, released=True))
  probe._handle_mouse_press.assert_called_once()
  probe._handle_mouse_release.assert_called_once()

  tap(shell, shell._back_button)
  triangles.reset_mock()
  frame(shell)
  assert shell._active_tool is None
  assert any(call.args[0] is shell._dock_top[0] for call in triangles.call_args_list)


def test_tool_input_is_gated_for_menu_chrome_and_disabled_desktop(shell, probe):
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  shell._open_tool('Probe', probe)
  tap(shell, shell._tool_rect)
  probe._handle_mouse_press.assert_called_once()
  probe._handle_mouse_release.assert_called_once()
  probe._handle_mouse_press.reset_mock()
  probe._handle_mouse_release.reset_mock()

  # A whole queued gesture after the menu tap must not reach content in the same frame.
  frame(shell, event(60, 50, pressed=True), event(60, 50, released=True),
        event(1000, 500, pressed=True), event(1000, 500, released=True))
  assert not probe.enabled
  probe._handle_mouse_press.assert_not_called()
  probe._handle_mouse_release.assert_not_called()
  tap(shell, shell._menu_button)
  assert shell._menu_open
  tap(shell, shell._tool_rect)
  assert not shell._menu_open
  probe._handle_mouse_press.assert_not_called()
  probe._handle_mouse_release.assert_not_called()
  shell.set_enabled(False)
  tap(shell, shell._tool_rect)
  assert not probe.enabled
  probe._handle_mouse_press.assert_not_called()
  shell.set_enabled(True)
  tap(shell, shell._tool_rect)
  probe._handle_mouse_press.assert_called_once()
  probe._handle_mouse_release.assert_called_once()


def test_tool_lifecycle_runs_once_and_closed_tool_is_not_rendered(shell, probe, monkeypatch):
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  create = Mock(return_value=probe)
  monkeypatch.setattr(desktop, 'Terminal', create)
  frame(shell)
  create.assert_not_called()
  tap(shell, shell._launchers[0][0])
  probe.show_event.assert_called_once()
  tap(shell, shell._launchers[0][0])
  create.assert_called_once()
  probe._handle_mouse_press.assert_called_once()
  probe._handle_mouse_release.assert_called_once()
  probe._handle_mouse_press.reset_mock()
  probe._handle_mouse_release.reset_mock()
  x, y = shell._back_button.x + 40, shell._back_button.y + 40
  frame(shell, event(x, y, pressed=True))
  frame(shell, event(x, y, released=True), event(1000, 500, pressed=True), event(1000, 500, released=True))
  assert not gui_app.mouse_events
  probe._handle_mouse_press.assert_not_called()
  probe._handle_mouse_release.assert_not_called()
  probe.hide_event.assert_called_once()
  probe.render_calls.reset_mock()
  probe._update_state.reset_mock()
  shell.hide_event()
  for _ in range(3):
    frame(shell)
  probe.hide_event.assert_called_once()
  probe.render_calls.assert_not_called()
  probe._update_state.assert_not_called()


def test_closed_tool_instances_are_released(shell):
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  instances = []
  for _ in range(100):
    tap(shell, shell._launchers[0][0])
    instances.append(weakref.ref(shell._active_tool))
    tap(shell, shell._back_button)
  gc.collect()
  assert all(instance() is None for instance in instances)


def test_files_has_its_own_launcher_and_blank_shelf_does_not_launch(shell, probe, monkeypatch):
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  create = Mock(return_value=probe)
  monkeypatch.setattr(desktop, 'Files', create)
  terminal_target, files_target = (item[0] for item in shell._launchers[:2])
  # A gesture crossing launcher boundaries must not start either tool.
  frame(shell, event(terminal_target.x + 20, terminal_target.y + 20, pressed=True))
  frame(shell, event(files_target.x + 20, files_target.y + 20, released=True))
  assert shell._active_tool is None
  tap(shell, rl.Rectangle(shell._dock.x + 2, shell._dock.y + 210, 10, 10))
  assert shell._active_tool is None
  tap(shell, files_target)
  assert shell._active_tool is probe
  create.assert_called_once_with(shell._permitted)
  frame(shell)
  assert desktop.gui_label.call_args_list[-2].args[1] == 'Files'
  tap(shell, shell._back_button)
  probe.hide_event.assert_called_once()
  assert shell._active_tool is None


def test_rebecca_launcher_uses_shared_lifecycle(shell, probe, monkeypatch):
  create = Mock(return_value=probe)
  monkeypatch.setattr(desktop, 'Rebecca', create)
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  tap(shell, shell._launchers[2][0])
  assert shell._active_tool is probe and not gui_app.mouse_events
  create.assert_called_once_with()
  probe.show_event.assert_called_once()
  frame(shell)
  assert desktop.gui_label.call_args_list[-2].args[1] == 'Rebecca'
  tap(shell, shell._back_button)
  probe.hide_event.assert_called_once()


def test_rebecca_menu_pauses_and_dismissal_does_not_flap(shell, monkeypatch):
  game = desktop.Rebecca()
  monkeypatch.setattr(game, 'show_event', Mock())
  monkeypatch.setattr(game, '_render', Mock())
  monkeypatch.setattr(rl, 'get_frame_time', lambda: 1 / 60)
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  shell._open_tool('Rebecca', game)
  game._reset()
  frame(shell)
  tap(shell, shell._menu_button)
  frozen = game._distance, game._y, game._vy
  frame(shell)
  tap(shell, game._button)
  assert not shell._menu_open
  assert (game._distance, game._y, game._vy) == frozen
  frame(shell)
  assert game._distance > frozen[0]


@pytest.fixture
def main(shell, monkeypatch):
  ui = NS(started=False, is_body=False, add_on_body_changed_callbacks=Mock())
  ui.is_offroad = lambda: not ui.started
  sidebar = Mock(is_visible=False)
  sidebar.set_visible.side_effect = lambda visible: setattr(sidebar, 'is_visible', visible)
  settings = Mock()
  namespace = {'rl': rl, 'IntEnum': IntEnum, 'Widget': Widget, 'gui_app': gui_app, 'ui_state': ui,
               'ArabesqueLayout': ArabesqueLayout, 'Sidebar': lambda: sidebar,
               'SIDEBAR_WIDTH': 300, 'HomeLayout': Mock, 'BodyLayout': Mock, 'SettingsLayout': Mock(return_value=settings),
               'AugmentedRoadView': Mock, 'messaging': NS(PubMaster=Mock()),
               'device': NS(add_interactive_timeout_callback=Mock()), 'OnboardingWindow': lambda: NS(completed=True),
               'PanelType': NS(TOGGLES=2, FIREHOSE=4)}
  native = layout_classes(ROOT / 'ui/layouts/main.py', {'MainState', 'MainLayout'}, namespace)
  monkeypatch.setattr(gui_app, 'push_widget', lambda widget: None)
  layout = native.MainLayout()
  layout.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  layout._set_current_layout(native.MainState.SETTINGS)
  return NS(layout=layout, state=native.MainState, ui=ui, settings=settings, native=native)


def test_entry_wiring_and_offroad_gate(main):
  m = main
  callbacks = m.native.SettingsLayout.call_args.kwargs
  assert callbacks['on_open_arabesque'] == m.layout._open_arabesque
  m.ui.started = True
  callbacks['on_open_arabesque']()
  assert m.layout._current_mode == m.state.SETTINGS
  m.ui.started = False
  callbacks['on_open_arabesque']()
  assert m.layout._current_mode == m.state.ARABESQUE
  frame(m.layout)
  m.settings.render.assert_not_called()


def test_handoff_discards_queued_touches_and_restores_settings(main, probe):
  m = main
  gui_app._mouse_events = [event(60, 50, pressed=True), event(60, 50, released=True)]
  m.layout._open_arabesque()
  assert not gui_app.mouse_events
  view = m.layout._layouts[m.state.ARABESQUE]
  frame(m.layout)
  view._open_tool('Probe', probe)
  tap(m.layout, view._menu_button)
  frame(m.layout, event(100, 170, pressed=True))
  frame(m.layout, event(100, 170, released=True), event(1000, 500, pressed=True), event(1000, 500, released=True))
  assert m.layout._current_mode == m.state.SETTINGS
  assert not gui_app.mouse_events
  m.settings.render.assert_not_called()
  assert not view._menu_open
  assert view._active_tool is None
  probe.hide_event.assert_called_once()
  frame(m.layout)
  m.settings.render.assert_called_once()


def test_onroad_transition_draws_normal_alert_owner_immediately(main, probe):
  m = main
  m.layout._open_arabesque()
  view = m.layout._layouts[m.state.ARABESQUE]
  frame(m.layout)
  view._open_tool('Probe', probe)
  tap(m.layout, view._menu_button)
  m.ui.started = True
  frame(m.layout, event(100, 170, pressed=True))
  assert m.layout._current_mode == m.state.ONROAD
  assert not view._menu_open
  assert view._active_tool is None
  probe.hide_event.assert_called_once()
  assert not gui_app.mouse_events
  m.layout._layouts[m.state.ONROAD].render.assert_called_once()
  m.ui.started = False
  frame(m.layout)
  assert m.layout._current_mode == m.state.HOME


def test_shutdown_closes_active_tool_once(main, probe):
  main.layout._open_arabesque()
  view = main.layout._layouts[main.state.ARABESQUE]
  frame(main.layout)
  view._open_tool('Probe', probe)
  main.layout.close()
  main.layout.close()
  assert view._active_tool is None
  probe.hide_event.assert_called_once()


def test_authority_revocation_closes_tool_without_rendering(main, probe):
  main.layout._open_arabesque()
  view = main.layout._layouts[main.state.ARABESQUE]
  frame(main.layout)
  view._open_tool('Probe', probe)
  main.ui.started = True
  main.layout.poll_arabesque()
  assert main.layout._current_mode == main.state.ONROAD
  assert view._active_tool is None
  assert view._time_sync_cancelled.is_set()
  probe.hide_event.assert_called_once()


def test_covering_widget_pauses_arabesque_runtime_without_rendering(main, monkeypatch):
  main.layout._open_arabesque()
  view = main.layout._layouts[main.state.ARABESQUE]
  view.poll_runtime = Mock()
  main.native.device.awake = True
  monkeypatch.setattr(gui_app, 'get_active_widget', lambda: object())
  main.layout.poll_arabesque()
  view.poll_runtime.assert_called_once_with(False)
  monkeypatch.setattr(gui_app, 'get_active_widget', lambda: main.layout)
  main.layout.poll_arabesque()
  view.poll_runtime.assert_called_with(True)


def test_repeated_entry_exit_reuses_view_and_never_runs_it_closed(main):
  m = main
  view = m.layout._layouts[m.state.ARABESQUE]
  for _ in range(100):
    m.layout._open_arabesque()
    frame(m.layout)
    tap(m.layout, view._menu_button)
    tap(m.layout, view._exit_button)
    assert m.layout._current_mode == m.state.SETTINGS
    assert m.layout._layouts[m.state.ARABESQUE] is view
  view._render = Mock()
  frame(m.layout)
  view._render.assert_not_called()


def test_star_large_routes_shell_and_uses_confirmed_offroad(main, probe):
  namespace = vars(main.native).copy()
  namespace['Destination'] = NS(NETWORK='network')
  namespace['ShellMode'] = NS(HOME='home', SETTINGS='settings', ONROAD='onroad')
  star = layout_classes(ROOT.parent / 'starpilot/ui/runtime_app.py', {'StarMainLayout'}, namespace)
  main.layout.__class__ = star.StarMainLayout
  layout = main.layout
  layout.star, layout.page = Mock(), Mock()
  layout.star.selected = 'developer'
  layout._large_destination = None
  layout._network_bridge = Mock()
  layout.star.adapter.confirmed_offroad.return_value = False
  layout._open_arabesque()
  assert layout._current_mode == main.state.SETTINGS
  layout.star.adapter.confirmed_offroad.return_value = True
  layout._open_arabesque()
  frame(layout)
  view = layout._layouts[main.state.ARABESQUE]
  view._open_tool('Probe', probe)
  layout.page.render.assert_not_called()
  layout.star.adapter.confirmed_offroad.return_value = False
  frame(layout)
  assert layout._current_mode == main.state.HOME
  layout.page.render.assert_called_once()
  probe.hide_event.assert_called_once()
  assert view._active_tool is None
  layout.star.adapter.confirmed_offroad.return_value = True
  layout._set_current_layout(main.state.SETTINGS)
  layout._open_arabesque()
  view._open_tool('Probe', probe)
  layout._native_onroad = layout._layouts[main.state.ONROAD]
  layout.close()
  assert probe.hide_event.call_count == 2
  assert view._active_tool is None
  layout.star.close.assert_called_once()
  layout._native_onroad.close.assert_called_once()


def test_time_sync_startup_cancellation_and_reentry(shell, monkeypatch):
  thread, run = Mock(), Mock(return_value=NS(stdout='204||Mon, 02 Nov 2026 18:00:00 GMT'))
  monkeypatch.setattr(desktop, 'Thread', thread)
  monkeypatch.setattr(desktop.subprocess, 'run', run)
  shell.show_event()
  thread.assert_not_called()
  monkeypatch.setattr(desktop, 'PC', False)
  shell._permitted = Mock(return_value=False)
  shell.show_event()
  thread.assert_not_called()
  shell._permitted.return_value = True
  shell.show_event()
  run.assert_not_called()
  assert thread.call_args.kwargs['daemon']
  thread.return_value.start.assert_called_once()
  shell.show_event()
  thread.assert_called_once()
  shell.hide_event()
  shell.show_event()
  shell.poll_runtime(True)
  thread.assert_called_once()
  thread.call_args.kwargs['target'](*thread.call_args.kwargs['args'])
  assert run.call_count == 1  # Closing cancels the correction after the request returns.
  thread.return_value.is_alive.return_value = False
  shell._clock_prompted = True
  shell._permitted.return_value = False
  shell.poll_runtime(True)
  thread.assert_called_once()
  shell._permitted.return_value = True
  shell.poll_runtime(True)
  assert thread.call_count == 2
  assert shell._clock_prompted
  thread.call_args.kwargs['target'](*thread.call_args.kwargs['args'])
  assert run.call_count == 3
  fetch, apply = run.call_args_list[-2:]
  assert fetch.args[0] == ['curl', '-q', '-fsI', '-m', '10', '-H', 'Cache-Control: no-cache', '-o', '/dev/null',
                          '-w', '%{response_code}|%header{age}|%header{date}', 'https://www.google.com/generate_204']
  assert fetch.kwargs['timeout'] == 11
  assert apply.args[0] == ['sudo', '-n', 'date', '-u', '-s', '2026-11-02T18:00:00+00:00']
  assert apply.kwargs['timeout'] == 2
  assert apply.kwargs['stdout'] == apply.kwargs['stderr'] == subprocess.DEVNULL
  shell.poll_runtime(True)
  assert thread.call_count == 2


@pytest.mark.parametrize('response', [
  '', '204||', '204||invalid', '204||Sat, 01 Jan 2000 00:00:00 GMT', '204||Tue, 01 Jan 2036 00:00:00 GMT',
  '204||Mon, 02 Nov 2026 18:00:00', '200||Fri, 02 Oct 2026 18:00:00 GMT', '302||Fri, 02 Oct 2026 18:00:00 GMT',
  '204|2678400|Fri, 02 Oct 2026 18:00:00 GMT',
  subprocess.CalledProcessError(22, ['curl'], output='204||Mon, 02 Nov 2026 18:00:00 GMT'),
  subprocess.CalledProcessError(28, ['curl'], output='204||Mon, 02 Nov 2026 18:00:00 GMT'),
  subprocess.CalledProcessError(60, ['curl']),
  subprocess.TimeoutExpired(['curl'], 11), FileNotFoundError('curl'),
])
def test_failed_or_invalid_time_sync_never_sets_clock(shell, monkeypatch, response):
  run = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=NS(stdout=response))
  monkeypatch.setattr(desktop.subprocess, 'run', run)
  shell._sync_time(Event())
  assert run.call_count == 1


@pytest.mark.parametrize('error', [subprocess.CalledProcessError(1, ['sudo']), subprocess.TimeoutExpired(['sudo'], 2), FileNotFoundError('sudo')])
def test_time_sync_permission_or_command_failures_stay_quiet(shell, monkeypatch, error):
  run = Mock(side_effect=[NS(stdout='204|0|Mon, 02 Nov 2026 18:00:00 GMT'), error])
  monkeypatch.setattr(desktop.subprocess, 'run', run)
  shell._sync_time(Event())
  assert run.call_count == 2


@pytest.mark.parametrize('now', [datetime(1970, 1, 1), datetime(2026, 7, 28, 16, 43)])
@pytest.mark.parametrize('success', [False, True])
def test_clock_prompts_only_after_sync_fails(shell, monkeypatch, now, success):
  current = Mock(now=Mock(return_value=now))
  monkeypatch.setattr(desktop, 'datetime', current)
  monkeypatch.setattr(desktop, 'PC', False)
  shell._time_sync_thread = Mock(is_alive=Mock(return_value=True))
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  shell.poll_runtime(True)
  assert not shell._clock_open
  shell._time_sync_thread.is_alive.return_value = False
  shell._time_sync_success = success
  if success:
    current.now.return_value = datetime(2026, 11, 2, 16, 11)
  shell.poll_runtime(False)
  assert not shell._clock_open
  shell.poll_runtime(True)
  assert shell._clock_open is not success
  frame(shell)
  label = next(call for call in desktop.gui_label.call_args_list if call.args[0] is shell._clock_label)
  assert label.args[1] == ('Mon Nov 2  4:11 PM' if success else 'Set Date & Time')
  assert label.kwargs['alignment'] == desktop.TextAlignment.RIGHT
  if not success:
    tap(shell, shell._clock_buttons[2][0])
    shell.poll_runtime(True)
    assert not shell._clock_open


def test_clock_menu_blocks_tool_input(shell, probe):
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  shell._open_tool('Probe', probe)
  tap(shell, shell._clock_button)
  assert shell._clock_open and not shell._menu_open
  frame(shell, event(1000, 600, pressed=True), event(1000, 600, released=True))
  probe._handle_mouse_press.assert_not_called()
  probe._handle_mouse_release.assert_not_called()
  assert not shell._clock_open


@pytest.fixture
def clock_editor(shell, monkeypatch):
  monkeypatch.setattr(desktop, 'PC', False)
  monkeypatch.setattr(desktop, 'Thread', Mock())
  monkeypatch.setattr(desktop.subprocess, 'run', Mock())
  monkeypatch.setattr(clock, 'gui_label', Mock())
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: 0)
  monkeypatch.setattr(rl, 'get_char_pressed', lambda: 0)
  monkeypatch.setattr(rl, 'get_mouse_wheel_move', lambda: 0)
  monkeypatch.setattr(rl, 'get_frame_time', lambda: 1 / 60)
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  tap(shell, shell._clock_button)
  tap(shell, shell._clock_buttons[1][0])
  assert isinstance(shell._active_tool, desktop.ClockEditor)
  return shell._active_tool


def swipe_clock_wheel(shell, editor, index, amount):
  wheel = editor._wheels[index]
  x, y = wheel.x + wheel.width / 2, wheel.y + wheel.height / 2
  frame(shell, event(x, y, pressed=True))
  frame(shell, event(x, y - amount * editor._row_height))
  frame(shell, event(x, y - amount * editor._row_height, released=True))


def test_manual_save_validates_and_sets_clock_without_network(shell, clock_editor):
  for text in ('invalid', '1970-01-01 00:00', '2026-02-30 12:00', '2036-01-01 00:00'):
    shell._set_clock(text)
  desktop.Thread.assert_not_called()
  clock_editor._values[:6] = [11, 2, 2026, 1, 5, 1]
  tap(shell, clock_editor._save_button)
  assert clock_editor.saving
  shell._set_clock('2026-11-02 13:05')
  desktop.Thread.assert_called_once()
  desktop.subprocess.run.assert_not_called()
  desktop.Thread.call_args.kwargs['target'](*desktop.Thread.call_args.kwargs['args'])
  desktop.subprocess.run.assert_called_once()
  assert desktop.subprocess.run.call_args.args[0] == ['sudo', '-n', 'date', '-u', '-s', '2026-11-02T13:05:00-06:00']
  desktop.Thread.return_value.is_alive.return_value = False
  shell.poll_runtime(True)
  assert shell._active_tool is None and shell._time_sync_success


@pytest.mark.parametrize('failure', ['permission', 'command', 'closed'])
def test_manual_save_failure_and_cancellation(shell, clock_editor, failure):
  if failure == 'permission':
    shell._permitted = lambda: False
  elif failure == 'command':
    desktop.subprocess.run.side_effect = subprocess.CalledProcessError(1, ['sudo'])
  shell._set_clock('2026-11-02 13:05')
  if failure == 'permission':
    desktop.Thread.assert_not_called()
  else:
    if failure == 'closed':
      shell.hide_event()
    desktop.Thread.call_args.kwargs['target'](*desktop.Thread.call_args.kwargs['args'])
    if failure == 'closed':
      desktop.subprocess.run.assert_not_called()
      assert shell._active_tool is None
    else:
      desktop.Thread.return_value.is_alive.return_value = False
      shell.poll_runtime(True)
      assert shell._active_tool is clock_editor and not clock_editor.saving
      assert 'Could not set' in clock_editor.error
      shell.poll_runtime(True)
      assert not shell._clock_open


def test_manual_time_remains_known_when_reentering_offline(shell, clock_editor, monkeypatch):
  monkeypatch.setattr(desktop, 'datetime', Mock(now=Mock(return_value=datetime(2026, 11, 2, 12))))
  shell._time_sync_success = True
  shell.hide_event()
  shell.show_event()
  desktop.subprocess.run.side_effect = subprocess.CalledProcessError(28, ['curl'])
  desktop.Thread.call_args.kwargs['target'](*desktop.Thread.call_args.kwargs['args'])
  desktop.Thread.return_value.is_alive.return_value = False
  shell.poll_runtime(True)
  assert shell._time_sync_success and not shell._clock_open


@pytest.mark.parametrize('year,expected_day', [(2027, 28), (2028, 29)])
def test_date_wheels_keep_end_of_month_valid(shell, clock_editor, year, expected_day):
  clock_editor._values[:6] = [1, 31, year, 1, 5, 1]
  swipe_clock_wheel(shell, clock_editor, 0, 1)
  assert clock_editor._values[:3] == [2, expected_day, year]
  if year == 2028:
    swipe_clock_wheel(shell, clock_editor, 2, -1)
    assert clock_editor._values[:3] == [2, 28, 2027]


@pytest.mark.parametrize('hour,ampm,expected', [(12, 0, '00:05'), (12, 1, '12:05'), (1, 1, '13:05')])
def test_wheel_save_converts_am_and_pm_correctly(shell, clock_editor, hour, ampm, expected):
  clock_editor._values[:6] = [11, 2, 2026, hour, 5, ampm]
  clock_editor._submit = Mock()
  tap(shell, clock_editor._save_button)
  clock_editor._submit.assert_called_once_with(f'2026-11-02 {expected}')


def test_wheel_flick_slows_and_stops_at_a_selection(shell, clock_editor):
  clock_editor._values[:6] = [1, 15, 2026, 1, 5, 1]
  wheel = clock_editor._wheels[0]
  x, y = wheel.x + wheel.width / 2, wheel.y + wheel.height / 2
  frame(shell, MouseEvent(MousePos(x, y), 0, True, False, True, 1.0))
  frame(shell, MouseEvent(MousePos(x, y - clock_editor._row_height), 0, False, False, True, 1.04))
  frame(shell, MouseEvent(MousePos(x, y - clock_editor._row_height), 0, False, True, False, 1.05))
  assert clock_editor._values[0] == 2 and clock_editor._moving == 0
  for _ in range(60):
    frame(shell)
  assert clock_editor._values[0] != 2
  assert clock_editor._moving is None and clock_editor._offset == 0


def test_wheels_and_repeated_hour_support_keyboard(shell, clock_editor, monkeypatch):
  clock_editor._values[:6] = [11, 2, 2026, 1, 5, 1]
  clock_editor._submit = Mock()
  keys = iter((rl.KEY_TAB, rl.KEY_DOWN, rl.KEY_ENTER, 0))
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: next(keys, 0))
  monkeypatch.setattr(rl, 'is_key_down', lambda _: False)
  frame(shell)
  clock_editor._submit.assert_called_once_with('2026-11-03 13:05')
  clock_editor._values[:6] = [11, 1, 2026, 1, 30, 0]
  clock_editor.fold, clock_editor._focus = 0, 6
  keys = iter((rl.KEY_TAB, rl.KEY_ENTER, 0))
  frame(shell)
  assert clock_editor.fold == 1 and clock_editor.time_edited


@pytest.mark.parametrize('edited', [False, True])
def test_changing_timezone_preserves_the_right_time(shell, clock_editor, edited):
  shell._time_sync_success = True
  clock_editor._values[:6] = [1, 15, 2027, 1, 5, 1]
  if edited:
    swipe_clock_wheel(shell, clock_editor, 4, 1)
  swipe_clock_wheel(shell, clock_editor, 6, 1)
  assert clock_editor.timezone == 'America/New_York'
  assert clock_editor._values[3:6] == ([1, 6, 1] if edited else [2, 5, 1])
  tap(shell, clock_editor._save_button)
  if edited:
    assert desktop.Thread.call_args.kwargs['args'][1].isoformat() == '2027-01-15T13:06:00-05:00'
  else:
    desktop.Thread.assert_not_called()
    assert shell._timezone.key == 'America/New_York' and shell._active_tool is None
    tap(shell, shell._clock_button)
    tap(shell, shell._clock_buttons[1][0])
    assert shell._active_tool.timezone == 'America/New_York'
    assert ArabesqueLayout(Mock())._timezone.key == 'America/Chicago'


def test_timezone_change_preserves_the_second_occurrence(shell, clock_editor):
  clock_editor._values[:6] = [11, 1, 2026, 1, 30, 0]
  clock_editor.fold = 0
  swipe_clock_wheel(shell, clock_editor, 6, 1)
  assert clock_editor.timezone == 'America/New_York'
  assert clock_editor._values[:6] == [11, 1, 2026, 1, 30, 0] and clock_editor.fold == 1


@pytest.mark.parametrize('date,offset', [
  ('2026-01-15 13:05', '-06:00'), ('2026-07-15 13:05', '-05:00'), ('2027-03-14 02:30', None),
])
def test_local_time_uses_daylight_saving_rules(shell, clock_editor, date, offset):
  shell._set_clock(date)
  if offset is None:
    desktop.Thread.assert_not_called()
    assert 'does not exist' in clock_editor.error
  else:
    assert desktop.Thread.call_args.kwargs['args'][1].isoformat().endswith(offset)


@pytest.mark.parametrize('second,offset', [(False, '-05:00'), (True, '-06:00')])
def test_repeated_hour_can_set_either_occurrence(shell, clock_editor, second, offset):
  clock_editor._values[:6] = [11, 1, 2026, 1, 30, 0]
  clock_editor.fold = 0
  if second:
    tap(shell, clock_editor._fold_button)
    swipe_clock_wheel(shell, clock_editor, 4, 1)
    assert clock_editor.fold == 1
  tap(shell, clock_editor._save_button)
  assert desktop.Thread.call_args.kwargs['args'][1].isoformat().endswith(offset)
