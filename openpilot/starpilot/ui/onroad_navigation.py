from collections import OrderedDict
from datetime import datetime, timedelta
import hashlib

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.navigation_icons import ASSETS, MANIFEST, icon_name
from openpilot.starpilot.ui.navigation_state import distance_text
from openpilot.starpilot.ui.presentation import FontRole, Profile


def _overlaps_slc_actions(rect, profile, state) -> bool:
  from openpilot.starpilot.ui.onroad_state import slc_action_occludes
  return slc_action_occludes(profile, state, (rect.x, rect.y, rect.x + rect.width, rect.y + rect.height))


def arrival_time(remaining_seconds: float, now: datetime) -> str:
  arrival = now + timedelta(seconds=max(0, remaining_seconds))
  return arrival.strftime('%I:%M %p').lstrip('0')


class NavigationCard:
  def __init__(self, fonts, *, clock=None):
    self.fonts = fonts
    self.clock = clock or (lambda: datetime.now().astimezone())
    self.collapsed = False
    self._key = None
    self._press = None
    self._textures = OrderedDict()
    self._text_layout = None

  def bounds(self, state):
    if (state.navigation is None or state.alert.size != 'none' or state.reverse_driver_camera or
        state.appearance.camera_view in (CameraViewChoice.DRIVER, CameraViewChoice.NONE)):
      return None
    if state.navigation.key != self._key:
      self._key, self.collapsed = state.navigation.key, False
    rect = self._placed(state)
    # A live SLC decision keeps its touch priority only where its buttons actually sit on the card.
    if rect is not None and _overlaps_slc_actions(rect, self.fonts.profile, state):
      return None
    return rect

  def _placed(self, state):
    if self.fonts.profile == Profile.COMPACT:
      return rl.Rectangle(244, 18, 72, 72) if self.collapsed else rl.Rectangle(96, 16, 368, 142)
    # The card follows the right edge; legacy callers without a placement keep its fixed spot.
    placed = state.customization["layouts"]["large"].get("nav_card")
    if placed is not None and not placed["enabled"]:
      return None
    x, y = (placed["x"], placed["y"]) if placed is not None else (1230, 415)
    x += state.viewport_width - 1860
    return rl.Rectangle(x + 448, y, 112, 112) if self.collapsed else rl.Rectangle(x, y, 560, 195)

  def press(self, x, y, state):
    self.cancel()
    rect = self.bounds(state)
    if rect is not None and rect.x <= x <= rect.x + rect.width and rect.y <= y <= rect.y + rect.height:
      self._press = (x, y, state.navigation.key)
      return True
    return False

  def move(self, x, y, state):
    if self._press is not None:
      px, py, key = self._press
      if abs(x - px) > 5 or abs(y - py) > 5 or self.bounds(state) is None or state.navigation.key != key:
        self.cancel()

  def release(self, x, y, state):
    self.move(x, y, state)
    if self._press is not None:
      self.collapsed = not self.collapsed
    self.cancel()

  def cancel(self):
    self._press = None

  def _icon(self, name):
    if name not in self._textures:
      data = (ASSETS / name).read_bytes()
      expected = MANIFEST[name]
      if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
        raise ValueError('Navigation artwork differs from its manifest')
      texture = rl.load_texture(str(ASSETS / name))
      if not texture.id:
        raise RuntimeError('Unable to load navigation artwork')
      rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      self._textures[name] = texture
      if len(self._textures) > 8:
        rl.unload_texture(self._textures.popitem(last=False)[1])
    self._textures.move_to_end(name)
    return self._textures[name]

  def _lines(self, text, width, size):
    key = (text, width, size)
    if self._text_layout is not None and self._text_layout[0] == key:
      return self._text_layout[1]
    words, lines, line = text.split(), [], ''
    for word in words:
      candidate = f'{line} {word}'.strip()
      if line and self.fonts.measure(candidate, FontRole.SEMI_BOLD, size).width > width:
        lines.append(line)
        line = word
      else:
        line = candidate
    lines.append(line)
    if len(lines) > 2:
      lines = lines[:2]
      lines[-1] += '...'
    for i, line in enumerate(lines):
      while len(line) > 3 and self.fonts.measure(line, FontRole.SEMI_BOLD, size).width > width:
        line = line[:-4] + '...' if line.endswith('...') else line[:-1] + '...'
      lines[i] = line
    self._text_layout = key, tuple(lines)
    return self._text_layout[1]

  def render(self, state):
    rect = self.bounds(state)
    if rect is None:
      self.cancel()
      return
    nav = state.navigation
    compact = self.fonts.profile == Profile.COMPACT
    rl.draw_rectangle_rounded(rect, .2, 8, rl.Color(15, 13, 23, 255 if compact else 245))
    rl.draw_rectangle_rounded_lines_ex(rect, .2, 8, 2, rl.Color(160, 126, 220, 190))
    size = (52 if compact else 80) if self.collapsed else (68 if compact else 100)
    padding = 10 if self.collapsed else (18 if compact else 24)
    icon = self._icon(icon_name('arrive' if nav.arrived else nav.maneuver_type, nav.modifier))
    rl.draw_texture_pro(icon, rl.Rectangle(0, 0, icon.width, icon.height),
                        rl.Rectangle(rect.x + padding, rect.y + (rect.height - size) / 2, size, size), rl.Vector2(0, 0), 0, rl.WHITE)
    if self.collapsed:
      return
    x, y = rect.x + padding * 2 + size, rect.y + (14 if compact else 22)
    width = rect.x + rect.width - padding - x
    primary_size, secondary_size = (28, 22) if compact else (40, 30)
    for i, text in enumerate(self._lines(nav.text, width, primary_size)):
      self.fonts.draw(text, FontRole.SEMI_BOLD, primary_size, x, y + i * (primary_size + 3))
    distance = 'Arrived' if nav.arrived else distance_text(nav.distance_m, state.metric)
    self.fonts.draw(distance, FontRole.BOLD, secondary_size, x, rect.y + rect.height - (58 if compact else 85),
                    rl.Color(199, 174, 247, 255))
    if not nav.arrived:
      now = self.clock()
      font_size = 22 if compact else 32
      y = rect.y + rect.height - (30 if compact else 44)
      if now is not None:
        remaining = f'ETA {arrival_time(nav.remaining_seconds, now)}'
        measured = self.fonts.measure(remaining, FontRole.SEMI_BOLD, font_size)
        self.fonts.draw(remaining, FontRole.SEMI_BOLD, font_size, rect.x + rect.width - padding - measured.width,
                        y, rl.Color(235, 225, 255, 255))
      if not compact:
        trip = f'{distance_text(nav.remaining_distance_m, state.metric)} | {max(1, round(nav.remaining_seconds / 60))} min'
        self.fonts.draw(trip, FontRole.NORMAL, 20, rect.x + padding, y + 8, rl.Color(190, 187, 197, 255))

  def close(self):
    for texture in self._textures.values():
      rl.unload_texture(texture)
    self._textures.clear()
