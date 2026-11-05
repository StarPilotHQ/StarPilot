"""Frozen C3/C4 side-camera crop shaders; renderer owns their GPU lifetime."""

import platform

PIP_SHADER_VERSION = "#version 330 core\n" if platform.system() == "Darwin" else "#version 300 es\nprecision mediump float;\n"

PIP_VERTEX_SHADER = PIP_SHADER_VERSION + """
in vec3 vertexPosition;
in vec2 vertexTexCoord;
in vec3 vertexNormal;
in vec4 vertexColor;
uniform mat4 mvp;
out vec2 fragTexCoord;
out vec4 fragColor;
void main() {
  fragTexCoord = vertexTexCoord;
  fragColor = vertexColor;
  gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""

PIP_FRAGMENT_SHADER = PIP_SHADER_VERSION + """
in vec2 fragTexCoord;
uniform sampler2D texture0;
uniform sampler2D texture1;
uniform vec2 uCropMin;
uniform vec2 uCropSize;
uniform int uFlipX;
uniform vec3 uGain;
uniform float uSaturation;
out vec4 fragColor;

const float BUBBLE_REFRACTION = 0.016;
const float BUBBLE_EDGE_DARKEN = 0.15;
const float BUBBLE_HIGHLIGHT = 0.12;
const float BUBBLE_RIM = 0.10;
const float BUBBLE_EDGE_TRANSPARENCY = 0.12;

void main() {
  // Calculate the mask before sampling: fragments outside the bubble do no texture work.
  vec2 p = fragTexCoord * 2.0 - 1.0;
  float radius = length(p);
  float aa = max(fwidth(radius), 0.00001);
  float alpha = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, radius);
  if (radius > 1.0 + aa) {
    discard;
  }

  // A shallow hemisphere gives the image a convex bubble surface without a mesh or pass.
  float z = sqrt(max(0.0, 1.0 - dot(p, p)));
  vec2 sampleCoord = clamp(
    fragTexCoord + p * BUBBLE_REFRACTION * (1.0 - z),
    0.001,
    0.999
  );

  vec2 cropCoord = sampleCoord;
  if (uFlipX == 1) {
    cropCoord.x = 1.0 - cropCoord.x;
  }
  vec2 uv = uCropMin + cropCoord * uCropSize;
  float y = texture(texture0, uv).r;
  vec2 c = texture(texture1, uv).ra - 0.5;
  vec3 rgb = vec3(y + 1.402 * c.y, y - 0.344 * c.x - 0.714 * c.y, y + 1.772 * c.x);
  // Cancel the cabin camera's IR (no IR-cut filter) magenta cast; leave clipped highlights white.
  // The cast is strongest through the glass and absent in open air, so one gain can't fix both:
  // a mild desaturation hides what's left (pink foliage, a slightly cyan sky).
  rgb = mix(rgb * uGain, rgb, smoothstep(0.95, 1.0, max(rgb.r, max(rgb.g, rgb.b))));
  rgb = mix(vec3(dot(rgb, vec3(0.299, 0.587, 0.114))), rgb, uSaturation);

  // Keep the interior gradient and use cheap analytic lighting instead of specular math.
  float edgeShade = smoothstep(0.22, 0.98, radius);
  rgb *= mix(1.0, 1.0 - BUBBLE_EDGE_DARKEN, edgeShade);

  vec2 highlightOffset = p - vec2(-0.28, -0.34);
  float highlight = 1.0 - smoothstep(0.0, 0.22, dot(highlightOffset, highlightOffset));
  rgb += vec3(1.0) * highlight * z * BUBBLE_HIGHLIGHT;

  // Let the rim blend into the camera image and the UI underneath it.
  float rim = smoothstep(0.60, 0.99, radius);
  rgb = mix(rgb, vec3(0.48, 0.70, 1.0), rim * BUBBLE_RIM);
  float surfaceAlpha = alpha * (1.0 - rim * BUBBLE_EDGE_TRANSPARENCY);
  fragColor = vec4(rgb, surfaceAlpha);
}
"""

PIP_CURVED_FRAGMENT_SHADER = PIP_SHADER_VERSION + """
in vec2 fragTexCoord;
uniform sampler2D texture0;
uniform sampler2D texture1;
uniform vec2 uCropMin;
uniform vec2 uCropSize;
uniform int uFlipX;
uniform vec3 uGain;
uniform float uSaturation;
uniform vec2 uRectSize;
out vec4 fragColor;

const float CORNER_RADIUS_FRACTION = 0.22;
const float CURVE_AMOUNT = 0.07;
const float EDGE_DARKEN = 0.14;
const float RIM_BLEND = 0.06;

void main() {
  vec2 p = fragTexCoord * 2.0 - 1.0;
  float halfW = uRectSize.x * 0.5;
  float halfH = uRectSize.y * 0.5;
  float radius = CORNER_RADIUS_FRACTION * min(uRectSize.x, uRectSize.y);

  // Rounded-rectangle SDF in pixel space; mask before sampling.
  vec2 q = abs(vec2(p.x * halfW, p.y * halfH)) - (vec2(halfW, halfH) - radius);
  float dist = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
  float aa = max(fwidth(dist), 0.00001);
  float alpha = 1.0 - smoothstep(-aa, aa, dist);
  if (dist > aa) {
    discard;
  }

  // Gentle convex curvature along both axes to mimic the curved OLED panel.
  float curve = CURVE_AMOUNT * (1.0 - p.x * p.x) * (1.0 - p.y * p.y);
  vec2 sampleCoord = clamp(fragTexCoord + curve * vec2(0.0, 0.5), 0.001, 0.999);

  // The saved mask is a square crop, but the curved panel is wider than tall.
  // Sample an aspect-matched horizontal band of that square (centered) instead
  // of stretching it, so the image is never distorted. The circle's diameter
  // (the square crop) becomes the panel's length; the height follows the aspect.
  float aspect = uRectSize.x / max(uRectSize.y, 0.0001);
  vec2 cropCoord = sampleCoord;
  if (aspect >= 1.0) {
    cropCoord.y = 0.5 + (sampleCoord.y - 0.5) / aspect;
  } else {
    cropCoord.x = 0.5 + (sampleCoord.x - 0.5) * aspect;
  }
  if (uFlipX == 1) {
    cropCoord.x = 1.0 - cropCoord.x;
  }
  vec2 uv = uCropMin + cropCoord * uCropSize;
  float y = texture(texture0, uv).r;
  vec2 c = texture(texture1, uv).ra - 0.5;
  vec3 rgb = vec3(y + 1.402 * c.y, y - 0.344 * c.x - 0.714 * c.y, y + 1.772 * c.x);
  // Cancel the cabin camera's IR (no IR-cut filter) magenta cast; leave clipped highlights white.
  // The cast is strongest through the glass and absent in open air, so one gain can't fix both:
  // a mild desaturation hides what's left (pink foliage, a slightly cyan sky).
  rgb = mix(rgb * uGain, rgb, smoothstep(0.95, 1.0, max(rgb.r, max(rgb.g, rgb.b))));
  rgb = mix(vec3(dot(rgb, vec3(0.299, 0.587, 0.114))), rgb, uSaturation);

  // Let the rim blend into the camera image and the UI underneath it.
  float edgeShade = smoothstep(-radius, 0.0, dist);
  rgb *= mix(1.0, 1.0 - EDGE_DARKEN, edgeShade);
  float rim = smoothstep(radius * 0.55, radius, radius - dist);
  rgb = mix(rgb, vec3(0.48, 0.70, 1.0), rim * RIM_BLEND);

  fragColor = vec4(rgb, alpha);
}
"""

