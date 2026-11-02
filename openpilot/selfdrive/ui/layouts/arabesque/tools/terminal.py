"""One local shell; the installed tmux owns terminal interpretation."""

from collections import deque
from importlib.resources import as_file
from itertools import groupby
import os
import re
import subprocess
import tempfile
import time
import unicodedata

from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.layouts.arabesque.theme import DEFAULT_THEME, Theme
from openpilot.system.ui.lib.application import FONT_DIR, FONT_SCALE, FontWeight, gui_app, MousePos, TextAlignment
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget, device
from openpilot.system.ui.widgets.keyboard import KEYBOARD_LAYOUTS, BACKSPACE_KEY, ENTER_KEY, SHIFT_INACTIVE_KEY, SHIFT_ACTIVE_KEY
from openpilot.system.ui.widgets.label import gui_label

_SGR = re.compile(r'\x1b\[([0-9;:]*)m|([\x0e\x0f])')
_LINK = re.compile(r'\x1b\]8;[^\x07\x1b]*(?:\x07|\x1b\\)')
_ACS = str.maketrans({'q': '─', 'x': '│', 'l': '┌', 'k': '┐', 'm': '└', 'j': '┘', 'n': '┼', 't': '├', 'u': '┤', 'v': '┴', 'w': '┬', 'a': '▒'})
_PALETTE = ((0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16), (36, 114, 200), (188, 63, 188), (17, 168, 205),
            (229, 229, 229), (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67), (59, 142, 234),
            (214, 112, 214), (41, 184, 219), (255, 255, 255))
_KEYS = {rl.KEY_ENTER: 'Enter', rl.KEY_KP_ENTER: 'Enter', rl.KEY_BACKSPACE: 'BSpace', rl.KEY_TAB: 'Tab',
         rl.KEY_ESCAPE: 'Escape', rl.KEY_LEFT: 'Left', rl.KEY_RIGHT: 'Right', rl.KEY_UP: 'Up', rl.KEY_DOWN: 'Down',
         rl.KEY_HOME: 'Home', rl.KEY_END: 'End', rl.KEY_DELETE: 'DC', rl.KEY_INSERT: 'IC',
         rl.KEY_PAGE_UP: 'PPage', rl.KEY_PAGE_DOWN: 'NPage', **{getattr(rl, f'KEY_F{i}'): f'F{i}' for i in range(1, 13)}}

_KEY_NAMES = frozenset(_KEYS.values()) | {'C-Left', 'C-Right', 'C-Up', 'C-Down', 'C-Home', 'C-End', 'BTab'}


