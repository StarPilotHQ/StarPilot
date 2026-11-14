"""The Android Auto now-playing card; both live rendering and previews use this drawing.

Hit boxes, NowPlayingTouch, cover art, TrackText, scrolling names, the control glyphs and the
'Play on StarPilot' take-over are adapted from Peter Nguyen's now-playing card.
"""
import math
import time
import unicodedata
from functools import cache
from importlib.resources import as_file, files

import numpy as np

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.soloist.now_playing import ELSEWHERE, elapsed_ms
from openpilot.starpilot.system.starport.projection_layout import NOW_PLAYING_WIDGET, now_playing_size
from openpilot.starpilot.ui import clip
from openpilot.starpilot.ui.presentation import FontRole, TextSize

PRIMARY = rl.Color(157, 114, 255, 255)
BT_BLUE = rl.Color(96, 165, 250, 255)
ACCENT = rl.Color(199, 174, 247, 255)
INK = rl.Color(15, 13, 23, 255)
TEXT = rl.Color(245, 242, 252, 255)
MUTED = rl.Color(214, 210, 224, 255)
TIMES = rl.Color(184, 178, 200, 255)
DIM = rl.Color(214, 210, 224, 77)
DISC = rl.Color(255, 255, 255, 36)
CHIP = rl.Color(0, 0, 0, 89)
BORDER = rl.Color(255, 255, 255, 31)
WASH = (27, 20, 56)
COVER_TINT = (80, 78, 92)

CONTROLS = ('shuffle', 'previous', 'play_pause', 'next', 'repeat')
REPEAT_NEXT = {'off': 'context', 'context': 'track', 'track': 'off'}
BIG_RADIUS = 40
PAD = 40
SLOT = 96
ROW_TOP = 160
ROW_Y = 208
BUTTON_R = 32
PLAY_R = 36
COLLAPSED = 96
PLAY_HERE = (320, 64)
SCROLL_SPEED = 40
SCROLL_GAP = 60
SCROLL_PAUSE = 2.0
HOLD = 4.0
PENDING = 3.0
PRESS_FADE = .35
PRESS_MAX = 2.0
TRACK_FONTS = {FontRole.SEMI_BOLD: 'Inter-SemiBold.ttf', FontRole.NORMAL: 'Inter-Regular.ttf'}
TRACK_FONT_PX = 48
ASCII = range(32, 127)


def _kind(placed, track):
  if track['status'] == ELSEWHERE:
    return 'elsewhere'
  if track['status'] != 'playing' and placed.get('collapse'):
    return 'collapsed'
  return 'big' if placed.get('size') == 'big' else 'small'


def card_layout(placed, track, viewport_width):
  """(card rect, {control: hit rect}) for what is drawn, or None. Rendering and taps share it."""
  if not placed or not placed['enabled'] or not track:
    return None
  rect = rl.Rectangle(placed['x'], placed['y'], *now_playing_size(placed))
  kind, big = _kind(placed, track), placed.get('size') == 'big'
  if kind == 'elsewhere':
    if not big:
      return rect, {'play_here': rect}
    button = rl.Rectangle(rect.x + (rect.width - PLAY_HERE[0]) / 2, rect.y + 140, *PLAY_HERE)
    return rect, {'play_here': button}
  if kind == 'collapsed':
    right = rect.x + rect.width / 2 > viewport_width / 2
    button = rl.Rectangle(rect.x + rect.width - COLLAPSED if right else rect.x, rect.y + rect.height - COLLAPSED, COLLAPSED, COLLAPSED)
    return button, {'play_pause': button}
  if kind == 'small':
    return rect, {'play_pause': rl.Rectangle(rect.x, rect.y, rect.height, rect.height)}
  left = rect.x + rect.width / 2 - SLOT * len(CONTROLS) / 2
  return rect, {name: rl.Rectangle(left + index * SLOT, rect.y + ROW_TOP, SLOT, rect.height - ROW_TOP)
                for index, name in enumerate(CONTROLS)}


def _alpha(color, alpha: float) -> rl.Color:
  return rl.Color(color.r, color.g, color.b, round(color.a * alpha))


def _center(box: rl.Rectangle) -> rl.Vector2:
  return rl.Vector2(box.x + box.width / 2, box.y + box.height / 2)


