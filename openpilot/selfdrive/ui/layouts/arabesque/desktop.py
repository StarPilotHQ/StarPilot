from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import subprocess
from threading import Event, Thread
from zoneinfo import ZoneInfo

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.hardware import PC
from openpilot.common.time_helpers import MIN_DATE, MAX_DATE
from openpilot.selfdrive.ui.layouts.arabesque.theme import DEFAULT_THEME
from openpilot.selfdrive.ui.layouts.arabesque.tools.browser import Browser
from openpilot.selfdrive.ui.layouts.arabesque.tools.clock import ClockEditor
from openpilot.selfdrive.ui.layouts.arabesque.tools.files import Files
from openpilot.selfdrive.ui.layouts.arabesque.tools.rebecca import Rebecca
from openpilot.selfdrive.ui.layouts.arabesque.tools.terminal import Terminal
from openpilot.system.ui.lib.application import gui_app, MouseEvent, MousePos, TextAlignment
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import gui_label


_DOCK_SHADOW = rl.Color(0, 0, 0, 52)
_DOCK_FACE = rl.Color(72, 54, 98, 255)
_DOCK_RIM = rl.Color(113, 88, 147, 255)


class ArabesqueLayout(Widget):
  def __init__(self, on_exit: Callable[[], None], permitted: Callable[[], bool] = lambda: True):
    super().__init__()
    self._on_exit = on_exit
    self._permitted = permitted
    self._timezone = ZoneInfo('America/Chicago')
    self._menu_open = False
    self._pressed_rect: rl.Rectangle | None = None
    self._active_tool: Widget | None = None
    self._active_name = ""
    self._time_sync_thread: Thread | None = None
    self._time_sync_cancelled = Event()
    self._time_sync_success = False
    self._clock_open = self._clock_prompted = False

  def show_event(self):
    super().show_event()
    self._clock_prompted = False
    if PC or not self._permitted() or self._time_sync_thread is not None and self._time_sync_thread.is_alive():
      return
    self._time_sync_cancelled = Event()
    self._time_sync_thread = Thread(target=self._sync_time, args=(self._time_sync_cancelled,), daemon=True)
    self._time_sync_thread.start()

  def _sync_time(self, cancelled: Event, net_time: datetime | None = None):
    try:
      if net_time is None:
        # shortcut: HTTP recovery time is unauthenticated, replace when an authenticated bootstrap source is available.
        headers = subprocess.run(
          ['curl', '-fsI', '-m', '10', '-H', 'Cache-Control: no-cache', 'http://www.google.com'],
          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True, timeout=11,
        ).stdout
        date = next((line.partition(':')[2].strip() for line in headers.splitlines() if line.lower().startswith('date:')), '')
        net_time = parsedate_to_datetime(date)
      if net_time.tzinfo is None or not MIN_DATE < net_time.astimezone(UTC).replace(tzinfo=None) < MAX_DATE or cancelled.is_set():
        return
      subprocess.run(['sudo', '-n', 'date', '-u', '-s', net_time.isoformat()],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=2)
      self._time_sync_success = True
    except (OSError, ValueError, TypeError, OverflowError, subprocess.SubprocessError):
      pass

  def _set_clock(self, text: str):
    editor = self._active_tool
    if not isinstance(editor, ClockEditor) or editor.saving or PC or not self._permitted():
      return
    if self._time_sync_thread is not None and self._time_sync_thread.is_alive():
      editor.error = tr('The automatic check is still running. Try again in a moment.')
      return
    if self._time_sync_success and not editor.time_edited:
      self._timezone = ZoneInfo(editor.timezone)
      self._close_tool()
      return
    try:
      local = datetime.strptime(text, '%Y-%m-%d %H:%M')
      zone = ZoneInfo(editor.timezone)
      value = local.replace(tzinfo=zone, fold=editor.fold)
      if value.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local:
        editor.error = tr('That time does not exist in this time zone.')
        return
      if not MIN_DATE < value.astimezone(UTC).replace(tzinfo=None) < MAX_DATE:
        raise ValueError
    except (ValueError, OverflowError, OSError):
      editor.error = tr('Choose a supported date and time.')
      return
    editor.saving = True
    self._clock_prompted = True
    self._time_sync_success = False
    self._time_sync_cancelled = Event()
    self._time_sync_thread = Thread(target=self._sync_time, args=(self._time_sync_cancelled, value), daemon=True)
    self._time_sync_thread.start()

  def _clock_needs_attention(self) -> bool:
    now = datetime.now()
    # This device's July 2026 reset date passes the normal date bounds.
    return not MIN_DATE < now < MAX_DATE or not self._time_sync_success and (
      (now.year, now.month, now.day) == (2026, 7, 28) or
      not PC and self._time_sync_thread is not None and not self._time_sync_thread.is_alive())

  def _update_layout_rects(self):
    rect = self._rect
    # GuiApplication scales both drawing and input from these logical coordinates.
    self._scale = s = min(rect.width / 2160, rect.height / 1080)
    self._bar = rl.Rectangle(rect.x, rect.y, rect.width, 104 * s)
    self._menu_button = rl.Rectangle(rect.x + 24 * s, rect.y + 8 * s, 88 * s, 88 * s)
    self._exit_button = rl.Rectangle(rect.x + 24 * s, rect.y + 116 * s, 456 * s, 116 * s)
    self._clock_button = rl.Rectangle(rect.x + rect.width - 424 * s, rect.y + 8 * s, 400 * s, 88 * s)
    self._clock_label = rl.Rectangle(self._clock_button.x + 16 * s, self._bar.y, self._clock_button.width - 32 * s, self._bar.height)
    self._back_button = rl.Rectangle(self._clock_button.x - 296 * s, rect.y + 8 * s, 272 * s, 88 * s)
    self._clock_menu = rl.Rectangle(rect.x + rect.width - 728 * s, rect.y + 116 * s, 704 * s, 344 * s)
    self._clock_buttons = tuple((rl.Rectangle(self._clock_menu.x + (24 + index * 224) * s, self._clock_menu.y + 232 * s,
                                             208 * s, 88 * s), action) for index, action in enumerate(('retry', 'manual', 'later')))
    self._dock = dock = rl.Rectangle(rect.x + (rect.width - 1080 * s) / 2, rect.y + rect.height - 272 * s, 1080 * s, 240 * s)
    self._dock_shelf = rl.Rectangle(dock.x, dock.y + 178 * s, dock.width, 10 * s)
    self._dock_top = (rl.Vector2(dock.x + 36 * s, dock.y + 118 * s), rl.Vector2(dock.x, self._dock_shelf.y),
                      rl.Vector2(dock.x + dock.width, self._dock_shelf.y), rl.Vector2(dock.x + dock.width - 36 * s, dock.y + 118 * s))
    self._dock_shadows = (rl.Rectangle(dock.x - 10 * s, dock.y + 174 * s, dock.width + 20 * s, 32 * s),
                          rl.Rectangle(dock.x - 4 * s, dock.y + 178 * s, dock.width + 8 * s, 22 * s))
    self._launchers = (
      (rl.Rectangle(dock.x + 60 * s, dock.y, 240 * s, 200 * s), "Terminal", lambda: Terminal()),
      (rl.Rectangle(dock.x + 300 * s, dock.y, 240 * s, 200 * s), "Files", lambda: Files(self._permitted)),
      (rl.Rectangle(dock.x + 540 * s, dock.y, 240 * s, 200 * s), "Rebecca", lambda: Rebecca()),
      (rl.Rectangle(dock.x + 780 * s, dock.y, 240 * s, 200 * s), "Firefox", lambda: Browser(self._permitted)),
    )
    self._tool_rect = rl.Rectangle(rect.x, self._bar.y + self._bar.height, rect.width, rect.height - self._bar.height)

  def _open_tool(self, name: str, tool: Widget):
    self._close_tool()
    self._active_tool, self._active_name = tool, name
    tool.set_rect(self._tool_rect)
    tool.show_event()

  def _close_tool(self):
    tool, self._active_tool = self._active_tool, None
    self._active_name = ""
    self._menu_open = False
    self._clock_open = False
    self._pressed_rect = None
    gui_app.mouse_events.clear()
    # Transient tools have one explicit lifecycle owner, rather than persistent _child registration.
    if tool is not None:
      tool.hide_event()

  def hide_event(self):
    # MainLayout also hides this view when offroad access is revoked.
    self._time_sync_cancelled.set()
    super().hide_event()
    self._close_tool()

  def poll_runtime(self, awake: bool):
    if self._time_sync_cancelled.is_set() and self._time_sync_thread is not None and not self._time_sync_thread.is_alive():
      prompted = self._clock_prompted
      self.show_event()
      self._clock_prompted = prompted
    if self._time_sync_thread is not None and not self._time_sync_thread.is_alive() and not self._time_sync_cancelled.is_set():
      if isinstance(self._active_tool, ClockEditor) and self._active_tool.saving:
        if self._time_sync_success:
          self._timezone = ZoneInfo(self._active_tool.timezone)
          self._close_tool()
        else:
          self._active_tool.saving = False
          self._active_tool.error = tr('Could not set the time. Please try again.')
    checking = self._time_sync_thread is not None and self._time_sync_thread.is_alive()
    if not checking and not PC and not self._clock_prompted and awake and self.enabled and self._permitted() and self._clock_needs_attention():
      self._clock_open = self._clock_prompted = True
      self._menu_open = False
      self._pressed_rect = None
      gui_app.mouse_events.clear()
    if isinstance(self._active_tool, Browser):
      self._active_tool.poll_runtime(awake)

  def _handle_mouse_press(self, pos: MousePos):
    self._pressed_rect = None
    if rl.check_collision_point_rec(pos, self._menu_button):
      self._pressed_rect = self._menu_button
    elif rl.check_collision_point_rec(pos, self._clock_button):
      self._pressed_rect = self._clock_button
    elif self._clock_open:
      self._pressed_rect = next((target for target, _ in self._clock_buttons if rl.check_collision_point_rec(pos, target)), None)
    elif self._menu_open:
      if rl.check_collision_point_rec(pos, self._exit_button):
        self._pressed_rect = self._exit_button
    elif self._active_tool is not None and rl.check_collision_point_rec(pos, self._back_button):
      self._pressed_rect = self._back_button
    elif self._active_tool is None:
      self._pressed_rect = next((target for target, _, _ in self._launchers if rl.check_collision_point_rec(pos, target)), None)

  def _handle_mouse_release(self, pos: MousePos):
    pressed = self._pressed_rect
    self._pressed_rect = None
    if pressed is self._menu_button and rl.check_collision_point_rec(pos, self._menu_button):
      self._menu_open = not self._menu_open
      self._clock_open = False
    elif pressed is self._clock_button and rl.check_collision_point_rec(pos, self._clock_button):
      self._clock_open = not self._clock_open
      self._clock_prompted = True
      self._menu_open = False
      gui_app.mouse_events.clear()
    elif self._clock_open:
      self._clock_open = False
      self._clock_prompted = True
      gui_app.mouse_events.clear()
      for target, action in self._clock_buttons:
        if pressed is target and rl.check_collision_point_rec(pos, target):
          if action == 'retry':
            self.show_event()
          elif action == 'manual':
            self._open_tool(tr('Date & Time'), ClockEditor(self._set_clock, self._close_tool, self._timezone))
          break
    elif self._menu_open:
      self._menu_open = False
      self._clock_open = False
      if pressed is self._exit_button and rl.check_collision_point_rec(pos, self._exit_button):
        self._on_exit()
    elif pressed is self._back_button and rl.check_collision_point_rec(pos, self._back_button):
      self._close_tool()
    elif self._active_tool is None:
      for target, name, create in self._launchers:
        if pressed is target and rl.check_collision_point_rec(pos, target):
          self._open_tool(tr(name), create())
          break

  def _handle_mouse_event(self, mouse_event: MouseEvent):
    if mouse_event.left_released and not rl.check_collision_point_rec(mouse_event.pos, self._rect):
      self._pressed_rect = None
      self._menu_open = False
      self._clock_open = False

  def _render(self, rect: rl.Rectangle):
    s, theme = self._scale, DEFAULT_THEME
    rl.draw_rectangle_rec(rect, theme.background)
    if self._active_tool is not None:
      # Widget handles input after drawing: gate the whole frame before the tool sees chrome gestures.
      tool_enabled = self.enabled and not self._menu_open and not self._clock_open and self._pressed_rect is None and not any(
        event.slot == 0 and not rl.check_collision_point_rec(event.pos, self._tool_rect) for event in gui_app.mouse_events)
      self._active_tool.set_enabled(tool_enabled)
      rl.begin_scissor_mode(int(self._tool_rect.x), int(self._tool_rect.y), int(self._tool_rect.width), int(self._tool_rect.height))
      self._active_tool.render(self._tool_rect)
      rl.end_scissor_mode()

    rl.draw_rectangle_rec(self._bar, theme.surface)
    rl.draw_line_ex(rl.Vector2(rect.x, self._bar.y + self._bar.height),
                    rl.Vector2(rect.x + rect.width, self._bar.y + self._bar.height), 2 * s, theme.control)
    menu_pressed = self._menu_open or self._pressed_rect is self._menu_button and self.is_pressed
    rl.draw_rectangle_rounded(self._menu_button, 0.28, 12, theme.pressed if menu_pressed else theme.control)

    center = rl.Vector2(self._menu_button.x + 42 * s, self._menu_button.y + 44 * s)
    rl.draw_ring(center, 15 * s, 20 * s, 0, 360, 24, theme.background)
    rl.draw_line_ex(rl.Vector2(center.x + 18 * s, center.y - 20 * s), rl.Vector2(center.x + 18 * s, center.y + 20 * s),
                    5 * s, theme.background)
    clock_pressed = self._clock_open or self._pressed_rect is self._clock_button and self.is_pressed
    clock_attention = self._clock_needs_attention()
    clock_color = theme.pressed if clock_attention else theme.control
    if clock_pressed:
      rl.draw_rectangle_rounded(self._clock_button, 0.2, 8, clock_color)
    now = datetime.now(self._timezone)
    clock_text = f'{now:%a %b} {now.day}  ' + now.strftime('%I:%M %p').lstrip('0')
    if clock_attention:
      clock_text = tr('Set Date & Time')
    elif not PC and not self._time_sync_success:
      clock_text = tr('Syncing time…')
    gui_label(self._clock_label, clock_text, round(28 * s),
              theme.background if clock_pressed else clock_color if clock_attention else theme.text, theme.font_weight,
              alignment=TextAlignment.RIGHT)
    gui_label(rl.Rectangle(rect.x + 136 * s, rect.y, self._back_button.x - rect.x - 160 * s, self._bar.height),
              self._active_name or "Arabesque", round(40 * s), theme.text, theme.font_weight)

    if self._active_tool is not None:
      back_pressed = self._pressed_rect is self._back_button and self.is_pressed
      rl.draw_rectangle_rounded(self._back_button, 0.28, 12, theme.pressed if back_pressed else theme.control)
      gui_label(self._back_button, "< " + tr("Desktop"), round(32 * s), theme.background, theme.font_weight,
                alignment=TextAlignment.CENTER)
    else:
      # Two fixed translucent shapes soften the shelf's shadow without a blur pass or texture.
      for shadow in self._dock_shadows:
        rl.draw_rectangle_rounded(shadow, 0.9, 12, _DOCK_SHADOW)
      rl.draw_rectangle_rec(self._dock_shelf, theme.surface)
      top = self._dock_top
      rl.draw_triangle(top[0], top[1], top[2], _DOCK_FACE)
      rl.draw_triangle(top[0], top[2], top[3], _DOCK_FACE)
      rl.draw_line_ex(top[1], top[2], 2 * s, _DOCK_RIM)
      for target, name, _ in self._launchers:
        icon = rl.Vector2(target.x + target.width / 2, self._dock.y + 72 * s)
        rl.draw_ellipse(int(icon.x), int(icon.y + 68 * s), 52 * s, 9 * s, _DOCK_SHADOW)
        tint = theme.pressed if self._pressed_rect is target and self.is_pressed else theme.control
        rl.draw_circle_v(rl.Vector2(icon.x, icon.y + 5 * s), 64 * s, theme.surface)
        rl.draw_circle_v(icon, 64 * s, tint)
        rl.draw_circle_v(icon, 54 * s, theme.background)
        if name == "Terminal":
          rl.draw_line_ex(rl.Vector2(icon.x - 32 * s, icon.y - 24 * s), rl.Vector2(icon.x - 8 * s, icon.y), 6 * s, tint)
          rl.draw_line_ex(rl.Vector2(icon.x - 8 * s, icon.y), rl.Vector2(icon.x - 32 * s, icon.y + 24 * s), 6 * s, tint)
          rl.draw_line_ex(rl.Vector2(icon.x + 8 * s, icon.y + 24 * s), rl.Vector2(icon.x + 36 * s, icon.y + 24 * s), 6 * s, tint)
        elif name == "Files":
          rl.draw_rectangle_rounded(rl.Rectangle(icon.x - 32 * s, icon.y - 26 * s, 30 * s, 18 * s), 0.3, 8, tint)
          rl.draw_rectangle_rounded(rl.Rectangle(icon.x - 36 * s, icon.y - 16 * s, 72 * s, 44 * s), 0.2, 8, tint)
          rl.draw_rectangle_rounded(rl.Rectangle(icon.x - 29 * s, icon.y - 9 * s, 58 * s, 30 * s), 0.2, 8, theme.background)
        elif name == "Firefox":
          rl.draw_ring(icon, 35 * s, 40 * s, 0, 360, 40, tint)
          rl.draw_ellipse_lines(int(icon.x), int(icon.y), 16 * s, 39 * s, tint)
          rl.draw_line_ex(rl.Vector2(icon.x - 38 * s, icon.y), rl.Vector2(icon.x + 38 * s, icon.y), 4 * s, tint)
        else:
          rl.draw_circle_v(rl.Vector2(icon.x - 19 * s, icon.y - 6 * s), 20 * s, tint)
          rl.draw_circle_v(rl.Vector2(icon.x + 19 * s, icon.y - 6 * s), 20 * s, tint)
          for offset in (-19, 19):
            rl.draw_circle_v(rl.Vector2(icon.x + offset * s, icon.y - 8 * s), 11 * s, theme.background)
          rl.draw_line_ex(rl.Vector2(icon.x - 8 * s, icon.y + 25 * s), rl.Vector2(icon.x + 8 * s, icon.y + 25 * s), 3 * s, tint)
        gui_label(rl.Rectangle(icon.x - 96 * s, icon.y + 68 * s, 192 * s, 36 * s),
                  tr(name), round(28 * s), theme.text, theme.font_weight, alignment=TextAlignment.CENTER)

    if self._menu_open:
      exit_pressed = self._pressed_rect is self._exit_button and self.is_pressed
      color = theme.pressed if exit_pressed else theme.surface
      rl.draw_rectangle_rounded(self._exit_button, 0.22, 12, color)
      rl.draw_rectangle_rounded_lines_ex(self._exit_button, 0.22, 12, 2 * s, theme.control)
      gui_label(rl.Rectangle(self._exit_button.x + 28 * s, self._exit_button.y, self._exit_button.width - 56 * s, self._exit_button.height),
                tr("Exit Arabesque"), round(40 * s), theme.background if exit_pressed else theme.text, theme.font_weight)

    if self._clock_open:
      panel = self._clock_menu
      if clock_attention:
        rl.draw_rectangle_rec(self._tool_rect, rl.Color(theme.background.r, theme.background.g, theme.background.b, 176))
      rl.draw_rectangle_rounded(panel, 0.08, 12, theme.surface)
      rl.draw_rectangle_rounded_lines_ex(panel, 0.08, 12, 2 * s, clock_color)
      busy = self._time_sync_thread is not None and self._time_sync_thread.is_alive()
      status = tr('Checking online. You can also set it manually.') if busy else (
        tr('Automatic sync unavailable. Set the date and time.') if clock_attention else tr('Time updated.'))
      title = tr('Set Date & Time') if clock_attention else tr('Date & Time')
      current = tr('Device:') + ' ' + datetime.now(self._timezone).strftime('%b %d, %Y  %I:%M %p %Z')
      for offset, text, size in ((20, title, 36), (80, status, 26), (140, current, 28)):
        gui_label(rl.Rectangle(panel.x + 24 * s, panel.y + offset * s, panel.width - 48 * s, 64 * s),
                  text, round(size * s), clock_color if clock_attention and offset == 20 else theme.text, theme.font_weight)
      for (target, action), label in zip(self._clock_buttons, (tr('Retry'), tr('Set Manually'), tr('Later')), strict=True):
        pressed = self._pressed_rect is target and self.is_pressed
        primary = action == 'manual'
        face = clock_color if primary else theme.background
        if pressed:
          face = theme.control if primary and clock_attention else theme.pressed
        rl.draw_rectangle_rounded(target, 0.2, 8, face)
        gui_label(target, label, round(28 * s), theme.background if pressed or primary else theme.text,
                  theme.font_weight, alignment=TextAlignment.CENTER)
