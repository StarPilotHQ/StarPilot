"""Short onroad notice when the car's drive mode selects new matched longitudinal profiles."""

from typing import Any

from openpilot.starpilot.longitudinal import drive_mode
from openpilot.starpilot.longitudinal.profile_document import DEFAULT_DECELERATION_PROFILE
from openpilot.starpilot.longitudinal.profile_preferences import read_document_value
from openpilot.starpilot.ui.long_profile_feature import preset_label
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert

NOTICE_NS = 1_500_000_000
SETTINGS_REFRESH_NS = 1_000_000_000
ALERT_TYPE = "driveModeChanged/warning"
MODE_LABELS = {'eco': 'Eco', 'normal': 'Normal', 'sport': 'Sport'}
PROFILE_LABELS = {'dom_default': 'Default', 'eco': 'Eco', 'standard': 'Standard', 'sport': 'Sport', 'sport_plus': 'Sport+'}
DEFAULT_SELECTED = ('dom_default', DEFAULT_DECELERATION_PROFILE)


def notice_text(mode: str, matched: tuple[bool, bool], selected: tuple[str, str]) -> tuple[str, str]:
  profiles = tuple(mapped if match else own for mapped, match, own in zip(drive_mode.PROFILES[mode], matched, selected, strict=True))
  labels = tuple(PROFILE_LABELS.get(profile, preset_label(profile)) for profile in profiles)
  return f"{MODE_LABELS[mode]} Mode", f"Accel: {labels[0]}, Decel: {labels[1]}"


def read_selected(params: Any) -> tuple[str, str]:
  saved = read_document_value(params)
  document = saved.value if saved.valid and isinstance(saved.value, dict) else None
  if document is None:
    return DEFAULT_SELECTED
  return (str(document.get('selectedAccelerationProfile', DEFAULT_SELECTED[0])),
          str(document.get('selectedDecelerationProfile', DEFAULT_SELECTED[1])))


class DriveModeNotice:
  def __init__(self, params: Any) -> None:
    self.params = params
    self._enabled = (False, False)
    self._selected = DEFAULT_SELECTED
    self._settings_ns: int | None = None
    self._drive_frame: int | None = None
    self._last: str | None = None
    self._until_ns = 0
    self._text = ("", "")

  def _settings(self, now_ns: int) -> tuple[bool, bool]:
    if self._settings_ns is None or not 0 <= now_ns - self._settings_ns < SETTINGS_REFRESH_NS:
      try:
        self._enabled = drive_mode.read_enabled(self.params)
      except (AttributeError, OSError, TypeError, ValueError):
        self._enabled = (False, False)
      try:
        self._selected = read_selected(self.params) if any(self._enabled) else DEFAULT_SELECTED
      except Exception:
        self._selected = DEFAULT_SELECTED
      self._settings_ns = now_ns
    return self._enabled

  def _reset(self) -> None:
    self._drive_frame = None
    self._last = None
    self._until_ns = 0

  def update(self, source: Any | None, car: Any | None, *, cp: Any | None, drive_frame: int, event_ns: int, now_ns: int,
             native_alert: OnroadAlert, traffic_active: bool) -> OnroadAlert:
    acceleration, deceleration = self._settings(now_ns)
    if not drive_mode.capable(cp) or not (acceleration or deceleration):
      self._reset()
      return native_alert
    car_valid = car is not None and bool(getattr(car, 'canValid', False)) and not getattr(car, 'canTimeout', True)
    if source is not None and car_valid and 0 < event_ns <= now_ns:
      mode = 'eco' if source.ecoGear else 'sport' if source.sportGear else 'normal'
      if self._drive_frame != drive_frame or self._last is None:
        self._drive_frame = drive_frame
        self._last = mode
        self._until_ns = 0
      elif mode != self._last:
        self._last = mode
        if traffic_active:
          self._until_ns = 0
        else:
          self._text = notice_text(mode, (acceleration, deceleration), self._selected)
          self._until_ns = event_ns + NOTICE_NS
    if native_alert.size != AlertSize.NONE or now_ns >= self._until_ns:
      return native_alert
    return OnroadAlert(AlertSize.MID, self._text[0], self._text[1], alert_type=ALERT_TYPE)
