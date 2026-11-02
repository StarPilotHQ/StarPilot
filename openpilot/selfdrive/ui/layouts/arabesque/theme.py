from dataclasses import dataclass

from openpilot.starpilot.ui.raylib import rl

from openpilot.system.ui.lib.application import FontWeight


@dataclass(frozen=True)
class Theme:
  background: rl.Color
  surface: rl.Color
  text: rl.Color
  secondary_text: rl.Color
  control: rl.Color
  pressed: rl.Color
  font_weight: FontWeight = FontWeight.NORMAL


# Shared colors are read-only; use dataclasses.replace() for a tool's overrides.
DEFAULT_THEME = Theme(
  background=rl.Color(12, 10, 18, 255),
  surface=rl.Color(58, 42, 82, 255),
  text=rl.Color(236, 242, 250, 255),
  secondary_text=rl.Color(210, 196, 229, 255),
  control=rl.Color(205, 181, 247, 255),
  pressed=rl.Color(175, 140, 235, 255),
)
