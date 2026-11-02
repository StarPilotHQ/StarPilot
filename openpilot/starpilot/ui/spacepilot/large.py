"""The native Big UI Developer version row, sharing the parked game and gesture."""

import math

from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.starpilot.ui.feature_settings import DETAIL_SIZE, LABEL_SIZE
from openpilot.starpilot.ui.feature_settings_state import FEATURE_ROW_HEIGHT, feature_row_top
from openpilot.starpilot.ui.presentation import FontRole
from openpilot.starpilot.ui.starpilot_settings_adapter_large import StarPilotSettingsAdapterLarge
from openpilot.starpilot.ui.spacepilot.guard import ParkGate
from openpilot.starpilot.ui.spacepilot.native import SpacePilotPage, live_witness
from openpilot.starpilot.ui.spacepilot.state import FiveTaps
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.list_view import text_item


class SpacePilotDeveloperAdapter(StarPilotSettingsAdapterLarge):
  def __init__(self, panel, fonts, on_back, on_unsafe):
    super().__init__(panel, fonts, 'Developer', on_back)
    self.version = text_item('Version', lambda: ui_state.params.get('Version') or 'N/A')
    panel._scroller._items.insert(0, self.version)
    self.on_unsafe = on_unsafe
    self.gate = ParkGate()
    self.taps = FiveTaps()
    self.press = None
    self.version_touch = False

  def _reset_gesture(self):
    self.taps.reset()
    self.press = None
    self.version_touch = False

  def show_event(self):
    self._reset_gesture()
    self.gate.reset()
    super().show_event()

  def hide_event(self):
    self._reset_gesture()
    self.gate.reset()
    super().hide_event()

  def _update_state(self):
    super()._update_state()
    witness = live_witness()
    self.gate.update(witness)
    if not self.active or not self.enabled or witness is None:
      self._reset_gesture()

  def _version_bounds(self):
    state = self.snapshot()
    if state.scroll or state.editor or not state.rows or state.rows[0].key != str(id(self.version)):
      return rl.Rectangle(0, 0, 0, 0)
    left = 520 if state.sidebar_expanded else 20
    row = state.rows[0]
    detail = self.view._elide(row.value, FontRole.NORMAL, DETAIL_SIZE, 2094 - left - 55)
    lt, lb = self.view.fonts.vertical_ink(row.label, FontRole.NORMAL, LABEL_SIZE)
    dt, db = self.view.fonts.vertical_ink(detail, FontRole.NORMAL, DETAIL_SIZE)
    height = lb - lt + 12 + db - dt
    top = feature_row_top(state) + (FEATURE_ROW_HEIGHT - height) / 2 + lb - lt + 12
    return rl.Rectangle(self.origin[0] + left + 55, self.origin[1] + top - 8,
                        self.view.fonts.measure(detail, FontRole.NORMAL, DETAIL_SIZE).width, db - dt + 16)

  def _process_mouse_events(self):
    if not self._touch_valid():
      self._reset_gesture()
    for event in gui_app.mouse_events:
      if event.slot == 0 and event.left_pressed and not rl.check_collision_point_rec(event.pos, self._version_bounds()):
        self._reset_gesture()
    super()._process_mouse_events()

  def _handle_mouse_press(self, pos):
    self.version_touch = bool(rl.check_collision_point_rec(pos, self._version_bounds()))
    self.press = None
    if self.version_touch:
      self.input.cancel()
      if self.view.reset(self.snapshot()):
        self.taps.reset()
        self.version_touch = False
      return
    self.taps.reset()
    super()._handle_mouse_press(pos)

  def _handle_mouse_event(self, mouse_event):
    event = mouse_event
    if not self.version_touch:
      super()._handle_mouse_event(event)
      return
    if event.left_pressed:
      self.press = event
    elif self.press is not None:
      if (not 0 <= event.t - self.press.t <= .45 or
          math.hypot(event.pos.x - self.press.pos.x, event.pos.y - self.press.pos.y) > 24 or
          not self._touch_valid()):
        self.press = None
        self.taps.reset()
      elif event.left_released:
        self.press = None
        witness = live_witness()
        self.gate.update(witness)
        if not rl.check_collision_point_rec(event.pos, self._version_bounds()) or witness is None:
          self.taps.reset()
        elif self.taps.tap(event.t):
          gui_app.mouse_events.clear()
          gui_app.push_widget(SpacePilotPage(self.gate, self.on_unsafe))

  def _handle_mouse_release(self, pos):
    if not self.version_touch:
      super()._handle_mouse_release(pos)

  def _emit(self, request):
    self._reset_gesture()
    super()._emit(request)
