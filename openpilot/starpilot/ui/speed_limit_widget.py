"""The latest limit, its attention animation and override speed on both screens."""

import math
import time
from typing import cast

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.large_speed_geometry import LARGE_SIGN_SCALE, SIGN_WIDTH, SIGN_HEIGHT, CONFIRMATION_SCALE, PROMPT_SCALE
from openpilot.starpilot.ui.onroad_customization import placement
from openpilot.starpilot.ui.onroad_state import (SIGN_PADDING, OnroadState, manual_slc_confirmation, speed_limit_bounds, slc_controls,
                                                 slc_accept_available)
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile
from openpilot.starpilot.ui.speed_limit_pulse import SpeedLimitPulse
from openpilot.starpilot.ui.speed_limit_shapes import SpeedLimitShapes
from openpilot.starpilot.ui.unified_speed_presentation import displayed_adjusted_limit_mps, positive, resolve_unified_speed


INK = rl.Color(241, 246, 243, 255)
MUTED = rl.Color(190, 208, 202, 255)
IN_USE = rl.Color(128, 216, 166, 255)
OVERRIDE = rl.Color(214, 188, 240, 255)
REJECT = rl.Color(242, 139, 139, 255)
SOURCE_LABELS = {'dashboard': 'DASH', 'map': 'OSM', 'vision': 'VISION', 'mapbox': 'NAV'}
NUMBER_SIZE, HEADING_SIZE = 70, 26
# Large UI labels (SET, source, offset, status) are read from further away than their numbers need.
LABEL_SCALE = {Profile.LARGE: 36 / 26, Profile.COMPACT: 1.}
# The large Vienna ring fills more of its tap area; text inside keeps its proportions to the ring.
VIENNA_SCALE = 1.15
# Ink-centered rows, shared by the portrait sign and the confirmation notice.
HEADING_ROW, NUMBER_ROW = 16.75, 61
# SET, the sign and the large new-limit notice put their number on this row below the card top,
# so the numbers line up in every state and SET never moves.
SHARED_NUMBER_ROW = 85
SET_HEADING_ROW, SET_NUMBER_ROW = SHARED_NUMBER_ROW - (NUMBER_ROW - HEADING_ROW), SHARED_NUMBER_ROW
SET_STATUS_ROW = SHARED_NUMBER_ROW + 40


# A soft shade around large text keeps it readable over bright or busy camera views.
HALO = tuple((4 * math.cos(k * math.pi / 4), 4 * math.sin(k * math.pi / 4)) for k in range(8))


def draw_text(fonts, text, role, size, x, y, color=INK):
  if isinstance(color, (tuple, list)):
    color = rl.Color(*color)
  large = fonts.profile == Profile.LARGE
  if large:
    halo = rl.Color(6, 14, 10, round(color.a * .25))
    for dx, dy in HALO:
      fonts.draw(text, role, size, x + dx, y + dy, halo)
  radius = 1.15 if large else 1.
  contour = rl.Color(6, 14, 10, round(color.a * .95))
  for dx, dy in ((-radius, 0), (radius, 0), (0, -radius), (0, radius)):
    fonts.draw(text, role, size, x + dx, y + dy, contour)
  fonts.draw(text, role, size, x, y, color)


def draw_line(profile, start, end, stroke, color):
  radius = 1.4 if profile == Profile.LARGE else 1.
  rl.draw_line_ex(start, end, stroke + 2 * radius, rl.Color(6, 14, 10, round(color.a * .87)))
  rl.draw_line_ex(start, end, stroke, color)


def draw_ink(fonts, text, role, size, x, top, color=INK):
  ink_top, ink_bottom = fonts.vertical_ink(text, role, size)
  draw_text(fonts, text, role, size, x, top - ink_top, color)
  return fonts.measure(text, role, size).width, ink_bottom - ink_top


def draw_speed_row(fonts, text, role, size, center, row, color, reference):
  # Shared glyph references keep SET and SLC on the same baseline as values change.
  left, right = fonts.horizontal_ink(text, role, size)
  top, bottom = fonts.vertical_ink(reference, role, size)
  draw_text(fonts, text, role, size, center - (left + right) / 2, row - (top + bottom) / 2, color)


