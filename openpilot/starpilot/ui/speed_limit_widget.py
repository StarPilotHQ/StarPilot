"""The latest limit, its attention animation and override mark on both screens."""

import time
from typing import cast

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.large_speed_geometry import LARGE_SIGN_SCALE, CONFIRMATION_SCALE, CONFIRMATION_PADDING
from openpilot.starpilot.ui.onroad_state import OnroadState, manual_slc_confirmation, speed_limit_bounds, slc_controls, slc_accept_available
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile
from openpilot.starpilot.ui.speed_limit_pulse import SpeedLimitPulse
from openpilot.starpilot.ui.speed_limit_shapes import SpeedLimitShapes
from openpilot.starpilot.ui.stop_sign_widget import render_stop_sign
from openpilot.starpilot.ui.unified_speed_presentation import displayed_adjusted_limit_mps, positive, resolve_unified_speed


INK = rl.Color(241, 246, 243, 255)
MUTED = rl.Color(190, 208, 202, 255)
SOURCE_LABELS = {'dashboard': 'DASH', 'map': 'OSM', 'vision': 'VISION', 'mapbox': 'NAV'}


def draw_text(fonts, text, role, size, x, y, color=INK):
  if isinstance(color, (tuple, list)):
    color = rl.Color(*color)
  radius = 1.15 if fonts.profile == Profile.LARGE else 1.
  contour = rl.Color(6, 14, 10, round(color.a * .90))
  for dx, dy in ((-radius, 0), (radius, 0), (0, -radius), (0, radius)):
    fonts.draw(text, role, size, x + dx, y + dy, contour)
  fonts.draw(text, role, size, x, y + (1.5 if fonts.profile == Profile.LARGE else 1.2),
             rl.Color(7, 16, 12, round(color.a * .32)))
  fonts.draw(text, role, size, x, y, color)


def draw_line(profile, start, end, stroke, color):
  radius = 1.4 if profile == Profile.LARGE else 1.
  rl.draw_line_ex(start, end, stroke + 2 * radius, rl.Color(6, 14, 10, round(color.a * .87)))
  rl.draw_line_ex(start, end, stroke, color)


def draw_ink(fonts, text, role, size, x, top, color=INK):
  ink_top, ink_bottom = fonts.vertical_ink(text, role, size)
  draw_text(fonts, text, role, size, x, top - ink_top, color)
  return fonts.measure(text, role, size).width, ink_bottom - ink_top


def draw_caption(fonts, label, x, top, color=MUTED, *, unit=None, size=None, centered=False):
  large = fonts.profile == Profile.LARGE
  label_size = size or (28 if large else 24)
  unit_size, gap = (28, 12) if large else (20, 8)
  if centered:
    width = fonts.measure(label, FontRole.MEDIUM, label_size).width
    if unit:
      width += gap + fonts.measure(unit, FontRole.NORMAL, unit_size).width
    x -= width / 2
  width, height = draw_ink(fonts, label, FontRole.MEDIUM, label_size, x, top, color)
  if unit:
    unit_top, _ = fonts.vertical_ink(unit, FontRole.NORMAL, unit_size)
    # Exclude the p descender when aligning mph to the caption baseline.
    _, baseline = fonts.vertical_ink('mh' if unit == 'mph' else unit, FontRole.NORMAL, unit_size)
    unit_width, _ = draw_ink(fonts, unit, FontRole.NORMAL, unit_size, x + width + gap,
                             top + height - (baseline - unit_top), color)
    width += gap + unit_width
  return width, height


