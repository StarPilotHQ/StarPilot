"""Offline two-profile StarPilot presentation and request-only input boundary.

The host supplies immutable snapshots, including the explicit onroad mode. This
module owns no Params, SubMaster, camera IPC, updater, or device operation.
"""

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.device import DeviceView
from openpilot.starpilot.ui.feature_settings import FeatureSettingsView
from openpilot.starpilot.ui.feature_settings_state import FeatureInput, FeatureSettingsState, FeatureUiAction
from openpilot.starpilot.ui.device_state import DeviceAction, DeviceInput, DeviceState
from openpilot.starpilot.ui.home import HomeView
from openpilot.starpilot.ui.home_state import HomeAction, HomeInput, HomeState
from openpilot.starpilot.ui.onroad import CameraLayer, OnroadView, OverlayLayer, PipLayer
from openpilot.starpilot.ui.onroad_state import OnroadInput, OnroadRequest, OnroadState, SlcUiRequest
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.settings import SettingsView
from openpilot.starpilot.ui.settings_state import Destination, SettingsAction, SettingsInput, SettingsState
from openpilot.starpilot.ui.software import SoftwareView
from openpilot.starpilot.ui.software_state import SoftwareAction, SoftwareInput, SoftwareState
from openpilot.starpilot.ui.toggles import TogglesView
from openpilot.starpilot.ui.toggles_state import ToggleRequest, TogglesInput, TogglesState


class ShellMode(StrEnum):
  HOME = "home"
  SETTINGS = "settings"
  ONROAD = "onroad"


@dataclass(frozen=True)
class ShellSnapshot:
  mode: ShellMode
  home: HomeState
  settings: SettingsState
  onroad: OnroadState
  device: DeviceState = field(default_factory=DeviceState)
  software: SoftwareState = field(default_factory=SoftwareState)
  toggles: TogglesState = field(default_factory=TogglesState)
  selected: Destination = Destination.STAR
  features: FeatureSettingsState = field(default_factory=FeatureSettingsState)
  sounds: FeatureSettingsState = field(default_factory=FeatureSettingsState)
  appearance: FeatureSettingsState = field(default_factory=FeatureSettingsState)
  display: FeatureSettingsState = field(default_factory=FeatureSettingsState)
  models: FeatureSettingsState = field(default_factory=FeatureSettingsState)


@dataclass(frozen=True)
class ShellRequest:
  source: str
  action: HomeAction | SettingsAction | DeviceAction | SoftwareAction | ToggleRequest | OnroadRequest | SlcUiRequest | FeatureUiAction


