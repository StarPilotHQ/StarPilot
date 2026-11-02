"""Adapted large StarPilot onroad widgets with supplied observations.

The speed group uses the reviewed device geometry; the current speed and wheel
retain their native anchors. See the adjacent UI LICENSE for retained source.
"""

from pathlib import Path
import hashlib
import json

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.onroad_customization import offset, rgba, widget_size
from openpilot.starpilot.ui.onroad_state import OnroadState, manual_slc_confirmation, speed_limit_bounds
from openpilot.starpilot.ui.onroad_widget_style import draw_control_card
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile
from openpilot.starpilot.ui.wheel_feedback import WheelFeedbackFade
from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed
from openpilot.starpilot.ui.speed_limit_widget import MUTED, SpeedLimitWidget, draw_caption, draw_ink, draw_line, draw_text


ENGAGED = rl.Color(128, 216, 166, 255)
DISENGAGED = rl.Color(145, 155, 149, 255)


def _center(fonts: BitmapFonts, text: str, role: FontRole, size: int, rect: rl.Rectangle, y: float,
            color: rl.Color = rl.WHITE) -> None:
  measured = fonts.measure(text, role, size)
  fonts.draw(text, role, size, rect.x + (rect.width - measured.width) / 2, y, color)


class UnifiedSpeedWidget:
  """The set speed with the latest limit beside it."""

  def __init__(self, fonts: BitmapFonts):
    self.fonts = fonts
    self.sign = SpeedLimitWidget(fonts, Profile.LARGE)

  def close(self):
    self.sign.close()

  def _draw_sources(self, rect, state):
    """Each enabled source as a small label over its reading; the selected source is highlighted."""
    from openpilot.starpilot.ui.large_speed_geometry import PADDING
    rows = [row for row in state.speed_limit.source_readings if row.enabled]
    if not rows:
      text = 'No sources enabled' if state.speed_limit.source_readings else 'No speed limit'
      width = self.fonts.measure(text, FontRole.NORMAL, 26).width
      draw_text(self.fonts, text, FontRole.NORMAL, 26, rect.x + (rect.width - width) / 2, rect.y + 22, rl.WHITE)
      return
    active = state.speed_limit.source if str(state.speed_limit.kind) == 'valid' else state.speed_limit.accepted_source
    labels = {'dashboard': 'Dash', 'map': 'Map', 'vision': 'Vision', 'online': 'Online'}
    cell = (rect.width - 2 * PADDING) / len(rows)
    for index, row in enumerate(rows):
      color = rl.WHITE if row.source == active else rl.Color(170, 179, 174, 255)
      value = str(round(row.speed_mps * (3.6 if state.metric else 2.2369362921))) if row.kind == 'valid' and row.speed_mps is not None else '–'
      box = rl.Rectangle(rect.x + PADDING + index * cell, rect.y, cell, rect.height)
      size = 22
      while size > 12 and self.fonts.measure(labels[row.source], FontRole.NORMAL, size).width > cell - 4:
        size -= 1
      for text, role, font_size, y in ((labels[row.source], FontRole.NORMAL, size, rect.y + 10),
                                     (value, FontRole.MEDIUM if row.source == active else FontRole.NORMAL, 30, rect.y + 36)):
        width = self.fonts.measure(text, role, font_size).width
        draw_text(self.fonts, text, role, font_size, box.x + (box.width - width) / 2, y, color)

  def render(self, content: rl.Rectangle, state: OnroadState) -> None:
    from openpilot.starpilot.ui.large_speed_geometry import GAP, WIDTH, PADDING, SOURCE_ROW_HEIGHT, SOURCE_TOP, card_height
    from openpilot.starpilot.ui.onroad_customization import placement
    shown = resolve_unified_speed(state)
    if shown.mode == 'hidden':
      return
    saved = placement(state.customization, 'large', 'cruise_limits')
    height = card_height(shown.mode)
    rect = rl.Rectangle(saved['x'], saved['y'], WIDTH, height)
    sources = state.customization.get('speedSources') is True
    separate = state.customization.get('speedSourcesCard') is True
    fill = rl.Color(*rgba(state.customization, 'cardFill', 'large', 'cruise_limits'))
    border = rl.Color(*rgba(state.customization, 'cardBorder', 'large', 'cruise_limits'))
    text = rl.Color(*rgba(state.customization, 'text', 'large', 'cruise_limits'))
    if shown.mode != 'limit_only':
      if shown.max_text == '–':
        center = rect.x + 120
        draw_caption(self.fonts, 'SET', center, rect.y + 15, unit=shown.unit, centered=True)
        draw_line(Profile.LARGE, rl.Vector2(center - 28, rect.y + 101), rl.Vector2(center + 28, rect.y + 101), 8, MUTED)
      elif manual_slc_confirmation(Profile.LARGE, state):
        x = rect.x + PADDING
        draw_caption(self.fonts, 'SET', x, rect.y + 19, unit=shown.unit, size=32)
        size = 120
        while size > 72 and self.fonts.measure(shown.max_text, FontRole.SET_SPEED, size).width > 148:
          size -= 1
        draw_ink(self.fonts, shown.max_text, FontRole.SET_SPEED, size, x, rect.y + 51, text)
      else:
        center = rect.x + 120
        draw_caption(self.fonts, 'SET', center, rect.y + 15, unit=shown.unit, centered=True)
        size = 114.28
        size *= min(1, 200 / max(1, self.fonts.measure(shown.max_text, FontRole.SET_SPEED, size).width))
        left, right = self.fonts.horizontal_ink(shown.max_text, FontRole.SET_SPEED, size)
        top, bottom = self.fonts.vertical_ink(shown.max_text, FontRole.SET_SPEED, size)
        draw_ink(self.fonts, shown.max_text, FontRole.SET_SPEED, size, center - (left + right) / 2,
                 rect.y + 101 - (bottom - top) / 2, text)
    self.sign.render(state, defer_confirmation=True)
    if shown.mode == 'limit_only' and (bounds := speed_limit_bounds(Profile.LARGE, state)) is not None:
      draw_ink(self.fonts, shown.unit, FontRole.NORMAL, 24, bounds[2], bounds[3] - 32)
    if sources:
      row = rl.Rectangle(rect.x, rect.y + max(height, SOURCE_TOP) + (GAP if separate else 0), WIDTH, SOURCE_ROW_HEIGHT)
      if separate:
        draw_control_card(row, fill=fill, border=border, border_width=1, roundness=.2)
      else:
        rl.draw_line_ex(rl.Vector2(rect.x + 1, row.y), rl.Vector2(rect.x + WIDTH - 1, row.y), 1, border)
      self._draw_sources(row, state)