def _play(c: rl.Vector2, k: float, color) -> None:
  rl.draw_triangle(rl.Vector2(c.x - 8 * k, c.y - 14 * k), rl.Vector2(c.x - 8 * k, c.y + 14 * k), rl.Vector2(c.x + 14 * k, c.y), color)


def _pause(c: rl.Vector2, k: float, color) -> None:
  for dx in (-10, 3):
    rl.draw_rectangle_rounded(rl.Rectangle(c.x + dx * k, c.y - 13 * k, 7 * k, 26 * k), .5, 4, color)


def _spinner(c: rl.Vector2, k: float, now: float, color) -> None:
  start = now * 360 % 360
  rl.draw_ring(c, 13 * k, 17 * k, start, start + 270, 24, color)


def _play_pause(c: rl.Vector2, r: float, playing: bool, busy_at: float | None = None) -> None:
  """A white disc with a dark play or pause glyph; a turning arc while the player is busy."""
  rl.draw_circle_v(c, r, TEXT)
  k = r / PLAY_R
  if busy_at is not None:
    _spinner(c, k, busy_at, INK)
  elif playing:
    _pause(c, k, INK)
  else:
    _play(c, k, INK)


def _skip(c: rl.Vector2, forward: bool) -> None:
  rl.draw_circle_v(c, BUTTON_R, DISC)
  d = 1 if forward else -1
  tip, back = c.x + 9 * d, c.x - 9 * d
  points = (rl.Vector2(back, c.y - 12), rl.Vector2(back, c.y + 12), rl.Vector2(tip, c.y))
  rl.draw_triangle(*(points if forward else (points[1], points[0], points[2])), TEXT)
  rl.draw_rectangle_rounded(rl.Rectangle(tip + (0 if forward else -5), c.y - 12, 5, 24), .5, 4, TEXT)


def _toggle_color(state, on):
  return DIM if state is None else ACCENT if on else MUTED


def _bezier(p0, p1, p2, p3, steps=8):
  return tuple(tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t * t * c + t ** 3 * d
                     for a, b, c, d in zip(p0, p1, p2, p3, strict=True))
               for t in (i / steps for i in range(steps + 1)))


SHUFFLE_PATHS = tuple(path for y0, y1 in ((-10, 10), (10, -10))
                      for path in (_bezier((-16, y0), (0, y0), (0, y1), (18, y1)), ((13, y1 - 6), (19, y1), (13, y1 + 6))))


def _stroke(points, thick, color) -> None:
  for a, b in zip(points, points[1:], strict=False):
    rl.draw_line_ex(a, b, thick, color)
  for point in points[::len(points) - 1]:
    rl.draw_circle_v(point, thick / 2, color)


def _shuffle(c: rl.Vector2, state) -> None:
  color = _toggle_color(state, state)
  for path in SHUFFLE_PATHS:
    _stroke([rl.Vector2(c.x + dx, c.y + dy) for dx, dy in path], 3.5, color)
  if state:
    rl.draw_circle_v(rl.Vector2(c.x, c.y + 26), 3, color)


def _repeat(c: rl.Vector2, state) -> None:
  color = _toggle_color(state, state in ('context', 'track'))
  x, y = c.x, c.y
  rl.draw_line_ex(rl.Vector2(x - 14, y - 9), rl.Vector2(x + 12, y - 9), 4, color)
  rl.draw_line_ex(rl.Vector2(x + 14, y + 9), rl.Vector2(x - 12, y + 9), 4, color)
  rl.draw_line_ex(rl.Vector2(x - 16, y - 11), rl.Vector2(x - 16, y + 4), 4, color)
  rl.draw_line_ex(rl.Vector2(x + 16, y + 11), rl.Vector2(x + 16, y - 4), 4, color)
  rl.draw_triangle(rl.Vector2(x + 10, y - 16), rl.Vector2(x + 10, y - 2), rl.Vector2(x + 19, y - 9), color)
  rl.draw_triangle(rl.Vector2(x - 10, y + 16), rl.Vector2(x - 10, y + 2), rl.Vector2(x - 19, y + 9), color)
  if state == 'track':
    rl.draw_rectangle_rounded(rl.Rectangle(x - 2, y - 5, 4, 10), .5, 4, color)
  if state in ('context', 'track'):
    rl.draw_circle_v(rl.Vector2(x, y + 26), 3, color)


