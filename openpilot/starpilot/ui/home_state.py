"""Immutable Home presentation data and explicit input actions."""

from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Callable

from openpilot.starpilot.ui.presentation import Profile


class HomeMode(StrEnum):
  CHILL = "chill"
  EXPERIMENTAL = "experimental"
  CONDITIONAL_EXPERIMENTAL = "conditional_experimental"
  CONDITIONAL_CHILL = "conditional_chill"


class HomeActionKind(StrEnum):
  OPEN_SETTINGS = "open_settings"
  OPEN_ALERTS = "open_alerts"
  OPEN_TOGGLES = "open_toggles"
  OPEN_PAIRING = "open_pairing"
  SET_EXPERIMENTAL = "set_experimental"


@dataclass(frozen=True)
class DriveSummary:
  drives: int = 0
  distance: float = 0.0
  hours: float = 0.0
  unit: str = "miles"


@dataclass(frozen=True)
class DailyDistance:
  label: str
  distance: float = 0.0
  is_today: bool = False
  is_future: bool = False


@dataclass(frozen=True)
class PersonalRecord:
  title: str
  value: str
  detail: str


@dataclass(frozen=True)
class DriveStatsData:
  all_time: DriveSummary
  past_week: DriveSummary
  this_week: DriveSummary
  daily_distance: tuple[DailyDistance, ...]
  records: tuple[PersonalRecord, ...]


@dataclass(frozen=True)
class HomeState:
  version: str
  commit: str
  commit_date: str
  model_label: str
  description: str
  mode: HomeMode
  experimental_enabled: bool
  experimental_available: bool
  stats: DriveStatsData | None
  paired: bool = True
  network: str = "none"
  network_strength: int = 0
  temperature_c: float | None = None
  vehicle_online: bool = True
  connection: str = "OFFLINE"
  bluetooth: str = ""
  gpu_present: bool = False
  gpu_active: bool = False
  recording_audio: bool = False
  branch: str = ""
  gpu_state: str = "disconnected"
  temperature_metric: bool = False
  temperature_high: bool = False
  jetlink: str = "off"


@dataclass(frozen=True)
class HomeAction:
  kind: HomeActionKind
  value: bool | None = None


class HomeInput:
  """Dispatch touch gestures without writing settings or accessing services."""

  def __init__(self, profile: Profile, emit: Callable[[HomeAction], None]):
    self.profile = profile
    self.emit = emit
    self._press: tuple[float, float, float] | None = None
    self._long_press_handled = False
    self._hold_started: float | None = None
    self._inside = False

  def press(self, x: float, y: float, timestamp: float) -> None:
    width, height = self.profile.size
    self._press = (x, y, timestamp) if 0 <= x < width and 0 <= y < height else None
    self._long_press_handled = False
    self._inside = self._press is not None
    self._hold_started = timestamp if self._inside else None

  def move(self, x: float, y: float, timestamp: float) -> None:
    width, height = self.profile.size
    inside = 0 <= x < width and 0 <= y < height
    if self._press is not None and self.profile == Profile.COMPACT:
      if not inside:
        self._hold_started = None
        self._long_press_handled = False
      elif not self._inside:
        self._hold_started = timestamp
    self._inside = inside

  def tick(self, timestamp: float, state: HomeState) -> None:
    if self.profile == Profile.COMPACT and self._hold_started is not None and not self._long_press_handled:
      if timestamp - self._hold_started > 0.5:
        self._long_press_handled = True
        if state.experimental_available:
          self.emit(HomeAction(HomeActionKind.SET_EXPERIMENTAL, not state.experimental_enabled))

  def release(self, x: float, y: float, timestamp: float, state: HomeState) -> None:
    if self._press is None:
      return
    self.move(x, y, timestamp)
    self.tick(timestamp, state)
    px, py, _ = self._press
    self._press = None
    self._hold_started = None
    if self._long_press_handled:
      return
    if self.profile == Profile.COMPACT:
      if 0 <= x < 536 and 0 <= y < 240:
        # Right edge is upstream's alerts pill zone (mici ALERTS_ZONE_WIDTH); without alerts it opens settings
        self.emit(HomeAction(HomeActionKind.OPEN_ALERTS if x >= 536 - 180 else HomeActionKind.OPEN_SETTINGS))
    elif px < 300 and 50 <= x <= 250 and 35 <= y <= 152:
      self.emit(HomeAction(HomeActionKind.OPEN_SETTINGS))
    elif px < 300 and state.recording_audio and 170 <= x <= 245 and 245 <= y <= 285:
      self.emit(HomeAction(HomeActionKind.OPEN_TOGGLES))
    elif 1370 <= px <= 2120 and 145 <= py <= 270 and 1370 <= x <= 2120 and 145 <= y <= 270:
      self.emit(HomeAction(HomeActionKind.OPEN_TOGGLES))
    elif not state.paired and 1370 <= px <= 2120 and 300 <= py <= 430 and 1370 <= x <= 2120 and 300 <= y <= 430:
      self.emit(HomeAction(HomeActionKind.OPEN_PAIRING))

  def cancel(self) -> None:
    self._press = None
    self._long_press_handled = False
    self._hold_started = None
    self._inside = False
