"""Firefox's own chrome in a Raylib pane; all browser work exists only while open."""

from collections.abc import Callable
import math
import time

from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.layouts.arabesque.theme import DEFAULT_THEME
from openpilot.selfdrive.ui.layouts.arabesque.tools.browser_session import BrowserSession, load_setup
from openpilot.system.ui.lib.application import GL_VERSION, gui_app, MouseEvent, MousePos, TextAlignment
from openpilot.system.ui.widgets import Widget, device
from openpilot.system.ui.widgets.keyboard import KEYBOARD_LAYOUTS, BACKSPACE_KEY, ENTER_KEY, SHIFT_ACTIVE_KEY, SHIFT_INACTIVE_KEY
from openpilot.system.ui.widgets.label import gui_label

_KEYS = {rl.KEY_ENTER: 0xff0d, rl.KEY_KP_ENTER: 0xff0d, rl.KEY_BACKSPACE: 0xff08, rl.KEY_TAB: 0xff09,
         rl.KEY_ESCAPE: 0xff1b, rl.KEY_DELETE: 0xffff, rl.KEY_LEFT: 0xff51, rl.KEY_UP: 0xff52,
         rl.KEY_RIGHT: 0xff53, rl.KEY_DOWN: 0xff54, rl.KEY_HOME: 0xff50, rl.KEY_END: 0xff57,
         rl.KEY_PAGE_UP: 0xff55, rl.KEY_PAGE_DOWN: 0xff56}
_FRAGMENT = GL_VERSION + """
precision mediump float;
in vec2 fragTexCoord;
uniform sampler2D texture0;
out vec4 finalColor;
void main() { finalColor = vec4(texture(texture0, fragTexCoord).bgr, 1.0); }
"""
_VERTEX = GL_VERSION + """
in vec3 vertexPosition;
in vec2 vertexTexCoord;
uniform mat4 mvp;
out vec2 fragTexCoord;
void main() { fragTexCoord = vertexTexCoord; gl_Position = mvp*vec4(vertexPosition, 1.0); }
"""


