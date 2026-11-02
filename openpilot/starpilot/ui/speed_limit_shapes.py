"""One cached atlas for speed-limit signs and confirmation surfaces."""

from pathlib import Path

from openpilot.starpilot.ui.raylib import rl


ATLAS_PATH = Path(__file__).with_name('assets') / 'speed_limit_shapes.png'
ATLAS_SIZE = (384, 484)
SPRITES = {
  'disc': (0, 0, 128, 128), 'ring': (128, 0, 128, 128),
  'arrow_large': (0, 128, 32, 40), 'arrow_compact': (32, 128, 32, 48),
  'outline': (256, 0, 128, 144),
  'confirmation': (0, 176, 104, 308), 'confirmation_open': (104, 176, 72, 308),
  'confirmation_pulse': (176, 176, 104, 136),
  'wheel_plus': (280, 176, 48, 48), 'wheel_minus': (328, 176, 48, 48),
  'arrow_confirmation': (280, 224, 32, 48),
}
SPRITE_DENSITIES = {'arrow_compact': 2, 'confirmation': 2, 'confirmation_open': 2, 'confirmation_pulse': 2,
                    'wheel_plus': 2, 'wheel_minus': 2, 'arrow_confirmation': 2}


class SpeedLimitShapes:
  def __init__(self, scale=1, *, headless_context=False):
    self.scale = scale
    self._headless_context = headless_context
    self.texture = None
    self.short_outline = None
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

  def _draw(self, name, x, y, color, rotation=0, *, scale=None):
    self._load()
    source = self.sources[name]
    origin = self.origins[name] if scale is None else rl.Vector2(source.width * scale / SPRITE_DENSITIES.get(name, 1) / 2,
                                                               source.height * scale / SPRITE_DENSITIES.get(name, 1) / 2)
    scale = (self.scale if scale is None else scale) / SPRITE_DENSITIES.get(name, 1)
    rl.draw_texture_pro(self.texture, source, rl.Rectangle(x, y, source.width * scale, source.height * scale),
                        origin, rotation, color)

  def circle(self, x, y, color):
    self._draw('disc', x, y, rl.WHITE)
    self._draw('ring', x, y, color)

  def outline(self, x, y, color, *, short=False):
    if not short:
      self._draw('outline', x, y, color)
      return
    self._load()
    if self.short_outline is None:
      source = self.sources['outline']
      # Remove only the straight middle: corners and border thickness stay intact.
      self.short_outline = ((rl.Rectangle(source.x, source.y, source.width, 64), -64),
                            (rl.Rectangle(source.x, source.y + 80, source.width, 64), 0))
    origin = rl.Vector2(64 * self.scale, 0)
    for source, dy in self.short_outline:
      rl.draw_texture_pro(self.texture, source,
                          rl.Rectangle(x, y + dy * self.scale, source.width * self.scale, source.height * self.scale),
                          origin, 0, color)

  def arrow(self, size, x, y, direction, color, *, scale=None):
    name = 'arrow_large' if size == 'large' else 'arrow_confirmation' if size == 'confirmation' else 'arrow_compact'
    self._draw(name, x, y, color, 180 if direction < 0 else 0, scale=scale)

  def wheel_key(self, plus, x, y, *, scale):
    self._draw('wheel_plus' if plus else 'wheel_minus', x, y, rl.WHITE, scale=scale)

  def confirmation(self, bounds, *, opened=False, strength=0):
    self._load()
    x, y, right, bottom = bounds
    scale = (bottom - y) / 152
    layers = [('confirmation_open' if opened else 'confirmation', y, 255)]
    if not opened and strength > 0:
      layers.append(('confirmation_pulse', y + 86 * scale, round(255 * strength)))
    for name, top, alpha in layers:
      source = self.sources[name]
      texel_scale = scale / SPRITE_DENSITIES[name]
      cap = (source.width - 8) / 2
      # Stretch only the flat middle; keep the two-pixel atlas padding out of the quad.
      for sx, sw, dx, dw in ((source.x + 2, cap, x, cap * texel_scale),
                              (source.x + 2 + cap, 4, x + cap * texel_scale, right - x - 2 * cap * texel_scale),
                              (source.x + source.width - 2 - cap, cap, right - cap * texel_scale, cap * texel_scale)):
        rl.draw_texture_pro(self.texture, rl.Rectangle(sx, source.y + 2, sw, source.height - 4),
                            rl.Rectangle(dx, top, dw, (source.height - 4) * texel_scale),
                            rl.Vector2(0, 0), 0, rl.Color(255, 255, 255, alpha))

  def close(self):
    if self.texture is not None and (rl.is_window_ready() or self._headless_context):
      rl.unload_texture(self.texture)
    self.texture = None
