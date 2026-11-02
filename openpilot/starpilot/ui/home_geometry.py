"""Native geometry adjustments for the protected Home presentation."""

from openpilot.starpilot.ui.raylib import rl


def outside_rounded_border(rect, roundness, segments, thickness, color):
  # Raylib 6 offsets outline vertices by half a pixel. Keep the intended corner
  # radius while compensating for that offset at both edges.
  shortest = min(rect.width, rect.height)
  expanded = rl.Rectangle(rect.x - 0.5, rect.y - 0.5, rect.width + 1, rect.height + 1)
  rl.draw_rectangle_rounded_lines_ex(expanded, roundness * shortest / (shortest + 1), segments, thickness, color)
