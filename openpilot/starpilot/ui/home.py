"""Offroad Home presentation driven exclusively by supplied immutable state.

The Home remains available before device pairing. Pairing uses the native dialog;
other setup, update, and alert flows remain with their existing owners.
"""

import hashlib
import json
import math
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import clip

from openpilot.starpilot.ui.home_colors import draw_mode_banner_gradient, mode_atom_color
from openpilot.starpilot.ui.home_geometry import outside_rounded_border
from openpilot.starpilot.ui.home_state import HomeMode, HomeState
from openpilot.starpilot.ui.moon import draw_moon
from openpilot.starpilot.ui.home_stats import DriveStatsDashboard
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile


BLUETOOTH_COLORS = {"off": rl.Color(255, 255, 255, 102), "on": rl.Color(255, 255, 255, 230), "connected": rl.Color(0, 130, 252, 230)}
JETLINK_COLORS = {"plugged": rl.Color(160, 160, 160, 255), "connected": rl.Color(70, 200, 110, 255)}


def draw_jetlink(rect: rl.Rectangle, state: str) -> None:
  """Jetlink's home icon: a tablet outline with a link dot."""
  color = JETLINK_COLORS.get(state)
  if color is None:
    return
  body = rl.Rectangle(rect.x + rect.width * 0.18, rect.y, rect.width * 0.64, rect.height)
  rl.draw_rectangle_rounded_lines_ex(body, 0.3, 8, max(2.0, rect.width * 0.08), color)
  rl.draw_circle_v(rl.Vector2(body.x + body.width / 2, body.y + body.height * 0.8), max(2.0, rect.width * 0.07), color)


