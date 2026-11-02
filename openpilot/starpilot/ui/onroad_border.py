"""Source-bound C4 turn and blindspot half-border presentation."""

from __future__ import annotations

import math

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import clip
from openpilot.system.ui.lib.application import gui_app
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadState


BLINDSPOT_RED = rl.Color(201, 34, 49, 255)
BLINKER_AMBER = rl.Color(255, 214, 0, 255)


def half_colors(state: OnroadState, time_s: float) -> tuple[rl.Color | None, rl.Color | None]:
  signals = state.border_signals
  if (not state.camera_available or state.alert.size == AlertSize.FULL or state.alert.critical or
      signals is None or not math.isfinite(time_s)):
    return None, None
  appearance = state.appearance
  blindspot_present = appearance.show_blindspot_border and (signals.left_blindspot or signals.right_blindspot)
  interval_ms = 250 if blindspot_present else 500
  blinker_on = (int(time_s * 1000) % (interval_ms * 2)) < interval_ms

  def color(blindspot: bool, blinker: bool) -> rl.Color | None:
    if appearance.show_blindspot_border and blindspot:
      return BLINDSPOT_RED
    if appearance.show_signal_border and blinker and blinker_on:
      return BLINKER_AMBER
    return None

  return color(signals.left_blindspot, signals.left_blinker), color(signals.right_blindspot, signals.right_blinker)


def render_compact_half_borders(state: OnroadState, time_s: float) -> None:
  left, right = half_colors(state, time_s)
  border = rl.Rectangle(4, 4, 468, 232)
  for x, width, color in ((0, 238, left), (238, 238, right)):
    if color is None:
      continue
    clip.begin_scissor_mode(x, 0, width, 240)
    try:
      rl.draw_rectangle_rounded_lines_ex(border, 0.12, 16, 8, color)
    finally:
      clip.end_scissor_mode()


def render_large_half_borders(state: OnroadState, time_s: float, content: rl.Rectangle, frame_height: int) -> None:
  """Android Auto: tint each half of the large status edge, matching the C4 half borders."""
  left, right = half_colors(state, time_s)
  middle = int(content.x + content.width / 2)
  for x, width, color in ((0, middle, left), (middle, int(content.x * 2 + content.width) - middle, right)):
    if color is None:
      continue
    clip.begin_scissor_mode(x, 0, width, frame_height)
    try:
      rl.draw_rectangle_rounded_lines_ex(content, 0.12, 10, 30, color)
    finally:
      clip.end_scissor_mode()


def draw_turn_signal(x: float, y: float, direction: int, alpha: int = 255, *, blocked: bool = False) -> None:
  name, width, height = ('blind_spot_left.png', 134, 150) if blocked else ('turn_signal_left.png', 104, 96)
  texture = gui_app.texture('icons_mici/onroad/' + name, width, height, flip_x=direction > 0)
  rl.draw_texture_ex(texture, rl.Vector2(x, y), 0, 1, rl.Color(255, 255, 255, alpha))


def render_compact_turn_signals(state: OnroadState, time_s: float) -> None:
  signals = state.border_signals
  if (not state.camera_available or state.alert.size != AlertSize.NONE or
      signals is None or not math.isfinite(time_s) or int(time_s / .375) % 2):
    return
  for active, x, direction in ((signals.left_blinker, 122, -1), (signals.right_blinker, 250, 1)):
    if active:
      draw_turn_signal(x, 5, direction)
