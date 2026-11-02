"""Bounded native resource ownership for the Settings development presentation."""

import hashlib
import json
import math
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.settings_icons import draw_icon_geometry


class SettingsAssets:
  def __init__(self, directory: Path):
    self.directory = directory
    manifest = json.loads(Path(__file__).with_name("settings-assets.json").read_text())
    self._manifest = {entry["file"]: entry for entry in manifest["files"]}
    self._images: dict[tuple, rl.Texture] = {}
    self._icons: dict[tuple, rl.RenderTexture] = {}
    self._pixel_scale = (max(1, rl.get_render_width() / max(1, rl.get_screen_width())),
                         max(1, rl.get_render_height() / max(1, rl.get_screen_height())))

  def image(self, filename: str, width: int, height: int) -> rl.Texture:
    key = filename, width, height
    if key in self._images:
      return self._images[key]
    if filename not in self._manifest or not 0 < width <= 4096 or not 0 < height <= 4096:
      raise ValueError("Unreviewed Settings image or dimensions")
    record = self._manifest[filename]
    path = self.directory / filename
    with path.open("rb") as stream:
      data = stream.read(record["bytes"] + 1)
    if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
      raise ValueError(f"Settings image differs from the reviewed manifest: {filename}")
    image = rl.load_image(str(path))
    texture = None
    try:
      if image.data is None or not 0 < image.width <= 4096 or not 0 < image.height <= 4096:
        raise ValueError(f"Invalid Settings image: {filename}")
      logical_ratio = min(width / image.width, height / image.height)
      logical_width, logical_height = round(image.width * logical_ratio), round(image.height * logical_ratio)
      ratio = min(min(int(width * self._pixel_scale[0]), image.width) / image.width,
                  min(int(height * self._pixel_scale[1]), image.height) / image.height)
      if ratio != 1:
        rl.image_resize(image, int(image.width * ratio), int(image.height * ratio))
      texture = rl.load_texture_from_image(image)
      if texture.id == 0:
        raise RuntimeError(f"Unable to load Settings texture: {filename}")
      rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      rl.set_texture_wrap(texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)
      texture.width, texture.height = logical_width, logical_height
      self._images[key] = texture
    except BaseException:
      if texture is not None and texture.id:
        rl.unload_texture(texture)
      raise
    finally:
      if image.data is not None:
        rl.unload_image(image)
    return texture

  def prepare_icon(self, name: str, scale: float, color: rl.Color) -> None:
    """Create vector cache between frames; render-texture modes cannot nest."""
    if name not in ("sound", "aicar", "steering", "system", "display", "vehicle") or not 0 < scale <= 3:
      raise ValueError("Unreviewed Settings vector icon")
    key = name, scale, color.r, color.g, color.b, color.a
    if key in self._icons:
      return
    padding = max(1, math.ceil(3 * scale))
    size = max(1, round(60 * scale)) + 2 * padding
    target = rl.load_render_texture(size, size)
    try:
      if not target.id or not target.texture.id:
        raise RuntimeError("Unable to create Settings vector cache")
      rl.begin_texture_mode(target)
      try:
        rl.clear_background(rl.BLANK)
        rl.rl_set_blend_factors_separate(rl.RL_SRC_ALPHA, rl.RL_ONE_MINUS_SRC_ALPHA,
                                       rl.RL_ONE, rl.RL_ONE_MINUS_SRC_ALPHA, rl.RL_FUNC_ADD, rl.RL_FUNC_ADD)
        rl.begin_blend_mode(rl.BlendMode.BLEND_CUSTOM_SEPARATE)
        try:
          draw_icon_geometry(name, padding, padding, scale, color)
        finally:
          rl.end_blend_mode()
      finally:
        rl.end_texture_mode()
      rl.set_texture_filter(target.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      rl.set_texture_wrap(target.texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)
    except BaseException:
      if target.id:
        rl.unload_render_texture(target)
      raise
    self._icons[key] = target

  def icon(self, name: str, x: float, y: float, scale: float, color: rl.Color) -> None:
    target = self._icons[(name, scale, color.r, color.g, color.b, color.a)]
    padding = max(1, math.ceil(3 * scale))
    width, height = target.texture.width, target.texture.height
    rl.begin_blend_mode(rl.BlendMode.BLEND_ALPHA_PREMULTIPLY)
    try:
      rl.draw_texture_pro(target.texture, rl.Rectangle(0, 0, width, -height),
                          rl.Rectangle(x - padding, y - padding, width, height), rl.Vector2(0, 0), 0, rl.WHITE)
    finally:
      rl.end_blend_mode()

  def close(self) -> None:
    if rl.is_window_ready():
      for image in self._images.values():
        rl.unload_texture(image)
      for icon in self._icons.values():
        rl.unload_render_texture(icon)
    self._images.clear()
    self._icons.clear()