class ShellView:
  """The established profile views composed over one supplied shell snapshot."""

  def __init__(self, fonts: BitmapFonts, asset_directory: Path, *, camera_layer: CameraLayer | None = None,
               extra_overlays: OverlayLayer | None = None, pip_layer: PipLayer | None = None):
    self.profile = fonts.profile
    with ExitStack() as acquired:
      self.home = HomeView(fonts, asset_directory)
      acquired.callback(self.home.close)
      self.settings = SettingsView(fonts, asset_directory)
      acquired.callback(self.settings.close)
      self.onroad = OnroadView(fonts, asset_directory, camera_layer=camera_layer, extra_overlays=extra_overlays,
                              pip_layer=pip_layer)
      acquired.callback(self.onroad.close)
      self.device = DeviceView(fonts) if self.profile == Profile.LARGE else None
      self.software = SoftwareView(fonts) if self.profile == Profile.LARGE else None
      self.toggles = TogglesView(fonts, asset_directory) if self.profile == Profile.LARGE else None
      self.features = FeatureSettingsView(fonts) if self.profile == Profile.LARGE else None
      self.sounds = FeatureSettingsView(fonts) if self.profile == Profile.LARGE else None
      self.appearance = FeatureSettingsView(fonts) if self.profile == Profile.LARGE else None
      self.display = FeatureSettingsView(fonts) if self.profile == Profile.LARGE else None
      self.models = FeatureSettingsView(fonts) if self.profile == Profile.LARGE else None
      if self.toggles is not None:
        acquired.callback(self.toggles.close)
      self.settings.prepare()
      acquired.pop_all()

  def render(self, snapshot: ShellSnapshot, drag_x: float = 0.0) -> None:
    if snapshot.mode == ShellMode.HOME:
      self.home.render(snapshot.home)
    elif snapshot.mode == ShellMode.ONROAD:
      self.onroad.render(snapshot.onroad)
    elif self.profile == Profile.LARGE and snapshot.selected == Destination.DEVICE:
      self.device.render(snapshot.device)
      self.settings.render_rail(snapshot.settings, selected=Destination.DEVICE)
    elif self.profile == Profile.LARGE and snapshot.selected == Destination.SOFTWARE:
      self.software.render(snapshot.software)
      self.settings.render_rail(snapshot.settings, selected=Destination.SOFTWARE)
    elif self.profile == Profile.LARGE and snapshot.selected == Destination.TOGGLES:
      self.toggles.render(snapshot.toggles)
      self.settings.render_rail(snapshot.settings, selected=Destination.TOGGLES)
    elif self.profile == Profile.LARGE and snapshot.selected == Destination.DRIVING_CONTROLS:
      self.features.render(snapshot.features, drag_x)
      self.settings.render_rail(snapshot.settings, selected=Destination.STAR)
    elif self.profile == Profile.LARGE and snapshot.selected == Destination.SOUNDS:
      self.sounds.render(snapshot.sounds, drag_x)
      self.settings.render_rail(snapshot.settings, selected=Destination.STAR)
    elif self.profile == Profile.LARGE and snapshot.selected == Destination.APPEARANCE:
      self.appearance.render(snapshot.appearance, drag_x)
      self.settings.render_rail(snapshot.settings, selected=Destination.STAR)
    elif self.profile == Profile.LARGE and snapshot.selected == Destination.SYSTEM:
      self.display.render(snapshot.display, drag_x)
      self.settings.render_rail(snapshot.settings, selected=Destination.STAR)
    elif self.profile == Profile.LARGE and snapshot.selected == Destination.DRIVING_MODEL:
      self.models.render(snapshot.models, drag_x)
      self.settings.render_rail(snapshot.settings, selected=Destination.STAR)
    elif self.profile == Profile.LARGE and snapshot.selected == Destination.NETWORK:
      rail_width = 500 if snapshot.settings.sidebar_expanded else 0
      rl.draw_rectangle_rounded(rl.Rectangle(rail_width + 10, 10, 2140 - rail_width, 1060),
                                0.04, 30, rl.Color(41, 41, 41, 255))
      self.settings.render_rail(snapshot.settings, selected=Destination.NETWORK)
    else:
      self.settings.render(snapshot.settings)

  def close(self) -> None:
    if self.toggles is not None:
      self.toggles.close()
    self.home.close()
    self.settings.close()
    self.onroad.close()


