"""Adapt existing large Settings panels to StarPilot styling and pagination without changing their base files."""

from collections.abc import Callable

from openpilot.system.ui.lib.display_translation import DisplayText

from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.layouts.settings.initial_cruise_speed import InitialCruiseSpeedControl
from openpilot.starpilot.ui.clip import placed_at
from openpilot.starpilot.ui.feature_settings import FeatureSettingsView
from openpilot.starpilot.ui.feature_settings_state import FeatureInput, FeatureRow, FeatureSettingsState, feature_scroll, value_editor_state, value_request
from openpilot.system.ui.lib.application import gui_app, MouseEvent, MousePos
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.wifi_manager import SecurityType, normalize_ssid
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
from openpilot.system.ui.widgets.list_view import ButtonAction, DualButtonAction, MultipleButtonAction, TextAction, ToggleAction, _resolve_value
from openpilot.system.ui.widgets.network import NetworkUI, PanelType, UIState


class StarPilotSettingsAdapterLarge(Widget):
  def __init__(self, panel, fonts, title: str, on_back: Callable[[], None]):
    super().__init__()
    self.panel, self.title, self.on_back = panel, title, on_back
    self.view = FeatureSettingsView(fonts)
    self.input = FeatureInput(self._emit)
    self.view.pressed_action = lambda state: self.input.pressed_action(state) if self.active and self.enabled else None
    self.scroll = 0
    self.edit_key: str | None = None
    self.active = False
    self.sidebar_expanded = True
    self.origin = (0, 0)
    self.items = {}
    self._touch_time: float | None = None

  def show_event(self):
    self.scroll = 0
    self.edit_key = None
    self.active = True
    self.input.cancel()
    self.view.reset()
    self.panel.show_event()

  def hide_event(self):
    self.active = False
    self.edit_key = None
    self.input.cancel()
    self.view.reset()
    self.panel.hide_event()

  def render(self, rect=None):
    if rect is not None:
      self.sidebar_expanded = rect.width < 2000
      left = 520 if self.sidebar_expanded else 20
      self.origin = (rect.x - left - 30, rect.y - 25)
      rect = rl.Rectangle(self.origin[0] + left, self.origin[1] + 10, 2140 - left, 1060)
    return super().render(rect)

  @property
  def _hit_rect(self):
    rect = self.rect
    if self.sidebar_expanded:
      rect = rl.Rectangle(self.origin[0], rect.y, 2140, rect.height)
    return rect if self._parent_rect is None else rl.get_collision_rec(rect, self._parent_rect)

  def _update_state(self):
    self.panel._update_state()
    if isinstance(self.panel, NetworkUI):
      child = self.panel._wifi_panel if self.panel._current_panel == PanelType.WIFI else self.panel._advanced_panel
      child._update_state()
      if child is self.panel._wifi_panel and child.state in (UIState.NEEDS_AUTH, UIState.SHOW_FORGET_CONFIRM):
        child._render(self.rect)
    else:
      for item in self.panel._scroller._items:
        if item.action_item is not None:
          item.action_item._update_state()

  def snapshot(self):
    if self.title == "Developer":
      from openpilot.selfdrive.ui.widgets.ssh_key import SshKeyAction, SshKeyActionState
    rows = []
    self.items = {}
    page = self.title
    if isinstance(self.panel, NetworkUI):
      page += f":{self.panel._current_panel}"
      wifi = self.panel._current_panel == PanelType.WIFI
      rows.append(FeatureRow("network:nav", tr("Advanced Network Settings") if wifi else tr("Wi-Fi Networks"), "",
                             available=True, actions=((tr("OPEN"), True),)))
      if wifi:
        owner = self.panel._wifi_panel
        for network in owner._networks:
          key = "wifi:" + network.ssid
          self.items[key] = network
          saved = owner._wifi_manager.is_connection_saved(network.ssid)
          connected = owner._wifi_manager.connected_ssid == network.ssid
          busy = owner._state_network is not None and owner._state_network.ssid == network.ssid and owner.state in (
            UIState.CONNECTING, UIState.FORGETTING)
          detail = tr("CONNECTING...") if busy and owner.state == UIState.CONNECTING else tr("FORGETTING...") if busy else (
            tr("Connected") if connected else tr("Saved") if saved else tr("Available"))
          security = tr("Unsupported security") if network.security_type == SecurityType.UNSUPPORTED else (
            tr("Open") if network.security_type == SecurityType.OPEN else tr("Secured"))
          actions = [(tr("CONNECT"), not busy and not connected and network.security_type != SecurityType.UNSUPPORTED)]
          if saved:
            actions.append((tr("Forget"), not busy))
          rows.append(FeatureRow(key, DisplayText(normalize_ssid(network.ssid)), f"{detail} - {security} - {network.strength}%",
                                 available=owner._allowed(), actions=tuple(actions)))
        if not owner._networks:
          rows.append(FeatureRow("", tr("Scanning Wi-Fi networks..."), ""))
        return self._state(page, rows)
      items = self.panel._advanced_panel._scroller._items
    else:
      items = self.panel._scroller._items
    for item in items:
      if not item.is_visible:
        continue
      key = str(id(item))
      self.items[key] = item
      action = item.action_item
      enabled = item.enabled and (action is None or action.enabled)
      value, choices, buttons = "", (), ()
      if isinstance(action, ToggleAction):
        value, choices = ("On" if action.get_state() else "Off"), ("Off", "On")
      elif isinstance(action, ButtonAction):
        value, buttons = action.value, ((action.text, True),)
      elif isinstance(action, TextAction):
        value, enabled = action.text, False
      elif isinstance(action, DualButtonAction):
        buttons = tuple((_resolve_value(button._label._text), button.enabled) for button in (
          action.left_button, action.right_button) if button.is_visible)
      elif isinstance(action, MultipleButtonAction):
        choices = tuple(_resolve_value(text) for text in action.buttons)
        value = choices[action.selected_button]
      elif isinstance(action, InitialCruiseSpeedControl):
        choices, index = action.display_options()
        value = choices[index]
      elif self.title == "Developer" and isinstance(action, SshKeyAction):
        value = action._username or ""
        buttons = ((tr(action._state.value), action._state != SshKeyActionState.LOADING),)
      else:
        enabled = False
      rows.append(FeatureRow(key, item.title or tr("Device power"), value, choices=choices, available=enabled, actions=buttons))
    return self._state(page, rows)

  def _state(self, page, rows):
    self.scroll = feature_scroll(self.scroll, 0, len(rows))
    state = FeatureSettingsState(page=page, title=self.title, parent_title="Settings", rows=tuple(rows), scroll=self.scroll,
                                 sidebar_expanded=self.sidebar_expanded)
    return value_editor_state(state, self.edit_key)

  def _render(self, rect):
    with placed_at(rl.Rectangle(*self.origin, 2160, 1080)):
      self.view.render(self.snapshot(), self.input.drag_x)

  def _position(self, pos: MousePos) -> tuple[float, float]:
    return pos.x - self.origin[0], pos.y - self.origin[1]

  def _handle_mouse_press(self, pos):
    self._touch_time = None
    state = self.snapshot()
    x, y = self._position(pos)
    block_tap = (x >= (520 if state.sidebar_expanded else 20) and self.view.reset(state) and
                 FeatureInput._in_body(x, y, state))
    self.input.press(x, y, state)
    if block_tap:
      self.input.cancel_tap()

  def _handle_mouse_event(self, mouse_event: MouseEvent):
    self._touch_time = mouse_event.t
    x, y = self._position(mouse_event.pos)
    state = self.snapshot()
    self.input.move(x, y, state, mouse_event.t if mouse_event.left_down else None)
    if mouse_event.left_released and not (self.is_pressed and rl.check_collision_point_rec(mouse_event.pos, self._hit_rect)):
      # Finish releases skipped by Widget without restoring canceled taps.
      self.input.cancel_tap()
      self.input.release(x, y, state, mouse_event.t)

  def _handle_mouse_release(self, pos):
    self.input.release(*self._position(pos), self.snapshot(), self._touch_time)

  def _emit(self, request):
    if not self.active:
      return
    state = self.snapshot()
    if request.kind == "scroll" and not state.editor:
      self.scroll = feature_scroll(self.scroll, request.direction, len(state.rows))
      return
    if request.kind == "back":
      if self.edit_key is not None:
        self.edit_key = None
        self.input.cancel()
        self.view.reset()
      elif isinstance(self.panel, NetworkUI) and self.panel._current_panel == PanelType.ADVANCED:
        self._cycle_network()
      else:
        self.on_back()
      return
    row = request.row
    if row is None or row not in state.rows:
      return
    if request.kind == "open" and row.available and row.presets and not state.editor:
      self.edit_key = row.key
      self.input.cancel()
      self.view.reset()
      return
    if row.key == "network:nav" and request.kind == "action":
      self._cycle_network()
      return
    item = self.items.get(row.key)
    if row.key.startswith("wifi:"):
      if request.kind == "action":
        owner = self.panel._wifi_panel
        callback = owner._networks_buttons_callback if request.direction == 0 else owner._forget_networks_buttons_callback
        callback(item)
      elif request.kind == "details":
        gui_app.push_widget(ConfirmDialog(f"{row.label}\n{row.value}", tr("OK"), cancel_text=""))
      return
    if item is None:
      return
    action = item.action_item
    if isinstance(action, InitialCruiseSpeedControl) and request.kind in ("change", "action"):
      change = value_request(state, request) if state.editor else None
      self.panel._update_toggles()
      if item.is_visible and item.enabled:
        if change is not None:
          choices, index = action.display_options()
          if row.value == choices[index] and change.value in choices:
            action.select_speed(choices.index(change.value))
        elif not row.presets and request.kind == "change":
          action.adjust_speed(request.direction)
      return
    if isinstance(action, MultipleButtonAction) and row.presets and request.kind in ("change", "action"):
      change = value_request(state, request) if state.editor else None
      if change is not None:
        index = row.choices.index(change.value)
        if self.title == "Toggles":
          self.panel.request_personality(index)
        else:
          action.set_selected_button(index)
          if action.callback is not None:
            action.callback(index)
      return
    if self.title == "Toggles" and request.kind == "change":
      if isinstance(action, MultipleButtonAction):
        self.panel.request_personality((action.selected_button + request.direction) % len(action.buttons))
      elif isinstance(action, ToggleAction):
        key = next(key for key, control in self.panel._toggles.items() if control is item)
        self.panel.request_toggle(key, not action.get_state())
      return
    if request.kind == "details":
      if item.description_opened_callback is not None:
        item.description_opened_callback()
      gui_app.push_widget(ConfirmDialog(item.description or row.value or row.label, tr("OK"), cancel_text="", rich=True))
    elif request.kind == "change" and isinstance(action, ToggleAction):
      action.set_state(not action.get_state())
      if action.toggle._callback is not None:
        action.toggle._callback(action.get_state())
      if item.callback is not None:
        item.callback()
    elif request.kind == "change" and isinstance(action, MultipleButtonAction):
      index = (action.selected_button + request.direction) % len(action.buttons)
      action.set_selected_button(index)
      if action.callback is not None:
        action.callback(index)
    elif request.kind == "action":
      if isinstance(action, DualButtonAction):
        buttons = [button for button in (action.left_button, action.right_button) if button.is_visible]
        buttons[request.direction]._click_callback()
      elif self.title == "Developer":
        from openpilot.selfdrive.ui.widgets.ssh_key import SshKeyAction
        if isinstance(action, SshKeyAction):
          action._handle_button_click()
        elif item.callback is not None:
          item.callback()
      elif item.callback is not None:
        item.callback()

  def _cycle_network(self):
    self.panel._cycle_panel()
    self.scroll = 0
    self.edit_key = None
    self.input.cancel()
    self.view.reset()
