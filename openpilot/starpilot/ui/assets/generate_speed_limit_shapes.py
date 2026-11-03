"""Rasterize fixed SLC geometry once; runtime only tints and draws atlas quads."""

import numpy as np
from PIL import Image

from openpilot.starpilot.ui.speed_limit_shapes import ATLAS_PATH, ATLAS_SIZE, SPRITES, SPRITE_DENSITIES


SAMPLES = 8


def coverage(mask, width, height):
  return mask.reshape(height, SAMPLES, width, SAMPLES).mean(axis=(1, 3))


def generate():
  atlas = Image.new('RGBA', ATLAS_SIZE)
  for name, (x, y, width, height) in SPRITES.items():
    density = SPRITE_DENSITIES.get(name, 1)
    px = ((np.arange(width * SAMPLES) + .5) / SAMPLES - width / 2) / density
    py = ((np.arange(height * SAMPLES) + .5) / SAMPLES - height / 2) / density
    xx, yy = np.meshgrid(px, py)
    rgba = np.full((height, width, 4), 255, dtype=np.uint8)
    if name in ('disc', 'ring'):
      radius = np.hypot(xx, yy)
      # End the white backing inside the opaque rim, avoiding an outer white fringe.
      mask = radius <= 55 if name == 'disc' else (radius <= 59) & (radius >= 51)
      rgba[..., 3] = np.rint(coverage(mask, width, height) * 255).astype(np.uint8)
    elif name in ('outline', 'override_outline'):
      # Signed distance to a rounded rectangle; supersampling is offline only.
      half_width, half_height = (57, 64) if name == 'outline' else (66, 47)
      qx, qy = np.abs(xx) - (half_width - 8), np.abs(yy) - (half_height - 8)
      distance = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - 8
      rgba[..., 3] = np.rint(coverage((distance >= -2) & (distance <= 0), width, height) * 255).astype(np.uint8)
    elif name == 'override_circle':
      distance = np.hypot(xx, yy) - 60
      alpha = coverage(distance <= 0, width, height)
      interior = coverage(distance <= -6, width, height)
      weight = np.divide(interior, alpha, out=np.zeros_like(alpha), where=alpha > 0)[..., None]
      rgba[..., :3] = np.rint(np.array((201, 34, 49)) * (1 - weight) + 255 * weight).astype(np.uint8)
      rgba[..., 3] = np.rint(alpha * 255).astype(np.uint8)
    elif name == 'override_slash':
      mask = np.hypot(np.maximum(np.abs(xx) - 20.5, 0), yy) <= 1.2
      rgba[..., 3] = np.rint(coverage(mask, width, height) * 255).astype(np.uint8)
    elif name in ('wheel_plus', 'wheel_minus'):
      qx, qy = np.abs(xx) - 4, np.abs(yy) - 4
      distance = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - 5
      border = coverage(np.abs(distance) <= .5, width, height)
      mark = (np.abs(xx) <= 5) & (np.abs(yy) <= .75)
      if name == 'wheel_plus':
        mark |= (np.abs(xx) <= .75) & (np.abs(yy) <= 5)
      mark = coverage(mark, width, height)
      alpha = border + mark
      # Keep nearby colors in transparent pixels for clean bilinear edges.
      center = (np.abs(xx[SAMPLES//2::SAMPLES, SAMPLES//2::SAMPLES]) < 6) & (np.abs(yy[SAMPLES//2::SAMPLES, SAMPLES//2::SAMPLES]) < 6)
      for channel, (edge, ink) in enumerate(zip((150, 170, 164), (216, 225, 222), strict=True)):
        rgba[..., channel] = np.rint(np.divide(border * edge + mark * ink, alpha,
                                              out=np.where(center, ink, edge).astype(float), where=alpha > 0)).astype(np.uint8)
      rgba[..., 3] = np.rint(alpha * 255).astype(np.uint8)
    elif name.startswith('confirmation'):
      pulse = name == 'confirmation_pulse'
      radius = 16 if name == 'confirmation_open' else 24
      qx = np.abs(xx) - ((width / density - 2) / 2 - radius)
      qy = np.abs(yy + (43 if pulse else 0)) - (76 - radius)
      distance = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - radius
      fill = coverage(distance <= 0, width, height)
      row = ((np.arange(height) + .5) / density - 1 + (86 if pulse else 0)) / 152
      if pulse:
        rgba[..., :3] = (191, 115, 253)
        alpha = fill * np.clip((row * 152 - 86) / 66, 0, 1)[:, None] * .20
      else:
        stops = (0, .08, .55, .64, 1)
        colors = np.array(((73, 69, 94), (57, 55, 77), (55, 52, 78), (65, 53, 90), (86, 55, 118)))
        base = np.stack([np.interp(row, stops, colors[:, c]) for c in range(3)], axis=-1)[:, None, :]
        colors = np.array(((173, 153, 205), (116, 103, 141), (94, 72, 124)))
        edge = np.stack([np.interp(row, (0, .5, 1), colors[:, c]) for c in range(3)], axis=-1)[:, None, :]
        rim = coverage((distance >= -1) & (distance <= 0), width, height)
        rim = np.divide(rim, fill, out=np.zeros_like(fill), where=fill > 0) * np.interp(row, (0, .5, 1), (.68, .46, .40))[:, None]
        alpha = rim + 230 / 255 * (1 - rim)
        rgba[..., :3] = np.rint((rim[..., None] * edge + 230 / 255 * (1 - rim[..., None]) * base) / alpha[..., None]).astype(np.uint8)
        alpha *= fill
      rgba[..., 3] = np.rint(alpha * 255).astype(np.uint8)
      rgba[rgba[..., 3] == 0] = 0
    else:
      half_height, wing, stem, head, outline = (14, 9, 3, 9, 1.4) if name == 'arrow_large' else (7, 5, 2.5, 5, 1)
      base = half_height - head
      points = ((-stem/2, -half_height), (stem/2, -half_height), (stem/2, base), (wing, base),
                (0, half_height), (-wing, base), (-stem/2, base))
      inside = ((np.abs(xx) <= stem/2) & (yy >= -half_height) & (yy <= base)) | (
        (yy >= base) & (yy <= half_height) & (np.abs(xx) <= wing * (half_height - yy) / head))
      distance_sq = np.full_like(xx, np.inf)
      for (ax, ay), (bx, by) in zip(points, (*points[1:], points[0]), strict=True):
        dx, dy = bx - ax, by - ay
        t = np.clip(((xx - ax) * dx + (yy - ay) * dy) / (dx*dx + dy*dy), 0, 1)
        distance_sq = np.minimum(distance_sq, (xx - ax - t*dx)**2 + (yy - ay - t*dy)**2)
      fill = coverage(inside, width, height)
      border = np.zeros_like(fill) if name == 'arrow_confirmation' else coverage((distance_sq <= outline**2) & ~inside, width, height) * .87
      alpha = fill + border
      for channel, dark in enumerate((6, 14, 10)):
        rgba[..., channel] = np.rint(np.divide(fill * 255 + border * dark, alpha,
                                              out=np.full_like(alpha, 255 if name == 'arrow_confirmation' else dark), where=alpha > 0)).astype(np.uint8)
      rgba[..., 3] = np.rint(alpha * 255).astype(np.uint8)
    atlas.paste(Image.fromarray(rgba), (x, y))
  return atlas


if __name__ == '__main__':
  generate().save(ATLAS_PATH, optimize=True)
