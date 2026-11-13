"""Curve Speed's opt-in read-only border and status presentation.

The frozen C3 border supplied the four-edge breathing geometry. Validated
model curvature sets its severity; absent curvature uses the base color.
"""

import math

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.onroad_conditional import curve_slowing
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadState


ACTIVE = rl.Color(34, 197, 94, 255)
TRAINING = rl.Color(112, 192, 216, 255)
AMBER = rl.Color(251, 191, 36, 255)
ORANGE = rl.Color(234, 88, 12, 255)
RED = rl.Color(201, 34, 49, 255)
SPAN = 2.5
BASE_INTENSITY = 0.70
PERIOD = 4.5
CURVATURE_MIN = 0.0005
CURVATURE_MAX = 0.02


def intensity(curvature: float | None) -> float:
  if curvature is None or not math.isfinite(curvature) or abs(curvature) < CURVATURE_MIN:
    return BASE_INTENSITY
  fraction = min(1.0, (abs(curvature) - CURVATURE_MIN) / (CURVATURE_MAX - CURVATURE_MIN))
  return BASE_INTENSITY + (1.0 - BASE_INTENSITY) * fraction


def period(intensity_value: float) -> float:
  fraction = max(0.0, min(1.0, (intensity_value - BASE_INTENSITY) / (1.0 - BASE_INTENSITY)))
  return PERIOD / (1.0 + 2.0 * fraction)


def _blend(first: rl.Color, second: rl.Color, fraction: float) -> rl.Color:
  fraction = max(0.0, min(1.0, fraction))
  return rl.Color(first.r + int((second.r - first.r) * fraction),
                  first.g + int((second.g - first.g) * fraction),
                  first.b + int((second.b - first.b) * fraction), 255)


def glow_color(intensity_value: float) -> rl.Color:
  fraction = max(0.0, min(1.0, (intensity_value - BASE_INTENSITY) / (1.0 - BASE_INTENSITY)))
  if fraction < 0.5:
    return _blend(ACTIVE, AMBER, fraction / 0.5)
  if fraction < 0.6:
    return _blend(AMBER, ORANGE, (fraction - 0.5) / 0.1)
  return _blend(ORANGE, RED, (fraction - 0.6) / 0.4)


def controlling(state: OnroadState) -> bool:
  """A Curve winner needs current independent-axis control authority."""
  curve = state.curve
  return bool(curve is not None and curve.configured and curve.applied and curve.controlling and
              state.longitudinal_active and state.slc_system_long_available and state.alert.size == AlertSize.NONE)


def training(state: OnroadState) -> bool:
  curve = state.curve
  return bool(curve is not None and curve.configured and curve.training and state.camera_available and
              state.alert.size == AlertSize.NONE)


def visible(state: OnroadState) -> bool:
  return state.show_curve_status and (controlling(state) or training(state))


def glowing(state: OnroadState) -> bool:
  curve = state.curve
  return bool(state.show_curve_status and curve is not None and curve.configured and curve.glow and
              state.longitudinal_active and state.slc_system_long_available and state.alert.size == AlertSize.NONE)


def status_label(state: OnroadState) -> str | None:
  """Only a selected, current cap can appear as a controlled target."""
  preview = state.visual_preview
  if preview is not None and preview.curve_curvature is not None:
    return 'CURVE PREVIEW' if state.alert.size != AlertSize.FULL else None
  if not visible(state):
    return None
  curve = state.curve
  assert curve is not None
  if controlling(state) and curve.ceiling_mps is not None:
    speed = round(curve.ceiling_mps * (3.6 if state.metric else 2.2369362921))
    return f"CURVE {speed} {'KM/H' if state.metric else 'MPH'}"
  if training(state):
    return f"CURVE LEARNING {round(curve.progress)}%"
  return None


class GlowPhase:
  """Advance the breath by dt/period; (t % period) / period jumps whenever the period changes."""

  def __init__(self):
    self._phase = 0.0
    self._time_s: float | None = None

  def advance(self, time_s: float, cycle: float) -> float:
    if self._time_s is not None and 0.0 <= time_s - self._time_s <= 1.0:
      self._phase = (self._phase + (time_s - self._time_s) / cycle) % 1.0
    self._time_s = time_s
    return self._phase


def render_glow(rect: rl.Rectangle, state: OnroadState, *, border_width: float, time_s: float,
                phase: GlowPhase | None = None) -> None:
  """Draw behind the axis-status border; absent/stale/off draws nothing."""
  preview = state.visual_preview
  preview_curve = preview.curve_curvature if preview is not None and state.alert.size != AlertSize.FULL else None
  if preview_curve is not None:
    active = True
    strength = intensity(preview_curve)
  else:
    active = glowing(state) or curve_slowing(state)
    if not active and not (state.show_curve_status and training(state)):
      return
    curve = state.curve
    strength = intensity(curve.road_curvature if curve is not None else None) if active else BASE_INTENSITY
  color = glow_color(strength) if active else TRAINING
  cycle = period(strength)
  fraction = max(0.0, min(1.0, (strength - BASE_INTENSITY) / (1.0 - BASE_INTENSITY)))
  amplitude = 0.3 + 0.7 * fraction
  position = phase.advance(time_s, cycle) if phase is not None else (time_s % cycle) / cycle
  breath = max(0.0, min(1.0, 0.5 + amplitude * math.sin(position * 2 * math.pi)))
  alpha = int(200 * strength * (0.6 + 0.4 * breath))
  span = max(1, int(border_width * SPAN))
  edge = rl.Color(color.r, color.g, color.b, alpha)
  fade = rl.Color(color.r, color.g, color.b, 0)
  x, y, width, height = int(rect.x), int(rect.y), int(rect.width), int(rect.height)
  rl.draw_rectangle_gradient_v(x, y, width, span, edge, fade)
  rl.draw_rectangle_gradient_v(x, y + height - span, width, span, fade, edge)
  rl.draw_rectangle_gradient_h(x, y, span, height, edge, fade)
  rl.draw_rectangle_gradient_h(x + width - span, y, span, height, fade, edge)