def _bluetooth(c: rl.Vector2, k: float, color) -> None:
  top, bottom, side, thick = rl.Vector2(c.x, c.y - 11 * k), rl.Vector2(c.x, c.y + 11 * k), 6 * k, 2.4 * k
  _stroke([rl.Vector2(c.x - side, c.y - 6 * k), rl.Vector2(c.x + side, c.y + 5.5 * k), bottom, top,
           rl.Vector2(c.x + side, c.y - 5.5 * k), rl.Vector2(c.x - side, c.y + 6 * k)], thick, color)


def _note(c: rl.Vector2, k: float, color) -> None:
  rl.draw_circle_v(rl.Vector2(c.x - 3 * k, c.y + 7 * k), 5 * k, color)
  rl.draw_rectangle_rec(rl.Rectangle(c.x, c.y - 11 * k, 2.6 * k, 18 * k), color)
  rl.draw_rectangle_rec(rl.Rectangle(c.x, c.y - 11 * k, 8 * k, 3.2 * k), color)


def _speaker(c: rl.Vector2, k: float, color) -> None:
  x, y = c.x - 3 * k, c.y
  rl.draw_rectangle_rec(rl.Rectangle(x - 10 * k, y - 5 * k, 5 * k, 10 * k), color)
  a, b, cc, d = (rl.Vector2(x - 5 * k, y - 5 * k), rl.Vector2(x + 4 * k, y - 12 * k),
                 rl.Vector2(x + 4 * k, y + 12 * k), rl.Vector2(x - 5 * k, y + 5 * k))
  rl.draw_triangle(a, d, cc, color)
  rl.draw_triangle(a, cc, b, color)
  rl.draw_ring(rl.Vector2(x + 6 * k, y), 6 * k, 8.5 * k, -50, 50, 10, color)


def _source_glyph(c: rl.Vector2, k: float, source: str) -> None:
  if source == 'phone':
    _bluetooth(c, k, BT_BLUE)
  else:
    _note(c, k, ACCENT)


