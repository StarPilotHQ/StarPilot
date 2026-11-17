from collections import OrderedDict
from datetime import datetime, timedelta
import hashlib

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.navigation_icons import ASSETS, MANIFEST, icon_name
from openpilot.starpilot.ui.navigation_state import distance_text
from openpilot.starpilot.ui.onroad_customization import NAV_CARD_SIZE
from openpilot.starpilot.ui.presentation import FontRole, Profile


FAR_TURN_M = 1500.
THEN_MAX_GAP_M = 400.
LANES_SHOW_M = 800.


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
    self._total = 0.

  def bounds(self, state):
    if (state.navigation is None or state.alert.size != 'none' or state.reverse_driver_camera or
        state.appearance.camera_view in (CameraViewChoice.DRIVER, CameraViewChoice.NONE)):
      return None
    if state.navigation.key != self._key:
      self._key, self.collapsed, self._total = state.navigation.key, False, 0.
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
    x, y = (placed["x"], placed["y"]) if placed is not None else (1230, 355)
    x += state.viewport_width - 1860
    width, height = NAV_CARD_SIZE
    return rl.Rectangle(x + width - 112, y, 112, 112) if self.collapsed else rl.Rectangle(x, y, width, height)

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
      if len(self._textures) > 16:
        rl.unload_texture(self._textures.popitem(last=False)[1])
    self._textures.move_to_end(name)
    return self._textures[name]

  def _lines(self, text, width, size, max_lines=2):
    key = (text, width, size, max_lines)
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
    if len(lines) > max_lines:
      lines = lines[:max_lines]
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
    icon = self._icon(icon_name('arrive' if nav.arrived else nav.maneuver_type, nav.modifier))
    if self.collapsed:
      size = 64 if compact else 100
      rl.draw_texture_pro(icon, rl.Rectangle(0, 0, icon.width, icon.height),
                          rl.Rectangle(rect.x + (rect.width - size) / 2, rect.y + (rect.height - size) / 2, size, size),
                          rl.Vector2(0, 0), 0, rl.WHITE)
      return
    self._total = max(self._total, nav.remaining_distance_m)
    padding = 18 if compact else 24
    lanes = () if nav.arrived or nav.distance_m > LANES_SHOW_M else nav.lanes
    header = rect.height * ((.52 if lanes else .64) if compact else (.55 if lanes else .66))
    size = min(84 if compact else 122, header - 6)
    if not lanes:
      rl.draw_texture_pro(icon, rl.Rectangle(0, 0, icon.width, icon.height),
                          rl.Rectangle(rect.x + padding, rect.y + (header - size) / 2, size, size), rl.Vector2(0, 0), 0, rl.WHITE)
    x = rect.x + padding * (1 if lanes else 2) + (0 if lanes else size)
    width = rect.x + rect.width - padding - x
    distance_size, text_size = (32, 20) if compact else (66, 38)
    distance = 'Arrived' if nav.arrived else distance_text(nav.distance_m, state.metric)
    far = not nav.arrived and nav.distance_m > FAR_TURN_M
    y = rect.y + (8 if compact else 12)
    self.fonts.draw(distance, FontRole.BOLD, distance_size, x, y, rl.WHITE)
    y += distance_size + (2 if compact else 4)
    lines = self._lines(nav.text, width, text_size, max_lines=1 if far or lanes else 2)
    for i, text in enumerate(lines):
      self.fonts.draw(text, FontRole.SEMI_BOLD, text_size, x, y + i * (text_size + 2), rl.Color(222, 214, 244, 255))
    if nav.arrived:
      return
    top = rect.y + header
    now = self.clock()
    eta = f'ETA {arrival_time(nav.remaining_seconds, now)}' if now is not None else ''
    right = rect.x + rect.width - padding
    bar_center = rect.y + rect.height - (11 if compact else 21)
    eta_size = 19 if compact else 32
    bar_right = right
    if eta:
      eta_width = self.fonts.measure(eta, FontRole.SEMI_BOLD, eta_size).width
      self.fonts.draw(eta, FontRole.SEMI_BOLD, eta_size, right - eta_width, bar_center - eta_size * (.55 if compact else .6), rl.Color(235, 225, 255, 255))
      bar_right -= eta_width + (12 if compact else 18)
    if lanes:
      lane_top = top + (6 if compact else 9)
      self._lane_strip(lanes, rect.x + padding, lane_top, right, bar_center - (8 if compact else 12) - 6 - lane_top)
    else:
      chip = 20 if compact else 28
      self._then(nav, rect.x + padding, top + 7, chip)
      if not compact:
        trip = f'{distance_text(nav.remaining_distance_m, state.metric)} | {max(1, round(nav.remaining_seconds / 60))} min'
        trip_size = 32
        trip_width = self.fonts.measure(trip, FontRole.MEDIUM, trip_size).width
        self.fonts.draw(trip, FontRole.MEDIUM, trip_size, right - trip_width, top, rl.Color(214, 210, 226, 255))
    if self._total > 0:
      thick = 5 if compact else 6
      bar = rl.Rectangle(rect.x + padding, bar_center - thick / 2, bar_right - (rect.x + padding), thick)
      rl.draw_rectangle_rounded(bar, 1., 4, rl.Color(160, 126, 220, 60))
      done = min(1., max(0., 1 - nav.remaining_distance_m / self._total))
      if done > 0:
        rl.draw_rectangle_rounded(rl.Rectangle(bar.x, bar.y, max(thick, bar.width * done), thick), 1., 4, rl.Color(178, 150, 255, 255))

  def _lane_strip(self, lanes, x, y, right, height):
    """One cell per lane with the maneuver artwork for its arrow: usable lanes lit, the rest dim."""
    gap = max(4., height * .12)
    cell = min(height * 1.2, (right - x + gap) / len(lanes) - gap)
    x += (right - x - (cell + gap) * len(lanes) + gap) / 2
    size = height * .8
    for i, (active, directions, chosen) in enumerate(lanes):
      box = rl.Rectangle(x + i * (cell + gap), y, cell, height)
      rl.draw_rectangle_rounded(box, .3, 6, rl.Color(78, 62, 128, 255) if active else rl.Color(34, 30, 46, 255))
      direction = chosen if active and chosen else (directions[0] if directions else 'straight')
      icon = self._icon(icon_name('turn', direction))
      rl.draw_texture_pro(icon, rl.Rectangle(0, 0, icon.width, icon.height),
                          rl.Rectangle(box.x + (box.width - size) / 2, box.y + (box.height - size) / 2, size, size),
                          rl.Vector2(0, 0), 0, rl.WHITE if active else rl.Color(112, 104, 134, 255))

  def _then(self, nav, x, y, size):
    """A "Then <arrow>" chip when the turn after this one comes soon after it."""
    if not nav.then_type or nav.then_distance_m is None:
      return
    gap = nav.then_distance_m - nav.distance_m
    if not 1 < gap <= THEN_MAX_GAP_M:
      return
    label = 'Then'
    label_width = self.fonts.measure(label, FontRole.SEMI_BOLD, size - 4).width
    chip = rl.Rectangle(x, y - 4, label_width + size + 28, size + 8)
    rl.draw_rectangle_rounded(chip, .5, 8, rl.Color(44, 37, 70, 255))
    self.fonts.draw(label, FontRole.SEMI_BOLD, size - 4, x + 12, y + 1, rl.Color(199, 174, 247, 255))
    icon = self._icon(icon_name(nav.then_type, nav.then_modifier))
    rl.draw_texture_pro(icon, rl.Rectangle(0, 0, icon.width, icon.height),
                        rl.Rectangle(x + label_width + 18, y - 1, size, size), rl.Vector2(0, 0), 0, rl.WHITE)

  def close(self):
    for texture in self._textures.values():
      rl.unload_texture(texture)
    self._textures.clear()
