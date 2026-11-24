from __future__ import annotations

import math
from typing import Any, TYPE_CHECKING

from openpilot.selfdrive.ui.mici.layouts.home import HOME_PADDING
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.raylib import rl
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.system.ui.lib.application import MouseEvent, MousePos
from openpilot.system.ui.widgets import Widget

if TYPE_CHECKING:
  from openpilot.starpilot.ui.runtime_app import StarShellSession


class StarShellPage(Widget):
  def __init__(self, session: StarShellSession, mode: ShellMode, *, on_background_tap: Any = None,
               alerts_pill: Widget | None = None):
    super().__init__()
    self.session, self.mode = session, mode
    self.on_background_tap = on_background_tap
    self.alerts_pill = alerts_pill
    self._press_pos: tuple[float, float] | None = None
    self._dragged = False
    self._touch_time: float | None = None

  def _render(self, rect: rl.Rectangle) -> None:
    self.session.render(self.mode, rect, self._parent_rect)
    if self.alerts_pill is not None:  # bottom right, where upstream's compact home draws it
      self.alerts_pill.set_position(rect.x + rect.width - self.alerts_pill.rect.width - HOME_PADDING,
                                    rect.y + rect.height - self.alerts_pill.rect.height)
      self.alerts_pill.render()
    if self.mode == ShellMode.ONROAD and self.session.profile == Profile.COMPACT:
      bookmark = self.session.camera_owner._bookmark_icon
      bookmark.set_touch_valid_callback(lambda: self._touch_valid() and abs(self.rect.x) < 1)
      bookmark.set_parent_rect(self._parent_rect or self.rect)
      bookmark.render(rect)

  def _handle_mouse_press(self, mouse_pos: MousePos) -> None:
    self._press_pos = (mouse_pos.x, mouse_pos.y)
    self._dragged = False
    self._touch_time = None
    self.session.press(self.mode, mouse_pos.x - self.rect.x, mouse_pos.y - self.rect.y)

  def _handle_mouse_event(self, mouse_event: MouseEvent) -> None:
    self._touch_time = mouse_event.t
    if mouse_event.left_down and (not mouse_event.left_pressed or self.mode == ShellMode.SETTINGS):
      self._track_background_drag(mouse_event.pos)
      self.session.move(self.mode, mouse_event.pos.x - self.rect.x, mouse_event.pos.y - self.rect.y, timestamp=mouse_event.t)
    elif mouse_event.left_released and not rl.check_collision_point_rec(mouse_event.pos, self.rect):
      self.session.cancel()

  def _handle_mouse_release(self, mouse_pos: MousePos) -> None:
    self._track_background_drag(mouse_pos)
    handled = self.session.release(self.mode, mouse_pos.x - self.rect.x, mouse_pos.y - self.rect.y, timestamp=self._touch_time)
    bookmark_handled = (self.mode == ShellMode.ONROAD and self.session.profile == Profile.COMPACT and
                        self.session.camera_owner._bookmark_icon.interacting())
    if not handled and not self._dragged and not bookmark_handled and self.on_background_tap is not None:
      self.on_background_tap()
    self._press_pos = None
    self._dragged = False

  def _track_background_drag(self, pos: MousePos) -> None:
    tolerance = 36 if self.session.profile == Profile.LARGE else 5
    if self._press_pos is not None and math.hypot(pos.x - self._press_pos[0], pos.y - self._press_pos[1]) > tolerance:
      self._dragged = True

  def hide_event(self) -> None:
    self.session.cancel()
    if self.mode == ShellMode.ONROAD and self.session.profile == Profile.COMPACT:
      self.session.camera_owner._bookmark_icon.hide_event()
    self._press_pos = None
    self._dragged = False
    super().hide_event()