def _color(index):
  if index < 16:
    return _PALETTE[index]
  if index < 232:
    levels = (0, 95, 135, 175, 215, 255)
    return tuple(levels[(index - 16) // divisor % 6] for divisor in (36, 6, 1))
  return (8 + (index - 232) * 10,) * 3


def _styled_rows(lines):
  """tmux positioned the text; interpret only its exported cell styling."""
  rows, fg, bg, reverse, hidden, acs = [], None, None, False, False, False
  for line in lines:
    line = _LINK.sub('', line)
    spans, column, start = [], 0, 0
    for match in (*_SGR.finditer(line), None):
      end = match.start() if match else len(line)
      text = line[start:end].translate(_ACS) if acs else line[start:end]
      if text:
        width = sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text)
        spans.append((column, text, fg, bg, reverse, hidden, width))
        column += width
      if match is None:
        break
      if match[2]:
        acs = match[2] == '\x0e'
        start = match.end()
        continue
      codes = [int(value or 0) for value in match[1].replace(':2::', ';2;').replace(':', ';').split(';')]
      i = 0
      while i < len(codes):
        code = codes[i]
        if code == 0:
          fg, bg, reverse, hidden = None, None, False, False
        elif code in (7, 27):
          reverse = code == 7
        elif code in (8, 28):
          hidden = code == 8
        elif 30 <= code <= 37 or 90 <= code <= 97:
          fg = _color(code - 30 if code < 90 else code - 90 + 8)
        elif 40 <= code <= 47 or 100 <= code <= 107:
          bg = _color(code - 40 if code < 100 else code - 100 + 8)
        elif code == 39:
          fg = None
        elif code == 49:
          bg = None
        elif code in (38, 48, 58) and i + 1 < len(codes):
          count = 1 if codes[i + 1] == 5 else 3 if codes[i + 1] == 2 else 0
          values = codes[i + 2:i + 2 + count]
          if count and len(values) == count and all(0 <= value <= 255 for value in values):
            color = _color(values[0]) if count == 1 else tuple(values)
            if code == 38:
              fg = color
            elif code == 48:
              bg = color
            i += count + 1
        i += 1
      start = match.end()
    rows.append(spans)
  return rows


class _Session:
  def __init__(self, columns, rows):
    self.rows, self.cursor, self.error = [], (0, 0, False), ''
    self._size = self._desired_size = columns, rows
    self._pending = deque([('ack', 0), ('open', 1)])
    self._block, self._body = None, []
    self._buffer, self._out = bytearray(), bytearray()
    self._snapshot, self._last_capture = False, 0.0
    self._deadline = time.monotonic() + 3
    self._folder = None
    self._process = None
    try:
      self._folder = tempfile.TemporaryDirectory(prefix='arabesque-')
      self._process = subprocess.Popen(
        ['tmux', '-u', '-S', self._folder.name + '/socket', '-f', '/dev/null', '-C',
         'set-option', '-s', 'exit-unattached', 'on', ';', 'new-session', '-f', 'no-output', '-s', 'arabesque', '-P', '-F', '#{pane_id}',
         '-x', str(columns), '-y', str(rows), '-c', os.path.expanduser('~'), '/bin/bash', '-i'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
        # A fresh server inherits this UI's environment, including the private host Params namespace.
        env={key: value for key, value in os.environ.items() if key not in ('TMUX', 'TMUX_PANE')})
      os.set_blocking(self._process.stdin.fileno(), False)
      os.set_blocking(self._process.stdout.fileno(), False)
    except OSError:
      self.error = tr('Local shell unavailable. Bash and tmux must be installed.')
      self.close()

  def _request(self, command, kind='ack', lines=0):
    encoded = command.encode() + b'\n'
    if len(self._out) + len(encoded) > 32768 or len(self._pending) >= 64:
      return False
    if not self._pending:
      self._deadline = time.monotonic() + 3
    self._out.extend(encoded)
    self._pending.append((kind, lines))
    return True

  def keys(self, value):
    if self._process is None or not hasattr(self, '_pane') or self.error:
      return
    if isinstance(value, bytes):
      if not value:
        return
      command = f'send-keys -t {self._pane} -H ' + ' '.join(f'{byte:02x}' for byte in value) if len(value) <= 2048 else ''
    else:
      assert value in _KEY_NAMES
      command = f'send-keys -t {self._pane} {value}'
    if not command or not self._request(command):
      self.error = tr('Shell input limit reached. Return to Desktop to open a new session.')
      self.close()
    else:
      self._last_capture = 0
      self._flush()

  def resize(self, columns, rows):
    self._desired_size = columns, rows

  def _line(self, line):
    if self._block is None:
      if line.startswith(b'%begin '):
        if not self._pending or not re.fullmatch(rb'%begin \d+ \d+ \d+', line):
          raise ValueError('Unexpected tmux reply')
        self._block, self._body, self._reply_bytes = line[7:], [], 0
      return
    kind, count = self._pending[0]
    if line == b'%error ' + self._block:
      raise ValueError('tmux command failed')
    # Count rows before accepting the guard: terminal text can itself start with "%end".
    if len(self._body) < count:
      self._reply_bytes += len(line)
      if self._reply_bytes > 262144:
        raise ValueError('Oversized tmux screen')
      self._body.append(line)
      return
    if line != b'%end ' + self._block:
      raise ValueError('Malformed tmux reply')
    body = [value.decode('utf-8', errors='replace') for value in self._body]
    self._pending.popleft()
    self._block = None
    if kind == 'open':
      if not re.fullmatch(r'%\d+', body[0]):
        raise ValueError('Missing tmux pane')
      self._pane = body[0]
    elif kind == 'screen':
      if body != getattr(self, '_previous', None):
        self.rows = _styled_rows(body)
        self._previous = body
    elif kind == 'cursor':
      x, y, visible = map(int, body[0].split())
      self.cursor = x, y, bool(visible)
      self._snapshot = False

  def _flush(self):
    if not self._out:
      return True
    try:
      del self._out[:os.write(self._process.stdin.fileno(), self._out)]
    except BlockingIOError:
      pass
    except OSError:
      self.error = tr('Local shell connection ended. Return to Desktop to try again.')
      self.close()
      return False
    return True

  def update(self):
    process = self._process
    if process is None:
      return
    now = time.monotonic()
    try:
      if not self._flush():
        return
      # Snapshot-only output avoids copying arbitrary command output into Python queues.
      remaining = 65536
      while remaining > 0:
        try:
          chunk = os.read(process.stdout.fileno(), min(16384, remaining))
        except BlockingIOError:
          break
        if not chunk:
          self.error = tr('Shell exited. Return to Desktop to open a new session.')
          self.close()
          return
        remaining -= len(chunk)
        self._buffer.extend(chunk)
        if len(self._buffer) > 262144:
          raise ValueError('Oversized tmux reply')
        while (end := self._buffer.find(b'\n')) >= 0:
          line = bytes(self._buffer[:end])
          del self._buffer[:end + 1]
          self._line(line)
      if self._pending and now > self._deadline:
        raise ValueError('tmux stopped responding')
      if not self._pending and not self._out and not self._snapshot and now - self._last_capture >= 1 / 30:
        if self._desired_size != self._size:
          self._size = self._desired_size
          self._request(f'refresh-client -C {self._size[0]},{self._size[1]}')
        self._request(f'capture-pane -p -e -N -t {self._pane} -S 0 -E {self._size[1] - 1}', 'screen', self._size[1])
        self._request(f"display-message -p -t {self._pane} '#{{cursor_x}} #{{cursor_y}} #{{cursor_flag}}'", 'cursor', 1)
        self._snapshot, self._last_capture, self._deadline = True, now, now + 3
        self._flush()
    except (OSError, ValueError, IndexError):
      self.error = tr('Local shell connection ended. Return to Desktop to try again.')
      self.close()

  def close(self):
    process, self._process = self._process, None
    if process is not None:
      try:
        # This socket belongs only to this tool; never address the launcher server.
        os.write(process.stdin.fileno(), b'kill-server\n')
      except OSError:
        pass
      process.stdin.close()
      try:
        process.wait(timeout=0.02)
      except subprocess.TimeoutExpired:
        process.kill()
        try:
          process.wait(timeout=0.02)
        except subprocess.TimeoutExpired:
          # shortcut: Popen reaps a stalled child on its next launch; add async reaping only if observed on device.
          pass
      process.stdout.close()
    if self._folder is not None:
      self._folder.cleanup()
    self._out.clear()
    self._buffer.clear()
    self._pending.clear()


class Terminal(Widget):
  def __init__(self, theme: Theme = DEFAULT_THEME):
    super().__init__()
    self._theme = theme
    self._session, self._font = None, None
    self._fallback_font = None
    self._layout_name = 'lowercase'
    self._keyboard, self._ctrl = False, False
    self._pressed = None
    self._buttons, self._draw_rows = [], []
    self._previous_rows = None

  def show_event(self):
    super().show_event()
    rl.set_exit_key(0)
    self._drain_keys()
    if self._font is not None:
      return
    self._font = self._fallback_font = gui_app.font(FontWeight.UNIFONT)
    try:
      with as_file(FONT_DIR) as directory:
        font = rl.load_font_ex(str(directory / 'JetBrainsMono-Medium.ttf'), 64, None, 0)
    except OSError:
      font = rl.get_font_default()
    if font.texture.id != rl.get_font_default().texture.id:
      self._font = font
      rl.gen_texture_mipmaps(font.texture)
      rl.set_texture_filter(font.texture, rl.TextureFilter.TEXTURE_FILTER_TRILINEAR)
    self._update_layout_rects()
    self._session = _Session(*self._size)
    gui_app.add_render_prepare(self._prepare, self.hide_event)

  def hide_event(self):
    super().hide_event()
    if self._session is not None:
      gui_app.remove_render_prepare(self._prepare)
      self._session.close()
      self._session = None
    rl.set_exit_key(rl.KEY_ESCAPE)
    self._drain_keys()
    if self._font is not None and self._font is not self._fallback_font:
      # A Desktop/menu release may close us after glyphs were queued in this frame.
      rl.rl_draw_render_batch_active()
      rl.unload_font(self._font)
    self._font = self._fallback_font = None
    self._draw_rows, self._previous_rows, self._pressed = [], None, None

  def _prepare(self):
    session = self._session
    if session is None:
      return
    if session.rows is not self._previous_rows:
      self._draw_rows = []
      for row in session.rows:
        spans = []
        for column, text, foreground, background, reverse, hidden, _width in row:
          fg = rl.Color(*foreground, 255) if foreground else self._theme.text
          bg = rl.Color(*background, 255) if background else self._theme.background
          if reverse:
            fg, bg = bg, fg
          for offset, run, font, run_width in self._font_runs(text):
            spans.append((column + offset, run, fg, bg if background or reverse else None, hidden, run_width, font))
        self._draw_rows.append(spans)
      self._previous_rows = session.rows

  def _font_text(self, text):
    if text.isascii():
      return text
    result = []
    borders = {'─': '-', '│': '|', **dict.fromkeys('┌┐└┘├┤┴┬┼', '+')}
    for char in unicodedata.normalize('NFC', text):
      if unicodedata.combining(char):
        continue
      if ord(char) >= 128 and self._fallback_font.glyphs[rl.get_glyph_index(self._fallback_font, ord(char))].value != ord(char):
        # Unsupported wide characters must still occupy two terminal columns.
        char = borders.get(char, '?') + (' ' if unicodedata.east_asian_width(char) in ('W', 'F') else '')
      result.append(char)
    return ''.join(result)

  def _font_runs(self, text):
    text = self._font_text(text)
    if text.isascii():
      yield 0, text, self._font, len(text)
      return
    column = 0
    for ascii_text, characters in groupby(text, str.isascii):
      run = ''.join(characters)
      width = len(run) if ascii_text else sum(2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1 for char in run)
      yield column, run, self._font if ascii_text else self._fallback_font, width
      column += width

  @staticmethod
  def _drain_keys():
    while rl.get_key_pressed():
      pass
    while rl.get_char_pressed():
      pass

  def _update_layout_rects(self):
    rect = self._rect
    self._scale = s = min(rect.width / 2160, rect.height / 768)
    self._fallback_size = 31.2 * s * FONT_SCALE
    self._cell_w = self._fallback_size / 2  # Keep Unifont's 8×16 grid, enlarged 20% as in preview C.
    self._cell_h = self._fallback_size * 1.2
    advance = self._font.glyphs[rl.get_glyph_index(self._font, ord('M'))].advanceX if self._font else 0
    self._font_size = self._cell_w * self._font.baseSize / advance if advance else self._fallback_size
    keyboard_height = 368 * s if self._keyboard else 0
    self._screen = rl.Rectangle(rect.x + 20 * s, rect.y + 16 * s, rect.width - 40 * s, rect.height - 128 * s - keyboard_height)
    self._size = (max(20, min(240, int(self._screen.width / max(1, self._cell_w)))),
                  max(4, min(80, int(self._screen.height / max(1, self._cell_h)))))
    self._buttons = []
    controls = [('Esc', 'Escape'), ('Tab', 'Tab'), ('Ctrl', 'ctrl'), ('Left', 'Left'), ('Down', 'Down'),
                ('Up', 'Up'), ('Right', 'Right'), ('Hide keys' if self._keyboard else 'Keyboard', 'keyboard')]
    width = (rect.width - 40 * s) / len(controls)
    for i, (label, key) in enumerate(controls):
      self._buttons.append((rl.Rectangle(rect.x + 20 * s + i * width, rect.y + rect.height - 96 * s, width - 8 * s, 80 * s), label, key))
    if self._keyboard:
      labels = {SHIFT_INACTIVE_KEY: 'Shift', SHIFT_ACTIVE_KEY: 'Shift', BACKSPACE_KEY: 'Back', ENTER_KEY: 'Enter', ' ': 'Space'}
      for row, keys in enumerate(KEYBOARD_LAYOUTS[self._layout_name]):
        unit = (rect.width - 40 * s) / sum(3 if key == ' ' else 1 for key in keys)
        x = rect.x + 20 * s
        for key in keys:
          width = unit * (3 if key == ' ' else 1)
          self._buttons.append((rl.Rectangle(x, rect.y + rect.height - 464 * s + row * 88 * s, width - 8 * s, 80 * s), labels.get(key, key), key))
          x += width
    if self._session:
      self._session.resize(*self._size)

  def _send(self, value):
    if self._session is None:
      return
    if isinstance(value, str):
      if value in _KEY_NAMES:
        if self._ctrl and 'C-' + value in _KEY_NAMES:
          value = 'C-' + value
      elif self._ctrl and len(value) == 1:
        value = bytes([ord(value.upper()) & 31])
      else:
        value = value.encode()
    self._ctrl = False
    if reset_timeout := getattr(device, '_reset_interactive_timeout', None):
      reset_timeout()
    self._session.keys(value)

  def _update_state(self):
    if self._session is None:
      return
    # Main has checked off-road authority before rendering this tool.
    self._session.update()
    self._prepare()
    if not self.enabled:
      self._pressed, self._ctrl = None, False
      self._drain_keys()
      return
    ctrl = rl.is_key_down(rl.KEY_LEFT_CONTROL) or rl.is_key_down(rl.KEY_RIGHT_CONTROL)
    alt = rl.is_key_down(rl.KEY_LEFT_ALT) or rl.is_key_down(rl.KEY_RIGHT_ALT)
    characters = deque()
    char = rl.get_char_pressed()
    while char:
      if not ctrl and char >= 32:
        characters.append(chr(char))
      char = rl.get_char_pressed()
    keys = []
    key = rl.get_key_pressed()
    while key:
      keys.append(key)
      key = rl.get_key_pressed()
    for position, key in enumerate(keys):
      if key in _KEYS and characters and not any(32 <= later <= 96 for later in keys[position + 1:]):
        self._send(''.join(('\x1b' if alt else '') + char for char in characters))
        characters.clear()
      if ctrl and 32 <= key <= 95:
        self._send(bytes([key & 31]))
      elif key in _KEYS:
        self._send_key(key, ctrl, alt)
      elif 32 <= key <= 96 and characters:
        self._send(('\x1b' if alt else '') + characters.popleft())
    for key in _KEYS:
      if rl.is_key_pressed_repeat(key):
        self._send_key(key, ctrl, alt)
    if characters:
      self._send(''.join(('\x1b' if alt else '') + char for char in characters))

  def _send_key(self, key, ctrl, alt):
    name = _KEYS[key]
    if key == rl.KEY_TAB and (rl.is_key_down(rl.KEY_LEFT_SHIFT) or rl.is_key_down(rl.KEY_RIGHT_SHIFT)):
      name = 'BTab'
    elif ctrl and 'C-' + name in _KEY_NAMES:
      name = 'C-' + name
    if alt:
      self._send(b'\x1b')
    self._send(name)

  def _handle_mouse_press(self, pos: MousePos):
    self._pressed = next((key for rect, _, key in self._buttons if rl.check_collision_point_rec(pos, rect)), None)

  def _handle_mouse_release(self, pos: MousePos):
    key, self._pressed = self._pressed, None
    if key is None or not any(value == key and rl.check_collision_point_rec(pos, rect) for rect, _, value in self._buttons):
      return
    if key == 'keyboard':
      self._keyboard = not self._keyboard
    elif key == 'ctrl':
      self._ctrl = not self._ctrl
    elif key in (SHIFT_ACTIVE_KEY, SHIFT_INACTIVE_KEY):
      self._layout_name = 'lowercase' if self._layout_name == 'uppercase' else 'uppercase'
    elif key in ('123', '#+=', 'ABC'):
      self._layout_name = {'123': 'numbers', '#+=': 'specials', 'ABC': 'lowercase'}[key]
    else:
      self._send({BACKSPACE_KEY: 'BSpace', ENTER_KEY: 'Enter'}.get(key, key))
      if self._layout_name == 'uppercase':
        self._layout_name = 'lowercase'
    self._update_layout_rects()

  def _render(self, rect):
    theme, s = self._theme, self._scale
    rl.draw_rectangle_rec(rect, theme.background)
    if self._session and self._font:
      rl.begin_scissor_mode(int(self._screen.x), int(self._screen.y), int(self._screen.width), int(self._screen.height))
      # Terminal fonts must not be replaced by the UI language's proportional fallback.
      draw_text = getattr(rl, '_orig_draw_text_ex', rl.draw_text_ex)
      for row, spans in enumerate(self._draw_rows[:self._size[1]]):
        y = self._screen.y + row * self._cell_h
        for column, text, fg, bg, hidden, width, font in spans:
          x = self._screen.x + column * self._cell_w
          if bg:
            rl.draw_rectangle_rec(rl.Rectangle(x, y, width * self._cell_w, self._cell_h), bg)
          if not hidden:
            size = self._font_size if font is self._font else self._fallback_size
            draw_text(font, text, rl.Vector2(x, y), size, 0, fg)
      x, y, visible = self._session.cursor
      if visible and 0 <= x < self._size[0] and 0 <= y < self._size[1]:
        rl.draw_rectangle_lines_ex(rl.Rectangle(self._screen.x + x * self._cell_w, self._screen.y + y * self._cell_h,
                                               self._cell_w, self._cell_h), max(1, s), theme.control)
      rl.end_scissor_mode()
    error = self._session.error if self._session else ''
    if not error and not self._draw_rows:
      error = tr('Opening local shell…')
    if error:
      gui_label(self._screen, error, round(30 * s), theme.text, theme.font_weight, alignment=TextAlignment.CENTER)
    for bounds, label, key in self._buttons:
      pressed = self.enabled and (key == self._pressed and self.is_pressed or key == 'ctrl' and self._ctrl)
      rl.draw_rectangle_rounded(bounds, 0.2, 8, theme.control if pressed else theme.surface)
      gui_label(bounds, label, round(28 * s), theme.background if pressed else theme.text, theme.font_weight, alignment=TextAlignment.CENTER)
