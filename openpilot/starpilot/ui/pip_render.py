"""Onroad PiP GPU owner: current cabin frame and independent side-camera placement."""

from __future__ import annotations
import numpy as np

import time
from typing import TYPE_CHECKING

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.hardware import COMMA_HARDWARE
from openpilot.starpilot.ui.camera_availability import CameraAvailability
from openpilot.starpilot.ui.pip_shaders import PIP_CURVED_FRAGMENT_SHADER, PIP_FRAGMENT_SHADER, PIP_VERTEX_SHADER
from openpilot.starpilot.ui.pip_sidecam import Mask, PiPStream, Rect, Signals, bubble_rect, selected_sides
from openpilot.system.ui.lib.egl import EGLImage, bind_egl_image_to_texture, create_egl_image, destroy_egl_image, init_egl

if TYPE_CHECKING:
  from openpilot.starpilot.replay.render import ReplayCameraStream


def _external_shader(fragment: str) -> str:
  """Retain the frozen mask/crop, sampling CameraView's AGNOS EGL RGB image."""
  first, rest = fragment.split("\n", 1)
  result = first + "\n#extension GL_OES_EGL_image_external_essl3 : enable\n" + rest
  result = result.replace("uniform sampler2D texture0;", "uniform samplerExternalOES texture0;")
  result = result.replace("uniform sampler2D texture1;", "")
  yuv = """float y = texture(texture0, uv).r;
  vec2 c = texture(texture1, uv).ra - 0.5;
  vec3 rgb = vec3(y + 1.402 * c.y, y - 0.344 * c.x - 0.714 * c.y, y + 1.772 * c.x);"""
  assert yuv in result
  return result.replace(yuv, "vec3 rgb = pow(texture(texture0, uv).rgb, vec3(1.0/1.28));")


