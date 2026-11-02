import math
from openpilot.starpilot.ui.raylib import rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state, ChestnutState
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget


class ModelSource(Widget):
  def __init__(self, scale: int = 1):
    super().__init__()
    self._engaged = False
    self._small_model_engaged = False
    self._chestnut_drive_frame: int | None = None
    self._fade_time = 0.0
    self._icon: rl.Texture | None = None
    self._alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._loading = gui_app.texture('icons_mici/chestnut.png', 60 * scale, 44 * scale)
    self._active = gui_app.texture('icons_mici/chestnut_green.png', 60 * scale, 44 * scale)
    self._failed = gui_app.texture('icons_mici/chestnut_orange.png', 75 * scale, 44 * scale)
    self._fallback = gui_app.texture('icons_mici/egpu_crossed.png', 60 * scale, 52 * scale)

  def _update_state(self) -> None:
    sm = ui_state.sm
    if sm.recv_frame['carState'] < ui_state.started_frame:
      return
    engaged = sm['selfdriveState'].enabled
    if (engaged and not self._engaged and not ui_state.chestnut_loading and ui_state.chestnut_active is not True and
        sm.recv_frame['modelV2'] > ui_state.started_frame):
      self._small_model_engaged = True
    if engaged != self._engaged:
      self._fade_time = rl.get_time() if engaged else 0
    self._engaged = engaged

  def _render(self, rect: rl.Rectangle) -> None:
    now = rl.get_time()
    loading = ui_state.chestnut_state == ChestnutState.LOADING
    if ui_state.chestnut_present or (ui_state.replay_clock is not None and ui_state.chestnut_output_seen):
      self._chestnut_drive_frame = ui_state.started_frame
    self._small_model_engaged &= (self._chestnut_drive_frame == ui_state.started_frame and
                                 ui_state.chestnut_state in (ChestnutState.FAILED, ChestnutState.DISCONNECTED))
    if loading:
      icon = self._loading
      opacity = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(now * 6.0))
    elif self._small_model_engaged:
      icon = self._fallback
      opacity = 0.65
    elif ui_state.chestnut_state in (ChestnutState.UNCOMPILED, ChestnutState.FAILED):
      icon = self._failed
      opacity = 1.0
    elif ui_state.chestnut_state == ChestnutState.ACTIVE:
      icon = self._active
      opacity = 1.0
    else:
      return

    if icon is not self._icon:
      self._fade_time = now
      self._icon = icon
    alpha = self._alpha_filter.update(loading or now - self._fade_time < 2.5)
    if alpha < 1e-2:
      return

    pos = rl.Vector2(rect.x + rect.width - icon.width, rect.y + (rect.height - icon.height) / 2)
    rl.draw_texture_ex(icon, pos, 0.0, 1.0, rl.Color(255, 255, 255, int(255 * opacity * alpha)))