def draw_set_speed(fonts, shown, center, top, color):
  scale = LARGE_SIGN_SCALE if fonts.profile == Profile.LARGE else 1.
  text_scale = scale * Profile.COMPACT.font_scale / fonts.profile.font_scale
  draw_speed_row(fonts, 'SET', FontRole.SEMI_BOLD, HEADING_SIZE * LABEL_SCALE[fonts.profile] * text_scale, center,
                 top + SET_HEADING_ROW * scale, color, 'SET')
  if shown.max_text == '–':
    draw_line(fonts.profile, rl.Vector2(center - 20 * scale, top + SET_NUMBER_ROW * scale),
              rl.Vector2(center + 20 * scale, top + SET_NUMBER_ROW * scale), 5 * scale, color)
  else:
    draw_speed_row(fonts, shown.max_text, FontRole.SET_SPEED, NUMBER_SIZE * text_scale, center,
                   top + SET_NUMBER_ROW * scale, color, '0123456789')


def draw_set_status(fonts, label, center, top, color):
  scale = LARGE_SIGN_SCALE if fonts.profile == Profile.LARGE else 1.
  size = 22 * LABEL_SCALE[fonts.profile] * scale * Profile.COMPACT.font_scale / fonts.profile.font_scale
  left, right = fonts.horizontal_ink(label, FontRole.MEDIUM, size)
  # Stay inside SET's column, 92 px either side of its center.
  size *= min(1, min(108 * scale, 176) / max(1, right - left))
  draw_speed_row(fonts, label, FontRole.MEDIUM, size, center, top + SET_STATUS_ROW * scale, color, label)


def speed_status(shown):
  """The only status SET shows: the driver's chosen speed winning over the limit."""
  if shown.override_text is None:
    return None
  # SET shows the held speed right above; only a different one, such as a pedal override's, needs repeating.
  return 'OVERRIDE' if shown.override_text == shown.max_text else f'OVERRIDE {shown.override_text}'


def set_speed_status(state, profile, shown):
  # An open prompt shows the status as its own context line instead.
  if state.slc_prompt_open and speed_limit_bounds(profile, state) is not None and manual_slc_confirmation(profile, state):
    return None
  return speed_status(shown)