def clock_text(ms):
  seconds = max(0, int(ms // 1000))
  return f'{seconds // 60}:{seconds % 60:02d}'


def scroll_offset(text_width, width, seconds):
  """How far a too-wide line has scrolled left: still for SCROLL_PAUSE, then one pass to where its repeat starts."""
  if text_width <= width:
    return 0.0
  travel = text_width + SCROLL_GAP
  return min(travel, max(0.0, seconds % (SCROLL_PAUSE + travel / SCROLL_SPEED) - SCROLL_PAUSE) * SCROLL_SPEED)


@cache
def pill_rows(height, r, step=2):
  """(y, height, inset) strips that fill a rounded rect, so the cover takes the card's shape without a shader."""
  r = min(r, height / 2)
  rows = []
  for top in (True, False):
    if not top and height > 2 * r:
      rows.append((r, height - 2 * r, 0.0))
    y = 0.0
    while y < r:
      h = min(step, r - y)
      dy = r - (y + h / 2)
      rows.append(((y if top else height - y - h), h, r - math.sqrt(r * r - dy * dy)))
      y += h
  return tuple(rows)


def nfc(text):
  return unicodedata.normalize('NFC', text)


class TrackText:
  """Song and device names. The bitmap atlases hold ASCII only, so other names are drawn from the Inter TTF
  cut to ASCII plus that name's letters, one atlas per slot, replaced when the name changes."""

  def __init__(self, fonts):
    self.fonts = fonts
    self._cut = {}

  def _font(self, text, role, slot):
    covers = getattr(self.fonts, 'covers', None)
    if covers is None or role not in TRACK_FONTS:
      return None
    key = (role, slot)
    cut = self._cut.get(key)
    if cut is None or cut[0] != text:
      self._unload(key)
      self._cut[key] = (text, None if covers(text, role) else self._load(text, role))
    return self._cut[key][1]

  @staticmethod
  def _load(text, role):
    codepoints = sorted({*ASCII, *map(ord, text)})
    glyphs = np.array(codepoints, dtype=np.int32)
    with as_file(files("openpilot.selfdrive").joinpath("assets/fonts", TRACK_FONTS[role])) as path:
      font = rl.load_font_ex(str(path), TRACK_FONT_PX, glyphs, len(codepoints))
    if font.texture.id in (0, rl.get_font_default().texture.id) or font.glyphCount == 0:
      return None
    rl.gen_texture_mipmaps(font.texture)
    rl.set_texture_filter(font.texture, rl.TextureFilter.TEXTURE_FILTER_TRILINEAR)
    return font

  def _unload(self, key):
    cut = self._cut.pop(key, None)
    if cut is not None and cut[1] is not None and rl.is_window_ready():
      rl.rl_draw_render_batch_active()  # draws already queued with this atlas
      rl.unload_font(cut[1])

  def close(self):
    for key in list(self._cut):
      self._unload(key)

  def measure(self, text, role, size, slot=None):
    if (font := self._font(text, role, slot)) is None:
      return self.fonts.measure(text, role, size)
    measured = rl.measure_text_ex(font, text, size * self.fonts.profile.font_scale, 0)  # noqa: TID251
    return TextSize(measured.x, measured.y)

  def draw(self, text, role, size, x, y, color, slot=None):
    if (font := self._font(text, role, slot)) is None:
      self.fonts.draw(text, role, size, x, y, color)
      return
    draw = getattr(rl, "_orig_draw_text_ex", rl.draw_text_ex)
    draw(font, text, rl.Vector2(x, y), size * self.fonts.profile.font_scale, 0, color)


def _song_key(track):
  return track.get('source'), track.get('title'), track.get('artist'), track.get('status')


class NowPlayingWidget:
  def __init__(self, fonts, clock=time.time, ticks=time.monotonic):  # noqa: TID251 epoch-ms track anchors
    self.fonts = fonts
    self.text = TrackText(fonts)
    self.clock = clock
    self.ticks = ticks
    self.track = None
    self.artwork: tuple[str, tuple[int, int, bytes] | None] = ('', None)
    self._cover = None  # (url, texture or None)
    self._scroll = {}
    self._shown = None
    self._press = None
    self._pending = None

  def press(self, name):
    self._press = (name, self.ticks(), None)

  def release(self, fired):
    now = self.ticks()
    if self._press is not None:
      self._press = (self._press[0], self._press[1], now)
    if fired in ('previous', 'next', 'play_pause', 'play_here') and (track := self.showing()):
      self._pending = (_song_key(track), now)

  def showing(self):
    """The feed's song, or the last one for HOLD seconds so a skip does not blink the card out."""
    if self.track:
      return self.track
    if self._shown is not None and self.ticks() - self._shown[1] < HOLD:
      return self._shown[0]
    return None

  def close(self):
    self._drop_cover()
    self.text.close()

  def _drop_cover(self):
    if self._cover is not None and self._cover[1] is not None and rl.is_window_ready():
      rl.rl_draw_render_batch_active()
      rl.unload_texture(self._cover[1])
    self._cover = None

  def _cover_texture(self):
    url, data = self.artwork
    if not url and self.track is None and self._cover is not None:
      return self._cover[1]  # between songs: keep the last cover rather than flash the plain card
    if self._cover is not None and self._cover[0] == url:
      return self._cover[1]
    self._drop_cover()
    texture = None
    if url and data:
      width, height, pixels = data
      texture = rl.load_texture_from_image(rl.Image(None, width, height, 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8))
      if texture.id:
        rl.update_texture(texture, pixels)
        rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      else:
        texture = None
    self._cover = (url, texture)
    return texture

  def _busy(self, track, now):
    if track is not self.track:
      return True
    if self._pending is not None and (_song_key(track) != self._pending[0] or now - self._pending[1] >= PENDING):
      self._pending = None
    return self._pending is not None

  def _pressed(self, name, now):
    """1 while held on ``name``, fading after the lift, else 0."""
    if self._press is None or self._press[0] != name:
      return 0.0
    down, up = self._press[1:]
    if up is None:
      return 1.0 if now - down < PRESS_MAX else 0.0
    return max(0.0, 1 - (now - up) / PRESS_FADE)

  def _scrolling(self, slot, text, line_width, draw, x, y, width, height):
    """``draw(left)`` once when the line fits ``width``; otherwise scroll it left inside a clip, then repeat."""
    if line_width <= width:
      draw(x)
      return
    now = self.ticks()
    held = self._scroll.get(slot)
    if held is None or held[0] != text:
      held = self._scroll[slot] = (text, now)
    offset = scroll_offset(line_width, width, now - held[1])
    clip.begin_scissor_mode(x, y, width, height)
    try:
      for start in (x - offset, x - offset + line_width + SCROLL_GAP):
        if start < x + width:
          draw(start)
    finally:
      clip.end_scissor_mode()

  def _line(self, text, role, size, x, cy, width, color, slot):
    """One name, vertically centered on ``cy``, scrolling when wider than ``width``."""
    measured = self.text.measure(text, role, size, slot)
    top = cy - measured.height / 2
    self._scrolling(slot, text, measured.width, lambda left: self.text.draw(text, role, size, left, top, color, slot),
                    x, top - 4, width, measured.height + 8)

  def _background(self, rect, radius, opacity, texture):
    if texture is None:
      rl.draw_rectangle_rounded(rect, min(1.0, 2 * radius / min(rect.width, rect.height)), 24, rl.Color(*WASH, round(255 * opacity)))
      return
    tint = rl.Color(*COVER_TINT, round(255 * opacity))
    band = texture.height * rect.height / rect.width
    scale = texture.width / rect.width
    top = (texture.height - band) / 2
    for y, height, inset in pill_rows(rect.height, radius):
      rl.draw_texture_pro(texture, rl.Rectangle(inset * scale, top + y * scale, (rect.width - 2 * inset) * scale, height * scale),
                          rl.Rectangle(rect.x + inset, rect.y + y, rect.width - 2 * inset, height), rl.Vector2(0, 0), 0, tint)

  def _press_disc(self, center, r, name, now):
    if (pressed := self._pressed(name, now)) > 0:
      rl.draw_circle_v(center, r + 8, rl.Color(255, 255, 255, round(60 * pressed)))

  def render(self, state):
    placed = state.customization['layouts']['large'].get(NOW_PLAYING_WIDGET)
    now = self.ticks()
    if self.track:
      self._shown = (self.track, now) if self.track['status'] != ELSEWHERE else None
    track = self.showing()
    if state.alert.size != 'none' or (shape := card_layout(placed, track, getattr(state, 'viewport_width', 0))) is None:
      return
    rect, buttons = shape
    opacity = placed.get('opacity', 85) / 100
    kind = _kind(placed, track)
    if kind == 'collapsed':
      center = _center(rect)
      rl.draw_circle_v(center, rect.width / 2, rl.Color(*WASH, round(255 * opacity)))
      self._press_disc(center, PLAY_R, 'play_pause', now)
      _play_pause(center, PLAY_R, False, now if self._busy(track, now) else None)
    elif kind == 'elsewhere':
      self._elsewhere(rect, buttons['play_here'], track, opacity, now, placed.get('size') == 'big')
    elif kind == 'small':
      self._small(rect, track, opacity, now)
    else:
      self._big(rect, buttons, track, opacity, now)

  def _source_label(self, track):
    return 'Phone' if track.get('source') == 'phone' else nfc(track.get('device') or 'StarPilot')

  def _chip(self, x, cy, track):
    label = self._source_label(track)
    measured = self.text.measure(label, FontRole.SEMI_BOLD, 18, 'chip')
    chip = rl.Rectangle(x, cy - 18, 14 + 20 + 8 + measured.width + 14, 36)
    rl.draw_rectangle_rounded(chip, 1.0, 16, CHIP)
    rl.draw_rectangle_rounded_lines_ex(chip, 1.0, 16, 1, BORDER)
    _source_glyph(rl.Vector2(x + 24, cy), .85, track.get('source'))
    self.text.draw(label, FontRole.SEMI_BOLD, 18, x + 42, cy - measured.height / 2, TEXT, 'chip')

  def _big(self, rect, buttons, track, opacity, now):
    texture = self._cover_texture() if track.get('source') == 'spotify' else None
    self._background(rect, BIG_RADIUS, opacity, texture)
    self._chip(rect.x + PAD, rect.y + 38, track)
    service = 'Spotify' if track.get('source') == 'spotify' else nfc(track.get('device') or 'Bluetooth')
    service_size = self.text.measure(service, FontRole.NORMAL, 18, 'service')
    self.text.draw(service, FontRole.NORMAL, 18, rect.x + rect.width - PAD - service_size.width, rect.y + 38 - service_size.height / 2,
                   MUTED, 'service')
    self._title_line(rect, track)
    self._seek_bar(rect, track)
    row = {name: rl.Vector2(_center(buttons[name]).x, rect.y + ROW_Y) for name in CONTROLS}
    for name, center in row.items():
      self._press_disc(center, PLAY_R if name == 'play_pause' else BUTTON_R, name, now)
    _shuffle(row['shuffle'], track.get('shuffle'))
    _skip(row['previous'], False)
    _play_pause(row['play_pause'], PLAY_R, track['status'] == 'playing', now if self._busy(track, now) else None)
    _skip(row['next'], True)
    _repeat(row['repeat'], track.get('repeat'))

  def _title_line(self, rect, track):
    """"Title • Artist" centered on one line; the artist regular and muted. A line too wide scrolls."""
    title, artist = nfc(track['title']), nfc(track.get('artist') or track.get('album') or '')
    size, dot = 30, 32
    title_size = self.text.measure(title, FontRole.SEMI_BOLD, size, 'title')
    artist_width = self.text.measure(artist, FontRole.NORMAL, size, 'artist').width if artist else 0
    line_width = title_size.width + (dot + artist_width if artist else 0)
    width = rect.width - 2 * PAD
    cy = rect.y + 82
    top = cy - title_size.height / 2

    def draw(x):
      self.text.draw(title, FontRole.SEMI_BOLD, size, x, top, TEXT, 'title')
      if artist:
        rl.draw_circle_v(rl.Vector2(x + title_size.width + dot / 2, cy), 3.5, MUTED)
        self.text.draw(artist, FontRole.NORMAL, size, x + title_size.width + dot, top, MUTED, 'artist')

    left = rect.x + PAD if line_width > width else rect.x + rect.width / 2 - line_width / 2
    self._scrolling('line', f'{title}\n{artist}', line_width, draw, left, top - 4, width, title_size.height + 8)

  def _seek_bar(self, rect, track):
    left, right, y = rect.x + PAD, rect.x + rect.width - PAD, rect.y + 112
    rl.draw_rectangle_rounded(rl.Rectangle(left, y, right - left, 10), 1.0, 8, rl.Color(255, 255, 255, 46))
    duration = track.get('durationMs')
    if not duration:
      return
    elapsed = min(duration, elapsed_ms(track, self.clock() * 1000) if track.get('position') else 0)
    done = (right - left) * elapsed / duration
    if done > 0:
      rl.draw_rectangle_rounded(rl.Rectangle(left, y, max(10, done), 10), 1.0, 8, TEXT)
    total = clock_text(duration)
    self.fonts.draw(clock_text(elapsed), FontRole.NORMAL, 16, left, y + 16, TIMES)
    self.fonts.draw(total, FontRole.NORMAL, 16, right - self.fonts.measure(total, FontRole.NORMAL, 16).width, y + 16, TIMES)

  def _small(self, rect, track, opacity, now):
    texture = self._cover_texture() if track.get('source') == 'spotify' else None
    h = rect.height
    self._background(rect, h / 2, opacity, texture)
    disc = rl.Vector2(rect.x + h / 2, rect.y + h / 2)
    self._press_disc(disc, PLAY_R, 'play_pause', now)
    _play_pause(disc, PLAY_R, track['status'] == 'playing', now if self._busy(track, now) else None)
    left = rect.x + h + 4
    width = rect.x + rect.width - 20 - 44 - 12 - left
    self._line(nfc(track['title']), FontRole.SEMI_BOLD, 26, left, rect.y + 33, width, TEXT, 'title')
    artist = nfc(track.get('artist') or track.get('album') or '')
    if artist:
      self._line(artist, FontRole.NORMAL, 19, left, rect.y + 64, width, MUTED, 'artist')
    _source_glyph(rl.Vector2(rect.x + rect.width - 20 - 22, rect.y + h / 2), 1.3, track.get('source'))

  def _elsewhere(self, rect, button, track, opacity, now, big):
    name = nfc(track.get('device') or 'StarPilot')
    busy = self._busy(track, now)
    pressed = self._pressed('play_here', now)
    if not big:
      h = rect.height
      self._background(rect, h / 2, opacity, None)
      if pressed > 0:
        rl.draw_rectangle_rounded(rect, 1.0, 24, rl.Color(255, 255, 255, round(40 * pressed)))
      disc = rl.Vector2(rect.x + h / 2, rect.y + h / 2)
      rl.draw_circle_v(disc, PLAY_R, TEXT)
      if busy:
        _spinner(disc, 1, now, INK)
      else:
        _speaker(disc, 1.1, INK)
      width = rect.x + rect.width - 24 - (rect.x + h + 4)
      self._line('Spotify is on another device', FontRole.SEMI_BOLD, 24, rect.x + h + 4, rect.y + 34, width, TEXT, 'title')
      self._line(f'Tap to play on {name}', FontRole.NORMAL, 18, rect.x + h + 4, rect.y + 64, width, MUTED, 'artist')
      return
    self._background(rect, BIG_RADIUS, opacity, None)
    for text, role, size, cy, color, slot in (('Spotify is on another device', FontRole.SEMI_BOLD, 30, 70, TEXT, 'title'),
                                              (f'Tap to move the music to {name}', FontRole.NORMAL, 22, 108, MUTED, 'artist')):
      measured = self.text.measure(text, role, size, slot)
      x = rect.x + max(PAD, (rect.width - measured.width) / 2)
      self._line(text, role, size, x, rect.y + cy, rect.width - 2 * PAD, color, slot)
    rl.draw_rectangle_rounded(button, 1.0, 24, PRIMARY)
    if pressed > 0:
      rl.draw_rectangle_rounded(button, 1.0, 24, rl.Color(255, 255, 255, round(50 * pressed)))
    label = f'Play on {name}'
    room = button.width - 2 * 24 - 40
    content = 40 + min(room, self.text.measure(label, FontRole.SEMI_BOLD, 24, 'button').width)
    glyph = rl.Vector2(button.x + (button.width - content) / 2 + 14, button.y + button.height / 2)
    if busy:
      _spinner(glyph, .9, now, TEXT)
    else:
      _speaker(glyph, 1, TEXT)
    self._line(label, FontRole.SEMI_BOLD, 24, glyph.x + 26, glyph.y, room, TEXT, 'button')


class NowPlayingTouch:
  """A control fires when the finger lifts on the control it went down on. Taps on the card never fall through."""
  def __init__(self, send, feedback=None):
    self.send = send
    self.feedback = feedback
    self.pressed = None

  def cancel(self):
    self._lift(None)

  def _lift(self, fired):
    if self.pressed is not None and self.feedback is not None:
      self.feedback.release(fired)
    self.pressed = None

  def touch(self, kind, x, y, state, placed, track) -> bool:
    """True while the card owns the touch."""
    shape = None if kind == 'cancel' or state is None or state.alert.size != 'none' else card_layout(placed, track, state.viewport_width)
    if shape is None:
      claimed = self.pressed is not None
      self._lift(None)
      return claimed
    point = rl.Vector2(x, y)
    hit = next((name for name, box in shape[1].items() if rl.check_collision_point_rec(point, box)), None)
    if kind == 'down':
      self.pressed = hit
      if hit is not None and self.feedback is not None:
        self.feedback.press(hit)
      return hit is not None or rl.check_collision_point_rec(point, shape[0])
    if kind == 'up':
      pressed, fired = self.pressed, None
      if pressed is not None and pressed == hit:
        if pressed == 'shuffle':
          if track.get('shuffle') is not None:
            self.send('shuffle', {'enabled': not track['shuffle']})
        elif pressed == 'repeat':
          if track.get('repeat') in REPEAT_NEXT:
            self.send('repeat', {'mode': REPEAT_NEXT[track['repeat']]})
        elif pressed == 'play_here':
          self.send('activate', {})
          fired = pressed
        else:
          self.send(pressed, {})
          fired = pressed
      self._lift(fired)
      return pressed is not None or rl.check_collision_point_rec(point, shape[0])
    return self.pressed is not None
