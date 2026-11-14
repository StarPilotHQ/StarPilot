"""Home, Work and Bookmark cards shared by the AA renderer and its layout preview."""
from itertools import pairwise

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.presentation import FontRole
from openpilot.starpilot.ui.navigation_favorites_state import active_favorite, favorite_visible, map_end_rect, map_end_visible


class NavigationFavorites:
  def __init__(self, fonts):
    self.fonts = fonts
    self.document: dict | None = None
    self.error = ''

  def render(self, key, state):
    placed = state.customization['layouts']['large'].get(key)
    if placed is None or not placed['enabled'] or state.alert.size != 'none':
      return
    bookmark = key == 'bookmark'
    placements = state.customization['layouts']['large']
    if not bookmark and not favorite_visible(key, self.document, placements):
      return
    label = key.removeprefix('nav_').title()
    doc = self.document
    place = next((row for row in doc['favorites'] if row.get('label') == label.lower()), None) if doc else None
    active = not bookmark and key == active_favorite(doc, placements)
    available = bool(bookmark or active or place and doc and doc['enabled'] and doc['token'])
    action = self.error or ('End route' if active else 'Navigate' if available else 'Set in Galaxy' if place is None else 'Enable navigation')
    title = label if bookmark else action
    detail = 'Save event' if bookmark else label
    if active and doc is not None and not (place and doc['destination']['id'] == place['id']):
      detail = self.fit(doc['destination'].get('name') or 'Route', FontRole.SEMI_BOLD, 34, 290)
    icons = placed.get('display') == 'icons'
    rect = rl.Rectangle(placed['x'], placed['y'], 110 if icons else 320, 110)
    opacity = placed.get('opacity', 100) / 100
    alpha = round(255 * opacity)
    background = placed.get('background', True)
    tint = rl.Color(240, 150, 150, alpha) if active else rl.Color(199, 174, 247, alpha)
    if background:
      rl.draw_rectangle_rounded(rect, .3, 8, rl.Color(15, 13, 23, round(245 * opacity)))
      rl.draw_rectangle_rounded_lines_ex(rect, .3, 8, 3, tint if available else rl.Color(140, 140, 150, round(170 * opacity)))
    ink = tint if available else rl.Color(160, 157, 167, alpha)
    if icons:
      if active:
        for start, end in (((35, 35), (75, 75)), ((35, 75), (75, 35))):
          rl.draw_line_ex(rl.Vector2(rect.x + start[0], rect.y + start[1]),
                          rl.Vector2(rect.x + end[0], rect.y + end[1]), 6, ink)
      else:
        self.draw_icon(key, rect, ink)
      badge = '' if bookmark else '!' if self.error else '' if active else '+' if place is None else '!' if not available else ''
      if badge:
        if background:
          rl.draw_circle(int(rect.x + 91), int(rect.y + 19), 14, rl.Color(15, 13, 23, alpha))
        measured = self.fonts.measure(badge, FontRole.SEMI_BOLD, 23)
        self.fonts.draw(badge, FontRole.SEMI_BOLD, 23, rect.x + 91 - measured.width / 2, rect.y + 4, ink)
      return
    rows = ((title, FontRole.SEMI_BOLD, 34, 18), (detail, FontRole.NORMAL, 25, 64)) if bookmark else (
      (title, FontRole.NORMAL, 25, 18), (detail, FontRole.SEMI_BOLD, 34, 58))
    for text, role, size, offset in rows:
      measured = self.fonts.measure(text, role, size)
      self.fonts.draw(text, role, size, rect.x + (rect.width - measured.width) / 2, rect.y + offset,
                      ink)

  def render_map_end(self, map_x, map_y, map_width, map_height, placements):
    if not map_end_visible(self.document, placements):
      return
    x, y, size = map_end_rect(map_x, map_y, map_width, map_height)
    center, radius, arm = rl.Vector2(x + size / 2, y + size / 2), size / 2, size * 0.18
    tint = rl.Color(240, 150, 150, 255)
    rl.draw_circle_v(center, radius, rl.Color(15, 13, 23, 235))
    rl.draw_ring(center, radius - 2.5, radius, 0, 360, 48, tint)
    for dx, dy in ((1, 1), (1, -1)):
      rl.draw_line_ex(rl.Vector2(center.x - arm * dx, center.y - arm * dy),
                      rl.Vector2(center.x + arm * dx, center.y + arm * dy), 5, tint)

  def fit(self, text, role, size, width):
    if self.fonts.measure(text, role, size).width <= width:
      return text
    while text and self.fonts.measure(text + '…', role, size).width > width:
      text = text[:-1]
    return text.rstrip() + '…'

  @staticmethod
  def draw_icon(key, rect, color):
    paths = (((3, 11), (12, 3), (21, 11)), ((5, 9), (5, 21), (10, 21), (10, 14), (14, 14), (14, 21), (19, 21), (19, 9))) if key == 'nav_home' else (
      ((8, 7), (8, 4), (16, 4), (16, 7)), ((3, 7), (21, 7), (21, 21), (3, 21), (3, 7)),
      ((3, 12), (10, 15), (14, 15), (21, 12)), ((10, 13), (10, 17), (14, 17), (14, 13)))
    if key == 'bookmark':
      paths = (((6, 3), (18, 3), (18, 21), (12, 16), (6, 21), (6, 3)),)
    scale = 64 / 24
    for path in paths:
      for (x1, y1), (x2, y2) in pairwise(path):
        rl.draw_line_ex(rl.Vector2(rect.x + 23 + x1 * scale, rect.y + 23 + y1 * scale),
                        rl.Vector2(rect.x + 23 + x2 * scale, rect.y + 23 + y2 * scale), 4.5, color)
