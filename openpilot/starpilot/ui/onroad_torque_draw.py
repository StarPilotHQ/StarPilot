"""Antialiased torque arc, painted in one bounded pass without a polygon cache."""
import numpy as np

import math

from openpilot.starpilot.ui.raylib import rl
from openpilot.starpilot.ui.raylib import rl as native_rl

from openpilot.system.ui.lib.application import GL_VERSION
from openpilot.starpilot.ui.onroad_torque_geometry import MAX_HEIGHT, MAX_OFFSET, MIN_HEIGHT, MIN_OFFSET, RADIUS, TORQUE_ANGLE_SPAN, arc_scale


VERTEX_SHADER = GL_VERSION + """
in vec3 vertexPosition;
in vec2 vertexTexCoord;
uniform mat4 mvp;
out vec2 position;
void main() {
  position = vertexTexCoord;
  gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""

FRAGMENT_SHADER = GL_VERSION + """
in vec2 position;
out vec4 finalColor;
uniform vec4 geometry; // center, middle radius, half thickness
uniform vec4 backgroundNormals;
uniform vec4 indicatorNormals;
uniform vec4 style; // cap radius, background alpha, indicator alpha, dot alpha
uniform vec4 dotGeometry; // center, radius, reserved
uniform vec4 gradient; // signed inverse reach, center green, green change, blue

float capDistance(vec2 p, vec2 normal) {
  vec2 tangent = vec2(-normal.y, normal.x);
  float radial = abs(dot(p, normal) - geometry.z);
  return length(vec2(dot(p, tangent), max(radial - (geometry.w - style.x), 0.0))) - style.x;
}
float barDistance(vec2 p, vec4 normals, float ringDistance) {
  vec2 tangent0 = vec2(-normals.y, normals.x);
  vec2 tangent1 = vec2(-normals.w, normals.z);
  float sector = max(ringDistance, max(-dot(p, tangent0), dot(p, tangent1)));
  return min(sector, min(capDistance(p, normals.xy), capDistance(p, normals.zw)));
}
float coverage(float distance) {
  return clamp(0.5 - distance / max(fwidth(distance), 0.0001), 0.0, 1.0);
}
void main() {
  vec2 p = position - geometry.xy;
  float ringDistance = abs(length(p) - geometry.z) - geometry.w;
  float b = coverage(barDistance(p, backgroundNormals, ringDistance)) * style.y;
  float i = coverage(barDistance(p, indicatorNormals, ringDistance)) * style.z;
  float d = coverage(length(position - dotGeometry.xy) - dotGeometry.z) * style.w;
  float bi = b + i * (1.0 - b);
  float alpha = d + bi * (1.0 - d);
  float t = clamp(p.x * gradient.x, 0.0, 1.0);
  vec3 indicatorColor = vec3(1.0, gradient.y + gradient.z * t, gradient.w);
  vec3 premultiplied = (vec3(b * (1.0 - i)) + indicatorColor * i) * (1.0 - d) + vec3((182.0 / 255.0) * d);
  finalColor = vec4(premultiplied / max(alpha, 0.00001), alpha);
}
"""


class TorqueBarPainter:
  def __init__(self):
    self._shader = None
    self._uniforms = {}
    self._previous = {}

  def prepare(self) -> None:
    if self._shader is not None:
      return
    shader = rl.load_shader_from_memory(VERTEX_SHADER, FRAGMENT_SHADER)
    names = ('geometry', 'backgroundNormals', 'indicatorNormals', 'style', 'dotGeometry', 'gradient')
    locations = {name: rl.get_shader_location(shader, name) for name in names}
    if not shader.id or any(location < 0 for location in locations.values()):
      if shader.id and shader.id != rl.rl_get_shader_id_default():
        rl.unload_shader(shader)
      raise RuntimeError('Torque bar shader unavailable')
    self._uniforms = {name: (location, np.zeros(4, dtype=np.float32)) for name, location in locations.items()}
    self._previous.clear()
    self._shader = shader

  def render(self, rect: rl.Rectangle, torque: float, alpha: float, screen_width: float, cap_radius: float,
             offset: tuple[float, float] = (0, 0)) -> None:
    if alpha <= 0:
      return
    self.prepare()
    scale = arc_scale(rect.width, rect.height, screen_width)
    strength = max(0.0, min(1.0, (abs(torque) - 0.5) * 2))
    torque_offset = MIN_OFFSET * scale + (MAX_OFFSET * scale - MIN_OFFSET * scale) * strength
    thickness = MIN_HEIGHT * scale + (MAX_HEIGHT * scale - MIN_HEIGHT * scale) * strength
    radius = RADIUS * scale
    middle_radius = radius + thickness / 2
    cap = min(cap_radius, thickness / 2)
    cx = rect.x + rect.width / 2 + 8 + offset[0]
    cy = rect.y + rect.height + radius - torque_offset + offset[1]
    half_span = math.radians(alpha * TORQUE_ANGLE_SPAN / 2)
    sine, cosine = math.sin(half_span), math.cos(half_span)
    end_sine, end_cosine = math.sin(half_span * torque), math.cos(half_span * torque)
    start, end = ((end_sine, -end_cosine), (0, -1)) if torque < 0 else ((0, -1), (end_sine, -end_cosine))
    dot_y = rect.y + rect.height - torque_offset - thickness / 2 + offset[1]
    indicator_alpha = int(255 * 0.9 * alpha) / 255
    warmth = max(0.0, min(1.0, (abs(torque) - 0.75) * 4))
    extent = (middle_radius + thickness / 2 - cap) * sine + cap
    reach = max(0.65 * extent, 0.0001)
    values = {
      'geometry': (cx, cy, middle_radius, thickness / 2),
      'backgroundNormals': (-sine, -cosine, sine, -cosine),
      'indicatorNormals': (*start, *end),
      'style': (cap, int(255 * (0.25 + 0.25 * strength) * alpha) / 255,
                indicator_alpha, indicator_alpha if abs(torque) < 0.5 else 0),
      'dotGeometry': (int(cx), int(dot_y), 5 * scale, 0),
      'gradient': (math.copysign(1 / reach, torque), 1 - warmth + warmth * (115 / 255),
                   warmth * (85 / 255), 1 - warmth),
    }
    # Cover subpixel edge samples without allocating an offscreen surface, including desktop downscaling.
    extent += 4
    top = rect.y + rect.height - torque_offset - thickness + offset[1] - 4
    bottom = cy - (radius + cap) * cosine + cap + 4
    # Use the native bindings without per-vertex Python wrappers.
    native_rl.begin_shader_mode(self._shader)
    try:
      for name, value in values.items():
        if self._previous.get(name) != value:
          location, pointer = self._uniforms[name]
          pointer[0:4] = value
          native_rl.set_shader_value(self._shader, location, pointer, rl.ShaderUniformDataType.SHADER_UNIFORM_VEC4)
          self._previous[name] = value
      # rlgl may transform positions on the CPU; UVs keep distance calculations in logical pixels.
      left, right = cx - extent, cx + extent
      native_rl.rl_set_texture(0)
      native_rl.rl_begin(rl.RL_QUADS)
      for x, y in ((left, top), (left, bottom), (right, bottom), (right, top)):
        native_rl.rl_tex_coord2f(x, y)
        native_rl.rl_vertex2f(x, y)
      native_rl.rl_end()
    finally:
      native_rl.end_shader_mode()

  def close(self) -> None:
    if self._shader is not None and rl.is_window_ready():
      rl.unload_shader(self._shader)
    self._shader = None
    self._uniforms.clear()
    self._previous.clear()
