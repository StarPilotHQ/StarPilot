"""RGBA -> NV12 on the GPU, so the car renderer reads back 1.5 bytes per pixel instead of 4.

The encoder wants NV12: a full-resolution luma plane followed by a
half-resolution plane of interleaved U/V. GLES can only read RGBA back
portably, so each plane is packed four bytes to an RGBA texel:

* luma target, width/4 x height: texel (x, y) = Y of pixels 4x..4x+3 on row y
* chroma target, width/4 x height/2: texel (x, y) = U, V of the 2x2 blocks at
  columns 4x and 4x+2 on rows 2y and 2y+1

Read back in row order, the two targets are exactly the tightly packed NV12
frame. The integer arithmetic matches ``hw/rgba_to_nv12.h`` (BT.601 limited
range, rounded 2x2 chroma average), so either path gives the car the same
picture. By default source texel row 0 is the top of the image. ``compose`` and
margins let the car renderer convert its UI texture directly, folding the
top-down flip and padding into these passes instead of copying a full RGBA frame.
"""

from __future__ import annotations
import numpy as np

import platform

GL_ONE, GL_ZERO, GL_FUNC_ADD = 1, 0, 0x8006

VERSION = "#version 330 core\n" if platform.system() == "Darwin" else "#version 300 es\nprecision highp float;\nprecision highp int;\n"