class Browser(Widget):
  def __init__(self, permitted: Callable[[], bool]):
    super().__init__()
    self._permitted = permitted
    self._session = None
    self._texture = self._shader = None
    self._keyboard = self._pointer = False
    self._layout_name = 'lowercase'
    self._buttons = []
    self._pressed = self._gesture = None
    self._scroll_y = 0
    self._cursor = None
    self._awake = True
    self._wake_guard = False
    self._closing = None
    self._resume_pending = False
    self._declined = False
    self._offer = None
    self._message = ''
    self._restorable = self._confirm_restore = False
    self._actions = []
    self._retry_operation = None
    self._texture_generation = None
    self._error_seen = ''

  def show_event(self):
    super().show_event()
    if not self._permitted():
      return
    rl.set_exit_key(0)
    self._drain_keys()
    self._awake, self._resume_pending = True, False
    self._closing = BrowserSession.pending_stop
    self._resume_pending = self._closing is not None and not self._closing.is_set()
    if not self._resume_pending:
      self._start_session()
    gui_app.add_render_prepare(self._prepare, self.hide_event)

  def _start_session(self, operation=None):
    if not self._permitted():
      return
    if self._confirm_restore and operation is None:
      return
    self._update_layout_rects()
    offered = self._offer['target'] if operation == 'update' and self._offer else None
    self._offer = None
    try:
      setup = load_setup()
      info = setup.inspect()
      self._message = ''
      self._error_seen = ''
      self._restorable = info.get('restorable', False)
      if operation is None:
        operation = info['action']
        if operation == 'update' and not info.get('repair'):
          if not self._declined:
            self._offer = info
            return
          operation = 'browse'
      target = setup.bundle_id(info['target'])
      if offered is not None and target != setup.bundle_id(offered):
        raise RuntimeError('Firefox update changed. Reopen Firefox to review the current offer.')
    except (OSError, ValueError, RuntimeError) as error:
      self._message = str(error)
      return
    # Keep a readable fixed X screen; opening the keyboard crops/pans the view rather than squashing it.
    height = max(240, min(1200, round((self._rect.height - 88 * self._scale) * 1440 / self._rect.width)))
    self._session = BrowserSession(1440, height, max(1, min(30, gui_app.target_fps)), operation=operation, target=target)
    self._update_layout_rects()

  def hide_event(self):
    self._resume_pending = False
    self._stop_session()
    gui_app.remove_render_prepare(self._prepare)
    self._drain_keys()
    rl.set_exit_key(rl.KEY_ESCAPE)
    super().hide_event()

  def _stop_session(self):
    self._reset_input()
    if self._session is not None:
      self._session.close()
      self._closing = self._session.stopped
      self._session = None
    if self._texture is not None:
      rl.unload_texture(self._texture)
      self._texture = None
    if self._shader is not None:
      rl.unload_shader(self._shader)
      self._shader = None

  def poll_runtime(self, awake):
    if not awake and self._awake:
      self._stop_session()
      self._wake_guard = True
    elif awake and not self._awake and self._permitted():
      self._resume_pending = True
    self._awake = awake
    if awake and self._resume_pending and (self._closing is None or self._closing.is_set()) and self._permitted():
      self._resume_pending = False
      operation, self._retry_operation = self._retry_operation, None
      self._start_session(operation)
    if self._session is not None:
      if not self._permitted():
        self.hide_event()
        return
      self._session.poll()
      if self._session.error and self._session.error != self._error_seen:
        self._error_seen = self._session.error
        try:
          self._restorable = load_setup().inspect().get('restorable', False)
        except (OSError, ValueError, RuntimeError):
          self._restorable = False

  def set_enabled(self, enabled):
    super().set_enabled(enabled)
    if not self.enabled:
      self._reset_input()

  def _reset_input(self):
    if self._gesture is not None and self._session is not None:
      self._session.reset_input()
    self._gesture = self._pressed = None

  @staticmethod
  def _drain_keys():
    while rl.get_key_pressed() or rl.get_char_pressed():
      pass

  def _update_layout_rects(self):
    rect = self._rect
    self._scale = s = min(rect.width / 2160, rect.height / 976)
    bar = 88 * s
    keyboard = 376 * s if self._keyboard else 0
    self._screen = rl.Rectangle(rect.x, rect.y + bar, rect.width, max(1, rect.height - bar - keyboard))
    self._buttons = [(rl.Rectangle(rect.x + 8 * s, rect.y + 4 * s, 256 * s, 76 * s), 'Keyboard', 'keyboard'),
                     (rl.Rectangle(rect.x + 280 * s, rect.y + 4 * s, 256 * s, 76 * s),
                      'Pointer' if self._pointer else 'Scroll', 'pointer'),
                     (rl.Rectangle(rect.x + 552 * s, rect.y + 4 * s, 256 * s, 76 * s), 'Address', 'address')]
    self._pan = (rl.Rectangle(rect.x + rect.width - 280 * s, rect.y + 4 * s, 128 * s, 76 * s),
                 rl.Rectangle(rect.x + rect.width - 144 * s, rect.y + 4 * s, 128 * s, 76 * s))
    if self._keyboard:
      self._buttons += [(self._pan[0], 'Up', 'pan-up'), (self._pan[1], 'Down', 'pan-down')]
      labels = {BACKSPACE_KEY: '<-', ENTER_KEY: 'Enter', SHIFT_INACTIVE_KEY: 'Shift', SHIFT_ACTIVE_KEY: 'Shift', ' ': 'Space'}
      for row, keys in enumerate(KEYBOARD_LAYOUTS[self._layout_name]):
        unit = rect.width / sum(3 if key == ' ' else 1 for key in keys)
        x = rect.x
        for key in keys:
          width = unit * (3 if key == ' ' else 1)
          self._buttons.append((rl.Rectangle(x + 4 * s, rect.y + rect.height - keyboard + row * 92 * s,
                                             width - 8 * s, 84 * s), labels.get(key, key), key))
          x += width
    if self._session:
      self._factor = self._screen.width / self._session.width
      visible = min(self._session.height, self._screen.height / self._factor)
      self._scroll_y = max(0, min(self._scroll_y, self._session.height - visible))
      self._image = rl.Rectangle(self._screen.x, self._screen.y, self._screen.width, visible * self._factor)

  def _prepare(self):
    session = self._session
    generation = (id(session), session.generation) if session is not None else None
    if generation != self._texture_generation:
      if self._texture is not None:
        rl.unload_texture(self._texture)
        self._texture = None
      self._texture_generation = generation
    if session is None or not session.ready or not device.awake or not self._permitted():
      return
    frame = session.take_frame()
    if frame is None:
      return
    slot, buffer = frame
    pixels, failure = None, None
    try:
      if self._shader is None:
        self._shader = rl.load_shader_from_memory(_VERTEX, _FRAGMENT)
        if not rl.is_shader_valid(self._shader) or self._shader.id == rl.rl_get_shader_id_default():
          raise RuntimeError('Firefox color shader failed')
      pixels = buffer
      if self._texture is None:
        image = rl.Image(pixels, session.width, session.height, 1, rl.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
        self._texture = rl.load_texture_from_image(image)
        if not rl.is_texture_valid(self._texture):
          raise RuntimeError('Firefox texture allocation failed')
        rl.set_texture_filter(self._texture, rl.TEXTURE_FILTER_BILINEAR)
      else:
        rl.update_texture(self._texture, pixels)
      session.uploaded(slot)
    except (OSError, TypeError, RuntimeError, ValueError) as e:
      failure = str(e)
    finally:
      session.release(slot)
    if failure is not None:
      session.fail(failure)

  def _input(self, event, a, b):
    if self._session and self._session.interactive and self._permitted():
      self._session.send({'type': 'input', 'event': event, 'a': a, 'b': b})

  def _key(self, symbol, modifiers=0):
    if reset_timeout := getattr(device, '_reset_interactive_timeout', None):
      reset_timeout()
    self._input('key', symbol, modifiers)

  def _text(self, text):
    for char in text:
      code = ord(char)
      self._key(code if code <= 255 else 0x01000000 | code)

  def _update_state(self):
    if not self.enabled or not device.awake or self._wake_guard or not self._session or not self._session.interactive:
      self._drain_keys()
      return
    ctrl = rl.is_key_down(rl.KEY_LEFT_CONTROL) or rl.is_key_down(rl.KEY_RIGHT_CONTROL)
    alt = rl.is_key_down(rl.KEY_LEFT_ALT) or rl.is_key_down(rl.KEY_RIGHT_ALT)
    shift = rl.is_key_down(rl.KEY_LEFT_SHIFT) or rl.is_key_down(rl.KEY_RIGHT_SHIFT)
    chars = []
    while char := rl.get_char_pressed():
      if not ctrl and not alt and char >= 32:
        chars.append(chr(char))
    # Commit text before Enter/Tab; never inject printable physical keys a second time.
    self._text(''.join(chars))
    while key := rl.get_key_pressed():
      if key in _KEYS:
        self._key(_KEYS[key], int(ctrl) | int(alt) << 1 | int(shift) << 2)
      elif (ctrl or alt) and 32 <= key <= 96:
        self._key(ord(chr(key).lower()), int(ctrl) | int(alt) << 1 | int(shift) << 2)
    for key, symbol in _KEYS.items():
      if rl.is_key_pressed_repeat(key):
        self._key(symbol)

  def _point(self, pos):
    if not self._session or not rl.check_collision_point_rec(pos, self._image):
      return None
    return (min(self._session.width - 1, max(0, int((pos.x - self._image.x) / self._factor))),
            min(self._session.height - 1, max(0, int((pos.y - self._image.y) / self._factor + self._scroll_y))))

  def _process_mouse_events(self):
    if self._wake_guard:
      self._wake_guard = False
      return
    super()._process_mouse_events()

  def _handle_mouse_press(self, pos: MousePos):
    self._pressed = next((key for bounds, _, key in self._actions + self._buttons if rl.check_collision_point_rec(pos, bounds)), None)
    if self._pressed is not None or not self._session or not self._session.interactive:
      return
    if point := self._point(pos):
      self._gesture = (pos.x, pos.y, pos.y, time.monotonic(), False)
      self._cursor = pos
      self._input('motion', *point)
      if self._pointer:
        self._input('button', 1, True)

  def _handle_mouse_event(self, mouse_event: MouseEvent):
    if self._gesture is None:
      return
    point = self._point(mouse_event.pos)
    if point is None:
      self._reset_input()
      return
    x, y, last_y, started, moved = self._gesture
    moved |= math.hypot(mouse_event.pos.x - x, mouse_event.pos.y - y) > 16 * self._scale
    self._cursor = mouse_event.pos
    self._input('motion', *point)
    if self._pointer:
      if mouse_event.left_released:
        self._input('button', 1, False)
    elif moved:
      steps = max(-6, min(6, int((mouse_event.pos.y - last_y) / (36 * self._scale))))
      for _ in range(abs(steps)):
        button = 4 if steps > 0 else 5
        self._input('button', button, True)
        self._input('button', button, False)
      if steps:
        last_y = mouse_event.pos.y
    elif mouse_event.left_released:
      button = 3 if time.monotonic() - started >= 0.55 else 1
      self._input('button', button, True)
      self._input('button', button, False)
    self._gesture = None if mouse_event.left_released else (x, y, last_y, started, moved)

  def _handle_mouse_release(self, pos: MousePos):
    key, self._pressed = self._pressed, None
    if key is None or not any(value == key and rl.check_collision_point_rec(pos, bounds) for bounds, _, value in self._actions + self._buttons):
      return
    self._reset_input()
    if key in ('update', 'later'):
      if self._offer is None:
        return
      self._declined = True
      self._start_session('update' if key == 'update' else 'browse')
      self._drain_keys()
      return
    if key == 'restore':
      self._confirm_restore = True
      if self._session:
        self._message = self._session.error
      return
    if key == 'cancel-restore':
      self._confirm_restore = False
      return
    if key in ('retry', 'confirm-restore'):
      self._confirm_restore = False
      self._retry_operation = 'restore' if key == 'confirm-restore' else None
      self._stop_session()
      self._resume_pending = True
      return
    if not self._session or not self._session.interactive:
      return
    if key == 'keyboard':
      self._keyboard = not self._keyboard
    elif key == 'address':
      self._key(ord('l'), 1)
      self._keyboard = True
    elif key == 'pointer':
      self._pointer = not self._pointer
    elif key in ('pan-up', 'pan-down'):
      self._scroll_y += (-1 if key == 'pan-up' else 1) * self._screen.height / self._factor * 0.6
    elif key in (SHIFT_ACTIVE_KEY, SHIFT_INACTIVE_KEY):
      self._layout_name = 'lowercase' if self._layout_name == 'uppercase' else 'uppercase'
    elif key in ('123', '#+=', 'ABC'):
      self._layout_name = {'123': 'numbers', '#+=': 'specials', 'ABC': 'lowercase'}[key]
    elif key in (BACKSPACE_KEY, ENTER_KEY):
      self._key(0xff08 if key == BACKSPACE_KEY else 0xff0d)
    else:
      self._text(key)
      if self._layout_name == 'uppercase':
        self._layout_name = 'lowercase'
    self._update_layout_rects()

  def _render(self, rect):
    theme, s = DEFAULT_THEME, self._scale
    rl.draw_rectangle_rec(rect, theme.background)
    self._actions = []
    if (self._texture is not None and self._session and self._session.interactive and not self._session.error and
        self._texture_generation == (id(self._session), self._session.generation)):
      source = rl.Rectangle(0, self._scroll_y, self._session.width, self._image.height / self._factor)
      rl.begin_shader_mode(self._shader)
      rl.draw_texture_pro(self._texture, source, self._image, rl.Vector2(0, 0), 0, rl.WHITE)
      rl.end_shader_mode()
      if self._pointer and self._cursor and rl.check_collision_point_rec(self._cursor, self._image):
        rl.draw_circle_lines(int(self._cursor.x), int(self._cursor.y), 12 * s, theme.text)
    else:
      choices = []
      if self._confirm_restore:
        message = ('Restore previous Firefox and its pre-update browser data?\n' +
                   'Your current browser data will be saved separately.')
        choices = [('Restore previous Firefox', 'confirm-restore'), ('Cancel', 'cancel-restore')]
      elif self._offer:
        current, target = self._offer['current'], self._offer['target']
        message = ('Firefox update available: ' + current['version'] + ' -> ' + target['version']
                   if current['version'] != target['version'] else 'Browser component update available')
        choices = [('Update Firefox', 'update'), ('Later', 'later')]
      else:
        message = self._message
        if not message:
          if self._session:
            message = self._session.error or self._session.status
          else:
            message = 'Closing the previous Firefox session...' if self._resume_pending else 'Firefox is available off-road only.'
        if self._message or self._session and self._session.error:
          choices = [('Retry', 'retry')]
          if self._restorable:
            choices.append(('Restore previous Firefox', 'restore'))
      gui_label(self._screen, message.replace('…', '...'), round(28 * s), theme.text, theme.font_weight, alignment=TextAlignment.CENTER)
      for index, (label, key) in enumerate(choices):
        self._actions.append((rl.Rectangle(rect.x + rect.width / 2 + (index - len(choices) / 2) * 540 * s,
                                           rect.y + rect.height * 0.7, 516 * s, 92 * s), label, key))
    for bounds, label, key in self._buttons + self._actions:
      pressed = self.enabled and key == self._pressed and self.is_pressed
      rl.draw_rectangle_rounded(bounds, 0.2, 8, theme.control if pressed else theme.surface)
      gui_label(bounds, label, round(28 * s), theme.background if pressed else theme.text, theme.font_weight,
                alignment=TextAlignment.CENTER)