def validate_home_asset(directory: Path, filename: str) -> Path:
  manifest = json.loads(Path(__file__).with_name("home-assets.json").read_text())
  entry = next((row for row in manifest["files"] if row["file"] == filename), None)
  if entry is None:
    raise ValueError(f"Unreviewed Home asset: {filename}")
  path = directory / filename
  if not path.is_file() or path.stat().st_size != entry["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
    raise ValueError(f"Home asset does not match the reviewed manifest: {filename}")
  return path


class HomeView:
  def __init__(self, fonts: BitmapFonts, assets: Path):
    self.fonts = fonts
    self.assets = assets
    self._pixel_scale = (max(1.0, rl.get_render_width() / max(rl.get_screen_width(), 1)),
                         max(1.0, rl.get_render_height() / max(rl.get_screen_height(), 1)))
    self._textures: dict[tuple, rl.Texture] = {}
    self._mode_textures: dict[HomeMode, rl.Texture] = {}

  def _texture(self, filename: str, width: int, height: int) -> rl.Texture:
    key = filename, width, height
    if key not in self._textures:
      path = validate_home_asset(self.assets, filename)
      image = rl.load_image(str(path))
      try:
        if image.data is None or image.width <= 0 or image.height <= 0:
          raise ValueError(f"Invalid Home image: {filename}")
        target_width = min(int(width * self._pixel_scale[0]), image.width)
        target_height = min(int(height * self._pixel_scale[1]), image.height)
        ratio = min(target_width / image.width, target_height / image.height)
        if ratio != 1:
          rl.image_resize(image, int(image.width * ratio), int(image.height * ratio))
        texture = rl.load_texture_from_image(image)
        if texture.id == 0:
          raise RuntimeError(f"Unable to load Home texture: {filename}")
      finally:
        if image.data is not None:
          rl.unload_image(image)
      rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      rl.set_texture_wrap(texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)
      texture.width, texture.height = width, height
      self._textures[key] = texture
    return self._textures[key]

  def _icon(self, filename, width, height, x, y, opacity=1.0):
    texture = self._texture(filename, width, height)
    rl.draw_texture_ex(texture, rl.Vector2(x, y), 0.0, 1.0, rl.Color(255, 255, 255, int(opacity * 255)))

  def _bluetooth(self, state, x, y):
    """Like the Wi-Fi icon: dimmed with a slash when off, white when on, blue with a device connected."""
    texture = self._texture("icons_mici/settings/bluetooth.png", 38, 38)
    color = BLUETOOTH_COLORS.get(state, BLUETOOTH_COLORS["off"])
    rl.draw_texture_ex(texture, rl.Vector2(x, y), 0.0, 1.0, color)
    if state == "off":
      pad = 38 * 0.08
      start, end = rl.Vector2(x + pad, y + pad), rl.Vector2(x + 38 - pad, y + 38 - pad)
      rl.draw_line_ex(start, end, 9.0, rl.BLACK)
      rl.draw_line_ex(start, end, 4.0, color)

  def _text(self, text, role, size, x, y, color=rl.WHITE):
    self.fonts.draw(text, role, size, x, y, color)

  def _measure(self, text, role, size):
    return self.fonts.measure(text, role, size)

  def _label(self, text, role, size, rect, color=rl.WHITE):
    measured = self._measure(text, role, size)
    self._text(text, role, size, rect.x, rect.y + (rect.height - measured.height) / 2, color)

  def render(self, state: HomeState) -> None:
    if self.fonts.profile == Profile.LARGE:
      self._large(state)
    else:
      self._compact(state)

  def _large(self, state: HomeState) -> None:
    self._sidebar(state)
    header = rl.Rectangle(340, 40, 1780, 80)
    detail = (" " + state.description if state.description else "") + (" - " + state.model_label if state.model_label else "")
    size = 48
    brand = self._measure("StarPilot", FontRole.BRAND, size + 2)
    model = self._measure(detail, FontRole.MEDIUM, size)
    if brand.width + model.width > header.width:
      size = max(32, int(size * header.width / (brand.width + model.width)))
      brand = self._measure("StarPilot", FontRole.BRAND, size + 2)
      model = self._measure(detail, FontRole.MEDIUM, size)
    rendered_width = min(brand.width + model.width, header.width)
    x = header.x + header.width - rendered_width
    self._label("StarPilot", FontRole.BRAND, size + 2, rl.Rectangle(x, header.y, brand.width, header.height))
    self._label(detail, FontRole.MEDIUM, size, rl.Rectangle(x + brand.width, header.y, model.width, header.height))
    if state.stats is None:
      for rect in (rl.Rectangle(340, 145, 1005, 895), rl.Rectangle(1370, 295, 750, 745)):
        rl.draw_rectangle_rounded(rect, 0.04, 12, rl.Color(18, 20, 29, 255))
        self._label("Drive history unavailable", FontRole.MEDIUM, 36,
                    rl.Rectangle(rect.x + 30, rect.y + 30, rect.width - 60, 70), rl.GRAY)
    else:
      dashboard = DriveStatsDashboard(self.fonts, state.stats)
      dashboard.render_overview(rl.Rectangle(340, 145, 1005, 895))
      dashboard.render_records(rl.Rectangle(1370, 295, 750, 745))
    self._mode_banner(state, rl.Rectangle(1370, 145, 750, 125))
    if not state.paired:
      pair = rl.Rectangle(1370, 300, 750, 130)
      rl.draw_rectangle_rounded(pair, 0.16, 10, rl.Color(24, 47, 67, 255))
      rl.draw_rectangle_rounded_lines_ex(pair, 0.16, 10, 4, rl.Color(87, 176, 229, 255))
      self._label("PAIR DEVICE", FontRole.SEMI_BOLD, 43,
                  rl.Rectangle(pair.x + 35, pair.y + 10, pair.width - 70, pair.height - 20))

  def _sidebar(self, state: HomeState):
    self._icon("images/button_settings.png", 200, 117, 50, 35)
    self._icon("images/button_home.png", 180, 180, 60, 860)
    for index in range(5):
      color = rl.WHITE if index < state.network_strength else rl.Color(84, 84, 84, 255)
      rl.draw_circle(71 + index * 37, 209, 13, color)
    network_labels = {"none": "--", "wifi": "Wi-Fi", "ethernet": "ETH", "cell2G": "2G", "cell3G": "3G", "cell4G": "LTE", "cell5G": "5G"}
    self._text(network_labels.get(state.network, "Unknown"), FontRole.NORMAL, 35, 58, 247)
    temperature = "--"
    if state.temperature_c is not None:
      temperature = f"{state.temperature_c:.0f}°C" if state.metric else f"{state.temperature_c * 1.8 + 32:.0f}°F"
    metrics = [("TEMP", temperature, rl.Color(201, 34, 49, 255) if state.temperature_high else rl.WHITE, 338),
               ("VEHICLE" if state.vehicle_online else "NO", "ONLINE" if state.vehicle_online else "PANDA",
                rl.WHITE if state.vehicle_online else rl.Color(201, 34, 49, 255), 496),
               ("CONNECT", state.connection, rl.WHITE if state.connection == "ONLINE" else rl.Color(218, 202, 37, 255), 654)]
    for label, value, color, y in metrics:
      rect = rl.Rectangle(30, y, 240, 126)
      clip.begin_scissor_mode(34, y, 18, 126)
      rl.draw_rectangle_rounded(rl.Rectangle(34, y + 4, 100, 118), 0.3, 10, color)
      clip.end_scissor_mode()
      outside_rounded_border(rect, 0.3, 10, 2, rl.Color(255, 255, 255, 85))
      text_y = y + (63 - 2 * 35 * self.fonts.profile.font_scale)
      for text in (label, value):
        measured = self._measure(text, FontRole.SEMI_BOLD, 35)
        text_y += measured.height
        self._text(text, FontRole.SEMI_BOLD, 35, 52 + (218 - measured.width) / 2, text_y)
    if state.recording_audio:
      rl.draw_rectangle_rounded(rl.Rectangle(170, 245, 75, 40), 1, 10, rl.Color(201, 34, 49, 255))
      self._icon("icons/microphone.png", 30, 30, 192.5, 250)

  def _mode_banner(self, state: HomeState, rect: rl.Rectangle):
    clip.begin_scissor_mode(int(rect.x), int(rect.y), int(rect.width), int(rect.height))
    draw_mode_banner_gradient(rect, state.mode)
    outside_rounded_border(rect, 0.19, 10, 5, rl.BLACK)
    clip.end_scissor_mode()
    line_x = rect.x + rect.width - 130
    rl.draw_line_ex(rl.Vector2(line_x, rect.y), rl.Vector2(line_x, rect.y + rect.height), 3, rl.Color(0, 0, 0, 77))
    text = {HomeMode.CONDITIONAL_EXPERIMENTAL: "CONDITIONAL EXPERIMENTAL", HomeMode.CONDITIONAL_CHILL: "CONDITIONAL CHILL",
            HomeMode.EXPERIMENTAL: "EXPERIMENTAL MODE ON", HomeMode.CHILL: "CHILL MODE ON"}[state.mode]
    size = 45
    width = self._measure(text, FontRole.NORMAL, size).width
    if width > line_x - rect.x - 50:
      size = max(32, int(size * (line_x - rect.x - 50) / width))
    self._text(text, FontRole.NORMAL, size, int(rect.x + 25), int(rect.y + rect.height / 2 - size * self.fonts.profile.font_scale // 2), rl.BLACK)
    if state.experimental_enabled:
      self._icon("icons/experimental_grey.png", 80, 80, rect.x + rect.width - 105, rect.y + (rect.height - 80) / 2)
    else:
      draw_moon(rl.Rectangle(rect.x + rect.width - 97, rect.y + (rect.height - 64) / 2, 64, 64))

  def _mode_atom(self, mode: HomeMode, x: float, y: float):
    if mode not in self._mode_textures:
      path = validate_home_asset(self.assets, "icons_mici/experimental_mode.png")
      image = rl.load_image(str(path))
      try:
        if image.data is None or not 0 < image.width <= 4096 or not 0 < image.height <= 4096:
          raise ValueError("Invalid mode atom image")
        rl.image_format(image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
        pixels = bytearray(image.data[:image.width * image.height * 4])
        for row in range(image.height):
          for column in range(image.width):
            offset = (row * image.width + column) * 4
            if pixels[offset + 3] == 0:
              continue
            shade = 0.65 + max(pixels[offset:offset + 3]) / 255.0 * 0.35
            color = mode_atom_color(mode, column / max(image.width - 1, 1))
            pixels[offset:offset + 3] = bytes(round(value * shade) for value in (color.r, color.g, color.b))
        image.data[:len(pixels)][:] = bytes(pixels)
        texture = rl.load_texture_from_image(image)
        if texture.id == 0:
          raise RuntimeError("Unable to load mode atom texture")
        rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
        rl.set_texture_wrap(texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)
        self._mode_textures[mode] = texture
      finally:
        if image.data is not None:
          rl.unload_image(image)
    texture = self._mode_textures[mode]
    rl.draw_texture_pro(texture, rl.Rectangle(0, 0, texture.width, texture.height), rl.Rectangle(x, y, 48, 48), rl.Vector2(0, 0), 0, rl.WHITE)

  def _compact(self, state: HomeState):
    white = rl.Color(255, 255, 255, 229)
    self._text("StarPilot", FontRole.BRAND, 64, 6, 8, white)
    version_branch = f"{state.version}  {state.branch}".strip()
    date_width = self._measure(state.commit_date, FontRole.ROMAN, 30).width if state.commit_date else 0
    version_width = 520 - date_width - 16 if state.commit_date else 520
    for text, y, color, width in ((version_branch, 98, rl.GRAY, version_width), (state.model_label, 140, white, 520)):
      size = 30
      while size > 22 and self._measure(text, FontRole.ROMAN, size).width > width:
        size -= 1
      if y == 140 and " / " in text:
        parts = text.split(" / ", 1)
        budget = (width - self._measure(" / ", FontRole.ROMAN, size).width) / 2
        for i, part in enumerate(parts):
          while part and self._measure(part, FontRole.ROMAN, size).width > budget:
            part = part[:-2].rstrip('…') + '…'
          parts[i] = part
        text = " / ".join(parts)
      while text and self._measure(text, FontRole.ROMAN, size).width > width:
        text = text[:-2].rstrip('…') + '…'
      self._text(text, FontRole.ROMAN, size, 6, y, color)
      if y == 98 and state.commit_date:
        date_x = 6 + self._measure(text, FontRole.ROMAN, size).width + 16
        self._text(state.commit_date, FontRole.ROMAN, 30, date_x, y, rl.GRAY)
    self._icon("icons_mici/settings.png", 48, 48, 8, 192, 0.9)
    if state.network == "wifi":
      suffix = "none" if state.network_strength == 0 else "medium" if state.network_strength == 3 else "full" if state.network_strength >= 4 else "low"
      self._icon(f"icons_mici/settings/network/wifi_strength_{suffix}.png", 50, 37, 76, 197.5, 0.9)
    elif state.network.startswith("cell"):
      suffix = {0: "none", 2: "low", 3: "medium", 4: "high", 5: "full"}.get(state.network_strength, "none")
      self._icon(f"icons_mici/settings/network/cell_strength_{suffix}.png", 54, 36, 74, 198, 0.9)
    else:
      self._icon("icons_mici/settings/network/wifi_strength_slash.png", 50, 44, 76, 190.5, 0.9)
    x = 146
    if state.bluetooth:
      self._bluetooth(state.bluetooth, x, 197)
      x += 56
    if state.mode == HomeMode.CHILL:
      draw_moon(rl.Rectangle(x + 5, 197, 38, 38))
    else:
      self._mode_atom(state.mode, x, 192)
    x += 66
    if state.gpu_present or state.gpu_state in ("loading", "active", "failed", "uncompiled"):
      failed = state.gpu_state in ("failed", "uncompiled")
      loading = state.gpu_state == "loading"
      icon = "chestnut_orange.png" if failed else "chestnut.png" if loading else "chestnut_green.png"
      opacity = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(rl.get_time() * 6)) if loading else 1.0
      self._icon("icons_mici/" + icon, 68 if failed else 54, 40, x, 197, opacity)
      x += 76
    if state.jetlink in JETLINK_COLORS:
      draw_jetlink(rl.Rectangle(x, 195, 40, 42), state.jetlink)
      x += 58
    if state.recording_audio:
      self._icon("icons_mici/microphone.png", 32, 46, x, 193)

  def close(self):
    if rl.is_window_ready():
      for texture in (*self._textures.values(), *self._mode_textures.values()):
        rl.unload_texture(texture)
    self._textures.clear()
    self._mode_textures.clear()