VERTEX_SHADER = VERSION + """
in vec3 vertexPosition;
in vec2 vertexTexCoord;
in vec4 vertexColor;
uniform mat4 mvp;
out vec2 fragTexCoord;
void main() {
  fragTexCoord = vertexTexCoord;
  gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""

FRAGMENT_COMMON = """
uniform sampler2D texture0;
uniform ivec2 sourceOffset;
uniform int composeSource;
out vec4 finalColor;
ivec3 px(int x, int y) {
  ivec2 p = ivec2(x, y) - sourceOffset;
  ivec2 size = textureSize(texture0, 0);
  if (any(lessThan(p, ivec2(0))) || any(greaterThanEqual(p, size))) return ivec3(6, 6, 15);
  if (composeSource != 0) p.y = size.y - 1 - p.y;
  // Use RGB only. The UI texture starts from an opaque clear, so its RGB is
  // already the final color; its alpha depends on which blend mode drew each
  // pixel and changes between frames. Blending by it made bright areas flicker.
  vec3 color = texelFetch(texture0, p, 0).rgb;
  return ivec3(color * 255.0 + 0.5);
}
int luma(ivec3 p) { return ((66 * p.r + 129 * p.g + 25 * p.b + 128) >> 8) + 16; }
vec2 chroma(int x, int y) {
  ivec3 a = (px(x, y) + px(x + 1, y) + px(x, y + 1) + px(x + 1, y + 1) + 2) >> 2;
  int u = ((-38 * a.r - 74 * a.g + 112 * a.b + 128) >> 8) + 128;
  int v = ((112 * a.r - 94 * a.g - 18 * a.b + 128) >> 8) + 128;
  return vec2(float(u), float(v));
}
"""

LUMA_SHADER = VERSION + FRAGMENT_COMMON + """
void main() {
  ivec2 o = ivec2(gl_FragCoord.xy);
  int x = o.x * 4;
  finalColor = vec4(float(luma(px(x, o.y))), float(luma(px(x + 1, o.y))),
                    float(luma(px(x + 2, o.y))), float(luma(px(x + 3, o.y)))) / 255.0;
}
"""

CHROMA_SHADER = VERSION + FRAGMENT_COMMON + """
void main() {
  ivec2 o = ivec2(gl_FragCoord.xy);
  finalColor = vec4(chroma(o.x * 4, o.y * 2), chroma(o.x * 4 + 2, o.y * 2)) / 255.0;
}
"""


def compose_rgba(source, target, margin_w: int, margin_h: int, *, fit: bool = False, content_left: int | None = None, content_top: int | None = None) -> None:
  """RGBA fallback composition, matching ``Nv12Converter(compose=True)``.

  Centers the UI texture inside the car's margins, flips it top-down and copies
  its RGB. Like the NV12 pass it never blends by the texture's alpha, which
  varies between frames; blending by it made bright areas flicker.
  """
  from openpilot.starpilot.ui.raylib import rl
  rl.begin_texture_mode(target)
  rl.clear_background(rl.Color(6, 6, 15, 255))
  rl.rl_set_blend_factors(GL_ONE, GL_ZERO, GL_FUNC_ADD)
  rl.begin_blend_mode(rl.BlendMode.BLEND_CUSTOM)
  # A positive source height flips on the GPU so the readback is top-down for the encoder.
  left = margin_w // 2 if content_left is None else content_left
  bottom = margin_h // 2 if content_top is None else margin_h - content_top
  destination = rl.Rectangle(left, bottom, source.width, source.height)
  if fit:
    width, height = target.texture.width - margin_w, target.texture.height - margin_h
    scale = min(width / source.width, height / source.height)
    drawn_w, drawn_h = source.width * scale, source.height * scale
    destination = rl.Rectangle(left + (width - drawn_w) / 2,
                               bottom + (height - drawn_h) / 2, drawn_w, drawn_h)
  rl.draw_texture_pro(source, rl.Rectangle(0, 0, source.width, source.height),
                      destination, rl.Vector2(0, 0), 0.0, rl.WHITE)
  rl.end_blend_mode()
  rl.end_texture_mode()


def supported(width: int, height: int) -> bool:
  return width % 4 == 0 and height % 2 == 0


# ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: Fused GPU composition/conversion reduces readback size and avoids CPU color conversion.
# Altering this can degrade video smoothness or introduce lag on Android Auto.
# Do not change it autonomously; require an explicit user request specifically for this video optimization.
class Nv12Converter:
  def __init__(self, width: int, height: int, *, margin_w: int = 0, margin_h: int = 0, compose: bool = False,
               content_left: int | None = None, content_top: int | None = None):
    if not supported(width, height):
      raise ValueError("NV12 conversion needs a width divisible by 4 and an even height")
    if not 0 <= margin_w < width or not 0 <= margin_h < height:
      raise ValueError("NV12 margins must leave a visible image")
    from openpilot.starpilot.ui.raylib import rl
    self.rl = rl
    self.width, self.height = width, height
    self.source_size = (width - margin_w, height - margin_h)
    self.shaders = []
    self.targets = []
    try:
      for fragment in (LUMA_SHADER, CHROMA_SHADER):
        shader = rl.load_shader_from_memory(VERTEX_SHADER, fragment)
        if not shader.id or shader.id == rl.rl_get_shader_id_default():
          raise RuntimeError("NV12 shader did not compile")
        self.shaders.append(shader)
        # The old composition pass places floor(margin_h / 2) rows above the
        # flipped texture. Readback starts at the bottom, so an odd extra row
        # belongs at the start of the encoded image, matching that pass exactly.
        offset = np.array([margin_w // 2 if content_left is None else content_left,
                                      margin_h - margin_h // 2 if content_top is None else content_top], dtype=np.int32)
        compose_value = np.array([int(compose)], dtype=np.int32)
        rl.set_shader_value(shader, rl.get_shader_location(shader, "sourceOffset"), offset, rl.ShaderUniformDataType.SHADER_UNIFORM_IVEC2)
        rl.set_shader_value(shader, rl.get_shader_location(shader, "composeSource"), compose_value, rl.ShaderUniformDataType.SHADER_UNIFORM_INT)
      self.targets = [rl.load_render_texture(width // 4, height), rl.load_render_texture(width // 4, height // 2)]
    except BaseException:
      self.close()
      raise

  def convert(self, source) -> list[tuple[int, int, int, int]]:
    """Convert the visible UI texture and return padded, top-down readback regions."""
    if (source.width, source.height) != self.source_size:
      raise ValueError("NV12 source dimensions do not match the visible area")
    rl = self.rl
    for shader, target in zip(self.shaders, self.targets, strict=True):
      rl.begin_texture_mode(target)
      # Replace, never blend: the alpha channel carries a fourth sample.
      rl.rl_set_blend_factors(GL_ONE, GL_ZERO, GL_FUNC_ADD)
      rl.begin_blend_mode(rl.BlendMode.BLEND_CUSTOM)
      rl.begin_shader_mode(shader)
      rl.draw_texture_pro(source, rl.Rectangle(0, 0, source.width, source.height),
                          rl.Rectangle(0, 0, target.texture.width, target.texture.height), rl.Vector2(0, 0), 0.0, rl.WHITE)
      rl.end_shader_mode()
      rl.end_blend_mode()
      rl.end_texture_mode()
    luma, chroma = self.targets
    return [(luma.id, luma.texture.width, luma.texture.height, 0),
            (chroma.id, chroma.texture.width, chroma.texture.height, self.width * self.height)]

  def close(self) -> None:
    for target in self.targets:
      self.rl.unload_render_texture(target)
    for shader in self.shaders:
      self.rl.unload_shader(shader)
    self.targets, self.shaders = [], []


def reference_nv12(rgba: bytes, width: int, height: int) -> bytes:
  """The same conversion on the CPU (numpy), for tests and on-device checks."""
  import numpy as np
  image = np.frombuffer(rgba, np.uint8).reshape(height, width, 4)[..., :3].astype(np.int32)
  r, g, b = image[..., 0], image[..., 1], image[..., 2]
  y = ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16
  blocks = image.reshape(height // 2, 2, width // 2, 2, 3).sum(axis=(1, 3))
  a = (blocks + 2) >> 2
  u = ((-38 * a[..., 0] - 74 * a[..., 1] + 112 * a[..., 2] + 128) >> 8) + 128
  v = ((112 * a[..., 0] - 94 * a[..., 1] - 18 * a[..., 2] + 128) >> 8) + 128
  uv = np.stack((u, v), axis=-1).reshape(height // 2, width)
  return y.astype(np.uint8).tobytes() + uv.astype(np.uint8).tobytes()
