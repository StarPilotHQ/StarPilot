"""Phone notification toast for the car screen, polled away from Android Auto's drawing thread."""
from dataclasses import dataclass
import os
import threading
import unicodedata

from openpilot.starpilot.system.starport import phone_notifications

TEXT_LIMIT = 80
PUNCTUATION = str.maketrans({'‘': "'", '’': "'", '‚': "'", '‛': "'", '“': '"', '”': '"',
                             '„': '"', '–': '-', '—': '-', '…': '...', ' ': ' '})


def plain(value, limit):
  """ASCII the bitmap fonts can draw, on one line, cut to limit characters with an ellipsis."""
  value = unicodedata.normalize('NFKD', value.translate(PUNCTUATION))
  value = ' '.join(''.join(c for c in value if c.isascii() and (c.isprintable() or c.isspace())).split())
  return value if len(value) <= limit else value[:limit - 3].rstrip() + '...'


@dataclass(frozen=True)
class Toast:
  sequence: int
  observed: int
  app: str
  sender: str
  text: str
  hide_while_moving: bool


def toast_from(value, settings):
  if value is None or not settings['enabled']:
    return None
  sender, text = plain(value['sender'], 60), plain(value['text'], TEXT_LIMIT)
  if not sender and not text:
    text = 'New message'
  return Toast(value['sequence'], value['observed'], plain(value['appName'], 40) or plain(value['app'], 40), sender, text,
               settings['hideWhileMoving'])


class ProjectionNotificationFeed:
  """Polls the handoff and settings about four times a second; the renderer only takes the finished toast."""
  def __init__(self, read=None, settings_path=None, start=True):
    self.read = read or phone_notifications.current
    self.settings_path = settings_path or phone_notifications.SETTINGS_PATH
    self._settings_key = None
    self._settings = {**phone_notifications.DEFAULTS, 'apps': {}}
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._value = None
    self._thread = None
    if start:
      self._thread = threading.Thread(target=self._run, name='aa-notifications', daemon=True)
      self._thread.start()

  def _read_settings(self):
    try:
      stat = os.stat(self.settings_path)
      key = stat.st_mtime_ns, stat.st_size, stat.st_ino
    except OSError:
      key = None
    if key != self._settings_key:
      self._settings_key = key
      self._settings = phone_notifications.read_settings(self.settings_path)
    return self._settings

  def poll(self):
    try:
      value = toast_from(self.read(), self._read_settings())
    except Exception:
      value = None
    with self._lock:
      self._value = value

  def snapshot(self):
    with self._lock:
      return self._value

  def _run(self):
    while not self._stop.is_set():
      self.poll()
      self._stop.wait(0.25)

  def close(self):
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=2)


def fit(fonts, role, size, text, width):
  """The longest prefix of text (with an ellipsis when cut) that measures within width."""
  if fonts.measure(text, role, size).width <= width:
    return text
  low, high = 0, len(text)
  while low < high:
    middle = (low + high + 1) // 2
    if fonts.measure(text[:middle].rstrip() + '...', role, size).width <= width:
      low = middle
    else:
      high = middle - 1
  return text[:low].rstrip() + '...' if low else ''


def wrap(fonts, role, size, text, width, lines=2):
  """Word wrap into at most lines rows, the last one cut to fit."""
  rows, rest = [], text
  while rest and len(rows) < lines - 1:
    if fonts.measure(rest, role, size).width <= width:
      break
    words, row = rest.split(' '), ''
    for word in words:
      candidate = f'{row} {word}' if row else word
      if fonts.measure(candidate, role, size).width > width:
        break
      row = candidate
    if not row:
      break
    rows.append(row)
    rest = rest[len(row):].lstrip()
  if rest:
    rows.append(fit(fonts, role, size, rest, width))
  return tuple(rows)


def toast_visible(toast, now_ns, *, started, speed):
  if toast is None or not 0 < toast.observed <= now_ns <= toast.observed + phone_notifications.TOAST_NS:
    return False
  if toast.hide_while_moving and started and (speed is None or speed > 0.5):
    return False
  return True


class ToastPainter:
  """Draws the toast centered at the top of a screen, measuring text once per toast."""
  def __init__(self):
    self._layout = None

  def draw(self, rl, fonts, roles, toast, screen_width, screen_height):
    scale = min(screen_width / 1920, screen_height / 1080)
    # Between the default speed-limit card (ends x=576) and the steering wheel.
    width = max(560 * scale, min(screen_width * 0.6, 1200 * scale, screen_width - 1200))
    pad = 26 * scale
    app_size, sender_size, text_size = 24 * scale, 36 * scale, 30 * scale
    key = toast.sequence, toast.observed, round(width)
    if self._layout is None or self._layout[0] != key:
      inner = width - 2 * pad
      self._layout = (key, fit(fonts, roles.MEDIUM, app_size, toast.app, inner),
                      fit(fonts, roles.BOLD, sender_size, toast.sender, inner) if toast.sender else '',
                      wrap(fonts, roles.MEDIUM, text_size, toast.text, inner) if toast.text else ())
    _, app, sender, lines = self._layout
    step = 1.35
    height = 2 * pad + app_size * step + (sender_size * step if sender else 0) + len(lines) * text_size * step
    x, y = (screen_width - width) / 2, 24 * scale
    panel = rl.Rectangle(x, y, width, height)
    rl.draw_rectangle_rounded(panel, 0.25, 12, rl.Color(14, 12, 24, 238))
    rl.draw_rectangle_rounded_lines_ex(panel, 0.25, 12, max(2, 3 * scale), rl.Color(160, 126, 220, 200))
    y += pad
    fonts.draw(app, roles.MEDIUM, app_size, x + pad, y, rl.Color(178, 170, 200, 255))
    y += app_size * step
    if sender:
      fonts.draw(sender, roles.BOLD, sender_size, x + pad, y, rl.Color(255, 255, 255, 255))
      y += sender_size * step
    for line in lines:
      fonts.draw(line, roles.MEDIUM, text_size, x + pad, y, rl.Color(226, 220, 244, 255))
      y += text_size * step
