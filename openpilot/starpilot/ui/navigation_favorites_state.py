"""Shared visibility for projected favorite drawing and touch handling."""

FAVORITES = ('nav_home', 'nav_work')


def active_favorite(document, placements):
  if not document or not document['destination']:
    return None
  enabled = [key for key in FAVORITES if (placed := placements.get(key)) and placed['enabled']]
  for key in enabled:
    if any(row.get('label') == key.removeprefix('nav_') and row['id'] == document['destination']['id']
           for row in document['favorites']):
      return key
  # Any other route, such as one sent from the phone, ends from the first favorite slot.
  return enabled[0] if enabled else None


def favorite_visible(key, document, placements):
  active = active_favorite(document, placements)
  return active is None or key == active


MAP_END_SIZE = 60
MAP_END_SLOP = 14


def map_end_visible(document, placements):
  """The map's own end button, for a route no Home or Work slot can end."""
  return bool(document and document['destination']) and active_favorite(document, placements) is None


def map_end_rect(map_x, map_y, map_width, map_height):
  """Top-left of the map; a fixed size so it stays easy to hit on a small map."""
  inset = max(14.0, min(map_width, map_height) * 0.05)
  return map_x + inset, map_y + inset, MAP_END_SIZE
