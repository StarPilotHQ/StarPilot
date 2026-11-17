"""Display-only path and lane colors, shared by both native UI profiles."""

from functools import lru_cache

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.onroad_customization import ROAD_COLORS
from openpilot.system.ui.lib.shader_polygon import Gradient


def path_mode(style: dict) -> str:
  # "default" predates per-layout path styles; RainbowPath retirement rewrote rainbow users.
  mode = style.get("pathMode", "default")
  return "acceleration" if mode == "default" else mode


def lane_color(style: dict, adjacent: bool, default: rl.Color, probability: float) -> rl.Color:
  color = style.get("pathEdge" if adjacent else "laneLines")
  if color is None:
    return default
  red, green, blue, alpha = _rgba(color)
  return rl.Color(red, green, blue, int(max(0.0, min(0.7, probability)) * alpha))


def _rgba(color: str) -> tuple[int, ...]:
  return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5, 7))


@lru_cache(maxsize=16)
def solid_gradient(color: str = ROAD_COLORS["path"]) -> Gradient:
  red, green, blue, alpha = _rgba(color)
  return Gradient(start=(0.0, 1.0), end=(0.0, 0.0),
                  colors=[rl.Color(red, green, blue, int(alpha * fade)) for fade in (1.0, 0.55, 0.10)],
                  stops=[0.0, 0.5, 1.0])


@lru_cache(maxsize=16)
def edge_gradient(color: str) -> Gradient:
  red, green, blue, alpha = _rgba(color)
  return Gradient(start=(0.0, 1.0), end=(0.0, 0.0),
                  colors=[rl.Color(red, green, blue, int(alpha * fade)) for fade in (0.4, 0.35, 0.0)],
                  stops=[0.0, 0.5, 1.0])