class ShellInput:
  """One pointer, one pane: transitions cancel pending presses before reuse."""

  def __init__(self, profile: Profile, emit: Callable[[ShellRequest], None], *, native_panels: tuple[Destination, ...] = ()):
    self.profile = profile
    self.emit = emit
    self.native_panels = native_panels
    self._pane: tuple[ShellMode, Destination] | None = None
    self._selected = Destination.STAR
    self._settings_emitted = False
    self.home = HomeInput(profile, lambda action: emit(ShellRequest("home", action)))
    self.settings = SettingsInput(profile, self._settings_action, lambda: self._selected)
    self.device = DeviceInput(lambda action: emit(ShellRequest("device", action)))
    self.software = SoftwareInput(lambda action: emit(ShellRequest("software", action)))
    self.toggles = TogglesInput(lambda action: emit(ShellRequest("toggles", action)))
    self.features = FeatureInput(lambda action: emit(ShellRequest("features", action)))
    self.sounds = FeatureInput(lambda action: emit(ShellRequest("sounds", action)))
    self.appearance = FeatureInput(lambda action: emit(ShellRequest("appearance", action)))
    self.display = FeatureInput(lambda action: emit(ShellRequest("display", action)))
    self.models = FeatureInput(lambda action: emit(ShellRequest("models", action)))
    self.onroad = OnroadInput(lambda action: emit(ShellRequest("slc" if isinstance(action, SlcUiRequest) else "onroad", action)),
                              profile)

  def _settings_action(self, action: SettingsAction) -> None:
    self._settings_emitted = True
    self.emit(ShellRequest("settings", action))

  def cancel(self, *, close_prompt: bool = True) -> None:
    self.home.cancel()
    self.settings.cancel()
    self.device.cancel()
    self.software.cancel()
    self.toggles.cancel()
    self.features.cancel()
    self.sounds.cancel()
    self.appearance.cancel()
    self.display.cancel()
    self.models.cancel()
    self.onroad.cancel(close_prompt=close_prompt)
    self._pane = None

  def _sync(self, snapshot: ShellSnapshot) -> None:
    pane = (snapshot.mode, snapshot.selected)
    if self._pane is not None and self._pane != pane:
      self.cancel()
    self._selected = snapshot.selected

  def press(self, x: float, y: float, now: float, snapshot: ShellSnapshot) -> None:
    self._sync(snapshot)
    self._pane = (snapshot.mode, snapshot.selected)
    if snapshot.mode == ShellMode.HOME:
      self.home.press(x, y, now)
    elif snapshot.mode == ShellMode.ONROAD:
      self.onroad.press(x, y, snapshot.onroad)
    else:
      self.settings.press(x, y, snapshot.settings)
      if snapshot.selected in self.native_panels:
        return
      if self.profile == Profile.LARGE and snapshot.selected == Destination.DEVICE:
        self.device.press(x, y, snapshot.device)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.SOFTWARE:
        self.software.press(x, y, snapshot.software)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.TOGGLES:
        self.toggles.press(x, y, snapshot.toggles)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.DRIVING_CONTROLS:
        self.features.press(x, y, snapshot.features)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.SOUNDS:
        self.sounds.press(x, y, snapshot.sounds)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.APPEARANCE:
        self.appearance.press(x, y, snapshot.appearance)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.SYSTEM:
        self.display.press(x, y, snapshot.display)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.DRIVING_MODEL:
        self.models.press(x, y, snapshot.models)

  def move(self, x: float, y: float, now: float | None, snapshot: ShellSnapshot) -> None:
    self._sync(snapshot)
    if self._pane is None:
      return
    if snapshot.mode == ShellMode.HOME:
      if now is None:
        self.home.cancel()
        return
      self.home.move(x, y, now)
      self.home.tick(now, snapshot.home)
    elif snapshot.mode == ShellMode.ONROAD:
      self.onroad.move(x, y, snapshot.onroad)
    else:
      self.settings.move(x, y, snapshot.settings)
      if snapshot.selected in self.native_panels:
        return
      if self.profile == Profile.LARGE and snapshot.selected == Destination.DEVICE:
        self.device.move(x, y, snapshot.device)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.SOFTWARE:
        self.software.move(x, y, snapshot.software)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.TOGGLES:
        self.toggles.move(x, y, snapshot.toggles)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.DRIVING_CONTROLS:
        self.features.move(x, y, snapshot.features, now)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.SOUNDS:
        self.sounds.move(x, y, snapshot.sounds, now)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.APPEARANCE:
        self.appearance.move(x, y, snapshot.appearance, now)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.SYSTEM:
        self.display.move(x, y, snapshot.display, now)
      elif self.profile == Profile.LARGE and snapshot.selected == Destination.DRIVING_MODEL:
        self.models.move(x, y, snapshot.models, now)

  def release(self, x: float, y: float, now: float | None, snapshot: ShellSnapshot) -> None:
    self._sync(snapshot)
    if self._pane is None:
      return
    if snapshot.mode == ShellMode.HOME:
      if now is None:
        self.home.cancel()
        return
      self.home.release(x, y, now, snapshot.home)
    elif snapshot.mode == ShellMode.ONROAD:
      self.onroad.release(x, y, snapshot.onroad)
    else:
      # Navigation may synchronously change the selected pane in the host;
      # the press that caused it must never also activate a leaf control.
      self._settings_emitted = False
      self.settings.release(x, y, snapshot.settings)
      if (not self._settings_emitted and self._pane == (snapshot.mode, snapshot.selected) and
          snapshot.selected not in self.native_panels):
        if self.profile == Profile.LARGE and snapshot.selected == Destination.DEVICE:
          self.device.release(x, y, snapshot.device)
        elif self.profile == Profile.LARGE and snapshot.selected == Destination.SOFTWARE:
          self.software.release(x, y, snapshot.software)
        elif self.profile == Profile.LARGE and snapshot.selected == Destination.TOGGLES:
          self.toggles.release(x, y, snapshot.toggles)
        elif self.profile == Profile.LARGE and snapshot.selected == Destination.DRIVING_CONTROLS:
          self.features.release(x, y, snapshot.features, now)
        elif self.profile == Profile.LARGE and snapshot.selected == Destination.SOUNDS:
          self.sounds.release(x, y, snapshot.sounds, now)
        elif self.profile == Profile.LARGE and snapshot.selected == Destination.APPEARANCE:
          self.appearance.release(x, y, snapshot.appearance, now)
        elif self.profile == Profile.LARGE and snapshot.selected == Destination.SYSTEM:
          self.display.release(x, y, snapshot.display, now)
        elif self.profile == Profile.LARGE and snapshot.selected == Destination.DRIVING_MODEL:
          self.models.release(x, y, snapshot.models, now)
    self.cancel(close_prompt=snapshot.mode != ShellMode.ONROAD)
