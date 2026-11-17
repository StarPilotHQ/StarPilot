"""Jetlink status card shared by the native and Android Auto screens."""
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.models.jetlink_status import JetlinkStatus
from openpilot.starpilot.ui.onroad_customization import JETLINK_SIZE, JETLINK_WIDGET
from openpilot.starpilot.ui.presentation import FontRole

LABELS = {'active': 'Link up', 'connected': 'Host connected', 'ready': 'Ready to switch',
          'connecting': 'Connecting', 'disconnected': 'Disconnected', 'unavailable': 'Unavailable'}
GREEN = rl.Color(70, 200, 110, 255)
ORANGE = rl.Color(255, 150, 60, 255)
ACCENT = rl.Color(199, 174, 247, 255)
GRAY = rl.Color(140, 136, 152, 255)
TEXT = rl.Color(245, 242, 252, 255)
MUTED = rl.Color(184, 178, 200, 255)
DOT = {'active': GREEN, 'ready': GREEN, 'unavailable': ORANGE, 'disconnected': GRAY}


class JetlinkStatusWidget:
  def __init__(self, fonts):
    self.fonts = fonts
    self.status = JetlinkStatus()

  def _fit(self, text, role, size, width):
    measured = self.fonts.measure(text, role, size)
    return size * width / measured.width if measured.width > width else size

  def render(self, state):
    placed = state.customization['layouts']['large'].get(JETLINK_WIDGET)
    status = self.status
    if not placed or not placed['enabled'] or status.state == 'off' or state.alert.size != 'none':
      return
    rect = rl.Rectangle(placed['x'], placed['y'], *JETLINK_SIZE)
    h = rect.height
    rl.draw_rectangle_rounded(rect, 1.0, 16, rl.Color(15, 13, 23, round(235 * placed.get('opacity', 85) / 100)))
    rl.draw_circle_v(rl.Vector2(rect.x + h / 2, rect.y + h / 2), 9, DOT.get(status.state, ACCENT))
    right = rect.x + rect.width - 30
    if status.state == 'active' and status.latency_ms is not None:
      value, unit = f'{status.latency_ms:.1f}', 'ms'
      unit_w = self.fonts.measure(unit, FontRole.NORMAL, 22).width
      value_w = self.fonts.measure(value, FontRole.SEMI_BOLD, 34).width
      self.fonts.draw(unit, FontRole.NORMAL, 22, right - unit_w, rect.y + h / 2 - 6, MUTED)
      right -= unit_w + 8
      self.fonts.draw(value, FontRole.SEMI_BOLD, 34, right - value_w, rect.y + h / 2 - 18, TEXT)
      right -= value_w + 16
    left = rect.x + h - 6
    width = right - left
    title = LABELS.get(status.state, 'Unavailable')
    detail = f'Jetlink • {status.device}' if status.device else 'Jetlink'
    self.fonts.draw(title, FontRole.SEMI_BOLD, self._fit(title, FontRole.SEMI_BOLD, 28, width), left, rect.y + 12, TEXT)
    self.fonts.draw(detail, FontRole.NORMAL, self._fit(detail, FontRole.NORMAL, 22, width), left, rect.y + 46, MUTED)
