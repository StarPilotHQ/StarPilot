"""Compact settings navigation and scroll lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.settings_state import SettingsState, compact_menu
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.system.ui.lib.application import MouseEvent, MousePos, gui_app
from openpilot.system.ui.widgets.nav_widget import NavWidget

if TYPE_CHECKING:
  from openpilot.starpilot.ui.runtime_app import StarShellSession


class StarCompactSettings(NavWidget):
  def __init__(self, session: StarShellSession):
    NavWidget.__init__(self)
    self.session, self.mode = session, ShellMode.SETTINGS
    from openpilot.system.ui.lib.scroll_panel2 import GuiScrollPanel2
    self._panel = GuiScrollPanel2(horizontal=True)

  def show_event(self) -> None:
    super().show_event()
    from openpilot.system.ui.lib.scroll_panel2 import GuiScrollPanel2
    # Reset position and momentum on a fresh push, not when a submenu is popped.
    self.session.cancel()
    self._panel = GuiScrollPanel2(horizontal=True)
    self.session.compact_scroll_x = 0.0
    self.session._snapshot_cache = None

  def covers_camera(self, rect: rl.Rectangle) -> bool:
    return bool(gui_app.get_active_widget() is self and self.enabled and self.is_visible and
                not gui_app.mouse_events and not self.is_dismissing and self._drag_start_pos is None and
                self._shown_callback is None and self._y_pos_filter.x == 0 and self._y_pos_filter.velocity.x == 0 and
                self.rect.x == rect.x == 0 and self.rect.y == rect.y == 0 and
                self.rect.width == rect.width == gui_app.width and self.rect.height == rect.height == gui_app.height)

  def _update_state(self) -> None:
    NavWidget._update_state(self)
    target = -422 * round(-self._panel.get_offset() / 422)
    # Project the pairing snapshot after rendering updates the panel offset.
    paired = bool(self.session.adapter.ui_state.prime_state.is_paired())
    self.session.compact_scroll_x = self._panel.update(self.rect, 20 + len(compact_menu(SettingsState(paired=paired))) * 422,
                                                        snap_target=target)

  def _render(self, rect) -> None:
    self.session.render(self.mode, rect)

  def _handle_mouse_event(self, mouse_event: MouseEvent) -> None:
    NavWidget._handle_mouse_event(self, mouse_event)
    if mouse_event.left_down and not mouse_event.left_pressed:
      self.session.move(self.mode, mouse_event.pos.x - self.rect.x, mouse_event.pos.y - self.rect.y)
    elif mouse_event.left_released and not rl.check_collision_point_rec(mouse_event.pos, self.rect):
      self.session.cancel()

  def _handle_mouse_press(self, mouse_pos: MousePos) -> None:
    self.session.press(self.mode, mouse_pos.x - self.rect.x, mouse_pos.y - self.rect.y)

  def _handle_mouse_release(self, mouse_pos: MousePos) -> None:
    self.session.release(self.mode, mouse_pos.x - self.rect.x, mouse_pos.y - self.rect.y)

  def hide_event(self) -> None:
    self.session.cancel()
    super().hide_event()
