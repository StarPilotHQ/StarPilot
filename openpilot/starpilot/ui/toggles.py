"""Large first-viewport Toggles presentation; all values come from the caller."""

import hashlib
import json
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import clip

from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile
from openpilot.starpilot.ui.toggles_state import Personality, ToggleKey, TogglesState


ROWS = (("Enable openpilot", "chffr_wheel.png", "enabled"),
        ("Experimental Mode", "experimental_white.png", "experimental"),
        ("Safe Mode", "warning.png", "safe_mode"),
        ("Disengage on Accelerator Pedal", "disengage_on_accelerator.png", "disengage_accelerator"),
        ("Driving Personality", "speed_limit.png", "personality"),
        ("Enable Lane Departure Warnings", "warning.png", "lane_departure"),
        ("Always-On Driver Monitoring", "monitoring.png", "always_on_dm"),
        ("Right Hand Driving", "monitoring.png", "right_hand_driving"),
        ("Record and Upload Driver Camera", "monitoring.png", "record_front"),
        ("Record and Upload Microphone Audio", "microphone.png", "record_audio"),
        ("Use Metric System", "metric.png", "metric"))


class TogglesView:
  def __init__(self, fonts: BitmapFonts, asset_directory):
    if fonts.profile != Profile.LARGE:
      raise ValueError("Only the large Toggles viewport is captured")
    self.fonts = fonts
    self.assets = asset_directory
    self.textures: dict[str, rl.Texture] = {}
    self._reviewed = {entry["file"]: entry for entry in
                      json.loads(Path(__file__).with_name("toggles-assets.json").read_text())["files"]}

  def _icon(self, filename: str) -> rl.Texture:
    if filename not in self.textures:
      path = self.assets / "icons" / filename
      data = path.read_bytes()
      reviewed = self._reviewed[f"icons/{filename}"]
      if len(data) != reviewed["bytes"] or hashlib.sha256(data).hexdigest() != reviewed["sha256"]:
        raise ValueError(f"Unreviewed Toggles icon {filename}")
      texture = rl.load_texture(str(path))
      if not texture.id:
        raise RuntimeError(f"Unable to load Toggles icon {filename}")
      self.textures[filename] = texture
    return self.textures[filename]

  def render(self, state: TogglesState) -> None:
    rl.draw_rectangle_rounded(rl.Rectangle(510, 10, 1640, 1060), 0.04, 30, rl.BLACK)
    clip.begin_scissor_mode(550, 50, 1560, 980)
    try:
      for index, (title, icon, field) in enumerate(ROWS):
        top = round(50 + index * 171 - state.scroll_y)
        if top + 171 < 50 or top > 1030:
          continue
        measured = self.fonts.measure(title, FontRole.NORMAL, 50)
        self.fonts.draw(title, FontRole.NORMAL, 50, 670, top + (170 - measured.height) // 2)
        texture = self._icon(icon)
        rl.draw_texture_pro(texture, rl.Rectangle(0, 0, texture.width, texture.height),
                            rl.Rectangle(570, top + 45, 80, 80), rl.Vector2(0, 0), 0, rl.WHITE)
        if field == "personality":
          for button_index, personality in enumerate(Personality):
            rect = rl.Rectangle(1305 + button_index * 275, top + 35, 255, 100)
            selected = state.personality == personality
            rl.draw_rectangle_rounded(rect, 1, 20, rl.Color(51, 171, 76, 255) if selected else rl.Color(57, 57, 57, 255))
            label = personality.value.capitalize()
            size = self.fonts.measure(label, FontRole.MEDIUM, 40)
            self.fonts.draw(label, FontRole.MEDIUM, 40, rect.x + (rect.width - size.width) / 2,
                            rect.y + (rect.height - size.height) / 2, rl.Color(228, 228, 228, 255))
        else:
          enabled = getattr(state, field)
          if ToggleKey(field) in state.unavailable:
            self.fonts.draw("N/A", FontRole.MEDIUM, 38, 1960, top + 65, rl.GRAY)
            continue
          rl.draw_rectangle_rounded(rl.Rectangle(1955, top + 55, 150, 60), 1, 10,
                                    rl.Color(51, 171, 76, 255) if enabled else rl.Color(57, 57, 57, 255))
          rl.draw_circle(2070 if enabled else 1990, top + 85, 40, rl.WHITE)
        if index < len(ROWS) - 1:
          rl.draw_line(590, top + 170, 2070, top + 170, rl.GRAY)
    finally:
      clip.end_scissor_mode()

  def close(self) -> None:
    if rl.is_window_ready():
      for texture in self.textures.values():
        rl.unload_texture(texture)
    self.textures.clear()
