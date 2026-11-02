"""A single-pane touch file manager owned by the Arabesque shell."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import math
import os
from pathlib import Path
import stat
from threading import Event

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.basedir import BASEDIR
from openpilot.common.hardware import PC
from openpilot.common.hardware.hw import Paths
from openpilot.selfdrive.ui.layouts.arabesque.theme import DEFAULT_THEME, Theme
from openpilot.selfdrive.ui.layouts.arabesque.tools.filesystem import Entry, FileSystem
from openpilot.system.loggerd.storage_roots import LEGACY_NAMES
from openpilot.system.ui.lib.application import gui_app, MousePos, TextAlignment
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.scroll_panel import DRAG_THRESHOLD, GuiScrollPanel
from openpilot.system.ui.lib.wrap_text import wrap_text
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.inputbox import InputBox
from openpilot.system.ui.widgets.keyboard import KEYBOARD_LAYOUTS, BACKSPACE_KEY, ENTER_KEY, SHIFT_ACTIVE_KEY, SHIFT_INACTIVE_KEY
from openpilot.system.ui.widgets.label import gui_label


def _display(text):
  return ''.join(char if char.isprintable() else '�' for char in str(text))


def _size(value):
  for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
    if value < 1024 or unit == 'TiB':
      return f'{value:g} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
    value /= 1024


class _NameInput(InputBox):
  def __init__(self, submit, cancel, theme):
    super().__init__()
    self._submit, self._cancel = submit, cancel
    self._theme = theme

  def _render(self, rect, color=rl.BLACK, border_color=rl.DARKGRAY, text_color=rl.WHITE, font_size=80):
    theme = self._theme
    super()._render(rect, color=theme.surface, border_color=theme.surface, text_color=theme.text, font_size=round(rect.height * 40 / 104))

  def _handle_keyboard_input(self):
    if self.enabled:
      char = rl.get_char_pressed()
      while char:
        if char >= 32:
          self.add_char_at_cursor(chr(char))
        char = rl.get_char_pressed()
      super()._handle_keyboard_input()
    else:
      self._last_key_pressed = 0
      while rl.get_key_pressed() or rl.get_char_pressed():
        pass

  def _process_key(self, key):
    if key in (rl.KEY_ENTER, rl.KEY_KP_ENTER):
      self._submit()
    elif key == rl.KEY_ESCAPE:
      self._cancel()
    else:
      super()._process_key(key)


class Files(Widget):
  def __init__(self, permitted: Callable[[], bool], theme: Theme = DEFAULT_THEME):
    super().__init__()
    self._permitted, self._theme = permitted, theme
    home = Path.home().resolve()
    data = Path(Paths.comma_home()) if PC else Path('/data')
    recordings = Path(Paths.log_root())
    self._places: tuple[tuple[str, Path], ...] = ((tr('Home'), home), (tr('Data'), data), (tr('Logs'), Path(Paths.swaglog_root())),
                    (tr('Recordings'), recordings), (tr('Software'), Path(BASEDIR)))
    protected = (Path(BASEDIR), Path(os.environ.get('PARAMS_ROOT', str(data / 'params'))), Path(Paths.persist_root()),
                 recordings, *(recordings.parent / name for name in LEGACY_NAMES))
    self._fs = FileSystem((home, data), protected, permitted)
    self._path, self._entries = home, []
    self._visible_key, self._visible_entries = None, []
    self._executor, self._future = None, None
    self._cancelled = Event()
    self._job = self._panel = self._notice = self._error = ''
    self._selected: Entry | None = None
    self._transfer = ''
    self._hidden = False
    self._pressed = None
    self._press_pos = None
    self._dragged = False
    self._buttons = []
    self._scroll, self._preview_scroll = GuiScrollPanel(), GuiScrollPanel()
    self._preview, self._preview_lines = '', None
    self._input = self._child(_NameInput(self._submit_name, self._close_panel, theme))
    self._input.set_enabled(lambda: self.enabled and self._future is None)
    self._keyboard = 'lowercase'

  def show_event(self):
    if self._executor is not None:
      return
    super().show_event()
    rl.set_exit_key(0)
    self._drain_keys()
    self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='arabesque-files')
    self._load(self._path)

  def hide_event(self):
    self._cancelled.set()
    if self._executor is not None:
      self._executor.shutdown(wait=False, cancel_futures=True)
      self._executor = None
    self._future = None
    self._panel = self._transfer = ''
    self._buttons, self._pressed, self._press_pos = [], None, None
    self._drain_keys()
    rl.set_exit_key(rl.KEY_ESCAPE)
    super().hide_event()

  @staticmethod
  def _drain_keys():
    while rl.get_key_pressed() or rl.get_char_pressed():
      pass

  def _run(self, job, function, *args, **kwargs):
    if self._executor is None or self._future is not None:
      return
    if not self._permitted():
      self._error = tr('Files is available only while parked.')
      return
    self._error, self._job = '', job
    self._cancelled = Event()
    self._future = self._executor.submit(function, *args, self._cancelled, **kwargs)
    self._pressed = None
    gui_app.mouse_events.clear()

  def _load(self, path):
    self._run('list', self._fs.listing, path)

  def _change(self, action, **kwargs):
    self._run(action, self._fs.change, action, **kwargs)

  def _update_state(self):
    if self._future is not None and self._future.done():
      future, self._future = self._future, None
      try:
        result = future.result()
        if self._job == 'list':
          self._path, self._entries = result
          self._scroll.set_offset(0)
        elif self._job == 'preview':
          self._preview, self._preview_lines = result, None
          self._preview_scroll.set_offset(0)
        else:
          self._panel = self._transfer = ''
          self._notice = tr('Done.')
          self._load(self._path)
      except (OSError, ValueError) as error:
        self._error = tr('This name already exists. Choose another name or folder.') if isinstance(error, FileExistsError) else str(error)
        if self._job == 'delete':
          self._panel = ''
          self._notice = tr('Delete stopped. Some items may already have been removed.') + ' ' + self._error
          self._load(self._path)
    if not self.enabled:
      self._pressed, self._press_pos = None, None
      self._scroll.set_offset(self._scroll.offset)
      self._preview_scroll.set_offset(self._preview_scroll.offset)
      self._drain_keys()
    elif self._panel not in ('rename', 'mkdir'):
      key = rl.get_key_pressed()
      if key == rl.KEY_ESCAPE and self._future is None:
        if self._panel:
          self._close_panel()
        elif self._transfer:
          self._transfer = ''
        else:
          self._load(self._path.parent)
      self._drain_keys()

  def _update_layout_rects(self):
    self._scale = min(self._rect.width / 2160, self._rect.height / 976)
    self._preview_lines = None

  def _close_panel(self):
    self._panel, self._error = '', ''
    self._pressed, self._press_pos = None, None
    self._input._last_key_pressed = 0
    gui_app.mouse_events.clear()

  def _submit_name(self):
    if self.enabled and self._panel in ('rename', 'mkdir'):
      self._change(self._panel, entry=self._selected, destination=self._path, name=self._input.text)

  def _activate(self, action):
    if not self.enabled or not self._permitted():
      return
    self._notice = ''
    if isinstance(action, tuple):
      kind, value = action
      if kind == 'place':
        self._close_panel()
        self._load(value)
      elif kind == 'open':
        if value.directory:
          self._load(value.path)
        else:
          self._selected, self._panel, self._preview = value, 'preview', ''
          self._run('preview', self._fs.preview, value)
      elif kind == 'actions':
        self._selected, self._panel, self._error = value, 'actions', ''
      elif kind == 'key':
        if value == ENTER_KEY:
          self._submit_name()
        elif value == BACKSPACE_KEY:
          self._input.delete_char_before_cursor()
        elif value in (SHIFT_ACTIVE_KEY, SHIFT_INACTIVE_KEY):
          self._keyboard = 'lowercase' if self._keyboard == 'uppercase' else 'uppercase'
        elif value in ('123', '#+=', 'ABC'):
          self._keyboard = {'123': 'numbers', '#+=': 'specials', 'ABC': 'lowercase'}[value]
        else:
          self._input.add_char_at_cursor(value)
          if self._keyboard == 'uppercase':
            self._keyboard = 'lowercase'
    elif action == 'cancel-job':
      self._cancelled.set()
      self._notice = tr('Cancelling...')
    elif action == 'close':
      self._close_panel()
    elif action == 'submit':
      self._submit_name()
    elif action in ('up', 'home', 'refresh'):
      self._load(self._path.parent if action == 'up' else self._places[0][1] if action == 'home' else self._path)
    elif action == 'places':
      self._panel = 'places'
    elif action == 'hidden':
      self._hidden = not self._hidden
      self._scroll.set_offset(0)
    elif action in ('rename', 'mkdir'):
      self._panel, self._keyboard, self._error = action, 'lowercase', ''
      self._input.text = self._selected.path.name if action == 'rename' else ''
    elif action in ('copy', 'move'):
      self._transfer, self._panel = action, ''
    elif action == 'cancel-transfer':
      self._transfer = ''
    elif action == 'paste':
      self._change(self._transfer, entry=self._selected, destination=self._path)
    elif action == 'delete':
      self._panel = 'delete'
    elif action == 'confirm-delete':
      self._change('delete', entry=self._selected)
    self._pressed, self._press_pos = None, None
    gui_app.mouse_events.clear()

  def _handle_mouse_press(self, pos: MousePos):
    self._pressed = next((action for rect, action in reversed(self._buttons) if rl.check_collision_point_rec(pos, rect)), None)
    self._press_pos, self._dragged = pos, False

  def _handle_mouse_event(self, mouse_event):
    if self._press_pos and math.hypot(mouse_event.pos.x - self._press_pos.x, mouse_event.pos.y - self._press_pos.y) > DRAG_THRESHOLD:
      self._dragged = True

  def _handle_mouse_release(self, pos: MousePos):
    action, self._pressed = self._pressed, None
    if not self._dragged and action is not None and any(value == action and rl.check_collision_point_rec(pos, rect)
                                                       for rect, value in self._buttons):
      self._activate(action)

  def _label(self, x, y, width, height, text, size=32, color=None, center=False):
    s, theme = self._scale, self._theme
    gui_label(rl.Rectangle(x, y, width, height), _display(text), round(size * s), color or theme.text, theme.font_weight,
              alignment=TextAlignment.CENTER if center else TextAlignment.LEFT)

  def _button(self, rect, text, action, *, enabled=True, primary=False, clip=None):
    theme = self._theme
    pressed = self.enabled and self._pressed == action and self.is_pressed
    color = theme.pressed if pressed else theme.control if primary else theme.surface
    rl.draw_rectangle_rounded(rect, 0.22, 12, color)
    self._label(rect.x, rect.y, rect.width, rect.height, tr(text), color=theme.background if primary else theme.text if enabled else theme.secondary_text,
                center=True)
    if enabled and (self._future is None or action == 'cancel-job'):
      hit = rl.get_collision_rec(rect, clip) if clip is not None else rect
      if hit.width > 0 and hit.height > 0:
        self._buttons.append((hit, action))

  def _render(self, rect):
    s, theme = self._scale, self._theme
    rl.draw_rectangle_rec(rect, theme.background)
    self._buttons = []
    if self._panel in ('rename', 'mkdir'):
      self._render_name(rect)
    elif self._panel == 'places':
      self._render_places(rect)
    elif self._panel == 'preview':
      self._render_preview(rect)
    else:
      self._render_list(rect)
      if self._panel in ('actions', 'delete'):
        self._render_actions(rect)
    footer = rl.Rectangle(rect.x + 32 * s, rect.y + rect.height - 88 * s, rect.width - 64 * s, 64 * s)
    if self._future is not None:
      labels = {'list': tr('Loading...'), 'preview': tr('Loading preview...'), 'copy': tr('Copying...'), 'move': tr('Moving...'),
                'delete': tr('Deleting...'), 'rename': tr('Renaming...'), 'mkdir': tr('Creating folder...')}
      self._label(footer.x, footer.y, footer.width - 240 * s, footer.height, labels[self._job], color=theme.secondary_text)
      self._button(rl.Rectangle(footer.x + footer.width - 224 * s, footer.y, 224 * s, footer.height), 'Cancel', 'cancel-job')
    else:
      message = self._error or self._notice
      if not message and not self._panel and not self._transfer:
        message = f'{len(self._visible_entries)} ' + tr('items')
        if not self._fs.writable(self._path / 'new folder'):
          message += ' · ' + tr('Read-only location')
      self._label(footer.x, footer.y, footer.width, footer.height, message, 28, theme.secondary_text)

  def _render_list(self, rect):
    s, theme = self._scale, self._theme
    x, y = rect.x + 32 * s, rect.y + 24 * s
    for label, action, width in [('Up', 'up', 120), ('Home', 'home', 160), ('Places', 'places', 180)]:
      self._button(rl.Rectangle(x, y, width * s, 80 * s), label, action)
      x += (width + 12) * s
    x = rect.x + rect.width - 32 * s
    for label, action, width in [('Refresh', 'refresh', 180), ('Hide hidden' if self._hidden else 'Show hidden', 'hidden', 240),
                                 ('New folder', 'mkdir', 240)]:
      x -= width * s
      self._button(rl.Rectangle(x, y, width * s, 80 * s), label, action,
                   enabled=action != 'mkdir' or not self._transfer and self._fs.writable(self._path / 'new folder'))
      x -= 12 * s
    self._label(rect.x + 40 * s, y + 104 * s, rect.width - 80 * s, 64 * s, self._path, 38)
    viewport = rl.Rectangle(rect.x + 32 * s, rect.y + 208 * s, rect.width - 64 * s, rect.height - (416 if self._transfer else 312) * s)
    visible_key = id(self._entries), self._hidden, bool(self._transfer)
    if visible_key != self._visible_key:
      self._visible_entries = [entry for entry in self._entries if (self._hidden or not entry.path.name.startswith('.')) and
                              (not self._transfer or entry.directory)]
      self._visible_key = visible_key
    entries = self._visible_entries
    row_height = 112 * s
    offset = self._scroll.offset
    if self.enabled and not self._panel and self._future is None:
      offset = self._scroll.update(viewport, rl.Rectangle(0, 0, viewport.width, len(entries) * row_height))
    rl.begin_scissor_mode(int(viewport.x), int(viewport.y), int(viewport.width), int(viewport.height))
    first = max(0, int(-offset / row_height))
    for index in range(first, min(len(entries), first + math.ceil(viewport.height / row_height) + 2)):
      entry = entries[index]
      row = rl.Rectangle(viewport.x, viewport.y + index * row_height + offset, viewport.width, row_height)
      action = ('open', entry)
      if self._pressed == action and self.is_pressed and not self._dragged:
        rl.draw_rectangle_rec(row, theme.surface)
      icon = rl.Rectangle(row.x + 20 * s, row.y + 32 * s, 48 * s, 48 * s)
      if entry.directory:
        rl.draw_rectangle_rounded(rl.Rectangle(icon.x, icon.y - 5 * s, 24 * s, 16 * s), 0.2, 8, theme.control)
      rl.draw_rectangle_rounded_lines_ex(icon, 0.15, 8, 3 * s, theme.control)
      self._label(row.x + 96 * s, row.y + 12 * s, row.width - 256 * s, 52 * s, entry.path.name, 36)
      kind = tr('Folder') if entry.directory else tr('Symbolic link') if stat.S_ISLNK(entry.info.st_mode) else _size(entry.info.st_size)
      self._label(row.x + 96 * s, row.y + 64 * s, row.width - 256 * s, 36 * s, kind, 26, theme.secondary_text)
      if not self._panel and self._future is None:
        hit = rl.get_collision_rec(row, viewport)
        if hit.width > 0 and hit.height > 0 and self._scroll.is_touch_valid():
          self._buttons.append((hit, action))
        if not self._transfer:
          self._button(rl.Rectangle(row.x + row.width - 112 * s, row.y + 12 * s, 96 * s, 88 * s), '...', ('actions', entry),
                       clip=viewport, enabled=self._scroll.is_touch_valid())
      rl.draw_line_ex(rl.Vector2(row.x + 96 * s, row.y + row.height), rl.Vector2(row.x + row.width, row.y + row.height), s, theme.surface)
    if not entries and self._future is None:
      text = tr('No folders here.') if self._transfer else tr('No visible items.') if self._entries else tr('This folder is empty.')
      self._label(viewport.x, viewport.y, viewport.width, viewport.height, text, color=theme.secondary_text, center=True)
    rl.end_scissor_mode()
    if self._transfer:
      y = rect.y + rect.height - 196 * s
      self._label(rect.x + 40 * s, y, rect.width - 720 * s, 80 * s, self._selected.path.name, 32)
      self._button(rl.Rectangle(rect.x + rect.width - 664 * s, y, 280 * s, 80 * s), 'Cancel', 'cancel-transfer')
      self._button(rl.Rectangle(rect.x + rect.width - 360 * s, y, 328 * s, 80 * s),
                   'Copy here' if self._transfer == 'copy' else 'Move here', 'paste', primary=True,
                   enabled=self._fs.writable(self._path / self._selected.path.name))

  def _render_places(self, rect):
    s = self._scale
    self._label(rect.x + 40 * s, rect.y + 24 * s, rect.width - 400 * s, 80 * s, tr('Places'), 40)
    self._button(rl.Rectangle(rect.x + rect.width - 272 * s, rect.y + 24 * s, 240 * s, 80 * s), 'Back', 'close')
    for index, (name, path) in enumerate(self._places):
      y = rect.y + (144 + index * 128) * s
      self._button(rl.Rectangle(rect.x + 40 * s, y, 320 * s, 104 * s), name, ('place', path))
      self._label(rect.x + 400 * s, y, rect.width - 440 * s, 104 * s, path, 30, self._theme.secondary_text)

  def _render_actions(self, rect):
    s, theme = self._scale, self._theme
    self._buttons = []
    rl.draw_rectangle_rec(rect, rl.Color(theme.background.r, theme.background.g, theme.background.b, 180))
    entry = self._selected
    deleting = self._panel == 'delete'
    name_lines = wrap_text(gui_app.font(theme.font_weight), _display(entry.path.name), round(30 * s), int(rect.width - 80 * s)) if deleting else []
    height = max(352, 312 + len(name_lines) * 40) * s
    panel = rl.Rectangle(rect.x, rect.y + rect.height - 104 * s - height, rect.width, height)
    rl.draw_rectangle_rec(panel, theme.surface)
    self._label(panel.x + 40 * s, panel.y + 24 * s, panel.width - 360 * s, 64 * s,
                tr('Delete permanently?') if deleting else entry.path.name, 40)
    self._button(rl.Rectangle(panel.x + panel.width - 272 * s, panel.y + 24 * s, 240 * s, 80 * s), 'Cancel' if deleting else 'Close', 'close')
    if deleting:
      for index, line in enumerate(name_lines):
        self._label(panel.x + 40 * s, panel.y + (112 + index * 40) * s, panel.width - 80 * s, 40 * s, line, 30)
      self._label(panel.x + 40 * s, panel.y + (120 + len(name_lines) * 40) * s, panel.width - 80 * s, 40 * s,
                  tr('This item and its contents cannot be restored.'), 28, theme.secondary_text)
    else:
      self._label(panel.x + 40 * s, panel.y + 112 * s, panel.width - 80 * s, 64 * s, entry.path, 30, theme.secondary_text)
    supported = entry.directory or stat.S_ISREG(entry.info.st_mode)
    writable = supported and self._fs.writable(entry.path, recursive=entry.directory)
    choices = [('Delete permanently', 'confirm-delete')] if deleting else [('Copy', 'copy'), ('Move', 'move'), ('Rename', 'rename'), ('Delete', 'delete')]
    width = 440 * s if deleting else (panel.width - 128 * s) / 4
    for index, (label, action) in enumerate(choices):
      x = panel.x + panel.width - 40 * s - width if deleting else panel.x + 40 * s + index * (width + 16 * s)
      self._button(rl.Rectangle(x, panel.y + panel.height - 128 * s, width, 88 * s), label, action,
                   enabled=supported if action == 'copy' else writable, primary=deleting)

  def _render_name(self, rect):
    s, theme = self._scale, self._theme
    title = tr('Rename') if self._panel == 'rename' else tr('New folder')
    self._label(rect.x + 40 * s, rect.y + 24 * s, rect.width - 680 * s, 80 * s, title, 40)
    self._button(rl.Rectangle(rect.x + rect.width - 584 * s, rect.y + 24 * s, 240 * s, 80 * s), 'Cancel', 'close')
    self._button(rl.Rectangle(rect.x + rect.width - 320 * s, rect.y + 24 * s, 288 * s, 80 * s), 'Save', 'submit', primary=True,
                 enabled=bool(self._input.text))
    # InputBox is kept inline so the shell continues checking off-road authority.
    field = rl.Rectangle(rect.x + 40 * s, rect.y + 152 * s, rect.width - 80 * s, 104 * s)
    self._input.render(field)
    self._label(field.x, field.y + field.height + 24 * s, field.width, 56 * s,
                self._selected.path.parent if self._panel == 'rename' else self._path, 28, theme.secondary_text)
    labels = {SHIFT_ACTIVE_KEY: tr('Shift'), SHIFT_INACTIVE_KEY: tr('Shift'), BACKSPACE_KEY: tr('Backspace'), ENTER_KEY: tr('Save'), ' ': tr('Space')}
    for row, keys in enumerate(KEYBOARD_LAYOUTS[self._keyboard]):
      unit = (rect.width - 80 * s) / sum(3 if key == ' ' else 1 for key in keys)
      x = rect.x + 40 * s
      for key in keys:
        width = unit * (3 if key == ' ' else 1)
        self._button(rl.Rectangle(x, rect.y + rect.height - (496 - row * 88) * s, width - 8 * s, 80 * s),
                     labels.get(key, key), ('key', key))
        x += width

  def _render_preview(self, rect):
    s, theme = self._scale, self._theme
    self._button(rl.Rectangle(rect.x + 32 * s, rect.y + 24 * s, 180 * s, 80 * s), 'Back', 'close')
    self._label(rect.x + 244 * s, rect.y + 24 * s, rect.width - 500 * s, 80 * s, self._selected.path.name, 40)
    self._button(rl.Rectangle(rect.x + rect.width - 224 * s, rect.y + 24 * s, 192 * s, 80 * s), 'Actions', ('actions', self._selected))
    try:
      modified = datetime.fromtimestamp(self._selected.info.st_mtime).strftime('%Y-%m-%d %H:%M')
    except (ValueError, OSError, OverflowError):
      modified = tr('Unknown date')
    self._label(rect.x + 40 * s, rect.y + 120 * s, rect.width - 80 * s, 48 * s,
                _size(self._selected.info.st_size) + ' · ' + modified + ' · ' + str(self._selected.path), 28, theme.secondary_text)
    viewport = rl.Rectangle(rect.x + 40 * s, rect.y + 200 * s, rect.width - 80 * s, rect.height - 304 * s)
    if self._preview_lines is None:
      text = '\n'.join(_display(line.expandtabs(4)) for line in self._preview.splitlines())
      self._preview_lines = wrap_text(gui_app.font(theme.font_weight), text, round(30 * s), int(viewport.width))
    line_height = 46 * s
    offset = self._preview_scroll.offset
    if self.enabled and self._future is None:
      offset = self._preview_scroll.update(viewport, rl.Rectangle(0, 0, viewport.width, len(self._preview_lines) * line_height))
    rl.begin_scissor_mode(int(viewport.x), int(viewport.y), int(viewport.width), int(viewport.height))
    first = max(0, int(-offset / line_height))
    for index in range(first, min(len(self._preview_lines), first + math.ceil(viewport.height / line_height) + 2)):
      self._label(viewport.x, viewport.y + index * line_height + offset, viewport.width, line_height, self._preview_lines[index], 30)
    rl.end_scissor_mode()
