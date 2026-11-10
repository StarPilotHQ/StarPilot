"""Ephemeral STOP display beside the existing speed sign on both profiles."""
import math
import time

from openpilot.starpilot.speed_limits.vision.stop_observation import STOP_MAX_AGE_NS
from openpilot.starpilot.ui.large_speed_geometry import LARGE_SIGN_SCALE, LIMIT_RECTS
from openpilot.starpilot.ui.onroad_customization import offset, placement
from openpilot.starpilot.ui.onroad_state import AlertSize, slc_action_occludes
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.presentation import FontRole, Profile
from openpilot.starpilot.ui.raylib import rl


def stop_sign_bounds(profile: Profile, state):
  widget = 'speed_limit' if profile == Profile.COMPACT else 'cruise_limits'
  if not placement(state.customization, profile, widget)['enabled']:
    return None
  x, y, width, _ = LIMIT_RECTS[profile]
  dx, dy = offset(state.customization, profile, widget)
  scale = LARGE_SIGN_SCALE if profile == Profile.LARGE else .7
  left = x + dx + width + 24 if profile == Profile.LARGE else x + dx - 94
  top = y + dy + (66 * LARGE_SIGN_SCALE - 50 * scale if profile == Profile.LARGE else 8)
  if slc_action_occludes(profile, state, (left, top, left + 100 * scale, top + 100 * scale)):
    return None
  return left, top, 100 * scale, scale


def render_stop_sign(fonts, state) -> None:
  if (state.alert.size != AlertSize.NONE or state.reverse_driver_camera or state.reversing or
      state.appearance.camera_view == CameraViewChoice.DRIVER):
    return
  now = state.observed_ns or time.monotonic_ns()
  if not state.stop_sign_observed_ns > 0 or not 0 <= now - state.stop_sign_observed_ns < STOP_MAX_AGE_NS:
    return
  bounds = stop_sign_bounds(fonts.profile, state)
  if bounds is None:
    return
  draw_stop_sign(fonts, bounds[0], bounds[1], scale=bounds[3])


def draw_stop_sign(fonts, x: float, y: float, *, scale: float = 1) -> None:
  white = rl.Color(244, 247, 245, 255)
  center = rl.Vector2(x + 50 * scale, y + 50 * scale)
  radius = 50 * scale / math.cos(math.pi / 8)
  rl.draw_poly(center, 8, radius, 22.5, rl.Color(200, 0, 0, 255))
  vertices = [rl.Vector2(center.x + radius * math.cos(math.radians(22.5 + i * 45)),
                         center.y + radius * math.sin(math.radians(22.5 + i * 45))) for i in range(8)]
  for i, point in enumerate(vertices):
    rl.draw_line_ex(point, vertices[(i + 1) % 8], 2 * scale, white)
  size = 28 * scale * Profile.COMPACT.font_scale / fonts.profile.font_scale
  left, right = fonts.horizontal_ink('STOP', FontRole.BOLD, size)
  top, bottom = fonts.vertical_ink('STOP', FontRole.BOLD, size)
  fonts.draw('STOP', FontRole.BOLD, size, center.x - (left + right) / 2, center.y - (top + bottom) / 2, white)
