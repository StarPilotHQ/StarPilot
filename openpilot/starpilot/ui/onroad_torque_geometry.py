"""Frozen torque arc dimensions and cap-inclusive logical-pixel bounds."""

import math

TORQUE_ANGLE_SPAN = 12.7
RADIUS = 1200
MIN_HEIGHT, MAX_HEIGHT = 14, 56
MIN_OFFSET, MAX_OFFSET = 22, 26
CAP_RADIUS = 7
LARGE_CAP_RADIUS = 3 * CAP_RADIUS


def arc_scale(width, height, screen_width):
  """Bound tall projection arcs horizontally, including their rounded caps."""
  scale = height / 240 * (width / screen_width)
  sine = math.sin(math.radians(TORQUE_ANGLE_SPAN / 2))
  cap = LARGE_CAP_RADIUS if screen_width == 2160 else CAP_RADIUS
  # Keep one logical pixel inside the bound before floor/ceil rounding.
  maximum = (width / 2 - 9 - cap * (1 - sine)) / ((RADIUS + MAX_HEIGHT) * sine)
  return min(scale, max(0, maximum))


def maximum_footprint(x, y, width, height, screen_width):
  scale = arc_scale(width, height, screen_width)
  cap_radius = LARGE_CAP_RADIUS if screen_width == 2160 else CAP_RADIUS
  half_angle = math.radians(TORQUE_ANGLE_SPAN / 2)
  radius = RADIUS * scale
  center_x = x + width / 2 + 8
  extent_x = (radius + MAX_HEIGHT * scale) * math.sin(half_angle) + cap_radius * (1 - math.sin(half_angle))
  left, right = math.floor(center_x - extent_x), math.ceil(center_x + extent_x)
  top = math.floor(y + height - (MAX_OFFSET + MAX_HEIGHT) * scale)
  bottom = math.ceil(y + height - MIN_OFFSET * scale + (radius + cap_radius) * (1 - math.cos(half_angle)))
  return left, top, right - left, bottom - top
