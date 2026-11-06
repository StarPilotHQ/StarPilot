"""Positioned native cone/person monitoring artwork with observed visibility."""

import math

from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.mici.onroad.driver_state import DriverStateRenderer
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.starpilot.ui.onroad_customization import PROFILES, placement, widget_size
from openpilot.starpilot.ui.onroad_state import AlertSize
from openpilot.starpilot.ui.onroad_widget_style import draw_widget_frame
from openpilot.starpilot.ui.presentation import Profile


def valid_observation(monitor, driver):
  try:
    vision = monitor.visionPolicyState
    return (type(monitor.isRHD) is bool and type(vision.faceDetected) is bool and
            all(math.isfinite(float(number)) for number in (vision.pose.pitch, vision.pose.yaw, vision.awarenessPercent)) and
            0 <= vision.awarenessPercent <= 100 and
            getattr(driver, 'rightDriverData' if monitor.isRHD else 'leftDriverData') is not None)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return False


def monitor_bounds(document, profile, is_rhd):
  position = placement(document, profile, 'driver_monitor')
  widget = PROFILES[str(profile)]['widgets']['driver_monitor']
  width, height = widget_size(document, profile, 'driver_monitor')
  x, y = position['x'], position['y']
  if profile == Profile.LARGE and is_rhd and (x, y) == (widget['default']['x'], widget['default']['y']):
    bounds = PROFILES['large']['bounds']
    x = 2 * bounds['x'] + bounds['width'] - x - width
  return rl.Rectangle(x, y, width, height)


def draw_monitor_art(renderer, bounds, profile):
  """Scale the native icon and cone together inside the saved square."""
  base = 128 if profile == Profile.LARGE else 60
  renderer._rect = rl.Rectangle(bounds.x, bounds.y, base, base)
  rl.rl_push_matrix()
  try:
    rl.rl_translatef(bounds.x, bounds.y, 0)
    rl.rl_scalef(bounds.width / base, bounds.height / base, 1)
    rl.rl_translatef(-bounds.x, -bounds.y, 0)
    renderer._render(renderer._rect)
  finally:
    rl.rl_pop_matrix()


def monitor_visible(profile, state, *, fresh, onroad, top_icons, bounds=None):
  from openpilot.starpilot.ui.onroad_state import slc_action_occludes

  monitor = bounds if bounds is not None else monitor_bounds(state.customization, profile, state.right_hand_drive)
  if slc_action_occludes(profile, state, (monitor.x, monitor.y, monitor.x + monitor.width, monitor.y + monitor.height)):
    return False
  occluded = False
  if profile == Profile.COMPACT and top_icons:
    speed = placement(state.customization, profile, 'max_speed')
    widget = PROFILES['compact']['widgets']['max_speed']
    occluded = rl.check_collision_recs(monitor, rl.Rectangle(speed['x'], speed['y'], widget['width'], widget['height']))
  return bool(onroad and state.alert.size != AlertSize.FULL and not state.alert.critical and
              not occluded and
              placement(state.customization, profile, 'driver_monitor')['enabled'])


class StableDriverStateRenderer(DriverStateRenderer):
  @property
  def should_draw(self):
    return self._should_draw

  def _update_state(self):
    super()._update_state()
    if self.should_draw:
      self._fade_filter.x = 1.0 if self._face_detected else min(self._fade_filter.x, 0.35)


class LargeDriverStateRenderer(StableDriverStateRenderer):
  def get_driver_data(self):
    result = super().get_driver_data()
    self._face_yaw = -ui_state.sm['driverMonitoringState'].visionPolicyState.pose.yaw
    return result


class DriverMonitorLayer:
  def __init__(self, profile):
    self.profile = profile
    self.renderer = LargeDriverStateRenderer() if profile == Profile.LARGE else StableDriverStateRenderer()
    if profile == Profile.LARGE:
      self.renderer.set_rect(rl.Rectangle(0, 0, 128, 128))
      self.renderer.load_icons()

  def render(self, state, *, monitor, driver, fresh, onroad, top_icons=False, y_offset=0):
    ready = fresh and valid_observation(monitor, driver)
    is_rhd = monitor.isRHD if ready else getattr(self.renderer, '_is_rhd', False)
    bounds = monitor_bounds(state.customization, self.profile, is_rhd)
    bounds.y += y_offset
    visible = monitor_visible(self.profile, state, fresh=ready, onroad=onroad, top_icons=top_icons, bounds=bounds)
    self.renderer.set_should_draw(visible)
    self.renderer.set_position(bounds.x, bounds.y)
    if ready:
      self.renderer._update_state()
    else:
      self.renderer._is_active = False
      self.renderer._awareness_unfull = False
      self.renderer._fade_filter.update(0.35 if visible else 0.0)
    if visible:
      draw_widget_frame(bounds, state.customization, self.profile, "driver_monitor")
      draw_monitor_art(self.renderer, bounds, self.profile)