def set_speed_visible(state, profile):
  return (state.customization.get('showMaxSpeed', True) if profile == Profile.LARGE else
          placement(state.customization, profile, 'max_speed')['enabled'])


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
    from openpilot.starpilot.ui.stop_sign_widget import render_stop_sign

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
    scale = (PROMPT_SCALE if state.slc_prompt_open else CONFIRMATION_SCALE)[self.profile]
    x, y, right, _ = bounds
    width = (right - x) / scale
    shown = resolve_unified_speed(state, confirmation=True)
    source = SOURCE_LABELS.get(shown.source, 'SPEED')
    current_status = (speed_status(resolve_unified_speed(state))
                      if state.slc_prompt_open or not set_speed_visible(state, self.profile) else None)
    white = rl.Color(242, 246, 244, 255)
    secondary = rl.Color(209, 222, 218, 255)

    def text_size(size):
      return size * scale * Profile.COMPACT.font_scale / self.profile.font_scale

    def ink_width(text, size, role=FontRole.MEDIUM):
      left, right = self.fonts.horizontal_ink(text, role, text_size(size))
      return (right - left) / scale

    def ink_height(label, size, role=FontRole.MEDIUM):
      top, bottom = self.fonts.vertical_ink(label, role, text_size(size))
      return (bottom - top) / scale

    def text(label, size, cx, cy, color=white, role=FontRole.MEDIUM):
      size = text_size(size)
      left, right = self.fonts.horizontal_ink(label, role, size)
      top, bottom = self.fonts.vertical_ink(label, role, size)
      draw_text(self.fonts, label, role, size, x + cx * scale - (left + right) / 2,
                y + cy * scale - (top + bottom) / 2, color)

    if state.slc_prompt_open:
      context = f'Now: {current_status}' if current_status else 'NEW LIMIT'
      context_size = 26 if self.profile == Profile.LARGE else 24
      context_size *= min(1, (width - 24) / max(1, ink_width(context, context_size)))
      text(context, context_size, width / 2, 24, secondary)
      label_size, number_size, detail_size, gap = (30, 58, 26, 8) if self.profile == Profile.LARGE else (26, 50, 24, 6)
      # Reserve the full label height so capitalization does not shift either row.
      heading_height = max(ink_height(label, label_size, FontRole.SEMI_BOLD) for label in ('Accept', 'Reject'))
      number_height = ink_height(shown.proposal_text, number_size, FontRole.SET_SPEED)
      for control in slc_controls(self.profile, state):
        accent = (IN_USE if control.label == 'Accept' else REJECT) if control.enabled else MUTED
        selected = control.enabled and pressed == control.request
        ink = accent if selected or not control.enabled else white
        left = (control.bounds[0] - x) / scale
        top = (control.bounds[1] - y) / scale
        w, h = (control.bounds[2] - control.bounds[0]) / scale, (control.bounds[3] - control.bounds[1]) / scale
        self.shapes.button(x + (left + w / 2) * scale, y + (top + h / 2) * scale, accent,
                           profile=self.profile, scale=scale, pressed=selected)
        start = top + (h - heading_height - gap - number_height) / 2
        text(control.label.upper(), label_size, left + w / 2, start + heading_height / 2, ink, FontRole.SEMI_BOLD)
        detail_y = start + heading_height + gap + number_height / 2
        if control.label == 'Accept':
          # Bind the adjusted proposal to its action, not to the current override or source reading.
          text(shown.proposal_text, number_size, left + w / 2, detail_y, ink, FontRole.SET_SPEED)
        else:
          text('NEW LIMIT', detail_size, left + w / 2, detail_y, ink)
      return

    content_top = y + (12 if self.profile == Profile.LARGE else 8)
    row_origin = (content_top - y) / scale
    large = self.profile == Profile.LARGE
    heading_row, number_row = (SET_HEADING_ROW, SHARED_NUMBER_ROW) if large else (HEADING_ROW, NUMBER_ROW)
    caption_row, hint_row = (131, 161) if large else (110, 136.75)
    source_color = rl.Color(197, 212, 208, 255)
    if current_status:
      source += f' / {current_status}'
    heading = HEADING_SIZE * LABEL_SCALE[self.profile]
    label_size = heading * min(1, (width - 40) / max(1, ink_width(source, heading, FontRole.SEMI_BOLD)))
    label_width = ink_width(source, label_size, FontRole.SEMI_BOLD)
    target = state.speed_limit.pending_adjusted_limit_mps
    if not positive(target):
      target = displayed_adjusted_limit_mps(state.speed_limit)
    direction = (-1 if target > state.speed_mps else 1) if target is not None and state.speed_mps is not None and target != state.speed_mps else 0
    start = (width - label_width - (19.2 if direction else 0)) / 2
    draw_speed_row(self.fonts, source, FontRole.SEMI_BOLD, text_size(label_size),
                   x + (start + label_width / 2) * scale, content_top + heading_row * scale, source_color, source)
    if direction:
      self.shapes.arrow('compact', x + (start + label_width + 12.6) * scale, content_top + (heading_row + .5) * scale,
                        direction, source_color, scale=1.1 * scale)
    nw = ink_width(shown.posted_text, NUMBER_SIZE, FontRole.SET_SPEED)
    offset_size = 32 * LABEL_SCALE[self.profile]
    ow = ink_width(shown.offset_text, offset_size) if shown.offset_text else 0
    start = (width - nw - (10 + ow if ow else 0)) / 2
    draw_speed_row(self.fonts, shown.posted_text, FontRole.SET_SPEED, text_size(NUMBER_SIZE),
                   x + (start + nw / 2) * scale, content_top + number_row * scale, white, '0123456789')
    if ow:
      offset_text = cast(str, shown.offset_text)
      ref_top, ref_bottom = self.fonts.vertical_ink('0123456789', FontRole.SET_SPEED, text_size(NUMBER_SIZE))
      _, ink_bottom = self.fonts.vertical_ink(shown.posted_text, FontRole.SET_SPEED, text_size(NUMBER_SIZE))
      number_bottom = row_origin + number_row + (ink_bottom - (ref_top + ref_bottom) / 2) / scale
      top, bottom = self.fonts.vertical_ink(offset_text, FontRole.MEDIUM, text_size(offset_size))
      text(offset_text, offset_size, start + nw + 10 + ow / 2, number_bottom - (bottom - top) / scale / 2, secondary)
    label = 'NEW LIMIT' if slc_accept_available(state) else 'Unavailable'
    caption = ink_width(label, 30, FontRole.SEMI_BOLD)
    cx = (width - caption - 10) / 2
    strength = self.pulse.confirmation_strength(now)
    accent = rl.Color(214, round(188 + 35 * strength), 240, 255)
    text(label, 30, cx + caption / 2, row_origin + caption_row, accent, FontRole.SEMI_BOLD)
    ax, ay = x + (cx + caption + 8) * scale, content_top + caption_row * scale
    draw_line(self.profile, rl.Vector2(ax - 2 * scale, ay - 4 * scale), rl.Vector2(ax + 2 * scale, ay), 1.5 * scale, accent)
    draw_line(self.profile, rl.Vector2(ax + 2 * scale, ay), rl.Vector2(ax - 2 * scale, ay + 4 * scale), 1.5 * scale, accent)
    if state.slc_wheel_confirmation_available:
      hint_size = 23
      accept, reject = ink_width('ACCEPT', hint_size), ink_width('REJECT', hint_size)
      start = (width - 18 - 4 - accept - 10 - 18 - 4 - reject) / 2
      for left, plus in ((start, True), (start + 18 + 4 + accept + 10, False)):
        self.shapes.wheel_key(plus, x + (left + 9) * scale, content_top + hint_row * scale, scale=scale)
      hint_ink = rl.Color(210, 216, 216, 255)
      text('ACCEPT', hint_size, start + 22 + accept / 2, row_origin + hint_row, hint_ink if slc_accept_available(state) else MUTED)
      text('REJECT', hint_size, start + 18 + 4 + accept + 10 + 22 + reject / 2, row_origin + hint_row, hint_ink)
    else:
      text('Tap to review', 22, width / 2, row_origin + hint_row, secondary)

  def _sign(self, state: OnroadState, bounds, now: float) -> None:
    shown, obs = resolve_unified_speed(state), state.speed_limit
    heading = SOURCE_LABELS.get(shown.source, 'SPEED')
    x, top = bounds[0] + 12, bounds[1] + SIGN_PADDING[self.profile][0]
    card_top, large = top, self.profile == Profile.LARGE
    scale, text_scale = self.scale, self.text_scale
    center_x = SIGN_WIDTH / 2
    pending = shown.pending and not obs.automatic_adoption_pending
    target = displayed_adjusted_limit_mps(obs) if pending and not shown.posted_retained else None
    arrow_direction = 0
    if target is not None and state.speed_mps is not None and target != state.speed_mps:
      arrow_direction = -1 if target > state.speed_mps else 1
    white = self.pulse.color(rl.Color(244, 247, 245, 255), now)
    secondary = self.pulse.color(rl.Color(223, 232, 227, 255), now)

    def ink(text, role, size):
      return tuple(value / scale for value in (*self.fonts.horizontal_ink(text, role, size * text_scale),
                                               *self.fonts.vertical_ink(text, role, size * text_scale)))

    def centered(text, role, size, ink_top, color, cx=None):
      left, right, a, b = ink(text, role, size)
      # Snap only the final draw, after fitting the visible glyphs inside the sign.
      origin = round(x + ((center_x if cx is None else cx) - (left + right) / 2) * scale)
      y = round(top + (ink_top - a) * scale)
      draw_text(self.fonts, text, role, size * text_scale, origin, y, color)
      return origin + left * scale, y + a * scale, origin + right * scale, y + b * scale

    vienna_center_y = 67

    def circle_fits(left, right, a, b):
      return all((px - 59) ** 2 + (py - vienna_center_y) ** 2 <= 46 ** 2
                 for px in (left, right) for py in (a, b))

    vienna = state.appearance.use_vienna_sign
    color = white
    # Clamping the controller target does not configure an offset for this sign.
    offset_text = shown.offset_text if vienna or shown.offset_configured else None
    # Lift the Vienna stack to keep the offset clear of the bottom rim.
    lift = (7 if offset_text else 2) if vienna else 0
    # Large signs share one set of rows with or without an offset; compact keeps its own taller-or-centered rows.
    header_y = 47 - lift if vienna else 21 if offset_text or large else 26
    number_center = (73 if offset_text else 80) - lift if vienna else 76 if offset_text or large else 90
    # The outline alone marks the limit being followed; it is drawn thicker so it reads at a glance.
    if vienna:
      if large:
        # A larger ring in the US sign's column, placed so its number sits on the shared row.
        scale, text_scale = scale * VIENNA_SCALE, text_scale * VIENNA_SCALE
        x = bounds[0] + 12 + SIGN_WIDTH / 2 * self.scale - 59 * scale
        top = card_top + SHARED_NUMBER_ROW * self.scale - number_center * scale
      else:
        x, top = x + 5 * scale, top + 5 * scale
      center_x = 59
      if shown.limit_in_use:
        self.shapes.in_use_ring(x + 59 * scale, top + vienna_center_y * scale, IN_USE, scale=scale)
      self.shapes.circle(x + 59 * scale, top + vienna_center_y * scale, self.pulse.color(rl.Color(201, 34, 49, 255), now), scale=scale)
    else:
      # Keep 4:5 proportions and the original text sizes inside the existing tap area.
      sign_scale = (160 if offset_text else 150) / SIGN_HEIGHT
      outline_row = SIGN_HEIGHT * sign_scale / 2 - 9
      if large:
        # The number sits on the shared row; without an offset the outline centers on the heading and number.
        top = card_top + (SHARED_NUMBER_ROW - number_center) * scale
        outline_row = outline_row if offset_text else 54.5
      draw = self.shapes.thick_outline if shown.limit_in_use else self.shapes.outline
      draw(x + center_x * scale, top + outline_row * scale, IN_USE if shown.limit_in_use else white,
           scale=scale * sign_scale, width_scale=SIGN_HEIGHT * 4 / 5 / SIGN_WIDTH)

    heading_size = HEADING_SIZE if not vienna else 18
    if vienna and self.profile == Profile.LARGE and not shown.pending:
      heading_size = 21
    if not vienna:
      heading_size *= LABEL_SCALE[self.profile]
    left, right, a, b = ink(heading, FontRole.SEMI_BOLD, heading_size)
    # Leave room for a pending limit's arrow (12 wide plus a 6 gap) beside the heading.
    fit = 108 - (18 if arrow_direction else 0)
    if not vienna and right - left > fit:
      heading_size *= fit / (right - left)
      left, right, a, b = ink(heading, FontRole.SEMI_BOLD, heading_size)
    arrow_scale = 1.
    if vienna:
      # Fit the heading and arrow inside the circle at the top of their ink.
      half_height = max((b - a) / 2, 8 if arrow_direction else 0)
      width = 2 * math.sqrt(46 ** 2 - (vienna_center_y - header_y + half_height) ** 2)
      gap = 6 if arrow_direction else 0
      row_width = right - left + (12 if arrow_direction else 0)
      if row_width + gap > width:
        arrow_scale = (width - gap) / row_width
        heading_size *= arrow_scale
        left, right, a, b = ink(heading, FontRole.SEMI_BOLD, heading_size)
    heading_width = right - left
    # The small arrow's visible ink is 12 x 16, with a six-pixel label gap.
    arrow_width = 12 * arrow_scale
    header_left = center_x - (heading_width + (6 + arrow_width if arrow_direction else 0)) / 2
    centered(heading, FontRole.SEMI_BOLD, heading_size, header_y - (b - a) / 2, color, header_left + heading_width / 2)
    if arrow_direction:
      self.shapes.arrow('compact', round(x + (header_left + heading_width + 6 + arrow_width / 2) * scale),
                        round(top + header_y * scale), arrow_direction, color, scale=scale * arrow_scale)

    role = FontRole.BOLD if vienna else FontRole.SET_SPEED
    size = 58 if vienna else NUMBER_SIZE
    # The Vienna ring is fixed, so larger labels there would shrink the number; only US labels grow.
    offset_size = 30 if vienna else 27 * LABEL_SCALE[self.profile]
    while True:
      left, right, a, b = ink(shown.posted_text, role, size)
      width, height = right - left, b - a
      number_top = number_center - height / 2
      if vienna:
        fits = number_top >= 57 - lift and circle_fits(59 - width / 2, 59 + width / 2, number_top, number_top + height)
        if offset_text:
          left, right, a, b = ink(offset_text, FontRole.MEDIUM, offset_size)
          offset_top = number_top + height + 4
          fits = fits and circle_fits(59 - (right - left) / 2, 59 + (right - left) / 2, offset_top, offset_top + b - a)
      else:
        fits = width <= 108 and height <= 54
      if fits:
        break
      size *= .95
      if vienna:
        offset_size *= .95
    if vienna:
      centered(shown.posted_text, role, size, number_top, color)
    else:
      draw_speed_row(self.fonts, shown.posted_text, role, size * text_scale, x + center_x * scale,
                     top + number_center * scale, color, '0123456789')
    if offset_text:
      if not vienna:
        left, right, a, b = ink(offset_text, FontRole.MEDIUM, offset_size)
        offset_size *= min(1, 108 / (right - left), 24 / (b - a))
        offset_top = 112
      centered(offset_text, FontRole.MEDIUM, offset_size, offset_top, color if vienna else secondary)