class CurrentSpeedHud:
  def __init__(self, fonts: BitmapFonts):
    self.fonts = fonts

  def render(self, content: rl.Rectangle, state: OnroadState) -> None:
    if state.speed_mps is None:
      return
    speed = str(round(state.speed_mps * (3.6 if state.metric else 2.2369362921)))
    dx, dy = offset(state.customization, "large", "current_speed")
    rect = rl.Rectangle(content.x + 610 + dx, content.y + dy, 580, 300)
    number_top, _ = self.fonts.vertical_ink(speed, FontRole.SPEED, 176)
    old_top, old_bottom = self.fonts.vertical_ink(speed, FontRole.BOLD, 176)
    _center(self.fonts, speed, FontRole.SPEED, 176, rect, content.y + 42 + dy + old_top - number_top,
            rl.Color(*rgba(state.customization, 'text', 'large', 'current_speed')))
    unit = 'km/h' if state.metric else 'mph'
    unit_top, _ = self.fonts.vertical_ink(unit, FontRole.NORMAL, 28)
    red, green, blue, alpha = rgba(state.customization, 'text', 'large', 'current_speed')
    _center(self.fonts, unit, FontRole.NORMAL, 28, rect, content.y + 42 + dy + old_bottom + 20 - unit_top,
            rl.Color(red, green, blue, int(alpha * 200 / 255)))



class SteeringWheelWidget:
  """Frozen ExpButton shape; clicks are handled by the separate request input."""

  def __init__(self, asset_directory: Path):
    self.asset_directory = asset_directory
    self._texture: rl.Texture | None = None
    self._feedback = WheelFeedbackFade()

  def prepare(self) -> None:
    if self._texture is not None:
      return
    path = self.asset_directory / "icons/chffr_wheel.png"
    entry = json.loads(Path(__file__).with_name("onroad-assets.json").read_text())["files"][0]
    data = path.read_bytes()
    if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
      raise ValueError("Unreviewed steering wheel art")
    image = rl.load_image(str(path))
    try:
      if image.data is None:
        raise RuntimeError("Unable to load steering wheel art")
      rl.image_resize(image, 144, 144)
      texture = rl.load_texture_from_image(image)
      if not texture.id:
        raise RuntimeError("Unable to create steering wheel texture")
      self._texture = texture
    finally:
      if image.data is not None:
        rl.unload_image(image)

  def render(self, content: rl.Rectangle, state: OnroadState) -> None:
    if self._texture is None:
      self.prepare()
    dx, dy = offset(state.customization, "large", "steering_wheel")
    x = content.x + content.width - 146 - 96 + dx
    y = content.y + 45 + dy
    size, _ = widget_size(state.customization, "large", "steering_wheel")
    radius = size / 2
    rl.draw_circle(int(x + radius), int(y + radius), radius, rl.Color(*rgba(state.customization, "cardFill", "large", "steering_wheel")))
    border = rl.Color(*rgba(state.customization, "cardBorder", "large", "steering_wheel"))
    if border.a:
      rl.draw_ring(rl.Vector2(x + radius, y + radius), radius - 3, radius, 0, 360, 64, border)
    feedback = self._feedback.update(state.wheel_acceleration, state.appearance.wheel_pedal_feedback, state.observed_ns, state.drive_frame)
    color = rl.Color(*feedback, 255)
    rl.draw_texture_pro(self._texture, rl.Rectangle(0, 0, 144, 144),
                        rl.Rectangle(x + size / 8, y + size / 8, size * .75, size * .75),
                        rl.Vector2(0, 0), 0, color)

  def close(self) -> None:
    if self._texture is not None and rl.is_window_ready():
      rl.unload_texture(self._texture)
    self._texture = None
