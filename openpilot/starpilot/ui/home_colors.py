"""Mode colors shared by the large banner and compact status atom."""
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.home_state import HomeMode


def _color(red: int, green: int, blue: int, alpha: int) -> rl.Color:
  return rl.Color(red, green, blue, alpha)


def _lerp_color(start: rl.Color, end: rl.Color, progress: float, alpha: int) -> rl.Color:
  progress = max(0.0, min(1.0, progress))
  return rl.Color(
    round(start.r + (end.r - start.r) * progress),
    round(start.g + (end.g - start.g) * progress),
    round(start.b + (end.b - start.b) * progress),
    alpha,
  )


def _conditional_colors(variant: HomeMode, alpha: int) -> tuple[rl.Color, rl.Color, rl.Color, rl.Color]:
  blue = _color(35, 149, 255, alpha)
  mint = _color(20, 255, 171, alpha)
  orange = _color(255, 138, 22, alpha)
  red = _color(219, 56, 34, alpha)
  if variant == HomeMode.CONDITIONAL_EXPERIMENTAL:
    return blue, mint, orange, red
  return orange, red, blue, mint


def mode_banner_color(variant: HomeMode, progress: float, alpha: int = 255) -> rl.Color:
  progress = max(0.0, min(1.0, progress))
  if variant == HomeMode.CHILL:
    return _lerp_color(_color(20, 255, 171, alpha), _color(35, 149, 255, alpha), progress, alpha)
  if variant == HomeMode.EXPERIMENTAL:
    return _lerp_color(_color(255, 155, 63, alpha), _color(219, 56, 34, alpha), progress, alpha)

  dominant_start, dominant_end, target_start, target_end = _conditional_colors(variant, alpha)
  if progress <= 0.58:
    return _lerp_color(dominant_start, dominant_end, progress / 0.58, alpha)
  if progress <= 0.80:
    return _lerp_color(dominant_end, target_start, (progress - 0.58) / 0.22, alpha)
  return _lerp_color(target_start, target_end, ((progress - 0.80) / 0.20) * 0.67, alpha)


def mode_atom_color(variant: HomeMode, progress: float, alpha: int = 255) -> rl.Color:
  # The compact atom reads left-to-right as blue to mint in fixed Chill mode.
  if variant == HomeMode.CHILL:
    progress = 1.0 - progress
  return mode_banner_color(variant, progress, alpha)


def draw_mode_banner_gradient(rect: rl.Rectangle, variant: HomeMode, alpha: int = 255) -> None:
  if variant in (HomeMode.CHILL, HomeMode.EXPERIMENTAL):
    rl.draw_rectangle_gradient_h(
      int(rect.x), int(rect.y), int(rect.width), int(rect.height),
      mode_banner_color(variant, 0.0, alpha), mode_banner_color(variant, 1.0, alpha),
    )
    return

  transition_start = int(rect.x + rect.width * 0.58)
  transition_end = int(rect.x + rect.width * 0.80)
  right = int(rect.x + rect.width)
  rl.draw_rectangle_gradient_h(
    int(rect.x), int(rect.y), transition_start - int(rect.x), int(rect.height),
    mode_banner_color(variant, 0.0, alpha), mode_banner_color(variant, 0.58, alpha),
  )
  rl.draw_rectangle_gradient_h(
    transition_start, int(rect.y), transition_end - transition_start, int(rect.height),
    mode_banner_color(variant, 0.58, alpha), mode_banner_color(variant, 0.80, alpha),
  )
  rl.draw_rectangle_gradient_h(
    transition_end, int(rect.y), right - transition_end, int(rect.height),
    mode_banner_color(variant, 0.80, alpha), mode_banner_color(variant, 1.0, alpha),
  )
