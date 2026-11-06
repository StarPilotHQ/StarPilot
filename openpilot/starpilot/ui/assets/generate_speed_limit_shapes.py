"""Rasterize fixed SLC geometry once; runtime only tints and draws atlas quads."""

import numpy as np
from PIL import Image

from openpilot.starpilot.ui.large_speed_geometry import (SIGN_WIDTH, SIGN_HEIGHT, CONFIRMATION_BUTTON, CONFIRMATION_HEIGHT,
                                                        CONFIRMATION_HEADER, CONFIRMATION_PADDING)
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
    if name == 'ring':
      radius = np.hypot(xx, yy)
      mask = (radius <= 59) & (radius >= 51)
      rgba[..., 3] = np.rint(coverage(mask, width, height) * 255).astype(np.uint8)
    elif name == 'outline':
      # Signed distance to a rounded rectangle; supersampling is offline only.
      half_width, half_height = SIGN_WIDTH / 2 - 2, SIGN_HEIGHT / 2 - 2
      qx, qy = np.abs(xx) - (half_width - 8), np.abs(yy) - (half_height - 8)
      distance = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - 8
      rgba[..., 3] = np.rint(coverage((distance >= -2) & (distance <= 0), width, height) * 255).astype(np.uint8)
    elif name.startswith('button'):
      profile = name.split('_')[1]
      half_width = (CONFIRMATION_BUTTON[profile] - 8) / 2
      half_height = (CONFIRMATION_HEIGHT[profile] - CONFIRMATION_HEADER - 2 * CONFIRMATION_PADDING) / 2
      qx, qy = np.abs(xx) - (half_width - 16), np.abs(yy) - (half_height - 16)
      distance = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - 16
      half_stroke = 1.5 if name.endswith('_pressed') else 1.
      fill = coverage(np.abs(distance) <= half_stroke, width, height)
      border = coverage((np.abs(distance) > half_stroke) & (np.abs(distance) <= half_stroke + .8), width, height) * .95
      alpha = fill + border
      # Bake the tight contour into the same quad so curved edges never overlap or facet.
      for channel, dark in enumerate((6, 14, 10)):
        rgba[..., channel] = np.rint(np.divide(fill * 255 + border * dark, alpha,
                                              out=np.full_like(alpha, dark), where=alpha > 0)).astype(np.uint8)
      rgba[..., 3] = np.rint(alpha * 255).astype(np.uint8)
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
      border = coverage((distance_sq <= outline**2) & ~inside, width, height) * .87
      alpha = fill + border
      for channel, dark in enumerate((6, 14, 10)):
        rgba[..., channel] = np.rint(np.divide(fill * 255 + border * dark, alpha,
                                              out=np.full_like(alpha, dark), where=alpha > 0)).astype(np.uint8)
      rgba[..., 3] = np.rint(alpha * 255).astype(np.uint8)
    atlas.paste(Image.fromarray(rgba), (x, y))
  return atlas


if __name__ == '__main__':
  generate().save(ATLAS_PATH, optimize=True)