class SpeedLimitWidget:
  def __init__(self, fonts: BitmapFonts, profile: Profile):
    self.fonts = fonts
    self.profile = profile
    self.scale = LARGE_SIGN_SCALE if profile == Profile.LARGE else 1
    self.text_scale = self.scale * Profile.COMPACT.font_scale / profile.font_scale
    self.pulse = SpeedLimitPulse()
    self.shapes = SpeedLimitShapes(self.scale, headless_context=getattr(fonts, '_headless_context', False))

  def close(self):
    self.shapes.close()

  def render(self, state: OnroadState, *, defer_confirmation=False) -> None:
    if state.appearance.show_speed_limit_sign or state.speed_limit.action_enabled:
      render_stop_sign(self.fonts, state)
    bounds = speed_limit_bounds(self.profile, state)
    now = state.observed_ns / 1e9 if state.observed_ns else time.monotonic()
    self.pulse.update(state, now, visible=bounds is not None)
    if bounds is None:
      return
    if manual_slc_confirmation(self.profile, state):
      if not defer_confirmation:
        self.render_confirmation(state)
      return
    self._sign(state, bounds, now)

  def render_confirmation(self, state: OnroadState, pressed=None):
    bounds = speed_limit_bounds(self.profile, state)
    if bounds is None or not manual_slc_confirmation(self.profile, state):
      return
    now = state.observed_ns / 1e9 if state.observed_ns else time.monotonic()
    self.pulse.update(state, now)
    scale = CONFIRMATION_SCALE[self.profile]
    x, y, right, _ = bounds
    width = (right - x) / scale
    shown = resolve_unified_speed(state, confirmation=True)
    source = SOURCE_LABELS.get(shown.source, 'SPEED')
    white = rl.Color(242, 246, 244, 255)
    secondary = rl.Color(209, 222, 218, 255)

    def text_size(size):
      return size * scale * Profile.COMPACT.font_scale / self.profile.font_scale

    def ink_width(text, size, role=FontRole.MEDIUM):
      left, right = self.fonts.horizontal_ink(text, role, text_size(size))
      return (right - left) / scale

    def text(label, size, cx, cy, color=white, role=FontRole.MEDIUM):
      size = text_size(size)
      left, right = self.fonts.horizontal_ink(label, role, size)
      top, bottom = self.fonts.vertical_ink(label, role, size)
      self.fonts.draw(label, role, size, x + cx * scale - (left + right) / 2,
                      y + cy * scale - (top + bottom) / 2, color)

    def rounded(left, top, w, h, color, radius=8):
      rect = rl.Rectangle(x + left * scale, y + top * scale, w * scale, h * scale)
      rl.draw_rectangle_rounded(rect, 2 * radius / min(w, h), 12, color)

    if state.slc_prompt_open:
      self.shapes.confirmation(bounds, opened=True)
      target_size = 40
      nw, uw = ink_width(shown.proposal_text, target_size, FontRole.BOLD), ink_width(shown.unit, 20)
      context = source + (f' {shown.posted_text} {shown.offset_text}' if shown.offset_text else '')
      context_size = 20 * min(1, (width - 24 - nw - 6 - uw - 16) / max(1, ink_width(context, 20)))
      context_width = ink_width(context, context_size)
      start = (width - nw - 6 - uw - 16 - context_width) / 2
      text(shown.proposal_text, target_size, start + nw / 2, 24, white, FontRole.BOLD)
      nt, nb = self.fonts.vertical_ink(shown.proposal_text, FontRole.BOLD, text_size(target_size))
      ut, ub = self.fonts.vertical_ink(shown.unit, FontRole.MEDIUM, text_size(20))
      text(shown.unit, 20, start + nw + 6 + uw / 2, 24 + (nb - nt - ub + ut) / scale / 2, secondary)
      text(context, context_size, start + nw + 6 + uw + 16 + context_width / 2, 24, rl.Color(203, 197, 215, 255))
      for control, (fill, border, ink) in zip(slc_controls(self.profile, state), (
          ((35, 74, 57), (106, 153, 127), (224, 243, 232)),
          ((77, 46, 57), (173, 122, 134), (248, 229, 234))), strict=True):
        if not control.enabled:
          fill, border, ink = (43, 49, 48), (94, 105, 101), (161, 174, 168)
        left = (control.bounds[0] - x) / scale
        top = (control.bounds[1] - y) / scale
        w, h = (control.bounds[2] - control.bounds[0]) / scale, (control.bounds[3] - control.bounds[1]) / scale
        # Keep the full touch area around the shorter, visually balanced face.
        face_top, face_height = top + CONFIRMATION_PADDING, h - 2 * CONFIRMATION_PADDING
        rounded(left, face_top, w, face_height, rl.Color(*border, 255), 16)
        color = tuple(min(255, channel + 14) for channel in fill) if pressed == control.request else fill
        rounded(left + 1.5, face_top + 1.5, w - 3, face_height - 3, rl.Color(*color, 255), 14.5)
        text(control.label, 37, left + w / 2, top + h / 2, rl.Color(*ink, 255))
      return

    self.shapes.confirmation(bounds, strength=self.pulse.confirmation_strength(now))
    source_color = rl.Color(197, 212, 208, 255)
    label_width = ink_width(source, 22, FontRole.SEMI_BOLD)
    target = state.speed_limit.pending_adjusted_limit_mps
    if not positive(target):
      target = displayed_adjusted_limit_mps(state.speed_limit)
    direction = (-1 if target > state.speed_mps else 1) if target is not None and state.speed_mps is not None and target != state.speed_mps else 0
    start = (width - label_width - (19.2 if direction else 0)) / 2
    text(source, 22, start + label_width / 2, 22, source_color, FontRole.SEMI_BOLD)
    if direction:
      self.shapes.arrow('confirmation', x + (start + label_width + 12.6) * scale, y + 22.5 * scale,
                        direction, source_color, scale=1.1 * scale)
    nw = ink_width(shown.posted_text, 64, FontRole.BOLD)
    ow = ink_width(shown.offset_text, 32) if shown.offset_text else 0
    start = (width - nw - (10 + ow if ow else 0)) / 2
    # Align the posted value and offset at their visible lower edge.
    number_top, number_bottom = self.fonts.vertical_ink(shown.posted_text, FontRole.BOLD, text_size(64))
    text(shown.posted_text, 64, start + nw / 2, 84 - (number_bottom - number_top) / scale / 2, white, FontRole.BOLD)
    if ow:
      offset_text = cast(str, shown.offset_text)
      top, bottom = self.fonts.vertical_ink(offset_text, FontRole.MEDIUM, text_size(32))
      text(offset_text, 32, start + nw + 10 + ow / 2, 84 - (bottom - top) / scale / 2, secondary)
    label = 'New Limit' if slc_accept_available(state) else 'Unavailable'
    caption = ink_width(label, 30, FontRole.SEMI_BOLD)
    cx = (width - caption - 10) / 2
    accent = rl.Color(214, 188, 240, 255)
    text(label, 30, cx + caption / 2, 104, accent, FontRole.SEMI_BOLD)
    ax, ay = x + (cx + caption + 8) * scale, y + 104 * scale
    rl.draw_line_ex(rl.Vector2(ax - 2 * scale, ay - 4 * scale), rl.Vector2(ax + 2 * scale, ay), 1.5 * scale, accent)
    rl.draw_line_ex(rl.Vector2(ax + 2 * scale, ay), rl.Vector2(ax - 2 * scale, ay + 4 * scale), 1.5 * scale, accent)
    if state.slc_wheel_confirmation_available:
      accept, reject = ink_width('Accept', 26), ink_width('Reject', 26)
      start = (width - 18 - 4 - accept - 10 - 18 - 4 - reject) / 2
      for left, plus in ((start, True), (start + 18 + 4 + accept + 10, False)):
        self.shapes.wheel_key(plus, x + (left + 9) * scale, y + 135.5 * scale, scale=scale)
      hint_ink = rl.Color(210, 216, 216, 255)
      text('Accept', 26, start + 22 + accept / 2, 135.5, hint_ink if slc_accept_available(state) else MUTED)
      text('Reject', 26, start + 18 + 4 + accept + 10 + 22 + reject / 2, 135.5, hint_ink)

  def _sign(self, state: OnroadState, bounds, now: float) -> None:
    shown, obs = resolve_unified_speed(state), state.speed_limit
    heading = SOURCE_LABELS.get(shown.source, 'SPEED')
    x, top = bounds[0] + 12, bounds[1] + 12
    scale = self.scale
    pending = shown.pending and not obs.automatic_adoption_pending
    target = displayed_adjusted_limit_mps(obs) if pending else None
    arrow_direction = 0
    if target is not None and state.speed_mps is not None and target != state.speed_mps:
      arrow_direction = -1 if target > state.speed_mps else 1
    white = self.pulse.color(rl.Color(244, 247, 245, 255), now)
    secondary = self.pulse.color(rl.Color(223, 232, 227, 255), now)

    def ink(text, role, size):
      return tuple(value / scale for value in (*self.fonts.horizontal_ink(text, role, size * self.text_scale),
                                               *self.fonts.vertical_ink(text, role, size * self.text_scale)))

    def centered(text, role, size, ink_top, color, center_x=59):
      left, right, a, b = ink(text, role, size)
      # Snap only the final draw, after fitting in the shared 118 x 132 geometry.
      origin = round(x + (center_x - (left + right) / 2) * scale)
      y = round(top + (ink_top - a) * scale)
      self.fonts.draw(text, role, size * self.text_scale, origin, y, color)
      return origin + left * scale, y + a * scale, origin + right * scale, y + b * scale

    def circle_fits(left, right, a, b):
      return all((px - 59) ** 2 + (py - 67) ** 2 <= 46 ** 2
                 for px in (left, right) for py in (a, b))

    vienna = state.appearance.use_vienna_sign
    large_steady = self.profile == Profile.LARGE and not vienna and not shown.pending
    color = rl.Color(0, 0, 0, 255) if vienna else white
    if vienna:
      self.shapes.circle(x + 59 * scale, top + 67 * scale, self.pulse.color(rl.Color(201, 34, 49, 255), now))
    else:
      self.shapes.outline(x + 59 * scale, top + 66 * scale, white)

    heading_size = 28 / self.text_scale if large_steady else 18
    left, right, a, b = ink(heading, FontRole.SEMI_BOLD, heading_size)
    if large_steady and right - left > 96:
      heading_size *= 96 / (right - left)
      left, right, a, b = ink(heading, FontRole.SEMI_BOLD, heading_size)
    heading_width = right - left
    header_y = 47 if vienna else 21 if large_steady else 26
    # The small arrow's visible ink is 12 x 16, with a six-pixel label gap.
    header_left = 59 - (heading_width + (18 if arrow_direction else 0)) / 2
    heading_color = self.pulse.color(MUTED, now) if large_steady else color
    centered(heading, FontRole.SEMI_BOLD, heading_size, header_y - (b - a) / 2, heading_color, header_left + heading_width / 2)
    if arrow_direction:
      self.shapes.arrow('compact', round(x + (header_left + heading_width + 12) * scale),
                        round(top + header_y * scale), arrow_direction, color)

    role = FontRole.SET_SPEED if large_steady else FontRole.BOLD
    size = 77.92 / self.text_scale if large_steady else 58 if vienna else 64
    offset_size = 30 if vienna else 27
    number_center = (73 if shown.offset_text else 80) if vienna else (67 if shown.offset_text else 81)
    while True:
      left, right, a, b = ink(shown.posted_text, role, size)
      width, height = right - left, b - a
      number_top = number_center - height / 2
      if vienna:
        fits = number_top >= 57 and circle_fits(59 - width / 2, 59 + width / 2, number_top, number_top + height)
        if shown.offset_text:
          left, right, a, b = ink(shown.offset_text, FontRole.MEDIUM, offset_size)
          offset_top = number_top + height + 4
          fits = fits and circle_fits(59 - (right - left) / 2, 59 + (right - left) / 2, offset_top, offset_top + b - a)
      else:
        fits = width <= 96 and height <= 54
      if fits:
        break
      size *= .95
      if vienna:
        offset_size *= .95
    number = centered(shown.posted_text, role, size, number_top, color)
    if shown.offset_text:
      if not vienna:
        left, right, a, b = ink(shown.offset_text, FontRole.MEDIUM, offset_size)
        offset_size *= min(1, 96 / (right - left), 24 / (b - a))
        offset_top = 97
      centered(shown.offset_text, FontRole.MEDIUM, offset_size, offset_top, color if vienna else secondary)
    if obs.driver_override_active:
      color = rl.Color(197, 163, 255, 240)
      left, top, right, bottom = number
      for start, end in ((rl.Vector2(left + 3 * scale, top + 3 * scale), rl.Vector2(right - 3 * scale, bottom - 3 * scale)),
                         (rl.Vector2(right - 3 * scale, top + 3 * scale), rl.Vector2(left + 3 * scale, bottom - 3 * scale))):
        rl.draw_line_ex(start, end, 4.5 * scale, rl.Color(6, 14, 10, 220))
        rl.draw_line_ex(start, end, 2.5 * scale, color)
