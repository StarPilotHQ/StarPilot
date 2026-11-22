"""Immutable Settings entry data and narrow, explicit preview navigation."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum

from openpilot.starpilot.ui.device_state import DeviceAction, DeviceState
from openpilot.starpilot.ui.feature_settings_state import FEATURE_BACK_WIDTH, FEATURE_HEADER_HEIGHT
from openpilot.starpilot.ui.home_state import HomeAction, HomeActionKind
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.software_state import SoftwareAction, SoftwareState
from openpilot.starpilot.ui.toggles_state import ToggleRequest, TogglesState


class Destination(StrEnum):
  STAR = "star"
  DEVICE = "device"
  NETWORK = "network"
  BLUETOOTH = "bluetooth"
  TOGGLES = "toggles"
  SOFTWARE = "software"
  DEVELOPER = "developer"
  SOUNDS = "sounds"
  DRIVING_MODEL = "driving_model"
  DRIVING_CONTROLS = "driving_controls"
  SYSTEM = "system"
  APPEARANCE = "appearance"
  VEHICLE = "vehicle"
  FIREHOSE = "firehose"
  FORCE_DRIVE = "force_drive"
  GALAXY = "galaxy"
  PAIR = "pair"


@dataclass(frozen=True)
class DestinationAvailability:
  destination: Destination
  available: bool = False
  reason: str = "This destination is not implemented in the development preview."
  request_value: str = ""
  request_revision: str | None = None


RAIL = ((Destination.STAR, "StarPilot"), (Destination.DEVICE, "Device"), (Destination.NETWORK, "Network"),
        (Destination.BLUETOOTH, "Bluetooth"), (Destination.TOGGLES, "Toggles"), (Destination.SOFTWARE, "Software"),
        (Destination.DEVELOPER, "Developer"))
TILES = ((Destination.SOUNDS, "Sounds & Alerts", "sound"), (Destination.DRIVING_MODEL, "Driving Model", "aicar"),
         (Destination.DRIVING_CONTROLS, "Driving Controls", "steering"), (Destination.SYSTEM, "System", "system"),
         (Destination.APPEARANCE, "Appearance", "display"), (Destination.VEHICLE, "Vehicle Settings", "vehicle"))

COMPACT_MENU = (
  (Destination.TOGGLES, "Toggles", "icons_mici/settings.png", 64, 64),
  (Destination.NETWORK, "Network", "icons_mici/settings/network/wifi_strength_full.png", 76, 56),
  (Destination.BLUETOOTH, "Bluetooth", "icons_mici/settings/bluetooth.png", 64, 64),
  (Destination.FORCE_DRIVE, "Force Drive State", None, 0, 0),
  (Destination.VEHICLE, "Vehicle", "icons_mici/settings/vehicle.png", 64, 57),
  (Destination.DEVICE, "Device", "icons_mici/settings/device_icon.png", 72, 58),
  (Destination.SOFTWARE, "Software", "icons_mici/settings/device/update.png", 64, 75),
  (Destination.DRIVING_MODEL, "Driving Model", "icons_mici/settings/device/lkas.png", 72, 56),
  (Destination.DRIVING_CONTROLS, "Driving Controls", "icons_mici/settings/device/lkas.png", 72, 56),
  (Destination.APPEARANCE, "Visuals", "icons_mici/settings/device/cameras.png", 64, 64),
  (Destination.GALAXY, "Galaxy", "icons_mici/settings/galaxy.png", 64, 64),
  (Destination.PAIR, "Pair to Connect", "icons_mici/settings/comma_icon.png", 33, 60),
  (Destination.DEVELOPER, "Developer", "icons_mici/settings/developer_icon.png", 64, 60),
)


@dataclass(frozen=True)
class SettingsState:
  sidebar_expanded: bool = True
  # Compact rendering accepts a static position snapshot. It does not reproduce
  # the original scroller, entry bounce or dismissal animation.
  compact_y: float = 0.0
  compact_scroll_x: float = 0.0
  paired: bool = False
  nav_bar_y: float = 6.0
  nav_bar_alpha: float = 1.0
  force_drive_label: str = "force drive state"
  availability: tuple[DestinationAvailability, ...] = field(default_factory=lambda: tuple(DestinationAvailability(d) for d in Destination))

  def destination(self, destination: Destination) -> DestinationAvailability:
    return next(item for item in self.availability if item.destination == destination)


def compact_menu(state: SettingsState):
  return tuple((item[0], state.force_drive_label, *item[2:]) if item[0] == Destination.FORCE_DRIVE else item
               for item in COMPACT_MENU if not (state.paired and item[0] == Destination.PAIR))


class SettingsActionKind(StrEnum):
  CLOSE = "close"
  TOGGLE_SIDEBAR = "toggle_sidebar"
  REQUEST_DESTINATION = "request_destination"


@dataclass(frozen=True)
class SettingsAction:
  kind: SettingsActionKind
  destination: DestinationAvailability | None = None


def tile_rects(state: SettingsState) -> tuple[tuple[float, float, float, float], ...]:
  x, width = (520.0, 1620.0) if state.sidebar_expanded else (20.0, 2120.0)
  tile_width = (width - 32) / 3
  return tuple((round(x + column * (tile_width + 16)), 112 + row * 477, round(tile_width), 461)
               for row in range(2) for column in range(3))


class SettingsInput:
  """Single-pointer static entry hit testing; drags cancel and never scroll.

  Compact back and motion belong to the eventual application adapter. The
  development router exposes a separate Back control, never an invented swipe.
  """

  def __init__(self, profile: Profile, emit: Callable[[SettingsAction], None],
               selected: Callable[[], Destination] | None = None):
    self.profile, self.emit = profile, emit
    self.selected = selected or (lambda: Destination.STAR)
    self._pressed: tuple[float, float, str] | None = None

  def _target(self, x: float, y: float, state: SettingsState) -> str | None:
    def inside(rect):
      rx, ry, width, height = rect
      return rx <= x <= rx + width and ry <= y <= ry + height
    if not (0 <= x < self.profile.size[0] and 0 <= y < self.profile.size[1]):
      return None
    if self.profile == Profile.COMPACT:
      for index, (destination, _, _, _, _) in enumerate(compact_menu(state)):
        if inside((20 + 422 * index + state.compact_scroll_x, state.compact_y + 30, 402, 180)):
          return destination
      return None
    if inside((0, 511, 40, 140)):
      return "collapse"
    if state.sidebar_expanded:
      if inside((150, 60, 200, 200)):
        return "close"
      for index, (destination, _) in enumerate(RAIL):
        if inside((50, 300 + index * 110, 350, 110)):
          return destination
    if self.selected() == Destination.STAR:
      if (not state.sidebar_expanded and 20 <= x < 20 + FEATURE_BACK_WIDTH and
          12 <= y <= 12 + FEATURE_HEADER_HEIGHT):
        return "close"
      for (destination, _, _), rect in zip(TILES, tile_rects(state), strict=True):
        if inside(rect):
          return destination
    return None

  def press(self, x: float, y: float, state: SettingsState) -> None:
    target = self._target(x, y, state)
    self._pressed = (x, y, target) if target is not None else None

  def move(self, x: float, y: float, state: SettingsState) -> None:
    if self._pressed is not None:
      px, py, target = self._pressed
      if ((self.profile == Profile.COMPACT and (abs(x - px) > 5 or abs(y - py) > 5)) or
          self._target(x, y, state) != target):
        self.cancel()

  def release(self, x: float, y: float, state: SettingsState) -> None:
    self.move(x, y, state)
    if self._pressed is None:
      return
    _, _, target = self._pressed
    self.cancel()
    if target == "close":
      self.emit(SettingsAction(SettingsActionKind.CLOSE))
    elif target == "collapse":
      self.emit(SettingsAction(SettingsActionKind.TOGGLE_SIDEBAR))
    elif isinstance(target, Destination):
      self.emit(SettingsAction(SettingsActionKind.REQUEST_DESTINATION, state.destination(Destination(target))))

  def cancel(self) -> None:
    self._pressed = None


class PreviewRouter:
  """Development-only Home/Settings/large leaf routing and inert requests."""

  def __init__(self, profile: Profile = Profile.LARGE):
    self.profile = profile
    self.in_settings = False
    self.settings = SettingsState()
    if profile == Profile.LARGE:
      self.settings = replace(self.settings, availability=tuple(
        replace(item, available=True, reason="Available in the large development preview.")
        if item.destination in (Destination.DEVICE, Destination.SOFTWARE, Destination.TOGGLES) else item for item in self.settings.availability))
    self.selected = Destination.STAR
    self.device = DeviceState()
    self.software = SoftwareState()
    self.toggles = TogglesState()
    self.notice = "Development preview — large Device, Toggles and Software navigation is available; other leaves and compact scrolling are unavailable."
    self.requests: list[SettingsAction] = []
    self.device_requests: list[DeviceAction] = []
    self.software_requests: list[SoftwareAction] = []
    self.toggle_requests: list[ToggleRequest] = []

  def home_action(self, action: HomeAction) -> None:
    if action.kind in (HomeActionKind.OPEN_SETTINGS, HomeActionKind.OPEN_ALERTS):  # the preview has no offroad alerts
      self.in_settings = True
      self.selected = Destination.STAR
    else:
      self.notice = "Development preview — this Home action has no connected backend."

  def settings_action(self, action: SettingsAction) -> None:
    if action.kind == SettingsActionKind.CLOSE:
      if self.selected in (Destination.DEVICE, Destination.SOFTWARE, Destination.TOGGLES):
        self.selected = Destination.STAR
      else:
        self.in_settings = False
    elif action.kind == SettingsActionKind.TOGGLE_SIDEBAR:
      self.settings = replace(self.settings, sidebar_expanded=not self.settings.sidebar_expanded)
    else:
      if action.destination is not None:
        destination = action.destination.destination
        if destination == Destination.STAR:
          self.selected = Destination.STAR
        elif destination == Destination.DEVICE and self.profile == Profile.LARGE:
          self.selected = Destination.DEVICE
        elif destination == Destination.SOFTWARE and self.profile == Profile.LARGE:
          self.selected = Destination.SOFTWARE
        elif destination == Destination.TOGGLES and self.profile == Profile.LARGE:
          self.selected = Destination.TOGGLES
        else:
          self.requests.append(action)
          self.notice = f"Preview request: {destination}. {action.destination.reason}"

  def device_action(self, action: DeviceAction) -> None:
    if self.in_settings and self.selected == Destination.DEVICE:
      self.device_requests.append(action)
      self.notice = f"Preview request: {action.request}. No device operation is connected."

  def software_action(self, action: SoftwareAction) -> None:
    if self.profile == Profile.LARGE and self.in_settings and self.selected == Destination.SOFTWARE:
      self.software_requests.append(action)
      unavailable = "No update, install, uninstall, configuration change or file read is connected."
      self.notice = f"Preview request: {action.request}. {unavailable}"

  def toggle_action(self, action: ToggleRequest) -> None:
    if self.profile == Profile.LARGE and self.in_settings and self.selected == Destination.TOGGLES:
      self.toggle_requests.append(action)
      self.notice = f"Preview request: {action}. No setting change is connected."
