"""Touch owner for projected Home, Work and Bookmark actions."""
import time

from openpilot.starpilot.navigation.owner import NavigationOwner
from openpilot.starpilot.system.starport.projection_layout import BOOKMARK_WIDGET, DISPLAY_WIDGETS, FAVORITE_WIDGETS, FAVORITE_SIZE, FAVORITE_ICON_SIZE
from openpilot.starpilot.ui.onroad_customization import widget_order
from openpilot.starpilot.ui.navigation_favorites_state import favorite_visible


def destination_label(document):
  """"Home" or "Work" when the active destination is that favorite, else its place name; '' when not navigating."""
  destination = document['destination'] if document is not None else None
  if destination is None:
    return ''
  place = next((row for row in document['favorites'] if row.get('id') == destination['id']), None)
  if place is not None and place.get('label') in ('home', 'work'):
    return place['label'].title()
  return destination.get('name', '')


class ProjectionFavorites:
  def __init__(self, authorized, owner=None):
    self.authorized = authorized
    self.owner = owner if owner is not None else NavigationOwner()
    self.document = None
    self.error = ''
    self.card_bounds = None
    self.bookmark = lambda: False
    self._read_at = float('-inf')
    self._press = None

  def refresh(self, *, force=False):
    now = time.monotonic()
    if not force and now - self._read_at < 1:
      return
    self._read_at = now
    try:
      self.document = self.owner.read()
    except (OSError, ValueError):
      self.document = None
      if self._press is not None and self._press[0] != BOOKMARK_WIDGET:
        self.cancel()

  def action(self, key):
    if key == BOOKMARK_WIDGET:
      return ('bookmark',)
    doc = self.document
    if doc is None or key not in FAVORITE_WIDGETS:
      return None
    label = key.removeprefix('nav_')
    place = next((row for row in doc['favorites'] if row.get('label') == label), None)
    if place is None:
      return None
    active = doc['destination'] is not None and doc['destination']['id'] == place['id']
    if not active and not (doc['enabled'] and doc['token']):
      return None
    return ('end' if active else 'start', doc['revision'], place)

  def bounds(self, key, state):
    if state.alert.size != 'none':
      return None
    placements = state.customization['layouts']['large']
    if key != BOOKMARK_WIDGET and not favorite_visible(key, self.document, placements):
      return None
    placed = placements.get(key)
    return placed if placed is not None and placed['enabled'] else None

  def hit(self, x, y, state, overlay=None):
    order = state.customization.get('widgetOrder', {}).get('large')
    if order is None:
      order = [*widget_order(state.customization, 'large'), *DISPLAY_WIDGETS]
    for key in reversed(order):
      if overlay is not None and key == overlay[0]:
        rect = overlay[1]
        if rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height:
          return key
      placed = state.customization['layouts']['large'].get(key)
      if placed is not None and placed['enabled']:
        px, py = placed['x'], placed['y']
        if key == 'nav_map':
          width, height = placed['width'], placed['height']
        elif key == 'nav_card' and self.card_bounds is not None and (rect := self.card_bounds(state)) is not None:
          px, py, width, height = rect.x, rect.y, rect.width, rect.height
        else:
          width = height = 0
        if px <= x < px + width and py <= y < py + height:
          return None
      if key in DISPLAY_WIDGETS and (placed := self.bounds(key, state)) is not None:
        width, height = (FAVORITE_ICON_SIZE, FAVORITE_ICON_SIZE) if placed.get('display') == 'icons' else FAVORITE_SIZE
        if placed['x'] <= x < placed['x'] + width and placed['y'] <= y < placed['y'] + height:
          return key
    return None

  def touch(self, kind, x, y, state, drive):
    if kind == 'cancel' or not self.authorized():
      self.cancel()
      return
    if kind == 'down':
      self.cancel()
      self.refresh(force=True)
      key = self.hit(x, y, state)
      if key is not None and (action := self.action(key)) is not None:
        self.error = ''
        self._press = (key, x, y, action, drive)
      return
    if self._press is None:
      return
    key, px, py, action, pressed_drive = self._press
    if abs(x - px) > 15 or abs(y - py) > 15 or drive != pressed_drive or self.hit(x, y, state) != key:
      self.cancel()
      return
    if kind != 'up':
      return
    self.cancel()
    if key == BOOKMARK_WIDGET:
      if self.authorized() and self.bounds(key, state) is not None:
        self.bookmark()
      return
    # A changed destination or relabeled favorite must never turn a held tap
    # into a different command. The owner's revision check covers concurrent writers.
    self.refresh(force=True)
    if self.action(key) != action:
      return
    command, revision, place = action
    def authorized():
      return self.authorized() and self.bounds(key, state) is not None
    try:
      if command == 'end':
        self.owner.clear(revision, authorized)
      else:
        self.owner.select(place, revision, authorized)
    except (OSError, ValueError, PermissionError):
      self.error = 'Try again'
    self.refresh(force=True)

  def cancel(self):
    self._press = None

  def close(self):
    self.cancel()
    self.owner.close()
