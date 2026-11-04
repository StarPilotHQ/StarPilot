"""Shared StarPilot torque bar with caller-qualified utilization."""

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.starpilot.ui.onroad_state import OnroadState
from openpilot.starpilot.ui.onroad_customization import offset as widget_offset, placement
from openpilot.starpilot.ui.onroad_torque_draw import TorqueBarPainter
from openpilot.starpilot.ui.onroad_torque_geometry import CAP_RADIUS, LARGE_CAP_RADIUS


class TorqueBarWidget:
  def __init__(self):
    self._torque_filter = FirstOrderFilter(0.0, 0.1, 1 / 60)
    self._alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / 60)
    self._drive_frame = None
    self._demand_only = False
    self._painter = TorqueBarPainter()

  def prepare(self) -> None:
    self._painter.prepare()

  def render(self, rect: rl.Rectangle, state: OnroadState, screen_width: float) -> None:
    drive_frame = getattr(state, 'torque_drive_frame', None)
    if drive_frame != self._drive_frame or state.torque_is_demand != self._demand_only:
      self._torque_filter.x = self._alpha_filter.x = 0.0
      self._drive_frame = drive_frame
      self._demand_only = state.torque_is_demand
    # Projected canvases use Large UI placements even when their width is 1860.
    profile = 'large' if screen_width >= 1860 else 'compact'
    enabled = placement(state.customization, profile, 'torque_bar')['enabled']
    if not state.lateral_active or not getattr(state, 'torque_source_available', True):
      self._torque_filter.x = 0.0
      self._alpha_filter.x = 0.0
      return
    utilization = max(-1.0, min(1.0, state.torque_utilization))
    torque = self._torque_filter.update(utilization)
    alpha = self._alpha_filter.update(1.0)
    if not enabled:
      return
    cap_radius = LARGE_CAP_RADIUS if screen_width == 2160 else CAP_RADIUS
    self._painter.render(rect, torque, alpha, screen_width, cap_radius, widget_offset(state.customization, profile, 'torque_bar'))

  def close(self) -> None:
    self._painter.close()
