"""Retained collapsed favorite-menu corner hint for the large onroad shell."""
import numpy as np

from functools import lru_cache
import math

from openpilot.starpilot.ui.raylib import rl


def _aether_color(t: float, alpha: int) -> rl.Color:
  if t <= 0.5:
    w = t * 2.0
    return rl.Color(int(196 - 24 * w), int(158 - 33 * w), 255, alpha)
  w = (t - 0.5) * 2.0
  return rl.Color(int(172 - 84 * w), int(125 - 75 * w), int(255 - 97 * w), alpha)


@lru_cache(maxsize=4)
def _commands(x: float, y: float, width: float, height: float) -> tuple:
  rect = rl.Rectangle(x, y, width, height)
  commands = []
  scale = max(0.35, min(rect.width / 2160.0, rect.height / 1080.0))
  x0, y0 = rect.x, rect.y + rect.height
  size, steps = 150.0 * scale, 48
  origin = rl.Vector2(x0, y0)
  inverse = 1.0 / steps
  top = [rl.Vector2(x0, y0 - size * (k * inverse)) for k in range(steps + 1)]
  right = [rl.Vector2(x0 + size * (k * inverse), y0) for k in range(steps + 1)]
  for index in range(steps):
    t = (index + 0.5) * inverse
    base_alpha = int(160 * (1 - t) ** 1.40)
    purple_alpha = int(105 * (1 - t) ** 1.75)
    for color in (rl.Color(8, 6, 18, base_alpha) if base_alpha > 0 else None,
                  _aether_color(t, purple_alpha) if purple_alpha > 0 else None):
      if color is None:
        continue
      if index == 0:
        commands.append((rl.draw_triangle, (origin, right[index + 1], top[index + 1], color)))
      else:
        # A strip emits the same two triangles, in the same blending order,
        # while crossing the Python/native boundary only once for this band.
        points = np.array([(v.x, v.y) for v in (top[index], right[index], top[index + 1], right[index + 1])], dtype=np.float32)
        commands.append((rl.draw_triangle_strip, (points, 4, color)))
  inset = 48.0 * scale
  center_x, center_y = rect.x + inset, rect.y + rect.height - inset
  tip = rl.Vector2(center_x + 15 * scale, center_y - 15 * scale)
  tail = rl.Vector2(center_x - 15 * scale, center_y + 15 * scale)
  wing1 = rl.Vector2(tip.x - 14 * scale, tip.y + 1.2 * scale)
  wing2 = rl.Vector2(tip.x - 1.2 * scale, tip.y + 14 * scale)
  line_width = 4.6 * scale
  offset = 1.6 * scale
  shifted = tuple(rl.Vector2(point.x + offset, point.y + offset) for point in (tail, tip, wing1, wing2))
  layers = ((shifted[0], shifted[1], shifted[2], shifted[3], line_width + 2 * scale, rl.Color(8, 6, 16, 105)),
            (tail, tip, wing1, wing2, line_width + 3.2 * scale, rl.Color(185, 145, 255, 55)),
            (tail, tip, wing1, wing2, line_width, rl.Color(255, 255, 255, 225)),
            (tail, tip, wing1, wing2, 2.0 * scale, rl.Color(255, 255, 255, 240)))
  for start, end, first_wing, second_wing, stroke_width, color in layers:
    commands.append((rl.draw_line_ex, (start, end, stroke_width, color)))
    commands.append((rl.draw_line_ex, (end, first_wing, stroke_width, color)))
    commands.append((rl.draw_line_ex, (end, second_wing, stroke_width, color)))
    for point in (start, end, first_wing, second_wing):
      commands.append((rl.draw_circle_v, (point, stroke_width / 2, color)))

  return tuple(commands)


class CornerHintCache:
  """One view-owned texture; failed dimensions use the retained primitives."""

  def __init__(self):
    self.texture = None
    self.key = None
    self.failed_key = None
    self.pending = None

  def close(self) -> None:
    texture, self.texture = self.texture, None
    self.key = self.failed_key = self.pending = None
    if texture is not None and rl.is_window_ready():
      rl.unload_render_texture(texture)

  def get(self, rect):
    key = (rect.width, rect.height)
    if self.texture is not None and self.key == key:
      return self.texture
    if self.failed_key != key:
      self.pending = key
    return None

  def prepare(self):
    """Called before drawing begins, never inside a parent render target."""
    key = self.pending
    if key is None or not rl.is_window_ready():
      return
    self.close()
    size = max(1, math.ceil(150.0 * max(0.35, min(key[0] / 2160.0, key[1] / 1080.0))))
    texture = None
    try:
      texture = rl.load_render_texture(size, size)
      if not texture.id or not texture.texture.id:
        raise RuntimeError("corner texture allocation failed")
      rl.begin_texture_mode(texture)
      try:
        rl.clear_background(rl.BLANK)
        # Scratch RGB is premultiplied, but alpha must accumulate coverage.
        # Raylib BLEND_ALPHA otherwise accumulates alpha squared (rlgl.h).
        rl.rl_set_blend_factors_separate(0x0302, 0x0303, 1, 0x0303, 0x8006, 0x8006)
        rl.begin_blend_mode(rl.BlendMode.BLEND_CUSTOM_SEPARATE)
        try:
          # Keep original scale and geometry, translating only the local origin.
          for draw, arguments in _commands(0.0, size - key[1], *key):
            draw(*arguments)
        finally:
          rl.end_blend_mode()
      finally:
        rl.end_texture_mode()
    except Exception:
      if texture is not None and texture.id:
        rl.unload_render_texture(texture)
      self.failed_key = key
      return None
    self.texture, self.key = texture, key
    return texture


def render_corner_hint(rect: rl.Rectangle, *, cache: CornerHintCache | None = None) -> None:
  """Compose retained primitives once when a view-owned texture is available."""
  texture = cache.get(rect) if cache is not None else None
  if texture is None:
    for draw, arguments in _commands(rect.x, rect.y, rect.width, rect.height):
      draw(*arguments)
    return
  size = texture.texture.width
  rl.begin_blend_mode(rl.BlendMode.BLEND_ALPHA_PREMULTIPLY)
  try:
    rl.draw_texture_pro(texture.texture, rl.Rectangle(0, 0, size, -size),
                        rl.Rectangle(rect.x, rect.y + rect.height - size, size, size),
                        rl.Vector2(0, 0), 0.0, rl.WHITE)
  finally:
    rl.end_blend_mode()
