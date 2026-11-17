"""Native compact placement adapter for the shared personality renderer."""

from openpilot.starpilot.ui.raylib import rl

from openpilot.cereal import log
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.starpilot.ui.onroad_personality import PersonalityWidget
from openpilot.system.ui.widgets import Widget


class LongIndicator(Widget):
  def __init__(self):
    super().__init__()
    self.indicator = PersonalityWidget()
    self._should_draw = False

  def set_should_draw(self, should_draw: bool):
    self._should_draw = should_draw

  def _render(self, rect: rl.Rectangle) -> None:
    sm = ui_state.sm
    if (sm.recv_frame['selfdriveState'] < ui_state.started_frame or
        not sm['selfdriveState'].enabled or not ui_state.has_longitudinal_control):
      self.indicator.reset()
      return
    self.indicator.draw(
      rect, personality=sm['selfdriveState'].personality.raw,
      has_lead=bool(sm.alive['longitudinalPlan'] and sm['longitudinalPlan'].hasLead),
      longitudinal_active=bool(sm.alive['carControl'] and sm['carControl'].longActive),
      gas_override=any(e.name == log.OnroadEvent.EventName.gasPressedOverride for e in sm['onroadEvents']),
      visible=self._should_draw and sm['selfdriveState'].alertSize == log.SelfdriveState.AlertSize.none, fit=False)
