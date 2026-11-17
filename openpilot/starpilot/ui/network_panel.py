"""Lifecycle boundary for the existing large native NetworkUI instance."""

from collections.abc import Callable

from openpilot.starpilot.ui.raylib import rl

from openpilot.system.ui.widgets.network import NetworkUI
from openpilot.system.ui.widgets import Widget


class NetworkPanelBridge:
  def __init__(self, panel: NetworkUI, authority: Callable[[], bool]):
    self.panel = panel
    self.presentation: Widget = panel
    self.authority = authority
    self.active = False
    self.generation = 0
    self.panel.set_action_guard(lambda: False)

  def _allowed(self, generation: int) -> bool:
    if not self.active or generation != self.generation:
      return False
    try:
      return bool(self.authority())
    except Exception:
      return False

  def enter(self) -> bool:
    if self.active:
      return self._allowed(self.generation)
    try:
      allowed = bool(self.authority())
    except Exception:
      allowed = False
    if not allowed:
      return False
    self.generation += 1
    self.active = True
    token = self.generation
    self.panel.set_action_guard(lambda: self._allowed(token))
    try:
      self.presentation.show_event()
    except Exception:
      self.leave()
      return False
    if not self._allowed(token):
      self.leave()
      return False
    return True

  def leave(self) -> None:
    if not self.active:
      return
    self.active = False
    self.generation += 1
    self.presentation.hide_event()

  def render(self, rect: rl.Rectangle) -> bool:
    if not self.active or not self._allowed(self.generation):
      self.leave()
      return False
    try:
      self.presentation.render(rect)
    except Exception:
      self.leave()
      return False
    if not self._allowed(self.generation):
      self.leave()
      return False
    return True
