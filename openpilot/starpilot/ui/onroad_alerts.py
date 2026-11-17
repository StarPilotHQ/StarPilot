"""Source-supplied alerts with native compact lane-guidance rendering."""

from dataclasses import replace
import re

from openpilot.starpilot.ui.raylib import rl

from openpilot.system.ui.lib.multilang import tr_display as tr
from openpilot.system.ui.lib.display_translation import DisplayText

from openpilot.common.filter_simple import BounceFilter, FirstOrderFilter
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert
from openpilot.selfdrive.ui.mici.onroad.alert_renderer import Alert as NativeAlert, AlertRenderer as NativeAlertRenderer
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile


def _wrap_display(text, measure, width):
  lines, current = [], ""
  for word in re.findall(r"\S+\s*", text):
    chunks = (word,) if measure(DisplayText(word.rstrip())) <= width else tuple(word)
    for chunk in chunks:
      candidate = current + chunk
      if current and measure(DisplayText(candidate.rstrip())) > width:
        lines.append(DisplayText(current.rstrip()))
        current = chunk.lstrip()
      else:
        current = candidate
  if current.strip():
    lines.append(DisplayText(current.rstrip()))
  return lines


class AlertRenderer:
  def __init__(self, fonts: BitmapFonts):
    self.fonts = fonts
    self._position = BounceFilter(0, 0.1, 1 / 60, initialized=fonts.profile == Profile.COMPACT)
    self._alpha = FirstOrderFilter(0, 0.05, 1 / 60)
    self._lane_renderer: NativeAlertRenderer | None = None
    self._lane_visible = False
    self._previous: OnroadAlert | None = None
    self.text_only_alert_names: frozenset[str] = frozenset()

  def _fit_size(self, text, role, size, width, letter_spacing=0):
    while size > 26 and self.fonts.measure(text, role, size, spacing=size * letter_spacing).width > width:
      size -= 2
    return size

  def _center(self, text: str, role: FontRole, size: int, rect: rl.Rectangle, y: float,
              color: rl.Color = rl.WHITE, *, letter_spacing: float = 0) -> None:
    text = DisplayText(tr(text))
    size = self._fit_size(text, role, size, rect.width, letter_spacing)
    spacing = size * letter_spacing
    lines = text.split('\n')
    height = self.fonts.measure(text, role, size, spacing=spacing).height
    # Preserve Raylib's measured newline spacing when drawing lines separately.
    line_step = (height - self.fonts.measure(lines[0], role, size, spacing=spacing).height) / max(1, len(lines) - 1)
    for line in lines:
      measured = self.fonts.measure(line, role, size, spacing=spacing)
      self.fonts.draw(line, role, size, rect.x + (rect.width - measured.width) / 2, y, color, spacing=spacing)
      y += line_step

  def render(self, rect: rl.Rectangle, alert: OnroadAlert, *, signal_direction: int = 0) -> None:
    event_name = alert.alert_type.split('/', 1)[0]
    text1, text2 = alert.text1, alert.text2
    if event_name == 'personalityChanged' and text1.lower().startswith('driving personality: '):
      text1, text2 = text1[len('driving personality: '):], 'Driving Personality'
    alert = replace(alert, text1=DisplayText(tr(text1)), text2=DisplayText(tr(text2)))
    if self.fonts.profile == Profile.COMPACT:
      lane_event = event_name in ('preLaneChangeLeft', 'preLaneChangeRight', 'laneChangeBlocked', 'laneChange',
                                                                    'steerTempUnavailable', 'steerTempUnavailableSilent')
      if lane_event or (self._lane_visible and alert.size == AlertSize.NONE):
        if self._lane_renderer is None:
          self._lane_renderer = NativeAlertRenderer()
        native = NativeAlert(alert.text1, alert.text2, size=list(AlertSize).index(alert.size),
                             status=2 if alert.critical else 1 if alert.user_prompt else 0,
                             visual_alert=alert.visual_alert, alert_type=alert.alert_type) if lane_event else None
        self._lane_visible = self._lane_renderer.render_alert(rect, native, signal_direction=signal_direction)
        self._previous = None
        self._alpha.x = 0
        return
      if self._lane_visible and self._lane_renderer is not None:
        self._lane_renderer._prev_alert = None
        self._lane_renderer._alpha_filter.x = 0
        self._lane_visible = False
    compact_y = self._position.update(rect.y - 50 if alert.size == AlertSize.NONE else rect.y) \
      if self.fonts.profile == Profile.COMPACT else rect.y
    if alert.size == AlertSize.NONE:
      self._alpha.update(0)
      if self._alpha.x <= .01 or self._previous is None:
        self._previous = None
        return
      alert = self._previous
    else:
      self._previous = alert
      self._alpha.update(1)
    if self.fonts.profile == Profile.LARGE:
      height = rect.height if alert.size == AlertSize.FULL else 271 if alert.size == AlertSize.SMALL else 420
      self._large(rect, alert, self._position.update(rect.y + rect.height - height),
                  text_only=alert.alert_type.split('/', 1)[0] in self.text_only_alert_names)
    else:
      self._compact(rect, alert, compact_y)

  def _large(self, rect: rl.Rectangle, alert: OnroadAlert, animated_y: float, *, text_only: bool = False) -> None:
    background_alpha = int(255 * 0.9 * self._alpha.x)
    title_color = rl.Color(255, 255, 255, background_alpha)
    subtitle_color = rl.Color(255, 255, 255, int(255 * 0.65 * self._alpha.x))
    if alert.size == AlertSize.FULL:
      color = (rl.Color(255, 0, 21, background_alpha) if alert.critical else
               rl.Color(255, 115, 0, background_alpha) if alert.user_prompt else rl.Color(0, 0, 0, background_alpha))
      solid = round(rect.height * 0.2)
      rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), solid, color)
      rl.draw_rectangle_gradient_v(int(rect.x), int(rect.y + solid), int(rect.width), int(rect.height - solid),
                                   color, rl.Color(color.r, color.g, color.b, 0))
      size = self._fit_size(alert.text1, FontRole.BOLD, 100 if len(alert.text1) > 16 else 110, rect.width - 120, -0.02)
      title_h = self.fonts.measure(alert.text1, FontRole.BOLD, size, spacing=size * -0.02).height
      self._center(alert.text1, FontRole.BOLD, size, rl.Rectangle(rect.x + 60, rect.y, rect.width - 120, rect.height),
                   animated_y + (rect.height - title_h) / 2, title_color,
                   letter_spacing=-0.02)
      self._center(alert.text2, FontRole.NORMAL, 56, rl.Rectangle(rect.x + 60, rect.y, rect.width - 120, rect.height),
                   animated_y + (rect.height - title_h) / 2 + title_h,
                   subtitle_color, letter_spacing=0.025)
      return
    alert_height = 271 if alert.size == AlertSize.SMALL else 420
    y = rect.y + rect.height - alert_height
    color = rl.Color(218, 111, 37, background_alpha) if alert.user_prompt else rl.Color(0, 0, 0, background_alpha)
    title_size = self._fit_size(alert.text1, FontRole.BOLD, 60 if alert.size == AlertSize.SMALL else 88, rect.width - 120, -0.02)
    title_h = self.fonts.measure(alert.text1, FontRole.BOLD, title_size, spacing=title_size * -0.02).height
    plateau_y = round(animated_y + (alert_height - title_h) / 2) - 2
    plateau_h = round(title_h) + 4
    if not text_only:
      rl.draw_rectangle_gradient_v(int(rect.x), int(y), int(rect.width), int(plateau_y - y), rl.BLANK, color)
      rl.draw_rectangle(int(rect.x), int(plateau_y), int(rect.width), plateau_h, color)
      rl.draw_rectangle_gradient_v(int(rect.x), int(plateau_y + plateau_h), int(rect.width), int(y + alert_height - plateau_y - plateau_h),
                                   color, rl.BLANK)
    text_rect = rl.Rectangle(rect.x + 60, y, rect.width - 120, alert_height)
    title_y = animated_y + (alert_height - title_h) / 2
    if text_only:
      shadow = rl.Color(0, 0, 0, int(220 * self._alpha.x))
      self._center(alert.text1, FontRole.BOLD, title_size,
                   rl.Rectangle(text_rect.x + 3, text_rect.y, text_rect.width, text_rect.height),
                   title_y + 3, shadow, letter_spacing=-0.02)
    self._center(alert.text1, FontRole.BOLD, title_size, text_rect, title_y, title_color, letter_spacing=-0.02)
    if alert.size == AlertSize.MID:
      if text_only:
        self._center(alert.text2, FontRole.NORMAL, 56,
                     rl.Rectangle(text_rect.x + 2, text_rect.y, text_rect.width, text_rect.height),
                     title_y + title_h + 2, rl.Color(0, 0, 0, int(200 * self._alpha.x)), letter_spacing=0.025)
      self._center(alert.text2, FontRole.NORMAL, 56, text_rect, title_y + title_h,
                   subtitle_color, letter_spacing=0.025)

  def _compact(self, rect: rl.Rectangle, alert: OnroadAlert, animated_y: float) -> None:
    background_alpha = int(255 * 0.9 * self._alpha.x)
    title_color = rl.Color(255, 255, 255, background_alpha)
    subtitle_color = rl.Color(255, 255, 255, int(255 * 0.65 * self._alpha.x))
    color = (rl.Color(255, 0, 21, background_alpha) if alert.critical else
               rl.Color(255, 115, 0, background_alpha) if alert.user_prompt else rl.Color(0, 0, 0, background_alpha))
    event_name = alert.alert_type.split('/', 1)[0]
    height = rect.height
    solid_height = round(height * 0.2)
    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), solid_height, color)
    rl.draw_rectangle_gradient_v(int(rect.x), int(rect.y + solid_height), int(rect.width), int(height - solid_height),
                                 color, rl.Color(color.r, color.g, color.b, 0))
    text = tr(alert.text1).lower()
    subtitle = tr(alert.text2).lower()
    if event_name == 'personalityChanged' and text.startswith('driving personality: '):
      text = text.removeprefix('driving personality: ')
      subtitle = 'driving personality'
    font_size = 82 if len(text) <= 12 else 70 if len(text) <= 16 else 54
    text_x = rect.x + 18
    available_width = rect.width - 36
    while alert.size != AlertSize.FULL and font_size > 30 and self.fonts.measure(
        text, FontRole.DISPLAY, font_size, spacing=-0.02 * font_size).width > available_width:
      font_size -= 2
    spacing = -0.02 * font_size
    if alert.size == AlertSize.FULL:
      # Match word wrapping and line spacing to measured glyph height, not nominal font size.
      available_width = rect.width - 18
      def wrapped(size):
        return _wrap_display(text, lambda value: self.fonts.measure(value, FontRole.DISPLAY, size, spacing=-0.02 * size).width,
                             available_width)
      lines = wrapped(font_size)
      while font_size > 26 and len(lines) * self.fonts.measure(text, FontRole.DISPLAY, font_size).height * .86 * .9 > rect.height - 12:
        font_size -= 2
        lines = wrapped(font_size)
      spacing = -0.02 * font_size
      y = animated_y - 4
      for line in lines:
        self.fonts.draw(line, FontRole.DISPLAY, font_size, text_x, y, title_color, spacing=spacing)
        y += self.fonts.measure(line, FontRole.DISPLAY, font_size, spacing=spacing).height * 0.86 * 0.9
      return
    title_y = animated_y + 12
    lines = [text]
    bottom = title_y
    for line in lines:
      measured = self.fonts.measure(line, FontRole.DISPLAY, font_size, spacing=spacing)
      x = text_x
      self.fonts.draw(line, FontRole.DISPLAY, font_size, x, title_y, title_color, spacing=spacing)
      bottom = title_y + measured.height
      title_y += measured.height * .86 * .9
    if subtitle:
      subtitle_y = bottom - 4
      self.fonts.draw(subtitle, FontRole.ROMAN, 36, text_x, subtitle_y,
                      subtitle_color, spacing=36 * 0.025)