class PiPRenderer:
  """One renderer; close it on onroad exit or when the owning shell closes."""

  def __init__(self, shape: str, stream: PiPStream | ReplayCameraStream | None = None, *, frame_availability=None):
    if shape not in ("bubble", "curved"):
      raise ValueError("unknown PiP shape")
    self.frame_availability = frame_availability if frame_availability is not None else CameraAvailability()
    self.shape = shape
    self.stream = stream or PiPStream()
    self.shader: rl.Shader | None = None
    self.texture_y: rl.Texture | None = None
    self.texture_uv: rl.Texture | None = None
    self.egl_texture: rl.Texture | None = None
    self.egl_images: dict[int, EGLImage] = {}
    self._size: tuple[int, int, int] | None = None
    self._last_frame_id: int | None = None
    self._stream_generation: int | None = None
    self._active_sides: set[str] = set()
    self._activated_at: dict[str, float] = {}

  def _shader(self) -> rl.Shader:
    if self.shader is None:
      if COMMA_HARDWARE and not init_egl():
        raise RuntimeError("PiP EGL unavailable")
      fragment = PIP_FRAGMENT_SHADER if self.shape == "bubble" else PIP_CURVED_FRAGMENT_SHADER
      if COMMA_HARDWARE:
        fragment = _external_shader(fragment)
      self.shader = rl.load_shader_from_memory(PIP_VERTEX_SHADER, fragment)
      if not self.shader.id:
        raise RuntimeError("PiP shader unavailable")
    return self.shader

  def _clear_images(self) -> None:
    for image in self.egl_images.values():
      destroy_egl_image(image)
    self.egl_images.clear()
    for name in ("texture_y", "texture_uv", "egl_texture"):
      texture = getattr(self, name)
      if texture is not None and texture.id:
        rl.unload_texture(texture)
      setattr(self, name, None)
    self._size = None
    self._last_frame_id = None

  def close(self) -> None:
    self.deactivate()
    self.frame_availability.close()
    if self.shader is not None and self.shader.id:
      rl.unload_shader(self.shader)
      self.shader.id = 0
    self.shader = None

  def deactivate(self) -> None:
    # EGL images retain duplicated DMA-BUF fds. Destroy them before releasing
    # the VisionIPC client that supplied the old buffer session.
    self._clear_images()
    self.stream.set_active(False)
    self._stream_generation = None
    self._active_sides.clear()
    self._activated_at.clear()

  def _texture(self, frame) -> rl.Texture | None:
    size = (int(frame.width), int(frame.height), int(frame.stride))
    if (size[0] <= 0 or size[1] <= 0 or size[2] < size[0] or
        size[0] % 2 or size[1] % 2 or size[2] % 2):
      return None
    if self._size != size:
      self._clear_images()
      self._size = size
      if COMMA_HARDWARE:
        image = rl.gen_image_color(1, 1, rl.BLACK)
        try:
          self.egl_texture = rl.load_texture_from_image(image)
        finally:
          rl.unload_image(image)
      else:
        self.texture_y = rl.load_texture_from_image(rl.Image(None, size[2], size[1], 1,
                                                              rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_GRAYSCALE))
        self.texture_uv = rl.load_texture_from_image(rl.Image(None, size[2] // 2, size[1] // 2, 1,
                                                               rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_GRAY_ALPHA))
    if COMMA_HARDWARE:
      if self.egl_texture is None or not self.egl_texture.id:
        return None
      idx = int(frame.idx)
      if idx not in self.egl_images:
        image = create_egl_image(size[0], size[1], size[2], frame.fd, frame.uv_offset)
        if image is None:
          return None
        self.egl_images[idx] = image
      self.egl_texture.width, self.egl_texture.height = size[0], size[1]
      bind_egl_image_to_texture(self.egl_texture.id, self.egl_images[idx])
      return self.egl_texture
    if self.texture_y is None or self.texture_uv is None or not self.texture_y.id or not self.texture_uv.id:
      return None
    if self._last_frame_id != frame.frame_id:
      data = frame.data
      uv_offset = int(frame.uv_offset)
      if uv_offset < size[2] * size[1] or len(data) < uv_offset + size[2] * (size[1] // 2):
        return None
      y = data[:size[2] * size[1]]
      uv = data[uv_offset:uv_offset + size[2] * (size[1] // 2)]
      rl.update_texture(self.texture_y, y)
      rl.update_texture(self.texture_uv, uv)
      self._last_frame_id = frame.frame_id
    return self.texture_y

  def render(self, content: rl.Rectangle, mask: Mask | None, signals: Signals, *, enabled: bool,
             on_blinker: bool, on_bsm: bool, invert: bool,
             now: float | None = None, placements: dict[str, Rect] | None = None, submit=None) -> str:
    if not enabled or mask is None or (placements is not None and not placements):
      self.deactivate()
      return "inactive"
    sides = selected_sides(mask, signals, started=True, enabled=enabled,
                           on_blinker=on_blinker, on_bsm=on_bsm)
    if placements is not None:
      sides = tuple(side for side in sides if side in placements)
    observed_now = time.monotonic() if now is None else now
    # While a bubble can still appear, keep the cabin stream connected and current even
    # when none is shown, so a blinker or blind-spot warning draws on the very next frame.
    self.stream.set_active(True)
    frame = self.stream.poll(observed_now)
    if self._stream_generation != self.stream.generation:
      self._clear_images()
      self._stream_generation = self.stream.generation
      self.stream.release_retired()
    if not sides:
      self._active_sides.clear()
      self._activated_at.clear()
      return "inactive"
    for side in set(sides) - self._active_sides:
      self._activated_at[side] = observed_now
    self._active_sides = set(sides)
    if frame is None:
      self._clear_images()
      return "no_frame"
    self.frame_availability.observe()
    mask = mask.for_frame(frame.width, frame.height)
    if mask is None:
      self._clear_images()
      return "wrong_frame_size"
    try:
      shader = self._shader()
      texture = self._texture(frame)
    except (OSError, RuntimeError, ValueError):
      self._clear_images()
      return "graphics_unavailable"
    if texture is None:
      return "graphics_unavailable"
    chosen = sides if self.shape == "bubble" else (max(sides, key=lambda side: self._activated_at[side]),)
    for side in chosen:
      crop = mask.crop(side)
      if crop is None:
        continue
      rect = bubble_rect(Rect(content.x, content.y, content.width, content.height), side) if self.shape == "bubble" else \
        Rect(content.x, content.y, content.width, content.height)
      if placements is not None:
        rect = placements[side]
      warning = (signals.vasm_left or signals.left_blindspot) if side == "left" else (signals.vasm_right or signals.right_blindspot)
      def draw(rect=rect, crop=crop, warning=warning):
        self._draw(shader, texture, frame, rect, crop.x, crop.y, crop.size, invert, warning=warning)
      if submit is None:
        draw()
      else:
        submit(f"pip_{side}", draw)
    return "rendered"

  def _draw(self, shader: rl.Shader, texture: rl.Texture, frame, rect: Rect,
            crop_x: float, crop_y: float, crop_size: float, invert: bool, *, warning: bool = False) -> None:
    texture_width = frame.width if COMMA_HARDWARE else frame.stride
    crop_min = rl.Vector2(crop_x / texture_width, crop_y / frame.height)
    crop_extent = rl.Vector2(crop_size / texture_width, crop_size / frame.height)
    # The crop shader expects fragment coordinates spanning 0..1. For copied
    # NV12 textures, the padded stride is the texture width, not frame.width.
    source = rl.Rectangle(0, 0, float(texture_width), float(frame.height))
    target = rl.Rectangle(rect.x, rect.y, rect.width, rect.height)
    rl.begin_shader_mode(shader)
    try:
      rl.set_shader_value(shader, rl.get_shader_location(shader, "uCropMin"), crop_min,
                          rl.ShaderUniformDataType.SHADER_UNIFORM_VEC2)
      rl.set_shader_value(shader, rl.get_shader_location(shader, "uCropSize"), crop_extent,
                          rl.ShaderUniformDataType.SHADER_UNIFORM_VEC2)
      flip = np.array([1 if invert else 0], dtype=np.int32)
      rl.set_shader_value(shader, rl.get_shader_location(shader, "uFlipX"), flip,
                          rl.ShaderUniformDataType.SHADER_UNIFORM_INT)
      if self.shape == "curved":
        panel = rl.Vector2(rect.width, rect.height)
        rl.set_shader_value(shader, rl.get_shader_location(shader, "uRectSize"), panel,
                            rl.ShaderUniformDataType.SHADER_UNIFORM_VEC2)
      if not COMMA_HARDWARE and self.texture_uv is not None:
        rl.set_shader_value_texture(shader, rl.get_shader_location(shader, "texture1"), self.texture_uv)
      rl.draw_texture_pro(texture, source, target, rl.Vector2(0, 0), 0.0, rl.WHITE)
    finally:
      rl.end_shader_mode()
    if self.shape == "bubble":
      radius = rect.width / 2
      rl.draw_ring(rl.Vector2(rect.x + radius, rect.y + radius), max(0, radius - 3), radius,
                   0, 360, 64, rl.Color(255, 60, 60, 255) if warning else rl.Color(94, 229, 238, 255))
    elif warning:
      rl.draw_rectangle_rounded_lines_ex(target, 0.44, 16, 4, rl.Color(255, 60, 60, 255))
