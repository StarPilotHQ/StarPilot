"""Compact StarPilot layout on the existing native scroller."""
from __future__ import annotations

from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.starpilot.ui.settings_state import Destination


class StarMiciMainLayout(MiciMainLayout):
  """Compact existing scroller/nav stack with StarPilot content pages."""

  def __init__(self):
    from openpilot.starpilot.ui import runtime_app as runtime
    runtime.validate_runtime_fonts(runtime.Profile.COMPACT)
    runtime.validate_runtime_assets()
    super().__init__()
    self._native_onroad = self._car_onroad_layout
    self._compact_panels: dict[runtime.Destination, runtime.Widget] = {}
    try:
      self.star = runtime.StarShellSession(runtime.Profile.COMPACT, self._native_onroad)
    except Exception:
      self._native_onroad.close()
      raise
    # Upstream's alerts pill is already wired to the alerts page; keep it on the StarPilot home
    self._home_layout = runtime.StarShellPage(self.star, runtime.ShellMode.HOME, alerts_pill=self._home_layout._alerts_pill)
    self._car_onroad_layout = runtime.StarShellPage(self.star, runtime.ShellMode.ONROAD,
                                            on_background_tap=lambda: self._scroll_to(self._home_layout))
    self._settings_layout = runtime.StarCompactSettings(self.star)
    self._scroller.items[1] = self._home_layout
    self._scroller.items[2] = self._car_onroad_layout
    for page in (self._home_layout, self._car_onroad_layout):
      page.set_touch_valid_callback(lambda: self._scroller.scroll_panel.is_touch_valid() and self.enabled)
    for page in (self._home_layout, self._car_onroad_layout, self._settings_layout):
      page.set_rect(runtime.rl.Rectangle(0, 0, runtime.gui_app.width, runtime.gui_app.height))
    self.star.set_navigation(on_settings=lambda: runtime.gui_app.push_widget(self._settings_layout),
                             on_home=lambda: self._settings_layout.dismiss(),
                             on_compact_destination=self._open_compact_destination,
                             on_alerts=self._open_alerts)
    self._scroller.set_scrolling_enabled(lambda: not self._native_onroad.is_swiping_left())
    self._on_body_changed()
    self.star._native_favorite_actions = self._native_favorites
    self.star._publish_bookmark = self._on_bookmark_clicked

  def _render(self, _) -> None:
    rect = _
    page = self._car_onroad_layout
    bookmark = self._native_onroad._bookmark_icon
    bookmark.set_rect(page.rect)
    bookmark.set_parent_rect(self.rect)
    if not (self.enabled and page.enabled and page.is_visible):
      bookmark.cancel_gesture()
    bookmark.process_gesture(can_start=(self.enabled and page.enabled and page.is_visible and page._touch_valid() and
                                       not self._scroller.is_auto_scrolling and abs(page.rect.x - self.rect.x) < 1))
    self.star.camera_paint = not self._settings_layout.covers_camera(rect)
    try:
      super()._render(rect)
    finally:
      self.star.camera_paint = True

  def _open_alerts(self) -> bool:
    if self._alerts_layout.active_alerts() == 0:
      return False
    self._scroll_to(self._alerts_layout)
    return True

  def _native_favorites(self):
    from openpilot.starpilot.ui import runtime_app as runtime
    def panel():
      from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
      if runtime.Destination.TOGGLES not in self._compact_panels:
        self._compact_panels[runtime.Destination.TOGGLES] = TogglesLayoutMici()
      return self._compact_panels[runtime.Destination.TOGGLES]
    return self.star.native_favorite_actions(lambda: (self._on_bookmark_clicked() or True),
                                             lambda index: panel().request_personality(index),
                                             lambda: panel().request_experimental())

  def _handle_transitions(self):
    from openpilot.starpilot.ui import runtime_app as runtime
    from openpilot.starpilot.ui.spacepilot.native import SpacePilotPage, Stage, live_witness
    page = runtime.gui_app.get_active_widget()
    if (isinstance(page, SpacePilotPage) and not page.closed and page.session.stage != Stage.CLOSED and
        live_witness() is not None):
      # Cancel only the cosmetic delayed startup redirect for a freshly parked
      # game. The base movement transition and the game's own gates still run.
      self._onroad_time_delay = None
    super()._handle_transitions()

  def _exit_spacepilot(self) -> None:
    # An unsafe game exit must reveal the existing road/alert presentation now,
    # including when engagement/alerts change while the car still reports Park.
    from openpilot.starpilot.ui import runtime_app as runtime
    runtime.gui_app.pop_widgets_to(self, instant=True)
    self._scroller.scroll_to(self._onroad_layout.rect.x if runtime.ui_state.started else self._home_layout.rect.x, smooth=False)

  def _open_compact_destination(self, destination: Destination) -> None:
    from openpilot.starpilot.ui import runtime_app as runtime
    if destination == runtime.Destination.BLUETOOTH:
      from openpilot.starpilot.ui.bluetooth_compact import BluetoothCompact
      panel = self._compact_panels.get(destination)
      if panel is None:
        panel = BluetoothCompact(self.star.connectivity_allowed)
        self._compact_panels[destination] = panel
      runtime.gui_app.push_widget(panel)
      return
    if destination == runtime.Destination.GALAXY:
      self.star.galaxy_flow.open_compact()
      return
    if destination == runtime.Destination.DRIVING_MODEL:
      from openpilot.starpilot.ui.models_compact import ModelsCompact
      if not hasattr(self, "_models_compact"):
        self._models_compact = ModelsCompact(self.star)
      self._models_compact.open()
      return
    if destination == runtime.Destination.DRIVING_CONTROLS:
      from openpilot.starpilot.ui.feature_settings_compact import FeatureSettingsCompact
      FeatureSettingsCompact(self.star).open(runtime.FeaturePage.HUB)
      return
    if destination == runtime.Destination.APPEARANCE:
      from openpilot.starpilot.ui.appearance_compact import AppearanceCompact
      AppearanceCompact(self.star).open()
      return
    if destination == runtime.Destination.VEHICLE:
      from openpilot.starpilot.ui.vehicle_compact import VehicleCompact
      runtime.gui_app.push_widget(VehicleCompact(self.star))
      return
    if destination == runtime.Destination.PAIR:
      self._open_compact_destination(runtime.Destination.DEVICE)
      self._compact_panels[runtime.Destination.DEVICE].scroll_to_pairing()
      return
    from openpilot.selfdrive.ui.mici.layouts.settings.device.device_layout import DeviceLayoutMici
    from openpilot.selfdrive.ui.mici.layouts.settings.developer import DeveloperLayoutMici
    from openpilot.selfdrive.ui.mici.layouts.settings.network.network_layout import NetworkLayoutMici
    from openpilot.selfdrive.ui.mici.layouts.settings.software import SoftwareLayoutMici
    from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
    panel_types = {runtime.Destination.TOGGLES: TogglesLayoutMici, runtime.Destination.NETWORK: NetworkLayoutMici,
                   runtime.Destination.DEVICE: DeviceLayoutMici, runtime.Destination.SOFTWARE: SoftwareLayoutMici,
                   runtime.Destination.DEVELOPER: DeveloperLayoutMici}
    panel_type = panel_types.get(destination)
    if panel_type is None:
      self.star._unavailable(destination.value)
      return
    panel = self._compact_panels.get(destination)
    if panel is None:
      panel = DeveloperLayoutMici(on_spacepilot_exit=self._exit_spacepilot) if destination == runtime.Destination.DEVELOPER else panel_type()
      if destination == runtime.Destination.DEVICE:
        from openpilot.starpilot.ui.display_compact import DisplayCompact
        from openpilot.starpilot.ui.replay_settings import replay_entry
        panel._scroller.add_widgets([DisplayCompact(self.star).entry_button(), replay_entry(self.star)])
      self._compact_panels[destination] = panel
    runtime.gui_app.push_widget(panel)

  def close(self) -> None:
    self.star.close()
    self._native_onroad.close()
