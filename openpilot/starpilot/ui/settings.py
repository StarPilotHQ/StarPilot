"""Settings entry presentation with supplied state and no service construction."""

from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import clip

from openpilot.starpilot.ui.home_geometry import outside_rounded_border
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile
from openpilot.starpilot.ui.settings_assets import SettingsAssets
from openpilot.starpilot.ui.settings_geometry import ACCENT, constellation, draw_constellation_nodes, draw_hud_background, draw_settings_header
from openpilot.starpilot.ui.settings_state import compact_menu, Destination, RAIL, TILES, SettingsState, tile_rects

ICON_COLOR = rl.Color(246, 242, 254, 255)
ICON_SCALE = 0.8 * (100 / 60) * 1.6


class SettingsView:
  def __init__(self, fonts: BitmapFonts, asset_directory: Path):
    self.fonts = fonts
    self.assets = SettingsAssets(asset_directory)

  def prepare(self) -> None:
    """Prepare owned vector targets outside any active render-texture mode."""
    if self.fonts.profile == Profile.LARGE:
      for _, _, icon in TILES:
        self.assets.prepare_icon(icon, ICON_SCALE, ICON_COLOR)

  def render(self, state: SettingsState) -> None:
    if self.fonts.profile == Profile.LARGE:
      self._large(state)
    else:
      self._compact(state)

  def _large(self, state: SettingsState):
    draw_settings_header(self.fonts, state.sidebar_expanded, "StarPilot", back=True)
    for (_, title, icon), bounds in zip(TILES, tile_rects(state), strict=True):
      rect = rl.Rectangle(*bounds)
      draw_hud_background(rect, ACCENT)
      nodes, edges = constellation(title, rect)
      draw_constellation_nodes(nodes, edges, rect, ACCENT, 1)
      text_scale = max(0.82, min(1.12, min(rect.width / 360, rect.height / 205)))
      size = max(44, round(50 * text_scale))
      icon_height = 128.0
      top = rect.y + max(0, (rect.height - icon_height - 8 - size) / 2)
      self.assets.icon(icon, rect.x + (rect.width - icon_height) / 2, top, ICON_SCALE, ICON_COLOR)
      self._fit_title(title, rect.x + 16, top + icon_height + 8, rect.width - 32, size)
    self.render_rail(state)

  def _fit_title(self, text, x, y, width, size):
    original_size = size
    measured = self.fonts.measure(text, FontRole.MEDIUM, size)
    if measured.width > width:
      high, low = max(16, round(size * width / measured.width)), 16
      size = high
      while low < high:
        middle = (low + high + 1) // 2
        if self.fonts.measure(text, FontRole.MEDIUM, middle).width <= width:
          low = size = middle
        else:
          high = middle - 1
      measured = self.fonts.measure(text, FontRole.MEDIUM, size)
    self.fonts.draw(text, FontRole.MEDIUM, size, round(x + (width - measured.width) / 2), round(y + (original_size - size) / 2))

  def render_rail(self, state: SettingsState, selected: Destination = Destination.STAR) -> None:
    """Draw the shared large navigation rail for a supplied selected panel."""
    width = 500 if state.sidebar_expanded else 0
    rl.draw_rectangle_rec(rl.Rectangle(0, 0, width, 1080), rl.BLACK)
    rl.draw_rectangle_rec(rl.Rectangle(0, 0, 2, 1080), rl.Color(139, 92, 246, 55))
    rl.draw_rectangle_rounded(rl.Rectangle(-40, 501, 90, 160), 0.5, 30, rl.Color(139, 92, 246, 30))
    tab = rl.Rectangle(-30, 511, 70, 140)
    rl.draw_rectangle_rounded(tab, 0.5, 30, rl.Color(139, 92, 246, 60))
    outside_rounded_border(tab, 0.5, 30, 2, rl.Color(139, 92, 246, 160))
    sign = 1 if state.sidebar_expanded else -1
    points = (rl.Vector2(20 + sign * 12, 557), rl.Vector2(20 - sign * 12, 581), rl.Vector2(20 + sign * 12, 605))
    for color, thickness in ((rl.Color(255, 255, 255, 35), 7), (rl.WHITE, 2.8)):
      rl.draw_line_ex(points[0], points[1], thickness, color)
      rl.draw_line_ex(points[1], points[2], thickness, color)
    if not state.sidebar_expanded:
      return
    rl.draw_rectangle_rounded(rl.Rectangle(150, 60, 200, 200), 1, 20, rl.Color(41, 41, 41, 255))
    texture = self.assets.image("icons/backspace.png", 70, 70)
    rl.draw_texture_pro(texture, rl.Rectangle(0, 0, 70, 70), rl.Rectangle(215, 125, 70, 70), rl.Vector2(0, 0), 0, rl.WHITE)
    for index, (destination, text) in enumerate(RAIL):
      size = self.fonts.measure(text, FontRole.MEDIUM, 65)
      self.fonts.draw(text, FontRole.MEDIUM, 65, round(400 - size.width), round(300 + index * 110 + (110 - size.height) / 2),
                      rl.WHITE if destination == selected else rl.Color(128, 128, 128, 255))

  def _compact(self, state: SettingsState):
    y = state.compact_y
    viewport = rl.Rectangle(0, y, 536, 240)
    rl.draw_rectangle_rec(rl.Rectangle(0, 0, 536, 240), rl.Color(0, 0, 0, int(200 * max(0, min(1, 1 - y / 240)))))
    rl.draw_rectangle_rec(rl.Rectangle(0, y, 536, 260), rl.BLACK)
    background = self.assets.image("icons_mici/buttons/button_rectangle.png", 402, 180)
    clip.begin_scissor_mode(0, int(y), 536, 240)
    try:
      menu = compact_menu(state)
      for index in reversed(range(len(menu))):
        destination, text, filename, width, height = menu[index]
        x, top = 20 + index * 422 + state.compact_scroll_x, y + 30
        if x >= 536 or x + 402 <= 0:
          continue
        rl.draw_texture_ex(background, rl.Vector2(x, top), 0, 1, rl.WHITE)
        font_size = 40 if destination == Destination.FORCE_DRIVE else 42 if destination == Destination.DRIVING_MODEL else (
          48 if destination in (Destination.GALAXY, Destination.PAIR) else 64)
        if destination == Destination.DRIVING_CONTROLS:
          font_size = 56
          text = text.replace(" ", "\n", 1)
        measured = self.fonts.measure(text, FontRole.BOLD, font_size)
        self.fonts.draw(text, FontRole.BOLD, font_size, x + 40, top + 180 - 23 - measured.height,
                        rl.Color(255, 255, 255, 229))
        if filename is not None:
          icon = self.assets.image(filename, width, height)
          width, height = icon.width, icon.height
          rl.draw_texture_pro(icon, rl.Rectangle(0, 0, width, height),
                              rl.Rectangle(x + 402 - 30 - width / 2, top + 30 + height / 2, width, height),
                              rl.Vector2(width / 2, height / 2), 0, rl.Color(255, 255, 255, 229))
    finally:
      clip.end_scissor_mode()
    rl.draw_rectangle_gradient_h(0, int(y), 20, 240, rl.Color(0, 0, 0, 204), rl.BLANK)
    rl.draw_rectangle_gradient_h(516, int(y), 20, 240, rl.BLANK, rl.Color(0, 0, 0, 204))
    indicator = self.assets.image("icons_mici/settings/horizontal_scroll_indicator.png", 96, 48)
    rl.draw_texture_pro(indicator, rl.Rectangle(0, 0, 96, 48), rl.Rectangle(0, max(viewport.y, 0) + 216, 100, 48),
                        rl.Vector2(0, 0), 0, rl.Color(255, 255, 255, 114))
    bar = rl.Rectangle(165.5, state.nav_bar_y, 205, 8)
    rl.draw_rectangle_rounded(bar, 1, 6, rl.Color(255, 255, 255, int(255 * 0.9 * state.nav_bar_alpha)))
    outside_rounded_border(bar, 1, 6, 2, rl.Color(0, 0, 0, int(255 * 0.3 * state.nav_bar_alpha)))

  def close(self) -> None:
    self.assets.close()
