"""Small crescent built without an opaque cutout over the camera."""

from __future__ import annotations
import numpy as np

import math
from functools import lru_cache

from openpilot.starpilot.ui.raylib import rl


_DISTANCE = math.hypot(.45, -.3)
_DIRECTION = math.atan2(-.3, .45)
_INTERSECTION = math.acos(_DISTANCE / 2)
_POINTS = tuple(point for i in range(25) for point in (
  (math.cos(_DIRECTION + _INTERSECTION + (2 * math.pi - 2 * _INTERSECTION) * i / 24),
   math.sin(_DIRECTION + _INTERSECTION + (2 * math.pi - 2 * _INTERSECTION) * i / 24)),
  (.45 + math.cos(_DIRECTION + math.pi - _INTERSECTION + 2 * _INTERSECTION * i / 24),
   -.3 + math.sin(_DIRECTION + math.pi - _INTERSECTION + 2 * _INTERSECTION * i / 24)),
))


@lru_cache(maxsize=64)
def _vertices(x: float, y: float, width: float, height: float):
  radius = min(width, height) / 2
  cx, cy = x + width / 2, y + height / 2
  return np.array([(cx + px * radius, cy + py * radius) for px, py in _POINTS], dtype=np.float32)


def draw_moon(rect: rl.Rectangle, color: rl.Color | None = None) -> None:
  points = _vertices(rect.x, rect.y, rect.width, rect.height)
  rl.draw_triangle_strip(points, len(_POINTS), color if color is not None else rl.Color(188, 132, 255, 255))
