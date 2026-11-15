"""One cached atlas for transparent speed signs, buttons, arrows and wheel cues."""

from pathlib import Path

from openpilot.starpilot.ui.raylib import rl


ATLAS_PATH = Path(__file__).with_name('assets') / 'speed_limit_shapes.png'
ATLAS_SIZE = (736, 688)
SPRITES = {
  'ring': (128, 0, 128, 128),
  'arrow_large': (0, 128, 32, 40), 'arrow_compact': (32, 128, 32, 48),
  'outline': (256, 0, 136, 152),
  'wheel_plus': (280, 176, 48, 48), 'wheel_minus': (328, 176, 48, 48),
  'button_large': (0, 224, 368, 248), 'button_large_pressed': (368, 224, 368, 248),
  'button_compact': (0, 472, 308, 216), 'button_compact_pressed': (308, 472, 308, 216),
}
SPRITE_DENSITIES = {name: 2 for name in SPRITES if name.startswith('button') or name in ('arrow_compact', 'wheel_plus', 'wheel_minus')}


class SpeedLimitShapes:
  def __init__(self, scale=1, *, headless_context=False):
    self.scale = scale
    self._headless_context = headless_context
    self.texture = None
    self.sources = {name: rl.Rectangle(*rect) for name, rect in SPRITES.items()}
    self.origins = {name: rl.Vector2(rect[2] * scale / SPRITE_DENSITIES.get(name, 1) / 2,
                                   rect[3] * scale / SPRITE_DENSITIES.get(name, 1) / 2) for name, rect in SPRITES.items()}

  def _load(self):
    if self.texture is not None:
      return
    if not (rl.is_window_ready() or self._headless_context):
      raise RuntimeError('Speed-limit shapes require a graphics context')
    image = rl.load_image(str(ATLAS_PATH))
    try:
      if image.data is None or (image.width, image.height) != ATLAS_SIZE:
        raise ValueError('Invalid speed-limit shape atlas')
      self.texture = rl.load_texture_from_image(image)
      if not self.texture.id:
        self.texture = None
        raise RuntimeError('Unable to upload speed-limit shapes')
      rl.set_texture_filter(self.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      rl.set_texture_wrap(self.texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)
    except BaseException:
      self.close()
      raise
    finally:
      if image.data is not None:
        rl.unload_image(image)

  def _draw(self, name, x, y, color, rotation=0, *, scale=None, width_scale=1):
    self._load()
    source = self.sources[name]
    origin = self.origins[name] if scale is None else rl.Vector2(source.width * scale / SPRITE_DENSITIES.get(name, 1) / 2,
                                                               source.height * scale / SPRITE_DENSITIES.get(name, 1) / 2)
    scale = (self.scale if scale is None else scale) / SPRITE_DENSITIES.get(name, 1)
    if width_scale != 1:
      origin = rl.Vector2(origin.x * width_scale, origin.y)
    rl.draw_texture_pro(self.texture, source, rl.Rectangle(x, y, source.width * scale * width_scale, source.height * scale),
                        origin, rotation, color)

  def circle(self, x, y, color):
    self._outlined('ring', x, y, color)

  def _outlined(self, name, x, y, color, *, scale=None, width_scale=1):
    radius = self.scale if scale is None else scale
    for dx, dy in ((-radius, 0), (radius, 0), (0, -radius), (0, radius)):
      self._draw(name, x + dx, y + dy, rl.Color(6, 14, 10, 230), scale=scale, width_scale=width_scale)
    self._draw(name, x, y, color, scale=scale, width_scale=width_scale)

  def outline(self, x, y, color, *, scale=None, width_scale=1):
    self._outlined('outline', x, y, color, scale=scale, width_scale=width_scale)

  def thick_outline(self, x, y, color, *, scale=None, width_scale=1):
    """The rectangular outline at about twice its stroke, for the limit being followed."""
    radius = self.scale if scale is None else scale
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
      self._draw('outline', x + dx * radius, y + dy * radius, rl.Color(6, 14, 10, 230), scale=scale, width_scale=width_scale)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0)):
      self._draw('outline', x + dx * radius, y + dy * radius, color, scale=scale, width_scale=width_scale)

  def in_use_ring(self, x, y, color):
    """A highlight ring around the red Vienna ring, whose outer edge is at radius 59.

    A see-through gap keeps the green apart from the red, so it reads as a highlight
    around the sign rather than part of it, with the weight of the thick rectangular outline.
    """
    center = rl.Vector2(x, y)
    rl.draw_ring(center, 61 * self.scale, 67.5 * self.scale, 0, 360, 96, rl.Color(6, 14, 10, 230))
    rl.draw_ring(center, 62 * self.scale, 66.5 * self.scale, 0, 360, 96, color)

  def button(self, x, y, color, *, profile, scale, pressed=False):
    self._draw(f'button_{profile}' + ('_pressed' if pressed else ''), x, y, color, scale=scale)

  def arrow(self, size, x, y, direction, color, *, scale=None):
    name = 'arrow_large' if size == 'large' else 'arrow_compact'
    self._draw(name, x, y, color, 180 if direction < 0 else 0, scale=scale)

  def wheel_key(self, plus, x, y, *, scale):
    self._outlined('wheel_plus' if plus else 'wheel_minus', x, y, rl.WHITE, scale=scale)

  def close(self):
    if self.texture is not None and (rl.is_window_ready() or self._headless_context):
      rl.unload_texture(self.texture)
    self.texture = None
